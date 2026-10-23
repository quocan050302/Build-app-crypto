"""
backend/tests/test_v17_4_timing_profiles.py
V17.4 Parts 041-045, 060, 151, 152, 153:
Comprehensive timing profile resolver tests covering:
- Three profiles: SILVER_BULLET_LONDON_NY, CG01_NY_PAPER_EXPANDED, CG01_ALL_SESSIONS_PAPER
- Multi-session matrix: Asia (20:30 NY), London (04:30 NY), NY AM (09:30 NY), NY PM (12:30 NY), Analytics OFF
- Daylight Saving Time (EDT vs EST)
- Scope enforcement: PAPER vs LIVE (rejects live scope from getting all-session window)
- Nullable window bounds (None, None for all-session)
"""
from datetime import datetime, timezone
import pytest
from zoneinfo import ZoneInfo

from services.cg01_engine import resolve_cg01_windows_for_profile, resolve_cg01_time_context
from services.cg01_types import CG01Config, CG01TimingProfile


NY_TZ = ZoneInfo("America/New_York")


def test_t01_cg01_all_sessions_paper_allows_all_sessions():
    """PHẦN 041, 151: CG01_ALL_SESSIONS_PAPER yields active window and null bounds across all hours."""
    # 1. Asia session (20:30 NY)
    dt_asia = datetime(2026, 4, 15, 20, 30, tzinfo=NY_TZ)
    win_id, w_start, w_end, eff_prof = resolve_cg01_windows_for_profile(
        dt_asia, profile="CG01_ALL_SESSIONS_PAPER", execution_scope="PAPER"
    )
    assert win_id == "ALL_SESSIONS_PAPER"
    assert w_start is None
    assert w_end is None
    assert eff_prof == "CG01_ALL_SESSIONS_PAPER"

    # 2. London outside Silver Bullet (04:30 NY)
    dt_london = datetime(2026, 4, 15, 4, 30, tzinfo=NY_TZ)
    win_id, w_start, w_end, eff_prof = resolve_cg01_windows_for_profile(
        dt_london, profile="CG01_ALL_SESSIONS_PAPER", execution_scope="PAPER"
    )
    assert win_id == "ALL_SESSIONS_PAPER"
    assert w_start is None
    assert w_end is None

    # 3. NY after window (12:30 NY)
    dt_ny_after = datetime(2026, 4, 15, 12, 30, tzinfo=NY_TZ)
    win_id, w_start, w_end, eff_prof = resolve_cg01_windows_for_profile(
        dt_ny_after, profile="CG01_ALL_SESSIONS_PAPER", execution_scope="PAPER"
    )
    assert win_id == "ALL_SESSIONS_PAPER"
    assert w_start is None
    assert w_end is None


def test_t02_legacy_silver_bullet_preserves_strict_windows():
    """PHẦN 043, 151: SILVER_BULLET_LONDON_NY strictly restricts to London 03-04 and NY 10-11."""
    # Inside London Silver Bullet (03:30 NY)
    dt_london_in = datetime(2026, 4, 15, 3, 30, tzinfo=NY_TZ)
    win_id, w_start, w_end, _ = resolve_cg01_windows_for_profile(
        dt_london_in, profile="SILVER_BULLET_LONDON_NY", execution_scope="PAPER"
    )
    assert win_id == "LONDON_SILVER_BULLET"
    assert w_start is not None and w_end is not None

    # Outside London Silver Bullet (04:30 NY)
    dt_london_out = datetime(2026, 4, 15, 4, 30, tzinfo=NY_TZ)
    win_id, _, _, _ = resolve_cg01_windows_for_profile(
        dt_london_out, profile="SILVER_BULLET_LONDON_NY", execution_scope="PAPER"
    )
    assert win_id is None

    # Inside NY Silver Bullet (10:15 NY)
    dt_ny_in = datetime(2026, 4, 15, 10, 15, tzinfo=NY_TZ)
    win_id, w_start, w_end, _ = resolve_cg01_windows_for_profile(
        dt_ny_in, profile="SILVER_BULLET_LONDON_NY", execution_scope="PAPER"
    )
    assert win_id == "NY_AM_SILVER_BULLET"
    assert w_start is not None and w_end is not None

    # NY outside window (09:30 NY)
    dt_ny_out = datetime(2026, 4, 15, 9, 30, tzinfo=NY_TZ)
    win_id, _, _, _ = resolve_cg01_windows_for_profile(
        dt_ny_out, profile="SILVER_BULLET_LONDON_NY", execution_scope="PAPER"
    )
    assert win_id is None


def test_t03_scope_guard_blocks_live_execution():
    """PHẦN 042, 153: LIVE scope MUST NOT receive ALL_SESSIONS_PAPER window permission."""
    dt = datetime(2026, 4, 15, 20, 0, tzinfo=NY_TZ)

    # LIVE scopes
    for live_scope in ("LIVE", "REAL", "LIVE_EXECUTION"):
        win_id, w_start, w_end, eff_prof = resolve_cg01_windows_for_profile(
            dt, profile="CG01_ALL_SESSIONS_PAPER", execution_scope=live_scope
        )
        assert win_id is None
        assert eff_prof == "SILVER_BULLET_LONDON_NY"

    # Non-live scopes
    for paper_scope in ("PAPER", "PAPER_EXECUTION", "PAPER_SHADOW", "SHADOW", "SIMULATION"):
        win_id, _, _, eff_prof = resolve_cg01_windows_for_profile(
            dt, profile="CG01_ALL_SESSIONS_PAPER", execution_scope=paper_scope
        )
        assert win_id == "ALL_SESSIONS_PAPER"
        assert eff_prof == "CG01_ALL_SESSIONS_PAPER"


def test_t04_dst_handling_edt_vs_est():
    """PHẦN 045, 152: Time context correctly respects Daylight Saving Time without fixed UTC offset."""
    # Summer (EDT, UTC-4)
    dt_edt = datetime(2026, 7, 15, 10, 30, tzinfo=NY_TZ)
    now_ms_edt = int(dt_edt.timestamp() * 1000)
    cfg_edt = CG01Config(timing_profile="CG01_ALL_SESSIONS_PAPER")
    ctx_edt = resolve_cg01_time_context(now_ms_edt, cfg_edt)
    assert "EDT" in ctx_edt.ny_time_str
    assert ctx_edt.is_in_window is True
    assert ctx_edt.active_window_id == "ALL_SESSIONS_PAPER"

    # Winter (EST, UTC-5)
    dt_est = datetime(2026, 1, 15, 10, 30, tzinfo=NY_TZ)
    now_ms_est = int(dt_est.timestamp() * 1000)
    ctx_est = resolve_cg01_time_context(now_ms_est, cfg_edt)
    assert "EST" in ctx_est.ny_time_str
    assert ctx_est.is_in_window is True
    assert ctx_est.active_window_id == "ALL_SESSIONS_PAPER"


def test_t05_time_context_permission_reason_human_readable():
    """PHẦN 044: Permission reason text explains all-session permission clearly."""
    dt = datetime(2026, 4, 15, 20, 0, tzinfo=NY_TZ)
    now_ms = int(dt.timestamp() * 1000)
    cfg = CG01Config(timing_profile="CG01_ALL_SESSIONS_PAPER")
    ctx = resolve_cg01_time_context(now_ms, cfg)
    assert ctx.active_window_id == "ALL_SESSIONS_PAPER"
    assert "Cho phép xét setup PAPER ở mọi phiên" in ctx.permission_reason
    assert "(None–None)" not in ctx.permission_reason
    assert ctx.window_start_ms is None
    assert ctx.window_end_ms is None
