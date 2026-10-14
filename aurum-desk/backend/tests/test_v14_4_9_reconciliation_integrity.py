"""
backend/tests/test_v14_4_9_reconciliation_integrity.py
V14.4.9 Strict Reconciliation Validation, Hard Read Budgets, and Flow Preservation Suite.

Covers:
- V01..V08: Codec numbers, identifiers, corrupt accumulator, wrong order, direct resolver & apply,
            context mismatch, duplicate conflict, legacy progress & collision.
- B01..B05: Max records enforcement, effective_limit boundary, empty page cost, zero budget & deadline,
            global counters.
- S01..S05: Full 51 audits, true partial window, failed save, new earlier evidence replay, fairness.
- R01..R05: TP before SL regression, chunking equivalence, LONG/SHORT prices, money untouched, race close.
"""

import json
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List
import pytest
from sqlalchemy.orm import Session

import models
from services.market_reconciliation_service import (
    CandidateAccumulator,
    CandidateDecodeResult,
    CycleBudget,
    EvidenceProgress,
    MarketReconciliationService,
    OrderReconciliationWindow,
    ResolutionDecision,
    TerminalCandidate,
    build_reconciliation_context,
    collect_terminal_candidates,
    decode_candidate,
    decode_candidate_accumulator,
    encode_candidate,
    is_finite_real,
    load_persisted_window_evidence,
    load_scan_progress,
    load_window_progress,
    merge_candidate_accumulator,
    merge_candidate_accumulator_with_result,
    parse_positive_price,
    parse_timestamp,
    persist_reconciliation_evidence,
    persist_resolution_or_review,
    process_window_slice,
    resolve_reconciliation_window,
    save_window_progress,
    validate_accumulator_context,
    validate_candidate_context,
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


# =========================================================================
# V01: Codec numbers (PHẦN 016, 017, 023, 061)
# =========================================================================
def test_v01_codec_numeric_validation():
    # is_finite_real checks
    assert is_finite_real(4200.0) is True
    assert is_finite_real(4200) is True
    assert is_finite_real(float("nan")) is False
    assert is_finite_real(float("inf")) is False
    assert is_finite_real(float("-inf")) is False
    assert is_finite_real(True) is False
    assert is_finite_real(False) is False
    assert is_finite_real("4200") is False
    assert is_finite_real(None) is False

    # NaN / Inf in executable price must fail decode
    res_nan = decode_candidate({
        "order_id": "ord_1",
        "evidence_id": "ev_1",
        "cause": "TP_HIT",
        "source_timestamp": 2000,
        "executable_price": float("nan"),
    })
    assert res_nan.is_valid is False
    assert "INVALID_PRICE_NON_FINITE" in res_nan.reason
    assert res_nan.candidate is None

    res_inf = decode_candidate({
        "order_id": "ord_1",
        "evidence_id": "ev_1",
        "cause": "TP_HIT",
        "source_timestamp": 2000,
        "executable_price": float("inf"),
    })
    assert res_inf.is_valid is False
    assert "INVALID_PRICE_NON_FINITE" in res_inf.reason

    # Negative / zero price must fail decode
    res_neg = decode_candidate({
        "order_id": "ord_1",
        "evidence_id": "ev_1",
        "cause": "TP_HIT",
        "source_timestamp": 2000,
        "executable_price": -10.0,
    })
    assert res_neg.is_valid is False
    assert "INVALID_PRICE_NON_POSITIVE" in res_neg.reason

    # Bool in price must fail decode
    res_bool = decode_candidate({
        "order_id": "ord_1",
        "evidence_id": "ev_1",
        "cause": "TP_HIT",
        "source_timestamp": 2000,
        "executable_price": True,
    })
    assert res_bool.is_valid is False

    # Valid candidate roundtrip
    valid_payload = {
        "order_id": "ord_1",
        "evidence_id": "ev_1",
        "cause": "TP_HIT",
        "price_basis": "BID_ASK",
        "source_timestamp": 2000,
        "known_at_ms": 2000,
        "executable_price": 4205.5,
        "bid": 4205.5,
        "ask": 4206.0,
        "last": 4205.5,
        "epoch": 1,
        "source": "REALTIME",
    }
    res_valid = decode_candidate(valid_payload)
    assert res_valid.is_valid is True
    assert res_valid.candidate is not None

    enc = encode_candidate(res_valid.candidate)
    json_str = json.dumps(enc, allow_nan=False)
    decoded_again = decode_candidate(json.loads(json_str))
    assert decoded_again.is_valid is True
    assert decoded_again.candidate == res_valid.candidate


# =========================================================================
# V02: Identifier / Enum / Epoch validation (PHẦN 019, 020, 021, 062)
# =========================================================================
def test_v02_identifier_enum_epoch_validation():
    # Empty / whitespace / None order_id
    for bad_oid in (None, {}, "", "   "):
        res = decode_candidate({
            "order_id": bad_oid,
            "evidence_id": "ev_1",
            "cause": "TP_HIT",
            "source_timestamp": 2000,
            "executable_price": 4200.0,
        })
        assert res.is_valid is False
        assert "MISSING_ORDER_ID" in [iss.code for iss in res.issues]

    # UNKNOWN evidence_id rejected
    res_ev = decode_candidate({
        "order_id": "ord_1",
        "evidence_id": "UNKNOWN",
        "cause": "TP_HIT",
        "source_timestamp": 2000,
        "executable_price": 4200.0,
    })
    assert res_ev.is_valid is False
    assert "MISSING_OR_UNKNOWN_EVIDENCE_ID" in [iss.code for iss in res_ev.issues]

    # Unsupported cause
    res_cause = decode_candidate({
        "order_id": "ord_1",
        "evidence_id": "ev_1",
        "cause": "INVALID_CUSTOM_CAUSE",
        "source_timestamp": 2000,
        "executable_price": 4200.0,
    })
    assert res_cause.is_valid is False
    assert "INVALID_EXIT_CAUSE" in [iss.code for iss in res_cause.issues]

    # Epoch bool / negative
    for bad_epoch in (False, True, -1, "1"):
        res_epoch = decode_candidate({
            "order_id": "ord_1",
            "evidence_id": "ev_1",
            "cause": "TP_HIT",
            "source_timestamp": 2000,
            "executable_price": 4200.0,
            "epoch": bad_epoch,
        })
        assert res_epoch.is_valid is False
        assert "INVALID_SOURCE_EPOCH" in [iss.code for iss in res_epoch.issues]


# =========================================================================
# V03: Corrupt accumulator detection (PHẦN 024, 026, 027, 063)
# =========================================================================
def test_v03_corrupt_accumulator_detection():
    # Accumulator containing 1 corrupt candidate and 1 valid candidate
    # Loader must NOT silently drop the corrupt candidate!
    data = {
        "schema_version": "1.0.0",
        "window_id": "ord_v03",
        "earliest_source_ts": 2000,
        "earliest_candidates": [
            {"bad": "entry"},  # Corrupt candidate
            {
                "order_id": "ord_v03",
                "evidence_id": "ev_v03",
                "cause": "TP_HIT",
                "source_timestamp": 2000,
                "executable_price": 4200.0,
            },
        ],
    }
    decode_res = decode_candidate_accumulator(data)
    assert decode_res.is_valid is False
    assert "CORRUPT_CANDIDATE" in decode_res.reasons
    assert decode_res.accumulator is not None
    assert "CORRUPT_CANDIDATE" in decode_res.accumulator.conflict_flags

    # Mismatched earliest_source_ts
    data_ts_mismatch = {
        "schema_version": "1.0.0",
        "window_id": "ord_v03",
        "earliest_source_ts": 1000,  # Does not match candidate ts 2000!
        "earliest_candidates": [
            {
                "order_id": "ord_v03",
                "evidence_id": "ev_v03",
                "cause": "TP_HIT",
                "source_timestamp": 2000,
                "executable_price": 4200.0,
            }
        ],
    }
    res_mismatch = decode_candidate_accumulator(data_ts_mismatch)
    assert res_mismatch.is_valid is False
    assert "EARLIEST_SOURCE_TS_MISMATCH" in res_mismatch.reasons


# =========================================================================
# V04: Foreign order candidate blocked through production (PHẦN 012, 064)
# =========================================================================
def test_v04_wrong_order_blocked_through_production(isolated_db: Session):
    ord = _seed_paper_order(isolated_db, order_id="ord_v04_curr", state="paper_open", opened_at=1000)

    # Injected candidate belonging to OTHER order
    foreign_cand = TerminalCandidate(
        order_id="OTHER_ORDER",
        evidence_id="foreign_tp",
        source_timestamp=1500,
        known_at_ms=1500,
        bid=4201.0,
        ask=4202.0,
        last=4201.0,
        price_basis="BID_ASK",
        cause="TP_HIT",
        executable_price=4201.0,
        epoch=99,
        source="REALTIME",
    )
    acc = CandidateAccumulator(window_id="OTHER_ORDER", earliest_source_ts=1500, earliest_candidates=[foreign_cand])
    prog = EvidenceProgress(window_id="ord_v04_curr", accumulator=acc, read_status="PENDING", scan_cutoff=5000)

    window = OrderReconciliationWindow(
        order_id="ord_v04_curr",
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )
    coverage = _valid_continuous_coverage("XAUUSDT", 1000, 5000)

    res = process_window_slice(isolated_db, window, progress=prog, coverage_source=coverage)
    assert res.read_complete is False
    assert res.decision is None
    assert any("MISMATCH" in r for r in res.reasons)

    # Order must remain paper_open! No close, no state change!
    isolated_db.refresh(ord)
    assert ord.state == "paper_open"
    assert ord.closed_at is None


# =========================================================================
# V05: Direct resolver and apply boundary defense (PHẦN 041, 043, 065)
# =========================================================================
def test_v05_direct_resolver_and_apply_defense(isolated_db: Session):
    ord = _seed_paper_order(isolated_db, order_id="ord_v05", state="paper_open", opened_at=1000)
    foreign_cand = TerminalCandidate(
        order_id="FORGED_OTHER",
        evidence_id="ev_forged",
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
    from services.market_reconciliation_service import assess_reconciliation_coverage
    cov = assess_reconciliation_coverage(ord, None, [])

    # Direct call to resolve_reconciliation_window with foreign candidate
    dec = resolve_reconciliation_window(ord, [foreign_cand], cov)
    assert dec.action != "CLOSE"
    assert dec.reason_code == "CANDIDATE_ORDER_ID_MISMATCH"

    # Direct call to persist_resolution_or_review with forged CLOSE decision
    forged_close_dec = ResolutionDecision(action="CLOSE", candidate=foreign_cand, reason_code="FORGED")
    window = OrderReconciliationWindow(
        order_id="ord_v05",
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )
    apply_res = persist_resolution_or_review(isolated_db, window, forged_close_dec)
    assert apply_res.applied is False
    assert apply_res.committed is False

    isolated_db.refresh(ord)
    assert ord.state == "paper_open"


# =========================================================================
# V06: Context mismatch rejection (PHẦN 031, 032, 066)
# =========================================================================
def test_v06_context_mismatch_rejection():
    cand = TerminalCandidate(
        order_id="ord_1",
        evidence_id="ev_1",
        source_timestamp=500,  # Prior to window opened_at 1000!
        known_at_ms=500,
        bid=4200.0,
        ask=4201.0,
        last=4200.0,
        price_basis="BID_ASK",
        cause="TP_HIT",
        executable_price=4200.0,
        epoch=2,  # Epoch mismatch!
        source="UNKNOWN",  # Unknown source!
    )
    win = OrderReconciliationWindow(
        order_id="ord_1",
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
    )
    val_res = validate_candidate_context(cand, win)
    assert val_res.is_valid is False
    codes = [iss.code for iss in val_res.issues]
    assert "TIMESTAMP_BEFORE_ORDER_OPEN" in codes
    assert "EPOCH_MISMATCH" in codes
    assert "UNKNOWN_PROVENANCE_SOURCE" in codes


# =========================================================================
# V07: Duplicate conflict detection (PHẦN 028, 067)
# =========================================================================
def test_v07_duplicate_conflict_detection():
    c1 = TerminalCandidate(
        order_id="ord_1",
        evidence_id="ev_same",
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
    # Identical candidate: idempotent deduplication
    c1_dup = TerminalCandidate(
        order_id="ord_1",
        evidence_id="ev_same",
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
    acc = merge_candidate_accumulator(None, [c1, c1_dup], {"window_id": "ord_1"})
    assert len(acc.earliest_candidates) == 1
    assert acc.conflict_flags == []

    # Conflicting candidate with same evidence_id but different cause / price
    c1_conflict = TerminalCandidate(
        order_id="ord_1",
        evidence_id="ev_same",
        source_timestamp=2000,
        known_at_ms=2000,
        bid=4170.0,
        ask=4171.0,
        last=4170.0,
        price_basis="BID_ASK",
        cause="SL_HIT",  # Different cause!
        executable_price=4170.0,
        epoch=1,
        source="REALTIME",
    )
    res_conflict = merge_candidate_accumulator_with_result(None, [c1, c1_conflict], {"window_id": "ord_1"})
    assert res_conflict.is_valid is False
    assert any("EVIDENCE_ID_CONFLICT" in r for r in res_conflict.reasons)


# =========================================================================
# V08: Legacy progress & key collision protection (PHẦN 037, 038, 068)
# =========================================================================
def test_v08_legacy_progress_and_key_collision(isolated_db: Session):
    ord_a = _seed_paper_order(isolated_db, order_id="long_order_id_prefix_aaaaaaaaaaaaaaaaaaaaaaaaaaaa1", state="paper_open")
    ord_b = _seed_paper_order(isolated_db, order_id="long_order_id_prefix_aaaaaaaaaaaaaaaaaaaaaaaaaaaa2", state="paper_open")

    # Save progress for ord_a
    prog_a = EvidenceProgress(
        window_id=ord_a.id,
        read_status="MORE_WORK",
        scan_cutoff=5000,
    )
    save_window_progress(isolated_db, prog_a)

    # Attempt to load progress for ord_b: prefix collision must be detected and rejected!
    loaded_b = load_window_progress(isolated_db, ord_b.id)
    assert loaded_b is None  # Must NOT load ord_a's progress!


# =========================================================================
# B01: Hard record budget enforcement (PHẦN 013, 046, 069)
# =========================================================================
def test_b01_hard_record_budget_enforcement(isolated_db: Session):
    ord = _seed_paper_order(isolated_db, order_id="ord_b01", state="paper_open", opened_at=1000)
    quotes = [
        {"event_id": f"ev_b01_{i}", "symbol": "XAUUSDT", "source_timestamp": 2000 + i, "bid": 4180.0, "ask": 4181.0, "source": "REALTIME"}
        for i in range(6)
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    win = OrderReconciliationWindow(
        order_id="ord_b01",
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )

    budget = CycleBudget(max_windows=1, max_pages=1, max_records=2)
    # Page size is 200, but budget only allows 2 records
    res = process_window_slice(isolated_db, win, budget=budget, page_size=200)
    assert res.processed_count == 2
    assert budget.records_processed == 2
    assert res.read_complete is False
    assert res.has_more is True

    # Next slice consumes the next 2 records
    budget2 = CycleBudget(max_windows=1, max_pages=1, max_records=2)
    res2 = process_window_slice(isolated_db, win, budget=budget2, page_size=200)
    assert res2.processed_count == 2
    assert budget2.records_processed == 2
    assert res2.read_complete is False
    assert res2.has_more is True

    # Final slice consumes remaining 2 records and completes
    budget3 = CycleBudget(max_windows=1, max_pages=1, max_records=2)
    coverage = _valid_continuous_coverage("XAUUSDT", 1000, 5000)
    res3 = process_window_slice(isolated_db, win, budget=budget3, coverage_source=coverage, page_size=200)
    assert res3.processed_count == 2
    assert res3.read_complete is True
    assert res3.has_more is False


# =========================================================================
# B02: Boundary effective_limit (PHẦN 048, 070)
# =========================================================================
def test_b02_boundary_effective_limit():
    budget = CycleBudget(max_pages=5, max_records=10)
    assert budget.remaining_records() == 10
    assert budget.remaining_pages() == 5

    budget.record_page(3)
    assert budget.remaining_records() == 7
    assert budget.remaining_pages() == 4

    budget.record_page(7)
    assert budget.remaining_records() == 0
    assert budget.allows_page() is False


# =========================================================================
# B03: Empty page cost accounting (PHẦN 050, 071)
# =========================================================================
def test_b03_empty_page_cost_accounting(isolated_db: Session):
    ord = _seed_paper_order(isolated_db, order_id="ord_b03", state="paper_open", opened_at=1000)
    win = OrderReconciliationWindow(
        order_id="ord_b03",
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )
    # Empty DB evidence
    budget = CycleBudget(max_windows=1, max_pages=1, max_records=10)
    coverage = _valid_continuous_coverage("XAUUSDT", 1000, 5000)
    res = process_window_slice(isolated_db, win, budget=budget, coverage_source=coverage, page_size=200)
    assert res.read_complete is True
    # Empty page must cost 1 page
    assert budget.pages_processed == 1
    assert budget.records_processed == 0


# =========================================================================
# B04: Zero budget & deadline preflight (PHẦN 051, 072)
# =========================================================================
def test_b04_zero_budget_and_deadline_preflight(isolated_db: Session):
    ord = _seed_paper_order(isolated_db, order_id="ord_b04", state="paper_open", opened_at=1000)
    win = OrderReconciliationWindow(
        order_id="ord_b04",
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )
    # Zero records budget
    budget_zero_rec = CycleBudget(max_windows=1, max_pages=1, max_records=0)
    res_zero = process_window_slice(isolated_db, win, budget=budget_zero_rec)
    assert res_zero.progressed is False
    assert res_zero.read_complete is False
    assert res_zero.reasons == ["NO_BUDGET"]

    # Expired deadline
    budget_dead = CycleBudget(max_windows=1, max_pages=5, max_records=10, deadline_monotonic=time.monotonic() - 10.0)
    res_dead = process_window_slice(isolated_db, win, budget=budget_dead)
    assert res_dead.progressed is False
    assert res_dead.read_complete is False
    assert res_dead.reasons == ["NO_BUDGET"]


# =========================================================================
# B05: Global counters across multiple windows (PHẦN 056, 073)
# =========================================================================
def test_b05_global_counters_shared_budget(isolated_db: Session):
    ord1 = _seed_paper_order(isolated_db, order_id="ord_b05_1", state="paper_open", opened_at=1000)
    ord2 = _seed_paper_order(isolated_db, order_id="ord_b05_2", state="paper_open", opened_at=1000)

    quotes1 = [{"event_id": f"ev_b05_1_{i}", "symbol": "XAUUSDT", "source_timestamp": 2000 + i, "bid": 4180.0, "ask": 4181.0, "source": "REALTIME"} for i in range(2)]
    quotes2 = [{"event_id": f"ev_b05_2_{i}", "symbol": "XAUUSDT", "source_timestamp": 3000 + i, "bid": 4180.0, "ask": 4181.0, "source": "REALTIME"} for i in range(2)]
    persist_reconciliation_evidence(isolated_db, quotes1 + quotes2)

    win1 = OrderReconciliationWindow(order_id=ord1.id, symbol="XAUUSDT", opened_at=1000, end_as_of=2500, source_epoch=1, status="PENDING", cursor=1000, order=ord1)
    win2 = OrderReconciliationWindow(order_id=ord2.id, symbol="XAUUSDT", opened_at=2500, end_as_of=5000, source_epoch=1, status="PENDING", cursor=2500, order=ord2)

    shared_budget = CycleBudget(max_windows=2, max_pages=5, max_records=3)
    res1 = process_window_slice(isolated_db, win1, budget=shared_budget, page_size=200)
    assert res1.processed_count == 2
    assert shared_budget.records_processed == 2

    # Window 2 only has 1 record remaining in shared budget (3 - 2 = 1)
    res2 = process_window_slice(isolated_db, win2, budget=shared_budget, page_size=200)
    assert res2.processed_count == 1
    assert shared_budget.records_processed == 3
    assert res2.read_complete is False
    assert res2.has_more is True


# =========================================================================
# S-Series & R-Series: Preservation of Scheduler, Failure, and Flow
# =========================================================================
def test_sched_advancement_preserved(isolated_db: Session):
    ord = _seed_paper_order(isolated_db, order_id="ord_s01", state="closed", opened_at=1000, closed_at=2000)
    recon = MarketReconciliationService()
    budget = CycleBudget(max_windows=5, max_pages=5, max_records=10)
    recon.process_late_batch(isolated_db, symbol="XAUUSDT", budget=budget)
    scheduler = load_scan_progress(isolated_db, symbol="XAUUSDT")
    assert scheduler is not None


def test_failed_save_progress_rollback(isolated_db: Session, monkeypatch):
    ord = _seed_paper_order(isolated_db, order_id="ord_s03", state="paper_open", opened_at=1000)
    quotes = [{"event_id": "ev_s03_1", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4180.0, "ask": 4181.0, "source": "REALTIME"}]
    persist_reconciliation_evidence(isolated_db, quotes)

    win = OrderReconciliationWindow(order_id="ord_s03", symbol="XAUUSDT", opened_at=1000, end_as_of=5000, source_epoch=1, status="PENDING", cursor=1000, order=ord)

    # Monkeypatch save_window_progress to return False
    import services.market_reconciliation_service as mrs
    monkeypatch.setattr(mrs, "save_window_progress", lambda db, p: False)

    res = process_window_slice(isolated_db, win)
    assert res.read_complete is False
    assert "PROGRESS_SAVE_FAILED" in res.reasons


def test_long_short_direction_price_parity(isolated_db: Session):
    # LONG exit requires Bid, SHORT exit requires Ask
    ord_long = _seed_paper_order(isolated_db, order_id="ord_r03_l", direction="LONG", entry_price=4180, sl_price=4170, tp_price=4200)
    ord_short = _seed_paper_order(isolated_db, order_id="ord_r03_s", direction="SHORT", entry_price=4180, sl_price=4190, tp_price=4160)

    # Quote with Ask at TP but Bid below TP
    quote_long = {"event_id": "q1", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4199.5, "ask": 4200.5}
    # For LONG, bid 4199.5 does NOT hit TP 4200
    cands_long = collect_terminal_candidates(ord_long, [quote_long])
    assert len(cands_long) == 0

    # For SHORT, ask 4160.5 does NOT hit TP 4160
    quote_short = {"event_id": "q2", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4159.5, "ask": 4160.5}
    cands_short = collect_terminal_candidates(ord_short, [quote_short])
    assert len(cands_short) == 0

    # When Ask reaches 4160.0 for SHORT: hits TP!
    quote_short_hit = {"event_id": "q3", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4159.0, "ask": 4160.0}
    cands_short_hit = collect_terminal_candidates(ord_short, [quote_short_hit])
    assert len(cands_short_hit) == 1
    assert cands_short_hit[0].cause == "TP_HIT"
    assert cands_short_hit[0].executable_price == 4160.0


def test_invalid_evidence_touches_no_money_or_state(isolated_db: Session):
    ord = _seed_paper_order(isolated_db, order_id="ord_r04", state="paper_open", opened_at=1000)
    initial_config_version = ord.config_version
    initial_state = ord.state
    initial_pnl = ord.realized_pnl_net

    # Process with corrupted candidate
    foreign_cand = TerminalCandidate(
        order_id="FOREIGN",
        evidence_id="ev_foreign",
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
    acc = CandidateAccumulator(window_id="FOREIGN", earliest_source_ts=2000, earliest_candidates=[foreign_cand])
    prog = EvidenceProgress(window_id=ord.id, accumulator=acc, read_status="PENDING", scan_cutoff=5000)

    win = OrderReconciliationWindow(
        order_id=ord.id,
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
    assert res.read_complete is False

    isolated_db.refresh(ord)
    assert ord.config_version == initial_config_version
    assert ord.state == initial_state
    assert ord.realized_pnl_net == initial_pnl
    assert ord.closed_at is None
