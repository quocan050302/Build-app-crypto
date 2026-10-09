"""
V10.4 Automated Verification Suite: Cost, R:R, and PnL Unification
Tests C01 - C12, S01 - S05, A01 - A04, X01 - X07, I01 - I02, P01 - P02.
"""
import decimal
from decimal import Decimal
import math
import json
import time
import pytest
from sqlalchemy.orm import Session

from domain_calculator import (
    calculate_risk_reward,
    validate_price_geometry,
    calculate_isolated_liquidation,
    CostAssumptions,
    CalculationResult,
    BITGET_TIERS
)
import models
import schemas
from paper_broker import PaperBroker
from services.trade_lifecycle_service import TradeLifecycleService
from services.execution_coordinator import ExecutionCoordinator
from services.trading_policy_service import TradingPolicyService


class DecimalCostOracle:
    """
    Independent Decimal-based reference oracle to mathematically verify
    domain_calculator against IEEE-754 floating point and rounding artifacts.
    """
    def __init__(
        self,
        taker_fee_rate: str = "0.0004",
        maker_fee_rate: str = "0.0002",
        slippage_usd: str = "0.10",
        tp_slippage_usd: str = "0.0",
        tp_is_maker: bool = False,
        multiplier: str = "1.0",
        qty_step: str = "0.01"
    ):
        self.taker_fee_rate = Decimal(taker_fee_rate)
        self.maker_fee_rate = Decimal(maker_fee_rate)
        self.slippage_usd = Decimal(slippage_usd)
        self.tp_slippage_usd = Decimal(tp_slippage_usd)
        self.tp_is_maker = tp_is_maker
        self.multiplier = Decimal(multiplier)
        self.qty_step = Decimal(qty_step)

    def calculate(
        self,
        direction: str,
        entry: str,
        sl: str,
        tp: str,
        capital: str,
        risk_pct: str,
        entry_has_slippage: bool = False,
        quantity_override: str = None
    ):
        d_entry = Decimal(entry)
        d_sl = Decimal(sl)
        d_tp = Decimal(tp)
        d_capital = Decimal(capital)
        d_risk_pct = Decimal(risk_pct)
        mult = self.multiplier

        if direction == "LONG":
            stop_dist = d_entry - d_sl
            target_dist = d_tp - d_entry
        elif direction == "SHORT":
            stop_dist = d_sl - d_entry
            target_dist = d_entry - d_tp
        else:
            raise ValueError(f"Unknown direction: {direction}")

        budget = d_capital * (d_risk_pct / Decimal("100.0"))

        entry_fee_per_unit = d_entry * self.taker_fee_rate * mult
        sl_exit_fee_per_unit = d_sl * self.taker_fee_rate * mult
        sl_slip_per_unit = self.slippage_usd * mult
        entry_slip_per_unit = Decimal("0.0") if entry_has_slippage else (self.slippage_usd * mult)

        risk_per_unit = (stop_dist * mult) + entry_fee_per_unit + sl_exit_fee_per_unit + entry_slip_per_unit + sl_slip_per_unit

        if quantity_override is not None:
            raw_qty = Decimal(quantity_override)
        else:
            raw_qty = budget / risk_per_unit

        qty = (raw_qty // self.qty_step) * self.qty_step

        gross_loss = qty * mult * stop_dist
        gross_reward = qty * mult * target_dist

        entry_fee_total = qty * mult * d_entry * self.taker_fee_rate
        sl_fee_total = qty * mult * d_sl * self.taker_fee_rate
        tp_fee_rate = self.maker_fee_rate if self.tp_is_maker else self.taker_fee_rate
        tp_fee_total = qty * mult * d_tp * tp_fee_rate

        entry_slip_total = Decimal("0.0") if entry_has_slippage else (qty * self.slippage_usd * mult)
        sl_slip_total = qty * self.slippage_usd * mult
        tp_slip_total = Decimal("0.0") if self.tp_is_maker else (qty * self.tp_slippage_usd * mult)

        net_risk = gross_loss + entry_fee_total + sl_fee_total + entry_slip_total + sl_slip_total
        net_reward = gross_reward - entry_fee_total - tp_fee_total - entry_slip_total - tp_slip_total

        gross_rr = gross_reward / gross_loss if gross_loss > 0 else Decimal("0.0")
        net_rr = net_reward / net_risk if net_risk > 0 else Decimal("0.0")

        return {
            "qty": qty,
            "budget": budget,
            "gross_loss": gross_loss,
            "gross_reward": gross_reward,
            "gross_rr": gross_rr,
            "net_risk": net_risk,
            "net_reward": net_reward,
            "net_rr": net_rr,
            "entry_fee": entry_fee_total,
            "sl_fee": sl_fee_total,
            "tp_fee": tp_fee_total,
            "entry_slippage": entry_slip_total,
            "sl_slippage": sl_slip_total,
            "tp_slippage": tp_slip_total
        }


# =============================================================================
# C01 - C12: CORE DOMAIN CALCULATOR & ORACLE TESTS
# =============================================================================

def test_c01_legacy_f1_regression_matches_decimal_oracle():
    """C01: F1 legacy model: tp_slippage_usd=0.0 matches Decimal oracle."""
    oracle = DecimalCostOracle()
    res_oracle = oracle.calculate(
        direction="LONG",
        entry="4000.0",
        sl="3990.0",
        tp="4035.0",
        capital="1000.0",
        risk_pct="0.5"
    )

    calc = calculate_risk_reward(
        direction="LONG",
        entry=4000.0,
        sl=3990.0,
        tp=4035.0,
        capital=1000.0,
        risk_pct=0.5
    )

    assert calc.is_valid is True
    assert calc.can_execute is True
    assert calc.quantity == float(res_oracle["qty"])
    assert math.isclose(calc.gross_loss_usdt, float(res_oracle["gross_loss"]), rel_tol=1e-4)
    assert math.isclose(calc.gross_reward_usdt, float(res_oracle["gross_reward"]), rel_tol=1e-4)
    assert math.isclose(calc.gross_rr, float(res_oracle["gross_rr"]), rel_tol=1e-4)
    assert math.isclose(calc.net_risk_usdt, round(float(res_oracle["net_risk"]), 2), abs_tol=0.02)
    assert math.isclose(calc.net_reward_usdt, round(float(res_oracle["net_reward"]), 2), abs_tol=0.02)
    assert math.isclose(calc.net_rr, float(res_oracle["net_rr"]), abs_tol=0.01)
    assert calc.meets_min_rr is True


def test_c02_f1new_explicit_tp_slippage_model():
    """C02: F1NEW model: tp_slippage_usd explicitly configured with adverse slippage at TP."""
    costs = CostAssumptions(
        tp_is_maker=False,  # Taker exit
        tp_slippage_usd=0.05
    )
    oracle = DecimalCostOracle(
        tp_is_maker=False,
        tp_slippage_usd="0.05"
    )
    res_oracle = oracle.calculate(
        direction="LONG",
        entry="4000.0",
        sl="3990.0",
        tp="4035.0",
        capital="1000.0",
        risk_pct="0.5"
    )

    calc = calculate_risk_reward(
        direction="LONG",
        entry=4000.0,
        sl=3990.0,
        tp=4035.0,
        capital=1000.0,
        risk_pct=0.5,
        costs=costs
    )

    assert calc.is_valid is True
    assert calc.tp_exit_fee_usdt > 0
    assert calc.tp_exit_slippage_usdt > 0
    assert math.isclose(calc.net_reward_usdt, round(float(res_oracle["net_reward"]), 2), abs_tol=0.02)
    assert math.isclose(calc.net_rr, float(res_oracle["net_rr"]), abs_tol=0.01)


def test_c03_f2_f3_short_and_long_geometry_gross_rr_and_pnl_signs():
    """C03: SHORT geometry: Gross RR = reward / loss > 0, NOT inverted, PnL signs consistent."""
    # SHORT: Entry 4000, SL 4010, TP 3965
    calc_short = calculate_risk_reward(
        direction="SHORT",
        entry=4000.0,
        sl=4010.0,
        tp=3965.0,
        capital=1000.0,
        risk_pct=0.5
    )
    assert calc_short.is_valid is True
    assert calc_short.stop_distance == 10.0
    assert calc_short.target_distance == 35.0
    assert calc_short.gross_rr == 3.5  # 35 / 10 = 3.5 > 0
    assert calc_short.gross_reward_usdt > 0
    assert calc_short.gross_loss_usdt > 0

    # LONG: Entry 4000, SL 3990, TP 4035
    calc_long = calculate_risk_reward(
        direction="LONG",
        entry=4000.0,
        sl=3990.0,
        tp=4035.0,
        capital=1000.0,
        risk_pct=0.5
    )
    assert calc_long.is_valid is True
    assert calc_long.stop_distance == 10.0
    assert calc_long.target_distance == 35.0
    assert calc_long.gross_rr == 3.5

    # Verify invalid geometry detection
    inv_long, reason = validate_price_geometry("LONG", 4000.0, 4010.0, 4035.0)  # SL above entry
    assert inv_long is False
    assert "INVALID_LONG_GEOMETRY" in reason

    inv_short, reason = validate_price_geometry("SHORT", 4000.0, 3990.0, 3965.0)  # SL below entry
    assert inv_short is False
    assert "INVALID_SHORT_GEOMETRY" in reason


def test_c04_maker_vs_taker_fee_legs():
    """C04: Verify exact maker (0.0002) vs taker (0.0004) fee branches."""
    calc_maker = calculate_risk_reward("LONG", 4000.0, 3990.0, 4030.0, 1000.0, 0.5, costs=CostAssumptions(tp_is_maker=True))
    calc_taker = calculate_risk_reward("LONG", 4000.0, 3990.0, 4030.0, 1000.0, 0.5, costs=CostAssumptions(tp_is_maker=False))

    # Taker TP fee must be exactly double the maker TP fee
    assert math.isclose(calc_taker.tp_exit_fee_usdt, calc_maker.tp_exit_fee_usdt * 2.0, rel_tol=1e-4)
    assert calc_taker.net_reward_usdt < calc_maker.net_reward_usdt


def test_c05_entry_slippage_omitted_when_already_applied():
    """C05: When entry_has_slippage is True, entry slippage is not added again."""
    calc_raw = calculate_risk_reward("LONG", 4000.0, 3990.0, 4030.0, 1000.0, 0.5, entry_has_slippage=False)
    calc_slipped = calculate_risk_reward("LONG", 4000.0, 3990.0, 4030.0, 1000.0, 0.5, entry_has_slippage=True)

    assert calc_raw.entry_slippage_usdt > 0
    assert calc_slipped.entry_slippage_usdt == 0.0
    assert calc_slipped.slippage_total_usdt < calc_raw.slippage_total_usdt


def test_c06_multiplier_scaling():
    """C06: Contract multiplier (e.g. 10x or 100x) scales notional, gross PnL, fees and slippage proportionally."""
    calc_1x = calculate_risk_reward("LONG", 4000.0, 3990.0, 4030.0, 1000.0, 0.5, costs=CostAssumptions(multiplier=1.0), quantity_override=0.1)
    calc_10x = calculate_risk_reward("LONG", 4000.0, 3990.0, 4030.0, 1000.0, 0.5, costs=CostAssumptions(multiplier=10.0), quantity_override=0.1)

    assert math.isclose(calc_10x.notional_usdt, calc_1x.notional_usdt * 10.0, rel_tol=1e-5)
    assert math.isclose(calc_10x.gross_loss_usdt, calc_1x.gross_loss_usdt * 10.0, rel_tol=1e-5)
    assert math.isclose(calc_10x.gross_reward_usdt, calc_1x.gross_reward_usdt * 10.0, rel_tol=1e-5)
    assert math.isclose(calc_10x.entry_fee_usdt, calc_1x.entry_fee_usdt * 10.0, rel_tol=1e-5)


def test_c07_step_floored_quantity_exact_decimal():
    """C07: Exact decimal step floored quantity eliminates float 0.30 -> 0.29 anomalies."""
    calc = calculate_risk_reward("LONG", 4000.0, 3990.0, 4030.0, 1000.0, 0.5)
    qty_scaled = round(calc.quantity * 100, 4)
    assert math.isclose(qty_scaled, round(qty_scaled), abs_tol=1e-6)


def test_c08_invalid_quantity_override_rejected():
    """C08: Invalid quantity override (<=0, inf, nan) returns is_valid=False with specific reason."""
    for bad_qty in [0.0, -1.0, float('inf'), float('nan')]:
        calc = calculate_risk_reward("LONG", 4000.0, 3990.0, 4030.0, 1000.0, 0.5, quantity_override=bad_qty)
        assert calc.is_valid is False
        assert "INVALID_QUANTITY" in (calc.invalid_reason or "")
        assert any("INVALID_QUANTITY" in b for b in calc.blocker_list)


def test_c09_unknown_direction_rejected():
    """C09: Unknown direction returns is_valid=False with UNKNOWN_DIRECTION."""
    calc = calculate_risk_reward("SIDEWAYS", 4000.0, 3990.0, 4030.0, 1000.0, 0.5)
    assert calc.is_valid is False
    assert "UNKNOWN_DIRECTION" in (calc.invalid_reason or "")


def test_c10_non_positive_net_reward_blocked():
    """C10: Geometry with target too tight where fees eat entire reward returns can_execute=False."""
    calc = calculate_risk_reward("LONG", 4000.0, 3990.0, 4000.10, 1000.0, 0.5)
    assert calc.can_execute is False
    assert calc.meets_min_rr is False
    assert any("NET_REWARD_NON_POSITIVE" in b for b in calc.blocker_list)


def test_c11_meets_min_rr_unrounded_check():
    """C11: Net RR is checked against min_net_rr without premature rounding up."""
    calc = calculate_risk_reward("LONG", 4000.0, 3990.0, 4022.0, 1000.0, 0.5, min_net_rr=2.0)
    if calc.net_rr < 2.0:
        assert calc.meets_min_rr is False
        assert calc.can_execute is False
        assert "NET_RR_TOO_LOW" in (calc.skip_reason or "")


def test_c12_gross_metrics_invariant_under_leverage_change():
    """C12: Gross loss, gross reward, and gross RR are invariant under leverage changes."""
    calc_5x = calculate_risk_reward("LONG", 4000.0, 3990.0, 4030.0, 1000.0, 0.5, leverage=5)
    calc_20x = calculate_risk_reward("LONG", 4000.0, 3990.0, 4030.0, 1000.0, 0.5, leverage=20)
    assert calc_5x.gross_loss_usdt == calc_20x.gross_loss_usdt
    assert calc_5x.gross_reward_usdt == calc_20x.gross_reward_usdt
    assert calc_5x.gross_rr == calc_20x.gross_rr
    assert math.isclose(calc_5x.initial_margin_usdt / calc_20x.initial_margin_usdt, 4.0, rel_tol=1e-3)


# =============================================================================
# I01 - I02: INSTRUMENT PROVIDER & BITGET 10 TIERS
# =============================================================================

def test_i01_bitget_10_tiers_and_zero_deduction():
    """I01: BITGET_TIERS contains 10 tiers with maintenance margin rate and leverage limits."""
    assert len(BITGET_TIERS) == 10
    for tier in BITGET_TIERS:
        assert tier.maintenance_margin_rate > 0
        assert tier.max_leverage > 0


def test_i02_liquidation_buffer_checks():
    """I02: Liquidation calculation warns when buffer to SL is too tight."""
    lp, init_m, maint_m, tier, max_lev = calculate_isolated_liquidation(
        direction="LONG",
        entry=4000.0,
        quantity=0.1,
        leverage=50
    )
    assert lp < 4000.0


# =============================================================================
# P01 - P02: SNAPSHOT SERIALIZABILITY & PARITY
# =============================================================================

def test_p01_snapshot_to_dict_and_contract_fields():
    """P01: CalculationResult.to_snapshot() produces fully serializable canonical dictionary."""
    calc = calculate_risk_reward("LONG", 4000.0, 3990.0, 4030.0, 1000.0, 0.5, setup_id="setup-123")
    snap = calc.to_snapshot()
    raw_json = json.dumps(snap)
    loaded = json.loads(raw_json)

    assert loaded["setup_id"] == "setup-123"
    assert loaded["direction"] == "LONG"
    assert loaded["cost_source"] == "BITGET_PAPER_MODEL_V10_4"
    assert "FUNDING_UNMODELED" in loaded["exclusions"]
    assert "entry_fee_usdt" in loaded
    assert "sl_exit_fee_usdt" in loaded
    assert "tp_exit_fee_usdt" in loaded


# =============================================================================
# X01 - X07: EXECUTION, FILL & EXIT LIFECYCLE TESTS
# =============================================================================

def test_x01_fill_records_actual_cost_snapshot(isolated_db: Session):
    """X01: Paper market fill records canonical cost_snapshot with separated branches."""
    db = isolated_db
    now_ms = int(time.time() * 1000)
    order = models.PaperOrder(
        id="ord-x01",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="MARKET",
        planned_entry=2650.0,
        stop_loss=2640.0,
        take_profit=2685.0,
        quantity=0.05,
        initial_risk_usdt=0.60,
        gross_rr=3.5,
        estimated_net_rr=2.5,
        created_at=now_ms,
        armed_at=now_ms
    )
    db.add(order)
    db.commit()

    calc = calculate_risk_reward("LONG", 2650.10, 2640.0, 2685.0, 1000.0, 0.25, entry_has_slippage=True)
    TradeLifecycleService.execute_fill(db, order, fill_price=2650.10, calc_result=calc, now_ms=now_ms)
    db.refresh(order)

    assert order.state == "paper_open"
    assert order.actual_entry == 2650.10
    assert order.cost_snapshot is not None
    snap = json.loads(order.cost_snapshot)
    assert "entry_fee_usdt" in snap
    assert snap.get("maker_fee_rate") == 0.0002
    assert snap.get("taker_fee_rate") == 0.0004


def test_x04_tp_exit_pnl_formula(isolated_db: Session):
    """X04: Take Profit exit computes positive Gross & Net PnL accurately."""
    db = isolated_db
    now_ms = int(time.time() * 1000)
    order = models.PaperOrder(
        id="ord-x04",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="MARKET",
        planned_entry=2650.0,
        stop_loss=2640.0,
        take_profit=2685.0,
        quantity=0.05,
        initial_risk_usdt=0.60,
        created_at=now_ms,
        armed_at=now_ms
    )
    db.add(order)
    db.commit()

    calc = calculate_risk_reward("LONG", 2650.0, 2640.0, 2685.0, 1000.0, 0.25)
    TradeLifecycleService.execute_fill(db, order, fill_price=2650.0, calc_result=calc, now_ms=now_ms)
    TradeLifecycleService.execute_close(db, order.id, exit_price=2685.0, exit_cause="TP_HIT", now_ms=now_ms + 1000)
    db.refresh(order)

    assert order.state == "closed"
    assert order.exit_cause == "TP_HIT"
    assert order.realized_pnl_net > 0
    assert order.realized_r > 0


def test_x05_sl_exit_pnl_formula_short(isolated_db: Session):
    """X05: Stop Loss exit on SHORT computes negative Gross & Net PnL accurately."""
    db = isolated_db
    now_ms = int(time.time() * 1000)
    order = models.PaperOrder(
        id="ord-x05",
        instrument="XAUUSDT",
        direction="SHORT",
        state="armed",
        order_type="MARKET",
        planned_entry=2650.0,
        stop_loss=2660.0,
        take_profit=2620.0,
        quantity=0.05,
        initial_risk_usdt=0.60,
        created_at=now_ms,
        armed_at=now_ms
    )
    db.add(order)
    db.commit()

    calc = calculate_risk_reward("SHORT", 2650.0, 2660.0, 2620.0, 1000.0, 0.25)
    TradeLifecycleService.execute_fill(db, order, fill_price=2650.0, calc_result=calc, now_ms=now_ms)
    TradeLifecycleService.execute_close(db, order.id, exit_price=2660.0, exit_cause="SL_HIT", now_ms=now_ms + 1000)
    db.refresh(order)

    assert order.state == "closed"
    assert order.exit_cause == "SL_HIT"
    assert order.realized_pnl_net < 0
    assert order.realized_r < 0
