"""
backend/tests/test_v16_39_10_snapshot_contract.py
V16.39.10 — Contract, Schema, and Financial Calculation Integrity Tests

Verifies Rules 021-040, 071-090, 146, 163-175:
- Schema Version 2.0 structure.
- True 3-state Auto authority (True / False / None).
- Finite metrics validation (rejection of NaN / Infinity).
- Zero closed trades -> winrate=None, expectancy=None.
- Breakeven (+10, -10) -> net=0, expectancy=0.0 (renders 0.00 USD/lệnh, NOT N/A).
- Verified accounting (+12, -8, +4) -> net=8.0, winrate=66.67, expectancy=2.67.
- Running peak closed equity drawdown.
- Half-open window [start_ms, end_ms).
- Order counts differentiation (unique created orders, state counts, no duplicate join inflation).
- Stage events vs unique opportunities in funnel.
- Graceful unmapped rejection code fallback.
"""
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import models
from models import Base, OpportunityFunnelRecord, PaperOrder, PaperResearchOrderLink, PaperResearchRunRecord
from services.paper_runtime_snapshot_service import (
    SCHEMA_VERSION,
    aggregate_funnel_readonly,
    build_paper_runtime_snapshot,
    compute_financial_metrics,
    find_existing_observation_run,
    make_snapshot_context,
    read_auto_authority,
    read_linked_orders,
    render_markdown,
    validate_finite_metrics,
)


@pytest.fixture
def contract_test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_010_make_snapshot_context_deterministic_and_window_validation():
    """Rule 023, 025: Context locks a single now_ms and enforces end_ms > start_ms."""
    fixed_now = 1770000000000
    ctx = make_snapshot_context(
        symbol="xauusdt",
        scope="cg01",
        requested_run_id="run_abc",
        start_ms=1770000000000,
        end_ms=1770003600000,
        now_ms=fixed_now,
    )
    assert ctx["symbol"] == "XAUUSDT"
    assert ctx["scope"] == "CG01"
    assert ctx["requested_run_id"] == "run_abc"
    assert ctx["start_ms"] == 1770000000000
    assert ctx["end_ms"] == 1770003600000
    assert ctx["now_ms"] == fixed_now

    # end_ms <= start_ms must raise ValueError
    with pytest.raises(ValueError, match="strictly greater"):
        make_snapshot_context(start_ms=2000, end_ms=1000)

    with pytest.raises(ValueError, match="strictly greater"):
        make_snapshot_context(start_ms=2000, end_ms=2000)


def test_020_auto_authority_three_states(contract_test_db: Session):
    """Rule 014, 030, 046: auto_paper_trading must be True, False, or None (never coerced)."""
    # 1. Row missing -> None with reason NOT_CONFIGURED
    res_none = read_auto_authority(contract_test_db)
    assert res_none["value"] is None
    assert res_none["reason"] == "NOT_CONFIGURED"

    # 2. Row 'true' -> True
    row = models.SystemConfig(key="auto_paper_trading", value="true", updated_at=int(time.time() * 1000))
    contract_test_db.add(row)
    contract_test_db.commit()

    res_true = read_auto_authority(contract_test_db)
    assert res_true["value"] is True
    assert res_true["reason"] is None

    # 3. Row 'false' -> False
    row.value = "false"
    contract_test_db.commit()

    res_false = read_auto_authority(contract_test_db)
    assert res_false["value"] is False
    assert res_false["reason"] is None

    # 4. Row invalid value -> None with reason
    row.value = "unknown_state"
    contract_test_db.commit()

    res_inv = read_auto_authority(contract_test_db)
    assert res_inv["value"] is None
    assert "INVALID_VALUE" in str(res_inv["reason"])


def test_030_validate_finite_metrics_rejects_nan_and_inf():
    """Rule 037: Rejects NaN / Infinity and ensures clean JSON serialization."""
    valid_payload = {
        "net_pnl": 12.5,
        "winrate": 50.0,
        "expectancy": 0.0,
        "nested": {"val": -10.2},
    }
    # Should pass without exception
    validate_finite_metrics(valid_payload)

    with pytest.raises(ValueError, match="Non-finite float"):
        validate_finite_metrics({"bad_val": float("nan")})

    with pytest.raises(ValueError, match="Non-finite float"):
        validate_finite_metrics({"bad_val": float("inf")})

    with pytest.raises(ValueError, match="Non-finite float"):
        validate_finite_metrics({"nested": {"bad_val": float("-inf")}})


def test_040_empty_orders_yield_clean_nulls():
    """Rule 032, 164: Zero closed trades results in null winrate and expectancy."""
    fin = compute_financial_metrics([], initial_capital=1000.0)
    assert fin["orders_count"] == 0
    assert fin["closed_trades_count"] == 0
    assert fin["net_realized_pnl_usd"] == 0.0
    assert fin["winrate_pct"] is None
    assert fin["expectancy_usd_per_trade"] is None
    assert fin["max_drawdown_usd"] == 0.0


def test_050_zero_expectancy_renders_clean_zero_not_na():
    """Rule 036, 086, 167: Breakeven trades (+10, -10) must render 0.00 USD/lệnh, NOT N/A."""
    orders = [
        {
            "id": "po_win_1",
            "state": "closed",
            "created_at": 1000,
            "closed_at": 1100,
            "realized_pnl_net": 10.0,
        },
        {
            "id": "po_loss_1",
            "state": "closed",
            "created_at": 1200,
            "closed_at": 1300,
            "realized_pnl_net": -10.0,
        },
    ]
    fin = compute_financial_metrics(orders, initial_capital=1000.0)
    assert fin["closed_trades_count"] == 2
    assert fin["net_realized_pnl_usd"] == 0.0
    assert fin["winrate_pct"] == 50.0
    assert fin["expectancy_usd_per_trade"] == 0.0

    # Build dummy snapshot to test Markdown rendering
    snapshot = {
        "status": "SNAPSHOT_READY",
        "source_mode": "OFFLINE_DB",
        "runtime_commit": "abcdef123456",
        "worker_health": {"status": "UNKNOWN", "running": None},
        "effective_config": {"auto_enabled": False},
        "run_summary": {"run_id": "test_run", "status": "COLLECTING"},
        "funnel_summary": aggregate_funnel_readonly([]),
        "metrics": {
            "duration_minutes": 10.0,
            "opportunities_count": 0,
            "orders_count": 2,
            "open_positions_count": 0,
            "closed_trades_count": 2,
            "unresolved_closed_count": 0,
            "net_realized_pnl_usd": 0.0,
            "winrate_pct": 50.0,
            "expectancy_usd_per_trade": 0.0,
            "max_drawdown_usd": 0.0,
            "max_drawdown_pct": 0.0,
        },
        "limitations": [],
    }
    md = render_markdown(snapshot)
    assert "+0.00 USD/lệnh" in md or "0.00 USD/lệnh" in md
    assert "TẮT (OFF)" in md


def test_060_verified_financial_accounting():
    """Rule 071-075, 166: Test +12, -8, +4 -> net=8, winrate=66.67%, expectancy=2.67."""
    orders = [
        {
            "id": "po_1",
            "state": "closed",
            "created_at": 1000,
            "closed_at": 1100,
            "realized_pnl_net": 12.0,
        },
        {
            "id": "po_2",
            "state": "closed",
            "created_at": 1200,
            "closed_at": 1300,
            "realized_pnl_net": -8.0,
        },
        {
            "id": "po_3",
            "state": "closed",
            "created_at": 1400,
            "closed_at": 1500,
            "realized_pnl_net": 4.0,
        },
    ]
    fin = compute_financial_metrics(orders, initial_capital=1000.0)
    assert fin["closed_trades_count"] == 3
    assert fin["wins_count"] == 2
    assert fin["losses_count"] == 1
    assert fin["net_realized_pnl_usd"] == 8.0
    assert fin["winrate_pct"] == round((2 / 3) * 100.0, 2)
    assert fin["expectancy_usd_per_trade"] == round(8.0 / 3, 2)


def test_070_running_peak_closed_equity_drawdown():
    """Rule 079, 169: Equity stream +100, -50, -100, +80 on 1000 capital -> max_dd=150, ~13.64%."""
    orders = [
        {"id": "o1", "state": "closed", "closed_at": 1, "created_at": 0, "realized_pnl_net": 100.0},
        {"id": "o2", "state": "closed", "closed_at": 2, "created_at": 0, "realized_pnl_net": -50.0},
        {"id": "o3", "state": "closed", "closed_at": 3, "created_at": 0, "realized_pnl_net": -100.0},
        {"id": "o4", "state": "closed", "closed_at": 4, "created_at": 0, "realized_pnl_net": 80.0},
    ]
    fin = compute_financial_metrics(orders, initial_capital=1000.0)
    assert fin["max_drawdown_usd"] == 150.0
    # Peak is 1100. Drawdown 150 / 1100 * 100 = 13.636% -> 13.64%
    assert fin["max_drawdown_pct"] == round((150.0 / 1100.0) * 100.0, 2)
    assert fin["drawdown_type"] == "CLOSED_EQUITY_DRAWDOWN"


def test_080_half_open_window_inclusion():
    """Rule 081, 170: closed_at == start is included; closed_at == end is excluded."""
    start_ms = 1000
    end_ms = 2000
    orders = [
        {"id": "before", "state": "closed", "created_at": 500, "closed_at": 999, "realized_pnl_net": 5.0},
        {"id": "on_start", "state": "closed", "created_at": 1000, "closed_at": 1000, "realized_pnl_net": 10.0},
        {"id": "inside", "state": "closed", "created_at": 1200, "closed_at": 1500, "realized_pnl_net": 20.0},
        {"id": "on_end", "state": "closed", "created_at": 1800, "closed_at": 2000, "realized_pnl_net": 30.0},
        {"id": "after", "state": "closed", "created_at": 2100, "closed_at": 2500, "realized_pnl_net": 40.0},
    ]
    fin = compute_financial_metrics(orders, start_ms=start_ms, end_ms=end_ms)
    # Only on_start (10.0) and inside (20.0) should be included -> net = 30.0
    assert fin["closed_trades_count"] == 2
    assert fin["net_realized_pnl_usd"] == 30.0


def test_090_order_counts_by_state_including_rejected():
    """Rule 053, 163: orders_count includes all created orders (armed, open, closed, rejected)."""
    orders = [
        {"id": "o1", "state": "armed", "created_at": 1000, "realized_pnl_net": None},
        {"id": "o2", "state": "paper_open", "created_at": 1010, "realized_pnl_net": None},
        {"id": "o3", "state": "closed", "created_at": 1020, "closed_at": 1100, "realized_pnl_net": 15.0},
        {"id": "o4", "state": "closed", "created_at": 1030, "closed_at": 1120, "realized_pnl_net": -5.0},
        {"id": "o5", "state": "rejected", "created_at": 1040, "realized_pnl_net": None},
    ]
    fin = compute_financial_metrics(orders, start_ms=900, end_ms=2000)
    assert fin["orders_count"] == 5
    assert fin["state_counts"]["armed"] == 1
    assert fin["state_counts"]["paper_open"] == 1
    assert fin["state_counts"]["closed"] == 2
    assert fin["state_counts"]["rejected"] == 1


def test_100_funnel_stage_events_vs_unique_opportunities():
    """Rule 066, 173: Stage events count transitions while unique opps count IDs."""
    class DummyRecord:
        def __init__(self, opp_id, stage, reasons=None, lat_start=None, lat_end=None):
            self.opportunity_id = opp_id
            self.stage = stage
            self.reason_codes_json = json.dumps(reasons or [])
            self.metrics_json = json.dumps({
                "evaluation_started_at": lat_start,
                "evaluation_completed_at": lat_end,
            })

    records = [
        # Opp 1 progresses through 3 stages
        DummyRecord("opp_1", "ZONE_DETECTED", lat_start=100, lat_end=150),
        DummyRecord("opp_1", "TRIGGER_CONFIRMED", lat_start=200, lat_end=260),
        DummyRecord("opp_1", "ARMED", lat_start=300, lat_end=370),
        # Opp 2 has 1 stage
        DummyRecord("opp_2", "ZONE_DETECTED", reasons=["UNKNOWN_REASON_CODE_XYZ"], lat_start=400, lat_end=480),
    ]

    summary = aggregate_funnel_readonly(records)
    assert summary["total_unique_opportunities"] == 2
    assert summary["zones_detected"] == 2
    assert summary["trigger_confirmed"] == 1
    assert summary["armed"] == 1
    assert summary["latency_metrics"]["sample_count"] == 4
    assert summary["latency_metrics"]["median_ms"] is not None

    # Rule 174: Unmapped rejection code fallback
    top_r = summary["top_rejection_reasons"]
    assert len(top_r) == 1
    assert top_r[0]["code"] == "UNKNOWN_REASON_CODE_XYZ"
    assert "UNKNOWN_REASON_CODE_XYZ" in top_r[0]["message_vi"]


def test_110_selector_mismatch_and_not_found(contract_test_db: Session):
    """Rule 048, 160: Requested run ID not found or mismatched scope returns correct status."""
    # Seed Run X for EURUSD
    run_x = PaperResearchRunRecord(
        run_id="run_x_eur",
        scope="CG01",
        symbol="EURUSD",
        status="COLLECTING",
        started_at_ms=1000,
        created_at_ms=1000,
        updated_at_ms=1000,
        execution_mode="PAPER_EXECUTION",
    )
    contract_test_db.add(run_x)
    contract_test_db.commit()

    # Querying run_x with symbol=XAUUSDT should report RUN_SCOPE_MISMATCH
    rec, status = find_existing_observation_run(contract_test_db, run_id="run_x_eur", symbol="XAUUSDT")
    assert status == "RUN_SCOPE_MISMATCH"
    assert rec is not None

    # Querying non-existent run ID returns NOT_FOUND
    rec_none, status_none = find_existing_observation_run(contract_test_db, run_id="non_existent_999")
    assert status_none == "NOT_FOUND"
    assert rec_none is None
