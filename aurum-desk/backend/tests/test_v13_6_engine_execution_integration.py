"""
Aurum Desk V13.6 — Engine Execution Pipeline Integration Tests (Phần 80).
Proves that ReplayEngine.run_replay() invokes real production helpers:
- submit_replay_order
- try_fill_pending_order
- evaluate_position_exit
- compute_closed_trade_accounting
- aggregate_replay_metrics
- run_replay_integrity_checks
Uses unittest.mock.spy wrappers around real production functions (no fake execution mocks).
"""

import pytest
from unittest.mock import patch
import lab.replay_execution as rex
import lab.replay_metrics as rmx
import lab.replay_integrity as rint
from lab.replay_engine import ReplayEngine
from schemas import ReplayRunRequest


def test_engine_invokes_real_execution_modules_on_baseline():
    """Verify baseline run invokes submit, fill, exit, accounting, aggregate_replay_metrics, and integrity."""
    real_submit = rex.submit_replay_order
    real_fill = rex.try_fill_pending_order
    real_exit = rex.evaluate_position_exit
    real_acct = rex.compute_closed_trade_accounting
    real_agg = rmx.aggregate_replay_metrics
    real_int = rint.run_replay_integrity_checks

    called_helpers = set()

    def spy_submit(*args, **kwargs):
        called_helpers.add("submit_replay_order")
        return real_submit(*args, **kwargs)

    def spy_fill(*args, **kwargs):
        called_helpers.add("try_fill_pending_order")
        return real_fill(*args, **kwargs)

    def spy_exit(*args, **kwargs):
        called_helpers.add("evaluate_position_exit")
        return real_exit(*args, **kwargs)

    def spy_acct(*args, **kwargs):
        called_helpers.add("compute_closed_trade_accounting")
        return real_acct(*args, **kwargs)

    def spy_agg(*args, **kwargs):
        called_helpers.add("aggregate_replay_metrics")
        return real_agg(*args, **kwargs)

    def spy_int(*args, **kwargs):
        called_helpers.add("run_replay_integrity_checks")
        return real_int(*args, **kwargs)

    with patch("lab.replay_execution.submit_replay_order", side_effect=spy_submit),          patch("lab.replay_engine.submit_replay_order", side_effect=spy_submit),          patch("lab.replay_execution.try_fill_pending_order", side_effect=spy_fill),          patch("lab.replay_engine.try_fill_pending_order", side_effect=spy_fill),          patch("lab.replay_execution.evaluate_position_exit", side_effect=spy_exit),          patch("lab.replay_engine.evaluate_position_exit", side_effect=spy_exit),          patch("lab.replay_execution.compute_closed_trade_accounting", side_effect=spy_acct),          patch("lab.replay_engine.compute_closed_trade_accounting", side_effect=spy_acct),          patch("lab.replay_metrics.aggregate_replay_metrics", side_effect=spy_agg),          patch("lab.replay_engine.aggregate_replay_metrics", side_effect=spy_agg),          patch("lab.replay_integrity.run_replay_integrity_checks", side_effect=spy_int),          patch("lab.replay_engine.run_replay_integrity_checks", side_effect=spy_int):

        req = ReplayRunRequest(
            run_name="test_spy_baseline",
            symbol="XAUUSDT",
            start_ts=1783609200000,
            end_ts=1791558000000,
            initial_equity=1000.0,
            risk_pct=0.5,
            leverage=30,
            strategy_variant="CURRENT_BASELINE",
            mode="HISTORICAL_MARKET"
        )
        res = ReplayEngine.run_replay(req)

        assert "submit_replay_order" in called_helpers, "submit_replay_order was not invoked by ReplayEngine"
        assert "try_fill_pending_order" in called_helpers, "try_fill_pending_order was not invoked by ReplayEngine"
        assert "evaluate_position_exit" in called_helpers, "evaluate_position_exit was not invoked by ReplayEngine"
        assert "compute_closed_trade_accounting" in called_helpers, "compute_closed_trade_accounting was not invoked by ReplayEngine"
        assert "aggregate_replay_metrics" in called_helpers, "aggregate_replay_metrics was not invoked by ReplayEngine"
        assert "run_replay_integrity_checks" in called_helpers, "run_replay_integrity_checks was not invoked by ReplayEngine"
        assert res.total_trades == 1


def test_engine_invokes_real_execution_modules_on_scheduled_paper():
    """Verify daily scheduled paper run invokes submit, fill, exit, accounting, aggregate_replay_metrics, and integrity."""
    real_submit = rex.submit_replay_order
    real_fill = rex.try_fill_pending_order
    real_exit = rex.evaluate_position_exit
    real_acct = rex.compute_closed_trade_accounting
    real_agg = rmx.aggregate_replay_metrics
    real_int = rint.run_replay_integrity_checks

    called_helpers = set()

    def spy_submit(*args, **kwargs):
        called_helpers.add("submit_replay_order")
        return real_submit(*args, **kwargs)

    def spy_fill(*args, **kwargs):
        called_helpers.add("try_fill_pending_order")
        return real_fill(*args, **kwargs)

    def spy_exit(*args, **kwargs):
        called_helpers.add("evaluate_position_exit")
        return real_exit(*args, **kwargs)

    def spy_acct(*args, **kwargs):
        called_helpers.add("compute_closed_trade_accounting")
        return real_acct(*args, **kwargs)

    def spy_agg(*args, **kwargs):
        called_helpers.add("aggregate_replay_metrics")
        return real_agg(*args, **kwargs)

    def spy_int(*args, **kwargs):
        called_helpers.add("run_replay_integrity_checks")
        return real_int(*args, **kwargs)

    with patch("lab.replay_execution.submit_replay_order", side_effect=spy_submit),          patch("lab.replay_engine.submit_replay_order", side_effect=spy_submit),          patch("lab.replay_execution.try_fill_pending_order", side_effect=spy_fill),          patch("lab.replay_engine.try_fill_pending_order", side_effect=spy_fill),          patch("lab.replay_execution.evaluate_position_exit", side_effect=spy_exit),          patch("lab.replay_engine.evaluate_position_exit", side_effect=spy_exit),          patch("lab.replay_execution.compute_closed_trade_accounting", side_effect=spy_acct),          patch("lab.replay_engine.compute_closed_trade_accounting", side_effect=spy_acct),          patch("lab.replay_metrics.aggregate_replay_metrics", side_effect=spy_agg),          patch("lab.replay_engine.aggregate_replay_metrics", side_effect=spy_agg),          patch("lab.replay_integrity.run_replay_integrity_checks", side_effect=spy_int),          patch("lab.replay_engine.run_replay_integrity_checks", side_effect=spy_int):

        # Single week test for fast execution
        req = ReplayRunRequest(
            run_name="test_spy_scheduled_paper",
            symbol="XAUUSDT",
            start_ts=1783609200000,
            end_ts=1784214000000,
            initial_equity=1000.0,
            risk_pct=0.5,
            quota_risk_pct=0.10,
            leverage=30,
            strategy_variant="NY_ADAPTIVE",
            entry_cadence="DAILY_PAPER",
            ny_max_fills=3,
            daily_min_fills_target=1,
            scheduled_deadline_hour=14,
            scheduled_deadline_minute=30,
            include_5m=True,
            use_5m_driver=True,
            mode="HISTORICAL_MARKET"
        )
        res = ReplayEngine.run_replay(req)

        assert "submit_replay_order" in called_helpers
        assert "try_fill_pending_order" in called_helpers
        assert "evaluate_position_exit" in called_helpers
        assert "compute_closed_trade_accounting" in called_helpers
        assert "aggregate_replay_metrics" in called_helpers
        assert "run_replay_integrity_checks" in called_helpers
        assert res.fills_count > 0
