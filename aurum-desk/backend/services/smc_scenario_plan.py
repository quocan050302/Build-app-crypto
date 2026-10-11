from dataclasses import dataclass
from typing import Optional, Dict, Any
import hashlib
from domain_calculator import calculate_risk_reward, CalculationResult, CostAssumptions
from services.smc_scenario_config import ScenarioConfig

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

    def to_dict(self) -> Dict[str, Any]:
        return {
            'is_valid': self.is_valid,
            'invalid_reason': self.invalid_reason,
            'direction': self.direction,
            'planned_entry': self.planned_entry,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'invalidation_price': self.invalidation_price,
            'net_rr': self.net_rr,
            'gross_rr': self.gross_rr,
            'quantity': self.quantity,
            'net_risk_usdt': self.net_risk_usdt,
            'net_reward_usdt': self.net_reward_usdt,
            'plan_fingerprint': self.plan_fingerprint,
        }

def make_plan_fingerprint(symbol: str, direction: str, entry: float, sl: float, tp: float) -> str:
    raw = f"{symbol}:{direction}:{entry:.2f}:{sl:.2f}:{tp:.2f}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]

def build_scenario_plan(
    *,
    symbol: str,
    direction: str,
    retest_price: float,
    sweep_extreme: float,
    target_level: Optional[float],
    config: ScenarioConfig,
    invalidation_price: Optional[float] = None,
    capital: float = 1000.0,
    risk_pct: Optional[float] = None,
    now_ms: Optional[int] = None,
    setup_id: Optional[str] = None,
    setup_instance_id: Optional[str] = None,
    revision: int = 1,
) -> ScenarioPlan:
    effective_risk_pct = risk_pct if risk_pct is not None else config.default_risk_pct
    tick = 0.01 # Standard XAUUSDT tick

    if direction not in ('LONG', 'SHORT'):
        return ScenarioPlan(
            is_valid=False,
            invalid_reason=f'UNKNOWN_DIRECTION: {direction}',
            direction=direction,
            planned_entry=0.0,
            stop_loss=0.0,
            take_profit=0.0,
            invalidation_price=0.0,
            net_rr=0.0,
            gross_rr=0.0,
            quantity=0.0,
            net_risk_usdt=0.0,
            net_reward_usdt=0.0,
            plan_fingerprint='',
        )

    if target_level is None or target_level <= 0:
        return ScenarioPlan(
            is_valid=False,
            invalid_reason='NO_VALID_TARGET',
            direction=direction,
            planned_entry=round(retest_price, 2) if retest_price > 0 else 0.0,
            stop_loss=round(sweep_extreme, 2) if sweep_extreme > 0 else 0.0,
            take_profit=0.0,
            invalidation_price=round(invalidation_price, 2) if invalidation_price else 0.0,
            net_rr=0.0,
            gross_rr=0.0,
            quantity=0.0,
            net_risk_usdt=0.0,
            net_reward_usdt=0.0,
            plan_fingerprint='',
        )

    buffer_dist = config.sl_buffer_ticks * tick
    if direction == 'LONG':
        sl = round(sweep_extreme - buffer_dist, 2)
        inv = round(invalidation_price if invalidation_price is not None else sweep_extreme, 2)
    else:
        sl = round(sweep_extreme + buffer_dist, 2)
        inv = round(invalidation_price if invalidation_price is not None else sweep_extreme, 2)

    entry = round(retest_price, 2)
    tp = round(target_level, 2)

    costs = CostAssumptions(
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0004,
        slippage_usd=0.10,
        multiplier=1.0,
    )

    calc = calculate_risk_reward(
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        capital=capital,
        risk_pct=effective_risk_pct,
        min_net_rr=config.min_net_rr,
        costs=costs,
        symbol=symbol,
        now_ms=now_ms,
        setup_id=setup_id,
        setup_instance_id=setup_instance_id,
        revision=revision,
    )

    fingerprint = make_plan_fingerprint(symbol, direction, entry, sl, tp)

    if not calc.is_valid:
        return ScenarioPlan(
            is_valid=False,
            invalid_reason=calc.invalid_reason,
            direction=direction,
            planned_entry=entry,
            stop_loss=sl,
            take_profit=tp,
            invalidation_price=inv,
            net_rr=calc.net_rr,
            gross_rr=calc.gross_rr,
            quantity=calc.quantity,
            net_risk_usdt=calc.net_risk_usdt,
            net_reward_usdt=calc.net_reward_usdt,
            plan_fingerprint=fingerprint,
            calc_result=calc,
        )

    if not calc.meets_min_rr:
        return ScenarioPlan(
            is_valid=False,
            invalid_reason=f'INSUFFICIENT_RR: Net R:R {calc.net_rr:.2f} < {config.min_net_rr:.2f}',
            direction=direction,
            planned_entry=entry,
            stop_loss=sl,
            take_profit=tp,
            invalidation_price=inv,
            net_rr=calc.net_rr,
            gross_rr=calc.gross_rr,
            quantity=calc.quantity,
            net_risk_usdt=calc.net_risk_usdt,
            net_reward_usdt=calc.net_reward_usdt,
            plan_fingerprint=fingerprint,
            calc_result=calc,
        )

    return ScenarioPlan(
        is_valid=True,
        invalid_reason=None,
        direction=direction,
        planned_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        invalidation_price=inv,
        net_rr=calc.net_rr,
        gross_rr=calc.gross_rr,
        quantity=calc.quantity,
        net_risk_usdt=calc.net_risk_usdt,
        net_reward_usdt=calc.net_reward_usdt,
        plan_fingerprint=fingerprint,
        calc_result=calc,
    )
