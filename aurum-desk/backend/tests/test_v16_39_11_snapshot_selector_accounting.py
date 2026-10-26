"""
backend/tests/test_v16_39_11_snapshot_selector_accounting.py
V16.39.11 Acceptance & Regression Suite: Snapshot Selector & Financial Accounting

Covers Rules 021-050 & 141-152:
- Scope mismatch & missing run isolation (no financial or funnel leakage)
- Prevention of unrequested fallback to completed runs
- OpportunityStage.PLAN_VALID enum aggregation parity
- Sub-window opening equity and closed-equity drawdown % calculation
- Exclusion of future orders from window state counts
- Unresolved closed outcomes & partial accounting integrity
- Clean 0-closed run reporting (realized net = 0.0, not None)
- Missing closed_at timestamp isolation
- HTTP schema validation enforcement
"""

import math
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services.paper_runtime_snapshot_service import (
    find_existing_observation_run,
    build_paper_runtime_snapshot,
    compute_financial_metrics,
    compute_window_opening_equity,
    read_run_funnel_records,
    aggregate_funnel_readonly,
    render_markdown,
)
from services.scalp_m1_types import OpportunityStage
from tools.paper_runtime_snapshot import parse_http_snapshot


@pytest.fixture
def mem_db():
    engine = create_engine("sqlite:///:memory:", echo=False)
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_scope_mismatch_prevents_financial_and_funnel_leakage(mem_db):
    """
    Rule 142: Seed run with scope OTHER and closed order +25 USD.
    Requesting scope CG01 must return RUN_SCOPE_MISMATCH, 0 orders, None PnL, and empty funnel.
    """
    run = models.PaperResearchRunRecord(
        run_id="run_other_scope_1",
        symbol="XAUUSDT",
        scope="OTHER_MODEL",
        status="COLLECTING",
        started_at_ms=1000000,
        initial_capital_usdt=1000.0,
        created_at_ms=1000000,
        updated_at_ms=1000000,
    )
    mem_db.add(run)

    order = models.PaperOrder(
        id="ord_mismatch_1",
        setup_id="set_1",
        direction="LONG",
        state="closed",
        planned_entry=2000.0,
        actual_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        realized_pnl_net=25.0,
        created_at=1001000,
        closed_at=1005000,
    )
    mem_db.add(order)
    link = models.PaperResearchOrderLink(
        run_id="run_other_scope_1",
        order_id="ord_mismatch_1",
        linked_at_ms=1001000,
    )
    mem_db.add(link)

    funnel_rec = models.OpportunityFunnelRecord(
        opportunity_id="opp_mismatch_1",
        symbol="XAUUSDT",
        model_id="CG01",
        direction="LONG",
        session_id="run_other_scope_1",
        run_id="run_other_scope_1",
        stage=OpportunityStage.TRIGGER_CONFIRMED.value,
        as_of_ms=1002000,
        payload_json="{}",
        created_at=1002000,
        updated_at=1002000,
    )
    mem_db.add(funnel_rec)
    mem_db.commit()

    # Request CG01 scope for this run_id
    snap = build_paper_runtime_snapshot(
        mem_db,
        run_id="run_other_scope_1",
        symbol="XAUUSDT",
        scope="CG01",
        now_ms=1010000,
    )

    assert snap["run_summary"]["resolution_status"] == "RUN_SCOPE_MISMATCH"
    assert snap["status"] == "NOT_CONFIGURED"
    # Financial metrics must NOT leak the +25 USD order
    assert snap["metrics"]["orders_count"] == 0
    assert snap["metrics"]["closed_trades_count"] == 0
    assert snap["metrics"]["net_realized_pnl_usd"] is None
    # Funnel must NOT leak the opportunity
    assert snap["metrics"]["opportunities_count"] == 0
    assert snap["funnel_summary"]["trigger_confirmed"] == 0


def test_missing_run_does_not_leak_symbol_funnel(mem_db):
    """
    Rule 143: DB has runB and funnel records for XAUUSDT.
    Requesting non-existent run_id must return RUN_NOT_FOUND and empty funnel.
    """
    run_b = models.PaperResearchRunRecord(
        run_id="run_b_1",
        symbol="XAUUSDT",
        scope="CG01",
        status="COLLECTING",
        started_at_ms=1000000,
        created_at_ms=1000000,
        updated_at_ms=1000000,
    )
    mem_db.add(run_b)
    opp_b = models.OpportunityFunnelRecord(
        opportunity_id="opp_b_1",
        symbol="XAUUSDT",
        model_id="CG01",
        direction="LONG",
        session_id="run_b_1",
        run_id="run_b_1",
        stage=OpportunityStage.ZONE_DETECTED.value,
        as_of_ms=1002000,
        payload_json="{}",
        created_at=1002000,
        updated_at=1002000,
    )
    mem_db.add(opp_b)
    mem_db.commit()

    snap = build_paper_runtime_snapshot(
        mem_db,
        run_id="non_existent_run",
        symbol="XAUUSDT",
        scope="CG01",
        now_ms=1010000,
    )

    assert snap["run_summary"]["resolution_status"] == "NOT_FOUND"
    assert snap["metrics"]["opportunities_count"] == 0
    assert snap["funnel_summary"]["zones_detected"] == 0


def test_completed_run_not_selected_by_default(mem_db):
    """
    Rule 144: Only completed runs in DB; requesting without run_id must NOT select completed run.
    """
    completed_run = models.PaperResearchRunRecord(
        run_id="run_completed_1",
        symbol="XAUUSDT",
        scope="CG01",
        status="COMPLETED",
        started_at_ms=1000000,
        ended_at_ms=1050000,
        created_at_ms=1000000,
        updated_at_ms=1000000,
    )
    mem_db.add(completed_run)
    mem_db.commit()

    run_found, status = find_existing_observation_run(mem_db, run_id=None, symbol="XAUUSDT", scope="CG01")
    assert run_found is None
    assert status == "NOT_FOUND"

    # But with explicit run_id, it is readable
    explicit_run, exp_status = find_existing_observation_run(mem_db, run_id="run_completed_1", symbol="XAUUSDT", scope="CG01")
    assert explicit_run is not None
    assert exp_status == "FOUND"


def test_funnel_plan_valid_enum_aggregation():
    """
    Rule 145: aggregate_funnel_readonly counts OpportunityStage.PLAN_VALID ("PLAN_VALID").
    """
    class DummyRecord:
        def __init__(self, stage, opp_id="opp_1"):
            self.stage = stage
            self.opportunity_id = opp_id
            self.reason_codes_json = "[]"
            self.metrics_json = "{}"

    records = [
        DummyRecord(OpportunityStage.ZONE_DETECTED.value),
        DummyRecord(OpportunityStage.TRIGGER_CONFIRMED.value),
        DummyRecord(OpportunityStage.PLAN_VALID.value),  # "PLAN_VALID"
        DummyRecord(OpportunityStage.ARMED.value),
    ]

    res = aggregate_funnel_readonly(records)
    assert res["plans_valid"] == 1
    assert res["zones_detected"] == 1
    assert res["trigger_confirmed"] == 1
    assert res["armed"] == 1


def test_subwindow_opening_equity_and_drawdown():
    """
    Rules 032, 147:
    Initial capital 1000 USD.
    Trade 1 (before start_ms): closed at 1000ms with +100 USD.
    Trade 2 (in window [2000, 5000)): closed at 3000ms with -100 USD.
    Opening equity = 1100 USD.
    Drawdown USD = 100 USD.
    Drawdown % = (100 / 1100) * 100 ≈ 9.09% (baseline gave 10.0% using initial capital).
    """
    orders = [
        {
            "id": "ord_1",
            "state": "closed",
            "created_at": 500,
            "closed_at": 1000,
            "realized_pnl_net": 100.0,
        },
        {
            "id": "ord_2",
            "state": "closed",
            "created_at": 2500,
            "closed_at": 3000,
            "realized_pnl_net": -100.0,
        },
    ]

    opening_info = compute_window_opening_equity(orders, initial_capital=1000.0, start_ms=2000)
    assert opening_info["completeness"] == "COMPLETE"
    assert opening_info["opening_equity"] == 1100.0
    assert opening_info["prior_trades_count"] == 1

    fin = compute_financial_metrics(orders, initial_capital=1000.0, start_ms=2000, end_ms=5000)
    assert fin["closed_trades_count"] == 1
    assert fin["max_drawdown_usd"] == 100.0
    # Expected: 100 / 1100 * 100 = 9.0909...
    expected_pct = round((100.0 / 1100.0) * 100.0, 2)
    assert fin["max_drawdown_pct"] == expected_pct
    assert fin["max_drawdown_pct"] != 10.0


def test_unverified_prior_trade_makes_window_drawdown_pct_null():
    """
    Rule 147: Closed trade before start_ms missing PnL -> opening equity unverified -> max_drawdown_pct is None.
    """
    orders = [
        {
            "id": "ord_prior_missing",
            "state": "closed",
            "created_at": 500,
            "closed_at": 1000,
            "realized_pnl_net": None,
        },
        {
            "id": "ord_window_1",
            "state": "closed",
            "created_at": 2500,
            "closed_at": 3000,
            "realized_pnl_net": -50.0,
        },
    ]
    fin = compute_financial_metrics(orders, initial_capital=1000.0, start_ms=2000, end_ms=5000)
    assert fin["max_drawdown_pct"] is None
    assert fin["max_drawdown_usd"] == 50.0


def test_future_orders_excluded_from_window_open_and_armed():
    """
    Rules 034, 146: Orders created at or after window end_ms must not be counted in window open/armed.
    """
    orders = [
        {"id": "o_in_win", "state": "open", "created_at": 1500},
        {"id": "o_future_open", "state": "open", "created_at": 5000},  # created >= end_ms (5000)
        {"id": "o_future_armed", "state": "armed", "created_at": 6000},
    ]
    fin = compute_financial_metrics(orders, initial_capital=1000.0, start_ms=1000, end_ms=5000)
    assert fin["open_positions_count"] == 1
    assert fin["armed_orders_count"] == 0


def test_incomplete_financial_outcomes_reporting():
    """
    Rules 037, 038, 148:
    1 verified win (+10 USD) and 1 closed with None PnL:
    - accounting_status = "PARTIAL"
    - net_realized_pnl_usd is None (total unknown)
    - verified_net_pnl_subtotal_usd is 10.0
    - winrate_pct is None (do not display 100% winrate as complete)
    - verified_winrate_pct is 100.0
    """
    orders = [
        {"id": "o_win", "state": "closed", "created_at": 1000, "closed_at": 2000, "realized_pnl_net": 10.0},
        {"id": "o_unres", "state": "closed", "created_at": 1100, "closed_at": 2100, "realized_pnl_net": None},
    ]
    fin = compute_financial_metrics(orders, initial_capital=1000.0, start_ms=500, end_ms=3000)
    assert fin["accounting_status"] == "PARTIAL"
    assert fin["unresolved_closed_count"] == 1
    assert fin["verified_closed_count"] == 1
    assert fin["net_realized_pnl_usd"] is None
    assert fin["verified_net_pnl_subtotal_usd"] == 10.0
    assert fin["winrate_pct"] is None
    assert fin["verified_winrate_pct"] == 100.0


def test_only_open_orders_reports_zero_realized_pnl():
    """
    Rules 039, 149: Run has only open/armed orders, zero closed trades, zero unresolved.
    Realized net PnL is 0.0, winrate is None, accounting_status is COMPLETE.
    """
    orders = [
        {"id": "o_open_1", "state": "open", "created_at": 1000},
        {"id": "o_armed_1", "state": "armed", "created_at": 1200},
    ]
    fin = compute_financial_metrics(orders, initial_capital=1000.0, start_ms=500, end_ms=3000)
    assert fin["accounting_status"] == "COMPLETE"
    assert fin["closed_trades_count"] == 0
    assert fin["unresolved_closed_count"] == 0
    assert fin["net_realized_pnl_usd"] == 0.0
    assert fin["winrate_pct"] is None


def test_closed_order_missing_closed_at_timestamp():
    """
    Rules 036, 150: Order marked closed but missing closed_at must NOT be silently assigned created_at.
    """
    orders = [
        {"id": "o_legacy_no_ts", "state": "closed", "created_at": 1000, "closed_at": None, "realized_pnl_net": 5.0}
    ]
    fin = compute_financial_metrics(orders, initial_capital=1000.0, start_ms=500, end_ms=3000)
    assert fin["unresolved_timestamp_count"] == 1
    assert fin["unresolved_closed_count"] == 1
    assert fin["closed_trades_count"] == 0


def test_parse_http_snapshot_schema_validation():
    """
    Rules 044, 152: parse_http_snapshot rejects invalid schema version or missing metrics keys.
    """
    with pytest.raises(ValueError, match="Unsupported schema_version"):
        parse_http_snapshot({"schema_version": "1.0", "metrics": {}})

    with pytest.raises(ValueError, match="missing required field"):
        parse_http_snapshot({"schema_version": "2.0", "metrics": {}})

    with pytest.raises(ValueError, match="missing required metric"):
        parse_http_snapshot({
            "schema_version": "2.0",
            "status": "SNAPSHOT_READY",
            "time_window": {},
            "effective_config": {},
            "worker_health": {},
            "metrics": {"duration_minutes": 1.0},
        })
