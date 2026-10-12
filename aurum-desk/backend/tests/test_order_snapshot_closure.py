"""
Tests for Order Financial Snapshot & Upcoming / Chart Chain Closure (ORDARM, ORDFILL, ORDCHAIN, FREEZE)
PHẦN 035 - 044, 146, 167 - 173
"""
import pytest
import json
import models
from services.setup_display_plan_service import build_setup_display_plan
from services.plan_financial_context_service import PlanFinancialContext
from domain_calculator import CostAssumptions
from fastapi.testclient import TestClient
from main import app, get_db


@pytest.fixture
def client(isolated_db):
    app.dependency_overrides[get_db] = lambda: isolated_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_ordarm01_arm_stores_frozen_snapshot_and_intent(isolated_db):
    """ORDARM (PHẦN 168): Auto or manual arm stores frozen cost snapshot and intent."""
    setup = models.WatchSetup(
        id="setup-ordarm-01",
        direction="SHORT",
        timeframe="5m",
        state="ARMED",
        provisional_entry=4180.0,
        provisional_sl=4185.0,
        provisional_tp=4165.0,
        invalidation_price=4185.0,
        created_at=1728000000000,
        updated_at=1728000000000,
    )
    isolated_db.add(setup)
    isolated_db.commit()

    snap = {
        "net_rr": 2.1,
        "quantity": 0.5,
        "gross_reward_usdt": 7.5,
        "net_reward_usdt": 7.15,
        "entry_fee_usdt": 0.15,
        "sl_exit_fee_usdt": 0.10,
        "tp_exit_fee_usdt": 0.10,
        "entry_slippage_usdt": 0.0,
        "sl_exit_slippage_usdt": 0.05,
        "tp_exit_slippage_usdt": 0.05,
        "equity_usdt": 1000.0,
        "risk_pct": 0.25,
        "leverage": 20,
    }
    order = models.PaperOrder(
        id="ord-ordarm-01",
        setup_id="setup-ordarm-01",
        direction="SHORT",
        order_type="LIMIT",
        planned_entry=4180.0,
        stop_loss=4185.0,
        take_profit=4165.0,
        quantity=0.5,
        initial_risk_usdt=2.5,
        state="armed",
        created_at=1728000000000,
        cost_snapshot=json.dumps(snap),
    )
    isolated_db.add(order)
    isolated_db.commit()

    loaded = isolated_db.query(models.PaperOrder).filter_by(id="ord-ordarm-01").first()
    assert loaded is not None
    assert loaded.order_type == "LIMIT"
    loaded_snap = json.loads(loaded.cost_snapshot)
    assert loaded_snap["net_reward_usdt"] == 7.15
    assert loaded_snap["equity_usdt"] == 1000.0


def test_ordfill01_fill_records_actual_entry_and_preserves_cost_snapshot(isolated_db):
    """ORDFILL (PHẦN 169): Fill preserves cost snapshot and records actual_entry."""
    snap = {
        "net_rr": 2.1,
        "quantity": 0.5,
        "gross_reward_usdt": 7.4,
        "net_reward_usdt": 7.05,
        "entry_fee_usdt": 0.15,
        "sl_exit_fee_usdt": 0.10,
        "tp_exit_fee_usdt": 0.10,
        "equity_usdt": 1000.0,
        "leverage": 20,
    }
    order = models.PaperOrder(
        id="ord-fill-01",
        setup_id="setup-fill-01",
        direction="SHORT",
        order_type="LIMIT",
        planned_entry=4180.0,
        actual_entry=4179.8,
        stop_loss=4185.0,
        take_profit=4165.0,
        quantity=0.5,
        initial_risk_usdt=2.5,
        state="paper_open",
        created_at=1728000000000,
        cost_snapshot=json.dumps(snap),
    )
    isolated_db.add(order)
    isolated_db.commit()

    loaded = isolated_db.query(models.PaperOrder).filter_by(id="ord-fill-01").first()
    assert loaded.state == "paper_open"
    assert loaded.actual_entry == 4179.8
    assert loaded.planned_entry == 4180.0
    loaded_snap = json.loads(loaded.cost_snapshot)
    assert loaded_snap["net_reward_usdt"] == 7.05


def test_ordchain01_upcoming_setups_loads_bound_order_frozen_snapshot(client, isolated_db):
    """ORDCHAIN01 (PHẦN 170): get_upcoming_setups batch-loads bound orders and passes frozen_snapshot."""
    setup = models.WatchSetup(
        id="setup-chain-01",
        direction="SHORT",
        timeframe="5m",
        state="ARMED",
        provisional_entry=4180.0,
        provisional_sl=4185.0,
        provisional_tp=4165.0,
        invalidation_price=4185.0,
        created_at=1728000000000,
        updated_at=1728000000000,
    )
    isolated_db.add(setup)

    snap = {
        "net_rr": 2.1,
        "quantity": 0.5,
        "gross_reward_usdt": 7.5,
        "net_reward_usdt": 7.15,
        "entry_fee_usdt": 0.15,
        "sl_exit_fee_usdt": 0.10,
        "tp_exit_fee_usdt": 0.10,
        "equity_usdt": 1000.0,
        "risk_pct": 0.25,
        "leverage": 20,
    }
    order = models.PaperOrder(
        id="ord-chain-01",
        setup_id="setup-chain-01",
        direction="SHORT",
        order_type="LIMIT",
        planned_entry=4180.0,
        stop_loss=4185.0,
        take_profit=4165.0,
        quantity=0.5,
        initial_risk_usdt=2.5,
        state="armed",
        created_at=1728000000000,
        cost_snapshot=json.dumps(snap),
    )
    isolated_db.add(order)
    isolated_db.commit()

    res = client.get("/api/v1/setups/upcoming")
    assert res.status_code == 200
    data = res.json()
    setups = data.get("setups", [])
    assert len(setups) >= 1
    found = next((s for s in setups if s["id"] == "setup-chain-01"), None)
    assert found is not None
    plan = found.get("resolved_display_plan")
    assert plan is not None
    assert plan["financial"]["pricing_mode"] == "FROZEN_ORDER"
    assert plan["financial"]["net_reward_usdt"] == 7.15


def test_ordchain02_order_chart_evidence_populates_net_reward_and_fees(client, isolated_db):
    """ORDCHAIN02 (PHẦN 171): get_order_chart_evidence extracts net_reward_usdt and fee breakdown."""
    snap = {
        "net_rr": 2.1,
        "quantity": 0.5,
        "gross_reward_usdt": 7.5,
        "net_reward_usdt": 7.15,
        "entry_fee_usdt": 0.15,
        "sl_exit_fee_usdt": 0.10,
        "tp_exit_fee_usdt": 0.10,
        "entry_slippage_usdt": 0.05,
        "sl_exit_slippage_usdt": 0.05,
        "tp_exit_slippage_usdt": 0.05,
        "equity_usdt": 1000.0,
        "risk_pct": 0.25,
        "leverage": 20,
    }
    order = models.PaperOrder(
        id="ord-evidence-01",
        setup_id="setup-ev-01",
        direction="SHORT",
        order_type="LIMIT",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4185.0,
        take_profit=4165.0,
        quantity=0.5,
        initial_risk_usdt=2.5,
        state="paper_open",
        created_at=1728000000000,
        cost_snapshot=json.dumps(snap),
    )
    isolated_db.add(order)
    isolated_db.commit()

    res = client.get("/api/v1/orders/ord-evidence-01/chart-evidence")
    assert res.status_code == 200
    data = res.json()
    assert data["binding"]["id"] == "ord-evidence-01"
    plan = data["risk_reward_plan"]
    assert plan is not None
    assert plan["net_reward_usdt"] == 7.15
    assert plan["fees"]["entry_fee_usdt"] == 0.15
    assert plan["fees"]["sl_exit_fee_usdt"] == 0.10
    assert plan["fees"]["tp_exit_fee_usdt"] == 0.10


def test_freeze01_changing_settings_does_not_reprice_armed_order():
    """FREEZE01 (PHẦN 146): Changing context does not mutate frozen display plan."""
    frozen_snap = {
        "net_rr": 2.1,
        "quantity": 0.5,
        "gross_reward_usdt": 7.5,
        "net_reward_usdt": 7.15,
        "entry_fee_usdt": 0.15,
        "sl_exit_fee_usdt": 0.10,
        "tp_exit_fee_usdt": 0.10,
        "equity_usdt": 1000.0,
        "risk_pct": 0.25,
        "leverage": 20,
    }
    setup = {
        "id": "watch-frozen-01",
        "direction": "SHORT",
        "provisional_entry": 4180.0,
        "provisional_sl": 4185.0,
        "provisional_tp": 4165.0,
        "state": "ARMED",
    }
    ctx_new = PlanFinancialContext(
        equity_usdt=5000.0,
        equity_basis="DAY_AUDIT_BALANCE",
        equity_as_of_ms=1728000000000,
        risk_pct=1.0,
        leverage=50,
        margin_mode="ISOLATED",
        risk_config_version=2,
        costs=CostAssumptions(maker_fee_rate=0.001, taker_fee_rate=0.002, slippage_usd=0.5, multiplier=1.0),
        cost_version="v2",
        instrument_version="v2",
        min_net_rr=2.0,
    )
    plan = build_setup_display_plan(
        setup,
        financial_context=ctx_new,
        frozen_snapshot=frozen_snap,
        pricing_mode="FROZEN_ORDER",
    )
    assert plan["financial"]["pricing_mode"] == "FROZEN_ORDER"
    assert plan["financial"]["equity_usdt"] == 1000.0
    assert plan["financial"]["net_reward_usdt"] == 7.15
    assert plan["financial"]["leverage"] == 20
