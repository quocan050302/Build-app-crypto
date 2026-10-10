"""
Aurum Desk V13.4 — Multi-session integration test across 40 real calendar days.
Phần 82.
"""
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from schemas import ReplayRunRequest
from domain_calculator import CostAssumptions
from lab.daily_research_scheduler import (
    build_session_interval,
    assess_session_data,
    evaluate_session_eligibility,
    should_schedule_daily_entry,
    choose_scheduled_direction,
    build_scheduled_price_plan,
    finalize_session_outcome,
    summarize_cadence,
    SessionResearchState,
    NY_TZ,
    VN_TZ
)


def test_01_multi_session_scheduler_40_days():
    """S01: Run scheduler across 40 consecutive days (2026-07-09 to 2026-08-17)."""
    start_dt = datetime(2026, 7, 9, tzinfo=NY_TZ)
    session_outcomes = []
    costs = CostAssumptions(taker_fee_pct=0.0006, maker_fee_pct=0.0002, slippage_usd=0.10, spread_usd=0.20)
    config = ReplayRunRequest(
        strategy_variant="NY_ADAPTIVE",
        entry_cadence="DAILY_PAPER",
        quota_risk_pct=0.10,
        leverage=30,
        ny_max_fills=3,
        scheduled_deadline_hour=14,
        scheduled_deadline_minute=30
    )

    eligible_count = 0
    filled_count = 0

    for i in range(40):
        cur_dt = start_dt + timedelta(days=i)
        ny_date_str = cur_dt.strftime("%Y-%m-%d")
        interval = build_session_interval(ny_date_str, config)
        is_weekend = cur_dt.weekday() in (5, 6)
        # Create realistic candles including warmup lookback for this session
        candles = ([
            {"timestamp": interval["start_ms"] - (25 - k) * 900000, "open": 2650.0, "high": 2655.0, "low": 2648.0, "close": 2652.0}
            for k in range(25)
        ] + [
            {"timestamp": interval["start_ms"] + j * 900000, "open": 2650.0, "high": 2655.0, "low": 2648.0, "close": 2652.0}
            for j in range(25)
        ]) if not is_weekend else []

        m_open, d_comp, w_comp, reasons = assess_session_data(interval, candles)
        elig = evaluate_session_eligibility(
            session_id=interval["session_id"],
            ny_date=ny_date_str,
            has_data=d_comp,
            warmup_complete=w_comp,
            is_weekend=(not m_open)
        )

        state = SessionResearchState(
            session_id=interval["session_id"],
            date_ny=ny_date_str,
            trade_day_vn=ny_date_str,
            is_eligible=elig.eligible
        )

        if elig.eligible:
            eligible_count += 1
            # Test scheduler at deadline 14:30
            deadline_ms = interval["deadline_ms"]
            due, _ = should_schedule_daily_entry(state, deadline_ms, config, elig)
            assert due is True, f"Expected scheduled entry to be due on eligible session {ny_date_str}"

            # Execute entry
            quote = {"sim_time": deadline_ms, "close": 2650.0, "spread_usd": 0.20, "costs": costs, "capital": 1000.0}
            context = {"recent_bars_5m": [], "recent_bars_15m": candles}
            dir_res, _, _, _ = choose_scheduled_direction(context, config)
            plan, err = build_scheduled_price_plan(dir_res, context, quote, config)

            if plan is not None:
                state.fills += 1
                state.scheduled_fill_count += 1
                state.status = "TARGET_FILLED"
                filled_count += 1

        outcome = finalize_session_outcome(state, elig)
        session_outcomes.append(outcome)

    assert len(session_outcomes) == 40
    # In 40 days, approx 28-29 weekdays (eligible) and 11-12 weekend days (market closed)
    assert eligible_count >= 28, f"Expected at least 28 eligible weekdays, got {eligible_count}"
    assert filled_count >= 28, f"Expected all eligible weekdays to be filled, got {filled_count}"

    cadence = summarize_cadence(session_outcomes, calendar_days=40)
    assert cadence["executable_sessions"] == eligible_count
    assert cadence["sessions_with_fills"] == filled_count
    assert cadence["coverage_pct"] == 100.0, f"Expected 100% coverage across executable sessions, got {cadence['coverage_pct']}%"
