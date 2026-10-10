import os
import math
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

import schemas
from domain_calculator import CostAssumptions, calculate_risk_reward, validate_price_geometry
import lab.daily_research_scheduler as drs
from lab.ny_strategy_variants import NY_TZ
from lab.replay_engine import ReplayEngine

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def test_01_schema_contract_and_range_validation():
    # 1. Default entry_cadence
    req = schemas.ReplayRunRequest()
    assert req.entry_cadence == "CONFIRMED_ONLY"
    assert req.daily_min_fills_target == 1
    assert req.ny_max_fills == 3
    assert req.scheduled_deadline_hour == 14
    assert req.scheduled_deadline_minute == 30

    # 2. Daily paper entry
    req2 = schemas.ReplayRunRequest(
        entry_cadence="DAILY_PAPER",
        daily_min_fills_target=1,
        ny_max_fills=3,
        scheduled_deadline_hour=14,
        scheduled_deadline_minute=30
    )
    assert req2.entry_cadence == "DAILY_PAPER"

    # 3. Invalid start_date
    with pytest.raises(ValueError, match="Định dạng ngày bắt đầu không hợp lệ"):
        schemas.ReplayRunRequest(start_date="not-a-date")

    # 4. Invalid end_date
    with pytest.raises(ValueError, match="Định dạng ngày kết thúc không hợp lệ"):
        schemas.ReplayRunRequest(end_date="invalid")

    # 5. Invalid ny_max_fills > 3
    with pytest.raises(ValueError, match="ny_max_fills"):
        schemas.ReplayRunRequest(ny_max_fills=4)

    # 6. Invalid daily_min_fills_target > ny_max_fills
    with pytest.raises(ValueError, match="daily_min_fills_target"):
        schemas.ReplayRunRequest(ny_max_fills=2, daily_min_fills_target=3)

    # 7. Range resolver
    start_ts, end_ts, meta = drs.resolve_research_range(
        schemas.ReplayRunRequest(start_date="2026-06-01", end_date="2026-06-10")
    )
    assert start_ts < end_ts
    assert meta["start_date_vn"] == "2026-06-01"
    assert meta["end_date_vn"] == "2026-06-10"


def test_02_session_eligibility_and_scheduler_conditions():
    now_ms = 1783609200000

    # Weekend session
    el_weekend = drs.evaluate_session_eligibility(
        session_id="NY-2026-06-06",
        ny_date="2026-06-06",
        has_data=True,
        warmup_complete=True,
        is_weekend=True,
        now_ms=now_ms
    )
    assert not el_weekend.market_open
    assert not el_weekend.eligible
    assert "WEEKEND_MARKET_CLOSED" in el_weekend.reasons

    # Missing data session
    el_nodata = drs.evaluate_session_eligibility(
        session_id="NY-2026-06-08",
        ny_date="2026-06-08",
        has_data=False,
        warmup_complete=True,
        is_weekend=False,
        now_ms=now_ms
    )
    assert not el_nodata.data_complete
    assert not el_nodata.eligible
    assert "DATA_MISSING" in el_nodata.reasons

    # Eligible session
    el_ok = drs.evaluate_session_eligibility(
        session_id="NY-2026-06-08",
        ny_date="2026-06-08",
        has_data=True,
        warmup_complete=True,
        is_weekend=False,
        now_ms=now_ms
    )
    assert el_ok.eligible

    # should_schedule_daily_entry logic
    state = drs.SessionResearchState(
        session_id="NY-2026-06-08",
        date_ny="2026-06-08",
        trade_day_vn="2026-06-08",
        status="PREPARING"
    )
    cfg = schemas.ReplayRunRequest(entry_cadence="DAILY_PAPER")

    # Time at 14:35 NY (deadline reached)
    dt_ny_deadline = datetime(2026, 6, 8, 14, 35, 0, tzinfo=NY_TZ)
    ms_deadline = int(dt_ny_deadline.timestamp() * 1000)

    should_run, reason = drs.should_schedule_daily_entry(state, ms_deadline, cfg, el_ok)
    assert should_run
    assert reason == "DEADLINE_REACHED_ZERO_FILLS"

    # Already filled -> suppressed
    state.fills = 1
    should_run_filled, reason_filled = drs.should_schedule_daily_entry(state, ms_deadline, cfg, el_ok)
    assert not should_run_filled
    assert reason_filled == "TARGET_ALREADY_FILLED"

    # Before deadline (e.g. 10:00 NY) -> not yet
    state.fills = 0
    dt_ny_early = datetime(2026, 6, 8, 10, 0, 0, tzinfo=NY_TZ)
    ms_early = int(dt_ny_early.timestamp() * 1000)
    should_run_early, reason_early = drs.should_schedule_daily_entry(state, ms_early, cfg, el_ok)
    assert not should_run_early
    assert reason_early == "DEADLINE_NOT_YET_REACHED"

    # Active cooldown -> suppressed
    state.cooldown_until_ms = ms_deadline + 10000
    should_run_cool, reason_cool = drs.should_schedule_daily_entry(state, ms_deadline, cfg, el_ok)
    assert not should_run_cool
    assert reason_cool == "COOLDOWN_ACTIVE"


def test_03_direction_and_price_plan_building():
    # 1. Aligned HTF
    ctx_bullish = {
        "h1_trend": "BULLISH",
        "h4_bias": "BULLISH",
        "d_bias": "BULLISH",
        "recent_bars_15m": [{"open": 2300, "close": 2310, "high": 2315, "low": 2295}],
        "recent_bars_5m": []
    }
    cfg = schemas.ReplayRunRequest(entry_cadence="DAILY_PAPER")
    d, model, refs, missing = drs.choose_scheduled_direction(ctx_bullish, cfg)
    assert d == "LONG"
    assert "BULLISH" in model
    assert "SCHEDULED_ENTRY_AT_DEADLINE" in missing

    # 2. Bearish aligned
    ctx_bearish = {
        "h1_trend": "BEARISH",
        "h4_bias": "BEARISH",
        "d_bias": "BEARISH",
        "recent_bars_15m": [{"open": 2310, "close": 2300, "high": 2315, "low": 2295}],
        "recent_bars_5m": []
    }
    d_b, model_b, refs_b, missing_b = drs.choose_scheduled_direction(ctx_bearish, cfg)
    assert d_b == "SHORT"
    assert "BEARISH" in model_b

    # 3. Price plan building with Net RR >= 2.0R
    curr_quote = {
        "sim_time": 1783609200000,
        "close": 2350.0,
        "spread_usd": 0.35,
        "costs": CostAssumptions(taker_fee_rate=0.0004, slippage_usd=0.10),
        "capital": 1000.0
    }
    plan, err = drs.build_scheduled_price_plan("LONG", ctx_bullish, curr_quote, cfg)
    assert plan is not None, f"Failed to build plan: {err}"
    assert plan["direction"] == "LONG"
    assert plan["calc"].net_rr >= 2.0
    assert plan["calc"].quantity > 0
    assert plan["stop_loss"] < plan["entry_price"] < plan["take_profit"]

    # SHORT price plan
    plan_s, err_s = drs.build_scheduled_price_plan("SHORT", ctx_bearish, curr_quote, cfg)
    assert plan_s is not None, f"Failed to build short plan: {err_s}"
    assert plan_s["direction"] == "SHORT"
    assert plan_s["calc"].net_rr >= 2.0
    assert plan_s["take_profit"] < plan_s["entry_price"] < plan_s["stop_loss"]

    # 4. evaluate_scheduled_entry composition
    state = drs.SessionResearchState(session_id="NY-2026-06-08", date_ny="2026-06-08", trade_day_vn="2026-06-08")
    candidate, rejections = drs.evaluate_scheduled_entry(ctx_bullish, state, cfg, curr_quote)
    assert candidate is not None
    assert candidate["entry_type"] == "SMC_CONTEXT_SCHEDULED_PAPER"
    assert candidate["confidence_kind"] == "HEURISTIC"
    assert "SCHEDULED_ENTRY_AT_DEADLINE" in candidate["missing_confirmations"]


def test_04_40_session_cadence_summary():
    # Construct 40 synthetic session outcomes
    outcomes = []
    for i in range(40):
        outcomes.append({
            "session_id": f"NY-2026-06-{i+1:02d}",
            "date_ny": f"2026-06-{i+1:02d}",
            "market_open": True,
            "data_complete": True,
            "eligible": True,
            "status": "TARGET_FILLED",
            "outcome_category": "TARGET_ACHIEVED",
            "fills_count": 1,
            "confirmed_fills": 0,
            "scheduled_fills": 1
        })

    summary = drs.summarize_cadence(outcomes)
    assert summary["calendar_days"] == 40
    assert summary["executable_sessions"] == 40
    assert summary["sessions_with_fills"] == 40
    assert summary["coverage_pct"] == 100.0
    assert summary["scheduled_fill_sessions"] == 40
    assert summary["unmet_sessions"] == 0


def test_05_hard_guards_priority_and_caps():
    # Verify that consecutive loss and daily loss limit stop scheduled entries
    state = drs.SessionResearchState(
        session_id="NY-2026-06-08",
        date_ny="2026-06-08",
        trade_day_vn="2026-06-08",
        status="PREPARING"
    )
    el = drs.SessionEligibility(
        session_id="NY-2026-06-08",
        ny_date="2026-06-08",
        market_open=True,
        data_complete=True,
        warmup_complete=True,
        execution_data_valid=True,
        eligible=True
    )
    dt_ny = datetime(2026, 6, 8, 14, 30, tzinfo=NY_TZ)
    ms_deadline = int(dt_ny.timestamp() * 1000)

    # 1. When consecutive losses is simulated as >= 2, final outcome is RISK_STOP
    state.block_reason = "RISK_CONSECUTIVE_LOSS_LIMIT"
    outcome = drs.finalize_session_outcome(state, el)
    assert outcome["outcome_category"] == "RISK_STOP"
    assert outcome["primary_reason"] == "RISK_CONSECUTIVE_LOSS_LIMIT"

    # 2. When fills == 1, target achieved
    state.fills = 1
    state.confirmed_fill_count = 1
    state.status = "TARGET_FILLED"
    outcome_met = drs.finalize_session_outcome(state, el)
    assert outcome_met["outcome_category"] == "TARGET_ACHIEVED"
    assert outcome_met["fills_count"] == 1


def test_06_empirical_3m_daily_paper_scheduler():
    # E02: Full 3-month empirical verification of DAILY_PAPER scheduling on Bitget XAUUSDT:
    # - >30 fills across >30 executable sessions (targeting 1 fill per executable NY session, max 3 fills/day)
    # - Distinct labels: SMC_CONFIRMED and SMC_CONTEXT_SCHEDULED_PAPER
    # - Cadence coverage >= 80%
    start_ts = 1783609200000
    end_ts = 1791558000000

    req = schemas.ReplayRunRequest(
        run_name="test_v13_3_daily_paper_3m",
        symbol="XAUUSDT",
        start_ts=start_ts,
        end_ts=end_ts,
        initial_equity=1000.0,
        risk_pct=0.5,
        quota_risk_pct=0.10,
        leverage=30,
        strategy_variant="NY_ADAPTIVE",
        entry_cadence="DAILY_PAPER",
        ny_max_fills=3,
        daily_min_fills_target=1,
        scheduled_deadline_hour=14,
        scheduled_deadline_minute=30,
        include_5m=True,
        use_5m_driver=True,
        mode="HISTORICAL_MARKET"
    )

    res = ReplayEngine.run_replay(req)

    # 1. Total fills and trades > 30
    assert res.fills_count > 30, f"Expected >30 fills across 3 months, got {res.fills_count}"
    assert res.total_trades > 30, f"Expected >30 closed trades, got {res.total_trades}"

    # 2. Distinct trade types
    assert res.trade_type_breakdown is not None
    confirmed_cnt = res.trade_type_breakdown.get("SMC_CONFIRMED", 0)
    scheduled_cnt = res.trade_type_breakdown.get("SMC_CONTEXT_SCHEDULED_PAPER", 0)
    assert confirmed_cnt > 0, "Expected at least 1 confirmed setup"
    assert scheduled_cnt > 30, f"Expected >30 scheduled paper fills, got {scheduled_cnt}"

    # 3. Cadence summary coverage
    assert res.cadence_summary is not None
    cs = res.cadence_summary
    assert cs["executable_sessions"] >= 60, f"Expected >=60 executable sessions, got {cs['executable_sessions']}"
    assert cs["sessions_with_fills"] > 30, f"Expected >30 sessions with fills, got {cs['sessions_with_fills']}"
    assert cs["coverage_pct"] >= 80.0, f"Expected >=80% coverage, got {cs['coverage_pct']}%"

    # 4. Strict daily cap <= 3
    daily_list = res.session_breakdown.get("daily_stats_list", [])
    assert len(daily_list) > 0, "Expected daily_stats_list to be populated"
    for day in daily_list:
        assert day.get("fills", 0) <= 3, f"Day {day.get('date')} exceeded max 3 fills cap: {day.get('fills')}"

    # 5. Missing confirmations recorded on all scheduled trades
    scheduled_trades = [t for t in res.trades if t.entry_type == "SMC_CONTEXT_SCHEDULED_PAPER"]
    for t in scheduled_trades:
        assert t.missing_confirmations is not None
        assert len(t.missing_confirmations) > 0
        assert t.confidence_kind == "HEURISTIC"
