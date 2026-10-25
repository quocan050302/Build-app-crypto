"""
backend/tests/test_v17_6_4_2_metadata_good_bypass.py
===================================================
ECI-08: Prevention of Metadata-Only False GOOD Quality Authorization.
Verifies that caller-supplied boolean flags (is_feed_healthy=True, is_quote_fresh=True)
or raw metadata strings ("coverage_quality": "GOOD") CANNOT bypass authoritative feed
verification without real, valid quote evidence.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import (
    EngineObservabilityService,
    ObservationEvidenceQuality,
    resolve_observation_evidence_quality,
)
from services.observation_interval_service import ObservationIntervalService


@pytest.fixture
def mem_db():
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


class TestMetadataGoodBypassPrevention:
    """Tests ensuring metadata flags cannot spoof GOOD observation intervals."""

    def test_mb01_spoofed_metadata_string_fails_closed(self, mem_db):
        """MB01: metadata={'coverage_quality': 'GOOD'} without feed evidence yields UNVERIFIED."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        # Heartbeat with spoofed coverage_quality: GOOD
        obs_svc.record_engine_heartbeat(
            session,
            symbol="XAUUSDT",
            run_id="RUN_MB01",
            state="RUNNING",
            heartbeat_at_ms=t0,
            metadata={"coverage_quality": "GOOD", "worker_role": "WATCHER_PROCESS"},
        )
        obs_svc.record_engine_heartbeat(
            session,
            symbol="XAUUSDT",
            run_id="RUN_MB01",
            state="RUNNING",
            heartbeat_at_ms=t0 + 15_000,
            metadata={"coverage_quality": "GOOD", "worker_role": "WATCHER_PROCESS"},
        )

        from services.observation_interval_service import observation_interval_service
        observation_interval_service.flush_active_intervals(session)

        rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_MB01").all()
        for r in rows:
            assert r.data_quality != "GOOD", f"Spoofed metadata produced false GOOD interval: {r.id}"

    def test_mb02_spoofed_boolean_flags_fail_closed(self, mem_db):
        """MB02: metadata={'is_feed_healthy': True, 'is_quote_fresh': True} without quote data fails closed."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        obs_svc.record_engine_heartbeat(
            session,
            symbol="XAUUSDT",
            run_id="RUN_MB02",
            state="RUNNING",
            heartbeat_at_ms=t0,
            metadata={
                "is_feed_healthy": True,
                "is_quote_fresh": True,
                "worker_role": "WATCHER_PROCESS",
            },
        )
        obs_svc.record_engine_heartbeat(
            session,
            symbol="XAUUSDT",
            run_id="RUN_MB02",
            state="RUNNING",
            heartbeat_at_ms=t0 + 15_000,
            metadata={
                "is_feed_healthy": True,
                "is_quote_fresh": True,
                "worker_role": "WATCHER_PROCESS",
            },
        )

        from services.observation_interval_service import observation_interval_service
        observation_interval_service.flush_active_intervals(session)

        rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_MB02").all()
        for r in rows:
            assert r.data_quality != "GOOD", f"Spoofed booleans produced false GOOD interval: {r.id}"

    def test_mb03_authoritative_structured_quality_credited(self, mem_db):
        """MB03: When authoritative verified quote quality is attached, GOOD is credited."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        verified_quality = ObservationEvidenceQuality(
            quality="GOOD",
            reason_code="FEED_VERIFIED_FRESH",
            feed_source="WS",
            quote_age_ms=200,
            quote_valid=True,
            transport_state="CONNECTED",
            owner_verified=True,
            evidence_timestamp_ms=t0,
        )

        obs_svc.record_engine_heartbeat(
            session,
            symbol="XAUUSDT",
            run_id="RUN_MB03",
            state="RUNNING",
            heartbeat_at_ms=t0,
            metadata={
                "evidence_quality": verified_quality,
                "worker_role": "WATCHER_PROCESS",
            },
        )
        obs_svc.record_engine_heartbeat(
            session,
            symbol="XAUUSDT",
            run_id="RUN_MB03",
            state="RUNNING",
            heartbeat_at_ms=t0 + 15_000,
            metadata={
                "evidence_quality": verified_quality,
                "worker_role": "WATCHER_PROCESS",
            },
        )

        from services.observation_interval_service import observation_interval_service
        observation_interval_service.flush_active_intervals(session)

        rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_MB03").all()
        assert len(rows) >= 1
        assert rows[0].data_quality == "GOOD"
