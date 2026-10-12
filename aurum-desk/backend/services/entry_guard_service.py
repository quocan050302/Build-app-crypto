"""
V14.2 - Common Entry Guard (orchestrator).

One decision for every ENTRY route (manual arm, auto arm, manual MARKET, pending fill):
identity, strategy state, order state/trigger, quote, execution feed, geometry, financial
(net R:R from the EXISTING calculator result), account, session policy, news blackout, lessons.

Design rules
- NOT a second calculator: `calc_result` is the output of domain_calculator.calculate_risk_reward
  computed by the adapter at planned (ARM) or actual fill (FILL) entry.
- Fail closed: any check missing or raising becomes UNKNOWN (`ENTRY_GUARD_DATA_UNAVAILABLE`)
  and `allowed` is False. `allowed` is derived: every required check must be PASS.
- The guard never opens/closes orders and never sends notifications. It does not commit by
  itself; existing policy/audit getters it reuses may commit (documented, unchanged behaviour).
- EXITS are never routed through here (process_exit_tick / execute_close / amend SL).
- ARM and FILL are different stages: FILL does not require the WatchSetup to be READY (the order
  is already armed with a frozen plan) and excludes `current_order_id` from armed-order checks.
"""
from __future__ import annotations

import functools
import logging
import math
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, Union

import models
from domain_calculator import validate_price_geometry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entry mutation serialization (V14.2)
# ---------------------------------------------------------------------------
# Process-local re-entrant lock shared by every entry mutation (manual arm,
# auto arm, direct MARKET, pending fill) so that "check guard -> write order"
# is one unit and two callers cannot both pass "max one armed/open".
# LIMITATION: this only serialises threads of ONE process (single uvicorn
# worker). Multi-worker deployments would need a DB-level invariant.
entry_mutation_lock = threading.RLock()


def serialize_entry_mutation(fn: Callable) -> Callable:
    """Decorator: run an entry mutation under ``entry_mutation_lock``.

    Exits (process_exit_tick / execute_close / amend SL) are intentionally NOT
    decorated so protective exits are never queued behind an entry.
    """

    @functools.wraps(fn)
    def _wrapper(*args, **kwargs):
        with entry_mutation_lock:
            return fn(*args, **kwargs)

    return _wrapper

CheckState = Literal["PASS", "BLOCK", "UNKNOWN"]
Stage = Literal["ARM", "FILL"]

GUARD_POLICY_VERSION = "14.2"
DATA_UNAVAILABLE = "ENTRY_GUARD_DATA_UNAVAILABLE"

# Order matters: it is the order of blockers (primary reason = first failing check).
# Plan/economic problems are listed before transient feed/quote problems because they are
# the actionable ones for a beginner.
REQUIRED: Dict[str, Tuple[str, ...]] = {
    "ARM": ("identity", "strategy", "geometry", "financial", "account", "session",
            "news", "lessons", "quote", "feed"),
    "FILL": ("identity", "order_state", "trigger", "geometry", "financial", "account",
             "session", "news", "lessons", "quote", "feed"),
}

# Origins that are explicit user drafts / direct market (no SMC provenance is claimed).
NON_SETUP_ORIGINS = ("MANUAL_DRAFT", "MANUAL_MARKET")


@dataclass(frozen=True)
class EntryCheck:
    code: str
    state: CheckState
    message_vi: str
    next_step_vi: str
    details: Mapping[str, Any] = field(default_factory=dict)


CheckValue = Union[EntryCheck, Sequence[EntryCheck]]


@dataclass(frozen=True)
class EntryGuardRequest:
    stage: Stage
    now_ms: int
    symbol: str
    direction: str
    execution_mode: str                      # MANUAL | AUTO
    origin: str                              # MANUAL_WEB | AUTO_STRATEGY | MANUAL_MARKET | MANUAL_DRAFT ...
    planned_entry: Optional[float] = None    # ARM: planned entry; FILL: actual fill price
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    calc_result: Optional[Any] = None        # output of calculate_risk_reward
    validated_quote: Optional[Any] = None    # QuoteValidator ValidatedQuote
    strategy_family: str = "STANDARD_SMC"
    order_type: str = "MARKET"
    timeframe: Optional[str] = "15M"
    plan_kind: str = "CONFIRMED"             # PROVISIONAL | CONFIRMED | DRAFT | ORDER
    direction_source: Optional[str] = None   # SIGNAL | TREND | UNCONFIRMED (None = not provided)
    setup_id: Optional[str] = None
    setup_instance_id: Optional[str] = None
    setup_version: Optional[int] = None
    setup_state: Optional[str] = None
    plan_fingerprint: Optional[str] = None
    risk_config_version: Optional[int] = None
    order_id: Optional[str] = None
    current_order_id: Optional[str] = None
    order_state: Optional[str] = None        # FILL: "armed" | "DIRECT_MARKET"
    order_expires_at_ms: Optional[int] = None
    trigger_hit: Optional[bool] = None       # FILL
    structure_chain_verified: Optional[bool] = None  # WAITING_RETRACE: sweep->break->FVG
    min_net_rr: Optional[float] = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    distance_to_entry_atr: Optional[float] = None
    session_instance_id: Optional[str] = None
    feed_health: Optional[Mapping[str, Any]] = None  # injected in tests; None -> default reader
    clock: Optional[Any] = None


@dataclass(frozen=True)
class EntryGuardDecision:
    allowed: bool
    stage: str
    evaluated_at_ms: int
    checks: Tuple[EntryCheck, ...]
    warnings: Tuple[str, ...] = ()
    news_view: Optional[Dict[str, Any]] = None
    lesson_result: Optional[Dict[str, Any]] = None
    policy_summary: Mapping[str, Any] = field(default_factory=dict)
    policy_version: str = GUARD_POLICY_VERSION
    settings_version: Optional[int] = None
    binding: Mapping[str, Any] = field(default_factory=dict)
    plan_kind: str = "CONFIRMED"

    @property
    def failed_checks(self) -> Tuple[EntryCheck, ...]:
        return tuple(c for c in self.checks if c.state != "PASS")

    @property
    def reason_codes(self) -> List[str]:
        out: List[str] = []
        for c in self.failed_checks:
            if c.code not in out:
                out.append(c.code)
        return out

    @property
    def blockers(self) -> List[Dict[str, str]]:
        seen = set()
        out = []
        for c in self.failed_checks:
            key = (c.code, c.message_vi)
            if key in seen:
                continue
            seen.add(key)
            out.append({"code": c.code, "message_vi": c.message_vi, "next_step_vi": c.next_step_vi})
        return out

    @property
    def primary_reason(self) -> Optional[Dict[str, str]]:
        b = self.blockers
        return b[0] if b else None

    def summary(self) -> Dict[str, Any]:
        """Small JSON-safe trace stored on the order (arm/fill stage are kept separate)."""
        return {
            "allowed": self.allowed,
            "stage": self.stage,
            "evaluated_at_ms": self.evaluated_at_ms,
            "reason_codes": self.reason_codes,
            "policy_version": self.policy_version,
            "settings_version": self.settings_version,
            "plan_kind": self.plan_kind,
            "binding": dict(self.binding),
            "news_event_id": (self.news_view or {}).get("event_id"),
            "news_blackout": bool((self.news_view or {}).get("is_blackout")),
            "lesson_ids": _lesson_ids(self.lesson_result),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "stage": self.stage,
            "evaluated_at_ms": self.evaluated_at_ms,
            "plan_kind": self.plan_kind,
            "reason_codes": self.reason_codes,
            "blockers": self.blockers,
            "warnings": list(self.warnings),
            "news_view": self.news_view,
            "lesson_result": self.lesson_result,
            "policy_version": self.policy_version,
            "settings_version": self.settings_version,
            "binding": dict(self.binding),
            "checks": [
                {"name": c.code, "state": c.state, "message_vi": c.message_vi} for c in self.checks
            ],
        }


def _lesson_ids(lesson_result: Optional[Mapping[str, Any]]) -> List[Any]:
    ids: List[Any] = []
    for ev in (lesson_result or {}).get("evaluations", []) or []:
        if isinstance(ev, Mapping):
            rid = ev.get("rule_id") or ev.get("lesson_id") or ev.get("id")
            if rid is not None and rid not in ids:
                ids.append(rid)
    return ids


def _flatten(value: CheckValue) -> Tuple[EntryCheck, ...]:
    if isinstance(value, EntryCheck):
        return (value,)
    return tuple(value)


def aggregate_entry_checks(
    stage: str,
    now_ms: int,
    checks: Mapping[str, CheckValue],
    *,
    warnings: Sequence[str] = (),
    news_view: Optional[Dict[str, Any]] = None,
    lesson_result: Optional[Dict[str, Any]] = None,
    policy_summary: Optional[Mapping[str, Any]] = None,
    settings_version: Optional[int] = None,
    binding: Optional[Mapping[str, Any]] = None,
    plan_kind: str = "CONFIRMED",
) -> EntryGuardDecision:
    """Missing required check == UNKNOWN (fail closed). allowed iff every check is PASS."""
    if stage not in REQUIRED:
        raise ValueError("Unsupported entry guard stage")
    ordered: List[EntryCheck] = []
    for name in REQUIRED[stage]:
        if name in checks:
            ordered.extend(_flatten(checks[name]))
        else:
            ordered.append(EntryCheck(
                code=DATA_UNAVAILABLE, state="UNKNOWN",
                message_vi=f"Chưa kiểm tra được điều kiện {name}.",
                next_step_vi="Chờ dữ liệu hợp lệ trước khi đặt lệnh.",
            ))
    return EntryGuardDecision(
        allowed=all(c.state == "PASS" for c in ordered),
        stage=stage,
        evaluated_at_ms=now_ms,
        checks=tuple(ordered),
        warnings=tuple(warnings),
        news_view=news_view,
        lesson_result=lesson_result,
        policy_summary=dict(policy_summary or {}),
        settings_version=settings_version,
        binding=dict(binding or {}),
        plan_kind=plan_kind,
    )


# ------------------------------------------------------------------ helpers

def _ok(code: str, msg: str = "OK") -> EntryCheck:
    return EntryCheck(code=code, state="PASS", message_vi=msg, next_step_vi="")


def _finite(x: Any) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def quote_age_ms_of(quote: Any) -> Optional[int]:
    """ValidatedQuote has freshness_sec (no age_ms). Returns None when unknown/non-finite."""
    sec = getattr(quote, "freshness_sec", None)
    if not _finite(sec):
        return None
    return int(round(float(sec) * 1000))


def read_execution_feed_health() -> Dict[str, Any]:
    """Default reader of the live execution consumer; tests inject `feed_health` instead."""
    from services.execution_consumer import execution_consumer
    return {
        "has_execution_gap": bool(execution_consumer.has_execution_gap),
        "is_lagging": bool(execution_consumer.is_lagging),
    }


def _data_unavailable(name: str, exc: BaseException) -> EntryCheck:
    diag = f"diag-{uuid.uuid4().hex[:8]}"
    logger.error(f"[{diag}] entry guard check '{name}' failed: {exc}", exc_info=True)
    return EntryCheck(
        code=DATA_UNAVAILABLE, state="UNKNOWN",
        message_vi=f"Chưa kiểm tra được điều kiện {name} ({diag}).",
        next_step_vi="Tạm dừng vào lệnh; thử lại khi dữ liệu hợp lệ.",
        details={"diagnostic_id": diag, "check": name, "error": str(exc)},
    )


# ------------------------------------------------------------------ individual checks

def _check_identity(req: EntryGuardRequest) -> EntryCheck:
    if req.stage not in REQUIRED:
        return EntryCheck("INVALID_ENTRY_REQUEST", "BLOCK", f"Giai đoạn không hợp lệ: {req.stage}.", "Liên hệ hỗ trợ.")
    if req.direction not in ("LONG", "SHORT"):
        return EntryCheck("INVALID_DIRECTION", "BLOCK", f"Hướng lệnh không hợp lệ: {req.direction}.",
                          "Chọn LONG hoặc SHORT hợp lệ.")
    if not isinstance(req.now_ms, int) or req.now_ms <= 0:
        return EntryCheck("INVALID_ENTRY_REQUEST", "BLOCK", "Thiếu thời điểm đánh giá.", "Thử lại.")
    if req.stage == "FILL" and not req.order_id and req.order_state != "DIRECT_MARKET":
        return EntryCheck("INVALID_ENTRY_REQUEST", "BLOCK", "Thiếu định danh lệnh chờ khớp.", "Thử lại.")
    return _ok("identity")


def _check_strategy(req: EntryGuardRequest) -> EntryCheck:
    if req.origin in NON_SETUP_ORIGINS and req.setup_state is None:
        return _ok("strategy", "Lệnh thủ công, không gắn nhãn SMC.")
    state = req.setup_state
    if state is None:
        return EntryCheck(DATA_UNAVAILABLE, "UNKNOWN", "Chưa có trạng thái cấu trúc của setup.",
                          "Chờ setup được cập nhật.")
    if state in ("INVALIDATED", "CANCELLED", "EXPIRED", "CLOSED", "REJECTED"):
        return EntryCheck("SETUP_TERMINAL", "BLOCK", f"Setup đã kết thúc ({state})",
                          "Chọn setup khác đang theo dõi.")
    if req.plan_kind == "PROVISIONAL" or req.direction_source == "UNCONFIRMED":
        return EntryCheck("UNCONFIRMED_ENTRY", "BLOCK",
                          "Điểm vào chưa xác nhận: đây chỉ là kế hoạch tạm để quan sát, hướng chưa đủ cơ sở xác nhận.",
                          "Chờ hệ thống xác nhận phá vỡ cấu trúc.")
    if state == "READY":
        if req.strategy_family == "STANDARD_SMC":
            from services.smc_scenario_config import active_scenario_config
            if active_scenario_config.certified_smc_entry_enabled:
                # V14.4.1: READY is a label, not evidence. Only an explicit True bound to the exact
                # scenario snapshot passes; None (unknown) and False both block.
                if req.structure_chain_verified is not True:
                    unknown = req.structure_chain_verified is None
                    return EntryCheck(
                        "UNCONFIRMED_STRUCTURE_CHAIN", "BLOCK",
                        ("Chưa có bằng chứng chuỗi cấu trúc SMC được xác minh cho setup này."
                         if unknown else
                         "Chuỗi cấu trúc SMC chưa được xác nhận đầy đủ (thiếu sweep, break MSS hoặc FVG)."),
                        "Chờ cấu trúc hoàn thành các bước xác nhận.")
                return _ok("strategy")
            # Flag OFF: legacy READY path, explicitly NOT certified.
            if req.structure_chain_verified is False:
                return EntryCheck("UNCONFIRMED_STRUCTURE_CHAIN", "BLOCK",
                                  "Chuỗi cấu trúc SMC chưa được xác nhận đầy đủ (thiếu sweep, break MSS hoặc FVG).",
                                  "Chờ cấu trúc hoàn thành các bước xác nhận.")
            return _ok("strategy", "LEGACY_UNCERTIFIED: chế độ cũ, chưa xác minh chuỗi SMC.")
        return _ok("strategy")
    if state == "WAITING_RETRACE":
        if req.structure_chain_verified is True:
            return _ok("strategy", "Chuỗi sweep→phá vỡ→FVG đã xác nhận; chỉ đặt lệnh chờ (LIMIT).")
        return EntryCheck("EVIDENCE_UNAVAILABLE", "BLOCK",
                          "Chưa có bằng chứng chuỗi sweep→phá vỡ cấu trúc→FVG để đặt lệnh chờ hồi.",
                          "Chờ xác nhận phá vỡ cấu trúc và FVG.")
    if state == "WAITING_PRICE":
        return EntryCheck("WAITING_STRUCTURE", "BLOCK",
                          "Setup đang chờ điều kiện giá hình thành cấu trúc/sweep, chưa đủ bằng chứng để Arm.",
                          "Chờ xác nhận phá vỡ cấu trúc.")
    return EntryCheck("STRATEGY_NOT_READY", "BLOCK",
                      f"Setup đang ở trạng thái {state}, chưa đủ bằng chứng để Arm.",
                      "Chờ cấu trúc xác nhận.")


def _check_order_state(req: EntryGuardRequest) -> EntryCheck:
    if req.order_state == "DIRECT_MARKET":
        return _ok("order_state", "Lệnh market thủ công trực tiếp.")
    if req.order_state != "armed":
        return EntryCheck("ORDER_NOT_ARMED", "BLOCK", f"Lệnh không ở trạng thái chờ khớp ({req.order_state}).",
                          "Không cần xử lý thêm.")
    if req.order_expires_at_ms is not None and req.now_ms > req.order_expires_at_ms:
        return EntryCheck("ORDER_EXPIRED", "BLOCK", "Lệnh chờ đã hết hạn.", "Chọn lại setup.")
    return _ok("order_state")


def _check_trigger(req: EntryGuardRequest) -> EntryCheck:
    if req.trigger_hit is None:
        return EntryCheck(DATA_UNAVAILABLE, "UNKNOWN", "Chưa xác định điều kiện kích hoạt khớp lệnh.", "Chờ báo giá.")
    if not req.trigger_hit:
        return EntryCheck("TRIGGER_NOT_HIT", "BLOCK", "Giá chưa chạm điều kiện khớp.", "Chờ giá chạm vùng vào.")
    return _ok("trigger")


def _check_quote(req: EntryGuardRequest) -> EntryCheck:
    q = req.validated_quote
    if q is None:
        return EntryCheck("QUOTE_UNAVAILABLE", "UNKNOWN", "Chưa có báo giá hợp lệ.", "Chờ báo giá mới.")
    if not getattr(q, "is_valid", False):
        code = "QUOTE_STALE" if getattr(q, "is_stale", False) else "QUOTE_INVALID"
        detail = getattr(q, "rejection_detail", None) or "Báo giá không hợp lệ"
        return EntryCheck(code, "BLOCK", f"{detail}", "Chờ báo giá mới.",
                          {"rejection_code": getattr(q, "rejection_code", None)})
    return _ok("quote")


def _check_feed(req: EntryGuardRequest) -> EntryCheck:
    health = req.feed_health if req.feed_health is not None else read_execution_feed_health()
    if health.get("has_execution_gap") or health.get("is_lagging"):
        return EntryCheck("EXECUTION_FEED_DEGRADED", "BLOCK",
                          "Hàng đợi thực thi thị trường đang bị quá tải hoặc thiếu dữ liệu, tạm dừng Arm/vào lệnh mới.",
                          "Chờ nguồn giá ổn định lại.")
    return _ok("feed")


def _check_geometry(req: EntryGuardRequest) -> EntryCheck:
    for name, v in (("Entry", req.planned_entry), ("SL", req.stop_loss), ("TP", req.take_profit)):
        if not _finite(v) or float(v) <= 0:
            return EntryCheck("INVALID_PRICE_GEOMETRY", "BLOCK",
                              f"Mức giá {name} không hợp lệ ({v}).", "Kiểm tra lại bộ giá Entry/SL/TP.")
    ok, err = validate_price_geometry(req.direction, float(req.planned_entry), float(req.stop_loss), float(req.take_profit))
    if not ok:
        return EntryCheck("INVALID_PRICE_GEOMETRY", "BLOCK", f"{err}", "Kiểm tra lại hướng và bộ giá.")
    return _ok("geometry")


def _check_financial(req: EntryGuardRequest) -> Sequence[EntryCheck]:
    c = req.calc_result
    if c is None:
        return [EntryCheck(DATA_UNAVAILABLE, "UNKNOWN", "Chưa tính được Net R:R của kế hoạch.", "Chờ tính toán.")]
    if not getattr(c, "is_valid", False):
        return [EntryCheck("CALCULATOR_INVALID", "BLOCK",
                           f"{getattr(c, 'invalid_reason', None) or 'Bộ giá không hợp lệ'}",
                           "Kiểm tra lại bộ giá.")]
    out: List[EntryCheck] = []
    net_reward = getattr(c, "net_reward_usdt", None)
    net_rr = getattr(c, "net_rr", None)
    min_rr = req.min_net_rr
    non_positive = _finite(net_reward) and float(net_reward) <= 0
    if non_positive:
        out.append(EntryCheck(
            "NET_REWARD_NON_POSITIVE", "BLOCK",
            f"Lợi nhuận ròng kỳ vọng ≤ 0 sau phí/trượt giá ({float(net_reward):.2f} USDT; Net R:R {net_rr:.2f}). TP hiện tại quá gần Entry.",
            "Cần điểm vào tốt hơn hoặc mục tiêu cấu trúc mới; không tự kéo TP.",
            {"net_reward_usdt": net_reward, "net_rr": net_rr}))
    if not getattr(c, "meets_min_rr", False):
        thr = f"{min_rr:.1f}" if _finite(min_rr) else "ngưỡng tối thiểu"
        out.append(EntryCheck(
            "INSUFFICIENT_RR", "BLOCK",
            f"Tỷ lệ Net R:R (1:{(net_rr if _finite(net_rr) else 0.0):.2f}) không đạt ngưỡng tối thiểu {thr} (Yêu cầu >= {thr})",
            "Cần điểm vào tốt hơn hoặc mục tiêu cấu trúc mới.",
            {"net_rr": net_rr, "min_net_rr": min_rr}))
    if not getattr(c, "can_execute", False) and not out:
        out.append(EntryCheck("CANNOT_EXECUTE", "BLOCK",
                              getattr(c, "skip_reason", None) or "Không đủ điều kiện thực thi",
                              "Kiểm tra khối lượng, đòn bẩy và ký quỹ."))
    return out or [_ok("financial")]


def _check_account(db, req: EntryGuardRequest) -> Sequence[EntryCheck]:
    from paper_broker import PaperBroker
    out: List[EntryCheck] = []
    risk = getattr(req.calc_result, "net_risk_usdt", None)
    risk = float(risk) if _finite(risk) else 0.0
    blocked = PaperBroker.evaluate_account_guard(db, risk, now_ms=req.now_ms, clock=req.clock)
    if blocked:
        code, msg = blocked
        out.append(EntryCheck(code, "BLOCK", msg, "Chờ điều kiện tài khoản cho phép."))
        if code == "DAILY_FILL_CAP":
            pass
    if req.stage == "ARM":
        q = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed")
        if req.current_order_id:
            q = q.filter(models.PaperOrder.id != req.current_order_id)
        other = q.first()
        if other is not None:
            out.append(EntryCheck("ARMED_ORDER_EXISTS", "BLOCK",
                                  f"Đã có một lệnh đang chờ khớp ({other.id})",
                                  "Hủy hoặc chờ lệnh chờ hiện tại."))
    return out or [_ok("account")]


_POLICY_SKIP_CODES = ("NEWS_BLACKOUT", DATA_UNAVAILABLE)


def _check_session(db, req: EntryGuardRequest, ctx: Dict[str, Any]) -> Sequence[EntryCheck]:
    from services.trading_policy_service import TradingPolicyService
    now_dt = datetime.fromtimestamp(req.now_ms / 1000.0, tz=timezone.utc)
    pe = TradingPolicyService.evaluate_entry_policy(
        db, req.symbol, now_dt, clock=req.clock, now_ms=req.now_ms,
        strategy_family=req.strategy_family or "STANDARD_SMC",
    )
    policy = pe.get("policy")
    ctx["policy_summary"] = {
        "session_instance_id": pe.get("session_instance_id"),
        "policy_config_version": pe.get("policy_config_version", 1),
        "daily_fills": pe.get("daily_fills", 0),
        "remaining_daily_slots": pe.get("remaining_daily_slots", 0),
        "ny_fills": pe.get("ny_fills", 0),
        "quota_state": pe.get("quota_state", "NOT_STARTED"),
        "reserved_slots": pe.get("reserved_slots", 0),
        "is_in_ny_window": pe.get("is_in_ny_window"),
        "policy_active": bool(policy and getattr(policy, "is_active", True)),
        "min_net_rr": getattr(policy, "min_net_rr", None) if policy else None,
        "ny_fallback_risk_pct_cap": getattr(policy, "ny_fallback_risk_pct_cap", None) if policy else None,
    }
    codes = list(pe.get("reason_codes") or [])
    msgs = list(pe.get("block_reasons") or [])
    out: List[EntryCheck] = []
    for i, code in enumerate(codes):
        if code in _POLICY_SKIP_CODES:
            continue
        msg = msgs[i] if i < len(msgs) else code
        out.append(EntryCheck(code, "BLOCK", msg, "Chờ phiên giao dịch phù hợp theo chính sách."))
    return out or [_ok("session")]


def _check_news(db, req: EntryGuardRequest, ctx: Dict[str, Any]) -> EntryCheck:
    from services.news_blackout_service import evaluate_news_blackout
    view = evaluate_news_blackout(db, req.now_ms)
    ctx["news_view"] = view.to_dict()
    if view.calendar_status == "EMPTY":
        ctx.setdefault("warnings", []).append("Chưa xác nhận dữ liệu lịch tin tức (lịch trống).")
    if view.is_blackout:
        return EntryCheck(
            "NEWS_BLACKOUT", "BLOCK",
            f"{view.reason_text()} - tạm dừng vào lệnh còn khoảng {view.remaining_minutes} phút",
            "Chờ hết thời gian tin tức rồi chọn lại setup.",
            {"event_id": view.event_id, "end_ms": view.end_ms})
    return _ok("news")


def _check_lessons(db, req: EntryGuardRequest, ctx: Dict[str, Any]) -> Sequence[EntryCheck]:
    from services.entry_decision_service import EntryDecisionService
    q = req.validated_quote
    bid = getattr(q, "bid", None) if q is not None else None
    ask = getattr(q, "ask", None) if q is not None else None
    stage = "BEFORE_ARM" if req.stage == "ARM" else "BEFORE_FILL"
    c = req.calc_result
    context = EntryDecisionService.build_context(
        stage=stage,
        symbol=req.symbol,
        direction=req.direction,
        strategy_family=req.strategy_family,
        timeframe=req.timeframe,
        execution_mode=req.execution_mode,
        origin=req.origin,
        planned_entry=req.planned_entry,
        stop_loss=req.stop_loss,
        take_profit=req.take_profit,
        net_rr=getattr(c, "net_rr", None),
        gross_rr=getattr(c, "gross_rr", None),
        bid=bid,
        ask=ask,
        now_ms=req.now_ms,
        session_instance_id=req.session_instance_id or ctx.get("policy_summary", {}).get("session_instance_id"),
        evidence=dict(req.evidence or {}),
        distance_to_entry_atr=req.distance_to_entry_atr,
        quote_age_ms=quote_age_ms_of(q) if q is not None else None,
        setup_id=req.setup_id,
        order_id=req.order_id,
        # Only a direct manual MARKET entry is "arm+fill in one step", so BEFORE_ARM-stage rules also apply.
        # Pending orders keep the exact stage semantics of the arm/fill stages.
        order_type=req.order_type if req.order_state == "DIRECT_MARKET" else None,
    )
    result = EntryDecisionService.evaluate_entry_rules(db, context)
    ctx["lesson_result"] = result
    for w in result.get("warning_messages", []) or []:
        ctx.setdefault("warnings", []).append(str(w))
    if result.get("can_proceed", False):
        return [_ok("lessons")]
    out: List[EntryCheck] = []
    for msg in result.get("blocking_reasons", []) or ["Quy tắc bài học chặn vào lệnh"]:
        if "LESSON_RULE_DATA_UNAVAILABLE" in msg:
            code = "LESSON_RULE_DATA_UNAVAILABLE"
        elif "LESSON_EVALUATION_FAILED" in msg:
            code = "LESSON_EVALUATION_FAILED"
        else:
            code = "LESSON_RULE_BLOCKED"
        out.append(EntryCheck(code, "BLOCK", str(msg), "Xem lại bài học đang áp dụng."))
    return out


def _safe(name: str, fn: Callable[[], CheckValue]) -> CheckValue:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - fail closed
        return _data_unavailable(name, e)


def evaluate_entry_guard(db, request: EntryGuardRequest) -> EntryGuardDecision:
    """
    Collect every real check for `request.stage` and aggregate. Never allows on missing data.
    """
    ctx: Dict[str, Any] = {"warnings": []}
    req = request
    checks: Dict[str, CheckValue] = {}

    checks["identity"] = _safe("identity", lambda: _check_identity(req))
    if req.stage == "ARM":
        checks["strategy"] = _safe("strategy", lambda: _check_strategy(req))
    else:
        checks["order_state"] = _safe("order_state", lambda: _check_order_state(req))
        checks["trigger"] = _safe("trigger", lambda: _check_trigger(req))
    checks["quote"] = _safe("quote", lambda: _check_quote(req))
    checks["feed"] = _safe("feed", lambda: _check_feed(req))
    checks["geometry"] = _safe("geometry", lambda: _check_geometry(req))
    checks["financial"] = _safe("financial", lambda: _check_financial(req))
    checks["account"] = _safe("account", lambda: _check_account(db, req))
    checks["session"] = _safe("session", lambda: _check_session(db, req, ctx))
    checks["news"] = _safe("news", lambda: _check_news(db, req, ctx))
    checks["lessons"] = _safe("lessons", lambda: _check_lessons(db, req, ctx))

    policy_version = None
    try:
        from services.lesson_policy_service import LessonPolicyService
        policy_version = LessonPolicyService.get_policy(db).get("version", 1)
    except Exception:  # noqa: BLE001 - metadata only
        policy_version = None

    binding = {
        "setup_id": req.setup_id,
        "setup_instance_id": req.setup_instance_id,
        "setup_version": req.setup_version,
        "direction": req.direction,
        "plan_fingerprint": req.plan_fingerprint,
        "risk_config_version": req.risk_config_version,
        "lesson_policy_version": policy_version,
        "news_evaluated_at_ms": (ctx.get("news_view") or {}).get("evaluated_at_ms"),
        "order_id": req.order_id,
        "order_type": req.order_type,
        "strategy_family": req.strategy_family,
        "execution_mode": req.execution_mode,
        "origin": req.origin,
    }
    return aggregate_entry_checks(
        req.stage, req.now_ms, checks,
        warnings=ctx.get("warnings", []),
        news_view=ctx.get("news_view"),
        lesson_result=ctx.get("lesson_result"),
        policy_summary=ctx.get("policy_summary"),
        settings_version=req.risk_config_version,
        binding=binding,
        plan_kind=req.plan_kind,
    )
