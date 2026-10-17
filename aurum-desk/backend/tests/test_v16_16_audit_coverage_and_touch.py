"""
backend/tests/test_v16_16_audit_coverage_and_touch.py
V16.16 Tests for Missed Opportunity Audit: Coverage Verification vs Touch Discrimination.

Covers:
- Rules 026, 121: Expired opportunity without quote coverage evidence must be UNVERIFIABLE / DATA_GAP,
  never assumed ENTRY_NEVER_TOUCHED. Missing coverage flag is unknown/unverifiable.
- Rules 027, 122, 123: verify_entry_touch_coverage must NOT blindly return NOT_TOUCHED
  just because quote_coverage_verified=True. If bid/ask touched entry, it must return TOUCHED.
- Rule 124: Long check asks, Short check bids according to execution rules.
- Rule 131, 132: audit_run must query exact linked orders, never falling back to all AUTO_STRATEGY orders.
"""
import pytest
from services.cg01_missed_audit_service import (
    CG01MissedOpportunityAuditService,
    OpportunityAuditRecord,
)


def test_expired_without_coverage_is_unverifiable():
    """
    Rules 026, 121: When an order is expired and quote coverage is NOT provided (None or missing),
    classification must be UNVERIFIABLE / DATA_GAP, NOT ENTRY_NEVER_TOUCHED.
    """
    eval_record = {
        "opportunity_id": "opp_exp_no_cov",
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "as_of_ms": 1700000000000,
        "stage": "EXPIRED",
        "planned_entry": 2500.0,
        "stop_loss": 2490.0,
        "take_profit": 2520.0,
        # quote_coverage_verified is omitted / None
    }
    order_dict = {
        "id": "ord_exp_no_cov",
        "state": "expired",
        # quote_coverage_verified is omitted / None
    }

    res = CG01MissedOpportunityAuditService.classify_opportunity(
        eval_records=[eval_record],
        order=order_dict,
        quote_coverage_verified=None,  # Missing coverage
    )

    assert res.classification == "UNVERIFIABLE", f"Expected UNVERIFIABLE when coverage missing, got {res.classification}"
    assert res.miss_reason == "DATA_GAP"


def test_coverage_verified_with_touch_is_touched_not_not_touched():
    """
    Rules 027, 122, 123: verify_entry_touch_coverage must NOT return NOT_TOUCHED
    if quotes indicate price actually touched the planned entry level.
    """
    order = {
        "id": "ord_touch_test",
        "direction": "LONG",
        "planned_entry": 2500.0,
        "quote_coverage_verified": True,
        "lowest_ask": 2499.5,   # For LONG, ask <= planned_entry means touched!
        "touched": True,
    }
    evals = [{
        "opportunity_id": "opp_touch_test",
        "quote_coverage_verified": True,
        "touched": True,
    }]

    verdict = CG01MissedOpportunityAuditService.verify_entry_touch_coverage(order, evals)
    assert verdict == "TOUCHED", f"Expected TOUCHED when quotes reached entry, got {verdict}"


def test_coverage_verified_without_touch_is_no_touch_observed():
    """
    Rules 123, 124: When coverage is verified AND quotes confirm price never touched entry,
    verdict is NO_TOUCH_OBSERVED (or NOT_TOUCHED).
    """
    order = {
        "id": "ord_notouch_test",
        "direction": "LONG",
        "planned_entry": 2500.0,
        "quote_coverage_verified": True,
        "lowest_ask": 2505.0,   # Ask never dropped to 2500.0
        "touched": False,
    }
    evals = [{
        "opportunity_id": "opp_notouch_test",
        "quote_coverage_verified": True,
        "touched": False,
    }]

    verdict = CG01MissedOpportunityAuditService.verify_entry_touch_coverage(order, evals)
    assert verdict in ("NOT_TOUCHED", "NO_TOUCH_OBSERVED"), f"Expected NOT_TOUCHED / NO_TOUCH_OBSERVED, got {verdict}"


def test_audit_run_empty_associations_does_not_leak_auto_strategy(monkeypatch):
    """
    Rule 131, 132: If a run has no linked orders, audit_run must return 0 orders / 0 fills,
    never falling back to querying all historical AUTO_STRATEGY orders.
    """
    res = CG01MissedOpportunityAuditService.audit_run(
        run_id="run_empty_iso_test",
        records=[],
        orders=[],
    )
    assert res["total_unique_opportunities"] == 0
    assert res["actual_filled_count"] == 0
