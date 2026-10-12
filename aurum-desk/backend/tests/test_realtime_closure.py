"""
Tests for Realtime Late Evidence Buffer and Worker Shutdown Closure (LATBUF, STLEASE, STQUEUE, STCANDLE)
PHẦN 061 - 063, 075 - 078, 185, 190 - 193
"""
import pytest
import asyncio
from services.quote_ordering_service import QuoteOrderingService, QuoteDisposition
from services.quote_validator import CanonicalQuote
from services.execution_consumer import ExecutionConsumer
from services.candle_cache_service import CandleCacheService


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_quote(ts: int, bid: float = 4180.0, ask: float = 4180.5, last: float = 4180.2) -> CanonicalQuote:
    return CanonicalQuote(
        symbol="XAUUSDT",
        bid=bid,
        ask=ask,
        last=last,
        exchange_ts_ms=ts,
        received_at_ms=ts + 10,
        status="VALID",
        source="BITGET_WS",
    )


def test_latbuf01_late_evidence_overflow_records_gap():
    """LATBUF (PHẦN 185): 50 late quotes buffered; 51st overflows and sets overflow gap."""
    ordering = QuoteOrderingService()
    # Establish watermark
    ordering.classify_quote(_make_quote(ts=100000))

    # Send 55 late quotes (< 100000)
    for i in range(55):
        disp, reason = ordering.classify_quote(_make_quote(ts=50000 + i))
        assert disp == QuoteDisposition.LATE_EVIDENCE

    gap = ordering.get_overflow_gap("XAUUSDT")
    assert gap is not None
    assert gap["overflow_count"] == 5
    assert gap["gap_start_ms"] == 50000
    assert gap["gap_end_ms"] == 50004


def test_latbuf02_claim_and_ack_late_evidence():
    """LATBUF (PHẦN 185): claim_late_evidence_batch and acknowledge_late_evidence."""
    ordering = QuoteOrderingService()
    ordering.classify_quote(_make_quote(ts=100000))

    ordering.classify_quote(_make_quote(ts=60000))
    ordering.classify_quote(_make_quote(ts=60001))

    claimed = ordering.claim_late_evidence_batch("XAUUSDT", limit=10)
    assert len(claimed) == 2
    assert claimed[0]["source_timestamp"] == 60000

    ordering.acknowledge_late_evidence("XAUUSDT", processed_count=len(claimed))

    # After acknowledging, claiming again should yield 0 items
    assert len(ordering.claim_late_evidence_batch("XAUUSDT", limit=10)) == 0


@pytest.mark.anyio
async def test_stlease01_execution_consumer_stop_returns_stop_result():
    """STLEASE (PHẦN 190): consumer.stop() returns typed StopResult dict with COMPLETE status."""
    consumer = ExecutionConsumer(max_queue_size=10)
    task = asyncio.create_task(consumer.run_consumer_loop())

    # Wait briefly for worker to initialize
    await asyncio.sleep(0.01)

    result = await consumer.stop(timeout=1.0)
    await task
    assert result is not None
    assert result["status"] == "COMPLETE"
    assert result["unprocessed_count"] == 0
    assert consumer.has_execution_gap is False


@pytest.mark.anyio
async def test_stqueue01_execution_consumer_timeout_sets_execution_gap():
    """STQUEUE (PHẦN 191): Timeout with unprocessed quotes sets has_execution_gap and returns TIMED_OUT."""
    consumer = ExecutionConsumer(max_queue_size=10)

    # Do not start run_consumer_loop so queue items remain unprocessed
    consumer.enqueue_quote(_make_quote(ts=10000))

    result = await consumer.stop(timeout=0.02)
    assert result["status"] == "TIMED_OUT"
    assert result["unprocessed_count"] >= 1
    assert consumer.has_execution_gap is True


@pytest.mark.anyio
async def test_stcandle01_candle_worker_stop_returns_stop_result():
    """STCANDLE (PHẦN 192): stop_persistence_worker returns StopResult dict."""
    candle_service = CandleCacheService()
    task = asyncio.create_task(candle_service.run_persistence_worker())

    await asyncio.sleep(0.01)

    result = await candle_service.stop_persistence_worker(timeout=1.0)
    await task
    assert result is not None
    assert result["status"] == "COMPLETE"
    assert result["remaining_count"] == 0
    assert candle_service.has_candle_gap is False
