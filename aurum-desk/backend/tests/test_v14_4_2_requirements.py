import pytest
from services.smc_scenario_config import describe_model_requirements, active_scenario_config, ScenarioConfig
from services.chart_evidence_service import read_snapshot_provenance
from services.smc_scenario_types import ScenarioSnapshot, ScenarioState
from services.smc_scenario_summary import build_entry_explanation
from services.smc_scenario_binding import verify_scenario_binding


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
        "detector_version": active_scenario_config.detector_version,
        "requirement_profile": "V14.4_SMC_BASELINE"
    }
    prov = read_snapshot_provenance(snap_dict, active_scenario_config)
    assert prov["is_outdated_version"] is False
    assert prov["is_custom_profile"] is False
    assert prov["is_current"] is True
    assert prov["version_label"] == "Phiên bản hiện tại"


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
    assert explanation["evidence_summary"]["fvg"] == "Chưa có bằng chứng"


# -------------------------------------------------------------------------------------------------
# RQ01 - RQ05 Cases
# -------------------------------------------------------------------------------------------------

def test_rq01_default_profile():
    """RQ01: Verify default requirement profile descriptors."""
    reqs = describe_model_requirements(active_scenario_config)
    assert reqs["requirement_profile"] == "V14.4_SMC_BASELINE"
    assert reqs["config_version"] == 1
    assert reqs["sweep"]["importance"] == "REQUIRED"
    assert reqs["break"]["importance"] == "REQUIRED"
    assert reqs["displacement"]["importance"] == "REQUIRED"
    assert reqs["fvg"]["importance"] == "REQUIRED"
    assert reqs["retest"]["importance"] == "REQUIRED"
    assert reqs["target"]["importance"] == "REQUIRED"
    assert reqs["m15_poi"]["importance"] == "SUPPORTING"


def test_rq02_required_poi_false_missing_poi_advisory():
    """RQ02: When require_m15_poi is False, POI absence is supporting / advisory."""
    cfg = ScenarioConfig(require_m15_poi=False)
    reqs = describe_model_requirements(cfg)
    assert reqs["m15_poi"]["importance"] == "SUPPORTING"
    assert "alignment" in reqs["m15_poi"]["description"].lower()


def test_rq03_required_poi_true_missing_poi_block():
    """RQ03: When require_m15_poi is True, POI absence is required / blocking."""
    cfg = ScenarioConfig(require_m15_poi=True)
    reqs = describe_model_requirements(cfg)
    assert reqs["m15_poi"]["importance"] == "REQUIRED"


def test_rq04_stale_config_binding_block():
    """RQ04: Stale config or mismatched profile blocks binding."""
    from services.smc_scenario_binding import verify_scenario_binding
    from unittest.mock import MagicMock

    mock_db = MagicMock()
    mock_row = MagicMock()
    mock_row.evidence_digest = "test_digest"

    # Create snapshot with mismatched config_version
    snap = ScenarioSnapshot(
        scenario_id="scen-1",
        instance_id="inst-1",
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="5M",
        context_timeframes=["H1", "H4"],
        family="STANDARD_SMC",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=1000,
        evidence_cutoff_ms=1000,
        evidence_ids=[],
        liquidity_map={},
        trigger_chain={"verified": True, "node_ids": {}},
        target={"price": 2700.0},
        invalidation={"price": 2640.0},
        summary_vi="Test",
        config_version=999,  # Mismatched config version
        detector_version=active_scenario_config.detector_version,
        digest="test_digest",
        plan_fingerprint="LONG:2650.00:2640.00:2700.00",
        plan={"is_valid": True, "plan_fingerprint": "LONG:2650.00:2640.00:2700.00"},
        blockers=[],
        schema_version="v14.4.1",
        requirement_profile="UNKNOWN_CUSTOM_PROFILE",
    )
    mock_db.query().filter().first.return_value = mock_row

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.smc_scenario_binding.get_scenario_snapshot", lambda db, sid: mock_row)
        mp.setattr("services.smc_scenario_binding.snapshot_from_row", lambda row: snap)

        res = verify_scenario_binding(
            mock_db,
            snapshot_id="snap-1",
            direction="LONG",
            symbol="XAUUSDT",
            setup_instance_id="inst-1",
            plan_levels={"entry": 2650.0, "stop_loss": 2640.0, "take_profit": 2700.0},
            now_ms=2000,
            config=active_scenario_config
        )
        assert res.verified is False
        assert "CONFIG_VERSION_MISMATCH" in res.reasons
        assert "REQUIREMENT_PROFILE_MISMATCH" in res.reasons


def test_rq05_old_order_readable():
    """RQ05: Older order snapshot with old config version remains readable with provenance warnings."""
    historical_snap = {
        "scenario_id": "scen-old",
        "instance_id": "inst-old",
        "state": "STRUCTURE_CONFIRMED",
        "config_version": 0,  # Older version
        "detector_version": "v14.2",
        "requirement_profile": "DEFAULT",
        "evidence": [
            {"id": "ev-1", "kind": "SWEEP_LOW", "level": 2640.0}
        ],
        "requirements": {
            "config_version": 0,
            "detector_version": "v14.2",
            "requirement_profile": "DEFAULT"
        }
    }
    prov = read_snapshot_provenance(historical_snap, active_scenario_config)
    assert prov["is_outdated_version"] is True
    assert prov["snapshot_config_version"] == 0
    assert prov["current_config_version"] == active_scenario_config.config_version
    assert prov["warning"] is not None
    assert "Luật SMC đã thay đổi" in prov["warning"]
    assert prov["historical_requirements"]["detector_version"] == "v14.2"


def test_p01_same_config_detector_profile():
    """P01 / TC-PROV-CURRENT: Identical config, detector, and baseline profile -> is_current=True."""
    snap = {
        "config_version": active_scenario_config.config_version,
        "detector_version": active_scenario_config.detector_version,
        "requirement_profile": "DEFAULT",
    }
    prov = read_snapshot_provenance(snap, active_scenario_config)
    assert prov["is_current"] is True
    assert prov["version_label"] == "Phiên bản hiện tại"
    assert prov["warning"] is None
    assert prov["is_outdated_version"] is False


def test_p02_different_detector_version():
    """P02: Outdated detector version -> is_current=False."""
    snap = {
        "config_version": active_scenario_config.config_version,
        "detector_version": "v14.0-legacy",
        "requirement_profile": "DEFAULT",
    }
    prov = read_snapshot_provenance(snap, active_scenario_config)
    assert prov["is_current"] is False
    assert prov["version_label"] == "Phiên bản lúc vào lệnh"
    assert prov["warning"] is not None
    assert prov["is_outdated_version"] is True


def test_p03_different_config_version():
    """P03: Outdated numeric config version -> is_current=False."""
    snap = {
        "config_version": 999,
        "detector_version": active_scenario_config.detector_version,
        "requirement_profile": "DEFAULT",
    }
    prov = read_snapshot_provenance(snap, active_scenario_config)
    assert prov["is_current"] is False
    assert prov["version_label"] == "Phiên bản lúc vào lệnh"


def test_p04_custom_requirement_profile():
    """P04 / TC-PROV-CUSTOM: Non-baseline custom profile -> is_current=False, is_custom_profile=True."""
    snap = {
        "config_version": active_scenario_config.config_version,
        "detector_version": active_scenario_config.detector_version,
        "requirement_profile": "CUSTOM_PRO_TREND",
    }
    prov = read_snapshot_provenance(snap, active_scenario_config)
    assert prov["is_current"] is False
    assert prov["is_custom_profile"] is True
    assert prov["version_label"] == "Phiên bản lúc vào lệnh"


def test_p05_missing_metadata_unknown():
    """P05 / TC-PROV-UNKNOWN: Legacy row with missing version metadata -> is_current=None, label indicates incomplete info."""
    snap = {
        "scenario_id": "scen-legacy",
    }
    prov = read_snapshot_provenance(snap, active_scenario_config)
    assert prov["is_current"] is None
    assert prov["version_label"] == "Chưa đủ thông tin phiên bản"
    assert prov["snapshot_config_version"] is None
    assert prov["snapshot_detector_version"] is None


def test_p06_canonical_dto_fields_complete():
    """P06: All canonical SnapshotProvenanceDTO fields are present with correct types."""
    snap = {
        "config_version": 1,
        "detector_version": "v14.4.1",
        "requirement_profile": "DEFAULT",
    }
    prov = read_snapshot_provenance(snap, active_scenario_config)
    canonical_keys = {
        "is_current",
        "snapshot_detector_version",
        "current_detector_version",
        "snapshot_config_version",
        "current_config_version",
        "snapshot_requirement_profile",
        "current_requirement_profile",
        "historical_requirements",
        "current_requirements",
        "version_label",
        "warning",
    }
    for k in canonical_keys:
        assert k in prov, f"Missing canonical key: {k}"
    assert isinstance(prov["current_config_version"], int)
    assert isinstance(prov["current_detector_version"], str)


def test_p07_binding_custom_profile_guard():
    """P07: Custom requirement profile is verified and not falsely whitelisted."""
    custom_config = ScenarioConfig(
        config_version=1,
        detector_version="v14.4.1",
        require_m15_poi=True,
        requirement_profile="CUSTOM_STRICT",
    )
    snap = ScenarioSnapshot(
        schema_version="v14.4.1",
        scenario_id="scen-cust",
        instance_id="inst-cust",
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="5M",
        context_timeframes=["H1"],
        family="STANDARD_SMC",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=1000,
        evidence_cutoff_ms=1000,
        evidence_ids=[],
        liquidity_map={},
        trigger_chain={},
        target={"price": 2700.0},
        invalidation={"price": 2640.0},
        summary_vi="Test",
        config_version=1,
        detector_version="v14.4.1",
        requirement_profile="DEFAULT",  # Snapshot is DEFAULT, active is CUSTOM_STRICT
    )
    mock_db = None
    mock_row = None
    import pytest
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.smc_scenario_binding.get_scenario_snapshot", lambda db, sid: snap)
        mp.setattr("services.smc_scenario_binding.snapshot_from_row", lambda row: snap)

        res = verify_scenario_binding(
            mock_db,
            snapshot_id="snap-cust",
            direction="LONG",
            symbol="XAUUSDT",
            setup_instance_id="inst-cust",
            plan_levels={"entry": 2650.0, "stop_loss": 2640.0, "take_profit": 2700.0},
            now_ms=2000,
            config=custom_config,
        )
        assert res.verified is False
        assert "REQUIREMENT_PROFILE_MISMATCH" in res.reasons
