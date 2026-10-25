"""V17.6.3: GOOD coverage only counts evidence within market-eligible time."""
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services.engine_observability_service import (
    engine_observability_service,
    build_market_open_interval_ranges,
    merge_time_intervals,
)

NY = ZoneInfo("America/New_York")
H = 3600000


def stamp(year, month, day, hour, minute=0):
    return int(datetime(year, month, day, hour, minute, tzinfo=NY).timestamp() * 1000)


def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'coverage.db'}")
    models.Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def add_interval(db, run_id, start, end, quality="GOOD"):
    db.add(models.PaperObservationInterval(
        run_id=run_id, symbol="XAUUSDT", scope="CG01",
        start_ms=start, end_ms=end, data_quality=quality, created_at_ms=end,
    ))


def test_closed_market_good_evidence_cannot_cause_100_percent(tmp_path):
    engine, db = db_session(tmp_path)
    try:
        start = stamp(2026, 10, 23, 12)  # Friday before weekend close
        end = stamp(2026, 10, 25, 20)  # Sunday, after open
        add_interval(db, "R", stamp(2026, 10, 24, 10), stamp(2026, 10, 24, 18))
        add_interval(db, "R", stamp(2026, 10, 25, 18), stamp(2026, 10, 25, 19))
        db.commit()

        coverage = engine_observability_service.calculate_observation_coverage(
            db, run_id="R", start_ms=start, end_ms=end
        )
        assert coverage["expected_market_open_ms"] == 7 * H
        assert coverage["observed_intervals_ms"] == H
        assert coverage["coverage_pct"] == 14.3
        assert coverage["unverified_hours"] == 6.0
    finally:
        db.close()
        engine.dispose()


def test_only_closed_market_good_interval_gives_zero_verified_coverage(tmp_path):
    engine, db = db_session(tmp_path)
    try:
        start = stamp(2026, 10, 23, 12)
        end = stamp(2026, 10, 25, 20)
        add_interval(db, "R", stamp(2026, 10, 24, 10), stamp(2026, 10, 24, 18))
        db.commit()
        coverage = engine_observability_service.calculate_observation_coverage(
            db, run_id="R", start_ms=start, end_ms=end
        )
        assert coverage["coverage_pct"] == 0.0
        assert coverage["observed_intervals_ms"] == 0
    finally:
        db.close()
        engine.dispose()


def test_degraded_overrides_good_without_double_counting(tmp_path):
    engine, db = db_session(tmp_path)
    try:
        start = stamp(2026, 10, 20, 12)
        end = start + H
        add_interval(db, "R", start, end, "GOOD")
        add_interval(db, "R", start + 20 * 60000, start + 40 * 60000, "DEGRADED")
        db.commit()

        coverage = engine_observability_service.calculate_observation_coverage(
            db, run_id="R", start_ms=start, end_ms=end
        )
        assert coverage["observed_intervals_ms"] == 40 * 60000
        assert coverage["degraded_hours"] == 0.33
        assert coverage["unverified_hours"] == 0
        assert coverage["coverage_pct"] == 66.7
    finally:
        db.close()
        engine.dispose()


def test_exact_market_open_segments_in_both_dst_seasons():
    for start, end in [
        (stamp(2026, 10, 23, 12), stamp(2026, 10, 25, 20)),
        (stamp(2026, 11, 6, 12), stamp(2026, 11, 8, 20)),
    ]:
        assert merge_time_intervals(build_market_open_interval_ranges(start, end)) == 7 * H
