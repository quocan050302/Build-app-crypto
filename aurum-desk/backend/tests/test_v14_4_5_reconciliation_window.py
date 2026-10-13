"""
backend/tests/test_v14_4_5_reconciliation_window.py
V14.4.5 — Case R01 & R02: Reconciliation Window, Authoritative Coverage & Multi-Poll Replay.

Covers PHẦN 015–030, 086–091, 109–110:
Case R01:
- LONG order opened_at=1000, entry=4180, SL=4170, TP=4200.
- Late SL tick arrives at source_timestamp=30000.
- Called through actual market_reconciliation_service.process_late_batch(isolated_db).
- Without provider coverage proof, runtime coverage evaluates to UNKNOWN.
- Order is NOT confirmed closed at poll 1; remains paper_open with evidence persisted.
- SHORT mirror behaves symmetrically.

Case R02:
- Poll 1: late SL at 30k arrives, is persisted to DomainEvent and acknowledged.
- Service is rebuilt / new Session created (simulating restart).
- Poll 2: earlier late TP at 20k arrives.
- Window loader replays both persisted records.
- With genuine external provider coverage proof, chronology correctly resolves earliest terminal (TP at 20k).
- If order was already closed prior, earlier conflicting quote creates durable review audit without reopening order.
"""
import json
import pytest
from sqlalchemy.orm import Session

import models
from database import SessionLocal
from services.quote_validator import CanonicalQuote
from services.quote_ordering_service import quote_ordering_service
from services.market_reconciliation_service import (
    CoverageStatus,
    OrderReconciliationWindow,
    MarketReconciliationService,
    load_pending_reconciliation_windows,
    load_persisted_window_evidence,
    load_reconciliation_coverage,
    assess_provider_coverage,
    resolve_reconciliation_window,
    collect_terminal_candidates,
    persist_resolution_or_review,
)


def _make_quote(ts: int, bid: float = 4180.0, ask: float = 4182.0, last: float = 4181.0, symbol: str = "XAUUSDT") -> CanonicalQuote:
    return CanonicalQuote(
        symbol=symbol,
        bid=bid,
        ask=ask,
        last=last,
        mark_price=last,
        exchange_ts_ms=ts,
        received_at_ms=ts + 10,
        status="VALID",
        source="BITGET_WS",
        connection_epoch=1,
    )


def test_r01_single_poll_unknown_coverage_does_not_close_confirmed(isolated_db: Session):
    """Case R01: Single poll late SL does NOT close order without authoritative provider proof."""
    # 1. Setup LONG order: opened at 1000, entry 4180, SL 4170, TP 4200
    order_long = models.PaperOrder(
        id="ord_r01_long",
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
    isolated_db.add(order_long)
    isolated_db.commit()

    # Reset quote ordering buffer and buffer a late SL quote
    quote_ordering_service.reset_watermark_for_testing("XAUUSDT")
    quote_ordering_service.classify_quote(_make_quote(ts=100000, bid=4180.0, ask=4182.0, last=4181.0))
    quote_ordering_service.classify_quote(_make_quote(ts=30000, bid=4169.0, ask=4171.0, last=4170.0))

    # Execute actual process_late_batch
    svc = MarketReconciliationService()
    processed_count = svc.process_late_batch(isolated_db, symbol="XAUUSDT")
    assert processed_count >= 1

    # Assert state in fresh DB query: must remain paper_open!
    isolated_db.refresh(order_long)
    assert order_long.state == "paper_open"
    assert order_long.closed_at is None

    # Verify evidence was durably persisted in DomainEvent
    evs = isolated_db.query(models.DomainEvent).filter(
        models.DomainEvent.event_type == "LATE_RECONCILIATION_EVIDENCE"
    ).all()
    assert len(evs) >= 1

    # 2. SHORT mirror test
    order_short = models.PaperOrder(
        id="ord_r01_short",
        instrument="XAUUSDT",
        direction="SHORT",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4190.0,
        take_profit=4160.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        opened_at=1000,
        created_at=1000,
    )
    isolated_db.add(order_short)
    isolated_db.commit()

    quote_ordering_service.classify_quote(_make_quote(ts=32000, bid=4191.0, ask=4192.0, last=4191.5))

    processed_short = svc.process_late_batch(isolated_db, symbol="XAUUSDT")
    assert processed_short >= 1

    isolated_db.refresh(order_short)
    assert order_short.state == "paper_open"
    assert order_short.closed_at is None


def test_r02_two_poll_replay_across_restart_and_authority_resolution(isolated_db: Session):
    """Case R02: Replays persisted window across polls/restart; resolves earliest TP when authority proof present."""
    order = models.PaperOrder(
        id="ord_r02_chrono",
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

    # Poll 1: late SL at ts=30000
    quote_ordering_service.reset_watermark_for_testing("XAUUSDT")
    quote_ordering_service.classify_quote(_make_quote(ts=100000, bid=4180.0, ask=4182.0, last=4181.0))
    quote_ordering_service.classify_quote(_make_quote(ts=30000, bid=4169.0, ask=4171.0, last=4170.0))

    svc1 = MarketReconciliationService()
    svc1.process_late_batch(isolated_db, symbol="XAUUSDT")

    # Order remains open because coverage is UNKNOWN
    isolated_db.refresh(order)
    assert order.state == "paper_open"

    # Poll 2: late TP at ts=20000 (earlier than SL!) arrives after service rebuild / restart
    svc2 = MarketReconciliationService()
    quote_ordering_service.classify_quote(_make_quote(ts=20000, bid=4202.0, ask=4204.0, last=4203.0))

    # In poll 2, a genuine provider proof spanning [1000, 35000] is provided
    genuine_provider_proof = {
        "source_state": {
            "continuity_proof": {
                "source": "BITGET_WS",
                "epoch": 1,
                "resolution": "TICK",
                "interval_start_ms": 1000,
                "interval_end_ms": 35000,
                "has_gap": False,
                "missing_intervals": [],
                "proof_references": ["ext_proof_ledger_1000_35000"],
            }
        }
    }

    svc2.process_late_batch(isolated_db, symbol="XAUUSDT", source_state=genuine_provider_proof["source_state"])

    # Now with authoritative provider coverage proof, window evaluates both ticks:
    # TP at 20000 came BEFORE SL at 30000!
    isolated_db.refresh(order)
    assert order.state == "closed"
    assert order.exit_cause == "TP_HIT"
    assert order.closed_at == 20000
    assert order.actual_exit == 4200.0  # TP level fill (per shared_exit_evaluator)


def test_r02_closed_order_conflict_only_appends_audit(isolated_db: Session):
    """Case R02 part 2: If order was already closed at 25000 with SL, an earlier TP at 20000 only logs review audit."""
    order = models.PaperOrder(
        id="ord_r02_closed",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        opened_at=1000,
        closed_at=25000,
        exit_cause="SL_HIT",
        actual_exit=4169.0,
        created_at=1000,
    )
    isolated_db.add(order)
    isolated_db.commit()

    quote_ordering_service.reset_watermark_for_testing("XAUUSDT")
    quote_ordering_service.classify_quote(_make_quote(ts=100000, bid=4180.0, ask=4182.0, last=4181.0))

    # Earlier conflicting TP at 20000 arrives post-close
    quote_ordering_service.classify_quote(_make_quote(ts=20000, bid=4202.0, ask=4204.0, last=4203.0))

    svc = MarketReconciliationService()
    svc.process_late_batch(isolated_db, symbol="XAUUSDT")

    isolated_db.refresh(order)
    assert order.state == "closed"
    assert order.exit_cause == "SL_HIT"  # PnL / exit cause not overwritten
    assert order.closed_at == 25000     # Closed time preserved
    assert order.invalidation_reason is not None
    assert "REVIEW_REQUIRED" in order.invalidation_reason
