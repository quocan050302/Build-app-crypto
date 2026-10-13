"""
backend/tests/test_final_forecast_proof_pipeline.py
V14.4.3 — Final Closure Suite for Forecast Structured Definition, Proof & Verification.

Tests:
1. FCPROOF01: Default snapshot creation is UNVERIFIED; numeric invalidation alone does NOT grant VERIFIED.
2. FCPROOF02: Caller passing verification_status="VERIFIED" without verified proof is overridden to UNVERIFIED.
3. FCPROOF03: Genuine confirmation proof + valid coverage -> target reached -> CONFIRMED_TARGET_BEFORE_INVALIDATION.
4. FCPROOF04: Feed gap during observation window -> DATA_UNAVAILABLE (never counted as WIN or LOSS).
5. FCPROOF05: Target reached before confirmation -> WATCHED_LIQUIDITY_TOUCH.
6. FCPROOF06: Summary cohort denominator strictly excludes unverified records.
"""
import pytest
import models
from services.smc_forecast_observation_service import (
    create_forecast_snapshot,
    evaluate_forecast_outcome,
    process_pending_forecasts,
    summarize_forecast_observations,
    validate_forecast_definition,
    assess_forecast_coverage,
    ForecastOutcomeDefinition,
)


def test_fcproof01_default_snapshot_creation_is_unverified(isolated_db):
    """FCPROOF01 (PHẦN 048, 049, 156):
    Snapshot created with numeric invalidation price (4170.0) but no confirmation proof.
    verification_status MUST be UNVERIFIED.
    """
    forecast_data = {
        "symbol": "XAUUSDT",
        "session": "ASIA",
        "session_id": "ASIA:2026-10-13:test01",
        "as_of_ms": 1728000000000,
        "hypothesis_type": "PRIMARY",
        "primary": {
            "direction": "LONG",
            "target": {"price": 4200.0, "source": "SWING_HIGH"},
        },
        "invalidation_price": 4170.0,
        # No confirmation proof!
    }

    snap = create_forecast_snapshot(isolated_db, forecast_data, as_of=1728000000000)
    assert snap is not None
    assert snap.verification_status == "UNVERIFIED"


def test_fcproof02_caller_spoofed_verified_is_overridden_to_unverified(isolated_db):
    """FCPROOF02 (PHẦN 048, 156):
    Caller explicitly sends verification_status='VERIFIED', but lacks server-verified proof.
    create_forecast_snapshot MUST override it to UNVERIFIED.
    """
    forecast_data = {
        "symbol": "XAUUSDT",
        "session": "LONDON",
        "session_id": "LONDON:2026-10-13:test02",
        "as_of_ms": 1728000000000,
        "hypothesis_type": "PRIMARY",
        "primary": {
            "direction": "LONG",
            "target": {"price": 4200.0, "source": "SWING_HIGH"},
        },
        "invalidation_price": 4170.0,
        "verification_status": "VERIFIED",  # Client spoof attempt!
    }

    snap = create_forecast_snapshot(isolated_db, forecast_data, as_of=1728000000000)
    assert snap is not None
    assert snap.verification_status == "UNVERIFIED"


def test_fcproof03_genuine_confirmation_proof_yields_confirmed_target(isolated_db):
    """FCPROOF03 (PHẦN 054, 056, 062):
    Forecast with genuine confirmation proof reaches target -> CONFIRMED_TARGET_BEFORE_INVALIDATION.
    """
    forecast_data = {
        "symbol": "XAUUSDT",
        "session": "NY",
        "session_id": "NY:2026-10-13:test03",
        "as_of_ms": 1000,
        "hypothesis_type": "PRIMARY",
        "primary": {
            "direction": "LONG",
            "target": {"price": 4200.0, "source": "SWING_HIGH"},
        },
        "invalidation_price": 4170.0,
        "confirmation_proof_id": "proof-retest-fvg-01",  # Verified server proof!
    }

    snap = create_forecast_snapshot(isolated_db, forecast_data, as_of=1000)
    assert snap is not None
    assert snap.verification_status == "VERIFIED"

    # Candle 1: Confirmation event confirmed at 1500 (known at 1800)
    # Candle 2: Target touched at 2000 (high=4205.0)
    obs = [
        {"source_timestamp": 1500, "known_at_ms": 1800, "high": 4185.0, "low": 4175.0, "close": 4180.0, "is_confirmation": True},
        {"source_timestamp": 2000, "known_at_ms": 2300, "high": 4205.0, "low": 4180.0, "close": 4195.0},
    ]

    res = evaluate_forecast_outcome(snapshot=snap, observations=obs, as_of_ms=2500, feed_gap=False)
    assert res["outcome"] == ForecastOutcomeDefinition.CONFIRMED_TARGET_BEFORE_INVALIDATION.value


def test_fcproof04_feed_gap_in_window_yields_data_unavailable(isolated_db):
    """FCPROOF04 (PHẦN 060, 157):
    Feed gap during observation window -> DATA_UNAVAILABLE (excluded from denominator).
    """
    forecast_data = {
        "symbol": "XAUUSDT",
        "session": "NY",
        "session_id": "NY:2026-10-13:test04",
        "as_of_ms": 1000,
        "hypothesis_type": "PRIMARY",
        "primary": {
            "direction": "LONG",
            "target": {"price": 4200.0, "source": "SWING_HIGH"},
        },
        "invalidation_price": 4170.0,
        "confirmation_proof_id": "proof-retest-fvg-04",
    }
    snap = create_forecast_snapshot(isolated_db, forecast_data, as_of=1000)

    # Observation with feed_gap=True
    obs = [
        {"source_timestamp": 1500, "known_at_ms": 1800, "high": 4205.0, "low": 4175.0, "close": 4180.0, "is_confirmation": True},
    ]

    res = evaluate_forecast_outcome(snapshot=snap, observations=obs, as_of_ms=2500, feed_gap=True)
    assert res["outcome"] == ForecastOutcomeDefinition.DATA_UNAVAILABLE.value


def test_fcproof05_target_reached_before_confirmation_is_watched_liquidity():
    """FCPROOF05 (PHẦN 062):
    Target hit before confirmation occurs -> WATCHED_LIQUIDITY_TOUCH.
    """
    snap = models.ForecastObservation(
        id="fc-obs-touch-05",
        forecast_id="s05",
        session="ASIA",
        session_id="ASIA:test05",
        as_of_ms=1000,
        context_cutoff_ms=1000,
        content_digest="d05",
        hypothesis_type="PRIMARY",
        direction="LONG",
        target_price=4200.0,
        invalidation_price=4170.0,
        outcome_status=ForecastOutcomeDefinition.PENDING.value,
        payload_json="{}",
        created_at=1000,
    )

    # Target hit at 1500; confirmation arrives later at 2000
    obs = [
        {"source_timestamp": 1500, "known_at_ms": 1800, "high": 4205.0, "low": 4180.0, "close": 4190.0, "is_confirmation": False},
        {"source_timestamp": 2000, "known_at_ms": 2300, "high": 4190.0, "low": 4175.0, "close": 4180.0, "is_confirmation": True},
    ]

    res = evaluate_forecast_outcome(snapshot=snap, observations=obs, as_of_ms=2500, feed_gap=False)
    assert res["outcome"] == ForecastOutcomeDefinition.WATCHED_LIQUIDITY_TOUCH.value


def test_fcproof06_summary_cohort_excludes_unverified_records(isolated_db):
    """FCPROOF06 (PHẦN 065):
    Summary cohort only includes decisive outcomes from VERIFIED records.
    Unverified records are counted in n_legacy_unverified and excluded from target_touch_fraction.
    """
    # Record 1: VERIFIED snapshot with CONFIRMED_TARGET
    r_ver = models.ForecastObservation(
        id="fc-ver-1",
        forecast_id="s_ver",
        revision=1,
        symbol="XAUUSDT",
        session="NY",
        session_id="NY:ver1",
        as_of_ms=100,
        context_cutoff_ms=100,
        content_digest="d_ver1",
        hypothesis_type="PRIMARY",
        target_price=4200.0,
        invalidation_price=4170.0,
        verification_status="VERIFIED",
        outcome_status=ForecastOutcomeDefinition.CONFIRMED_TARGET_BEFORE_INVALIDATION.value,
        payload_json="{}",
        created_at=100,
    )
    # Record 2: UNVERIFIED snapshot with CONFIRMED_TARGET
    r_unver = models.ForecastObservation(
        id="fc-unver-1",
        forecast_id="s_unver",
        revision=1,
        symbol="XAUUSDT",
        session="NY",
        session_id="NY:unver1",
        as_of_ms=100,
        context_cutoff_ms=100,
        content_digest="d_unver1",
        hypothesis_type="PRIMARY",
        target_price=4200.0,
        invalidation_price=4170.0,
        verification_status="UNVERIFIED",
        outcome_status=ForecastOutcomeDefinition.CONFIRMED_TARGET_BEFORE_INVALIDATION.value,
        payload_json="{}",
        created_at=100,
    )
    isolated_db.add(r_ver)
    isolated_db.add(r_unver)
    isolated_db.commit()

    summary = summarize_forecast_observations(isolated_db)
    assert summary["n_forecasts"] == 2
    assert summary["n_confirmed_target"] == 1  # Only the verified one!
    assert summary["n_legacy_unverified"] == 1  # The unverified one is classified here!
    assert summary["n_resolved_valid"] == 1
    assert summary["target_touch_fraction"] == 1.0  # 1/1 verified = 1.0
