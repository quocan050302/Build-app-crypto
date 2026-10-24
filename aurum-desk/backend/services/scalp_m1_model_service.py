"""
backend/services/scalp_m1_model_service.py
V15.0 - SMC M1 Scalp Models: Reversal & Continuation (PHẦN 042 - 053).

Implements:
- evaluate_m1_reversal_candidate: Causal M1 reversal model (Zone touch + Sweep/Rejection + CHOCH + Displacement + Retest).
- evaluate_m1_continuation_candidate: Causal M1 continuation model (Trend context + Pullback + M1 MSS + Net Room).
- Strict closed-candle verification (only closed M1 candles at now_ms).
- Canonical plan generation with net R:R and fees/slippage validation.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

import smc_engine
from domain_calculator import CalculationResult, calculate_risk_reward, validate_price_geometry
from services.stop_placement_types import StopApplicationResult
from services.liquidity_scenario_service import confirm_liquidity_sweep, identify_confirmed_swings
from services.ny_quota_helpers import closed_candles_at, validate_candle_ohlc
from services.scalp_m1_types import (
    ExecutionProfile,
    M1ScalpConfig,
    ModelIdentifier,
    OpportunityEvaluation,
    OpportunityReasonCode,
    OpportunityStage,
    ScalpMode,
    TargetDecision,
    WatchedZone,
    get_scalp_config,
)
from services.smc_liquidity_service import build_liquidity_map, select_unconsumed_target
from services.smc_scenario_types import EvidenceNode, FactStatus, LiquidityLevel, ScenarioSnapshot, ScenarioState

logger = logging.getLogger(__name__)


class ModelExecutionAction(str, Enum):
    EVALUATE_AND_EXECUTE = "EVALUATE_AND_EXECUTE"
    EVALUATE_SHADOW = "EVALUATE_SHADOW"
    DISABLED = "DISABLED"


def resolve_model_execution_policy(
    config: M1ScalpConfig,
    model_id: str,
    execution_scope: Optional[str] = "PAPER",
) -> Tuple[ModelExecutionAction, str]:
    """
    V16.38 Rule 033:
    Pure functional policy router resolving execution rights for an SMC model.
    Contract returns (action: ModelExecutionAction, reason_code: str).
    """
    exec_scope = str(execution_scope or "PAPER").strip().upper()
    exec_profile = getattr(config, "execution_profile", "BASELINE")
    scalp_mode = getattr(config, "scalp_m1_mode", "OFF")

    # Paper-only profile guard (Rule 027)
    if exec_profile == "PAPER_ADAPTIVE_SMC" and exec_scope == "LIVE":
        return ModelExecutionAction.DISABLED, "PAPER_ONLY_PROFILE_VIOLATION"

    # Validate model identifier existence
    valid_ids = {m.value for m in ModelIdentifier}
    if model_id not in valid_ids:
        return ModelExecutionAction.DISABLED, "UNKNOWN_MODEL"

    # Research model handling (Rule 020)
    if model_id == ModelIdentifier.NY_DAILY_RESEARCH.value:
        if getattr(config, "daily_ny_research_enabled", False):
            if exec_scope == "LIVE":
                return ModelExecutionAction.DISABLED, "LIVE_SCOPE_REJECTED"
            return ModelExecutionAction.EVALUATE_AND_EXECUTE, "RESEARCH_ACTIVE"
        return ModelExecutionAction.DISABLED, "RESEARCH_DISABLED"

    # Standalone CG01 Profile routing (Rule 034)
    if exec_profile == "CG01":
        if model_id == ModelIdentifier.CG01_SWEEP_MSS_FVG.value:
            pm = getattr(config, "cg01_profile_mode", "OFF")
            if pm == "PAPER_EXECUTION":
                if scalp_mode == "SHADOW":
                    return ModelExecutionAction.EVALUATE_SHADOW, "CG01_SHADOW_MODE"
                return ModelExecutionAction.EVALUATE_AND_EXECUTE, "CG01_ACTIVE"
            elif pm in ("SHADOW", "PAPER_SHADOW"):
                return ModelExecutionAction.EVALUATE_SHADOW, "CG01_PROFILE_SHADOW"
            else:
                return ModelExecutionAction.DISABLED, "CG01_PROFILE_OFF"
        else:
            return ModelExecutionAction.EVALUATE_SHADOW, "CG01_PROFILE_OTHER_MODEL"

    # PAPER_ADAPTIVE_SMC profile routing (Rules 016, 030, 034, 035, 037, 038)
    if exec_profile == "PAPER_ADAPTIVE_SMC":
        allowlist = getattr(config, "adaptive_model_allowlist", ()) or ()
        if model_id not in allowlist:
            return ModelExecutionAction.DISABLED, "MODEL_DISABLED_BY_PROFILE"

        # Check required feature flags
        if model_id == ModelIdentifier.SMC_M1_FVG_CONTINUATION.value and not getattr(config, "fvg_continuation_enabled", True):
            return ModelExecutionAction.DISABLED, "FVG_CONTINUATION_DISABLED"
        if model_id == ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value and not getattr(config, "sweep_reclaim_enabled", True):
            return ModelExecutionAction.DISABLED, "SWEEP_RECLAIM_DISABLED"
        if model_id == ModelIdentifier.CG01_SWEEP_MSS_FVG.value and not getattr(config, "cg01_enabled", True):
            return ModelExecutionAction.DISABLED, "CG01_DISABLED"

        if scalp_mode == "SHADOW":
            return ModelExecutionAction.EVALUATE_SHADOW, "ADAPTIVE_SHADOW"
        elif scalp_mode == "PAPER":
            return ModelExecutionAction.EVALUATE_AND_EXECUTE, "ADAPTIVE_PAPER_ACTIVE"
        else:
            return ModelExecutionAction.DISABLED, "SCALP_MODE_OFF"

    # BASELINE legacy profile routing (Rule 009, 165)
    if exec_profile == "BASELINE":
        if model_id == ModelIdentifier.CG01_SWEEP_MSS_FVG.value:
            if getattr(config, "cg01_enabled", False) and getattr(config, "cg01_profile_mode", "") == "PAPER_EXECUTION":
                if scalp_mode == "SHADOW":
                    return ModelExecutionAction.EVALUATE_SHADOW, "BASELINE_CG01_SHADOW"
                return ModelExecutionAction.EVALUATE_AND_EXECUTE, "BASELINE_CG01_ACTIVE"
            return ModelExecutionAction.DISABLED, "BASELINE_CG01_DISABLED"

        # Standard SMC models
        if model_id == ModelIdentifier.SMC_M1_FVG_CONTINUATION.value and not getattr(config, "fvg_continuation_enabled", True):
            return ModelExecutionAction.DISABLED, "FVG_CONTINUATION_DISABLED"
        if model_id == ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value and not getattr(config, "sweep_reclaim_enabled", True):
            return ModelExecutionAction.DISABLED, "SWEEP_RECLAIM_DISABLED"

        if scalp_mode == "PAPER":
            return ModelExecutionAction.EVALUATE_AND_EXECUTE, "BASELINE_PAPER_ACTIVE"
        elif scalp_mode == "SHADOW":
            return ModelExecutionAction.EVALUATE_SHADOW, "BASELINE_SHADOW"
        else:
            return ModelExecutionAction.DISABLED, "SCALP_MODE_OFF"

    # Default fallback
    return ModelExecutionAction.DISABLED, "UNHANDLED_POLICY"


def candle_intersects_price_zone(candle: Any, bottom: float, top: float) -> bool:
    """
    Checks genuine geometric interval intersection between candle range [low, high]
    and price zone [bottom, top] (PHẦN 021 - 027).
    Two closed intervals [low, high] and [bottom, top] intersect if and only if:
        low <= top and high >= bottom.
    Strictly validates finite numbers, low <= high, bottom < top.
    """
    try:
        c_low = float(candle.low)
        c_high = float(candle.high)
        bot = float(bottom)
        tp = float(top)
    except (ValueError, TypeError, AttributeError):
        return False

    if not (math.isfinite(c_low) and math.isfinite(c_high) and math.isfinite(bot) and math.isfinite(tp)):
        return False
    if c_low > c_high or bot >= tp:
        return False

    return c_low <= tp and c_high >= bot


def select_sweep_reference(
    levels: Sequence[Any],
    zone: WatchedZone,
    direction: str,
    as_of_ms: int,
    candles: Optional[Sequence[Any]] = None,
    atr: float = 1.0,
) -> Optional[LiquidityLevel]:
    """
    Selects a deterministic prior known liquidity reference level for reversal sweep (PHẦN 042 - 048).
    - LONG: Requires sell-side reference (SELL_SIDE / LOW) near or inside zone, known before or at as_of_ms.
    - SHORT: Requires buy-side reference (BUY_SIDE / HIGH) near or inside zone, known before or at as_of_ms.
    - Proximity: price within [zone.bottom - 0.5 * atr, zone.top + 0.5 * atr].
    - Deterministic selection:
      - When candles provided, selects level with confirmed breach + reclaim via confirm_liquidity_sweep.
      - Falls back to closest eligible boundary level if sweep not yet confirmed.
    """
    if not levels:
        return None

    z_bot = zone.bottom - (0.5 * atr)
    z_top = zone.top + (0.5 * atr)

    target_side = "SELL_SIDE" if direction == "LONG" else "BUY_SIDE"
    target_type = "LOW" if direction == "LONG" else "HIGH"

    eligible: List[LiquidityLevel] = []
    for lvl in levels:
        l_side = getattr(lvl, "side", None) or (lvl.get("side") if isinstance(lvl, dict) else None)
        l_type = getattr(lvl, "type", None) or (lvl.get("type") if isinstance(lvl, dict) else None)
        l_price = float(getattr(lvl, "price", 0.0) if hasattr(lvl, "price") else lvl.get("price", 0.0))
        l_known = int(getattr(lvl, "known_at_ms", 0) if hasattr(lvl, "known_at_ms") else lvl.get("known_at_ms", lvl.get("confirmed_at", 0)))

        if l_known > as_of_ms:
            continue
        if l_side != target_side and l_type != target_type:
            continue
        if not (z_bot <= l_price <= z_top):
            continue

        if not isinstance(lvl, LiquidityLevel):
            l_id = getattr(lvl, "id", None) or (lvl.get("id") if isinstance(lvl, dict) else f"lvl_{l_price}")
            l_occ = int(getattr(lvl, "occurred_at_ms", 0) if hasattr(lvl, "occurred_at_ms") else lvl.get("occurred_at_ms", l_known))
            lvl_obj = LiquidityLevel(
                id=str(l_id),
                price=l_price,
                side=target_side,
                source_kind=str(getattr(lvl, "source_kind", "SWING") if hasattr(lvl, "source_kind") else (lvl.get("source_kind") or "SWING")),
                occurred_at_ms=l_occ,
                known_at_ms=l_known,
                source_ids=[str(l_id)],
                label_vi=f"Mốc thanh khoản {l_price:.2f}",
            )
        else:
            lvl_obj = lvl
        eligible.append(lvl_obj)

    if not eligible:
        return None

    if candles is not None:
        confirmed = [
            l for l in eligible
            if (conf := confirm_liquidity_sweep(l, candles, now_ms=as_of_ms)) and conf.is_confirmed
        ]
        if confirmed:
            return min(confirmed, key=lambda l: l.price) if direction == "LONG" else max(confirmed, key=lambda l: l.price)

    if direction == "LONG":
        return min(eligible, key=lambda l: l.price)
    else:
        return max(eligible, key=lambda l: l.price)


def resolve_executable_target(
    levels: Optional[Sequence[Any]],
    direction: str,
    entry_price: float,
    as_of_ms: int,
    atr: float = 1.0,
    model_id: str = "SMC_M1_REVERSAL",
) -> TargetDecision:
    """
    Resolves authoritative executable target (PHẦN 081 - 091).
    Validates side, unconsumed state, minimum distance, and causal discovery time.
    Strictly forbids artificial risk-multiplier fallback when liquidity target is unavailable.
    """
    if not levels or entry_price <= 0:
        return TargetDecision(
            status="UNAVAILABLE",
            target_type="UNAVAILABLE",
            price=None,
            source_level_id=None,
            known_at_ms=None,
            reason_codes=("TARGET_UNAVAILABLE",),
            label_vi="Chưa có mục tiêu phù hợp, đang theo dõi.",
        )

    typed_levels: List[LiquidityLevel] = []
    for l in levels:
        if isinstance(l, LiquidityLevel):
            typed_levels.append(l)
        elif isinstance(l, dict):
            typed_levels.append(
                LiquidityLevel(
                    id=str(l.get("id", f"lvl_{l.get('price')}")),
                    price=float(l.get("price", 0.0)),
                    side=str(l.get("side", "BUY_SIDE")),
                    source_kind=str(l.get("source_kind", "SWING")),
                    occurred_at_ms=int(l.get("occurred_at_ms", 0)),
                    known_at_ms=int(l.get("known_at_ms", l.get("confirmed_at", 0))),
                    state=str(l.get("state", "UNTOUCHED")),
                    label_vi=str(l.get("label_vi", "")),
                )
            )

    min_dist = max(1.0 * atr, 0.50)
    filtered_levels = []
    for l in typed_levels:
        if direction == "LONG" and l.price >= entry_price + min_dist:
            filtered_levels.append(l)
        elif direction == "SHORT" and l.price <= entry_price - min_dist:
            filtered_levels.append(l)

    target_lvl = select_unconsumed_target(filtered_levels, direction, entry_price, as_of_ms=as_of_ms)
    if target_lvl is None:
        return TargetDecision(
            status="UNAVAILABLE",
            target_type="UNAVAILABLE",
            price=None,
            source_level_id=None,
            known_at_ms=None,
            reason_codes=("TARGET_UNAVAILABLE",),
            label_vi="Chưa có mục tiêu phù hợp, đang theo dõi.",
        )

    t_price = round(float(target_lvl.price), 2)
    if direction == "LONG" and t_price <= entry_price:
        return TargetDecision(
            status="INVALID_SIDE",
            target_type="UNAVAILABLE",
            price=None,
            source_level_id=target_lvl.id,
            known_at_ms=target_lvl.known_at_ms,
            reason_codes=("WRONG_SIDE_TARGET",),
            label_vi="Mục tiêu nằm sai phía so với điểm vào lệnh.",
        )
    if direction == "SHORT" and t_price >= entry_price:
        return TargetDecision(
            status="INVALID_SIDE",
            target_type="UNAVAILABLE",
            price=None,
            source_level_id=target_lvl.id,
            known_at_ms=target_lvl.known_at_ms,
            reason_codes=("WRONG_SIDE_TARGET",),
            label_vi="Mục tiêu nằm sai phía so với điểm vào lệnh.",
        )

    return TargetDecision(
        status="CONFIRMED",
        target_type="LIQUIDITY_LEVEL",
        price=t_price,
        source_level_id=target_lvl.id,
        known_at_ms=target_lvl.known_at_ms,
        reason_codes=(),
        label_vi=target_lvl.label_vi or f"Mục tiêu {t_price:.2f}",
    )


@dataclass
class ScalpCandidate:
    """Executable / Shadow scalp candidate result."""
    is_eligible: bool
    model_id: str                            # SMC_M1_REVERSAL or SMC_M1_CONTINUATION
    direction: str = "NONE"                  # LONG or SHORT
    candidate_key: str = ""
    symbol: str = "XAUUSDT"
    planned_entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    gross_rr: float = 0.0
    net_rr: float = 0.0
    calculation: Optional[CalculationResult] = None
    reason_code: Optional[str] = None
    missing_conditions: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    trigger_chain: Dict[str, Any] = field(default_factory=dict)
    snapshot: Optional[ScenarioSnapshot] = None
    as_of_ms: int = 0
    zone_id: Optional[str] = None
    hypothesis_id: Optional[str] = None
    model_version: str = "v16.1"
    evidence_ids: Tuple[str, ...] = ()
    origin_episode_id: Optional[str] = None
    valid_until_ms: Optional[int] = None
    execution_state: str = "PENDING"
    rejection_reasons: List[str] = field(default_factory=list)
    cluster_id: Optional[str] = None
    order_type: str = "MARKET"
    entry_intent: Optional[Dict[str, Any]] = None
    reference_ce: Optional[float] = None
    execution_profile: Optional[str] = None
    regime_snapshot: Optional[Dict[str, Any]] = None
    sr_evidence: Optional[List[Dict[str, Any]]] = None
    authority_binding: Optional[Dict[str, Any]] = None
    selection_reason: Optional[str] = None
    stop_placement: Optional[Any] = None
    stop_comparison: Optional[Any] = None


def apply_stop_placement_to_candidate(
    *,
    model_id: str,
    direction: str,
    entry_price: float,
    baseline_sl: float,
    baseline_target: float,
    closed_1m: Sequence[Any],
    now_ms: int,
    cfg: Any,
    policy_settings: Dict[str, Any],
    stop_policy: Optional[Any],
    stop_context: Optional[Dict[str, Any]],
    candidate_key: str,
    anchor_result: Any,
    atr_1m_val: float,
    bid: float,
    ask: float,
) -> StopApplicationResult:
    """
    Applies V17.5.1 Stop Placement policy and returns StopApplicationResult.
    CRITICAL: baseline_target is FROZEN and NEVER recalculated from proposed stop!
    In PAPER_STRUCTURAL mode, fails closed if Variant C is missing, waiting, or invalid.
    """
    capital = float(policy_settings.get("capital_usdt", 1000.0))
    risk_pct = float(policy_settings.get("risk_pct", getattr(cfg, "fallback_risk_pct", 0.1)))
    lev = int(policy_settings.get("leverage", 50))
    margin_mode = str(policy_settings.get("margin_mode", "ISOLATED"))
    min_net_rr = float(getattr(cfg, "min_net_rr", 1.5))
    tick_sz = float(getattr(cfg, "tick_size", 0.01))

    if stop_policy is None:
        calc = calculate_risk_reward(
            direction=direction,
            planned_entry=entry_price,
            stop_loss=baseline_sl,
            take_profit=baseline_target,
            capital_usdt=capital,
            risk_pct=risk_pct,
            leverage=lev,
            margin_mode=margin_mode,
            min_net_rr=min_net_rr,
        )
        return StopApplicationResult(
            allowed=calc.can_execute,
            selected_sl=baseline_sl,
            target=baseline_target,
            calculation=calc,
            decision=None,
            comparison=None,
            reason_codes=() if calc.can_execute else (calc.skip_reason or "CANNOT_EXECUTE",),
        )

    from services.stop_placement_service import compute_stop_atr, compute_stop_buffer, plan_structural_stop
    from services.stop_placement_research_service import build_stop_comparison_plan

    atr_m1_obj = (stop_context or {}).get("atr_m1")
    atr_m5_obj = (stop_context or {}).get("atr_m5")
    if atr_m1_obj is None and closed_1m:
        atr_m1_obj = compute_stop_atr(closed_1m, period=stop_policy.atr_period, timeframe="1M", as_of_ms=now_ms)

    buffer_res = compute_stop_buffer(
        policy=stop_policy,
        tick_size=tick_sz,
        atr_m1=atr_m1_obj,
        atr_m5=atr_m5_obj,
        bid=bid,
        ask=ask,
    )

    stop_dec = plan_structural_stop(
        direction=direction,
        planned_entry=entry_price,
        take_profit=baseline_target,
        legacy_sl=baseline_sl,
        anchor_result=anchor_result,
        buffer_result=buffer_res,
        policy=stop_policy,
        as_of_ms=now_ms,
        tick_size=tick_sz,
        atr_m1_val=atr_1m_val,
    )

    comp_plan = build_stop_comparison_plan(
        opportunity_key=candidate_key,
        model_id=model_id,
        symbol="XAUUSDT",
        direction=direction,
        planned_entry=entry_price,
        take_profit=baseline_target,
        legacy_sl=baseline_sl,
        anchor=anchor_result.anchor if (anchor_result and hasattr(anchor_result, "anchor")) else None,
        buffer_result=buffer_res,
        policy_settings=policy_settings,
        tick_size=tick_sz,
        min_net_rr=min_net_rr,
        atr_m1=atr_m1_obj.value if atr_m1_obj else None,
        atr_m5=atr_m5_obj.value if atr_m5_obj else None,
        spread=max(0.0, ask - bid),
        policy_version=stop_policy.version,
        now_ms=now_ms,
    )

    mode = getattr(stop_policy, "mode", "SHADOW_COMPARE")
    if mode == "PAPER_STRUCTURAL":
        is_structural_valid = (
            stop_dec is not None
            and stop_dec.status == "VALID"
            and stop_dec.applied_variant == "C"
            and stop_dec.anchor is not None
            and stop_dec.selected_sl is not None
            and atr_m1_obj is not None
            and atr_m1_obj.status == "AVAILABLE"
        )
        if not is_structural_valid:
            # DO NOT produce executable legacy calculation
            calc = calculate_risk_reward(
                direction=direction,
                planned_entry=entry_price,
                stop_loss=baseline_sl,
                take_profit=baseline_target,
                capital_usdt=capital,
                risk_pct=risk_pct,
                leverage=lev,
                margin_mode=margin_mode,
                min_net_rr=min_net_rr,
            )
            reasons = stop_dec.reasons if (stop_dec and stop_dec.reasons) else ((stop_dec.status,) if stop_dec else ("STRUCTURAL_STOP_INVALID",))
            calc = calc.model_copy(update={
                "can_execute": False,
                "skip_reason": f"STRUCTURAL_STOP_NOT_VALID: {', '.join(reasons)}"
            })
            return StopApplicationResult(
                allowed=False,
                selected_sl=stop_dec.selected_sl if (stop_dec and stop_dec.selected_sl is not None) else None,
                target=baseline_target,
                calculation=calc,
                decision=stop_dec,
                comparison=comp_plan,
                reason_codes=reasons,
            )

        selected_sl = stop_dec.selected_sl
        calc = calculate_risk_reward(
            direction=direction,
            planned_entry=entry_price,
            stop_loss=selected_sl,
            take_profit=baseline_target,
            capital_usdt=capital,
            risk_pct=risk_pct,
            leverage=lev,
            margin_mode=margin_mode,
            min_net_rr=min_net_rr,
        )
        if not (calc.is_valid and calc.can_execute and calc.meets_min_rr):
            reasons = (calc.skip_reason or calc.invalid_reason or "REJECTED_COST_RR",)
            return StopApplicationResult(
                allowed=False,
                selected_sl=selected_sl,
                target=baseline_target,
                calculation=calc,
                decision=stop_dec,
                comparison=comp_plan,
                reason_codes=reasons,
            )

        return StopApplicationResult(
            allowed=True,
            selected_sl=selected_sl,
            target=baseline_target,
            calculation=calc,
            decision=stop_dec,
            comparison=comp_plan,
            reason_codes=(),
        )

    # LEGACY and SHADOW_COMPARE: execute Variant A (baseline_sl)
    selected_sl = baseline_sl
    calc = calculate_risk_reward(
        direction=direction,
        planned_entry=entry_price,
        stop_loss=selected_sl,
        take_profit=baseline_target,
        capital_usdt=capital,
        risk_pct=risk_pct,
        leverage=lev,
        margin_mode=margin_mode,
        min_net_rr=min_net_rr,
    )
    allowed = bool(calc.can_execute and calc.meets_min_rr and calc.is_valid)
    return StopApplicationResult(
        allowed=allowed,
        selected_sl=selected_sl,
        target=baseline_target,
        calculation=calc,
        decision=stop_dec,
        comparison=comp_plan,
        reason_codes=() if allowed else (calc.skip_reason or "CANNOT_EXECUTE",),
    )


def candle_open_ms(bar: Any) -> Optional[int]:
    """
    V16 canonical accessor for candle open time in milliseconds (Rules 041-044).
    Supports objects with .open_time, .timestamp, .time, and dicts with corresponding keys.
    Returns valid positive integer ms, or None if missing or non-positive.
    """
    if bar is None:
        return None
    val = None
    if isinstance(bar, dict):
        val = bar.get("open_time") or bar.get("timestamp") or bar.get("time")
    else:
        val = getattr(bar, "open_time", None) or getattr(bar, "timestamp", None) or getattr(bar, "time", None)
    if val is not None:
        try:
            val_int = int(val)
            if val_int > 0:
                return val_int
        except (ValueError, TypeError):
            return None
    return None


def qualify_selected_break(
    candles: Sequence[Any],
    break_idx: int,
    direction: str,
    config: M1ScalpConfig,
    fallback_atr: float = 1.0,
) -> Tuple[bool, float, float, int, str]:
    """
    Qualifies displacement for the exact selected break bar (SECTIONS 076 - 084).
    Uses ONLY the body and prefix ATR of the selected break candle.
    A later strong candle CANNOT qualify an earlier weak break candle.
    Returns: (is_qualified, break_body, prefix_atr, known_at_ms, fail_reason)
    """
    if break_idx < 0 or break_idx >= len(candles):
        return False, 0.0, fallback_atr, 0, "Chỉ số nến phá vỡ không hợp lệ"

    bar = candles[break_idx]
    c_open = float(getattr(bar, "open", 0.0))
    c_close = float(getattr(bar, "close", 0.0))

    if not (math.isfinite(c_open) and math.isfinite(c_close)):
        return False, 0.0, fallback_atr, 0, "Dữ liệu nến phá vỡ không hợp lệ"

    body = abs(c_close - c_open)
    if body <= 0.0:
        return False, 0.0, fallback_atr, 0, "Nến Doji không đạt điều kiện xung lực"

    # Direction-specific check (Section 083)
    if direction == "LONG" and c_close <= c_open:
        return False, body, fallback_atr, 0, "Nến phá vỡ cấu trúc LONG phải là nến tăng"
    elif direction == "SHORT" and c_close >= c_open:
        return False, body, fallback_atr, 0, "Nến phá vỡ cấu trúc SHORT phải là nến giảm"

    # Prefix ATR computed strictly up to break candle (Section 084)
    prefix_atr = smc_engine.compute_atr(candles[:break_idx + 1], period=config.displacement_atr_period)
    if prefix_atr <= 0 or not math.isfinite(prefix_atr):
        prefix_atr = fallback_atr

    threshold = config.displacement_body_atr_min * prefix_atr
    open_ms = candle_open_ms(bar) or 0
    known_at_ms = open_ms + 60_000

    if body < threshold:
        return (
            False,
            body,
            prefix_atr,
            known_at_ms,
            f"Thân nến phá vỡ không đạt ngưỡng xung lực ({body:.2f} < {threshold:.2f})",
        )

    return True, body, prefix_atr, known_at_ms, ""


def validate_evidence_timeline(evidence_nodes: Sequence[Dict[str, Any]], now_ms: int) -> Tuple[bool, List[str]]:
    """
    V16.1 & V16.2 Rule 131: Causal evidence timeline and chain relation validation:
    1. occurred_at_ms <= known_at_ms <= now_ms for every node.
    2. Causal chain ordering between stages (reference -> sweep -> mss -> fvg -> trigger).
    """
    issues: List[str] = []
    nodes_by_role: Dict[str, Dict[str, Any]] = {}

    for ev in evidence_nodes:
        ev_id = ev.get("id", "unknown_node")
        occ = ev.get("occurred_at_ms")
        kn = ev.get("known_at_ms")
        role = ev.get("role") or ev.get("kind")
        if role:
            nodes_by_role[str(role).upper()] = ev

        if occ is None or kn is None:
            issues.append(f"{ev_id}: missing occurred_at_ms or known_at_ms")
            continue
        if occ <= 0 or kn <= 0:
            issues.append(f"{ev_id}: non-positive timestamp (occ={occ}, kn={kn})")
            continue
        if occ > kn:
            issues.append(f"{ev_id}: occurred_at_ms ({occ}) > known_at_ms ({kn})")
        if kn > now_ms:
            issues.append(f"{ev_id}: known_at_ms ({kn}) > now_ms ({now_ms}) (future leak)")

    # Validate causal relationships between steps (Rule 131)
    ref_node = nodes_by_role.get("REFERENCE") or nodes_by_role.get("LIQUIDITY_REFERENCE")
    sweep_node = nodes_by_role.get("SWEEP") or nodes_by_role.get("SWEEP_EVENT")
    mss_node = nodes_by_role.get("MSS") or nodes_by_role.get("STRUCTURE_BREAK")
    fvg_node = nodes_by_role.get("FVG") or nodes_by_role.get("FAIR_VALUE_GAP")
    trig_node = nodes_by_role.get("TRIGGER") or nodes_by_role.get("ENTRY_TRIGGER")

    if ref_node and sweep_node:
        if (ref_node.get("known_at_ms") or 0) > (sweep_node.get("occurred_at_ms") or 0):
            issues.append("Causal chain violation: liquidity reference known after sweep occurred")

    if sweep_node and mss_node:
        if (sweep_node.get("occurred_at_ms") or 0) > (mss_node.get("occurred_at_ms") or 0):
            issues.append("Causal chain violation: sweep occurred after MSS break occurred")

    if mss_node and fvg_node:
        if (mss_node.get("occurred_at_ms") or 0) > (fvg_node.get("known_at_ms") or 0):
            issues.append("Causal chain violation: MSS occurred after FVG confirmed")

    if fvg_node and trig_node:
        if (fvg_node.get("known_at_ms") or 0) > (trig_node.get("occurred_at_ms") or 0):
            issues.append("Causal chain violation: FVG confirmed after entry trigger occurred")

    return (len(issues) == 0, issues)


def resolve_confirmed_structure_reference(
    closed_1m: Sequence[Any],
    direction: str,
    as_of_ms: int,
    left_bars: int = 2,
    right_bars: int = 2,
) -> Optional[Dict[str, Any]]:
    """
    V16.1 Rule 048: Resolves causal confirmed swing reference known_at_ms <= as_of_ms.
    For LONG: confirmed swing high before break.
    For SHORT: confirmed swing low before break.
    Does NOT use future candles or lookahead.
    """
    if not closed_1m or len(closed_1m) < (left_bars + 1):
        return None

    bars = list(closed_1m)

    # Tier 1: Search for a confirmed swing pivot that actually has a confirmed continuation break
    for r in range(right_bars, -1, -1):
        candidate_pivots: List[Dict[str, Any]] = []
        for i in range(left_bars, len(bars) - max(1, r)):
            b = bars[i]
            b_open = candle_open_ms(b) or 0
            b_known = b_open + (r + 1) * 60_000
            if b_known > as_of_ms:
                continue

            if direction == "LONG":
                h = float(b.high)
                is_pivot = all(float(bars[j].high) <= h for j in range(i - left_bars, i + r + 1))
                if is_pivot:
                    candidate_pivots.append({
                        "id": f"m1_sh_{b_open}",
                        "price": h,
                        "occurred_at_ms": b_open,
                        "known_at_ms": b_known,
                        "side": "BUY_SIDE",
                    })
            else:
                l = float(b.low)
                is_pivot = all(float(bars[j].low) >= l for j in range(i - left_bars, i + r + 1))
                if is_pivot:
                    candidate_pivots.append({
                        "id": f"m1_sl_{b_open}",
                        "price": l,
                        "occurred_at_ms": b_open,
                        "known_at_ms": b_known,
                        "side": "SELL_SIDE",
                    })

        broken_candidates = []
        for p in reversed(candidate_pivots):
            brk = detect_confirmed_continuation_break(bars, p, direction, as_of_ms=as_of_ms)
            if brk is not None:
                broken_candidates.append((p, brk))

        if broken_candidates:
            best_p, _ = max(broken_candidates, key=lambda pair: pair[1]["broken_at_ms"])
            return best_p

    # Tier 2: Fallback to the latest pivot known at as_of_ms for observation/waiting
    for i in range(left_bars, len(bars) - max(1, right_bars)):
        b = bars[i]
        b_open = candle_open_ms(b) or 0
        b_known = b_open + (right_bars + 1) * 60_000
        if b_known > as_of_ms:
            continue

        if direction == "LONG":
            h = float(b.high)
            if all(float(bars[j].high) <= h for j in range(i - left_bars, i + right_bars + 1)):
                return {
                    "id": f"m1_sh_{b_open}",
                    "price": h,
                    "occurred_at_ms": b_open,
                    "known_at_ms": b_known,
                    "side": "BUY_SIDE",
                }
        else:
            l = float(b.low)
            if all(float(bars[j].low) >= l for j in range(i - left_bars, i + right_bars + 1)):
                return {
                    "id": f"m1_sl_{b_open}",
                    "price": l,
                    "occurred_at_ms": b_open,
                    "known_at_ms": b_known,
                    "side": "SELL_SIDE",
                }

    return None


def detect_confirmed_continuation_break(
    closed_1m: Sequence[Any],
    swing_ref: Dict[str, Any],
    direction: str,
    as_of_ms: int,
    tick_size: float = 0.01,
) -> Optional[Dict[str, Any]]:
    """
    V16.1 Rule 049: Detects closed candle breaking the reference structure level.
    LONG: closed candle > swing_ref.price
    SHORT: closed candle < swing_ref.price
    Only considers bars where closed_at <= as_of_ms and known_at >= swing_ref.known_at.
    """
    ref_px = float(swing_ref["price"])
    ref_known = int(swing_ref.get("known_at_ms", 0) or 0)

    for b in closed_1m:
        b_open = candle_open_ms(b) or 0
        b_close_ms = b_open + 60_000
        if b_close_ms > as_of_ms or b_open < ref_known - 60_000:
            continue

        b_close = float(b.close)
        if direction == "LONG":
            if b_close > ref_px:
                return {
                    "bar": b,
                    "break_price": b_close,
                    "broken_at_ms": b_open,
                    "known_at_ms": b_close_ms,
                    "swing_ref_id": swing_ref.get("id"),
                    "swing_ref_price": ref_px,
                    "excess": round(b_close - ref_px, 2),
                }
        else:
            if b_close < ref_px:
                return {
                    "bar": b,
                    "break_price": b_close,
                    "broken_at_ms": b_open,
                    "known_at_ms": b_close_ms,
                    "swing_ref_id": swing_ref.get("id"),
                    "swing_ref_price": ref_px,
                    "excess": round(ref_px - b_close, 2),
                }

    return None


def evaluate_displacement(
    breaking_bar: Any,
    atr_1m: float,
    min_body_atr: float = 0.70,
    direction: str = "LONG",
) -> Tuple[bool, float, Dict[str, Any]]:
    """
    V16.1 Rules 050-051: Evaluates candle displacement magnitude relative to causal prefix ATR.
    Checks body >= min_body_atr * ATR and candle close conviction.
    """
    body = abs(float(breaking_bar.close) - float(breaking_bar.open))
    atr = max(0.01, float(atr_1m))
    ratio = round(body / atr, 4)

    high = float(breaking_bar.high)
    low = float(breaking_bar.low)
    rng = max(0.01, high - low)

    if direction == "LONG":
        conviction = (float(breaking_bar.close) - low) / rng >= 0.40
    else:
        conviction = (high - float(breaking_bar.close)) / rng >= 0.40

    is_qualified = bool(ratio >= min_body_atr and conviction)
    details = {
        "body": round(body, 2),
        "atr": round(atr, 2),
        "ratio": ratio,
        "min_ratio": min_body_atr,
        "conviction": conviction,
        "is_qualified": is_qualified,
    }
    return is_qualified, ratio, details


def detect_cluster_sweep_event(
    closed_1m: Sequence[Any],
    cluster: Any,
    direction: str,
) -> Optional[Dict[str, Any]]:
    """
    V16.1 Rule 065: Specific cluster sweep detector.
    LONG: Low pierces below cluster.bottom.
    SHORT: High pierces above cluster.top.
    Filters candles to those known after cluster.known_at_ms.
    """
    c_known = int(getattr(cluster, "known_at_ms", 0) or 0)
    c_bot = float(cluster.bottom)
    c_top = float(cluster.top)

    swept_bar = None
    swept_extreme = None
    swept_at_ms = None

    for b in closed_1m:
        b_open = candle_open_ms(b) or 0
        if b_open < c_known - 60_000:
            continue

        if direction == "LONG":
            if float(b.low) < c_bot:
                if swept_extreme is None or float(b.low) < swept_extreme:
                    swept_extreme = float(b.low)
                    swept_bar = b
                    swept_at_ms = b_open
        else:
            if float(b.high) > c_top:
                if swept_extreme is None or float(b.high) > swept_extreme:
                    swept_extreme = float(b.high)
                    swept_bar = b
                    swept_at_ms = b_open

    if swept_bar is not None and swept_extreme is not None:
        return {
            "bar": swept_bar,
            "swept_extreme": swept_extreme,
            "swept_at_ms": swept_at_ms,
            "cluster_id": getattr(cluster, "id", ""),
            "direction": direction,
            "cluster_bot": c_bot,
            "cluster_top": c_top,
        }
    return None


def detect_cluster_reclaim_event(
    closed_1m: Sequence[Any],
    cluster: Any,
    sweep_event: Dict[str, Any],
    direction: str,
) -> Optional[Dict[str, Any]]:
    """
    V16.1 Rule 066: Specific cluster reclaim detector.
    LONG: Closed candle >= cluster.bottom after sweep.
    SHORT: Closed candle <= cluster.top after sweep.
    """
    c_bot = float(cluster.bottom)
    c_top = float(cluster.top)
    swept_time = int(sweep_event["swept_at_ms"])

    for b in closed_1m:
        b_open = candle_open_ms(b) or 0
        if b_open < swept_time:
            continue

        b_close = float(b.close)
        if direction == "LONG":
            if b_close >= c_bot:
                return {
                    "bar": b,
                    "reclaimed_at_ms": b_open,
                    "reclaim_price": b_close,
                    "direction": direction,
                }
        else:
            if b_close <= c_top:
                return {
                    "bar": b,
                    "reclaimed_at_ms": b_open,
                    "reclaim_price": b_close,
                    "direction": direction,
                }
    return None


def detect_post_reclaim_micro_break(
    closed_1m: Sequence[Any],
    sweep_event: Dict[str, Any],
    reclaim_event: Dict[str, Any],
    direction: str,
    as_of_ms: int,
    *,
    tick_size: float = 0.01,
    tick_excess_threshold: int = 1,
    structural_reference_price: Optional[float] = None,
    allow_sweep_candle_fallback: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    V16.2 Rules 011, 088, 089: Structural micro-break / MSS following sweep and reclaim.
    Requires closed candle beyond the confirmed structural swing reference price.
    Tick excess threshold is strictly applied (excess = tick_size * tick_excess_threshold).
    Sweep candle fallback (branches 2 & 3) is rejected by default for strict SMC models.
    """
    swept_time = int(sweep_event["swept_at_ms"])
    reclaim_time = int(reclaim_event["reclaimed_at_ms"])

    bars = list(closed_1m)
    sweep_idx = None
    for idx, b in enumerate(bars):
        if (candle_open_ms(b) or 0) == swept_time:
            sweep_idx = idx
            break

    if sweep_idx is None:
        sweep_idx = max(0, len(bars) - 4)

    pre_sweep_bars = bars[max(0, sweep_idx - 3): sweep_idx + 1]
    if not pre_sweep_bars:
        pre_sweep_bars = [sweep_event["bar"]]

    sweep_bar = sweep_event["bar"]
    excess = max(0.0, float(tick_size) * float(tick_excess_threshold))

    if direction == "LONG":
        micro_ref_price = (
            float(structural_reference_price)
            if structural_reference_price is not None
            else max(float(b.high) for b in pre_sweep_bars)
        )
        # 1. Structural break past pre-sweep high + required tick excess
        required_break = micro_ref_price + excess
        for b in bars:
            b_open = candle_open_ms(b) or 0
            if b_open < reclaim_time or b_open + 60_000 > as_of_ms:
                continue
            if float(b.close) >= required_break:
                return {
                    "bar": b,
                    "broken_at_ms": b_open,
                    "break_price": float(b.close),
                    "micro_swing_price": micro_ref_price,
                    "direction": direction,
                }

        if allow_sweep_candle_fallback:
            # Legacy fallback 2: Inside-bar / sweep candle reclaim break
            alt_micro_level = max(float(sweep_bar.open), float(sweep_bar.close))
            for b in bars:
                b_open = candle_open_ms(b) or 0
                if b_open < reclaim_time or b_open + 60_000 > as_of_ms:
                    continue
                if float(b.close) > alt_micro_level:
                    return {
                        "bar": b,
                        "broken_at_ms": b_open,
                        "break_price": float(b.close),
                        "micro_swing_price": alt_micro_level,
                        "direction": direction,
                    }
            # Legacy fallback 3: Reclaim bar closed decisively above cluster.bottom and reached top of sweep candle
            c_bot = float(sweep_event.get("cluster_bot", 0.0))
            reclaim_bar = reclaim_event["bar"]
            if float(reclaim_bar.close) >= c_bot and float(reclaim_bar.close) > float(sweep_bar.close):
                return {
                    "bar": reclaim_bar,
                    "broken_at_ms": reclaim_time,
                    "break_price": float(reclaim_bar.close),
                    "micro_swing_price": float(sweep_bar.close),
                    "direction": direction,
                }
    else:
        micro_ref_price = (
            float(structural_reference_price)
            if structural_reference_price is not None
            else min(float(b.low) for b in pre_sweep_bars)
        )
        required_break = micro_ref_price - excess
        for b in bars:
            b_open = candle_open_ms(b) or 0
            if b_open < reclaim_time or b_open + 60_000 > as_of_ms:
                continue
            if float(b.close) <= required_break:
                return {
                    "bar": b,
                    "broken_at_ms": b_open,
                    "break_price": float(b.close),
                    "micro_swing_price": micro_ref_price,
                    "direction": direction,
                }

        if allow_sweep_candle_fallback:
            alt_micro_level = min(float(sweep_bar.open), float(sweep_bar.close))
            for b in bars:
                b_open = candle_open_ms(b) or 0
                if b_open < reclaim_time or b_open + 60_000 > as_of_ms:
                    continue
                if float(b.close) < alt_micro_level:
                    return {
                        "bar": b,
                        "broken_at_ms": b_open,
                        "break_price": float(b.close),
                        "micro_swing_price": alt_micro_level,
                        "direction": direction,
                    }
            c_top = float(sweep_event.get("cluster_top", float("inf")))
            reclaim_bar = reclaim_event["bar"]
            if float(reclaim_bar.close) <= c_top and float(reclaim_bar.close) < float(sweep_bar.close):
                return {
                    "bar": reclaim_bar,
                    "broken_at_ms": reclaim_time,
                    "break_price": float(reclaim_bar.close),
                    "micro_swing_price": float(sweep_bar.close),
                    "direction": direction,
                }

    return None


def make_sweep_episode_id(
    cluster_id: str,
    direction: str,
    swept_extreme: float,
    swept_bar_time: int,
) -> str:
    """V16.1 Rule 072: Stable episode ID tied to cluster, direction, and root sweep event."""
    return f"ep_sr_{cluster_id}_{direction}_{int(swept_extreme * 100)}_{swept_bar_time}"


def candidate_execution_fingerprint(cand: ScalpCandidate, include_plan_revision: bool = False) -> str:
    """
    Rule 128: Episode-based candidate execution fingerprint for idempotency.
    Separates episode idempotency from plan revisions so quote jitter within the
    same episode does not create multiple arms.
    """
    sym = getattr(cand, "symbol", "XAUUSDT") or "XAUUSDT"
    direction = getattr(cand, "direction", "LONG") or "LONG"
    mid = getattr(cand, "model_id", "UNKNOWN") or "UNKNOWN"
    ep = getattr(cand, "origin_episode_id", "") or getattr(cand, "zone_id", "") or getattr(cand, "candidate_key", "")
    base_fp = f"fp_{sym}_{direction}_{mid}_{ep}"
    if include_plan_revision:
        px = round(float(getattr(cand, "planned_entry", 0.0) or 0.0), 1)
        return f"{base_fp}_{px}"
    return base_fp


class ScalpM1ModelService:
    @staticmethod
    def identify_m1_swings(
        candles_1m: Sequence[Any],
        left_bars: int = 2,
        right_bars: int = 2,
        *,
        as_of_ms: int = 0,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Identify causal M1 swing highs and swing lows (PHẦN 047, SECTIONS 021 - 027).
        known_at_ms strictly requires right_bars closed candles.
        Delegates to canonical identify_confirmed_swings with timeframe='1M'.
        """
        from services.liquidity_scenario_service import identify_confirmed_swings
        return identify_confirmed_swings(
            candles_1m,
            timeframe="1M",
            left_bars=left_bars,
            right_bars=right_bars,
            as_of_ms=as_of_ms,
        )

    @staticmethod
    def evaluate_m1_reversal_candidate(
        zone: WatchedZone,
        candles_1m: Sequence[Any],
        current_quote: Any,
        policy_settings: Dict[str, Any],
        *,
        now_ms: int,
        config: Optional[M1ScalpConfig] = None,
        context_levels: Optional[Sequence[Any]] = None,
        stop_policy: Optional[Any] = None,
        stop_context: Optional[Dict[str, Any]] = None,
    ) -> ScalpCandidate:
        """
        Evaluate M1 Reversal Candidate (PHẦN 040 - 050).
        Prerequisites:
        1. Closed M1 candles >= min_closed_m1_bars.
        2. Valid authoritative quote (bid > 0, ask >= bid, fresh, not stale).
        3. Price approached/touched watched zone.
        4. Sweep of local M1 swing or rejection extreme at zone edge.
        5. Causal structure break (CHOCH) past confirmed opposite M1 swing with displacement.
        6. Closed M1 retest pulling back towards the break zone without breaking sweep invalidation.
        7. Net R:R >= min_net_rr with valid SL/TP geometry and executable calculator sizing.
        """
        cfg = config or get_scalp_config()
        model_id = ModelIdentifier.SMC_M1_REVERSAL.value
        fallback_key = f"{model_id}_{zone.symbol}_{zone.zone_id}_{zone.direction}"

        # 1. Closed candles audit (PHẦN 042, 043)
        try:
            closed_1m = closed_candles_at(candles_1m, now_ms=now_ms, duration_ms=60_000)
        except ValueError as err:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=zone.direction,
                candidate_key=fallback_key, reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=[f"Lỗi dữ liệu nến M1: {err}"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )
        if len(closed_1m) < cfg.min_closed_m1_bars:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=zone.direction,
                candidate_key=fallback_key, reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=[f"Thiếu nến M1 đã đóng: {len(closed_1m)}/{cfg.min_closed_m1_bars}"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        # 2. Quote freshness and validity (PHẦN 035, 036)
        bid = float(getattr(current_quote, "bid", 0.0) if hasattr(current_quote, "bid") else (current_quote or {}).get("bid", 0.0))
        ask = float(getattr(current_quote, "ask", 0.0) if hasattr(current_quote, "ask") else (current_quote or {}).get("ask", 0.0))
        is_quote_valid = (
            bool(getattr(current_quote, "is_valid", True))
            and not bool(getattr(current_quote, "is_stale", False))
            and bid > 0
            and ask >= bid
            and math.isfinite(bid)
            and math.isfinite(ask)
        )
        if not is_quote_valid:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=zone.direction,
                candidate_key=fallback_key, reason_code=OpportunityReasonCode.QUOTE_STALE.value,
                missing_conditions=["Báo giá thị trường không hợp lệ hoặc bị mất kết nối"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        atr_1m = smc_engine.compute_atr(closed_1m, period=cfg.displacement_atr_period)
        if atr_1m <= 0:
            atr_1m = 1.0

        if context_levels is None:
            c_sh, c_sl = ScalpM1ModelService.identify_m1_swings(
                closed_1m, cfg.pivot_left_bars, cfg.pivot_right_bars, as_of_ms=now_ms
            )
            context_levels = build_liquidity_map(
                symbol=zone.symbol, timeframe="1M", swing_highs=c_sh, swing_lows=c_sl, now_ms=now_ms
            )

        sh_list, sl_list = ScalpM1ModelService.identify_m1_swings(
            closed_1m, cfg.pivot_left_bars, cfg.pivot_right_bars, as_of_ms=now_ms
        )

        direction = "LONG" if zone.direction in ("BULLISH", "LONG") else "SHORT"
        exec_price = ask if direction == "LONG" else bid

        if direction == "LONG":
            if not sl_list or not sh_list:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Chưa đủ swing high/low M1 để xác nhận cấu trúc đảo chiều"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            sweep_ref = select_sweep_reference(context_levels, zone, "LONG", as_of_ms=now_ms, candles=closed_1m, atr=atr_1m)
            if sweep_ref is None:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_LIQUIDITY_REFERENCE.value,
                    missing_conditions=["Chưa có điểm tham chiếu thanh khoản (sell-side reference) được xác nhận trước đợt quét"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            sweep_conf = confirm_liquidity_sweep(sweep_ref, closed_1m, now_ms=now_ms, candle_duration_ms=60_000)
            if sweep_conf is None or not sweep_conf.is_confirmed:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Chưa xác nhận quét và lấy lại thanh khoản (breach + reclaim) trên mốc tham chiếu"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            sweep_price = sweep_conf.breach_price
            sweep_ts = sweep_conf.occurred_at_ms

            prior_shs = [sh for sh in sh_list if sh["occurred_at_ms"] < sweep_ts]
            if not prior_shs:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Không tìm thấy pivot high M1 có trước điểm sweep để làm mốc phá vỡ CHOCH"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )
            pivot_to_break = prior_shs[-1]

            candidate_key = f"{model_id}_{zone.symbol}_{zone.zone_id}_{direction}_{pivot_to_break['id']}"
            break_level = pivot_to_break["price"]
            bars_after_sweep = [
                b for b in closed_1m
                if (int(getattr(b, "open_time", None) or getattr(b, "timestamp", 0))) >= sweep_ts
            ]
            breaking_bars = [b for b in bars_after_sweep if float(b.close) > break_level]

            if not breaking_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[f"Chưa có nến M1 đóng vượt mức phá vỡ CHOCH ({break_level:.2f})"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            first_break_bar = breaking_bars[0]
            first_break_idx = closed_1m.index(first_break_bar)
            is_break_qual, break_body, break_prefix_atr, break_known_at, fail_reason = qualify_selected_break(
                closed_1m, first_break_idx, "LONG", cfg, fallback_atr=atr_1m
            )
            if not is_break_qual:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[fail_reason],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            if any(float(b.low) < sweep_price for b in bars_after_sweep):
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Giá đã phá vỡ invalidation level (sweep low)"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            # Retest Verification (SECTIONS 021 - 040)
            sweep_bar = next((b for b in closed_1m if (int(getattr(b, "open_time", None) or getattr(b, "timestamp", 0))) == sweep_ts), None)
            sweep_idx = closed_1m.index(sweep_bar) if sweep_bar in closed_1m else max(0, first_break_idx - 5)

            # Causal FVG detection bound to the displacement break leg (SECTIONS 036, 037)
            disp_fvgs = []
            end_k = min(len(closed_1m), first_break_idx + 2)
            for k in range(max(2, sweep_idx + 2), end_k + 1):
                if k >= len(closed_1m):
                    break
                p2 = closed_1m[k - 2]
                curr = closed_1m[k]
                if float(curr.low) > float(p2.high):
                    bot = float(p2.high)
                    top = float(curr.low)
                    if bot >= sweep_price:
                        disp_fvgs.append((k, bot, top))

            if not disp_fvgs:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_RETEST.value,
                    missing_conditions=["Chờ xuất hiện Fair Value Gap (FVG) hợp lệ trong nhịp xung lực phá vỡ để retest"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            fvg_confirm_k, fvg_bot, fvg_top = disp_fvgs[-1]
            post_fvg_bars = closed_1m[fvg_confirm_k + 1:]
            if not post_fvg_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_RETEST.value,
                    missing_conditions=[f"Chờ nến M1 hồi về retest FVG [{fvg_bot:.2f} - {fvg_top:.2f}]"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            retest_bars = [
                b for b in post_fvg_bars
                if candle_intersects_price_zone(b, fvg_bot, fvg_top) and float(b.low) >= sweep_price
            ]
            if not retest_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_RETEST.value,
                    missing_conditions=[f"Chờ nến M1 hồi về retest FVG [{fvg_bot:.2f} - {fvg_top:.2f}]"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            # Plan levels
            sl_price = round(sweep_price - (cfg.sl_buffer_ticks * cfg.tick_size), 2)
            entry_price = round(exec_price, 2)

            # Target Resolution (SECTIONS 081 - 100)
            target_dec = resolve_executable_target(context_levels, direction, entry_price, as_of_ms=now_ms, atr=atr_1m, model_id=model_id)
            if target_dec.status != "CONFIRMED" or target_dec.price is None:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_LIQUIDITY_TARGET.value,
                    missing_conditions=["Chưa có mục tiêu thanh khoản chưa tiêu thụ phù hợp, đang theo dõi"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )
            target_price = target_dec.price

        else:
            # For Bearish Reversal
            if not sl_list or not sh_list:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Chưa đủ swing high/low M1 để xác nhận cấu trúc đảo chiều"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            sweep_ref = select_sweep_reference(context_levels, zone, "SHORT", as_of_ms=now_ms, candles=closed_1m, atr=atr_1m)
            if sweep_ref is None:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_LIQUIDITY_REFERENCE.value,
                    missing_conditions=["Chưa có điểm tham chiếu thanh khoản (buy-side reference) được xác nhận trước đợt quét"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            sweep_conf = confirm_liquidity_sweep(sweep_ref, closed_1m, now_ms=now_ms, candle_duration_ms=60_000)
            if sweep_conf is None or not sweep_conf.is_confirmed:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Chưa xác nhận quét và lấy lại thanh khoản (breach + reclaim) trên mốc tham chiếu"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            sweep_price = sweep_conf.breach_price
            sweep_ts = sweep_conf.occurred_at_ms

            prior_sls = [sl for sl in sl_list if sl["occurred_at_ms"] < sweep_ts]
            if not prior_sls:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Không tìm thấy pivot low M1 có trước điểm sweep để làm mốc phá vỡ CHOCH"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )
            pivot_to_break = prior_sls[-1]

            candidate_key = f"{model_id}_{zone.symbol}_{zone.zone_id}_{direction}_{pivot_to_break['id']}"
            break_level = pivot_to_break["price"]
            bars_after_sweep = [
                b for b in closed_1m
                if (int(getattr(b, "open_time", None) or getattr(b, "timestamp", 0))) >= sweep_ts
            ]
            breaking_bars = [b for b in bars_after_sweep if float(b.close) < break_level]

            if not breaking_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[f"Chưa có nến M1 đóng thủng mức phá vỡ CHOCH ({break_level:.2f})"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            first_break_bar = breaking_bars[0]
            first_break_idx = closed_1m.index(first_break_bar)
            is_break_qual, break_body, break_prefix_atr, break_known_at, fail_reason = qualify_selected_break(
                closed_1m, first_break_idx, "SHORT", cfg, fallback_atr=atr_1m
            )
            if not is_break_qual:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[fail_reason],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            if any(float(b.high) > sweep_price for b in bars_after_sweep):
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Giá đã phá vỡ invalidation level (sweep high)"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            # Retest Verification (SECTIONS 021 - 040)
            sweep_bar = next((b for b in closed_1m if (int(getattr(b, "open_time", None) or getattr(b, "timestamp", 0))) == sweep_ts), None)
            sweep_idx = closed_1m.index(sweep_bar) if sweep_bar in closed_1m else max(0, first_break_idx - 5)

            disp_fvgs = []
            end_k = min(len(closed_1m), first_break_idx + 2)
            for k in range(max(2, sweep_idx + 2), end_k + 1):
                if k >= len(closed_1m):
                    break
                p2 = closed_1m[k - 2]
                curr = closed_1m[k]
                if float(curr.high) < float(p2.low):
                    top = float(p2.low)
                    bot = float(curr.high)
                    if top <= sweep_price:
                        disp_fvgs.append((k, bot, top))

            if not disp_fvgs:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_RETEST.value,
                    missing_conditions=["Chờ xuất hiện Fair Value Gap (FVG) hợp lệ trong nhịp xung lực phá vỡ để retest"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            fvg_confirm_k, fvg_bot, fvg_top = disp_fvgs[-1]
            post_fvg_bars = closed_1m[fvg_confirm_k + 1:]
            if not post_fvg_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_RETEST.value,
                    missing_conditions=[f"Chờ nến M1 hồi về retest FVG [{fvg_bot:.2f} - {fvg_top:.2f}]"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            retest_bars = [
                b for b in post_fvg_bars
                if candle_intersects_price_zone(b, fvg_bot, fvg_top) and float(b.high) <= sweep_price
            ]
            if not retest_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_RETEST.value,
                    missing_conditions=[f"Chờ nến M1 hồi về retest FVG [{fvg_bot:.2f} - {fvg_top:.2f}]"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            sl_price = round(sweep_price + (cfg.sl_buffer_ticks * cfg.tick_size), 2)
            entry_price = round(exec_price, 2)

            # Target Resolution (SECTIONS 081 - 100)
            target_dec = resolve_executable_target(context_levels, direction, entry_price, as_of_ms=now_ms, atr=atr_1m, model_id=model_id)
            if target_dec.status != "CONFIRMED" or target_dec.price is None:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_LIQUIDITY_TARGET.value,
                    missing_conditions=["Chưa có mục tiêu thanh khoản chưa tiêu thụ phù hợp, đang theo dõi"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )
            target_price = target_dec.price

        # Trigger age check (PHẦN 114, SECTIONS 130, 131, 132)
        # Select the FIRST qualifying retest bar for non-refreshing trigger_known_at
        first_retest_bar = retest_bars[0]
        retest_open_ms = int(getattr(first_retest_bar, "open_time", None) or getattr(first_retest_bar, "timestamp", 0))
        trigger_known_at = retest_open_ms + 60_000
        trigger_age_ms = now_ms - trigger_known_at
        if trigger_age_ms > cfg.max_trigger_age_ms:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code=OpportunityReasonCode.TRIGGER_EXPIRED.value,
                missing_conditions=[f"Tín hiệu kích hoạt đã quá hạn ({trigger_age_ms}ms > {cfg.max_trigger_age_ms}ms)"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        # Entry distance check (PHẦN 115, SECTIONS 133, 134)
        entry_reference = float(first_retest_bar.close)
        entry_dist_atr = abs(entry_price - entry_reference) / max(0.50, atr_1m)
        if entry_dist_atr > cfg.max_entry_distance_atr:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code=OpportunityReasonCode.ENTRY_TOO_FAR.value,
                missing_conditions=[f"Khoảng cách vào lệnh quá xa ({entry_dist_atr:.2f} ATR > {cfg.max_entry_distance_atr:.2f} ATR)"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        # V17.5: Stop Placement & Paired Research resolution
        anchor_res = None
        if stop_policy is not None:
            from services.stop_placement_service import resolve_stop_anchor
            anchor_res = resolve_stop_anchor(
                model_id=model_id,
                direction=direction,
                closed_bars=closed_1m,
                as_of_ms=now_ms,
                selected_sweep={
                    "id": f"rev_sweep_{sweep_ts}",
                    "price": sweep_price,
                    "occurred_at_ms": sweep_ts,
                    "known_at_ms": sweep_ts + 60_000,
                },
            )

        sl_price, target_price, calc_res, stop_dec, comp_plan = apply_stop_placement_to_candidate(
            model_id=model_id,
            direction=direction,
            entry_price=entry_price,
            baseline_sl=sl_price,
            baseline_target=target_price,
            closed_1m=closed_1m,
            now_ms=now_ms,
            cfg=cfg,
            policy_settings=policy_settings,
            stop_policy=stop_policy,
            stop_context=stop_context,
            candidate_key=candidate_key,
            anchor_result=anchor_res,
            atr_1m_val=atr_1m,
            bid=bid,
            ask=ask,
        )

        is_geom, geom_err = validate_price_geometry(direction, entry_price, sl_price, target_price)
        if not is_geom:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code="INVALID_GEOMETRY", missing_conditions=[f"Sai hình học giá: {geom_err}"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )

        if not calc_res.is_valid:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                reason_code="CALCULATOR_INVALID",
                missing_conditions=[f"Kế hoạch tính toán không hợp lệ: {calc_res.invalid_reason}"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )

        if not calc_res.can_execute:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                reason_code="CANNOT_EXECUTE",
                missing_conditions=[f"Không đủ điều kiện thực thi: {calc_res.skip_reason}"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )

        if not calc_res.meets_min_rr:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                net_rr=calc_res.net_rr, gross_rr=calc_res.gross_rr, calculation=calc_res,
                reason_code=OpportunityReasonCode.INSUFFICIENT_NET_REWARD.value,
                missing_conditions=[f"Net R:R không đạt ngưỡng tối thiểu ({calc_res.net_rr:.2f} < {cfg.min_net_rr:.2f})"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )

        # Build evidence nodes including retest node with exact causal timestamps (PHẦN 042, 112, Rules 041-044)
        break_bar_open_ms = candle_open_ms(first_break_bar)
        if break_bar_open_ms is None or break_bar_open_ms <= 0:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=["Thiếu thời điểm nến phá vỡ CHOCH hợp lệ"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        fvg_id = f"fvg_{fvg_confirm_k}_{int(fvg_bot)}_{int(fvg_top)}"
        retest_low = float(first_retest_bar.low)
        retest_high = float(first_retest_bar.high)
        inter_bot = max(retest_low, fvg_bot)
        inter_top = min(retest_high, fvg_top)
        ev_nodes = [
            EvidenceNode(
                id=f"ev_zone_{zone.zone_id}", kind="POI", symbol=zone.symbol, timeframe=zone.timeframe,
                direction=direction, occurred_at_ms=zone.created_at, known_at_ms=zone.known_at,
                level=zone.midpoint, extra={"zone_top": zone.top, "zone_bottom": zone.bottom}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_sweep_{sweep_ref.id}",
                kind="SWEEP_LOW" if direction == "LONG" else "SWEEP_HIGH",
                symbol=zone.symbol, timeframe="1M", direction=direction,
                occurred_at_ms=sweep_conf.occurred_at_ms,
                known_at_ms=sweep_conf.known_at_ms,
                level=sweep_conf.breach_price,
                extra={
                    "reference_level_id": sweep_ref.id,
                    "reference_price": sweep_ref.price,
                    "swept_extreme": sweep_conf.breach_price,
                    "reclaim_price": sweep_conf.reclaim_price,
                    "rejection_ratio": sweep_conf.rejection_ratio,
                }
            ).to_dict(),
            EvidenceNode(
                id=f"ev_choch_{break_bar_open_ms}",
                kind="CHOCH", symbol=zone.symbol, timeframe="1M", direction=direction,
                occurred_at_ms=break_bar_open_ms,
                known_at_ms=break_bar_open_ms + 60_000, level=break_level,
                extra={
                    "broken_swing_id": pivot_to_break["id"],
                    "displacement_body": break_body,
                    "displacement_atr": break_prefix_atr,
                }
            ).to_dict(),
            EvidenceNode(
                id=f"ev_retest_{retest_open_ms}",
                kind="RETEST", symbol=zone.symbol, timeframe="1M", direction=direction,
                occurred_at_ms=retest_open_ms,
                known_at_ms=trigger_known_at, level=float(first_retest_bar.low if direction == "LONG" else first_retest_bar.high),
                extra={
                    "fvg_id": fvg_id,
                    "fvg_bounds": [fvg_bot, fvg_top],
                    "intersection_interval": [round(inter_bot, 2), round(inter_top, 2)],
                    "break_level": break_level,
                    "retest_candle_open_ms": retest_open_ms,
                }
            ).to_dict(),
            EvidenceNode(
                id=f"ev_target_{target_dec.source_level_id}",
                kind="TARGET", symbol=zone.symbol, timeframe="1M", direction=direction,
                occurred_at_ms=target_dec.known_at_ms or now_ms,
                known_at_ms=target_dec.known_at_ms or now_ms,
                level=target_price,
                extra={
                    "target_type": target_dec.target_type,
                    "source_level_id": target_dec.source_level_id,
                    "label_vi": target_dec.label_vi,
                }
            ).to_dict(),
        ]

        trigger_chain = {
            "verified": True,
            "reasons": (),
            "node_ids": [n["id"] for n in ev_nodes],
            "model_id": model_id,
            "trigger_known_at": trigger_known_at,
        }

        # Snapshot for chart evidence representation
        snap = ScenarioSnapshot(
            scenario_id=f"scen-{candidate_key}",
            instance_id=candidate_key,
            revision=1,
            symbol=zone.symbol,
            setup_timeframe="1M",
            context_timeframes=["15M", "5M", "1H"],
            family=model_id,
            direction=direction,
            state=ScenarioState.STRUCTURE_CONFIRMED,
            as_of_ms=now_ms,
            evidence_cutoff_ms=now_ms,
            evidence_ids=[n["id"] for n in ev_nodes],
            liquidity_map={"swept": {"price": sweep_price}},
            trigger_chain=trigger_chain,
            target={"price": target_price},
            invalidation={"price": sl_price},
            summary_vi=f"SMC M1 Reversal {direction} tại vùng {zone.timeframe} ({zone.bottom} - {zone.top})",
            config_version=cfg.config_version,
            detector_version="v15.0",
            plan={
                "is_valid": True,
                "entry": entry_price,
                "stop_loss": sl_price,
                "take_profit": target_price,
                "net_rr": calc_res.net_rr,
                "gross_rr": calc_res.gross_rr,
                **({"stop_placement": stop_dec.to_dict() if hasattr(stop_dec, "to_dict") else stop_dec} if stop_dec is not None else {}),
            },
            evidence=ev_nodes,
        )

        return ScalpCandidate(
            is_eligible=True,
            model_id=model_id,
            direction=direction,
            candidate_key=candidate_key,
            symbol=zone.symbol,
            planned_entry=entry_price,
            stop_loss=sl_price,
            take_profit=target_price,
            gross_rr=calc_res.gross_rr,
            net_rr=calc_res.net_rr,
            calculation=calc_res,
            evidence=ev_nodes,
            trigger_chain=trigger_chain,
            snapshot=snap,
            as_of_ms=now_ms,
            zone_id=zone.zone_id,
            stop_placement=stop_dec,
            stop_comparison=comp_plan,
        )

    @staticmethod
    def evaluate_m1_continuation_candidate(
        candles_1m: Sequence[Any],
        candles_5m: Sequence[Any],
        current_quote: Any,
        htf_bias: str,
        h1_alignment: str,
        policy_settings: Dict[str, Any],
        *,
        now_ms: int,
        config: Optional[M1ScalpConfig] = None,
        context_levels: Optional[Sequence[Any]] = None,
        stop_policy: Optional[Any] = None,
        stop_context: Optional[Dict[str, Any]] = None,
    ) -> ScalpCandidate:
        """
        Evaluate M1 Continuation Candidate (PHẦN 051).
        Prerequisites:
        1. Aligned HTF trend context (4H/1H aligned).
        2. Valid quote.
        3. Pullback into dynamic value area followed by M1 BOS in trend direction.
        4. Net R:R >= min_net_rr with valid SL/TP geometry and executable calculator sizing.
        """
        cfg = config or get_scalp_config()
        model_id = ModelIdentifier.SMC_M1_CONTINUATION.value
        fallback_key = f"{model_id}_XAUUSDT"

        # 1. HTF Trend alignment
        if htf_bias not in ("BULLISH", "BEARISH") or h1_alignment not in ("ALIGNED", "BULLISH", "BEARISH"):
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction="UNKNOWN", candidate_key=fallback_key,
                reason_code="HTF_BIAS_NOT_ALIGNED",
                missing_conditions=[f"Xu hướng khung lớn chưa đồng pha ({htf_bias} / {h1_alignment})"],
                as_of_ms=now_ms,
            )

        direction = "LONG" if htf_bias == "BULLISH" else "SHORT"

        # 2. Closed M1 candles audit
        try:
            closed_1m = closed_candles_at(candles_1m, now_ms=now_ms, duration_ms=60_000)
        except ValueError as err:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=[f"Lỗi dữ liệu nến M1: {err}"],
                as_of_ms=now_ms,
            )
        if len(closed_1m) < cfg.min_closed_m1_bars:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=[f"Thiếu nến M1 đã đóng: {len(closed_1m)}/{cfg.min_closed_m1_bars}"],
                as_of_ms=now_ms,
            )

        # 3. Quote freshness check
        bid = float(getattr(current_quote, "bid", 0.0) if hasattr(current_quote, "bid") else (current_quote or {}).get("bid", 0.0))
        ask = float(getattr(current_quote, "ask", 0.0) if hasattr(current_quote, "ask") else (current_quote or {}).get("ask", 0.0))
        is_quote_valid = (
            bool(getattr(current_quote, "is_valid", True))
            and not bool(getattr(current_quote, "is_stale", False))
            and bid > 0
            and ask >= bid
            and math.isfinite(bid)
            and math.isfinite(ask)
        )
        if not is_quote_valid:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                reason_code=OpportunityReasonCode.QUOTE_STALE.value,
                missing_conditions=["Báo giá thị trường không hợp lệ hoặc bị mất kết nối"],
                as_of_ms=now_ms,
            )

        atr_1m = smc_engine.compute_atr(closed_1m, period=cfg.displacement_atr_period)
        if atr_1m <= 0:
            atr_1m = 1.0

        sh_list, sl_list = ScalpM1ModelService.identify_m1_swings(
            closed_1m, cfg.pivot_left_bars, cfg.pivot_right_bars, as_of_ms=now_ms
        )

        exec_price = ask if direction == "LONG" else bid

        if direction == "LONG":
            if not sh_list or not sl_list:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Chưa đủ swing high/low M1 để kiểm tra tiếp diễn"],
                    as_of_ms=now_ms,
                )

            last_sh = sh_list[-1]
            last_sl = sl_list[-1]
            sl_ts = last_sl["occurred_at_ms"]
            bars_after_sl = [
                b for b in closed_1m
                if (int(getattr(b, "open_time", None) or getattr(b, "timestamp", 0))) >= sl_ts
            ]
            breaking_bars = [b for b in bars_after_sl if float(b.close) > last_sh["price"]]
            if not breaking_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[f"Nến M1 chưa đóng phá đỉnh tiếp diễn BOS (> {last_sh['price']:.2f})"],
                    as_of_ms=now_ms,
                )

            break_bar_open = int(getattr(breaking_bars[-1], "open_time", None) or getattr(breaking_bars[-1], "timestamp", 0))
            candidate_key = f"{model_id}_XAUUSDT_{direction}_{break_bar_open}"
            sl_price = round(last_sl["price"] - (cfg.sl_buffer_ticks * cfg.tick_size), 2)
            entry_price = round(exec_price, 2)
            risk_dist = entry_price - sl_price
            target_price = round(entry_price + max(risk_dist * 2.5, 3.0 * atr_1m), 2)

        else:
            if not sh_list or not sl_list:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Chưa đủ swing high/low M1 để kiểm tra tiếp diễn"],
                    as_of_ms=now_ms,
                )

            last_sh = sh_list[-1]
            last_sl = sl_list[-1]
            sh_ts = last_sh["occurred_at_ms"]
            bars_after_sh = [
                b for b in closed_1m
                if (int(getattr(b, "open_time", None) or getattr(b, "timestamp", 0))) >= sh_ts
            ]
            breaking_bars = [b for b in bars_after_sh if float(b.close) < last_sl["price"]]
            if not breaking_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[f"Nến M1 chưa đóng thủng đáy tiếp diễn BOS (< {last_sl['price']:.2f})"],
                    as_of_ms=now_ms,
                )

            break_bar_open = int(getattr(breaking_bars[-1], "open_time", None) or getattr(breaking_bars[-1], "timestamp", 0))
            candidate_key = f"{model_id}_XAUUSDT_{direction}_{break_bar_open}"
            sl_price = round(last_sh["price"] + (cfg.sl_buffer_ticks * cfg.tick_size), 2)
            entry_price = round(exec_price, 2)
            risk_dist = sl_price - entry_price
            target_price = round(entry_price - max(risk_dist * 2.5, 3.0 * atr_1m), 2)

        # V17.5: Stop Placement & Paired Research resolution
        anchor_res = None
        if stop_policy is not None:
            from services.stop_placement_service import resolve_stop_anchor
            anchor_res = resolve_stop_anchor(
                model_id=model_id,
                direction=direction,
                closed_bars=closed_1m,
                as_of_ms=now_ms,
                selected_break={"id": f"bos_{break_bar_open}", "bar": breaking_bars[-1]},
                confirmed_swings=(sh_list, sl_list),
            )

        sl_price, target_price, calc_res, stop_dec, comp_plan = apply_stop_placement_to_candidate(
            model_id=model_id,
            direction=direction,
            entry_price=entry_price,
            baseline_sl=sl_price,
            baseline_target=target_price,
            closed_1m=closed_1m,
            now_ms=now_ms,
            cfg=cfg,
            policy_settings=policy_settings,
            stop_policy=stop_policy,
            stop_context=stop_context,
            candidate_key=candidate_key,
            anchor_result=anchor_res,
            atr_1m_val=atr_1m,
            bid=bid,
            ask=ask,
        )

        is_geom, geom_err = validate_price_geometry(direction, entry_price, sl_price, target_price)
        if not is_geom:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code="INVALID_GEOMETRY", missing_conditions=[f"Sai hình học giá: {geom_err}"],
                as_of_ms=now_ms,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )

        if not calc_res.is_valid:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                reason_code="CALCULATOR_INVALID",
                missing_conditions=[f"Kế hoạch tính toán không hợp lệ: {calc_res.invalid_reason}"],
                as_of_ms=now_ms,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )

        if not calc_res.can_execute:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                reason_code="CANNOT_EXECUTE",
                missing_conditions=[f"Không đủ điều kiện thực thi: {calc_res.skip_reason}"],
                as_of_ms=now_ms,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )

        if not calc_res.meets_min_rr:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                net_rr=calc_res.net_rr, gross_rr=calc_res.gross_rr, calculation=calc_res,
                reason_code=OpportunityReasonCode.INSUFFICIENT_NET_REWARD.value,
                missing_conditions=[f"Net R:R không đạt ngưỡng tối thiểu ({calc_res.net_rr:.2f} < {cfg.min_net_rr:.2f})"],
                as_of_ms=now_ms,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )

        bos_bar = breaking_bars[-1] if breaking_bars else closed_1m[-1]
        bos_bar_open_ms = candle_open_ms(bos_bar) or candle_open_ms(closed_1m[-1])
        if bos_bar_open_ms is None or bos_bar_open_ms <= 0:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=["Thiếu thời điểm nến tiếp diễn BOS hợp lệ"],
                as_of_ms=now_ms,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )

        ev_nodes = [
            EvidenceNode(
                id=f"ev_bos_{bos_bar_open_ms}",
                kind="BOS", symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=bos_bar_open_ms,
                known_at_ms=bos_bar_open_ms + 60_000, level=entry_price,
                extra={"htf_bias": htf_bias, "h1_alignment": h1_alignment}
            ).to_dict(),
        ]

        trigger_chain = {
            "verified": True,
            "reasons": (),
            "node_ids": [n["id"] for n in ev_nodes],
            "model_id": model_id,
        }

        snap = ScenarioSnapshot(
            scenario_id=f"scen-{candidate_key}",
            instance_id=candidate_key,
            revision=1,
            symbol="XAUUSDT",
            setup_timeframe="1M",
            context_timeframes=["15M", "5M", "1H"],
            family=model_id,
            direction=direction,
            state=ScenarioState.STRUCTURE_CONFIRMED,
            as_of_ms=now_ms,
            evidence_cutoff_ms=now_ms,
            evidence_ids=[n["id"] for n in ev_nodes],
            liquidity_map={},
            trigger_chain=trigger_chain,
            target={"price": target_price},
            invalidation={"price": sl_price},
            summary_vi=f"SMC M1 Continuation {direction} đồng pha HTF ({htf_bias})",
            config_version=cfg.config_version,
            detector_version="v15.0",
            plan={
                "is_valid": True,
                "entry": entry_price,
                "stop_loss": sl_price,
                "take_profit": target_price,
                "net_rr": calc_res.net_rr,
                "gross_rr": calc_res.gross_rr,
                "stop_placement": stop_dec.to_dict() if stop_dec else None,
            },
            evidence=ev_nodes,
        )

        return ScalpCandidate(
            is_eligible=True,
            model_id=model_id,
            direction=direction,
            candidate_key=candidate_key,
            symbol="XAUUSDT",
            planned_entry=entry_price,
            stop_loss=sl_price,
            take_profit=target_price,
            gross_rr=calc_res.gross_rr,
            net_rr=calc_res.net_rr,
            calculation=calc_res,
            evidence=ev_nodes,
            trigger_chain=trigger_chain,
            snapshot=snap,
            as_of_ms=now_ms,
            stop_placement=stop_dec,
            stop_comparison=comp_plan,
        )

    @staticmethod
    def evaluate_m1_fvg_continuation_candidate(
        closed_1m: Sequence[Any],
        closed_5m: Sequence[Any],
        quote: Any,
        htf_bias: str,
        h1_alignment: str,
        policy_settings: Dict[str, Any],
        now_ms: int,
        config: Optional[M1ScalpConfig] = None,
        context_levels: Optional[Sequence[Any]] = None,
        stop_policy: Optional[Any] = None,
        stop_context: Optional[Dict[str, Any]] = None,
    ) -> ScalpCandidate:
        """
        V16.1 SMC M1 FVG Continuation Model (Rules 046 - 060).
        Evaluates trend-aligned confirmed break of structural reference,
        causal displacement leg, real FVG binding, and closed retest.
        """
        cfg = config if config is not None else get_scalp_config()
        model_id = ModelIdentifier.SMC_M1_FVG_CONTINUATION.value

        if not getattr(cfg, "fvg_continuation_enabled", True):
            return ScalpCandidate(is_eligible=False, model_id=model_id, reason_code="MODEL_DISABLED", as_of_ms=now_ms)

        if len(closed_1m) < cfg.min_closed_m1_bars:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=[f"Chưa đủ nến M1 đã đóng ({len(closed_1m)}/{cfg.min_closed_m1_bars})"],
                as_of_ms=now_ms,
            )

        # 1. Trend alignment & Conflict handling (Rule 047)
        if (htf_bias == "BULLISH" and h1_alignment == "BEARISH") or (htf_bias == "BEARISH" and h1_alignment == "BULLISH"):
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, candidate_key=f"{model_id}_{now_ms}",
                reason_code="HTF_H1_CONFLICT",
                missing_conditions=["Xung đột xu hướng giữa khung thời gian lớn HTF và H1"],
                as_of_ms=now_ms,
            )

        if htf_bias == "BULLISH" or (htf_bias == "UNKNOWN" and h1_alignment == "BULLISH"):
            direction = "LONG"
        elif htf_bias == "BEARISH" or (htf_bias == "UNKNOWN" and h1_alignment == "BEARISH"):
            direction = "SHORT"
        else:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, candidate_key=f"{model_id}_{now_ms}",
                reason_code="HTF_BIAS_NEUTRAL",
                missing_conditions=["HTF không có xu hướng rõ ràng cho mô hình tiếp diễn"],
                as_of_ms=now_ms,
            )

        fallback_key = f"{model_id}_{direction}_{now_ms}"

        # 2. Quote sanity (Rule 036)
        if quote is None:
            return ScalpCandidate(is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key, reason_code="QUOTE_UNAVAILABLE", as_of_ms=now_ms)

        bid = float(getattr(quote, "bid", 0.0) or (quote.get("bid", 0.0) if isinstance(quote, dict) else 0.0))
        ask = float(getattr(quote, "ask", 0.0) or (quote.get("ask", 0.0) if isinstance(quote, dict) else 0.0))
        if bid <= 0.0 or ask <= 0.0 or ask < bid:
            return ScalpCandidate(is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key, reason_code="QUOTE_INVALID", as_of_ms=now_ms)

        exec_price = ask if direction == "LONG" else bid
        atr_1m = smc_engine.compute_atr(closed_1m, period=cfg.displacement_atr_period) or 1.0

        # 3. Confirmed structure reference (Rule 048)
        swing_ref = resolve_confirmed_structure_reference(
            closed_1m, direction, as_of_ms=now_ms,
            left_bars=cfg.pivot_left_bars, right_bars=cfg.pivot_right_bars,
        )
        sh_list, sl_list = identify_confirmed_swings(
            closed_1m, left_bars=cfg.pivot_left_bars, right_bars=cfg.pivot_right_bars, as_of_ms=now_ms
        )
        if swing_ref is None:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                missing_conditions=["Chưa xác định được đỉnh/đáy cấu trúc M1 tham chiếu"],
                as_of_ms=now_ms,
            )

        # 4. Confirmed continuation break (Rule 049)
        break_res = detect_confirmed_continuation_break(closed_1m, swing_ref, direction, as_of_ms=now_ms, tick_size=cfg.tick_size)
        if break_res is None:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                reason_code="M1_BREAK_MISSING",
                missing_conditions=["Chưa có nến M1 đóng phá vỡ đỉnh/đáy cấu trúc tham chiếu (Break/BOS)"],
                as_of_ms=now_ms,
            )

        # 5. Causal displacement evaluation (Rules 050-051)
        is_disp, disp_ratio, disp_info = evaluate_displacement(
            break_res["bar"], atr_1m, min_body_atr=cfg.displacement_body_atr_min, direction=direction,
        )
        if not is_disp:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                reason_code="DISPLACEMENT_WEAK",
                missing_conditions=[f"Nến phá vỡ không đủ biên độ displacement ({disp_ratio:.2f} < {cfg.displacement_body_atr_min:.2f} ATR)"],
                as_of_ms=now_ms,
            )

        # 6. Detect real FVGs tied to the break/displacement leg (Rules 052-053)
        raw_fvgs = smc_engine.detect_fvgs(list(closed_1m), timeframe="1M")
        target_fvg_type = "BULLISH_FVG" if direction == "LONG" else "BEARISH_FVG"
        matching_fvgs = [
            f for f in raw_fvgs
            if f.get("type") == target_fvg_type
            and f.get("state") in ("confirmed", "partially_mitigated")
            and float(f.get("bottom", 0.0)) < float(f.get("top", 0.0))
            and int(f.get("confirmed_at") or f.get("timestamp") or 0) >= break_res["broken_at_ms"] - 60_000
        ]

        if not matching_fvgs:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                missing_conditions=["Chưa phát hiện FVG M1 hợp lệ gắn liền với nhịp phá vỡ cấu trúc"],
                as_of_ms=now_ms,
            )

        # Select closest FVG to current execution price
        selected_fvg = min(matching_fvgs, key=lambda f: abs(exec_price - ((float(f["top"]) + float(f["bottom"])) / 2.0)))
        fvg_top = float(selected_fvg["top"])
        fvg_bot = float(selected_fvg["bottom"])
        fvg_mid = round((fvg_top + fvg_bot) / 2.0, 2)
        fvg_created_ms = int(selected_fvg.get("confirmed_at") or selected_fvg.get("timestamp") or 0)
        fvg_id = selected_fvg.get("id", f"fvg_cont_{int(fvg_bot)}_{int(fvg_top)}")

        # 7. Check FVG invalidation (Rule 057)
        post_creation_bars = [b for b in closed_1m if (candle_open_ms(b) or 0) >= fvg_created_ms]
        if direction == "LONG":
            if any(float(b.close) < fvg_bot for b in post_creation_bars):
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code="FVG_INVALIDATED",
                    missing_conditions=["Vùng FVG tiếp diễn đã bị nến đóng xuyên qua mức vô hiệu"],
                    as_of_ms=now_ms,
                )
        else:
            if any(float(b.close) > fvg_top for b in post_creation_bars):
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code="FVG_INVALIDATED",
                    missing_conditions=["Vùng FVG tiếp diễn đã bị nến đóng xuyên qua mức vô hiệu"],
                    as_of_ms=now_ms,
                )

        # 8. Check for closed retest into FVG (Rules 054-055)
        retest_bars = [
            b for b in post_creation_bars
            if candle_intersects_price_zone(b, fvg_bot, fvg_top)
        ]
        if not retest_bars:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                reason_code=OpportunityReasonCode.WAITING_M1_RETEST.value,
                missing_conditions=[f"Chờ nến M1 hồi về retest FVG tiếp diễn [{fvg_bot:.2f} - {fvg_top:.2f}]"],
                as_of_ms=now_ms,
            )

        first_retest_bar = retest_bars[0]
        retest_open_ms = candle_open_ms(first_retest_bar) or 0
        trigger_known_at = retest_open_ms + 60_000
        trigger_age_ms = now_ms - trigger_known_at

        # Rule 056: Future candle check & trigger staleness
        if trigger_age_ms < 0:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                reason_code="FUTURE_TRIGGER",
                missing_conditions=["Tín hiệu retest thuộc nến chưa hoàn tất tại thời điểm now_ms"],
                as_of_ms=now_ms,
            )
        if trigger_age_ms > cfg.max_trigger_age_ms:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                reason_code=OpportunityReasonCode.TRIGGER_EXPIRED.value,
                missing_conditions=[f"Tín hiệu kích hoạt FVG đã quá hạn ({trigger_age_ms}ms > {cfg.max_trigger_age_ms}ms)"],
                as_of_ms=now_ms,
            )

        # 9. Pricing geometry & Target (Rules 058-059, 147-148)
        entry_price = round(exec_price, 2)
        if direction == "LONG":
            sl_price = round(fvg_bot - (cfg.sl_buffer_ticks * cfg.tick_size), 2)
        else:
            sl_price = round(fvg_top + (cfg.sl_buffer_ticks * cfg.tick_size), 2)

        target_dec = resolve_executable_target(context_levels or [], direction, entry_price, as_of_ms=now_ms, atr=atr_1m, model_id=model_id)
        if target_dec.status != "CONFIRMED" or target_dec.price is None:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                reason_code=OpportunityReasonCode.WAITING_LIQUIDITY_TARGET.value,
                missing_conditions=["Chưa có mục tiêu thanh khoản chưa tiêu thụ phù hợp"],
                as_of_ms=now_ms,
            )
        target_price = target_dec.price

        # Directional target check
        if direction == "LONG" and target_price <= entry_price:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                reason_code="INVALID_GEOMETRY", missing_conditions=["Mục tiêu thanh khoản phải lớn hơn giá vào cho lệnh LONG"],
                as_of_ms=now_ms,
            )
        if direction == "SHORT" and target_price >= entry_price:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                reason_code="INVALID_GEOMETRY", missing_conditions=["Mục tiêu thanh khoản phải nhỏ hơn giá vào cho lệnh SHORT"],
                as_of_ms=now_ms,
            )

        # V17.5: Stop Placement & Paired Research resolution
        candidate_key = f"fvg_cont_{direction}_{fvg_id}_{retest_open_ms}"
        anchor_res = None
        if stop_policy is not None:
            from services.stop_placement_service import resolve_stop_anchor
            anchor_res = resolve_stop_anchor(
                model_id=model_id,
                direction=direction,
                closed_bars=closed_1m,
                as_of_ms=now_ms,
                selected_break={"id": f"break_{break_res['broken_at_ms']}", "bar": break_res.get("bar")},
                selected_fvg={"id": fvg_id, "top": fvg_top, "bottom": fvg_bot, "start_ms": selected_fvg.get("start_ms")},
                confirmed_swings=(sh_list, sl_list),
            )

        sl_price, target_price, calc_res, stop_dec, comp_plan = apply_stop_placement_to_candidate(
            model_id=model_id,
            direction=direction,
            entry_price=entry_price,
            baseline_sl=sl_price,
            baseline_target=target_price,
            closed_1m=closed_1m,
            now_ms=now_ms,
            cfg=cfg,
            policy_settings=policy_settings,
            stop_policy=stop_policy,
            stop_context=stop_context,
            candidate_key=candidate_key,
            anchor_result=anchor_res,
            atr_1m_val=atr_1m,
            bid=bid,
            ask=ask,
        )

        is_geom, geom_err = validate_price_geometry(direction, entry_price, sl_price, target_price)
        if not is_geom:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code="INVALID_GEOMETRY", missing_conditions=[f"Sai hình học giá: {geom_err}"],
                as_of_ms=now_ms,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )

        if not calc_res.is_valid:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                reason_code="CALCULATOR_INVALID",
                missing_conditions=[f"Kế hoạch tính toán không hợp lệ: {calc_res.invalid_reason}"],
                as_of_ms=now_ms,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )
        if not calc_res.can_execute:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                reason_code="CANNOT_EXECUTE",
                missing_conditions=[f"Không đủ điều kiện thực thi: {calc_res.skip_reason}"],
                as_of_ms=now_ms,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )
        if not calc_res.meets_min_rr:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                net_rr=calc_res.net_rr, gross_rr=calc_res.gross_rr, calculation=calc_res,
                reason_code=OpportunityReasonCode.INSUFFICIENT_NET_REWARD.value,
                missing_conditions=[f"Net R:R không đạt ngưỡng tối thiểu ({calc_res.net_rr:.2f} < {cfg.min_net_rr:.2f})"],
                as_of_ms=now_ms,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )

        # 11. Full causal evidence chain (Rule 060)
        ev_nodes = [
            EvidenceNode(
                id=f"ev_context_{direction}_{now_ms}",
                kind="CONTEXT", symbol="XAUUSDT", timeframe="1H", direction=direction,
                occurred_at_ms=now_ms - 3600_000, known_at_ms=now_ms,
                level=entry_price, extra={"htf_bias": htf_bias, "h1_alignment": h1_alignment}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_struct_{swing_ref.get('id', 'ref')}",
                kind="STRUCTURE_REFERENCE", symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=swing_ref["occurred_at_ms"], known_at_ms=swing_ref["known_at_ms"],
                level=swing_ref["price"], extra={"swing_side": swing_ref.get("side")}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_break_{break_res['broken_at_ms']}",
                kind="BREAK", symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=break_res["broken_at_ms"], known_at_ms=break_res["known_at_ms"],
                level=break_res["break_price"], extra={"excess": break_res["excess"]}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_disp_{break_res['broken_at_ms']}",
                kind="DISPLACEMENT", symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=break_res["broken_at_ms"], known_at_ms=break_res["known_at_ms"],
                level=break_res["break_price"], extra=disp_info
            ).to_dict(),
            EvidenceNode(
                id=f"ev_fvg_{fvg_id}",
                kind="FVG", symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=fvg_created_ms, known_at_ms=fvg_created_ms + 60_000,
                level=fvg_mid, extra={"fvg_top": fvg_top, "fvg_bottom": fvg_bot, "displacement_leg_ms": break_res["broken_at_ms"]}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_retest_{retest_open_ms}",
                kind="RETEST", symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=retest_open_ms, known_at_ms=trigger_known_at,
                level=entry_price, extra={"retest_candle_open_ms": retest_open_ms}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_inval_{sl_price:.2f}",
                kind="INVALIDATION", symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=fvg_created_ms, known_at_ms=fvg_created_ms + 60_000,
                level=sl_price, extra={"buffer_ticks": cfg.sl_buffer_ticks}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_target_{target_dec.source_level_id}",
                kind="TARGET", symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=target_dec.known_at_ms or now_ms, known_at_ms=target_dec.known_at_ms or now_ms,
                level=target_price, extra={"target_type": target_dec.target_type, "label_vi": target_dec.label_vi}
            ).to_dict(),
        ]

        tl_ok, tl_issues = validate_evidence_timeline(ev_nodes, now_ms)
        trigger_chain = {
            "verified": tl_ok,
            "reasons": tuple(tl_issues),
            "node_ids": [n["id"] for n in ev_nodes],
            "model_id": model_id,
            "trigger_known_at": trigger_known_at,
        }

        snap = ScenarioSnapshot(
            scenario_id=f"scen-{candidate_key}",
            instance_id=candidate_key,
            revision=1,
            symbol="XAUUSDT",
            setup_timeframe="1M",
            context_timeframes=["15M", "5M", "1H"],
            family=model_id,
            direction=direction,
            state=ScenarioState.STRUCTURE_CONFIRMED,
            as_of_ms=now_ms,
            evidence_cutoff_ms=now_ms,
            evidence_ids=[n["id"] for n in ev_nodes],
            liquidity_map={},
            trigger_chain=trigger_chain,
            target={"price": target_price},
            invalidation={"price": sl_price},
            summary_vi=f"SMC M1 FVG Continuation {direction} [{fvg_bot:.2f} - {fvg_top:.2f}]",
            config_version=cfg.config_version,
            detector_version="v16.1",
            plan={
                "is_valid": True,
                "entry": entry_price,
                "stop_loss": sl_price,
                "take_profit": target_price,
                "net_rr": calc_res.net_rr,
                "gross_rr": calc_res.gross_rr,
                **({"stop_placement": stop_dec.to_dict() if hasattr(stop_dec, "to_dict") else stop_dec} if stop_dec is not None else {}),
            },
            evidence=ev_nodes,
        )

        return ScalpCandidate(
            is_eligible=True,
            model_id=model_id,
            direction=direction,
            candidate_key=candidate_key,
            symbol="XAUUSDT",
            planned_entry=entry_price,
            stop_loss=sl_price,
            take_profit=target_price,
            gross_rr=calc_res.gross_rr,
            net_rr=calc_res.net_rr,
            calculation=calc_res,
            evidence=ev_nodes,
            trigger_chain=trigger_chain,
            snapshot=snap,
            as_of_ms=now_ms,
            origin_episode_id=f"ep_fvg_{direction}_{fvg_id}_{retest_open_ms}",
            evidence_ids=tuple(n["id"] for n in ev_nodes),
            valid_until_ms=now_ms + cfg.max_trigger_age_ms,
            model_version="v16.1",
            stop_placement=stop_dec,
            stop_comparison=comp_plan,
        )

    @staticmethod
    def evaluate_m1_sweep_reclaim_candidate(
        closed_1m: Sequence[Any] = (),
        cluster: Any = None,
        quote: Any = None,
        policy_settings: Optional[Dict[str, Any]] = None,
        now_ms: int = 0,
        config: Optional[M1ScalpConfig] = None,
        context_levels: Optional[Sequence[Any]] = None,
        **kwargs: Any,
    ) -> ScalpCandidate:
        """
        V16.1 SMC M1 Sweep & Reclaim Model (Rules 061 - 074).
        Triggers on confirmed sweep of a liquidity cluster, closed reclaim,
        and mandatory micro-structure break.
        Does NOT require or fabricate an OB or FVG.
        """
        if not isinstance(closed_1m, (list, tuple)) and isinstance(cluster, (list, tuple)):
            closed_1m, cluster = cluster, closed_1m

        cfg = config if config is not None else get_scalp_config()
        model_id = ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value
        policy_dict = policy_settings or {}

        fallback_key = f"{model_id}_{getattr(cluster, 'id', 'cid')}_{now_ms}"

        if not getattr(cfg, "sweep_reclaim_enabled", True):
            return ScalpCandidate(is_eligible=False, model_id=model_id, candidate_key=fallback_key, reason_code="MODEL_DISABLED", as_of_ms=now_ms)

        # 1. Cluster validation (Rule 062)
        if cluster is None:
            return ScalpCandidate(is_eligible=False, model_id=model_id, candidate_key=fallback_key, reason_code="CLUSTER_INVALID", as_of_ms=now_ms)

        c_side = getattr(cluster, "side", "")
        if c_side not in ("BUY_SIDE", "SELL_SIDE"):
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, candidate_key=fallback_key,
                reason_code="CLUSTER_INVALID",
                missing_conditions=["Phía thanh khoản cluster không hợp lệ (phải là BUY_SIDE hoặc SELL_SIDE)"],
                as_of_ms=now_ms,
            )

        c_state = getattr(cluster, "state", "")
        if c_state == "ACCEPTED_BEYOND":
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, candidate_key=fallback_key,
                reason_code="ACCEPTED_BEYOND",
                missing_conditions=["Cụm thanh khoản đã bị thị trường chấp nhận phía ngoài (ACCEPTED_BEYOND), không đủ điều kiện đảo chiều"],
                as_of_ms=now_ms,
            )
        if c_state in ("INVALIDATED", "CONSUMED"):
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, candidate_key=fallback_key,
                reason_code="CLUSTER_INVALID",
                missing_conditions=[f"Cụm thanh khoản ở trạng thái {c_state}"],
                as_of_ms=now_ms,
            )

        try:
            c_bot = float(cluster.bottom)
            c_top = float(cluster.top)
        except (ValueError, TypeError, AttributeError):
            return ScalpCandidate(is_eligible=False, model_id=model_id, candidate_key=fallback_key, reason_code="CLUSTER_INVALID", as_of_ms=now_ms)

        if not (math.isfinite(c_bot) and math.isfinite(c_top)) or c_bot >= c_top:
            return ScalpCandidate(is_eligible=False, model_id=model_id, candidate_key=fallback_key, reason_code="CLUSTER_INVALID", as_of_ms=now_ms)

        cluster_known_at = int(getattr(cluster, "known_at_ms", 0) or 0)
        if cluster_known_at > now_ms:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, candidate_key=fallback_key,
                reason_code="FUTURE_CLUSTER",
                missing_conditions=["Cụm thanh khoản có thời điểm xác nhận lớn hơn now_ms"],
                as_of_ms=now_ms,
            )

        # 2. Direction: sweep of SELL_SIDE (lows) -> LONG; sweep of BUY_SIDE (highs) -> SHORT (Rule 063)
        direction = "LONG" if c_side == "SELL_SIDE" else "SHORT"
        cand_key = f"sweep_rec_{direction}_{getattr(cluster, 'id', 'cid')}_{now_ms}"

        if len(closed_1m) < 5:
            return ScalpCandidate(is_eligible=False, model_id=model_id, direction=direction, candidate_key=cand_key, reason_code="DATA_INCOMPLETE", as_of_ms=now_ms)

        # 3. Quote sanity (Rule 036)
        if quote is None:
            return ScalpCandidate(is_eligible=False, model_id=model_id, direction=direction, candidate_key=cand_key, reason_code="QUOTE_UNAVAILABLE", as_of_ms=now_ms)
        bid = float(getattr(quote, "bid", 0.0) or (quote.get("bid", 0.0) if isinstance(quote, dict) else 0.0))
        ask = float(getattr(quote, "ask", 0.0) or (quote.get("ask", 0.0) if isinstance(quote, dict) else 0.0))
        if bid <= 0.0 or ask <= 0.0 or ask < bid:
            return ScalpCandidate(is_eligible=False, model_id=model_id, direction=direction, candidate_key=cand_key, reason_code="QUOTE_INVALID", as_of_ms=now_ms)

        exec_price = ask if direction == "LONG" else bid
        atr_1m = smc_engine.compute_atr(closed_1m, period=cfg.displacement_atr_period) or 1.0

        # 4. Detect Sweep event (Rule 064-065)
        sweep_event = detect_cluster_sweep_event(closed_1m, cluster, direction)
        if sweep_event is None:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                candidate_key=cand_key,
                reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                missing_conditions=["Chờ xác nhận nến M1 quét qua biên của cụm thanh khoản"],
                as_of_ms=now_ms,
            )

        swept_extreme = sweep_event["swept_extreme"]
        swept_bar = sweep_event["bar"]
        swept_open_ms = sweep_event["swept_at_ms"]

        # Causal filter: sweep must not have occurred before cluster was known
        if swept_open_ms + 60_000 < cluster_known_at:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                candidate_key=cand_key,
                reason_code="SWEEP_BEFORE_CLUSTER_KNOWN",
                missing_conditions=["Lần quét xảy ra trước khi cụm thanh khoản được xác nhận hợp lệ"],
                as_of_ms=now_ms,
            )

        # 5. Detect Reclaim event (Rule 066)
        reclaim_event = detect_cluster_reclaim_event(closed_1m, cluster, sweep_event, direction)
        if reclaim_event is None:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                candidate_key=cand_key,
                reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                missing_conditions=["Chờ xác nhận nến M1 đóng quay trở lại vùng sau khi quét"],
                as_of_ms=now_ms,
            )

        reclaim_bar = reclaim_event["bar"]
        reclaim_open_ms = reclaim_event["reclaimed_at_ms"]
        trigger_known_at = reclaim_open_ms + 60_000
        trigger_age_ms = now_ms - trigger_known_at

        # 6. Mandatory Micro-Break (Rules 067-068)
        micro_break_event = detect_post_reclaim_micro_break(
            closed_1m, sweep_event, reclaim_event, direction, as_of_ms=now_ms,
            tick_size=getattr(cfg, "tick_size", 0.01),
            allow_sweep_candle_fallback=True,
        )
        if micro_break_event is None:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                candidate_key=cand_key,
                reason_code="M1_MICRO_BREAK_MISSING",
                missing_conditions=["Giá đã quay lại vùng nhưng chưa xác nhận phá cấu trúc nhỏ."],
                as_of_ms=now_ms,
            )

        # 7. Stable Episode ID (Rule 072)
        episode_id = make_sweep_episode_id(
            getattr(cluster, "id", "cid"), direction, swept_extreme, swept_open_ms
        )

        # Rule 056: Future candle check & trigger staleness
        if trigger_age_ms < 0:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                reason_code="FUTURE_TRIGGER",
                missing_conditions=["Tín hiệu retest/reclaim thuộc nến chưa hoàn tất tại thời điểm now_ms"],
                as_of_ms=now_ms,
            )
        if trigger_age_ms > cfg.max_trigger_age_ms:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                reason_code=OpportunityReasonCode.TRIGGER_EXPIRED.value,
                missing_conditions=[f"Tín hiệu kích hoạt quét & reclaim đã quá hạn ({trigger_age_ms}ms > {cfg.max_trigger_age_ms}ms)"],
                as_of_ms=now_ms,
            )

        # 8. Structural Pricing & Target (Rules 070, 147-148)
        entry_price = round(exec_price, 2)
        if direction == "LONG":
            sl_price = round(swept_extreme - (cfg.sl_buffer_ticks * cfg.tick_size), 2)
        else:
            sl_price = round(swept_extreme + (cfg.sl_buffer_ticks * cfg.tick_size), 2)

        target_dec = resolve_executable_target(context_levels or [], direction, entry_price, as_of_ms=now_ms, atr=atr_1m, model_id=model_id)
        if target_dec.status != "CONFIRMED" or target_dec.price is None:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                reason_code=OpportunityReasonCode.WAITING_LIQUIDITY_TARGET.value,
                missing_conditions=["Chưa có mục tiêu thanh khoản phù hợp cho mô hình Sweep Reclaim"],
                as_of_ms=now_ms,
            )
        target_price = target_dec.price

        # Directional target check
        if direction == "LONG" and target_price <= entry_price:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                reason_code="INVALID_GEOMETRY", missing_conditions=["Mục tiêu thanh khoản phải lớn hơn giá vào cho lệnh LONG"],
                as_of_ms=now_ms,
            )
        if direction == "SHORT" and target_price >= entry_price:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction,
                reason_code="INVALID_GEOMETRY", missing_conditions=["Mục tiêu thanh khoản phải nhỏ hơn giá vào cho lệnh SHORT"],
                as_of_ms=now_ms,
            )

        # V17.5: Stop Placement & Paired Research resolution
        candidate_key = f"sweep_rec_{direction}_{getattr(cluster, 'id', 'cid')}_{reclaim_open_ms}"
        stop_policy = kwargs.get("stop_policy")
        stop_context = kwargs.get("stop_context")
        anchor_res = None
        if stop_policy is not None:
            from services.stop_placement_service import resolve_stop_anchor
            anchor_res = resolve_stop_anchor(
                model_id=model_id,
                direction=direction,
                closed_bars=closed_1m,
                as_of_ms=now_ms,
                selected_sweep={
                    "id": f"sweep_{swept_open_ms}",
                    "price": swept_extreme,
                    "occurred_at_ms": swept_open_ms,
                    "known_at_ms": reclaim_open_ms,
                },
            )

        sl_price, target_price, calc_res, stop_dec, comp_plan = apply_stop_placement_to_candidate(
            model_id=model_id,
            direction=direction,
            entry_price=entry_price,
            baseline_sl=sl_price,
            baseline_target=target_price,
            closed_1m=closed_1m,
            now_ms=now_ms,
            cfg=cfg,
            policy_settings=policy_dict,
            stop_policy=stop_policy,
            stop_context=stop_context,
            candidate_key=candidate_key,
            anchor_result=anchor_res,
            atr_1m_val=atr_1m,
            bid=bid,
            ask=ask,
        )

        is_geom, geom_err = validate_price_geometry(direction, entry_price, sl_price, target_price)
        if not is_geom:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code="INVALID_GEOMETRY", missing_conditions=[f"Sai hình học giá: {geom_err}"],
                as_of_ms=now_ms,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )

        if not calc_res.is_valid:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                reason_code="CALCULATOR_INVALID",
                missing_conditions=[f"Kế hoạch tính toán không hợp lệ: {calc_res.invalid_reason}"],
                as_of_ms=now_ms,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )
        if not calc_res.can_execute:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                reason_code="CANNOT_EXECUTE",
                missing_conditions=[f"Không đủ điều kiện thực thi: {calc_res.skip_reason}"],
                as_of_ms=now_ms,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )
        if not calc_res.meets_min_rr:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                net_rr=calc_res.net_rr, gross_rr=calc_res.gross_rr, calculation=calc_res,
                reason_code=OpportunityReasonCode.INSUFFICIENT_NET_REWARD.value,
                missing_conditions=[f"Net R:R không đạt ngưỡng tối thiểu ({calc_res.net_rr:.2f} < {cfg.min_net_rr:.2f})"],
                as_of_ms=now_ms,
                stop_placement=stop_dec,
                stop_comparison=comp_plan,
            )

        # 10. Causal Evidence Chain (Rule 071)
        ev_nodes = [
            EvidenceNode(
                id=f"ev_cluster_{getattr(cluster, 'id', 'cid')}",
                kind="LIQUIDITY_CLUSTER", symbol="XAUUSDT", timeframe="15M", direction=direction,
                occurred_at_ms=cluster.known_at_ms, known_at_ms=cluster.known_at_ms,
                level=cluster.price_mid, extra={"bottom": c_bot, "top": c_top, "cluster_type": cluster.cluster_type}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_sweep_{swept_extreme:.2f}_{swept_open_ms}",
                kind="SWEEP_LOW" if direction == "LONG" else "SWEEP_HIGH",
                symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=swept_open_ms, known_at_ms=swept_open_ms + 60_000, level=swept_extreme,
                extra={"swept_level": c_bot if direction == "LONG" else c_top, "extreme": swept_extreme}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_reclaim_{reclaim_open_ms}",
                kind="RECLAIM", symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=reclaim_open_ms, known_at_ms=trigger_known_at,
                level=float(reclaim_bar.close), extra={"reclaim_price": float(reclaim_bar.close)}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_micro_break_{micro_break_event['broken_at_ms']}",
                kind="MICRO_BREAK", symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=micro_break_event["broken_at_ms"], known_at_ms=micro_break_event["broken_at_ms"] + 60_000,
                level=micro_break_event["break_price"], extra={"micro_swing_price": micro_break_event["micro_swing_price"]}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_inval_{sl_price:.2f}",
                kind="INVALIDATION", symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=swept_open_ms, known_at_ms=swept_open_ms + 60_000,
                level=sl_price, extra={"buffer_ticks": cfg.sl_buffer_ticks}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_target_{target_dec.source_level_id}",
                kind="TARGET", symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=target_dec.known_at_ms or now_ms, known_at_ms=target_dec.known_at_ms or now_ms,
                level=target_price, extra={"target_type": target_dec.target_type, "label_vi": target_dec.label_vi}
            ).to_dict(),
        ]

        tl_ok, tl_issues = validate_evidence_timeline(ev_nodes, now_ms)
        trigger_chain = {
            "verified": tl_ok,
            "reasons": tuple(tl_issues),
            "node_ids": [n["id"] for n in ev_nodes],
            "model_id": model_id,
            "trigger_known_at": trigger_known_at,
        }

        snap = ScenarioSnapshot(
            scenario_id=f"scen-{candidate_key}",
            instance_id=candidate_key,
            revision=1,
            symbol="XAUUSDT",
            setup_timeframe="1M",
            context_timeframes=["15M", "5M", "1H"],
            family=model_id,
            direction=direction,
            state=ScenarioState.STRUCTURE_CONFIRMED,
            as_of_ms=now_ms,
            evidence_cutoff_ms=now_ms,
            evidence_ids=[n["id"] for n in ev_nodes],
            liquidity_map={},
            trigger_chain=trigger_chain,
            target={"price": target_price},
            invalidation={"price": sl_price},
            summary_vi=f"SMC M1 Sweep Reclaim {direction} [{c_bot:.2f} - {c_top:.2f}]",
            config_version=cfg.config_version,
            detector_version="v16.1",
            plan={
                "is_valid": True,
                "entry": entry_price,
                "stop_loss": sl_price,
                "take_profit": target_price,
                "net_rr": calc_res.net_rr,
                "gross_rr": calc_res.gross_rr,
                **({"stop_placement": stop_dec.to_dict() if hasattr(stop_dec, "to_dict") else stop_dec} if stop_dec is not None else {}),
            },
            evidence=ev_nodes,
        )

        return ScalpCandidate(
            is_eligible=True,
            model_id=model_id,
            direction=direction,
            candidate_key=candidate_key,
            symbol="XAUUSDT",
            planned_entry=entry_price,
            stop_loss=sl_price,
            take_profit=target_price,
            gross_rr=calc_res.gross_rr,
            net_rr=calc_res.net_rr,
            calculation=calc_res,
            evidence=ev_nodes,
            trigger_chain=trigger_chain,
            snapshot=snap,
            as_of_ms=now_ms,
            cluster_id=getattr(cluster, "id", None),
            origin_episode_id=episode_id,
            evidence_ids=tuple(n["id"] for n in ev_nodes),
            valid_until_ms=now_ms + cfg.max_trigger_age_ms,
            model_version="v16.1",
            stop_placement=stop_dec,
            stop_comparison=comp_plan,
        )


def evaluate_smc_opportunity_candidates(
    candidates: List[ScalpCandidate],
    htf_bias: str = "UNKNOWN",
    cg01_policy: Optional[str] = None,
) -> List[ScalpCandidate]:
    """
    V16 Candidate Router & Deduplication (Rules 093 - 096, 131 - 134).
    - Deduplicates redundant candidates targeting the same direction and underlying level.
    - Resolves opposing conflicting signals deterministically:
      prefers candidate aligned with htf_bias; if tied, prefers higher net R:R.
    """
    if not candidates:
        return []

    eligible = [c for c in candidates if c.is_eligible]
    if len(eligible) <= 1:
        return eligible

    # Deduplicate same-direction candidates within close price proximity (e.g. within 1.0$)
    deduped: List[ScalpCandidate] = []
    seen_buckets: set = set()

    # Sort candidates by Net R:R descending
    sorted_cand = sorted(eligible, key=lambda c: getattr(c, "net_rr", 0.0), reverse=True)
    for c in sorted_cand:
        p_bucket = round(c.planned_entry / 2.0) * 2.0
        key = (c.direction, p_bucket)
        if key not in seen_buckets:
            seen_buckets.add(key)
            deduped.append(c)

    # Check for opposing candidates (LONG vs SHORT conflict)
    longs = [c for c in deduped if c.direction == "LONG"]
    shorts = [c for c in deduped if c.direction == "SHORT"]

    if longs and shorts:
        # Deterministic conflict resolution (Rule 096):
        # 1. Prefer HTF bias alignment
        if htf_bias == "BULLISH":
            return longs
        elif htf_bias == "BEARISH":
            return shorts
        else:
            # Pick the candidate with highest Net R:R
            best_long = max(longs, key=lambda c: c.net_rr)
            best_short = max(shorts, key=lambda c: c.net_rr)
            return [best_long] if best_long.net_rr >= best_short.net_rr else [best_short]

    return deduped
