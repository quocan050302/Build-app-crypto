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


@pytest.mark.anyio
async def test_stop_does_not_complete_while_actual_thread_running():
    """NEWBUGSTOP01 (PHẦN 201):
    When worker thread is actively executing a quote with an empty queue,
    stop(timeout) must return TIMED_OUT with inflight_done=False, not COMPLETE.
    """
    import threading
    barrier = threading.Event()
    entered_barrier = threading.Event()

    consumer = ExecutionConsumer(max_queue_size=10)

    orig_worker = consumer._process_quote_worker
    def blocking_worker(quote, clock=None):
        entered_barrier.set()
        barrier.wait(timeout=2.0)
        return orig_worker(quote, clock=clock)

    consumer._process_quote_worker = blocking_worker

    task = asyncio.create_task(consumer.run_consumer_loop())
    consumer.enqueue_quote(_make_quote(ts=10000))

    # Wait until quote is popped and thread enters the barrier
    while not entered_barrier.is_set():
        await asyncio.sleep(0.01)

    assert consumer.queue.empty(), "Queue should be empty as quote is in-flight"
    assert consumer._inflight is True, "Worker should be marked in-flight"

    res = await consumer.stop(timeout=0.05)

    assert res["status"] == "TIMED_OUT"
    assert res.get("inflight_done") is False
    assert consumer.has_execution_gap is True

    # Release barrier and let consumer finish cleanly
    barrier.set()
    await task
    assert consumer._active_worker_future.done()


def test_latack01_claim_overflow_ack_by_ids():
    """LATPRODRACE (PHẦN 212):
    Claiming a batch, appending new quotes, and then acknowledging by exact evidence_ids
    ensures that newly arrived quotes are retained even if queue shifts.
    """
    ordering = QuoteOrderingService()
    ordering.classify_quote(_make_quote(ts=100000))

    # Send 2 late quotes
    ordering.classify_quote(_make_quote(ts=60000))
    ordering.classify_quote(_make_quote(ts=60001))

    claimed = ordering.claim_late_evidence_batch("XAUUSDT", limit=10)
    assert len(claimed) == 2
    claimed_ids = [c["evidence_id"] for c in claimed]

    # While claimed is processing, 2 new late quotes arrive
    ordering.classify_quote(_make_quote(ts=60002))
    ordering.classify_quote(_make_quote(ts=60003))

    assert len(ordering.get_late_evidence("XAUUSDT")) == 4

    # Acknowledge only the claimed IDs
    ordering.acknowledge_late_evidence("XAUUSDT", evidence_ids=claimed_ids)

    remaining = ordering.get_late_evidence("XAUUSDT")
    assert len(remaining) == 2
    assert [r["source_timestamp"] for r in remaining] == [60002, 60003]


def test_lattrue01_market_reconciliation_consumer_processes_batch(isolated_db):
    """LATTRUE01 (PHẦN 061, 211):
    MarketReconciliationService processes late quotes, checks open orders,
    executes close on TP/SL hit, and acknowledges processed evidence IDs.
    """
    from services.market_reconciliation_service import market_reconciliation_service
    from services.quote_ordering_service import quote_ordering_service
    import models

    quote_ordering_service.reset_watermark_for_testing("XAUUSDT")
    quote_ordering_service.classify_quote(_make_quote(ts=200000))

    # Create an open position opened at 100000 with SL 4170 and TP 4200
    order = models.PaperOrder(
        id="ord-late-rec-01",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        opened_at=100000,
        created_at=100000,
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Enqueue a late tick at 150000 (< 200000 watermark) with bid hitting TP (4205.0)
    tp_quote = _make_quote(ts=150000, bid=4205.0, ask=4205.2, last=4205.1)
    disp, _ = quote_ordering_service.classify_quote(tp_quote)
    assert disp == QuoteDisposition.LATE_EVIDENCE

    # Process batch via market_reconciliation_service with authoritative provider proof (PHẦN 029)
    provider_proof = {
        "continuity_proof": {
            "source": "BITGET_WS",
            "epoch": 1,
            "resolution": "TICK",
            "interval_start_ms": 100000,
            "interval_end_ms": 160000,
            "has_gap": False,
            "missing_intervals": [],
            "proof_references": ["ext_provider_ledger_100k_160k"],
        }
    }
    processed_count = market_reconciliation_service.process_late_batch(
        isolated_db, symbol="XAUUSDT", source_state=provider_proof
    )
    assert processed_count >= 1

    # Verify order was closed due to TP_HIT
    isolated_db.refresh(order)
    assert order.state == "closed"
    assert order.exit_cause == "TP_HIT"
    assert order.actual_exit == 4200.0

    # Verify late evidence queue was acknowledged and is now empty
    remaining = quote_ordering_service.get_late_evidence("XAUUSDT")
    assert len(remaining) == 0
