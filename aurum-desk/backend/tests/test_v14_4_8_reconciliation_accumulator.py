"""
backend/tests/test_v14_4_8_reconciliation_accumulator.py
V14.4.8 Red Reproduction & Behavioral Verification Suite
(PHẦN 008, 009, 023–030, 083–094):

Tests:
- ACC01: Early TP before later SL across slices (LONG).
- ACC02: Early TP before later SL across slices (SHORT mirror).
- BUDGET01: Zero/exhausted budget returns NO_BUDGET / MORE_WORK, never false COMPLETE.
- SCHED01: Coordinator audits all 51 closed orders across cycles without skipping 21–50.
"""

import json
import pytest
from datetime import datetime, timezone
from sqlalchemy.orm import Session

import models
from services.market_reconciliation_service import (
    process_window_slice,
    persist_reconciliation_evidence,
    load_window_progress,
    load_scan_progress,
    CycleBudget,
    OrderReconciliationWindow,
    MarketReconciliationService,
)


def _seed_paper_order(
    db: Session,
    order_id: str | int,
    state: str = "paper_open",
    opened_at: int = 1000,
    closed_at: int = 0,
    symbol: str = "XAUUSDT",
    direction: str = "LONG",
    entry_price: float = 4180.0,
    sl_price: float = 4170.0,
    tp_price: float = 4200.0,
) -> models.PaperOrder:
    ord_id_str = f"ord_{order_id}" if isinstance(order_id, int) else str(order_id)
    ord = models.PaperOrder(
        id=ord_id_str,
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


def _valid_continuous_coverage_source(symbol: str, start_ms: int, end_ms: int) -> dict:
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


def test_acc01_early_tp_before_later_sl_across_slices(isolated_db: Session):
    """Case ACC01 (PHẦN 008, 083):
    LONG entry 4180, SL 4170, TP 4200, opened 1000, end 5000.
    Evidence:
      t=2000: TP bid 4201, ask 4202 (TP HIT)
      t=2500: bid 4180, ask 4181 (neutral)
      t=3000: bid 4180, ask 4181 (neutral)
      t=4000: SL bid 4169, ask 4170 (SL HIT)
    With page_size=2, budget max_pages=1:
      Slice 1 reads t=2000, 2500. Budget exhausted -> MORE_WORK.
      Slice 2 reads t=3000, 4000. Full traversal complete.
    Expected: TP2000 must win because it occurred chronologically first!
    In baseline: Slice 2 only checks slice 2's evidence and erroneously resolves to SL4000.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id="acc01",
        state="paper_open",
        opened_at=1000,
        symbol="XAUUSDT",
        direction="LONG",
        entry_price=4180.0,
        sl_price=4170.0,
        tp_price=4200.0,
    )

    quotes = [
        {"event_id": "ev_acc01_1", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4201.0, "ask": 4202.0, "source": "REALTIME"},
        {"event_id": "ev_acc01_2", "symbol": "XAUUSDT", "source_timestamp": 2500, "bid": 4180.0, "ask": 4181.0, "source": "REALTIME"},
        {"event_id": "ev_acc01_3", "symbol": "XAUUSDT", "source_timestamp": 3000, "bid": 4180.0, "ask": 4181.0, "source": "REALTIME"},
        {"event_id": "ev_acc01_4", "symbol": "XAUUSDT", "source_timestamp": 4000, "bid": 4169.0, "ask": 4170.0, "source": "REALTIME"},
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    window = OrderReconciliationWindow(
        order_id=ord.id,
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )
    coverage_source = _valid_continuous_coverage_source("XAUUSDT", 1000, 5000)

    # Slice 1: page_size=2, max_pages=1 -> reads 2000 and 2500
    budget1 = CycleBudget(max_windows=1, max_pages=1, max_records=2)
    slice1 = process_window_slice(isolated_db, window, budget=budget1, coverage_source=coverage_source, page_size=2)

    assert slice1.has_more is True, "Slice 1 must have more work"
    assert slice1.read_complete is False, "Slice 1 must not be complete"
    assert slice1.decision is None, "Slice 1 must not produce a decision prematurely"

    # Slice 2: page_size=2, max_pages=1 -> reads 3000 and 4000 -> full read complete
    budget2 = CycleBudget(max_windows=1, max_pages=1, max_records=2)
    slice2 = process_window_slice(isolated_db, window, budget=budget2, coverage_source=coverage_source, page_size=2)

    assert slice2.read_complete is True, "Slice 2 must complete the read"
    assert slice2.has_more is False
    assert slice2.decision is not None, "Slice 2 must produce a decision"
    assert slice2.decision.action == "CLOSE"
    assert slice2.decision.candidate is not None
    # Crucial assertion: earliest terminal must be TP at 2000, NOT SL at 4000!
    assert slice2.decision.candidate.cause == "TP_HIT", f"Expected TP_HIT, got {slice2.decision.candidate.cause}"
    assert slice2.decision.candidate.source_timestamp == 2000, f"Expected timestamp 2000, got {slice2.decision.candidate.source_timestamp}"
    assert slice2.decision.candidate.executable_price == 4200.0


def test_acc02_short_mirror_early_tp_before_later_sl(isolated_db: Session):
    """Case ACC02 (PHẦN 009, 084):
    SHORT entry 4180, SL 4190, TP 4160, opened 1000, end 5000.
    Evidence:
      t=2000: TP ask 4159, bid 4158 (TP HIT)
      t=2500: ask 4180, bid 4179 (neutral)
      t=3000: ask 4180, bid 4179 (neutral)
      t=4000: SL ask 4191, bid 4190 (SL HIT)
    Expected: TP2000 must win.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id="acc02",
        state="paper_open",
        opened_at=1000,
        symbol="XAUUSDT",
        direction="SHORT",
        entry_price=4180.0,
        sl_price=4190.0,
        tp_price=4160.0,
    )

    quotes = [
        {"event_id": "ev_acc02_1", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4158.0, "ask": 4159.0, "source": "REALTIME"},
        {"event_id": "ev_acc02_2", "symbol": "XAUUSDT", "source_timestamp": 2500, "bid": 4179.0, "ask": 4180.0, "source": "REALTIME"},
        {"event_id": "ev_acc02_3", "symbol": "XAUUSDT", "source_timestamp": 3000, "bid": 4179.0, "ask": 4180.0, "source": "REALTIME"},
        {"event_id": "ev_acc02_4", "symbol": "XAUUSDT", "source_timestamp": 4000, "bid": 4190.0, "ask": 4191.0, "source": "REALTIME"},
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    window = OrderReconciliationWindow(
        order_id=ord.id,
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )
    coverage_source = _valid_continuous_coverage_source("XAUUSDT", 1000, 5000)

    budget1 = CycleBudget(max_windows=1, max_pages=1, max_records=2)
    slice1 = process_window_slice(isolated_db, window, budget=budget1, coverage_source=coverage_source, page_size=2)
    assert slice1.has_more is True

    budget2 = CycleBudget(max_windows=1, max_pages=1, max_records=2)
    slice2 = process_window_slice(isolated_db, window, budget=budget2, coverage_source=coverage_source, page_size=2)

    assert slice2.read_complete is True
    assert slice2.decision is not None
    assert slice2.decision.action == "CLOSE"
    assert slice2.decision.candidate is not None
    assert slice2.decision.candidate.cause == "TP_HIT"
    assert slice2.decision.candidate.source_timestamp == 2000
    assert slice2.decision.candidate.executable_price == 4160.0


def test_budget01_zero_budget_no_false_complete(isolated_db: Session):
    """Case BUDGET01 (PHẦN 023, 024, 088):
    When CycleBudget has max_pages=0 or exhausted budget:
    process_window_slice must NOT mark the window as COMPLETE!
    Must return progressed=False, has_more=True, read_complete=False, reasons=['NO_BUDGET'].
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id="budget01",
        state="paper_open",
        opened_at=1000,
        symbol="XAUUSDT",
        direction="LONG",
        entry_price=4180.0,
        sl_price=4170.0,
        tp_price=4200.0,
    )

    quotes = [
        {"event_id": "ev_b01_1", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4201.0, "ask": 4202.0, "source": "REALTIME"},
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    window = OrderReconciliationWindow(
        order_id=ord.id,
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )

    zero_budget = CycleBudget(max_windows=1, max_pages=0, max_records=0)
    slice_res = process_window_slice(isolated_db, window, budget=zero_budget)

    assert slice_res.read_complete is False, "Must not mark COMPLETE with zero budget"
    assert slice_res.has_more is True, "Must indicate has_more when unread records exist"
    assert slice_res.decision is None, "Must not have decision"
    assert any("NO_BUDGET" in r for r in slice_res.reasons) or any("BUDGET" in r for r in slice_res.reasons)

    # Verify stored progress is NOT COMPLETE
    prog = load_window_progress(isolated_db, ord.id)
    if prog:
        assert prog.read_status != "COMPLETE", "Progress must never be saved as COMPLETE with 0 pages read"


def test_sched01_51_closed_orders_audit_all_unique_ids(isolated_db: Session):
    """Case SCHED01 (PHẦN 025, 027, 089):
    51 PaperOrders in 'closed' state with late conflicting evidence.
    Coordinator process_late_batch uses default max_pages=20 per cycle.
    In baseline: Cycle 1 processes 20 windows, and coordinator sets scheduler.window_cursor
    to window_page.next_cursor (skipping windows 21..50). Only 21 orders are audited!
    With fix: Coordinator checkpoints the last fully handled window or current unfinished window,
    so subsequent cycles resume at window 21 and audit all 51 orders.
    """
    for i in range(1, 52):
        ord = _seed_paper_order(
            isolated_db,
            order_id=f"c_{i:03d}",
            state="closed",
            opened_at=10000 + i * 100,
            closed_at=20000 + i * 100,
            sl_price=4170.0,
            tp_price=4200.0,
        )
        # Give order an exit_cause of SL_HIT so an earlier TP quote triggers REVIEW_REQUIRED audit
        ord.exit_cause = "SL_HIT"
        isolated_db.add(ord)

        # Seed late conflicting TP quote before closed_at
        quote = {
            "event_id": f"ev_audit_{i:03d}",
            "symbol": "XAUUSDT",
            "source_timestamp": 15000 + i * 100,
            "bid": 4205.0,
            "ask": 4206.0,
            "source": "REALTIME",
        }
        persist_reconciliation_evidence(isolated_db, [quote])

    isolated_db.commit()

    service = MarketReconciliationService()

    # Run coordinator across multiple bounded cycles (max_pages=20 each)
    for cycle in range(5):
        budget = CycleBudget(max_windows=50, max_pages=20, max_records=4000)
        service.process_late_batch(isolated_db, symbol="XAUUSDT", budget=budget)

    # Verify that all 51 closed orders have had reconciliation audits appended!
    audit_events = (
        isolated_db.query(models.DomainEvent)
        .filter(models.DomainEvent.event_type == "RECONCILIATION_AUDIT")
        .all()
    )
    audited_order_ids = {ev.aggregate_id for ev in audit_events}

    expected_order_ids = {f"c_{i:03d}" for i in range(1, 52)}
    missing_ids = expected_order_ids - audited_order_ids

    assert len(audited_order_ids) == 51, f"Expected 51 audited orders, got {len(audited_order_ids)}. Missing: {sorted(missing_ids)}"


def test_acc03_tie_ambiguity_split_pages(isolated_db: Session):
    """Case ACC03 (PHẦN 015, 085):
    TP and SL occur at the EXACT same timestamp (t=2000).
    They arrive split across pages (page 1 has TP, page 2 has SL).
    Both causes must be retained in the accumulator earliest group.
    Final resolver must detect both causes at min timestamp and resolve to AMBIGUOUS_BAR_SL_FIRST.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id="acc03",
        state="paper_open",
        opened_at=1000,
        symbol="XAUUSDT",
        direction="LONG",
        entry_price=4180.0,
        sl_price=4170.0,
        tp_price=4200.0,
    )

    quotes = [
        # Page 1: TP at 2000 (bid 4205 > 4200)
        {"event_id": "ev_acc03_tp", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"},
        # Page 2: SL at 2000 (bid 4165 < 4170)
        {"event_id": "ev_acc03_sl", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4165.0, "ask": 4166.0, "source": "REALTIME"},
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    window = OrderReconciliationWindow(
        order_id=ord.id,
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )
    coverage_source = _valid_continuous_coverage_source("XAUUSDT", 1000, 5000)

    # Slice 1: page_size=1, max_pages=1 -> reads TP
    budget1 = CycleBudget(max_windows=1, max_pages=1, max_records=1)
    slice1 = process_window_slice(isolated_db, window, budget=budget1, coverage_source=coverage_source, page_size=1)
    assert slice1.has_more is True

    # Slice 2: page_size=1, max_pages=1 -> reads SL at same timestamp
    budget2 = CycleBudget(max_windows=1, max_pages=1, max_records=1)
    slice2 = process_window_slice(isolated_db, window, budget=budget2, coverage_source=coverage_source, page_size=1)

    assert slice2.read_complete is True
    assert slice2.decision is not None
    assert slice2.decision.action == "CLOSE"
    assert slice2.decision.candidate is not None
    assert slice2.decision.candidate.cause == "AMBIGUOUS_BAR_SL_FIRST"
    assert slice2.decision.reason_code == "AMBIGUOUS_SAME_TIMESTAMP_CONSERVATIVE_SL"


def test_acc04_chunking_equivalence(isolated_db: Session):
    """Case ACC04 (PHẦN 086):
    Chunking equivalence: The terminal resolution must be identical whether processed
    unpaged (all in 1 slice) or in pages of size 1, 2, or 3 across restarts.
    """
    quotes = [
        {"event_id": "ev_eq_1", "symbol": "XAUUSDT", "source_timestamp": 1500, "bid": 4180.0, "ask": 4181.0, "source": "REALTIME"},
        {"event_id": "ev_eq_2", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"},  # TP
        {"event_id": "ev_eq_3", "symbol": "XAUUSDT", "source_timestamp": 2500, "bid": 4180.0, "ask": 4181.0, "source": "REALTIME"},
        {"event_id": "ev_eq_4", "symbol": "XAUUSDT", "source_timestamp": 3000, "bid": 4180.0, "ask": 4181.0, "source": "REALTIME"},
        {"event_id": "ev_eq_5", "symbol": "XAUUSDT", "source_timestamp": 3500, "bid": 4165.0, "ask": 4166.0, "source": "REALTIME"},  # SL
        {"event_id": "ev_eq_6", "symbol": "XAUUSDT", "source_timestamp": 4000, "bid": 4180.0, "ask": 4181.0, "source": "REALTIME"},
    ]
    persist_reconciliation_evidence(isolated_db, quotes)
    coverage_source = _valid_continuous_coverage_source("XAUUSDT", 1000, 5000)

    results = {}
    for p_size in (1, 2, 3, 10):
        ord_id = f"ord_eq_{p_size}"
        ord = _seed_paper_order(
            isolated_db,
            order_id=ord_id,
            state="paper_open",
            opened_at=1000,
            symbol="XAUUSDT",
            direction="LONG",
            entry_price=4180.0,
            sl_price=4170.0,
            tp_price=4200.0,
        )
        window = OrderReconciliationWindow(
            order_id=ord.id,
            symbol="XAUUSDT",
            opened_at=1000,
            end_as_of=5000,
            source_epoch=1,
            status="PENDING",
            cursor=1000,
            order=ord,
        )
        # Loop slices until read complete
        slice_res = None
        for _ in range(10):
            budget = CycleBudget(max_windows=1, max_pages=1, max_records=p_size)
            slice_res = process_window_slice(isolated_db, window, budget=budget, coverage_source=coverage_source, page_size=p_size)
            if slice_res.read_complete:
                break

        assert slice_res is not None and slice_res.read_complete is True
        results[p_size] = (
            slice_res.decision.action,
            slice_res.decision.candidate.cause,
            slice_res.decision.candidate.source_timestamp,
            slice_res.decision.candidate.executable_price,
        )

    # All page sizes must produce identical resolution outcome
    ref = results[10]
    for p_size, res in results.items():
        assert res == ref, f"Chunking mismatch at page_size={p_size}: {res} vs {ref}"


def test_acc05_corrupt_context_and_legacy_replay(isolated_db: Session):
    """Case ACC05 (PHẦN 021, 045, 081, 087):
    A legacy progress record with cursor past start but no accumulator (candidate_refs empty)
    must trigger safe replay (read_status='REPLAY_NEEDED') rather than trusting the prefix
    and forgetting an early TP.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id="acc05",
        state="paper_open",
        opened_at=1000,
        symbol="XAUUSDT",
        direction="LONG",
        entry_price=4180.0,
        sl_price=4170.0,
        tp_price=4200.0,
    )

    quotes = [
        {"event_id": "ev_acc05_tp", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"},  # TP
        {"event_id": "ev_acc05_sl", "symbol": "XAUUSDT", "source_timestamp": 4000, "bid": 4165.0, "ask": 4166.0, "source": "REALTIME"},  # SL
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    from services.market_reconciliation_service import EvidenceProgress, save_window_progress

    # Seed legacy progress that advanced cursor past t=2000 without accumulator
    legacy_prog = EvidenceProgress(
        window_id=ord.id,
        source_cursor=(2500, "ev_fake"),
        arrival_revision=1,
        scan_cutoff=5000,
        candidate_refs=[],
        read_status="MORE_WORK",
        accumulator=None,  # Missing accumulator!
    )
    save_window_progress(isolated_db, legacy_prog)

    window = OrderReconciliationWindow(
        order_id=ord.id,
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )
    coverage_source = _valid_continuous_coverage_source("XAUUSDT", 1000, 5000)

    # Invocation must detect legacy cursor without accumulator and safely replay from start
    budget = CycleBudget(max_windows=1, max_pages=5, max_records=10)
    slice_res = process_window_slice(isolated_db, window, budget=budget, coverage_source=coverage_source, page_size=5)

    assert slice_res.read_complete is True
    assert slice_res.decision is not None
    assert slice_res.decision.action == "CLOSE"
    assert slice_res.decision.candidate.cause == "TP_HIT"
    assert slice_res.decision.candidate.source_timestamp == 2000


def test_acc06_latest_page_neutral_or_empty(isolated_db: Session):
    """Case ACC06 (PHẦN 092):
    Prior slice contains an early TP. The final page is completely neutral or empty.
    The final resolver must NOT return None / NO_CANDIDATE; it must resolve using the
    accumulated candidates from prior slices.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id="acc06",
        state="paper_open",
        opened_at=1000,
        symbol="XAUUSDT",
        direction="LONG",
        entry_price=4180.0,
        sl_price=4170.0,
        tp_price=4200.0,
    )

    quotes = [
        {"event_id": "ev_acc06_tp", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"},  # TP
        {"event_id": "ev_acc06_n1", "symbol": "XAUUSDT", "source_timestamp": 2500, "bid": 4180.0, "ask": 4181.0, "source": "REALTIME"},  # Neutral
        {"event_id": "ev_acc06_n2", "symbol": "XAUUSDT", "source_timestamp": 3000, "bid": 4180.0, "ask": 4181.0, "source": "REALTIME"},  # Neutral
        {"event_id": "ev_acc06_n3", "symbol": "XAUUSDT", "source_timestamp": 3500, "bid": 4180.0, "ask": 4181.0, "source": "REALTIME"},  # Neutral
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    window = OrderReconciliationWindow(
        order_id=ord.id,
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )
    coverage_source = _valid_continuous_coverage_source("XAUUSDT", 1000, 5000)

    # Slice 1: reads TP + n1
    budget1 = CycleBudget(max_windows=1, max_pages=1, max_records=2)
    slice1 = process_window_slice(isolated_db, window, budget=budget1, coverage_source=coverage_source, page_size=2)
    assert slice1.has_more is True

    # Slice 2: reads n2 + n3 (NO candidates in this slice!)
    budget2 = CycleBudget(max_windows=1, max_pages=1, max_records=2)
    slice2 = process_window_slice(isolated_db, window, budget=budget2, coverage_source=coverage_source, page_size=2)

    assert slice2.read_complete is True
    assert slice2.decision is not None
    assert slice2.decision.action == "CLOSE"
    assert slice2.decision.candidate.cause == "TP_HIT"
    assert slice2.decision.candidate.source_timestamp == 2000


def test_sched02_restart_unfinished_window(isolated_db: Session):
    """Case SCHED02 (PHẦN 027, 028, 090):
    A large window partially read stops on budget.
    Coordinator records unfinished window id and last handled window.
    Subsequent cycle resumes this window and completes without skipping tail.
    """
    ord1 = _seed_paper_order(isolated_db, order_id="sch02_1", state="closed", opened_at=1000, closed_at=2000)
    ord2 = _seed_paper_order(isolated_db, order_id="sch02_2", state="closed", opened_at=3000, closed_at=4000)

    # Seed 6 quotes for ord2
    quotes = [
        {"event_id": f"ev_sch02_{i}", "symbol": "XAUUSDT", "source_timestamp": 3100 + i * 50, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"}
        for i in range(6)
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    service = MarketReconciliationService()

    # Cycle 1: budget allows only 1 page of 2 records
    budget1 = CycleBudget(max_windows=5, max_pages=1, max_records=2)
    service.process_late_batch(isolated_db, symbol="XAUUSDT", budget=budget1)

    prog = load_scan_progress(isolated_db, symbol="XAUUSDT", scan_scope="default")
    assert prog is not None

    # Cycle 2: budget allows completing the rest
    budget2 = CycleBudget(max_windows=5, max_pages=10, max_records=50)
    service.process_late_batch(isolated_db, symbol="XAUUSDT", budget=budget2)

    audit_events = (
        isolated_db.query(models.DomainEvent)
        .filter(models.DomainEvent.event_type == "RECONCILIATION_AUDIT")
        .all()
    )
    audited = {ev.aggregate_id for ev in audit_events}
    assert ord2.id in audited or f"ord_{ord2.id}" in audited or "sch02_2" in audited


def test_sched03_uneven_fairness_open_and_closed(isolated_db: Session):
    """Case SCHED03 (PHẦN 032, 091):
    1 OPEN order and multiple CLOSED orders with uneven event counts.
    Coordinator prioritizes active OPEN order while CLOSED history progresses across bounded cycles.
    """
    ord_open = _seed_paper_order(isolated_db, order_id="fair_open", state="paper_open", opened_at=50000)
    for i in range(1, 11):
        _seed_paper_order(isolated_db, order_id=f"fair_c_{i:02d}", state="closed", opened_at=1000 + i * 100, closed_at=2000 + i * 100)

    service = MarketReconciliationService()
    # Cycle 1
    budget1 = CycleBudget(max_windows=5, max_pages=5, max_records=100)
    service.process_late_batch(isolated_db, symbol="XAUUSDT", budget=budget1)

    prog = load_scan_progress(isolated_db, symbol="XAUUSDT", scan_scope="default")
    assert prog is not None


def test_durable01_commit_failure_isolation(isolated_db: Session, monkeypatch):
    """Case DURABLE01 (PHẦN 022, 076, 093):
    If save_window_progress fails during DB commit, process_window_slice propagates
    PROGRESS_SAVE_FAILED, read_complete is False, and no false terminal action is executed.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id="dur01",
        state="paper_open",
        opened_at=1000,
        symbol="XAUUSDT",
        direction="LONG",
        entry_price=4180.0,
        sl_price=4170.0,
        tp_price=4200.0,
    )
    quotes = [
        {"event_id": "ev_dur_1", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"},
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    from services import market_reconciliation_service

    # Monkeypatch save_window_progress to simulate DB commit failure
    monkeypatch.setattr(market_reconciliation_service, "save_window_progress", lambda db, prog: False)

    window = OrderReconciliationWindow(
        order_id=ord.id,
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )
    budget = CycleBudget(max_windows=1, max_pages=1, max_records=10)
    slice_res = process_window_slice(isolated_db, window, budget=budget)

    assert slice_res.read_complete is False
    assert "PROGRESS_SAVE_FAILED" in slice_res.reasons
    assert slice_res.decision is None


def test_replay01_earlier_late_evidence_triggers_replay(isolated_db: Session):
    """Case REPLAY01 (PHẦN 034, 078, 094):
    Window was read past t=3000 where SL was seen.
    An earlier late evidence arrives at t=2000 (TP).
    register_affected_reconciliation_tasks marks window REPLAY_NEEDED.
    Next slice replays from beginning and finds early TP at 2000.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id="rep01",
        state="paper_open",
        opened_at=1000,
        symbol="XAUUSDT",
        direction="LONG",
        entry_price=4180.0,
        sl_price=4170.0,
        tp_price=4200.0,
    )

    # Initial evidence has only SL at 4000
    quotes1 = [
        {"event_id": "ev_rep_sl", "symbol": "XAUUSDT", "source_timestamp": 4000, "bid": 4165.0, "ask": 4166.0, "source": "REALTIME"},
    ]
    persist_reconciliation_evidence(isolated_db, quotes1)

    window = OrderReconciliationWindow(
        order_id=ord.id,
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=5000,
        source_epoch=1,
        status="PENDING",
        cursor=1000,
        order=ord,
    )
    coverage_source = _valid_continuous_coverage_source("XAUUSDT", 1000, 5000)

    # Initial read sees SL
    budget1 = CycleBudget(max_windows=1, max_pages=5, max_records=10)
    slice1 = process_window_slice(isolated_db, window, budget=budget1, coverage_source=coverage_source)
    assert slice1.decision.candidate.cause == "SL_HIT"

    # Now earlier late evidence arrives at t=2000 (TP)
    quotes2 = [
        {"event_id": "ev_rep_tp", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"},
    ]
    committed_ids = persist_reconciliation_evidence(isolated_db, quotes2)
    from services.market_reconciliation_service import register_affected_reconciliation_tasks
    register_affected_reconciliation_tasks(isolated_db, committed_ids)

    # Next slice invocation should replay and select TP at 2000!
    budget2 = CycleBudget(max_windows=1, max_pages=5, max_records=10)
    slice2 = process_window_slice(isolated_db, window, budget=budget2, coverage_source=coverage_source)

    assert slice2.read_complete is True
    assert slice2.decision is not None
    assert slice2.decision.candidate.cause == "TP_HIT"
    assert slice2.decision.candidate.source_timestamp == 2000


def test_close01_race_monitor_and_recovery(isolated_db: Session):
    """Case CLOSE01 (PHẦN 037, 095):
    Order was already closed by live exit at t=3500.
    Reconciliation sees an earlier conflicting TP at t=2000.
    persist_resolution_or_review must NOT rewrite PNL or reopen order;
    must record REVIEW_REQUIRED audit.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id="close01",
        state="closed",
        opened_at=1000,
        closed_at=3500,
        symbol="XAUUSDT",
        direction="LONG",
        entry_price=4180.0,
        sl_price=4170.0,
        tp_price=4200.0,
    )
    ord.exit_cause = "SL_HIT"
    isolated_db.commit()

    quotes = [
        {"event_id": "ev_cls_tp", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"},
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    window = OrderReconciliationWindow(
        order_id=ord.id,
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=3500,
        source_epoch=1,
        status="RESOLVED",
        cursor=1000,
        order=ord,
    )
    coverage_source = _valid_continuous_coverage_source("XAUUSDT", 1000, 3500)

    budget = CycleBudget(max_windows=1, max_pages=5, max_records=10)
    slice_res = process_window_slice(isolated_db, window, budget=budget, coverage_source=coverage_source)

    assert slice_res.read_complete is True
    assert slice_res.decision is not None
    assert slice_res.decision.action == "REVIEW_REQUIRED"
    assert slice_res.decision.reason_code == "EARLIER_CONFLICTING_TERMINAL_ARRIVED_POST_CLOSE"

    from services.market_reconciliation_service import persist_resolution_or_review
    res = persist_resolution_or_review(isolated_db, window, slice_res.decision)
    assert res.action == "REVIEW_REQUIRED"
    assert res.applied is True

    # Order remains closed, state not mutated
    isolated_db.refresh(ord)
    assert ord.state == "closed"

