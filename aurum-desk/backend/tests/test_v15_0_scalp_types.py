"""
test_v15_0_scalp_types.py
V15.0 - Unit tests for M1 scalp core types, dataclasses, invariants, and configuration.
"""
from __future__ import annotations

import pytest
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    OpportunityEvaluation,
    OpportunityReasonCode,
    OpportunityStage,
    ScalpMode,
    WatchedZone,
    ZoneState,
    get_scalp_config,
)


def test_scalp_mode_and_model_identifiers():
    assert ScalpMode.OFF.value == "OFF"
    assert ScalpMode.SHADOW.value == "SHADOW"
    assert ScalpMode.PAPER.value == "PAPER"

    assert ModelIdentifier.STANDARD_SMC.value == "STANDARD_SMC"
    assert ModelIdentifier.SMC_M1_REVERSAL.value == "SMC_M1_REVERSAL"
    assert ModelIdentifier.SMC_M1_CONTINUATION.value == "SMC_M1_CONTINUATION"
    assert ModelIdentifier.NY_DAILY_RESEARCH.value == "NY_DAILY_RESEARCH"


def test_scalp_config_validation():
    # Valid config
    cfg = M1ScalpConfig(near_zone_enter_atr=0.30, near_zone_exit_atr=0.50)
    assert cfg.min_net_rr == 2.0
    assert cfg.min_closed_m1_bars == 15
    assert cfg.displacement_body_atr_min == 0.70

    # Invalid hysteresis: enter >= exit must raise ValueError
    with pytest.raises(ValueError, match="near_zone_enter_atr must be < near_zone_exit_atr"):
        M1ScalpConfig(near_zone_enter_atr=0.50, near_zone_exit_atr=0.30)

    # Invalid displacement
    with pytest.raises(ValueError, match="displacement_body_atr_min must be > 0"):
        M1ScalpConfig(displacement_body_atr_min=0)

    # Helper overrides
    cfg_override = get_scalp_config(scalp_m1_mode="SHADOW", min_net_rr=2.5)
    assert cfg_override.scalp_m1_mode == "SHADOW"
    assert cfg_override.min_net_rr == 2.5


def test_watched_zone_invariants_and_roundtrip():
    # Valid zone
    z = WatchedZone(
        zone_id="zone-XAUUSDT-15M-BULLISH-1000",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2650.0,
        bottom=2645.0,
        created_at=1000,
        known_at=1000,
        invalidation_level=2640.0,
    )
    assert z.top > z.bottom
    assert z.midpoint == 2647.5
    assert z.height == 5.0

    # Invariant: top <= bottom must raise ValueError
    with pytest.raises(ValueError, match="INVALID_ZONE_BOUNDS"):
        WatchedZone(
            zone_id="bad_zone",
            symbol="XAUUSDT",
            timeframe="15M",
            zone_type="FVG",
            direction="BULLISH",
            top=2640.0,
            bottom=2645.0,
            created_at=1000,
            known_at=1000,
        )

    # Roundtrip serialization
    d = z.to_dict()
    assert d["zone_id"] == "zone-XAUUSDT-15M-BULLISH-1000"
    z_copy = WatchedZone.from_dict(d)
    assert z_copy.zone_id == z.zone_id
    assert z_copy.top == z.top
    assert z_copy.bottom == z.bottom


def test_opportunity_evaluation_latency_and_roundtrip():
    opp = OpportunityEvaluation(
        opportunity_id="opp_rev_123",
        symbol="XAUUSDT",
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
        direction="LONG",
        session_id="NY_20261015",
        zone_id="zone_456",
        as_of_ms=1792000000000,
        stage=OpportunityStage.TRIGGER_CONFIRMED.value,
        reason_codes=("WAITING_M1_RETEST",),
        market_event_received_at=1792000000000,
        evaluation_started_at=1792000000010,
        evaluation_completed_at=1792000000025,
        planned_entry=2650.0,
        stop_loss=2642.0,
        take_profit=2670.0,
        net_rr=2.45,
    )
    d = opp.to_dict()
    assert d["opportunity_id"] == "opp_rev_123"
    assert d["evaluation_completed_at"] - d["evaluation_started_at"] == 15
    assert d["net_rr"] == 2.45

    opp_copy = OpportunityEvaluation.from_dict(d)
    assert opp_copy.opportunity_id == opp.opportunity_id
    assert opp_copy.planned_entry == 2650.0
    assert opp_copy.reason_codes == ("WAITING_M1_RETEST",)
