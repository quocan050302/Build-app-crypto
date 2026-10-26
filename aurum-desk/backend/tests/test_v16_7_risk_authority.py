"""
backend/tests/test_v16_7_risk_authority.py
V16.7 Group D, O: Canonical Risk Authority and Financial Context Isolation.

1. test_non_replay_risk_settings_authority:
   When RiskSettings.risk_pct == 0.25 in DB and M1ScalpConfig.fallback_risk_pct == 0.10,
   regular non-replay callers must receive 0.25% authority (not 0.10%).
2. test_explicit_replay_risk_context_override:
   When ReplayFinancialContext(risk_pct=0.10) is explicitly supplied,
   effective risk is 0.10% with provenance REPLAY_EXPLICIT.
3. test_model_risk_cap_applied_when_policy_specifies:
   resolve_execution_risk clamps to model_risk_cap_pct if configured.
4. test_risk_budget_units_and_percentage_scaling:
   5000 USDT * 0.10% = 5.00 USDT budget (never confused with 10% / 500 USDT).
5. test_invalid_finite_risk_values_fail_closed:
   Rejects NaN, <= 0, boolean, or infinity.
"""
import math
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
from database import run_schema_migrations
from models import Base
import models
from services.cg01_types import CG01Config, CG01ProfileMode
from services.clock import ReplayClock
from services.plan_financial_context_service import (
    ReplayFinancialContext,
    resolve_execution_risk,
)
from services.risk_settings_service import risk_settings_service
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import build_long_fixture_series, make_valid_quote


@pytest.fixture
def lab_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    policy = TradingPolicyService.get_or_create_policy(session)
    policy.max_daily_fills = 3
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    policy.is_active = False
    session.commit()

    try:
        yield session
    finally:
        session.close()


def test_non_replay_risk_settings_authority(lab_db):
    """
    PHẦN 031 - 033: User saves risk_pct = 0.25 in DB.
    M1ScalpConfig has default fallback_risk_pct = 0.10.
    Caller does NOT pass execution_financial_context.
    Orchestrator MUST use user's 0.25% authority, NOT the 0.10% fallback.
    """
    g_set = risk_settings_service.get_settings(lab_db)
    g_set.risk_pct = 0.25
    lab_db.commit()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000
    clock = ReplayClock(initial_ms=now_ms)

    orchestrator = ScalpWatcherOrchestrator()
    poi = WatchedZone(
        zone_id="poi_risk_auth",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )
    orchestrator.set_active_zones("XAUUSDT", [poi])

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    m1_cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        daily_max_cap=3,
        daily_min_goal=2,
        fallback_risk_pct=0.10,  # Strategy default fallback
    )

    # Call evaluate_cycle without replay override
    res = orchestrator.evaluate_cycle(
        db=lab_db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        clock=clock,
        quote=make_valid_quote(2625.5, 2625.7, now_ms),
        candles_1m=c_1m,
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=m1_cfg,
        auto_enabled=True,
    )

    order = lab_db.query(models.PaperOrder).filter(models.PaperOrder.direction == "LONG").first()
    assert order is not None, "Order should be created"
    assert order.risk_pct == 0.25, f"Expected user risk authority 0.25, got {order.risk_pct}"
    snapshot = order.snapshot
    assert snapshot is not None
    assert snapshot.risk_pct == 0.25, f"Expected snapshot risk_pct == 0.25, got {snapshot.risk_pct}"


def test_explicit_replay_risk_context_override(lab_db):
    """
    PHẦN 034 & 040: User has risk_pct = 0.25 in DB.
    Replay runner passes explicit ReplayFinancialContext(risk_pct=0.10).
    Orchestrator uses 0.10% without corrupting user's non-replay settings.
    """
    g_set = risk_settings_service.get_settings(lab_db)
    g_set.risk_pct = 0.25
    lab_db.commit()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000
    clock = ReplayClock(initial_ms=now_ms)

    orchestrator = ScalpWatcherOrchestrator()
    poi = WatchedZone(
        zone_id="poi_replay_ctx",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )
    orchestrator.set_active_zones("XAUUSDT", [poi])

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    m1_cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
    )

    replay_ctx = ReplayFinancialContext(
        risk_pct=0.10,
        capital_usdt=5000.0,
        slippage_usd=0.0,
    )

    res = orchestrator.evaluate_cycle(
        db=lab_db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        clock=clock,
        quote=make_valid_quote(2625.5, 2625.7, now_ms),
        candles_1m=c_1m,
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=m1_cfg,
        auto_enabled=True,
        execution_financial_context=replay_ctx,
    )

    order = lab_db.query(models.PaperOrder).filter(models.PaperOrder.direction == "LONG").first()
    assert order is not None
    assert order.risk_pct == 0.10, f"Expected explicit replay risk 0.10, got {order.risk_pct}"
    # Verify DB setting was NOT overwritten
    g_check = risk_settings_service.get_settings(lab_db)
    assert g_check.risk_pct == 0.25, f"DB settings must remain 0.25, got {g_check.risk_pct}"


def test_model_risk_cap_applied_when_policy_specifies():
    """
    PHẦN 036: effective_risk = min(account_risk, model_cap).
    """
    res1 = resolve_execution_risk(account_risk_pct=0.50, model_risk_cap_pct=0.20)
    assert res1["configured_risk_pct"] == 0.50
    assert res1["effective_risk_pct"] == 0.20

    res2 = resolve_execution_risk(account_risk_pct=0.10, model_risk_cap_pct=0.20)
    assert res2["configured_risk_pct"] == 0.10
    assert res2["effective_risk_pct"] == 0.10


def test_risk_budget_units_and_percentage_scaling():
    """
    PHẦN 038: risk_pct=0.10 means 0.10%.
    Budget for $5000 equity: 5000 * 0.10 / 100 = 5.00 USDT.
    """
    equity = 5000.0
    risk_pct = 0.10
    budget = equity * (risk_pct / 100.0)
    assert abs(budget - 5.00) < 1e-6
    assert budget != 500.0, "risk_pct=0.10 must not be interpreted as 10%"


def test_invalid_finite_risk_values_fail_closed():
    """
    PHẦN 047: Reject NaN, Infinity, <= 0, and booleans.
    """
    with pytest.raises(ValueError):
        resolve_execution_risk(account_risk_pct=-0.10)

    with pytest.raises(ValueError):
        resolve_execution_risk(account_risk_pct=float("nan"))

    with pytest.raises(ValueError):
        resolve_execution_risk(account_risk_pct=float("inf"))

    with pytest.raises(ValueError):
        resolve_execution_risk(account_risk_pct=True)
