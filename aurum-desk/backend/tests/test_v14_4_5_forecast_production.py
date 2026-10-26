"""
backend/tests/test_v14_4_5_forecast_production.py
V14.4.5 — Cases F01, F02, F03: Forecast Proof Pipeline & SMC Producer Verification.

Covers PHẦN 037–054, 094–101, 112–114:
Case F01 (PHẦN 112): Magic proof ID absent:
- When DomainEvent 'proof-retest-fvg-01' is absent from DB, calling create_forecast_snapshot
  yields UNVERIFIED.
- Unrelated event types (e.g. 'policy.upgraded', 'QUOTE_UPDATE') are rejected even if event exists.
- Only genuine 'scenario.confirmed' event with complete payload validates.

Case F02 (PHẦN 113): Exact binding, normalization & causal cutoff:
- Mismatched direction: nested primary.direction 'SHORT' with proof 'LONG' is rejected.
- Mismatched instance_id or config_version or profile is rejected.
- Future proof (known_at > as_of_ms) is rejected.

Case F03 (PHẦN 114): End-to-end SMC scenario confirmation to forecast summary:
- Detector confirmation event produced via record_scenario_confirmation_event.
- Forecast created referencing this genuine confirmation is VERIFIED.
- Observer loads ForecastEvidenceWindow, causally merges confirmation event and candles,
  and evaluates to CONFIRMED_TARGET_BEFORE_INVALIDATION.
- Cohort eligibility excludes legacy magic-ID unverified rows from summary stats.
"""
import json
from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session

import models
from services.smc_forecast_observation_service import (
    create_forecast_snapshot,
    evaluate_forecast_outcome,
    load_and_validate_confirmation_proof,
    normalize_forecast_identity,
    load_forecast_evidence_window,
    process_pending_forecasts,
    summarize_forecast_observations,
    assess_forecast_cohort_eligibility,
    ForecastOutcomeDefinition,
)
from services.smc_scenario_service import record_scenario_confirmation_event
from services.smc_scenario_types import (
    ScenarioSnapshot,
    ScenarioState,
    EvidenceNode,
    make_evidence_id,
)


def test_f01_magic_proof_id_absent_yields_unverified(isolated_db: Session):
    """Case F01 (PHẦN 037, 112):
    Absent proof-retest-fvg-01 DomainEvent in DB results in UNVERIFIED forecast snapshot.
    No hardcoded bypass or test prefix allows it to pass.
    """
    # 1. Zero DomainEvent exists with id 'proof-retest-fvg-01'
    existing = isolated_db.query(models.DomainEvent).filter_by(event_id="proof-retest-fvg-01").first()
    assert existing is None

    forecast_data = {
        "symbol": "XAUUSDT",
        "session": "NY",
        "session_id": "NY:2026-10-13:f01-absent",
        "as_of_ms": 1000,
        "hypothesis_type": "PRIMARY",
        "primary": {
            "direction": "LONG",
            "target": {"price": 4200.0},
        },
        "invalidation_price": 4170.0,
        "confirmation_proof_id": "proof-retest-fvg-01",  # Magic ID but NOT in DB!
    }

    snap = create_forecast_snapshot(isolated_db, forecast_data, as_of=1000)
    assert snap is not None
    assert snap.verification_status == "UNVERIFIED"

    # 2. Even if DomainEvent exists, but is an unrelated event type (PHẦN 038)
    unrelated_event = models.DomainEvent(
        event_id="proof-retest-fvg-01",
        sequence=1,
        schema_version="1.0.0",
        event_type="policy.upgraded",  # Unrelated event!
        aggregate_id="policy-01",
        aggregate_version=1,
        occurred_at=900,
        published_at=1000,
        payload=json.dumps({"symbol": "XAUUSDT", "direction": "LONG"}),
    )
    isolated_db.add(unrelated_event)
    isolated_db.commit()

    val_res = load_and_validate_confirmation_proof(isolated_db, "proof-retest-fvg-01", forecast_data, as_of_ms=1000)
    assert val_res["is_valid"] is False
    assert val_res["reason_code"] == "UNAUTHORIZED_EVENT_TYPE"

    snap2 = create_forecast_snapshot(isolated_db, forecast_data, as_of=1000)
    assert snap2 is not None
    assert snap2.verification_status == "UNVERIFIED"


def test_f02_exact_binding_and_causal_cutoff(isolated_db: Session):
    """Case F02 (PHẦN 039, 040, 041, 113):
    Exact binding to direction, instance_id, config_version, profile, and known_at <= as_of_ms.
    """
    # Create genuine snapshot in SMCScenarioSnapshot repository (PHẦN 059)
    from services.smc_scenario_types import ScenarioSnapshot, ScenarioState, EvidenceNode, FactStatus
    nodes = [
        EvidenceNode(id="s1", kind="SWEEP_LOW", symbol="XAUUSDT", timeframe="5M", direction=None, occurred_at_ms=800, known_at_ms=850, level=4170.0, fact_status=FactStatus.CONFIRMED_EVIDENCE),
        EvidenceNode(id="b1", kind="CHOCH", symbol="XAUUSDT", timeframe="5M", direction="BULLISH", occurred_at_ms=860, known_at_ms=900, source_ids=("s1",), level=4185.0, fact_status=FactStatus.CONFIRMED_EVIDENCE),
        EvidenceNode(id="f1", kind="FVG", symbol="XAUUSDT", timeframe="5M", direction="BULLISH", occurred_at_ms=910, known_at_ms=950, source_ids=("b1",), level=4182.0, fact_status=FactStatus.CONFIRMED_EVIDENCE),
        EvidenceNode(id="retest-1", kind="RETEST", symbol="XAUUSDT", timeframe="5M", direction="BULLISH", occurred_at_ms=960, known_at_ms=1000, source_ids=("f1",), level=4182.5, fact_status=FactStatus.CONFIRMED_EVIDENCE),
    ]
    snap_f02 = ScenarioSnapshot(
        scenario_id="scen-long-01",
        instance_id="inst-long-01",
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="5M",
        context_timeframes=["D", "4H", "15M"],
        family="STANDARD_SMC",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=1000,
        evidence_cutoff_ms=1000,
        evidence_ids=[n.id for n in nodes],
        liquidity_map={},
        trigger_chain={"verified": True, "reasons": [], "node_ids": {"sweep": "s1", "break": "b1", "fvg": "f1", "retest": "retest-1"}},
        target=None,
        invalidation=None,
        summary_vi="Snapshot",
        config_version="v1",
        detector_version="v14.4",
        requirement_profile="SMC_DEFAULT",
        evidence=[n.to_dict() for n in nodes],
    )
    snap_row = models.SMCScenarioSnapshot(
        snapshot_id="snap-long-01",
        scenario_id="scen-long-01",
        instance_id="inst-long-01",
        revision=1,
        symbol="XAUUSDT",
        timeframe="5M",
        direction="LONG",
        state="STRUCTURE_CONFIRMED",
        as_of_ms=1000,
        evidence_cutoff_ms=1000,
        evidence_digest="digest_test_f02",
        created_at=1000,
        payload=json.dumps(snap_f02.to_dict()),
    )
    isolated_db.add(snap_row)

    # Create genuine proof for LONG, instance "inst-long-01", config "v1", profile "SMC_DEFAULT"
    proof = models.DomainEvent(
        event_id="proof-genuine-long-01",
        sequence=1,
        schema_version="1.0.0",
        event_type="scenario.confirmed",
        aggregate_id="inst-long-01",
        aggregate_version=1,
        occurred_at=900,
        published_at=1000,
        payload=json.dumps({
            "event_kind": "scenario.confirmed",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "instance_id": "inst-long-01",
            "snapshot_id": "snap-long-01",
            "config_version": "v1",
            "profile": "SMC_DEFAULT",
            "retest_id": "retest-1",
            "evidence_chain_ids": ["s1", "b1", "f1", "retest-1"],
            "known_at_ms": 1000,
            "occurred_at_ms": 900,
        }),
    )
    isolated_db.add(proof)
    isolated_db.commit()

    # Subcase A: Direction mismatch (nested primary.direction SHORT vs proof LONG)
    fc_short = {
        "symbol": "XAUUSDT",
        "session_id": "NY:2026-10-13:f02-dir-mismatch",
        "instance_id": "inst-long-01",
        "primary": {"direction": "SHORT", "target": {"price": 4150.0}},
        "invalidation_price": 4190.0,
        "as_of_ms": 1000,
    }
    val_dir = load_and_validate_confirmation_proof(isolated_db, "proof-genuine-long-01", fc_short, as_of_ms=1000)
    assert val_dir["is_valid"] is False
    assert val_dir["reason_code"] == "PROOF_DIRECTION_MISMATCH"

    # Subcase B: Instance mismatch
    fc_wrong_inst = {
        "symbol": "XAUUSDT",
        "session_id": "NY:2026-10-13:f02-inst-mismatch",
        "instance_id": "inst-different-99",
        "primary": {"direction": "LONG", "target": {"price": 4200.0}},
        "invalidation_price": 4170.0,
        "as_of_ms": 1000,
    }
    val_inst = load_and_validate_confirmation_proof(isolated_db, "proof-genuine-long-01", fc_wrong_inst, as_of_ms=1000)
    assert val_inst["is_valid"] is False
    assert val_inst["reason_code"] == "PROOF_INSTANCE_MISMATCH"

    # Subcase C: Future known_at (as_of_ms is 800, but proof known_at is 1000)
    fc_matching = {
        "symbol": "XAUUSDT",
        "session_id": "NY:2026-10-13:f02-matching",
        "instance_id": "inst-long-01",
        "config_version": "v1",
        "profile": "SMC_DEFAULT",
        "primary": {"direction": "LONG", "target": {"price": 4200.0}},
        "invalidation_price": 4170.0,
        "as_of_ms": 800,  # BEFORE proof was known!
    }
    val_future = load_and_validate_confirmation_proof(isolated_db, "proof-genuine-long-01", fc_matching, as_of_ms=800)
    assert val_future["is_valid"] is False
    assert val_future["reason_code"] == "PROOF_KNOWN_AT_FUTURE"

    # Subcase D: Valid matching at as_of_ms=1000
    fc_matching["as_of_ms"] = 1000
    val_ok = load_and_validate_confirmation_proof(isolated_db, "proof-genuine-long-01", fc_matching, as_of_ms=1000)
    assert val_ok["is_valid"] is True
    assert val_ok["reason_code"] == "PROOF_VALIDATED"


def test_f03_genuine_producer_event_to_observer_and_summary(isolated_db: Session):
    """Case F03 (PHẦN 044, 047, 052, 114):
    Producer records scenario confirmation event -> Forecast snapshot created as VERIFIED ->
    Observer causal merge with candles -> Reaches CONFIRMED_TARGET_BEFORE_INVALIDATION ->
    Summary stats validates cohort eligibility and excludes unverified rows.
    """
    # 1. Producer records scenario confirmation event
    sweep_node = EvidenceNode(
        id="sweep_f03_01",
        kind="SWEEP_LOW",
        symbol="XAUUSDT",
        timeframe="5M",
        direction=None,
        occurred_at_ms=700,
        known_at_ms=800,
        level=4170.0,
    )
    break_node = EvidenceNode(
        id="break_f03_01",
        kind="CHOCH",
        symbol="XAUUSDT",
        timeframe="5M",
        direction="BULLISH",
        occurred_at_ms=900,
        known_at_ms=1000,
        source_ids=("sweep_f03_01",),
        level=4185.0,
    )
    fvg_node = EvidenceNode(
        id="fvg_f03_01",
        kind="FVG",
        symbol="XAUUSDT",
        timeframe="5M",
        direction="BULLISH",
        occurred_at_ms=1050,
        known_at_ms=1150,
        source_ids=("break_f03_01",),
        level=4182.0,
    )
    retest_node = EvidenceNode(
        id="retest_f03_01",
        kind="RETEST",
        symbol="XAUUSDT",
        timeframe="5M",
        direction="BULLISH",
        occurred_at_ms=1200,
        known_at_ms=1300,
        source_ids=("fvg_f03_01",),
        level=4180.0,
    )
    all_nodes = [sweep_node, break_node, fvg_node, retest_node]
    snap_producer = ScenarioSnapshot(
        scenario_id="sc-f03-ny",
        instance_id="inst-f03-long",
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="5M",
        context_timeframes=["D", "4H", "15M"],
        family="STANDARD_SMC",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=1300,
        evidence_cutoff_ms=1300,
        evidence_ids=[n.id for n in all_nodes],
        liquidity_map={},
        trigger_chain={
            "verified": True,
            "reasons": [],
            "node_ids": {
                "sweep": "sweep_f03_01",
                "break": "break_f03_01",
                "fvg": "fvg_f03_01",
                "retest": "retest_f03_01",
            },
        },
        target=None,
        invalidation=None,
        summary_vi="Test snapshot",
        config_version=1,
        detector_version="1.0",
        requirement_profile="SMC_DEFAULT",
        evidence=[n.to_dict() for n in all_nodes],
    )
    event = record_scenario_confirmation_event(isolated_db, snap_producer, transition="STRUCTURE_CONFIRMED")
    assert event is not None
    assert event.event_type == "scenario.confirmed"
    isolated_db.commit()

    # 2. Create forecast referencing genuine proof
    fc_payload = {
        "symbol": "XAUUSDT",
        "session": "NY",
        "session_id": "sc-f03-ny",
        "instance_id": "inst-f03-long",
        "config_version": "1",
        "profile": "SMC_DEFAULT",
        "as_of_ms": 1300,
        "hypothesis_type": "PRIMARY",
        "primary": {
            "direction": "LONG",
            "target": {"price": 4200.0},
        },
        "invalidation_price": 4170.0,
        "confirmation_proof_id": event.event_id,
    }
    forecast_snap = create_forecast_snapshot(isolated_db, fc_payload, as_of=1300)
    assert forecast_snap is not None
    assert forecast_snap.verification_status == "VERIFIED"
    isolated_db.commit()

    # 3. Add closed candles where target 4200 is touched AFTER confirmation
    c1 = models.Candle(
        symbol="XAUUSDT",
        timeframe="5M",
        timestamp=1500,
        open=4180.0,
        high=4185.0,
        low=4178.0,
        close=4182.0,
        volume=10.0,
        is_closed=True,
    )
    c2 = models.Candle(
        symbol="XAUUSDT",
        timeframe="5M",
        timestamp=301500,  # Continuous 5M step (1500 + 300_000)
        open=4182.0,
        high=4205.0,  # Hits target 4200!
        low=4181.0,
        close=4198.0,
        volume=12.0,
        is_closed=True,
    )
    isolated_db.add_all([c1, c2])
    isolated_db.commit()

    # 4. Observer processes pending forecast
    obs_res = process_pending_forecasts(isolated_db, symbol="XAUUSDT", as_of_ms=650000)
    assert obs_res["updated"] >= 1

    isolated_db.refresh(forecast_snap)
    assert forecast_snap.outcome_status == ForecastOutcomeDefinition.CONFIRMED_TARGET_BEFORE_INVALIDATION.value

    # 5. Summary statistics: verified record is included, legacy magic-ID row is excluded
    # Inject a legacy row that had fake VERIFIED without genuine DomainEvent
    legacy_fake = models.ForecastObservation(
        id="fc-legacy-fake-01",
        forecast_id="fc-fake",
        revision=1,
        symbol="XAUUSDT",
        session="NY",
        session_id="NY:legacy-fake",
        as_of_ms=1000,
        context_cutoff_ms=1000,
        content_digest="digest_fake",
        hypothesis_type="PRIMARY",
        direction="LONG",
        target_price=4200.0,
        invalidation_price=4170.0,
        verification_status="VERIFIED",  # Stored fake VERIFIED from baseline!
        outcome_status=ForecastOutcomeDefinition.CONFIRMED_TARGET_BEFORE_INVALIDATION.value,
        payload_json=json.dumps({"confirmation_proof_id": "nonexistent-proof-id"}),
        created_at=datetime.now(timezone.utc),
    )
    isolated_db.add(legacy_fake)
    isolated_db.commit()

    # Summary cohort validation must filter out legacy_fake from verified_records
    summary = summarize_forecast_observations(isolated_db, symbol="XAUUSDT")
    assert summary["n_confirmed_target"] == 1  # Only our genuine row, not the fake one!
    assert summary["eligible_count"] == 1
    assert summary["n_legacy_unverified"] == 1
