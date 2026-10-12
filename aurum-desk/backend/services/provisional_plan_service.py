"""
backend/services/provisional_plan_service.py
V14.4.2 — Pure service for provisional target selection, price geometry validation,
and display plan calculation per Appendix A contract.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Union

from domain_calculator import calculate_risk_reward, validate_price_geometry
from services.smc_liquidity_service import select_unconsumed_target
from services.smc_scenario_types import LiquidityLevel, make_evidence_id


@dataclass(frozen=True)
class ProvisionalPlanResult:
    status: str
    reason_code: Optional[str]
    direction: Optional[str]
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    target_id: Optional[str]
    target_known_at_ms: Optional[int]
    as_of_ms: int
    calculation: Optional[Any] = None


@dataclass(frozen=True)
class FinancialSettings:
    capital_usdt: float = 1000.0
    risk_pct: float = 0.25
    leverage: int = 5
    margin_mode: str = "ISOLATED"
    min_net_rr: float = 2.0
    costs: Optional[Any] = None


def adapt_legacy_liquidity_candidates(
    raw_levels_or_swings: Sequence[Any],
    symbol: str = "XAUUSDT",
    timeframe: str = "15M",
    as_of_ms: Optional[int] = None,
) -> List[LiquidityLevel]:
    """
    Adapts legacy swings or dict items into typed LiquidityLevel instances.
    Levels already typed as LiquidityLevel are passed through.
    Causal rules:
    - known_at_ms must be <= as_of_ms if as_of_ms is provided.
    - If known_at_ms is missing or unknown, do not fabricate untouched.
    - Preserves state: UNTOUCHED, APPROACHED, SWEPT, BROKEN, CONSUMED.
    """
    adapted: List[LiquidityLevel] = []
    if not raw_levels_or_swings:
        return adapted

    for idx, item in enumerate(raw_levels_or_swings):
        if isinstance(item, LiquidityLevel):
            if as_of_ms is None or item.known_at_ms <= as_of_ms:
                adapted.append(item)
            continue

        if not isinstance(item, dict):
            continue

        price = item.get("price")
        if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            continue
        price = float(price)

        raw_type = str(item.get("type", item.get("source_kind", ""))).upper()
        raw_side = str(item.get("side", "")).upper()

        if raw_side in ("BUY_SIDE", "SELL_SIDE"):
            side = raw_side
        elif "HIGH" in raw_type:
            side = "BUY_SIDE"
        elif "LOW" in raw_type:
            side = "SELL_SIDE"
        else:
            continue

        occ_ms = item.get("timestamp", item.get("occurred_at_ms"))
        known_ms = item.get("confirmed_at", item.get("known_at_ms"))
        if known_ms is None:
            known_ms = occ_ms

        if known_ms is None:
            # Metadata missing: causal rule dictates we cannot certify as untouched
            continue

        if as_of_ms is not None and known_ms > as_of_ms:
            # Future bar / lookahead rejected
            continue

        level_id = item.get("id") or make_evidence_id(symbol, timeframe, raw_type or side, f"{price:.2f}-{idx}")
        state = item.get("state", "UNTOUCHED")

        adapted.append(
            LiquidityLevel(
                id=level_id,
                price=price,
                side=side,
                source_kind=raw_type or ("SWING_HIGH" if side == "BUY_SIDE" else "SWING_LOW"),
                occurred_at_ms=int(occ_ms or known_ms),
                known_at_ms=int(known_ms),
                source_ids=[level_id],
                state=state,
                label_vi=item.get("label_vi", f"Mức thanh khoản {price:.2f}"),
            )
        )

    return adapted


def build_provisional_plan(
    *,
    direction: Optional[str],
    entry: Optional[float],
    stop_loss: Optional[float],
    levels: Sequence[Any],
    as_of_ms: Optional[int] = None,
    settings: Optional[Union[FinancialSettings, Dict[str, Any], Any]] = None,
    causal_candles: Optional[Sequence[Any]] = None,
) -> ProvisionalPlanResult:
    """
    Pure service building provisional plan according to Appendix A contract.
    Ensures:
    - SHORT always TP < Entry < SL
    - LONG always SL < Entry < TP
    - Causal unconsumed target only
    - No artificial offset fallback (e.g. ±20 USD) in certified/provisional SMC
    """
    now_ms = as_of_ms if as_of_ms is not None else int(time.time() * 1000)

    finite_price = lambda value: (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )

    if direction not in ("LONG", "SHORT"):
        return ProvisionalPlanResult(
            status="UNKNOWN_DIRECTION",
            reason_code="UNKNOWN_DIRECTION",
            direction=None,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=None,
            target_id=None,
            target_known_at_ms=None,
            as_of_ms=now_ms,
            calculation=None,
        )

    if not finite_price(entry) or not finite_price(stop_loss):
        return ProvisionalPlanResult(
            status="INCOMPLETE_DATA",
            reason_code="PLAN_LEVELS_MISSING",
            direction=direction,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=None,
            target_id=None,
            target_known_at_ms=None,
            as_of_ms=now_ms,
            calculation=None,
        )

    entry = float(entry)
    stop_loss = float(stop_loss)

    # Adapt candidates if needed
    typed_levels = adapt_legacy_liquidity_candidates(levels, as_of_ms=now_ms)

    # Update states if causal candles provided
    if causal_candles:
        from services.smc_liquidity_service import update_liquidity_states
        update_liquidity_states(typed_levels, causal_candles, now_ms)

    # Select unconsumed target strictly on the correct side
    target = select_unconsumed_target(typed_levels, direction, entry, as_of_ms=now_ms)
    if target is None:
        return ProvisionalPlanResult(
            status="NO_TARGET",
            reason_code="NO_VALID_TARGET",
            direction=direction,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=None,
            target_id=None,
            target_known_at_ms=None,
            as_of_ms=now_ms,
            calculation=None,
        )

    valid, reason = validate_price_geometry(direction, entry, stop_loss, target.price)
    if not valid:
        return ProvisionalPlanResult(
            status="INVALID_GEOMETRY",
            reason_code=reason,
            direction=direction,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=None,
            target_id=target.id,
            target_known_at_ms=target.known_at_ms,
            as_of_ms=now_ms,
            calculation=None,
        )

    # Map settings
    capital_usdt = 1000.0
    risk_pct = 0.25
    leverage = 5
    margin_mode = "ISOLATED"
    min_net_rr = 2.0
    costs = None

    if settings is not None:
        if isinstance(settings, dict):
            capital_usdt = settings.get("capital_usdt", settings.get("capital", 1000.0))
            risk_pct = settings.get("risk_pct", 0.25)
            leverage = settings.get("leverage", 5)
            margin_mode = settings.get("margin_mode", "ISOLATED")
            min_net_rr = settings.get("min_net_rr", 2.0)
            costs = settings.get("costs")
        else:
            capital_usdt = getattr(settings, "capital_usdt", getattr(settings, "capital", 1000.0))
            risk_pct = getattr(settings, "risk_pct", 0.25)
            leverage = getattr(settings, "leverage", 5)
            margin_mode = getattr(settings, "margin_mode", "ISOLATED")
            min_net_rr = getattr(settings, "min_net_rr", 2.0)
            costs = getattr(settings, "costs", None)

    calc = calculate_risk_reward(
        direction=direction,
        planned_entry=entry,
        stop_loss=stop_loss,
        take_profit=target.price,
        capital_usdt=capital_usdt,
        risk_pct=risk_pct,
        leverage=leverage,
        margin_mode=margin_mode,
        min_net_rr=min_net_rr,
        costs=costs,
    )

    status = "AVAILABLE" if calc.is_valid else "INVALID_GEOMETRY"
    code = calc.invalid_reason or "VALID_PROVISIONAL_PLAN"
    if calc.is_valid and not calc.meets_min_rr:
        status, code = "AVAILABLE_BLOCKED", "NET_RR_TOO_LOW"

    return ProvisionalPlanResult(
        status=status,
        reason_code=code,
        direction=direction,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=target.price,
        target_id=target.id,
        target_known_at_ms=target.known_at_ms,
        as_of_ms=now_ms,
        calculation=calc,
    )
