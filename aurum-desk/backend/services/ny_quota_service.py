"""
Aurum Desk V14.3 - NY quota cycle orchestration (fallback search -> upsert -> auto-arm) + audit.

Layering:
  * NYFallbackService.evaluate_fallback_setup  - PURE generator (no DB / network / wall clock)
  * upsert_ny_fallback_setup                   - dedicated WatchSetup upsert (this module)
  * run_ny_quota_cycle                         - orchestrates; arming is delegated to the injected `arm_fn`
                                                 (StrategyService._auto_arm_candidate -> V14.2 common entry guard)

SEARCH is not permission: arm/fill always pass through the V14.2 guard again. Quota only ever moves on an
actual PAPER fill (TradeLifecycleService / record_quota_fill) - nothing in this module increments it.
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import crud
import models
from domain_calculator import validate_price_geometry
from services.clock import IClock, ReplayClock
from services.entry_guard_service import entry_mutation_lock
from services.event_bus import event_bus
from services.ny_fallback_service import NYFallbackService
from services.ny_opportunity_audit_service import NYOpportunityAuditService
from services.ny_quota_helpers import (
    DAILY_LOSS_BUDGET_PCT, classify_reason, ny_signal_id, primary_reason, remaining_ny_risk_budget,
)
from services.trading_policy_service import TradingPolicyService, _fallback_search_allowed

logger = logging.getLogger(__name__)

FAMILY = "NY_QUOTA_PAPER"
ENTRY_MATERIAL_DELTA = 0.5  # USDT; smaller drift of the planned entry is not a material change
TERMINAL_STATES = ("REJECTED", "EXPIRED", "CANCELLED", "CLOSED", "INVALIDATED")
FROZEN_STATES = ("ARMED", "PAPER_OPEN", "TRIGGERED")


@dataclass
class NYCycleContext:
    """Everything the cycle needs from the outside world, injected so history is deterministic."""
    now_ms: int
    clock: Optional[IClock] = None
    quote: Any = None                       # services.quote_validator.ValidatedQuote
    ticker: Optional[Dict[str, Any]] = None  # raw ticker when no validated quote is supplied
    htf_bias: str = "UNKNOWN"
    h1_alignment: str = "UNKNOWN"
    candles_5m: Optional[list] = None       # injected data reader (tests / lab); None -> bounded DB read
    feed_health: Optional[Dict[str, Any]] = None
    auto_enabled: Optional[bool] = None
    standard_status: Optional[str] = None   # e.g. STANDARD_DATA_MISSING
    risk_settings: Any = None


def _diag_id() -> str:
    return f"diag-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------------------------
# Budget (DB adapter around the pure helper; same semantics as PaperBroker.evaluate_account_guard)
# ---------------------------------------------------------------------------------------------
def compute_remaining_ny_risk_budget(db, *, clock: Optional[IClock] = None) -> Tuple[float, float, Dict[str, float]]:
    """(remaining_usdt, equity, info). Raises ValueError('RISK_BUDGET_DATA_UNAVAILABLE'/...) on bad data.

    daily budget = DAYAUDIT.initial_equity * 1.5% (broker rule) - realized loss today - reserved net risk of
    ARMED orders. Open-position risk is not double counted: an open position already blocks new entries.
    """
    audit = crud.get_or_create_today_audit(db, clock=clock)
    equity = float(audit.current_equity if audit.current_equity is not None else float("nan"))
    if not math.isfinite(equity) or equity <= 0:
        raise ValueError("RISK_BUDGET_DATA_UNAVAILABLE")
    daily_budget = float(audit.initial_equity) * DAILY_LOSS_BUDGET_PCT
    reserved = 0.0
    for o in db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").all():
        reserved += float(o.initial_risk_usdt or 0.0)
    remaining = remaining_ny_risk_budget(
        daily_loss_budget_usdt=daily_budget,
        realized_pnl_today=float(audit.realized_pnl_today or 0.0),
        reserved_net_risk_usdt=reserved,
    )
    return remaining, equity, {"daily_budget": daily_budget, "reserved": reserved,
                               "realized_pnl_today": float(audit.realized_pnl_today or 0.0)}


# ---------------------------------------------------------------------------------------------
# Dedicated upsert
# ---------------------------------------------------------------------------------------------
class UpsertError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _finite_pos(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def _validate_candidate(candidate: Dict[str, Any], session: Dict[str, Any], now_ms: int) -> Tuple[float, float, float]:
    if not session or not session.get("session_instance_id"):
        raise UpsertError("SESSION_MISSING", "Thiếu định danh phiên NY")
    start, end = session.get("window_start_ms"), session.get("window_end_ms")
    if start is None or end is None or not (start <= now_ms < end):
        raise UpsertError("OUTSIDE_ENTRY_WINDOW", "Ứng viên nằm ngoài cửa sổ phiên Mỹ")
    if candidate.get("direction") not in ("LONG", "SHORT"):
        raise UpsertError("UNKNOWN_DIRECTION", "Hướng ứng viên không hợp lệ")
    if not candidate.get("instance_id"):
        raise UpsertError("IDENTITY_MISSING", "Ứng viên thiếu định danh bằng chứng")
    targets = candidate.get("targets") or []
    entry, sl = candidate.get("planned_entry"), candidate.get("stop_loss")
    tp = targets[0].get("price") if targets else None
    for name, v in (("entry", entry), ("stop_loss", sl), ("take_profit", tp)):
        if not _finite_pos(v):
            raise UpsertError("MISSING_PRICE", f"Thiếu hoặc sai giá {name}: {v!r}")
    ok, err = validate_price_geometry(candidate["direction"], entry, sl, tp)
    if not ok:
        raise UpsertError("INVALID_GEOMETRY", f"Hình học giá không hợp lệ: {err}")
    return float(entry), float(sl), float(tp)


def _apply_candidate(row: models.WatchSetup, candidate: Dict[str, Any], session: Dict[str, Any],
                     entry: float, sl: float, tp: float, now_ms: int, *, settings_version: int,
                     policy_version: int) -> None:
    """Whole-batch write: direction + prices + math + meta change together (never field by field)."""
    ev = candidate.get("evidence") or {}
    row.setup_instance_id = candidate["instance_id"]
    row.session_instance_id = session["session_instance_id"]
    row.strategy = "NY_FALLBACK_V1"
    row.strategy_family = FAMILY
    row.direction = candidate["direction"]
    row.timeframe = "5M"
    row.state = "READY"
    row.plan_kind = "CONFIRMED"
    row.direction_source = "FALLBACK_STRUCTURE"
    row.provisional_entry = entry
    row.provisional_sl = sl
    row.provisional_tp = tp
    row.confirmed_entry = None
    row.confirmed_sl = None
    row.confirmed_tp = None
    row.invalidation_price = sl  # NOT NULL: invalidation == validated SL (never relax the constraint)
    row.invalidation_reason = candidate.get("invalidation_reason") or "Giá phá mức SL của cấu trúc fallback M5 đã xác nhận"
    row.htf_bias = candidate.get("htf_bias") or ev.get("htf_bias") or "UNKNOWN"
    row.h1_alignment = candidate.get("h1_alignment") or ev.get("h1_alignment") or "UNKNOWN"
    row.gross_rr = candidate.get("gross_rr")
    row.net_rr = candidate.get("estimated_net_rr")
    row.risk_usdt = candidate.get("initial_risk_usdt")
    row.quantity = candidate.get("quantity")
    row.leverage = candidate.get("leverage")
    row.margin_mode = candidate.get("margin_mode")
    row.risk_pct = candidate.get("effective_risk_pct")
    row.requested_risk_pct = candidate.get("requested_risk_pct")
    row.effective_risk_pct = candidate.get("effective_risk_pct")
    row.risk_profile = "QUOTA"
    row.config_version = settings_version
    row.policy_config_version = policy_version
    row.estimated_liquidation = candidate.get("estimated_liquidation")
    row.updated_at = now_ms
    row.expires_at = candidate.get("expires_at")
    row.evidence_timeline = json.dumps(ev, sort_keys=True)
    row.conditions_met = json.dumps([
        "HTF bias + H1 đồng thuận", "FVG 5M đã đóng, chưa lấp",
        f"Retest {ev.get('retest', {}).get('source', '?')}", "Target = pivot 5M đã xác nhận", "Net R:R đạt ngưỡng policy",
    ], ensure_ascii=False)
    row.conditions_remaining = "[]"
    zone = ev.get("fvg_zone") or [None, None]
    row.entry_zone_low, row.entry_zone_high = zone[0], zone[1]
    row.near_entry_alerted_at = None
    row.near_entry_distance_price = None
    row.near_entry_distance_atr = None
    row.distance_to_entry_atr = None
    row.distance_to_entry_usdt = None


def upsert_ny_fallback_setup(db, candidate: Dict[str, Any], session: Dict[str, Any], now_ms: int, *,
                             symbol: str = "XAUUSDT", settings_version: int = 1, policy_version: int = 1) -> Dict[str, Any]:
    """Create/update the NY fallback WatchSetup. Caller commits; a flush error propagates (caller rolls back).

    Outcomes: CREATED / UPDATED / UNCHANGED / FROZEN / TERMINAL_RETAINED. Never overwrites an ARMED/PAPER_OPEN
    row, never resurrects a terminal row of the SAME candidate identity, and always replaces the whole plan
    when the identity (session/direction/FVG anchor) is new. Re-reads the row inside the entry lock.
    """
    entry, sl, tp = _validate_candidate(candidate, session, now_ms)  # raises UpsertError BEFORE any write
    watch_id = f"watch-{symbol}-5M-fallback"
    instance_id = candidate["instance_id"]
    binding = {"watch_setup_id": watch_id, "setup_instance_id": instance_id,
               "session_instance_id": session["session_instance_id"], "signal_id": ny_signal_id(instance_id)}
    with entry_mutation_lock:
        row = (db.query(models.WatchSetup).filter(models.WatchSetup.id == watch_id)
               .populate_existing().first())  # re-read: never trust a state read seconds ago
        if row is None:
            row = models.WatchSetup(id=watch_id, version=1, created_at=now_ms, updated_at=now_ms,
                                    provisional_entry=entry, provisional_sl=sl, provisional_tp=tp,
                                    invalidation_price=sl, direction=candidate["direction"])
            _apply_candidate(row, candidate, session, entry, sl, tp, now_ms,
                             settings_version=settings_version, policy_version=policy_version)
            db.add(row)
            db.flush()  # surfaces IntegrityError here, before anything reports READY
            _publish(db, row, candidate, "setup.ready", now_ms)
            return {"outcome": "CREATED", "row": row, "binding": binding}

        if row.state in FROZEN_STATES:
            return {"outcome": "FROZEN", "row": row, "binding": binding}

        same_identity = (row.setup_instance_id == instance_id)
        if same_identity and row.state in TERMINAL_STATES:
            return {"outcome": "TERMINAL_RETAINED", "row": row, "binding": binding}

        if not same_identity:
            row.version = (row.version or 1) + 1
            row.created_at = now_ms
            _apply_candidate(row, candidate, session, entry, sl, tp, now_ms,
                             settings_version=settings_version, policy_version=policy_version)
            db.flush()
            _publish(db, row, candidate, "setup.ready", now_ms)
            return {"outcome": "UPDATED", "row": row, "binding": binding}

        material = (
            round(row.provisional_sl or 0.0, 2) != round(sl, 2)
            or round(row.provisional_tp or 0.0, 2) != round(tp, 2)
            or abs((row.provisional_entry or 0.0) - entry) >= ENTRY_MATERIAL_DELTA
            or row.state != "READY"
        )
        if not material:
            return {"outcome": "UNCHANGED", "row": row, "binding": binding}
        row.version = (row.version or 1) + 1
        _apply_candidate(row, candidate, session, entry, sl, tp, now_ms,
                         settings_version=settings_version, policy_version=policy_version)
        db.flush()
        _publish(db, row, candidate, "setup.updated", now_ms)
        return {"outcome": "UPDATED", "row": row, "binding": binding}


def _publish(db, row: models.WatchSetup, candidate: Dict[str, Any], event_type: str, now_ms: int) -> None:
    event_bus.publish_event(
        event_type=event_type, aggregate_id=row.setup_instance_id, aggregate_version=row.version,
        payload={
            "setup_id": row.id, "setup_instance_id": row.setup_instance_id, "revision": row.version,
            "direction": row.direction, "state": row.state, "planned_entry": row.provisional_entry,
            "stop_loss": row.provisional_sl, "take_profit": row.provisional_tp, "net_rr": row.net_rr,
            "risk_usdt": row.risk_usdt, "config_version": row.config_version,
            "strategy_family": FAMILY, "session_instance_id": row.session_instance_id,
            "conditions_met": json.loads(row.conditions_met or "[]"), "missing_conditions": [],
        },
        db=db, occurred_at=now_ms,
    )


# ---------------------------------------------------------------------------------------------
# The cycle
# ---------------------------------------------------------------------------------------------
def run_ny_quota_cycle(db, symbol: str, ctx: NYCycleContext, *, arm_fn: Callable[..., Any]) -> Dict[str, Any]:
    """One NY quota cycle. Returns a plain dict (outcome of this scan). Never raises for expected failures.

    Ordering / ownership:
      1. read/scan (policy, window, quota, data, generator) - no entry lock held, no network;
      2. upsert + arm - inside `entry_mutation_lock` (re-entrant; same lock as every entry mutation).
    """
    now_ms = int(ctx.now_ms)
    clock = ctx.clock or ReplayClock(now_ms)
    now_dt = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
    out: Dict[str, Any] = {
        "ran": False, "searched": False, "session_instance_id": None, "phase": None,
        "reason_codes": [], "primary_reason": None, "upsert_outcome": None, "armed": False,
        "order_id": None, "candidate_key": None, "strategy_candidate": False, "eligible": False,
        "diagnostic_id": None,
    }
    out["stage"] = "policy"
    reasons: List[str] = []       # NY-flow reasons of THIS scan (fallback search / arm / system)
    std_reasons: List[str] = []   # standard-flow data/system status (secondary for the primary reason)
    system_error: Optional[str] = None
    window: Dict[str, Any] = {}
    try:
        policy = TradingPolicyService.get_active_policy(db, symbol)
        window = TradingPolicyService.get_ny_session_window(policy, now_ms=now_ms, clock=clock)
        quota = TradingPolicyService.update_quota_lifecycle_state(db, policy, now_ms=now_ms, clock=clock)
        sid = window["session_instance_id"]
        out.update(session_instance_id=sid, phase=window["phase"], quota_state=quota.quota_status)
        if not getattr(policy, "is_active", True) or window["phase"] == "BEFORE_WINDOW":
            return out
        if window["phase"] == "AFTER_WINDOW":
            NYOpportunityAuditService.finalize_session_summary(db, sid, now_ms)
            db.commit()
            return out
        out["ran"] = True

        if ctx.standard_status:
            std_reasons.append(ctx.standard_status)

        fallback_phase = window["phase"] == "IN_FALLBACK_WINDOW"
        unfinished = (quota.total_fills or 0) < (quota.target_fills or 0)
        if not _fallback_search_allowed(policy, window, quota, now_ms):
            if fallback_phase and unfinished and not getattr(policy, "ny_fallback_enabled", False):
                reasons.append("FALLBACK_DISABLED")  # generator is NOT called
        elif crud.get_active_position(db) is not None:
            reasons.append("ACTIVE_POSITION_EXISTS")
        elif db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first() is not None:
            reasons.append("ARMED_ORDER_EXISTS")
        else:
            out["searched"] = True
            out["stage"] = "generator"
            reasons += _search_and_act(db, symbol, ctx, policy, window, now_ms, now_dt, clock, arm_fn, out)
    except Exception as e:  # noqa: BLE001 - outer loop must never die; user must see a reason
        db.rollback()
        diag = _diag_id()
        out["diagnostic_id"] = diag
        stage = out.get("stage")
        code = "UPSERT_FAILED" if stage == "upsert" else "SYSTEM_ERROR"
        system_error = f"{diag} stage={stage} session={out.get('session_instance_id')} candidate={out.get('candidate_key')} err={type(e).__name__}: {e}"
        logger.error(f"[{diag}] NY quota cycle failed at stage={stage}: {e}", exc_info=True)
        reasons.append(code)

    # The primary reason prefers the NY flow that is actually active in this scan (search/arm/system);
    # a standard-data gap stays visible in reason_codes but does not mask a more actionable NY blocker.
    out["reason_codes"] = reasons + std_reasons
    out["primary_reason"] = primary_reason(reasons) or primary_reason(std_reasons)
    if out.get("session_instance_id") and (out["ran"] or system_error):
        try:
            data_issue = any(classify_reason(r)[0] == "DATA" for r in reasons + std_reasons)
            NYOpportunityAuditService.record_scan(
                db, session_instance_id=out["session_instance_id"],
                session_date_ny=window.get("session_date_ny"), symbol=symbol,
                now_ms=now_ms, quota_state=out.get("quota_state"), searched=out["searched"],
                data_issue=data_issue, primary_code=out["primary_reason"], system_error=system_error)
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            logger.error(f"NY audit scan record failed: {e}", exc_info=True)
    return out


def _search_and_act(db, symbol, ctx, policy, window, now_ms, now_dt, clock, arm_fn, out) -> List[str]:
    reasons: List[str] = []
    sid = window["session_instance_id"]

    from services.quote_validator import QuoteValidator
    quote = ctx.quote
    if quote is None:
        from services.collector_service import collector_service
        raw = ctx.ticker if ctx.ticker is not None else collector_service.latest_ticker
        quote = QuoteValidator.validate_ticker(raw, now_ms=now_ms) if raw else None

    settings = ctx.risk_settings
    if settings is None:
        from services.risk_settings_service import risk_settings_service
        settings = risk_settings_service.get_settings(db)

    try:
        remaining, equity, _info = compute_remaining_ny_risk_budget(db, clock=clock)
    except ValueError:
        reasons.append("RISK_BUDGET_DATA_UNAVAILABLE")
        return reasons

    candles = ctx.candles_5m
    if candles is None:
        candles = crud.get_candles(db, symbol, "5M", limit=60, ascending=True, cutoff_ms=now_ms)

    res = NYFallbackService.evaluate_fallback_setup(
        symbol=symbol, candles_5m=candles,
        current_price=(getattr(quote, "last", None) or 0.0) if quote is not None else 0.0,
        htf_bias=ctx.htf_bias, h1_alignment=ctx.h1_alignment, policy=policy,
        account_equity=equity, remaining_risk_allowance_usdt=remaining,
        leverage=settings.requested_leverage, margin_mode=settings.margin_mode,
        now_ms=now_ms, window=window, quote=quote, requested_risk_pct=settings.risk_pct,
    )
    out["strategy_candidate"] = bool(res.get("strategy_candidate"))
    code = res.get("reason_code")
    eligible = bool(res.get("is_eligible") and res.get("candidate"))
    out["eligible"] = eligible

    struct = res.get("structure") or {}
    cand = res.get("candidate")
    key = (cand or {}).get("instance_id") or struct.get("instance_id")
    out["candidate_key"] = key
    if res.get("strategy_candidate") and key:
        NYOpportunityAuditService.record_candidate(
            db, session_instance_id=sid, session_date_ny=window.get("session_date_ny"), symbol=symbol,
            family=FAMILY, candidate_key=key, direction=struct.get("direction"), now_ms=now_ms,
            reasons=None if eligible else [code], is_financially_eligible=eligible,
            evidence=(cand or {}).get("evidence") or struct,
            fingerprint=_fingerprint(struct, cand), watch_setup_id=f"watch-{symbol}-5M-fallback")
        db.commit()
    if not eligible:
        reasons.append(code)
        return reasons

    # ---- mutation section: upsert + auto-arm are one serialized unit ----------------------------
    with entry_mutation_lock:
        out["stage"] = "upsert"
        try:
            up = upsert_ny_fallback_setup(
                db, cand, window, now_ms, symbol=symbol,
                settings_version=settings.config_version,
                policy_version=getattr(policy, "version", 1) or 1)
            db.commit()
        except UpsertError as e:
            db.rollback()
            reasons.append("UPSERT_FAILED")
            out["diagnostic_id"] = _diag_id()
            logger.error(f"[{out['diagnostic_id']}] NY fallback upsert rejected ({e.code}): {e.message}")
            return reasons
        except Exception:
            db.rollback()
            raise
        out["upsert_outcome"] = up["outcome"]
        row = up["row"]
        if up["outcome"] == "TERMINAL_RETAINED":
            reasons.append("SETUP_TERMINAL")
            return reasons
        if up["outcome"] == "FROZEN":
            reasons.append("ARMED_ORDER_EXISTS")
            return reasons
        if row.state != "READY":
            reasons.append("SETUP_TERMINAL")
            return reasons

        out["stage"] = "arm"
        auto = ctx.auto_enabled
        if auto is not True:
            reasons.append("AUTO_PAPER_OFF")
            _audit_blocked(db, sid, window, symbol, key, cand, reasons, now_ms)
            return reasons

        arm = arm_fn(db, row, {"signal_id": up["binding"]["signal_id"]}, now_ms, now_dt,
                     clock=clock, quote=quote, feed_health=ctx.feed_health)
        if arm and arm.get("armed"):
            out["armed"] = True
            out["order_id"] = arm.get("order_id")
            _audit_blocked(db, sid, window, symbol, key, cand, [], now_ms)   # blockers cleared (trace revision)
            NYOpportunityAuditService.mark_stage(
                db, session_instance_id=sid, family=FAMILY, candidate_key=key, armed=True,
                order_id=arm.get("order_id"), outcome="ARMED", now_ms=now_ms)
            TradingPolicyService.update_quota_lifecycle_state(db, policy, now_ms=now_ms, clock=clock)
            db.commit()
        else:
            codes = list((arm or {}).get("codes") or ["ARM_BLOCKED"])
            reasons += codes
            _audit_blocked(db, sid, window, symbol, key, cand, codes, now_ms)
    return reasons


def _audit_blocked(db, sid, window, symbol, key, cand, codes, now_ms):
    """Signal exists + is financially eligible, but a guard/switch stopped the arm: keep it in the funnel."""
    NYOpportunityAuditService.record_candidate(
        db, session_instance_id=sid, session_date_ny=window.get("session_date_ny"), symbol=symbol,
        family=FAMILY, candidate_key=key, direction=cand.get("direction"), now_ms=now_ms,
        reasons=codes, is_financially_eligible=True, evidence=cand.get("evidence"),
        fingerprint=_fingerprint(None, cand), outcome="ARM_BLOCKED", watch_setup_id=f"watch-{symbol}-5M-fallback",
        count_seen=False)
    db.commit()


def _fingerprint(struct: Optional[Dict[str, Any]], cand: Optional[Dict[str, Any]]) -> str:
    """Plan fingerprint on rounded levels: entry drift below the material delta does not change it."""
    from services.ny_quota_helpers import stable_hash
    if cand:
        entry = cand.get("planned_entry")
        sl = cand.get("stop_loss")
        tp = (cand.get("targets") or [{}])[0].get("price")
        d = cand.get("direction")
    else:
        s = struct or {}
        entry, sl, tp, d = s.get("entry_ref"), s.get("stop_loss"), s.get("take_profit"), s.get("direction")
    def r(v, step=ENTRY_MATERIAL_DELTA):
        return None if v is None else round(round(v / step) * step, 2)
    return stable_hash(d, r(entry), r(sl, 0.01), r(tp, 0.01), length=16)
