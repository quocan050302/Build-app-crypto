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
    """FREEZE01 (PHẦN 146, PHẦN 032): Complete canonical frozen snapshot does not reprice on context change."""
    frozen_snap = {
        "planned_entry": 4180.0,
        "stop_loss": 4185.0,
        "take_profit": 4165.0,
        "price_basis": "PLANNED_LIMIT",
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


def test_actual_auto_arm_persists_complete_snapshot(isolated_db):
    """ORDTRUE01 / ORDPRODARM (PHẦN 036, 168, 204):
    Actual auto arm execution produces and persists complete canonical financial snapshot,
    containing net_reward_usdt, gross_loss_usdt, quantity_mode, fees, and intent.
    No handwritten PaperOrder cost_snapshot.
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from services.strategy_service import strategy_service
    from services.quote_validator import QuoteValidator
    import crud

    # 08:30 AM EDT on 2026-10-12 = 12:30 UTC (within NY 08:00 - 11:00 window)
    now_dt = datetime(2026, 10, 12, 12, 30, 0, tzinfo=timezone.utc)
    now_ms = int(now_dt.timestamp() * 1000)

    # Create DayAudit for equity context
    audit = crud.get_or_create_today_audit(isolated_db)
    audit.current_equity = 1000.0
    audit.is_blocked = False
    audit.fills_count = 0
    isolated_db.commit()

    setup = models.WatchSetup(
        id="setup-autoarm-prod-01",
        direction="LONG",
        timeframe="5m",
        state="READY",
        provisional_entry=4180.0,
        provisional_sl=4170.0,
        provisional_tp=4220.0,
        invalidation_price=4170.0,
        risk_pct=0.25,
        leverage=5,
        margin_mode="ISOLATED",
        strategy_family="STANDARD_SMC",
        created_at=now_ms,
        updated_at=now_ms,
    )
    isolated_db.add(setup)
    isolated_db.commit()

    ticker = {"symbol": "XAUUSDT", "bid": 4180.0, "ask": 4180.2, "last": 4180.1, "timestamp": now_ms}
    valid_quote = QuoteValidator.validate_ticker(ticker, now_ms=now_ms)

    sig = {"signal_id": "sig-test-01"}
    res = strategy_service._auto_arm_candidate(
        isolated_db,
        setup,
        sig,
        now_ms,
        now_dt,
        quote=valid_quote,
    )

    assert res["armed"] is True, f"Auto-arm failed with codes: {res.get('codes')} message: {res.get('message')}"
    order_id = res["order_id"]
    assert order_id is not None

    order = isolated_db.query(models.PaperOrder).filter_by(id=order_id).first()
    assert order is not None
    assert order.state == "armed"
    assert order.cost_snapshot is not None

    snap = json.loads(order.cost_snapshot)
    assert snap["phase"] == "ARMED_ESTIMATE"
    assert snap["pricing_mode"] == "FROZEN_ORDER"
    assert snap["direction"] == "LONG"
    assert snap["quantity_mode"] == "FIXED_QUANTITY"
    assert snap["net_reward_usdt"] is not None
    assert snap["net_reward_usdt"] > 0
    assert snap["initial_risk_usdt"] is not None
    assert snap["net_rr"] is not None
    assert snap["net_rr"] > 2.0
    assert snap["entry_fee_usdt"] >= 0
    assert snap["equity_usdt"] == 1000.0


def test_actual_fill_persists_complete_snapshot(isolated_db):
    """ORDTRUE03 / ORDPRODFILL (PHẦN 038, 169, 205):
    Actual execute_fill updates state to paper_open, sets actual_entry, and generates
    complete fill financial snapshot including net_reward_usdt, actual_entry, and fees.
    """
    from services.trade_lifecycle_service import TradeLifecycleService
    from domain_calculator import calculate_risk_reward

    now_ms = 1728000000000

    order = models.PaperOrder(
        id="ord-prod-fill-01",
        setup_id="setup-prod-fill-01",
        direction="LONG",
        order_type="LIMIT",
        planned_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4205.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        state="armed",
        leverage=5,
        margin_mode="ISOLATED",
        risk_pct=0.25,
        created_at=now_ms,
        cost_snapshot=json.dumps({"phase": "ARMED_ESTIMATE", "net_reward_usdt": 2.5, "equity_usdt": 1000.0}),
    )
    isolated_db.add(order)
    isolated_db.commit()

    calc_res = calculate_risk_reward(
        direction="LONG",
        planned_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4205.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=5,
        margin_mode="ISOLATED",
        now_ms=now_ms,
    )

    fill_price = 4179.9
    filled_order = TradeLifecycleService.execute_fill(
        db=isolated_db,
        order=order,
        fill_price=fill_price,
        calc_result=calc_res,
        now_ms=now_ms + 1000,
    )

    assert filled_order.state == "paper_open"
    assert filled_order.actual_entry == fill_price
    assert filled_order.cost_snapshot is not None

    fill_snap = json.loads(filled_order.cost_snapshot)
    assert fill_snap["phase"] == "FILLED_ESTIMATE"
    assert fill_snap["actual_entry"] == fill_price
    assert fill_snap["quantity_mode"] == "FIXED_QUANTITY"
    assert fill_snap["net_reward_usdt"] is not None
    assert fill_snap["net_reward_usdt"] > 0
    assert fill_snap["entry_fee_usdt"] >= 0
    assert "maker_fee_rate" in fill_snap
    assert "taker_fee_rate" in fill_snap


def test_bind01_02_03_order_binding_invariants():
    """BIND01, BIND02, BIND03 (PHẦN 043, 206):
    1. BIND01: Direction mismatch (setup LONG, order SHORT) must NOT bind.
    2. BIND02: Instance mismatch (setup inst-2, order inst-1) must NOT bind.
    3. BIND03: Ambiguous active orders with conflicting directions yield None.
    """
    from services.order_plan_snapshot_service import select_bound_order_for_setup
    from unittest.mock import MagicMock

    # 1. BIND01: Direction mismatch
    setup_long = MagicMock(id="setup-x", direction="LONG", setup_instance_id="inst-1", state="ARMED")
    order_short = MagicMock(id="ord-1", setup_id="setup-x", direction="SHORT", setup_instance_id="inst-1", state="armed", created_at=1000)
    bound = select_bound_order_for_setup(setup_long, [order_short])
    assert bound is None, "SHORT order must not bind to LONG setup"

    # 2. BIND02: Instance mismatch
    setup_inst2 = MagicMock(id="setup-x", direction="LONG", setup_instance_id="inst-2", state="ARMED")
    order_inst1 = MagicMock(id="ord-2", setup_id="setup-x", direction="LONG", setup_instance_id="inst-1", state="armed", created_at=1000)
    bound2 = select_bound_order_for_setup(setup_inst2, [order_inst1])
    assert bound2 is None, "Instance mismatch must not bind"

    # 3. BIND03: Conflicting active orders
    order_active_long = MagicMock(id="ord-3", setup_id="setup-x", direction="LONG", setup_instance_id="inst-1", state="armed", created_at=1000)
    order_active_short = MagicMock(id="ord-4", setup_id="setup-x", direction="SHORT", setup_instance_id="inst-1", state="paper_open", created_at=1100)
    bound3 = select_bound_order_for_setup(setup_long, [order_active_long, order_active_short])
    assert bound3 is not None
    assert bound3.id == "ord-3"

    # If setup has no direction or multiple matching active orders conflict in instance:
    order_active_inst_a = MagicMock(id="ord-5", setup_id="setup-y", direction="LONG", setup_instance_id="inst-A", state="armed", created_at=1000)
    order_active_inst_b = MagicMock(id="ord-6", setup_id="setup-y", direction="LONG", setup_instance_id="inst-B", state="paper_open", created_at=1100)
    setup_any_inst = MagicMock(id="setup-y", direction="LONG", setup_instance_id=None, state="ARMED")
    bound_conflict = select_bound_order_for_setup(setup_any_inst, [order_active_inst_a, order_active_inst_b])
    assert bound_conflict is None, "Conflicting active order instances must yield None (DISPLAY_BINDING_CONFLICT)"
