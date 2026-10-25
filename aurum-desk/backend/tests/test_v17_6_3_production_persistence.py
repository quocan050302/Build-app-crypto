"""V17.6.3: Verify the actual watcher early-return path persists telemetry across sessions.

These tests use a disk-backed SQLite database and explicitly close the caller's
Session before querying from a fresh independent Session.
"""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import database
import models
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator


def test_disabled_watcher_cycle_remains_committed_after_caller_closes(tmp_path, monkeypatch):
    db_path = tmp_path / "watcher_durable.db"
    engine = create_engine(f"sqlite:///{db_path}")
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)

    watcher = ScalpWatcherOrchestrator()
    monkeypatch.setattr(watcher, "_record_and_broadcast_cg01_evaluation", lambda *args, **kwargs: None)
    disabled_config = SimpleNamespace(
        scalp_m1_mode="OFF",
        scalp_m1_enabled=False,
        daily_ny_research_enabled=False,
        cg01_enabled=False,
    )
    caller = factory()
    try:
        output = watcher.evaluate_cycle(
            caller,
            symbol="XAUUSDT",
            now_ms=1792584000000,
            config=disabled_config,
            auto_enabled=False,
        )
        assert output["decision_status"] == "SKIPPED"
        cycle_id = output["cycle_id"]
    finally:
        # No commit here: matches run_watcher_loop's caller-owned Session.
        caller.close()

    verifier = factory()
    try:
        persisted = verifier.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id=cycle_id).one_or_none()
        assert persisted is not None, "Watcher returned, but its cycle was rolled back on Session close"
        assert persisted.status == "SKIPPED"
        heartbeat = verifier.query(models.EngineHeartbeatRecord).filter_by(
            engine_instance_id=watcher_instance_id()
        ).one_or_none()
        # The cycle row is authoritative; heartbeat identity can be shared with scheduler.
        assert heartbeat is not None
        assert heartbeat.last_cycle_id == cycle_id
    finally:
        verifier.close()
        engine.dispose()


def watcher_instance_id():
    from services.engine_observability_service import engine_observability_service
    return engine_observability_service.current_instance_id
