"""
backend/tests/test_v16_9_executable_sizing_contract.py
V16.9 Acceptance Suite - Group 02 (PHẦN 018 - 020), Group 12 & 13 (PHẦN 111 - 130), Group 15 (PHẦN 147 - 150).

Verifies:
1. Pure resolver resolve_executable_sizing_context differentiates target budget, safety budget, and executable budget.
2. CURRENT_ACCOUNT_BALANCE scales target risk with current account balance (gain & loss).
3. FIXED_INITIAL_CAPITAL preserves initial target budget when account equity is sufficient.
4. FIXED_INITIAL_CAPITAL applies ACCOUNT_SAFETY_CAP when current equity is degraded below target.
5. End-to-end execution through ExecutionCoordinator persists sizing contract facts into cost_snapshot at fill.
6. Symmetric validation for both LONG and SHORT directions.
7. Legacy order compatibility (orders without sizing intent continue with legacy behavior safely).
"""
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import crud
from database import run_schema_migrations
from services.clock import ReplayClock
from services.execution_coordinator import (
    ExecutionCoordinator,
    resolve_executable_sizing_context,
)
from services.order_plan_snapshot_service import (
    build_arm_plan_snapshot,
    build_fill_plan_snapshot,
)
from domain_calculator import CostAssumptions, calculate_risk_reward


def test_resolve_executable_sizing_context_current_mode():
    """PHẦN 112, 149: CURRENT_ACCOUNT_BALANCE scales target budget with account equity."""
    order = models.PaperOrder(
        id="ord_curr_test",
        direction="LONG",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2025.0,
        risk_pct=0.10,
        initial_risk_usdt=5.0,
    )

    # Initial 5000 -> target is 5.0
    ctx1 = resolve_executable_sizing_context(order, current_equity=5000.0, capital_mode="CURRENT_ACCOUNT_BALANCE")
    assert ctx1["capital_mode"] == "CURRENT_ACCOUNT_BALANCE"
    assert ctx1["target_capital"] == 5000.0
    assert ctx1["target_budget"] == 5.0
    assert ctx1["executable_budget"] == 5.0
    assert ctx1["adjustment_reason"] == "NONE"

    # Degraded 4500 -> target reduces to 4.5
    ctx2 = resolve_executable_sizing_context(order, current_equity=4500.0, capital_mode="CURRENT_ACCOUNT_BALANCE")
    assert ctx2["target_capital"] == 4500.0
    assert ctx2["target_budget"] == 4.5
    assert ctx2["executable_budget"] == 4.5
    assert ctx2["adjustment_reason"] == "NONE"

    # Gain 5500 -> target increases to 5.5
    ctx3 = resolve_executable_sizing_context(order, current_equity=5500.0, capital_mode="CURRENT_ACCOUNT_BALANCE")
    assert ctx3["target_capital"] == 5500.0
    assert ctx3["target_budget"] == 5.5
    assert ctx3["executable_budget"] == 5.5


def test_resolve_executable_sizing_context_fixed_mode():
    """PHẦN 113, 114, 148: FIXED_INITIAL_CAPITAL preserves target, caps when equity degraded."""
    snap = {
        "capital_mode": "FIXED_INITIAL_CAPITAL",
        "target_capital": 5000.0,
        "target_risk_budget": 5.0,
        "risk_pct": 0.10,
    }
    order = models.PaperOrder(
        id="ord_fixed_test",
        direction="LONG",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2025.0,
        risk_pct=0.10,
        initial_risk_usdt=5.0,
    )

    # When equity is 5000: target is 5.0, executable is 5.0
    ctx1 = resolve_executable_sizing_context(order, current_equity=5000.0, order_cost_snap=snap)
    assert ctx1["capital_mode"] == "FIXED_INITIAL_CAPITAL"
    assert ctx1["target_capital"] == 5000.0
    assert ctx1["target_budget"] == 5.0
    assert ctx1["executable_budget"] == 5.0
    assert ctx1["adjustment_reason"] == "NONE"

    # When equity is degraded to 4500: target is still 5.0, but safety cap reduces executable to 4.5
    ctx2 = resolve_executable_sizing_context(order, current_equity=4500.0, order_cost_snap=snap)
    assert ctx2["capital_mode"] == "FIXED_INITIAL_CAPITAL"
    assert ctx2["target_capital"] == 5000.0
    assert ctx2["target_budget"] == 5.0
    assert ctx2["executable_budget"] == 4.5
    assert ctx2["adjustment_reason"] == "ACCOUNT_SAFETY_CAP"


def test_coordinator_fill_persists_sizing_contract():
    """PHẦN 018, 126, 128, 130, 150: Coordinator fill persists sizing contract in cost_snapshot."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    clock = ReplayClock(initial_ms=1_000_000)

    try:
        # Create audit with degraded equity (4500)
        audit = crud.get_or_create_today_audit(db, clock=clock)
        audit.current_equity = 4500.0
        audit.current_balance = 4500.0
        db.commit()

        # Arm a LIMIT order with FIXED_INITIAL_CAPITAL intent
        calc_arm = calculate_risk_reward(
            direction="LONG",
            entry=100.0,
            sl=90.0,
            tp=125.0,
            capital=5000.0,
            risk_pct=0.10,
        )
        arm_snap = build_arm_plan_snapshot(
            calc_arm,
            planned_entry=100.0,
            stop_loss=90.0,
            take_profit=125.0,
            capital_usdt=5000.0,
            risk_pct=0.10,
            leverage=5,
            now_ms=1_000_000,
        )
        arm_snap["capital_mode"] = "FIXED_INITIAL_CAPITAL"
        arm_snap["target_capital"] = 5000.0
        arm_snap["target_risk_budget"] = 5.0

        order = models.PaperOrder(
            id="ord_coord_sizing_test",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="LIMIT",
            planned_entry=100.0,
            stop_loss=90.0,
            take_profit=125.0,
            quantity=calc_arm.quantity,
            initial_risk_usdt=5.0,
            risk_pct=0.10,
            cost_snapshot=json.dumps(dict(arm_snap)),
            created_at=1_000_000,
            armed_at=1_000_000,
            expires_at=2_000_000,
        )
        db.add(order)
        db.commit()

        coord = ExecutionCoordinator()
        # Trigger fill at price 100.0
        coord.evaluate_orders_sync(
            db,
            ticker_override={"bid": 99.95, "ask": 100.0, "last": 100.0, "timestamp": 1_010_000},
            clock=clock,
            now_ms=1_010_000,
        )

        db.refresh(order)
        assert order.state == "paper_open"
        fill_snap = json.loads(order.cost_snapshot)
        assert fill_snap["phase"] == "FILLED_ESTIMATE"
        assert fill_snap["capital_mode"] == "FIXED_INITIAL_CAPITAL"
        assert fill_snap["target_capital"] == 5000.0
        assert fill_snap["target_risk_budget"] == 5.0
        assert fill_snap["account_capital_at_fill"] == 4500.0
        assert fill_snap["sizing_adjustment_reason"] == "ACCOUNT_SAFETY_CAP"
    finally:
        db.close()


def test_resolve_executable_sizing_context_excess_equity():
    """PHẦN 113, 114: FIXED_INITIAL_CAPITAL keeps initial budget when account equity grows."""
    snap = {
        "capital_mode": "FIXED_INITIAL_CAPITAL",
        "target_capital": 5000.0,
        "target_risk_budget": 5.0,
        "risk_pct": 0.10,
    }
    order = models.PaperOrder(
        id="ord_excess_equity",
        direction="LONG",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2025.0,
        risk_pct=0.10,
        initial_risk_usdt=5.0,
    )
    # When equity grew to 6000: fixed initial capital stays at 5.0, adjustment_reason NONE
    ctx = resolve_executable_sizing_context(order, current_equity=6000.0, order_cost_snap=snap)
    assert ctx["capital_mode"] == "FIXED_INITIAL_CAPITAL"
    assert ctx["target_capital"] == 5000.0
    assert ctx["target_budget"] == 5.0
    assert ctx["executable_budget"] == 5.0
    assert ctx["adjustment_reason"] == "NONE"


def test_resolve_executable_sizing_context_short_direction():
    """PHẦN 115, 149: SHORT direction sizing contract symmetry."""
    snap = {
        "capital_mode": "FIXED_INITIAL_CAPITAL",
        "target_capital": 5000.0,
        "target_risk_budget": 5.0,
        "risk_pct": 0.10,
    }
    order = models.PaperOrder(
        id="ord_short_sizing",
        direction="SHORT",
        planned_entry=2050.0,
        stop_loss=2060.0,
        take_profit=2025.0,
        risk_pct=0.10,
        initial_risk_usdt=5.0,
    )
    # Degraded equity safety cap works symmetrically for SHORT
    ctx = resolve_executable_sizing_context(order, current_equity=4000.0, order_cost_snap=snap)
    assert ctx["capital_mode"] == "FIXED_INITIAL_CAPITAL"
    assert ctx["target_capital"] == 5000.0
    assert ctx["target_budget"] == 5.0
    assert ctx["executable_budget"] == 4.0
    assert ctx["adjustment_reason"] == "ACCOUNT_SAFETY_CAP"


def test_legacy_order_fill_without_sizing_context():
    """PHẦN 117, 126: Legacy order without sizing metadata fills safely and populates defaults."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    clock = ReplayClock(initial_ms=1_000_000)

    try:
        audit = crud.get_or_create_today_audit(db, clock=clock)
        audit.current_equity = 1000.0
        db.commit()

        # Legacy order with minimal attributes
        order = models.PaperOrder(
            id="ord_legacy",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="LIMIT",
            planned_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2030.0,
            quantity=0.05,
            initial_risk_usdt=0.5,
            risk_pct=0.05,
            cost_snapshot=None,  # No arm snapshot
            created_at=1_000_000,
            armed_at=1_000_000,
            expires_at=2_000_000,
        )
        db.add(order)
        db.commit()

        coord = ExecutionCoordinator()
        coord.evaluate_orders_sync(
            db,
            ticker_override={"bid": 1999.5, "ask": 2000.0, "last": 2000.0, "timestamp": 1_010_000},
            clock=clock,
            now_ms=1_010_000,
        )

        db.refresh(order)
        assert order.state == "paper_open"
        fill_snap = json.loads(order.cost_snapshot)
        assert fill_snap["capital_mode"] == "CURRENT_ACCOUNT_BALANCE"
        assert fill_snap["executable_risk_budget"] > 0
        assert fill_snap["sizing_adjustment_reason"] == "NONE"
    finally:
        db.close()
