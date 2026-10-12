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


def build_setup_display_plan(setup: Any, quality_summary: Optional[str] = None) -> Dict[str, Any]:
    """
    Builds the canonical resolved_display_plan DTO for upcoming setup items.
    Enforces:
    - Atomic price triple via resolve_plan_levels (no mixing confirmed entry with provisional SL/TP).
    - If plan is invalid or geometry fails:
      take_profit = None, financial = None, is_valid = False.
    - If plan is valid:
      financial estimates are populated without fake defaults.
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
        # Check specific reason
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

    # Read or compute authoritative financial figures
    gross_rr = _get("gross_rr", 0.0)
    net_rr = _get("net_rr", 0.0)
    risk_usdt = _get("risk_usdt", 0.0)
    quantity = _get("quantity", 0.0)
    leverage = _get("leverage", 5)
    margin_mode = _get("margin_mode", "ISOLATED")
    est_liq = _get("estimated_liquidation")

    # If financial figures are missing or uncomputed, calculate them
    if (net_rr <= 0 or quantity <= 0) and finite_price(entry) and finite_price(stop_loss) and finite_price(take_profit):
        calc = calculate_risk_reward(
            direction=direction,
            planned_entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            capital_usdt=1000.0,
            risk_pct=_get("risk_pct", 0.25),
            leverage=leverage,
            margin_mode=margin_mode,
        )
        if calc.is_valid:
            gross_rr = calc.gross_rr
            net_rr = calc.net_rr
            risk_usdt = calc.net_risk_usdt
            quantity = calc.quantity
            est_liq = calc.estimated_liquidation

    meets_min_rr = (net_rr >= 2.0)
    status = "AVAILABLE" if meets_min_rr else "AVAILABLE_BLOCKED"
    if level_source == "confirmed":
        status = "CONFIRMED"
    reason_code = "PLAN_VALID" if meets_min_rr else "NET_RR_TOO_LOW"
    reason_msg = "Kế hoạch giá hợp lệ" if meets_min_rr else f"Tỷ lệ Net R:R 1:{net_rr:.2f} chưa đạt ngưỡng tối thiểu 1:2.0"

    financial_dto = {
        "gross_rr": round(float(gross_rr), 4) if gross_rr is not None else 0.0,
        "net_rr": round(float(net_rr), 4) if net_rr is not None else 0.0,
        "net_risk_usdt": round(float(risk_usdt), 2) if risk_usdt is not None else 0.0,
        "quantity": round(float(quantity), 4) if quantity is not None else 0.0,
        "leverage": int(leverage) if leverage else 5,
        "margin_mode": str(margin_mode or "ISOLATED"),
        "estimated_liquidation": round(float(est_liq), 2) if est_liq is not None else None,
        "meets_min_rr": meets_min_rr,
    }

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
