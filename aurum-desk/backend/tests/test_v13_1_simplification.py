"""
Aurum Desk V13.1 — Acceptance & Regression Tests for Simplification and Causal Corrections.
Covers:
- Section 6: No misleading default fallbacks (null quality_score, no fake 80%, no fake PROVIDED_VALIDATED)
- Section 7: No fake candles at 2650, no slicing current data for 3 months ago
- Section 8: Today's explicit time_of_day is respected (as_of != now_ms)
- Section 9: Case A (not triggered -> 0 MFE, no win) and Case B (SL hit -> stops at SL, no future surge MFE)
- Section 10: Swing TP is not artificially stretched to force 1.8R
"""

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient

from database import SessionLocal
import models
import crud
from main import app
from research_context import build_research_context, ResearchMode
from research_engine import generate_research_report, CandleObj
from research_review import evaluate_scenario_outcome_and_mfe_mae
from scenario_builder import build_validated_scenarios

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NY_TZ = ZoneInfo("America/New_York")


@pytest.fixture
def client():
    return TestClient(app)


def test_section6_no_misleading_default_fallbacks(client):
    """Verify that reports with null quality_score/data_coverage do NOT fallback to 0.8 or PROVIDED_VALIDATED."""
    db = SessionLocal()
    try:
        # Create a report with explicit None for quality_score
        rep = models.ResearchReport(
            report_type="SESSION_REPORT",
            created_at=1780000000000,
            session_name="New York (Mỹ)",
            d_4h_bias="BEARISH",
            h1_alignment="NEUTRAL",
            content_markdown="Test report",
            strategy_version="1.0.0",
            research_date="2026-05-01",
            market_regime=None,
            data_coverage_status=None,
            quality_score=None
        )
        db.add(rep)
        db.commit()
        db.refresh(rep)
        rep_id = rep.id

        resp = client.get(f"/api/v1/reports/{rep_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["quality_score"] is None, "quality_score must remain None, not fallback to 0.8 / 80%!"
        assert data["data_coverage_status"] == "CHƯA_KIỂM_ĐỊNH", "data_coverage_status must not fallback to PROVIDED_VALIDATED!"
        assert data["market_regime"] == "CHƯA_XÁC_ĐỊNH", "market_regime must not fallback to XU HƯỚNG BÌNH THƯỜNG!"
    finally:
        db.close()


def test_section7_insufficient_data_does_not_create_fake_candles():
    """Verify that when no candles exist before as_of, report is created with DATA_UNAVAILABLE and zero fake candles."""
    db = SessionLocal()
    try:
        # Query date in far past where DB has no candles
        ctx = build_research_context(
            selected_date="2020-01-01",
            time_of_day="08:30",
            session="NEW_YORK",
            now_ms=1785000000000
        )
        report = generate_research_report(
            db=db,
            candles_15m=[], # Empty candles
            context=ctx
        )
        assert report.data_coverage_status == "DATA_UNAVAILABLE"
        assert report.quality_score is None, "Quality score must be None when no data is available!"
        assert report.market_regime == "CHƯA_RÕ"
        assert "no_trade" in report.scenarios
        import json
        prov = json.loads(report.provenance_metadata)
        assert any("KHÔNG_ĐỦ_NẾN" in str(lim) for lim in prov.get("limitations", []))
    finally:
        db.close()


def test_section8_today_explicit_time_respected():
    """Verify that when user picks today with 08:30, as_of is 08:30 and not overwritten with now_ms."""
    # Set now to 14:00 VN time (afternoon)
    now_dt = datetime(2026, 7, 26, 14, 0, 0, tzinfo=VN_TZ)
    now_ms = int(now_dt.astimezone(ZoneInfo("UTC")).timestamp() * 1000)
    today_str = now_dt.strftime("%Y-%m-%d")

    ctx = build_research_context(
        selected_date=today_str,
        time_of_day="08:30",
        date_basis="VN_DATE",
        now_ms=now_ms
    )
    as_of_vn = ctx.as_of_dt_vn()
    assert as_of_vn.hour == 8
    assert as_of_vn.minute == 30
    assert ctx.as_of_ms != now_ms, "as_of_ms must not be overwritten with now_ms when explicit time_of_day is provided!"


def test_section9_case_a_unfilled_entry_never_counts_as_win():
    """Case A: LONG entry 100, SL 90, TP 120. Price only moves 125-130. Never hits entry -> NOT_TRIGGERED."""
    scenario = {
        "direction": "LONG",
        "planned_entry": 100.0,
        "stop_loss": 90.0,
        "take_profit": 120.0
    }
    candles = [
        {"timestamp": 1000, "open": 125.0, "high": 128.0, "low": 124.0, "close": 127.0},
        {"timestamp": 2000, "open": 127.0, "high": 130.0, "low": 125.0, "close": 129.0},
        {"timestamp": 3000, "open": 129.0, "high": 135.0, "low": 128.0, "close": 134.0}
    ]
    res = evaluate_scenario_outcome_and_mfe_mae(scenario, candles)
    assert res["status"] == "NOT_TRIGGERED"
    assert res["is_filled"] is False
    assert res["mfe_usd"] == 0.0
    assert res["mae_usd"] == 0.0
    assert res["candidate_lesson"] is None, "Must not propose trade lesson for an unfilled order!"


def test_section9_case_b_sl_termination_prevents_future_surge_leakage():
    """Case B: LONG entry 100, SL 90, TP 120. Candle 1 drops to 89 (SL hit). Candle 2 surges to 150.
    Evaluation must terminate upon hitting SL and NOT count candle 2 as MFE."""
    scenario = {
        "direction": "LONG",
        "planned_entry": 100.0,
        "stop_loss": 90.0,
        "take_profit": 120.0
    }
    candles = [
        {"timestamp": 1000, "open": 100.0, "high": 102.0, "low": 89.0, "close": 90.0},
        {"timestamp": 2000, "open": 90.0, "high": 150.0, "low": 90.0, "close": 149.0}
    ]
    res = evaluate_scenario_outcome_and_mfe_mae(scenario, candles)
    assert res["status"] == "CLOSED_SL"
    assert res["is_filled"] is True
    assert res["mfe_usd"] == 2.0, f"Future surge leaked into MFE! Got {res['mfe_usd']}"
    assert res["mae_usd"] == 11.0 # 100 - 89


def test_section10_structural_tp_not_artificially_stretched():
    """Verify that when swing_high is too close (< 1.80R), TP is NOT stretched to 2.2*risk to force pass."""
    scenarios = build_validated_scenarios(
        current_price=2650.0,
        atr_val=4.0,
        swing_high=2655.0,
        swing_low=2640.0,
        equilibrium=2647.5,
        regime="TREND_UP"
    )
    long_sc = scenarios["bullish"]
    assert long_sc["is_valid"] is False, "Must mark invalid when structural Swing High does not provide Net RR >= 1.80R!"
    assert long_sc["setup_state"] == "CHƯA_HỢP_LỆ"
    assert long_sc["take_profit"] == 2655.0, "TP must reflect real Swing High, not be stretched to 2670+!"
