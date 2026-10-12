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
        # Keep frozen financial figures from order lifecycle without recalculating from today's equity
        gross_rr = float(gross_rr) if financial_value_is_known(gross_rr) else 0.0
        net_rr = float(net_rr) if financial_value_is_known(net_rr) else 0.0
        risk_usdt = float(risk_usdt) if financial_value_is_known(risk_usdt) else 0.0
        quantity = float(quantity) if financial_value_is_known(quantity) else 0.0
        meets_min_rr = net_rr >= (financial_context.min_net_rr if financial_context else 2.0)
        status = "FROZEN_ORDER"
        reason_code = "ORDER_FROZEN"
        reason_msg = "Lệnh đã kích hoạt; thông số tài chính được bảo toàn"
        financial_dto: Optional[Dict[str, Any]] = {
            "gross_rr": round(gross_rr, 4),
            "net_rr": round(net_rr, 4),
            "net_risk_usdt": round(risk_usdt, 2),
            "quantity": round(quantity, 4),
            "leverage": int(leverage) if leverage else 5,
            "margin_mode": str(margin_mode or "ISOLATED"),
            "estimated_liquidation": round(float(est_liq), 2) if financial_value_is_known(est_liq) else None,
            "meets_min_rr": meets_min_rr,
            "equity_usdt": financial_context.equity_usdt if financial_context else None,
            "cost_version": financial_context.cost_version if financial_context else "frozen_snapshot",
            "instrument_version": financial_context.instrument_version if financial_context else "frozen_snapshot",
            "risk_config_version": financial_context.risk_config_version if financial_context else 1,
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
                "net_risk_usdt": round(float(risk_usdt), 2),
                "quantity": round(float(quantity), 4),
                "leverage": int(leverage) if leverage else 5,
                "margin_mode": str(margin_mode or "ISOLATED"),
                "estimated_liquidation": round(float(est_liq), 2) if financial_value_is_known(est_liq) else None,
                "meets_min_rr": meets_min_rr,
                "equity_usdt": financial_context.equity_usdt,
                "cost_version": financial_context.cost_version,
                "instrument_version": financial_context.instrument_version,
                "risk_config_version": financial_context.risk_config_version,
            }
        else:
            status = "AVAILABLE_BLOCKED"
            reason_code = calc.invalid_reason or "CALCULATION_INVALID"
            reason_msg = calc.invalid_reason or "Không thể tính toán kế hoạch tài chính"
            financial_dto = None
    else:
        # No financial context provided: do NOT guess 1000.0
        # If stored metrics already exist as valid numbers, display them with explicit provenance
        if financial_value_is_known(net_rr) and financial_value_is_known(quantity) and quantity > 0 and net_rr > 0:
            meets_min_rr = net_rr >= 2.0
            status = "AVAILABLE" if meets_min_rr else "AVAILABLE_BLOCKED"
            if level_source == "confirmed":
                status = "CONFIRMED"
            reason_code = "PLAN_VALID" if meets_min_rr else "NET_RR_TOO_LOW"
            reason_msg = "Kế hoạch giá hợp lệ" if meets_min_rr else f"Tỷ lệ Net R:R 1:{net_rr:.2f} chưa đạt ngưỡng tối thiểu 1:2.0"
            financial_dto = {
                "gross_rr": round(float(gross_rr), 4) if financial_value_is_known(gross_rr) else 0.0,
                "net_rr": round(float(net_rr), 4),
                "net_risk_usdt": round(float(risk_usdt), 2) if financial_value_is_known(risk_usdt) else 0.0,
                "quantity": round(float(quantity), 4),
                "leverage": int(leverage) if leverage else 5,
                "margin_mode": str(margin_mode or "ISOLATED"),
                "estimated_liquidation": round(float(est_liq), 2) if financial_value_is_known(est_liq) else None,
                "meets_min_rr": meets_min_rr,
                "equity_usdt": None,
                "cost_version": None,
                "instrument_version": None,
                "risk_config_version": None,
            }
        else:
            # Uncomputed and no context available: return None for financial without crashing
            status = "CONTEXT_UNAVAILABLE"
            reason_code = "FINANCIAL_CONTEXT_MISSING"
            reason_msg = "Chưa có ngữ cảnh tài chính để tính toán kế hoạch"
            financial_dto = None

    return {
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
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "as_of_ms": updated_at,
        "price_basis": "SIGNAL_CONFIRMED" if level_source == "confirmed" else "CLOSED_REFERENCE",
        "target_source_id": _get("evidence_snapshot_id"),
        "target_cutoff_ms": updated_at,
        "financial": financial_dto,
        "diagnostic_prices": diagnostic_prices,
    }
