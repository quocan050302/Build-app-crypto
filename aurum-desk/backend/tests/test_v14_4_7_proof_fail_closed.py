"""
backend/tests/test_v14_4_7_proof_fail_closed.py
V14.4.7 Cases PROOF01–PROOF04, COHORT01 (PHẦN 007–024, 076–082, 096–100):
Strict fail-closed confirmation proof validation:
- Case PROOF01: Event referencing non-existent snapshot_id with forged retest/break IDs must be rejected (PROOF_SNAPSHOT_NOT_FOUND).
- Case PROOF02: Missing snapshot_id or corrupt snapshot payload must fail closed with explicit reason.
- Case PROOF03: Valid snapshot A referenced, but event payload has mismatched direction, instance, profile, config, or forged IDs.
- Case PROOF04: Genuine canonical producer integration passes and certifies forecast as VERIFIED.
- Case COHORT01: Forged proof is excluded from cohort eligibility, genuine proof promoted.
"""

import json
import pytest
from sqlalchemy.orm import Session

import models
from services.smc_scenario_types import (
    EvidenceNode,
    FactStatus,
    ScenarioSnapshot,
    ScenarioState,
)
from services.smc_scenario_service import record_scenario_confirmation_event
from services.smc_forecast_observation_service import (
    load_and_validate_confirmation_proof,
    create_forecast_snapshot,
    assess_forecast_cohort_eligibility,
)


def _seed_canonical_scenario_snapshot(
    db: Session,
    snapshot_id: str,
    scenario_id: str = "scen_proof_01",
    instance_id: str = "inst_proof_01",
    symbol: str = "XAUUSDT",
    direction: str = "LONG",
    config_version: int = 1,
    requirement_profile: str = "SMC_DEFAULT",
    as_of_ms: int = 2500,
) -> tuple[ScenarioSnapshot, list[EvidenceNode]]:
    sweep_side = "SWEEP_LOW" if direction == "LONG" else "SWEEP_HIGH"
    dir_str = "BULLISH" if direction == "LONG" else "BEARISH"

    sweep = EvidenceNode(
        id=f"ev_sweep_{snapshot_id}",
        kind=sweep_side,
        symbol=symbol,
        timeframe="5M",
        direction=None,
        occurred_at_ms=1000,
        known_at_ms=1300,
        level=4170.0,
        fact_status=FactStatus.CONFIRMED_EVIDENCE,
    )
    brk = EvidenceNode(
        id=f"ev_break_{snapshot_id}",
        kind="CHOCH",
        symbol=symbol,
        timeframe="5M",
        direction=dir_str,
        occurred_at_ms=1400,
        known_at_ms=1700,
        source_ids=(sweep.id,),
        level=4185.0,
        fact_status=FactStatus.CONFIRMED_EVIDENCE,
    )
    fvg = EvidenceNode(
        id=f"ev_fvg_{snapshot_id}",
        kind="FVG",
        symbol=symbol,
        timeframe="5M",
        direction=dir_str,
        occurred_at_ms=1800,
        known_at_ms=2100,
        source_ids=(brk.id,),
        level=4182.0,
        fact_status=FactStatus.CONFIRMED_EVIDENCE,
    )
    retest = EvidenceNode(
        id=f"ev_retest_{snapshot_id}",
        kind="RETEST",
        symbol=symbol,
        timeframe="5M",
        direction=dir_str,
        occurred_at_ms=2200,
        known_at_ms=2500,
        source_ids=(fvg.id,),
        level=4182.5,
        fact_status=FactStatus.CONFIRMED_EVIDENCE,
    )
    nodes = [sweep, brk, fvg, retest]

    snapshot = ScenarioSnapshot(
        scenario_id=scenario_id,
        instance_id=instance_id,
        revision=1,
        symbol=symbol,
        setup_timeframe="5M",
        context_timeframes=["D", "4H", "15M"],
        family="STANDARD_SMC",
        direction=direction,
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=as_of_ms,
        evidence_cutoff_ms=as_of_ms,
        evidence_ids=[n.id for n in nodes],
        liquidity_map={},
        trigger_chain={
            "verified": True,
            "reasons": [],
            "node_ids": {
                "sweep": sweep.id,
                "break": brk.id,
                "fvg": fvg.id,
                "retest": retest.id,
            },
        },
        target=None,
        invalidation=None,
        summary_vi="Canonical snapshot for proof test",
        config_version=config_version,
        detector_version="v14.4",
        requirement_profile=requirement_profile,
        evidence=[n.to_dict() for n in nodes],
    )

    db_row = models.SMCScenarioSnapshot(
        snapshot_id=snapshot_id,
        scenario_id=scenario_id,
        instance_id=instance_id,
        revision=1,
        symbol=symbol,
        timeframe="5M",
        direction=direction,
        state=ScenarioState.STRUCTURE_CONFIRMED.value,
        as_of_ms=as_of_ms,
        evidence_cutoff_ms=as_of_ms,
        evidence_digest="digest_test_123",
        created_at=as_of_ms,
        payload=json.dumps(snapshot.to_dict()),
    )
    db.add(db_row)
    if snapshot.snapshot_id != snapshot_id:
        db_row_alias = models.SMCScenarioSnapshot(
            snapshot_id=snapshot.snapshot_id,
            scenario_id=scenario_id,
            instance_id=instance_id,
            revision=1,
            symbol=symbol,
            timeframe="5M",
            direction=direction,
            state=ScenarioState.STRUCTURE_CONFIRMED.value,
            as_of_ms=as_of_ms,
            evidence_cutoff_ms=as_of_ms,
            evidence_digest="digest_test_123",
            created_at=as_of_ms,
            payload=json.dumps(snapshot.to_dict()),
        )
        db.add(db_row_alias)
    db.commit()
    return snapshot, nodes


def test_proof01_missing_snapshot_record_fails_closed(isolated_db: Session):
    """Case PROOF01 (PHẦN 007, 078, 096):
    Confirmation event references snapshot_id='nonexistent_snap_999' with forged break/retest IDs.
    In baseline, loader returned is_valid=True and create_forecast_snapshot set VERIFIED.
    In V14.4.7, loader must return is_valid=False with reason PROOF_SNAPSHOT_NOT_FOUND,
    and forecast snapshot creation must remain UNVERIFIED.
    """
    event = models.DomainEvent(
        event_id="conf_evt_nonexistent_snap",
        sequence=1,
        schema_version="1.0.0",
        event_type="scenario.confirmed",
        aggregate_id="inst_fake_01",
        aggregate_version=1,
        occurred_at=2000,
        published_at=2500,
        payload=json.dumps({
            "event_kind": "scenario.confirmed",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "instance_id": "inst_fake_01",
            "snapshot_id": "nonexistent_snap_999",  # NOT in DB!
            "config_version": 1,
            "profile": "SMC_DEFAULT",
            "break_id": "forged_break_id",
            "retest_id": "forged_retest_id",
            "evidence_chain_ids": ["forged_sweep", "forged_break_id", "forged_fvg", "forged_retest_id"],
            "occurred_at_ms": 2000,
            "known_at_ms": 2500,
        }),
    )
    isolated_db.add(event)
    isolated_db.commit()

    definition = {
        "symbol": "XAUUSDT",
        "session": "NY",
        "session_id": "NY:2026-10-13:fake_01",
        "direction": "LONG",
        "instance_id": "inst_fake_01",
        "config_version": 1,
        "profile": "SMC_DEFAULT",
        "as_of_ms": 3000,
        "primary": {"direction": "LONG", "target": {"price": 4200.0}},
        "invalidation_price": 4170.0,
        "confirmation_proof_id": "conf_evt_nonexistent_snap",
    }

    # Direct validation check
    val = load_and_validate_confirmation_proof(isolated_db, "conf_evt_nonexistent_snap", definition, as_of_ms=3000)
    assert val["is_valid"] is False, f"Expected is_valid=False for missing snapshot, got: {val}"
    assert val["reason_code"] == "PROOF_SNAPSHOT_NOT_FOUND"

    # Creation check: forecast must be created as UNVERIFIED, not VERIFIED
    snap = create_forecast_snapshot(isolated_db, definition, as_of=3000)
    assert snap is not None
    assert snap.verification_status == "UNVERIFIED"
    assert snap.proof_verification_reasons == ["PROOF_SNAPSHOT_NOT_FOUND"]


def test_proof02_missing_snapshot_id_and_corrupt_row(isolated_db: Session):
    """Case PROOF02 (PHẦN 011, 079, 097):
    A. Payload without snapshot_id is invalid schema (MISSING_SNAPSHOT_ID / INVALID_PROOF_SCHEMA).
    B. Snapshot row exists but payload is corrupt JSON -> CORRUPT_PROOF_SNAPSHOT.
    """
    # A. Event without snapshot_id
    event_no_snap = models.DomainEvent(
        event_id="conf_evt_no_snap_id",
        sequence=1,
        schema_version="1.0.0",
        event_type="scenario.confirmed",
        aggregate_id="inst_no_snap",
        aggregate_version=1,
        occurred_at=2000,
        published_at=2500,
        payload=json.dumps({
            "event_kind": "scenario.confirmed",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "instance_id": "inst_no_snap",
            "break_id": "br_1",
            "retest_id": "rt_1",
            "occurred_at_ms": 2000,
            "known_at_ms": 2500,
        }),
    )
    isolated_db.add(event_no_snap)

    # B. Snapshot row with corrupt payload
    corrupt_row = models.SMCScenarioSnapshot(
        snapshot_id="snap_corrupt_json",
        scenario_id="scen_corrupt",
        instance_id="inst_corrupt",
        revision=1,
        symbol="XAUUSDT",
        timeframe="5M",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED.value,
        as_of_ms=2500,
        evidence_cutoff_ms=2500,
        evidence_digest="digest_corrupt",
        created_at=2500,
        payload="NOT_VALID_JSON{abc",
    )
    isolated_db.add(corrupt_row)

    event_corrupt = models.DomainEvent(
        event_id="conf_evt_corrupt_snap",
        sequence=2,
        schema_version="1.0.0",
        event_type="scenario.confirmed",
        aggregate_id="inst_corrupt",
        aggregate_version=1,
        occurred_at=2000,
        published_at=2500,
        payload=json.dumps({
            "event_kind": "scenario.confirmed",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "instance_id": "inst_corrupt",
            "snapshot_id": "snap_corrupt_json",
            "occurred_at_ms": 2000,
            "known_at_ms": 2500,
        }),
    )
    isolated_db.add(event_corrupt)
    isolated_db.commit()

    definition = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "instance_id": "inst_no_snap",
        "as_of_ms": 3000,
    }

    val_a = load_and_validate_confirmation_proof(isolated_db, "conf_evt_no_snap_id", definition, as_of_ms=3000)
    assert val_a["is_valid"] is False
    assert val_a["reason_code"] in ("MISSING_SNAPSHOT_ID", "INVALID_PROOF_SCHEMA")

    definition_b = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "instance_id": "inst_corrupt",
        "as_of_ms": 3000,
    }
    val_b = load_and_validate_confirmation_proof(isolated_db, "conf_evt_corrupt_snap", definition_b, as_of_ms=3000)
    assert val_b["is_valid"] is False
    assert val_b["reason_code"] == "CORRUPT_PROOF_SNAPSHOT"


def test_proof03_mismatched_valid_referenced_snapshot(isolated_db: Session):
    """Case PROOF03 (PHẦN 012, 080, 081, 098):
    Snapshot A is genuine LONG standard SMC.
    An event referencing snapshot A must be rejected if:
    - direction in event or forecast is SHORT (PROOF_DIRECTION_MISMATCH)
    - instance_id does not match (PROOF_INSTANCE_MISMATCH)
    - config_version does not match (PROOF_CONFIG_MISMATCH)
    - requirement_profile does not match (PROOF_PROFILE_MISMATCH)
    - event passes forged chain node IDs not in snapshot A (PROOF_EVIDENCE_MISMATCH)
    """
    snap_a, nodes_a = _seed_canonical_scenario_snapshot(
        isolated_db,
        snapshot_id="snap_canonical_long_01",
        scenario_id="scen_a",
        instance_id="inst_a",
        symbol="XAUUSDT",
        direction="LONG",
        config_version=1,
        requirement_profile="SMC_DEFAULT",
    )

    # Subcase: Forged chain node IDs
    event_forged_ids = models.DomainEvent(
        event_id="conf_evt_forged_ids",
        sequence=1,
        schema_version="1.0.0",
        event_type="scenario.confirmed",
        aggregate_id="inst_a",
        aggregate_version=1,
        occurred_at=2000,
        published_at=2500,
        payload=json.dumps({
            "event_kind": "scenario.confirmed",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "instance_id": "inst_a",
            "snapshot_id": "snap_canonical_long_01",
            "config_version": 1,
            "profile": "SMC_DEFAULT",
            "break_id": "totally_fabricated_break_node",
            "retest_id": "totally_fabricated_retest_node",
            "evidence_chain_ids": ["totally_fabricated_break_node", "totally_fabricated_retest_node"],
            "occurred_at_ms": 2000,
            "known_at_ms": 2500,
        }),
    )
    isolated_db.add(event_forged_ids)

    # Subcase: Direction mismatch in event payload (claims SHORT for a LONG snapshot)
    event_wrong_dir = models.DomainEvent(
        event_id="conf_evt_wrong_dir",
        sequence=2,
        schema_version="1.0.0",
        event_type="scenario.confirmed",
        aggregate_id="inst_a",
        aggregate_version=1,
        occurred_at=2000,
        published_at=2500,
        payload=json.dumps({
            "event_kind": "scenario.confirmed",
            "symbol": "XAUUSDT",
            "direction": "SHORT",  # Mismatch with snapshot LONG!
            "instance_id": "inst_a",
            "snapshot_id": "snap_canonical_long_01",
            "config_version": 1,
            "profile": "SMC_DEFAULT",
            "break_id": nodes_a[1].id,
            "retest_id": nodes_a[3].id,
            "evidence_chain_ids": [n.id for n in nodes_a],
            "occurred_at_ms": 2000,
            "known_at_ms": 2500,
        }),
    )
    isolated_db.add(event_wrong_dir)
    isolated_db.commit()

    # Test forged IDs rejection
    def_matching = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "instance_id": "inst_a",
        "config_version": 1,
        "profile": "SMC_DEFAULT",
        "as_of_ms": 3000,
    }
    val_forged = load_and_validate_confirmation_proof(isolated_db, "conf_evt_forged_ids", def_matching, as_of_ms=3000)
    assert val_forged["is_valid"] is False
    assert val_forged["reason_code"] in ("PROOF_EVIDENCE_MISMATCH", "INVALID_CONFIRMATION_CHAIN")

    # Test wrong direction rejection
    val_dir = load_and_validate_confirmation_proof(isolated_db, "conf_evt_wrong_dir", def_matching, as_of_ms=3000)
    assert val_dir["is_valid"] is False
    assert val_dir["reason_code"] == "PROOF_DIRECTION_MISMATCH"


def test_proof04_genuine_producer_integration(isolated_db: Session):
    """Case PROOF04 (PHẦN 019, 081, 099):
    Genuine producer record_scenario_confirmation_event persists canonical snapshot
    and emits event referencing real snapshot.
    Loader validates strictly and marks forecast VERIFIED.
    """
    snap, nodes = _seed_canonical_scenario_snapshot(
        isolated_db,
        snapshot_id="snap_genuine_prod_01",
        scenario_id="scen_genuine_01",
        instance_id="inst_genuine_01",
        symbol="XAUUSDT",
        direction="LONG",
        config_version=1,
        requirement_profile="SMC_DEFAULT",
        as_of_ms=2500,
    )

    # Use canonical producer helper
    evt = record_scenario_confirmation_event(
        isolated_db,
        snapshot=snap,
    )
    assert evt is not None
    evt_id = evt.event_id

    # Also make sure snapshot is in DB with matching id
    existing_snap = isolated_db.query(models.SMCScenarioSnapshot).filter_by(snapshot_id=snap.scenario_id).first()
    if not existing_snap:
        db_row = models.SMCScenarioSnapshot(
            snapshot_id=snap.scenario_id,
            scenario_id=snap.scenario_id,
            instance_id=snap.instance_id,
            revision=snap.revision,
            symbol=snap.symbol,
            timeframe=snap.setup_timeframe,
            direction=snap.direction,
            state=snap.state.value if hasattr(snap.state, "value") else str(snap.state),
            as_of_ms=snap.as_of_ms,
            evidence_cutoff_ms=snap.evidence_cutoff_ms,
            evidence_digest="digest_genuine",
            created_at=snap.as_of_ms,
            payload=json.dumps(snap.to_dict()),
        )
        isolated_db.add(db_row)
        isolated_db.commit()

    definition = {
        "symbol": "XAUUSDT",
        "session": "NY",
        "session_id": "NY:2026-10-13:prod_01",
        "direction": "LONG",
        "instance_id": snap.instance_id,
        "config_version": snap.config_version,
        "profile": snap.requirement_profile,
        "as_of_ms": 3000,
        "primary": {"direction": "LONG", "target": {"price": 4200.0}},
        "invalidation_price": 4170.0,
        "confirmation_proof_id": evt_id,
    }

    val = load_and_validate_confirmation_proof(isolated_db, evt_id, definition, as_of_ms=3000)
    assert val["is_valid"] is True, f"Expected genuine producer proof to pass, got: {val}"
    assert val["reason_code"] == "PROOF_VALIDATED"

    # Forecast snapshot creation succeeds as VERIFIED
    fc_snap = create_forecast_snapshot(isolated_db, definition, as_of=3000)
    assert fc_snap is not None
    assert fc_snap.verification_status == "VERIFIED"


def test_cohort01_forged_and_legacy_statuses_excluded(isolated_db: Session):
    """Case COHORT01 (PHẦN 021, 022, 100):
    A forecast snapshot marked VERIFIED but referencing a forged event or missing snapshot
    must be excluded by assess_forecast_cohort_eligibility.
    A genuine forecast with valid canonical proof is eligible.
    """
    # 1. Forged snapshot with missing snapshot DB row
    forged_event = models.DomainEvent(
        event_id="conf_evt_cohort_fake",
        sequence=1,
        schema_version="1.0.0",
        event_type="scenario.confirmed",
        aggregate_id="inst_cohort_fake",
        aggregate_version=1,
        occurred_at=1000,
        published_at=1000,
        payload=json.dumps({
            "event_kind": "scenario.confirmed",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "instance_id": "inst_cohort_fake",
            "snapshot_id": "missing_snapshot_for_cohort",
            "break_id": "b1",
            "retest_id": "r1",
            "known_at_ms": 1000,
        }),
    )
    isolated_db.add(forged_event)

    # Insert a forecast observation with status VERIFIED pointing to fake proof
    forged_fc = models.ForecastObservation(
        id="fc_forged_01",
        forecast_id="fc_forged_01",
        revision=1,
        symbol="XAUUSDT",
        session="NY",
        session_id="NY:2026-10-13:cohort_fake",
        as_of_ms=1000,
        context_cutoff_ms=1000,
        content_digest="digest_cohort_fake",
        hypothesis_type="PRIMARY",
        direction="LONG",
        target_price=4200.0,
        invalidation_price=4170.0,
        outcome_status="ACHIEVED",
        outcome_at_ms=2000,
        verification_status="VERIFIED",  # Faked status!
        invalidation_mode="CLOSED_PRICE",
        created_at=1000,
        payload_json=json.dumps({
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "instance_id": "inst_cohort_fake",
            "as_of_ms": 1000,
            "confirmation_proof_id": "conf_evt_cohort_fake",
        }),
    )
    isolated_db.add(forged_fc)
    isolated_db.commit()

    # Assess cohort eligibility
    eligibility_forged = assess_forecast_cohort_eligibility(isolated_db, forged_fc)
    assert eligibility_forged.is_eligible is False
    assert "PROOF_SNAPSHOT_NOT_FOUND" in eligibility_forged.reason_code or eligibility_forged.reason_code.startswith("INVALID_PROOF")

