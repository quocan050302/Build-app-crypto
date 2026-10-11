import pytest
from services.setup_quality_service import (
    QualityCheck,
    EvidenceRef,
    derive_quality_status,
    select_related_evidence,
    build_plan_fingerprint,
    build_quality_snapshot,
    assess_setup_quality,
)

def test_derive_quality_status_precedence():
    # 1. Validation error takes top priority -> ERROR
    res_err = derive_quality_status([], assessed=True, validation_errors=["INVALID_CONFIG"])
    assert res_err == "ERROR"

    # 2. Existing hard blockers take priority over checks -> BLOCKED_EXISTING
    checks_all_met = [
        QualityCheck("C1", "MET", "REQUIRED", "ok"),
        QualityCheck("C2", "MET", "REQUIRED", "ok")
    ]
    res_blocked = derive_quality_status(checks_all_met, assessed=True, existing_hard_blockers=["DAY_FILL_LIMIT_REACHED"])
    assert res_blocked == "BLOCKED_EXISTING"

    # 3. Not assessed -> NOT_ASSESSED
    res_unassessed = derive_quality_status(checks_all_met, assessed=False)
    assert res_unassessed == "NOT_ASSESSED"

    # 4. Any required check UNKNOWN -> INSUFFICIENT_DATA
    checks_unknown = [
        QualityCheck("C1", "MET", "REQUIRED", "ok"),
        QualityCheck("C2", "UNKNOWN", "REQUIRED", "missing data"),
        QualityCheck("S1", "MET", "SUPPORTING", "ok")
    ]
    res_insufficient = derive_quality_status(checks_unknown, assessed=True)
    assert res_insufficient == "INSUFFICIENT_DATA"

    # 5. Any required check NOT_MET -> PARTIAL
    checks_not_met = [
        QualityCheck("C1", "MET", "REQUIRED", "ok"),
        QualityCheck("C2", "NOT_MET", "REQUIRED", "waiting"),
        QualityCheck("S1", "MET", "SUPPORTING", "ok")
    ]
    res_partial = derive_quality_status(checks_not_met, assessed=True)
    assert res_partial == "PARTIAL"

    # 6. All required checks MET -> SATISFIED (even if supporting is NOT_MET)
    checks_satisfied = [
        QualityCheck("C1", "MET", "REQUIRED", "ok"),
        QualityCheck("C2", "MET", "REQUIRED", "ok"),
        QualityCheck("S1", "NOT_MET", "SUPPORTING", "no displacement")
    ]
    res_satisfied = derive_quality_status(checks_satisfied, assessed=True)
    assert res_satisfied == "SATISFIED"

def test_select_related_evidence_strict_causality():
    as_of = 1700000500000
    evidence = [
        EvidenceRef(id="ev-1", kind="SWING_HIGH", symbol="XAUUSDT", timeframe="15M", known_at_ms=1700000100000),
        EvidenceRef(id="ev-2", kind="SWEEP", symbol="XAUUSDT", timeframe="15M", known_at_ms=1700000300000, source_ids=("ev-1",)),
        # Future event known after as_of MUST be filtered out!
        EvidenceRef(id="ev-future", kind="CHOCH", symbol="XAUUSDT", timeframe="15M", known_at_ms=1700000600000, source_ids=("ev-2",)),
    ]

    selected, missing = select_related_evidence(
        evidence,
        matched_ids=["ev-2", "ev-future"],
        symbol="XAUUSDT",
        as_of_ms=as_of
    )
    sel_ids = [e.id for e in selected]
    assert "ev-1" in sel_ids
    assert "ev-2" in sel_ids
    assert "ev-future" not in sel_ids
    assert "ev-future" in missing

def test_long_full_chain_assessment():
    now_ms = 1700000500000
    context = {
        "symbol": "XAUUSDT",
        "timeframe": "15M",
        "direction": "LONG",
        "as_of_ms": now_ms,
        "analysis": {
            "swings": [{"type": "LOW", "price": 2640.0, "timestamp": 1700000100000, "confirmed_at": 1700000200000}],
            "structure_events": [
                {"id": "sw-1", "event_type": "SWEEP", "level": 2640.0, "timestamp": 1700000300000, "confirmed_at": 1700000300000},
                {"id": "ch-1", "event_type": "CHOCH", "direction": "BULLISH", "level": 2655.0, "is_displacement": True, "timestamp": 1700000400000, "confirmed_at": 1700000400000}
            ],
            "active_fvgs": [{"bottom": 2648.0, "top": 2652.0}],
            "htf_bias": "BULLISH",
        },
        "plan": {
            "entry_price": 2650.0,
            "stop_loss": 2640.0,
            "take_profit": 2680.0,
            "net_rr": 2.8,
            "quantity": 0.05,
            "leverage": 30,
            "margin_mode": "ISOLATED"
        },
        "eligibility": {"eligible": True, "reasons": []},
        "quote_view": {"is_valid": True, "price": 2650.5},
        "news_view": {"is_blackout": False},
        "calc": {"net_rr": 2.8}
    }

    assessment, rel_ev = assess_setup_quality(context)
    assert assessment.status == "SATISFIED"
    assert assessment.summary.required_met == assessment.summary.required_total
    assert assessment.summary.score_pct == 100.0
    assert "ch-1" in assessment.related_evidence_ids
    assert assessment.mode == "EXPLAIN_ONLY"

def test_short_full_chain_assessment():
    now_ms = 1700000500000
    context = {
        "symbol": "XAUUSDT",
        "timeframe": "15M",
        "direction": "SHORT",
        "as_of_ms": now_ms,
        "analysis": {
            "swings": [{"type": "HIGH", "price": 4133.0, "timestamp": 1700000100000, "confirmed_at": 1700000200000}],
            "structure_events": [
                {"id": "sw-short-1", "event_type": "SWEEP", "level": 4133.0, "timestamp": 1700000300000, "confirmed_at": 1700000300000},
                {"id": "ch-short-1", "event_type": "CHOCH", "direction": "BEARISH", "level": 4118.0, "is_displacement": True, "timestamp": 1700000400000, "confirmed_at": 1700000400000}
            ],
            "active_fvgs": [{"bottom": 4120.0, "top": 4125.0}],
            "htf_bias": "BEARISH",
        },
        "plan": {
            "entry_price": 4123.0,
            "stop_loss": 4133.0,
            "take_profit": 4103.0,
            "net_rr": 2.0,
            "quantity": 0.05,
            "leverage": 30,
            "margin_mode": "ISOLATED"
        },
        "eligibility": {"eligible": True, "reasons": []},
        "quote_view": {"is_valid": True, "price": 4122.8},
        "news_view": {"is_blackout": False},
        "calc": {"net_rr": 2.0}
    }

    assessment, rel_ev = assess_setup_quality(context)
    assert assessment.status == "SATISFIED"
    assert "ch-short-1" in assessment.related_evidence_ids

def test_waiting_setup_yields_partial_status():
    # Setup has sweep but is waiting for CHOCH break
    now_ms = 1700000500000
    context = {
        "symbol": "XAUUSDT",
        "timeframe": "15M",
        "direction": "LONG",
        "as_of_ms": now_ms,
        "analysis": {
            "swings": [{"type": "LOW", "price": 2640.0, "timestamp": 1700000100000, "confirmed_at": 1700000200000}],
            "structure_events": [
                {"id": "sw-1", "event_type": "SWEEP", "level": 2640.0, "timestamp": 1700000300000, "confirmed_at": 1700000300000},
                # NO CHOCH yet!
            ],
            "active_fvgs": [],
            "htf_bias": "BULLISH",
        },
        "plan": {
            "entry_price": 2650.0,
            "stop_loss": 2640.0,
            "take_profit": 2680.0,
            "net_rr": 2.5
        },
        "eligibility": {"eligible": True, "reasons": []},
        "quote_view": {"is_valid": True},
        "news_view": {"is_blackout": False},
    }

    assessment, _ = assess_setup_quality(context)
    assert assessment.status == "PARTIAL"
    assert "Chờ:" in assessment.summary.next_condition_vi
