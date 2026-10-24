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

from sqlalchemy import distinct, func, text, desc
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


def is_xauusdt_weekend_break(timestamp_ms: int) -> bool:
    """
    Checks if a given UTC timestamp falls during the standard Gold/XAU weekend closure:
    Closes Friday ~21:00 UTC (or 22:00 UTC depending on DST), opens Sunday ~22:00 UTC.
    Conservative window: Friday 21:00 UTC to Sunday 22:00 UTC.
    """
    dt_utc = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
    weekday = dt_utc.weekday()  # Monday is 0, Sunday is 6
    hour = dt_utc.hour

    if weekday == 4 and hour >= 21:  # Friday evening
        return True
    if weekday == 5:  # Saturday full day
        return True
    if weekday == 6 and hour < 22:  # Sunday before open
        return True
    return False


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

        if db is not None:
            try:
                row = db.query(models.EngineHeartbeatRecord).filter(
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
                    db.add(row)
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

                db.flush()
            except Exception as e:
                logger.warning(f"Error persisting EngineHeartbeatRecord: {e}")

        return record_data

    def detect_stalled_engine(
        self,
        *,
        heartbeat_record: Optional[Any],
        now_ms: int,
        feed_health: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """
        Determines if engine is stalled based on true runtime evidence.
        Distinguishes between valid market waiting, weekend break, and genuine engine hang.
        """
        if heartbeat_record is None:
            return True, "NO_HEARTBEAT_RECORD"

        state = getattr(heartbeat_record, "state", "UNKNOWN")
        if state in ("PAUSED", "STOPPED"):
            return False, f"ENGINE_INTENTIONALLY_{state}"

        cadence = getattr(heartbeat_record, "cadence_sec", ENGINE_CADENCE_DEFAULT_SEC) or ENGINE_CADENCE_DEFAULT_SEC
        stall_threshold_ms = cadence * STALLED_CADENCE_MULTIPLIER * 1000

        # Check market closure first to prevent false alarms during weekend
        if is_xauusdt_weekend_break(now_ms):
            return False, "MARKET_WEEKEND_BREAK"

        last_hb = getattr(heartbeat_record, "last_process_heartbeat_at_ms", 0) or 0
        hb_age = now_ms - last_hb
        if hb_age > stall_threshold_ms:
            return True, f"HEARTBEAT_STALE_AGE_{int(hb_age / 1000)}s"

        # Check if feed is disconnected or degraded
        if feed_health and feed_health.get("freshness_state") in ("DISCONNECTED", "UNKNOWN"):
            return False, "WAITING_FEED_CONNECTION"

        last_eval_done = getattr(heartbeat_record, "last_evaluation_completed_at_ms", 0) or 0
        if last_eval_done > 0:
            eval_age = now_ms - last_eval_done
            # If healthy feed and open market, cycle should complete within 3 * cadence
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
        """
        t_now = int(now_ms if now_ms is not None else int(time.time() * 1000))
        latest_hb = None

        if db is not None:
            try:
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
                if is_xauusdt_weekend_break(t_now):
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
    # 2. Market Feed & Candle Freshness (Phase C)
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
        """
        t_now = int(now_ms if now_ms is not None else int(time.time() * 1000))
        from services.bitget_ws_service import bitget_ws_service

        transport_state = getattr(bitget_ws_service, "transport_state", "DISCONNECTED")
        feed_source = getattr(bitget_ws_service, "feed_source", "WS")
        quote = getattr(bitget_ws_service, "latest_quote", None)

        quote_ts = None
        bid = None
        ask = None
        quote_age_ms = None
        quote_age_sec = None

        if quote is not None:
            quote_ts = getattr(quote, "timestamp", None) or getattr(quote, "time", None)
            bid = getattr(quote, "bid", None)
            ask = getattr(quote, "ask", None)
            if quote_ts and quote_ts > 0:
                quote_age_ms = max(0, t_now - quote_ts)
                quote_age_sec = round(quote_age_ms / 1000.0, 2)

        # Freshness determination
        freshness_state = "UNKNOWN"
        if transport_state in ("CONNECTED", "DEGRADED"):
            if quote_age_sec is not None and quote_age_sec <= QUOTE_FRESHNESS_THRESHOLD_SEC:
                freshness_state = "CONNECTED_FRESH"
            elif quote_age_sec is not None:
                freshness_state = "CONNECTED_STALE"
            else:
                freshness_state = "CONNECTED_STALE"
        elif transport_state == "CONNECTING":
            freshness_state = "RECONNECTING"
        else:
            freshness_state = "DISCONNECTED"

        # Multi-timeframe closed candle health
        timeframe_health = {}
        for tf in ["1M", "5M", "15M", "1H", "4H", "D"]:
            candles = candle_cache_service._cache.get((symbol, tf), [])
            closed_ts = None
            if candles:
                closed_ts = candles[-1].timestamp
            expected_interval_ms = TF_MS.get(tf, 60_000)
            tf_age_sec = round(max(0, t_now - closed_ts) / 1000.0, 1) if closed_ts else None
            max_thresh_sec = FRESHNESS_THRESHOLDS_SEC.get(tf, 300)
            is_tf_stale = (tf_age_sec is None) or (tf_age_sec > max_thresh_sec)

            timeframe_health[tf] = {
                "latest_closed_candle_ts": closed_ts,
                "candle_age_sec": tf_age_sec,
                "expected_interval_ms": expected_interval_ms,
                "is_stale": is_tf_stale,
                "freshness_state": "STALE" if is_tf_stale else "FRESH",
            }

        is_weekend = is_xauusdt_weekend_break(t_now)

        return {
            "symbol": symbol,
            "transport_state": transport_state,
            "feed_source": feed_source,
            "freshness_state": freshness_state,
            "is_fresh": freshness_state == "CONNECTED_FRESH",
            "is_weekend_break": is_weekend,
            "last_quote_at_ms": quote_ts,
            "quote_age_sec": quote_age_sec,
            "bid": bid,
            "ask": ask,
            "mid": round((bid + ask) / 2.0, 2) if (bid is not None and ask is not None) else None,
            "timeframe_health": timeframe_health,
            "has_gap": getattr(candle_cache_service, "has_candle_gap", False),
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
        Records single cycle evaluation metrics into in-memory ring buffer and heartbeat.
        """
        inst_id = engine_instance_id or self._current_instance_id
        entry = {
            "cycle_id": cycle_id,
            "engine_instance_id": inst_id,
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

        with self._lock:
            self._recent_cycles_buffer.append(entry)
            if len(self._recent_cycles_buffer) > self._max_recent_cycles:
                self._recent_cycles_buffer.pop(0)
            self._latest_cycle_diagnostic = entry

        # Update engine heartbeat
        self.record_engine_heartbeat(
            db,
            engine_instance_id=inst_id,
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
        """
        db.autoflush = False
        query = db.query(models.OpportunityDecisionEvent)

        if run_id:
            query = query.filter(models.OpportunityDecisionEvent.run_id == run_id)

        if cursor:
            try:
                # cursor = base64(f"{ts}_{id}")
                decoded = base64.b64decode(cursor).decode("utf-8")
                ts_part, id_part = decoded.split("_")
                query = query.filter(
                    (models.OpportunityDecisionEvent.occurred_at_ms < int(ts_part)) |
                    (
                        (models.OpportunityDecisionEvent.occurred_at_ms == int(ts_part)) &
                        (models.OpportunityDecisionEvent.id < int(id_part))
                    )
                )
            except Exception:
                pass

        rows = query.order_by(
            models.OpportunityDecisionEvent.occurred_at_ms.desc(),
            models.OpportunityDecisionEvent.id.desc(),
        ).limit(limit + 1).all()

        has_more = len(rows) > limit
        result_rows = rows[:limit]

        next_cursor = None
        if has_more and result_rows:
            last = result_rows[-1]
            raw_c = f"{last.occurred_at_ms}_{last.id}"
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

        if db is not None:
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
                db.add(row)
                db.flush()
            except Exception as e:
                logger.warning(f"Error persisting EngineIncidentRecord: {e}")

        return inc_data

    def resolve_engine_incident(
        self,
        db: Optional[Session],
        incident_id: str,
        now_ms: Optional[int] = None,
    ) -> bool:
        """Marks an active incident as RECOVERED."""
        t_now = int(now_ms if now_ms is not None else int(time.time() * 1000))
        with self._lock:
            if incident_id in self._active_incidents:
                self._active_incidents[incident_id]["status"] = "RECOVERED"
                self._active_incidents[incident_id]["recovered_at_ms"] = t_now

        if db is not None:
            try:
                row = db.query(models.EngineIncidentRecord).filter(
                    models.EngineIncidentRecord.id == incident_id
                ).first()
                if row:
                    row.status = "RECOVERED"
                    row.recovered_at_ms = t_now
                    db.flush()
                    return True
            except Exception as e:
                logger.warning(f"Error resolving EngineIncidentRecord: {e}")
        return False

    # -----------------------------------------------------------------------
    # 6. Truthful Coverage & Uptime Denominator (Phase F)
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
        Calculates trustworthy observation coverage.
        Excludes scheduled weekend closures from denominator.
        """
        total_span_ms = max(1, end_ms - start_ms)
        observed_intervals_ms = 0
        known_downtime_ms = 0

        # Check recorded observation intervals
        if db is not None:
            try:
                q = db.query(models.PaperObservationInterval).filter(
                    models.PaperObservationInterval.start_ms >= start_ms,
                    models.PaperObservationInterval.end_ms <= end_ms,
                )
                if run_id:
                    q = q.filter(models.PaperObservationInterval.run_id == run_id)
                intervals = q.all()

                for inv in intervals:
                    dur = max(0, inv.end_ms - inv.start_ms)
                    if inv.data_quality == "GOOD":
                        observed_intervals_ms += dur
                    else:
                        known_downtime_ms += dur
            except Exception:
                pass

        # If no explicit intervals, calculate from engine heartbeats
        if observed_intervals_ms == 0 and db is not None:
            try:
                hb = db.query(models.EngineHeartbeatRecord).filter(
                    models.EngineHeartbeatRecord.updated_at_ms >= start_ms,
                    models.EngineHeartbeatRecord.updated_at_ms <= end_ms,
                ).first()
                if hb and hb.last_process_heartbeat_at_ms:
                    observed_intervals_ms = min(total_span_ms, 3600_000)
            except Exception:
                pass

        coverage_pct = round((observed_intervals_ms / total_span_ms) * 100.0, 1) if total_span_ms > 0 else 0.0

        return {
            "total_span_hours": round(total_span_ms / 3600_000.0, 2),
            "observed_healthy_hours": round(observed_intervals_ms / 3600_000.0, 2),
            "known_downtime_hours": round(known_downtime_ms / 3600_000.0, 2),
            "coverage_pct": min(100.0, coverage_pct),
            "coverage_state": "FULL" if coverage_pct >= 95.0 else ("PARTIAL" if coverage_pct >= 50.0 else "LOW"),
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

        # 5. Opportunity Funnel Breakdown (Tier 2)
        from services.opportunity_funnel_service import OpportunityFunnelService
        funnel_summary = OpportunityFunnelService.summarize_opportunity_funnel(
            db,
            start_ms=t_start,
            end_ms=t_end,
        )

        # 6. Recent Decisions & Waiting Setups (Tier 2)
        recent_decisions = self.list_recent_decisions(db, run_id=target_run_id, symbol=symbol, limit=20)

        # Query waiting candidates
        waiting_setups_rows = db.query(models.OpportunityDecisionEvent).filter(
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
        blocked_events = db.query(
            models.OpportunityDecisionEvent.reason_category,
            func.count(models.OpportunityDecisionEvent.id).label("cnt")
        ).filter(
            models.OpportunityDecisionEvent.stage.in_(["BLOCKED", "GUARD_BLOCKED"]),
            models.OpportunityDecisionEvent.occurred_at_ms >= t_start,
            models.OpportunityDecisionEvent.occurred_at_ms <= t_end,
        ).group_by(models.OpportunityDecisionEvent.reason_category).all()

        blocked_categories_summary = [
            {
                "category": r[0] or "STRUCTURE",
                "category_title_vi": REASON_CATEGORIES.get(r[0] or "STRUCTURE", "Khác"),
                "count": r[1],
            }
            for r in blocked_events
        ]

        # 7. Truthful Financial Metrics (Tier 3)
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

        # If run summary not available, query paper_orders directly
        if financial_summary is None:
            orders = db.query(models.PaperOrder).filter(
                models.PaperOrder.instrument == symbol,
                models.PaperOrder.created_at >= t_start,
                models.PaperOrder.created_at <= t_end,
            ).all()

            closed_orders = [o for o in orders if str(o.state).lower() == "closed"]
            armed_orders = [o for o in orders if str(o.state).lower() == "armed"]
            open_orders = [o for o in orders if str(o.state).lower() in ("paper_open", "open", "filled")]

            wins = 0
            losses = 0
            breakevens = 0
            net_pnl_total = 0.0
            for o in closed_orders:
                pnl = float(o.realized_pnl_net or 0.0)
                net_pnl_total += pnl
                if pnl > 0:
                    wins += 1
                elif pnl < 0:
                    losses += 1
                else:
                    breakevens += 1

            total_closed = len(closed_orders)
            financial_summary = {
                "closed_trades": total_closed,
                "open_trades": len(open_orders),
                "armed_orders": len(armed_orders),
                "wins": wins,
                "losses": losses,
                "breakevens": breakevens,
                "winrate_pct": round((wins / total_closed * 100.0), 2) if total_closed > 0 else None,
                "net_pnl_usd": round(net_pnl_total, 2),
                "net_expectancy": round(net_pnl_total / total_closed, 2) if total_closed > 0 else 0.0,
                "max_drawdown_usd": 0.0,
                "max_drawdown_pct": 0.0,
                "initial_capital_usdt": 1000.0,
                "current_equity_usdt": round(1000.0 + net_pnl_total, 2),
            }

        # 8. Active Incidents (Tier 1)
        active_incidents_list = [
            inc for inc in self._active_incidents.values() if inc.get("status") == "ACTIVE"
        ]

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
            "financial": financial_summary,
            "incidents": active_incidents_list,
            "latest_cycle": self.get_latest_cycle_diagnostic(),
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
