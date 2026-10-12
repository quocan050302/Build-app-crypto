import pytest
from services.smc_scenario_config import describe_model_requirements, active_scenario_config
from services.chart_evidence_service import read_snapshot_provenance
from services.smc_scenario_types import ScenarioSnapshot, ScenarioState
from services.smc_scenario_summary import build_entry_explanation

def test_describe_model_requirements():
    reqs = describe_model_requirements(active_scenario_config)
    assert reqs["config_version"] == active_scenario_config.config_version
    assert "sweep" in reqs
    assert "break" in reqs
    assert "fvg" in reqs
    assert reqs["m15_poi"]["importance"] == "SUPPORTING"

def test_read_snapshot_provenance():
    snap_dict = {
        "scenario_id": "test",
        "instance_id": "test",
        "state": "STRUCTURE_CONFIRMED",
        "evidence": [],
        "config_version": active_scenario_config.config_version,
        "requirement_profile": "V14.4_SMC_BASELINE"
    }
    prov = read_snapshot_provenance(snap_dict, active_scenario_config)
    assert prov["is_outdated_version"] is False
    assert prov["is_custom_profile"] is True

def test_build_entry_explanation():
    snap = ScenarioSnapshot.from_dict({
        "schema_version": "v14.4.1",
        "scenario_id": "test",
        "instance_id": "inst",
        "revision": "1",
        "symbol": "XAUUSDT",
        "setup_timeframe": "5M",
        "context_timeframes": ["H1", "H4"],
        "family": "STANDARD_SMC",
        "evidence_cutoff_ms": 1000,
        "evidence_ids": [],
        "liquidity_map": {},
        "trigger_chain": {},
        "invalidation": {},
        "summary_vi": "",
        "config_version": 1,
        "detector_version": "v14.4.1",
        "state": "STRUCTURE_CONFIRMED",
        "as_of_ms": 1000,
        "direction": "LONG",
        "evidence": [
            {"kind": "SWEEP_LOW", "extra": {"detail": "Sweep tại đáy Á"}},
            {"kind": "BOS", "extra": {"detail": "Break với nến M5 thân lớn"}}
        ],
        "target": {"price": 2600.0},
        "context": {"h4_bias": "BULLISH", "h1_alignment": "ALIGNED"}
    })
    
    explanation = build_entry_explanation(snap, None, None)
    assert explanation["context"]["h4_bias"] == "BULLISH"
    assert explanation["evidence_summary"]["sweep"] == "Sweep tại đáy Á"
    assert explanation["evidence_summary"]["break"] == "Break với nến M5 thân lớn"
    assert explanation["evidence_summary"]["fvg"] == "Không"
