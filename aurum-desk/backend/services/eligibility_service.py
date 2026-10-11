import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)
from sqlalchemy.orm import Session
import models, crud
from domain_calculator import validate_price_geometry, calculate_risk_reward
from services.trading_policy_service import TradingPolicyService


def _has_evidence(raw: Any) -> bool:
    """True when an evidence JSON column carries real content (not null/empty)."""
    if raw is None:
        return False
    if isinstance(raw, str):
        txt = raw.strip()
        if txt in ("", "null", "{}", "[]", "false"):
            return False
        try:
            import json
            parsed = json.loads(txt)
        except Exception:
            return True
        return bool(parsed)
    return bool(raw)


def _risk_config_version(db: Session) -> Optional[int]:
    try:
        from services.risk_settings_service import risk_settings_service
        return int(risk_settings_service.get_settings(db).config_version)
    except Exception:
        return None


def _derive_plan_kind(setup, custom_entry, custom_sl, custom_tp, entry, sl, tp, level_source) -> str:
    """
    plan_kind comes from the SOURCE of the resolved levels, never from geometry or net R:R.
    A custom triple identical to the setup's own resolved plan keeps that plan's kind;
    any other custom triple is a DRAFT.
    """
    def _setup_kind() -> str:
        stored = getattr(setup, "plan_kind", None)
        if stored in ("PROVISIONAL", "CONFIRMED"):
            return stored
        # Legacy rows without provenance: structural state (not geometry / net R:R) decides.
        if level_source == "confirmed" or getattr(setup, "state", None) in (
            "READY", "WAITING_RETRACE", "ARMED", "PAPER_OPEN"
        ):
            return "CONFIRMED"
        return "PROVISIONAL"

    if level_source == "INVALID":
        return "PROVISIONAL"
    if custom_entry is None and custom_sl is None and custom_tp is None:
        return _setup_kind()
    try:
        from services.plan_resolver import resolve_plan_levels
        base = resolve_plan_levels(setup)
        if base.get("is_valid") and all(
            round(float(a), 2) == round(float(b), 2)
            for a, b in ((base["entry"], entry), (base["stop_loss"], sl), (base["take_profit"], tp))
        ):
            return _setup_kind()
    except Exception:
        pass
    return "DRAFT"



def evaluate_setup_eligibility(
    db: Session,
    setup: models.WatchSetup,
    custom_entry: Optional[float] = None,
    custom_sl: Optional[float] = None,
    custom_tp: Optional[float] = None,
    custom_margin_mode: Optional[str] = None,
    now_ms: Optional[int] = None,
    execution_mode: str = "MANUAL",
    origin: str = "MANUAL_WEB",
    clock: Optional[Any] = None,
    validated_quote: Optional[Any] = None,
    feed_health: Optional[Dict[str, Any]] = None,
    current_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Authoritative, shared V7 eligibility evaluator for upcoming setups, preview cards, and arm endpoints.
    Distinguishes:
    - strategy_state: READY, WAITING_RETRACE, WAITING_PRICE, etc.
    - execution_eligibility: can_arm (bool), can_execute_now (bool), reason_codes (List[str]), block_reasons (List[str])
    - trading policy: daily fill cap (3), NY window (08:00-11:00 NY), NY slot reservation.
    """
    current_time = now_ms if now_ms is not None else int(time.time() * 1000)
    current_dt = datetime.fromtimestamp(current_time / 1000.0, tz=timezone.utc)
    reason_codes: List[str] = []
    block_reasons: List[str] = []

    symbol = getattr(setup, "instrument", None) or getattr(setup, "symbol", None) or "XAUUSDT"
    policy = TradingPolicyService.get_active_policy(db, symbol)

    # 2. Margin Mode check (Cross is unsupported for paper execution)
    effective_margin_mode = custom_margin_mode or setup.margin_mode or "ISOLATED"
    if effective_margin_mode.upper() == "CROSS":
        reason_codes.append("CROSS_MARGIN_UNSUPPORTED")
        block_reasons.append("CROSS_MARGIN_UNSUPPORTED: Chế độ Cross margin chưa được hỗ trợ thực thi trên tài khoản paper (Cần Isolated)")

    day_audit = crud.get_or_create_today_audit(db, clock=clock)

    # 8. Authoritative Price Geometry & Financial snapshot validation (V10.2 Plan Resolver)
    from services.plan_resolver import resolve_plan_levels, PlanResolutionError
    try:
        resolved_plan = resolve_plan_levels(setup, custom_entry, custom_sl, custom_tp)
        entry = resolved_plan["entry"]
        sl = resolved_plan["stop_loss"]
        tp = resolved_plan["take_profit"]
        level_source = resolved_plan["level_source"]
        is_geom_valid = resolved_plan["is_geometry_valid"]
        geom_err = resolved_plan["geometry_error"]
    except PlanResolutionError as pe:
        reason_codes.append(pe.code)
        block_reasons.append(f"{pe.code}: {pe.message}")
        entry = custom_entry or getattr(setup, "provisional_entry", 0.0) or 0.0
        sl = custom_sl or getattr(setup, "provisional_sl", 0.0) or 0.0
        tp = custom_tp or getattr(setup, "provisional_tp", 0.0) or 0.0
        level_source = "INVALID"
        is_geom_valid = False
        geom_err = pe.message

    if not is_geom_valid and "INVALID_PRICE_GEOMETRY" not in reason_codes:
        reason_codes.append("INVALID_PRICE_GEOMETRY")
        block_reasons.append(f"INVALID_PRICE_GEOMETRY: {geom_err}")

    # Sizing & risk
    equity = day_audit.current_equity if day_audit else 1000.0
    effective_risk_pct = setup.risk_pct or 0.25
    if getattr(setup, "strategy_family", "STANDARD_SMC") == "NY_QUOTA_PAPER":
        effective_risk_pct = min(effective_risk_pct, getattr(policy, "ny_fallback_risk_pct_cap", 0.10) or 0.10)

    min_rr = getattr(policy, "min_net_rr", 2.0) or 2.0
    calc_res = calculate_risk_reward(
        direction=setup.direction,
        planned_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        capital_usdt=equity,
        risk_pct=effective_risk_pct,
        min_net_rr=min_rr,
        leverage=setup.leverage or 5,
        margin_mode=effective_margin_mode
    )

    # 9. V14.2 Common Entry Guard (stage ARM): state/evidence, quote, feed, account, session policy,
    #    news blackout and governed lessons are collected by ONE orchestrator shared with auto-arm,
    #    manual MARKET and pending fill. The calculator result above is reused (no second formula).
    from services.entry_guard_service import EntryGuardRequest, evaluate_entry_guard
    from services.quote_validator import QuoteValidator
    from services.setup_quality_service import build_plan_fingerprint

    ev_dict: Dict[str, Any] = {}
    chain_verified: Optional[bool] = None
    if getattr(setup, "scenario_snapshot_id", None):
        try:
            from models import SMCScenarioSnapshot
            snap_row = db.query(SMCScenarioSnapshot).filter(SMCScenarioSnapshot.snapshot_id == setup.scenario_snapshot_id).first()
            if snap_row:
                import json
                p_data = json.loads(snap_row.payload)
                cc = p_data.get("chain_check", {})
                chain_verified = bool(cc.get("verified"))
                ev_dict = {
                    "sweep_detected": True,
                    "fvg_found": True,
                    "structure_confirmed": chain_verified,
                }
        except Exception:
            pass

    if chain_verified is None and getattr(setup, "evidence_snapshot_id", None):
        try:
            ev_row = db.query(models.StrategyEvidence).filter(models.StrategyEvidence.id == setup.evidence_snapshot_id).first()
            if ev_row:
                sweep = _has_evidence(ev_row.sweep_evidence)
                brk = _has_evidence(ev_row.displacement_evidence)
                fvg = _has_evidence(ev_row.fvg_evidence)
                chain_verified = bool(sweep and brk and fvg)
                ev_dict = {
                    "sweep_detected": sweep,
                    "fvg_found": fvg,
                    "structure_confirmed": chain_verified,
                }
        except Exception:
            ev_dict = {}
            chain_verified = None

    if validated_quote is None:
        try:
            from services.collector_service import collector_service
            raw_ticker = collector_service.latest_ticker
            # No ticker at all stays None (QUOTE_UNAVAILABLE) instead of a fabricated stale quote.
            validated_quote = QuoteValidator.validate_ticker(raw_ticker, now_ms=current_time) if raw_ticker else None
        except Exception:
            validated_quote = None

    plan_kind = _derive_plan_kind(setup, custom_entry, custom_sl, custom_tp, entry, sl, tp, level_source)
    fingerprint = None
    if level_source != "INVALID":
        try:
            fingerprint = build_plan_fingerprint({
                "direction": setup.direction, "planned_entry": entry, "stop_loss": sl, "take_profit": tp,
            })
        except Exception:
            fingerprint = None

    decision = evaluate_entry_guard(db, EntryGuardRequest(
        stage="ARM",
        now_ms=current_time,
        symbol=symbol,
        direction=setup.direction,
        execution_mode=execution_mode,
        origin=origin,
        planned_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        calc_result=calc_res,
        validated_quote=validated_quote,
        strategy_family=getattr(setup, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC",
        order_type="MARKET" if setup.state == "READY" else "LIMIT",
        timeframe=getattr(setup, "timeframe", "15M"),
        plan_kind=plan_kind,
        direction_source=getattr(setup, "direction_source", None),
        setup_id=setup.id,
        setup_instance_id=setup.setup_instance_id,
        setup_version=setup.version,
        setup_state=setup.state,
        plan_fingerprint=fingerprint,
        risk_config_version=_risk_config_version(db),
        current_order_id=current_order_id,
        structure_chain_verified=chain_verified,
        min_net_rr=min_rr,
        evidence=ev_dict,
        distance_to_entry_atr=getattr(setup, "distance_to_entry_atr", None),
        feed_health=feed_health,
        clock=clock,
    ))
    policy_eval = dict(decision.policy_summary)
    lesson_eval = decision.lesson_result or {
        "can_proceed": False, "blocking_reasons": [], "warning_messages": [], "advisory_notes": [],
        "evaluations": [], "lessons_retrieved_snapshot": [],
    }
    guard_codes = list(decision.reason_codes)
    for blocker in decision.blockers:
        code = blocker["code"]
        if code == "CANNOT_EXECUTE" and "CROSS_MARGIN_UNSUPPORTED" in reason_codes:
            continue
        if code == "INVALID_PRICE_GEOMETRY" and "INVALID_PRICE_GEOMETRY" in reason_codes:
            continue
        if code not in reason_codes:
            reason_codes.append(code)
        if code == "MAX_DAILY_ENTRIES" and "DAILY_FILL_CAP" not in reason_codes:
            reason_codes.append("DAILY_FILL_CAP")
        block_reasons.append(blocker["message_vi"] if blocker["message_vi"].startswith(code) or code.startswith("LESSON") else f"{code}: {blocker['message_vi']}")
    if "DAILY_FILL_CAP" in reason_codes and "MAX_DAILY_ENTRIES" not in reason_codes:
        reason_codes.append("MAX_DAILY_ENTRIES")

    can_arm = len(reason_codes) == 0
    can_execute_now = can_arm and (setup.state == "READY") and calc_res.can_execute

    entry_order_type = "MARKET" if setup.state == "READY" else "LIMIT"

    return {
        "setup_id": setup.id,
        "setup_instance_id": setup.setup_instance_id,
        "revision": setup.version if setup.version is not None else 1,
        "version": setup.version,
        "plan_kind": plan_kind,
        "direction_source": getattr(setup, "direction_source", None),
        "stage": "ARM",
        "allowed": can_arm,
        "blockers": decision.blockers,
        "warnings": list(decision.warnings),
        "news_view": decision.news_view,
        "binding": dict(decision.binding),
        "plan_fingerprint": fingerprint,
        "entry_guard": decision.to_dict(),
        "direction": setup.direction,
        "strategy_state": setup.state,
        "strategy_family": getattr(setup, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC",
        "margin_mode": effective_margin_mode,
        "eligible": can_arm,
        "resolved_entry": round(entry, 2) if entry is not None else None,
        "resolved_sl": round(sl, 2) if sl is not None else None,
        "resolved_tp": round(tp, 2) if tp is not None else None,
        "can_arm": can_arm,
        "can_execute": can_execute_now,  # Backward compatibility
        "can_execute_now": can_execute_now,
        "entry_order_type": entry_order_type,
        "reason_codes": reason_codes,
        "block_reason": block_reasons[0] if block_reasons else None,
        "block_reasons": block_reasons,
        "all_block_reasons": block_reasons,
        "session_instance_id": policy_eval.get("session_instance_id"),
        "policy_config_version": getattr(policy, "version", 1),
        "lesson_policy_version": decision.binding.get("lesson_policy_version"),
        "risk_config_version": decision.settings_version if decision.settings_version is not None else 1,
        "daily_fill_count": policy_eval.get("daily_fills", 0),
        "remaining_daily_slots": policy_eval.get("remaining_daily_slots", 0),
        "ny_fill_count": policy_eval.get("ny_fills", 0),
        "ny_quota_status": policy_eval.get("quota_state", "NOT_STARTED"),
        "reserved_slots": policy_eval.get("reserved_slots", 0),
        "effective_financial_snapshot": {
            "entry": round(entry, 2) if entry is not None else None,
            "sl": round(sl, 2) if sl is not None else None,
            "tp": round(tp, 2) if tp is not None else None,
            "gross_rr": round(calc_res.gross_rr, 2) if calc_res and calc_res.gross_rr is not None else 0.0,
            "net_rr": round(calc_res.net_rr, 2) if calc_res and calc_res.net_rr is not None else 0.0,
            "quantity": calc_res.quantity,
            "risk_pct": effective_risk_pct,
            "initial_margin": calc_res.initial_margin_usdt,
            "estimated_liquidation": calc_res.estimated_liquidation,
            "leverage": calc_res.leverage,
            "margin_mode": calc_res.margin_mode,
            "level_source": level_source
        },
        "level_source": level_source,
        "evidence_snapshot_id": getattr(setup, "evidence_snapshot_id", None),
        "evaluated_at": current_time,
        # V10/V10.2 Lesson Rules Details (DTOs and backward-compatible strings)
        "lesson_advisories": lesson_eval.get("lesson_advisories", lesson_eval["advisory_notes"]),
        "lesson_warnings": lesson_eval.get("lesson_warnings", lesson_eval["warning_messages"]),
        "lesson_blockers": lesson_eval.get("lesson_blockers", lesson_eval["blocking_reasons"]),
        "lesson_items": lesson_eval.get("lesson_items", []),
        "legacy_advisories": lesson_eval["advisory_notes"],
        "legacy_warnings": lesson_eval["warning_messages"],
        "legacy_blockers": lesson_eval["blocking_reasons"],
        "lesson_evaluations": lesson_eval["evaluations"],
        "lessons_retrieved_snapshot": lesson_eval["lessons_retrieved_snapshot"]
    }
