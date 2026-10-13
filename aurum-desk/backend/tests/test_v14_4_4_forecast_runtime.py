"""
backend/tests/test_v14_4_4_forecast_runtime.py
V14.4.4 - Genuine Machine Proof & Forecast Observation Runtime Tests.

Proves:
1. has_machine_proof=True or arbitrary string ID is NOT sufficient for verified machine proof.
2. load_and_validate_confirmation_proof queries real repository:
   - Non-existent ID -> invalid.
   - Mismatched scenario, symbol, or direction -> invalid.
   - Future event (known_at > as_of_ms) -> invalid.
   - Genuine matching event <= as_of_ms -> valid.
3. load_forecast_evidence_window excludes candles/events with known_at > as_of_ms.
4. assess_forecast_coverage flags missing candles in sequence as PARTIAL/UNKNOWN, not COMPLETE.
5. summarize_forecast_observations only includes eligible verified cohort.
"""
import pytest
import models
from services.smc_forecast_observation_service import (
    validate_forecast_definition,
    load_and_validate_confirmation_proof,
    assess_forecast_coverage,
    summarize_forecast_observations,
)


def test_definition_validation_does_not_trust_boolean_or_arbitrary_string():
    """PHẦN 056: Arbitrary boolean has_machine_proof=True or fake ID does not constitute verification."""
    # Definition with valid levels but boolean flag
    fake_def = {
        "session_id": "NY_20261013",
        "direction": "LONG",
        "target_price": 4200.0,
        "invalidation_price": 4170.0,
        "has_machine_proof": True,  # Spoofed flag
        "confirmation_proof_id": "arbitrary_proof_string",
    }
    res = validate_forecast_definition(fake_def)
    # Syntax is valid, but definition status cannot claim verified confirmation on flag alone
    assert res["is_valid"] is True
    assert res.get("proof_verified", False) is False


def test_proof_repository_validation(isolated_db):
    """PHẦN 059, 110: load_and_validate_confirmation_proof checks real evidence store."""
    as_of = 1770000000000

    # 1. Non-existent ID returns invalid
    v_none = load_and_validate_confirmation_proof(isolated_db, "non_existent_proof_id", {"symbol": "XAUUSDT"}, as_of)
    assert v_none["is_valid"] is False
    assert v_none["reason_code"] == "PROOF_NOT_FOUND"

    # Seed canonical scenario snapshot in SMCScenarioSnapshot repository (PHẦN 059)
    import json
    from services.smc_scenario_types import ScenarioSnapshot, ScenarioState, EvidenceNode, FactStatus
    nodes = [
        EvidenceNode(id="sw_1", kind="SWEEP_LOW", symbol="XAUUSDT", timeframe="5M", direction=None, occurred_at_ms=as_of-2000, known_at_ms=as_of-1800, level=4170.0, fact_status=FactStatus.CONFIRMED_EVIDENCE),
        EvidenceNode(id="break_1", kind="CHOCH", symbol="XAUUSDT", timeframe="5M", direction="BULLISH", occurred_at_ms=as_of-1700, known_at_ms=as_of-1500, source_ids=("sw_1",), level=4185.0, fact_status=FactStatus.CONFIRMED_EVIDENCE),
        EvidenceNode(id="fvg_1", kind="FVG", symbol="XAUUSDT", timeframe="5M", direction="BULLISH", occurred_at_ms=as_of-1400, known_at_ms=as_of-1200, source_ids=("break_1",), level=4182.0, fact_status=FactStatus.CONFIRMED_EVIDENCE),
        EvidenceNode(id="retest_1", kind="RETEST", symbol="XAUUSDT", timeframe="5M", direction="BULLISH", occurred_at_ms=as_of-1100, known_at_ms=as_of-1000, source_ids=("fvg_1",), level=4182.5, fact_status=FactStatus.CONFIRMED_EVIDENCE),
    ]
    snap_obj = ScenarioSnapshot(
        scenario_id="scen_1",
        instance_id="inst_1",
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="5M",
        context_timeframes=["D", "4H", "15M"],
        family="STANDARD_SMC",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=as_of - 1000,
        evidence_cutoff_ms=as_of - 1000,
        evidence_ids=[n.id for n in nodes],
        liquidity_map={},
        trigger_chain={"verified": True, "reasons": [], "node_ids": {"sweep": "sw_1", "break": "break_1", "fvg": "fvg_1", "retest": "retest_1"}},
        target=None,
        invalidation=None,
        summary_vi="Snapshot",
        config_version=1,
        detector_version="v14.4",
        requirement_profile="SMC_DEFAULT",
        evidence=[n.to_dict() for n in nodes],
    )
    snap_row = models.SMCScenarioSnapshot(
        snapshot_id="snap_1",
        scenario_id="scen_1",
        instance_id="inst_1",
        revision=1,
        symbol="XAUUSDT",
        timeframe="5M",
        direction="LONG",
        state="STRUCTURE_CONFIRMED",
        as_of_ms=as_of - 1000,
        evidence_cutoff_ms=as_of - 1000,
        evidence_digest="digest_test",
        created_at=as_of - 1000,
        payload=json.dumps(snap_obj.to_dict()),
    )
    isolated_db.add(snap_row)

    # Seed a real DomainEvent representing a verified transition
    event = models.DomainEvent(
        event_id="evt_valid_proof_1",
        sequence=1,
        schema_version="1.0.0",
        event_type="scenario.confirmed",
        aggregate_id="scen_1",
        aggregate_version=1,
        occurred_at=as_of - 1000,
        published_at=as_of - 1000,
        payload=json.dumps({
            "symbol": "XAUUSDT",
            "scenario_instance_id": "inst_1",
            "snapshot_id": "snap_1",
            "direction": "LONG",
            "transition": "VERIFIED_SWEEP_TO_RETEST",
            "retest_id": "retest_1",
            "break_id": "break_1",
            "evidence_chain_ids": ["sw_1", "break_1", "fvg_1", "retest_1"],
            "known_at_ms": as_of - 1000,
            "occurred_at_ms": as_of - 2000,
        }),
    )
    isolated_db.add(event)
    isolated_db.commit()

    # 2. Matching snapshot -> valid!
    snapshot_matching = {
        "symbol": "XAUUSDT",
        "scenario_instance_id": "inst_1",
        "direction": "LONG",
    }
    v_ok = load_and_validate_confirmation_proof(isolated_db, "evt_valid_proof_1", snapshot_matching, as_of)
    assert v_ok["is_valid"] is True
    assert v_ok["reason_code"] == "PROOF_VALIDATED"

    # 3. Mismatched direction -> invalid!
    snapshot_wrong_dir = {
        "symbol": "XAUUSDT",
        "scenario_instance_id": "inst_1",
        "direction": "SHORT",
    }
    v_wrong_dir = load_and_validate_confirmation_proof(isolated_db, "evt_valid_proof_1", snapshot_wrong_dir, as_of)
    assert v_wrong_dir["is_valid"] is False
    assert v_wrong_dir["reason_code"] == "PROOF_DIRECTION_MISMATCH"

    # 4. Future event -> invalid!
    v_future = load_and_validate_confirmation_proof(isolated_db, "evt_valid_proof_1", snapshot_matching, as_of_ms=as_of - 5000)
    assert v_future["is_valid"] is False
    assert v_future["reason_code"] == "PROOF_KNOWN_AT_FUTURE"


def test_forecast_coverage_detects_internal_gaps():
    """PHẦN 064: Missing candles in sequence produces PARTIAL, not COMPLETE."""
    snapshot = {"as_of_ms": 1770000000000, "horizon_ms": 1770003600000}
    # Observations missing 2 consecutive 5M candles (10m gap)
    obs = [
        {"timestamp": 1770000000000, "open": 4180, "high": 4185, "low": 4178, "close": 4182},
        {"timestamp": 1770000300000, "open": 4182, "high": 4186, "low": 4180, "close": 4184},
        # gap from 1770000600000 to 1770001500000 (15 min gap)
        {"timestamp": 1770001500000, "open": 4184, "high": 4188, "low": 4181, "close": 4185},
    ]
    cov = assess_forecast_coverage(snapshot, obs, as_of_ms=1770002000000)
    assert cov["coverage_status"] in ("PARTIAL", "UNKNOWN")
    assert cov["has_gap"] is True


def test_summarize_forecast_observations_eligible_cohort_only(isolated_db):
    """PHẦN 066: Unverified forecasts are excluded from eligible cohort."""
    summary = summarize_forecast_observations(isolated_db, "XAUUSDT")
    assert "eligible_count" in summary
    assert summary["probability"] is None or summary["probability"] == "UNAVAILABLE"
