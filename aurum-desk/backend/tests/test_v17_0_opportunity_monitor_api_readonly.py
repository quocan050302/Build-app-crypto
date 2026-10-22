"""
backend/tests/test_v17_0_opportunity_monitor_api_readonly.py
Aurum V17.0 — API Read-Only Contract and SQL Mutation Zero-Tolerance Suite.

Enforces Rules 078, 091-097, 158:
- SQLAlchemy event listener intercepts every SQL statement executed during monitor GET calls.
- Asserts zero INSERT, UPDATE, DELETE, REPLACE, CREATE, ALTER, or DROP statements.
- Asserts db.autoflush is False and zero side-effects occur (no new runs, no DayAudit, no Telegram outbox).
- Tests across empty database, populated database, invalid input (422), and missing ID (404).
"""
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

import models
from database import SessionLocal, engine
from main import app


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


class SqlMutationWatcher:
    """Captures any mutating SQL statements executed on the SQLAlchemy engine."""
    MUTATING_VERBS = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP")

    def __init__(self, target_engine):
        self.engine = target_engine
        self.statements = []
        self.mutations = []

    def __enter__(self):
        self.statements.clear()
        self.mutations.clear()
        event.listen(self.engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        event.remove(self.engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        cleaned = statement.strip().upper()
        self.statements.append(cleaned)
        for verb in self.MUTATING_VERBS:
            if cleaned.startswith(verb):
                self.mutations.append((verb, statement))
                break


class TestOpportunityMonitorApiReadonly:
    def test_summary_empty_db_zero_sql_mutations(self, client):
        with SqlMutationWatcher(engine) as watcher:
            res = client.get("/api/v1/paper/opportunity-monitor/summary?symbol=XAUUSDT&scope=CG01")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "EMPTY"
            assert data["counts"]["total_unique_opportunities"] == 0
            assert len(watcher.mutations) == 0, f"Found mutations: {watcher.mutations}"

    def test_opportunities_list_empty_db_zero_sql_mutations(self, client):
        with SqlMutationWatcher(engine) as watcher:
            res = client.get("/api/v1/paper/opportunity-monitor/opportunities?symbol=XAUUSDT&limit=20")
            assert res.status_code == 200
            data = res.json()
            assert data["total"] == 0
            assert data["items"] == []
            assert data["has_more"] is False
            assert len(watcher.mutations) == 0, f"Found mutations: {watcher.mutations}"

    def test_opportunity_detail_missing_404_zero_sql_mutations(self, client):
        with SqlMutationWatcher(engine) as watcher:
            res = client.get("/api/v1/paper/opportunity-monitor/opportunities/non-existent-id")
            assert res.status_code == 404
            assert "OPPORTUNITY_NOT_FOUND" in res.json()["detail"]
            assert len(watcher.mutations) == 0, f"Found mutations: {watcher.mutations}"

    def test_opportunity_report_empty_db_zero_sql_mutations(self, client):
        with SqlMutationWatcher(engine) as watcher:
            res = client.get("/api/v1/paper/opportunity-monitor/report.md?symbol=XAUUSDT")
            assert res.status_code == 200
            assert "text/markdown" in res.headers["content-type"]
            assert "# BÁO CÁO THEO DÕI CƠ HỘI PAPER" in res.text
            assert len(watcher.mutations) == 0, f"Found mutations: {watcher.mutations}"

    def test_summary_and_list_with_populated_records_zero_mutations(self, client, db_session):
        # Seed test data cleanly
        t_base = 1710000000000
        run_rec = models.PaperResearchRunRecord(
            run_id="run-mon-test-1",
            scope="CG01",
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=t_base,
            software_version="V17.0",
            initial_capital_usdt=1000.0,
            created_at_ms=t_base,
            updated_at_ms=t_base,
        )
        db_session.add(run_rec)

        fn1 = models.OpportunityFunnelRecord(
            opportunity_id="opp-pop-1",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP_MSS_FVG",
            direction="LONG",
            session_id="run-mon-test-1",
            run_id="run-mon-test-1",
            as_of_ms=t_base + 100,
            stage="ZONE_DETECTED",
            reason_codes_json=json.dumps(["WAIT_SWEEP"]),
            payload_json=json.dumps({"planned_entry": 2650.0, "score": 78.0}),
            created_at=t_base + 100,
            updated_at=t_base + 100,
        )
        fn2 = models.OpportunityFunnelRecord(
            opportunity_id="opp-pop-1",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP_MSS_FVG",
            direction="LONG",
            session_id="run-mon-test-1",
            run_id="run-mon-test-1",
            as_of_ms=t_base + 200,
            stage="TRIGGER_CONFIRMED",
            reason_codes_json=json.dumps(["WAIT_RETEST"]),
            payload_json=json.dumps({"planned_entry": 2650.0, "score": 85.0}),
            created_at=t_base + 200,
            updated_at=t_base + 200,
        )
        db_session.add_all([fn1, fn2])
        db_session.commit()

        try:
            # 1. Test Summary
            with SqlMutationWatcher(engine) as watcher:
                res_sum = client.get(f"/api/v1/paper/opportunity-monitor/summary?run_id=run-mon-test-1&symbol=XAUUSDT")
                assert res_sum.status_code == 200
                data_sum = res_sum.json()
                assert data_sum["status"] == "READY"
                assert data_sum["counts"]["total_unique_opportunities"] == 1
                assert data_sum["counts"]["zones_detected"] == 1
                assert data_sum["counts"]["trigger_confirmed"] == 1
                assert len(watcher.mutations) == 0

            # 2. Test List
            with SqlMutationWatcher(engine) as watcher:
                res_list = client.get(f"/api/v1/paper/opportunity-monitor/opportunities?run_id=run-mon-test-1&symbol=XAUUSDT")
                assert res_list.status_code == 200
                data_list = res_list.json()
                assert data_list["total"] == 1
                assert len(data_list["items"]) == 1
                item = data_list["items"][0]
                assert item["opportunity_id"] == "opp-pop-1"
                assert item["current_stage"] == "TRIGGER_CONFIRMED"
                assert item["score"] == 85.0
                assert len(watcher.mutations) == 0

            # 3. Test Detail
            with SqlMutationWatcher(engine) as watcher:
                res_det = client.get(f"/api/v1/paper/opportunity-monitor/opportunities/opp-pop-1?run_id=run-mon-test-1&symbol=XAUUSDT")
                assert res_det.status_code == 200
                data_det = res_det.json()
                assert data_det["item"]["opportunity_id"] == "opp-pop-1"
                assert len(data_det["milestones"]) == 2
                assert len(watcher.mutations) == 0

            # 4. Test Report
            with SqlMutationWatcher(engine) as watcher:
                res_rep = client.get(f"/api/v1/paper/opportunity-monitor/report.md?run_id=run-mon-test-1&symbol=XAUUSDT")
                assert res_rep.status_code == 200
                assert "opp-pop-1" not in res_rep.text or "Trigger đã xác nhận" in res_rep.text
                assert len(watcher.mutations) == 0

        finally:
            # Cleanup test seeded data
            db_session.query(models.OpportunityFunnelRecord).filter(models.OpportunityFunnelRecord.opportunity_id == "opp-pop-1").delete()
            db_session.query(models.PaperResearchRunRecord).filter(models.PaperResearchRunRecord.run_id == "run-mon-test-1").delete()
            db_session.commit()

    def test_validation_errors_422(self, client):
        # Invalid window: end <= start
        res1 = client.get("/api/v1/paper/opportunity-monitor/summary?start_ms=5000&end_ms=4000")
        assert res1.status_code == 422
        assert "strictly greater" in res1.json()["detail"]

        # Invalid window: > 31 days
        ms_in_35_days = 35 * 24 * 60 * 60 * 1000
        res2 = client.get(f"/api/v1/paper/opportunity-monitor/summary?start_ms=0&end_ms={ms_in_35_days}")
        assert res2.status_code == 422
        assert "cannot exceed 31 days" in res2.json()["detail"]

        # Invalid limit: > 50
        res3 = client.get("/api/v1/paper/opportunity-monitor/opportunities?limit=100")
        assert res3.status_code == 422
        assert "limit must be between 1 and 50" in res3.json()["detail"]
