"""
backend/tests/test_v17_6_1_run_isolation.py
V17.6.1 OBS-05: Strict Observation Run and Symbol Isolation Tests.
"""

import pytest
import json
from services.opportunity_funnel_service import OpportunityFunnelService
from services.engine_observability_service import engine_observability_service
import models


def test_iso_01_funnel_summary_isolated_by_run_id(isolated_db):
    """OBS-05: Funnel counts are strictly isolated by run_id."""
    now_ms = 1792591200000

    # Create records for Run A
    rec_a1 = models.OpportunityFunnelRecord(
        opportunity_id="opp_a1",
        symbol="XAUUSDT",
        model_id="m_scalp_m1",
        direction="BULLISH",
        session_id="sess_a",
        run_id="run-AAA",
        stage="ZONE_DETECTED",
        as_of_ms=now_ms - 5000,
        payload_json=json.dumps({"stage": "ZONE_DETECTED"}),
        created_at=now_ms - 5000,
        updated_at=now_ms - 5000,
    )
    rec_a2 = models.OpportunityFunnelRecord(
        opportunity_id="opp_a1",
        symbol="XAUUSDT",
        model_id="m_scalp_m1",
        direction="BULLISH",
        session_id="sess_a",
        run_id="run-AAA",
        stage="ARMED",
        as_of_ms=now_ms - 3000,
        payload_json=json.dumps({"stage": "ARMED"}),
        created_at=now_ms - 3000,
        updated_at=now_ms - 3000,
    )

    # Create records for Run B
    rec_b1 = models.OpportunityFunnelRecord(
        opportunity_id="opp_b1",
        symbol="XAUUSDT",
        model_id="m_scalp_m1",
        direction="BEARISH",
        session_id="sess_b",
        run_id="run-BBB",
        stage="ZONE_DETECTED",
        as_of_ms=now_ms - 4000,
        payload_json=json.dumps({"stage": "ZONE_DETECTED"}),
        created_at=now_ms - 4000,
        updated_at=now_ms - 4000,
    )

    isolated_db.add_all([rec_a1, rec_a2, rec_b1])
    isolated_db.commit()

    summary_a = OpportunityFunnelService.summarize_opportunity_funnel(
        isolated_db,
        symbol="XAUUSDT",
        run_id="run-AAA",
    )
    assert summary_a["zones_detected"] == 1
    assert summary_a["armed"] == 1

    summary_b = OpportunityFunnelService.summarize_opportunity_funnel(
        isolated_db,
        symbol="XAUUSDT",
        run_id="run-BBB",
    )
    assert summary_b["zones_detected"] == 1
    assert summary_b["armed"] == 0  # Run B has 0 armed setups!


def test_iso_02_waiting_setups_isolated_by_run_id(isolated_db):
    """OBS-05: Waiting setups query in dashboard snapshot does not leak older or other runs' setups."""
    now_ms = 1792591200000

    # Run A waiting setup
    ev_a = models.OpportunityDecisionEvent(
        event_id="dev-a1",
        run_id="run-ALPHA",
        opportunity_id="opp-alpha",
        model_id="m_scalp_m1",
        direction="BULLISH",
        stage="WAITING",
        occurred_at_ms=now_ms - 1000,
        created_at_ms=now_ms - 1000,
        payload_json=json.dumps({"wait_reason": "WAITING_LIQUIDITY_SWEEP"}),
    )

    # Run B waiting setup
    ev_b = models.OpportunityDecisionEvent(
        event_id="dev-b1",
        run_id="run-BETA",
        opportunity_id="opp-beta",
        model_id="m_scalp_m1",
        direction="BEARISH",
        stage="WAITING",
        occurred_at_ms=now_ms - 500,
        created_at_ms=now_ms - 500,
        payload_json=json.dumps({"wait_reason": "WAITING_BOS_CONFIRMATION"}),
    )

    isolated_db.add_all([ev_a, ev_b])
    isolated_db.commit()

    dash_a = engine_observability_service.build_observation_dashboard_snapshot(
        isolated_db,
        symbol="XAUUSDT",
        run_id="run-ALPHA",
        now_ms=now_ms,
    )
    waiting_a = dash_a.get("waiting_setups", [])
    assert len(waiting_a) == 1
    assert waiting_a[0]["opportunity_id"] == "opp-alpha"

    dash_b = engine_observability_service.build_observation_dashboard_snapshot(
        isolated_db,
        symbol="XAUUSDT",
        run_id="run-BETA",
        now_ms=now_ms,
    )
    waiting_b = dash_b.get("waiting_setups", [])
    assert len(waiting_b) == 1
    assert waiting_b[0]["opportunity_id"] == "opp-beta"


def test_iso_03_blocked_categories_isolated_by_run_id(isolated_db):
    """OBS-05: Blocked categories in dashboard snapshot are strictly isolated by run_id."""
    now_ms = 1792591200000

    ev_a_risk = models.OpportunityDecisionEvent(
        event_id="dev-risk-a",
        run_id="run-ONE",
        opportunity_id="opp-1",
        model_id="m_scalp_m1",
        direction="BULLISH",
        stage="BLOCKED",
        reason_category="RISK",
        occurred_at_ms=now_ms - 1000,
        created_at_ms=now_ms - 1000,
    )

    ev_b_news = models.OpportunityDecisionEvent(
        event_id="dev-news-b",
        run_id="run-TWO",
        opportunity_id="opp-2",
        model_id="m_scalp_m1",
        direction="BEARISH",
        stage="BLOCKED",
        reason_category="NEWS",
        occurred_at_ms=now_ms - 500,
        created_at_ms=now_ms - 500,
    )

    isolated_db.add_all([ev_a_risk, ev_b_news])
    isolated_db.commit()

    dash_1 = engine_observability_service.build_observation_dashboard_snapshot(
        isolated_db,
        symbol="XAUUSDT",
        run_id="run-ONE",
        now_ms=now_ms,
    )
    cats_1 = [c["category"] for c in dash_1.get("top_blocked_categories", [])]
    assert "RISK" in cats_1
    assert "NEWS" not in cats_1


def test_iso_04_coverage_clipped_to_run_lifespan(isolated_db):
    """OBS-06: Observation coverage window is clipped to run start and end times."""
    now_ms = 1792591200000
    run_start = now_ms - (4 * 3600 * 1000)  # 4 hours ago
    run_end = now_ms - (1 * 3600 * 1000)    # 1 hour ago (duration 3 hours)

    run = models.PaperResearchRunRecord(
        run_id="run-CLIPPED",
        symbol="XAUUSDT",
        scope="CG01",
        status="COMPLETED",
        started_at_ms=run_start,
        ended_at_ms=run_end,
        created_at_ms=run_start,
        updated_at_ms=run_end,
    )
    isolated_db.add(run)
    isolated_db.commit()

    # Pass 24-hour broad window
    wide_start = now_ms - (24 * 3600 * 1000)
    wide_end = now_ms

    cov = engine_observability_service.calculate_observation_coverage(
        isolated_db,
        run_id="run-CLIPPED",
        start_ms=wide_start,
        end_ms=wide_end,
    )

    # Expected clipped total_span_hours is ~3.0 hours, NOT 24.0 hours!
    assert cov["total_span_hours"] == 3.0
