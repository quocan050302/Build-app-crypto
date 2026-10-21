"""
backend/tests/test_v16_39_12_historical_snapshot_states.py
V16.39.12 Regression Suite: Historical Snapshot State Reconstruction & Null-Safe Reporting

Tests Bug 6 & Rules 101-120:
- Bug 6: compute_financial_metrics currently guesses open_positions based on current DB state instead of recognizing that historical state is unavailable.
- compute_position_state_view pure function with CURRENT_PERSISTED vs HISTORICAL_AS_OF view modes.
- Null-safe rendering in render_markdown: unknown counts render 'Chưa đủ lịch sử xác minh' instead of '0'.
- Payload includes historical_state_available, state_view, state_as_of_ms, state_observed_at_ms.
"""

import pytest
from services.paper_runtime_snapshot_service import (
    compute_financial_metrics,
    render_markdown,
)

def test_101_106_compute_position_state_view_and_financial_metrics_historical():
    """
    Bug 6 & Rules 101-106:
    When an order was opened and closed in the past, but window end_ms is historical (eff_end < now_ms),
    historical_state_available must be False and open_positions_count / armed_orders_count must be None
    with reason 'HISTORICAL_TRANSITIONS_UNAVAILABLE' because event transition logs are not available.
    """
    try:
        from services.paper_runtime_snapshot_service import compute_position_state_view
    except ImportError:
        compute_position_state_view = None

    assert compute_position_state_view is not None, "compute_position_state_view must be implemented in paper_runtime_snapshot_service"

    # Order currently marked "closed" in DB
    orders = [
        {
            "id": "ord_1",
            "state": "closed",
            "created_at": 500,
            "opened_at": 1500,
            "closed_at": 3500,
            "realized_pnl_net": 10.0,
        }
    ]

    # Historical window: end_ms = 2500, now_ms = 5000
    state_view_hist = compute_position_state_view(
        orders,
        requested_as_of_ms=2500,
        observed_at_ms=5000,
        view_mode="HISTORICAL_AS_OF",
    )
    assert state_view_hist["historical_state_available"] is False
    assert state_view_hist["open_positions_count"] is None
    assert state_view_hist["armed_orders_count"] is None
    assert state_view_hist["reason"] == "HISTORICAL_TRANSITIONS_UNAVAILABLE"
    assert state_view_hist["state_view"] == "HISTORICAL_AS_OF"

    # Current window: view_mode="CURRENT_PERSISTED", observed_at_ms=5000
    state_view_curr = compute_position_state_view(
        orders,
        requested_as_of_ms=5000,
        observed_at_ms=5000,
        view_mode="CURRENT_PERSISTED",
    )
    assert state_view_curr["historical_state_available"] is True
    assert state_view_curr["open_positions_count"] == 0
    assert state_view_curr["armed_orders_count"] == 0
    assert state_view_curr["state_view"] == "CURRENT_PERSISTED"

    # In compute_financial_metrics:
    fin_hist = compute_financial_metrics(
        orders,
        initial_capital=1000.0,
        start_ms=1000,
        end_ms=2500,
        state_as_of_ms=2500,
    )
    assert fin_hist.get("historical_state_available") is False
    assert fin_hist.get("open_positions_count") is None
    assert fin_hist.get("armed_orders_count") is None
    assert fin_hist.get("state_view") == "HISTORICAL_AS_OF"


def test_113_render_markdown_null_safe_historical():
    """
    Rule 113:
    When open_positions_count is None (historical state unavailable),
    markdown report must display 'Chưa đủ lịch sử xác minh', NOT '0' or 'None'.
    """
    snapshot = {
        "schema_version": "1.0.0",
        "generated_at": 1700000000000,
        "source_mode": "RO_SNAPSHOT",
        "status": "SNAPSHOT_READY",
        "runtime_commit": "abcdef123456",
        "reporter_commit": "abcdef123456",
        "time_window": {"start_ms": 1000, "end_ms": 2500},
        "effective_config": {"auto_enabled": False},
        "worker_health": {"status": "STOPPED", "running": False},
        "run_summary": {"run_id": "test_run", "status": "COMPLETED"},
        "funnel_summary": {
            "total_unique_opportunities": 0,
            "zones_detected": 0,
            "trigger_confirmed": 0,
            "plans_valid": 0,
            "armed": 0,
            "filled": 0,
            "top_rejection_reasons": [],
        },
        "metrics": {
            "duration_minutes": 25.0,
            "opportunities_count": 0,
            "orders_count": 0,
            "state_counts": {},
            "open_positions_count": None,  # Historical unavailable
            "armed_orders_count": None,
            "closed_trades_count": 0,
            "unresolved_closed_count": 0,
            "accounting_status": "COMPLETE",
            "net_realized_pnl_usd": 0.0,
            "winrate_pct": None,
            "expectancy_usd_per_trade": None,
            "max_drawdown_usd": 0.0,
            "max_drawdown_pct": 0.0,
            "historical_state_available": False,
            "state_view": "HISTORICAL_AS_OF",
        },
        "errors": [],
        "warnings": [],
        "limitations": [],
    }

    md = render_markdown(snapshot)
    assert "Vị thế đang mở (Open Positions): **Chưa đủ lịch sử xác minh**" in md
    assert "Open Positions): **0**" not in md
    assert "Open Positions): **None**" not in md
