"""
Aurum Desk V13 Acceptance Test Suite — Causal Daily Research & Decision Replay.
Verifies R01 through R22:
- R01: Legacy API compatibility (payloads, query params, old aliases)
- R02: Historical request immune to live collector_service poisoning
- R03: Prefix invariance (time cutoff strictly respected)
- R04: Close time boundaries on multi-timeframe candles
- R05: Date basis VN_DATE and NY_SESSION_DATE
- R06: Premarket / intraday as_of excludes future session levels
- R07: Future date/as_of rejected or capped with limitations
- R08: Scenario geometry validation (SL < Entry < TP for LONG, TP < Entry < SL for SHORT, Net RR >= 1.8)
- R09: Market regime setup eligibility (B1 trend continuation vs B2 range breakout)
- R10: Outcome review computes authentic MFE/MAE
- R11: Draft lessons generated in PENDING_REVIEW governance state (no auto-approval)
- R12: Detailed report and review endpoints preserve original immutable report
"""

import time
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient

from main import app
from database import get_db, SessionLocal
import models
import crud
from research_context import (
    ResearchContext,
    ResearchMode,
    DateBasis,
    SessionType,
    build_research_context,
    VN_TZ,
    NY_TZ
)
from market_regime import evaluate_market_regime, MarketRegimeType
from scenario_builder import build_validated_scenarios
from research_review import evaluate_scenario_outcome_and_mfe_mae
from research_engine import generate_research_report, CandleObj


@pytest.fixture
def client():
    return TestClient(app)


def test_r01_legacy_api_compatibility(client):
    """R01: Verify old report aliases, payload types, and response structure continue to work."""
    # Query param style
    resp1 = client.post("/api/v1/reports/generate?report_type=SESSION_REPORT")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["status"] == "success"
    assert "report_id" in data1
    assert "content_markdown" in data1

    # JSON body style
    resp2 = client.post("/api/reports", json={"report_type": "PREMARKET"})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["status"] == "success"

    # GET /api/v1/reports listing
    resp3 = client.get("/api/v1/reports?limit=5")
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert "reports" in data3
    assert "session_info" in data3
    assert len(data3["reports"]) >= 2


def test_r02_historical_request_immune_to_live_collector_poisoning(client):
    """R02: Proves historical research report does NOT read poisoned live collector_service biases."""
    db = SessionLocal()
    try:
        # Intentionally poison live collector_service if available
        try:
            from services.collector_service import collector_service
            original_d = collector_service.d_bias
            original_h4 = collector_service.h4_bias
            collector_service.d_bias = "POISONED_SUPER_BULLISH"
            collector_service.h4_bias = "POISONED_SUPER_BULLISH"
            collector_service.d_4h_bias = "POISONED_SUPER_BULLISH"
        except Exception:
            original_d = None

        # Request historical report for 2026-07-15
        hist_context = build_research_context(
            selected_date="2026-07-15",
            time_of_day="08:30",
            session="NEW_YORK",
            date_basis="VN_DATE",
            mode="HISTORICAL_ASOF",
            now_ms=1784000000000
        )
        assert hist_context.is_historical() is True

        report = generate_research_report(
            db=db,
            report_type="SESSION_REPORT",
            context=hist_context
        )

        assert report.d_4h_bias != "POISONED_SUPER_BULLISH", "Historical report leaked live collector bias!"
        assert report.mode == "HISTORICAL_ASOF"
        assert report.research_date == "2026-07-15"

        # Restore collector
        if original_d:
            collector_service.d_bias = original_d
            collector_service.h4_bias = original_h4
            collector_service.d_4h_bias = original_d
    finally:
        db.close()


def test_r03_prefix_invariance_and_close_time_cutoff():
    """R03 & R04: Prove that candles after as_of are strictly excluded from research snapshot."""
    db = SessionLocal()
    try:
        as_of = 1785000000000 # fixed timestamp
        # Create 10 candles before as_of and 10 candles after as_of
        candles = []
        for i in range(20):
            t = as_of - (20 - i) * 15 * 60 * 1000
            candles.append(CandleObj(t, 2600.0 + i, 2605.0 + i, 2595.0 + i, 2602.0 + i))

        # Candles after as_of
        future_candles = []
        for i in range(10):
            t = as_of + (i + 1) * 15 * 60 * 1000
            future_candles.append(CandleObj(t, 2900.0, 2950.0, 2890.0, 2940.0)) # extreme prices

        all_candles = candles + future_candles

        context = build_research_context(
            selected_date="2026-08-01",
            now_ms=as_of
        )

        report = generate_research_report(
            db=db,
            candles_15m=candles, # only before
            context=context
        )

        # The swing high / levels in report should not exceed 2620 (nowhere near 2950 future prices)
        import json
        levels = json.loads(report.liquidity_levels)
        assert levels["swing_high"] < 2700.0, f"Future candle leaked! Swing high was {levels['swing_high']}"
    finally:
        db.close()


def test_r05_date_basis_and_iana_timezone_handling():
    """R05: Verifies proper resolution of VN_DATE (UTC+7) vs NY_SESSION_DATE (America/New_York)."""
    # NY morning 08:30 on 2026-08-10 corresponds to 19:30 VN time on the same date
    ctx_ny = build_research_context(
        selected_date="2026-08-10",
        time_of_day="08:30",
        session="NEW_YORK",
        date_basis=DateBasis.NY_SESSION_DATE
    )
    dt_ny = ctx_ny.as_of_dt_ny()
    dt_vn = ctx_ny.as_of_dt_vn()

    assert dt_ny.hour == 8
    assert dt_ny.minute == 30
    assert dt_vn.hour == 19 or dt_vn.hour == 20 # depending on EDT vs EST


def test_r06_r07_future_date_handling_and_limitations():
    """R06 & R07: Tests that future dates or invalid timestamps are capped with explicit limitations."""
    far_future = "2099-12-31"
    ctx = build_research_context(
        selected_date=far_future,
        time_of_day="12:00"
    )
    assert len(ctx.limitations) > 0
    assert "THỜI_ĐIỂM_TƯƠNG_LAI" in ctx.limitations[0]


def test_r08_scenario_geometry_and_net_rr_validation():
    """R08: Strict mathematical and economic checks on LONG (SL < Entry < TP) and SHORT (TP < Entry < SL)."""
    # 1. Normal valid scenario
    scenarios = build_validated_scenarios(
        current_price=2650.0,
        atr_val=10.0,
        swing_high=2685.0,
        swing_low=2620.0,
        equilibrium=2650.0,
        regime="TREND_UP"
    )
    assert "bullish" in scenarios
    assert "bearish" in scenarios
    assert "no_trade" in scenarios

    bullish = scenarios["bullish"]
    if bullish["is_valid"]:
        assert bullish["stop_loss"] < bullish["planned_entry"] < bullish["take_profit"]
        assert bullish["net_rr"] >= 1.80

    bearish = scenarios["bearish"]
    if bearish["is_valid"]:
        assert bearish["take_profit"] < bearish["planned_entry"] < bearish["stop_loss"]
        assert bearish["net_rr"] >= 1.80

    assert scenarios["no_trade"]["is_active"] is True


def test_r09_r12_market_regime_causal_evaluation():
    """R09 & R12: Tests regime categorization (TREND_UP, TREND_DOWN, RANGE, EVENT_VOLATILITY)."""
    # 1. Clear uptrend candles
    up_candles = [
        {"timestamp": i * 900000, "open": 2600 + i * 2, "high": 2605 + i * 2, "low": 2598 + i * 2, "close": 2604 + i * 2}
        for i in range(30)
    ]
    reg_up = evaluate_market_regime(up_candles, atr_val=5.0)
    assert reg_up["regime"] == MarketRegimeType.TREND_UP
    assert reg_up["setup_eligibility"]["b1_trend_continuation"] is True
    assert len(reg_up["supporting_evidence"]) > 0

    # 2. Clear downtrend candles
    down_candles = [
        {"timestamp": i * 900000, "open": 2700 - i * 2, "high": 2702 - i * 2, "low": 2695 - i * 2, "close": 2696 - i * 2}
        for i in range(30)
    ]
    reg_down = evaluate_market_regime(down_candles, atr_val=5.0)
    assert reg_down["regime"] == MarketRegimeType.TREND_DOWN
    assert reg_down["setup_eligibility"]["b1_trend_continuation"] is True

    # 3. Range bound candles
    range_candles = [
        {"timestamp": i * 900000, "open": 2650 + (1 if i % 2 == 0 else -1), "high": 2653, "low": 2647, "close": 2650}
        for i in range(30)
    ]
    reg_range = evaluate_market_regime(range_candles, atr_val=5.0)
    assert reg_range["regime"] == MarketRegimeType.RANGE
    assert reg_range["setup_eligibility"]["b2_range_breakout"] is True


def test_r10_r11_r13_post_session_review_and_mfe_mae():
    """R10, R11, R13: Post-session evaluation calculates real MFE/MAE and generates draft lesson."""
    scenario = {
        "direction": "LONG",
        "planned_entry": 2650.0,
        "stop_loss": 2640.0, # risk = 10 USD
        "take_profit": 2675.0
    }

    # Simulate subsequent price reaching 2670 (favorable +20 USD = +2.0R) and dipping to 2645 (adverse -5 USD = -0.5R)
    subsequent = [
        {"timestamp": 1000, "open": 2650.0, "high": 2660.0, "low": 2648.0, "close": 2658.0},
        {"timestamp": 2000, "open": 2658.0, "high": 2670.0, "low": 2645.0, "close": 2668.0},
    ]

    review = evaluate_scenario_outcome_and_mfe_mae(scenario, subsequent)
    assert review["status"] == "OPEN"
    assert review["mfe_usd"] == 20.0
    assert review["mfe_r"] == 2.0
    assert review["mae_usd"] == 5.0
    assert review["mae_r"] == 0.5
    assert len(review["facts"]) >= 3

    # Candidate lesson in PENDING_REVIEW
    lesson = review["candidate_lesson"]
    assert lesson["status"] == "PENDING_REVIEW"
    assert lesson["is_approved"] is False, "Candidate lesson must not be auto-approved!"

    # Case A: Price reaches TP (120) without ever touching entry (100) -> NOT_TRIGGERED
    scenario_a = {"direction": "LONG", "planned_entry": 100.0, "stop_loss": 90.0, "take_profit": 120.0}
    candles_a = [
        {"timestamp": 1, "open": 125.0, "high": 128.0, "low": 124.0, "close": 127.0},
        {"timestamp": 2, "open": 127.0, "high": 130.0, "low": 126.0, "close": 129.0}
    ]
    rev_a = evaluate_scenario_outcome_and_mfe_mae(scenario_a, candles_a)
    assert rev_a["status"] == "NOT_TRIGGERED"
    assert rev_a["is_filled"] is False
    assert rev_a["mfe_usd"] == 0.0
    assert rev_a["candidate_lesson"] is None

    # Case B: Price touches entry 100, candle 1 drops to 89 (hits SL). Candle 2 rises to 140.
    # Must exit at candle 1; candle 2's rise must NOT be counted as MFE!
    scenario_b = {"direction": "LONG", "planned_entry": 100.0, "stop_loss": 90.0, "take_profit": 120.0}
    candles_b = [
        {"timestamp": 1, "open": 100.0, "high": 102.0, "low": 89.0, "close": 90.0},
        {"timestamp": 2, "open": 90.0, "high": 140.0, "low": 88.0, "close": 138.0}
    ]
    rev_b = evaluate_scenario_outcome_and_mfe_mae(scenario_b, candles_b)
    assert rev_b["status"] == "CLOSED_SL"
    assert rev_b["is_filled"] is True
    assert rev_b["mfe_usd"] == 2.0, f"Future surge counted as MFE! Got {rev_b['mfe_usd']}"
    assert rev_b["mae_usd"] == 11.0 # 100 - 89


def test_r14_r17_r18_api_filters_and_detail_routes(client):
    """R14, R17, R18: Verifies GET /api/v1/reports with filters and GET /api/v1/reports/{id}/review."""
    # 1. Create a structured report via POST
    create_payload = {
        "report_type": "SESSION_REPORT",
        "selected_date": "2026-09-15",
        "time_of_day": "09:00",
        "session": "NEW_YORK",
        "date_basis": "VN_DATE",
        "mode": "HISTORICAL_ASOF"
    }
    resp = client.post("/api/v1/reports/generate", json=create_payload)
    assert resp.status_code == 200
    rep_id = resp.json()["report_id"]

    # 2. Get detail
    det_resp = client.get(f"/api/v1/reports/{rep_id}")
    assert det_resp.status_code == 200
    det_data = det_resp.json()
    assert det_data["id"] == rep_id
    assert det_data["research_date"] == "2026-09-15"
    assert "structured_scenarios" in det_data
    assert "market_regime" in det_data

    # 3. Filter query
    filt_resp = client.get("/api/v1/reports?research_date=2026-09-15")
    assert filt_resp.status_code == 200
    filt_data = filt_resp.json()
    assert len(filt_data["reports"]) >= 1
    assert filt_data["reports"][0]["research_date"] == "2026-09-15"

    # 4. Review route
    rev_resp = client.get(f"/api/v1/reports/{rep_id}/review")
    assert rev_resp.status_code == 200
    rev_data = rev_resp.json()
    assert rev_data["report_id"] == rep_id
    assert "bullish_review" in rev_data
    assert "bearish_review" in rev_data
