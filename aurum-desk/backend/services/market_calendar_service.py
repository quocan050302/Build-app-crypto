"""
backend/services/market_calendar_service.py
Authoritative Market Calendar Service for AURUM V17.6.4 (Phase C, Parts 021-040).

Differentiates between THREE independent schedules:
1. CONTRACT TRADING AVAILABILITY:
   Bitget XAUUSDT Perpetual (USDT-FUTURES) trades 24/7.
   Scheduled market open is continuous (24/7).
   Outages/suspensions are operational events, not scheduled calendar closures.

2. UNDERLYING GOLD REFERENCE MARKET SESSIONS:
   Traditional CME/Spot Gold schedule with New York wall-clock DST awareness:
   - Weekend Break: Friday 17:00 NY to Sunday 18:00 NY.
   - Daily Maintenance: Monday-Thursday 17:00 to 18:00 NY.

3. STRATEGY EXECUTION PERMISSION WINDOWS:
   AURUM trading policy windows (e.g. New York session preference).
   Restricting entries to specific hours is a strategy risk control,
   NOT an instrument availability closure.
"""
from __future__ import annotations

import math
import time
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo


class MarketCalendarType(str, Enum):
    BITGET_XAUUSDT_PERPETUAL = "BITGET_XAUUSDT_PERPETUAL"
    TRADITIONAL_GOLD_REFERENCE = "TRADITIONAL_GOLD_REFERENCE"


def get_ny_timezone() -> ZoneInfo:
    return ZoneInfo("America/New_York")


def get_contract_trading_availability(
    *,
    symbol: str = "XAUUSDT",
    venue: str = "BITGET",
    product_type: str = "USDT-FUTURES",
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Evaluates Bitget perpetual contract trading schedule (Phase C, PART 027-029).
    Bitget USDT-M precious metals perpetual contracts trade 24/7.
    """
    t_now = int(now_ms if now_ms is not None else int(time.time() * 1000))
    if t_now < 0 or t_now > 4_102_444_800_000:  # Valid epoch sanity check (before year 2100)
        return {
            "venue": venue.upper(),
            "symbol": symbol.upper(),
            "product_type": product_type.upper(),
            "trading_schedule": "UNKNOWN",
            "scheduled_open": False,
            "operational_status": "INVALID_TIMESTAMP",
            "source": "BITGET_PRODUCT_SPEC",
            "timestamp_ms": t_now,
        }

    is_bitget_perpetual = (
        venue.upper() == "BITGET"
        and "FUTURES" in product_type.upper()
        and symbol.upper() == "XAUUSDT"
    )

    if is_bitget_perpetual:
        return {
            "venue": "BITGET",
            "symbol": "XAUUSDT",
            "product_type": "USDT-FUTURES",
            "trading_schedule": "24_7",
            "scheduled_open": True,
            "operational_status": "OPEN",
            "source": "BITGET_PRODUCT_SPEC",
            "timestamp_ms": t_now,
        }

    # Generic fallback
    return {
        "venue": venue.upper(),
        "symbol": symbol.upper(),
        "product_type": product_type.upper(),
        "trading_schedule": "STANDARD",
        "scheduled_open": True,
        "operational_status": "UNKNOWN",
        "source": "GENERIC_FALLBACK",
        "timestamp_ms": t_now,
    }


def get_underlying_reference_session(
    *,
    symbol: str = "XAUUSDT",
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Evaluates traditional CME / Spot Gold reference market session (Phase C, PART 030).
    Schedule:
    - Weekend break: Friday 17:00 NY to Sunday 18:00 NY.
    - Daily maintenance: Monday-Thursday 17:00 to 18:00 NY.
    """
    t_now = int(now_ms if now_ms is not None else int(time.time() * 1000))
    tz_ny = get_ny_timezone()
    dt_ny = datetime.fromtimestamp(t_now / 1000.0, tz=tz_ny)
    weekday = dt_ny.weekday()  # 0 = Monday, ..., 4 = Friday, 5 = Saturday, 6 = Sunday
    hour = dt_ny.hour

    is_weekend_break = False
    is_daily_maintenance = False
    close_reason = "OPEN"

    # Friday after 17:00 NY -> Weekend break
    if weekday == 4 and hour >= 17:
        is_weekend_break = True
        close_reason = "WEEKEND_BREAK"
    # Saturday -> All day weekend break
    elif weekday == 5:
        is_weekend_break = True
        close_reason = "WEEKEND_BREAK"
    # Sunday before 18:00 NY -> Weekend break
    elif weekday == 6 and hour < 18:
        is_weekend_break = True
        close_reason = "WEEKEND_BREAK"
    # Mon-Thu daily break: 17:00 to 18:00 NY
    elif weekday in (0, 1, 2, 3) and hour == 17:
        is_daily_maintenance = True
        close_reason = "DAILY_MAINTENANCE"

    is_open = not (is_weekend_break or is_daily_maintenance)

    return {
        "symbol": symbol.upper(),
        "reference_market": "CME_SPOT_GOLD",
        "is_open": is_open,
        "is_weekend_break": is_weekend_break,
        "is_daily_maintenance": is_daily_maintenance,
        "session_state": "OPEN" if is_open else "CLOSED",
        "close_reason": close_reason,
        "ny_weekday": weekday,
        "ny_hour": hour,
        "timestamp_ms": t_now,
    }


def get_strategy_execution_session(
    *,
    strategy_id: str = "CG01",
    now_ms: Optional[int] = None,
    policy: Any = None,
) -> Dict[str, Any]:
    """
    Evaluates whether strategy risk policy permits new position entries at now_ms.
    Strategy execution permission is independent of exchange trading availability (PART 025).
    """
    t_now = int(now_ms if now_ms is not None else int(time.time() * 1000))
    tz_ny = get_ny_timezone()
    dt_ny = datetime.fromtimestamp(t_now / 1000.0, tz=tz_ny)
    weekday = dt_ny.weekday()

    # Traditional gold weekends: Strategy typically restricts entries on weekends by policy
    if weekday in (5, 6):
        return {
            "strategy_id": strategy_id,
            "permitted": False,
            "session_state": "RESTRICTED",
            "reason": "WEEKEND_POLICY_RESTRICTION",
            "timestamp_ms": t_now,
        }

    return {
        "strategy_id": strategy_id,
        "permitted": True,
        "session_state": "PERMITTED",
        "reason": "SESSION_OPEN",
        "timestamp_ms": t_now,
    }


def build_contract_eligible_interval_ranges(
    start_ms: int,
    end_ms: int,
    calendar_type: MarketCalendarType = MarketCalendarType.BITGET_XAUUSDT_PERPETUAL,
) -> List[Tuple[int, int]]:
    """
    Builds eligible observation intervals based on calendar type (PART 033).
    For BITGET_XAUUSDT_PERPETUAL (24/7): returns [(start_ms, end_ms)] if valid.
    For TRADITIONAL_GOLD_REFERENCE: segments by CME New York schedule.
    """
    if end_ms <= start_ms:
        return []

    if calendar_type == MarketCalendarType.BITGET_XAUUSDT_PERPETUAL:
        return [(start_ms, end_ms)]

    # Traditional gold reference calendar
    return build_underlying_gold_reference_interval_ranges(start_ms, end_ms)


def build_underlying_gold_reference_interval_ranges(
    start_ms: int,
    end_ms: int,
) -> List[Tuple[int, int]]:
    """
    Segments by traditional New York gold market schedule (NY whole-hour boundaries).
    Preserved for underlying reference market analytics (PART 034).
    """
    if end_ms <= start_ms:
        return []

    hour_ms = 3_600_000
    intervals: List[Tuple[int, int]] = []
    cursor = start_ms
    while cursor < end_ms:
        boundary = min(end_ms, ((cursor // hour_ms) + 1) * hour_ms)
        midpoint = cursor + (boundary - cursor) // 2
        ref = get_underlying_reference_session(now_ms=midpoint)
        if ref["is_open"]:
            intervals.append((cursor, boundary))
        cursor = boundary

    # Merge contiguous intervals
    if not intervals:
        return []
    merged: List[Tuple[int, int]] = []
    intervals.sort(key=lambda x: x[0])
    for s, e in intervals:
        if not merged:
            merged.append((s, e))
        else:
            last_s, last_e = merged[-1]
            if s <= last_e:
                merged[-1] = (last_s, max(last_e, e))
            else:
                merged.append((s, e))
    return merged
