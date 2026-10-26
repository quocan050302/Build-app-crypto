"""
backend/services/smc_scenario_plan.py
V14.4.1 — Atomic price plan for a confirmed SMC scenario, computed by the SAME calculator as the rest of the app.

No synthetic target (retest +/- 20), no sweep-level-as-stop and no production default capital/leverage:
settings (capital, risk, leverage, margin, costs, tick) are required inputs supplied by the caller.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

from domain_calculator import CalculationResult, CostAssumptions, calculate_risk_reward
from services.smc_scenario_config import ScenarioConfig


@dataclass(frozen=True)
class FinancialSettings:
    """Canonical financial snapshot (the same numbers feed chart, drawer, guard and order)."""
    capital_usdt: float
    risk_pct: float
    leverage: int
    margin_mode: str
    costs: CostAssumptions
    instrument_version: str = "XAUUSDT-paper-v1"

    def to_dict(self) -> Dict[str, Any]:
        return {"capital_usdt": self.capital_usdt, "risk_pct": self.risk_pct, "leverage": self.leverage,
                "margin_mode": self.margin_mode, "costs": self.costs.model_dump(),
                "instrument_version": self.instrument_version}


@dataclass(frozen=True)
class ScenarioPlan:
    is_valid: bool
    invalid_reason: Optional[str]
    direction: str
    planned_entry: float
    stop_loss: float
    take_profit: float
    invalidation_price: float
    net_rr: float
    gross_rr: float
    quantity: float
    net_risk_usdt: float
    net_reward_usdt: float
    plan_fingerprint: str
    calc_result: Optional[CalculationResult] = None
    financial: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "invalid_reason": self.invalid_reason,
            "direction": self.direction,
            "planned_entry": self.planned_entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "invalidation_price": self.invalidation_price,
            "net_rr": self.net_rr,
            "gross_rr": self.gross_rr,
            "quantity": self.quantity,
            "net_risk_usdt": self.net_risk_usdt,
            "net_reward_usdt": self.net_reward_usdt,
            "plan_fingerprint": self.plan_fingerprint,
            "can_execute": bool(self.calc_result.can_execute) if self.calc_result is not None else False,
            "financial": self.financial,
        }


def make_plan_fingerprint(symbol: str, direction: str, entry: float, sl: float, tp: float) -> str:
    raw = f"{symbol}:{direction}:{entry:.2f}:{sl:.2f}:{tp:.2f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _finite_pos(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0


def _invalid(direction: str, reason: str, *, entry: float = 0.0, sl: float = 0.0, tp: float = 0.0,
             inv: float = 0.0, financial: Optional[Dict[str, Any]] = None) -> ScenarioPlan:
    return ScenarioPlan(
        is_valid=False, invalid_reason=reason, direction=direction, planned_entry=entry, stop_loss=sl,
        take_profit=tp, invalidation_price=inv, net_rr=0.0, gross_rr=0.0, quantity=0.0,
        net_risk_usdt=0.0, net_reward_usdt=0.0, plan_fingerprint="", financial=financial,
    )


def build_scenario_plan(
    *,
    symbol: str,
    direction: str,
    entry_price: float,
    sweep_extreme: Optional[float],
    target_level: Optional[float],
    config: ScenarioConfig,
    settings: FinancialSettings,
    now_ms: Optional[int] = None,
    setup_id: Optional[str] = None,
    setup_instance_id: Optional[str] = None,
    revision: int = 1,
) -> ScenarioPlan:
    """Plan from MEASURED inputs only.

    entry_price      current executable reference (last closed price / validated quote), NOT a historical wick.
    sweep_extreme    the ACTUAL wick extreme of the sweep candle (invalidation/SL anchor + buffer).
    target_level     confirmed, unconsumed liquidity on the correct side of entry.
    A missing extreme or target yields an invalid plan with an explicit reason (no guessing)."""
    fin = settings.to_dict()
    if direction not in ("LONG", "SHORT"):
        return _invalid(direction, f"UNKNOWN_DIRECTION: {direction}", financial=fin)
    if not _finite_pos(entry_price):
        return _invalid(direction, "NO_VALID_ENTRY", financial=fin)
    if not _finite_pos(sweep_extreme):
        return _invalid(direction, "SWEEP_EXTREME_MISSING", entry=round(entry_price, 2), financial=fin)
    if not _finite_pos(target_level):
        return _invalid(direction, "NO_VALID_TARGET", entry=round(entry_price, 2),
                        sl=round(sweep_extreme, 2), financial=fin)

    buffer_dist = config.sl_buffer_ticks * config.tick_size
    sl = round(sweep_extreme - buffer_dist, 2) if direction == "LONG" else round(sweep_extreme + buffer_dist, 2)
    inv = round(sweep_extreme, 2)
    entry, tp = round(entry_price, 2), round(target_level, 2)

    geometry_ok = (sl < entry < tp) if direction == "LONG" else (tp < entry < sl)
    if not geometry_ok:
        return _invalid(direction, "INVALID_PRICE_GEOMETRY", entry=entry, sl=sl, tp=tp, inv=inv, financial=fin)

    calc = calculate_risk_reward(
        direction=direction, planned_entry=entry, stop_loss=sl, take_profit=tp,
        capital_usdt=settings.capital_usdt, risk_pct=settings.risk_pct,
        min_net_rr=config.min_net_rr, costs=settings.costs,
        leverage=settings.leverage, margin_mode=settings.margin_mode,
        symbol=symbol, now_ms=now_ms, setup_id=setup_id, setup_instance_id=setup_instance_id, revision=revision,
    )
    fingerprint = make_plan_fingerprint(symbol, direction, entry, sl, tp)
    reason: Optional[str] = None
    if not calc.is_valid:
        reason = calc.invalid_reason or "CALCULATOR_INVALID"
    elif not calc.meets_min_rr:
        reason = f"INSUFFICIENT_RR: Net R:R {calc.net_rr:.2f} < {config.min_net_rr:.2f}"
    elif not calc.can_execute:
        reason = str(calc.skip_reason or "CALCULATOR_CANNOT_EXECUTE")

    return ScenarioPlan(
        is_valid=reason is None, invalid_reason=reason, direction=direction, planned_entry=entry,
        stop_loss=sl, take_profit=tp, invalidation_price=inv, net_rr=calc.net_rr, gross_rr=calc.gross_rr,
        quantity=calc.quantity, net_risk_usdt=calc.net_risk_usdt, net_reward_usdt=calc.net_reward_usdt,
        plan_fingerprint=fingerprint, calc_result=calc, financial=fin,
    )
