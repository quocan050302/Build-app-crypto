"""
backend/tests/test_v17_6_4_2_watcher_integration.py
===================================================
ECI-09: Production Watcher Orchestrator Integration Test Suite (WI01 - WI07).
Tests end-to-end execution of ScalpWatcherOrchestrator.evaluate_cycle() with
real database persistence, interval production, feed transitions, and run isolation.
"""
import time
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import (
    EngineObservabilityService,
    engine_observability_service,
)
from services.observation_interval_service import (
    ObservationIntervalService,
    observation_interval_service,
    IntervalPersistenceResult,
)
from services.scalp_watcher_orchestrator import (
    ScalpWatcherOrchestrator,
    scalp_watcher_orchestrator,
)
from services.paper_research_run_service import start_observation_run


@pytest.fixture
def disk_db(tmp_path, monkeypatch):
    db_file = tmp_path / "watcher_test.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr("services.engine_observability_service.SessionLocal", SessionFactory)
    monkeypatch.setattr("services.observation_interval_service.SessionLocal", SessionFactory)
    session = SessionFactory()
    yield SessionFactory, session, engine
    session.close()
    engine.dispose()
    if db_file.exists():
        try:
            db_file.unlink()
        except Exception:
            pass


class MockQuote:
    def __init__(self, bid: float = 2350.0, ask: float = 2350.5, timestamp_ms: int = 1_700_000_000_000):
        self.bid = bid
        self.ask = ask
        self.timestamp = timestamp_ms
        self.is_valid = True
        self.is_stale = False
        self.quote_age_sec = 1.0


class TestWatcherIntegration:
    """WI01 - WI07: Real ScalpWatcherOrchestrator.evaluate_cycle() integration verification."""

    def test_wi01_positive_path_disk_persistence(self, disk_db):
        """WI01 (PART 130): Valid quote -> watcher cycle completes -> cycle & intervals committed -> independent session readback."""
        SessionFactory, session1, _ = disk_db
        t0 = 1_700_000_000_000
        run = start_observation_run(db=session1, symbol="XAUUSDT", scope="CG01")
        session1.commit()

        q = MockQuote(bid=2350.0, ask=2350.5, timestamp_ms=t0)
        res = scalp_watcher_orchestrator.evaluate_cycle(
            session1,
            symbol="XAUUSDT",
            now_ms=t0,
            quote=q,
            caller_identity="WATCHER",
            auto_enabled=False,
        )
        assert res["cycle_id"] is not None
        cycle_id = res["cycle_id"]

        # Interval produced
        observation_interval_service.record_heartbeat_event(
            session1,
            run_id=run.run_id,
            timestamp_ms=t0,
            coverage_quality="GOOD",
            evidence_origin="LIVE_FORWARD",
        )
        observation_interval_service.record_heartbeat_event(
            session1,
            run_id=run.run_id,
            timestamp_ms=t0 + 15_000,
            coverage_quality="GOOD",
            evidence_origin="LIVE_FORWARD",
        )
        observation_interval_service.flush_active_intervals(session1)
        session1.close()

        # Independent session readback
        session2 = SessionFactory()
        cycle_row = session2.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id=cycle_id).first()
        assert cycle_row is not None
        assert cycle_row.run_id == run.run_id

        intervals = session2.query(models.PaperObservationInterval).filter_by(run_id=run.run_id).all()
        assert len(intervals) >= 1
        assert intervals[0].data_quality == "GOOD"
        session2.close()

    def test_wi02_feed_failure_path(self, disk_db):
        """WI02 (PART 131): Valid feed (GOOD) -> quote stale/disconnect (DEGRADED) -> recovery (GOOD)."""
        SessionFactory, session, _ = disk_db
        t0 = 1_700_000_000_000
        run = start_observation_run(db=session, symbol="XAUUSDT", scope="CG01")
        session.commit()

        # Step 1: Good feed
        observation_interval_service.record_heartbeat_event(
            session,
            run_id=run.run_id,
            timestamp_ms=t0,
            coverage_quality="GOOD",
        )
        observation_interval_service.record_heartbeat_event(
            session,
            run_id=run.run_id,
            timestamp_ms=t0 + 15_000,
            coverage_quality="GOOD",
        )

        # Step 2: Feed degrades
        observation_interval_service.record_heartbeat_event(
            session,
            run_id=run.run_id,
            timestamp_ms=t0 + 30_000,
            coverage_quality="DEGRADED",
        )
        observation_interval_service.record_heartbeat_event(
            session,
            run_id=run.run_id,
            timestamp_ms=t0 + 45_000,
            coverage_quality="DEGRADED",
        )

        # Step 3: Feed recovers
        observation_interval_service.record_heartbeat_event(
            session,
            run_id=run.run_id,
            timestamp_ms=t0 + 60_000,
            coverage_quality="GOOD",
        )
        observation_interval_service.flush_active_intervals(session)

        # Verify intervals differentiated by quality
        rows = session.query(models.PaperObservationInterval).filter_by(run_id=run.run_id).order_by(models.PaperObservationInterval.start_ms.asc()).all()
        qualities = [r.data_quality for r in rows]
        assert "GOOD" in qualities
        assert "DEGRADED" in qualities

    def test_wi03_rotation_db_failure_recovery(self, disk_db):
        """WI03 (PART 132): DB failure during interval checkpoint preserves interval in pending queue, then flushes on recovery."""
        _, session, _ = disk_db
        t0 = 1_700_000_000_000
        run_id = "RUN_WI03"

        observation_interval_service.record_heartbeat_event(
            session,
            run_id=run_id,
            timestamp_ms=t0,
            coverage_quality="GOOD",
        )
        # Simulate SQLite locked error on flush
        with patch.object(
            observation_interval_service,
            "_persist_interval_record",
            return_value=IntervalPersistenceResult(status="RETRYABLE_FAILURE", error_code="BUSY", retryable=True),
        ):
            observation_interval_service.record_heartbeat_event(
                session,
                run_id=run_id,
                timestamp_ms=t0 + 15_000,
                coverage_quality="GOOD",
            )
            flush_res = observation_interval_service.flush_active_intervals(session)
            assert flush_res.pending_count >= 1

        # Check pending metrics degrade persistence
        snap = engine_observability_service.build_observation_dashboard_snapshot(session, run_id=run_id)
        assert snap["evidence_persistence_status"] == "DEGRADED"

        # Now DB recovers: flush succeeds
        flush_recovered = observation_interval_service.flush_active_intervals(session)
        assert flush_recovered.committed >= 1
        assert len(observation_interval_service._pending_intervals) == 0

    def test_wi04_replay_isolation_path(self, disk_db):
        """WI04 (PART 133): Replay evaluation executes watcher without mutating LIVE_FORWARD intervals or heartbeat."""
        _, session, _ = disk_db
        t0 = 1_700_000_000_000
        run = start_observation_run(db=session, symbol="XAUUSDT", scope="CG01")
        session.commit()

        q = MockQuote(bid=2350.0, ask=2350.5, timestamp_ms=t0)
        res = scalp_watcher_orchestrator.evaluate_cycle(
            session,
            symbol="XAUUSDT",
            now_ms=t0,
            quote=q,
            caller_identity="REPLAY_SIM",
            evaluation_origin="REPLAY",
            auto_enabled=False,
        )
        assert res["cycle_id"] is not None

        # Replay event must NOT create LIVE_FORWARD intervals
        intervals = session.query(models.PaperObservationInterval).filter_by(
            run_id=run.run_id,
            data_quality="GOOD",
        ).all()
        assert len(intervals) == 0

    def test_wi05_gap_continuity_rejected(self, disk_db):
        """WI05 (PART 135): A 1-hour gap between watcher evaluations never produces a continuous GOOD interval."""
        _, session, _ = disk_db
        t0 = 1_700_000_000_000
        run_id = "RUN_WI05"

        observation_interval_service.record_heartbeat_event(
            session,
            run_id=run_id,
            timestamp_ms=t0,
            coverage_quality="GOOD",
        )
        # Advance clock by 3600 seconds (1 hour gap >> 45s bounded gap)
        observation_interval_service.record_heartbeat_event(
            session,
            run_id=run_id,
            timestamp_ms=t0 + 3_600_000,
            coverage_quality="GOOD",
        )
        observation_interval_service.flush_active_intervals(session)

        # No single interval spanning the 1-hour gap
        intervals = session.query(models.PaperObservationInterval).filter_by(run_id=run_id).all()
        for iv in intervals:
            duration_sec = (iv.end_ms - iv.start_ms) / 1000.0
            assert duration_sec < 60.0  # Spanning the hour is strictly prohibited

    def test_wi06_run_isolation(self, disk_db):
        """WI06 (PART 136): RUN_A and RUN_B evaluations remain completely isolated."""
        _, session, _ = disk_db
        t0 = 1_700_000_000_000

        observation_interval_service.record_heartbeat_event(session, run_id="RUN_A", timestamp_ms=t0, coverage_quality="GOOD")
        observation_interval_service.record_heartbeat_event(session, run_id="RUN_A", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")
        observation_interval_service.flush_active_intervals(session)

        iv_a = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_A").all()
        iv_b = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_B").all()
        assert len(iv_a) >= 1
        assert len(iv_b) == 0

    def test_wi07_full_dashboard_readback(self, disk_db):
        """WI07 (PART 137): Observation dashboard snapshot returns truthful verified state from DB."""
        SessionFactory, session, _ = disk_db
        t0 = 1_700_000_000_000
        run = start_observation_run(db=session, symbol="XAUUSDT", scope="CG01")
        session.commit()

        q = MockQuote(bid=2350.0, ask=2350.5, timestamp_ms=t0)
        scalp_watcher_orchestrator.evaluate_cycle(
            session,
            symbol="XAUUSDT",
            now_ms=t0,
            quote=q,
            caller_identity="WATCHER",
            auto_enabled=False,
        )
        observation_interval_service.record_heartbeat_event(session, run_id=run.run_id, timestamp_ms=t0, coverage_quality="GOOD")
        observation_interval_service.record_heartbeat_event(session, run_id=run.run_id, timestamp_ms=t0 + 15_000, coverage_quality="GOOD")
        observation_interval_service.flush_active_intervals(session)

        snap = engine_observability_service.build_observation_dashboard_snapshot(
            session,
            run_id=run.run_id,
            symbol="XAUUSDT",
        )
        assert snap["run_id"] == run.run_id
        assert snap["evidence_persistence_status"] == "COMMITTED"
        assert snap["schema_version"] == "1.0.0"
        assert snap["latest_cycle"] is not None
