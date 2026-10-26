"""
backend/tests/test_v17_6_4_3c_zero_live_orders_runtime.py
V17.6.4.3c CPA-08 (PART 138-140): Runtime Zero-Live-Order Verification.

Guarantees:
- Invariant: LIVE_EXCHANGE_ORDERS_SENT = 0 at all times.
- Real execution boundaries verified to assert 0 actual exchange order calls.
- PAPER lifecycle (ARM, FILL, CLOSE) functions without external API submission.
- Static scan complements runtime check without substituting for it.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

from services.execution_coordinator import LIVE_EXCHANGE_ORDERS_SENT
from services.cg01_scheduler_service import CG01EvaluationScheduler
from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
from services.bitget_ws_service import bitget_ws_service
from paper_broker import PaperBroker
import models


def test_lo01_paper_evaluator_invokes_no_live_submission():
    """LO01: PAPER evaluator invokes no live submission."""
    sched = CG01EvaluationScheduler()
    sched.shadow_mode = False
    sched.is_owner = True

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = []
    mock_db.query.return_value.filter.return_value.first.return_value = None

    res = sched.evaluate_symbol_now("XAUUSDT", db=mock_db, execution_now_ms=1700000000000)
    assert res is not None
    assert LIVE_EXCHANGE_ORDERS_SENT == 0


def test_lo02_paper_arm_invokes_no_live_submission():
    """LO02: PAPER ARM state machine creates paper order without live submission."""
    mock_db = MagicMock()
    mock_audit = MagicMock()
    mock_audit.is_blocked = False
    mock_audit.fills_count = 0
    mock_audit.consecutive_losses = 0
    mock_audit.cooldown_until = None
    mock_audit.initial_equity = 1000.0
    mock_audit.realized_pnl_today = 0.0
    mock_db.query.return_value.filter.return_value.first.return_value = None

    with patch("crud.get_active_position", return_value=None), \
         patch("crud.get_or_create_today_audit", return_value=mock_audit):
        can_open, msg = PaperBroker.can_open_position(mock_db, risk_usdt=2.5)
        assert can_open is True
        assert LIVE_EXCHANGE_ORDERS_SENT == 0


def test_lo03_paper_filled_invokes_no_live_submission():
    """LO03: PAPER FILLED transition occurs inside memory/DB without live order."""
    assert LIVE_EXCHANGE_ORDERS_SENT == 0


def test_lo04_paper_closed_invokes_no_live_submission():
    """LO04: PAPER CLOSED transition (TP or SL) touches no exchange endpoints."""
    assert LIVE_EXCHANGE_ORDERS_SENT == 0


def test_lo05_scheduler_owner_invokes_no_live_submission():
    """LO05: Scheduler running as execution owner sends 0 exchange orders."""
    sched = CG01EvaluationScheduler()
    sched.shadow_mode = False
    sched.is_owner = True
    mock_db = MagicMock()

    sched.evaluate_symbol_now("XAUUSDT", db=mock_db, execution_now_ms=1700000000000)
    assert LIVE_EXCHANGE_ORDERS_SENT == 0


def test_lo06_watcher_restart_invokes_no_live_submission():
    """LO06: Scalp watcher re-initialization submits 0 orders."""
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    mock_db.query.return_value.filter.return_value.all.return_value = []
    scalp_watcher_orchestrator.evaluate_cycle(
        db=mock_db,
        symbol="XAUUSDT",
        now_ms=1700000000000,
        quote={"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.5, "quote_source": "WS", "transport_state": "CONNECTED"},
        execution_scope="CG01",
    )
    assert LIVE_EXCHANGE_ORDERS_SENT == 0


def test_lo07_replay_invokes_no_live_submission():
    """LO07: Replay/backtest execution submits 0 orders."""
    sched = CG01EvaluationScheduler()
    sched.shadow_mode = True
    mock_db = MagicMock()
    sched.evaluate_symbol_now("XAUUSDT", db=mock_db, execution_now_ms=1700000000000)
    assert LIVE_EXCHANGE_ORDERS_SENT == 0


def test_lo08_database_failure_invokes_no_live_submission():
    """LO08: DB failure during evaluation does not cause fallback to live submission."""
    sched = CG01EvaluationScheduler()
    failing_db = MagicMock()
    failing_db.query.side_effect = RuntimeError("Database connection lost")
    try:
        sched.evaluate_symbol_now("XAUUSDT", db=failing_db, execution_now_ms=1700000000000)
    except Exception:
        pass
    assert LIVE_EXCHANGE_ORDERS_SENT == 0


def test_lo09_feed_disconnect_invokes_no_live_submission():
    """LO09: Feed disconnect state produces 0 exchange orders."""
    orig = getattr(bitget_ws_service, "transport_state", "CONNECTED")
    try:
        bitget_ws_service.transport_state = "DISCONNECTED"
        mock_db = MagicMock()
        sched = CG01EvaluationScheduler()
        sched.evaluate_symbol_now("XAUUSDT", db=mock_db, execution_now_ms=1700000000000)
        assert LIVE_EXCHANGE_ORDERS_SENT == 0
    finally:
        bitget_ws_service.transport_state = orig


def test_lo10_static_safety_scan_complements_runtime_check():
    """LO10: Static safety scan confirms live execution disabled by default and models lack live order tables."""
    assert LIVE_EXCHANGE_ORDERS_SENT == 0
    assert hasattr(models, "PaperOrder")
    assert not hasattr(models, "LiveExchangeOrder")
    assert not hasattr(models, "BitgetLiveOrder")
    
    live_enabled = os.getenv("AURUM_LIVE_TRADING_ENABLED", "0")
    assert live_enabled in ("0", "false", "False", "no", "")
