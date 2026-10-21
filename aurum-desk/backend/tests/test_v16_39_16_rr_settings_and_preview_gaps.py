"""
backend/tests/test_v16_39_16_rr_settings_and_preview_gaps.py
Aurum V16.39.16 — Verification of Centralized RR Policy, Preview Threshold Alignment,
and Frozen Historical Order Display Integrity.

Rules:
- Gap 1: evaluate_quality_checks respects explicit min_net_rr (no hardcoded 2.0)
- Gap 2: unrounded boundary comparisons and dynamic Vietnamese explanations
- Gap 3: frozen orders without snapshot report min_net_rr: None and UNKNOWN compliance (no fake 2.0)
- Non-regression: legacy orders maintain complete financial amounts and geometry
"""

import json
import math
import pytest
from services.setup_quality_service import (
    evaluate_quality_checks,
    resolve_quality_rr_threshold,
)
from services.setup_display_plan_service import (
    build_setup_display_plan,
    build_order_row_display_plan,
    read_frozen_rr_threshold,
)


# ==============================================================================
# GAP 1 & GAP 2: QUALITY CHECK THRESHOLD & BOUNDARY BEHAVIOR
# ==============================================================================

def test_resolve_quality_rr_threshold_sources():
    """Verify priority and parsing of explicit thresholds across context structures."""
    # 1. profile dict
    th, src = resolve_quality_rr_threshold({"profile": {"min_net_rr": 1.5}})
    assert th == 1.5
    assert src == "EXPLICIT_THRESHOLD"

    # 2. top-level context
    th, src = resolve_quality_rr_threshold({"min_net_rr": 1.8})
    assert th == 1.8
    assert src == "EXPLICIT_THRESHOLD"

    # 3. calc dict
    th, src = resolve_quality_rr_threshold({"calc": {"min_net_rr": 2.2}})
    assert th == 2.2
    assert src == "EXPLICIT_THRESHOLD"

    # 4. plan dict
    th, src = resolve_quality_rr_threshold({"plan": {"min_net_rr": 2.5}})
    assert th == 2.5
    assert src == "EXPLICIT_THRESHOLD"

    # 5. Missing threshold anywhere -> legacy default 2.0
    th, src = resolve_quality_rr_threshold({"symbol": "XAUUSDT"})
    assert th == 2.0
    assert src == "LEGACY_DEFAULT"

    # 6. Explicitly invalid/malformed -> INVALID_EXPLICIT_THRESHOLD
    th, src = resolve_quality_rr_threshold({"profile": {"min_net_rr": "bad_value"}})
    assert th is None
    assert src == "INVALID_EXPLICIT_THRESHOLD"

    th, src = resolve_quality_rr_threshold({"profile": {"min_net_rr": -1.5}})
    assert th is None
    assert src == "INVALID_EXPLICIT_THRESHOLD"

    th, src = resolve_quality_rr_threshold({"profile": {"min_net_rr": True}})
    assert th is None
    assert src == "INVALID_EXPLICIT_THRESHOLD"


def test_evaluate_quality_checks_respects_configured_threshold():
    """
    B01 & B02: With actual net_rr=1.70:
      - When threshold=1.50 -> MET, expected >= 1.50R
      - When threshold=2.00 -> NOT_MET, expected >= 2.00R
    """
    ctx_15 = {
        "direction": "LONG",
        "plan": {"net_rr": 1.7},
        "profile": {"min_net_rr": 1.5},
    }
    checks_15, _ = evaluate_quality_checks(ctx_15)
    rr_chk_15 = next(c for c in checks_15 if c.code == "NET_RR_COMPLIANT")
    assert rr_chk_15.status == "MET"
    assert rr_chk_15.expected == ">= 1.50R"
    assert rr_chk_15.observed == "1.70R"
    assert "đạt 1.70R" in rr_chk_15.explanation_vi
    assert ">= 1.50R" in rr_chk_15.explanation_vi

    ctx_20 = {
        "direction": "LONG",
        "plan": {"net_rr": 1.7},
        "profile": {"min_net_rr": 2.0},
    }
    checks_20, _ = evaluate_quality_checks(ctx_20)
    rr_chk_20 = next(c for c in checks_20 if c.code == "NET_RR_COMPLIANT")
    assert rr_chk_20.status == "NOT_MET"
    assert rr_chk_20.expected == ">= 2.00R"
    assert rr_chk_20.observed == "1.70R"
    assert "chỉ đạt 1.70R" in rr_chk_15.explanation_vi or "< 2.00R" in rr_chk_20.explanation_vi


def test_evaluate_quality_checks_unrounded_boundaries():
    """Rule 069: Compare unrounded actual net_rr with threshold; no premature rounding."""
    # 1.4996 is strictly less than 1.5000 -> NOT_MET
    ctx_under = {
        "direction": "LONG",
        "plan": {"net_rr": 1.4996},
        "profile": {"min_net_rr": 1.5},
    }
    checks_under, _ = evaluate_quality_checks(ctx_under)
    rr_under = next(c for c in checks_under if c.code == "NET_RR_COMPLIANT")
    assert rr_under.status == "NOT_MET"

    # 1.5004 is greater than 1.5000 -> MET
    ctx_over = {
        "direction": "LONG",
        "plan": {"net_rr": 1.5004},
        "profile": {"min_net_rr": 1.5},
    }
    checks_over, _ = evaluate_quality_checks(ctx_over)
    rr_over = next(c for c in checks_over if c.code == "NET_RR_COMPLIANT")
    assert rr_over.status == "MET"


def test_evaluate_quality_checks_handles_invalid_or_missing_threshold():
    """Rule 066 & 068: Invalid threshold yields UNKNOWN, does not throw exception."""
    ctx_invalid = {
        "direction": "LONG",
        "plan": {"net_rr": 1.7},
        "profile": {"min_net_rr": "corrupt"},
    }
    checks, _ = evaluate_quality_checks(ctx_invalid)
    rr_chk = next(c for c in checks if c.code == "NET_RR_COMPLIANT")
    assert rr_chk.status == "UNKNOWN"
    assert "không hợp lệ" in rr_chk.explanation_vi

    # Missing net_rr
    ctx_no_rr = {
        "direction": "LONG",
        "plan": {"net_rr": None},
        "profile": {"min_net_rr": 1.5},
    }
    checks_no_rr, _ = evaluate_quality_checks(ctx_no_rr)
    rr_no_rr = next(c for c in checks_no_rr if c.code == "NET_RR_COMPLIANT")
    assert rr_no_rr.status == "UNKNOWN"


# ==============================================================================
# GAP 3: FROZEN ORDER DISPLAY INTEGRITY & NO FAKE 2.0 DEFAULT
# ==============================================================================

def test_read_frozen_rr_threshold():
    """Tests for pure historical threshold parser."""
    # Known with min_net_rr_at_arm
    st, val, src, fp = read_frozen_rr_threshold({
        "min_net_rr_at_arm": 1.75,
        "rr_policy_source": "ORDER_SNAPSHOT",
        "rr_policy_fingerprint": "fp123"
    })
    assert st == "KNOWN"
    assert val == 1.75
    assert src == "ORDER_SNAPSHOT"
    assert fp == "fp123"

    # Known historical valid < 1.5 (e.g. 1.25 under old version, must be preserved without floor)
    st, val, src, _ = read_frozen_rr_threshold({"min_net_rr_at_arm": 1.25})
    assert st == "KNOWN"
    assert val == 1.25

    # Missing keys -> MISSING
    st, val, src, _ = read_frozen_rr_threshold({"slippage_usdt": 0.1})
    assert st == "MISSING"
    assert val is None

    # Invalid boolean or non-positive -> INVALID
    st, val, _, _ = read_frozen_rr_threshold({"min_net_rr_at_arm": True})
    assert st == "INVALID"
    assert val is None

    st, val, _, _ = read_frozen_rr_threshold({"min_net_rr_at_arm": -1.0})
    assert st == "INVALID"
    assert val is None


def test_build_setup_display_plan_frozen_known_snapshot():
    """Order with valid snapshot retains exact min_net_rr and determines compliance."""
    cost_snap = {
        "min_net_rr_at_arm": 1.50,
        "rr_policy_source": "ORDER_SNAPSHOT",
        "net_rr": 1.70,
        "quantity": 0.5,
        "risk_usdt": 50.0,
        "gross_loss_usdt": 50.0,
        "gross_reward_usdt": 127.5,
        "net_reward_usdt": 85.0,
    }
    order_dict = {
        "id": "ord-001",
        "direction": "LONG",
        "state": "armed",
        "planned_entry": 2700.0,
        "stop_loss": 2690.0,
        "take_profit": 2725.5,
        "quantity": 0.5,
        "gross_rr": 2.55,
        "net_rr": 1.70,
        "risk_usdt": 50.0,
        "cost_snapshot": json.dumps(cost_snap)
    }
    plan = build_order_row_display_plan(
        order=order_dict,
        frozen_snapshot=cost_snap
    )
    fin = plan["financial"]
    assert fin is not None
    assert fin["min_net_rr"] == 1.50
    assert fin["meets_min_rr"] is True
    assert fin["rr_compliance_status"] == "MET"
    assert fin["net_rr"] == 1.70
    assert fin["quantity"] == 0.5


def test_build_setup_display_plan_frozen_missing_snapshot():
    """
    B08: Historical order with complete financial metrics but lacking min_net_rr_at_arm:
    Must report min_net_rr: None and compliance UNKNOWN (NOT 2.0!).
    Amounts, geometry, leverage, and net_rr remain fully available.
    """
    cost_snap = {
        "net_rr": 1.70,
        "quantity": 0.5,
        "risk_usdt": 50.0,
        "gross_loss_usdt": 50.0,
        "gross_reward_usdt": 127.5,
        "net_reward_usdt": 85.0,
        "cost_source": "LEGACY_SNAPSHOT"
    }
    order_dict = {
        "id": "ord-legacy-002",
        "direction": "LONG",
        "state": "closed",
        "planned_entry": 2700.0,
        "actual_entry": 2700.0,
        "stop_loss": 2690.0,
        "take_profit": 2725.5,
        "quantity": 0.5,
        "gross_rr": 2.55,
        "net_rr": 1.70,
        "risk_usdt": 50.0,
        "cost_snapshot": json.dumps(cost_snap)
    }
    plan = build_order_row_display_plan(
        order=order_dict,
        frozen_snapshot=cost_snap
    )
    fin = plan["financial"]
    assert fin is not None
    assert fin["min_net_rr"] is None, "Must NOT fake 2.0 for missing historical threshold!"
    assert fin["meets_min_rr"] is None, "Compliance must be null when threshold is missing!"
    assert fin["rr_compliance_status"] == "UNKNOWN"
    # Preserves actual financials
    assert fin["net_rr"] == 1.70
    assert fin["quantity"] == 0.5
    assert fin["net_risk_usdt"] == 50.0


def test_build_setup_display_plan_stored_legacy_missing_threshold():
    """WatchSetup queried without financial context and without min_net_rr reports UNKNOWN."""
    setup_dict = {
        "id": "setup-legacy-003",
        "direction": "SHORT",
        "state": "WATCHING",
        "provisional_entry": 2750.0,
        "provisional_sl": 2760.0,
        "provisional_tp": 2720.0,
        "net_rr": 1.8,
        "gross_rr": 2.0,
        "quantity": 0.2,
        "risk_usdt": 20.0,
        "leverage": 10,
        "margin_mode": "ISOLATED",
    }
    plan = build_setup_display_plan(setup_dict, financial_context=None)
    fin = plan["financial"]
    assert fin is not None
    assert fin["min_net_rr"] is None
    assert fin["meets_min_rr"] is None
    assert fin["rr_compliance_status"] == "UNKNOWN"
    assert plan["status"] == "AVAILABLE"
    assert "chưa xác định" in plan["reason_message"]


def test_preview_setup_quality_api_dynamic_rr():
    """Rule 147: Calling /api/v1/quality/preview uses backend authoritative configured threshold (1.5)."""
    from fastapi.testclient import TestClient
    from main import app, get_db
    from database import SessionLocal
    from services.rr_policy_service import stage_min_net_rr_update

    db = SessionLocal()
    try:
        stage_min_net_rr_update(db, 1.5)
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    # Target plan geometry giving net_rr around 1.7 - 1.8:
    # entry 2700, SL 2690, TP 2717.5 gives gross 1.75, net ~1.7
    payload = {
        "symbol": "XAUUSDT",
        "timeframe": "15M",
        "direction": "LONG",
        "entry_price": 2700.0,
        "stop_loss": 2690.0,
        "take_profit": 2718.0,
        "quantity": 0.1,
        "leverage": 20,
        "margin_mode": "ISOLATED",
        "risk_pct": 0.25
    }
    resp = client.post("/api/v1/quality/preview", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    # Calculator threshold
    assert data["calc"]["min_net_rr"] == 1.5
    # Quality check threshold
    checks = data["assessment"]["checks"]
    rr_chk = next(c for c in checks if c["code"] == "NET_RR_COMPLIANT")
    assert rr_chk["expected"] == ">= 1.50R", f"Expected '>= 1.50R', got {rr_chk['expected']}"
    assert ">= 2.00R" not in rr_chk["expected"]

