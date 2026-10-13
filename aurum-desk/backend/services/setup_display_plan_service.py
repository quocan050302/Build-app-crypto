"""
backend/services/setup_display_plan_service.py
V14.4.2 — Builds canonical resolved_display_plan DTO for WatchSetup / Upcoming API.
Provides single authoritative source of truth for cards, chart overlay, and copy actions.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from domain_calculator import calculate_risk_reward, validate_price_geometry
from services.plan_resolver import resolve_plan_levels
from services.plan_financial_context_service import (
    PlanFinancialContext,
    calculate_display_financial,
    financial_value_is_known,
)


def build_setup_display_plan(
    setup: Any,
    quality_summary: Optional[str] = None,
    *,
    financial_context: Optional[PlanFinancialContext] = None,
    pricing_mode: str = "CURRENT_PREVIEW",
    frozen_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Builds the canonical resolved_display_plan DTO for upcoming setup items.
    Enforces:
    - Atomic price triple via resolve_plan_levels (no mixing confirmed entry with provisional SL/TP).
    - If plan is invalid or geometry fails:
      take_profit = None, financial = None, is_valid = False.
    - If plan is valid:
      financial estimates are populated without fake defaults, using explicit financial_context.
      If financial_context is absent and uncomputed, financial = None without raising TypeError.
    """
    def _get(field: str, default: Any = None) -> Any:
        if isinstance(setup, dict):
            return setup.get(field, default)
        return getattr(setup, field, default)

    setup_id = _get("id")
    instance_id = _get("setup_instance_id")
    revision = _get("version", 1)
    timeframe = _get("timeframe", "15M")
    direction = _get("direction")
    state = _get("state", "WATCHING")
    updated_at = _get("updated_at") or _get("created_at")

    # Atomic plan resolution
    resolution = resolve_plan_levels(setup)

    finite_price = lambda value: (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )

    diagnostic_prices = {
        "provisional_entry": _get("provisional_entry"),
        "provisional_sl": _get("provisional_sl"),
        "provisional_tp": _get("provisional_tp"),
        "confirmed_entry": _get("confirmed_entry"),
        "confirmed_sl": _get("confirmed_sl"),
        "confirmed_tp": _get("confirmed_tp"),
    }

    if not resolution.is_valid:
        err_code = resolution.error_code or "INVALID_PLAN"
        err_msg = resolution.error_message or "Kế hoạch giá chưa hợp lệ."
        if "SHORT" in err_msg and "Take Profit" in err_msg:
            err_msg = "TP lệnh bán đang cao hơn Entry; setup chưa hợp lệ"
        elif "LONG" in err_msg and "Take Profit" in err_msg:
            err_msg = "TP lệnh mua đang thấp hơn Entry; setup chưa hợp lệ"

        entry_val = _get("confirmed_entry") if resolution.level_source == "confirmed" else _get("provisional_entry")
        sl_val = _get("confirmed_sl") if resolution.level_source == "confirmed" else _get("provisional_sl")

        return {
            "identity": {
                "setup_id": setup_id,
                "instance_id": instance_id,
                "revision": revision,
                "timeframe": timeframe,
            },
            "status": "INVALID_GEOMETRY" if "GEOMETRY" in err_code else err_code,
            "is_valid": False,
            "reason_code": err_code,
            "reason_message": err_msg,
            "direction": direction,
            "level_source": resolution.level_source,
            "entry": float(entry_val) if finite_price(entry_val) else None,
            "stop_loss": float(sl_val) if finite_price(sl_val) else None,
            "take_profit": None,  # Strictly None for invalid plan
            "as_of_ms": updated_at,
            "price_basis": "CLOSED_REFERENCE",
            "target_source_id": _get("evidence_snapshot_id"),
            "target_cutoff_ms": updated_at,
            "financial": None,  # Strictly None, no fake defaults
            "diagnostic_prices": diagnostic_prices,
        }

    # Resolution is valid
    entry = float(resolution.entry)
    stop_loss = float(resolution.stop_loss)
    take_profit = float(resolution.take_profit)
    level_source = resolution.level_source

    is_frozen_order = str(state).upper() in ("ARMED", "PAPER_OPEN", "CLOSED") or pricing_mode == "FROZEN_ORDER"
    if is_frozen_order and frozen_snapshot is not None and isinstance(frozen_snapshot, dict):
        frozen_entry = frozen_snapshot.get("entry")
        if frozen_entry is None:
            if str(state).upper() in ("PAPER_OPEN", "CLOSED"):
                frozen_entry = frozen_snapshot.get("actual_entry")
            else:
                frozen_entry = frozen_snapshot.get("planned_entry")
        if frozen_entry is not None and finite_price(frozen_entry):
            entry = float(frozen_entry)
        if frozen_snapshot.get("stop_loss") is not None and finite_price(frozen_snapshot["stop_loss"]):
            stop_loss = float(frozen_snapshot["stop_loss"])
        if frozen_snapshot.get("take_profit") is not None and finite_price(frozen_snapshot["take_profit"]):
            take_profit = float(frozen_snapshot["take_profit"])

    # Read stored financial figures safely (handling None without TypeError)
    gross_rr = _get("gross_rr")
    net_rr = _get("net_rr")
    risk_usdt = _get("risk_usdt")
    quantity = _get("quantity")
    leverage = _get("leverage") or (financial_context.leverage if financial_context else 5)
    margin_mode = _get("margin_mode") or (financial_context.margin_mode if financial_context else "ISOLATED")
    est_liq = _get("estimated_liquidation")
    meets_min_rr = False

    is_frozen_order = str(state).upper() in ("ARMED", "PAPER_OPEN", "CLOSED")

    if is_frozen_order:
        stored_gross_loss = None
        stored_gross_reward = None
        stored_net_reward = None
        stored_multiplier = 1.0
        stored_risk_pct = None
        stored_eff_risk_pct = None
        stored_entry_fee = None
        stored_sl_exit_fee = None
        stored_tp_exit_fee = None
        stored_entry_slip = None
        stored_sl_exit_slip = None
        stored_tp_exit_slip = None
        stored_cost_source = "FROZEN_ORDER_SNAPSHOT"
        stored_equity_as_of = updated_at

        # Check if an explicit frozen snapshot was passed
        if frozen_snapshot is not None and isinstance(frozen_snapshot, dict):
            stored_gross_rr = frozen_snapshot.get("gross_rr")
            stored_net_rr = frozen_snapshot.get("net_rr")
            stored_risk = frozen_snapshot.get("net_risk_usdt", frozen_snapshot.get("risk_usdt"))
            stored_qty = frozen_snapshot.get("quantity")
            stored_lev = frozen_snapshot.get("leverage")
            stored_mm = frozen_snapshot.get("margin_mode")
            stored_liq = frozen_snapshot.get("estimated_liquidation")
            stored_eq = frozen_snapshot.get("equity_usdt")
            stored_cv = frozen_snapshot.get("cost_version")
            stored_iv = frozen_snapshot.get("instrument_version")
            stored_rv = frozen_snapshot.get("risk_config_version")
            stored_gross_loss = frozen_snapshot.get("gross_loss_usdt")
            stored_gross_reward = frozen_snapshot.get("gross_reward_usdt")
            stored_net_reward = frozen_snapshot.get("net_reward_usdt")
            stored_multiplier = frozen_snapshot.get("multiplier", 1.0)
            stored_risk_pct = frozen_snapshot.get("risk_pct")
            stored_eff_risk_pct = frozen_snapshot.get("effective_risk_pct", stored_risk_pct)
            stored_entry_fee = frozen_snapshot.get("entry_fee_usdt")
            stored_sl_exit_fee = frozen_snapshot.get("sl_exit_fee_usdt")
            stored_tp_exit_fee = frozen_snapshot.get("tp_exit_fee_usdt")
            stored_entry_slip = frozen_snapshot.get("entry_slippage_usdt")
            stored_sl_exit_slip = frozen_snapshot.get("sl_exit_slippage_usdt")
            stored_tp_exit_slip = frozen_snapshot.get("tp_exit_slippage_usdt")
            stored_cost_source = frozen_snapshot.get("cost_source", "FROZEN_ORDER_SNAPSHOT")
            stored_equity_as_of = frozen_snapshot.get("equity_as_of_ms", updated_at)
        else:
            stored_gross_rr = _get("gross_rr")
            stored_net_rr = _get("net_rr")
            stored_risk = _get("risk_usdt")
            stored_qty = _get("quantity")
            stored_lev = _get("leverage")
            stored_mm = _get("margin_mode")
            stored_liq = _get("estimated_liquidation")
            stored_eq = _get("initial_equity") or _get("equity_usdt")
            stored_cv = _get("cost_version")
            stored_iv = _get("instrument_version")
            stored_rv = _get("risk_config_version") or _get("config_version")
            raw_cs = _get("cost_snapshot")
            if raw_cs is not None:
                if isinstance(raw_cs, str):
                    import json
                    try:
                        raw_cs = json.loads(raw_cs)
                    except Exception:
                        raw_cs = {}
                if isinstance(raw_cs, dict):
                    stored_gross_loss = raw_cs.get("gross_loss_usdt")
                    stored_gross_reward = raw_cs.get("gross_reward_usdt")
                    stored_net_reward = raw_cs.get("net_reward_usdt")
                    stored_multiplier = raw_cs.get("multiplier", 1.0)
                    stored_risk_pct = raw_cs.get("risk_pct")
                    stored_eff_risk_pct = raw_cs.get("effective_risk_pct", stored_risk_pct)
                    stored_entry_fee = raw_cs.get("entry_fee_usdt")
                    stored_sl_exit_fee = raw_cs.get("sl_exit_fee_usdt")
                    stored_tp_exit_fee = raw_cs.get("tp_exit_fee_usdt")
                    stored_entry_slip = raw_cs.get("entry_slippage_usdt")
                    stored_sl_exit_slip = raw_cs.get("sl_exit_slippage_usdt")
                    stored_tp_exit_slip = raw_cs.get("tp_exit_slippage_usdt")
                    stored_cost_source = raw_cs.get("cost_source", "FROZEN_ORDER_SNAPSHOT")
                    stored_equity_as_of = raw_cs.get("equity_as_of_ms", updated_at)

        has_complete_metrics = (
            financial_value_is_known(stored_net_rr)
            and financial_value_is_known(stored_qty)
            and float(stored_qty) > 0
        )

        if has_complete_metrics:
            status = "FROZEN_ORDER"
            reason_code = "ORDER_FROZEN"
            reason_msg = "Lệnh đã kích hoạt; thông số tài chính được bảo toàn"
            meets_min_rr = float(stored_net_rr) >= 2.0
            financial_dto = {
                "gross_rr": round(float(stored_gross_rr), 4) if financial_value_is_known(stored_gross_rr) else None,
                "net_rr": round(float(stored_net_rr), 4),
                "gross_loss_usdt": round(float(stored_gross_loss), 2) if financial_value_is_known(stored_gross_loss) else None,
                "gross_reward_usdt": round(float(stored_gross_reward), 2) if financial_value_is_known(stored_gross_reward) else None,
                "net_risk_usdt": round(float(stored_risk), 2) if financial_value_is_known(stored_risk) else None,
                "net_reward_usdt": round(float(stored_net_reward), 2) if financial_value_is_known(stored_net_reward) else None,
                "quantity": round(float(stored_qty), 4),
                "multiplier": float(stored_multiplier) if financial_value_is_known(stored_multiplier) else 1.0,
                "leverage": int(stored_lev) if stored_lev else 5,
                "margin_mode": str(stored_mm or "ISOLATED"),
                "risk_pct": float(stored_risk_pct) if financial_value_is_known(stored_risk_pct) else None,
                "effective_risk_pct": float(stored_eff_risk_pct) if financial_value_is_known(stored_eff_risk_pct) else None,
                "entry_fee_usdt": round(float(stored_entry_fee), 4) if financial_value_is_known(stored_entry_fee) else None,
                "sl_exit_fee_usdt": round(float(stored_sl_exit_fee), 4) if financial_value_is_known(stored_sl_exit_fee) else None,
                "tp_exit_fee_usdt": round(float(stored_tp_exit_fee), 4) if financial_value_is_known(stored_tp_exit_fee) else None,
                "entry_slippage_usdt": round(float(stored_entry_slip), 4) if financial_value_is_known(stored_entry_slip) else None,
                "sl_exit_slippage_usdt": round(float(stored_sl_exit_slip), 4) if financial_value_is_known(stored_sl_exit_slip) else None,
                "tp_exit_slippage_usdt": round(float(stored_tp_exit_slip), 4) if financial_value_is_known(stored_tp_exit_slip) else None,
                "estimated_liquidation": round(float(stored_liq), 2) if financial_value_is_known(stored_liq) else None,
                "meets_min_rr": meets_min_rr,
                "equity_usdt": float(stored_eq) if financial_value_is_known(stored_eq) else None,
                "equity_basis": "FROZEN_SNAPSHOT",
                "equity_as_of_ms": stored_equity_as_of,
                "cost_source": str(stored_cost_source) if stored_cost_source else "FROZEN_ORDER_SNAPSHOT",
                "cost_version": str(stored_cv) if stored_cv else "frozen_snapshot",
                "instrument_version": str(stored_iv) if stored_iv else "frozen_snapshot",
                "risk_config_version": int(stored_rv) if stored_rv else 1,
                "calculator_version": "v10.4_canonical_calculator",
                "pricing_mode": "FROZEN_ORDER",
                "financial_status": "FROZEN",
            }
        else:
            status = "FINANCIAL_UNAVAILABLE"
            reason_code = "ORDER_FINANCIAL_UNAVAILABLE"
            reason_msg = "Lệnh lịch sử chưa đầy đủ thông số tài chính"
            financial_dto = {
                "gross_rr": round(float(stored_gross_rr), 4) if financial_value_is_known(stored_gross_rr) else None,
                "net_rr": round(float(stored_net_rr), 4) if financial_value_is_known(stored_net_rr) else None,
                "gross_loss_usdt": None,
                "gross_reward_usdt": None,
                "net_risk_usdt": round(float(stored_risk), 2) if financial_value_is_known(stored_risk) else None,
                "net_reward_usdt": None,
                "quantity": round(float(stored_qty), 4) if financial_value_is_known(stored_qty) else None,
                "multiplier": 1.0,
                "leverage": int(stored_lev) if stored_lev else 5,
                "margin_mode": str(stored_mm or "ISOLATED"),
                "risk_pct": None,
                "effective_risk_pct": None,
                "entry_fee_usdt": None,
                "sl_exit_fee_usdt": None,
                "tp_exit_fee_usdt": None,
                "entry_slippage_usdt": None,
                "sl_exit_slippage_usdt": None,
                "tp_exit_slippage_usdt": None,
                "estimated_liquidation": None,
                "meets_min_rr": False,
                "equity_usdt": None,
                "equity_basis": "UNKNOWN",
                "equity_as_of_ms": updated_at,
                "cost_source": "FROZEN_ORDER_UNAVAILABLE",
                "cost_version": None,
                "instrument_version": None,
                "risk_config_version": None,
                "calculator_version": "v10.4_canonical_calculator",
                "pricing_mode": "FROZEN_ORDER",
                "financial_status": "FINANCIAL_UNAVAILABLE",
            }

    elif financial_context is not None:
        # Canonical dynamic recalculation using explicit shared financial context
        calc = calculate_display_financial(
            direction=direction,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            context=financial_context,
        )
        if calc.is_valid:
            gross_rr = calc.gross_rr
            net_rr = calc.net_rr
            risk_usdt = calc.net_risk_usdt
            quantity = calc.quantity
            est_liq = calc.estimated_liquidation
            meets_min_rr = bool(calc.meets_min_rr)
            status = "AVAILABLE" if meets_min_rr else "AVAILABLE_BLOCKED"
            if level_source == "confirmed":
                status = "CONFIRMED"
            reason_code = "PLAN_VALID" if meets_min_rr else "NET_RR_TOO_LOW"
            reason_msg = "Kế hoạch giá hợp lệ" if meets_min_rr else f"Tỷ lệ Net R:R 1:{net_rr:.2f} chưa đạt ngưỡng tối thiểu 1:{financial_context.min_net_rr:.1f}"

            financial_dto = {
                "gross_rr": round(float(gross_rr), 4),
                "net_rr": round(float(net_rr), 4),
                "gross_loss_usdt": round(float(calc.gross_loss_usdt), 2) if hasattr(calc, "gross_loss_usdt") else None,
                "gross_reward_usdt": round(float(calc.gross_reward_usdt), 2) if hasattr(calc, "gross_reward_usdt") else None,
                "net_risk_usdt": round(float(risk_usdt), 2),
                "net_reward_usdt": round(float(calc.net_reward_usdt), 2) if hasattr(calc, "net_reward_usdt") else None,
                "quantity": round(float(quantity), 4),
                "multiplier": float(getattr(calc, "multiplier", 1.0)),
                "leverage": int(financial_context.leverage),
                "margin_mode": str(financial_context.margin_mode or "ISOLATED"),
                "risk_pct": float(financial_context.risk_pct),
                "effective_risk_pct": round(float(getattr(calc, "effective_risk_pct", financial_context.risk_pct)), 4),
                "entry_fee_usdt": round(float(getattr(calc, "entry_fee_usdt", 0.0)), 4),
                "sl_exit_fee_usdt": round(float(getattr(calc, "sl_exit_fee_usdt", 0.0)), 4),
                "tp_exit_fee_usdt": round(float(getattr(calc, "tp_exit_fee_usdt", 0.0)), 4),
                "entry_slippage_usdt": round(float(getattr(calc, "entry_slippage_usdt", 0.0)), 4),
                "sl_exit_slippage_usdt": round(float(getattr(calc, "sl_exit_slippage_usdt", 0.0)), 4),
                "tp_exit_slippage_usdt": round(float(getattr(calc, "tp_exit_slippage_usdt", 0.0)), 4),
                "estimated_liquidation": round(float(est_liq), 2) if financial_value_is_known(est_liq) else None,
                "meets_min_rr": meets_min_rr,
                "equity_usdt": financial_context.equity_usdt,
                "equity_basis": financial_context.equity_basis,
                "equity_as_of_ms": financial_context.equity_as_of_ms,
                "cost_source": getattr(calc, "cost_source", "BITGET_PAPER_MODEL_V10_4"),
                "cost_version": financial_context.cost_version,
                "instrument_version": financial_context.instrument_version,
                "risk_config_version": financial_context.risk_config_version,
                "calculator_version": "v10.4_canonical_calculator",
                "pricing_mode": pricing_mode,
                "financial_status": "AVAILABLE" if meets_min_rr else "AVAILABLE_BLOCKED",
            }
        else:
            status = "AVAILABLE_BLOCKED"
            reason_code = calc.invalid_reason or "CALCULATION_INVALID"
            reason_msg = calc.invalid_reason or "Không thể tính toán kế hoạch tài chính"
            financial_dto = None
    else:
        # No financial context provided: do NOT guess 1000.0
        stored_net_rr = _get("net_rr")
        stored_gross_rr = _get("gross_rr")
        stored_risk = _get("risk_usdt")
        stored_qty = _get("quantity")
        stored_lev = _get("leverage")
        stored_mm = _get("margin_mode")
        stored_liq = _get("estimated_liquidation")

        if financial_value_is_known(stored_net_rr) and financial_value_is_known(stored_qty) and stored_qty > 0 and stored_net_rr > 0:
            meets_min_rr = stored_net_rr >= 2.0
            status = "AVAILABLE" if meets_min_rr else "AVAILABLE_BLOCKED"
            if level_source == "confirmed":
                status = "CONFIRMED"
            reason_code = "PLAN_VALID" if meets_min_rr else "NET_RR_TOO_LOW"
            reason_msg = "Kế hoạch giá hợp lệ" if meets_min_rr else f"Tỷ lệ Net R:R 1:{stored_net_rr:.2f} chưa đạt ngưỡng tối thiểu 1:2.0"
            financial_dto = {
                "gross_rr": round(float(stored_gross_rr), 4) if financial_value_is_known(stored_gross_rr) else None,
                "net_rr": round(float(stored_net_rr), 4),
                "gross_loss_usdt": None,
                "gross_reward_usdt": None,
                "net_risk_usdt": round(float(stored_risk), 2) if financial_value_is_known(stored_risk) else None,
                "net_reward_usdt": None,
                "quantity": round(float(stored_qty), 4),
                "multiplier": 1.0,
                "leverage": int(stored_lev) if stored_lev else 5,
                "margin_mode": str(stored_mm or "ISOLATED"),
                "risk_pct": None,
                "effective_risk_pct": None,
                "entry_fee_usdt": None,
                "sl_exit_fee_usdt": None,
                "tp_exit_fee_usdt": None,
                "entry_slippage_usdt": None,
                "sl_exit_slippage_usdt": None,
                "tp_exit_slippage_usdt": None,
                "estimated_liquidation": round(float(stored_liq), 2) if financial_value_is_known(stored_liq) else None,
                "meets_min_rr": meets_min_rr,
                "equity_usdt": None,
                "equity_basis": "STORED_LEGACY",
                "equity_as_of_ms": updated_at,
                "cost_source": "STORED_LEGACY",
                "cost_version": None,
                "instrument_version": None,
                "risk_config_version": None,
                "calculator_version": "v10.4_canonical_calculator",
                "pricing_mode": "STORED_PREVIEW",
                "financial_status": "AVAILABLE" if meets_min_rr else "AVAILABLE_BLOCKED",
            }
        else:
            status = "CONTEXT_UNAVAILABLE"
            reason_code = "FINANCIAL_CONTEXT_MISSING"
            reason_msg = "Chưa có ngữ cảnh tài chính để tính toán kế hoạch"
            financial_dto = None

    plan_dto = {
        "identity": {
            "setup_id": setup_id,
            "instance_id": instance_id,
            "revision": revision,
            "timeframe": timeframe,
        },
        "status": status,
        "is_valid": True,
        "reason_code": reason_code,
        "reason_message": reason_msg,
        "direction": direction,
        "level_source": level_source,
        "entry": entry,
        "planned_entry": float(frozen_snapshot.get("planned_entry")) if (is_frozen_order and frozen_snapshot and frozen_snapshot.get("planned_entry") is not None) else entry,
        "actual_entry": float(frozen_snapshot.get("actual_entry")) if (is_frozen_order and frozen_snapshot and frozen_snapshot.get("actual_entry") is not None) else None,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "as_of_ms": updated_at,
        "price_basis": "SIGNAL_CONFIRMED" if level_source == "confirmed" else "CLOSED_REFERENCE",
        "target_source_id": _get("evidence_snapshot_id"),
        "target_cutoff_ms": updated_at,
        "financial": financial_dto,
        "diagnostic_prices": diagnostic_prices,
    }
    plan_dto["fingerprint"] = compute_plan_fingerprint(plan_dto)
    return plan_dto


def compute_plan_fingerprint(plan: Dict[str, Any]) -> str:
    """
    Computes a canonical deterministic hash (fingerprint) of a plan snapshot
    excluding volatile render timestamps and localized display labels.
    """
    import hashlib
    import json

    ident = plan.get("identity") or {}
    fin = plan.get("financial") or {}

    core = {
        "schema_version": "v14.4.2",
        "setup_id": ident.get("setup_id"),
        "instance_id": ident.get("instance_id"),
        "direction": plan.get("direction"),
        "entry": round(float(plan["entry"]), 4) if plan.get("entry") is not None else None,
        "stop_loss": round(float(plan["stop_loss"]), 4) if plan.get("stop_loss") is not None else None,
        "take_profit": round(float(plan["take_profit"]), 4) if plan.get("take_profit") is not None else None,
        "quantity": round(float(fin["quantity"]), 4) if fin.get("quantity") is not None else None,
        "net_risk_usdt": round(float(fin["net_risk_usdt"]), 4) if fin.get("net_risk_usdt") is not None else None,
        "net_rr": round(float(fin["net_rr"]), 4) if fin.get("net_rr") is not None else None,
        "leverage": fin.get("leverage"),
        "margin_mode": fin.get("margin_mode"),
        "cost_version": fin.get("cost_version"),
        "risk_config_version": fin.get("risk_config_version"),
        "pricing_mode": fin.get("pricing_mode"),
    }
    dumped = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:16]
