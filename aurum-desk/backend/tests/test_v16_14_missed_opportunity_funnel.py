"""
backend/tests/test_v16_14_missed_opportunity_funnel.py
V16.14 Automated Tests for Opportunity Funnel and Missed Opportunity Audit.

Covers:
- Rules 091-100: Funnel progression, additive stages, and reason classification.
- Rule 094: Separation of intentional POLICY_BLOCKED from technical misses.
- Rule 097: ENTRY_NEVER_TOUCHED when armed order expires without market retracing to entry.
- Rules 098, 180: Causal sequential audit without hindsight cherry-picking.
- Rules 101-110: audit_run report, top bottlenecks, Long/Short representative examples.
"""
import pytest
from services.cg01_missed_audit_service import (
    CG01MissedOpportunityAuditService,
    OpportunityAuditRecord,
)
from services.scalp_m1_types import OpportunityReasonCode, OpportunityStage


# =========================================================================
# 1. Additive Opportunity Stages and Classification (Rules 091-097)
# =========================================================================
def test_opportunity_stages_progression_additive_values():
    """
    Rule 092: Additive stages exist without breaking legacy enum values.
    """
    assert OpportunityStage.POI_WATCHED.value == "POI_WATCHED"
    assert OpportunityStage.POI_APPROACHED.value == "POI_APPROACHED"
    assert OpportunityStage.SWEPT.value == "SWEPT"
    assert OpportunityStage.MSS_CONFIRMED.value == "MSS_CONFIRMED"
    assert OpportunityStage.FVG_CONFIRMED.value == "FVG_CONFIRMED"
    assert OpportunityStage.ENTRY_READY.value == "ENTRY_READY"
    assert OpportunityStage.TERMINAL.value == "TERMINAL"


def test_classify_filled_opportunity():
    """
    A setup that reached filled state is correctly classified as FILLED with zero miss reason.
    """
    eval_records = [
        {"opportunity_id": "opp_01", "as_of_ms": 1000, "stage": OpportunityStage.POI_WATCHED.value, "symbol": "XAUUSDT", "direction": "LONG"},
        {"opportunity_id": "opp_01", "as_of_ms": 2000, "stage": OpportunityStage.SWEPT.value, "symbol": "XAUUSDT", "direction": "LONG"},
        {"opportunity_id": "opp_01", "as_of_ms": 3000, "stage": OpportunityStage.MSS_CONFIRMED.value, "symbol": "XAUUSDT", "direction": "LONG"},
        {"opportunity_id": "opp_01", "as_of_ms": 4000, "stage": OpportunityStage.ARMED.value, "symbol": "XAUUSDT", "direction": "LONG"},
        {"opportunity_id": "opp_01", "as_of_ms": 5000, "stage": OpportunityStage.FILLED.value, "symbol": "XAUUSDT", "direction": "LONG"},
    ]
    res = CG01MissedOpportunityAuditService.classify_opportunity(eval_records)
    assert res.classification == "FILLED"
    assert res.miss_reason is None
    assert res.terminal_stage == "FILLED"


def test_rule_094_policy_blocked_is_not_labeled_as_technical_miss():
    """
    Rule 094: An opportunity blocked by protective guards (news blackout, active position, daily cap)
    MUST be classified as POLICY_BLOCKED, never mislabeled as a software/detector miss.
    """
    eval_records = [
        {"opportunity_id": "opp_news", "as_of_ms": 1000, "stage": OpportunityStage.SWEPT.value, "symbol": "XAUUSDT", "direction": "SHORT"},
        {"opportunity_id": "opp_news", "as_of_ms": 2000, "stage": OpportunityStage.MSS_CONFIRMED.value, "symbol": "XAUUSDT", "direction": "SHORT"},
        {
            "opportunity_id": "opp_news",
            "as_of_ms": 3000,
            "stage": OpportunityStage.GUARD_BLOCKED.value,
            "reason_codes": ["NEWS_BLACKOUT"],
            "symbol": "XAUUSDT",
            "direction": "SHORT",
        },
    ]
    res = CG01MissedOpportunityAuditService.classify_opportunity(eval_records)
    assert res.classification == "POLICY_BLOCKED"
    assert res.miss_reason == "NEWS_BLACKOUT"
    assert "Intentional policy block" in res.notes


def test_rule_097_entry_never_touched_classification():
    """
    Rule 097: An order that was successfully armed but the market price never retraced to entry
    before expiry MUST be classified as ENTRY_NEVER_TOUCHED, not a bot failure.
    """
    eval_records = [
        {"opportunity_id": "opp_notouch", "as_of_ms": 1000, "stage": OpportunityStage.ARMED.value, "symbol": "XAUUSDT", "direction": "LONG"},
        {"opportunity_id": "opp_notouch", "as_of_ms": 2000, "stage": OpportunityStage.EXPIRED.value, "symbol": "XAUUSDT", "direction": "LONG"},
    ]
    order = {"id": "ord_notouch", "state": "expired"}
    res = CG01MissedOpportunityAuditService.classify_opportunity(eval_records, order=order)
    assert res.classification == "MARKET_NON_FILL"
    assert res.miss_reason == "ENTRY_NEVER_TOUCHED"
    assert res.terminal_stage == "EXPIRED"


def test_classify_latency_detection_late():
    """
    Rule 095 & 096: When scheduler delay exceeds acceptable budget, classify as DETECTION_LATE with timing evidence.
    """
    eval_records = [
        {
            "opportunity_id": "opp_late",
            "as_of_ms": 1000,
            "market_event_received_at": 1000,
            "evaluation_started_at": 1800,  # 800ms delay > 500ms budget
            "evaluation_completed_at": 1830,
            "stage": OpportunityStage.SWEPT.value,
            "symbol": "XAUUSDT",
            "direction": "LONG",
        }
    ]
    res = CG01MissedOpportunityAuditService.classify_opportunity(eval_records, max_retrace_delay_ms=500)
    assert res.classification == "TECHNICAL_MISS"
    assert res.miss_reason == "DETECTION_LATE"
    assert "800ms" in res.notes


# =========================================================================
# 2. Audit Run Aggregation and Bottlenecks (Rules 101-110)
# =========================================================================
def test_cg01_missed_opportunity_audit_run_summary():
    """
    Rules 101-110: audit_run aggregates unique opportunities, counts bottlenecks,
    and returns representative Long and Short records.
    """
    records = [
        # Opp 1: Filled Long
        {"opportunity_id": "opp_1", "as_of_ms": 1000, "stage": OpportunityStage.FILLED.value, "direction": "LONG", "symbol": "XAUUSDT"},
        # Opp 2: Blocked Short by News Guard
        {"opportunity_id": "opp_2", "as_of_ms": 1100, "stage": OpportunityStage.GUARD_BLOCKED.value, "direction": "SHORT", "reason_codes": ["NEWS_BLACKOUT"], "symbol": "XAUUSDT"},
        # Opp 3: Entry never touched Long
        {"opportunity_id": "opp_3", "as_of_ms": 1200, "stage": OpportunityStage.EXPIRED.value, "direction": "LONG", "symbol": "XAUUSDT"},
    ]
    orders = [
        {"setup_instance_id": "opp_1", "state": "filled"},
        {"setup_instance_id": "opp_3", "state": "expired"},
    ]

    report = CG01MissedOpportunityAuditService.audit_run(
        run_id="run_test_001",
        records=records,
        orders=orders,
    )

    assert report["total_unique_opportunities"] == 3
    assert report["actual_filled_count"] == 1
    assert report["policy_blocked_count"] == 1
    assert report["market_non_fill_count"] == 1
    assert report["capture_rate_pct"] == 33.33

    # Check bottlenecks
    bottlenecks = {b["reason"]: b["count"] for b in report["top_bottlenecks"]}
    assert bottlenecks.get("NEWS_BLACKOUT") == 1
    assert bottlenecks.get("ENTRY_NEVER_TOUCHED") == 1

    # Check representative examples
    assert report["representative_examples"]["long"]["opportunity_id"] in ("opp_1", "opp_3")
    assert report["representative_examples"]["short"]["opportunity_id"] == "opp_2"
