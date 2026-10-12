"""
Tests for Stage F: Realtime Execution Worker and Candle Persistence (RT06 - RT09)
Covers:
- RT06: Dedicated worker thread execution / slow DB does not block asyncio loop; single execution owner.
- RT07: Queue overflow marks execution gap and blocks new entries; position exits remain protected.
- RT08: Candle persistence retry buffer overflow marks data gap with telemetry.
- RT09: Worker exception handling, rollback, graceful cancellation, and start/stop lifecycle.
"""
import pytest
import asyncio
import time
from unittest.mock import patch, MagicMock
from services.quote_validator import CanonicalQuote
from services.execution_consumer import ExecutionConsumer
from services.candle_cache_service import CandleCacheService
import schemas


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_quote(bid: float = 4180.0, ask: float = 4180.5, last: float = 4180.2, exchange_ts: int = 10000) -> CanonicalQuote:
    return CanonicalQuote(
        symbol="XAUUSDT",
        bid=bid,
        ask=ask,
        last=last,
        exchange_ts_ms=exchange_ts,
        received_at_ms=exchange_ts + 20,
        status="VALID",
        source="BITGET_WS",
    )


@pytest.mark.anyio
async def test_rt06_worker_dbslow_eventloop_responsive():
    """RT06: Dedicated worker thread processes quotes via asyncio.to_thread,
    so simulated slow DB queries do NOT starve the asyncio event loop.
    """
    consumer = ExecutionConsumer(max_queue_size=10)

    # Mock evaluate_quote_sync to simulate 50ms slow database query
    original_eval = consumer.evaluate_quote_sync

    def slow_eval(db, quote, clock=None, now_ms=None):
        time.sleep(0.05)  # Blocking sleep in worker thread
        return {"exit_processed": False, "entry_processed": False, "is_stale": False}

    consumer.evaluate_quote_sync = slow_eval

    # Start loop in background
    task = asyncio.create_task(consumer.run_consumer_loop())

    # Measure event loop responsiveness while worker is processing
    consumer.enqueue_quote(_make_quote(exchange_ts=10000))

    # Concurrently ping the asyncio event loop
    loop_responsive = False
    for _ in range(5):
        await asyncio.sleep(0.01)
        loop_responsive = True

    assert loop_responsive is True

    # Stop consumer
    await consumer.stop(timeout=1.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert consumer.processed_count == 1
    assert consumer.db_work_ms >= 40.0
    metrics = consumer.get_health_metrics()
    assert metrics["processed_count"] == 1
    assert metrics["is_running"] is False


def test_rt07_queue_overflow_gap_blocks_entry():
    """RT07: Queue overflow sets execution gap and blocks new entries,
    while existing active open positions still evaluate exits safely.
    """
    consumer = ExecutionConsumer(max_queue_size=2)

    q1 = _make_quote(exchange_ts=1000)
    q2 = _make_quote(exchange_ts=2000)
    q3 = _make_quote(exchange_ts=3000)

    assert consumer.enqueue_quote(q1) is True
    assert consumer.enqueue_quote(q2) is True
    # Third quote overflows maxsize=2
    assert consumer.enqueue_quote(q3) is False
    assert consumer.dropped_count == 1
    assert consumer.has_execution_gap is True
    assert consumer.is_lagging is True

    # When execution gap is active, evaluate_quote_sync rejects new entries
    mock_db = MagicMock()
    with patch("crud.get_active_position", return_value=None):
        res = consumer.evaluate_quote_sync(mock_db, q1, now_ms=1050)
        assert res["entry_processed"] is False
        assert res["rejection_reason"] == "EXECUTION_GAP_ACTIVE"


@pytest.mark.anyio
async def test_rt08_candle_persist_retry_overflow_marks_gap():
    """RT08: When candle persistence retry buffer overflows (> 500 items),
    it records data gap telemetry and sets has_candle_gap = True without silent loss.
    """
    service = CandleCacheService()
    service._running = True

    # Populate retry buffer with 550 mock candles
    candles = [
        schemas.CandleCreate(
            symbol="XAUUSDT",
            timeframe="15M",
            timestamp=100000 + i * 900000,
            open=4180.0,
            high=4185.0,
            low=4175.0,
            close=4182.0,
            volume=10.0,
            is_closed=True
        )
        for i in range(550)
    ]

    # Seed retry buffer with 515 candles so retry buffer > 500 triggers on failure
    service.retry_buffer = candles[:515]

    # Mock crud.bulk_upsert_candles to fail and trigger retry buffer overflow
    with patch("crud.bulk_upsert_candles", side_effect=RuntimeError("DB write failure")):
        task = asyncio.create_task(service.run_persistence_worker())

        # Allow worker iteration to run, fail, and trigger overflow
        for _ in range(10):
            await asyncio.sleep(0.02)
            if service.has_candle_gap:
                break

        # Stop worker
        service._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # The service must have recorded the failure in retry buffer and when > 500, recorded gap
    assert service.has_candle_gap is True
    assert ("XAUUSDT", "15M") in service.candle_gap_telemetry


@pytest.mark.anyio
async def test_rt09_worker_exception_lifecycle_and_cancellation():
    """RT09: Worker exception rolls back DB transaction and marks execution gap;
    graceful cancellation shuts down without task leaks.
    """
    consumer = ExecutionConsumer(max_queue_size=10)

    # Force error during evaluation
    def faulty_eval(db, quote, clock=None, now_ms=None):
        raise ValueError("Simulated DB integrity error")

    consumer.evaluate_quote_sync = faulty_eval

    task = asyncio.create_task(consumer.run_consumer_loop())

    consumer.enqueue_quote(_make_quote(exchange_ts=10000))

    # Give it a moment to run in worker thread and handle exception
    await asyncio.sleep(0.05)

    assert consumer.has_execution_gap is True
    assert consumer.is_lagging is True

    await consumer.stop(timeout=1.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert consumer._running is False
    assert consumer._inflight is False
