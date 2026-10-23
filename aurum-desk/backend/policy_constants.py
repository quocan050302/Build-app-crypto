"""
backend/policy_constants.py
V14.4.4 - Pure Trading Policy Constants, Defaults & Validation Logic.

Contains no SQLAlchemy, Session, or external service dependencies.
Authoritative source for default policy across models, DDL, migrations, and services.
"""
from __future__ import annotations

import math
from datetime import time as dtime
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ALL_SESSIONS_WITH_NY_RESERVE = "ALL_SESSIONS_WITH_NY_RESERVE"
NY_ONLY = "NY_ONLY"
ALL_SESSIONS_SETUP_FIRST = "ALL_SESSIONS_SETUP_FIRST"
CG01_ALL_SESSIONS_PAPER = "CG01_ALL_SESSIONS_PAPER"
SILVER_BULLET_LONDON_NY = "SILVER_BULLET_LONDON_NY"
CG01_NY_PAPER_EXPANDED = "CG01_NY_PAPER_EXPANDED"
VALID_ENTRY_SESSION_POLICIES = (
    ALL_SESSIONS_WITH_NY_RESERVE,
    NY_ONLY,
    ALL_SESSIONS_SETUP_FIRST,
)

DEFAULT_POLICY_VALUES: Dict[str, Any] = {
    "id": "default",
    "version": 1,
    "mode": "PAPER",
    "symbol": "XAUUSDT",
    "max_daily_fills": 3,
    "daily_min_goal": 2,
    "daily_timezone": "Asia/Ho_Chi_Minh",
    "entry_session_policy": "ALL_SESSIONS_WITH_NY_RESERVE",
    "ny_timezone": "America/New_York",
    "ny_entry_start": "08:00",
    "ny_entry_end": "11:00",
    "ny_min_fills": 1,
    "reserve_ny_slot": True,
    "ny_fallback_enabled": True,
    "ny_fallback_start": "10:30",
    "ny_fallback_risk_pct_cap": 0.10,
    "min_net_rr": 2.0,
    "max_open_positions": 1,
    "max_armed_orders": 1,
}


def get_default_policy_values() -> Mapping[str, Any]:
    """PHẦN 007, 096: Returns immutable mapping of centralized default policy values."""
    return MappingProxyType(DEFAULT_POLICY_VALUES)


def compute_reserved_capacity(
    max_fills: int,
    daily_fills: int,
    ny_fills: int,
    target: int,
    reserve_enabled: bool,
) -> Dict[str, int]:
    """
    PHẦN 014, 099: Pure capacity & reservation calculation.
    Enforces that numeric parameters are non-negative integers (rejecting booleans).
    """
    for name, val in [
        ("max_fills", max_fills),
        ("daily_fills", daily_fills),
        ("ny_fills", ny_fills),
        ("target", target),
    ]:
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            raise ValueError(f"{name} must be a non-negative integer, got {val!r}")

    if not isinstance(reserve_enabled, bool):
        raise ValueError(f"reserve_enabled must be a boolean, got {reserve_enabled!r}")

    remaining = max(0, max_fills - daily_fills)
    needed_ny = max(0, target - ny_fills)
    reserved = min(remaining, needed_ny) if reserve_enabled else 0
    return {
        "remaining": remaining,
        "reserved_ny": reserved,
        "available_outside_ny": max(0, remaining - reserved),
    }


def validate_trading_policy_update(
    current_policy: Any,
    patch: Dict[str, Any],
) -> Dict[str, Any]:
    """
    PHẦN 010, 011, 021, 027, 028, 029: Centralized policy update validator.
    Merges patch with current policy and validates invariants:
    - max_daily_fills fixed at 3 (int, not bool)
    - min_net_rr >= 1.5 in PAPER
    - ny_fallback_risk_pct_cap <= 0.10
    - max_open_positions == 1
    - entry_session_policy in ('ALL_SESSIONS_WITH_NY_RESERVE', 'NY_ONLY', 'ALL_SESSIONS_SETUP_FIRST')
    - ny_min_fills >= 0; if ALL_SESSIONS_SETUP_FIRST, ny_min_fills must be 0, reserve_ny_slot=False, ny_fallback_enabled=False
    - Time format HH:MM and ordering ny_entry_start < ny_fallback_start < ny_entry_end
    - Timezone validity
    """
    def _val(k: str, default: Any = None) -> Any:
        if k in patch:
            return patch[k]
        if hasattr(current_policy, k):
            return getattr(current_policy, k)
        if isinstance(current_policy, dict):
            return current_policy.get(k, default)
        return default

    # 1. Daily fill cap
    max_daily = _val("max_daily_fills", 3)
    if isinstance(max_daily, bool) or not isinstance(max_daily, int) or max_daily != 3:
        raise ValueError("Chính sách yêu cầu max_daily_fills cố định là 3 (nguyên tắc an toàn vốn UTC+7).")

    # 2. Min net R:R (V16.39.15: floor 1.5 in PAPER mode)
    min_rr = _val("min_net_rr", 2.0)
    if isinstance(min_rr, bool) or not isinstance(min_rr, (int, float)) or not math.isfinite(min_rr) or float(min_rr) < 1.5:
        raise ValueError("min_net_rr (Min Net R:R) phải là số hữu hạn từ 1.5 trở lên trong chế độ PAPER hiện tại.")

    # 3. Fallback risk cap
    fb_cap = _val("ny_fallback_risk_pct_cap", 0.10)
    if isinstance(fb_cap, bool) or not isinstance(fb_cap, (int, float)) or not math.isfinite(fb_cap) or float(fb_cap) > 0.10 or float(fb_cap) <= 0:
        raise ValueError("ny_fallback_risk_pct_cap phải > 0 và không được vượt quá 0.10% rủi ro.")

    # 4. Max open positions
    max_pos = _val("max_open_positions", 1)
    if isinstance(max_pos, bool) or not isinstance(max_pos, int) or max_pos != 1:
        raise ValueError("max_open_positions cố định là 1 (chỉ một vị thế mở tại một thời điểm).")

    # 5. Session policy enum
    session_pol = _val("entry_session_policy", ALL_SESSIONS_WITH_NY_RESERVE)
    if session_pol not in VALID_ENTRY_SESSION_POLICIES:
        raise ValueError(
            f"entry_session_policy không hợp lệ: {session_pol!r}. "
            f"Cho phép: {', '.join(VALID_ENTRY_SESSION_POLICIES)}."
        )

    # 5b. NY quota & reservation validation (Parts 027, 028)
    default_ny_min = 0 if session_pol == ALL_SESSIONS_SETUP_FIRST else 1
    ny_min = _val("ny_min_fills", default_ny_min)
    if isinstance(ny_min, bool) or not isinstance(ny_min, int) or ny_min < 0:
        raise ValueError(f"ny_min_fills phải là số nguyên không âm, nhận được {ny_min!r}.")
    if ny_min > max_daily:
        raise ValueError(f"ny_min_fills ({ny_min}) không được vượt quá max_daily_fills ({max_daily}).")

    if session_pol == ALL_SESSIONS_SETUP_FIRST:
        if ny_min != 0:
            raise ValueError("Trong chế độ ALL_SESSIONS_SETUP_FIRST, ny_min_fills bắt buộc phải là 0.")
        if _val("reserve_ny_slot", False) is True:
            raise ValueError("Trong chế độ ALL_SESSIONS_SETUP_FIRST, reserve_ny_slot không được bật (phải là False).")
        if _val("ny_fallback_enabled", False) is True:
            raise ValueError("Trong chế độ ALL_SESSIONS_SETUP_FIRST, ny_fallback_enabled không được bật (phải là False).")

    # 6. Timezones
    ny_tz = _val("ny_timezone", "America/New_York")
    try:
        ZoneInfo(str(ny_tz))
    except (ZoneInfoNotFoundError, Exception):
        raise ValueError(f"ny_timezone không hợp lệ: {ny_tz}")

    daily_tz = _val("daily_timezone", "Asia/Ho_Chi_Minh")
    try:
        ZoneInfo(str(daily_tz))
    except (ZoneInfoNotFoundError, Exception):
        raise ValueError(f"daily_timezone không hợp lệ: {daily_tz}")

    # 7. Time format & ordering
    start_str = str(_val("ny_entry_start", "08:00"))
    fb_str = str(_val("ny_fallback_start", "10:30"))
    end_str = str(_val("ny_entry_end", "11:00"))

    try:
        sh, sm = map(int, start_str.split(":"))
        fh, fm = map(int, fb_str.split(":"))
        eh, em = map(int, end_str.split(":"))
        t_s = dtime(sh, sm)
        t_f = dtime(fh, fm)
        t_e = dtime(eh, em)
        if not (t_s < t_f < t_e):
            raise ValueError(
                f"Cửa sổ phiên Mỹ không hợp lệ: Yêu cầu ny_entry_start ({start_str}) < "
                f"ny_fallback_start ({fb_str}) < ny_entry_end ({end_str})."
            )
    except ValueError as ve:
        if "Cửa sổ phiên Mỹ không hợp lệ" in str(ve):
            raise
        raise ValueError(f"Định dạng giờ không hợp lệ: {ve}. Yêu cầu HH:MM.")

    # Build validated dictionary
    validated: Dict[str, Any] = {}
    for k, v in patch.items():
        if k in DEFAULT_POLICY_VALUES:
            validated[k] = v

    return validated


def build_ny_quota_progress(
    quota: Any,
    latest_scan: Optional[Dict[str, Any]],
    window: Dict[str, Any],
    policy: Any,
) -> Dict[str, Any]:
    """
    PHẦN 021, 075, 101: Compact structured progress DTO for NY quota.
    Provides clear visibility into target vs actual fills, current window phase,
    and audited primary blockers.
    """
    pol_mode = getattr(policy, "entry_session_policy", "")
    is_setup_first = (pol_mode == ALL_SESSIONS_SETUP_FIRST)

    total_fills = int(getattr(quota, "total_fills", 0) or 0)
    target_fills = 0 if is_setup_first else int(getattr(quota, "target_fills", 1) or 1)
    standard_fills = int(getattr(quota, "standard_fills", 0) or 0)
    fallback_fills = int(getattr(quota, "fallback_fills", 0) or 0)
    status = getattr(quota, "quota_status", "NOT_STARTED")
    reason = getattr(quota, "last_status_reason", "")
    phase = window.get("phase", "BEFORE_WINDOW")

    if is_setup_first:
        primary_blocker = "NOT_REQUIRED"
        next_action = "Không đặt chỉ tiêu riêng cho phiên Mỹ (ALL_SESSIONS_SETUP_FIRST)."
        is_fallback_active = False
    elif total_fills >= target_fills:
        primary_blocker = "FULFILLED"
        next_action = "Chỉ tiêu phiên Mỹ đã hoàn thành."
        is_fallback_active = False
    elif phase == "BEFORE_WINDOW":
        primary_blocker = "BEFORE_WINDOW"
        next_action = f"Chờ mở cửa sổ phiên Mỹ ({window.get('ny_window_str', '')})."
        is_fallback_active = False
    elif phase == "AFTER_WINDOW":
        primary_blocker = "AFTER_WINDOW_MISSED" if total_fills == 0 else "WINDOW_CLOSED"
        next_action = "Cửa sổ phiên Mỹ đã kết thúc."
        is_fallback_active = False
    else:
        is_fallback_active = bool(window.get("is_fallback_active", False))
        if "BLOCKED" in status:
            primary_blocker = status
            next_action = reason
        elif status == "ARMED_PENDING_FILL":
            primary_blocker = "ARMED_PENDING_FILL"
            next_action = "Lệnh chờ đã lên nòng, đang đợi giá chạm để khớp."
        elif phase == "IN_FALLBACK_WINDOW":
            primary_blocker = "SEEKING_FALLBACK" if getattr(policy, "ny_fallback_enabled", True) else "FALLBACK_DISABLED"
            next_action = "Đang tìm cơ hội Fallback FVG retest."
        else:
            primary_blocker = "SEEKING_STANDARD"
            next_action = "Đang tìm kiếm cơ hội SMC chuẩn trong phiên Mỹ."

    return {
        "target_fills": target_fills,
        "actual_fills": total_fills,
        "standard_fills": standard_fills,
        "fallback_fills": fallback_fills,
        "phase": phase,
        "quota_status": status,
        "primary_blocker": primary_blocker,
        "reason": reason,
        "next_action": next_action,
        "session_instance_id": getattr(quota, "session_instance_id", window.get("session_instance_id")),
        "is_in_window": window.get("is_in_window", False),
        "is_fallback_active": is_fallback_active,
        "ny_goal_required": not is_setup_first,
    }
