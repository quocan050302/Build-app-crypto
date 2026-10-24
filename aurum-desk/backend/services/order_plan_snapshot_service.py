"""
backend/services/order_plan_snapshot_service.py
V14.4.2 — Canonical Order Financial Snapshot Producers and Binding Service.

Guarantees:
1. Actual producers (auto-arm, manual-arm, execute_fill) persist immutable, complete
   financial and plan snapshots into PaperOrder.cost_snapshot.
2. Preserves legacy keys (phase, maker_fee_rate, taker_fee_rate, slippage_usd, multiplier, fee totals)
   for backward compatibility with exit monitor and lifecycle calculators.
3. Enforces exact setup-order binding:
   - setup_id match
   - direction match (SHORT order never binds to LONG setup)
   - setup_instance_id match when present
   - active orders (armed, paper_open) prioritized over historical terminal orders
   - conflicting active orders yield None (DISPLAY_BINDING_CONFLICT).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("order_plan_snapshot")


class OrderPlanSnapshot(dict):
    """
    V16.6: Authoritative order financial and plan snapshot representation.
    Subclasses dict to support both dictionary lookup (snapshot['risk_pct'])
    and attribute access (snapshot.risk_pct, snapshot.slippage, snapshot.initial_risk_usd).
    """
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'OrderPlanSnapshot' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


def parse_order_plan_snapshot(raw: Any) -> Optional[OrderPlanSnapshot]:
    """Parses raw cost_snapshot (JSON string, dict, or OrderPlanSnapshot) into OrderPlanSnapshot."""
    if not raw:
        return None
    if isinstance(raw, OrderPlanSnapshot):
        return raw
    if isinstance(raw, dict):
        return OrderPlanSnapshot(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return OrderPlanSnapshot(parsed)
        except Exception:
            return None
    return None


def build_arm_plan_snapshot(
    calc_res: Any,
    *,
    order_type: str = "LIMIT",
    direction: str = "LONG",
    planned_entry: float,
    stop_loss: float,
    take_profit: float,
    capital_usdt: float,
    risk_pct: float,
    leverage: int,
    margin_mode: str = "ISOLATED",
    now_ms: int,
    cost_source: str = "BITGET_PAPER_MODEL_V10_4",
    policy_config_version: int = 1,
    risk_config_version: int = 1,
    slippage_usd: Optional[float] = None,
    capital_mode: str = "CURRENT_ACCOUNT_BALANCE",
    target_capital: Optional[float] = None,
    target_risk_budget: Optional[float] = None,
    min_net_rr: Optional[float] = None,
) -> OrderPlanSnapshot:
    """Builds canonical frozen financial snapshot at ARM stage."""
    from services.cg01_types import resolve_slippage_usd
    raw_arm_slip = slippage_usd if slippage_usd is not None else getattr(calc_res, "slippage_usd", None)
    resolved_arm_slip = resolve_slippage_usd(raw_arm_slip, default=0.10)
    qty = getattr(calc_res, "quantity", 0.0)
    multiplier = getattr(calc_res, "multiplier", 1.0)
    net_rr = getattr(calc_res, "net_rr", None)
    gross_rr = getattr(calc_res, "gross_rr", None)
    net_risk = getattr(calc_res, "net_risk_usdt", None)
    gross_loss = getattr(calc_res, "gross_loss_usdt", None)
    gross_reward = getattr(calc_res, "gross_reward_usdt", None)
    net_reward = getattr(calc_res, "net_reward_usdt", None)

    entry_fee = getattr(calc_res, "entry_fee_usdt", 0.0)
    sl_exit_fee = getattr(calc_res, "sl_exit_fee_usdt", 0.0)
    tp_exit_fee = getattr(calc_res, "tp_exit_fee_usdt", 0.0)
    entry_slip = getattr(calc_res, "entry_slippage_usdt", 0.0)
    sl_exit_slip = getattr(calc_res, "sl_exit_slippage_usdt", 0.0)
    tp_exit_slip = getattr(calc_res, "tp_exit_slippage_usdt", 0.0)
    fees_total = getattr(calc_res, "fees_total_usdt", entry_fee + sl_exit_fee + tp_exit_fee)
    slip_total = getattr(calc_res, "slippage_total_usdt", entry_slip + sl_exit_slip + tp_exit_slip)

    maker_fee_rate = getattr(calc_res, "maker_fee_rate", 0.0002)
    taker_fee_rate = getattr(calc_res, "taker_fee_rate", 0.0004)

    return OrderPlanSnapshot({
        "phase": "ARMED_ESTIMATE",
        "pricing_mode": "FROZEN_ORDER",
        "order_type": order_type,
        "direction": direction,
        "planned_entry": planned_entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "quantity": qty,
        "quantity_mode": "FIXED_QUANTITY",
        "gross_rr": gross_rr,
        "net_rr": net_rr,
        "initial_risk_usdt": net_risk,
        "initial_risk_usd": net_risk,
        "net_risk_usdt": net_risk,
        "risk_usdt": net_risk,
        "gross_loss_usdt": gross_loss,
        "gross_reward_usdt": gross_reward,
        "net_reward_usdt": net_reward,
        "entry_fee_usdt": entry_fee,
        "sl_exit_fee_usdt": sl_exit_fee,
        "tp_exit_fee_usdt": tp_exit_fee,
        "entry_slippage_usdt": entry_slip,
        "sl_exit_slippage_usdt": sl_exit_slip,
        "tp_exit_slippage_usdt": tp_exit_slip,
        "fees_total_usdt": fees_total,
        "slippage_total_usdt": slip_total,
        "maker_fee_rate": maker_fee_rate,
        "taker_fee_rate": taker_fee_rate,
        "slippage": resolved_arm_slip,
        "slippage_usd": resolved_arm_slip,
        "multiplier": multiplier,
        "capital_usdt": capital_usdt,
        "equity_usdt": capital_usdt,
        "risk_pct": risk_pct,
        "effective_risk_pct": risk_pct,
        "leverage": leverage,
        "margin_mode": margin_mode,
        "estimated_liquidation": getattr(calc_res, "estimated_liquidation", None),
        "cost_source": cost_source,
        "policy_config_version": policy_config_version,
        "cost_version": policy_config_version,
        "risk_config_version": risk_config_version,
        "calculated_at": now_ms,
        "equity_as_of_ms": now_ms,
        "capital_mode": capital_mode,
        "target_capital": target_capital if target_capital is not None else capital_usdt,
        "target_risk_budget": target_risk_budget if target_risk_budget is not None else net_risk,
        "account_capital_at_fill": capital_usdt,
        "executable_risk_budget": net_risk,
        "actual_risk_after_rounding": net_risk,
        "sizing_adjustment_reason": "NONE",
        # Rule 040/042/043: Persist the effective min_net_rr at ARM time so the
        # fill coordinator can re-validate with the same threshold instead of the
        # calculator default (2.0).  None means "use canonical config at fill time".
        "min_net_rr_at_arm": min_net_rr if min_net_rr is not None else getattr(calc_res, "min_net_rr", None),
    })


def extract_immutable_entry_metadata(arm_snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    V16.39.4 Sections 049 - 052:
    Pure extraction of immutable entry session, timestamps, and binding metadata from arm_snapshot.
    Strictly preserves provenance across fill transitions without copying dynamic or estimated financial fields.
    """
    if not arm_snapshot or not isinstance(arm_snapshot, dict):
        return {}

    whitelist_keys = (
        "entry_session",
        "session",
        "analysis_session",
        "session_policy_version",
        "decision_as_of_ms",
        "armed_at_ms",
        "execution_profile",
        "setup_id",
        "setup_instance_id",
        "signal_id",
        "idempotency_key",
        "min_net_rr_at_arm",
        "entry_mode",
        "order_type",
        "quality_assessment",
        "stop_placement",
        "stop_comparison",
        "stop_comparison_plan_id",
        "stop_mode_at_arm",
        "stop_policy_version",
        "applied_variant",
    )

    out = {}
    for k in whitelist_keys:
        if k in arm_snapshot and arm_snapshot[k] is not None:
            if k in ("quality_assessment", "stop_placement", "stop_comparison"):
                import copy
                out[k] = copy.deepcopy(arm_snapshot[k])
            else:
                out[k] = arm_snapshot[k]

    # Ensure canonical session mapping
    session_val = arm_snapshot.get("entry_session") or arm_snapshot.get("analysis_session") or arm_snapshot.get("session")
    if session_val:
        out["entry_session"] = session_val
        out["analysis_session"] = session_val
        out["session"] = session_val

    return out


def build_fill_plan_snapshot(
    calc_result: Any,
    order: Any,
    fill_price: float,
    *,
    now_ms: int,
    cost_source: str = "BITGET_PAPER_MODEL_V10_4",
    sizing_context: Optional[Dict[str, Any]] = None,
) -> OrderPlanSnapshot:
    """Builds canonical frozen financial snapshot at FILL stage, preserving arm refs and legacy keys."""
    qty = getattr(calc_result, "quantity", getattr(order, "quantity", 0.0))
    multiplier = getattr(calc_result, "multiplier", 1.0)
    net_rr = getattr(calc_result, "net_rr", getattr(order, "estimated_net_rr", None))
    gross_rr = getattr(calc_result, "gross_rr", getattr(order, "gross_rr", None))
    net_risk = getattr(calc_result, "net_risk_usdt", getattr(order, "initial_risk_usdt", None))
    gross_loss = getattr(calc_result, "gross_loss_usdt", None)
    gross_reward = getattr(calc_result, "gross_reward_usdt", None)
    net_reward = getattr(calc_result, "net_reward_usdt", None)

    entry_fee = getattr(calc_result, "entry_fee_usdt", 0.0)
    sl_exit_fee = getattr(calc_result, "sl_exit_fee_usdt", 0.0)
    tp_exit_fee = getattr(calc_result, "tp_exit_fee_usdt", 0.0)
    entry_slip = getattr(calc_result, "entry_slippage_usdt", 0.0)
    sl_exit_slip = getattr(calc_result, "sl_exit_slippage_usdt", 0.0)
    tp_exit_slip = getattr(calc_result, "tp_exit_slippage_usdt", 0.0)
    fees_total = getattr(calc_result, "fees_total_usdt", entry_fee + sl_exit_fee + tp_exit_fee)
    slip_total = getattr(calc_result, "slippage_total_usdt", entry_slip + sl_exit_slip + tp_exit_slip)

    maker_fee_rate = getattr(calc_result, "maker_fee_rate", 0.0002)
    taker_fee_rate = getattr(calc_result, "taker_fee_rate", 0.0004)

    tp_slip_usd = (
        tp_exit_slip / (qty * multiplier)
        if (qty > 0 and multiplier > 0)
        else 0.0
    )

    # Read existing arm snapshot if present to preserve planned context
    arm_dict = {}
    if getattr(order, "cost_snapshot", None):
        try:
            arm_dict = json.loads(order.cost_snapshot) if isinstance(order.cost_snapshot, str) else dict(order.cost_snapshot)
        except Exception:
            arm_dict = {}

    from services.cg01_types import resolve_slippage_usd
    arm_slip = arm_dict.get("slippage_usd") if arm_dict else None
    calc_slip = getattr(calc_result, "slippage_usd", None)
    raw_fill_slip = calc_slip if calc_slip is not None else arm_slip
    resolved_fill_slip = resolve_slippage_usd(raw_fill_slip, default=0.10)

    equity = getattr(order, "capital_usdt", None) or arm_dict.get("equity_usdt") or 1000.0

    risk_pct_val = getattr(order, "risk_pct", None) if getattr(order, "risk_pct", None) is not None else arm_dict.get("risk_pct")

    fill_dict = {
        # Legacy keys required by exit monitor and lifecycle calculators
        "phase": "FILLED_ESTIMATE",
        "maker_fee_rate": maker_fee_rate,
        "taker_fee_rate": taker_fee_rate,
        "slippage": resolved_fill_slip,
        "slippage_usd": resolved_fill_slip,
        "tp_slippage_usd": tp_slip_usd,
        "multiplier": multiplier,
        "entry_fee_usdt": entry_fee,
        "sl_exit_fee_usdt": sl_exit_fee,
        "tp_exit_fee_usdt": tp_exit_fee,
        "entry_slippage_usdt": entry_slip,
        "sl_exit_slippage_usdt": sl_exit_slip,
        "tp_exit_slippage_usdt": tp_exit_slip,
        "fees_total_usdt": fees_total,
        "slippage_total_usdt": slip_total,
        "source": cost_source,
        # Canonical full financial and geometry keys
        "pricing_mode": "FROZEN_ORDER",
        "order_id": getattr(order, "id", None),
        "direction": getattr(order, "direction", "LONG"),
        "actual_entry": fill_price,
        "planned_entry": getattr(order, "planned_entry", fill_price),
        "stop_loss": getattr(order, "stop_loss", None),
        "take_profit": getattr(order, "take_profit", None),
        "quantity": qty,
        "quantity_mode": "FIXED_QUANTITY",
        "gross_rr": gross_rr,
        "net_rr": net_rr,
        "initial_risk_usdt": net_risk,
        "initial_risk_usd": net_risk,
        "net_risk_usdt": net_risk,
        "risk_usdt": net_risk,
        "gross_loss_usdt": gross_loss,
        "gross_reward_usdt": gross_reward,
        "net_reward_usdt": net_reward,
        "capital_usdt": equity,
        "equity_usdt": equity,
        "risk_pct": risk_pct_val,
        "effective_risk_pct": getattr(order, "effective_risk_pct", None) if getattr(order, "effective_risk_pct", None) is not None else arm_dict.get("effective_risk_pct"),
        "leverage": getattr(calc_result, "leverage", getattr(order, "leverage", 5)),
        "margin_mode": getattr(calc_result, "margin_mode", getattr(order, "margin_mode", "ISOLATED")),
        "initial_margin_usdt": getattr(calc_result, "initial_margin_usdt", None),
        "estimated_liquidation": getattr(calc_result, "estimated_liquidation", getattr(order, "estimated_liquidation", None)),
        "cost_source": cost_source,
        "cost_version": getattr(order, "policy_config_version", None) or getattr(order, "config_version", 1),
        "risk_config_version": getattr(order, "config_version", 1),
        "calculated_at": now_ms,
        "equity_as_of_ms": getattr(order, "opened_at", None) or now_ms,
        "capital_mode": (sizing_context or {}).get("capital_mode") or arm_dict.get("capital_mode", "CURRENT_ACCOUNT_BALANCE"),
        "target_capital": (sizing_context or {}).get("target_capital") if (sizing_context and "target_capital" in sizing_context) else arm_dict.get("target_capital"),
        "target_risk_budget": (sizing_context or {}).get("target_budget") if (sizing_context and "target_budget" in sizing_context) else arm_dict.get("target_risk_budget"),
        "account_capital_at_fill": (sizing_context or {}).get("account_capital_at_fill") or equity,
        "executable_risk_budget": (sizing_context or {}).get("executable_budget") or net_risk,
        "actual_risk_after_rounding": net_risk,
        "sizing_adjustment_reason": (sizing_context or {}).get("adjustment_reason") or arm_dict.get("sizing_adjustment_reason", "NONE"),
        # Rule 031: Preserve min_net_rr_at_arm from arm stage for historical audit trail
        "min_net_rr_at_arm": arm_dict.get("min_net_rr_at_arm"),
    }

    # V16.39.4 Sections 049 - 052: Inject immutable entry metadata into fill plan snapshot
    fill_dict.update(extract_immutable_entry_metadata(arm_dict))

    return OrderPlanSnapshot(fill_dict)


def select_bound_order_for_setup(
    setup: Any,
    candidates: Sequence[Any],
) -> Optional[Any]:
    """
    Selects the single authoritative PaperOrder bound to a WatchSetup.
    Strict Invariants:
    1. setup_id must match.
    2. direction must match: a SHORT order cannot bind to a LONG setup.
    3. setup_instance_id must match when present on both.
    4. Active orders (armed, paper_open) are prioritized over historical terminal orders.
    5. If setup is in READY state, historical terminal orders do not bind.
    6. If multiple conflicting active orders exist for the same setup -> returns None (DISPLAY_BINDING_CONFLICT).
    """
    setup_id = getattr(setup, "id", None) if not isinstance(setup, dict) else setup.get("id")
    setup_dir = getattr(setup, "direction", None) if not isinstance(setup, dict) else setup.get("direction")
    setup_inst = getattr(setup, "setup_instance_id", None) if not isinstance(setup, dict) else setup.get("setup_instance_id")
    setup_state = str(getattr(setup, "state", "WATCHING") if not isinstance(setup, dict) else setup.get("state", "WATCHING")).upper()

    matching = []
    for o in candidates:
        o_setup_id = getattr(o, "setup_id", None)
        if o_setup_id != setup_id:
            continue

        o_dir = getattr(o, "direction", None)
        if setup_dir and o_dir and o_dir != setup_dir:
            # Direction mismatch: reject binding!
            continue

        o_inst = getattr(o, "setup_instance_id", None)
        # Strict instance binding (PHẦN 043, 224 / BINDNULL01):
        # If either setup or order specifies an instance, they MUST match exactly.
        if (setup_inst is not None or o_inst is not None) and setup_inst != o_inst:
            continue

        matching.append(o)

    if not matching:
        return None

    # Check active orders
    active_matches = [o for o in matching if getattr(o, "state", "") in ("armed", "paper_open")]
    if len(active_matches) > 1:
        # Check if active matches conflict in direction or instance
        distinct_dirs = {getattr(o, "direction", "") for o in active_matches}
        distinct_insts = {getattr(o, "setup_instance_id", "") for o in active_matches if getattr(o, "setup_instance_id", "")}
        if len(distinct_dirs) > 1 or len(distinct_insts) > 1:
            logger.warning(f"Binding conflict for setup {setup_id}: multiple active orders with conflicting properties")
            return None
        # Sort by created_at desc
        active_matches.sort(key=lambda x: getattr(x, "created_at", 0) or 0, reverse=True)
        return active_matches[0]
    elif len(active_matches) == 1:
        return active_matches[0]

    # If setup is READY and has no active orders, historical terminal orders should NOT override it
    if setup_state in ("READY", "WAITING", "WATCHING"):
        return None

    # Sort terminal matches by created_at desc
    matching.sort(key=lambda x: getattr(x, "created_at", 0) or 0, reverse=True)
    return matching[0]


def resolve_order_display_snapshot(order: Any) -> Dict[str, Any]:
    """Extracts the frozen financial snapshot dictionary from a PaperOrder."""
    cost_dict = {}
    raw_snap = getattr(order, "cost_snapshot", None)
    if raw_snap:
        try:
            cost_dict = json.loads(raw_snap) if isinstance(raw_snap, str) else dict(raw_snap)
        except Exception:
            cost_dict = {}

    order_state = getattr(order, "state", "armed")
    is_open = order_state in ("paper_open", "closed")
    entry_price = getattr(order, "actual_entry", None) if is_open and getattr(order, "actual_entry", None) is not None else getattr(order, "planned_entry", None)

    return {
        "order_id": getattr(order, "id", None),
        "direction": getattr(order, "direction", None),
        "order_type": getattr(order, "order_type", "LIMIT"),
        "state": order_state,
        "entry": entry_price,
        "planned_entry": getattr(order, "planned_entry", None),
        "actual_entry": getattr(order, "actual_entry", None),
        "stop_loss": getattr(order, "stop_loss", None),
        "take_profit": getattr(order, "take_profit", None),
        "gross_rr": getattr(order, "gross_rr", None) or cost_dict.get("gross_rr"),
        "net_rr": getattr(order, "estimated_net_rr", None) or cost_dict.get("net_rr"),
        "risk_usdt": getattr(order, "initial_risk_usdt", None) or cost_dict.get("risk_usdt"),
        "net_risk_usdt": getattr(order, "initial_risk_usdt", None) or cost_dict.get("net_risk_usdt"),
        "quantity": getattr(order, "quantity", None) or cost_dict.get("quantity"),
        "leverage": getattr(order, "leverage", None) or cost_dict.get("leverage"),
        "margin_mode": getattr(order, "margin_mode", None) or cost_dict.get("margin_mode"),
        "estimated_liquidation": getattr(order, "estimated_liquidation", None) or cost_dict.get("estimated_liquidation"),
        "gross_loss_usdt": cost_dict.get("gross_loss_usdt"),
        "gross_reward_usdt": cost_dict.get("gross_reward_usdt"),
        "net_reward_usdt": cost_dict.get("net_reward_usdt"),
        "multiplier": cost_dict.get("multiplier", 1.0),
        "risk_pct": getattr(order, "risk_pct", None) or cost_dict.get("risk_pct"),
        "effective_risk_pct": getattr(order, "effective_risk_pct", None) or cost_dict.get("effective_risk_pct"),
        "entry_fee_usdt": cost_dict.get("entry_fee_usdt"),
        "sl_exit_fee_usdt": cost_dict.get("sl_exit_fee_usdt"),
        "tp_exit_fee_usdt": cost_dict.get("tp_exit_fee_usdt"),
        "entry_slippage_usdt": cost_dict.get("entry_slippage_usdt"),
        "sl_exit_slippage_usdt": cost_dict.get("sl_exit_slippage_usdt"),
        "tp_exit_slippage_usdt": cost_dict.get("tp_exit_slippage_usdt"),
        "cost_source": cost_dict.get("cost_source", "FROZEN_ORDER_SNAPSHOT"),
        "equity_as_of_ms": getattr(order, "opened_at", None) or getattr(order, "armed_at", None) or getattr(order, "created_at", None),
        "cost_version": getattr(order, "policy_config_version", None) or getattr(order, "config_version", 1),
        "risk_config_version": getattr(order, "config_version", 1),
    }
