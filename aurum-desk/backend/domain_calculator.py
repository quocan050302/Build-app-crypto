import math
from typing import Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field

# Bitget XAUUSDT USDT-Margined Perpetual Contract Specifications
INSTRUMENT_METADATA = {
    "symbol": "XAUUSDT",
    "base_asset": "XAU",
    "quote_asset": "USDT",
    "multiplier": 1.0,           # 1 contract = 1 troy ounce
    "tick_size": 0.01,           # Price increment $0.01
    "qty_step": 0.01,            # Quantity step 0.01 oz
    "min_qty": 0.01,             # Minimum quantity 0.01 oz
    "min_notional": 5.0,         # Minimum order notional 5.0 USDT
    "maker_fee_rate": 0.0002,    # 0.02% maker fee
    "taker_fee_rate": 0.0004,    # 0.04% taker fee
    "default_slippage_usd": 0.10 # $0.10 slippage assumption on market order
}

class CostAssumptions(BaseModel):
    maker_fee_rate: float = 0.0002
    taker_fee_rate: float = 0.0004
    slippage_usd: float = 0.10
    multiplier: float = 1.0

class CalculationResult(BaseModel):
    is_valid: bool
    invalid_reason: Optional[str] = None
    direction: str
    planned_entry: float
    stop_loss: float
    take_profit: float
    stop_distance: float
    target_distance: float
    quantity: float
    budget_usdt: float
    gross_loss_usdt: float
    gross_reward_usdt: float
    net_risk_usdt: float
    net_reward_usdt: float
    gross_rr: float
    net_rr: float
    meets_min_rr: bool           # net_rr >= min_net_rr (strictly checked, no premature rounding)
    effective_risk_pct: float
    notional_usdt: float
    fees_total_usdt: float
    slippage_total_usdt: float
    can_execute: bool
    skip_reason: Optional[str] = None


def validate_price_geometry(direction: str, entry: float, sl: float, tp: float) -> Tuple[bool, Optional[str]]:
    """Strictly validates price ordering without using abs() to mask invalid directions."""
    if not (math.isfinite(entry) and math.isfinite(sl) and math.isfinite(tp)):
        return False, "PRICES_NOT_FINITE"

    if entry <= 0 or sl <= 0 or tp <= 0:
        return False, "PRICES_MUST_BE_POSITIVE"

    if direction == "LONG":
        if not (sl < entry < tp):
            return False, f"INVALID_LONG_GEOMETRY: Yêu cầu SL ({sl:.2f}) < Entry ({entry:.2f}) < TP ({tp:.2f})"
    elif direction == "SHORT":
        if not (tp < entry < sl):
            return False, f"INVALID_SHORT_GEOMETRY: Yêu cầu TP ({tp:.2f}) < Entry ({entry:.2f}) < SL ({sl:.2f})"
    else:
        return False, f"UNKNOWN_DIRECTION: {direction}"

    if abs(entry - sl) < 0.01:
        return False, "STOP_DISTANCE_ZERO_OR_TOO_TIGHT"

    return True, None


def calculate_risk_reward(
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    capital: float = 1000.0,
    risk_pct: float = 0.25,        # Default 0.25% equity (conservative paper default)
    min_net_rr: float = 2.0,       # Strict threshold, no rounding before check
    quantity_override: Optional[float] = None,
    costs: Optional[CostAssumptions] = None
) -> CalculationResult:
    """
    Authoritative domain calculation for Risk, Reward, Sizing, and Fees.
    Used identically across Preview, Strategy, Broker, and Journal.
    """
    if costs is None:
        costs = CostAssumptions()

    # 1. Geometry Validation
    is_valid, invalid_reason = validate_price_geometry(direction, entry, sl, tp)
    if not is_valid:
        return CalculationResult(
            is_valid=False,
            invalid_reason=invalid_reason,
            direction=direction,
            planned_entry=entry,
            stop_loss=sl,
            take_profit=tp,
            stop_distance=0.0,
            target_distance=0.0,
            quantity=0.0,
            budget_usdt=0.0,
            gross_loss_usdt=0.0,
            gross_reward_usdt=0.0,
            net_risk_usdt=0.0,
            net_reward_usdt=0.0,
            gross_rr=0.0,
            net_rr=0.0,
            meets_min_rr=False,
            effective_risk_pct=0.0,
            notional_usdt=0.0,
            fees_total_usdt=0.0,
            slippage_total_usdt=0.0,
            can_execute=False,
            skip_reason=invalid_reason
        )

    mult = costs.multiplier
    stop_distance = (entry - sl) if direction == "LONG" else (sl - entry)
    target_distance = (tp - entry) if direction == "LONG" else (entry - tp)

    budget_usdt = capital * (risk_pct / 100.0)

    # 2. Risk Per Unit Model
    # Entry fee: taker rate
    # SL exit fee: taker rate
    # Slippage: applied to entry and SL
    entry_fee_per_unit = entry * costs.taker_fee_rate * mult
    sl_exit_fee_per_unit = sl * costs.taker_fee_rate * mult
    sl_slippage_per_unit = costs.slippage_usd * mult
    entry_slippage_per_unit = costs.slippage_usd * mult

    total_risk_per_unit = (stop_distance * mult) + entry_fee_per_unit + sl_exit_fee_per_unit + entry_slippage_per_unit + sl_slippage_per_unit

    # 3. Quantity Sizing
    can_execute = True
    skip_reason = None

    if quantity_override is not None and quantity_override > 0:
        raw_qty = quantity_override
    else:
        raw_qty = budget_usdt / total_risk_per_unit

    # Floor to quantity step (0.01)
    step = INSTRUMENT_METADATA["qty_step"]
    qty = math.floor(raw_qty / step) * step
    qty = round(qty, 4)

    # Check minimum quantity constraint
    min_qty = INSTRUMENT_METADATA["min_qty"]
    if qty < min_qty:
        min_unit_risk = min_qty * total_risk_per_unit
        if min_unit_risk > budget_usdt:
            can_execute = False
            skip_reason = f"MIN_QTY_EXCEEDS_BUDGET: Khối lượng tối thiểu {min_qty} oz có rủi ro ${min_unit_risk:.2f} vượt ngân sách rủi ro ${budget_usdt:.2f}"
            qty = min_qty
        else:
            qty = min_qty

    notional = qty * mult * entry
    min_notional = INSTRUMENT_METADATA["min_notional"]
    if notional < min_notional:
        can_execute = False
        skip_reason = f"MIN_NOTIONAL_NOT_MET: Giá trị lệnh ${notional:.2f} nhỏ hơn mức tối thiểu ${min_notional:.2f}"

    # 4. Gross & Net Risk / Reward Calculations
    gross_loss = qty * mult * stop_distance
    gross_reward = qty * mult * target_distance

    # Exact fees for both branches
    entry_fee_total = qty * mult * entry * costs.taker_fee_rate
    sl_exit_fee_total = qty * mult * sl * costs.taker_fee_rate
    tp_exit_fee_total = qty * mult * tp * costs.maker_fee_rate  # Target exit uses maker fee
    entry_slippage_total = qty * costs.slippage_usd * mult
    sl_slippage_total = qty * costs.slippage_usd * mult

    net_risk = gross_loss + entry_fee_total + sl_exit_fee_total + entry_slippage_total + sl_slippage_total
    net_reward = gross_reward - entry_fee_total - tp_exit_fee_total - entry_slippage_total

    gross_rr = gross_reward / gross_loss if gross_loss > 0 else 0.0
    net_rr = net_reward / net_risk if net_risk > 0 else 0.0

    # Policy Check: Net RR >= 2.0 without rounding up
    meets_min_rr = (net_rr >= min_net_rr)
    if not meets_min_rr and can_execute:
        can_execute = False
        skip_reason = f"NET_RR_TOO_LOW: Net R:R 1:{net_rr:.4f} chưa đạt ngưỡng tối thiểu 1:{min_net_rr:.1f}"

    effective_risk_pct = (net_risk / capital) * 100.0 if capital > 0 else 0.0

    return CalculationResult(
        is_valid=True,
        invalid_reason=None,
        direction=direction,
        planned_entry=round(entry, 2),
        stop_loss=round(sl, 2),
        take_profit=round(tp, 2),
        stop_distance=round(stop_distance, 2),
        target_distance=round(target_distance, 2),
        quantity=qty,
        budget_usdt=round(budget_usdt, 2),
        gross_loss_usdt=round(gross_loss, 2),
        gross_reward_usdt=round(gross_reward, 2),
        net_risk_usdt=round(net_risk, 2),
        net_reward_usdt=round(net_reward, 2),
        gross_rr=round(gross_rr, 4),
        net_rr=round(net_rr, 4),
        meets_min_rr=meets_min_rr,
        effective_risk_pct=round(effective_risk_pct, 4),
        notional_usdt=round(notional, 2),
        fees_total_usdt=round(entry_fee_total + sl_exit_fee_total, 3),
        slippage_total_usdt=round(entry_slippage_total + sl_slippage_total, 3),
        can_execute=can_execute,
        skip_reason=skip_reason
    )
