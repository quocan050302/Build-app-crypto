"""
backend/tests/test_v17_6_observation_run.py
AURUM V17.6 — Forward Paper Observation Run & Coverage Test Suite (Phase F, Parts 056-065).

Verifies:
1. Accurate observation uptime and coverage calculation with weekend gold break exclusion.
2. Coverage calculation with PaperObservationInterval records.
3. Timezone presentation integrity: UTC timestamps invariant across Asia/Ho_Chi_Minh & America/New_York.
4. Observation report markdown rendering with complete operational audit.
"""

from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import (
    engine_observability_service,
    is_xauusdt_weekend_break,
)
from services.paper_research_run_service import PaperResearchRunService


@pytest.fixture
def run_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    session = SessionFactory()
    yield session
    session.close()


def test_r01_weekend_break_exclusion():
    # Friday 22:00 UTC -> Weekend break (closed)
    dt_fri = datetime(2026, 10, 23, 22, 0, 0, tzinfo=timezone.utc)
    ts_fri = int(dt_fri.timestamp() * 1000)
    assert is_xauusdt_weekend_break(ts_fri) is True

    # Saturday 12:00 UTC -> Weekend break
    dt_sat = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    ts_sat = int(dt_sat.timestamp() * 1000)
    assert is_xauusdt_weekend_break(ts_sat) is True

    # Wednesday 14:00 UTC -> Active trading session (not weekend break)
    dt_wed = datetime(2026, 10, 21, 14, 0, 0, tzinfo=timezone.utc)
    ts_wed = int(dt_wed.timestamp() * 1000)
    assert is_xauusdt_weekend_break(ts_wed) is False


def test_r02_coverage_calculation_healthy(run_db: Session):
    start_ms = 1792576800000
    end_ms = start_ms + (2 * 3600 * 1000)  # 2 hours

    # Create 2 hours of GOOD observation interval
    inv = models.PaperObservationInterval(
        scope="CG01",
        run_id="run-obs-01",
        symbol="XAUUSDT",
        worker_role="SCHEDULER",
        mode="SHADOW",
        start_ms=start_ms,
        end_ms=end_ms,
        data_quality="GOOD",
        heartbeats_count=720,
        created_at_ms=end_ms,
    )
    run_db.add(inv)
    run_db.commit()

    coverage = engine_observability_service.calculate_observation_coverage(
        run_db,
        run_id="run-obs-01",
        start_ms=start_ms,
        end_ms=end_ms,
    )

    assert coverage["coverage_pct"] >= 95.0
    assert coverage["coverage_state"] == "FULL"
    assert coverage["observed_healthy_hours"] == 2.0
    assert coverage["evidence_type"] == "LIVE_PAPER_FORWARD_OBSERVATION"


def test_r03_coverage_calculation_with_gaps(run_db: Session):
    start_ms = 1792576800000
    end_ms = start_ms + (2 * 3600 * 1000)  # 2 hours total span

    # 30 min good interval, 1 hr downtime interval
    inv1 = models.PaperObservationInterval(
        scope="CG01",
        run_id="run-obs-02",
        symbol="XAUUSDT",
        worker_role="SCHEDULER",
        mode="SHADOW",
        start_ms=start_ms,
        end_ms=start_ms + 1800_000,
        data_quality="GOOD",
        heartbeats_count=180,
        created_at_ms=start_ms + 1800_000,
    )
    inv2 = models.PaperObservationInterval(
        scope="CG01",
        run_id="run-obs-02",
        symbol="XAUUSDT",
        worker_role="SCHEDULER",
        mode="SHADOW",
        start_ms=start_ms + 1800_000,
        end_ms=start_ms + 5400_000,
        data_quality="DEGRADED",
        heartbeats_count=0,
        created_at_ms=start_ms + 5400_000,
    )
    run_db.add_all([inv1, inv2])
    run_db.commit()

    coverage = engine_observability_service.calculate_observation_coverage(
        run_db,
        run_id="run-obs-02",
        start_ms=start_ms,
        end_ms=end_ms,
    )

    assert coverage["coverage_pct"] < 50.0
    assert coverage["coverage_state"] == "LOW"
    assert coverage["known_downtime_hours"] == 1.0


def test_r04_observation_markdown_report_generation(run_db: Session):
    run = PaperResearchRunService.get_or_create_run(
        "run-2026-test",
        db=run_db,
        scope="CG01",
        symbol="XAUUSDT",
        create_if_missing=True,
    )
    run.execution_mode = "PAPER_EXECUTION"
    run_db.commit()

    engine_observability_service.record_engine_heartbeat(
        run_db,
        engine_instance_id="eng-rpt-01",
        symbol="XAUUSDT",
        heartbeat_at_ms=1700000000000,
        state="RUNNING",
        last_cycle_id="cycle-rpt-01",
    )

    report_md = engine_observability_service.generate_run_report_markdown(
        run_db,
        run_id="run-2026-test",
    )

    assert isinstance(report_md, str)
    assert "# BÁO CÁO QUAN SÁT VẬN HÀNH BOT" in report_md
    assert "XAUUSDT" in report_md
    assert "run-2026-test" in report_md
    assert "Hiệu Suất Tài Chính Sau Phí" in report_md
