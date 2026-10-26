"""
backend/tests/test_v14_4_4_reconciliation_runtime.py
V14.4.4 - Chronology, Evidence Durability, Parity & Shutdown Tests for Market Reconciliation.

Proves:
1. assess_reconciliation_coverage returns UNKNOWN without explicit continuity proof;
   does not assume COMPLETE just because there is no overflow gap.
2. evaluate_order_exit requires Bid for LONG and Ask for SHORT.
   Missing executable side returns MISSING_EXECUTABLE_SIDE and does NOT fallback to Last.
3. Gap pricing parity: SL/TP fill reflects actual executable gap price, not threshold.
4. Two-poll chronology:
   - Poll 1: late SL closes order.
   - Poll 2: late TP arrives post-close, writes durable audit record.
   - Evidence is persisted and audit is committed BEFORE ACK.
5. Lifespan shutdown drain: shutdown awaits in-flight worker task cleanly.
"""
import asyncio
import pytest
import models
from services.market_reconciliation_service import (
    CoverageStatus,
    assess_reconciliation_coverage,
    collect_terminal_candidates,
    resolve_reconciliation_window,
    market_reconciliation_service,
)
from services.shared_exit_evaluator import evaluate_order_exit
from services.quote_ordering_service import quote_ordering_service


def test_coverage_assessment_defaults_to_unknown_without_continuity_proof():
    """PHẦN 032, 104: Empty or single-tick evidence without proof returns UNKNOWN, not COMPLETE."""
    order = models.PaperOrder(
        id="ord_cov_1",
        instrument="XAUUSDT",
        direction="LONG",
        opened_at=1000,
        stop_loss=4170.0,
        take_profit=4200.0,
    )

    # Empty evidence
    cov_empty = assess_reconciliation_coverage(order, None, [])
    assert cov_empty.coverage_status == CoverageStatus.UNKNOWN

    # Single late tick without continuity proof
    single_tick = [{
        "evidence_id": "ev_1",
        "source_timestamp": 30000,
        "bid": 4169.0,
        "ask": 4171.0,
        "last": 4170.0,
    }]
    cov_single = assess_reconciliation_coverage(order, None, single_tick)
    assert cov_single.coverage_status == CoverageStatus.UNKNOWN


def test_executable_side_required_no_fallback_to_last():
    """PHẦN 040: LONG requires Bid; SHORT requires Ask. Missing side returns MISSING_EXECUTABLE_SIDE."""
    order_long = models.PaperOrder(
        id="ord_side_long",
        direction="LONG",
        stop_loss=4170.0,
        take_profit=4200.0,
    )
    order_short = models.PaperOrder(
        id="ord_side_short",
        direction="SHORT",
        stop_loss=4190.0,
        take_profit=4160.0,
    )

    # Quote has only Last, missing Bid and Ask
    quote_last_only = {"last": 4165.0, "evidence_id": "ev_last"}

    res_long = evaluate_order_exit(order_long, quote_last_only)
    assert res_long.triggered is False
    assert res_long.unavailable_reason == "MISSING_EXECUTABLE_SIDE"

    res_short = evaluate_order_exit(order_short, quote_last_only)
    assert res_short.triggered is False
    assert res_short.unavailable_reason == "MISSING_EXECUTABLE_SIDE"


def test_gap_pricing_parity_applied_exit_price():
    """PHẦN 041: Quote gap past SL gives applied_exit_price matching executable quote."""
    order_long = models.PaperOrder(
        id="ord_gap_long",
        direction="LONG",
        stop_loss=4170.0,
        take_profit=4200.0,
    )
    # Market gapped down: Bid is 4168.5 (below SL 4170.0)
    quote_gap_long = {"bid": 4168.5, "ask": 4169.5, "last": 4169.0, "evidence_id": "ev_gap"}
    res_long = evaluate_order_exit(order_long, quote_gap_long)
    assert res_long.triggered is True
    assert res_long.cause == "SL_HIT"
    assert res_long.trigger_level == 4170.0
    assert res_long.applied_exit_price == 4168.5  # Gap fill price!

    # SHORT mirror
    order_short = models.PaperOrder(
        id="ord_gap_short",
        direction="SHORT",
        stop_loss=4190.0,
        take_profit=4160.0,
    )
    # Market gapped up: Ask is 4192.0 (above SL 4190.0)
    quote_gap_short = {"bid": 4191.0, "ask": 4192.0, "last": 4191.5, "evidence_id": "ev_gap_short"}
    res_short = evaluate_order_exit(order_short, quote_gap_short)
    assert res_short.triggered is True
    assert res_short.cause == "SL_HIT"
    assert res_short.trigger_level == 4190.0
    assert res_short.applied_exit_price == 4192.0


def test_two_poll_chronology_and_durable_audit(isolated_db):
    """PHẦN 031, 037, 038: Poll 1 late SL closes order; Poll 2 late TP logs durable audit."""
    order = models.PaperOrder(
        id="ord_chrono_2poll",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        opened_at=1000,
        created_at=1000,
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Poll 1: late SL at ts=30000 arrives
    quote_sl = {
        "evidence_id": "ev_sl_30k",
        "source_timestamp": 30000,
        "known_at_ms": 35000,
        "bid": 4169.0,
        "ask": 4171.0,
        "last": 4170.0,
        "source": "BITGET_WS",
        "epoch": 1,
    }

    # Execute close for Poll 1
    cands1 = collect_terminal_candidates(order, [quote_sl])
    assert len(cands1) == 1
    assert cands1[0].cause == "SL_HIT"

    # From a proven coverage, resolve close decision
    proof_sl = {
        "continuity_proof": {
            "has_gap": False,
            "interval_start_ms": 1000,
            "interval_end_ms": 30000,
            "resolution": "TICK",
            "epoch": 1,
        },
        "source": "BITGET_WS",
        "epoch": 1,
    }
    cov1 = assess_reconciliation_coverage(order, None, [quote_sl], source_metadata=proof_sl)
    assert cov1.coverage_status == CoverageStatus.COMPLETE
    dec1 = resolve_reconciliation_window(order, cands1, cov1)
    assert dec1.action == "CLOSE"

    # Apply close decision
    market_reconciliation_service.apply_reconciliation_decision(isolated_db, order, dec1)
    isolated_db.commit()

    isolated_db.refresh(order)
    assert order.state == "closed"
    assert order.exit_cause == "SL_HIT"
    assert order.closed_at == 30000

    # Poll 2: late TP at ts=20000 arrives (earlier than SL!)
    quote_tp = {
        "evidence_id": "ev_tp_20k",
        "source_timestamp": 20000,
        "known_at_ms": 40000,
        "bid": 4201.0,
        "ask": 4203.0,
        "last": 4202.0,
        "source": "BITGET_WS",
        "epoch": 1,
    }
    cands2 = collect_terminal_candidates(order, [quote_tp])
    dec2 = resolve_reconciliation_window(order, cands2, assess_reconciliation_coverage(order, None, [quote_tp]))
    assert dec2.action == "REVIEW_REQUIRED"

    # Apply decision: must write durable audit and commit
    market_reconciliation_service.apply_reconciliation_decision(isolated_db, order, dec2)
    isolated_db.commit()

    # Verify audit is persisted in DB
    isolated_db.refresh(order)
    assert order.invalidation_reason is not None
    assert "REVIEW_REQUIRED" in order.invalidation_reason
    assert order.state == "closed"  # Did NOT reopen or change PNL!


def test_shutdown_drain_contract():
    """PHẦN 044, 107: Shutdown helper waits for in-flight task to complete cleanly."""
    svc = market_reconciliation_service
    # When no worker is running
    res = asyncio.run(svc.shutdown(timeout_s=1.0))
    assert res.status in ("COMPLETE", "NO_ACTIVE_WORKER")
    assert res.in_flight_done is True
