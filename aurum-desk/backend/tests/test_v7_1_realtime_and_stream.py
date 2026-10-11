import pytest
import time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models, crud, schemas
from services.quote_validator import QuoteValidator, CanonicalQuote
from services.execution_consumer import ExecutionConsumer
from services.candle_cache_service import CandleCacheService
from services.analysis_cache_service import AnalysisCacheService
from services.bitget_ws_service import BitgetWSService
from services.trade_lifecycle_service import TradeLifecycleService
from domain_calculator import CalculationResult

@pytest.fixture
def isolated_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()

def test_canonical_quote_validation():
    """Verify CanonicalQuote validates numeric values, positive prices, spreads, and freshness."""
    now_ms = 1728447000000

    # 1. Valid quote
    valid_raw = {
        "symbol": "XAUUSDT",
        "bid": "2650.40",
        "ask": "2650.60",
        "last": "2650.50",
        "mark_price": "2650.55",
        "exchange_ts_ms": now_ms - 200,
        "received_at_ms": now_ms
    }
    q = QuoteValidator.validate_canonical(valid_raw, source="WS", connection_epoch=1, now_ms=now_ms)
    assert q.is_valid is True
    assert q.bid == 2650.40
    assert q.ask == 2650.60
    assert q.last == 2650.50
    assert q.status == "VALID"
    assert q.spread == 0.20

    # 2. Inverted spread (ask < bid)
    inverted_raw = {
        "symbol": "XAUUSDT",
        "bid": "2651.00",
        "ask": "2650.00",
        "last": "2650.50",
        "exchange_ts_ms": now_ms,
        "received_at_ms": now_ms
    }
    q_inv = QuoteValidator.validate_canonical(inverted_raw, source="WS", now_ms=now_ms)
    assert q_inv.is_valid is False
    assert q_inv.status == "INVERTED"
    assert q_inv.rejection_code == "INVERTED_SPREAD"

    # 3. Non-finite / NaN
    nan_raw = {
        "symbol": "XAUUSDT",
        "bid": float("nan"),
        "ask": "2650.00",
        "last": "2650.00"
    }
    q_nan = QuoteValidator.validate_canonical(nan_raw, source="WS", now_ms=now_ms)
    assert q_nan.is_valid is False
    assert q_nan.status == "MALFORMED"

    # 4. Stale quote (>15s)
    stale_raw = {
        "symbol": "XAUUSDT",
        "bid": "2650.00",
        "ask": "2650.50",
        "last": "2650.25",
        "exchange_ts_ms": now_ms - 25000,
        "received_at_ms": now_ms
    }
    q_stale = QuoteValidator.validate_canonical(stale_raw, source="WS", now_ms=now_ms, max_age_sec=15.0)
    assert q_stale.is_valid is False
    assert q_stale.status == "STALE"
    assert q_stale.rejection_code == "TICKER_STALE"

def test_execution_consumer_ordered_touch_exit(isolated_db):
    """
    Test rapid touch of TP within 1.5s:
    Ordered consumer must close on the very first qualifying quote, even if quote subsequently reverses!
    """
    now_ms = 1728447000000
    consumer = ExecutionConsumer(max_queue_size=100)

    # 1. Create active LONG open order in DB
    order = models.PaperOrder(
        id="ord-test-long-1",
        setup_id="setup-1",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=2650.0,
        actual_entry=2650.0,
        stop_loss=2640.0,
        take_profit=2670.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        created_at=now_ms - 60000,
        opened_at=now_ms - 60000
    )
    isolated_db.add(order)
    isolated_db.commit()

    # 2. Quote 1: Price touches TP (Bid = 2670.50)
    q1 = CanonicalQuote(
        symbol="XAUUSDT",
        last=2670.50,
        bid=2670.50,
        ask=2670.70,
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
        status="VALID"
    )

    res1 = consumer.evaluate_quote_sync(isolated_db, q1, now_ms=now_ms)
    assert res1["exit_processed"] is True
    assert res1["exit_cause"] == "TP_HIT"

    # Verify order is closed in DB
    closed_order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == "ord-test-long-1").first()
    assert closed_order.state == "closed"
    assert closed_order.exit_cause == "TP_HIT"
    assert closed_order.actual_exit == 2670.0

    # 3. Quote 2: 200ms later, price falls back to 2660.00
    q2 = CanonicalQuote(
        symbol="XAUUSDT",
        last=2660.00,
        bid=2659.80,
        ask=2660.20,
        exchange_ts_ms=now_ms + 200,
        received_at_ms=now_ms + 200,
        status="VALID"
    )
    res2 = consumer.evaluate_quote_sync(isolated_db, q2, now_ms=now_ms + 200)
    # Already closed, idempotent, no further exit
    assert res2["exit_processed"] is False

def test_execution_consumer_short_exit_uses_ask(isolated_db):
    """SHORT exit must use executable Ask price for SL/TP evaluation."""
    now_ms = 1728447000000
    consumer = ExecutionConsumer(max_queue_size=100)

    # Create active SHORT open order
    order = models.PaperOrder(
        id="ord-test-short-1",
        setup_id="setup-2",
        instrument="XAUUSDT",
        direction="SHORT",
        state="paper_open",
        planned_entry=2650.0,
        actual_entry=2650.0,
        stop_loss=2660.0,
        take_profit=2630.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        created_at=now_ms - 60000,
        opened_at=now_ms - 60000
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Quote where Bid touched TP (2629.50) but Ask did not (2630.50 > 2630.0)
    q_near = CanonicalQuote(
        symbol="XAUUSDT",
        last=2630.0,
        bid=2629.50,
        ask=2630.50,
        status="VALID",
        received_at_ms=now_ms
    )
    res_near = consumer.evaluate_quote_sync(isolated_db, q_near, now_ms=now_ms)
    assert res_near["exit_processed"] is False

    # Quote where executable Ask touches TP (2629.80 <= 2630.0)
    q_hit = CanonicalQuote(
        symbol="XAUUSDT",
        last=2629.5,
        bid=2629.00,
        ask=2629.80,
        status="VALID",
        received_at_ms=now_ms + 100
    )
    res_hit = consumer.evaluate_quote_sync(isolated_db, q_hit, now_ms=now_ms + 100)
    assert res_hit["exit_processed"] is True
    assert res_hit["exit_cause"] == "TP_HIT"

def test_candle_cache_boundary_rollover():
    """Verify CandleCacheService advances open bar and finalizes closed bar at timeframe boundary via real candle payloads."""
    cache = CandleCacheService(max_bars=50)

    # 1. First candle update at t0
    t0 = 1728447000000
    rows1 = [[str(t0), "2650.0", "2650.0", "2650.0", "2650.0", "10.5", "27825.0", "27825.0"]]
    closed1 = cache.process_candle_payload("XAUUSDT", "1M", rows1)
    assert len(closed1) == 0

    candles_1m = cache.get_candles("XAUUSDT", "1M")
    assert len(candles_1m) == 1
    assert candles_1m[-1].open == 2650.0
    assert candles_1m[-1].close == 2650.0
    assert candles_1m[-1].volume == 10.5

    # 2. Second update inside same 1M window: cumulative volume replaces, high/low/close updated
    rows2 = [[str(t0), "2650.0", "2655.0", "2649.5", "2654.0", "15.2", "40340.0", "40340.0"]]
    closed2 = cache.process_candle_payload("XAUUSDT", "1M", rows2)
    assert len(closed2) == 0
    assert candles_1m[-1].high == 2655.0
    assert candles_1m[-1].low == 2649.5
    assert candles_1m[-1].close == 2654.0
    assert candles_1m[-1].volume == 15.2

    # 3. Third update crosses 1M boundary (t0 + 60_000)
    t1 = t0 + 60000
    rows3 = [[str(t1), "2654.0", "2658.0", "2653.0", "2656.0", "8.0", "21248.0", "21248.0"]]
    closed3 = cache.process_candle_payload("XAUUSDT", "1M", rows3)
    assert len(closed3) == 1
    closed_bar = closed3[0]
    assert closed_bar.is_closed is True
    assert closed_bar.timestamp == t0
    assert closed_bar.high == 2655.0
    assert closed_bar.close == 2654.0
    assert closed_bar.volume == 15.2

    # Now cache should have 2 bars
    updated_1m = cache.get_candles("XAUUSDT", "1M")
    assert len(updated_1m) == 2
    assert updated_1m[-1].timestamp == t1
    assert updated_1m[-1].open == 2654.0
    assert updated_1m[-1].is_closed is False

def test_analysis_cache_single_flight():
    """Verify AnalysisCacheService caches results and invalidates properly."""
    service = AnalysisCacheService(ttl_sec=10.0)

    # Empty cache initially
    assert ("XAUUSDT", "15M") not in service._cache

    service._cache[("XAUUSDT", "15M")] = {"status": "TEST_CACHED", "trend": "BULLISH"}
    service._last_as_of[("XAUUSDT", "15M")] = int(time.time() * 1000)

    # Invalidation on bar closed
    service.on_bar_closed("XAUUSDT", "15M")
    assert ("XAUUSDT", "15M") not in service._cache
