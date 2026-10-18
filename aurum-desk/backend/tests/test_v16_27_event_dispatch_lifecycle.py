"""
V16.27 Event Dispatch Lifecycle & Regression Closure Test Suite.

Validates:
1. Zero coroutine leaks (no unawaited coroutines).
2. Clean dispatch lifecycle across same-loop, worker threads, closed/stopped loops, and scheduling races.
3. Generation token tracking, payload immutability snapshotting.
4. Exception observation and cancellation safety (no unretrieved task exceptions).
5. Monotonic dispatch draining, timeout, and stop-accepting semantics.
6. Clean restart, unbind, and idempotency.
7. Atomic unit-of-work persistence: commit before broadcast, rollback cleanup, session reuse isolation.
8. Telegram outbox persistence and deduplication independence from WebSocket transport.
"""

import sys
import gc
import json
import uuid
import time
import asyncio
import warnings
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import SessionLocal
from services.event_bus import EventBus, event_bus
from services.market_broadcaster import MarketBroadcaster


def create_isolated_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_event_bus():
    event_bus.reset_for_test()
    yield
    event_bus.reset_for_test()


# ======================================================================
# 1. CORE DISPATCH LIFECYCLE & COROUTINE LEAK TESTS
# ======================================================================

@pytest.mark.anyio
async def test_001_same_loop_success():
    """Rule 036 & 122: Same-loop dispatch executes coroutine and delivers to broadcaster."""
    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.set_main_loop(loop)

    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_domain_event = AsyncMock()

    test_payload = {
        "event_id": "ev-001",
        "event_type": "trade.opened",
        "aggregate_id": "tr-001",
        "payload": {"price": 2700.5}
    }

    with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
        bus.dispatch_websocket_broadcast(test_payload)
        drain_res = await bus.drain_websocket_broadcasts(timeout_s=1.0)
        assert drain_res["status"] == "COMPLETED"
        assert drain_res["pending_count"] == 0
        assert drain_res["active_task_count"] == 0

        assert mock_broadcaster.broadcast_domain_event.call_count == 1
        call_arg = mock_broadcaster.broadcast_domain_event.call_args[0][0]
        assert call_arg["event_id"] == "ev-001"
        assert call_arg["payload"]["price"] == 2700.5


@pytest.mark.anyio
async def test_002_worker_thread_success():
    """Rule 035 & 123: Worker thread synchronous dispatch schedules on bound main loop."""
    bus = EventBus()
    main_loop = asyncio.get_running_loop()
    bus.set_main_loop(main_loop)

    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_domain_event = AsyncMock()

    thread_ids = []

    async def record_thread(payload):
        thread_ids.append(threading.get_ident())

    mock_broadcaster.broadcast_domain_event.side_effect = record_thread

    def worker_dispatch():
        bus.dispatch_websocket_broadcast({
            "event_id": "ev-worker",
            "event_type": "order.armed",
            "aggregate_id": "ord-001",
            "payload": {}
        })

    with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
        await asyncio.to_thread(worker_dispatch)
        drain_res = await bus.drain_websocket_broadcasts(timeout_s=1.0)
        assert drain_res["status"] == "COMPLETED"

        assert len(thread_ids) == 1
        # Assert broadcast executed on main thread/loop, not the worker thread
        assert thread_ids[0] == threading.get_ident()


@pytest.mark.anyio
async def test_003_unbound_current_loop_fallback_and_shutdown_rejection():
    """Rule 046, 047 & 124: Unbound legacy mode runs on current loop, but is rejected after shutdown."""
    bus = EventBus()  # Not calling set_main_loop
    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_domain_event = AsyncMock()

    with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
        # 1. Unbound dispatch uses running loop
        bus.dispatch_websocket_broadcast({"event_id": "ev-unbound", "payload": {}})
        drain_res = await bus.drain_websocket_broadcasts(timeout_s=1.0)
        assert drain_res["status"] == "COMPLETED"
        assert mock_broadcaster.broadcast_domain_event.call_count == 1

        # 2. After shutdown, fallback is forbidden
        bus.begin_dispatch_shutdown()
        mock_broadcaster.broadcast_domain_event.reset_mock()

        bus.dispatch_websocket_broadcast({"event_id": "ev-after-shutdown", "payload": {}})
        await asyncio.sleep(0.02)
        assert mock_broadcaster.broadcast_domain_event.call_count == 0


def test_004_no_running_loop_synchronous_context():
    """Rule 037 & 125: Dispatch from synchronous context without any loop generates no coroutine or warning."""
    bus = EventBus()
    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_domain_event = MagicMock()

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
            bus.dispatch_websocket_broadcast({"event_id": "ev-no-loop", "payload": {}})
            gc.collect()

        assert mock_broadcaster.broadcast_domain_event.call_count == 0
        coro_warnings = [w for w in recorded if "was never awaited" in str(w.message)]
        assert len(coro_warnings) == 0


def test_005_closed_bound_loop_no_coroutine_leak():
    """Rule 005, 033, 126 & 151: Dispatch against closed bound loop produces 0 unawaited coroutine warnings."""
    bus = EventBus()
    temp_loop = asyncio.new_event_loop()
    bus.set_main_loop(temp_loop)
    temp_loop.close()

    mock_broadcaster = MarketBroadcaster()

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
            # Dispatch must detect closed loop and exit without creating coroutine
            bus.dispatch_websocket_broadcast({"event_id": "ev-closed", "payload": {}})
            gc.collect()

        coro_warnings = [w for w in recorded if "was never awaited" in str(w.message)]
        assert len(coro_warnings) == 0, f"Found unawaited coroutine warnings: {coro_warnings}"


def test_006_stopped_bound_loop_no_pending_coroutine():
    """Rule 038 & 127: Bound loop is not running. Dispatch is rejected without pending coroutine."""
    bus = EventBus()
    temp_loop = asyncio.new_event_loop()
    bus.set_main_loop(temp_loop)

    mock_broadcaster = MarketBroadcaster()

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
            bus.dispatch_websocket_broadcast({"event_id": "ev-stopped", "payload": {}})
            gc.collect()

        coro_warnings = [w for w in recorded if "was never awaited" in str(w.message)]
        assert len(coro_warnings) == 0
        assert len(bus._pending_tokens) == 0
        assert len(bus._active_tasks) == 0

    temp_loop.close()


@pytest.mark.anyio
async def test_007_scheduling_raise_cleans_up_registry():
    """Rule 034, 072 & 128: When call_soon_threadsafe raises RuntimeError, token is cleaned up and 0 coroutines leak."""
    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.set_main_loop(loop)

    mock_broadcaster = MarketBroadcaster()

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        with patch.object(loop, "call_soon_threadsafe", side_effect=RuntimeError("Loop closed race")):
            with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
                bus.dispatch_websocket_broadcast({"event_id": "ev-race", "payload": {}})
                gc.collect()

        assert len(bus._pending_tokens) == 0
        assert len(bus._active_tasks) == 0
        coro_warnings = [w for w in recorded if "was never awaited" in str(w.message)]
        assert len(coro_warnings) == 0


@pytest.mark.anyio
async def test_008_late_callback_rejected_across_generations():
    """Rule 049, 062 & 130: Callback from previous generation is rejected and drops event."""
    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.set_main_loop(loop)
    initial_gen = bus._generation

    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_domain_event = AsyncMock()

    # Rebind loop to advance generation
    bus.set_main_loop(loop)
    assert bus._generation > initial_gen

    with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
        # Directly invoke callback with old generation
        bus._pending_tokens.add("old-token")
        bus._start_broadcast_on_loop("old-token", {"event_id": "ev-old"}, initial_gen, loop)
        await asyncio.sleep(0.01)

        # Assert old event was dropped and never passed to broadcaster
        assert "old-token" not in bus._pending_tokens
        assert mock_broadcaster.broadcast_domain_event.call_count == 0


@pytest.mark.anyio
async def test_009_payload_snapshot_immutability():
    """Rule 051 & 131: Dispatch deepcopies payload; mutating caller dict does not corrupt broadcast."""
    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.set_main_loop(loop)

    received_payloads = []

    async def fake_broadcast(envelope):
        received_payloads.append(envelope["payload"]["price"])

    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_domain_event = fake_broadcast

    payload = {"price": 100.0}
    event_dict = {"event_id": "ev-snap", "payload": payload}

    with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
        bus.dispatch_websocket_broadcast(event_dict)
        # Mutate immediately after dispatch
        payload["price"] = 9999.0

        await bus.drain_websocket_broadcasts(timeout_s=1.0)
        assert len(received_payloads) == 1
        assert received_payloads[0] == 100.0, "Broadcaster saw mutated payload!"


@pytest.mark.anyio
async def test_010_broadcast_exception_observed_no_unretrieved_warning():
    """Rule 039, 067, 132 & 154: Broadcaster exceptions are retrieved and logged, leaving no unretrieved warning."""
    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.set_main_loop(loop)

    mock_broadcaster = MagicMock()

    async def fail_broadcast(payload):
        raise RuntimeError("Simulated network fail")

    mock_broadcaster.broadcast_domain_event = fail_broadcast

    custom_exceptions = []

    def custom_handler(l, ctx):
        custom_exceptions.append(ctx)

    old_handler = loop.get_exception_handler()
    loop.set_exception_handler(custom_handler)

    try:
        with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
            bus.dispatch_websocket_broadcast({"event_id": "ev-err", "payload": {}})
            drain_res = await bus.drain_websocket_broadcasts(timeout_s=1.0)
            assert drain_res["status"] == "COMPLETED"
            assert len(bus._active_tasks) == 0

            # No "Task exception was never retrieved" unhandled exceptions
            assert len(custom_exceptions) == 0
    finally:
        loop.set_exception_handler(old_handler)


@pytest.mark.anyio
async def test_011_task_cancel_and_done_callback_safety():
    """Rule 066, 133 & 134: Task cancellation is observed safely without raising in done callback."""
    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.set_main_loop(loop)

    hang_barrier = asyncio.Event()

    async def hanging_broadcast(payload):
        await hang_barrier.wait()

    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_domain_event = hanging_broadcast

    with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
        bus.dispatch_websocket_broadcast({"event_id": "ev-hang", "payload": {}})
        await asyncio.sleep(0.02)
        assert len(bus._active_tasks) == 1

        # Cancel the task
        task = list(bus._active_tasks)[0]
        task.cancel()
        await asyncio.sleep(0.02)

        # Done callback safely observed cancellation and removed from active tasks
        assert len(bus._active_tasks) == 0
        assert task.cancelled()


# ======================================================================
# 2. DRAIN & LIFECYCLE SHUTDOWN TESTS
# ======================================================================

@pytest.mark.anyio
async def test_012_drain_pending_callback_before_task():
    """Rule 054, 085 & 136: Drain waits for accepted pending callbacks even before task is instantiated."""
    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.set_main_loop(loop)

    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_domain_event = AsyncMock()

    with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
        token = str(uuid.uuid4())
        bus._pending_tokens.add(token)

        # Start drain task concurrently
        drain_task = asyncio.create_task(bus.drain_websocket_broadcasts(timeout_s=0.5))
        await asyncio.sleep(0.02)
        assert not drain_task.done(), "Drain returned clean while pending_tokens was not empty!"

        # Now remove pending token
        bus._pending_tokens.remove(token)
        res = await drain_task
        assert res["status"] == "COMPLETED"


@pytest.mark.anyio
async def test_013_drain_timeout_behavior():
    """Rule 087 & 137: Drain times out when task is stuck and returns TIMED_OUT status."""
    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.set_main_loop(loop)

    stuck_event = asyncio.Event()

    async def stuck_broadcast(payload):
        await stuck_event.wait()

    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_domain_event = stuck_broadcast

    with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
        bus.dispatch_websocket_broadcast({"event_id": "ev-stuck", "payload": {}})
        await asyncio.sleep(0.02)

        # Drain with 0.1s timeout
        res = await bus.drain_websocket_broadcasts(timeout_s=0.1)
        assert res["status"] == "TIMED_OUT"
        assert res["active_task_count"] == 1

        # Clean up stuck task
        stuck_event.set()
        await asyncio.sleep(0.02)


@pytest.mark.anyio
async def test_014_stop_accepting_and_idempotent_shutdown():
    """Rule 091, 094, 138 & 139: begin_dispatch_shutdown rejects new work, is idempotent, and preserves accepted work."""
    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.set_main_loop(loop)

    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_domain_event = AsyncMock()

    with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
        # 1. Accepted work before shutdown
        bus.dispatch_websocket_broadcast({"event_id": "ev-before", "payload": {}})

        # 2. Begin shutdown
        bus.begin_dispatch_shutdown()
        # Shutdown called twice is idempotent
        bus.begin_dispatch_shutdown()

        # 3. New work after shutdown rejected
        bus.dispatch_websocket_broadcast({"event_id": "ev-after", "payload": {}})

        drain_res = await bus.drain_websocket_broadcasts(timeout_s=1.0)
        assert drain_res["status"] == "COMPLETED"

        # Broadcaster only received the event dispatched BEFORE shutdown
        assert mock_broadcaster.broadcast_domain_event.call_count == 1
        assert mock_broadcaster.broadcast_domain_event.call_args[0][0]["event_id"] == "ev-before"


@pytest.mark.anyio
async def test_015_restart_and_stale_unbind_safety():
    """Rule 095, 096, 140 & 141: Loop restart binds cleanly; stale unbind does not unbind new lifecycle."""
    bus = EventBus()
    loop_a = asyncio.get_running_loop()
    bus.set_main_loop(loop_a)
    gen_a = bus._generation

    # Simulate shutdown of loop A
    bus.begin_dispatch_shutdown()
    await bus.drain_websocket_broadcasts(timeout_s=0.1)

    # Now startup with new loop (or new generation)
    loop_b = asyncio.get_running_loop()
    bus.set_main_loop(loop_b)
    gen_b = bus._generation
    assert gen_b > gen_a
    assert bus._is_accepting is True

    # Stale unbind with gen_a must NOT unbind current lifecycle
    bus.unbind_main_loop(expected_gen=gen_a)
    assert bus._main_loop is loop_b
    assert bus._is_accepting is True

    # Unbind with correct gen_b cleans up
    bus.unbind_main_loop(expected_gen=gen_b)
    assert bus._main_loop is None
    assert bus._is_accepting is False


# ======================================================================
# 3. WEBSOCKET BROADCASTER & CLIENT CONTRACT TESTS
# ======================================================================

@pytest.mark.anyio
async def test_016_no_connected_clients_succeeds():
    """Rule 105 & 142: Zero connected WebSocket clients completes normally without error."""
    broadcaster = MarketBroadcaster()
    assert len(broadcaster._clients) == 0

    # Must complete cleanly without raising
    await broadcaster.broadcast_domain_event({"event_id": "ev-noclient", "event_type": "setup.ready"})


@pytest.mark.anyio
async def test_017_fake_websocket_client_domain_event_envelope():
    """Rule 106 & 143: DOMAIN_EVENT envelope preserves schema and payload."""
    broadcaster = MarketBroadcaster()

    received_messages = []

    class FakeClient:
        async def send_text(self, text: str):
            received_messages.append(json.loads(text))

    client = FakeClient()
    await broadcaster.connect(client)

    event_payload = {
        "event_id": "ev-client-01",
        "event_type": "trade.opened",
        "aggregate_id": "tr-999",
        "payload": {"entry_price": 2680.0}
    }

    await broadcaster.broadcast_domain_event(event_payload)
    await broadcaster.disconnect(client)

    assert len(received_messages) == 1
    msg = received_messages[0]
    assert msg["protocol_version"] == "7.1.0"
    assert msg["type"] == "DOMAIN_EVENT"
    assert "server_sequence" in msg
    assert "published_at_ms" in msg
    assert msg["event"]["event_id"] == "ev-client-01"
    assert msg["event"]["payload"]["entry_price"] == 2680.0


# ======================================================================
# 4. TRANSACTION ATOMICITY & POST-COMMIT BROADCAST TESTS
# ======================================================================

@pytest.mark.anyio
async def test_018_commit_before_broadcast():
    """Rule 025, 112 & 144: Broadcast is deferred until caller's db.commit()."""
    db = create_isolated_db()
    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.set_main_loop(loop)

    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_domain_event = AsyncMock()

    with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
        bus.publish_event(
            event_type="setup.ready",
            aggregate_id="setup-1",
            payload={"symbol": "XAUUSDT"},
            db=db
        )
        await asyncio.sleep(0.02)
        # Not committed yet: broadcaster must NOT have been called!
        assert mock_broadcaster.broadcast_domain_event.call_count == 0

        # Now commit
        db.commit()
        await bus.drain_websocket_broadcasts(timeout_s=1.0)
        assert mock_broadcaster.broadcast_domain_event.call_count == 1
    db.close()


@pytest.mark.anyio
async def test_019_rollback_no_broadcast():
    """Rule 113, 114 & 145: Transaction rollback produces zero broadcast."""
    db = create_isolated_db()
    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.set_main_loop(loop)

    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_domain_event = AsyncMock()

    with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
        bus.publish_event(
            event_type="order.rejected",
            aggregate_id="ord-bad",
            payload={"reason": "INSUFFICIENT_MARGIN"},
            db=db
        )
        db.rollback()
        await asyncio.sleep(0.05)

        assert mock_broadcaster.broadcast_domain_event.call_count == 0
    db.close()


@pytest.mark.anyio
async def test_020_rollback_then_session_reuse():
    """Rule 113, 114 & 146: Reusing session after rollback does NOT broadcast the rolled-back event."""
    db = create_isolated_db()
    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.set_main_loop(loop)

    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_domain_event = AsyncMock()

    with patch("services.market_broadcaster.market_broadcaster", mock_broadcaster):
        # 1. First event published and rolled back
        bus.publish_event(
            event_type="order.rejected",
            aggregate_id="ord-bad",
            payload={"tag": "FIRST_ROLLED_BACK"},
            db=db
        )
        db.rollback()

        # 2. Same session reused for a second event and committed
        bus.publish_event(
            event_type="order.armed",
            aggregate_id="ord-good",
            payload={"tag": "SECOND_COMMITTED"},
            db=db
        )
        db.commit()

        await bus.drain_websocket_broadcasts(timeout_s=1.0)

        # EXACTLY 1 broadcast for the committed second event, NEVER the first!
        assert mock_broadcaster.broadcast_domain_event.call_count == 1
        call_arg = mock_broadcaster.broadcast_domain_event.call_args[0][0]
        assert call_arg["event_type"] == "order.armed"
        assert call_arg["payload"]["tag"] == "SECOND_COMMITTED"
    db.close()


def test_021_domain_event_persistence_when_loop_unavailable():
    """Rule 111 & 147: Domain event is persisted to DB even when event loop is unavailable."""
    db = create_isolated_db()
    bus = EventBus()
    # No loop bound, synchronous context

    # Use patch SessionLocal to isolated_db
    with patch("services.event_bus.SessionLocal", return_value=db):
        ev = bus.publish_event(
            event_type="trade.closed",
            aggregate_id="tr-persist",
            payload={"pnl": 150.0, "exit_cause": "TP_HIT"},
            db=None  # owns_session=True
        )

    # Verify event was persisted in DB
    found = db.query(models.DomainEvent).filter(models.DomainEvent.aggregate_id == "tr-persist").first()
    assert found is not None
    assert found.event_type == "trade.closed"
    assert json.loads(found.payload)["pnl"] == 150.0
    db.close()


def test_022_outbox_persistence_and_deduplication():
    """Rule 115, 116, 119, 148 & 149: Telegram outbox persistence and deduplication operate independently of WS loop."""
    db = create_isolated_db()
    bus = EventBus()

    # Configure Telegram in DB
    tg_config = models.TelegramConfig(
        id=1,
        bot_token="123456:FAKE_BOT_TOKEN",
        chat_id="999888777",
        enabled=True,
        subscribed_events=json.dumps(["FILLED", "TP_HIT", "SL_HIT"]),
        updated_at=datetime.now(timezone.utc)
    )
    db.add(tg_config)
    db.commit()

    # Publish trade.opened (maps to FILLED)
    with patch("services.event_bus.SessionLocal", return_value=db):
        bus.publish_event(
            event_type="trade.opened",
            aggregate_id="tr-100",
            payload={"trade_id": "tr-100", "price": 2690.0},
            db=None
        )

        # Attempt to publish duplicate with same trade_id
        bus.publish_event(
            event_type="trade.opened",
            aggregate_id="tr-100",
            payload={"trade_id": "tr-100", "price": 2690.0},
            db=None
        )

    # Assert exactly 1 outbox entry exists (deduplicated by key tr-100:FILLED)
    outbox_entries = db.query(models.NotificationOutbox).all()
    assert len(outbox_entries) == 1
    assert outbox_entries[0].dedupe_key == "tr-100:FILLED"
    assert outbox_entries[0].priority == "CRITICAL"
    db.close()
