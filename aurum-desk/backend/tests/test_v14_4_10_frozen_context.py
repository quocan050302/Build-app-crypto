"""
backend/tests/test_v14_4_10_frozen_context.py
V14.4.10 Frozen Context Validation Suite:
Covers:
- C01 / CTX01: Context mismatch repro (mismatched symbol/direction/policy must fail closed, zero mutation)
- C02 / CTX02: Fingerprint geometry validation (SL/TP change causes fingerprint mismatch)
- C03 / CTX03: Revision/generation/epoch validation
- C04 / CTX04: Frozen cutoff vs advancing window.end_as_of
- C05 / CTX05: Schema and legacy context compatibility
- C06 / CTX06: Duplicate evidence conflict detection
- C07 / CTX07: Direct callers fail-closed without valid context
- C08 / CTX08: Persist failure isolation
- C09 / CTX09: Read budget preservation
- C10 / CTX10: Earliest TP preservation across slices
"""

import json
import math
from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session

import models
from services.market_reconciliation_service import (
    CandidateAccumulator,
    CycleBudget,
    EvidenceProgress,
    OrderReconciliationWindow,
    ResolutionDecision,
    TerminalCandidate,
    assess_reconciliation_coverage,
    build_reconciliation_context,
    canonical_num,
    fingerprint_exit_context,
    load_window_progress,
    merge_candidate_accumulator,
    merge_candidate_accumulator_with_result,
    persist_reconciliation_evidence,
    persist_resolution_or_review,
    process_window_slice,
    read_frozen_exit_context,
    resolve_reconciliation_window,
    save_window_progress,
    validate_accumulator_context,
    validate_candidate_context,
    validate_context_compatibility,
    CoverageAssessment,
    CoverageStatus,
)
from services.trade_lifecycle_service import TradeLifecycleService


def _seed_paper_order(
    db: Session,
    order_id: str,
    state: str = "paper_open",
    opened_at: int = 1000,
    closed_at: int = 0,
    symbol: str = "XAUUSDT",
    direction: str = "LONG",
    entry_price: float = 4180.0,
    sl_price: float = 4170.0,
    tp_price: float = 4200.0,
    cost_snapshot: str = None,
) -> models.PaperOrder:
    ord = models.PaperOrder(
        id=str(order_id),
        instrument=symbol,
        direction=direction,
        state=state,
        quantity=0.1,
        initial_risk_usdt=2.0,
        planned_entry=entry_price,
        actual_entry=entry_price,
        stop_loss=sl_price,
        take_profit=tp_price,
        cost_snapshot=cost_snapshot,
        opened_at=opened_at,
        closed_at=closed_at if state == "closed" and closed_at > 0 else None,
        created_at=opened_at,
    )
    db.add(ord)
    db.commit()
    return ord


def _valid_continuous_coverage(symbol: str = "XAUUSDT", start_ms: int = 1000, end_ms: int = 5000) -> dict:
    return {
        "continuity_proof": {
            "source": symbol,
            "epoch": 1,
            "resolution": "TICK",
            "interval_start_ms": start_ms,
            "interval_end_ms": end_ms,
            "has_gap": False,
            "proof_references": ["proof_ref_1"],
        }
    }


def test_c01_ctx01_context_mismatch_repro_fails_closed(isolated_db: Session):
    """
    PHẦN 003, 071: Repro test for context mismatch.
    Order is LONG on XAUUSDT (entry 4180, SL 4170, TP 4200).
    Candidate matches order ID, epoch 1, REALTIME, ts 2000.
    Stored validation_context has direction SHORT, symbol BTCUSDT, exit_policy_version FOREIGN.
    In baseline: process_window_slice still resolves to CLOSE!
    After fix: must reject mismatch, return read_complete=False, decision=None, reasons contain mismatch.
    Order state and PnL must remain untouched.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id="ord_c01",
        state="paper_open",
        opened_at=1000,
        symbol="XAUUSDT",
        direction="LONG",
        entry_price=4180.0,
        sl_price=4170.0,
        tp_price=4200.0,
    )

    cand = TerminalCandidate(
        order_id="ord_c01",
        evidence_id="ev_tp",
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

    # Corrupt/foreign stored validation_context
    mismatched_context = {
        "order_id": "ord_c01",
        "window_id": "ord_c01",
        "symbol": "BTCUSDT",  # Mismatch!
        "direction": "SHORT",  # Mismatch!
        "opened_at": 1000,
        "scan_cutoff": 5000,
        "source_epoch": 1,
        "exit_policy_version": "FOREIGN_POLICY_99",  # Mismatch!
    }

    acc = CandidateAccumulator(
        window_id="ord_c01",
        earliest_source_ts=2000,
        earliest_candidates=[cand],
        validation_context=mismatched_context,
    )

    prog = EvidenceProgress(
        window_id="ord_c01",
        accumulator=acc,
        read_status="PENDING",
        scan_cutoff=5000,
    )

    win = OrderReconciliationWindow(
        order_id="ord_c01",
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )
    coverage = _valid_continuous_coverage("XAUUSDT", 1000, 5000)

    res = process_window_slice(isolated_db, win, progress=prog, coverage_source=coverage)

    # Must NOT close the order!
    assert res.decision is None or res.decision.action != "CLOSE"
    assert res.read_complete is False
    assert any("CONTEXT" in r or "MISMATCH" in r for r in res.reasons)

    # Order must remain paper_open with 0 mutation!
    isolated_db.refresh(ord)
    assert ord.state == "paper_open"
    assert ord.closed_at is None
    assert ord.realized_pnl_net is None or ord.realized_pnl_net == 0.0


def test_c02_ctx02_fingerprint_geometry_validation(isolated_db: Session):
    """
    PHẦN 048, 072: Tests fingerprint geometry calculation and mismatch detection.
    4200 vs 4200.0 yields identical canonical representation.
    Different SL/TP produces a distinct fingerprint.
    Stored fingerprint mismatch causes compatibility check to fail with CONTEXT_FINGERPRINT_MISMATCH.
    """
    # 1. Canonical numeric normalization
    assert canonical_num(4200) == "4200"
    assert canonical_num(4200.0) == "4200"
    assert canonical_num(4180.5) == "4180.5"

    ctx_base = {
        "context_schema_version": "1.0.0",
        "order_id": "ord_c02",
        "window_id": "ord_c02",
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "opened_at": 1000,
        "scan_cutoff": 5000,
        "source_epoch": 1,
        "query_generation": 1,
        "arrival_revision": 1,
        "entry_price": 4180.0,
        "stop_loss": 4170.0,
        "take_profit": 4200.0,
        "exit_policy_version": "1.0.0",
    }
    fp1 = fingerprint_exit_context(ctx_base)

    ctx_same_numeric = dict(ctx_base, entry_price=4180, take_profit=4200)
    fp2 = fingerprint_exit_context(ctx_same_numeric)
    assert fp1 == fp2  # Canonical numbers match

    # Alter SL -> fingerprint must differ
    ctx_diff_sl = dict(ctx_base, stop_loss=4175.0)
    fp3 = fingerprint_exit_context(ctx_diff_sl)
    assert fp1 != fp3

    # Compatibility check with wrong stored fingerprint
    stored = dict(ctx_base, exit_context_fingerprint=fp3)
    expected = dict(ctx_base, exit_context_fingerprint=fp1)
    compat = validate_context_compatibility(stored, expected)
    assert not compat.is_valid
    assert any(iss.code == "CONTEXT_FINGERPRINT_MISMATCH" for iss in compat.issues)


def test_c03_ctx03_revision_generation_epoch_validation():
    """
    PHẦN 073: Mismatched arrival revision, query generation, or source epoch fails closed.
    Matching values pass.
    """
    expected = {
        "context_schema_version": "1.0.0",
        "order_id": "ord_c03",
        "window_id": "ord_c03",
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "opened_at": 1000,
        "scan_cutoff": 5000,
        "exit_policy_version": "1.0.0",
        "exit_context_fingerprint": "fp_c03",
        "source_epoch": 1,
        "query_generation": 2,
        "arrival_revision": 3,
    }

    # Epoch mismatch
    stored_bad_epoch = dict(expected, source_epoch=2)
    res_epoch = validate_context_compatibility(stored_bad_epoch, expected)
    assert not res_epoch.is_valid
    assert any("EPOCH" in iss.code for iss in res_epoch.issues)

    # Generation mismatch
    stored_bad_gen = dict(expected, query_generation=99)
    res_gen = validate_context_compatibility(stored_bad_gen, expected)
    assert not res_gen.is_valid
    assert any("GENERATION" in iss.code for iss in res_gen.issues)

    # Revision mismatch
    stored_bad_rev = dict(expected, arrival_revision=1)
    res_rev = validate_context_compatibility(stored_bad_rev, expected)
    assert not res_rev.is_valid
    assert any("REVISION" in iss.code for iss in res_rev.issues)

    # All matching -> PASS
    res_ok = validate_context_compatibility(expected, expected)
    assert res_ok.is_valid


def test_c04_ctx04_frozen_cutoff_advancing_window(isolated_db: Session):
    """
    PHẦN 051, 074: Continuation preserves frozen scan cutoff even when window.end_as_of advances.
    Candidates beyond frozen cutoff are rejected.
    """
    ord = _seed_paper_order(isolated_db, "ord_c04", opened_at=1000)

    # Progress has frozen cutoff 5000
    prog = EvidenceProgress(
        window_id="ord_c04",
        scan_cutoff=5000,
        arrival_revision=2,
    )

    # Window current end_as_of has advanced to 8000
    win = OrderReconciliationWindow(
        order_id="ord_c04",
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=8000,
        order=ord,
    )

    ctx = build_reconciliation_context(win, prog)
    assert ctx["scan_cutoff"] == 5000  # Preserves progress frozen cutoff!

    # Candidate at 4500 (within cutoff) is valid
    cand_valid = TerminalCandidate(
        order_id="ord_c04",
        evidence_id="ev_valid",
        source_timestamp=4500,
        known_at_ms=4500,
        bid=4200.0,
        ask=4201.0,
        last=4200.0,
        price_basis="BID_ASK",
        cause="TP_HIT",
        executable_price=4200.0,
        epoch=1,
        source="REALTIME",
    )
    val_valid = validate_candidate_context(cand_valid, win, ctx)
    assert val_valid.is_valid

    # Candidate at 5500 (> cutoff 5000) is rejected
    cand_late = TerminalCandidate(
        order_id="ord_c04",
        evidence_id="ev_late",
        source_timestamp=5500,
        known_at_ms=5500,
        bid=4200.0,
        ask=4201.0,
        last=4200.0,
        price_basis="BID_ASK",
        cause="TP_HIT",
        executable_price=4200.0,
        epoch=1,
        source="REALTIME",
    )
    val_late = validate_candidate_context(cand_late, win, ctx)
    assert not val_late.is_valid
    assert any(iss.code == "TIMESTAMP_AFTER_SCAN_CUTOFF" for iss in val_late.issues)


def test_c05_ctx05_schema_and_legacy_context_compatibility(isolated_db: Session):
    """
    PHẦN 049, 075: Tests schema validation, legacy order fallback, and corrupt snapshot detection.
    """
    # 1. Empty stored context fails
    res_empty = validate_context_compatibility({}, {"order_id": "1"})
    assert not res_empty.is_valid
    assert any(iss.code == "EMPTY_VALIDATION_CONTEXT" for iss in res_empty.issues)

    # 2. Legacy order without cost_snapshot returns UNVERIFIED with legacy version
    legacy_ord = _seed_paper_order(isolated_db, "ord_legacy", opened_at=1000, cost_snapshot=None)
    frozen_res = read_frozen_exit_context(legacy_ord)
    assert frozen_res.status == "UNVERIFIED"
    assert "legacy" in frozen_res.exit_policy_version

    # 3. Corrupt JSON in cost_snapshot returns INVALID
    corrupt_ord = _seed_paper_order(isolated_db, "ord_corrupt", opened_at=1000, cost_snapshot="{corrupt_json")
    corrupt_res = read_frozen_exit_context(corrupt_ord)
    assert corrupt_res.status == "INVALID"
    assert "CORRUPT_COST_SNAPSHOT_JSON" in corrupt_res.issues

    # 4. Inverted geometry (LONG SL >= Entry) returns INVALID
    bad_geom_ord = _seed_paper_order(
        isolated_db,
        "ord_bad_geom",
        direction="LONG",
        entry_price=4180.0,
        sl_price=4190.0,  # SL above entry!
        tp_price=4200.0,
    )
    bad_geom_res = read_frozen_exit_context(bad_geom_ord)
    assert bad_geom_res.status == "INVALID"
    assert "LONG_SL_GTE_ENTRY" in bad_geom_res.issues


def test_c06_ctx06_duplicate_evidence_conflict_detection():
    """
    PHẦN 056, 076: Duplicate evidence with identical payload is idempotent.
    Differing price, cause, ts, basis, bid, ask, or source triggers conflict.
    """
    c1 = TerminalCandidate(
        order_id="ord_c06",
        evidence_id="ev_dup_1",
        source_timestamp=2000,
        known_at_ms=2000,
        bid=4200.0,
        ask=4201.0,
        last=4200.0,
        price_basis="BID_ASK",
        cause="TP_HIT",
        executable_price=4200.0,
        epoch=1,
        source="REALTIME",
    )

    ctx = {
        "context_schema_version": "1.0.0",
        "window_id": "ord_c06",
        "order_id": "ord_c06",
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "opened_at": 1000,
        "scan_cutoff": 5000,
        "source_epoch": 1,
        "query_generation": 1,
        "arrival_revision": 1,
        "exit_policy_version": "1.0.0",
        "exit_context_fingerprint": "fp_c06",
    }

    # First insertion
    res1 = merge_candidate_accumulator_with_result(None, [c1], ctx)
    assert res1.is_valid
    acc1 = res1.accumulator

    # Duplicate insertion with exact same payload -> idempotent, valid
    res_idempotent = merge_candidate_accumulator_with_result(acc1, [c1], ctx)
    assert res_idempotent.is_valid
    assert len(res_idempotent.accumulator.earliest_candidates) == 1

    # Conflicting candidate with different executable_price / bid
    c1_conflict = TerminalCandidate(
        order_id="ord_c06",
        evidence_id="ev_dup_1",  # Same evidence_id!
        source_timestamp=2000,
        known_at_ms=2000,
        bid=4210.0,  # Different!
        ask=4211.0,
        last=4210.0,
        price_basis="BID_ASK",
        cause="TP_HIT",
        executable_price=4210.0,  # Different!
        epoch=1,
        source="REALTIME",
    )
    res_conflict = merge_candidate_accumulator_with_result(acc1, [c1_conflict], ctx)
    assert not res_conflict.is_valid
    assert any("EVIDENCE_ID_CONFLICT" in r for r in res_conflict.reasons)


def test_c07_ctx07_direct_callers_fail_closed_without_valid_context(isolated_db: Session):
    """
    PHẦN 060, 077: Direct callers defense in resolve_reconciliation_window and persist_resolution_or_review.
    Foreign order ID or invalid prices reject without execution.
    """
    ord = _seed_paper_order(isolated_db, "ord_c07", opened_at=1000)

    # 1. Candidate with foreign order_id
    cand_foreign = TerminalCandidate(
        order_id="foreign_order_999",
        evidence_id="ev_foreign",
        source_timestamp=2000,
        known_at_ms=2000,
        bid=4200.0,
        ask=4201.0,
        last=4200.0,
        price_basis="BID_ASK",
        cause="TP_HIT",
        executable_price=4200.0,
    )
    coverage = CoverageAssessment(
        interval_start_ms=1000,
        interval_end_ms=3000,
        source="XAUUSDT",
        epoch=1,
        price_basis="BID_ASK",
        coverage_status=CoverageStatus.COMPLETE,
    )
    dec_foreign = resolve_reconciliation_window(ord, [cand_foreign], coverage)
    assert dec_foreign.action == "REVIEW_REQUIRED"
    assert dec_foreign.reason_code == "CANDIDATE_ORDER_ID_MISMATCH"

    # 2. Candidate with non-finite price
    cand_nan = TerminalCandidate(
        order_id="ord_c07",
        evidence_id="ev_nan",
        source_timestamp=2000,
        known_at_ms=2000,
        bid=4200.0,
        ask=4201.0,
        last=4200.0,
        price_basis="BID_ASK",
        cause="TP_HIT",
        executable_price=float("nan"),
    )
    dec_nan = resolve_reconciliation_window(ord, [cand_nan], coverage)
    assert dec_nan.action == "REVIEW_REQUIRED"
    assert dec_nan.reason_code == "CANDIDATE_PRICE_INVALID"

    # 3. persist_resolution_or_review boundary defense
    win = OrderReconciliationWindow(order_id="ord_c07", symbol="XAUUSDT", opened_at=1000, end_as_of=5000, order=ord)
    fake_close_dec = ResolutionDecision(action="CLOSE", candidate=cand_foreign)
    apply_res = persist_resolution_or_review(isolated_db, win, fake_close_dec)
    assert not apply_res.applied
    assert not apply_res.committed

    # Order remains open
    isolated_db.refresh(ord)
    assert ord.state == "paper_open"


def test_c08_ctx08_persist_failure_atomic_isolation(isolated_db: Session, monkeypatch):
    """
    PHẦN 078: When progress save fails, process_window_slice does not mutate state or complete.
    """
    ord = _seed_paper_order(isolated_db, "ord_c08", opened_at=1000)
    win = OrderReconciliationWindow(order_id="ord_c08", symbol="XAUUSDT", opened_at=1000, end_as_of=5000, order=ord)

    # Monkeypatch save_window_progress to fail
    import services.market_reconciliation_service as mrs
    monkeypatch.setattr(mrs, "save_window_progress", lambda db, prog: False)

    res = process_window_slice(isolated_db, win)
    assert res.read_complete is False
    assert "PROGRESS_SAVE_FAILED" in res.reasons

    isolated_db.refresh(ord)
    assert ord.state == "paper_open"


def test_c09_ctx09_budget_preservation_regression(isolated_db: Session):
    """
    PHẦN 079: Budget accounting is strictly preserved.
    Zero budget preflight check returns NO_BUDGET.
    """
    ord = _seed_paper_order(isolated_db, "ord_c09", opened_at=1000)
    win = OrderReconciliationWindow(order_id="ord_c09", symbol="XAUUSDT", opened_at=1000, end_as_of=5000, order=ord)

    # Exhausted budget: max_records = 0
    budget = CycleBudget(max_records=0, max_pages=1)
    res = process_window_slice(isolated_db, win, budget=budget)
    assert res.read_complete is False
    assert "NO_BUDGET" in res.reasons


def test_c10_ctx10_earliest_tp_and_audit_preservation(isolated_db: Session):
    """
    PHẦN 080: Earliest TP at 2000 before SL at 4000 is preserved as winning candidate.
    """
    c_tp = TerminalCandidate(
        order_id="ord_c10",
        evidence_id="ev_tp_early",
        source_timestamp=2000,
        known_at_ms=2000,
        bid=4200.0,
        ask=4201.0,
        last=4200.0,
        price_basis="BID_ASK",
        cause="TP_HIT",
        executable_price=4200.0,
        epoch=1,
        source="REALTIME",
    )
    c_sl = TerminalCandidate(
        order_id="ord_c10",
        evidence_id="ev_sl_late",
        source_timestamp=4000,
        known_at_ms=4000,
        bid=4170.0,
        ask=4171.0,
        last=4170.0,
        price_basis="BID_ASK",
        cause="SL_HIT",
        executable_price=4170.0,
        epoch=1,
        source="REALTIME",
    )

    ctx = {
        "context_schema_version": "1.0.0",
        "window_id": "ord_c10",
        "order_id": "ord_c10",
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "opened_at": 1000,
        "scan_cutoff": 5000,
        "source_epoch": 1,
        "query_generation": 1,
        "arrival_revision": 1,
        "exit_policy_version": "1.0.0",
        "exit_context_fingerprint": "fp_c10",
    }

    # Pass in both candidates (order doesn't matter)
    acc = merge_candidate_accumulator(None, [c_sl, c_tp], ctx)
    assert acc.earliest_source_ts == 2000
    assert len(acc.earliest_candidates) == 1
    assert acc.earliest_candidates[0].evidence_id == "ev_tp_early"

    # Resolving window confirms earliest TP
    ord = _seed_paper_order(isolated_db, "ord_c10", opened_at=1000)
    coverage = CoverageAssessment(
        interval_start_ms=1000,
        interval_end_ms=5000,
        source="XAUUSDT",
        epoch=1,
        price_basis="BID_ASK",
        coverage_status=CoverageStatus.COMPLETE,
    )
    dec = resolve_reconciliation_window(ord, acc.earliest_candidates, coverage)
    assert dec.action == "CLOSE"
    assert dec.candidate.cause == "TP_HIT"
    assert dec.candidate.executable_price == 4200.0
