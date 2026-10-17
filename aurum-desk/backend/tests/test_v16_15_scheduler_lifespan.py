"""
backend/tests/test_v16_15_scheduler_lifespan.py
V16.15 Automated Tests for Scheduler Lifespan Integration, Worker Health, and Shutdown.

Covers:
- Rules 011-013, 021, 031-040: Wiring scheduler into lifespan, single worker task, clean shutdown.
- Rules 034-035: Health snapshot states (CONFIGURED, RUNNING, STOPPED, effective owner).
- Rules 036-037: Graceful shutdown without interrupting open position management.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from main import app, lifespan
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
    cg01_evaluation_scheduler,
)


def test_scheduler_lifespan_startup_and_shutdown():
    """
    Rule 021, 031, 036: Lifespan context manager starts the scheduler worker
    and stops it cleanly on exit without creating orphaned async tasks.
    """
    async def _run():
        # Configure scheduler
        cg01_evaluation_scheduler.enabled = True
        cg01_evaluation_scheduler.is_owner = False
        cg01_evaluation_scheduler.shadow_mode = True

        # Run lifespan context
        async with lifespan(app):
            # Allow event loop to schedule worker task
            await asyncio.sleep(0.05)
            # Assert worker is running inside lifespan
            assert cg01_evaluation_scheduler.is_running() is True
            health = cg01_evaluation_scheduler.get_health_snapshot()
            assert health["running"] is True
            assert health["status"] == "RUNNING"
            assert health["enabled"] is True

        # Assert worker is cleanly stopped after lifespan exit
        assert cg01_evaluation_scheduler.is_running() is False
        health_stopped = cg01_evaluation_scheduler.get_health_snapshot()
        assert health_stopped["running"] is False
        assert health_stopped["status"] == "STOPPED"

    asyncio.run(_run())


def test_scheduler_duplicate_run_protection():
    """
    Rule 032: run() cannot be invoked twice on the same process; duplicate call is a no-op.
    """
    async def _run():
        sched = CG01EvaluationScheduler()
        sched.enabled = True

        # Start run task
        task = asyncio.create_task(sched.run())
        await asyncio.sleep(0.05)
        assert sched.is_running() is True

        # Second run call should return immediately without interfering
        await sched.run()
        assert sched.is_running() is True

        # Stop scheduler
        sched.stop()
        await task
        assert sched.is_running() is False

    asyncio.run(_run())


def test_scheduler_health_snapshot_distinguishes_states():
    """
    Rule 034-035: Health snapshot reports CONFIGURED, RUNNING, STOPPED,
    and effective owner accurately rather than just reflecting enabled flag.
    """
    sched = CG01EvaluationScheduler()
    sched.enabled = True
    sched.is_owner = True
    sched.shadow_mode = False

    # State: configured but not running
    h1 = sched.get_health_snapshot()
    assert h1["running"] is False
    assert h1["status"] == "CONFIGURED"
    assert h1["effective_owner"] == "WATCHER"  # Cannot be owner if not running!

    # Simulate running
    sched._running = True
    h2 = sched.get_health_snapshot()
    assert h2["running"] is True
    assert h2["status"] == "RUNNING"
    assert h2["effective_owner"] == "SCHEDULER"

    # In shadow mode
    sched.shadow_mode = True
    h3 = sched.get_health_snapshot()
    assert h3["effective_owner"] == "WATCHER"  # Shadow mode delegates execution to watcher

    # Disabled and stopped
    sched._running = False
    sched.enabled = False
    h4 = sched.get_health_snapshot()
    assert h4["status"] == "STOPPED"
    assert h4["effective_owner"] == "WATCHER"


def test_notify_market_change_preserves_earliest_arrival():
    """
    Rule 067-068: notify_market_change preserves earliest event arrival timestamp
    for pending market events across multiple fast arrivals.
    """
    sched = CG01EvaluationScheduler()
    sched.enabled = True
    sched.notify_market_change(
        {
            "symbol": "XAUUSDT",
            "timeframe": "1M",
            "event_type": "CANDLE_CLOSED",
            "candle_timestamp": 1700000000000,
            "received_at_ms": 1700000000050,
            "exchange_event_time_ms": 1700000000000,
        }
    )
    first_ev = sched._pending_market_events.get("XAUUSDT")
    assert first_ev is not None
    assert first_ev["received_at_ms"] == 1700000000050

    # Second arrival for same symbol before worker drains
    sched.notify_market_change(
        {
            "symbol": "XAUUSDT",
            "timeframe": "1M",
            "event_type": "CANDLE_CLOSED",
            "candle_timestamp": 1700000060000,
            "received_at_ms": 1700000060020,
            "exchange_event_time_ms": 1700000060000,
        }
    )
    second_ev = sched._pending_market_events.get("XAUUSDT")
    assert second_ev is not None
    # Earliest arrival time preserved
    assert second_ev["received_at_ms"] == 1700000000050
    # Latest candle closed timestamp updated
    assert second_ev["exchange_event_time_ms"] == 1700000060000
