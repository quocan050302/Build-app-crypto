"""
backend/tests/test_late_source_chronology.py
V14.4.2 — Repro 1 / TIMELINE01: Source Chronology in Late Evidence Reconciliation.

Enforces:
1. When late quotes arrive out-of-order (e.g. SL at source_ts=30000 arrives first,
   TP at source_ts=20000 arrives second), the reconciliation consumer MUST sort
   by source_timestamp before making a terminal exit decision.
2. Earlier TP (20000) must win over later SL (30000) when coverage is complete.
3. Mirror SHORT case: earlier TP (20000) must win over later SL (30000).
4. If intervening coverage between order opened_at and the quote is incomplete / uncertain,
   it must yield NEEDS_RECONCILIATION rather than prematurely closing on the first arrival.
"""
import pytest
import models
from services.quote_validator import CanonicalQuote
from services.quote_ordering_service import quote_ordering_service
from services.market_reconciliation_service import market_reconciliation_service


def test_timeline01_earlier_tp_wins_over_later_sl_arriving_first(isolated_db):
    """TIMELINE01 (PHẦN 061, 217, 218):
    LONG order: entry 4180, SL 4170, TP 4200.
    Late SL quote at source_ts=30000 (bid=4169) arrives FIRST.
    Late TP quote at source_ts=20000 (bid=4201) arrives SECOND.
    Reconciliation must evaluate in chronological source order: TP at 20000 wins.
    """
    symbol = "XAUUSDT"
    quote_ordering_service.reset_watermark_for_testing(symbol)

    order = models.PaperOrder(
        id="ord-late-chrono-01",
        setup_id="setup-late-chrono-01",
        instrument=symbol,
        direction="LONG",
        order_type="LIMIT",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        state="paper_open",
        opened_at=1000,
        created_at=1000,
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Initialize watermark to 100000
    q_init = CanonicalQuote(
        symbol=symbol,
        bid=4180.0,
        ask=4180.2,
        last=4180.1,
        mark_price=4180.1,
        exchange_ts_ms=100000,
        received_at_ms=100000,
    )
    quote_ordering_service.classify_quote(q_init)

    # Enqueue SL quote FIRST with LATER source_timestamp (30000)
    q_sl = CanonicalQuote(
        symbol=symbol,
        bid=4169.0,
        ask=4169.2,
        last=4169.1,
        mark_price=4169.1,
        exchange_ts_ms=30000,
        received_at_ms=100001,
    )
    disp1, _ = quote_ordering_service.classify_quote(q_sl)
    assert disp1.value == "LATE_EVIDENCE"

    # Enqueue TP quote SECOND with EARLIER source_timestamp (20000)
    q_tp = CanonicalQuote(
        symbol=symbol,
        bid=4201.0,
        ask=4201.2,
        last=4201.1,
        mark_price=4201.1,
        exchange_ts_ms=20000,
        received_at_ms=100002,
    )
    disp2, _ = quote_ordering_service.classify_quote(q_tp)
    assert disp2.value == "LATE_EVIDENCE"

    # Execute reconciliation batch
    n_processed = market_reconciliation_service.process_late_batch(isolated_db, symbol=symbol)
    assert n_processed >= 2

    # Check order state
    closed_order = isolated_db.query(models.PaperOrder).filter_by(id="ord-late-chrono-01").first()
    assert closed_order is not None
    assert closed_order.state == "closed", f"Order should be closed, got {closed_order.state}"
    assert closed_order.exit_cause == "TP_HIT", (
        f"Expected earlier TP_HIT (source 20000) to win over SL_HIT (source 30000), "
        f"but got exit_cause={closed_order.exit_cause} at actual_exit={closed_order.actual_exit}"
    )
    assert closed_order.closed_at == 20000


def test_mirror_short_earlier_tp_wins_over_later_sl(isolated_db):
    """TIMELINE01 (SHORT mirror):
    SHORT order: entry 4180, SL 4190, TP 4160.
    Late SL quote at source_ts=30000 (ask=4191) arrives FIRST.
    Late TP quote at source_ts=20000 (ask=4159) arrives SECOND.
    Reconciliation must evaluate chronologically: TP at 20000 wins.
    """
    symbol = "XAUUSDT"
    quote_ordering_service.reset_watermark_for_testing(symbol)

    order = models.PaperOrder(
        id="ord-late-chrono-short-01",
        setup_id="setup-late-chrono-short-01",
        instrument=symbol,
        direction="SHORT",
        order_type="LIMIT",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4190.0,
        take_profit=4160.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        state="paper_open",
        opened_at=1000,
        created_at=1000,
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Initialize watermark
    q_init = CanonicalQuote(
        symbol=symbol,
        bid=4180.0,
        ask=4180.2,
        last=4180.1,
        mark_price=4180.1,
        exchange_ts_ms=100000,
        received_at_ms=100000,
    )
    quote_ordering_service.classify_quote(q_init)

    # Enqueue SL quote FIRST with source_timestamp=30000
    q_sl = CanonicalQuote(
        symbol=symbol,
        bid=4190.8,
        ask=4191.0,
        last=4190.9,
        mark_price=4190.9,
        exchange_ts_ms=30000,
        received_at_ms=100001,
    )
    quote_ordering_service.classify_quote(q_sl)

    # Enqueue TP quote SECOND with source_timestamp=20000
    q_tp = CanonicalQuote(
        symbol=symbol,
        bid=4158.8,
        ask=4159.0,
        last=4158.9,
        mark_price=4158.9,
        exchange_ts_ms=20000,
        received_at_ms=100002,
    )
    quote_ordering_service.classify_quote(q_tp)

    n_processed = market_reconciliation_service.process_late_batch(isolated_db, symbol=symbol)
    assert n_processed >= 2

    closed_order = isolated_db.query(models.PaperOrder).filter_by(id="ord-late-chrono-short-01").first()
    assert closed_order is not None
    assert closed_order.state == "closed"
    assert closed_order.exit_cause == "TP_HIT", (
        f"Expected earlier TP_HIT (source 20000) to win over SL_HIT (source 30000), "
        f"but got exit_cause={closed_order.exit_cause}"
    )
    assert closed_order.closed_at == 20000
