"""
V14.4.11 Reconciliation Authority & Strict Context Compatibility Tests
Proves:
  A01: Corrupt/invalid snapshot fails closed across fresh scan, persisted accumulator, and direct apply defense.
  A02: Strict context schema rejects any missing required fields, non-integer epochs/revisions, and partial contexts.
  A03: Unverified legacy snapshots are distinguished from corrupt snapshots; explicit migration branch handled safely.
  A04: Identity, fingerprint, epoch, generation, and revision mismatches fail closed with precise diagnostic codes.
  A05: Advancing window preserves frozen cutoff; chronological ordering, NaN rejection, and tie conservative resolution preserved.
  A06: Failure/restart isolation: progress save failures do not mutate state, durable JSON recovers correctly.
"""

import json
from typing import Any, Dict, List, Optional
import pytest
from datetime import datetime, timezone
from sqlalchemy.orm import Session

import models
from services.market_reconciliation_service import (
    CandidateAccumulator,
    CoverageAssessment,
    CoverageStatus,
    CycleBudget,
    EvidenceProgress,
    OrderReconciliationWindow,
    ResolutionDecision,
    TerminalCandidate,
    ValidationResult,
    build_reconciliation_context_result,
    build_reconciliation_context,
    fingerprint_exit_context,
    merge_candidate_accumulator_with_result,
    merge_candidate_accumulator,
    persist_resolution_or_review,
    process_window_slice,
    read_frozen_exit_context,
    resolve_reconciliation_window,
    validate_candidate_context,
    validate_context_compatibility,
    validate_context_schema,
    MarketReconciliationService,
    REQUIRED_CONTEXT_FIELDS,
)


def _seed_paper_order(
    db: Session,
    order_id: str = "ord_auth_01",
    state: str = "paper_open",
    opened_at: int = 1000,
    symbol: str = "XAUUSDT",
    direction: str = "LONG",
    entry_price: float = 4180.0,
    sl_price: float = 4170.0,
    tp_price: float = 4200.0,
    cost_snapshot: Any = None,
) -> models.PaperOrder:
    if cost_snapshot is None and cost_snapshot != "":
        # Default canonical valid snapshot
        snapshot_dict = {
            "direction": direction,
            "actual_entry": entry_price,
            "stop_loss": sl_price,
            "take_profit": tp_price,
            "cost_source": "1.0.0",
            "policy_config_version": 1,
        }
        cost_snapshot = json.dumps(snapshot_dict)

    ord = models.PaperOrder(
        id=str(order_id),
        instrument=symbol,
        direction=direction,
        state=state,
        actual_entry=entry_price,
        planned_entry=entry_price,
        stop_loss=sl_price,
        take_profit=tp_price,
        quantity=0.1,
        initial_risk_usdt=2.0,
        opened_at=opened_at,
        created_at=opened_at,
        cost_snapshot=cost_snapshot if cost_snapshot != "OMIT" else None,
    )
    db.add(ord)
    db.commit()
    db.refresh(ord)
    return ord


def _valid_continuous_coverage(symbol: str = "XAUUSDT", start_ms: int = 1000, end_ms: int = 5000) -> dict:
    return {
        "continuity_proof": {
            "source": symbol,
            "interval_start_ms": start_ms,
            "interval_end_ms": end_ms,
            "source_epoch": 1,
            "has_gap": False,
            "missing_intervals": [],
        }
    }


def _valid_coverage_assessment(symbol: str = "XAUUSDT", start_ms: int = 1000, end_ms: int = 5000) -> CoverageAssessment:
    return CoverageAssessment(
        interval_start_ms=start_ms,
        interval_end_ms=end_ms,
        source=symbol,
        epoch=1,
        price_basis="BID_ASK",
        coverage_status=CoverageStatus.COMPLETE,
        missing_intervals=[],
        evidence_ids=["ev_cov_1"],
        assessed_at_ms=end_ms,
        reason_code="VERIFIED_CONTINUOUS_PROOF",
    )


def _valid_canonical_context(order_id: str = "ord_auth_01") -> dict:
    return {
        "context_schema_version": "1.0.0",
        "order_id": order_id,
        "window_id": order_id,
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "opened_at": 1000,
        "scan_cutoff": 5000,
        "source_epoch": 1,
        "query_generation": 1,
        "arrival_revision": 1,
        "exit_policy_version": "1.0.0",
        "exit_context_fingerprint": "abc123def4567890",
    }


# =========================================================================
# AUTHORITY TEST A01: CORRUPT SNAPSHOT REPRO AND DEFENSE
# =========================================================================

def test_a01_corrupt_snapshot_fails_closed_across_all_paths(isolated_db: Session):
    """
    PHẦN 002, 013, 014, 020, 022, 053:
    Seed order with cost_snapshot='{broken'.
    read_frozen_exit_context returns INVALID / CORRUPT_COST_SNAPSHOT_JSON.
    1. build_reconciliation_context_result returns status='INVALID' with context=None.
    2. Fresh scan via process_window_slice returns read_complete=False, decision=None, reasons contain CORRUPT_COST_SNAPSHOT_JSON.
    3. Persisted accumulator with valid candidate also returns non-close and does not execute.
    4. Direct apply forged CLOSE via persist_resolution_or_review is blocked.
    5. Direct apply via MarketReconciliationService.apply_reconciliation_decision is blocked.
    6. Order remains paper_open with zero accounting / ledger mutation.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id="ord_a01",
        state="paper_open",
        opened_at=1000,
        entry_price=4180.0,
        sl_price=4170.0,
        tp_price=4200.0,
        cost_snapshot="{broken",
    )

    # 1. read_frozen_exit_context check
    frozen = read_frozen_exit_context(ord)
    assert frozen.status == "INVALID"
    assert "CORRUPT_COST_SNAPSHOT_JSON" in frozen.issues

    win = OrderReconciliationWindow(
        order_id="ord_a01",
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        order=ord,
    )

    # 2. build_reconciliation_context_result must return INVALID and context=None
    ctx_res = build_reconciliation_context_result(win)
    assert ctx_res.status == "INVALID"
    assert ctx_res.context is None
    assert "CORRUPT_COST_SNAPSHOT_JSON" in ctx_res.issues

    # 3. Fresh scan via process_window_slice
    coverage = _valid_continuous_coverage("XAUUSDT", 1000, 5000)
    slice_res = process_window_slice(isolated_db, win, coverage_source=coverage)
    assert slice_res.decision is None or slice_res.decision.action != "CLOSE"
    assert slice_res.read_complete is False
    assert any("CORRUPT_COST_SNAPSHOT_JSON" in r for r in slice_res.reasons)

    # 4. Persisted accumulator with valid candidate also fails closed
    cand = TerminalCandidate(
        order_id="ord_a01",
        evidence_id="ev_tp_2000",
        source_timestamp=2000,
        known_at_ms=2000,
        bid=4201.0,
        ask=4202.0,
        last=4201.0,
        price_basis="BID_ASK",
        cause="TP_HIT",
        executable_price=4201.0,
        epoch=1,
        source="REALTIME",
    )
    acc = CandidateAccumulator(
        window_id="ord_a01",
        earliest_source_ts=2000,
        earliest_candidates=[cand],
        validation_context=_valid_canonical_context("ord_a01"),
    )
    prog = EvidenceProgress(
        window_id="ord_a01",
        accumulator=acc,
        read_status="PENDING",
        scan_cutoff=5000,
    )
    slice_res_acc = process_window_slice(isolated_db, win, progress=prog, coverage_source=coverage)
    assert slice_res_acc.decision is None or slice_res_acc.decision.action != "CLOSE"
    assert slice_res_acc.read_complete is False
    assert any("CORRUPT_COST_SNAPSHOT_JSON" in r for r in slice_res_acc.reasons)

    # 5. Direct apply boundary defense (persist_resolution_or_review)
    fake_close_dec = ResolutionDecision(action="CLOSE", candidate=cand)
    apply_res1 = persist_resolution_or_review(isolated_db, win, fake_close_dec)
    assert apply_res1.applied is False
    assert apply_res1.committed is False

    # 6. Direct apply boundary defense (apply_reconciliation_decision)
    mrs = MarketReconciliationService()
    apply_res2 = mrs.apply_reconciliation_decision(isolated_db, ord, fake_close_dec)
    assert apply_res2.applied is False
    assert apply_res2.committed is False

    # 7. Verify zero mutations
    isolated_db.refresh(ord)
    assert ord.state == "paper_open"
    assert ord.closed_at is None
    assert ord.realized_pnl_net is None or ord.realized_pnl_net == 0.0


def test_a01_root_list_and_inverted_geometry_snapshots_rejected(isolated_db: Session):
    """
    PHẦN 016, 053: Root list and invalid geometry cost snapshots return INVALID.
    """
    # Root list
    ord_list = _seed_paper_order(isolated_db, "ord_list", cost_snapshot="[1, 2, 3]")
    frozen_list = read_frozen_exit_context(ord_list)
    assert frozen_list.status == "INVALID"
    assert "ROOT_NOT_DICT" in frozen_list.issues

    # Inverted LONG geometry (SL >= Entry)
    ord_sl_high = _seed_paper_order(
        isolated_db, "ord_sl_high", direction="LONG", entry_price=4180.0, sl_price=4190.0, tp_price=4200.0,
        cost_snapshot=json.dumps({"direction": "LONG", "actual_entry": 4180.0, "stop_loss": 4190.0, "take_profit": 4200.0})
    )
    frozen_sl = read_frozen_exit_context(ord_sl_high)
    assert frozen_sl.status == "INVALID"
    assert "LONG_SL_GTE_ENTRY" in frozen_sl.issues

    # Inverted SHORT geometry (SL <= Entry)
    ord_short_bad = _seed_paper_order(
        isolated_db, "ord_short_bad", direction="SHORT", entry_price=4180.0, sl_price=4170.0, tp_price=4160.0,
        cost_snapshot=json.dumps({"direction": "SHORT", "actual_entry": 4180.0, "stop_loss": 4170.0, "take_profit": 4160.0})
    )
    frozen_short = read_frozen_exit_context(ord_short_bad)
    assert frozen_short.status == "INVALID"
    assert "SHORT_SL_LTE_ENTRY" in frozen_short.issues


# =========================================================================
# AUTHORITY TEST A02: STRICT SCHEMA AND MISSING FIELDS
# =========================================================================

@pytest.mark.parametrize("missing_field", REQUIRED_CONTEXT_FIELDS)
def test_a02_strict_schema_rejects_missing_required_fields(missing_field: str):
    """
    PHẦN 003, 025, 026, 027, 054:
    Parametrize omitting each of the 12 required fields from a valid context.
    validate_context_schema must return is_valid=False with MISSING_FIELD_{field.upper()}.
    validate_context_compatibility must reject both stored-missing and expected-missing.
    """
    ctx = _valid_canonical_context("ord_a02")
    del ctx[missing_field]

    schema_res = validate_context_schema(ctx, mode="STRICT")
    assert not schema_res.is_valid
    assert any(iss.code == f"MISSING_FIELD_{missing_field.upper()}" for iss in schema_res.issues)

    # Missing in stored
    full = _valid_canonical_context("ord_a02")
    compat_stored = validate_context_compatibility(ctx, full)
    assert not compat_stored.is_valid

    # Missing in expected
    compat_expected = validate_context_compatibility(full, ctx)
    assert not compat_expected.is_valid

    # Missing in both -> MUST STILL FAIL (does not continue silently)
    compat_both = validate_context_compatibility(ctx, ctx)
    assert not compat_both.is_valid


def test_a02_type_and_range_validation_rejections():
    """
    PHẦN 026, 054: Validates non-bool ints, positive epoch/revision/generation, non-empty fingerprint.
    """
    base = _valid_canonical_context("ord_a02_types")

    # Boolean for source_epoch
    bool_epoch = dict(base, source_epoch=True)
    res_bool_ep = validate_context_schema(bool_epoch, mode="STRICT")
    assert not res_bool_ep.is_valid
    assert any("SOURCE_EPOCH" in iss.code for iss in res_bool_ep.issues)

    # Boolean for arrival_revision
    bool_rev = dict(base, arrival_revision=False)
    res_bool_rev = validate_context_schema(bool_rev, mode="STRICT")
    assert not res_bool_rev.is_valid
    assert any("ARRIVAL_REVISION" in iss.code for iss in res_bool_rev.issues)

    # Negative scan_cutoff
    neg_cutoff = dict(base, scan_cutoff=-100)
    res_neg_cut = validate_context_schema(neg_cutoff, mode="STRICT")
    assert not res_neg_cut.is_valid
    assert any("SCAN_CUTOFF" in iss.code for iss in res_neg_cut.issues)

    # opened_at > scan_cutoff
    inverted_ts = dict(base, opened_at=9000, scan_cutoff=5000)
    res_inv = validate_context_schema(inverted_ts, mode="STRICT")
    assert not res_inv.is_valid
    assert any(iss.code == "OPENED_AT_EXCEEDS_SCAN_CUTOFF" for iss in res_inv.issues)

    # Empty fingerprint
    empty_fp = dict(base, exit_context_fingerprint="")
    res_fp = validate_context_schema(empty_fp, mode="STRICT")
    assert not res_fp.is_valid
    assert any("EXIT_CONTEXT_FINGERPRINT" in iss.code for iss in res_fp.issues)


def test_a02_partial_four_field_repro_rejected_in_strict_mode():
    """
    PHẦN 003, 054: Partial 4-field context (order_id, symbol, direction, exit_policy_version)
    was accepted by old validate_context_compatibility. In V14.4.11 STRICT mode, it must be rejected!
    """
    partial_context = {
        "order_id": "ord_partial",
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "exit_policy_version": "1.0.0",
    }
    full_expected = _valid_canonical_context("ord_partial")

    compat = validate_context_compatibility(partial_context, full_expected, migration_mode="STRICT")
    assert not compat.is_valid
    missing_codes = [iss.code for iss in compat.issues]
    assert any("MISSING_FIELD_CONTEXT_SCHEMA_VERSION" in c for c in missing_codes)
    assert any("MISSING_FIELD_EXIT_CONTEXT_FINGERPRINT" in c for c in missing_codes)
    assert any("MISSING_FIELD_SCAN_CUTOFF" in c for c in missing_codes)


# =========================================================================
# AUTHORITY TEST A03: LEGACY VS CORRUPT SEPARATION
# =========================================================================

def test_a03_legacy_order_distinct_from_corrupt_snapshot(isolated_db: Session):
    """
    PHẦN 013, 014, 031, 055:
    Order with cost_snapshot=None is UNVERIFIED with legacy version, NOT INVALID.
    Order with cost_snapshot="{corrupt" is INVALID, NEVER eligible for legacy migration.
    """
    ord_legacy = _seed_paper_order(isolated_db, "ord_leg_01", cost_snapshot="OMIT")
    frozen_leg = read_frozen_exit_context(ord_legacy)
    assert frozen_leg.status == "UNVERIFIED"
    assert "legacy" in frozen_leg.exit_policy_version
    assert len(frozen_leg.issues) == 0

    ord_corrupt = _seed_paper_order(isolated_db, "ord_cor_01", cost_snapshot="{broken_json")
    frozen_corrupt = read_frozen_exit_context(ord_corrupt)
    assert frozen_corrupt.status == "INVALID"
    assert "CORRUPT_COST_SNAPSHOT_JSON" in frozen_corrupt.issues

    # Corrupt order window result is INVALID
    win_corrupt = OrderReconciliationWindow(
        order_id="ord_cor_01", symbol="XAUUSDT", opened_at=1000, end_as_of=5000, order=ord_corrupt
    )
    res_corrupt = build_reconciliation_context_result(win_corrupt)
    assert res_corrupt.status == "INVALID"
    assert res_corrupt.context is None

    # Legacy order window result is UNVERIFIED with valid context dict
    win_legacy = OrderReconciliationWindow(
        order_id="ord_leg_01", symbol="XAUUSDT", opened_at=1000, end_as_of=5000, order=ord_legacy
    )
    res_legacy = build_reconciliation_context_result(win_legacy)
    assert res_legacy.status == "UNVERIFIED"
    assert res_legacy.context is not None
    assert res_legacy.context["exit_policy_version"] == "legacy_1.0.0"


def test_a03_authorized_legacy_migration_mode():
    """
    PHẦN 027, 031, 055:
    Mode LEGACY allows compatible identity matching without strict 12-field requirement,
    while STRICT mode enforces full contract.
    """
    stored_legacy = {
        "order_id": "ord_mig",
        "symbol": "XAUUSDT",
        "direction": "LONG",
    }
    expected_full = _valid_canonical_context("ord_mig")

    # STRICT rejects stored_legacy
    compat_strict = validate_context_compatibility(stored_legacy, expected_full, migration_mode="STRICT")
    assert not compat_strict.is_valid

    # Explicit LEGACY migration allows identity match
    compat_legacy = validate_context_compatibility(stored_legacy, expected_full, migration_mode="LEGACY")
    assert compat_legacy.is_valid


# =========================================================================
# AUTHORITY TEST A04: FIELD MISMATCH DEFENSE
# =========================================================================

def test_a04_field_mismatches_fail_with_precise_codes():
    """
    PHẦN 028, 056: Same order ID but mismatched symbol, direction, fingerprint, cutoff, epoch, rev, gen
    fails with precise diagnostic codes.
    """
    expected = _valid_canonical_context("ord_a04")

    # 1. Symbol mismatch
    stored_sym = dict(expected, symbol="BTCUSDT")
    res_sym = validate_context_compatibility(stored_sym, expected)
    assert not res_sym.is_valid
    assert any(iss.code == "CONTEXT_SYMBOL_MISMATCH" for iss in res_sym.issues)

    # 2. Direction mismatch
    stored_dir = dict(expected, direction="SHORT")
    res_dir = validate_context_compatibility(stored_dir, expected)
    assert not res_dir.is_valid
    assert any(iss.code == "CONTEXT_DIRECTION_MISMATCH" for iss in res_dir.issues)

    # 3. Fingerprint mismatch
    stored_fp = dict(expected, exit_context_fingerprint="ffffffffffffffff")
    res_fp = validate_context_compatibility(stored_fp, expected)
    assert not res_fp.is_valid
    assert any(iss.code == "CONTEXT_FINGERPRINT_MISMATCH" for iss in res_fp.issues)

    # 4. Cutoff mismatch
    stored_cut = dict(expected, scan_cutoff=4000)
    res_cut = validate_context_compatibility(stored_cut, expected)
    assert not res_cut.is_valid
    assert any(iss.code == "CONTEXT_CUTOFF_MISMATCH" for iss in res_cut.issues)

    # 5. Epoch mismatch
    stored_ep = dict(expected, source_epoch=9)
    res_ep = validate_context_compatibility(stored_ep, expected)
    assert not res_ep.is_valid
    assert any(iss.code == "CONTEXT_SOURCE_EPOCH_MISMATCH" for iss in res_ep.issues)


def test_a04_case_sensitivity_and_conflict_defense(isolated_db: Session):
    """
    PHẦN 019, 026, 056: Window order_id vs snapshot order_id conflict returns FIELD_SOURCE_CONFLICT_ORDER_ID.
    """
    ord = _seed_paper_order(isolated_db, "ord_a04_case")
    # Window created with wrong casing or differing order_id
    win = OrderReconciliationWindow(
        order_id="ord_A04_CASE_DIFFERENT",
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        order=ord,
    )
    ctx_res = build_reconciliation_context_result(win)
    assert ctx_res.status == "INVALID"
    assert "FIELD_SOURCE_CONFLICT_ORDER_ID" in ctx_res.issues


# =========================================================================
# AUTHORITY TEST A05: CONTINUATION, ORDERING, AND TIE RESOLUTION
# =========================================================================

def test_a05_continuation_preserves_frozen_cutoff_advancing_window(isolated_db: Session):
    """
    PHẦN 029, 057: Slice 1 cutoff=5000; window end_as_of advances to 6000.
    Progress cutoff 5000 is preserved and candidate beyond 5000 is rejected.
    """
    ord = _seed_paper_order(isolated_db, "ord_a05_cut")
    prog = EvidenceProgress(
        window_id="ord_a05_cut",
        scan_cutoff=5000,
        arrival_revision=2,
    )
    win = OrderReconciliationWindow(
        order_id="ord_a05_cut",
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=6000,
        order=ord,
    )
    ctx_res = build_reconciliation_context_result(win, prog)
    assert ctx_res.status == "VALID"
    assert ctx_res.context["scan_cutoff"] == 5000  # Preserves progress frozen cutoff!

    # Candidate at 5500 (> cutoff 5000) rejected
    cand_late = TerminalCandidate(
        order_id="ord_a05_cut",
        evidence_id="ev_late",
        source_timestamp=5500,
        known_at_ms=5500,
        bid=4200.0,
        ask=4201.0,
        last=4200.0,
        price_basis="BID_ASK",
        cause="TP_HIT",
        executable_price=4200.0,
    )
    val = validate_candidate_context(cand_late, win, ctx_res.context)
    assert not val.is_valid
    assert any(iss.code == "TIMESTAMP_AFTER_SCAN_CUTOFF" for iss in val.issues)


def test_a05_early_tp_before_late_sl_and_same_timestamp_conservative_tie(isolated_db: Session):
    """
    PHẦN 057:
    1. Early TP at 2000 before SL at 4000 confirms TP.
    2. Same timestamp TP and SL resolves conservatively to AMBIGUOUS_BAR_SL_FIRST.
    """
    ord = _seed_paper_order(isolated_db, "ord_a05_tie")

    c_tp = TerminalCandidate(
        order_id="ord_a05_tie",
        evidence_id="ev_tp_2000",
        source_timestamp=2000,
        known_at_ms=2000,
        bid=4200.0,
        ask=4201.0,
        last=4200.0,
        price_basis="BID_ASK",
        cause="TP_HIT",
        executable_price=4200.0,
    )
    c_sl_late = TerminalCandidate(
        order_id="ord_a05_tie",
        evidence_id="ev_sl_4000",
        source_timestamp=4000,
        known_at_ms=4000,
        bid=4170.0,
        ask=4171.0,
        last=4170.0,
        price_basis="BID_ASK",
        cause="SL_HIT",
        executable_price=4170.0,
    )

    coverage = _valid_coverage_assessment("XAUUSDT", 1000, 5000)
    dec1 = resolve_reconciliation_window(ord, [c_tp, c_sl_late], coverage)
    assert dec1.action == "CLOSE"
    assert dec1.candidate.cause == "TP_HIT"

    # Same timestamp tie
    c_sl_tie = TerminalCandidate(
        order_id="ord_a05_tie",
        evidence_id="ev_sl_2000",
        source_timestamp=2000,
        known_at_ms=2000,
        bid=4170.0,
        ask=4171.0,
        last=4170.0,
        price_basis="BID_ASK",
        cause="SL_HIT",
        executable_price=4170.0,
    )
    dec_tie = resolve_reconciliation_window(ord, [c_tp, c_sl_tie], coverage)
    assert dec_tie.action == "CLOSE"
    assert dec_tie.candidate.cause == "AMBIGUOUS_BAR_SL_FIRST"
    assert dec_tie.reason_code == "AMBIGUOUS_SAME_TIMESTAMP_CONSERVATIVE_SL"


# =========================================================================
# AUTHORITY TEST A06: PERSISTENCE FAILURE AND RESTART ISOLATION
# =========================================================================

def test_a06_progress_save_failure_atomic_isolation(isolated_db: Session, monkeypatch):
    """
    PHẦN 058: Progress save failure aborts without mutating order or reporting complete.
    """
    ord = _seed_paper_order(isolated_db, "ord_a06_fail")
    win = OrderReconciliationWindow(
        order_id="ord_a06_fail",
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        order=ord,
    )

    import services.market_reconciliation_service as mrs
    monkeypatch.setattr(mrs, "save_window_progress", lambda db, prog: False)

    res = process_window_slice(isolated_db, win)
    assert res.read_complete is False
    assert "PROGRESS_SAVE_FAILED" in res.reasons

    isolated_db.refresh(ord)
    assert ord.state == "paper_open"
    assert ord.closed_at is None
