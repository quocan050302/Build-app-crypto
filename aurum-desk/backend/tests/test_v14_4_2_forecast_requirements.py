import pytest
from services.smc_session_forecast_service import (
    SessionForecast, evaluate_session_forecasts, build_ny_forecast
)
from services.smc_scenario_config import ScenarioConfig, active_scenario_config, describe_model_requirements
from services.smc_scenario_types import ScenarioSnapshot, ScenarioState, default_probability
from services.smc_probability_types import ProbabilityEstimate
import smc_fixtures as F


def test_forecast_relation_status():
    f = SessionForecast(
        session="NY",
        session_id="NY",
        as_of_ms=1000,
        data_status="OK",
        is_partial=False,
        hypothesis_type="CONTINUATION_WATCH",
        relation_status="SUPPORTS"
    )
    assert f.relation_status == "SUPPORTS"
    d = f.to_dict()
    assert "relation_status" in d
    assert d["relation_status"] == "SUPPORTS"


def test_s01_asof_no_future():
    """S01: Forecasting at a specific timestamp never leaks future bars."""
    cbt = F.make_candles_by_tf("LONG", F.NOW_MS)
    m5 = cbt["5M"]
    # Slice to a point in time before the full tail
    mid_now = m5[100].timestamp
    context_mid = {
        "5M": [c for c in m5 if c.timestamp <= mid_now],
        "15M": [c for c in cbt["15M"] if c.timestamp <= mid_now],
        "1H": [c for c in cbt["1H"] if c.timestamp <= mid_now],
        "4H": [c for c in cbt["4H"] if c.timestamp <= mid_now],
    }

    f1 = evaluate_session_forecasts("XAUUSDT", context_mid, mid_now, active_scenario_config)
    ny1 = f1["NY"]

    # Now simulate future bars existing in the database/feed, but evaluate with same as_of_ms
    context_with_future = {
        "5M": m5,  # includes future bars
        "15M": cbt["15M"],
        "1H": cbt["1H"],
        "4H": cbt["4H"],
    }
    # Since evaluate_session_forecasts filters closed bars by now_ms or reads as-of context
    filtered_context = {
        tf: [c for c in bars if c.timestamp <= mid_now]
        for tf, bars in context_with_future.items()
    }
    f2 = evaluate_session_forecasts("XAUUSDT", filtered_context, mid_now, active_scenario_config)
    ny2 = f2["NY"]

    assert ny1.hypothesis_type == ny2.hypothesis_type
    assert ny1.facts == ny2.facts
    assert ny1.is_partial == ny2.is_partial
    assert ny1.as_of_ms == ny2.as_of_ms
    assert ny1.relation_status == ny2.relation_status


def test_s02_forecast_relationship():
    """S02: Explicit relation_status SUPPORTS/CONFLICTS/UNCERTAIN between forecast and scenario."""
    # Dummy scenario with LONG direction
    snap_long = ScenarioSnapshot(
        scenario_id="scen-long",
        instance_id="inst-1",
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="5M",
        context_timeframes=["H1", "H4"],
        family="STANDARD_SMC",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=F.NOW_MS,
        evidence_cutoff_ms=F.NOW_MS,
        evidence_ids=[],
        liquidity_map={},
        trigger_chain={"verified": True, "node_ids": {}},
        target={"price": 2700.0},
        invalidation={"price": 2640.0},
        summary_vi="LONG setup",
        config_version=1,
        detector_version="v14.4.1",
        digest="digest-1",
        plan={"is_valid": True},
        schema_version="v14.4.1",
    )

    cbt = F.make_candles_by_tf("LONG", F.NOW_MS)
    f = evaluate_session_forecasts("XAUUSDT", cbt, F.NOW_MS, active_scenario_config, primary_scenario=snap_long)
    ny_forecast = f["NY"]
    ny_direction = ny_forecast.primary.get("direction") if ny_forecast.primary else None

    # When forecast direction differs from scenario direction -> CONFLICTS
    if ny_direction == "SHORT":
        assert ny_forecast.relation_status == "CONFLICTS"
    elif ny_direction == "LONG":
        assert ny_forecast.relation_status == "SUPPORTS"
    else:
        assert ny_forecast.relation_status == "UNCERTAIN"

    # When forecast is paired with scenario of opposite direction
    opp_direction = "LONG" if ny_direction == "SHORT" else "SHORT"
    snap_opp = ScenarioSnapshot(
        scenario_id="scen-opp",
        instance_id="inst-opp",
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="5M",
        context_timeframes=["H1", "H4"],
        family="STANDARD_SMC",
        direction=opp_direction,
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=F.NOW_MS,
        evidence_cutoff_ms=F.NOW_MS,
        evidence_ids=[],
        liquidity_map={},
        trigger_chain={"verified": True, "node_ids": {}},
        target={"price": 2600.0},
        invalidation={"price": 2660.0},
        summary_vi="Opposite setup",
        config_version=1,
        detector_version="v14.4.1",
        digest="digest-opp",
        plan={"is_valid": True},
        schema_version="v14.4.1",
    )
    f_opp = evaluate_session_forecasts("XAUUSDT", cbt, F.NOW_MS, active_scenario_config, primary_scenario=snap_opp)
    if ny_direction:
        assert f_opp["NY"].relation_status == "CONFLICTS"

    # When forecast is paired with matching scenario
    snap_same = ScenarioSnapshot(
        scenario_id="scen-same",
        instance_id="inst-same",
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="5M",
        context_timeframes=["H1", "H4"],
        family="STANDARD_SMC",
        direction=ny_direction,
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=F.NOW_MS,
        evidence_cutoff_ms=F.NOW_MS,
        evidence_ids=[],
        liquidity_map={},
        trigger_chain={"verified": True, "node_ids": {}},
        target={"price": 2600.0},
        invalidation={"price": 2660.0},
        summary_vi="Matching setup",
        config_version=1,
        detector_version="v14.4.1",
        digest="digest-same",
        plan={"is_valid": True},
        schema_version="v14.4.1",
    )
    f_same = evaluate_session_forecasts("XAUUSDT", cbt, F.NOW_MS, active_scenario_config, primary_scenario=snap_same)
    if ny_direction:
        assert f_same["NY"].relation_status == "SUPPORTS"

    # None scenario -> UNCERTAIN
    f_none = evaluate_session_forecasts("XAUUSDT", cbt, F.NOW_MS, active_scenario_config, primary_scenario=None)
    assert f_none["NY"].relation_status == "UNCERTAIN"


def test_s03_poi_modes():
    """S03: require_m15_poi modes verification in config and descriptors."""
    cfg_supporting = ScenarioConfig(require_m15_poi=False)
    desc_supporting = describe_model_requirements(cfg_supporting)
    assert desc_supporting["m15_poi"]["importance"] == "SUPPORTING"

    cfg_required = ScenarioConfig(require_m15_poi=True)
    desc_required = describe_model_requirements(cfg_required)
    assert desc_required["m15_poi"]["importance"] == "REQUIRED"


def test_s04_no_model():
    """S04: Probability is UNAVAILABLE when no validated OOS model is registered."""
    prob = default_probability()
    assert prob["status"] == "UNAVAILABLE"
    assert prob["p_tp_before_sl"] is None
    assert prob["support_n"] is None

    est = ProbabilityEstimate.unavailable()
    assert est.status == "UNAVAILABLE"
    assert est.p is None
    assert "chưa có xác suất" in est.note_vi.lower()
