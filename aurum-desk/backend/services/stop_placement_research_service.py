"""
backend/services/stop_placement_research_service.py
AURUM V17.5 — Paired Research, Shadow Comparison Plans & Evidence Analytics.
Tracks opportunities and compares Variants A, B, and C without creating duplicate orders
or altering paper trading balance/positions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from sqlalchemy.orm import Session

import models
from domain_calculator import calculate_risk_reward, CostAssumptions
from services.stop_placement_types import (
    ComparisonEntryGateResult,
    StopAnchor,
    StopBufferResult,
    StopComparisonPlan,
    VariantPlan,
)
from services.stop_placement_service import quantize_stop_outward

logger = logging.getLogger(__name__)


def evaluate_stop_comparison_entry_gate(
    *,
    plan: Union[StopComparisonPlan, Dict[str, Any]],
    variant: Dict[str, Any],
    quote: Union[Dict[str, Any], Any],
    as_of_ms: int,
    cost_result: Dict[str, Any],
) -> ComparisonEntryGateResult:
    """
    AURUM V17.5.5 (Appendix A2):
    Pure evaluation gate enforcing strict context and cost verification before
    a research comparison variant can transition to OPEN or evaluate triggers.

    Evaluation hierarchy:
    1. Context status: Must be VERIFIED. (CONTEXT_INCOMPLETE or unverified plans blocked).
    2. Cost availability: Cost snapshot must be available with finite non-negative values.
    3. Expiry: Plan must not have expired.
    4. Variant eligibility: is_valid, can_execute, and meets_min_rr must be True.
    """
    plan_dict = plan.to_dict() if hasattr(plan, "to_dict") else plan
    context_status = plan_dict.get("context_status")
    cost_available = bool(cost_result.get("available", False)) if isinstance(cost_result, dict) else False

    # 1. Context verification
    if context_status != "VERIFIED":
        return ComparisonEntryGateResult(
            allowed=False,
            reason_code="CONTEXT_INCOMPLETE",
            explanation=f"Research comparison plan context is {context_status or 'UNSPECIFIED'}, expected VERIFIED",
            context_status=context_status,
            cost_available=cost_available,
        )

    # 2. Cost verification
    if not cost_available or not isinstance(cost_result, dict):
        reason = cost_result.get("reason") if isinstance(cost_result, dict) else None
        return ComparisonEntryGateResult(
            allowed=False,
            reason_code="COST_UNAVAILABLE",
            explanation=f"Cost snapshot is unavailable: {reason or 'MISSING_COST_SNAPSHOT'}",
            context_status=context_status,
            cost_available=False,
        )

    entry_slip = cost_result.get("entry_slippage_usd")
    maker_fee = cost_result.get("maker_fee_rate")
    taker_fee = cost_result.get("taker_fee_rate")

    def _is_valid_num(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0

    if not (_is_valid_num(entry_slip) and _is_valid_num(maker_fee) and _is_valid_num(taker_fee)):
        return ComparisonEntryGateResult(
            allowed=False,
            reason_code="INVALID_COST",
            explanation="Cost snapshot contains non-finite, negative, or invalid fee/slippage values",
            context_status=context_status,
            cost_available=False,
        )

    # 3. Plan expiry verification
    expires_at_ms = (
        plan_dict.get("expires_at_ms")
        or plan_dict.get("order_expiry_ms")
        or plan_dict.get("expiry_at_ms")
    )
    if expires_at_ms is not None:
        try:
            exp_val = int(expires_at_ms)
            if exp_val > 0 and as_of_ms >= exp_val:
                return ComparisonEntryGateResult(
                    allowed=False,
                    reason_code="EXPIRED",
                    explanation=f"Plan expired at {exp_val} <= as_of {as_of_ms}",
                    context_status=context_status,
                    cost_available=True,
                )
        except (ValueError, TypeError):
            pass

    # 4. Variant eligibility
    is_valid = variant.get("is_valid", True)
    can_execute = variant.get("can_execute", False)
    meets_min_rr = variant.get("meets_min_rr", True)
    if not (is_valid and can_execute and meets_min_rr):
        reason = variant.get("rejection_reason") or "VARIANT_INELIGIBLE"
        return ComparisonEntryGateResult(
            allowed=False,
            reason_code=reason,
            explanation=f"Variant is ineligible: valid={is_valid}, can_execute={can_execute}, meets_min_rr={meets_min_rr}",
            context_status=context_status,
            cost_available=True,
        )

    return ComparisonEntryGateResult(
        allowed=True,
        reason_code="READY",
        explanation="Variant satisfies all context, cost, and eligibility gates",
        context_status=context_status,
        cost_available=True,
    )


def make_opportunity_hash_id(prefix: str, opportunity_key: str, length: int = 70) -> str:
    """Generates a bounded, deterministic ID <= length chars for PaperObservation."""
    digest = hashlib.sha256(opportunity_key.encode("utf-8")).hexdigest()[:32]
    clean_prefix = prefix[:30].rstrip("_")
    return f"{clean_prefix}_{digest}"[:length]


def build_stop_comparison_plan(
    *,
    opportunity_key: str,
    model_id: str,
    symbol: str,
    direction: str,
    planned_entry: float,
    take_profit: float,
    legacy_sl: float,
    anchor: Optional[StopAnchor],
    buffer_result: Optional[StopBufferResult],
    policy_settings: Dict[str, Any],
    tick_size: float = 0.01,
    min_net_rr: float = 1.5,
    atr_m1: Optional[float] = None,
    atr_m5: Optional[float] = None,
    spread: Optional[float] = None,
    policy_version: int = 1,
    now_ms: int = 0,
    entry_intent: str = "MARKET",
    order_type: Optional[str] = None,
    decision_cutoff_ms: int = 0,
    expires_at_ms: Optional[int] = None,
    cost_snapshot: Optional[Dict[str, Any]] = None,
    context_status: Optional[str] = None,
    context_reason: Optional[str] = None,
    provenance: Optional[str] = None,
) -> StopComparisonPlan:
    """
    Constructs a paired StopComparisonPlan comparing Variants A, B, and C
    evaluated on the exact same opportunity with identical entry and take profit.
    """
    capital = float(policy_settings.get("capital_usdt", 1000.0))
    risk_pct = float(policy_settings.get("risk_pct", 0.1))
    lev = int(policy_settings.get("leverage", 50))
    margin_mode = str(policy_settings.get("margin_mode", "ISOLATED"))
    costs = policy_settings.get("costs") or CostAssumptions()

    # Variant A: Legacy Stop
    calc_a = calculate_risk_reward(
        direction=direction, planned_entry=planned_entry, stop_loss=legacy_sl, take_profit=take_profit,
        capital_usdt=capital, risk_pct=risk_pct, leverage=lev, margin_mode=margin_mode, min_net_rr=min_net_rr,
        costs=costs, now_ms=now_ms,
    )
    plan_a = VariantPlan(
        variant_id="A",
        stop_loss=legacy_sl,
        take_profit=take_profit,
        quantity=float(getattr(calc_a, "quantity", 0.0)),
        net_rr=float(getattr(calc_a, "net_rr", 0.0)),
        gross_rr=float(getattr(calc_a, "gross_rr", 0.0)),
        is_valid=bool(getattr(calc_a, "is_valid", False)),
        can_execute=bool(getattr(calc_a, "can_execute", False)),
        meets_min_rr=bool(getattr(calc_a, "meets_min_rr", False)),
        rejection_reason=getattr(calc_a, "skip_reason", None) or getattr(calc_a, "invalid_reason", None),
        buffer_price=0.05,
        dominant_buffer="legacy_ticks",
    )

    # If anchor is unavailable, B and C mirror A with missing evidence
    if anchor is None:
        plan_b = VariantPlan(
            variant_id="B", stop_loss=legacy_sl, take_profit=take_profit,
            quantity=plan_a.quantity, net_rr=plan_a.net_rr, gross_rr=plan_a.gross_rr,
            is_valid=False, can_execute=False, meets_min_rr=False,
            rejection_reason="MISSING_STRUCTURAL_ANCHOR", buffer_price=0.0, dominant_buffer="none",
        )
        plan_c = VariantPlan(
            variant_id="C", stop_loss=legacy_sl, take_profit=take_profit,
            quantity=plan_a.quantity, net_rr=plan_a.net_rr, gross_rr=plan_a.gross_rr,
            is_valid=False, can_execute=False, meets_min_rr=False,
            rejection_reason="MISSING_STRUCTURAL_ANCHOR", buffer_price=0.0, dominant_buffer="none",
        )
    else:
        # Variant B: Structural anchor + tick floor (5 ticks)
        tick_floor_buf = 5.0 * tick_size
        b_sl = quantize_stop_outward(
            anchor_price=anchor.price, buffer_price=tick_floor_buf, direction=direction, tick_size=tick_size,
        )
        calc_b = calculate_risk_reward(
            direction=direction, planned_entry=planned_entry, stop_loss=b_sl, take_profit=take_profit,
            capital_usdt=capital, risk_pct=risk_pct, leverage=lev, margin_mode=margin_mode, min_net_rr=min_net_rr,
            costs=costs, now_ms=now_ms,
        )
        plan_b = VariantPlan(
            variant_id="B",
            stop_loss=b_sl,
            take_profit=take_profit,
            quantity=float(getattr(calc_b, "quantity", 0.0)),
            net_rr=float(getattr(calc_b, "net_rr", 0.0)),
            gross_rr=float(getattr(calc_b, "gross_rr", 0.0)),
            is_valid=bool(getattr(calc_b, "is_valid", False)),
            can_execute=bool(getattr(calc_b, "can_execute", False)),
            meets_min_rr=bool(getattr(calc_b, "meets_min_rr", False)),
            rejection_reason=getattr(calc_b, "skip_reason", None) or getattr(calc_b, "invalid_reason", None),
            buffer_price=tick_floor_buf,
            dominant_buffer="tick_floor",
        )

        # Variant C: Structural anchor + full dynamic buffer
        c_buf = buffer_result.buffer_price if buffer_result else tick_floor_buf
        c_dom = buffer_result.dominant_source if buffer_result else "tick_floor"
        c_sl = quantize_stop_outward(
            anchor_price=anchor.price, buffer_price=c_buf, direction=direction, tick_size=tick_size,
        )
        calc_c = calculate_risk_reward(
            direction=direction, planned_entry=planned_entry, stop_loss=c_sl, take_profit=take_profit,
            capital_usdt=capital, risk_pct=risk_pct, leverage=lev, margin_mode=margin_mode, min_net_rr=min_net_rr,
            costs=costs, now_ms=now_ms,
        )
        plan_c = VariantPlan(
            variant_id="C",
            stop_loss=c_sl,
            take_profit=take_profit,
            quantity=float(getattr(calc_c, "quantity", 0.0)),
            net_rr=float(getattr(calc_c, "net_rr", 0.0)),
            gross_rr=float(getattr(calc_c, "gross_rr", 0.0)),
            is_valid=bool(getattr(calc_c, "is_valid", False)),
            can_execute=bool(getattr(calc_c, "can_execute", False)),
            meets_min_rr=bool(getattr(calc_c, "meets_min_rr", False)),
            rejection_reason=getattr(calc_c, "skip_reason", None) or getattr(calc_c, "invalid_reason", None),
            buffer_price=c_buf,
            dominant_buffer=c_dom,
        )

    if context_status is None:
        if expires_at_ms is not None and cost_snapshot is not None:
            c_status = "VERIFIED"
            c_reason = None
        else:
            c_status = "CONTEXT_INCOMPLETE"
            c_reason = "MISSING_EXPIRY_OR_COST"
    else:
        c_status = context_status
        c_reason = context_reason

    return StopComparisonPlan(
        opportunity_key=opportunity_key,
        model_id=model_id,
        symbol=symbol,
        direction=direction,
        planned_entry=planned_entry,
        take_profit=take_profit,
        as_of_ms=now_ms,
        variant_a=plan_a,
        variant_b=plan_b,
        variant_c=plan_c,
        anchor=anchor,
        atr_m1=atr_m1,
        atr_m5=atr_m5,
        spread=spread,
        policy_version=policy_version,
        entry_intent=entry_intent,
        order_type=order_type or entry_intent,
        decision_cutoff_ms=decision_cutoff_ms,
        expires_at_ms=expires_at_ms,
        cost_snapshot=cost_snapshot,
        context_status=c_status,
        context_reason=c_reason,
        provenance=provenance,
    )


def persist_stop_comparison(
    db: Session,
    plan: StopComparisonPlan,
    *,
    now_ms: int,
) -> None:
    """
    Saves or updates a StopComparisonPlan in the PaperObservation table.
    Preserves immutable plan payload once written to avoid resetting state.
    Does NOT call db.commit() to preserve caller transaction ownership.
    """
    opp_key = getattr(plan, "opportunity_key", None) or (plan.get("opportunity_key") if isinstance(plan, dict) else "unknown_opp")
    obs_id = make_opportunity_hash_id("stop_plan", str(opp_key), length=75)
    row = db.query(models.PaperObservation).filter(models.PaperObservation.id == obs_id).first()
    plan_data = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)
    payload_str = json.dumps(plan_data, ensure_ascii=False)

    if row is None:
        row = models.PaperObservation(
            id=obs_id,
            namespace="STOP_PLACEMENT_V1",
            event_kind="COMPARISON_PLAN",
            snapshot_id=None,
            scenario_id=None,
            order_id=None,
            as_of_ms=now_ms,
            payload_json=payload_str,
        )
        db.add(row)
    else:
        try:
            existing_data = json.loads(row.payload_json)
            if existing_data.get("context_status") == "CONTEXT_INCOMPLETE" and plan_data.get("context_status") == "VERIFIED":
                row.payload_json = payload_str
                row.as_of_ms = now_ms
        except Exception:
            row.payload_json = payload_str


def resolve_stop_comparison_execution_context(
    cand: Any,
    policy_settings: Optional[Dict[str, Any]] = None,
    decision_as_of_ms: int = 0,
    session_context: Optional[Any] = None,
    cfg: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Pure helper (V17.5.4 Appendix A2, updated V17.5.5):
    Resolves authoritative execution context from production candidate for research comparison.
    Extracts order_type, absolute expires_at_ms, decision_cutoff_ms, cost_snapshot, and provenance.
    Enforces unified cost authority via resolve_execution_cost_context.
    Status is VERIFIED only if intent, expiry, AND cost are valid.
    """
    from services.cg01_types import candidate_execution_order_type, resolve_candidate_order_expiry
    from services.plan_financial_context_service import resolve_execution_cost_context

    settings = policy_settings or {}
    as_of = int(decision_as_of_ms or getattr(cand, "as_of_ms", 0) or 0)

    # 1. Authoritative order type
    resolved_order_type = candidate_execution_order_type(cand, legacy_default="MARKET")

    # 2. Authoritative absolute expiry
    resolved_expires_at = None
    try:
        resolved_expires_at = resolve_candidate_order_expiry(
            cand, now_ms=as_of, session_context=session_context
        )
    except Exception:
        resolved_expires_at = None

    # 3. Unified Cost Authority (V17.5.5 Appendix B)
    cost_res = resolve_execution_cost_context(
        candidate=cand,
        settings=settings,
        config=cfg,
        as_of_ms=as_of,
    )

    cost_snapshot = {
        "entry_slippage_usd": cost_res["entry_slippage_usd"],
        "tp_slippage_usd": cost_res["tp_slippage_usd"],
        "sl_slippage_usd": cost_res["sl_slippage_usd"],
        "maker_fee_rate": cost_res["maker_fee_rate"],
        "taker_fee_rate": cost_res["taker_fee_rate"],
        "version": cost_res["version"],
        "provenance": cost_res["provenance"],
        "available": cost_res["available"],
    }
    if not cost_res["available"]:
        cost_snapshot["reason"] = cost_res.get("reason")

    # Provenance
    model_id = str(getattr(cand, "model_id", "UNKNOWN"))
    cand_key = str(getattr(cand, "candidate_key", "UNKNOWN"))
    provenance = f"{model_id}:{cand_key}:decision@{as_of}"

    # Status determination: VERIFIED only if order_type, expires_at, AND cost are valid
    if not (resolved_order_type and resolved_expires_at is not None and resolved_expires_at > 0):
        status = "CONTEXT_INCOMPLETE"
        reason = "MISSING_EXPIRY_OR_INTENT"
    elif not cost_res.get("available", False):
        status = "CONTEXT_INCOMPLETE"
        reason = cost_res.get("reason") or "COST_UNAVAILABLE"
    else:
        status = "VERIFIED"
        reason = None

    return {
        "status": status,
        "reason": reason,
        "order_type": resolved_order_type,
        "entry_intent": resolved_order_type,
        "decision_cutoff_ms": as_of,
        "expires_at_ms": resolved_expires_at,
        "cost_snapshot": cost_snapshot,
        "provenance": provenance,
    }


def bind_stop_comparison_execution_context(
    plan: Union[StopComparisonPlan, Dict[str, Any]],
    context: Dict[str, Any],
) -> Union[StopComparisonPlan, Dict[str, Any]]:
    """
    Pure helper (V17.5.4 Appendix A3):
    Binds resolved execution context to a StopComparisonPlan without mutating original.
    """
    import copy

    if isinstance(plan, StopComparisonPlan):
        plan_dict = plan.to_dict()
    else:
        plan_dict = copy.deepcopy(plan)

    plan_dict["order_type"] = context.get("order_type", plan_dict.get("order_type", "MARKET"))
    plan_dict["entry_intent"] = context.get("entry_intent", plan_dict.get("entry_intent", "MARKET"))
    plan_dict["decision_cutoff_ms"] = int(context.get("decision_cutoff_ms", plan_dict.get("decision_cutoff_ms", 0)))
    plan_dict["expires_at_ms"] = context.get("expires_at_ms", plan_dict.get("expires_at_ms"))
    plan_dict["cost_snapshot"] = context.get("cost_snapshot", plan_dict.get("cost_snapshot"))
    plan_dict["context_status"] = context.get("status", "VERIFIED")
    plan_dict["context_reason"] = context.get("reason")
    plan_dict["provenance"] = context.get("provenance")

    if isinstance(plan, StopComparisonPlan):
        return StopComparisonPlan(
            opportunity_key=plan_dict["opportunity_key"],
            model_id=plan_dict["model_id"],
            symbol=plan_dict["symbol"],
            direction=plan_dict["direction"],
            planned_entry=plan_dict["planned_entry"],
            take_profit=plan_dict["take_profit"],
            as_of_ms=plan_dict["as_of_ms"],
            variant_a=plan.variant_a,
            variant_b=plan.variant_b,
            variant_c=plan.variant_c,
            anchor=plan.anchor,
            atr_m1=plan.atr_m1,
            atr_m5=plan.atr_m5,
            spread=plan.spread,
            policy_version=plan.policy_version,
            entry_intent=plan_dict["entry_intent"],
            order_type=plan_dict["order_type"],
            decision_cutoff_ms=plan_dict["decision_cutoff_ms"],
            expires_at_ms=plan_dict["expires_at_ms"],
            cost_snapshot=plan_dict["cost_snapshot"],
            context_status=plan_dict["context_status"],
            context_reason=plan_dict["context_reason"],
            provenance=plan_dict["provenance"],
        )
    return plan_dict


def read_verified_comparison_cost_snapshot(
    plan: Union[StopComparisonPlan, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Pure helper (V17.5.4 Appendix B1 & B2, updated V17.5.5):
    Reads verified cost snapshot from comparison plan via unified cost authority.
    Distinguishes explicit 0.0 slippage from missing snapshot.
    """
    from services.plan_financial_context_service import resolve_execution_cost_context

    plan_dict = plan.to_dict() if hasattr(plan, "to_dict") else plan
    snapshot = plan_dict.get("cost_snapshot") if isinstance(plan_dict, dict) else None
    if not isinstance(snapshot, dict):
        return {
            "available": False,
            "reason": "MISSING_COST_SNAPSHOT",
            "entry_slippage_usd": 0.0,
            "tp_slippage_usd": 0.0,
            "sl_slippage_usd": 0.0,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
            "provenance": "MISSING_SNAPSHOT",
            "version": 1,
        }
    return resolve_execution_cost_context(snapshot=snapshot)


def compute_stop_excursions(
    direction: str,
    planned_entry: float,
    quote: Any,
) -> Dict[str, float]:
    """
    Computes Maximum Adverse Excursion (MAE) and Maximum Favorable Excursion (MFE).
    LONG:  adverse = max(0, entry - bid), favorable = max(0, bid - entry)
    SHORT: adverse = max(0, ask - entry), favorable = max(0, entry - ask)
    """
    bid = float(getattr(quote, "bid", 0.0) or (quote.get("bid", 0.0) if isinstance(quote, dict) else 0.0))
    ask = float(getattr(quote, "ask", 0.0) or (quote.get("ask", 0.0) if isinstance(quote, dict) else 0.0))
    norm_dir = direction.upper()

    if norm_dir in ("LONG", "BUY"):
        adverse = max(0.0, planned_entry - bid) if bid > 0 else 0.0
        favorable = max(0.0, bid - planned_entry) if bid > 0 else 0.0
    else:
        adverse = max(0.0, ask - planned_entry) if ask > 0 else 0.0
        favorable = max(0.0, planned_entry - ask) if ask > 0 else 0.0

    return {
        "adverse_price": round(adverse, 2),
        "favorable_price": round(favorable, 2),
    }


def build_stop_comparison_summary(
    records: Sequence[Dict[str, Any]],
    *,
    as_of_ms: int,
) -> Dict[str, Any]:
    """
    Aggregates comparison metrics across recorded paired plans.
    Separates total planned opportunities from common executable pairs.
    """
    total_plans = len(records)
    variants = {"A": {"valid": 0, "executable": 0}, "B": {"valid": 0, "executable": 0}, "C": {"valid": 0, "executable": 0}}

    for rec in records:
        va = rec.get("variant_a", {})
        vb = rec.get("variant_b", {})
        vc = rec.get("variant_c", {})
        if va.get("is_valid"): variants["A"]["valid"] += 1
        if va.get("can_execute") and va.get("meets_min_rr"): variants["A"]["executable"] += 1
        if vb.get("is_valid"): variants["B"]["valid"] += 1
        if vb.get("can_execute") and vb.get("meets_min_rr"): variants["B"]["executable"] += 1
        if vc.get("is_valid"): variants["C"]["valid"] += 1
        if vc.get("can_execute") and vc.get("meets_min_rr"): variants["C"]["executable"] += 1

    return {
        "as_of_ms": as_of_ms,
        "total_opportunities_recorded": total_plans,
        "variant_a_legacy": variants["A"],
        "variant_b_anchor_ticks": variants["B"],
        "variant_c_structural_dynamic": variants["C"],
        "status": "EMPIRICAL_UNPROVEN",
        "summary_vi": f"Đã thu thập {total_plans} cơ hội đối chiếu; cần tiếp tục theo dõi trên PAPER.",
    }


def evaluate_comparison_entry(
    *,
    direction: str,
    planned_entry: float,
    order_type: str,
    bid: float,
    ask: float,
    order_slip: float = 0.0,
) -> Tuple[bool, bool, Optional[float], str]:
    """
    Evaluates entry trigger and fill price with strict parity to ExecutionCoordinator.evaluate_orders_sync.
    Returns (triggered, supported, fill_price, reason).
    """
    norm_type = (order_type or "MARKET").upper()
    norm_dir = (direction or "SHORT").upper()

    if norm_type == "MARKET":
        # Immediate market trigger: Ask for LONG, Bid for SHORT + directional slippage
        fill = round(ask + order_slip, 2) if norm_dir == "LONG" else round(bid - order_slip, 2)
        return True, True, fill, "MARKET_TRIGGERED"

    elif norm_type == "LIMIT":
        if norm_dir == "LONG":
            # BUY LIMIT: Ask <= limit
            if ask <= planned_entry:
                return True, True, planned_entry, "BUY_LIMIT_TOUCHED"
            return False, True, None, "BUY_LIMIT_WAITING_ASK"
        else:
            # SELL LIMIT: Bid >= limit
            if bid >= planned_entry:
                return True, True, planned_entry, "SELL_LIMIT_TOUCHED"
            return False, True, None, "SELL_LIMIT_WAITING_BID"

    elif norm_type == "STOP":
        if norm_dir == "LONG":
            # BUY STOP: Ask >= stop
            if ask >= planned_entry:
                fill = round(ask + order_slip, 2)
                return True, True, fill, "BUY_STOP_BREAKOUT"
            return False, True, None, "BUY_STOP_WAITING_ASK"
        else:
            # SELL STOP: Bid <= stop
            if bid <= planned_entry:
                fill = round(bid - order_slip, 2)
                return True, True, fill, "SELL_STOP_BREAKOUT"
            return False, True, None, "SELL_STOP_WAITING_BID"

    return False, False, None, f"UNSUPPORTED_ORDER_TYPE_{norm_type}"


def advance_stop_comparison(
    plan_state: Dict[str, Any],
    market_evidence: Dict[str, Any],
    *,
    as_of_ms: int,
) -> Dict[str, Any]:
    """
    Pure state advancement for a paired stop comparison plan.
    Guarantees strict purity (deepcopy) without mutating caller inputs.
    Validates quotes before state transitions (empty/invalid quotes cannot open positions).
    Tracks MAE/MFE, fill triggers, and stop/target outcomes.
    """
    import copy
    from services.shared_exit_evaluator import evaluate_order_exit

    updated = copy.deepcopy(plan_state)
    direction = updated.get("direction", "SHORT")
    planned_entry = float(updated.get("planned_entry", 0.0))
    take_profit = float(updated.get("take_profit", 0.0))

    # Validate market quote evidence strictly
    bid = float(market_evidence.get("bid", 0.0) or getattr(market_evidence, "bid", 0.0) or 0.0)
    ask = float(market_evidence.get("ask", 0.0) or getattr(market_evidence, "ask", 0.0) or 0.0)
    is_quote_valid = (
        math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask >= bid
    )
    if not is_quote_valid:
        # Invalid or empty quote cannot transition state or compute excursions
        return updated

    # Source quote timestamp check (R1): Must have a valid, finite positive timestamp
    quote_ts = (
        market_evidence.get("exchange_ts_ms")
        or market_evidence.get("timestamp")
        or market_evidence.get("exchange_time")
        or getattr(market_evidence, "exchange_ts_ms", None)
        or getattr(market_evidence, "timestamp", None)
    )
    if quote_ts is None:
        # Quote missing source timestamp cannot transition state or open variants
        return updated
    try:
        quote_ts = int(quote_ts)
        if quote_ts <= 0:
            return updated
    except (ValueError, TypeError):
        return updated

    # Causal timeline gate (R2): Quote timestamp must not be far in the future relative to processing clock
    if quote_ts > as_of_ms + 5000:
        return updated

    # Causal timeline gate (P3): Quote timestamp and processing clock must not be strictly before plan cutoff
    plan_cutoff = (
        updated.get("decision_cutoff_ms")
        or updated.get("plan_cutoff_ms")
        or updated.get("as_of_ms")
        or updated.get("created_at_ms")
        or updated.get("now_ms")
    )
    if plan_cutoff is not None:
        try:
            plan_cutoff = int(plan_cutoff)
            if quote_ts < plan_cutoff or as_of_ms < plan_cutoff:
                return updated
        except (ValueError, TypeError):
            pass

    # Expiry check (R3): Pending plans that reached deadline expire cleanly
    expires_at_ms = (
        updated.get("expires_at_ms")
        or updated.get("order_expiry_ms")
        or updated.get("expiry_at_ms")
    )
    is_expired = False
    if expires_at_ms is not None:
        try:
            exp_val = int(expires_at_ms)
            if as_of_ms >= exp_val or quote_ts >= exp_val:
                is_expired = True
        except (ValueError, TypeError):
            pass

    # Backlog quote freshness check (R7)
    quote_age = as_of_ms - quote_ts
    is_quote_stale = bool(
        market_evidence.get("is_stale", False)
        or getattr(market_evidence, "is_stale", False)
        or (quote_age > 15000)
    )

    # Compute observation-level excursion for the evidence quote (Appendix B6)
    obs_excursion = compute_stop_excursions(direction, planned_entry, market_evidence)
    curr_excursions = updated.setdefault("observation_excursions", {"max_adverse": 0.0, "max_favorable": 0.0})
    curr_excursions["max_adverse"] = max(curr_excursions.get("max_adverse", 0.0), obs_excursion["adverse_price"])
    curr_excursions["max_favorable"] = max(curr_excursions.get("max_favorable", 0.0), obs_excursion["favorable_price"])
    updated["excursions"] = curr_excursions

    # Read verified cost snapshot for execution parity (Appendix B1 & B2)
    costs = read_verified_comparison_cost_snapshot(updated)

    # Advance each variant independently
    for v_key in ("variant_a", "variant_b", "variant_c"):
        v_data = updated.get(v_key)
        if not v_data:
            continue

        v_state = v_data.setdefault("execution_state", "WAIT_ENTRY")
        if v_state in ("CLOSED", "EXPIRED", "CENSORED"):
            continue

        # Expiry: Pending plans that exceeded lifetime expire cleanly (R3)
        if is_expired and v_state == "WAIT_ENTRY":
            v_data["execution_state"] = "EXPIRED"
            v_data["expired_at_ms"] = as_of_ms
            v_data["rejection_reason"] = "PLAN_EXPIRED"
            continue

        sl_price = float(v_data.get("stop_loss", 0.0))

        if v_state == "WAIT_ENTRY":
            # Stale backlog quotes cannot open new entries (R7)
            if is_quote_stale:
                v_data["wait_reason"] = "STALE_QUOTE_BACKLOG"
                continue

            # Strict V17.5.5 Entry Context & Cost Gate (Appendix A3)
            gate = evaluate_stop_comparison_entry_gate(
                plan=updated,
                variant=v_data,
                quote=market_evidence,
                as_of_ms=as_of_ms,
                cost_result=costs,
            )
            if not gate.allowed:
                v_data["wait_reason"] = gate.reason_code
                v_data["gate_explanation"] = gate.explanation
                continue

            # Intent-aware entry trigger evaluation with slippage parity (Appendix B1-B3)
            order_type = (
                v_data.get("order_type")
                or v_data.get("entry_intent")
                or updated.get("order_type")
                or updated.get("entry_intent")
                or "MARKET"
            )
            order_slip = costs["entry_slippage_usd"]
            triggered, supported, fill_price, entry_reason = evaluate_comparison_entry(
                direction=direction,
                planned_entry=planned_entry,
                order_type=order_type,
                bid=bid,
                ask=ask,
                order_slip=order_slip,
            )
            if not (supported and triggered):
                v_data["wait_reason"] = entry_reason
                continue

            # Activated on valid market fill trigger
            v_data["execution_state"] = "OPEN"
            v_data["opened_at_ms"] = as_of_ms
            v_data["actual_entry"] = fill_price
            v_data["excursions"] = {"max_adverse": 0.0, "max_favorable": 0.0}
            v_data.pop("wait_reason", None)
            v_data.pop("gate_explanation", None)

        if v_data.get("execution_state") == "OPEN":
            actual_entry_px = float(v_data.get("actual_entry", planned_entry))

            # Excursion tracking for OPEN variant based on actual entry (Appendix B6)
            var_exc = v_data.setdefault("excursions", {"max_adverse": 0.0, "max_favorable": 0.0})
            trade_exc = compute_stop_excursions(direction, actual_entry_px, market_evidence)
            var_exc["max_adverse"] = max(var_exc.get("max_adverse", 0.0), trade_exc["adverse_price"])
            var_exc["max_favorable"] = max(var_exc.get("max_favorable", 0.0), trade_exc["favorable_price"])

            # Exit evaluation with full cost snapshot (Appendix B4)
            full_cost_snap = {
                "tp_slippage_usd": costs.get("tp_slippage_usd", 0.0),
                "sl_slippage_usd": costs.get("sl_slippage_usd", costs.get("entry_slippage_usd", 0.0)),
                "maker_fee_rate": costs.get("maker_fee_rate", 0.0002),
                "taker_fee_rate": costs.get("taker_fee_rate", 0.0006),
            }
            mock_order = {
                "direction": direction,
                "planned_entry": actual_entry_px,
                "stop_loss": sl_price,
                "take_profit": take_profit,
                "cost_snapshot": json.dumps(full_cost_snap),
            }
            exit_dec = evaluate_order_exit(mock_order, market_evidence)
            if exit_dec.triggered:
                v_data["execution_state"] = "CLOSED"
                v_data["closed_at_ms"] = as_of_ms
                v_data["exit_cause"] = exit_dec.cause
                v_data["exit_price"] = exit_dec.applied_exit_price
                v_data["price_basis"] = exit_dec.price_basis

    updated["last_updated_at_ms"] = as_of_ms
    return updated


def resume_stop_comparisons(
    db: Session,
    *,
    namespace: str = "STOP_PLACEMENT_V1",
    as_of_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Loads all active/open comparison plans from PaperObservation under the namespace.
    Preserves existing cutoff/state/costs and avoids double counting on restart.
    """
    rows = db.query(models.PaperObservation).filter(
        models.PaperObservation.namespace == namespace,
        models.PaperObservation.event_kind == "COMPARISON_PLAN",
    ).all()

    plans: List[Dict[str, Any]] = []
    for r in rows:
        try:
            data = json.loads(r.payload_json)
            plans.append(data)
        except Exception:
            continue
    return plans


def advance_active_stop_comparisons(
    db: Session,
    quote: Any,
    *,
    as_of_ms: int,
    namespace: str = "STOP_PLACEMENT_V1",
) -> int:
    """
    Production runtime caller: advances in-flight comparison plans with incoming quotes.
    Preserves isolation so research tracking does not block or fail live trading.
    """
    quote_dict = quote if isinstance(quote, dict) else (quote.to_dict() if hasattr(quote, "to_dict") else {})
    if not quote_dict:
        return 0

    rows = db.query(models.PaperObservation).filter(
        models.PaperObservation.namespace == namespace,
        models.PaperObservation.event_kind == "COMPARISON_PLAN",
    ).all()
    if not rows:
        return 0

    updated_count = 0
    for r in rows:
        try:
            data = json.loads(r.payload_json)
            all_closed = True
            for v_key in ("variant_a", "variant_b", "variant_c"):
                v = data.get(v_key, {})
                if v.get("can_execute") and v.get("execution_state") not in ("CLOSED", "EXPIRED", "CENSORED"):
                    all_closed = False
                    break
            if all_closed:
                continue

            advanced = advance_stop_comparison(data, quote_dict, as_of_ms=as_of_ms)
            if advanced != data:
                r.payload_json = json.dumps(advanced, ensure_ascii=False)
                r.as_of_ms = as_of_ms
                updated_count += 1
        except Exception:
            continue

    if updated_count > 0:
        db.flush()

    return updated_count


def advance_stop_comparisons_durable(
    *,
    session_factory: Any,
    quote: Any,
    as_of_ms: int,
    namespace: str = "STOP_PLACEMENT_V1",
) -> Dict[str, Any]:
    """
    Dedicated transaction owner for research comparison durability.
    Opens an isolated session from session_factory, advances in-flight plans,
    commits on changes, rolls back on error, and ensures clean closure.
    """
    research_db = None
    try:
        research_db = session_factory()
    except Exception as init_err:
        logger.warning(f"Failed to create research DB session: {init_err}")
        return {"status": "ERROR", "error": str(init_err), "changed": 0}

    try:
        changed = advance_active_stop_comparisons(
            research_db, quote, as_of_ms=as_of_ms, namespace=namespace
        )
        if changed > 0:
            research_db.commit()
        return {"status": "COMMITTED", "changed": changed}
    except Exception as e:
        if research_db is not None and hasattr(research_db, "rollback"):
            try:
                research_db.rollback()
            except Exception:
                pass
        logger.warning(f"Research durability transaction failed: {e}")
        return {"status": "ERROR", "error": str(e), "changed": 0}
    finally:
        if research_db is not None and hasattr(research_db, "close"):
            try:
                research_db.close()
            except Exception:
                pass


def serialize_stop_comparison_for_ui(plan_or_dict: Any) -> Dict[str, Any]:
    """
    Canonical serializer providing a unified shape v1 for frontend consumers.
    Supports both variants.A/B/C and variant_a/b/c representations simultaneously.
    """
    import copy
    if hasattr(plan_or_dict, "to_dict"):
        d = plan_or_dict.to_dict()
    elif isinstance(plan_or_dict, dict):
        d = copy.deepcopy(plan_or_dict)
    else:
        return {}

    va = d.get("variant_a") or {}
    vb = d.get("variant_b") or {}
    vc = d.get("variant_c") or {}

    d["schema_version"] = "stop-comparison.v1"
    d["variants"] = {
        "A": va,
        "B": vb,
        "C": vc,
        "A_LEGACY": va,
        "B_STRUCTURAL_TICK": vb,
        "C_STRUCTURAL_DYNAMIC": vc,
    }
    return d

