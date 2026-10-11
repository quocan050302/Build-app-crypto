import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models, crud, schemas
from services.bitget_ws_service import BitgetWSService, CANDLE_CHANNELS, ALL_SUBSCRIBE_CHANNELS
from services.candle_cache_service import CandleCacheService, validate_candle_row
from services.execution_consumer import ExecutionConsumer
from services.analysis_cache_service import AnalysisCacheService
from services.quote_validator import CanonicalQuote
from services.event_bus import EventBus
from services.clock import IClock

# Virtual Clock for deterministic time testing
class VirtualClock(IClock):
    def __init__(self, start_ms: int = 1728447000000):
        self._now_ms = start_ms

    def now_ms(self) -> int:
        return self._now_ms

    def now_datetime(self) -> datetime:
        return datetime.fromtimestamp(self._now_ms / 1000.0, tz=timezone.utc)

    def advance_ms(self, delta_ms: int):
        self._now_ms += delta_ms


def create_isolated_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


# ==================== 1. BITGET WS PROTOCOL HANDSHAKE & ACK ====================

def test_bitget_subscription_ack_protocol_fixtures():
    """
    Test Section 4.1:
    - Ack without 'code' field passes and marks channel ACKED.
    - Ack with code=0 (int) and code="0" (string) pass.
    - Ack with non-zero code or event="error" fails and marks channel FAILED.
    - Unknown/mismatched arg does not ack requested channels.
    """
    svc = BitgetWSService(symbol="XAUUSDT")
    assert svc.channel_registry["ticker"] == "PENDING"
    assert svc.channel_registry["candle1m"] == "PENDING"

    # 1. Standard Bitget Classic Ack (NO code field)
    ack_no_code = {
        "event": "subscribe",
        "arg": {"instType": "USDT-FUTURES", "channel": "ticker", "instId": "XAUUSDT"}
    }
    assert svc.handle_ack(ack_no_code) is True
    assert svc.channel_registry["ticker"] == "ACKED"
    assert svc.transport_state == "CONNECTED"

    # 2. Ack with integer 0
    ack_int_zero = {
        "event": "subscribe",
        "arg": {"instType": "USDT-FUTURES", "channel": "candle1m", "instId": "XAUUSDT"},
        "code": 0
    }
    assert svc.handle_ack(ack_int_zero) is True
    assert svc.channel_registry["candle1m"] == "ACKED"

    # 3. Ack with string "0"
    ack_str_zero = {
        "event": "subscribe",
        "arg": {"instType": "USDT-FUTURES", "channel": "candle5m", "instId": "XAUUSDT"},
        "code": "0"
    }
    assert svc.handle_ack(ack_str_zero) is True
    assert svc.channel_registry["candle5m"] == "ACKED"

    # 4. Error response / non-zero code
    ack_error = {
        "event": "error",
        "arg": {"instType": "USDT-FUTURES", "channel": "candle1H", "instId": "XAUUSDT"},
        "code": "40001",
        "msg": "Invalid channel"
    }
    assert svc.handle_ack(ack_error) is False
    assert svc.channel_registry["candle1H"] == "FAILED"

    # 5. Mismatched instrument / unknown channel
    ack_mismatch = {
        "event": "subscribe",
        "arg": {"instType": "USDT-FUTURES", "channel": "ticker", "instId": "BTCUSDT"}
    }
    assert svc.handle_ack(ack_mismatch) is False


def test_bitget_heartbeat_pong_timeout_and_reconnect():
    """
    Test Section 4.4:
    - Heartbeat sends text 'ping'.
    - If no 'pong' received within pong_timeout, forces socket close to trigger reconnect.
    """
    async def _run():
        svc = BitgetWSService(symbol="XAUUSDT")
        svc._running = True

        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        mock_ws.close = AsyncMock()

        # Run heartbeat with short virtual intervals: ping every 0.02s, pong_timeout 0.03s
        heartbeat_task = asyncio.create_task(
            svc._heartbeat_loop(mock_ws, ping_interval=0.02, pong_timeout=0.03)
        )

        # Let ping trigger, but never send pong
        await asyncio.sleep(0.08)
        mock_ws.send.assert_called_with("ping")

        # Due to missing pong, ws.close should have been called!
        assert mock_ws.close.called
        assert svc.last_error == "PONG_TIMEOUT"

        svc.stop()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(_run())


# ==================== 2. REAL OHLCV CANDLE PARSER & ROW VALIDATION ====================

def test_candle_row_validation():
    """
    Test Section 4.3:
    - Min fields, finite OHLC, low <= min(open, close), high >= max(open, close), high >= low.
    - Malformed or negative volume rejected.
    """
    # Valid row
    valid_row = ["1728447000000", "4170.00", "4172.00", "4169.00", "4171.50", "12.34", "51479.00", "51479.00"]
    parsed = validate_candle_row(valid_row)
    assert parsed is not None
    assert parsed["open"] == 4170.0
    assert parsed["high"] == 4172.0
    assert parsed["low"] == 4169.0
    assert parsed["close"] == 4171.5
    assert parsed["volume"] == 12.34

    # Geometric violation: low > min(open, close)
    bad_geom_row = ["1728447000000", "4170.00", "4172.00", "4171.00", "4170.50", "12.34"]
    assert validate_candle_row(bad_geom_row) is None

    # High < Low
    inverted_row = ["1728447000000", "4170.00", "4165.00", "4172.00", "4168.00", "10.0"]
    assert validate_candle_row(inverted_row) is None

    # Negative volume
    neg_vol_row = ["1728447000000", "4170.00", "4172.00", "4169.00", "4171.50", "-5.0"]
    assert validate_candle_row(neg_vol_row) is None


def test_cumulative_volume_replace_and_authoritative_correction():
    """
    Test Section 4.3 & 5:
    - Do NOT cumulative '+=' volume on subsequent updates of the same bar: replace with new cumulative revision.
    - REST authoritative candles replace synthetic / cached bars cleanly.
    """
    cache = CandleCacheService(max_bars=100)
    t0 = 1728447000000

    # Push 1: Volume = 10.0
    cache.process_candle_payload("XAUUSDT", "5M", [
        [str(t0), "4170.00", "4171.00", "4169.00", "4170.50", "10.0"]
    ])
    candles = cache.get_candles("XAUUSDT", "5M")
    assert len(candles) == 1
    assert candles[0].volume == 10.0

    # Push 2: Volume revised to 14.5 (cumulative, NOT 10.0 + 14.5 = 24.5)
    cache.process_candle_payload("XAUUSDT", "5M", [
        [str(t0), "4170.00", "4172.00", "4169.00", "4171.80", "14.5"]
    ])
    assert len(candles) == 1
    assert candles[0].volume == 14.5  # Replaced, not added!
    assert candles[0].high == 4172.0

    # Test REST authoritative replacement:
    rest_candles = [
        schemas.CandleCreate(
            symbol="XAUUSDT",
            timeframe="5M",
            timestamp=t0,
            open=4170.0,
            high=4173.0,
            low=4168.5,
            close=4172.0,
            volume=16.8,
            is_closed=True
        )
    ]
    cache.update_from_rest_sync("XAUUSDT", "5M", rest_candles)
    updated = cache.get_candles("XAUUSDT", "5M")
    assert len(updated) == 1
    assert updated[0].high == 4173.0
    assert updated[0].volume == 16.8
    assert updated[0].is_closed is True


# ==================== 3. SINGLE EXECUTION OWNER & STALE BACKLOG ====================

def test_stale_backlog_quote_blocked_from_opening_order():
    """
    Test Section 6:
    - Consumer re-validates freshness against current processing time (clock.now_ms()).
    - A backlog quote older than 15s relative to processing time CANNOT trigger armed order fill!
    """
    isolated_db = create_isolated_db()
    clock = VirtualClock(start_ms=1728447000000)
    consumer = ExecutionConsumer(max_queue_size=50)

    # Create an armed order
    armed = models.PaperOrder(
        id="ord-armed-1",
        setup_id="setup-1",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        planned_entry=2650.0,
        stop_loss=2640.0,
        take_profit=2670.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        created_at=clock.now_ms() - 60000
    )
    isolated_db.add(armed)
    isolated_db.commit()

    # Create a quote that was received 20 seconds ago (sat in backlog)
    stale_quote_ts = clock.now_ms() - 20000
    q_backlog = CanonicalQuote(
        symbol="XAUUSDT",
        last=2650.0,
        bid=2649.9,
        ask=2650.0,  # Touches entry
        exchange_ts_ms=stale_quote_ts,
        received_at_ms=stale_quote_ts,
        status="VALID"
    )

    # Evaluate at current clock time: processing time is 20s later than quote!
    res = consumer.evaluate_quote_sync(isolated_db, q_backlog, clock=clock)
    assert res["is_stale"] is True
    assert res["entry_processed"] is False
    assert "BACKLOG_QUOTE_STALE" in res.get("rejection_reason", "")

    # Verify armed order remained armed and was NOT filled on stale quote
    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == "ord-armed-1").first()
    assert order.state == "armed"
    isolated_db.close()


def test_queue_overflow_marks_execution_gap_and_blocks_eligibility():
    """
    Test Section 6:
    - When consumer queue overflows, dropped quotes trigger has_execution_gap.
    - Shared eligibility evaluator blocks all new entries with EXECUTION_FEED_DEGRADED.
    """
    isolated_db = create_isolated_db()
    consumer = ExecutionConsumer(max_queue_size=2)

    q = CanonicalQuote(symbol="XAUUSDT", last=2650.0, bid=2649.9, ask=2650.1, status="VALID")
    assert consumer.enqueue_quote(q) is True
    assert consumer.enqueue_quote(q) is True

    # 3rd quote exceeds capacity -> dropped!
    assert consumer.enqueue_quote(q) is False
    assert consumer.dropped_count == 1
    assert consumer.has_execution_gap is True
    assert consumer.is_lagging is True

    now_ms = int(time.time() * 1000)
    # Check eligibility evaluator
    from services.eligibility_service import evaluate_setup_eligibility
    setup = models.WatchSetup(
        id="setup-elig-1",
        direction="LONG",
        state="READY",
        provisional_entry=2650.0,
        provisional_sl=2640.0,
        provisional_tp=2670.0,
        invalidation_price=2635.0,
        confirmed_entry=2650.0,
        confirmed_sl=2640.0,
        confirmed_tp=2670.0,
        margin_mode="ISOLATED",
        created_at=now_ms,
        updated_at=now_ms
    )
    isolated_db.add(setup)
    isolated_db.commit()

    with patch("services.execution_consumer.execution_consumer", consumer):
        eval_res = evaluate_setup_eligibility(isolated_db, setup)
        assert eval_res["can_arm"] is False
        assert "EXECUTION_FEED_DEGRADED" in eval_res["reason_codes"]
    isolated_db.close()


# ==================== 4. DOMAIN EVENTS THREAD-SAFE BRIDGE & SINGLE DELIVERY ====================

def test_domain_event_threadsafe_bridge_and_single_delivery():
    """
    Test Section 3.E & 7:
    - Domain events emitted from worker thread (via asyncio.to_thread) bridge safely to main loop.
    - No RuntimeError dropped.
    - Exactly one broadcast delivery via market_broadcaster.
    - Rollback produces zero broadcasts.
    """
    async def _run():
        isolated_db = create_isolated_db()
        bus = EventBus()
        main_loop = asyncio.get_running_loop()
        bus.set_main_loop(main_loop)

        mock_broadcaster = MagicMock()
        mock_broadcaster.broadcast_domain_event = AsyncMock()

        with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
            # Case 1: Successful transaction commit -> broadcast delivered
            def worker_function():
                # Runs inside a worker thread without a running event loop!
                bus.publish_event(
                    event_type="trade.opened",
                    aggregate_id="trade-123",
                    payload={"entry_price": 2650.0, "direction": "LONG"},
                    db=isolated_db
                )
                isolated_db.commit()  # Triggers after_commit hook!

            await asyncio.to_thread(worker_function)
            await asyncio.sleep(0.05)  # Allow threadsafe coroutine to schedule

            # Verify broadcast was called exactly once through market_broadcaster
            assert mock_broadcaster.broadcast_domain_event.call_count == 1
            call_arg = mock_broadcaster.broadcast_domain_event.call_args[0][0]
            assert call_arg["event_type"] == "trade.opened"
            assert call_arg["aggregate_id"] == "trade-123"

            # Case 2: Transaction rollback -> zero broadcast!
            mock_broadcaster.broadcast_domain_event.reset_mock()
            def rollback_worker():
                bus.publish_event(
                    event_type="trade.closed",
                    aggregate_id="trade-456",
                    payload={"exit_price": 2670.0, "direction": "LONG"},
                    db=isolated_db
                )
                isolated_db.rollback()  # Rollback, after_commit must NOT fire!

            await asyncio.to_thread(rollback_worker)
            await asyncio.sleep(0.05)
            assert mock_broadcaster.broadcast_domain_event.call_count == 0

        isolated_db.close()

    asyncio.run(_run())


# ==================== 5. ANALYSIS CACHE CONCURRENCY & REFERENCE QUOTE ====================

def test_analysis_cache_single_flight_and_reference_quote():
    """
    Test Section 8:
    - Single-flight compute: 5 concurrent callers only trigger compute once.
    - When live quote is missing, outputs quote_source=CANDLE_REFERENCE and executable=False.
    """
    async def _run():
        isolated_db = create_isolated_db()
        service = AnalysisCacheService(ttl_sec=10.0)

        # Populate dummy candles so SMC does not abort on insufficient data
        from services.candle_cache_service import candle_cache_service
        test_candles = [
            schemas.CandleCreate(
                symbol="XAUUSDT",
                timeframe="15M",
                timestamp=1728447000000 + i * 900000,
                open=2650.0 + i,
                high=2655.0 + i,
                low=2648.0 + i,
                close=2652.0 + i,
                volume=100.0,
                is_closed=True
            )
            for i in range(25)
        ]
        candle_cache_service.update_from_rest_sync("XAUUSDT", "15M", test_candles)

        # 1. Missing quote test
        res = service.compute_analysis_sync(isolated_db, "XAUUSDT", "15M", latest_quote=None)
        assert res.get("quote_source") == "CANDLE_REFERENCE"
        assert res.get("executable") is False

        # Invalidate cache so next concurrent calls trigger compute
        service.invalidate("XAUUSDT", "15M")

        # 2. Concurrency test: 5 simultaneous requests for the same key
        compute_spy = MagicMock(wraps=service.compute_analysis_sync)
        service.compute_analysis_sync = compute_spy

        tasks = [
            service.get_or_compute_analysis("XAUUSDT", "15M", latest_quote=None)
            for _ in range(5)
        ]
        results = await asyncio.gather(*tasks)

        assert len(results) == 5
        # Single-flight lock ensures compute_analysis_sync was executed exactly once!
        assert compute_spy.call_count == 1
        isolated_db.close()

    asyncio.run(_run())
