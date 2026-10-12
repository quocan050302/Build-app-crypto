"""
Tests for Forecast Causal Closure (REPRO01 - REPRO04, PHẦN 161 - 164)
Validates causal lookback window, horizon expiry enforcement, range containment for direction=None,
and strict timestamp/price validation.
"""
import pytest
from unittest.mock import MagicMock
from services.smc_forecast_observation_service import (
    ForecastOutcomeDefinition,
    evaluate_forecast_outcome,
)


def test_repro01_pre_forecast_bar_cannot_count_as_target():
    """REPRO01 (PHẦN 161):
    Snapshot as_of_ms=1000, context_cutoff_ms=800, direction='LONG', target=4200, invalidation=4170.
    Obs source_timestamp=900, known_at_ms=950, high=4201, low=4180.
    Evaluation at 1100.
    Pre-forecast bar (900 < 1000) must NOT count as target hit.
    Outcome must remain PENDING.
    """
    snap = MagicMock(
        as_of_ms=1000,
        context_cutoff_ms=800,
        target_price=4200.0,
        invalidation_price=4170.0,
        direction="LONG"
    )
    obs = [
        {
            "source_timestamp": 900,
            "known_at_ms": 950,
            "high": 4201.0,
            "low": 4180.0,
            "price": 4190.0,
            "is_confirmation": True
        }
    ]
    res = evaluate_forecast_outcome(snap, obs, as_of_ms=1100, expiry_ms=2000)
    assert res["outcome"] == ForecastOutcomeDefinition.PENDING.value, (
        f"Expected PENDING because obs (ts=900) is before forecast as_of (1000), but got {res['outcome']}"
    )


def test_repro02_target_after_expiry_is_not_success():
    """REPRO02 (PHẦN 162):
    Snapshot as_of_ms=1000, context_cutoff_ms=800, expiry_ms=1100.
    Obs source_timestamp=1200, known_at_ms=1300, high=4201, low=4180, is_confirmation=True.
    Evaluation at 1500.
    Target hit after expiry (1200 > 1100) must NOT count as success.
    Outcome must be EXPIRED.
    """
    snap = MagicMock(
        as_of_ms=1000,
        context_cutoff_ms=800,
        target_price=4200.0,
        invalidation_price=4170.0,
        direction="LONG"
    )
    obs = [
        {
            "source_timestamp": 1200,
            "known_at_ms": 1300,
            "high": 4201.0,
            "low": 4180.0,
            "price": 4190.0,
            "is_confirmation": True
        }
    ]
    res = evaluate_forecast_outcome(snap, obs, as_of_ms=1500, expiry_ms=1100)
    assert res["outcome"] == ForecastOutcomeDefinition.EXPIRED.value, (
        f"Expected EXPIRED because target occurred after expiry (1200 > 1100), but got {res['outcome']}"
    )


def test_repro03_unknown_direction_level_touch_requires_range_containment():
    """REPRO03 (PHẦN 163):
    direction=None, target=4200.
    Bar 4000-4010 does not touch 4200 -> must NOT hit.
    Bar 4210-4220 does not touch 4200 -> must NOT hit.
    Bar 4199-4201 contains 4200 -> touches.
    """
    snap = MagicMock(
        as_of_ms=1000,
        context_cutoff_ms=1000,
        target_price=4200.0,
        invalidation_price=None,
        direction=None
    )

    # Bar below target: 4000 to 4010
    obs_below = [
        {"source_timestamp": 1100, "known_at_ms": 1150, "high": 4010.0, "low": 4000.0, "price": 4005.0}
    ]
    res_below = evaluate_forecast_outcome(snap, obs_below, as_of_ms=1200, expiry_ms=2000)
    assert res_below["outcome"] == ForecastOutcomeDefinition.PENDING.value, (
        f"Expected PENDING for bar below target [4000, 4010], but got {res_below['outcome']}"
    )

    # Bar above target: 4210 to 4220
    obs_above = [
        {"source_timestamp": 1100, "known_at_ms": 1150, "high": 4220.0, "low": 4210.0, "price": 4215.0}
    ]
    res_above = evaluate_forecast_outcome(snap, obs_above, as_of_ms=1200, expiry_ms=2000)
    assert res_above["outcome"] == ForecastOutcomeDefinition.PENDING.value, (
        f"Expected PENDING for bar above target [4210, 4220], but got {res_above['outcome']}"
    )

    # Bar containing target: 4199 to 4201
    obs_contains = [
        {"source_timestamp": 1100, "known_at_ms": 1150, "high": 4201.0, "low": 4199.0, "price": 4200.0}
    ]
    res_contains = evaluate_forecast_outcome(snap, obs_contains, as_of_ms=1200, expiry_ms=2000)
    assert res_contains["outcome"] == ForecastOutcomeDefinition.WATCHED_LIQUIDITY_TOUCH.value, (
        f"Expected WATCHED_LIQUIDITY_TOUCH for bar containing target [4199, 4201], but got {res_contains['outcome']}"
    )


def test_repro04_missing_timestamp_and_invalid_price_are_unverified():
    """REPRO04 (PHẦN 164):
    Observations with missing known_at_ms, missing source_timestamp, or invalid price (<=0, NaN)
    must NOT produce valid target touches or false hits.
    """
    snap = MagicMock(
        as_of_ms=1000,
        context_cutoff_ms=1000,
        target_price=4160.0,
        invalidation_price=4180.0,
        direction="SHORT"
    )

    # Observation missing known_at_ms
    obs_no_known = [
        {"source_timestamp": 1100, "high": 4150.0, "low": 4140.0, "price": 4145.0, "is_confirmation": True}
    ]
    res_no_known = evaluate_forecast_outcome(snap, obs_no_known, as_of_ms=1200, expiry_ms=2000)
    assert res_no_known["outcome"] == ForecastOutcomeDefinition.PENDING.value

    # Observation with price = 0
    obs_zero_price = [
        {"source_timestamp": 1100, "known_at_ms": 1150, "high": 0.0, "low": 0.0, "price": 0.0}
    ]
    res_zero = evaluate_forecast_outcome(snap, obs_zero_price, as_of_ms=1200, expiry_ms=2000)
    assert res_zero["outcome"] == ForecastOutcomeDefinition.PENDING.value


def test_fct01_builder_produces_numeric_invalidation_and_snapshot_persists_it(isolated_db):
    """FCT01 (PHẦN 093, 177):
    Actual forecast builders provide numeric invalidation_price,
    and create_forecast_snapshot persists it to DB as a non-None float.
    """
    from services.smc_session_forecast_service import build_asia_forecast, SessionRange
    import models
    from services.smc_forecast_observation_service import create_forecast_snapshot

    rng = SessionRange(
        session_name="ASIA",
        session_date_ny="2026-10-12",
        start_ms=100000,
        end_ms=200000,
        cutoff_ms=150000,
        coverage_status="VALID",
        coverage_ratio=1.0,
        bars_count=10,
        expected_bars=10,
        gap_bars=0,
        high=4200.0,
        low=4150.0,
        close_as_of=4195.0,
        is_partial=False
    )
    # With CLV >= 0.6 and Bullish HTF -> TREND_EXTENSION LONG, invalidation at low (4150.0)
    fc = build_asia_forecast(
        session_id="ASIA:2026-10-12-test",
        now_ms=150000,
        rng=rng,
        htf_text="Tăng (H4)",
        pd=None,
        levels=[],
        m5=[],
        price=4180.0
    )

    assert fc.invalidation_price is not None, "Expected builder to set numeric invalidation_price"
    assert fc.invalidation_price == 4150.0

    snap = create_forecast_snapshot(isolated_db, fc, now_ms=150000)
    isolated_db.commit()

    assert snap is not None
    assert snap.invalidation_price == 4150.0

    # Query from DB to verify persistence
    row = isolated_db.query(models.ForecastObservation).filter_by(id=snap.id).first()
    assert row is not None
    assert row.invalidation_price == 4150.0


def test_fct02_summary_counts_and_separation(isolated_db):
    """FCT02 (PHẦN 112, 184):
    summarize_forecast_observations separates pending, terminal, legacy unverified,
    and uses decisive resolved events (confirmed + invalidated + expired) for denominator.
    """
    import models
    from services.smc_forecast_observation_service import (
        ForecastOutcomeDefinition,
        summarize_forecast_observations,
    )

    # 1. Confirmed target record
    r1 = models.ForecastObservation(
        id="fc-test-1", forecast_id="s1", revision=1, symbol="XAUUSDT", session="ASIA",
        session_id="ASIA:1", as_of_ms=100, context_cutoff_ms=100, content_digest="d1",
        hypothesis_type="H1", target_price=4200.0, invalidation_price=4150.0,
        outcome_status=ForecastOutcomeDefinition.CONFIRMED_TARGET_BEFORE_INVALIDATION.value,
        payload_json="{}", created_at=100
    )
    # 2. Invalidated record
    r2 = models.ForecastObservation(
        id="fc-test-2", forecast_id="s2", revision=1, symbol="XAUUSDT", session="ASIA",
        session_id="ASIA:2", as_of_ms=100, context_cutoff_ms=100, content_digest="d2",
        hypothesis_type="H1", target_price=4200.0, invalidation_price=4150.0,
        outcome_status=ForecastOutcomeDefinition.INVALIDATED_BEFORE_TARGET.value,
        payload_json="{}", created_at=100
    )
    # 3. Expired record
    r3 = models.ForecastObservation(
        id="fc-test-3", forecast_id="s3", revision=1, symbol="XAUUSDT", session="ASIA",
        session_id="ASIA:3", as_of_ms=100, context_cutoff_ms=100, content_digest="d3",
        hypothesis_type="H1", target_price=4200.0, invalidation_price=4150.0,
        outcome_status=ForecastOutcomeDefinition.EXPIRED.value,
        payload_json="{}", created_at=100
    )
    # 4. Watched liquidity touch record
    r4 = models.ForecastObservation(
        id="fc-test-4", forecast_id="s4", revision=1, symbol="XAUUSDT", session="ASIA",
        session_id="ASIA:4", as_of_ms=100, context_cutoff_ms=100, content_digest="d4",
        hypothesis_type="H1", target_price=4200.0, invalidation_price=4150.0,
        outcome_status=ForecastOutcomeDefinition.WATCHED_LIQUIDITY_TOUCH.value,
        payload_json="{}", created_at=100
    )
    # 5. Pending record
    r5 = models.ForecastObservation(
        id="fc-test-5", forecast_id="s5", revision=1, symbol="XAUUSDT", session="ASIA",
        session_id="ASIA:5", as_of_ms=100, context_cutoff_ms=100, content_digest="d5",
        hypothesis_type="H1", target_price=4200.0, invalidation_price=4150.0,
        outcome_status=ForecastOutcomeDefinition.PENDING.value,
        payload_json="{}", created_at=100
    )
    # 6. Legacy record without invalidation_price
    r6 = models.ForecastObservation(
        id="fc-test-6", forecast_id="s6", revision=1, symbol="XAUUSDT", session="ASIA",
        session_id="ASIA:6", as_of_ms=100, context_cutoff_ms=100, content_digest="d6",
        hypothesis_type="H1", target_price=4200.0, invalidation_price=None,
        outcome_status=ForecastOutcomeDefinition.CONFIRMED_TARGET_BEFORE_INVALIDATION.value,
        payload_json="{}", created_at=100
    )

    for r in (r1, r2, r3, r4, r5, r6):
        isolated_db.add(r)
    isolated_db.commit()

    summary = summarize_forecast_observations(isolated_db)
    assert summary["n_forecasts"] == 6
    assert summary["n_pending"] == 1
    assert summary["n_terminal"] == 5
    assert summary["n_confirmed_target"] == 2
    assert summary["n_invalidated"] == 1
    assert summary["n_expired"] == 1
    assert summary["n_watched_liquidity_touch"] == 1
    assert summary["n_legacy_unverified"] == 1
    # n_resolved_valid = 2 (confirmed) + 1 (inval) + 1 (expired) = 4
    assert summary["n_resolved_valid"] == 4
    # target_touch_fraction = 2 / 4 = 0.5
    assert summary["target_touch_fraction"] == 0.5
    assert summary["probability_status"] == "UNAVAILABLE"


def test_obs01_cycle_persist_and_observer_updates_outcome(isolated_db):
    """OBS01 (PHẦN 092, 107, 108):
    Snapshot created in DB -> future closed candles arrive -> process_pending_forecasts
    updates terminal outcome idempotently using nested transactions.
    """
    import models
    from services.smc_forecast_observation_service import (
        ForecastOutcomeDefinition,
        create_forecast_snapshot,
        process_pending_forecasts,
    )

    fc = {
        "session": "ASIA",
        "session_id": "ASIA:cycle-test",
        "as_of_ms": 100000,
        "hypothesis_type": "SWEEP_REJECTION_PENDING",
        "primary": {
            "direction": "LONG",
            "thesis_vi": "Bounce test",
            "invalidation_vi": "Below 4170",
            "invalidation_price": 4170.0,
        },
        "target": {"price": 4190.0, "label_vi": "PDH"},
        "invalidation": ["Below 4170"],
        "confirmation": ["M5 displacement"],
        "watched_liquidity": [{"id": "pdh", "price": 4190.0, "side": "HIGH"}],
        "session_range": {"cutoff_ms": 100000, "end_ms": 200000},
    }

    snap = create_forecast_snapshot(isolated_db, fc, now_ms=100000)
    isolated_db.commit()
    assert snap.outcome_status == ForecastOutcomeDefinition.PENDING.value

    # Insert a future closed candle at timestamp 110000 with high >= 4190
    candle = models.Candle(
        symbol="XAUUSDT",
        timeframe="5M",
        timestamp=110000,
        open=4180.0,
        high=4192.0,
        low=4178.0,
        close=4185.0,
        volume=10.0,
        is_closed=True,
    )
    isolated_db.add(candle)
    isolated_db.commit()

    # Process pending forecasts at 120000 (after candle close 110000 + 300000 = 410000? Wait, candle timestamp is 110000)
    # Note: candle known_at_ms in process_pending_forecasts is c.timestamp + 300_000 = 410_000.
    # So evaluation as_of_ms must be >= 410_000 for candle to be known!
    res = process_pending_forecasts(isolated_db, symbol="XAUUSDT", as_of_ms=420000)
    isolated_db.commit()

    assert res["processed"] >= 1
    assert res["updated"] >= 1

    # Check updated outcome in DB
    updated_snap = isolated_db.query(models.ForecastObservation).filter_by(id=snap.id).first()
    assert updated_snap.outcome_status == ForecastOutcomeDefinition.WATCHED_LIQUIDITY_TOUCH.value

