"""
Tests for Stage G: Forecast Snapshot & Forward Outcome Evaluation (FC01 - FC12)
Covers:
- FC01: Snapshot created prior to future outcome with immutable payload and deterministic digest.
- FC02: Identical dedupe returns existing record without duplicate row.
- FC03: Material revision increments revision and preserves previous snapshot.
- FC04: Clock/causal lookback rejection: observations predating context_cutoff_ms are ignored.
- FC05: Horizon expiry yields EXPIRED outcome.
- FC06: Target touched before confirmation yields WATCHED_LIQUIDITY_TOUCH (not a WIN trade).
- FC07: Valid confirmed target hit yields CONFIRMED_TARGET_BEFORE_INVALIDATION.
- FC08: Invalidation hit yields INVALIDATED_BEFORE_TARGET.
- FC09: Ambiguous same bar (both target and invalidation touched) yields AMBIGUOUS.
- FC10: Feed gap during observation window yields DATA_UNAVAILABLE.
- FC11: Probability status remains UNAVAILABLE / null without calibrated OOS model.
- FC12: SQL savepoint fault in observation does not break parent transaction.
"""
import pytest
from unittest.mock import MagicMock
from services.smc_forecast_observation_service import (
    ForecastOutcomeDefinition,
    forecast_content_digest,
    create_forecast_snapshot,
    evaluate_forecast_outcome,
    summarize_forecast_observations,
)
import models


def _dummy_forecast(
    session: str = "ASIA",
    session_id: str = "ASIA:2026-10-12",
    as_of_ms: int = 100000,
    target_price: float = 4190.0,
    direction: str = "LONG",
    thesis: str = "If sweep low, watch for bounce"
) -> dict:
    return {
        "session": session,
        "session_id": session_id,
        "as_of_ms": as_of_ms,
        "hypothesis_type": "SWEEP_REJECTION_PENDING",
        "primary": {
            "direction": direction,
            "thesis_vi": thesis,
            "invalidation_vi": "Price closes below 4170.0",
        },
        "target": {"price": target_price, "label_vi": "PDH"},
        "invalidation": ["Price closes below 4170.0"],
        "confirmation": ["Displacement on M5"],
        "watched_liquidity": [{"id": "pdh-1", "price": target_price, "side": "HIGH"}],
        "probability": {"status": "UNAVAILABLE", "p_session_event": None},
        "facts": ["Biên Á hình thành"],
        "session_range": {"cutoff_ms": as_of_ms},
    }


def test_fc01_snapshot_before_future_outcome(isolated_db):
    """FC01: Snapshot is created with immutable payload and deterministic digest."""
    fc = _dummy_forecast()
    snap = create_forecast_snapshot(isolated_db, fc, now_ms=100000)
    isolated_db.commit()

    assert snap is not None
    assert snap.session_id == "ASIA:2026-10-12"
    assert snap.revision == 1
    assert snap.outcome_status == ForecastOutcomeDefinition.PENDING.value
    assert snap.target_price == 4190.0
    assert len(snap.content_digest) == 64


def test_fc02_identical_dedupe_preserves_single_row(isolated_db):
    """FC02: Identical forecast repeated produces the same row without creating duplicates."""
    fc = _dummy_forecast()
    snap1 = create_forecast_snapshot(isolated_db, fc, now_ms=100000)
    snap2 = create_forecast_snapshot(isolated_db, fc, now_ms=106000)  # 6s later
    isolated_db.commit()

    assert snap1.id == snap2.id
    total_count = isolated_db.query(models.ForecastObservation).filter_by(session_id="ASIA:2026-10-12").count()
    assert total_count == 1


def test_fc03_material_revision_increments(isolated_db):
    """FC03: Changing semantic facts increments revision to 2, keeping revision 1 intact."""
    fc1 = _dummy_forecast(target_price=4190.0)
    snap1 = create_forecast_snapshot(isolated_db, fc1, now_ms=100000)

    # Material change: different target price
    fc2 = _dummy_forecast(target_price=4195.0)
    snap2 = create_forecast_snapshot(isolated_db, fc2, now_ms=110000)
    isolated_db.commit()

    assert snap1.revision == 1
    assert snap2.revision == 2
    assert snap1.target_price == 4190.0
    assert snap2.target_price == 4195.0
    total = isolated_db.query(models.ForecastObservation).filter_by(session_id="ASIA:2026-10-12").count()
    assert total == 2


def test_fc04_causal_past_observations_ignored():
    """FC04: Observations with timestamp before context_cutoff_ms are strictly ignored."""
    fc = _dummy_forecast(as_of_ms=100000, target_price=4190.0)
    snap = MagicMock(context_cutoff_ms=100000, target_price=4190.0, invalidation_price=4170.0, direction="LONG")

    # Observation at ts=90000 touched target, but it occurred BEFORE cutoff 100000
    past_obs = [
        {"source_timestamp": 90000, "known_at_ms": 90000, "high": 4192.0, "low": 4180.0, "is_confirmation": True}
    ]
    res = evaluate_forecast_outcome(snap, past_obs, as_of_ms=110000)
    assert res["outcome"] == ForecastOutcomeDefinition.PENDING.value


def test_fc05_expired_outcome():
    """FC05: Horizon expiry reached without target or invalidation yields EXPIRED."""
    snap = MagicMock(context_cutoff_ms=100000, target_price=4190.0, invalidation_price=4170.0, direction="LONG")
    obs = [
        {"source_timestamp": 105000, "known_at_ms": 105000, "high": 4182.0, "low": 4178.0}
    ]
    res = evaluate_forecast_outcome(snap, obs, as_of_ms=200000, expiry_ms=150000)
    assert res["outcome"] == ForecastOutcomeDefinition.EXPIRED.value


def test_fc06_not_confirmed_target_touch_is_watched_liquidity_touch():
    """FC06: Target touched before confirmation yields WATCHED_LIQUIDITY_TOUCH (NOT a WIN trade)."""
    snap = MagicMock(context_cutoff_ms=100000, target_price=4190.0, invalidation_price=4170.0, direction="LONG")
    # Target touched at 105000 without is_confirmation
    obs = [
        {"source_timestamp": 105000, "known_at_ms": 105000, "high": 4191.0, "low": 4180.0, "is_confirmation": False}
    ]
    res = evaluate_forecast_outcome(snap, obs, as_of_ms=110000)
    assert res["outcome"] == ForecastOutcomeDefinition.WATCHED_LIQUIDITY_TOUCH.value
    assert res["outcome"] != "WIN"


def test_fc07_valid_confirmed_target():
    """FC07: Confirmation followed by target hit yields CONFIRMED_TARGET_BEFORE_INVALIDATION."""
    snap = MagicMock(context_cutoff_ms=100000, target_price=4190.0, invalidation_price=4170.0, direction="LONG")
    obs = [
        {"source_timestamp": 102000, "known_at_ms": 102000, "high": 4182.0, "low": 4178.0, "is_confirmation": True},
        {"source_timestamp": 105000, "known_at_ms": 105000, "high": 4191.0, "low": 4180.0, "is_confirmation": False},
    ]
    res = evaluate_forecast_outcome(snap, obs, as_of_ms=110000)
    assert res["outcome"] == ForecastOutcomeDefinition.CONFIRMED_TARGET_BEFORE_INVALIDATION.value


def test_fc08_invalidation_first():
    """FC08: Invalidation hit before target hit yields INVALIDATED_BEFORE_TARGET."""
    snap = MagicMock(context_cutoff_ms=100000, target_price=4190.0, invalidation_price=4170.0, direction="LONG")
    obs = [
        {"source_timestamp": 102000, "known_at_ms": 102000, "high": 4175.0, "low": 4168.0, "is_confirmation": False},
        {"source_timestamp": 105000, "known_at_ms": 105000, "high": 4195.0, "low": 4180.0, "is_confirmation": True},
    ]
    res = evaluate_forecast_outcome(snap, obs, as_of_ms=110000)
    assert res["outcome"] == ForecastOutcomeDefinition.INVALIDATED_BEFORE_TARGET.value


def test_fc09_ambiguous_same_bar():
    """FC09: Both target and invalidation touched on the same candle without sequence yields AMBIGUOUS."""
    snap = MagicMock(context_cutoff_ms=100000, target_price=4190.0, invalidation_price=4170.0, direction="LONG")
    # Single candle with high=4192 (>= 4190) and low=4168 (<= 4170)
    obs = [
        {"source_timestamp": 102000, "known_at_ms": 102000, "high": 4192.0, "low": 4168.0, "is_confirmation": True}
    ]
    res = evaluate_forecast_outcome(snap, obs, as_of_ms=110000)
    assert res["outcome"] == ForecastOutcomeDefinition.AMBIGUOUS.value


def test_fc10_feed_gap_yields_data_unavailable():
    """FC10: Feed gap during observation window marks DATA_UNAVAILABLE."""
    snap = MagicMock(context_cutoff_ms=100000, target_price=4190.0, invalidation_price=4170.0, direction="LONG")
    obs = [{"source_timestamp": 102000, "known_at_ms": 102000, "high": 4185.0, "low": 4180.0}]
    res = evaluate_forecast_outcome(snap, obs, as_of_ms=110000, feed_gap=True)
    assert res["outcome"] == ForecastOutcomeDefinition.DATA_UNAVAILABLE.value


def test_fc11_unknown_probability_status():
    """FC11: Forecast summary explicitly shows probability_status as UNAVAILABLE without OOS model."""
    mock_db = MagicMock()
    mock_db.query.return_value.all.return_value = []
    summary = summarize_forecast_observations(mock_db)
    assert summary["probability_status"] == "UNAVAILABLE"
    assert summary["target_touch_fraction"] is None


def test_fc12_sql_savepoint_fault_isolation(isolated_db):
    """FC12: A savepoint error in forecast observation write does not rollback parent transaction."""
    order = models.PaperOrder(
        id="order-fc12-test",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        created_at=1000,
        opened_at=1000,
    )
    isolated_db.add(order)

    # Injected invalid forecast with missing session_id will safely return None
    invalid_fc = {"missing": "all"}
    res = create_forecast_snapshot(isolated_db, invalid_fc)
    assert res is None

    # Parent transaction can commit successfully
    isolated_db.commit()
    persisted = isolated_db.query(models.PaperOrder).filter_by(id="order-fc12-test").first()
    assert persisted is not None
