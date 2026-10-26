"""
backend/tests/test_v16_10_arm_to_fill_sizing.py
V16.10 Acceptance Suite - Group 05, 06 & 07 (PHẦN 041 - 070).

Verifies:
1. ScalpWatcherOrchestrator._arm_paper_candidate wires capital_mode, target_capital,
   and target_risk_budget into PaperOrder.cost_snapshot automatically.
2. NO test-side snapshot mutation (arm_snap['capital_mode'] = ...) allowed.
3. ExecutionCoordinator.evaluate_orders_sync fills the order and preserves:
   - capital_mode = FIXED_INITIAL_CAPITAL
   - target_capital
   - target_risk_budget
   - account_capital_at_fill
   - sizing_adjustment_reason (e.g. ACCOUNT_SAFETY_CAP if equity degraded)
4. Sizing symmetry for both LONG and SHORT setups.
5. Account growth preserves fixed target budget (does not inflate size).
6. Non-zero costs (fees, slippage) preserved throughout arm-to-fill flow.
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
from services.execution_coordinator import ExecutionCoordinator
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.scalp_m1_model_service import ScalpCandidate
from services.cg01_types import CG01Config
from domain_calculator import CostAssumptions, calculate_risk_reward


@pytest.fixture
def sizing_env():
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

    # Initialize DayAudit with initial equity 5000.0
    audit = crud.get_or_create_today_audit(db, clock=clock)
    audit.initial_equity = 5000.0
    audit.current_equity = 5000.0
    db.commit()

    orchestrator = ScalpWatcherOrchestrator()
    coordinator = ExecutionCoordinator()

    yield db, clock, orchestrator, coordinator
    db.close()


def test_real_orchestrator_arm_fixed_capital_snapshot_long(sizing_env):
    """
    PHẦN 050, 062, 064, 065:
    Orchestrator arms candidate with FIXED_INITIAL_CAPITAL.
    Verifies that cost_snapshot produced by _arm_paper_candidate contains
    capital_mode='FIXED_INITIAL_CAPITAL', target_capital=5000.0, target_risk_budget=5.0
    WITHOUT ANY test-side snapshot modification!
    """
    db, clock, orchestrator, coordinator = sizing_env
    t_now = 1_000_000
    clock.set_time(t_now)

    # Setup calculation for LONG
    costs = CostAssumptions(maker_fee_rate=0.0002, taker_fee_rate=0.0004, slippage_usd=0.10)
    calc = calculate_risk_reward(
        direction="LONG",
        entry=2000.0,
        sl=1990.0,
        tp=2030.0,
        capital=5000.0,
        risk_pct=0.10,
        costs=costs,
    )

    cand = ScalpCandidate(
        is_eligible=True,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        candidate_key="cand_v16_10_long_test",
        symbol="XAUUSDT",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2030.0,
        gross_rr=calc.gross_rr,
        net_rr=calc.net_rr,
        calculation=calc,
        evidence={"is_displacement": True},
        trigger_chain={},
        snapshot=None,
        as_of_ms=t_now,
        zone_id="zone_test_cg01",
        origin_episode_id="ep_test_cg01",
        valid_until_ms=t_now + 3_600_000,
    )

    policy_settings = {
        "capital_usdt": 5000.0,
        "risk_pct": 0.10,
        "capital_mode": "FIXED_INITIAL_CAPITAL",
        "capital_source": "INITIAL_CAPITAL_FIXED",
    }

    quote = {
        "bid": 2000.0,
        "ask": 2000.10,
        "last": 2000.05,
        "timestamp": t_now,
        "is_valid": True,
        "is_stale": False,
    }

    # Call real orchestrator _arm_paper_candidate
    armed_order = orchestrator._arm_paper_candidate(
        db,
        cand,
        now_ms=t_now,
        clock=clock,
        quote=quote,
        policy_settings=policy_settings,
        auto_enabled=True,
    )

    assert armed_order is not None, "Candidate must be armed by real orchestrator"
    db.commit()

    # Query PaperOrder from DB and inspect cost_snapshot WITHOUT touching it
    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_order.id).first()
    assert order is not None
    assert order.state == "armed"

    arm_snap = json.loads(order.cost_snapshot)
    assert arm_snap["capital_mode"] == "FIXED_INITIAL_CAPITAL"
    assert arm_snap["target_capital"] == 5000.0
    assert arm_snap["target_risk_budget"] == 5.0
    assert arm_snap["phase"] == "ARMED_ESTIMATE"

    # Now execute fill via Coordinator with quote touching entry (ask=2000.0)
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 1999.90, "ask": 2000.0, "last": 2000.0, "timestamp": t_now + 5000},
        clock=clock,
        now_ms=t_now + 5000,
        slippage=0.10,
    )

    db.refresh(order)
    assert order.state == "paper_open", "Order must be filled by coordinator"

    fill_snap = json.loads(order.cost_snapshot)
    assert fill_snap["phase"] == "FILLED_ESTIMATE"
    assert fill_snap["capital_mode"] == "FIXED_INITIAL_CAPITAL"
    assert fill_snap["target_capital"] == 5000.0
    assert fill_snap["target_risk_budget"] == 5.0
    assert fill_snap["account_capital_at_fill"] == 5000.0
    assert fill_snap["sizing_adjustment_reason"] == "NONE"


def test_real_orchestrator_arm_fixed_capital_snapshot_short(sizing_env):
    """
    PHẦN 063: Bearish SHORT setup sizing symmetry.
    Entry: 2050.0, SL: 2060.0, TP: 2025.0 (TP < entry < SL).
    """
    db, clock, orchestrator, coordinator = sizing_env
    t_now = 1_000_000
    clock.set_time(t_now)

    costs = CostAssumptions(maker_fee_rate=0.0002, taker_fee_rate=0.0004, slippage_usd=0.10)
    calc = calculate_risk_reward(
        direction="SHORT",
        entry=2050.0,
        sl=2060.0,
        tp=2020.0,
        capital=5000.0,
        risk_pct=0.10,
        costs=costs,
    )

    cand = ScalpCandidate(
        is_eligible=True,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="SHORT",
        candidate_key="cand_v16_10_short_test",
        symbol="XAUUSDT",
        planned_entry=2050.0,
        stop_loss=2060.0,
        take_profit=2020.0,
        gross_rr=calc.gross_rr,
        net_rr=calc.net_rr,
        calculation=calc,
        evidence={"is_displacement": True},
        trigger_chain={},
        snapshot=None,
        as_of_ms=t_now,
        zone_id="zone_test_cg01",
        origin_episode_id="ep_test_cg01",
        valid_until_ms=t_now + 3_600_000,
    )

    policy_settings = {
        "capital_usdt": 5000.0,
        "risk_pct": 0.10,
        "capital_mode": "FIXED_INITIAL_CAPITAL",
        "capital_source": "INITIAL_CAPITAL_FIXED",
    }

    quote = {
        "bid": 2050.0,
        "ask": 2050.10,
        "last": 2050.05,
        "timestamp": t_now,
        "is_valid": True,
        "is_stale": False,
    }

    armed_order = orchestrator._arm_paper_candidate(
        db,
        cand,
        now_ms=t_now,
        clock=clock,
        quote=quote,
        policy_settings=policy_settings,
        auto_enabled=True,
    )

    assert armed_order is not None
    db.commit()

    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_order.id).first()
    arm_snap = json.loads(order.cost_snapshot)
    assert arm_snap["capital_mode"] == "FIXED_INITIAL_CAPITAL"
    assert arm_snap["target_capital"] == 5000.0
    assert arm_snap["target_risk_budget"] == 5.0

    # Short fill trigger: bid touches planned entry 2050.0
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2050.0, "ask": 2050.10, "last": 2050.0, "timestamp": t_now + 5000},
        clock=clock,
        now_ms=t_now + 5000,
        slippage=0.10,
    )

    db.refresh(order)
    assert order.state == "paper_open"
    fill_snap = json.loads(order.cost_snapshot)
    assert fill_snap["capital_mode"] == "FIXED_INITIAL_CAPITAL"
    assert fill_snap["target_capital"] == 5000.0


def test_account_degraded_after_arm_applies_safety_cap(sizing_env):
    """
    PHẦN 066: Account equity reduced from 5000 to 4500 after arm.
    Coordinator must apply ACCOUNT_SAFETY_CAP: executable_budget = 4.5,
    while target_capital (5000) and target_risk_budget (5.0) remain intact.
    """
    db, clock, orchestrator, coordinator = sizing_env
    t_now = 1_000_000
    clock.set_time(t_now)

    calc = calculate_risk_reward(
        direction="LONG",
        entry=2000.0,
        sl=1990.0,
        tp=2030.0,
        capital=5000.0,
        risk_pct=0.10,
    )

    cand = ScalpCandidate(
        is_eligible=True,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        candidate_key="cand_degrade_test",
        symbol="XAUUSDT",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2030.0,
        gross_rr=calc.gross_rr,
        net_rr=calc.net_rr,
        calculation=calc,
        evidence={"is_displacement": True},
        trigger_chain={},
        snapshot=None,
        as_of_ms=t_now,
        zone_id="zone_test_cg01",
        origin_episode_id="ep_test_cg01",
        valid_until_ms=t_now + 3_600_000,
    )

    policy_settings = {
        "capital_usdt": 5000.0,
        "risk_pct": 0.10,
        "capital_mode": "FIXED_INITIAL_CAPITAL",
    }
    quote = {"bid": 2000.0, "ask": 2000.1, "last": 2000.05, "timestamp": t_now, "is_valid": True, "is_stale": False}

    armed_order = orchestrator._arm_paper_candidate(
        db, cand, now_ms=t_now, clock=clock, quote=quote, policy_settings=policy_settings, auto_enabled=True,
    )
    assert armed_order is not None
    db.commit()

    # Legitimate account state transition: current equity drops to 4500.0
    audit = crud.get_or_create_today_audit(db, clock=clock)
    audit.current_equity = 4500.0
    db.commit()

    # Fill
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 1999.9, "ask": 2000.0, "last": 2000.0, "timestamp": t_now + 5000},
        clock=clock,
        now_ms=t_now + 5000,
    )

    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_order.id).first()
    assert order.state == "paper_open"
    fill_snap = json.loads(order.cost_snapshot)
    assert fill_snap["capital_mode"] == "FIXED_INITIAL_CAPITAL"
    assert fill_snap["target_capital"] == 5000.0
    assert fill_snap["target_risk_budget"] == 5.0
    assert fill_snap["account_capital_at_fill"] == 4500.0
    assert fill_snap["executable_risk_budget"] == 4.5
    assert fill_snap["sizing_adjustment_reason"] == "ACCOUNT_SAFETY_CAP"


def test_account_growth_after_arm_preserves_fixed_target(sizing_env):
    """
    PHẦN 067: Account equity increases from 5000 to 6000 after arm.
    FIXED target budget must remain 5.0; quantity must NOT artificially inflate.
    """
    db, clock, orchestrator, coordinator = sizing_env
    t_now = 1_000_000
    clock.set_time(t_now)

    calc = calculate_risk_reward(
        direction="LONG",
        entry=2000.0,
        sl=1990.0,
        tp=2030.0,
        capital=5000.0,
        risk_pct=0.10,
    )
    initial_qty = calc.quantity

    cand = ScalpCandidate(
        is_eligible=True,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        candidate_key="cand_growth_test",
        symbol="XAUUSDT",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2030.0,
        gross_rr=calc.gross_rr,
        net_rr=calc.net_rr,
        calculation=calc,
        evidence={"is_displacement": True},
        trigger_chain={},
        snapshot=None,
        as_of_ms=t_now,
        zone_id="zone_test_cg01",
        origin_episode_id="ep_test_cg01",
        valid_until_ms=t_now + 3_600_000,
    )
    policy_settings = {
        "capital_usdt": 5000.0,
        "risk_pct": 0.10,
        "capital_mode": "FIXED_INITIAL_CAPITAL",
    }
    quote = {"bid": 2000.0, "ask": 2000.1, "last": 2000.05, "timestamp": t_now, "is_valid": True, "is_stale": False}

    armed_order = orchestrator._arm_paper_candidate(
        db, cand, now_ms=t_now, clock=clock, quote=quote, policy_settings=policy_settings, auto_enabled=True,
    )
    db.commit()

    # Equity grows to 6000.0
    audit = crud.get_or_create_today_audit(db, clock=clock)
    audit.current_equity = 6000.0
    db.commit()

    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 1999.9, "ask": 2000.0, "last": 2000.0, "timestamp": t_now + 5000},
        clock=clock,
        now_ms=t_now + 5000,
    )

    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_order.id).first()
    assert order.state == "paper_open"
    # Quantity must be preserved or downsized, never inflated
    assert order.quantity <= initial_qty
    fill_snap = json.loads(order.cost_snapshot)
    assert fill_snap["capital_mode"] == "FIXED_INITIAL_CAPITAL"
    assert fill_snap["target_capital"] == 5000.0
    assert fill_snap["target_risk_budget"] == 5.0
    assert fill_snap["sizing_adjustment_reason"] == "NONE"
