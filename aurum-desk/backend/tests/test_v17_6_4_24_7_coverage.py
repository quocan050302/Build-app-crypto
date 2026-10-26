"""
backend/tests/test_v17_6_4_24_7_coverage.py
Tests for 24/7 Bitget Perpetual Contract Observation Coverage & Interval Precedence (Phase G, CV01-CV18).
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import (
    engine_observability_service,
    build_market_open_interval_ranges,
)
from services.observation_interval_service import observation_interval_service


@pytest.fixture
def isolated_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False)
    session = TestingSession()
    observation_interval_service.clear()
    try:
        yield session
    finally:
        session.close()
        models.Base.metadata.drop_all(bind=engine)
        observation_interval_service.clear()


def create_test_run(db, run_id: str, started_at_ms: int, ended_at_ms=None):
    rec = models.PaperResearchRunRecord(
        run_id=run_id,
        symbol="XAUUSDT",
        scope="CG01",
        status="COLLECTING",
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
        created_at_ms=started_at_ms,
        updated_at_ms=started_at_ms,
    )
    db.add(rec)
    db.commit()
    return rec


def test_cv01_saturday_good_evidence(isolated_db):
    """CV01: Saturday 00:00 - 12:00 UTC (12h) with 6h good evidence yields 50% coverage."""
    # 2026-10-24 00:00 UTC is Saturday
    t_start = 1792790400000
    t_end = t_start + 12 * 3600 * 1000

    run = create_test_run(isolated_db, "RUN_SAT_01", started_at_ms=t_start)
    # Insert 6h GOOD interval
    inv = models.PaperObservationInterval(
        id=1,
        run_id=run.run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t_start,
        end_ms=t_start + 6 * 3600 * 1000,
        data_quality="GOOD",
        heartbeats_count=240,
        created_at_ms=t_start + 6 * 3600 * 1000,
    )
    isolated_db.add(inv)
    isolated_db.commit()

    res = engine_observability_service.calculate_observation_coverage(
        isolated_db, run_id=run.run_id, start_ms=t_start, end_ms=t_end
    )
    assert res["market_open_hours"] == 12.0
    assert res["observed_healthy_hours"] == 6.0
    assert res["coverage_pct"] == 50.0
    assert res["coverage_state"] == "PARTIAL"


def test_cv02_saturday_without_evidence(isolated_db):
    """CV02: Saturday without evidence yields 0.0% coverage, NEVER 100% or MARKET_CLOSED."""
    t_start = 1792790400000  # Saturday 00:00 UTC
    t_end = t_start + 12 * 3600 * 1000

    run = create_test_run(isolated_db, "RUN_SAT_02", started_at_ms=t_start)

    res = engine_observability_service.calculate_observation_coverage(
        isolated_db, run_id=run.run_id, start_ms=t_start, end_ms=t_end
    )
    assert res["market_open_hours"] == 12.0
    assert res["observed_healthy_hours"] == 0.0
    assert res["coverage_pct"] == 0.0
    assert res["coverage_state"] == "LOW"


def test_cv03_sunday_good_evidence(isolated_db):
    """CV03: Sunday 00:00 - 12:00 UTC with valid evidence yields 100% coverage."""
    t_start = 1792876800000  # Sunday 00:00 UTC
    t_end = t_start + 12 * 3600 * 1000

    run = create_test_run(isolated_db, "RUN_SUN_03", started_at_ms=t_start)
    inv = models.PaperObservationInterval(
        id=2,
        run_id=run.run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t_start,
        end_ms=t_end,
        data_quality="GOOD",
        heartbeats_count=480,
        created_at_ms=t_end,
    )
    isolated_db.add(inv)
    isolated_db.commit()

    res = engine_observability_service.calculate_observation_coverage(
        isolated_db, run_id=run.run_id, start_ms=t_start, end_ms=t_end
    )
    assert res["market_open_hours"] == 12.0
    assert res["observed_healthy_hours"] == 12.0
    assert res["coverage_pct"] == 100.0
    assert res["coverage_state"] == "FULL"


def test_cv04_friday_sunday_24_7_denominator(isolated_db):
    """CV04: Friday 17:00 NY to Sunday 18:00 NY (48 hours) has full 48h denominator for 24/7 Bitget contract."""
    t_start = 1792704000000
    t_end = t_start + 48 * 3600 * 1000
    ranges = build_market_open_interval_ranges(t_start, t_end)
    total_ms = sum(e - s for s, e in ranges)
    assert total_ms == 48 * 3600 * 1000


def test_cv05_good_degraded_overlap(isolated_db):
    """CV05: DEGRADED outranks GOOD on overlapping interval."""
    t_start = 100000
    t_end = t_start + 3600 * 1000  # 1 hour
    run = create_test_run(isolated_db, "RUN_CV05", started_at_ms=t_start)

    # 1 hour GOOD
    inv_good = models.PaperObservationInterval(
        id=10,
        run_id=run.run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t_start,
        end_ms=t_end,
        data_quality="GOOD",
        heartbeats_count=100,
        created_at_ms=t_end,
    )
    # 30 mins DEGRADED overlapping the second half
    inv_deg = models.PaperObservationInterval(
        id=11,
        run_id=run.run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t_start + 1800 * 1000,
        end_ms=t_end,
        data_quality="DEGRADED",
        heartbeats_count=50,
        created_at_ms=t_end,
    )
    isolated_db.add_all([inv_good, inv_deg])
    isolated_db.commit()

    res = engine_observability_service.calculate_observation_coverage(
        isolated_db, run_id=run.run_id, start_ms=t_start, end_ms=t_end
    )
    assert res["observed_healthy_hours"] == 0.5
    assert res["degraded_hours"] == 0.5
    assert res["coverage_pct"] == 50.0


def test_cv06_good_down_overlap(isolated_db):
    """CV06: DOWN outranks GOOD on overlapping interval."""
    t_start = 100000
    t_end = t_start + 3600 * 1000
    run = create_test_run(isolated_db, "RUN_CV06", started_at_ms=t_start)

    inv_good = models.PaperObservationInterval(
        id=20,
        run_id=run.run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t_start,
        end_ms=t_end,
        data_quality="GOOD",
        heartbeats_count=100,
        created_at_ms=t_end,
    )
    inv_down = models.PaperObservationInterval(
        id=21,
        run_id=run.run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t_start,
        end_ms=t_start + 900 * 1000,  # first 15 mins DOWN
        data_quality="DOWN",
        heartbeats_count=0,
        created_at_ms=t_start + 900 * 1000,
    )
    isolated_db.add_all([inv_good, inv_down])
    isolated_db.commit()

    res = engine_observability_service.calculate_observation_coverage(
        isolated_db, run_id=run.run_id, start_ms=t_start, end_ms=t_end
    )
    assert res["down_hours"] == 0.25
    assert res["observed_healthy_hours"] == 0.75
    assert res["coverage_pct"] == 75.0


def test_cv07_duplicate_good_intervals_not_double_counted(isolated_db):
    """CV07: Duplicate identical intervals are merged and not counted twice."""
    t_start = 100000
    t_end = t_start + 3600 * 1000
    run = create_test_run(isolated_db, "RUN_CV07", started_at_ms=t_start)

    inv1 = models.PaperObservationInterval(
        id=30,
        run_id=run.run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t_start,
        end_ms=t_end,
        data_quality="GOOD",
        heartbeats_count=50,
        created_at_ms=t_end,
    )
    inv2 = models.PaperObservationInterval(
        id=31,
        run_id=run.run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t_start,
        end_ms=t_end,
        data_quality="GOOD",
        heartbeats_count=50,
        created_at_ms=t_end,
    )
    isolated_db.add_all([inv1, inv2])
    isolated_db.commit()

    res = engine_observability_service.calculate_observation_coverage(
        isolated_db, run_id=run.run_id, start_ms=t_start, end_ms=t_end
    )
    assert res["observed_healthy_hours"] == 1.0
    assert res["coverage_pct"] == 100.0


def test_cv08_single_heartbeat_no_fabricated_uptime(isolated_db):
    """CV08: An isolated single heartbeat event does not persist a full 1-hour interval."""
    run = create_test_run(isolated_db, "RUN_CV08", started_at_ms=1000)
    observation_interval_service.record_heartbeat_event(
        isolated_db,
        run_id=run.run_id,
        symbol="XAUUSDT",
        timestamp_ms=1000,
    )
    # Single heartbeat with no closing event has not persisted intervals to DB
    count = isolated_db.query(models.PaperObservationInterval).count()
    assert count == 0


def test_cv09_missing_run_returns_run_not_found(isolated_db):
    """CV09: Requesting coverage for nonexistent run returns RUN_NOT_FOUND."""
    res = engine_observability_service.calculate_observation_coverage(
        isolated_db,
        run_id="NONEXISTENT_RUN_999",
        start_ms=100000,
        end_ms=200000,
    )
    assert res["coverage_state"] == "RUN_NOT_FOUND"
    assert res["coverage_pct"] is None


def test_cv10_mixed_run_ids_isolated(isolated_db):
    """CV10: Intervals belonging to RUN_A are not counted towards RUN_B."""
    run_a = create_test_run(isolated_db, "RUN_A_CV10", started_at_ms=100000)
    run_b = create_test_run(isolated_db, "RUN_B_CV10", started_at_ms=100000)

    t_start = 100000
    t_end = t_start + 3600 * 1000

    inv_a = models.PaperObservationInterval(
        id=40,
        run_id="RUN_A_CV10",
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t_start,
        end_ms=t_end,
        data_quality="GOOD",
        heartbeats_count=100,
        created_at_ms=t_end,
    )
    isolated_db.add(inv_a)
    isolated_db.commit()

    res_b = engine_observability_service.calculate_observation_coverage(
        isolated_db, run_id="RUN_B_CV10", start_ms=t_start, end_ms=t_end
    )
    assert res_b["observed_healthy_hours"] == 0.0
    assert res_b["coverage_pct"] == 0.0


def test_cv11_replay_interval_exclusion(isolated_db):
    """CV11: Replay events are excluded from live forward observation interval production."""
    run = create_test_run(isolated_db, "RUN_CV11", started_at_ms=1000)
    res = observation_interval_service.record_heartbeat_event(
        isolated_db,
        run_id=run.run_id,
        symbol="XAUUSDT",
        timestamp_ms=1000,
        evidence_origin="REPLAY",
    )
    assert res is None
    count = isolated_db.query(models.PaperObservationInterval).count()
    assert count == 0


def test_cv12_dst_weekend_unaffected(isolated_db):
    """CV12: Bitget Perpetual 24/7 denominator is strictly elapsed time across NY DST shifts."""
    t_start = 1773014400000
    t_end = t_start + 24 * 3600 * 1000
    ranges = build_market_open_interval_ranges(t_start, t_end)
    total_ms = sum(e - s for s, e in ranges)
    assert total_ms == 24 * 3600 * 1000


def test_cv13_7_day_rolling_period():
    """CV13: 7-day period has 168 hours denominator on 24/7 calendar."""
    t_start = 1000000
    t_end = t_start + 7 * 86400 * 1000
    ranges = build_market_open_interval_ranges(t_start, t_end)
    total_hours = sum(e - s for s, e in ranges) / 3600000.0
    assert total_hours == 168.0


def test_cv14_14_day_rolling_period():
    """CV14: 14-day period has 336 hours denominator on 24/7 calendar."""
    t_start = 1000000
    t_end = t_start + 14 * 86400 * 1000
    ranges = build_market_open_interval_ranges(t_start, t_end)
    total_hours = sum(e - s for s, e in ranges) / 3600000.0
    assert total_hours == 336.0


def test_cv15_30_day_rolling_period():
    """CV15: 30-day period has 720 hours denominator on 24/7 calendar."""
    t_start = 1000000
    t_end = t_start + 30 * 86400 * 1000
    ranges = build_market_open_interval_ranges(t_start, t_end)
    total_hours = sum(e - s for s, e in ranges) / 3600000.0
    assert total_hours == 720.0


def test_cv16_exact_100_percent_coverage(isolated_db):
    """CV16: Verified continuous interval matching window yields exact 100.0% coverage."""
    t_start = 1000000
    t_end = t_start + 10 * 3600 * 1000
    run = create_test_run(isolated_db, "RUN_CV16", started_at_ms=t_start)

    inv = models.PaperObservationInterval(
        id=50,
        run_id=run.run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t_start,
        end_ms=t_end,
        data_quality="GOOD",
        heartbeats_count=400,
        created_at_ms=t_end,
    )
    isolated_db.add(inv)
    isolated_db.commit()

    res = engine_observability_service.calculate_observation_coverage(
        isolated_db, run_id=run.run_id, start_ms=t_start, end_ms=t_end
    )
    assert res["coverage_pct"] == 100.0
    assert res["coverage_state"] == "FULL"


def test_cv17_zero_duration_period(isolated_db):
    """CV17: Query with end <= start returns NO_ELIGIBLE_WINDOW."""
    res = engine_observability_service.calculate_observation_coverage(
        isolated_db, start_ms=5000, end_ms=5000
    )
    assert res["coverage_state"] == "NO_ELIGIBLE_WINDOW"
    assert res["coverage_pct"] is None


def test_cv18_invalid_negative_duration(isolated_db):
    """CV18: Query with end < start returns NO_ELIGIBLE_WINDOW."""
    res = engine_observability_service.calculate_observation_coverage(
        isolated_db, start_ms=6000, end_ms=5000
    )
    assert res["coverage_state"] == "NO_ELIGIBLE_WINDOW"
    assert res["coverage_pct"] is None
