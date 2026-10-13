"""
backend/tests/test_v14_4_6_strict_proof_producer.py
V14.4.6 Cases SMC01–SMC04 (PHẦN 008–025, 075–083, 104–107):
Strict confirmation chain validation, canonical producer contract (no .nodes/.profile dynamic fields),
and causality enforcement (sweep -> break -> FVG -> retest).
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
from services.smc_scenario_service import (
    extract_confirmation_chain,
    record_scenario_confirmation_event,
)
from services.smc_forecast_observation_service import (
    load_and_validate_confirmation_proof,
)


def _build_valid_chain_nodes(symbol="XAUUSDT", direction="LONG") -> list[EvidenceNode]:
    sweep_side = "SWEEP_LOW" if direction == "LONG" else "SWEEP_HIGH"
    dir_str = "BULLISH" if direction == "LONG" else "BEARISH"

    sweep = EvidenceNode(
        id=f"ev_sweep_{direction.lower()}",
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
        id=f"ev_break_{direction.lower()}",
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
        id=f"ev_fvg_{direction.lower()}",
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
        id=f"ev_retest_{direction.lower()}",
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
    return [sweep, brk, fvg, retest]


def test_smc01_transition_only_without_chain_fails(isolated_db: Session):
    """Case SMC01 (PHẦN 017, 104):
    An event with transition='ANY_TEXT' and known_at but without genuine SMC chain/binding
    must be rejected by proof validator and cannot certify forecast as VERIFIED.
    """
    event = models.DomainEvent(
        event_id="conf_fake_transition_01",
        sequence=1,
        schema_version="1.0.0",
        event_type="scenario.confirmed",
        aggregate_id="inst_fake",
        aggregate_version=1,
        occurred_at=1000,
        published_at=1000,
        payload=json.dumps({
            "event_kind": "scenario.confirmed",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "instance_id": "inst_fake",
            "config_version": 1,
            "profile": "SMC_DEFAULT",
            "transition": "ANY_TEXT",
            "occurred_at_ms": 1000,
            "known_at_ms": 1000,
        }),
    )
    isolated_db.add(event)
    isolated_db.commit()

    proof_res = load_and_validate_confirmation_proof(
        isolated_db,
        "conf_fake_transition_01",
        {
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "instance_id": "inst_fake",
            "config_version": 1,
            "profile": "SMC_DEFAULT",
            "as_of_ms": 2000,
        },
        as_of_ms=2000,
    )

    assert proof_res["is_valid"] is False
    assert proof_res["reason_code"] in (
        "INCOMPLETE_CONFIRMATION_CHAIN",
        "INVALID_OR_MISSING_CONFIRMATION_CHAIN",
    )


def test_smc02_missing_or_mismatched_required_binding(isolated_db: Session):
    """Case SMC02 (PHẦN 018, 105):
    Missing required binding fields or mismatches in direction, instance, or profile
    must be rejected by proof validation.
    """
    event = models.DomainEvent(
        event_id="conf_mismatch_01",
        sequence=1,
        schema_version="1.0.0",
        event_type="scenario.confirmed",
        aggregate_id="inst_a",
        aggregate_version=1,
        occurred_at=1000,
        published_at=1000,
        payload=json.dumps({
            "event_kind": "scenario.confirmed",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "instance_id": "inst_a",
            "config_version": 1,
            "profile": "SMC_DEFAULT",
            "sweep_id": "sw_1",
            "break_id": "br_1",
            "fvg_id": "fvg_1",
            "retest_id": "rt_1",
            "evidence_chain_ids": ["sw_1", "br_1", "fvg_1", "rt_1"],
            "occurred_at_ms": 1000,
            "known_at_ms": 1000,
        }),
    )
    isolated_db.add(event)
    isolated_db.commit()

    # Direction mismatch: definition is SHORT, proof is LONG
    res_dir = load_and_validate_confirmation_proof(
        isolated_db,
        "conf_mismatch_01",
        {
            "symbol": "XAUUSDT",
            "direction": "SHORT",
            "instance_id": "inst_a",
            "config_version": 1,
            "profile": "SMC_DEFAULT",
            "as_of_ms": 2000,
        },
        as_of_ms=2000,
    )
    assert res_dir["is_valid"] is False
    assert res_dir["reason_code"] == "PROOF_DIRECTION_MISMATCH"

    # Instance mismatch
    res_inst = load_and_validate_confirmation_proof(
        isolated_db,
        "conf_mismatch_01",
        {
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "instance_id": "inst_b",
            "config_version": 1,
            "profile": "SMC_DEFAULT",
            "as_of_ms": 2000,
        },
        as_of_ms=2000,
    )
    assert res_inst["is_valid"] is False
    assert res_inst["reason_code"] == "PROOF_INSTANCE_MISMATCH"


def test_smc03_canonical_producer_uses_evidence_and_profile_no_dynamic_nodes(isolated_db: Session):
    """Case SMC03 (PHẦN 008, 010, 014, 076, 080, 106):
    ScenarioSnapshot canonical contract has .evidence and .requirement_profile (no .nodes/.profile).
    record_scenario_confirmation_event must extract chain from canonical fields and emit authoritative event.
    """
    nodes = _build_valid_chain_nodes(symbol="XAUUSDT", direction="LONG")
    sweep, brk, fvg, retest = nodes

    snapshot = ScenarioSnapshot(
        scenario_id="scen_xau_ny_long",
        instance_id="inst_xau_ny_long_01",
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="5M",
        context_timeframes=["D", "4H", "15M"],
        family="STANDARD_SMC",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=2500,
        evidence_cutoff_ms=2500,
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
        summary_vi="Canonical snapshot test",
        config_version=1,
        detector_version="v14.4",
        requirement_profile="SMC_DEFAULT",
        evidence=[n.to_dict() for n in nodes],
    )

    # Ensure NO dynamic attributes exist on snapshot
    assert not hasattr(snapshot, "nodes")
    assert not hasattr(snapshot, "profile")

    # Extract confirmation chain
    chain_res = extract_confirmation_chain(snapshot)
    assert chain_res.is_valid is True
    assert chain_res.retest_node is not None
    assert chain_res.retest_node.id == retest.id

    # Emit confirmation event
    event = record_scenario_confirmation_event(isolated_db, snapshot, transition="STRUCTURE_CONFIRMED")
    assert event is not None
    assert event.event_type == "scenario.confirmed"
    payload = json.loads(event.payload)
    assert payload["sweep_id"] == sweep.id
    assert payload["break_id"] == brk.id
    assert payload["fvg_id"] == fvg.id
    assert payload["retest_id"] == retest.id
    assert payload["profile"] == "SMC_DEFAULT"
    assert payload["occurred_at_ms"] == retest.occurred_at_ms
    assert payload["known_at_ms"] == retest.known_at_ms


def test_smc04_chain_graph_causality_validation():
    """Case SMC04 (PHẦN 011, 012, 077, 107):
    Causality violations (e.g. sweep known after break or break before sweep) must fail extraction.
    """
    nodes = _build_valid_chain_nodes(symbol="XAUUSDT", direction="LONG")
    sweep, brk, fvg, retest = nodes

    # Invert causality: break occurs BEFORE sweep
    inverted_brk = EvidenceNode(
        id="ev_break_inverted",
        kind="CHOCH",
        symbol="XAUUSDT",
        timeframe="5M",
        direction="BULLISH",
        occurred_at_ms=800,  # earlier than sweep (1000)
        known_at_ms=900,
        source_ids=(sweep.id,),
        level=4185.0,
        fact_status=FactStatus.CONFIRMED_EVIDENCE,
    )
    bad_nodes = [sweep, inverted_brk, fvg, retest]

    snapshot = ScenarioSnapshot(
        scenario_id="scen_xau_ny_long",
        instance_id="inst_xau_ny_long_02",
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="5M",
        context_timeframes=["D", "4H", "15M"],
        family="STANDARD_SMC",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=2500,
        evidence_cutoff_ms=2500,
        evidence_ids=[n.id for n in bad_nodes],
        liquidity_map={},
        trigger_chain={
            "verified": True,
            "reasons": [],
            "node_ids": {
                "sweep": sweep.id,
                "break": inverted_brk.id,
                "fvg": fvg.id,
                "retest": retest.id,
            },
        },
        target=None,
        invalidation=None,
        summary_vi="Inverted causality test",
        config_version=1,
        detector_version="v14.4",
        requirement_profile="SMC_DEFAULT",
        evidence=[n.to_dict() for n in bad_nodes],
    )

    chain_res = extract_confirmation_chain(snapshot)
    assert chain_res.is_valid is False
    assert any("SWEEP" in r or "ORDER" in r for r in chain_res.reasons)
