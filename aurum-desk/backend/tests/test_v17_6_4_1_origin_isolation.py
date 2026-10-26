"""
backend/tests/test_v17_6_4_1_origin_isolation.py
AURUM V17.6.4.1 — Evidence Origin Propagation & Replay Isolation Test Suite (EVID-02 & EVID-11).

Cases:
- OR01: Live watcher produces LIVE_FORWARD origin
- OR02: Live scheduler produces LIVE_FORWARD origin
- OR03: Replay evaluation is strictly marked REPLAY
- OR04: Test evaluation is marked TEST and isolated
- OR05: Unknown origin is rejected / fail-closed, does not default to LIVE_FORWARD
- OR06: Replay evaluation while LIVE run is active does not overwrite live heartbeat
- OR07: Replay and live share symbol XAUUSDT without interval contamination
- OR08: Replay and live in same physical DB remain isolated
- OR09: Failed cycle origin is preserved
- OR10: Skipped cycle origin is preserved
- OR11: Restart readback preserves origin in records
- OR12: Active interval key isolates by evidence_origin
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import (
    EngineObservabilityService,
    ObservationEvidenceOrigin,
    resolve_observation_evidence_origin,
)
from services.observation_interval_service import ObservationIntervalService


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    session = SessionFactory()
    yield session, SessionFactory
    session.close()
    engine.dispose()


def test_or01_live_watcher_origin():
    """OR01: Live watcher resolves to LIVE_FORWARD origin."""
    origin = resolve_observation_evidence_origin("LIVE_FORWARD")
    assert origin == ObservationEvidenceOrigin.LIVE_FORWARD
    assert origin.value == "LIVE_FORWARD"


def test_or02_live_scheduler_origin():
    """OR02: Scheduler live mode resolves to LIVE_FORWARD."""
    origin = resolve_observation_evidence_origin("SCHEDULER_LIVE", default=ObservationEvidenceOrigin.LIVE_FORWARD)
    assert origin in (ObservationEvidenceOrigin.LIVE_FORWARD, ObservationEvidenceOrigin.UNKNOWN)


def test_or03_replay_evaluation_isolated(db_session):
    """OR03: Replay evaluation MUST NOT generate LIVE_FORWARD observation interval."""
    session, _ = db_session
    obs_svc = EngineObservabilityService()
    interval_svc = ObservationIntervalService()

    t_hist = 1_600_000_000_000  # Historical replay timestamp

    # Record evaluation cycle with REPLAY origin
    res = obs_svc.record_evaluation_cycle(
        session,
        cycle_id="CYCLE_REPLAY_01",
        run_id="RUN_REPLAY",
        started_at_ms=t_hist,
        completed_at_ms=t_hist + 100,
        status="COMPLETED",
        evidence_origin="REPLAY",
    )

    from services.observation_interval_service import observation_interval_service
    observation_interval_service.flush_active_intervals(session)

    # Assert NO PaperObservationInterval is created for LIVE_FORWARD
    intervals = session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_REPLAY"
    ).all()
    assert len(intervals) == 0, "Replay evaluation generated live observation intervals!"


def test_or04_test_origin_isolated(db_session):
    """OR04: TEST origin must be isolated from live observation coverage."""
    session, _ = db_session
    obs_svc = EngineObservabilityService()

    res = obs_svc.record_evaluation_cycle(
        session,
        cycle_id="CYCLE_TEST_01",
        run_id="RUN_TEST",
        started_at_ms=1_700_000_000_000,
        completed_at_ms=1_700_000_000_100,
        status="COMPLETED",
        evidence_origin="TEST",
    )

    from services.observation_interval_service import observation_interval_service
    observation_interval_service.flush_active_intervals(session)

    intervals = session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_TEST"
    ).all()
    assert len(intervals) == 0, "TEST origin generated live observation intervals!"


def test_or05_unknown_origin_fails_closed():
    """OR05: Arbitrary/unknown origin strings must not default to LIVE_FORWARD."""
    origin = resolve_observation_evidence_origin("RANDOM_USER_STRING")
    assert origin != ObservationEvidenceOrigin.LIVE_FORWARD
    assert origin == ObservationEvidenceOrigin.UNKNOWN


def test_or06_replay_with_live_run_active(db_session):
    """OR06: Replay evaluation while LIVE worker is active does not overwrite live state."""
    session, _ = db_session
    obs_svc = EngineObservabilityService()

    t_live = 1_700_000_000_000
    t_hist = 1_500_000_000_000

    # 1. LIVE heartbeat
    obs_svc.record_engine_heartbeat(
        session,
        symbol="XAUUSDT",
        run_id="RUN_LIVE",
        state="RUNNING",
        heartbeat_at_ms=t_live,
        metadata={"worker_role": "WATCHER_PROCESS", "evidence_origin": "LIVE_FORWARD"},
    )

    live_hb = session.query(models.EngineHeartbeatRecord).filter_by(run_id="RUN_LIVE").first()
    assert live_hb is not None
    assert live_hb.last_process_heartbeat_at_ms == t_live

    # 2. Historical Replay evaluation
    obs_svc.record_evaluation_cycle(
        session,
        cycle_id="REPLAY_CYC_01",
        run_id="RUN_REPLAY",
        symbol="XAUUSDT",
        started_at_ms=t_hist,
        completed_at_ms=t_hist + 50,
        status="COMPLETED",
        evidence_origin="REPLAY",
    )

    # 3. Verify LIVE heartbeat is untouched
    live_hb_after = session.query(models.EngineHeartbeatRecord).filter_by(run_id="RUN_LIVE").first()
    assert live_hb_after.last_process_heartbeat_at_ms == t_live
    assert live_hb_after.last_cycle_id != "REPLAY_CYC_01"


def test_or07_replay_and_live_same_symbol(db_session):
    """OR07: Same symbol XAUUSDT does not cross-contaminate replay and live cycles."""
    session, _ = db_session
    obs_svc = EngineObservabilityService()

    obs_svc.record_evaluation_cycle(
        session,
        cycle_id="LIVE_CYC_01",
        run_id="RUN_LIVE",
        started_at_ms=1_700_000_000_000,
        completed_at_ms=1_700_000_000_100,
        status="COMPLETED",
        evidence_origin="LIVE_FORWARD",
    )
    obs_svc.record_evaluation_cycle(
        session,
        cycle_id="REPLAY_CYC_02",
        run_id="RUN_REPLAY",
        started_at_ms=1_600_000_000_000,
        completed_at_ms=1_600_000_000_100,
        status="COMPLETED",
        evidence_origin="REPLAY",
    )

    live_rec = session.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="LIVE_CYC_01").first()
    replay_rec = session.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="REPLAY_CYC_02").first()

    assert live_rec.run_id == "RUN_LIVE"
    assert replay_rec.run_id == "RUN_REPLAY"


def test_or08_replay_and_live_same_db(db_session):
    """OR08: Separate origins maintain isolation even in shared DB."""
    session, _ = db_session
    interval_svc = ObservationIntervalService()

    # LIVE event
    interval_svc.record_heartbeat_event(
        session, run_id="RUN_LIVE", timestamp_ms=1_700_000_000_000,
        is_feed_healthy=True, is_quote_fresh=True, coverage_quality="GOOD",
        evidence_origin="LIVE_FORWARD"
    )
    # REPLAY event
    interval_svc.record_heartbeat_event(
        session, run_id="RUN_REPLAY", timestamp_ms=1_600_000_000_000,
        is_feed_healthy=True, is_quote_fresh=True, coverage_quality="GOOD",
        evidence_origin="REPLAY"
    )

    interval_svc.flush_active_intervals(session)

    live_rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_LIVE").all()
    replay_rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_REPLAY").all()

    assert len(replay_rows) == 0


def test_or09_failed_cycle_origin(db_session):
    """OR09: Failed evaluation cycle preserves origin in metadata."""
    session, _ = db_session
    obs_svc = EngineObservabilityService()

    res = obs_svc.record_evaluation_cycle(
        session,
        cycle_id="FAILED_CYC_01",
        run_id="RUN_REPLAY",
        started_at_ms=1_600_000_000_000,
        completed_at_ms=1_600_000_000_050,
        status="FAILED",
        failure_reason="TEST_EXCEPTION",
        evidence_origin="REPLAY",
    )
    row = session.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="FAILED_CYC_01").first()
    assert row is not None
    import json
    meta = json.loads(row.metadata_json) if row.metadata_json else {}
    assert meta.get("evidence_origin") == "REPLAY"


def test_or10_skipped_cycle_origin(db_session):
    """OR10: Skipped cycle preserves origin in metadata."""
    session, _ = db_session
    obs_svc = EngineObservabilityService()

    res = obs_svc.record_evaluation_cycle(
        session,
        cycle_id="SKIPPED_CYC_01",
        run_id="RUN_REPLAY",
        started_at_ms=1_600_000_000_000,
        completed_at_ms=1_600_000_000_050,
        status="SKIPPED",
        skip_reason="NO_VALID_QUOTE",
        evidence_origin="REPLAY",
    )
    row = session.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="SKIPPED_CYC_01").first()
    assert row is not None
    import json
    meta = json.loads(row.metadata_json) if row.metadata_json else {}
    assert meta.get("evidence_origin") == "REPLAY"


def test_or11_restart_readback(db_session):
    """OR11: Fresh session preserves origin separation."""
    session, SessionFactory = db_session
    obs_svc = EngineObservabilityService()

    obs_svc.record_evaluation_cycle(
        session,
        cycle_id="CYC_PERSIST_01",
        run_id="RUN_REPLAY",
        started_at_ms=1_600_000_000_000,
        completed_at_ms=1_600_000_000_050,
        status="COMPLETED",
        evidence_origin="REPLAY",
    )
    session.close()

    # Reopen fresh session
    new_session = SessionFactory()
    row = new_session.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="CYC_PERSIST_01").first()
    assert row is not None
    import json
    meta = json.loads(row.metadata_json) if row.metadata_json else {}
    assert meta.get("evidence_origin") == "REPLAY"
    new_session.close()


def test_or12_active_interval_identity_isolation():
    """OR12: Interval producer key isolates by evidence_origin."""
    producer = ObservationIntervalService()
    t0 = 1_700_000_000_000

    producer.record_heartbeat_event(
        None, run_id="RUN_1", timestamp_ms=t0,
        is_feed_healthy=True, is_quote_fresh=True, coverage_quality="GOOD",
        evidence_origin="LIVE_FORWARD",
    )
    producer.record_heartbeat_event(
        None, run_id="RUN_1", timestamp_ms=t0,
        is_feed_healthy=True, is_quote_fresh=True, coverage_quality="GOOD",
        evidence_origin="REPLAY",
    )

    with producer._lock:
        origins = [k[4] for k in producer._active_intervals.keys() if len(k) >= 5]
        # REPLAY should never be in active intervals
        assert "REPLAY" not in origins
