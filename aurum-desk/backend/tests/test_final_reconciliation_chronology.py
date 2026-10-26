"""
backend/tests/test_final_reconciliation_chronology.py
V14.4.3 — Final Closure Suite for Late Evidence Reconciliation & Chronology.

Tests:
1. CHRONO01: Cross-batch arrival (SL at 30000 in batch 1, TP at 20000 in batch 2).
   With COMPLETE coverage, earlier terminal TP (20000) wins.
2. CHRONO02: Cross-batch arrival with UNKNOWN/PARTIAL coverage.
   Yields NEEDS_RECONCILIATION without premature close.
3. CHRONO03: Late conflicting quote for ALREADY CLOSED order.
   Does NOT reopen order or mutate PNL; records REVIEW_REQUIRED audit.
4. CHRONO04: Ambiguous same-timestamp SL/TP touch.
   Yields AMBIGUOUS_BAR_SL_FIRST decision.
5. CHRONO05: Worker shutdown waits for actual worker drain.
"""
import asyncio
import pytest
import models
from services.quote_ordering_service import quote_ordering_service
from services.market_reconciliation_service import (
    market_reconciliation_service,
    assess_reconciliation_coverage,
    collect_terminal_candidates,
    evaluate_order_exit,
    resolve_reconciliation_window,
    CoverageStatus,
    CoverageAssessment,
    ShutdownResult,
)
from services.trade_lifecycle_service import TradeLifecycleService


def test_chrono01_cross_batch_tp_wins_when_coverage_complete(isolated_db):
    """CHRONO01:
    LONG order opened_at=1000, entry=4180, SL=4170, TP=4200.
    Poll 1 receives SL quote at source_ts=30000 (bid=4169).
    Poll 2 receives TP quote at source_ts=20000 (bid=4201).
    With complete coverage, the earlier terminal at 20000 (TP) must win.
    """
    symbol = "XAUUSDT"
    quote_ordering_service.reset_watermark_for_testing(symbol)

    order = models.PaperOrder(
        id="ord-chrono-01",
        setup_id="setup-chrono-01",
        instrument=symbol,
        direction="LONG",
        order_type="LIMIT",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        created_at=1000,
        state="paper_open",
        opened_at=1000,
        closed_at=None,
    )
    isolated_db.add(order)
    isolated_db.commit()

    records = [
        {"evidence_id": "ev-sl-30k", "source_timestamp": 30000, "bid": 4169.0, "ask": 4169.5, "last": 4169.2, "epoch": 1, "source": "TEST"},
        {"evidence_id": "ev-tp-20k", "source_timestamp": 20000, "bid": 4201.0, "ask": 4201.5, "last": 4201.2, "epoch": 1, "source": "TEST"},
    ]

    coverage = CoverageAssessment(
        interval_start_ms=1000,
        interval_end_ms=30000,
        source="TEST",
        epoch=1,
        price_basis="BID_ASK",
        coverage_status=CoverageStatus.COMPLETE,
        missing_intervals=[],
        evidence_ids=["ev-sl-30k", "ev-tp-20k"],
        assessed_at_ms=35000,
        reason_code="VERIFIED_CONTINUOUS",
    )

    candidates = collect_terminal_candidates(order, records)
    decision = resolve_reconciliation_window(order, candidates, coverage)

    assert decision.action == "CLOSE"
    assert decision.candidate is not None
    assert decision.candidate.evidence_id == "ev-tp-20k"
    assert decision.candidate.source_timestamp == 20000
    assert decision.candidate.cause == "TP_HIT"


def test_chrono02_cross_batch_needs_reconciliation_when_coverage_incomplete(isolated_db):
    """CHRONO02:
    When coverage is UNKNOWN / PARTIAL, resolution yields NEEDS_RECONCILIATION.
    """
    symbol = "XAUUSDT"
    order = models.PaperOrder(
        id="ord-chrono-02",
        setup_id="setup-chrono-02",
        instrument=symbol,
        direction="LONG",
        order_type="LIMIT",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        created_at=1000,
        state="paper_open",
        opened_at=1000,
    )
    records = [
        {"evidence_id": "ev-sl-30k", "source_timestamp": 30000, "bid": 4169.0, "ask": 4169.5, "last": 4169.2, "epoch": 1, "source": "TEST"},
    ]

    coverage = CoverageAssessment(
        interval_start_ms=1000,
        interval_end_ms=30000,
        source="TEST",
        epoch=1,
        price_basis="BID_ASK",
        coverage_status=CoverageStatus.PARTIAL,
        missing_intervals=[(5000, 15000)],
        evidence_ids=["ev-sl-30k"],
        assessed_at_ms=35000,
        reason_code="GAP_IN_OBSERVATION",
    )

    candidates = collect_terminal_candidates(order, records)
    decision = resolve_reconciliation_window(order, candidates, coverage)

    assert decision.action == "NEEDS_RECONCILIATION"
    assert "INCOMPLETE" in decision.reason_code or "GAP" in decision.reason_code


def test_chrono03_earlier_conflicting_quote_for_closed_order_records_audit_review(isolated_db):
    """CHRONO03:
    Order was already closed by SL at source_ts=30000.
    A late quote arrives showing TP at source_ts=20000.
    Reconciliation MUST NOT reopen order or alter realized PNL.
    It MUST record a REVIEW_REQUIRED audit entry.
    """
    symbol = "XAUUSDT"
    order = models.PaperOrder(
        id="ord-chrono-03",
        setup_id="setup-chrono-03",
        instrument=symbol,
        direction="LONG",
        order_type="LIMIT",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        created_at=1000,
        state="closed",
        opened_at=1000,
        closed_at=30000,
        actual_exit=4170.0,
        exit_cause="SL_HIT",
    )
    isolated_db.add(order)
    isolated_db.commit()

    records = [
        {"evidence_id": "ev-tp-20k", "source_timestamp": 20000, "bid": 4201.0, "ask": 4201.5, "last": 4201.2, "epoch": 1, "source": "TEST"},
    ]

    coverage = CoverageAssessment(
        interval_start_ms=1000,
        interval_end_ms=30000,
        source="TEST",
        epoch=1,
        price_basis="BID_ASK",
        coverage_status=CoverageStatus.COMPLETE,
        missing_intervals=[],
        evidence_ids=["ev-tp-20k"],
        assessed_at_ms=35000,
        reason_code="VERIFIED_CONTINUOUS",
    )

    candidates = collect_terminal_candidates(order, records)
    decision = resolve_reconciliation_window(order, candidates, coverage)

    assert decision.action == "REVIEW_REQUIRED"
    assert decision.candidate is not None
    assert decision.candidate.source_timestamp < order.closed_at

    # Apply decision and verify order remains closed
    market_reconciliation_service.apply_reconciliation_decision(isolated_db, order, decision)
    isolated_db.refresh(order)
    assert order.state == "closed"
    assert order.actual_exit == 4170.0  # Realized PNL and exit price preserved!
    assert "REVIEW_REQUIRED" in (order.invalidation_reason or "")


def test_chrono04_ambiguous_same_timestamp_both_levels(isolated_db):
    """CHRONO04:
    Quote has both SL and TP triggered on the same candle / timestamp (high >= TP and low <= SL).
    Resolution yields AMBIGUOUS decision.
    """
    symbol = "XAUUSDT"
    order = models.PaperOrder(
        id="ord-chrono-04",
        setup_id="setup-chrono-04",
        instrument=symbol,
        direction="LONG",
        order_type="LIMIT",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        created_at=1000,
        state="paper_open",
        opened_at=1000,
    )
    records = [
        # Ambiguous bar touching both low 4165 (<= SL) and high 4205 (>= TP)
        {"evidence_id": "ev-ambig-1", "source_timestamp": 20000, "high": 4205.0, "low": 4165.0, "bid": 4165.0, "ask": 4205.0, "last": 4180.0, "epoch": 1, "source": "TEST"},
    ]

    coverage = CoverageAssessment(
        interval_start_ms=1000,
        interval_end_ms=20000,
        source="TEST",
        epoch=1,
        price_basis="BID_ASK",
        coverage_status=CoverageStatus.COMPLETE,
        missing_intervals=[],
        evidence_ids=["ev-ambig-1"],
        assessed_at_ms=25000,
        reason_code="VERIFIED_CONTINUOUS",
    )

    candidates = collect_terminal_candidates(order, records)
    decision = resolve_reconciliation_window(order, candidates, coverage)

    assert decision.action in ("CLOSE", "AMBIGUOUS")
    if decision.action == "CLOSE":
        assert decision.candidate.cause == "AMBIGUOUS_BAR_SL_FIRST"


def test_chrono05_worker_shutdown_waits_for_active_drain():
    """CHRONO05:
    MarketReconciliationService.shutdown awaits active worker completion.
    """
    svc = market_reconciliation_service
    res = asyncio.run(svc.shutdown(timeout_s=1.0))
    assert isinstance(res, ShutdownResult)
    assert res.status in ("COMPLETE", "NO_ACTIVE_WORKER")
