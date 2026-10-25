"""
backend/services/engine_observability_service.py
AURUM V17.6 — Durable Engine Observability, Decision Intelligence & Forward Paper Monitoring Service.

Provides:
- Tier 1: Engine heartbeat, liveness detection, stalled engine detection, operational incident logging.
- Tier 1: Canonical market feed freshness, quote age calculation, multi-timeframe candle tracking, gap detection.
- Tier 1: Evaluation cycle telemetry, zero-opportunity cycle tracking, duration measurement.
- Tier 2: Decision event recording, standardized reason categorization, waiting conditions inspection, setup timeline.
- Tier 3: Forward paper observation run health, truthful coverage denominator, post-fee financial metrics.
- Read Model: Authoritative, strictly read-only dashboard snapshot builder.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, text, desc, and_
from sqlalchemy.orm import Session

import crud
import models
from database import SessionLocal
from services.candle_cache_service import TF_MS, FRESHNESS_THRESHOLDS_SEC, candle_cache_service
from services.collector_service import collector_service
from services.paper_opportunity_monitor_service import (
    STAGE_TITLES_VI,
    get_reason_description_vi,
    get_stage_title_vi,
)
from services.paper_research_run_service import (
    PaperResearchRun,
    PaperResearchRunService,
    build_cg01_paper_run_summary,
    resolve_active_observation_run,
)

logger = logging.getLogger("engine_observability_service")

# ---------------------------------------------------------------------------
# Constants & Taxonomy
# ---------------------------------------------------------------------------
OBSERVATION_SCHEMA_VERSION = "1.0.0"
ENGINE_CADENCE_DEFAULT_SEC = 15
QUOTE_FRESHNESS_THRESHOLD_SEC = 15.0
STALLED_CADENCE_MULTIPLIER = 3

REASON_CATEGORIES = {
    "STRUCTURE": "Cấu trúc thị trường",
    "MARKET_DATA": "Dữ liệu thị trường",
    "RISK": "Quản trị rủi ro",
    "NET_RR": "Tỷ lệ Lợi nhuận/Rủi ro",
    "NEWS": "Tin tức & Blackout",
    "SESSION": "Phiên giao dịch",
    "CAPITAL": "Vốn & Ký quỹ",
    "EXECUTION": "Thực thi kỹ thuật",
    "CONFIGURATION": "Cấu hình chiến lược",
    "SYSTEM": "Hệ thống vận hành",
}


# ---------------------------------------------------------------------------
# Timezone & Market Calendar Helpers
# ---------------------------------------------------------------------------
def get_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def resolve_canonical_quote_timestamps(
    quote: Any,
    *,
    now_ms: int,
) -> Dict[str, Any]:
    """
    OBS-01: Canonical market quote timestamp authority.
    Extracts and strictly validates exchange_ts_ms and received_at_ms.
    Calculates exchange_age_ms, receive_age_ms, and transport_delay_ms.
    Fails closed on missing, malformed, negative, or clock-skewed timestamps.
    """
    if quote is None:
        return {
            "status": "UNAVAILABLE",
            "exchange_ts_ms": None,
            "received_at_ms": None,
            "exchange_age_ms": None,
            "receive_age_ms": None,
            "transport_delay_ms": None,
            "bid": None,
            "ask": None,
            "last": None,
            "reason_codes": ["QUOTE_IS_NONE"],
        }

    # Extract raw attributes whether dict, CanonicalQuote, or legacy object
    raw_exch = None
    raw_recv = None
    bid = None
    ask = None
    last = None

    if isinstance(quote, dict):
        bid = quote.get("bid")
        ask = quote.get("ask")
        last = quote.get("last")
        raw_exch = quote.get("exchange_ts_ms") or quote.get("ts")
        raw_recv = quote.get("received_at_ms") or quote.get("observed_at")
        if raw_exch is None:
            raw_exch = quote.get("timestamp") or quote.get("time")
    else:
        # Check canonical attributes without triggering mock auto-generation
        for attr in ("exchange_ts_ms", "ts"):
            if hasattr(quote, attr):
                val = getattr(quote, attr)
                if not hasattr(val, "_mock_name"):
                    raw_exch = val
                    break
        for attr in ("received_at_ms", "observed_at"):
            if hasattr(quote, attr):
                val = getattr(quote, attr)
                if not hasattr(val, "_mock_name"):
                    raw_recv = val
                    break
        # Legacy attributes fallback
        if raw_exch is None:
            for attr in ("timestamp", "time"):
                if hasattr(quote, attr):
                    val = getattr(quote, attr)
                    if not hasattr(val, "_mock_name"):
                        raw_exch = val
                        break

        for attr in ("bid", "ask", "last"):
            if hasattr(quote, attr):
                val = getattr(quote, attr)
                if not hasattr(val, "_mock_name"):
                    if attr == "bid":
                        bid = val
                    elif attr == "ask":
                        ask = val
                    elif attr == "last":
                        last = val

    def _validate_ts(val: Any) -> Optional[int]:
        if val is None or isinstance(val, bool) or hasattr(val, "_mock_name"):
            return None
        if isinstance(val, (int, float)):
            if not math.isfinite(val):
                return None
            val_int = int(val)
        elif isinstance(val, str) and val.strip().isdigit():
            val_int = int(val.strip())
        else:
            return None

        # Millisecond timestamp must be reasonable (e.g. > 1_000_000_000_000, i.e., year 2001+)
        # and not absurdly in future (> now_ms + 60_000)
        if val_int < 1_000_000_000_000 or val_int > (now_ms + 60_000):
            return None
        return val_int

    valid_exch = _validate_ts(raw_exch)
    valid_recv = _validate_ts(raw_recv)

    reason_codes: List[str] = []
    if valid_exch is None and raw_exch is not None:
        reason_codes.append("INVALID_EXCHANGE_TS")
    if valid_recv is None and raw_recv is not None:
        reason_codes.append("INVALID_RECEIVE_TS")

    exchange_age_ms = max(0, now_ms - valid_exch) if valid_exch is not None else None
    receive_age_ms = max(0, now_ms - valid_recv) if valid_recv is not None else None

    transport_delay_ms = None
    if valid_exch is not None and valid_recv is not None:
        transport_delay_ms = max(0, valid_recv - valid_exch)

    if valid_exch is not None:
        status = "VALID"
    elif valid_recv is not None:
        status = "UNVERIFIED_EXCHANGE"
        reason_codes.append("EXCHANGE_TS_MISSING_FALLBACK_TO_RECEIVE")
    else:
        status = "UNAVAILABLE"
        reason_codes.append("ALL_TIMESTAMPS_MISSING")

    def _to_float(v: Any) -> Optional[float]:
        if v is None or isinstance(v, bool) or hasattr(v, "_mock_name"):
            return None
        try:
            fv = float(v)
            return fv if math.isfinite(fv) and fv > 0 else None
        except (ValueError, TypeError):
            return None

    return {
        "status": status,
        "exchange_ts_ms": valid_exch,
        "received_at_ms": valid_recv,
        "exchange_age_ms": exchange_age_ms,
        "receive_age_ms": receive_age_ms,
        "transport_delay_ms": transport_delay_ms,
        "bid": _to_float(bid),
        "ask": _to_float(ask),
        "last": _to_float(last),
        "reason_codes": reason_codes,
    }


def is_xauusdt_market_closed(timestamp_ms: int) -> Tuple[bool, str]:
    """
    OBS-07: Authoritative XAUUSDT market hours with New York wall-clock DST awareness.
    Returns: (is_closed: bool, reason: "WEEKEND_BREAK" | "DAILY_MAINTENANCE" | "OPEN")
    Schedule:
    - Weekend break: Friday 17:00 NY to Sunday 18:00 NY.
    - Daily maintenance: Monday-Thursday 17:00 to 18:00 NY.
    """
    tz_ny = get_tz("America/New_York")
    dt_ny = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=tz_ny)
    weekday = dt_ny.weekday()  # 0 = Monday, ..., 4 = Friday, 5 = Saturday, 6 = Sunday
    hour = dt_ny.hour

    # Friday after 17:00 NY -> Weekend break
    if weekday == 4 and hour >= 17:
        return True, "WEEKEND_BREAK"
    # Saturday -> All day weekend break
    if weekday == 5:
        return True, "WEEKEND_BREAK"
    # Sunday before 18:00 NY -> Weekend break
    if weekday == 6 and hour < 18:
        return True, "WEEKEND_BREAK"
    # Mon-Thu daily break: 17:00 to 18:00 NY
    if weekday in (0, 1, 2, 3) and hour == 17:
        return True, "DAILY_MAINTENANCE"

    return False, "OPEN"


def is_xauusdt_weekend_break(timestamp_ms: int) -> bool:
    """Convenience wrapper for weekend break checks."""
    is_closed, reason = is_xauusdt_market_closed(timestamp_ms)
    return is_closed and reason == "WEEKEND_BREAK"


def calculate_market_open_duration_ms(start_ms: int, end_ms: int, step_minutes: int = 15) -> int:
    """
    OBS-06/07: Calculates exact duration (in ms) that XAUUSDT market is open
    between start_ms and end_ms, respecting NY wall-clock weekend breaks and daily maintenance.
    """
    if end_ms <= start_ms:
        return 0

    total_ms = end_ms - start_ms
    step_ms = step_minutes * 60 * 1000

    if total_ms < 30 * 60 * 1000:
        step_ms = 60 * 1000

    open_ms = 0
    curr = start_ms
    while curr < end_ms:
        next_curr = min(curr + step_ms, end_ms)
        mid_point = curr + (next_curr - curr) // 2
        is_closed, _ = is_xauusdt_market_closed(mid_point)
        if not is_closed:
            open_ms += (next_curr - curr)
        curr = next_curr

    return open_ms


# ---------------------------------------------------------------------------
# Reason Categorization & Localization
# ---------------------------------------------------------------------------
def categorize_reason_code(code: Optional[str]) -> str:
    """Categorizes raw reason code into standardized category."""
    if not code:
        return "STRUCTURE"
    c = str(code).upper()

    if any(k in c for k in ("SWEEP", "MSS", "FVG", "BOS", "CHOCH", "ZONE", "POI", "STRUCTURE", "LEG", "ANCHOR", "REVERSAL", "CONTINUATION")):
        return "STRUCTURE"
    if any(k in c for k in ("QUOTE", "CANDLE", "DATA", "FEED", "STALE", "GAP", "SPREAD", "TICKER")):
        return "MARKET_DATA"
    if any(k in c for k in ("RISK", "DAILY_LOSS", "MAX_LOSS", "MAX_POSITIONS", "ONE_POSITION", "CAP", "CONCURRENT")):
        return "RISK"
    if any(k in c for k in ("NET_RR", "GROSS_RR", "MIN_RR", "FRICTION", "SLIPPAGE", "FEE")):
        return "NET_RR"
    if any(k in c for k in ("NEWS", "BLACKOUT", "NFP", "FOMC", "CPI")):
        return "NEWS"
    if any(k in c for k in ("SESSION", "NY_", "ASIA", "LONDON", "TIME_WINDOW")):
        return "SESSION"
    if any(k in c for k in ("CAPITAL", "EQUITY", "MARGIN", "LEVERAGE", "BALANCE")):
        return "CAPITAL"
    if any(k in c for k in ("LOCK", "MUTATION", "SERIALIZE", "COLLISION", "TIMEOUT", "EXECUTION")):
        return "EXECUTION"
    if any(k in c for k in ("DISABLED", "CONFIG", "FLAG", "MODE_OFF", "ALLOWLIST", "PROFILE")):
        return "CONFIGURATION"
    if any(k in c for k in ("FAIL", "ROLLBACK", "DB_", "INTERNAL", "EXCEPTION", "ERROR", "POISON")):
        return "SYSTEM"

    return "STRUCTURE"


def get_primary_decision_reason(
    reason_codes: Sequence[str],
    stage: str,
    decision_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Evaluates primary decision reason with category, Vietnamese title, and technical detail.
    """
    if not reason_codes:
        if stage in ("ARMED", "FILLED", "ENTRY_READY", "QUALIFIED"):
            return {
                "primary_reason_code": "ALL_CONDITIONS_MET",
                "category": "STRUCTURE",
                "category_title_vi": REASON_CATEGORIES["STRUCTURE"],
                "short_message_vi": "Tất cả điều kiện SMC & Quản trị rủi ro đã đạt yêu cầu",
                "technical_message": "All structural criteria and risk safety guards passed.",
            }
        return {
            "primary_reason_code": "AWAITING_CONDITIONS",
            "category": "STRUCTURE",
            "category_title_vi": REASON_CATEGORIES["STRUCTURE"],
            "short_message_vi": "Đang theo dõi và chờ xác nhận thêm điều kiện SMC",
            "technical_message": "Setup is awaiting required SMC structural milestones.",
        }

    # Priority ranking of reason codes: Safety & guards first, then structure
    priority_order = [
        "CYCLE_WRITE_FAILED",
        "QUOTE_STALE",
        "NO_QUOTE",
        "DAILY_LOSS_LIMIT",
        "MAX_DAILY_FILLS",
        "MAX_OPEN_POSITIONS",
        "ONE_POSITION_PER_SYMBOL",
        "NEWS_BLACKOUT",
        "SESSION_NOT_ELIGIBLE",
        "NET_RR_BELOW_MINIMUM",
        "MIN_NET_RR_POLICY_VIOLATED",
        "SWEEP_NOT_CONFIRMED",
        "MSS_NOT_CONFIRMED",
        "FVG_NOT_CONFIRMED",
        "ZONE_BREACHED",
        "NO_SETUP",
    ]

    selected_code = str(reason_codes[0])
    for p in priority_order:
        for r in reason_codes:
            if p in str(r).upper():
                selected_code = str(r)
                break
        if selected_code != str(reason_codes[0]):
            break

    cat = categorize_reason_code(selected_code)
    vi_desc = get_reason_description_vi(selected_code)
    if not vi_desc or vi_desc == selected_code or vi_desc.startswith("Mã:"):
        if cat == "NET_RR":
            vi_desc = "Tỷ lệ Net R:R không đạt ngưỡng tối thiểu theo chính sách"
        elif cat == "STRUCTURE":
            vi_desc = "Cấu trúc thị trường chưa hoàn tất điều kiện SMC/ICT"
        elif cat == "RISK":
            vi_desc = "Giới hạn quản trị rủi ro (Risk Guard) đã bị chặn"
        elif cat == "MARKET_DATA":
            vi_desc = "Dữ liệu thị trường chưa đủ mới hoặc mất kết nối báo giá"
        elif cat == "NEWS":
            vi_desc = "Trong khung thời gian tin tức mạnh bị cấm vào lệnh"
        elif cat == "SESSION":
            vi_desc = "Ngoài khung thời gian phiên giao dịch cho phép"
        elif cat == "CAPITAL":
            vi_desc = "Số dư hoặc mức ký quỹ tài khoản không đủ"
        elif cat == "EXECUTION":
            vi_desc = "Tiến trình vào lệnh bị khóa hoặc vượt quá số lệnh cho phép"
        elif cat == "CONFIGURATION":
            vi_desc = "Mô hình hoặc tính năng đang bị tắt trong cấu hình"
        elif cat == "SYSTEM":
            vi_desc = "Lỗi hệ thống hoặc cơ sở dữ liệu tạm thời"

    return {
        "primary_reason_code": selected_code,
        "category": cat,
        "category_title_vi": REASON_CATEGORIES.get(cat, cat),
        "short_message_vi": vi_desc,
        "technical_message": f"Evaluated reason '{selected_code}' under stage '{stage}'.",
    }


# ---------------------------------------------------------------------------
# Waiting Conditions Inspector
# ---------------------------------------------------------------------------
def inspect_waiting_conditions(
    stage: str,
    payload: Optional[Dict[str, Any]] = None,
    now_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Extracts detailed waiting conditions for a setup currently in WAITING or WATCHING stage.
    """
    p = payload or {}
    conditions: List[Dict[str, Any]] = []

    # 1. Sweep confirmation
    has_sweep = bool(p.get("sweep_confirmed") or p.get("liquidity_swept") or False)
    conditions.append({
        "id": "LIQUIDITY_SWEEP",
        "name_vi": "Quét thanh khoản (Liquidity Sweep)",
        "status": "FULFILLED" if has_sweep else "WAITING",
        "detail": "Đã quét thanh khoản vùng đỉnh/đáy" if has_sweep else "Chờ giá quét thanh khoản tại vùng POI/Zone",
    })

    # 2. Market Structure Shift (MSS)
    has_mss = bool(p.get("mss_confirmed") or p.get("structure_broken") or False)
    conditions.append({
        "id": "MSS_CONFIRMATION",
        "name_vi": "Phá vỡ cấu trúc (MSS)",
        "status": "FULFILLED" if has_mss else "WAITING",
        "detail": "Cấu trúc M1 đã đảo chiều (MSS đã xác nhận)" if has_mss else "Chờ nến M1 đóng qua mốc swing để xác nhận MSS",
    })

    # 3. Fair Value Gap (FVG)
    has_fvg = bool(p.get("fvg_confirmed") or p.get("has_fvg") or False)
    conditions.append({
        "id": "FVG_IMBALANCE",
        "name_vi": "Tạo khoảng trống FVG",
        "status": "FULFILLED" if has_fvg else "WAITING",
        "detail": "Đã xuất hiện FVG hợp lệ làm điểm tựa vào lệnh" if has_fvg else "Chờ nến tạo FVG mất cân bằng giá",
    })

    # 4. Net Risk:Reward Check
    est_rr = float(p.get("estimated_net_rr") or p.get("net_rr") or 0.0)
    min_rr = float(p.get("min_net_rr") or 2.0)
    rr_ok = est_rr >= min_rr if est_rr > 0 else False
    conditions.append({
        "id": "NET_RR_CRITERIA",
        "name_vi": f"Tỷ lệ Net R:R (Yêu cầu >= {min_rr:.1f}R)",
        "status": "FULFILLED" if rr_ok else ("WAITING" if est_rr <= 0 else "FAILED"),
        "detail": f"Net R:R ước tính: {est_rr:.2f}R" if est_rr > 0 else "Chờ xác định mức Stop Loss để tính Net R:R",
    })

    # 5. Entry Session Window
    session_eligible = bool(p.get("session_eligible", True))
    conditions.append({
        "id": "SESSION_WINDOW",
        "name_vi": "Khung thời gian giao dịch hợp lệ",
        "status": "FULFILLED" if session_eligible else "BLOCKED",
        "detail": "Đang trong phiên giao dịch cho phép" if session_eligible else "Ngoài khung giờ giao dịch của phiên",
    })

    return conditions


# ---------------------------------------------------------------------------
# Pure Coverage & Cursor Helpers
# ---------------------------------------------------------------------------
def clip_interval(start_ms: int, end_ms: int, win_start_ms: int, win_end_ms: int) -> Optional[Tuple[int, int]]:
    """Clips an interval [start_ms, end_ms] to [win_start_ms, win_end_ms] (FIX-02 & PART 040)."""
    s = max(start_ms, win_start_ms)
    e = min(end_ms, win_end_ms)
    return (s, e) if e > s else None


def merge_time_intervals(intervals: List[Tuple[int, int]]) -> int:
    """Merges overlapping intervals and returns total non-overlapping duration in ms (FIX-02 & PART 041)."""
    if not intervals:
        return 0
    sorted_invs = sorted(intervals, key=lambda x: x[0])
    merged: List[Tuple[int, int]] = []
    cur_start, cur_end = sorted_invs[0]
    for s, e in sorted_invs[1:]:
        if s <= cur_end:
            cur_end = max(cur_end, e)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = s, e
    merged.append((cur_start, cur_end))
    return sum(e - s for s, e in merged)



def normalize_interval_ranges(intervals: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Return a sorted, non-overlapping union of positive-length half-open intervals."""
    intervals = sorted((int(s), int(e)) for s, e in intervals if e > s)
    merged: List[Tuple[int, int]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def intersect_interval_ranges(
    left: List[Tuple[int, int]], right: List[Tuple[int, int]]
) -> List[Tuple[int, int]]:
    """Intersection of two interval sets, with no double counting."""
    a = normalize_interval_ranges(left)
    b = normalize_interval_ranges(right)
    i = j = 0
    result: List[Tuple[int, int]] = []
    while i < len(a) and j < len(b):
        start = max(a[i][0], b[j][0])
        end = min(a[i][1], b[j][1])
        if end > start:
            result.append((start, end))
        if a[i][1] <= b[j][1]:
            i += 1
        else:
            j += 1
    return result


def subtract_interval_ranges(
    source: List[Tuple[int, int]], excluded: List[Tuple[int, int]]
) -> List[Tuple[int, int]]:
    """Subtract a union of excluded intervals from a union of source intervals."""
    base = normalize_interval_ranges(source)
    removal = normalize_interval_ranges(excluded)
    result: List[Tuple[int, int]] = []
    j = 0
    for start, end in base:
        cursor = start
        while j < len(removal) and removal[j][1] <= cursor:
            j += 1
        k = j
        while k < len(removal) and removal[k][0] < end:
            cut_start, cut_end = removal[k]
            if cut_start > cursor:
                result.append((cursor, min(cut_start, end)))
            cursor = max(cursor, cut_end)
            if cursor >= end:
                break
            k += 1
        if cursor < end:
            result.append((cursor, end))
    return result


def build_market_open_interval_ranges(start_ms: int, end_ms: int) -> List[Tuple[int, int]]:
    """
    Segment the configured New York market schedule by absolute UTC-hour boundaries.
    All current weekly/daily closure transitions occur on NY whole-hour boundaries;
    NY DST changes also occur on UTC-hour boundaries. This is the configured schedule,
    not proof of exchange holiday availability.
    """
    if end_ms <= start_ms:
        return []
    hour_ms = 3_600_000
    intervals: List[Tuple[int, int]] = []
    cursor = start_ms
    while cursor < end_ms:
        boundary = min(end_ms, ((cursor // hour_ms) + 1) * hour_ms)
        midpoint = cursor + (boundary - cursor) // 2
        if not is_xauusdt_market_closed(midpoint)[0]:
            intervals.append((cursor, boundary))
        cursor = boundary
    return normalize_interval_ranges(intervals)


def decode_observation_cursor(
    cursor_str: str,
    expected_run_id: Optional[str] = None,
    expected_symbol: Optional[str] = None,
) -> Tuple[int, int]:
    """
    Decodes and strictly validates pagination cursor (FIX-09 & PART 069-070).
    Raises HTTPException(422) on corrupted or invalid cursors.
    """
    from fastapi import HTTPException
    try:
        raw = base64.b64decode(cursor_str.strip()).decode("utf-8")
        parts = raw.split("_")
        if len(parts) < 2:
            raise ValueError("Insufficient cursor segments")
        ts_part = int(parts[0])
        id_part = int(parts[1])
        if ts_part <= 0 or id_part <= 0:
            raise ValueError("Non-positive timestamp or id")
        # Check run_id mismatch if encoded in cursor (parts[2])
        if len(parts) >= 3 and expected_run_id and parts[2] and parts[2] != expected_run_id:
            raise ValueError(f"Cursor run_id mismatch: expected {expected_run_id}, got {parts[2]}")
        # Check symbol mismatch if encoded in cursor (parts[3])
        if len(parts) >= 4 and expected_symbol and parts[3] and parts[3] != expected_symbol:
            raise ValueError(f"Cursor symbol mismatch: expected {expected_symbol}, got {parts[3]}")
        return ts_part, id_part
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid pagination cursor: {e}")


# ---------------------------------------------------------------------------
# Engine Observability Service Class
# ---------------------------------------------------------------------------
class EngineObservabilityService:
    def __init__(self):
        self._lock = threading.Lock()
        self._current_instance_id = f"aurum-engine-{uuid.uuid4().hex[:12]}"
        self._boot_time_ms = int(time.time() * 1000)
        self._recent_cycles_buffer: List[Dict[str, Any]] = []
        self._max_recent_cycles = 50
        self._latest_cycle_diagnostic: Optional[Dict[str, Any]] = None
        self._active_incidents: Dict[str, Dict[str, Any]] = {}

    @property
    def current_instance_id(self) -> str:
        return self._current_instance_id

    # -----------------------------------------------------------------------
    # 1. Engine Heartbeat & Liveness (Phase B)
    # -----------------------------------------------------------------------
    def record_engine_heartbeat(
        self,
        db: Optional[Session],
        *,
        engine_instance_id: Optional[str] = None,
        symbol: str = "XAUUSDT",
        run_id: Optional[str] = None,
        state: str = "RUNNING",
        heartbeat_at_ms: Optional[int] = None,
        last_cycle_id: Optional[str] = None,
        evaluation_started_at_ms: Optional[int] = None,
        evaluation_completed_at_ms: Optional[int] = None,
        successful_cycle: bool = False,
        cycle_failed: bool = False,
        cadence_sec: int = ENGINE_CADENCE_DEFAULT_SEC,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Durable UPSERT into engine_heartbeats table.
        Maintains distinct timestamps: process heartbeat vs evaluation cycle times.
        """
        inst_id = engine_instance_id or self._current_instance_id
        t_now = int(heartbeat_at_ms if heartbeat_at_ms is not None else int(time.time() * 1000))

        record_data = {
            "engine_instance_id": inst_id,
            "symbol": symbol,
            "run_id": run_id,
            "state": state,
            "last_process_heartbeat_at_ms": t_now,
            "last_cycle_id": last_cycle_id,
            "cadence_sec": cadence_sec,
            "updated_at_ms": t_now,
        }

        close_session = False
        target_db = db
        if target_db is None:
            try:
                from database import SessionLocal
                target_db = SessionLocal()
                close_session = True
            except Exception as e:
                logger.warning(f"Could not open fallback SessionLocal for heartbeat: {e}")

        if target_db is not None:
            try:
                row = target_db.query(models.EngineHeartbeatRecord).filter(
                    models.EngineHeartbeatRecord.engine_instance_id == inst_id
                ).first()

                if row is None:
                    row = models.EngineHeartbeatRecord(
                        engine_instance_id=inst_id,
                        symbol=symbol,
                        run_id=run_id,
                        state=state,
                        last_process_heartbeat_at_ms=t_now,
                        last_evaluation_started_at_ms=evaluation_started_at_ms,
                        last_evaluation_completed_at_ms=evaluation_completed_at_ms,
                        last_successful_evaluation_at_ms=t_now if successful_cycle else None,
                        last_cycle_id=last_cycle_id,
                        cycles_count=1 if successful_cycle or cycle_failed else 0,
                        failed_cycles_count=1 if cycle_failed else 0,
                        cadence_sec=cadence_sec,
                        metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
                        updated_at_ms=t_now,
                    )
                    target_db.add(row)
                else:
                    row.state = state
                    row.last_process_heartbeat_at_ms = t_now
                    if run_id:
                        row.run_id = run_id
                    if last_cycle_id:
                        row.last_cycle_id = last_cycle_id
                    if evaluation_started_at_ms:
                        row.last_evaluation_started_at_ms = evaluation_started_at_ms
                    if evaluation_completed_at_ms:
                        row.last_evaluation_completed_at_ms = evaluation_completed_at_ms
                    if successful_cycle:
                        row.last_successful_evaluation_at_ms = evaluation_completed_at_ms or t_now
                        row.cycles_count = (row.cycles_count or 0) + 1
                    if cycle_failed:
                        row.failed_cycles_count = (row.failed_cycles_count or 0) + 1
                    if metadata:
                        row.metadata_json = json.dumps(metadata, ensure_ascii=False)
                    row.updated_at_ms = t_now

                if close_session:
                    target_db.commit()
                else:
                    target_db.flush()
            except Exception as e:
                if close_session:
                    target_db.rollback()
                logger.warning(f"Error persisting EngineHeartbeatRecord: {e}")
            finally:
                if close_session:
                    target_db.close()

        return record_data

    def persist_engine_heartbeat(self, **kwargs) -> Dict[str, Any]:
        """
        Durable isolated writer (FIX-01 & PART 015):
        Persists heartbeat using an owned session that commits independently.
        """
        return self.record_engine_heartbeat(db=None, **kwargs)


    def detect_stalled_engine(
        self,
        *,
        heartbeat_record: Optional[Any],
        now_ms: int,
        feed_health: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """
        Determines if engine is stalled based on true runtime evidence.
        Distinguishes between valid market waiting, weekend break, maintenance, and genuine engine hang.
        """
        if heartbeat_record is None:
            return True, "NO_HEARTBEAT_RECORD"

        state = getattr(heartbeat_record, "state", "UNKNOWN")
        if state in ("PAUSED", "STOPPED"):
            return False, f"ENGINE_INTENTIONALLY_{state}"

        cadence = getattr(heartbeat_record, "cadence_sec", ENGINE_CADENCE_DEFAULT_SEC) or ENGINE_CADENCE_DEFAULT_SEC
        stall_threshold_ms = cadence * STALLED_CADENCE_MULTIPLIER * 1000

        # 1. Process heartbeat liveness first (FIX-06 & PART 074):
        # A dead worker process is STALLED regardless of whether the market is open or closed!
        last_hb = getattr(heartbeat_record, "last_process_heartbeat_at_ms", 0) or 0
        hb_age = now_ms - last_hb
        if hb_age > stall_threshold_ms:
            return True, f"HEARTBEAT_STALE_AGE_{int(hb_age / 1000)}s"

        # 2. Weekend break: exchange closed, engine legitimately waits
        if is_xauusdt_weekend_break(now_ms):
            return False, "MARKET_WEEKEND_BREAK"

        # 3. Check daily maintenance for evaluation cycle stall
        is_market_closed, close_reason = is_xauusdt_market_closed(now_ms)
        if is_market_closed:
            return False, f"MARKET_{close_reason}"

        # Check if feed is disconnected or degraded
        if feed_health and feed_health.get("freshness_state") in ("DISCONNECTED", "UNKNOWN"):
            return False, "WAITING_FEED_CONNECTION"

        last_eval_done = getattr(heartbeat_record, "last_evaluation_completed_at_ms", 0) or 0
        if last_eval_done > 0:
            eval_age = now_ms - last_eval_done
            # If healthy feed and open market, cycle should complete within 2 * stall threshold
            if eval_age > stall_threshold_ms * 2:
                return True, f"EVALUATION_CYCLE_STALLED_AGE_{int(eval_age / 1000)}s"

        return False, "HEALTHY"

    def build_engine_health_snapshot(
        self,
        db: Optional[Session],
        *,
        symbol: str = "XAUUSDT",
        now_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Builds authoritative engine health snapshot from DB and in-memory state.
        Prioritizes active worker instance ID, then latest record for symbol.
        """
        t_now = int(now_ms if now_ms is not None else int(time.time() * 1000))
        latest_hb = None

        if db is not None:
            try:
                # Prioritize current instance ID if recorded
                latest_hb = db.query(models.EngineHeartbeatRecord).filter(
                    models.EngineHeartbeatRecord.engine_instance_id == self._current_instance_id
                ).first()
                if not latest_hb:
                    latest_hb = db.query(models.EngineHeartbeatRecord).filter(
                        models.EngineHeartbeatRecord.symbol == symbol
                    ).order_by(models.EngineHeartbeatRecord.updated_at_ms.desc()).first()
            except Exception:
                latest_hb = None

        feed_health = self.build_market_feed_health_snapshot(symbol=symbol, now_ms=t_now)
        is_stalled, stall_reason = self.detect_stalled_engine(
            heartbeat_record=latest_hb,
            now_ms=t_now,
            feed_health=feed_health,
        )

        current_state = "UNKNOWN"
        if latest_hb is not None:
            if is_stalled:
                current_state = "STALLED"
            else:
                base_state = getattr(latest_hb, "state", "RUNNING")
                is_closed, _ = is_xauusdt_market_closed(t_now)
                if is_closed:
                    current_state = "WAITING_MARKET"
                elif feed_health.get("freshness_state") in ("DISCONNECTED", "CONNECTED_STALE"):
                    current_state = "DEGRADED"
                else:
                    current_state = base_state

        last_eval_ms = getattr(latest_hb, "last_successful_evaluation_at_ms", None) if latest_hb else None
        last_eval_age_sec = round((t_now - last_eval_ms) / 1000.0, 1) if last_eval_ms else None

        last_hb_ms = getattr(latest_hb, "last_process_heartbeat_at_ms", None) if latest_hb else None
        last_hb_age_sec = round((t_now - last_hb_ms) / 1000.0, 1) if last_hb_ms else None

        return {
            "engine_instance_id": getattr(latest_hb, "engine_instance_id", self._current_instance_id),
            "symbol": symbol,
            "run_id": getattr(latest_hb, "run_id", None),
            "state": current_state,
            "is_stalled": is_stalled,
            "stalled_reason": stall_reason if is_stalled else None,
            "last_process_heartbeat_at_ms": last_hb_ms,
            "last_heartbeat_age_sec": last_hb_age_sec,
            "last_evaluation_started_at_ms": getattr(latest_hb, "last_evaluation_started_at_ms", None),
            "last_evaluation_completed_at_ms": getattr(latest_hb, "last_evaluation_completed_at_ms", None),
            "last_successful_evaluation_at_ms": last_eval_ms,
            "last_evaluation_age_sec": last_eval_age_sec,
            "last_cycle_id": getattr(latest_hb, "last_cycle_id", None),
            "cycles_count": getattr(latest_hb, "cycles_count", 0) or 0,
            "failed_cycles_count": getattr(latest_hb, "failed_cycles_count", 0) or 0,
            "cadence_sec": getattr(latest_hb, "cadence_sec", ENGINE_CADENCE_DEFAULT_SEC),
            "now_ms": t_now,
        }

    # -----------------------------------------------------------------------
    # 2. Market Feed & Candle Freshness (Phase C - OBS-01 & OBS-02)
    # -----------------------------------------------------------------------
    def build_market_feed_health_snapshot(
        self,
        *,
        symbol: str = "XAUUSDT",
        now_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Examines Bitget WebSocket adapter, latest quote, and multi-timeframe candles.
        Distinguishes connection state from quote freshness.
        Implements OBS-01 canonical quote authority and OBS-02 closed-candle verification.
        """
        t_now = int(now_ms if now_ms is not None else int(time.time() * 1000))
        from services.bitget_ws_service import bitget_ws_service

        transport_state = getattr(bitget_ws_service, "transport_state", "DISCONNECTED")
        feed_source = getattr(bitget_ws_service, "feed_source", "WS")

        # Resolve quote authority (OBS-01 & FIX-07)
        raw_quote = getattr(bitget_ws_service, "latest_quote", None)
        quote_source = "WS"
        if raw_quote is None or not getattr(raw_quote, "is_valid", True):
            raw_quote = getattr(collector_service, "latest_ticker", None)
            quote_source = "REST_FALLBACK" if raw_quote is not None else "NONE"

        quote_res = resolve_canonical_quote_timestamps(raw_quote, now_ms=t_now)

        quote_ts = quote_res["exchange_ts_ms"] or quote_res["received_at_ms"]
        bid = quote_res["bid"]
        ask = quote_res["ask"]
        exchange_age_sec = round(quote_res["exchange_age_ms"] / 1000.0, 2) if quote_res["exchange_age_ms"] is not None else None
        receive_age_sec = round(quote_res["receive_age_ms"] / 1000.0, 2) if quote_res["receive_age_ms"] is not None else None
        quote_age_sec = exchange_age_sec if exchange_age_sec is not None else receive_age_sec

        # Quote validity inspection (FIX-07 & PART 081-085)
        is_quote_structurally_valid = True
        quote_validation_reasons = []

        if raw_quote is not None:
            if not getattr(raw_quote, "is_valid", True):
                is_quote_structurally_valid = False
                quote_validation_reasons.append("QUOTE_NOT_VALID")
            if getattr(raw_quote, "status", "OK") in ("INVALID", "MALFORMED"):
                is_quote_structurally_valid = False
                quote_validation_reasons.append(f"STATUS_{getattr(raw_quote, 'status')}")

        if bid is None or ask is None:
            is_quote_structurally_valid = False
            quote_validation_reasons.append("BID_OR_ASK_NONE")
        else:
            try:
                bid_f = float(bid)
                ask_f = float(ask)
                if not (math.isfinite(bid_f) and math.isfinite(ask_f)):
                    is_quote_structurally_valid = False
                    quote_validation_reasons.append("NON_FINITE_BID_OR_ASK")
                elif bid_f <= 0:
                    is_quote_structurally_valid = False
                    quote_validation_reasons.append("NON_POSITIVE_BID")
                elif ask_f < bid_f:
                    is_quote_structurally_valid = False
                    quote_validation_reasons.append("INVERTED_SPREAD")
            except (ValueError, TypeError):
                is_quote_structurally_valid = False
                quote_validation_reasons.append("INVALID_PRICE_FORMAT")

        # Freshness determination
        freshness_state = "UNKNOWN"
        if transport_state == "CONNECTING":
            freshness_state = "RECONNECTING"
        elif transport_state == "DISCONNECTED" or raw_quote is None:
            freshness_state = "DISCONNECTED"
        elif not is_quote_structurally_valid:
            freshness_state = "INVALID_QUOTE"
        elif transport_state in ("CONNECTED", "DEGRADED"):
            if quote_age_sec is not None and quote_age_sec <= QUOTE_FRESHNESS_THRESHOLD_SEC:
                freshness_state = "CONNECTED_FRESH"
            elif quote_age_sec is not None:
                freshness_state = "CONNECTED_STALE"
            else:
                freshness_state = "CONNECTED_STALE"
        else:
            freshness_state = "DISCONNECTED"

        # Multi-timeframe closed candle health (OBS-02 & FIX-14)
        timeframe_health = {}
        data_gaps_detected = False

        for tf in ["1M", "5M", "15M", "1H", "4H", "D"]:
            candles = candle_cache_service._cache.get((symbol, tf), [])
            expected_interval_ms = TF_MS.get(tf, 60_000)
            max_thresh_sec = FRESHNESS_THRESHOLDS_SEC.get(tf, 300)

            latest_closed_ts = None
            tf_age_sec = None

            if candles:
                # Sort candles chronologically by timestamp ascending (FIX-14)
                sorted_candles = sorted(
                    candles,
                    key=lambda c: getattr(c, "timestamp", 0) if hasattr(c, "timestamp") else (c.get("timestamp") or c.get("time") or 0)
                )
                # Find the most recent strictly closed candle
                for c in reversed(sorted_candles):
                    c_ts = getattr(c, "timestamp", 0) if hasattr(c, "timestamp") else (c.get("timestamp") or c.get("time") or 0)
                    is_explicitly_closed = getattr(c, "is_closed", False) if hasattr(c, "is_closed") else c.get("is_closed", False)

                    # Ensure closed candle boundary does not exceed current time (FIX-14)
                    close_boundary = c_ts + expected_interval_ms
                    if close_boundary <= t_now + 5000 and (is_explicitly_closed or close_boundary <= t_now):
                        latest_closed_ts = close_boundary
                        tf_age_sec = round(max(0, t_now - latest_closed_ts) / 1000.0, 1)
                        break

                # Gap check on the last 5 candles if available
                if len(sorted_candles) >= 2:
                    for i in range(len(sorted_candles) - 1, max(0, len(sorted_candles) - 5), -1):
                        prev_c = sorted_candles[i - 1]
                        curr_c = sorted_candles[i]
                        prev_ts = getattr(prev_c, "timestamp", 0) if hasattr(prev_c, "timestamp") else (prev_c.get("timestamp") or 0)
                        curr_ts = getattr(curr_c, "timestamp", 0) if hasattr(curr_c, "timestamp") else (curr_c.get("timestamp") or 0)
                        if curr_ts - prev_ts > expected_interval_ms:
                            # Check if gap is due to market closure
                            mid_gap = prev_ts + expected_interval_ms
                            if not is_xauusdt_market_closed(mid_gap)[0]:
                                data_gaps_detected = True
                                break

            is_tf_stale = (tf_age_sec is None) or (tf_age_sec > max_thresh_sec)

            timeframe_health[tf] = {
                "latest_closed_candle_ts": latest_closed_ts,
                "candle_age_sec": tf_age_sec,
                "expected_interval_ms": expected_interval_ms,
                "is_stale": is_tf_stale,
                "freshness_state": "STALE" if is_tf_stale else "FRESH",
            }

        is_market_closed, market_close_reason = is_xauusdt_market_closed(t_now)
        is_weekend = is_market_closed and market_close_reason == "WEEKEND_BREAK"
        is_maintenance = is_market_closed and market_close_reason == "DAILY_MAINTENANCE"

        can_calc_mid = (
            is_quote_structurally_valid
            and bid is not None
            and ask is not None
            and ask >= bid
        )

        return {
            "symbol": symbol,
            "transport_state": transport_state,
            "feed_source": feed_source,
            "quote_source": quote_source,
            "is_quote_valid": is_quote_structurally_valid,
            "quote_validation_reasons": quote_validation_reasons,
            "freshness_state": freshness_state,
            "is_fresh": freshness_state == "CONNECTED_FRESH",
            "is_weekend_break": is_weekend,
            "is_market_closed": is_market_closed,
            "market_close_reason": market_close_reason,
            "last_quote_at_ms": quote_ts,
            "exchange_ts_ms": quote_res["exchange_ts_ms"],
            "received_at_ms": quote_res["received_at_ms"],
            "exchange_age_sec": exchange_age_sec,
            "receive_age_sec": receive_age_sec,
            "quote_age_sec": quote_age_sec,
            "transport_delay_ms": quote_res["transport_delay_ms"],
            "timestamp_status": quote_res["status"],
            "timestamp_reason_codes": quote_res["reason_codes"],
            "bid": bid,
            "ask": ask,
            "mid": round((bid + ask) / 2.0, 2) if can_calc_mid else None,
            "timeframe_health": timeframe_health,
            "has_gap": data_gaps_detected or getattr(candle_cache_service, "has_candle_gap", False),
            "reconnect_count": getattr(bitget_ws_service, "reconnect_count", 0),
            "last_success_time": getattr(bitget_ws_service, "last_success_time", 0),
            "now_ms": t_now,
        }

    # -----------------------------------------------------------------------
    # 3. Evaluation Cycle Telemetry (Phase D)
    # -----------------------------------------------------------------------
    def record_evaluation_cycle(
        self,
        db: Optional[Session],
        *,
        cycle_id: str,
        engine_instance_id: Optional[str] = None,
        started_at_ms: int,
        completed_at_ms: int,
        status: str,  # STARTED, COMPLETED, SKIPPED, FAILED, ABORTED
        symbol: str = "XAUUSDT",
        run_id: Optional[str] = None,
        duration_ms: float = 0.0,
        opportunities_detected: int = 0,
        opportunities_qualified: int = 0,
        opportunities_blocked: int = 0,
        armed_order_id: Optional[str] = None,
        decision_status: str = "NO_SETUP",  # NO_SETUP, BLOCKED, ARMED, WAITING, SKIPPED, FAILED
        skip_reason: Optional[str] = None,
        failure_reason: Optional[str] = None,
        models_evaluated: Optional[List[str]] = None,
        market_regime: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Records single cycle evaluation metrics into in-memory ring buffer,
        persists into EngineEvaluationCycleRecord (FIX-13), and updates engine heartbeat (FIX-01).
        """
        inst_id = engine_instance_id or self._current_instance_id
        entry = {
            "cycle_id": cycle_id,
            "engine_instance_id": inst_id,
            "run_id": run_id,
            "symbol": symbol,
            "started_at_ms": started_at_ms,
            "completed_at_ms": completed_at_ms,
            "status": status,
            "duration_ms": max(0.0, round(duration_ms, 2)),
            "opportunities_detected": opportunities_detected,
            "opportunities_qualified": opportunities_qualified,
            "opportunities_blocked": opportunities_blocked,
            "armed_order_id": armed_order_id,
            "decision_status": decision_status,
            "skip_reason": skip_reason,
            "failure_reason": failure_reason,
            "models_evaluated": models_evaluated or [],
            "market_regime": market_regime or "UNKNOWN",
        }

        # Idempotency check: if cycle already exists in memory or DB, avoid duplicate accounting (FIX-13)
        with self._lock:
            existing = [c for c in self._recent_cycles_buffer if c.get("cycle_id") == cycle_id]
            if existing and existing[0].get("status") in ("COMPLETED", status):
                res = dict(existing[0])
                res["deduplicated"] = True
                return res

            self._recent_cycles_buffer.append(entry)
            if len(self._recent_cycles_buffer) > self._max_recent_cycles:
                self._recent_cycles_buffer.pop(0)
            self._latest_cycle_diagnostic = entry

        # Persist into EngineEvaluationCycleRecord (FIX-13)
        close_session = False
        target_db = db
        if target_db is None:
            try:
                from database import SessionLocal
                target_db = SessionLocal()
                close_session = True
            except Exception as e:
                logger.warning(f"Could not open fallback SessionLocal for cycle persistence: {e}")

        if target_db is not None:
            try:
                existing_row = target_db.query(models.EngineEvaluationCycleRecord).filter(
                    models.EngineEvaluationCycleRecord.cycle_id == cycle_id
                ).first()
                if existing_row is not None:
                    res = dict(entry)
                    res["deduplicated"] = True
                    return res
                else:
                    cycle_row = models.EngineEvaluationCycleRecord(
                        cycle_id=cycle_id,
                        engine_instance_id=inst_id,
                        run_id=run_id,
                        symbol=symbol,
                        status=status,
                        started_at_ms=started_at_ms,
                        completed_at_ms=completed_at_ms,
                        duration_ms=max(0.0, round(duration_ms, 2)),
                        decision_status=decision_status,
                        skip_reason=skip_reason,
                        failure_reason=failure_reason,
                        opportunities_detected=opportunities_detected,
                        opportunities_qualified=opportunities_qualified,
                        opportunities_blocked=opportunities_blocked,
                        armed_order_id=armed_order_id,
                        metadata_json=json.dumps({
                            "models_evaluated": models_evaluated or [],
                            "market_regime": market_regime or "UNKNOWN",
                        }, ensure_ascii=False),
                        created_at_ms=completed_at_ms or int(time.time() * 1000),
                    )
                    target_db.add(cycle_row)
                    if close_session:
                        target_db.commit()
                    else:
                        target_db.flush()
            except Exception as e:
                if close_session:
                    target_db.rollback()
                logger.warning(f"Error persisting EngineEvaluationCycleRecord: {e}")
            finally:
                if close_session:
                    target_db.close()

        # Update engine heartbeat (respects borrowed session ownership)
        self.record_engine_heartbeat(
            db,
            engine_instance_id=inst_id,
            run_id=run_id,
            symbol=symbol,
            state="RUNNING" if status == "COMPLETED" else ("DEGRADED" if status == "FAILED" else "IDLE"),
            heartbeat_at_ms=completed_at_ms,
            last_cycle_id=cycle_id,
            evaluation_started_at_ms=started_at_ms,
            evaluation_completed_at_ms=completed_at_ms,
            successful_cycle=(status == "COMPLETED"),
            cycle_failed=(status == "FAILED"),
        )

        return entry

    def get_recent_cycles(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            return list(reversed(self._recent_cycles_buffer[-limit:]))

    def get_latest_cycle_diagnostic(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._latest_cycle_diagnostic) if self._latest_cycle_diagnostic else None

    def get_latest_persisted_cycle(
        self,
        db: Optional[Session],
        symbol: str = "XAUUSDT",
        run_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Reconstructs latest cycle from database after restart (FIX-13 & PART 036)."""
        diag = self.get_latest_cycle_diagnostic()
        if diag and diag.get("symbol") == symbol and (
            run_id is None or diag.get("run_id") == run_id
        ):
            return diag
        if db is None:
            return None
        try:
            q = db.query(models.EngineEvaluationCycleRecord).filter(
                models.EngineEvaluationCycleRecord.symbol == symbol
            )
            if run_id:
                q = q.filter(models.EngineEvaluationCycleRecord.run_id == run_id)
            row = q.order_by(models.EngineEvaluationCycleRecord.completed_at_ms.desc()).first()
            if row:
                return {
                    "cycle_id": row.cycle_id,
                    "engine_instance_id": row.engine_instance_id,
                    "run_id": row.run_id,
                    "symbol": row.symbol,
                    "started_at_ms": row.started_at_ms,
                    "completed_at_ms": row.completed_at_ms,
                    "status": row.status,
                    "duration_ms": row.duration_ms,
                    "opportunities_detected": row.opportunities_detected,
                    "opportunities_qualified": row.opportunities_qualified,
                    "opportunities_blocked": row.opportunities_blocked,
                    "armed_order_id": row.armed_order_id,
                    "decision_status": row.decision_status,
                    "skip_reason": row.skip_reason,
                    "failure_reason": row.failure_reason,
                }
        except Exception as e:
            logger.warning(f"Error querying persisted cycle: {e}")
        return None


    # -----------------------------------------------------------------------
    # 4. Decision Intelligence & Opportunity Timeline (Phase E)
    # -----------------------------------------------------------------------
    def record_decision_event(
        self,
        db: Optional[Session],
        *,
        opportunity_id: str,
        model_id: str,
        direction: str,
        stage: str,
        symbol: str = "XAUUSDT",
        event_id: Optional[str] = None,
        run_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
        reason_codes: Optional[Sequence[str]] = None,
        evidence_ids: Optional[Sequence[str]] = None,
        waiting_conditions: Optional[Sequence[Dict[str, Any]]] = None,
        payload: Optional[Dict[str, Any]] = None,
        occurred_at_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Records an immutable decision event into opportunity_decision_events table.
        Applies deduplication: if last event for this opportunity had the same stage and
        primary reason code, avoids spamming identical records.
        """
        t_now = int(occurred_at_ms if occurred_at_ms is not None else int(time.time() * 1000))
        ev_id = event_id or f"ev-{uuid.uuid4().hex[:12]}"
        reasons_list = list(reason_codes or [])

        primary_reason_info = get_primary_decision_reason(reasons_list, stage, payload)
        primary_code = primary_reason_info["primary_reason_code"]
        reason_cat = primary_reason_info["category"]

        if db is not None:
            try:
                # Check previous event for deduplication
                prev_ev = db.query(models.OpportunityDecisionEvent).filter(
                    models.OpportunityDecisionEvent.opportunity_id == opportunity_id
                ).order_by(models.OpportunityDecisionEvent.occurred_at_ms.desc()).first()

                if prev_ev is not None:
                    if prev_ev.stage == stage and prev_ev.primary_reason_code == primary_code:
                        # Identical state within 60s -> skip duplicate
                        if (t_now - prev_ev.occurred_at_ms) < 60_000:
                            return {
                                "event_id": prev_ev.event_id,
                                "deduplicated": True,
                                "stage": stage,
                                "primary_reason_code": primary_code,
                            }

                row = models.OpportunityDecisionEvent(
                    event_id=ev_id,
                    event_version=1,
                    run_id=run_id,
                    cycle_id=cycle_id,
                    opportunity_id=opportunity_id,
                    symbol=symbol,
                    model_id=model_id,
                    direction=direction,
                    stage=stage,
                    primary_reason_code=primary_code,
                    reason_category=reason_cat,
                    reason_codes_json=json.dumps(reasons_list, ensure_ascii=False),
                    evidence_ids_json=json.dumps(list(evidence_ids or []), ensure_ascii=False),
                    waiting_conditions_json=json.dumps(list(waiting_conditions or []), ensure_ascii=False),
                    payload_json=json.dumps(payload or {}, ensure_ascii=False),
                    occurred_at_ms=t_now,
                    created_at_ms=t_now,
                )
                db.add(row)
                db.flush()
            except Exception as e:
                logger.warning(f"Error persisting OpportunityDecisionEvent: {e}")

        return {
            "event_id": ev_id,
            "opportunity_id": opportunity_id,
            "model_id": model_id,
            "direction": direction,
            "stage": stage,
            "primary_reason_code": primary_code,
            "reason_category": reason_cat,
            "occurred_at_ms": t_now,
        }

    def list_recent_decisions(
        self,
        db: Session,
        *,
        run_id: Optional[str] = None,
        symbol: str = "XAUUSDT",
        limit: int = 20,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetches paginated decision events with stable ordering (occurred_at_ms DESC, id DESC).
        Validates cursor strictly (FIX-09) and enforces symbol provenance.
        """
        db.autoflush = False
        query = db.query(models.OpportunityDecisionEvent)

        if run_id:
            query = query.filter(models.OpportunityDecisionEvent.run_id == run_id)

        if symbol:
            query = query.filter(
                (models.OpportunityDecisionEvent.symbol == symbol) |
                (models.OpportunityDecisionEvent.symbol == None)
            )

        if cursor:
            ts_part, id_part = decode_observation_cursor(cursor, expected_run_id=run_id, expected_symbol=symbol)
            query = query.filter(
                (models.OpportunityDecisionEvent.occurred_at_ms < ts_part) |
                (
                    (models.OpportunityDecisionEvent.occurred_at_ms == ts_part) &
                    (models.OpportunityDecisionEvent.id < id_part)
                )
            )

        rows = query.order_by(
            models.OpportunityDecisionEvent.occurred_at_ms.desc(),
            models.OpportunityDecisionEvent.id.desc(),
        ).limit(limit + 1).all()

        has_more = len(rows) > limit
        result_rows = rows[:limit]

        next_cursor = None
        if has_more and result_rows:
            last = result_rows[-1]
            raw_c = f"{last.occurred_at_ms}_{last.id}_{run_id or ''}_{symbol or ''}"
            next_cursor = base64.b64encode(raw_c.encode("utf-8")).decode("utf-8")

        items = []
        for r in result_rows:
            try:
                payload = json.loads(r.payload_json or "{}")
            except Exception:
                payload = {}
            try:
                reasons = json.loads(r.reason_codes_json or "[]")
            except Exception:
                reasons = []

            primary_info = get_primary_decision_reason(reasons, r.stage, payload)

            items.append({
                "event_id": r.event_id,
                "opportunity_id": r.opportunity_id,
                "model_id": r.model_id,
                "direction": r.direction,
                "stage": r.stage,
                "stage_title_vi": get_stage_title_vi(r.stage),
                "primary_reason_code": r.primary_reason_code or primary_info["primary_reason_code"],
                "reason_category": r.reason_category or primary_info["category"],
                "reason_category_title_vi": REASON_CATEGORIES.get(r.reason_category or primary_info["category"], "Khác"),
                "short_message_vi": primary_info["short_message_vi"],
                "technical_message": primary_info["technical_message"],
                "occurred_at_ms": r.occurred_at_ms,
                "order_id": payload.get("order_id") or payload.get("armed_order_id"),
                "setup_id": payload.get("setup_id"),
            })

        return {
            "items": items,
            "limit": limit,
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    # -----------------------------------------------------------------------
    # 5. Operational Incidents & Reliability (Phase K)
    # -----------------------------------------------------------------------
    def record_engine_incident(
        self,
        db: Optional[Session],
        *,
        component: str,
        severity: str,  # INFO, WARNING, CRITICAL, RECOVERED
        message: str,
        reason_code: Optional[str] = None,
        engine_instance_id: Optional[str] = None,
        run_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
        opportunity_id: Optional[str] = None,
        order_id: Optional[str] = None,
        incident_id: Optional[str] = None,
        now_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Records or deduplicates an operational incident across backend restarts.
        """
        t_now = int(now_ms if now_ms is not None else int(time.time() * 1000))
        inc_id = incident_id or f"inc-{component.lower()}-{uuid.uuid4().hex[:8]}"

        inc_data = {
            "id": inc_id,
            "component": component,
            "severity": severity,
            "message": message,
            "reason_code": reason_code,
            "engine_instance_id": engine_instance_id or self._current_instance_id,
            "run_id": run_id,
            "cycle_id": cycle_id,
            "opportunity_id": opportunity_id,
            "order_id": order_id,
            "status": "ACTIVE",
            "created_at_ms": t_now,
            "recovered_at_ms": None,
        }

        with self._lock:
            self._active_incidents[inc_id] = inc_data

        close_session = False
        target_db = db
        if target_db is None:
            try:
                from database import SessionLocal
                target_db = SessionLocal()
                close_session = True
            except Exception as e:
                logger.warning(f"Could not open fallback SessionLocal for incident: {e}")

        if target_db is not None:
            try:
                row = models.EngineIncidentRecord(
                    id=inc_id,
                    severity=severity,
                    component=component,
                    engine_instance_id=engine_instance_id or self._current_instance_id,
                    run_id=run_id,
                    cycle_id=cycle_id,
                    opportunity_id=opportunity_id,
                    order_id=order_id,
                    reason_code=reason_code,
                    message=message,
                    status="ACTIVE",
                    created_at_ms=t_now,
                )
                target_db.add(row)
                if close_session:
                    target_db.commit()
                else:
                    target_db.flush()
            except Exception as e:
                if close_session:
                    target_db.rollback()
                logger.warning(f"Error persisting EngineIncidentRecord: {e}")
            finally:
                if close_session:
                    target_db.close()

        return inc_data

    # Alias for API compatibility
    raise_engine_incident = record_engine_incident

    def persist_engine_incident(self, **kwargs) -> Dict[str, Any]:
        """Durable isolated incident writer using owned session (FIX-01)."""
        return self.record_engine_incident(db=None, **kwargs)

    def resolve_engine_incident(
        self,
        db: Optional[Session],
        incident_id: str,
        now_ms: Optional[int] = None,
    ) -> bool:
        """Marks an active incident as RECOVERED without committing borrowed session (FIX-01)."""
        t_now = int(now_ms if now_ms is not None else int(time.time() * 1000))
        with self._lock:
            if incident_id in self._active_incidents:
                self._active_incidents[incident_id]["status"] = "RECOVERED"
                self._active_incidents[incident_id]["recovered_at_ms"] = t_now

        close_session = False
        target_db = db
        if target_db is None:
            try:
                from database import SessionLocal
                target_db = SessionLocal()
                close_session = True
            except Exception as e:
                logger.warning(f"Could not open fallback SessionLocal for incident resolution: {e}")

        if target_db is not None:
            try:
                row = target_db.query(models.EngineIncidentRecord).filter(
                    models.EngineIncidentRecord.id == incident_id
                ).first()
                if row:
                    row.status = "RECOVERED"
                    row.recovered_at_ms = t_now
                    if close_session:
                        target_db.commit()
                    else:
                        target_db.flush()
                    return True
            except Exception as e:
                if close_session:
                    target_db.rollback()
                logger.warning(f"Error resolving EngineIncidentRecord: {e}")
            finally:
                if close_session:
                    target_db.close()
        return False

    def resolve_active_incidents(
        self,
        db: Optional[Session] = None,
        *,
        component: Optional[str] = None,
        reason_code: Optional[str] = None,
        run_id: Optional[str] = None,
        now_ms: Optional[int] = None,
    ) -> int:
        """
        Auto-recovers active incidents matching component, reason_code, or run_id (OBS-12 & FIX-01).
        Respects transaction boundaries: flushes borrowed sessions, commits owned sessions.
        Returns the number of resolved incidents.
        """
        t_now = int(now_ms if now_ms is not None else int(time.time() * 1000))
        resolved_count = 0
        resolved_ids = []

        with self._lock:
            for inc_id, inc in list(self._active_incidents.items()):
                if inc.get("status") == "ACTIVE":
                    match = True
                    if component and inc.get("component") != component:
                        match = False
                    if reason_code and inc.get("reason_code") != reason_code:
                        match = False
                    if run_id and inc.get("run_id") != run_id:
                        match = False
                    if match:
                        inc["status"] = "RECOVERED"
                        inc["recovered_at_ms"] = t_now
                        resolved_ids.append(inc_id)
                        resolved_count += 1

        close_session = False
        target_db = db
        if target_db is None:
            try:
                from database import SessionLocal
                target_db = SessionLocal()
                close_session = True
            except Exception as e:
                logger.warning(f"Could not open fallback SessionLocal for bulk incident resolution: {e}")

        if target_db is not None:
            try:
                q = target_db.query(models.EngineIncidentRecord).filter(
                    models.EngineIncidentRecord.status == "ACTIVE"
                )
                if component:
                    q = q.filter(models.EngineIncidentRecord.component == component)
                if reason_code:
                    q = q.filter(models.EngineIncidentRecord.reason_code == reason_code)
                if run_id:
                    q = q.filter(models.EngineIncidentRecord.run_id == run_id)
                rows = q.all()
                for r in rows:
                    r.status = "RECOVERED"
                    r.recovered_at_ms = t_now
                    if r.id not in resolved_ids:
                        resolved_count += 1
                if close_session:
                    target_db.commit()
                else:
                    target_db.flush()
            except Exception as e:
                if close_session:
                    target_db.rollback()
                logger.warning(f"Error persisting bulk incident resolution: {e}")
            finally:
                if close_session:
                    target_db.close()

        return resolved_count

    def list_active_engine_incidents(
        self,
        db: Optional[Session],
        *,
        run_id: Optional[str] = None,
        symbol: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Queries authoritative active engine incidents from persistent DB and memory (FIX-10 & PART 106-107).
        """
        active_dict: Dict[str, Dict[str, Any]] = {}
        if db is not None:
            try:
                q = db.query(models.EngineIncidentRecord).filter(
                    models.EngineIncidentRecord.status == "ACTIVE"
                )
                if run_id:
                    q = q.filter(
                        (models.EngineIncidentRecord.run_id == run_id) |
                        (models.EngineIncidentRecord.run_id == None)
                    )
                for r in q.all():
                    active_dict[r.id] = {
                        "id": r.id,
                        "component": r.component,
                        "severity": r.severity,
                        "message": r.message,
                        "reason_code": r.reason_code,
                        "engine_instance_id": r.engine_instance_id,
                        "run_id": r.run_id,
                        "cycle_id": r.cycle_id,
                        "opportunity_id": r.opportunity_id,
                        "order_id": r.order_id,
                        "status": r.status,
                        "created_at_ms": r.created_at_ms,
                        "recovered_at_ms": r.recovered_at_ms,
                    }
            except Exception as e:
                logger.warning(f"Error querying active incidents in list_active_engine_incidents: {e}")

        with self._lock:
            for inc_id, inc in self._active_incidents.items():
                if inc.get("status") == "ACTIVE":
                    if run_id and inc.get("run_id") and inc.get("run_id") != run_id:
                        continue
                    active_dict[inc_id] = inc

        return list(active_dict.values())

    # -----------------------------------------------------------------------
    # 6. Truthful Coverage & Uptime Denominator (Phase F - OBS-06 & FIX-02)
    # -----------------------------------------------------------------------
    def calculate_observation_coverage(
        self,
        db: Optional[Session],
        *,
        run_id: Optional[str] = None,
        start_ms: int,
        end_ms: int,
    ) -> Dict[str, Any]:
        """
        Calculate GOOD coverage only inside eligible market-open intervals.
        Overlaps are normalized; DOWN outranks DEGRADED, which outranks GOOD.
        Missing observation evidence remains UNVERIFIED, never estimated from cycle counts.
        """
        effective_start, effective_end = int(start_ms), int(end_ms)
        run = None
        if run_id and db is not None:
            try:
                run = db.query(models.PaperResearchRunRecord).filter(
                    models.PaperResearchRunRecord.run_id == run_id
                ).first()
                if run is not None:
                    run_start = getattr(run, "started_at_ms", None) or getattr(run, "created_at_ms", None)
                    run_end = getattr(run, "ended_at_ms", None)
                    if run_start is not None:
                        effective_start = max(effective_start, int(run_start))
                    if run_end is not None:
                        effective_end = min(effective_end, int(run_end))
            except Exception as exc:
                logger.warning("Cannot verify observation run boundary for %s: %s", run_id, exc)
                return {
                    "coverage_pct": None, "coverage_state": "UNVERIFIABLE",
                    "expected_market_open_ms": None, "observed_intervals_ms": None,
                    "reason_code": "RUN_BOUNDARY_LOOKUP_FAILED",
                }

        if effective_end <= effective_start:
            return {
                "total_span_hours": 0.0,
                "market_open_hours": 0.0,
                "expected_market_open_ms": 0,
                "observed_intervals_ms": 0,
                "weekend_closed_hours": 0.0,
                "observed_healthy_hours": 0.0,
                "known_downtime_hours": 0.0,
                "degraded_hours": 0.0,
                "down_hours": 0.0,
                "unverified_hours": 0.0,
                "coverage_pct": None,
                "coverage_state": "NO_ELIGIBLE_WINDOW",
                "evidence_type": "LIVE_PAPER_FORWARD_OBSERVATION",
            }

        total_span_ms = effective_end - effective_start
        market_open = build_market_open_interval_ranges(effective_start, effective_end)
        market_open_ms = merge_time_intervals(market_open)
        observed_by_quality: Dict[str, List[Tuple[int, int]]] = {
            "GOOD": [], "DEGRADED": [], "DOWN": [],
        }
        if db is not None:
            try:
                query = db.query(models.PaperObservationInterval).filter(
                    models.PaperObservationInterval.end_ms > effective_start,
                    models.PaperObservationInterval.start_ms < effective_end,
                )
                if run_id:
                    query = query.filter(models.PaperObservationInterval.run_id == run_id)
                if run is not None and getattr(run, "symbol", None):
                    query = query.filter(models.PaperObservationInterval.symbol == run.symbol)
                if run is not None and getattr(run, "scope", None):
                    query = query.filter(models.PaperObservationInterval.scope == run.scope)
                for interval in query.all():
                    clipped = clip_interval(
                        interval.start_ms, interval.end_ms, effective_start, effective_end
                    )
                    if clipped is None:
                        continue
                    quality = str(getattr(interval, "data_quality", "UNVERIFIED") or "UNVERIFIED").upper()
                    if quality in observed_by_quality:
                        observed_by_quality[quality].append(clipped)
                    elif quality not in ("UNKNOWN", "UNVERIFIED"):
                        # Non-GOOD degradation codes cannot be counted as healthy.
                        observed_by_quality["DEGRADED"].append(clipped)
            except Exception as exc:
                logger.warning("Observation interval query failed: %s", exc)
                return {
                    "coverage_pct": None, "coverage_state": "UNVERIFIABLE",
                    "expected_market_open_ms": market_open_ms, "observed_intervals_ms": None,
                    "reason_code": "OBSERVATION_INTERVAL_QUERY_FAILED",
                }

        down = intersect_interval_ranges(observed_by_quality["DOWN"], market_open)
        degraded = subtract_interval_ranges(
            intersect_interval_ranges(observed_by_quality["DEGRADED"], market_open), down
        )
        healthy = subtract_interval_ranges(
            subtract_interval_ranges(
                intersect_interval_ranges(observed_by_quality["GOOD"], market_open), down
            ), degraded
        )

        down_ms = merge_time_intervals(down)
        degraded_ms = merge_time_intervals(degraded)
        good_ms = merge_time_intervals(healthy)
        unverified_ms = max(0, market_open_ms - good_ms - degraded_ms - down_ms)

        if market_open_ms == 0:
            coverage_pct = None
            coverage_state = "MARKET_CLOSED"
        else:
            coverage_pct = round((good_ms / market_open_ms) * 100.0, 1)
            coverage_state = "FULL" if coverage_pct >= 95.0 else (
                "PARTIAL" if coverage_pct >= 50.0 else "LOW"
            )

        return {
            "total_span_hours": round(total_span_ms / 3_600_000.0, 2),
            "market_open_hours": round(market_open_ms / 3_600_000.0, 2),
            "expected_market_open_ms": market_open_ms,
            "observed_intervals_ms": good_ms,
            "weekend_closed_hours": round((total_span_ms - market_open_ms) / 3_600_000.0, 2),
            "observed_healthy_hours": round(good_ms / 3_600_000.0, 2),
            "known_downtime_hours": round((down_ms + degraded_ms) / 3_600_000.0, 2),
            "degraded_hours": round(degraded_ms / 3_600_000.0, 2),
            "down_hours": round(down_ms / 3_600_000.0, 2),
            "unverified_hours": round(unverified_ms / 3_600_000.0, 2),
            "coverage_pct": coverage_pct,
            "coverage_state": coverage_state,
            "evidence_type": "LIVE_PAPER_FORWARD_OBSERVATION",
        }

    # -----------------------------------------------------------------------
    # 7. Aggregated Read-Only Observation Snapshot (Phase H & J)
    # -----------------------------------------------------------------------
    def build_observation_dashboard_snapshot(
        self,
        db: Session,
        *,
        symbol: str = "XAUUSDT",
        scope: str = "CG01",
        run_id: Optional[str] = None,
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
        now_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Builds complete Tier 1 + Tier 2 + Tier 3 Observation Snapshot.
        Strictly Read-Only (db.autoflush = False).
        Implements OBS-05 strict run and symbol isolation.
        """
        db.autoflush = False
        t_now = int(now_ms if now_ms is not None else int(time.time() * 1000))
        t_end = int(end_ms if end_ms is not None else t_now)
        t_start = int(start_ms if start_ms is not None else (t_end - 86400 * 1000))

        # 1. Resolve Active Run
        active_run = None
        target_run_id = run_id
        if not target_run_id:
            resolved_run = resolve_active_observation_run(db, scope=scope, symbol=symbol)
            if resolved_run:
                active_run = resolved_run
                target_run_id = resolved_run.run_id
        elif run_id:
            active_run = PaperResearchRunService.get_or_create_run(run_id, db=db, create_if_missing=False)

        # 2. Engine Health (Tier 1)
        engine_health = self.build_engine_health_snapshot(db, symbol=symbol, now_ms=t_now)

        # 3. Market Feed Health (Tier 1)
        feed_health = self.build_market_feed_health_snapshot(symbol=symbol, now_ms=t_now)

        # 4. Coverage Uptime (Tier 1 & 3)
        coverage_data = self.calculate_observation_coverage(
            db,
            run_id=target_run_id,
            start_ms=t_start,
            end_ms=t_end,
        )

        # 5. Opportunity Funnel Breakdown (Tier 2 - OBS-05 Isolated)
        from services.opportunity_funnel_service import OpportunityFunnelService
        funnel_summary = OpportunityFunnelService.summarize_opportunity_funnel(
            db,
            symbol=symbol,
            run_id=target_run_id,
            start_ms=t_start,
            end_ms=t_end,
        )

        # 6. Recent Decisions & Waiting Setups (Tier 2 - OBS-05 Isolated)
        recent_decisions = self.list_recent_decisions(db, run_id=target_run_id, symbol=symbol, limit=20)

        # Query waiting candidates - strictly latest stage per opportunity (FIX-08 & PART 062-064)
        subq = db.query(
            models.OpportunityDecisionEvent.opportunity_id,
            func.max(models.OpportunityDecisionEvent.id).label("max_id")
        ).filter(
            models.OpportunityDecisionEvent.opportunity_id != None,
            models.OpportunityDecisionEvent.opportunity_id != "",
        )
        if target_run_id:
            subq = subq.filter(models.OpportunityDecisionEvent.run_id == target_run_id)
        if symbol:
            subq = subq.filter(
                (models.OpportunityDecisionEvent.symbol == symbol) |
                (models.OpportunityDecisionEvent.symbol == None)
            )
        subq = subq.group_by(models.OpportunityDecisionEvent.opportunity_id).subquery()

        waiting_setups_rows = db.query(models.OpportunityDecisionEvent).join(
            subq,
            and_(
                models.OpportunityDecisionEvent.opportunity_id == subq.c.opportunity_id,
                models.OpportunityDecisionEvent.id == subq.c.max_id,
            )
        ).filter(
            models.OpportunityDecisionEvent.stage.in_(["WAITING", "WATCHING"])
        ).order_by(models.OpportunityDecisionEvent.occurred_at_ms.desc()).limit(10).all()

        waiting_conditions_list = []
        for w in waiting_setups_rows:
            try:
                p = json.loads(w.payload_json or "{}")
            except Exception:
                p = {}
            waiting_conditions_list.append({
                "opportunity_id": w.opportunity_id,
                "model_id": w.model_id,
                "direction": w.direction,
                "stage": w.stage,
                "stage_title_vi": get_stage_title_vi(w.stage),
                "occurred_at_ms": w.occurred_at_ms,
                "conditions": inspect_waiting_conditions(w.stage, p, now_ms=t_now),
            })

        # Top Blocked Categories
        q_blocked = db.query(
            models.OpportunityDecisionEvent.reason_category,
            func.count(models.OpportunityDecisionEvent.id).label("cnt")
        ).filter(
            models.OpportunityDecisionEvent.stage.in_(["BLOCKED", "GUARD_BLOCKED"]),
            models.OpportunityDecisionEvent.occurred_at_ms >= t_start,
            models.OpportunityDecisionEvent.occurred_at_ms <= t_end,
        )
        if target_run_id:
            q_blocked = q_blocked.filter(models.OpportunityDecisionEvent.run_id == target_run_id)
        blocked_events = q_blocked.group_by(models.OpportunityDecisionEvent.reason_category).all()

        blocked_categories_summary = [
            {
                "category": r[0] or "STRUCTURE",
                "category_title_vi": REASON_CATEGORIES.get(r[0] or "STRUCTURE", "Khác"),
                "count": r[1],
            }
            for r in blocked_events
        ]

        # 7. Truthful Financial Metrics (Tier 3 - Strict Run Isolation FIX-03 & PART 053-060)
        financial_summary = None
        if target_run_id:
            run_summary = build_cg01_paper_run_summary(target_run_id, db=db)
            if run_summary.get("status") != "NOT_FOUND":
                tr_dict = run_summary.get("trades") or {}
                fin_dict = run_summary.get("financial_metrics_post_fee") or {}
                net_pnl = fin_dict.get("net_realized_pnl_usd", run_summary.get("net_pnl_usd", 0.0))
                init_cap = fin_dict.get("initial_capital_usdt", run_summary.get("initial_capital_usdt", 1000.0))

                financial_summary = {
                    "closed_trades": tr_dict.get("total_closed_trades", run_summary.get("total_trades", 0)),
                    "open_trades": tr_dict.get("open_trades_count", 0),
                    "armed_orders": tr_dict.get("armed_orders_count", 0),
                    "wins": tr_dict.get("winning_trades", run_summary.get("wins", 0)),
                    "losses": tr_dict.get("losing_trades", run_summary.get("losses", 0)),
                    "breakevens": tr_dict.get("breakeven_trades", run_summary.get("breakevens", 0)),
                    "winrate_pct": tr_dict.get("winrate_pct"),  # None if closed_trades == 0 (Rule 070)
                    "net_pnl_usd": net_pnl,
                    "net_expectancy": fin_dict.get("net_expectancy_usd", 0.0),
                    "max_drawdown_usd": fin_dict.get("max_drawdown_usd", 0.0),
                    "max_drawdown_pct": fin_dict.get("max_drawdown_pct", 0.0),
                    "initial_capital_usdt": init_cap,
                    "current_equity_usdt": round(init_cap + net_pnl, 2),
                }
            else:
                financial_summary = {
                    "status": "NOT_FOUND",
                    "run_id": target_run_id,
                    "closed_trades": None,
                    "open_trades": None,
                    "armed_orders": None,
                    "wins": None,
                    "losses": None,
                    "breakevens": None,
                    "winrate_pct": None,
                    "net_pnl_usd": None,
                    "net_expectancy": None,
                    "max_drawdown_usd": None,
                    "max_drawdown_pct": None,
                    "initial_capital_usdt": None,
                    "current_equity_usdt": None,
                }

        # 8. Active Incidents (Tier 1 - Persisted DB + RAM cache - FIX-10 & PART 106-107)
        active_incidents_list = self.list_active_engine_incidents(
            db,
            run_id=target_run_id,
            symbol=symbol,
            scope=scope,
        )

        return {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "symbol": symbol,
            "scope": scope,
            "run_id": target_run_id,
            "window": {
                "start_ms": t_start,
                "end_ms": t_end,
                "now_ms": t_now,
            },
            "engine": engine_health,
            "feed": feed_health,
            "coverage": coverage_data,
            "funnel": funnel_summary,
            "decisions": recent_decisions,
            "waiting_setups": waiting_conditions_list,
            "blocked_categories": blocked_categories_summary,
            "top_blocked_categories": blocked_categories_summary,
            "financial": financial_summary,
            "incidents": active_incidents_list,
            "latest_cycle": self.get_latest_persisted_cycle(db, symbol=symbol, run_id=target_run_id),
        }

    def generate_run_report_markdown(
        self,
        db: Session,
        run_id: str,
    ) -> str:
        """
        Builds a comprehensive Markdown observation audit report for a specific run.
        """
        from services.paper_research_run_service import PaperResearchRunService, build_cg01_paper_run_summary

        db.autoflush = False
        run = PaperResearchRunService.get_or_create_run(run_id, db=db, create_if_missing=False)
        if not run:
            return f"# LỖI: Không tìm thấy Observation Run '{run_id}'\n"

        summary = build_cg01_paper_run_summary(run_id, db=db)
        t_now = int(time.time() * 1000)
        coverage = self.calculate_observation_coverage(
            db,
            run_id=run_id,
            start_ms=run.started_at_ms,
            end_ms=run.ended_at_ms or t_now,
        )

        closed_trades = summary.get("total_trades", 0)
        winrate_str = f"{summary.get('winrate_pct')}%" if summary.get("winrate_pct") is not None else "N/A (0 trades)"
        net_pnl = summary.get("net_pnl_usd", 0.0)

        return f"""# BÁO CÁO QUAN SÁT VẬN HÀNH BOT — RUN: {run_id}
**Scope:** {run.scope} | **Symbol:** {run.symbol} | **Mode:** {run.execution_mode}
**Bắt đầu:** {datetime.fromtimestamp(run.started_at_ms / 1000, tz=timezone.utc).isoformat()}
**Trạng thái:** {run.status}

---

## 1. Hiệu Suất Tài Chính Sau Phí (Committed Paper Ledger)
- **Tổng Lệnh Đã Đóng:** {closed_trades}
- **Tỷ Lệ Thắng (Winrate):** {winrate_str}
- **Realized Net PnL:** ${net_pnl:.2f} USD
- **Kỳ Vọng Ròng (Net Expectancy):** ${summary.get('net_expectancy', 0.0):.2f} USD / trade
- **Drawdown Lớn Nhất:** ${summary.get('max_drawdown_usd', 0.0):.2f} USD ({summary.get('max_drawdown_pct', 0.0):.2f}%)
- **Số Vốn Ban Đầu:** ${summary.get('initial_capital_usdt', 1000.0):.2f} USDT

## 2. Đo Lường Độ Bao Phủ Quan Sát (Truthful Uptime Coverage)
- **Thời Gian Đã Chạy:** {coverage.get('total_span_hours', 0.0)} giờ
- **Thời Gian Quan Sát Khỏe Mạnh:** {coverage.get('observed_healthy_hours', 0.0)} giờ
- **Độ Bao Phủ Dữ Liệu:** {coverage.get('coverage_pct', 0.0)}% ({coverage.get('coverage_state', 'UNKNOWN')})
- **Loại Bằng Chứng:** {coverage.get('evidence_type', 'LIVE_PAPER_FORWARD_OBSERVATION')}

---
*Báo cáo được khởi tạo tự động bởi AURUM DESK V17.6 Observability Engine.*
"""


# Singleton instance
engine_observability_service = EngineObservabilityService()
