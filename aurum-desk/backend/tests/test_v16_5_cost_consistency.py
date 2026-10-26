"""
backend/tests/test_v16_5_cost_consistency.py
V16.5 Gate B: Cost Consistency & Authoritative Slippage (Rules 051 - 080, 143 - 147):
- test_rule_052_resolve_slippage_usd: 0.0 is preserved; None returns default; negative/NaN/inf raises ValueError
- test_rule_057_058_market_entry_pricing_directional: LONG = ask + slip, SHORT = bid - slip, 0.0 = exact ask/bid
- test_rule_060_coordinator_zero_slippage_execution: cost snapshot slippage 0.0 fills at exact ask without 0.10 penalty
- test_rule_062_no_double_entry_slippage_in_calculator: entry_has_slippage=True does not charge entry slippage twice
- test_rule_064_zero_slippage_retains_exchange_fees: slippage 0.0 still charges maker/taker fees (net R:R < gross R:R)
- test_rule_066_config_change_rejects_pending_order: changed config version rejects pending order to protect snapshot
"""
import math
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
import models
from models import Base
from domain_calculator import CostAssumptions, calculate_risk_reward
from services.cg01_types import resolve_slippage_usd
from services.clock import ReplayClock
from services.execution_coordinator import ExecutionCoordinator
from services.risk_settings_service import risk_settings_service
from services.trading_policy_service import TradingPolicyService


@pytest.fixture
def cost_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    policy = TradingPolicyService.get_or_create_policy(session)
    policy.max_daily_fills = 3
    policy.daily_min_goal = 2
    policy.is_active = False
    session.commit()

    try:
        yield session, engine
    finally:
        session.close()


def test_rule_052_resolve_slippage_usd():
    """
    Rule 052: resolve_slippage_usd must strictly preserve 0.0.
    Must not coerce 0.0 or 0.10 -> 0.10.
    None returns default.
    Negative, NaN, inf, or unparseable raise ValueError.
    """
    # 0.0 is valid and must remain 0.0
    assert resolve_slippage_usd(0.0) == 0.0
    assert resolve_slippage_usd(0) == 0.0
    assert resolve_slippage_usd("0.0") == 0.0

    # None returns default
    assert resolve_slippage_usd(None, default=0.10) == 0.10
    assert resolve_slippage_usd(None, default=0.25) == 0.25

    # Positive values
    assert resolve_slippage_usd(0.20) == 0.20
    assert resolve_slippage_usd("0.15") == 0.15

    # Negative values must be rejected
    with pytest.raises(ValueError):
        resolve_slippage_usd(-0.10)

    with pytest.raises(ValueError):
        resolve_slippage_usd(-0.001)

    # NaN / Inf must be rejected
    with pytest.raises(ValueError):
        resolve_slippage_usd(float("nan"))

    with pytest.raises(ValueError):
        resolve_slippage_usd(float("inf"))

    with pytest.raises(ValueError):
        resolve_slippage_usd("invalid_string")


def test_rule_057_058_market_entry_pricing_directional():
    """
    Rules 057 & 058:
    LONG market = ask + adverse slippage (with 0.0 -> exactly ask)
    SHORT market = bid - adverse slippage (with 0.0 -> exactly bid)
    """
    ask = 2623.85
    bid = 2623.75

    # Non-zero slippage (0.20)
    slip_20 = resolve_slippage_usd(0.20)
    long_20 = round(ask + slip_20, 2)
    short_20 = round(bid - slip_20, 2)
    assert long_20 == 2624.05
    assert short_20 == 2623.55

    # Zero slippage (0.00)
    slip_0 = resolve_slippage_usd(0.0)
    long_0 = round(ask + slip_0, 2)
    short_0 = round(bid - slip_0, 2)
    assert long_0 == 2623.85
    assert short_0 == 2623.75


def test_rule_060_coordinator_zero_slippage_execution(cost_db):
    """
    Rule 060, 071 & 143:
    When an order's cost_snapshot defines slippage_usd=0.0,
    ExecutionCoordinator MUST fill at exact ask (for LONG) or bid (for SHORT),
    and must NOT use the 0.10 default.
    """
    db, _ = cost_db
    now_ms = 1792050000000
    coordinator = ExecutionCoordinator()

    # Create an armed order with cost_snapshot having slippage_usd = 0.0
    import json
    arm_snap = {
        "phase": "ARMED_ESTIMATE",
        "slippage_usd": 0.0,
        "maker_fee_rate": 0.0002,
        "taker_fee_rate": 0.0004,
        "multiplier": 1.0,
    }

    order = models.PaperOrder(
        id="ord_test_zero_slip",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="MARKET",
        planned_entry=2623.85,
        actual_entry=None,
        stop_loss=2615.0,
        take_profit=2655.0,
        quantity=0.5,
        initial_risk_usdt=5.0,
        risk_pct=0.25,
        leverage=5,
        margin_mode="ISOLATED",
        created_at=now_ms,
        armed_at=now_ms,
        expires_at=now_ms + 300_000,
        cost_snapshot=json.dumps(arm_snap),
    )
    db.add(order)
    db.commit()

    quote = {
        "bid": 2623.75,
        "ask": 2623.85,
        "last": 2623.80,
        "timestamp": now_ms + 1000,
    }
    clock = ReplayClock(initial_ms=now_ms + 1000)

    coordinator.evaluate_orders_sync(
        db,
        ticker_override=quote,
        clock=clock,
        now_ms=now_ms + 1000,
    )
    db.refresh(order)

    assert order.state == "paper_open"
    # Actual entry must be 2623.85 (ask + 0.0), NOT 2623.95 (ask + 0.10)
    assert order.actual_entry == 2623.85


def test_rule_062_no_double_entry_slippage_in_calculator():
    """
    Rule 062, 063 & 078:
    When entry_has_slippage=True, calculator does NOT add entry_slippage again.
    SL exit slippage is still charged according to contract.
    """
    costs = CostAssumptions(slippage_usd=0.10, taker_fee_rate=0.0004)

    # Case A: entry already includes slippage (e.g. at fill time)
    calc_filled = calculate_risk_reward(
        direction="LONG",
        entry=2623.95,  # Ask 2623.85 + 0.10 slippage
        sl=2615.00,
        tp=2640.00,
        capital=1000.0,
        risk_pct=0.25,
        costs=costs,
        entry_has_slippage=True,
    )
    assert calc_filled.entry_slippage_usdt == 0.0
    assert calc_filled.sl_exit_slippage_usdt > 0.0  # SL exit slippage is preserved

    # Case B: entry does NOT have slippage (e.g. limit order planned entry)
    calc_preview = calculate_risk_reward(
        direction="LONG",
        entry=2623.85,
        sl=2615.00,
        tp=2640.00,
        capital=1000.0,
        risk_pct=0.25,
        costs=costs,
        entry_has_slippage=False,
    )
    assert calc_preview.entry_slippage_usdt > 0.0


def test_rule_064_zero_slippage_retains_exchange_fees():
    """
    Rule 064: Zero slippage does NOT mean zero fees.
    Maker/taker fees must still be charged, making net R:R lower than gross R:R.
    """
    costs_zero_slip = CostAssumptions(slippage_usd=0.0, taker_fee_rate=0.0004, maker_fee_rate=0.0002)

    calc = calculate_risk_reward(
        direction="LONG",
        entry=2620.00,
        sl=2610.00,
        tp=2640.00,
        capital=1000.0,
        risk_pct=0.25,
        costs=costs_zero_slip,
        entry_has_slippage=True,
    )

    assert calc.is_valid
    assert calc.slippage_total_usdt == 0.0
    # Fees must be non-zero
    assert calc.fees_total_usdt > 0.0
    # Net R:R must be less than gross R:R due to fees
    assert calc.net_rr < calc.gross_rr


def test_rule_066_config_change_rejects_pending_order(cost_db):
    """
    Rule 066 & 147:
    If risk settings / config version changes while an order is armed,
    coordinator must execute_reject rather than silently repricing the order.
    """
    db, _ = cost_db
    now_ms = 1792050000000
    coordinator = ExecutionCoordinator()

    # Get current config version
    settings = risk_settings_service.get_settings(db)
    curr_v = settings.config_version

    order = models.PaperOrder(
        id="ord_test_cfg_change",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=2620.00,
        stop_loss=2610.00,
        take_profit=2640.00,
        quantity=0.2,
        initial_risk_usdt=2.5,
        created_at=now_ms,
        armed_at=now_ms,
        expires_at=now_ms + 300_000,
        config_version=curr_v,
    )
    db.add(order)
    db.commit()

    # Now increment global config version in the database
    v_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "risk_config_version").first()
    if not v_cfg:
        v_cfg = models.SystemConfig(key="risk_config_version", value=str(curr_v + 1), updated_at=now_ms)
        db.add(v_cfg)
    else:
        v_cfg.value = str(curr_v + 1)
        v_cfg.updated_at = now_ms
    db.commit()

    quote = {
        "bid": 2619.90,
        "ask": 2620.00,  # Touches limit!
        "last": 2619.95,
        "timestamp": now_ms + 1000,
    }
    clock = ReplayClock(initial_ms=now_ms + 1000)

    coordinator.evaluate_orders_sync(
        db,
        ticker_override=quote,
        clock=clock,
        now_ms=now_ms + 1000,
    )
    db.refresh(order)

    # Must be rejected due to config version change
    assert order.state == "rejected"
    assert "CONFIG_VERSION_CHANGED" in (order.invalidation_reason or "")
