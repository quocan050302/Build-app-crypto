"""
backend/tests/test_v16_39_15_rr_policy_unification.py
V16.39.15 — Comprehensive Verification Suite for Unified Effective Net RR Policy

Verifies:
A01: Canonical resolution priority (SystemConfig > TradingPolicy > Default).
A02: Legacy TradingPolicy fallback when SystemConfig is absent.
A03: Default 2.0 fallback when both sources are absent.
A04: Corrupt persisted value in SystemConfig or TradingPolicy raises typed RrPolicyConfigError.
A05: Input validation blocks values < 1.5, bools, None, NaN, and Infinity.
A06: Atomic staging and rollback preserves both SystemConfig and TradingPolicy.
A07: PlanFinancialContext propagates resolved min_net_rr, source, and fingerprint.
A08: EligibilityService resolves effective min_rr and permits setups in [1.5, 2.0).
A09: SetupDisplayPlanService honors immutable snapshot min_net_rr_at_arm for frozen orders.
A10: ExecutionCoordinator fills order using snapshot threshold (1.5) even if policy changed to 2.0.
A11: ExecutionCoordinator rejects order if fill net RR drops below snapshot threshold.
A12: Auto-ARM and manual ARM persist min_net_rr_at_arm in OrderPlanSnapshot.
A13: Boundary tests at 1.49 (rejected), 1.50 (accepted), and 1.51 (accepted).
A14: Policy update endpoint rolls back both tables on auxiliary field validation error.
"""
import math
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient

import models
from models import SystemConfig, TradingPolicy, PaperOrder, SetupQualitySnapshot
from services.order_plan_snapshot_service import build_arm_plan_snapshot, OrderPlanSnapshot
from services.rr_policy_service import (
    resolve_effective_rr_policy,
    validate_new_min_net_rr,
    parse_persisted_min_net_rr,
    stage_min_net_rr_update,
    compute_rr_policy_fingerprint,
    RrPolicyConfigError,
    RrPolicySource,
    DEFAULT_MIN_NET_RR,
    MIN_SUPPORTED_NET_RR,
)
from services.plan_financial_context_service import load_plan_financial_context
from services.eligibility_service import evaluate_setup_eligibility
from services.setup_display_plan_service import build_setup_display_plan, build_order_row_display_plan
from services.execution_coordinator import ExecutionCoordinator
from services.trading_policy_service import TradingPolicyService, IClock


class MockClock(IClock):
    def __init__(self, dt: datetime):
        self._dt = dt

    def now(self) -> datetime:
        return self._dt

    def now_datetime(self) -> datetime:
        return self._dt

    def now_ms(self) -> int:
        return int(self._dt.timestamp() * 1000)

    def monotonic_s(self) -> float:
        return self._dt.timestamp()


# ==============================================================================
# A01 - A03: RESOLUTION HIERARCHY
# ==============================================================================

def test_a01_canonical_resolution_priority(isolated_db):
    """A01: SystemConfig('min_net_rr') overrides TradingPolicy('default')."""
    # Seed SystemConfig with 1.5
    sys_entry = SystemConfig(key="min_net_rr", value="1.5", updated_at=1000)
    # Seed TradingPolicy with 2.0
    pol_entry = TradingPolicy(id="default", min_net_rr=2.0, updated_at=900)
    isolated_db.add(sys_entry)
    isolated_db.add(pol_entry)
    isolated_db.commit()

    ctx = resolve_effective_rr_policy(isolated_db)
    assert ctx.min_net_rr == 1.5
    assert ctx.source == RrPolicySource.SYSTEM_CONFIG.value
    assert ctx.fingerprint == compute_rr_policy_fingerprint(1.5)


def test_a02_legacy_trading_policy_fallback(isolated_db):
    """A02: Absence of SystemConfig falls back to TradingPolicy."""
    pol_entry = TradingPolicy(id="default", min_net_rr=2.5, updated_at=950)
    isolated_db.add(pol_entry)
    isolated_db.commit()

    ctx = resolve_effective_rr_policy(isolated_db)
    assert ctx.min_net_rr == 2.5
    assert ctx.source == RrPolicySource.LEGACY_TRADING_POLICY.value
    assert ctx.fingerprint == compute_rr_policy_fingerprint(2.5)


def test_a03_default_fallback(isolated_db):
    """A03: Absence of both tables/rows falls back to DEFAULT 2.0."""
    ctx = resolve_effective_rr_policy(isolated_db)
    assert ctx.min_net_rr == DEFAULT_MIN_NET_RR
    assert ctx.source == RrPolicySource.DEFAULT.value
    assert ctx.fingerprint == compute_rr_policy_fingerprint(2.0)


# ==============================================================================
# A04: CORRUPT PERSISTED VALUES FAIL-CLOSED
# ==============================================================================

@pytest.mark.parametrize("corrupt_val", ["abc", "-1.0", "0.5", "nan", "inf", ""])
def test_a04_corrupt_persisted_system_config_raises(isolated_db, corrupt_val):
    """A04a: Corrupt SystemConfig min_net_rr must raise RrPolicyConfigError (no silent 2.0 fallback)."""
    sys_entry = SystemConfig(key="min_net_rr", value=corrupt_val, updated_at=1000)
    isolated_db.add(sys_entry)
    isolated_db.commit()

    with pytest.raises(RrPolicyConfigError):
        resolve_effective_rr_policy(isolated_db)


def test_a04_corrupt_persisted_trading_policy_raises(isolated_db):
    """A04b: Corrupt TradingPolicy min_net_rr (< 1.5 or non-finite) raises RrPolicyConfigError."""
    pol_entry = TradingPolicy(id="default", min_net_rr=0.8, updated_at=1000)
    isolated_db.add(pol_entry)
    isolated_db.commit()

    with pytest.raises(RrPolicyConfigError):
        resolve_effective_rr_policy(isolated_db)


# ==============================================================================
# A05: INPUT VALIDATION
# ==============================================================================

@pytest.mark.parametrize("bad_val", [
    1.49, 1.4999, 0.0, -1.5, True, False, None,
    float("nan"), float("inf"), float("-inf"), "not_a_number"
])
def test_a05_validate_new_min_net_rr_rejects(bad_val):
    """A05a: validate_new_min_net_rr rejects any value < 1.5, boolean, None, or non-finite."""
    with pytest.raises(RrPolicyConfigError):
        validate_new_min_net_rr(bad_val)


@pytest.mark.parametrize("valid_val, expected", [
    (1.5, 1.5),
    ("1.5", 1.5),
    (1.75, 1.75),
    (2.0, 2.0),
    (3.5, 3.5),
])
def test_a05_validate_new_min_net_rr_accepts(valid_val, expected):
    """A05b: validate_new_min_net_rr accepts valid floats >= 1.5."""
    assert validate_new_min_net_rr(valid_val) == expected


# ==============================================================================
# A06: ATOMIC STAGING AND ROLLBACK
# ==============================================================================

def test_a06_atomic_staging_and_rollback(isolated_db):
    """A06: stage_min_net_rr_update writes to both tables and obeys session transactions."""
    now_ms = 1700000000000
    # Stage 1.5
    ctx = stage_min_net_rr_update(isolated_db, 1.5, now_ms=now_ms)
    assert ctx.min_net_rr == 1.5

    # Simulate error before commit -> rollback
    isolated_db.rollback()

    # Verify neither table has 1.5
    sys_row = isolated_db.query(SystemConfig).filter(SystemConfig.key == "min_net_rr").first()
    assert sys_row is None

    # Stage 1.5 and commit
    stage_min_net_rr_update(isolated_db, 1.5, now_ms=now_ms)
    isolated_db.commit()

    sys_row = isolated_db.query(SystemConfig).filter(SystemConfig.key == "min_net_rr").first()
    assert sys_row is not None and sys_row.value == "1.5"

    pol_row = isolated_db.query(TradingPolicy).filter(TradingPolicy.id == "default").first()
    assert pol_row is not None and pol_row.min_net_rr == 1.5


# ==============================================================================
# A07: PLAN FINANCIAL CONTEXT
# ==============================================================================

def test_a07_plan_financial_context_propagation(isolated_db):
    """A07: load_plan_financial_context includes resolved min_net_rr and provenance."""
    stage_min_net_rr_update(isolated_db, 1.5)
    isolated_db.commit()

    ctx = load_plan_financial_context(isolated_db)
    assert ctx.min_net_rr == 1.5
    assert ctx.rr_policy_source == RrPolicySource.SYSTEM_CONFIG.value
    assert ctx.rr_policy_fingerprint == compute_rr_policy_fingerprint(1.5)


# ==============================================================================
# A08: ELIGIBILITY SERVICE RESOLVES POLICY
# ==============================================================================

def test_a08_eligibility_service_resolves_effective_rr(isolated_db):
    """
    A08: Setup with net RR ~ 1.84:
    - Passes when min_net_rr is 1.5
    - Fails when min_net_rr is 2.0
    """
    # Seed DayAudit for account balance
    isolated_db.add(models.DayAudit(date_str="2026-10-21", initial_equity=1000.0, current_equity=1000.0))

    setup = models.WatchSetup(
        id="setup_test_rr_1",
        direction="LONG",
        provisional_entry=2700.0,
        provisional_sl=2690.0,
        provisional_tp=2725.0,
        confirmed_entry=2700.0,
        confirmed_sl=2690.0,
        confirmed_tp=2725.0,
        invalidation_price=2690.0,
        margin_mode="ISOLATED",
        state="READY",
        strategy_family="CG01",
        created_at=1700000000000,
        updated_at=1700000000000,
    )
    isolated_db.add(setup)

    # Scenario 1: Policy 1.5
    stage_min_net_rr_update(isolated_db, 1.5)
    isolated_db.commit()

    # Geometry: entry=2700, sl=2690 (dist 10), tp=2725 (dist 25) -> net RR ~ 1.84
    res_1_5 = evaluate_setup_eligibility(
        db=isolated_db,
        setup=setup,
    )
    assert res_1_5["min_net_rr"] == 1.5
    assert res_1_5["effective_financial_snapshot"]["net_rr"] >= 1.5
    assert res_1_5["effective_financial_snapshot"]["net_rr"] < 2.0
    assert "INSUFFICIENT_RR" not in res_1_5["reason_codes"]

    # Scenario 2: Policy 2.0
    stage_min_net_rr_update(isolated_db, 2.0)
    isolated_db.commit()

    res_2_0 = evaluate_setup_eligibility(
        db=isolated_db,
        setup=setup,
    )
    assert res_2_0["min_net_rr"] == 2.0
    assert "INSUFFICIENT_RR" in res_2_0["reason_codes"]


# ==============================================================================
# A09: FROZEN ORDER DISPLAY PLAN GOVERNED BY SNAPSHOT
# ==============================================================================

def test_a09_frozen_order_display_plan_governed_by_snapshot(isolated_db):
    """
    A09: Frozen order with snapshot min_net_rr_at_arm=1.5 and net_rr=1.7
    evaluates meets_min_rr=True even if current policy in DB is 2.0.
    """
    cost_snap = {
        "min_net_rr_at_arm": 1.5,
        "rr_policy_source": "ORDER_SNAPSHOT",
        "rr_policy_fingerprint": compute_rr_policy_fingerprint(1.5),
        "net_rr": 1.7,
        "quantity": 0.25,
        "gross_loss_usdt": 2.5,
        "gross_reward_usdt": 4.5,
        "net_reward_usdt": 4.25,
        "multiplier": 1.0,
        "risk_pct": 0.25,
    }

    order = PaperOrder(
        id="ord_frozen_test_1",
        state="paper_open",
        direction="LONG",
        instrument="XAUUSDT",
        planned_entry=2700.0,
        actual_entry=2700.0,
        stop_loss=2690.0,
        take_profit=2725.0,
        quantity=0.25,
        gross_rr=1.8,
        estimated_net_rr=1.7,
        initial_risk_usdt=2.5,
        cost_snapshot=json.dumps(cost_snap),
        created_at=1700000000000,
    )
    isolated_db.add(order)

    # Current DB policy is 2.0
    stage_min_net_rr_update(isolated_db, 2.0)
    isolated_db.commit()

    plan_dto = build_order_row_display_plan(
        order=order,
        frozen_snapshot=cost_snap,
        financial_context=load_plan_financial_context(isolated_db),
    )

    fin = plan_dto.get("financial")
    assert fin is not None
    assert fin["min_net_rr"] == 1.5
    assert fin["meets_min_rr"] is True
    assert fin["net_rr"] == 1.7


# ==============================================================================
# A10 & A11: EXECUTION COORDINATOR SNAPSHOT IMMUTABILITY
# ==============================================================================

def test_a10_coordinator_fill_honors_snapshot_over_current_policy(isolated_db):
    """
    A10: Order armed with snapshot min_net_rr_at_arm=1.5.
    Current policy is updated to 2.0.
    Coordinator evaluate_orders_sync fills the order at net RR ~ 1.84 without rejection.
    """
    now_ms = 1700000000000
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 21, 10, 0, tzinfo=ny_tz)
    clock = MockClock(dt)

    order = PaperOrder(
        id="ord_coord_arm_1_5",
        state="armed",
        direction="LONG",
        instrument="XAUUSDT",
        planned_entry=2700.0,
        stop_loss=2690.0,
        take_profit=2725.0,
        quantity=0.20,
        order_type="LIMIT",
        initial_risk_usdt=2.5,
        cost_snapshot=json.dumps({
            "min_net_rr_at_arm": 1.5,
            "slippage_usd": 0.10,
        }),
        created_at=now_ms,
        armed_at=now_ms,
    )
    isolated_db.add(order)

    # Current policy in DB changed to 2.0
    stage_min_net_rr_update(isolated_db, 2.0)
    isolated_db.commit()

    coord = ExecutionCoordinator()
    # Ticker hits entry
    ticker = {"bid": 2699.9, "ask": 2700.0, "last": 2700.0, "server_time": now_ms, "observed_at": now_ms}
    coord.evaluate_orders_sync(isolated_db, ticker_override=ticker, clock=clock, now_ms=now_ms)

    isolated_db.refresh(order)
    assert order.state == "paper_open", f"Order must fill successfully, got {order.state}"
    assert order.actual_entry == 2700.0


def test_a11_coordinator_fill_rejects_on_degradation_below_snapshot(isolated_db):
    """
    A11: Order armed with snapshot min_net_rr_at_arm=1.5.
    Price slips severely or geometry at fill results in net RR < 1.5.
    Coordinator evaluate_orders_sync rejects the order safely.
    """
    now_ms = 1700000000000
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 21, 10, 0, tzinfo=ny_tz)
    clock = MockClock(dt)

    # Entry geometry with very close TP: entry=2700, sl=2690 (dist 10), tp=2712 (dist 12) -> net RR < 1.5
    order = PaperOrder(
        id="ord_coord_slip_degrade",
        state="armed",
        direction="LONG",
        instrument="XAUUSDT",
        planned_entry=2700.0,
        stop_loss=2690.0,
        take_profit=2712.0,
        quantity=0.20,
        order_type="LIMIT",
        initial_risk_usdt=2.5,
        cost_snapshot=json.dumps({
            "min_net_rr_at_arm": 1.5,
            "slippage_usd": 0.10,
        }),
        created_at=now_ms,
        armed_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    coord = ExecutionCoordinator()
    ticker = {"bid": 2699.9, "ask": 2700.0, "last": 2700.0, "server_time": now_ms, "observed_at": now_ms}
    coord.evaluate_orders_sync(isolated_db, ticker_override=ticker, clock=clock, now_ms=now_ms)

    isolated_db.refresh(order)
    # Order must be rejected due to net RR degradation below snapshot min_net_rr
    assert order.state in ("cancelled", "rejected"), f"Order should be rejected, got {order.state}"
    assert "NET_RR_TOO_LOW" in (order.invalidation_reason or "")


# ==============================================================================
# A12: ARM SNAPSHOT PERSISTENCE
# ==============================================================================

def test_a12_build_arm_plan_snapshot_persists_threshold(isolated_db):
    """A12: build_arm_plan_snapshot includes min_net_rr_at_arm and provenance."""
    from domain_calculator import calculate_risk_reward

    calc_res = calculate_risk_reward(
        direction="LONG",
        entry=2700.0,
        sl=2690.0,
        tp=2725.0,
        capital=1000.0,
        risk_pct=0.25,
        min_net_rr=1.5,
    )

    snap = build_arm_plan_snapshot(
        calc_res,
        planned_entry=2700.0,
        stop_loss=2690.0,
        take_profit=2725.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=5,
        now_ms=1700000000000,
        min_net_rr=1.5,
    )

    assert snap["min_net_rr_at_arm"] == 1.5
    assert snap["planned_entry"] == 2700.0
    assert snap["stop_loss"] == 2690.0
    assert snap["take_profit"] == 2725.0


# ==============================================================================
# A13: BOUNDARY TESTING
# ==============================================================================

def test_a13_boundary_testing():
    """A13: Boundary tests at 1.49 (rejected), 1.50 (accepted), 1.51 (accepted)."""
    with pytest.raises(RrPolicyConfigError) as exc_1_49:
        validate_new_min_net_rr(1.49)
    assert exc_1_49.value.code == "RR_BELOW_MINIMUM_ALLOWED"

    assert validate_new_min_net_rr(1.50) == 1.5
    assert validate_new_min_net_rr(1.51) == 1.51


# ==============================================================================
# A14: POLICY UPDATE ENDPOINT ATOMIC ROLLBACK
# ==============================================================================

def test_a14_policy_update_endpoint_atomic_rollback_on_invalid_field(client, isolated_db):
    """
    A14: If an update request contains valid min_net_rr: 1.5 but an invalid auxiliary field
    (e.g. invalid daily cap or non-existent profile), transaction rolls back and neither
    SystemConfig nor TradingPolicy is modified.
    """
    # Baseline: both at default
    ctx_before = resolve_effective_rr_policy(isolated_db)
    assert ctx_before.min_net_rr == 2.0

    # Payload with min_net_rr: 1.5 and invalid timing_profile
    res = client.post(
        "/api/scalp/config",
        json={
            "min_net_rr": 1.5,
            "timing_profile": "INVALID_TIMING_PROFILE_ABC",
        }
    )
    assert res.status_code == 400

    # Verify rollback: min_net_rr in DB is NOT changed
    sys_row = isolated_db.query(SystemConfig).filter(SystemConfig.key == "min_net_rr").first()
    assert sys_row is None

    pol_row = isolated_db.query(TradingPolicy).filter(TradingPolicy.id == "default").first()
    if pol_row is not None:
        assert pol_row.min_net_rr != 1.5
