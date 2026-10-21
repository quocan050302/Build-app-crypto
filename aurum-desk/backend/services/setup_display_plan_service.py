"""
backend/services/setup_display_plan_service.py
V14.4.2 — Builds canonical resolved_display_plan DTO for WatchSetup / Upcoming API.
Provides single authoritative source of truth for cards, chart overlay, and copy actions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from domain_calculator import calculate_risk_reward, validate_price_geometry
from services.plan_resolver import resolve_plan_levels
from services.plan_financial_context_service import (
    PlanFinancialContext,
    calculate_display_financial,
    financial_value_is_known,
)


@dataclass(frozen=True)
class SelectedPlanSource:
    source_kind: str  # "FROZEN_ORDER" or "SETUP_PREVIEW"
    direction: Optional[str]
    setup_id: Optional[str]
    order_id: Optional[str]
    setup_instance_id: Optional[str]
    setup_revision: Optional[int]
    entry: Optional[float]
    planned_entry: Optional[float]
    actual_entry: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    level_source: str
    price_basis: str
    is_complete: bool
    missing_fields: List[str]
    raw_snapshot: Optional[Dict[str, Any]] = None
    as_of_ms: Optional[int] = None
    target_source_id: Optional[str] = None
    target_cutoff_ms: Optional[int] = None


@dataclass(frozen=True)
class PlanValidation:
    is_valid: bool
    reason_code: str
    reason_message: str


def finite_price(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def select_authoritative_plan_source(
    setup: Any,
    pricing_mode: str = "CURRENT_PREVIEW",
    frozen_snapshot: Optional[Dict[str, Any]] = None,
) -> SelectedPlanSource:
    """Pure selector (PHẦN 032, 133, 153):
    Extracts the authoritative price triple entirely from a single source.
    If order is bound/frozen, selects strictly from frozen_snapshot without borrowing setup levels.
    """
    def _get(field: str, default: Any = None) -> Any:
        if isinstance(setup, dict):
            return setup.get(field, default)
        return getattr(setup, field, default)

    setup_id = _get("id")
    instance_id = _get("setup_instance_id")
    revision = _get("version", 1)
    state = str(_get("state", "WATCHING")).upper()
    direction = _get("direction")
    updated_at = _get("updated_at") or _get("created_at")

    is_frozen_order = state in ("ARMED", "PAPER_OPEN", "CLOSED") or pricing_mode == "FROZEN_ORDER"
    if is_frozen_order and (frozen_snapshot is None and pricing_mode == "FROZEN_ORDER"):
        return SelectedPlanSource(
            source_kind="FROZEN_ORDER",
            direction=direction,
            setup_id=setup_id,
            order_id=None,
            setup_instance_id=instance_id,
            setup_revision=revision,
            entry=None,
            planned_entry=None,
            actual_entry=None,
            stop_loss=None,
            take_profit=None,
            level_source="frozen_order",
            price_basis="NONE",
            is_complete=False,
            missing_fields=["missing_frozen_snapshot"],
            raw_snapshot=None,
            as_of_ms=updated_at,
            target_source_id=None,
            target_cutoff_ms=updated_at,
        )

    if is_frozen_order and frozen_snapshot is not None and isinstance(frozen_snapshot, dict):
        order_direction = frozen_snapshot.get("direction") or direction
        planned_entry = frozen_snapshot.get("planned_entry")
        actual_entry = frozen_snapshot.get("actual_entry")
        entry = frozen_snapshot.get("entry")
        sl = frozen_snapshot.get("stop_loss")
        tp = frozen_snapshot.get("take_profit")

        # In FROZEN_ORDER mode, NEVER borrow provisional SL/TP or entry from setup (PHẦN 031, 092)
        if entry is None:
            if state in ("PAPER_OPEN", "CLOSED"):
                entry = actual_entry
            else:
                entry = planned_entry

        missing = []
        if entry is None or not finite_price(entry):
            missing.append("entry")
        if sl is None or not finite_price(sl):
            missing.append("stop_loss")
        if tp is None or not finite_price(tp):
            missing.append("take_profit")

        is_comp = len(missing) == 0

        # Price basis:
        basis = frozen_snapshot.get("price_basis")
        if not basis:
            if state in ("PAPER_OPEN", "CLOSED"):
                basis = "ACTUAL_FILL" if (actual_entry is not None and finite_price(actual_entry)) else "UNSPECIFIED"
            else:
                basis = "PLANNED_LIMIT"

        return SelectedPlanSource(
            source_kind="FROZEN_ORDER",
            direction=order_direction,
            setup_id=setup_id,
            order_id=frozen_snapshot.get("order_id"),
            setup_instance_id=instance_id,
            setup_revision=revision,
            entry=float(entry) if (entry is not None and finite_price(entry)) else None,
            planned_entry=float(planned_entry) if (planned_entry is not None and finite_price(planned_entry)) else None,
            actual_entry=float(actual_entry) if (actual_entry is not None and finite_price(actual_entry)) else None,
            stop_loss=float(sl) if (sl is not None and finite_price(sl)) else None,
            take_profit=float(tp) if (tp is not None and finite_price(tp)) else None,
            level_source="frozen_order",
            price_basis=basis,
            is_complete=is_comp,
            missing_fields=missing,
            raw_snapshot=frozen_snapshot,
            as_of_ms=updated_at,
            target_source_id=frozen_snapshot.get("target_source_id") or _get("evidence_snapshot_id"),
            target_cutoff_ms=updated_at,
        )

    # Setup preview source
    resolution = resolve_plan_levels(setup)
    missing = []
    if not resolution.is_valid:
        missing.append("invalid_setup_resolution")
    return SelectedPlanSource(
        source_kind="SETUP_PREVIEW",
        direction=direction,
        setup_id=setup_id,
        order_id=None,
        setup_instance_id=instance_id,
        setup_revision=revision,
        entry=float(resolution.entry) if resolution.entry is not None else None,
        planned_entry=float(resolution.entry) if resolution.entry is not None else None,
        actual_entry=None,
        stop_loss=float(resolution.stop_loss) if resolution.stop_loss is not None else None,
        take_profit=float(resolution.take_profit) if resolution.take_profit is not None else None,
        level_source=resolution.level_source,
        price_basis="CLOSED_REFERENCE",
        is_complete=resolution.is_valid,
        missing_fields=missing,
        raw_snapshot=None,
        as_of_ms=updated_at,
        target_source_id=_get("evidence_snapshot_id"),
        target_cutoff_ms=updated_at,
    )


def validate_selected_plan(selected: SelectedPlanSource) -> PlanValidation:
    """Pure validation (PHẦN 033, 154):
    Validates signed price geometry. No abs() tricks!
    LONG: stop_loss < entry < take_profit
    SHORT: take_profit < entry < stop_loss
    """
    entry = selected.entry
    sl = selected.stop_loss
    tp = selected.take_profit
    direction = selected.direction

    if entry is None or sl is None or tp is None:
        return PlanValidation(is_valid=False, reason_code="MISSING_PRICE_LEVELS", reason_message="Thiếu mức giá trong kế hoạch.")

    if not finite_price(entry) or not finite_price(sl) or not finite_price(tp):
        return PlanValidation(is_valid=False, reason_code="INVALID_PRICE_INPUT", reason_message="Mức giá không hợp lệ.")

    if direction == "LONG":
        if not (sl < entry < tp):
            msg = "TP lệnh mua đang thấp hơn Entry hoặc SL cao hơn Entry; hình học không hợp lệ."
            return PlanValidation(is_valid=False, reason_code="INVALID_GEOMETRY", reason_message=msg)
    elif direction == "SHORT":
        if not (tp < entry < sl):
            msg = "TP lệnh bán đang cao hơn Entry hoặc SL thấp hơn Entry; hình học không hợp lệ."
            return PlanValidation(is_valid=False, reason_code="INVALID_GEOMETRY", reason_message=msg)
    else:
        return PlanValidation(is_valid=False, reason_code="UNSUPPORTED_DIRECTION", reason_message=f"Hướng giao dịch không hỗ trợ: {direction}")

    return PlanValidation(is_valid=True, reason_code="VALID_PLAN", reason_message="Kế hoạch giá hợp lệ.")


def build_degraded_display_plan(
    selected: SelectedPlanSource,
    reason_code: str,
    reason_message: Optional[str] = None,
    diagnostic_prices: Optional[dict] = None,
) -> Dict[str, Any]:
    """Builds a standardized degraded display plan dictionary."""
    err_msg = reason_message or "Kế hoạch giá chưa hợp lệ hoặc thiếu dữ liệu nguồn."
    return {
        "identity": {
            "setup_id": selected.setup_id,
            "order_id": selected.order_id,
            "instance_id": selected.setup_instance_id,
            "revision": selected.setup_revision,
            "timeframe": "15M",
        },
        "status": "INVALID_GEOMETRY" if "GEOMETRY" in reason_code else reason_code,
        "is_valid": False,
        "reason_code": reason_code,
        "reason_message": err_msg,
        "direction": selected.direction,
        "level_source": selected.level_source,
        "entry": selected.entry,
        "planned_entry": selected.planned_entry,
        "actual_entry": selected.actual_entry,
        "stop_loss": selected.stop_loss,
        "take_profit": None,  # Strictly None for invalid plan
        "as_of_ms": selected.as_of_ms,
        "price_basis": selected.price_basis,
        "target_source_id": selected.target_source_id,
        "target_cutoff_ms": selected.target_cutoff_ms,
        "financial": None,  # Strictly None, no fake defaults
        "diagnostic_prices": diagnostic_prices or {},
    }


def build_setup_display_plan(
    setup: Any,
    quality_summary: Optional[str] = None,
    *,
    financial_context: Optional[PlanFinancialContext] = None,
    pricing_mode: str = "CURRENT_PREVIEW",
    frozen_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Builds the canonical resolved_display_plan DTO for upcoming setup items (PHẦN 034, 153).
    Follows strict 5-step control flow:
    1. select_authoritative_plan_source
    2. Check source completeness
    3. validate_selected_plan
    4. resolve matching financial
    5. serialize canonical DTO
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

    diagnostic_prices = {
        "provisional_entry": _get("provisional_entry"),
        "provisional_sl": _get("provisional_sl"),
        "provisional_tp": _get("provisional_tp"),
        "confirmed_entry": _get("confirmed_entry"),
        "confirmed_sl": _get("confirmed_sl"),
        "confirmed_tp": _get("confirmed_tp"),
    }

    # Step 1: Select Authoritative Source
    selected = select_authoritative_plan_source(setup, pricing_mode, frozen_snapshot)

    # Step 2: Check source completeness
    if not selected.is_complete:
        if selected.source_kind == "FROZEN_ORDER":
            err_code = "INCOMPLETE_FROZEN_SOURCE"
            err_msg = f"Snapshot lệnh chưa đầy đủ: thiếu {', '.join(selected.missing_fields)}"
        else:
            resolution = resolve_plan_levels(setup)
            err_code = resolution.error_code or "INVALID_PLAN"
            err_msg = resolution.error_message or "Kế hoạch giá chưa hợp lệ."
            if "SHORT" in err_msg and "Take Profit" in err_msg:
                err_msg = "TP lệnh bán đang cao hơn Entry; setup chưa hợp lệ"
            elif "LONG" in err_msg and "Take Profit" in err_msg:
                err_msg = "TP lệnh mua đang thấp hơn Entry; setup chưa hợp lệ"
        return build_degraded_display_plan(selected, err_code, err_msg, diagnostic_prices=diagnostic_prices)

    # Step 3: Validate Selected Plan
    validation = validate_selected_plan(selected)
    if not validation.is_valid:
        return build_degraded_display_plan(selected, validation.reason_code, validation.reason_message, diagnostic_prices=diagnostic_prices)

    # Step 4: Plan is valid!
    entry = float(selected.entry)
    stop_loss = float(selected.stop_loss)
    take_profit = float(selected.take_profit)
    level_source = selected.level_source
    direction = selected.direction or direction

    # Read stored financial figures safely (handling None without TypeError)
    gross_rr = _get("gross_rr")
    net_rr = _get("net_rr")
    risk_usdt = _get("risk_usdt")
    quantity = _get("quantity")
    leverage = _get("leverage") or (financial_context.leverage if financial_context else 5)
    margin_mode = _get("margin_mode") or (financial_context.margin_mode if financial_context else "ISOLATED")
    est_liq = _get("estimated_liquidation")
    meets_min_rr = False

    is_frozen_order = str(state).upper() in ("ARMED", "PAPER_OPEN", "CLOSED") or pricing_mode == "FROZEN_ORDER"

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
            fin_sub = frozen_snapshot.get("financial") if isinstance(frozen_snapshot.get("financial"), dict) else {}
            stored_gross_rr = frozen_snapshot.get("gross_rr") if frozen_snapshot.get("gross_rr") is not None else fin_sub.get("gross_rr")
            stored_net_rr = frozen_snapshot.get("net_rr") if frozen_snapshot.get("net_rr") is not None else fin_sub.get("net_rr")
            stored_risk = (
                frozen_snapshot.get("net_risk_usdt")
                if frozen_snapshot.get("net_risk_usdt") is not None
                else frozen_snapshot.get("risk_usdt")
                if frozen_snapshot.get("risk_usdt") is not None
                else fin_sub.get("net_risk_usdt", fin_sub.get("net_risk", fin_sub.get("risk_usdt")))
            )
            stored_qty = frozen_snapshot.get("quantity") if frozen_snapshot.get("quantity") is not None else fin_sub.get("quantity")
            stored_lev = frozen_snapshot.get("leverage") if frozen_snapshot.get("leverage") is not None else fin_sub.get("leverage")
            stored_mm = frozen_snapshot.get("margin_mode") if frozen_snapshot.get("margin_mode") is not None else fin_sub.get("margin_mode")
            stored_liq = frozen_snapshot.get("estimated_liquidation") if frozen_snapshot.get("estimated_liquidation") is not None else fin_sub.get("estimated_liquidation")
            stored_eq = frozen_snapshot.get("equity_usdt") if frozen_snapshot.get("equity_usdt") is not None else fin_sub.get("equity_usdt")
            stored_cv = frozen_snapshot.get("cost_version") if frozen_snapshot.get("cost_version") is not None else fin_sub.get("cost_version")
            stored_iv = frozen_snapshot.get("instrument_version") if frozen_snapshot.get("instrument_version") is not None else fin_sub.get("instrument_version")
            stored_rv = frozen_snapshot.get("risk_config_version") if frozen_snapshot.get("risk_config_version") is not None else fin_sub.get("risk_config_version")
            stored_gross_loss = frozen_snapshot.get("gross_loss_usdt") if frozen_snapshot.get("gross_loss_usdt") is not None else fin_sub.get("gross_loss_usdt")
            stored_gross_reward = frozen_snapshot.get("gross_reward_usdt") if frozen_snapshot.get("gross_reward_usdt") is not None else fin_sub.get("gross_reward_usdt")
            stored_net_reward = frozen_snapshot.get("net_reward_usdt") if frozen_snapshot.get("net_reward_usdt") is not None else fin_sub.get("net_reward_usdt", fin_sub.get("net_reward"))
            stored_multiplier = frozen_snapshot.get("multiplier", 1.0)
            stored_risk_pct = frozen_snapshot.get("risk_pct") if frozen_snapshot.get("risk_pct") is not None else fin_sub.get("risk_pct")
            stored_eff_risk_pct = frozen_snapshot.get("effective_risk_pct", stored_risk_pct)
            stored_entry_fee = frozen_snapshot.get("entry_fee_usdt")
            stored_sl_exit_fee = frozen_snapshot.get("sl_exit_fee_usdt")
            stored_tp_exit_fee = frozen_snapshot.get("tp_exit_fee_usdt")
            stored_entry_slip = frozen_snapshot.get("entry_slippage_usdt")
            stored_sl_exit_slip = frozen_snapshot.get("sl_exit_slippage_usdt")
            stored_tp_exit_slip = frozen_snapshot.get("tp_exit_slippage_usdt")
            stored_cost_source = frozen_snapshot.get("cost_source", "FROZEN_ORDER_SNAPSHOT")
            stored_equity_as_of = frozen_snapshot.get("equity_as_of_ms", updated_at)
            stored_min_net_rr = frozen_snapshot.get("min_net_rr_at_arm") or frozen_snapshot.get("min_net_rr")
            stored_rr_source = frozen_snapshot.get("rr_policy_source") or "ORDER_SNAPSHOT"
            stored_rr_fp = frozen_snapshot.get("rr_policy_fingerprint")
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
                    stored_min_net_rr = raw_cs.get("min_net_rr_at_arm") or raw_cs.get("min_net_rr")
                    stored_rr_source = raw_cs.get("rr_policy_source") or "ORDER_SNAPSHOT" if stored_min_net_rr is not None else None
                    stored_rr_fp = raw_cs.get("rr_policy_fingerprint")
                else:
                    stored_min_net_rr = None
                    stored_rr_source = None
                    stored_rr_fp = None
            else:
                stored_min_net_rr = None
                stored_rr_source = None
                stored_rr_fp = None

        has_complete_metrics = (
            financial_value_is_known(stored_net_rr)
            and financial_value_is_known(stored_qty)
            and float(stored_qty) > 0
        )

        if has_complete_metrics:
            status = "FROZEN_ORDER"
            reason_code = "ORDER_FROZEN"
            reason_msg = "Lệnh đã kích hoạt; thông số tài chính được bảo toàn"
            if stored_min_net_rr is not None and financial_value_is_known(stored_min_net_rr) and float(stored_min_net_rr) > 0:
                eff_snap_min_rr = float(stored_min_net_rr)
                meets_min_rr = float(stored_net_rr) >= eff_snap_min_rr
                eff_rr_source = stored_rr_source or "ORDER_SNAPSHOT"
            else:
                eff_snap_min_rr = 2.0
                meets_min_rr = float(stored_net_rr) >= 2.0
                eff_rr_source = None
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
                "min_net_rr": eff_snap_min_rr,
                "rr_policy_source": eff_rr_source,
                "rr_policy_fingerprint": stored_rr_fp,
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
                "min_net_rr": financial_context.min_net_rr,
                "rr_policy_source": getattr(financial_context, "rr_policy_source", None),
                "rr_policy_fingerprint": getattr(financial_context, "rr_policy_fingerprint", None),
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
                "min_net_rr": 2.0,
                "rr_policy_source": "STORED_LEGACY",
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
            "order_id": selected.order_id,
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
        "price_basis": selected.price_basis or ("SIGNAL_CONFIRMED" if level_source == "confirmed" else "CLOSED_REFERENCE"),
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


def select_order_row_plan_source(
    order: Any,
    frozen_snapshot: Optional[Dict[str, Any]] = None,
) -> SelectedPlanSource:
    """Builds authoritative SelectedPlanSource from a PaperOrder row without borrowing from WatchSetup (PHẦN 032, 033)."""
    def _get(field: str, default: Any = None) -> Any:
        if isinstance(order, dict):
            return order.get(field, default)
        return getattr(order, field, default)

    order_id = _get("id") or _get("order_id")
    setup_id = _get("setup_id")
    direction = _get("direction")
    state = str(_get("state", "CLOSED")).upper()
    planned_entry = _get("planned_entry")
    actual_entry = _get("actual_entry")
    sl = _get("stop_loss")
    tp = _get("take_profit")
    opened_at = _get("opened_at") or _get("created_at") or 0

    if state in ("PAPER_OPEN", "CLOSED"):
        entry = actual_entry
        basis = "ACTUAL_FILL" if (actual_entry is not None and finite_price(actual_entry)) else "UNSPECIFIED"
    else:
        entry = planned_entry
        basis = "PLANNED_LIMIT" if (planned_entry is not None and finite_price(planned_entry)) else "UNSPECIFIED"

    # If frozen_snapshot has complete geometry, prefer it
    if frozen_snapshot and isinstance(frozen_snapshot, dict):
        if frozen_snapshot.get("entry") is not None:
            entry = frozen_snapshot.get("entry")
        if frozen_snapshot.get("planned_entry") is not None:
            planned_entry = frozen_snapshot.get("planned_entry")
        if frozen_snapshot.get("actual_entry") is not None:
            actual_entry = frozen_snapshot.get("actual_entry")
        if frozen_snapshot.get("stop_loss") is not None:
            sl = frozen_snapshot.get("stop_loss")
        if frozen_snapshot.get("take_profit") is not None:
            tp = frozen_snapshot.get("take_profit")
        if frozen_snapshot.get("price_basis"):
            basis = frozen_snapshot.get("price_basis")

    missing = []
    if entry is None or not finite_price(entry):
        missing.append("entry")
    if sl is None or not finite_price(sl):
        missing.append("stop_loss")
    if tp is None or not finite_price(tp):
        missing.append("take_profit")

    is_comp = len(missing) == 0

    return SelectedPlanSource(
        source_kind="FROZEN_ORDER",
        direction=direction,
        setup_id=setup_id,
        order_id=order_id,
        setup_instance_id=_get("setup_instance_id"),
        setup_revision=_get("version", 1),
        entry=float(entry) if (entry is not None and finite_price(entry)) else None,
        planned_entry=float(planned_entry) if (planned_entry is not None and finite_price(planned_entry)) else None,
        actual_entry=float(actual_entry) if (actual_entry is not None and finite_price(actual_entry)) else None,
        stop_loss=float(sl) if (sl is not None and finite_price(sl)) else None,
        take_profit=float(tp) if (tp is not None and finite_price(tp)) else None,
        level_source="frozen_order",
        price_basis=basis,
        is_complete=is_comp,
        missing_fields=missing,
        raw_snapshot=frozen_snapshot,
        as_of_ms=opened_at,
        target_source_id=order_id,
        target_cutoff_ms=opened_at,
    )


def build_order_row_display_plan(
    order: Any,
    frozen_snapshot: Optional[Dict[str, Any]] = None,
    financial_context: Optional[PlanFinancialContext] = None,
) -> Dict[str, Any]:
    """Builds canonical display plan from an authoritative PaperOrder row without borrowing setup levels (PHẦN 032)."""
    selected = select_order_row_plan_source(order, frozen_snapshot=frozen_snapshot)
    if not selected.is_complete:
        return build_degraded_display_plan(selected, "INCOMPLETE_FROZEN_SOURCE", f"Order {selected.order_id} thiếu giá lịch sử: {', '.join(selected.missing_fields)}")
    validation = validate_selected_plan(selected)
    if not validation.is_valid:
        return build_degraded_display_plan(selected, validation.reason_code, validation.reason_message)
    # Serialize canonical plan with frozen geometry
    snap = dict(frozen_snapshot) if frozen_snapshot else {}
    snap.setdefault("entry", selected.entry)
    snap.setdefault("planned_entry", selected.planned_entry)
    snap.setdefault("actual_entry", selected.actual_entry)
    snap.setdefault("stop_loss", selected.stop_loss)
    snap.setdefault("take_profit", selected.take_profit)
    snap.setdefault("price_basis", selected.price_basis)
    snap.setdefault("order_id", selected.order_id)
    return build_setup_display_plan(order, financial_context=financial_context, pricing_mode="FROZEN_ORDER", frozen_snapshot=snap)
