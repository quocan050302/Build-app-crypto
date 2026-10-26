"""
backend/tests/test_v17_6_4_observation_api.py
Tests for V17.6.4 Observation REST Endpoints (Phase J, API01-API10).
"""
import time
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from main import app, get_db
from services.engine_observability_service import engine_observability_service
from services.observation_interval_service import observation_interval_service
from services.paper_research_run_service import start_observation_run


@pytest.fixture
def test_env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    db_session = TestingSession()
    observation_interval_service.clear()

    yield client, db_session

    db_session.close()
    app.dependency_overrides.clear()
    models.Base.metadata.drop_all(bind=engine)
    observation_interval_service.clear()


def test_api01_status_with_valid_run(test_env):
    """API01: /api/v1/observation/status returns OK, engine, feed, and active run."""
    client, db = test_env
    t_open = 1792584000000  # Wednesday open reference session
    run = start_observation_run(db=db, symbol="XAUUSDT", scope="CG01")

    engine_observability_service.record_engine_heartbeat(
        db,
        symbol="XAUUSDT",
        run_id=run.run_id,
        state="RUNNING",
        heartbeat_at_ms=t_open,
    )

    from services.quote_validator import CanonicalQuote
    from unittest.mock import patch

    with patch("services.bitget_ws_service.bitget_ws_service") as mock_ws:
        mock_ws.transport_state = "CONNECTED"
        mock_ws.latest_quote = CanonicalQuote(
            symbol="XAUUSDT",
            bid=2730.0,
            ask=2730.2,
            last=2730.1,
            exchange_ts_ms=t_open - 1000,
            received_at_ms=t_open - 900,
            source="WS",
            connection_epoch=1,
            status="VALID",
        )
        resp = client.get(f"/api/v1/observation/status?symbol=XAUUSDT&now_ms={t_open}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "OK"
        assert data["symbol"] == "XAUUSDT"
        assert data["engine"]["state"] == "RUNNING"
        assert data["active_run"]["run_id"] == run.run_id


def test_api02_summary_with_valid_run(test_env):
    """API02: /api/v1/observation/summary returns aggregated snapshot with coverage and feed."""
    client, db = test_env
    t_now = int(time.time() * 1000)
    run = start_observation_run(db=db, symbol="XAUUSDT", scope="CG01")

    resp = client.get(f"/api/v1/observation/summary?symbol=XAUUSDT&run_id={run.run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "XAUUSDT"
    assert data["run_id"] == run.run_id
    assert "feed" in data
    assert "coverage" in data


def test_api03_unknown_run(test_env):
    """API03: Nonexistent run_id yields status NOT_FOUND in financial and RUN_NOT_FOUND in coverage."""
    client, db = test_env
    resp = client.get("/api/v1/observation/summary?symbol=XAUUSDT&run_id=RUN_NONEXISTENT_404")
    assert resp.status_code == 200
    data = resp.json()
    assert data["financial"]["status"] == "NOT_FOUND"
    assert data["financial"]["current_equity_usdt"] is None
    assert data["coverage"]["coverage_state"] == "RUN_NOT_FOUND"
    assert data["coverage"]["coverage_pct"] is None


def test_api04_run_a_vs_run_b_isolation(test_env):
    """API04: Querying run_A does not return run_B metrics or cycles."""
    client, db = test_env
    t_now = int(time.time() * 1000)
    run_a = start_observation_run(db=db, symbol="XAUUSDT", scope="CG01", run_id="RUN_AAA")
    run_b = start_observation_run(db=db, symbol="XAUUSDT", scope="CG01", run_id="RUN_BBB")

    engine_observability_service.record_evaluation_cycle(
        db,
        cycle_id="cycle-a-only",
        run_id="RUN_AAA",
        started_at_ms=t_now - 5000,
        completed_at_ms=t_now - 4000,
        status="COMPLETED",
        symbol="XAUUSDT",
        decision_status="ARMED",
    )
    engine_observability_service.record_evaluation_cycle(
        db,
        cycle_id="cycle-b-only",
        run_id="RUN_BBB",
        started_at_ms=t_now - 2000,
        completed_at_ms=t_now - 1000,
        status="COMPLETED",
        symbol="XAUUSDT",
        decision_status="NO_SETUP",
    )

    resp_a = client.get("/api/v1/observation/summary?symbol=XAUUSDT&run_id=RUN_AAA")
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    assert data_a["latest_cycle"]["cycle_id"] == "cycle-a-only"
    assert data_a["latest_cycle"]["decision_status"] == "ARMED"


def test_api05_null_financial_metrics_preserved(test_env):
    """API05: Null financial metrics remain null rather than defaulting to fabricated 0.0 or 1000."""
    client, db = test_env
    resp = client.get("/api/v1/observation/summary?symbol=XAUUSDT&run_id=RUN_UNRESOLVED")
    data = resp.json()
    assert data["financial"]["net_pnl_usd"] is None
    assert data["financial"]["initial_capital_usdt"] is None


def test_api06_no_cycle_returns_none(test_env):
    """API06: When no cycle has run for symbol, latest_cycle is None."""
    client, db = test_env
    # Clear in-memory diagnostic
    with engine_observability_service._lock:
        engine_observability_service._latest_cycle_diagnostic = None
    resp = client.get("/api/v1/observation/status?symbol=XAUUSDT")
    assert resp.status_code == 200
    data = resp.json()
    assert data["latest_cycle"] is None


def test_api07_market_calendar_status_exposed(test_env):
    """API07: Market feed snapshot exposes 24/7 contract schedule and underlying reference states."""
    client, db = test_env
    resp = client.get("/api/v1/observation/status?symbol=XAUUSDT")
    assert resp.status_code == 200
    data = resp.json()
    feed = data["feed"]
    assert feed["contract_schedule"] == "24_7"
    assert "contract_operational_state" in feed
    assert "underlying_reference_state" in feed
    assert "strategy_session_state" in feed


def test_api08_coverage_without_intervals(test_env):
    """API08: Coverage without intervals on 24/7 calendar reports 0.0% coverage."""
    client, db = test_env
    t_now = int(time.time() * 1000)
    run = start_observation_run(db=db, symbol="XAUUSDT", scope="CG01")
    run_rec = db.query(models.PaperResearchRunRecord).filter(models.PaperResearchRunRecord.run_id == run.run_id).first()
    run_rec.started_at_ms = t_now - 3600000
    run_rec.updated_at_ms = t_now
    db.commit()

    resp = client.get(
        f"/api/v1/observation/summary?symbol=XAUUSDT&run_id={run.run_id}&start_ms={t_now - 3600000}&end_ms={t_now}"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["coverage"]["coverage_pct"] == 0.0
    assert data["coverage"]["coverage_state"] == "LOW"


def test_api09_coverage_with_verified_intervals(test_env):
    """API09: Summary coverage calculation matches verified DB intervals."""
    client, db = test_env
    t_start = 1792790400000
    t_end = t_start + 3600 * 1000

    rec = models.PaperResearchRunRecord(
        run_id="RUN_API09",
        symbol="XAUUSDT",
        scope="CG01",
        status="COLLECTING",
        started_at_ms=t_start,
        created_at_ms=t_start,
        updated_at_ms=t_start,
    )
    inv = models.PaperObservationInterval(
        id=901,
        run_id="RUN_API09",
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t_start,
        end_ms=t_end,
        data_quality="GOOD",
        heartbeats_count=240,
        created_at_ms=t_end,
    )
    db.add_all([rec, inv])
    db.commit()

    resp = client.get(
        f"/api/v1/observation/summary?symbol=XAUUSDT&run_id=RUN_API09&start_ms={t_start}&end_ms={t_end}"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["coverage"]["coverage_pct"] == 100.0
    assert data["coverage"]["coverage_state"] == "FULL"


def test_api10_restart_readback_restores_from_db(test_env):
    """API10: When in-memory cache is wiped, /observation/status restores cycle from DB."""
    client, db = test_env
    run = start_observation_run(db=db, symbol="XAUUSDT", scope="CG01")

    engine_observability_service.record_evaluation_cycle(
        db,
        cycle_id="cycle-persisted-api10",
        run_id=run.run_id,
        started_at_ms=10000,
        completed_at_ms=10500,
        status="COMPLETED",
        symbol="XAUUSDT",
        decision_status="ARMED",
    )

    # Wipe in-memory ring buffer and diagnostic
    with engine_observability_service._lock:
        engine_observability_service._latest_cycle_diagnostic = None
        engine_observability_service._recent_cycles_buffer.clear()

    resp = client.get("/api/v1/observation/status?symbol=XAUUSDT")
    assert resp.status_code == 200
    data = resp.json()
    assert data["latest_cycle"] is not None
    assert data["latest_cycle"]["cycle_id"] == "cycle-persisted-api10"
    assert data["latest_cycle"]["decision_status"] == "ARMED"
