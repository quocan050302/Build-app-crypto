"""
backend/tests/test_v14_4_6_forecast_state_transitions.py
V14.4.6 Cases STATE01, STATE02, LEGACY01 (PHẦN 026–035, 084–090, 108–110):
Forecast state transitions upon later genuine confirmation, atomicity of verdict persistence,
and exclusion of legacy unverified / no-proof rows from summary statistics.
"""

import json
import pytest
from sqlalchemy.orm import Session

import models
from services.smc_scenario_service import (
    EvidenceNode,
    FactStatus,
    ScenarioSnapshot,
    ScenarioState,
)
from services.smc_forecast_observation_service import (
    assess_forecast_cohort_eligibility,
    create_forecast_snapshot,
    process_pending_forecasts,
    summarize_forecast_observations,
)


def _seed_genuine_confirmation_event(db: Session, event_id: str, instance_id: str, occurred_at: int, known_at: int):
    snap_id = f"snap_{event_id}"
    sw_id = f"sw_{event_id}"
    br_id = f"br_{event_id}"
    fvg_id = f"fvg_{event_id}"
    rt_id = f"rt_{event_id}"

    sw = EvidenceNode(
        id=sw_id,
        kind="SWEEP_LOW",
        symbol="XAUUSDT",
        timeframe="5M",
        direction=None,
        occurred_at_ms=occurred_at - 400,
        known_at_ms=occurred_at - 300,
        level=4170.0,
        fact_status=FactStatus.CONFIRMED_EVIDENCE,
    )
    br = EvidenceNode(
        id=br_id,
        kind="CHOCH",
        symbol="XAUUSDT",
        timeframe="5M",
        direction="BULLISH",
        occurred_at_ms=occurred_at - 300,
        known_at_ms=occurred_at - 200,
        source_ids=(sw.id,),
        level=4185.0,
        fact_status=FactStatus.CONFIRMED_EVIDENCE,
    )
    fvg = EvidenceNode(
        id=fvg_id,
        kind="FVG",
        symbol="XAUUSDT",
        timeframe="5M",
        direction="BULLISH",
        occurred_at_ms=occurred_at - 200,
        known_at_ms=occurred_at - 100,
        source_ids=(br.id,),
        level=4182.0,
        fact_status=FactStatus.CONFIRMED_EVIDENCE,
    )
    rt = EvidenceNode(
        id=rt_id,
        kind="RETEST",
        symbol="XAUUSDT",
        timeframe="5M",
        direction="BULLISH",
        occurred_at_ms=occurred_at,
        known_at_ms=known_at,
        source_ids=(fvg.id,),
        level=4182.5,
        fact_status=FactStatus.CONFIRMED_EVIDENCE,
    )
    nodes = [sw, br, fvg, rt]
    scenario_id = f"scen_{instance_id}"
    snapshot = ScenarioSnapshot(
        scenario_id=scenario_id,
        instance_id=instance_id,
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="5M",
        context_timeframes=["D", "4H", "15M"],
        family="STANDARD_SMC",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=known_at,
        evidence_cutoff_ms=known_at,
        evidence_ids=[n.id for n in nodes],
        liquidity_map={},
        trigger_chain={
            "verified": True,
            "reasons": [],
            "node_ids": {
                "sweep": sw.id,
                "break": br.id,
                "fvg": fvg.id,
                "retest": rt.id,
            },
        },
        target=None,
        invalidation=None,
        summary_vi="Canonical snapshot",
        config_version=1,
        detector_version="v14.4",
        requirement_profile="DEFAULT",
        evidence=[n.to_dict() for n in nodes],
    )
    db_row = models.SMCScenarioSnapshot(
        snapshot_id=snap_id,
        scenario_id=scenario_id,
        instance_id=instance_id,
        revision=1,
        symbol="XAUUSDT",
        timeframe="5M",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED.value,
        as_of_ms=known_at,
        evidence_cutoff_ms=known_at,
        evidence_digest=f"d_{event_id}",
        created_at=known_at,
        payload=json.dumps(snapshot.to_dict()),
    )
    db.add(db_row)

    payload = {
        "event_kind": "scenario.confirmed",
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "scenario_id": scenario_id,
        "instance_id": instance_id,
        "snapshot_id": snap_id,
        "revision": 1,
        "config_version": 1,
        "profile": "DEFAULT",
        "evidence_digest": f"d_{event_id}",
        "sweep_id": sw_id,
        "break_id": br_id,
        "fvg_id": fvg_id,
        "retest_id": rt_id,
        "evidence_chain_ids": [sw_id, br_id, fvg_id, rt_id],
        "occurred_at_ms": occurred_at,
        "known_at_ms": known_at,
        "schema_version": "1.0.0",
    }
    event = models.DomainEvent(
        event_id=event_id,
        sequence=1,
        schema_version="1.0.0",
        event_type="scenario.confirmed",
        aggregate_id=instance_id,
        aggregate_version=1,
        occurred_at=occurred_at,
        published_at=known_at,
        payload=json.dumps(payload),
    )
    db.add(event)
    db.commit()


def test_state01_later_genuine_confirmation_promotes_outcome_and_cohort(isolated_db: Session):
    """Case STATE01 (PHẦN 027, 028, 088, 108):
    Forecast created at as_of=1000 without proof (UNVERIFIED).
    Genuine confirmation event occurs later at 1200 (known at 1300).
    Target hit candle at 1600.
    Observer at as_of=2000 must:
    1. Detect genuine confirmation in window
    2. Transition verification_status from UNVERIFIED to VERIFIED/CONFIRMED
    3. Evaluate outcome to CONFIRMED_TARGET_BEFORE_INVALIDATION
    4. Cohort eligibility must mark row as ELIGIBLE
    5. summarize_forecast_observations must record n_resolved_valid=1, n_confirmed_target=1.
    """
    # 1. Create forecast without proof at as_of=1_000_000
    fc = create_forecast_snapshot(
        isolated_db,
        forecast={
            "symbol": "XAUUSDT",
            "session": "NY",
            "session_id": "sc-ny-state01",
            "instance_id": "inst-state01-long",
            "config_version": 1,
            "profile": "DEFAULT",
            "as_of_ms": 1_000_000,
            "hypothesis_type": "PRIMARY",
            "primary": {
                "direction": "LONG",
                "target": {"price": 4200.0, "timeframe": "5M"},
                "invalidation_price": 4170.0,
            },
            "target_price": 4200.0,
            "invalidation_price": 4170.0,
            "direction": "LONG",
        },
        proof_id=None,
    )
    assert fc.outcome_status == "PENDING"
    assert fc.verification_status == "UNVERIFIED"

    # 2. Seed later genuine confirmation event (known at 1_300_000)
    _seed_genuine_confirmation_event(
        isolated_db,
        event_id="conf_state01_01",
        instance_id="inst-state01-long",
        occurred_at=1_200_000,
        known_at=1_300_000,
    )

    # 3. Seed 5M candles (step=300_000ms): 1_000_000, 1_300_000, 1_600_000 (hits 4200 target at 1_600_000)
    c1 = models.Candle(symbol="XAUUSDT", timeframe="5M", timestamp=1_000_000, open=4180.0, high=4185.0, low=4175.0, close=4182.0, is_closed=True)
    c2 = models.Candle(symbol="XAUUSDT", timeframe="5M", timestamp=1_300_000, open=4182.0, high=4190.0, low=4180.0, close=4188.0, is_closed=True)
    c3 = models.Candle(symbol="XAUUSDT", timeframe="5M", timestamp=1_600_000, open=4188.0, high=4205.0, low=4185.0, close=4202.0, is_closed=True)
    isolated_db.add_all([c1, c2, c3])
    isolated_db.commit()

    # 4. Observer processes pending forecasts at as_of=2_000_000
    res = process_pending_forecasts(isolated_db, symbol="XAUUSDT", as_of_ms=2_000_000)
    assert res["updated"] == 1

    # Reload forecast observation
    obs = isolated_db.query(models.ForecastObservation).filter(models.ForecastObservation.id == fc.id).first()
    assert obs is not None
    assert obs.outcome_status == "CONFIRMED_TARGET_BEFORE_INVALIDATION"
    # Verification status must NOT be UNVERIFIED anymore
    assert obs.verification_status in ("CONFIRMED", "VERIFIED")

    # 5. Cohort eligibility & Summary
    elig = assess_forecast_cohort_eligibility(isolated_db, obs)
    assert elig.is_eligible is True

    summary = summarize_forecast_observations(isolated_db, symbol="XAUUSDT")
    assert summary["n_resolved_valid"] == 1
    assert summary["n_confirmed_target"] == 1


def test_state02_terminal_retry_and_atomicity(isolated_db: Session):
    """Case STATE02 (PHẦN 029, 109):
    Concurrent or repeated observer runs do not duplicate outcomes or mutate resolved records.
    """
    obs = models.ForecastObservation(
        id="fc_resolved_already",
        forecast_id="fc_resolved_already",
        symbol="XAUUSDT",
        session="NY",
        session_id="sc-test-resolved",
        as_of_ms=1000,
        context_cutoff_ms=1000,
        content_digest="digest_resolved_already",
        hypothesis_type="PRIMARY",
        outcome_status="CONFIRMED_TARGET_BEFORE_INVALIDATION",
        verification_status="VERIFIED",
        invalidation_price=4170.0,
        payload_json=json.dumps({"confirmed_at_ms": 1300, "confirmation_proof_id": "conf_dummy"}),
        created_at=1000,
    )
    isolated_db.add(obs)
    isolated_db.commit()

    # Running observer does not reprocess already resolved record
    res = process_pending_forecasts(isolated_db, symbol="XAUUSDT", as_of_ms=3000)
    assert res["updated"] == 0


def test_legacy01_no_proof_legacy_verified_excluded(isolated_db: Session):
    """Case LEGACY01 (PHẦN 031, 110):
    A legacy record with stored verification_status='VERIFIED' but with no proof reference
    must be excluded by assess_forecast_cohort_eligibility with reason and not count in n_resolved_valid.
    """
    legacy_row = models.ForecastObservation(
        id="fc_legacy_fake_verified",
        forecast_id="fc_legacy_fake_verified",
        symbol="XAUUSDT",
        session="NY",
        session_id="sc-test-legacy",
        as_of_ms=1000,
        context_cutoff_ms=1000,
        content_digest="digest_legacy_fake_verified",
        hypothesis_type="PRIMARY",
        outcome_status="CONFIRMED_TARGET_BEFORE_INVALIDATION",
        verification_status="VERIFIED",
        invalidation_price=4170.0,
        payload_json=json.dumps({
            # No confirmation_proof_id or confirmation_event_id!
            "target_price": 4200.0,
        }),
        created_at=1000,
    )
    isolated_db.add(legacy_row)
    isolated_db.commit()

    elig = assess_forecast_cohort_eligibility(isolated_db, legacy_row)
    assert elig.is_eligible is False
    assert "NO_PROOF" in elig.reason_code or "MISSING_PROOF" in elig.reason_code

    summary = summarize_forecast_observations(isolated_db, symbol="XAUUSDT")
    assert summary["n_resolved_valid"] == 0
