"""
backend/tests/test_realtime_source_ordering.py
Tests for realtime quote source ordering gate and watermark monotonicity (RT01–RT05).
"""

import time
import pytest
from services.quote_validator import CanonicalQuote
from services.quote_ordering_service import (
    QuoteOrderingService,
    QuoteDisposition,
    quote_ordering_service,
)
from services.bitget_ws_service import BitgetWSService


def _make_quote(symbol="XAUUSDT", bid=4180.0, ask=4180.5, last=4180.2, exchange_ts=1728000000000, epoch=1) -> CanonicalQuote:
    return CanonicalQuote(
        symbol=symbol,
        bid=bid,
        ask=ask,
        last=last,
        mark_price=last,
        exchange_ts_ms=exchange_ts,
        received_at_ms=exchange_ts + 50,
        source="WS",
        connection_epoch=epoch,
        status="VALID",
    )


def test_rt01_newer_to_older_monotonic_watermark():
    """RT01 / TC-RT-MONOTONIC: Dispatching quote A (ts=5000), then quote B (ts=4000).
    Watermark must stay at 5000; quote B must be classified as LATE_EVIDENCE,
    and BitgetWSService.latest_quote must remain quote A, not roll back to B.
    """
    svc = BitgetWSService()
    quote_ordering_service.reset_watermark_for_testing("XAUUSDT")

    q_a = _make_quote(last=4180.0, exchange_ts=5000)
    q_b = _make_quote(last=4170.0, exchange_ts=4000)

    svc._dispatch_quote(q_a)
    assert svc.latest_quote.last == 4180.0
    assert svc.latest_quote.exchange_ts_ms == 5000

    svc._dispatch_quote(q_b)
    # latest_quote must NOT roll back to 4170.0!
    assert svc.latest_quote.last == 4180.0
    assert svc.latest_quote.exchange_ts_ms == 5000

    # Service state check
    state = quote_ordering_service.get_state("XAUUSDT")
    assert state.watermark_ms == 5000
    assert state.total_accepted == 1
    assert state.total_late == 1


def test_rt02_sametime_identical_duplicate_vs_legitimate_changed_bidask():
    """RT02 / TC-RT-EQUAL-TS: Same timestamp with identical digest is DUPLICATE.
    Same timestamp with changed bid/ask is ACCEPT_CURRENT.
    """
    quote_ordering_service.reset_watermark_for_testing("XAUUSDT")

    q1 = _make_quote(bid=4180.0, ask=4180.5, last=4180.2, exchange_ts=6000)
    disp1, _ = quote_ordering_service.classify_quote(q1)
    assert disp1 == QuoteDisposition.ACCEPT_CURRENT

    # Identical replay at same timestamp -> DUPLICATE
    q2_dup = _make_quote(bid=4180.0, ask=4180.5, last=4180.2, exchange_ts=6000)
    disp2, reason2 = quote_ordering_service.classify_quote(q2_dup)
    assert disp2 == QuoteDisposition.DUPLICATE
    assert "IDENTICAL" in reason2

    # Changed price at same timestamp -> ACCEPT_CURRENT
    q3_changed = _make_quote(bid=4180.5, ask=4181.0, last=4180.8, exchange_ts=6000)
    disp3, reason3 = quote_ordering_service.classify_quote(q3_changed)
    assert disp3 == QuoteDisposition.ACCEPT_CURRENT
    assert "EQUAL_TS_PRICE_UPDATE" in reason3


def test_rt03_reconnect_epoch_and_rest_fallback_preserve_watermark():
    """RT03 / TC-RT-RECONNECT-WATERMARK: Reconnect epoch increment and REST fallback
    do not reset source watermark to 0 or allow older quotes to fill.
    """
    quote_ordering_service.reset_watermark_for_testing("XAUUSDT")

    q1 = _make_quote(exchange_ts=10000, epoch=1)
    quote_ordering_service.classify_quote(q1)
    state = quote_ordering_service.get_state("XAUUSDT")
    assert state.watermark_ms == 10000

    # Transport reconnects with epoch 2
    quote_ordering_service.on_reconnect_or_source_switch("XAUUSDT", new_epoch=2, source="WS_RECONNECT")
    assert state.watermark_ms == 10000

    # Stale replay quote with ts=9000 under new epoch must still be rejected as LATE_EVIDENCE
    q_stale = _make_quote(exchange_ts=9000, epoch=2)
    disp, _ = quote_ordering_service.classify_quote(q_stale)
    assert disp == QuoteDisposition.LATE_EVIDENCE
    assert state.watermark_ms == 10000

    # Switch to REST fallback
    quote_ordering_service.on_reconnect_or_source_switch("XAUUSDT", new_epoch=3, source="REST_FALLBACK")
    assert state.watermark_ms == 10000

    # REST quote with ts=10500 is accepted
    q_rest = _make_quote(exchange_ts=10500, epoch=3)
    disp_rest, _ = quote_ordering_service.classify_quote(q_rest)
    assert disp_rest == QuoteDisposition.ACCEPT_CURRENT
    assert state.watermark_ms == 10500


def test_rt04_late_tick_before_open_cannot_tp_sl_new_order(isolated_db):
    """RT04 / TC-RT-LATE-TICK-BEFORE-OPEN: Quote with source timestamp before an order's opened_at
    cannot retroactively trigger TP/SL on that new order.
    """
    import models
    from services.execution_consumer import execution_consumer

    isolated_db.query(models.PaperOrder).delete()
    # Order opened at ts=10000
    order = models.PaperOrder(
        id="order-rt04-test",
        instrument="XAUUSDT",
        direction="SHORT",
        state="paper_open",
        actual_entry=4180.0,
        stop_loss=4185.0,
        take_profit=4160.0,
        created_at=10000,
        opened_at=10000,
        planned_entry=4180.0,
        initial_risk_usdt=2.5,
        quantity=0.28,
        cost_snapshot="{}",
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Quote with source timestamp 9000 (BEFORE opened_at 10000) that touches SL (ask = 4186.0)
    q_old_sl = _make_quote(bid=4185.5, ask=4186.0, last=4185.8, exchange_ts=9000)

    # Evaluate quote
    res = execution_consumer.evaluate_quote_sync(isolated_db, q_old_sl, now_ms=10500)

    # The order must NOT be closed because the quote exchange timestamp predates opened_at
    isolated_db.refresh(order)
    assert order.state == "paper_open"
    assert res.get("exit_processed") is False


def test_rt05_late_tick_for_open_order_reconciliation(isolated_db):
    """RT05 / TC-RT-LATE-RECONCILIATION: Quote whose exchange timestamp is AFTER opened_at
    can close the position and preserves audit trail.
    """
    import models
    from services.execution_consumer import execution_consumer

    isolated_db.query(models.PaperOrder).delete()
    order = models.PaperOrder(
        id="order-rt05-test",
        instrument="XAUUSDT",
        direction="SHORT",
        state="paper_open",
        actual_entry=4180.0,
        stop_loss=4185.0,
        take_profit=4160.0,
        created_at=10000,
        opened_at=10000,
        planned_entry=4180.0,
        initial_risk_usdt=2.5,
        quantity=0.28,
        cost_snapshot="{}",
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Valid quote at ts=11000 (> opened_at 10000) touching TP (ask = 4159.0)
    q_tp = _make_quote(bid=4158.5, ask=4159.0, last=4158.8, exchange_ts=11000)
    res = execution_consumer.evaluate_quote_sync(isolated_db, q_tp, now_ms=11050)

    isolated_db.refresh(order)
    assert order.state == "closed"
    assert order.exit_cause == "TP_HIT"
    assert res.get("exit_processed") is True
