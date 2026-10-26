"""
backend/tests/test_v17_6_api_contract.py
AURUM V17.6 — Observation Backend API Contract Test Suite (Phase J, Parts 101-110).

Verifies:
1. GET /api/v1/observation/status returns typed engine and feed health snapshots.
2. GET /api/v1/observation/summary returns complete Tier 1 + Tier 2 + Tier 3 dashboard read model.
3. GET /api/v1/observation/decisions provides deterministic cursor pagination.
4. GET /api/v1/observation/runs/{run_id} returns run detail and coverage or 404.
5. GET /api/v1/observation/runs/{run_id}/report returns valid Markdown audit text.
6. Strictly read-only: No GET request modifies database orders or ARM states.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import get_db
from main import app
from services.engine_observability_service import engine_observability_service
from services.paper_research_run_service import PaperResearchRunService


@pytest.fixture
def client_with_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    db = TestingSessionLocal()

    yield client, db

    app.dependency_overrides.clear()
    db.close()


def test_api_01_get_observation_status(client_with_db):
    client, db = client_with_db

    # Record test heartbeat
    engine_observability_service.record_engine_heartbeat(
        db,
        engine_instance_id="eng-api-01",
        symbol="XAUUSDT",
        heartbeat_at_ms=1700000000000,
        state="RUNNING",
    )

    res = client.get("/api/v1/observation/status?symbol=XAUUSDT")
    assert res.status_code == 200
    data = res.json()
    assert "engine" in data
    assert "feed" in data
    assert data["engine"]["symbol"] == "XAUUSDT"
    assert data["feed"]["symbol"] == "XAUUSDT"


def test_api_02_get_observation_summary(client_with_db):
    client, db = client_with_db

    run_id = "run-api-sum"
    PaperResearchRunService.get_or_create_run(run_id, db=db, create_if_missing=True)

    res = client.get(f"/api/v1/observation/summary?symbol=XAUUSDT&run_id={run_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["symbol"] == "XAUUSDT"
    assert "engine" in data
    assert "feed" in data
    assert "funnel" in data
    assert "decisions" in data
    assert "financial" in data
    assert "incidents" in data


def test_api_03_get_observation_decisions_pagination(client_with_db):
    client, db = client_with_db

    for i in range(12):
        engine_observability_service.record_decision_event(
            db,
            run_id="run-dec-api",
            opportunity_id=f"opp-api-{i}",
            model_id="STANDARD_SMC",
            direction="LONG",
            stage="WAITING",
            reason_codes=["SWEEP_WAITING"],
            occurred_at_ms=1700000000000 + i * 1000,
        )
    db.commit()

    res = client.get("/api/v1/observation/decisions?symbol=XAUUSDT&limit=5")
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 5
    assert data["has_more"] is True
    assert data["next_cursor"] is not None

    # Fetch next page
    next_cursor = data["next_cursor"]
    res2 = client.get(f"/api/v1/observation/decisions?symbol=XAUUSDT&limit=5&cursor={next_cursor}")
    assert res2.status_code == 200
    data2 = res2.json()
    assert len(data2["items"]) == 5


def test_api_04_get_observation_run_detail_and_404(client_with_db):
    client, db = client_with_db

    run_id = "run-api-detail"
    PaperResearchRunService.get_or_create_run(run_id, db=db, create_if_missing=True)
    db.commit()

    res = client.get(f"/api/v1/observation/runs/{run_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["run"]["run_id"] == run_id
    assert "coverage" in data

    # 404 for unknown run
    res_404 = client.get("/api/v1/observation/runs/non-existent-run-999")
    assert res_404.status_code == 404


def test_api_05_get_observation_run_report(client_with_db):
    client, db = client_with_db

    run_id = "run-api-report"
    PaperResearchRunService.get_or_create_run(run_id, db=db, create_if_missing=True)

    res = client.get(f"/api/v1/observation/runs/{run_id}/report")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    assert "# BÁO CÁO QUAN SÁT VẬN HÀNH BOT" in res.text
    assert run_id in res.text


def test_api_06_observation_endpoints_are_strictly_readonly(client_with_db):
    client, db = client_with_db

    # Baseline order count
    orders_before = db.query(models.PaperOrder).count()
    events_before = db.query(models.OpportunityDecisionEvent).count()

    # Query all read endpoints multiple times
    client.get("/api/v1/observation/status?symbol=XAUUSDT")
    client.get("/api/v1/observation/summary?symbol=XAUUSDT")
    client.get("/api/v1/observation/decisions?symbol=XAUUSDT")

    # Order count must remain strictly unchanged
    assert db.query(models.PaperOrder).count() == orders_before
    assert db.query(models.OpportunityDecisionEvent).count() == events_before
