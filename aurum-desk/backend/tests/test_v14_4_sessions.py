import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from services.smc_session_context import (
    build_session_range,
    build_previous_day_levels,
    NY_TZ,
)

class CandleLike:
    def __init__(self, **kwargs):
        self.is_closed = True
        self.__dict__.update(kwargs)

def test_partial_session_range():
    # London session for NY date 2026-10-12 (starts at 03:00 NY time)
    base_dt = datetime(2026, 10, 12, 3, 0, tzinfo=NY_TZ)
    base_ms = int(base_dt.timestamp() * 1000)

    candles = [
        CandleLike(timestamp=base_ms, open=2650.0, high=2655.0, low=2648.0, close=2652.0),
        CandleLike(timestamp=base_ms + 3600000, open=2652.0, high=2660.0, low=2651.0, close=2658.0),
    ]

    now_ms = base_ms + 2 * 3600000 # In session, not complete yet
    rng = build_session_range(
        session_name='LONDON',
        ny_date_str='2026-10-12',
        closed_candles=candles,
        now_ms=now_ms
    )
    assert rng is not None
    assert rng.is_complete is False
    assert rng.high == 2660.0
    assert rng.low == 2648.0

def test_dst_ny_session_boundary():
    # Winter (EST, UTC-5): Jan 15, 2026
    dt_winter = datetime(2026, 1, 15, 8, 30, tzinfo=ZoneInfo('America/New_York'))
    utc_winter = dt_winter.astimezone(timezone.utc)
    assert utc_winter.hour == 13 # 8:30 EST = 13:30 UTC

    # Summer (EDT, UTC-4): July 15, 2026
    dt_summer = datetime(2026, 7, 15, 8, 30, tzinfo=ZoneInfo('America/New_York'))
    utc_summer = dt_summer.astimezone(timezone.utc)
    assert utc_summer.hour == 12 # 8:30 EDT = 12:30 UTC

def test_previous_day_levels():
    base_dt = datetime(2026, 10, 11, 0, 0, tzinfo=NY_TZ)
    base_ms = int(base_dt.timestamp() * 1000)
    day_candles = [
        CandleLike(timestamp=base_ms, open=2640.0, high=2670.0, low=2630.0, close=2665.0),
    ]
    levels = build_previous_day_levels(
        current_ny_date_str='2026-10-12',
        closed_daily_candles=day_candles,
        now_ms=base_ms + 86400000 + 1000
    )
    assert levels is not None
    assert levels.pdh == 2670.0
    assert levels.pdl == 2630.0
    assert levels.pdc == 2665.0
    assert levels.pdo == 2640.0
