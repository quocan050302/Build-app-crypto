"""
Aurum Desk V14.3 - pure helpers for the NY session PAPER quota flow.

Everything here is a pure function (no DB, no network, no wall clock). Callers inject
`now_ms`. "May SEARCH for a fallback candidate" is NOT permission to ARM or FILL: the V14.2
common entry guard re-checks every mutation.
"""
from __future__ import annotations

import hashlib
import math
from math import isfinite
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

FIVE_MIN_MS = 5 * 60 * 1000
FALLBACK_MODEL_VERSION = "NY_FALLBACK_FVG_RETEST_V1"
FALLBACK_TTL_MS = 45 * 60 * 1000
# Mirrors PaperBroker.evaluate_account_guard: daily loss budget = 1.5% of initial equity.
DAILY_LOSS_BUDGET_PCT = 0.015
FALLBACK_HARD_RISK_CAP_PCT = 0.10
MIN_ATR_WARMUP_BARS = 15  # 14 true ranges need 15 closed bars

# ---------------------------------------------------------------------------
# Search eligibility (Appendix A)
# ---------------------------------------------------------------------------

def should_search_ny_fallback(*, policy_active: bool, mode: str,
                              fallback_enabled: bool, now_ms: int,
                              window_start_ms: int, fallback_start_ms: int,
                              window_end_ms: int, target_fills: int,
                              total_fills: int) -> bool:
    return bool(
        policy_active and mode == 'PAPER' and fallback_enabled and
        window_start_ms <= fallback_start_ms < window_end_ms and
        fallback_start_ms <= now_ms < window_end_ms and
        target_fills > 0 and 0 <= total_fills < target_fills
    )


def remaining_ny_risk_budget(*, daily_loss_budget_usdt: float,
                             realized_pnl_today: float,
                             reserved_net_risk_usdt: float) -> float:
    values = (daily_loss_budget_usdt, realized_pnl_today, reserved_net_risk_usdt)
    if not all(isfinite(v) for v in values):
        raise ValueError('RISK_BUDGET_DATA_UNAVAILABLE')
    if daily_loss_budget_usdt < 0 or reserved_net_risk_usdt < 0:
        raise ValueError('INVALID_RISK_BUDGET')
    # Same convention as the account guard: profit does not raise the daily budget.
    realized_loss = max(0.0, -realized_pnl_today)
    return max(0.0, daily_loss_budget_usdt - realized_loss - reserved_net_risk_usdt)


def effective_fallback_risk_pct(*, equity: float, remaining_usdt: float,
                                configured_cap_pct: float,
                                requested_risk_pct: float) -> float:
    values = (equity, remaining_usdt, configured_cap_pct, requested_risk_pct)
    if not all(isfinite(v) for v in values):
        raise ValueError('RISK_BUDGET_DATA_UNAVAILABLE')
    if equity <= 0 or min(remaining_usdt, configured_cap_pct, requested_risk_pct) <= 0:
        return 0.0
    # Never round UP: the calculator applies the quantity step and checks net risk.
    raw = min(FALLBACK_HARD_RISK_CAP_PCT, configured_cap_pct, requested_risk_pct,
              remaining_usdt / equity * 100.0)
    return math.floor(raw * 1_000_000) / 1_000_000


# ---------------------------------------------------------------------------
# Closed candles (Appendix B)
# ---------------------------------------------------------------------------

def closed_candles_at(candles, *, now_ms: int, duration_ms: int):
    """Closed bars as of `now_ms`: explicit is_closed True and timestamp+duration <= now_ms."""
    if duration_ms <= 0:
        raise ValueError('INVALID_TIMEFRAME')
    by_timestamp = {}
    for candle in candles:
        stamp = getattr(candle, 'timestamp', None)
        if not isinstance(stamp, int) or isinstance(stamp, bool) or stamp <= 0:
            raise ValueError('INVALID_CANDLE_TIMESTAMP')
        if getattr(candle, 'is_closed', None) is not True:
            continue
        if stamp + duration_ms > now_ms:
            continue
        if stamp in by_timestamp:
            old = by_timestamp[stamp]
            old_ohlc = tuple(getattr(old, k) for k in ('open', 'high', 'low', 'close'))
            new_ohlc = tuple(getattr(candle, k) for k in ('open', 'high', 'low', 'close'))
            if old_ohlc != new_ohlc:
                raise ValueError('CONFLICTING_CANDLE_DUPLICATE')
        by_timestamp[stamp] = candle
    return [by_timestamp[t] for t in sorted(by_timestamp)]


def validate_candle_ohlc(candles: Sequence[Any]) -> Optional[str]:
    """Returns None when every bar has finite, positive, internally consistent OHLC."""
    for c in candles:
        try:
            o, h, l, cl = float(c.open), float(c.high), float(c.low), float(c.close)
        except (TypeError, ValueError, AttributeError):
            return 'DATA_INVALID_CANDLE'
        if not all(isfinite(v) and v > 0 for v in (o, h, l, cl)):
            return 'DATA_INVALID_CANDLE'
        if h < max(o, cl, l) or l > min(o, cl, h):
            return 'DATA_INVALID_CANDLE'
    return None


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def stable_hash(*parts: Any, length: int = 12) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


def ny_setup_instance_id(session_instance_id: str, symbol: str, direction: str, fvg_id: str) -> str:
    """Stable per NY session + symbol + direction + FVG anchor. Never derived from a clock."""
    return f"nyfb-{session_instance_id}-{stable_hash(session_instance_id, symbol, direction, fvg_id)}"


def ny_signal_id(instance_id: str) -> str:
    return f"sig-nyfb-{stable_hash(instance_id)}"


def fallback_expiry_ms(now_ms: int, window_end_ms: int, ttl_ms: int = FALLBACK_TTL_MS) -> int:
    """expires_at = min(now + TTL, session window end): a pending fallback never outlives the window."""
    return min(int(now_ms) + int(ttl_ms), int(window_end_ms))


# ---------------------------------------------------------------------------
# Reason taxonomy: DATA / STRATEGY / SAFETY / EXECUTION / SYSTEM
# ---------------------------------------------------------------------------

REASONS: Dict[str, Tuple[str, str]] = {
    # DATA - something we need to decide is missing/invalid (never conclude "no edge" from this)
    "STANDARD_DATA_MISSING": ("DATA", "Thiếu nến 15M để quét SMC chuẩn"),
    "FALLBACK_DATA_MISSING": ("DATA", "Thiếu nến 5M đã đóng để quét fallback"),
    "FALLBACK_INSUFFICIENT_DATA": ("DATA", "Thiếu nến 5M đã đóng để quét fallback"),
    "DATA_INVALID_CANDLE": ("DATA", "Lỗi dữ liệu nến 5M fallback"),
    "CANDLE_DATA_ERROR": ("DATA", "Lỗi dữ liệu nến 5M fallback"),
    "QUOTE_UNAVAILABLE": ("DATA", "Chưa có báo giá hợp lệ"),
    "RISK_BUDGET_DATA_UNAVAILABLE": ("DATA", "Không đọc được ngân sách rủi ro"),
    "HTF_DATA_UNAVAILABLE": ("DATA", "Chưa có xu hướng khung lớn"),
    # STRATEGY - data is fine but the setup does not exist (yet)
    "FALLBACK_HTF_NOT_CONFIRMED": ("STRATEGY", "Xu hướng khung lớn chưa rõ hướng"),
    "FALLBACK_H1_NOT_ALIGNED": ("STRATEGY", "H1 chưa đồng thuận với xu hướng"),
    "FALLBACK_NO_FVG": ("STRATEGY", "Chưa có vùng FVG 5M đúng hướng"),
    "FALLBACK_NO_ELIGIBLE_CANDIDATE": ("STRATEGY", "Chưa có vùng FVG 5M đúng hướng"),
    "FALLBACK_WAITING_RETRACE": ("STRATEGY", "Chờ hồi về FVG"),
    "FALLBACK_NO_LIQUIDITY_TARGET": ("STRATEGY", "Không có target đủ RR"),
    "NET_RR_TOO_LOW": ("STRATEGY", "Không có target đủ RR"),
    "INVALID_GEOMETRY": ("STRATEGY", "Hình học giá không hợp lệ"),
    "NO_CANDIDATE": ("STRATEGY", "Chưa có tín hiệu"),
    "FALLBACK_CANDIDATE_READY": ("STRATEGY", "Có ứng viên fallback"),
    # SAFETY - a signal exists but a guard says no
    "NEWS_BLACKOUT": ("SAFETY", "Tạm dừng vì tin"),
    "NO_REMAINING_RISK": ("SAFETY", "Hết ngân sách rủi ro trong ngày"),
    "MAX_DAILY_ENTRIES": ("SAFETY", "Đủ 3 lệnh/ngày"),
    "DAILY_FILL_CAP": ("SAFETY", "Đủ 3 lệnh/ngày"),
    "DAILY_LOSS_BUDGET_EXCEEDED": ("SAFETY", "Vượt ngân sách lỗ ngày"),
    "MAX_CONSECUTIVE_LOSSES": ("SAFETY", "Đã dừng sau 2 lệnh lỗ liên tiếp"),
    "DAY_BLOCKED": ("SAFETY", "Ngày giao dịch đang bị khóa"),
    "COOLDOWN_ACTIVE": ("SAFETY", "Đang cooldown"),
    "ACTIVE_POSITION_EXISTS": ("SAFETY", "Đang có vị thế mở"),
    "ARMED_ORDER_EXISTS": ("SAFETY", "Đã có lệnh chờ khớp"),
    "OUTSIDE_ENTRY_WINDOW": ("SAFETY", "Ngoài cửa sổ phiên Mỹ"),
    "UNSUPPORTED_MODE": ("SAFETY", "Chỉ hỗ trợ PAPER"),
    "SETUP_TERMINAL": ("SAFETY", "Ứng viên đã kết thúc"),
    # EXECUTION - quote/feed/calc/auto switch
    "QUOTE_STALE": ("EXECUTION", "Báo giá đã cũ"),
    "QUOTE_INVALID": ("EXECUTION", "Báo giá không hợp lệ"),
    "EXECUTION_FEED_DEGRADED": ("EXECUTION", "Nguồn giá thực thi đang gián đoạn"),
    "CALCULATOR_REJECTED": ("EXECUTION", "Máy tính khối lượng từ chối lệnh"),
    "AUTO_PAPER_OFF": ("EXECUTION", "Tự động đang tắt"),
    "ARM_BLOCKED": ("EXECUTION", "Guard chặn Arm"),
    # SYSTEM
    "STANDARD_SYSTEM_ERROR": ("SYSTEM", "Lỗi hệ thống khi quét SMC chuẩn"),
    "CANDIDATE_ALREADY_USED": ("SAFETY", "Ứng viên này đã đặt lệnh, không thử lại"),
    "SYSTEM_ERROR": ("SYSTEM", "Lỗi hệ thống fallback"),
    "UPSERT_FAILED": ("SYSTEM", "Lỗi lưu ứng viên fallback"),
    # informational (not a miss reason)
    "FALLBACK_DISABLED": ("SAFETY", "Fallback đang tắt"),
}


def classify_reason(code: Optional[str]) -> Tuple[str, str]:
    """(category, message_vi) for a machine code. Unknown codes are SAFETY-guard codes by default."""
    if not code:
        return ("STRATEGY", "Chưa có tín hiệu")
    if code in REASONS:
        return REASONS[code]
    if code.startswith("LESSON"):
        return ("SAFETY", "Bài học đã duyệt đang chặn")
    if code.startswith("QUOTE") or code.startswith("EXECUTION"):
        return ("EXECUTION", code)
    if code.startswith("DATA") or code.endswith("DATA_UNAVAILABLE"):
        return ("DATA", code)
    return ("SAFETY", code)


# Priority used for the stable "primary reason" (lowest index wins).
CATEGORY_PRIORITY = ("SYSTEM", "DATA", "SAFETY", "EXECUTION", "STRATEGY")


def primary_reason(codes: Iterable[str]) -> Optional[str]:
    codes = [c for c in codes if c]
    if not codes:
        return None
    def key(c: str):
        cat = classify_reason(c)[0]
        return (CATEGORY_PRIORITY.index(cat), codes.index(c))
    return sorted(set(codes), key=key)[0]
