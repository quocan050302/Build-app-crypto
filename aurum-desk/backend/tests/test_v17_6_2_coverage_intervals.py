"""
backend/tests/test_v17_6_2_coverage_intervals.py
AURUM V17.6.2 — Trustworthy Observation Coverage Mathematics (FIX-02).

Verifies:
- CV01-CV05: clip_interval and merge_time_intervals pure mathematical correctness.
- Overlapping intervals are not double-counted.
- Synthetic cycles_count * cadence fallback is completely removed.
- When market open duration is 0, coverage_pct is None and state is MARKET_CLOSED.
- Run isolation: intervals belonging to RUN_B are not counted towards RUN_A.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services.engine_observability_service import (
    clip_interval,
    merge_time_intervals,
    engine_observability_service,
)


def test_cv01_clip_interval_inside_window():
    res = clip_interval(100, 200, 50, 250)
    assert res == (100, 200)


def test_cv02_clip_interval_starts_before_window():
    res = clip_interval(50, 150, 100, 200)
    assert res == (100, 150)


def test_cv03_clip_interval_ends_after_window():
    res = clip_interval(150, 250, 100, 200)
    assert res == (150, 200)


def test_cv04_clip_interval_completely_outside():
    res = clip_interval(10, 50, 100, 200)
    assert res is None

    res2 = clip_interval(250, 300, 100, 200)
    assert res2 is None


def test_cv05_merge_overlapping_intervals():
    intervals = [
        (1000, 2000),  # 1000ms
        (1500, 2500),  # overlaps with first, union is [1000, 2500] (1500ms)
        (3000, 4000),  # disjoint [3000, 4000] (1000ms)
    ]
    total_ms = merge_time_intervals(intervals)
    assert total_ms == 2500  # 1500 + 1000, NOT 1000 + 1000 + 1000 = 3000!


def test_cv06_merge_empty_and_single_intervals():
    assert merge_time_intervals([]) == 0
    assert merge_time_intervals([(100, 200)]) == 100


def test_cv07_zero_market_open_returns_market_closed(tmp_path):
    db_path = tmp_path / "coverage_test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    models.Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()

    try:
        # A Saturday window where market is 100% closed:
        # 2026-10-24 12:00:00 UTC (Saturday)
        sat_start = 1792843200000
        sat_end = sat_start + 3600 * 1000

        cov = engine_observability_service.calculate_observation_coverage(
            session,
            run_id="run-sat-01",
            start_ms=sat_start,
            end_ms=sat_end,
            calendar_type="TRADITIONAL_GOLD_REFERENCE",
        )

        assert cov["expected_market_open_ms"] == 0
        assert cov["coverage_pct"] is None
        assert cov["coverage_state"] == "MARKET_CLOSED"
    finally:
        session.close()


def test_cv08_coverage_run_isolation_and_no_synthetic_cadence_fallback(tmp_path):
    db_path = tmp_path / "coverage_iso.db"
    engine = create_engine(f"sqlite:///{db_path}")
    models.Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()

    try:
        t_start = 1700060400000  # Wednesday 15:00 UTC (10:00 AM NY - fully open market)
        t_end = t_start + 3600 * 1000

        # Seed interval for RUN_A
        inv_a = models.PaperObservationInterval(
            run_id="RUN_A",
            start_ms=t_start + 100_000,
            end_ms=t_start + 700_000,  # 600,000ms duration
            data_quality="GOOD",
            created_at_ms=t_start + 700_000,
        )

        # Seed interval for RUN_B
        inv_b = models.PaperObservationInterval(
            run_id="RUN_B",
            start_ms=t_start + 200_000,
            end_ms=t_start + 1_200_000,  # 1,000,000ms duration
            data_quality="GOOD",
            created_at_ms=t_start + 1_200_000,
        )
        session.add(inv_a)
        session.add(inv_b)
        session.commit()

        cov_a = engine_observability_service.calculate_observation_coverage(
            session,
            run_id="RUN_A",
            start_ms=t_start,
            end_ms=t_end,
        )

        cov_b = engine_observability_service.calculate_observation_coverage(
            session,
            run_id="RUN_B",
            start_ms=t_start,
            end_ms=t_end,
        )

        assert cov_a["observed_intervals_ms"] == 600_000
        assert cov_b["observed_intervals_ms"] == 1_000_000
    finally:
        session.close()
