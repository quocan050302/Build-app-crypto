"""
backend/tests/test_v16_15_causal_audit_classification.py
V16.15 Automated Tests for Causal Opportunity Audit, Quote Coverage, and Exact Linkage.

Covers:
- Rules 018, 027, 141-144: Expired order without quote coverage is UNVERIFIABLE / DATA_GAP, not ENTRY_NEVER_TOUCHED.
- Rules 028, 145, 146: Scheduler delay without runaway does not mislabel filled or valid trades as TECHNICAL_MISS.
- Rules 141, 149: Recognizing paper_open as FILLED and active armed orders as PENDING.
- Rule 150: Exact order association without substring false matching (e.g. opp_1 vs opp_10).
"""
import pytest

from services.cg01_missed_audit_service import (
    CG01MissedOpportunityAuditService,
    OpportunityAuditRecord,
)
from services.scalp_m1_types import OpportunityReasonCode, OpportunityStage


def test_expired_without_quote_coverage_is_unverifiable():
    """
    Rules 027, 142, 144: Expired order without complete quote coverage must NOT be claimed
    as ENTRY_NEVER_TOUCHED; it must be classified as UNVERIFIABLE with miss_reason=DATA_GAP.
    """
    eval_records = [
        {
            "opportunity_id": "opp_exp_no_cov",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "as_of_ms": 1000,
            "stage": OpportunityStage.EXPIRED.value,
            "quote_coverage_verified": False,  # Missing tape coverage
        }
    ]
    order = {"id": "ord_1", "state": "expired", "quote_coverage_verified": False}

    audit = CG01MissedOpportunityAuditService.classify_opportunity(
        eval_records=eval_records, order=order
    )
    assert audit.classification == "UNVERIFIABLE"
    assert audit.miss_reason == "DATA_GAP"
    assert "Expired without complete quote tape coverage" in audit.notes


def test_expired_with_quote_coverage_is_entry_never_touched():
    """
    Rules 027, 142: With verified quote coverage, an expired order is legitimately
    classified as MARKET_NON_FILL with miss_reason=ENTRY_NEVER_TOUCHED.
    """
    eval_records = [
        {
            "opportunity_id": "opp_exp_cov",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "as_of_ms": 1000,
            "stage": OpportunityStage.EXPIRED.value,
            "quote_coverage_verified": True,  # Full tape verified
        }
    ]
    order = {"id": "ord_2", "state": "expired", "quote_coverage_verified": True}

    audit = CG01MissedOpportunityAuditService.classify_opportunity(
        eval_records=eval_records, order=order
    )
    assert audit.classification == "MARKET_NON_FILL"
    assert audit.miss_reason == "ENTRY_NEVER_TOUCHED"


def test_delayed_evaluation_that_fills_is_classified_filled():
    """
    Rules 028, 145: Evaluation delayed > 500ms but subsequently filled must be classified
    as FILLED, not mislabeled as a TECHNICAL_MISS.
    """
    eval_records = [
        {
            "opportunity_id": "opp_delayed_filled",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "as_of_ms": 1000,
            "market_event_received_at": 1000,
            "evaluation_started_at": 1800,  # 800ms delay > 500ms budget
            "stage": OpportunityStage.FILLED.value,
        }
    ]
    order = {"id": "ord_3", "state": "paper_open"}  # Filled / active position

    audit = CG01MissedOpportunityAuditService.classify_opportunity(
        eval_records=eval_records, order=order, max_retrace_delay_ms=500
    )
    assert audit.classification == "FILLED"
    assert audit.miss_reason is None


def test_delayed_evaluation_with_runaway_is_technical_miss():
    """
    Rules 145, 146: Latency budget violation + causal runaway entry moves is classified
    as TECHNICAL_MISS / DETECTION_LATE_MISS.
    """
    eval_records = [
        {
            "opportunity_id": "opp_late_miss",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "as_of_ms": 1000,
            "market_event_received_at": 1000,
            "evaluation_started_at": 1700,  # 700ms delay > 500ms budget
            "stage": "TRIGGER_CONFIRMED",
            "reason_codes": [OpportunityReasonCode.ENTRY_TOO_FAR.value],
        }
    ]

    audit = CG01MissedOpportunityAuditService.classify_opportunity(
        eval_records=eval_records, order=None, max_retrace_delay_ms=500
    )
    assert audit.classification == "TECHNICAL_MISS"
    assert audit.miss_reason in ("DETECTION_LATE", "DETECTION_LATE_MISS")


def test_active_armed_order_is_classified_pending():
    """
    Rule 141: An active order in armed state without expiry/fill is classified as PENDING.
    """
    eval_records = [
        {
            "opportunity_id": "opp_pending_arm",
            "symbol": "XAUUSDT",
            "direction": "SHORT",
            "as_of_ms": 1000,
            "stage": OpportunityStage.ARMED.value,
        }
    ]
    order = {"id": "ord_arm", "state": "armed"}

    audit = CG01MissedOpportunityAuditService.classify_opportunity(
        eval_records=eval_records, order=order
    )
    assert audit.classification == "PENDING"
    assert audit.miss_reason == "IN_PROGRESS"


def test_exact_order_matching_avoids_substring_false_positive():
    """
    Rule 150: Exact matching must not match opp_1 with ord_opp_10 or similar substring collisions.
    """
    records = [
        {
            "opportunity_id": "opp_1",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "as_of_ms": 1000,
            "stage": OpportunityStage.EXPIRED.value,
            "quote_coverage_verified": True,
        },
        {
            "opportunity_id": "opp_10",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "as_of_ms": 2000,
            "stage": OpportunityStage.FILLED.value,
        }
    ]
    # Order belongs to opp_10 ONLY
    orders = [
        {
            "id": "ord_opp_10",
            "setup_instance_id": "opp_10",
            "state": "filled",
        }
    ]

    audit_res = CG01MissedOpportunityAuditService.audit_run(
        run_id="run_exact_test", records=records, orders=orders
    )

    # opp_1 must be MARKET_NON_FILL, not FILLED
    assert audit_res["total_unique_opportunities"] == 2
    assert audit_res["actual_filled_count"] == 1
    assert audit_res["market_non_fill_count"] == 1
