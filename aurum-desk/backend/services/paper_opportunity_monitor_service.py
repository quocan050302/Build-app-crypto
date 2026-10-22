"""
backend/services/paper_opportunity_monitor_service.py
Aurum V17.0 — Forward Paper Opportunity Monitor Read-Only Service.

Architecture & Contract Principles (Phần 001 - 100):
- Strictly Read-Only: zero side-effects (zero INSERT/UPDATE/DELETE/REPLACE/CREATE/ALTER/DROP).
- No mutating active trading runs, trading policy, day audits, or starting workers.
- Canonical Opportunity Identity: distinct opportunity_id within run and symbol scope.
- Truthful UPSERT semantics: OpportunityFunnelRecord is unique by (opportunity_id, stage).
  Stage snapshots maintain first_recorded_at (created_at), last_updated_at (updated_at),
  and last_observed_at (as_of_ms). Never conflate late reasons with early creation time.
- Clear separation:
  * total_unique_opportunities = distinct canonical opportunity identities.
  * funnel stage counts = milestone occurrences (can overlap across progression).
  * closed_verified_trades = count of verified closed orders (from committed order accounting).
  * 0 closed trades => winrate/expectancy N/A.
  * verified breakeven => expectancy 0.00.
  * POLICY_BLOCKED is intentional protection, not a technical bug.
  * TECHNICAL_MISS requires causal evidence and quote coverage.
  * Missing coverage => UNVERIFIABLE.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

import models
from services.cg01_missed_audit_service import CG01MissedOpportunityAuditService
from services.ny_quota_helpers import classify_reason
from services.paper_runtime_snapshot_service import (
    DEFAULT_SCOPE,
    DEFAULT_SYMBOL,
    SCHEMA_VERSION,
    compute_financial_metrics,
    find_existing_observation_run,
    read_linked_orders,
    redact_sensitive_text,
)
from services.scalp_m1_types import OpportunityStage

logger = logging.getLogger("paper_opportunity_monitor_service")

MONITOR_SCHEMA_VERSION = "1.0"
MAX_QUERY_WINDOW_DAYS = 31
MAX_WINDOW_MS = MAX_QUERY_WINDOW_DAYS * 24 * 60 * 60 * 1000
DEFAULT_LIMIT = 20
MAX_LIMIT = 50

# Vietnamese stage titles catalog for display
STAGE_TITLES_VI: Dict[str, str] = {
    "DATA_READY": "Dữ liệu sẵn sàng",
    "ZONE_DETECTED": "Đã phát hiện vùng",
    "ZONE_WATCHED": "Đang theo dõi vùng",
    "PRICE_NEAR": "Giá tiếp cận vùng",
    "ZONE_TOUCHED": "Giá đã chạm vùng",
    "TRIGGER_PENDING": "Chờ xác nhận trigger",
    "TRIGGER_CONFIRMED": "Trigger đã xác nhận",
    "PLAN_VALID": "Kế hoạch hợp lệ",
    "PLANS_VALID": "Kế hoạch hợp lệ",
    "GUARD_BLOCKED": "Bị chặn bởi Guard",
    "ARMED": "Đã đặt lệnh chờ (ARM)",
    "FILLED": "Đã khớp lệnh",
    "EXPIRED": "Hết thời hạn hiệu lực",
    "INVALIDATED": "Kế hoạch bị hủy",
    "POI_WATCHED": "Đang theo dõi POI",
    "POI_APPROACHED": "Giá tiếp cận POI",
    "SWEPT": "Đã quét thanh khoản",
    "MSS_CONFIRMED": "Đã phá cấu trúc (MSS)",
    "FVG_CONFIRMED": "Đã tạo FVG",
    "ENTRY_READY": "Đủ điều kiện vào lệnh",
    "TERMINAL": "Đã kết thúc",
    "CLOSED": "Đã đóng lệnh",
    "OPEN": "Đang mở vị thế",
    "PAPER_OPEN": "Đang mở vị thế",
}


@dataclass
class MonitorQueryContext:
    symbol: str = DEFAULT_SYMBOL
    scope: str = DEFAULT_SCOPE
    run_id: Optional[str] = None
    model_id: Optional[str] = None
    session_id: Optional[str] = None
    start_ms: Optional[int] = None
    end_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    now_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    limit: int = DEFAULT_LIMIT
    cursor: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MonitorChartTarget:
    kind: str  # 'SETUP' | 'ORDER'
    id: str
    setup_instance_id: Optional[str] = None
    revision: Optional[int] = None
    evidence_snapshot_id: Optional[str] = None
    verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedMonitorStage:
    opportunity_id: str
    stage: str
    stage_label_vi: str
    symbol: str
    model_id: Optional[str]
    direction: str  # 'LONG' | 'SHORT' | 'UNKNOWN'
    session_id: Optional[str]
    run_id: Optional[str]
    zone_id: Optional[str]
    as_of_ms: int
    created_at_ms: int
    updated_at_ms: int
    reason_codes: List[str]
    primary_reason_code: Optional[str]
    primary_reason_vi: str
    evidence_ids: List[str]
    metrics: Dict[str, Any]
    payload: Dict[str, Any]
    score: Optional[float] = None
    planned_entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    data_quality: str = "VERIFIED"  # 'VERIFIED' | 'PARTIAL' | 'UNVERIFIABLE'
    limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ObservedMilestone:
    id: str
    stage: str
    stage_label_vi: str
    first_recorded_at_ms: Optional[int]
    last_updated_at_ms: Optional[int]
    snapshot_as_of_ms: Optional[int]
    occurred_at_ms: Optional[int]
    reason_codes: List[str]
    reason_summary_vi: str
    source: str  # 'FUNNEL_STAGE_SNAPSHOT' | 'COMMITTED_ORDER_MILESTONE' | 'TRACE'
    time_semantics: str
    evidence_ids: List[str] = field(default_factory=list)
    limitation: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OpportunityMonitorListItem:
    key: str  # Canonical identity composite key
    opportunity_id: str
    run_id: Optional[str]
    model_id: Optional[str]
    direction: str  # 'LONG' | 'SHORT' | 'UNKNOWN'
    session_id: Optional[str]
    session_label_vi: Optional[str]
    first_recorded_at_ms: Optional[int]
    last_observed_at_ms: Optional[int]
    current_stage: str
    stage_label_vi: str
    primary_reason_code: Optional[str]
    primary_reason_vi: str
    classification: Optional[str]
    data_quality: str  # 'VERIFIED' | 'PARTIAL' | 'UNVERIFIABLE'
    observed_milestone_count: int
    score: Optional[float] = None
    score_source: Optional[str] = None
    zone_label: Optional[str] = None
    chart_target: Optional[MonitorChartTarget] = None
    limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        if self.chart_target:
            res["chart_target"] = self.chart_target.to_dict()
        return res


@dataclass
class OpportunityMonitorDetail:
    item: OpportunityMonitorListItem
    milestones: List[ObservedMilestone]
    plan: Optional[Dict[str, Any]] = None
    order_outcomes: List[Dict[str, Any]] = field(default_factory=list)
    latency: Optional[Dict[str, Any]] = None
    classifier_result: Optional[Dict[str, Any]] = None
    truncated: bool = False
    limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["item"] = self.item.to_dict()
        res["milestones"] = [m.to_dict() for m in self.milestones]
        return res


@dataclass
class OpportunityMonitorSummary:
    schema_version: str
    generated_at_ms: int
    query_scope: Dict[str, Any]
    status: str  # 'READY' | 'EMPTY' | 'NOT_CONFIGURED' | 'DEGRADED'
    counts: Dict[str, Any]
    top_reasons: List[Dict[str, Any]]
    financial: Dict[str, Any]
    coverage: Dict[str, Any]
    latency: Optional[Dict[str, Any]] = None
    limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MonitorListPage:
    items: List[OpportunityMonitorListItem]
    total: int
    has_more: bool
    next_cursor: Optional[str]
    snapshot_as_of_ms: int
    limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "total": self.total,
            "has_more": self.has_more,
            "next_cursor": self.next_cursor,
            "snapshot_as_of_ms": self.snapshot_as_of_ms,
            "limitations": self.limitations,
        }


# ---------------------------------------------------------------------------
# Helpers & Utilities
# ---------------------------------------------------------------------------

def encode_cursor(as_of_ms: int, opportunity_id: str) -> str:
    raw = f"{as_of_ms}:{opportunity_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor_str: Optional[str]) -> Optional[Tuple[int, str]]:
    if not cursor_str:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor_str.encode("ascii")).decode("utf-8")
        parts = raw.split(":", 1)
        if len(parts) == 2:
            return int(parts[0]), parts[1]
    except Exception:
        pass
    return None


def get_stage_title_vi(stage: Optional[str]) -> str:
    if not stage:
        return "Chưa xác định"
    norm = str(stage).strip().upper()
    return STAGE_TITLES_VI.get(norm, norm.replace("_", " "))


def get_reason_description_vi(code: Optional[str]) -> str:
    if not code:
        return "Đang quan sát thị trường"
    _, msg = classify_reason(code)
    return msg or f"Mã: {code}"


# ---------------------------------------------------------------------------
# Core Service Functions
# ---------------------------------------------------------------------------

def make_monitor_query_context(
    *,
    symbol: str = DEFAULT_SYMBOL,
    scope: str = DEFAULT_SCOPE,
    run_id: Optional[str] = None,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    model_id: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    cursor: Optional[str] = None,
    now_ms: Optional[int] = None,
) -> MonitorQueryContext:
    """
    Phần 021 - 030: Validates input deterministically and locks a single now_ms.
    Interval is half-open: [start_ms, end_ms).
    """
    fixed_now = int(now_ms if now_ms is not None else (time.time() * 1000))

    clean_symbol = str(symbol or DEFAULT_SYMBOL).strip().upper()
    clean_scope = str(scope or DEFAULT_SCOPE).strip().upper()

    if start_ms is not None and not isinstance(start_ms, int):
        try:
            start_ms = int(start_ms)
        except (ValueError, TypeError):
            raise ValueError(f"start_ms must be an integer epoch timestamp, got: {start_ms}")

    if end_ms is not None and not isinstance(end_ms, int):
        try:
            end_ms = int(end_ms)
        except (ValueError, TypeError):
            raise ValueError(f"end_ms must be an integer epoch timestamp, got: {end_ms}")

    effective_end = end_ms if end_ms is not None else fixed_now

    if start_ms is not None and effective_end is not None:
        if effective_end <= start_ms:
            raise ValueError(f"end_ms ({effective_end}) must be strictly greater than start_ms ({start_ms})")
        if (effective_end - start_ms) > MAX_WINDOW_MS:
            raise ValueError(f"Query window cannot exceed {MAX_QUERY_WINDOW_DAYS} days ({MAX_WINDOW_MS} ms)")

    eff_limit = limit
    if eff_limit < 1:
        eff_limit = DEFAULT_LIMIT
    elif eff_limit > MAX_LIMIT:
        eff_limit = MAX_LIMIT

    clean_run_id = str(run_id).strip() if run_id else None
    clean_model_id = str(model_id).strip() if model_id else None
    clean_session_id = str(session_id).strip() if session_id else None

    return MonitorQueryContext(
        symbol=clean_symbol,
        scope=clean_scope,
        run_id=clean_run_id,
        model_id=clean_model_id,
        session_id=clean_session_id,
        start_ms=start_ms,
        end_ms=effective_end,
        now_ms=fixed_now,
        limit=eff_limit,
        cursor=cursor,
    )


def normalize_monitor_stage_row(row: Any) -> NormalizedMonitorStage:
    """
    Phần 040, B3: Pure normalization of OpportunityFunnelRecord or dict.
    Safely parses JSON payload, handles malformed JSON without 500, preserves provenance.
    """
    opp_id = str(getattr(row, "opportunity_id", "") or "")
    stage = str(getattr(row, "stage", "") or "").upper()
    symbol = str(getattr(row, "symbol", "") or DEFAULT_SYMBOL).upper()
    model_id = getattr(row, "model_id", None)
    raw_dir = str(getattr(row, "direction", "") or "").upper()
    direction = raw_dir if raw_dir in ("LONG", "SHORT") else "UNKNOWN"
    session_id = getattr(row, "session_id", None)
    run_id = getattr(row, "run_id", None)
    zone_id = getattr(row, "zone_id", None)
    as_of_ms = int(getattr(row, "as_of_ms", 0) or 0)
    created_at_ms = int(getattr(row, "created_at", 0) or as_of_ms)
    updated_at_ms = int(getattr(row, "updated_at", 0) or created_at_ms)

    reasons: List[str] = []
    evidence_ids: List[str] = []
    metrics: Dict[str, Any] = {}
    payload: Dict[str, Any] = {}
    limitations: List[str] = []
    data_quality = "VERIFIED"

    # 1. Parse payload_json
    payload_raw = getattr(row, "payload_json", None)
    if isinstance(payload_raw, str) and payload_raw.strip():
        try:
            parsed = json.loads(payload_raw)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            data_quality = "PARTIAL"
            limitations.append("MALFORMED_STAGE_PAYLOAD: Không thể đọc cấu trúc JSON chi tiết")
    elif isinstance(payload_raw, dict):
        payload = payload_raw

    # 2. Parse reason_codes_json
    reasons_raw = getattr(row, "reason_codes_json", None)
    if isinstance(reasons_raw, str) and reasons_raw.strip():
        try:
            parsed = json.loads(reasons_raw)
            if isinstance(parsed, list):
                reasons = [str(r) for r in parsed]
        except Exception:
            data_quality = "PARTIAL"
    elif isinstance(reasons_raw, list):
        reasons = [str(r) for r in reasons_raw]
    if not reasons and payload.get("reason_codes"):
        reasons = [str(r) for r in payload.get("reason_codes", [])]

    # 3. Parse evidence_ids_json
    ev_raw = getattr(row, "evidence_ids_json", None)
    if isinstance(ev_raw, str) and ev_raw.strip():
        try:
            parsed = json.loads(ev_raw)
            if isinstance(parsed, list):
                evidence_ids = [str(e) for e in parsed]
        except Exception:
            pass
    elif isinstance(ev_raw, list):
        evidence_ids = [str(e) for e in ev_raw]
    if not evidence_ids and payload.get("evidence_ids"):
        evidence_ids = [str(e) for e in payload.get("evidence_ids", [])]

    # 4. Parse metrics_json
    met_raw = getattr(row, "metrics_json", None)
    if isinstance(met_raw, str) and met_raw.strip():
        try:
            parsed = json.loads(met_raw)
            if isinstance(parsed, dict):
                metrics = parsed
        except Exception:
            pass
    elif isinstance(met_raw, dict):
        metrics = met_raw

    # Primary reason
    primary_code = reasons[0] if reasons else None
    primary_vi = get_reason_description_vi(primary_code)

    # Optional plan & score extraction from payload
    score = payload.get("score") or payload.get("quality_score")
    if score is not None:
        try:
            score = float(score)
        except Exception:
            score = None

    p_entry = payload.get("planned_entry") or payload.get("entry_price") or payload.get("entry")
    p_sl = payload.get("stop_loss") or payload.get("sl")
    p_tp = payload.get("take_profit") or payload.get("tp")

    def _to_float(v):
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    return NormalizedMonitorStage(
        opportunity_id=opp_id,
        stage=stage,
        stage_label_vi=get_stage_title_vi(stage),
        symbol=symbol,
        model_id=model_id or payload.get("model_id"),
        direction=direction,
        session_id=session_id or payload.get("session_id"),
        run_id=run_id or payload.get("run_id"),
        zone_id=zone_id or payload.get("zone_id"),
        as_of_ms=as_of_ms,
        created_at_ms=created_at_ms,
        updated_at_ms=updated_at_ms,
        reason_codes=reasons,
        primary_reason_code=primary_code,
        primary_reason_vi=primary_vi,
        evidence_ids=evidence_ids,
        metrics=metrics,
        payload=payload,
        score=score,
        planned_entry=_to_float(p_entry),
        stop_loss=_to_float(p_sl),
        take_profit=_to_float(p_tp),
        data_quality=data_quality,
        limitations=limitations,
    )


def link_monitor_order_evidence(
    db: Session,
    *,
    run_id: Optional[str],
    opportunity_ids: Sequence[str],
    symbol: str = DEFAULT_SYMBOL,
) -> Dict[str, Tuple[Optional[MonitorChartTarget], List[Dict[str, Any]]]]:
    """
    Phần 051 - 060, B6: Reads order associations and checks instrument/run consistency.
    Returns { opportunity_id: (chart_target, [order_outcomes]) }.
    """
    if not opportunity_ids:
        return {}

    # Query links
    links_query = db.query(models.PaperResearchOrderLink).filter(
        models.PaperResearchOrderLink.opportunity_id.in_(list(opportunity_ids))
    )
    if run_id:
        links_query = links_query.filter(models.PaperResearchOrderLink.run_id == run_id)
    links = links_query.all()

    order_ids = [l.order_id for l in links if l.order_id]
    order_map: Dict[str, models.PaperOrder] = {}
    if order_ids:
        orders_db = db.query(models.PaperOrder).filter(models.PaperOrder.id.in_(order_ids)).all()
        order_map = {o.id: o for o in orders_db}

    # Group links by opportunity_id
    opp_to_orders: Dict[str, List[models.PaperOrder]] = {oid: [] for oid in opportunity_ids}
    for l in links:
        if l.opportunity_id and l.order_id in order_map:
            opp_to_orders[l.opportunity_id].append(order_map[l.order_id])

    result: Dict[str, Tuple[Optional[MonitorChartTarget], List[Dict[str, Any]]]] = {}
    for oid in opportunity_ids:
        orders = opp_to_orders.get(oid, [])
        outcomes: List[Dict[str, Any]] = []
        target: Optional[MonitorChartTarget] = None

        for o in orders:
            is_valid_sym = (o.instrument == symbol)
            outcomes.append({
                "order_id": o.id,
                "state": str(o.state).lower() if o.state else "unknown",
                "instrument": o.instrument,
                "direction": o.direction,
                "planned_entry": float(o.planned_entry) if o.planned_entry is not None else None,
                "actual_entry": float(o.actual_entry) if o.actual_entry is not None else None,
                "stop_loss": float(o.stop_loss) if o.stop_loss is not None else None,
                "take_profit": float(o.take_profit) if o.take_profit is not None else None,
                "actual_exit": float(o.actual_exit) if o.actual_exit is not None else None,
                "realized_pnl_net": float(o.realized_pnl_net) if o.realized_pnl_net is not None else None,
                "gross_rr": float(o.gross_rr) if o.gross_rr is not None else None,
                "estimated_net_rr": float(o.estimated_net_rr) if o.estimated_net_rr is not None else None,
                "exit_cause": o.exit_cause,
                "created_at": o.created_at,
                "armed_at": o.armed_at,
                "opened_at": o.opened_at,
                "closed_at": getattr(o, "closed_at", None),
                "is_verified": is_valid_sym,
            })

            if target is None and is_valid_sym:
                target = MonitorChartTarget(
                    kind="ORDER",
                    id=o.id,
                    setup_instance_id=getattr(o, "setup_id", None),
                    verified=True,
                )

        result[oid] = (target, outcomes)

    return result


def group_monitor_opportunities(
    stages: Sequence[NormalizedMonitorStage],
    order_data: Optional[Dict[str, Tuple[Optional[MonitorChartTarget], List[Dict[str, Any]]]]] = None,
) -> List[OpportunityMonitorListItem]:
    """
    Phần 034 - 040, B5: Pure function grouping stage snapshots by canonical opportunity identity.
    Determines current stage from authoritative order states or latest observed stage.
    Never lets an older ARMED override a newer INVALIDATED/EXPIRED.
    """
    if not stages:
        return []

    # Group by opportunity_id
    grouped: Dict[str, List[NormalizedMonitorStage]] = {}
    for s in stages:
        grouped.setdefault(s.opportunity_id, []).append(s)

    items: List[OpportunityMonitorListItem] = []

    for opp_id, s_list in grouped.items():
        # Sort chronologically by snapshot as_of_ms, tie-break by updated_at_ms
        s_list.sort(key=lambda x: (x.as_of_ms, x.updated_at_ms))

        first_rec = min(s.created_at_ms for s in s_list)
        last_obs = max(s.as_of_ms for s in s_list)
        last_stage_rec = s_list[-1]

        # Check linked orders
        chart_target = None
        linked_outcomes: List[Dict[str, Any]] = []
        if order_data and opp_id in order_data:
            chart_target, linked_outcomes = order_data[opp_id]

        # Determine authoritative current stage
        curr_stage = last_stage_rec.stage
        primary_reason_code = last_stage_rec.primary_reason_code
        primary_reason_vi = last_stage_rec.primary_reason_vi
        limitations: List[str] = []

        # If any stage was updated in-place (UPSERT limitation)
        for s in s_list:
            if s.created_at_ms != s.updated_at_ms:
                limitations.append("UPSERT_SNAPSHOT_OVERWRITE: Mốc được cập nhật snapshot mới, không giữ reason cũ")
                break

        # Authoritative order state precedence if closed or open
        if linked_outcomes:
            latest_order = linked_outcomes[-1]
            st = str(latest_order.get("state", "")).lower()
            if st in ("closed", "paper_closed"):
                curr_stage = "CLOSED"
                primary_reason_vi = f"Lệnh đã đóng ({latest_order.get('exit_cause') or 'hoàn tất'})"
            elif st in ("open", "paper_open"):
                curr_stage = "OPEN"
                primary_reason_vi = "Đang mở vị thế trong phiên"
            elif st in ("armed", "paper_armed"):
                if curr_stage not in ("INVALIDATED", "EXPIRED", "CLOSED"):
                    curr_stage = "ARMED"
                    primary_reason_vi = "Lệnh đã đặt chờ khớp"

        # Direction: find non-unknown direction if available
        eff_dir = "UNKNOWN"
        for s in reversed(s_list):
            if s.direction in ("LONG", "SHORT"):
                eff_dir = s.direction
                break

        # Zone label
        zone_label = None
        for s in reversed(s_list):
            if s.zone_id:
                zone_label = s.zone_id
                break

        # Score
        eff_score = None
        score_src = None
        for s in reversed(s_list):
            if s.score is not None:
                eff_score = s.score
                score_src = s.stage
                break

        # Canonical key
        canonical_key = f"{last_stage_rec.run_id or 'norun'}:{opp_id}"

        # Setup chart target fallback if order target missing
        if chart_target is None:
            watch_id = last_stage_rec.payload.get("watch_setup_id") or last_stage_rec.payload.get("setup_id")
            if watch_id:
                chart_target = MonitorChartTarget(
                    kind="SETUP",
                    id=str(watch_id),
                    setup_instance_id=str(watch_id),
                    verified=True,
                )

        items.append(
            OpportunityMonitorListItem(
                key=canonical_key,
                opportunity_id=opp_id,
                run_id=last_stage_rec.run_id,
                model_id=last_stage_rec.model_id,
                direction=eff_dir,
                session_id=last_stage_rec.session_id,
                session_label_vi=last_stage_rec.session_id,
                first_recorded_at_ms=first_rec,
                last_observed_at_ms=last_obs,
                current_stage=curr_stage,
                stage_label_vi=get_stage_title_vi(curr_stage),
                primary_reason_code=primary_reason_code,
                primary_reason_vi=primary_reason_vi,
                classification=None,
                data_quality="PARTIAL" if limitations else "VERIFIED",
                observed_milestone_count=len(s_list),
                score=eff_score,
                score_source=score_src,
                zone_label=zone_label,
                chart_target=chart_target,
                limitations=list(set(limitations)),
            )
        )

    # Sort descending by last_observed_at_ms, tie-break by opportunity_id
    items.sort(key=lambda x: (x.last_observed_at_ms or 0, x.opportunity_id), reverse=True)
    return items


def list_monitor_opportunities(db: Session, query: MonitorQueryContext) -> MonitorListPage:
    """
    Phần 031 - 040, B4: Read-only query for paginated monitor opportunities.
    Uses grouped distinct canonical IDs, avoiding N+1 via batch fetching.
    """
    # Build filter criteria
    conds = [models.OpportunityFunnelRecord.symbol == query.symbol]

    if query.run_id:
        conds.append(
            (models.OpportunityFunnelRecord.run_id == query.run_id)
            | (
                models.OpportunityFunnelRecord.run_id.is_(None)
                & (models.OpportunityFunnelRecord.session_id == query.run_id)
            )
        )
    if query.model_id:
        conds.append(models.OpportunityFunnelRecord.model_id == query.model_id)
    if query.session_id:
        conds.append(models.OpportunityFunnelRecord.session_id == query.session_id)
    if query.start_ms is not None:
        conds.append(models.OpportunityFunnelRecord.as_of_ms >= query.start_ms)
    if query.end_ms is not None:
        conds.append(models.OpportunityFunnelRecord.as_of_ms < query.end_ms)

    # 1. Total distinct canonical opportunities count
    total_count = (
        db.query(func.count(distinct(models.OpportunityFunnelRecord.opportunity_id)))
        .filter(*conds)
        .scalar()
        or 0
    )

    if total_count == 0:
        return MonitorListPage(
            items=[],
            total=0,
            has_more=False,
            next_cursor=None,
            snapshot_as_of_ms=query.now_ms,
            limitations=[],
        )

    # 2. Distinct opportunities with max(as_of_ms) for deterministic pagination
    group_query = (
        db.query(
            models.OpportunityFunnelRecord.opportunity_id,
            func.max(models.OpportunityFunnelRecord.as_of_ms).label("max_as_of"),
        )
        .filter(*conds)
        .group_by(models.OpportunityFunnelRecord.opportunity_id)
        .order_by(func.max(models.OpportunityFunnelRecord.as_of_ms).desc(), models.OpportunityFunnelRecord.opportunity_id.desc())
    )

    # Decode cursor if provided
    cursor_info = decode_cursor(query.cursor)
    if cursor_info:
        cur_as_of, cur_id = cursor_info
        group_query = group_query.having(
            (func.max(models.OpportunityFunnelRecord.as_of_ms) < cur_as_of)
            | (
                (func.max(models.OpportunityFunnelRecord.as_of_ms) == cur_as_of)
                & (models.OpportunityFunnelRecord.opportunity_id < cur_id)
            )
        )

    # Fetch limit + 1 to detect has_more
    paged_rows = group_query.limit(query.limit + 1).all()

    has_more = len(paged_rows) > query.limit
    page_target_rows = paged_rows[: query.limit]
    page_ids = [r[0] for r in page_target_rows]

    next_cursor = None
    if has_more and page_target_rows:
        last_r = page_target_rows[-1]
        next_cursor = encode_cursor(int(last_r[1]), str(last_r[0]))

    if not page_ids:
        return MonitorListPage(
            items=[],
            total=total_count,
            has_more=False,
            next_cursor=None,
            snapshot_as_of_ms=query.now_ms,
            limitations=[],
        )

    # 3. Batch fetch all stage records for page_ids
    stage_rows = (
        db.query(models.OpportunityFunnelRecord)
        .filter(
            models.OpportunityFunnelRecord.opportunity_id.in_(page_ids),
            *conds,
        )
        .all()
    )

    # 4. Batch fetch order links
    order_data = link_monitor_order_evidence(
        db,
        run_id=query.run_id,
        opportunity_ids=page_ids,
        symbol=query.symbol,
    )

    # 5. Normalize and group
    normalized_stages = [normalize_monitor_stage_row(r) for r in stage_rows]
    grouped_items = group_monitor_opportunities(normalized_stages, order_data=order_data)

    # Maintain deterministic ordering matching page_ids
    id_to_item = {item.opportunity_id: item for item in grouped_items}
    ordered_items = [id_to_item[oid] for oid in page_ids if oid in id_to_item]

    return MonitorListPage(
        items=ordered_items,
        total=total_count,
        has_more=has_more,
        next_cursor=next_cursor,
        snapshot_as_of_ms=query.now_ms,
        limitations=["UPSERT_STAGE_SNAPSHOTS: Bảng lưu theo mốc cập nhật cuối của mỗi stage, không phải log từng giây."],
    )


def build_opportunity_monitor_detail(
    db: Session,
    opportunity_id: str,
    query: MonitorQueryContext,
) -> Optional[OpportunityMonitorDetail]:
    """
    Phần 041 - 050, B7: Retrieves detailed observation milestones, plan levels,
    order outcomes, classifier result, and latency metrics for one opportunity.
    """
    clean_opp_id = str(opportunity_id).strip()
    if not clean_opp_id:
        return None

    # Query stages for this opportunity
    stage_query = db.query(models.OpportunityFunnelRecord).filter(
        models.OpportunityFunnelRecord.opportunity_id == clean_opp_id,
        models.OpportunityFunnelRecord.symbol == query.symbol,
    )
    if query.run_id:
        stage_query = stage_query.filter(
            (models.OpportunityFunnelRecord.run_id == query.run_id)
            | (
                models.OpportunityFunnelRecord.run_id.is_(None)
                & (models.OpportunityFunnelRecord.session_id == query.run_id)
            )
        )
    stage_rows = stage_query.all()
    if not stage_rows:
        return None

    normalized_stages = [normalize_monitor_stage_row(r) for r in stage_rows]

    # Query order associations
    order_data = link_monitor_order_evidence(
        db,
        run_id=query.run_id,
        opportunity_ids=[clean_opp_id],
        symbol=query.symbol,
    )

    # Group to get ListItem
    list_items = group_monitor_opportunities(normalized_stages, order_data=order_data)
    if not list_items:
        return None
    list_item = list_items[0]

    _, linked_outcomes = order_data.get(clean_opp_id, (None, []))

    # Milestones construction with provenance
    milestones: List[ObservedMilestone] = []
    limitations: List[str] = []

    # 1. Funnel stage snapshots
    for s in normalized_stages:
        lim = None
        if s.created_at_ms != s.updated_at_ms:
            lim = "Snapshot được cập nhật gần nhất lúc updated_at"
        milestones.append(
            ObservedMilestone(
                id=f"stage_{s.stage}_{s.as_of_ms}",
                stage=s.stage,
                stage_label_vi=s.stage_label_vi,
                first_recorded_at_ms=s.created_at_ms,
                last_updated_at_ms=s.updated_at_ms,
                snapshot_as_of_ms=s.as_of_ms,
                occurred_at_ms=s.as_of_ms,
                reason_codes=s.reason_codes,
                reason_summary_vi=s.primary_reason_vi,
                source="FUNNEL_STAGE_SNAPSHOT",
                time_semantics="STAGE_SNAPSHOT_TIME",
                evidence_ids=s.evidence_ids[:10],
                limitation=lim,
            )
        )

    # 2. Committed order milestones
    for o in linked_outcomes:
        oid = o["order_id"]
        order_milestones = (
            db.query(models.PaperResearchOrderMilestone)
            .filter(models.PaperResearchOrderMilestone.order_id == oid)
            .order_by(models.PaperResearchOrderMilestone.timestamp_ms.asc())
            .all()
        )
        for om in order_milestones:
            m_label = str(om.milestone).upper()
            milestones.append(
                ObservedMilestone(
                    id=f"ord_mile_{om.id}",
                    stage=m_label,
                    stage_label_vi=get_stage_title_vi(m_label),
                    first_recorded_at_ms=om.created_at_ms,
                    last_updated_at_ms=om.created_at_ms,
                    snapshot_as_of_ms=om.timestamp_ms,
                    occurred_at_ms=om.timestamp_ms,
                    reason_codes=[],
                    reason_summary_vi=f"Cột mốc lệnh {om.milestone}",
                    source="COMMITTED_ORDER_MILESTONE",
                    time_semantics="COMMITTED_EVENT_TIME",
                    evidence_ids=[],
                    limitation=None,
                )
            )

    # 3. Traces (bounded)
    traces = (
        db.query(models.CG01TraceRecord)
        .filter(
            (models.CG01TraceRecord.candidate_key == clean_opp_id)
            | (models.CG01TraceRecord.candidate_key == f"cand_{clean_opp_id}")
            | (models.CG01TraceRecord.symbol == query.symbol)
        )
        .order_by(models.CG01TraceRecord.created_at_ms.desc())
        .limit(20)
        .all()
    )

    latency_data: Optional[Dict[str, Any]] = None
    matching_trace = None
    for t in traces:
        if t.candidate_key in (clean_opp_id, f"cand_{clean_opp_id}"):
            matching_trace = t
            break

    if matching_trace:
        latency_data = {
            "trace_id": matching_trace.trace_id,
            "detector_runtime_ms": matching_trace.total_runtime_ms,
            "feed_delay_ms": matching_trace.feed_delay_ms,
            "scheduler_delay_ms": matching_trace.scheduler_delay_ms,
            "arm_delay_ms": matching_trace.arm_delay_ms,
            "fill_delay_ms": matching_trace.fill_delay_ms,
            "sample_count": 1,
            "source": "CG01_TRACE_RECORD",
        }

    # Sort milestones chronologically
    milestones.sort(key=lambda m: (m.snapshot_as_of_ms or m.first_recorded_at_ms or 0))

    # Plan details
    plan_info = None
    for s in reversed(normalized_stages):
        if s.planned_entry is not None:
            plan_info = {
                "entry": s.planned_entry,
                "stop_loss": s.stop_loss,
                "take_profit": s.take_profit,
                "direction": s.direction,
                "price_source": "FUNNEL_STAGE_PAYLOAD",
                "as_of_ms": s.as_of_ms,
            }
            break

    if plan_info is None and linked_outcomes:
        lo = linked_outcomes[0]
        if lo.get("planned_entry") is not None:
            plan_info = {
                "entry": lo["planned_entry"],
                "stop_loss": lo["stop_loss"],
                "take_profit": lo["take_profit"],
                "direction": lo["direction"],
                "price_source": "COMMITTED_ORDER",
                "as_of_ms": lo.get("created_at"),
            }

    # Classifier audit
    eval_dicts = [s.payload or {"stage": s.stage, "reason_codes": s.reason_codes} for s in normalized_stages]
    order_dict = linked_outcomes[0] if linked_outcomes else None
    audit_rec = CG01MissedOpportunityAuditService.classify_opportunity(eval_dicts, order=order_dict)

    classifier_res = {
        "classification": audit_rec.classification,
        "terminal_stage": audit_rec.terminal_stage,
        "miss_reason": audit_rec.miss_reason,
        "notes": audit_rec.notes,
    }
    list_item.classification = audit_rec.classification

    return OpportunityMonitorDetail(
        item=list_item,
        milestones=milestones,
        plan=plan_info,
        order_outcomes=linked_outcomes,
        latency=latency_data,
        classifier_result=classifier_res,
        truncated=False,
        limitations=limitations,
    )


def build_opportunity_monitor_summary(
    db: Session,
    query: MonitorQueryContext,
) -> OpportunityMonitorSummary:
    """
    Phần 071 - 090, B8: Read-only summary aggregating distinct opportunities,
    funnel stage counts, top unique-opportunity reasons, financial metrics (reused from snapshot service),
    and latency metrics.
    """
    # 1. Base conditions for funnel records
    conds = [models.OpportunityFunnelRecord.symbol == query.symbol]
    if query.run_id:
        conds.append(
            (models.OpportunityFunnelRecord.run_id == query.run_id)
            | (
                models.OpportunityFunnelRecord.run_id.is_(None)
                & (models.OpportunityFunnelRecord.session_id == query.run_id)
            )
        )
    if query.model_id:
        conds.append(models.OpportunityFunnelRecord.model_id == query.model_id)
    if query.session_id:
        conds.append(models.OpportunityFunnelRecord.session_id == query.session_id)
    if query.start_ms is not None:
        conds.append(models.OpportunityFunnelRecord.as_of_ms >= query.start_ms)
    if query.end_ms is not None:
        conds.append(models.OpportunityFunnelRecord.as_of_ms < query.end_ms)

    records = db.query(models.OpportunityFunnelRecord).filter(*conds).all()

    # Distinct opportunities
    unique_opp_ids: Set[str] = set()
    stage_counts: Dict[str, int] = {
        "ZONE_DETECTED": 0,
        "ZONE_WATCHED": 0,
        "ZONE_TOUCHED": 0,
        "TRIGGER_CONFIRMED": 0,
        "PLAN_VALID": 0,
        "PLANS_VALID": 0,
        "GUARD_BLOCKED": 0,
        "ARMED": 0,
        "FILLED": 0,
        "EXPIRED": 0,
        "INVALIDATED": 0,
    }
    # Track which unique opportunity had which reason
    opp_reasons: Dict[str, Set[str]] = {}
    latencies: List[float] = []

    for r in records:
        oid = str(r.opportunity_id)
        unique_opp_ids.add(oid)

        stg = str(r.stage).upper()
        if stg in stage_counts:
            stage_counts[stg] += 1

        # Parse reasons
        parsed_reasons: List[str] = []
        if r.reason_codes_json:
            try:
                p = json.loads(r.reason_codes_json)
                if isinstance(p, list):
                    parsed_reasons = [str(x) for x in p]
            except Exception:
                pass
        for code in parsed_reasons:
            opp_reasons.setdefault(code, set()).add(oid)

        # Parse latency
        if r.metrics_json:
            try:
                m = json.loads(r.metrics_json)
                t_start = m.get("evaluation_started_at")
                t_done = m.get("evaluation_completed_at")
                if t_start and t_done and t_done >= t_start:
                    latencies.append(float(t_done - t_start))
            except Exception:
                pass

    total_unique = len(unique_opp_ids)

    # Top rejection reasons (unique opportunities denominator)
    top_reasons_list: List[Dict[str, Any]] = []
    sorted_reasons = sorted(opp_reasons.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for code, opp_set in sorted_reasons[:5]:
        cat, msg_vi = classify_reason(code)
        top_reasons_list.append({
            "code": code,
            "unique_opportunity_count": len(opp_set),
            "category": cat or "GENERAL",
            "message_vi": msg_vi or code,
        })

    # Latency percentiles
    lat_median: Optional[float] = None
    lat_p95: Optional[float] = None
    if latencies:
        latencies.sort()
        n = len(latencies)
        lat_median = round(latencies[n // 2], 2)
        p95_idx = min(int(n * 0.95), n - 1)
        lat_p95 = round(latencies[p95_idx], 2)

    latency_summary = {
        "sample_count": len(latencies),
        "median_ms": lat_median,
        "p95_ms": lat_p95,
        "source": "FUNNEL_EVALUATION_METRICS",
    }

    # Financial Metrics (Reused from snapshot service)
    run_record, _ = find_existing_observation_run(
        db,
        run_id=query.run_id,
        symbol=query.symbol,
        scope=query.scope,
    )
    financial_data: Dict[str, Any] = {
        "metric_scope": "RUN_WINDOW",
        "description_vi": "Số liệu tài chính theo toàn bộ lệnh của phiên quan sát trong khoảng thời gian, không lọc theo model.",
        "orders_count": 0,
        "closed_trades_count": 0,
        "verified_closed_count": 0,
        "wins_count": 0,
        "losses_count": 0,
        "breakevens_count": 0,
        "net_realized_pnl_usd": None,
        "verified_net_pnl_subtotal_usd": 0.0,
        "winrate_pct": None,
        "expectancy_usd_per_trade": None,
        "accounting_status": "COMPLETE",
    }

    if run_record:
        linked_orders, _ = read_linked_orders(db, run_id=run_record.run_id, symbol=query.symbol)
        fin = compute_financial_metrics(
            linked_orders,
            initial_capital=float(run_record.initial_capital_usdt or 1000.0),
            start_ms=query.start_ms,
            end_ms=query.end_ms,
            now_ms=query.now_ms,
        )
        financial_data.update({
            "orders_count": fin.get("orders_count", 0),
            "closed_trades_count": fin.get("closed_trades_count", 0),
            "verified_closed_count": fin.get("verified_closed_count", 0),
            "wins_count": fin.get("wins_count", 0),
            "losses_count": fin.get("losses_count", 0),
            "breakevens_count": fin.get("breakevens_count", 0),
            "net_realized_pnl_usd": fin.get("net_realized_pnl_usd"),
            "verified_net_pnl_subtotal_usd": fin.get("verified_net_pnl_subtotal_usd", 0.0),
            "winrate_pct": fin.get("winrate_pct"),
            "expectancy_usd_per_trade": fin.get("expectancy_usd_per_trade"),
            "accounting_status": fin.get("accounting_status", "COMPLETE"),
        })

    status = "READY" if total_unique > 0 else "EMPTY"

    counts = {
        "total_unique_opportunities": total_unique,
        "zones_detected": stage_counts["ZONE_DETECTED"],
        "zones_watched": stage_counts["ZONE_WATCHED"],
        "zones_touched": stage_counts["ZONE_TOUCHED"],
        "trigger_confirmed": stage_counts["TRIGGER_CONFIRMED"],
        "plans_valid": stage_counts["PLAN_VALID"] + stage_counts["PLANS_VALID"],
        "guard_blocked": stage_counts["GUARD_BLOCKED"],
        "armed": stage_counts["ARMED"],
        "filled": stage_counts["FILLED"],
        "expired": stage_counts["EXPIRED"],
        "invalidated": stage_counts["INVALIDATED"],
    }

    coverage = {
        "run_id": run_record.run_id if run_record else None,
        "run_status": run_record.status if run_record else "NOT_FOUND",
        "symbol": query.symbol,
        "scope": query.scope,
    }

    limitations = [
        "UPSERT_STAGE_SNAPSHOTS: OpportunityFunnelRecord lưu snapshot cuối mỗi stage, không phải append-only tick log.",
        "FINANCIAL_SCOPE_RUN_WINDOW: Số liệu tài chính tính trên toàn bộ lệnh thuộc observation run trong khoảng thời gian.",
    ]

    return OpportunityMonitorSummary(
        schema_version=MONITOR_SCHEMA_VERSION,
        generated_at_ms=query.now_ms,
        query_scope=query.to_dict(),
        status=status,
        counts=counts,
        top_reasons=top_reasons_list,
        financial=financial_data,
        coverage=coverage,
        latency=latency_summary,
        limitations=limitations,
    )


def render_opportunity_monitor_markdown(
    summary: OpportunityMonitorSummary,
    items: Sequence[OpportunityMonitorListItem],
    limitations: Optional[List[str]] = None,
) -> str:
    """
    Phần 100, 141 - 150, B9: Pure markdown report renderer for opportunity monitor.
    Safe, redacted, strictly formatted with clear data dictionary and limitations.
    """
    lines: List[str] = []
    lines.append("# BÁO CÁO THEO DÕI CƠ HỘI PAPER & LÝ DO CHƯA VÀO LỆNH (V17.0)")
    lines.append(f"- **Mã sản phẩm:** `{summary.query_scope.get('symbol', DEFAULT_SYMBOL)}`")
    lines.append(f"- **Phạm vi quan sát:** `{summary.query_scope.get('scope', DEFAULT_SCOPE)}`")
    lines.append(f"- **Phiên quan sát (Run ID):** `{summary.coverage.get('run_id') or 'Không chỉ định'}`")
    dt_gen = datetime.fromtimestamp(summary.generated_at_ms / 1000.0, tz=ZoneInfo("Asia/Ho_Chi_Minh"))
    lines.append(f"- **Thời điểm lập báo cáo:** {dt_gen.strftime('%d/%m/%Y %H:%M:%S (Asia/Ho_Chi_Minh)')}")
    lines.append("")

    lines.append("## 1. Tóm Tắt Tổng Quan")
    c = summary.counts
    lines.append(f"- **Tổng số cơ hội độc lập ghi nhận:** **{c.get('total_unique_opportunities', 0)}**")
    lines.append(f"- **Cơ hội chạm vùng (Touch):** {c.get('zones_touched', 0)}")
    lines.append(f"- **Cơ hội xác nhận Trigger:** {c.get('trigger_confirmed', 0)}")
    lines.append(f"- **Cơ hội bị chặn bởi Guard:** {c.get('guard_blocked', 0)}")
    lines.append(f"- **Lệnh đã ARM (Đặt chờ):** {c.get('armed', 0)}")
    lines.append(f"- **Lệnh đã Khớp (Filled):** {c.get('filled', 0)}")
    lines.append("")

    lines.append("## 2. Kết Quả Tài Chính Sau Phí (Lệnh Thực Tế Thuộc Phiên)")
    fin = summary.financial
    cl_trades = fin.get("closed_trades_count", 0)
    if cl_trades > 0:
        lines.append(f"- **Số lệnh đã đóng xác nhận:** {cl_trades} (Thắng: {fin.get('wins_count', 0)}, Thua: {fin.get('losses_count', 0)}, Hòa: {fin.get('breakevens_count', 0)})")
        lines.append(f"- **Tỷ lệ thắng (Winrate):** {fin.get('winrate_pct', 'N/A')}%")
        pnl = fin.get("net_realized_pnl_usd")
        pnl_str = f"{pnl:+.2f} USDT" if pnl is not None else "Chưa xác minh toàn phần"
        lines.append(f"- **Lãi/lỗ ròng sau phí:** {pnl_str}")
        exp = fin.get("expectancy_usd_per_trade")
        exp_str = f"{exp:+.2f} USDT/lệnh" if exp is not None else "N/A"
        lines.append(f"- **Kỳ vọng lợi nhuận (Expectancy):** {exp_str}")
    else:
        lines.append("- **Số lệnh đã đóng:** 0 lệnh (Tỷ lệ thắng và kỳ vọng lợi nhuận: N/A - Không đủ dữ liệu mẫu).")
    lines.append("")

    lines.append("## 3. Lý Do Phổ Biến Chưa Vào Lệnh")
    if summary.top_reasons:
        for r in summary.top_reasons:
            lines.append(f"- **{r.get('message_vi')}** (`{r.get('code')}`): {r.get('unique_opportunity_count')} cơ hội")
    else:
        lines.append("- Chưa ghi nhận lý do từ chối nào trong khoảng thời gian này.")
    lines.append("")

    lines.append("## 4. Danh Sách Cơ Hội Đã Quan Sát")
    if items:
        lines.append("| Thời gian ghi nhận | Hướng | Trạng thái hiện tại | Lý do chính | Điểm | Vùng theo dõi |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for it in items[:50]:
            t_str = "--:--"
            if it.last_observed_at_ms:
                dt_it = datetime.fromtimestamp(it.last_observed_at_ms / 1000.0, tz=ZoneInfo("Asia/Ho_Chi_Minh"))
                t_str = dt_it.strftime("%H:%M:%S")
            score_str = f"{it.score:.0f}" if it.score is not None else "-"
            zone_str = it.zone_label or "-"
            lines.append(f"| {t_str} | **{it.direction}** | {it.stage_label_vi} | {it.primary_reason_vi} | {score_str} | `{zone_str}` |")
        if len(items) > 50:
            lines.append(f"\n*(Đã giới hạn hiển thị 50 / {len(items)} cơ hội)*")
    else:
        lines.append("*(Không có cơ hội nào phù hợp bộ lọc)*")
    lines.append("")

    lines.append("## 5. Từ Điển Dữ Liệu & Giới Hạn Cần Biết")
    lines.append("1. **Cơ hội độc lập (Unique Opportunity):** Đếm theo mã định danh duy nhất (opportunity_id). Một cơ hội đi qua nhiều mốc (Phát hiện -> Trigger -> Guard) chỉ được tính là 1 cơ hội.")
    lines.append("2. **Cơ chế UPSERT mốc stage:** Cơ sở dữ liệu lưu snapshot theo cặp `(opportunity_id, stage)`. Khi stage được cập nhật, lý do và thời điểm mới nhất ghi đè bản cũ.")
    lines.append("3. **Bị chặn bởi Guard (POLICY_BLOCKED):** Đây là hành vi bảo vệ chủ động của hệ thống (tin tức mạnh, hết quota ngày, bài học kinh nghiệm), không phải lỗi phần mềm.")
    lines.append("4. **Chưa có lệnh:** Việc không có lệnh trong phiên là bình thường nếu thị trường không thỏa mãn điều kiện nghiêm ngặt của mô hình.")
    lines.append("")

    if summary.limitations:
        lines.append("## 6. Hạn Chế Đo Lường Ghi Nhận")
        for lim in summary.limitations:
            lines.append(f"- {lim}")
        lines.append("")

    return redact_sensitive_text("\n".join(lines))
