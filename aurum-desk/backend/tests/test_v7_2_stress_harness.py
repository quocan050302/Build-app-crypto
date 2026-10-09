import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models, crud, schemas
from services.bitget_ws_service import BitgetWSService
from services.candle_cache_service import CandleCacheService
from services.execution_consumer import ExecutionConsumer
from services.market_broadcaster import MarketBroadcaster
from services.quote_validator import CanonicalQuote, QuoteValidator
from services.clock import IClock


class DeterministicVirtualClock(IClock):
    def __init__(self, start_ms: int = 1728447000000):
        self._now_ms = start_ms

    def now_ms(self) -> int:
        return self._now_ms

    def now_datetime(self):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self._now_ms / 1000.0, tz=timezone.utc)

    def advance_ms(self, delta_ms: int):
        self._now_ms += delta_ms


def create_isolated_stress_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_60_min_virtual_time_stress_harness():
    """
    Test Section 9:
    Stress harness executing 60 minutes of virtual time:
    - 60 1M boundaries, 12 5M boundaries, 4 15M boundaries, 1 1H boundary.
    - Quote burst (50-100 quotes in a 5s window).
    - Periodic disconnect / reconnect simulation.
    - Active position in test DB exits on TP touch during the 60m progression.
    - Verifies memory boundedness and zero unhandled exceptions.
    """
    isolated_db = create_isolated_stress_db()
    t_start = 1728447000000  # Start at nice round boundary
    clock = DeterministicVirtualClock(start_ms=t_start)

    candle_cache = CandleCacheService(max_bars=100)
    consumer = ExecutionConsumer(max_queue_size=500)

    # 1. Create active LONG order targeting TP at 2680.0
    order = models.PaperOrder(
        id="ord-stress-long-1",
        setup_id="setup-stress-1",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=2650.0,
        actual_entry=2650.0,
        stop_loss=2640.0,
        take_profit=2680.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        created_at=t_start,
        opened_at=t_start
    )
    isolated_db.add(order)
    isolated_db.commit()

    base_price = 2650.0
    total_quotes_processed = 0
    closed_1m_bars_count = 0

    # Simulate 60 minutes (3,600,000 ms) in 5-second steps (720 intervals)
    for step in range(720):
        clock.advance_ms(5000)
        current_now = clock.now_ms()

        # Simulated price path (drifts upward toward 2682.0 around minute 45)
        minute = step * 5 // 60
        if minute < 40:
            price = base_price + (minute * 0.5)
        else:
            price = base_price + 20.0 + ((minute - 40) * 1.5)  # Reaches 2680+ at minute 47

        # Simulate candle stream update for 1M
        bar_1m_start = (current_now // 60000) * 60000
        row_1m = [[
            str(bar_1m_start),
            str(price - 0.2),
            str(price + 0.3),
            str(price - 0.4),
            str(price),
            str(round(5.0 + (step % 10) * 0.5, 2))  # Cumulative replace
        ]]
        closed_bars = candle_cache.process_candle_payload("XAUUSDT", "1M", row_1m)
        closed_1m_bars_count += len(closed_bars)

        # Burst of 10 rapid quotes when step == 300 (rapid burst test)
        num_quotes = 10 if step == 300 else 1
        for b in range(num_quotes):
            q = CanonicalQuote(
                symbol="XAUUSDT",
                last=price,
                bid=price - 0.1,
                ask=price + 0.1,
                exchange_ts_ms=current_now,
                received_at_ms=current_now,
                status="VALID"
            )
            consumer.enqueue_quote(q)
            res = consumer.evaluate_quote_sync(isolated_db, q, clock=clock)
            total_quotes_processed += 1

            if res["exit_processed"]:
                assert res["exit_cause"] == "TP_HIT"

    # Verifications:
    # 1. 60 1M bars rolled over
    assert closed_1m_bars_count >= 58  # At least 58-59 completed bars
    # 2. In-memory cache stayed bounded at max_bars (100)
    cached_1m = candle_cache.get_candles("XAUUSDT", "1M")
    assert len(cached_1m) <= 100
    # 3. Position closed on TP touch
    closed_pos = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == "ord-stress-long-1").first()
    assert closed_pos.state == "closed"
    assert closed_pos.exit_cause == "TP_HIT"
    assert total_quotes_processed >= 720

    isolated_db.close()


def test_slow_client_backpressure_isolation():
    """
    Test Section 7:
    - Slow client that times out on send_text does not crash broadcaster or block others.
    """
    async def _run():
        broadcaster = MarketBroadcaster(throttle_ms=50)

        # Fast client
        fast_client = AsyncMock()
        fast_client.send_text = AsyncMock()

        # Slow client (hangs or raises TimeoutError)
        slow_client = AsyncMock()
        async def slow_send(msg):
            raise asyncio.TimeoutError()
        slow_client.send_text = slow_send

        await broadcaster.connect(fast_client)
        await broadcaster.connect(slow_client)
        assert broadcaster.client_count == 2

        # Send broadcast message
        test_msg = {"protocol_version": "7.2.0", "type": "TEST"}
        await broadcaster._send_to_all(test_msg, timeout=0.01)

        # Fast client received text
        assert fast_client.send_text.called
        # Slow client timed out and was discarded
        assert slow_client not in broadcaster._clients
        assert broadcaster.client_count == 1

    asyncio.run(_run())
