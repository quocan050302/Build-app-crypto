import math
from typing import Dict, Any, Optional, Tuple, List
from pydantic import BaseModel, Field
from services.instrument_provider import instrument_provider, MarginTier

def get_tier_info(notional: float) -> MarginTier:
    """Retrieve Bitget tier parameters based on position notional value."""
    meta = instrument_provider.get_metadata_sync("XAUUSDT")
    for t in meta.tiers:
        if notional <= t.max_notional:
            return t
    return meta.tiers[-1] if meta.tiers else None

class CostAssumptions(BaseModel):
    maker_fee_rate: float = 0.0002
    taker_fee_rate: float = 0.0004
    slippage_usd: float = 0.10
    multiplier: float = 1.0
    tp_is_maker: bool = False     # False = TP triggered as market order (taker fee assumption)


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

    # Leverage, Margin & Liquidation
    leverage: int = 5
    margin_mode: str = "ISOLATED"
    initial_margin_usdt: float = 0.0
    maintenance_margin_usdt: float = 0.0
    estimated_liquidation: Optional[float] = None
    sl_lp_buffer_usdt: Optional[float] = None
    tier: int = 1
    max_tier_leverage: int = 50


def validate_price_geometry(direction: str, entry: float, sl: float, tp: float) -> Tuple[bool, Optional[str]]:
    """Strictly validates price ordering without using abs() to mask invalid directions."""
    if not (math.isfinite(entry) and math.isfinite(sl) and math.isfinite(tp)):
        return False, "PRICES_NOT_FINITE: Mức giá phải là số thực hợp lệ"

    if entry <= 0 or sl <= 0 or tp <= 0:
        return False, "PRICES_MUST_BE_POSITIVE: Mọi mức giá phải lớn hơn 0"

    if direction == "LONG":
        if not (sl < entry < tp):
            return False, f"INVALID_LONG_GEOMETRY: Yêu cầu SL ({sl:.2f}) < Entry ({entry:.2f}) < TP ({tp:.2f})"
    elif direction == "SHORT":
        if not (tp < entry < sl):
            return False, f"INVALID_SHORT_GEOMETRY: Yêu cầu TP ({tp:.2f}) < Entry ({entry:.2f}) < SL ({sl:.2f})"
    else:
        return False, f"UNKNOWN_DIRECTION: {direction}"

    if abs(entry - sl) < 0.01:
        return False, "STOP_DISTANCE_ZERO_OR_TOO_TIGHT: Khoảng cách dừng lỗ bằng 0 hoặc dưới 1 tick (0.01)"

    return True, None


def calculate_isolated_liquidation(
    direction: str,
    entry: float,
    quantity: float,
    leverage: int,
    multiplier: float = 1.0,
    taker_fee_rate: float = 0.0004
) -> Tuple[float, float, float, int, int]:
    """
    Bitget USDT-Margined Perpetual Isolated Liquidation Price Model.
    Based on official Bitget Classic Contract maintenance margin and tier rules:
    Notional = Quantity * Multiplier * Entry
    Initial Margin = Notional / Leverage
    Maintenance Margin = Notional * MMR - Deduction

    Long:
      Equity = Margin + (LP - Entry)*Q*M
      At Liquidation: Equity = MaintenanceMargin(LP) + ClosingFee
      LP_long = [Entry * Q * M - Margin - Deduction] / [Q * M * (1 - MMR - ClosingFeeRate)]

    Short:
      Equity = Margin + (Entry - LP)*Q*M
      At Liquidation: Equity = MaintenanceMargin(LP) + ClosingFee
      LP_short = [Entry * Q * M + Margin + Deduction] / [Q * M * (1 + MMR + ClosingFeeRate)]

    Returns (LP, InitialMargin, MaintenanceMargin, Tier, MaxTierLeverage)
    """
    notional = quantity * multiplier * entry
    tier_info = get_tier_info(notional)
    mmr = tier_info.maintenance_margin_rate if tier_info else 0.005
    deduction = 0.0 # Bitget's public API query-position-lever doesn't currently return deduction, avoid inventing formula
    max_tier_lev = tier_info.max_leverage if tier_info else 100

    eff_leverage = min(leverage, max_tier_lev)
    initial_margin = notional / eff_leverage if eff_leverage > 0 else notional

    qm = quantity * multiplier
    denom_long = qm * (1.0 - mmr - taker_fee_rate)
    denom_short = qm * (1.0 + mmr + taker_fee_rate)

    if direction == "LONG":
        numerator = (entry * qm) - initial_margin - deduction
        lp = numerator / denom_long if denom_long > 0 else 0.0
    else:
        numerator = (entry * qm) + initial_margin + deduction
        lp = numerator / denom_short if denom_short > 0 else entry * 2.0

    maint_margin = max(0.0, (notional * mmr) - deduction)
    return round(lp, 2), round(initial_margin, 2), round(maint_margin, 2), tier_info.tier if tier_info else 1, max_tier_lev


def calculate_risk_reward(
    direction: str,
    entry: Optional[float] = None,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    capital: float = 1000.0,
    risk_pct: float = 0.25,        # Default 0.25% equity (conservative paper default)
    min_net_rr: float = 2.0,       # Strict threshold, no rounding before check
    quantity_override: Optional[float] = None,
    costs: Optional[CostAssumptions] = None,
    entry_has_slippage: bool = False, # Set True if entry is already actual_entry containing slippage
    leverage: int = 5,
    margin_mode: str = "ISOLATED",
    min_sl_lp_buffer_usdt: float = 1.0, # Minimum buffer between SL and Liquidation price
    planned_entry: Optional[float] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    capital_usdt: Optional[float] = None,
) -> CalculationResult:
    """
    Authoritative domain calculation for Risk, Reward, Sizing, Fees, Leverage, Margin and Liquidation.
    Used identically across Preview, Strategy, Broker, and Journal.
    """
    entry = entry if entry is not None else (planned_entry if planned_entry is not None else 0.0)
    sl = sl if sl is not None else (stop_loss if stop_loss is not None else 0.0)
    tp = tp if tp is not None else (take_profit if take_profit is not None else 0.0)
    if capital_usdt is not None:
        capital = capital_usdt

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
            skip_reason=invalid_reason,
            leverage=leverage,
            margin_mode=margin_mode
        )

    mult = costs.multiplier
    stop_distance = (entry - sl) if direction == "LONG" else (sl - entry)
    target_distance = (tp - entry) if direction == "LONG" else (entry - tp)

    budget_usdt = capital * (risk_pct / 100.0)

    # 2. Risk Per Unit Model
    # Entry fee: taker rate
    # SL exit fee: taker rate
    # Slippage: applied to SL exit; entry slippage applied only if entry does NOT already have it
    entry_fee_per_unit = entry * costs.taker_fee_rate * mult
    sl_exit_fee_per_unit = sl * costs.taker_fee_rate * mult
    sl_slippage_per_unit = costs.slippage_usd * mult
    entry_slippage_per_unit = 0.0 if entry_has_slippage else (costs.slippage_usd * mult)

    total_risk_per_unit = (stop_distance * mult) + entry_fee_per_unit + sl_exit_fee_per_unit + entry_slippage_per_unit + sl_slippage_per_unit

    # 3. Quantity Sizing
    can_execute = True
    skip_reason = None

    if quantity_override is not None and quantity_override > 0:
        raw_qty = quantity_override
    else:
        raw_qty = budget_usdt / total_risk_per_unit if total_risk_per_unit > 0 else 0.0

    # Floor to quantity step (0.01)
    meta = instrument_provider.get_metadata_sync("XAUUSDT")
    step = meta.qty_step
    qty = math.floor(raw_qty / step) * step
    qty = round(qty, 4)

    # Check minimum quantity constraint
    min_qty = meta.min_qty
    if qty < min_qty:
        min_unit_risk = min_qty * total_risk_per_unit
        if min_unit_risk > budget_usdt:
            can_execute = False
            skip_reason = f"MIN_QTY_EXCEEDS_BUDGET: Khối lượng tối thiểu {min_qty} oz có rủi ro ${min_unit_risk:.2f} vượt ngân sách rủi ro ${budget_usdt:.2f}"
            qty = min_qty
        else:
            qty = min_qty

    notional = qty * mult * entry
    min_notional = meta.min_notional_usdt
    if notional < min_notional:
        can_execute = False
        skip_reason = f"MIN_NOTIONAL_NOT_MET: Giá trị lệnh ${notional:.2f} nhỏ hơn mức tối thiểu ${min_notional:.2f}"

    # 4. Gross & Net Risk / Reward Calculations
    gross_loss = qty * mult * stop_distance
    gross_reward = qty * mult * target_distance

    # Exact fees for both branches
    entry_fee_total = qty * mult * entry * costs.taker_fee_rate
    sl_exit_fee_total = qty * mult * sl * costs.taker_fee_rate
    tp_fee_rate = costs.maker_fee_rate if costs.tp_is_maker else costs.taker_fee_rate
    tp_exit_fee_total = qty * mult * tp * tp_fee_rate

    # If entry already included slippage in price, do not double-count entry slippage in net_risk
    entry_slippage_total = 0.0 if entry_has_slippage else (qty * costs.slippage_usd * mult)
    sl_slippage_total = qty * costs.slippage_usd * mult

    net_risk = gross_loss + entry_fee_total + sl_exit_fee_total + entry_slippage_total + sl_slippage_total
    net_reward = gross_reward - entry_fee_total - tp_exit_fee_total - entry_slippage_total

    # Guard: if quantity_override was provided, verify it does not exceed budget
    if quantity_override is not None and net_risk > (budget_usdt * 1.001):
        can_execute = False
        skip_reason = f"QTY_OVERRIDE_EXCEEDS_BUDGET: Khối lượng chỉ định {qty} oz có rủi ro ${net_risk:.2f} vượt ngân sách rủi ro ${budget_usdt:.2f}"

    gross_rr = gross_reward / gross_loss if gross_loss > 0 else 0.0
    net_rr = net_reward / net_risk if net_risk > 0 else 0.0

    # Policy Check: Net RR >= 2.0 without premature rounding up
    meets_min_rr = (net_rr >= min_net_rr)
    if not meets_min_rr and can_execute:
        can_execute = False
        skip_reason = f"NET_RR_TOO_LOW: Net R:R 1:{net_rr:.4f} chưa đạt ngưỡng tối thiểu 1:{min_net_rr:.1f}"

    effective_risk_pct = (net_risk / capital) * 100.0 if capital > 0 else 0.0

    # 5. Leverage, Margin & Liquidation Calculation
    lp, init_margin, maint_margin, tier, max_tier_lev = calculate_isolated_liquidation(
        direction=direction,
        entry=entry,
        quantity=qty,
        leverage=leverage,
        multiplier=mult,
        taker_fee_rate=costs.taker_fee_rate
    )

    # Check Margin Availability
    if init_margin > capital and can_execute:
        can_execute = False
        skip_reason = f"INSUFFICIENT_MARGIN: Ký quỹ yêu cầu ${init_margin:.2f} vượt quá vốn khả dụng ${capital:.2f}"

    # Check Liquidation buffer relative to SL
    # Long: LP < SL < Entry. Liquidation buffer = SL - LP
    # Short: Entry < SL < LP. Liquidation buffer = LP - SL
    sl_lp_buffer = (sl - lp) if direction == "LONG" else (lp - sl)

    if direction == "LONG":
        if lp >= sl:
            if can_execute:
                can_execute = False
                skip_reason = f"LIQUIDATION_BEFORE_SL: Giá thanh lý ước tính ({lp:.2f}) nằm TRÊN hoặc BẰNG Stop Loss ({sl:.2f})"
        elif sl_lp_buffer < min_sl_lp_buffer_usdt:
            if can_execute:
                can_execute = False
                skip_reason = f"LIQUIDATION_BUFFER_TOO_TIGHT: Khoảng đệm SL-Thanh lý (${sl_lp_buffer:.2f}) nhỏ hơn tối thiểu ${min_sl_lp_buffer_usdt:.2f}"
    elif direction == "SHORT":
        if lp <= sl:
            if can_execute:
                can_execute = False
                skip_reason = f"LIQUIDATION_BEFORE_SL: Giá thanh lý ước tính ({lp:.2f}) nằm DƯỚI hoặc BẰNG Stop Loss ({sl:.2f})"
        elif sl_lp_buffer < min_sl_lp_buffer_usdt:
            if can_execute:
                can_execute = False
                skip_reason = f"LIQUIDATION_BUFFER_TOO_TIGHT: Khoảng đệm SL-Thanh lý (${sl_lp_buffer:.2f}) nhỏ hơn tối thiểu ${min_sl_lp_buffer_usdt:.2f}"

    # Cross margin policy: only isolated execution is modeled in V4 paper broker
    if margin_mode.upper() == "CROSS" and can_execute:
        can_execute = False
        skip_reason = "CROSS_MARGIN_UNSUPPORTED: Chế độ Cross margin chưa được hỗ trợ thực thi trên tài khoản paper"

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
        skip_reason=skip_reason,
        leverage=leverage,
        margin_mode=margin_mode,
        initial_margin_usdt=init_margin,
        maintenance_margin_usdt=maint_margin,
        estimated_liquidation=lp,
        sl_lp_buffer_usdt=round(sl_lp_buffer, 2),
        tier=tier,
        max_tier_leverage=max_tier_lev
    )
