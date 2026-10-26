"""
test_v15_0_opportunity_funnel.py
V15.0 - Unit tests for Opportunity Funnel Tracking & Reporting (PHẦN 026 - 032).
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session
import models
from services.opportunity_funnel_service import OpportunityFunnelService
from services.scalp_m1_types import (
    ModelIdentifier,
    OpportunityEvaluation,
    OpportunityReasonCode,
    OpportunityStage,
)


def test_funnel_transition_idempotency_and_update(isolated_db: Session):
    """PHẦN 028, 029: Upsert transition is idempotent and deduplicated by (opportunity_id, stage)."""
    opp1 = OpportunityEvaluation(
        opportunity_id="opp_audit_01",
        symbol="XAUUSDT",
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
        direction="LONG",
        session_id="NY_20261015",
        zone_id="zone_01",
        as_of_ms=1000,
        stage=OpportunityStage.ZONE_DETECTED.value,
        reason_codes=(),
        market_event_received_at=1000,
    )

    # First record insert
    res1 = OpportunityFunnelService.record_opportunity_transition(isolated_db, opp1, now_ms=1000)
    assert res1.opportunity_id == "opp_audit_01"

    row1 = isolated_db.query(models.OpportunityFunnelRecord).filter(
        models.OpportunityFunnelRecord.opportunity_id == "opp_audit_01",
        models.OpportunityFunnelRecord.stage == OpportunityStage.ZONE_DETECTED.value,
    ).first()
    assert row1 is not None
    assert row1.created_at == 1000
    assert row1.updated_at == 1000

    # Second record with same opportunity & stage but updated timestamps
    opp1_update = OpportunityEvaluation(
        opportunity_id="opp_audit_01",
        symbol="XAUUSDT",
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
        direction="LONG",
        session_id="NY_20261015",
        zone_id="zone_01",
        as_of_ms=2000,
        stage=OpportunityStage.ZONE_DETECTED.value,
        reason_codes=(),
        market_event_received_at=1000,
    )
    OpportunityFunnelService.record_opportunity_transition(isolated_db, opp1_update, now_ms=2000)

    # Still exactly ONE row for this stage, updated_at refreshed
    rows = isolated_db.query(models.OpportunityFunnelRecord).filter(
        models.OpportunityFunnelRecord.opportunity_id == "opp_audit_01",
        models.OpportunityFunnelRecord.stage == OpportunityStage.ZONE_DETECTED.value,
    ).all()
    assert len(rows) == 1
    assert rows[0].created_at == 1000
    assert rows[0].updated_at == 2000

    # Moving to next stage: ZONE_TOUCHED creates a distinct record
    opp1_touched = OpportunityEvaluation(
        opportunity_id="opp_audit_01",
        symbol="XAUUSDT",
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
        direction="LONG",
        session_id="NY_20261015",
        zone_id="zone_01",
        as_of_ms=2500,
        stage=OpportunityStage.ZONE_TOUCHED.value,
        market_event_received_at=2500,
    )
    OpportunityFunnelService.record_opportunity_transition(isolated_db, opp1_touched, now_ms=2500)

    total_opp_rows = isolated_db.query(models.OpportunityFunnelRecord).filter(
        models.OpportunityFunnelRecord.opportunity_id == "opp_audit_01"
    ).all()
    assert len(total_opp_rows) == 2


def test_funnel_summary_pure_and_db(isolated_db: Session):
    """PHẦN 032: Statistical funnel summary with stage counts and top rejection reasons."""
    # Seed multiple transitions
    now = 1792000000000
    records = [
        OpportunityEvaluation(
            opportunity_id="opp_01", stage=OpportunityStage.ZONE_DETECTED.value,
            evaluation_started_at=now, evaluation_completed_at=now + 12,
        ),
        OpportunityEvaluation(
            opportunity_id="opp_01", stage=OpportunityStage.PRICE_NEAR.value,
            evaluation_started_at=now + 100, evaluation_completed_at=now + 115,
        ),
        OpportunityEvaluation(
            opportunity_id="opp_01", stage=OpportunityStage.ZONE_TOUCHED.value,
            evaluation_started_at=now + 200, evaluation_completed_at=now + 218,
        ),
        OpportunityEvaluation(
            opportunity_id="opp_01", stage=OpportunityStage.TRIGGER_CONFIRMED.value,
            evaluation_started_at=now + 300, evaluation_completed_at=now + 322,
        ),
        OpportunityEvaluation(
            opportunity_id="opp_01", stage=OpportunityStage.ARMED.value,
            evaluation_started_at=now + 400, evaluation_completed_at=now + 420,
        ),
        OpportunityEvaluation(
            opportunity_id="opp_02", stage=OpportunityStage.ZONE_DETECTED.value,
            reason_codes=(OpportunityReasonCode.WAITING_M1_STRUCTURE.value,),
            evaluation_started_at=now, evaluation_completed_at=now + 10,
        ),
        OpportunityEvaluation(
            opportunity_id="opp_02", stage=OpportunityStage.GUARD_BLOCKED.value,
            reason_codes=(OpportunityReasonCode.GUARD_BLOCKED.value,),
            evaluation_started_at=now + 500, evaluation_completed_at=now + 514,
        ),
    ]

    for r in records:
        OpportunityFunnelService.record_opportunity_transition(isolated_db, r, now_ms=now)
    isolated_db.commit()

    summary = OpportunityFunnelService.summarize_opportunity_funnel(isolated_db)
    assert summary["total_unique_opportunities"] == 2
    assert summary["zones_detected"] == 2
    assert summary["price_near"] == 1
    assert summary["zones_touched"] == 1
    assert summary["trigger_confirmed"] == 1
    assert summary["armed"] == 1
    assert summary["guard_blocked"] == 1

    # Top rejection reasons
    reasons = summary["top_rejection_reasons"]
    assert len(reasons) >= 1
    reason_codes = [r["code"] for r in reasons]
    assert OpportunityReasonCode.GUARD_BLOCKED.value in reason_codes

    # Latency percentiles
    lat = summary["latency_metrics"]
    assert lat["sample_count"] == 7
    assert lat["median_ms"] is not None
    assert lat["p95_ms"] is not None
    assert lat["median_ms"] > 0
