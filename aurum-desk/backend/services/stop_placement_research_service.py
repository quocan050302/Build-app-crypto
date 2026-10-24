"""
backend/services/stop_placement_research_service.py
AURUM V17.5 — Paired Research, Shadow Comparison Plans & Evidence Analytics.
Tracks opportunities and compares Variants A, B, and C without creating duplicate orders
or altering paper trading balance/positions.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy.orm import Session

import models
from domain_calculator import calculate_risk_reward, CostAssumptions
from services.stop_placement_types import (
    StopAnchor,
    StopBufferResult,
    StopComparisonPlan,
    VariantPlan,
)
from services.stop_placement_service import quantize_stop_outward


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

    # Causal timeline gate (P3): Quote timestamp and processing clock must not be strictly before plan cutoff
    quote_ts = (
        market_evidence.get("exchange_ts_ms")
        or market_evidence.get("timestamp")
        or market_evidence.get("exchange_time")
        or getattr(market_evidence, "exchange_ts_ms", None)
        or getattr(market_evidence, "timestamp", None)
    )
    if quote_ts is not None:
        try:
            quote_ts = int(quote_ts)
        except (ValueError, TypeError):
            quote_ts = None

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
        except (ValueError, TypeError):
            plan_cutoff = None

    if plan_cutoff is not None:
        if quote_ts is not None and quote_ts < plan_cutoff:
            return updated
        if as_of_ms < plan_cutoff:
            return updated

    # Compute excursion for the evidence quote
    excursion = compute_stop_excursions(direction, planned_entry, market_evidence)
    curr_excursions = updated.setdefault("excursions", {"max_adverse": 0.0, "max_favorable": 0.0})
    curr_excursions["max_adverse"] = max(curr_excursions.get("max_adverse", 0.0), excursion["adverse_price"])
    curr_excursions["max_favorable"] = max(curr_excursions.get("max_favorable", 0.0), excursion["favorable_price"])

    # Advance each variant independently
    for v_key in ("variant_a", "variant_b", "variant_c"):
        v_data = updated.get(v_key)
        if not v_data:
            continue

        # Eligibility gate (P4): variant must be valid, executable, and meet minimum R:R
        is_valid = v_data.get("is_valid", True)
        can_execute = v_data.get("can_execute", False)
        meets_min_rr = v_data.get("meets_min_rr", True)
        if not (is_valid and can_execute and meets_min_rr):
            continue

        v_state = v_data.setdefault("execution_state", "WAIT_ENTRY")
        sl_price = float(v_data.get("stop_loss", 0.0))

        if v_state == "WAIT_ENTRY":
            # Activated on first valid quote >= plan creation time
            v_data["execution_state"] = "OPEN"
            v_data["opened_at_ms"] = as_of_ms
            v_data["actual_entry"] = planned_entry

        if v_data.get("execution_state") == "OPEN":
            mock_order = {
                "direction": direction,
                "planned_entry": planned_entry,
                "stop_loss": sl_price,
                "take_profit": take_profit,
                "cost_snapshot": json.dumps({"tp_slippage_usd": 0.0}),
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
    research_db = session_factory()
    try:
        changed = advance_active_stop_comparisons(
            research_db, quote, as_of_ms=as_of_ms, namespace=namespace
        )
        if changed > 0:
            research_db.commit()
        return {"status": "COMMITTED", "changed": changed}
    except Exception as e:
        research_db.rollback()
        logger.warning(f"Research durability transaction failed: {e}")
        return {"status": "ERROR", "error": str(e), "changed": 0}
    finally:
        research_db.close()


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

