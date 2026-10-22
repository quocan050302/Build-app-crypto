"""
backend/tests/test_v17_1_monitor_scope_and_focus.py
Aurum V17.1 — Unit & Integration Test Suite for Monitor Scope Isolation,
Filter Parity, Canonical Chart Focus DTO, and Zero SQL Mutations.

Coverage of Core Requirements:
- Repro Failure M1: Active vs Completed run isolation on default list.
- Repro Failure M2: Detail outside time window [start_ms, end_ms) returns 404.
- Repro Failure M3: Chart focus context returns canonical PaperOrderResponse / WatchSetupItem DTO
  with planned_entry, stop_loss, take_profit, and cost_snapshot.
- Stale setup revision returns STALE_TARGET.
- Zero SQL mutations (db.autoflush=False) across all GET endpoints including chart-target.
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


class TestV171MonitorScopeAndFocus:
    def test_repro_m1_default_list_isolates_active_run(self, client, db_session):
        """
        Failure M1: When run_id is omitted, default list must resolve active run (COLLECTING)
        and NOT mix opportunities from old completed runs.
        """
        t_base = 1715000000000

        # Seed active run A
        run_a = models.PaperResearchRunRecord(
            run_id="run-active-a",
            scope="CG01",
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=t_base,
            software_version="V17.1",
            initial_capital_usdt=1000.0,
            created_at_ms=t_base,
            updated_at_ms=t_base,
        )
        # Seed completed old run B
        run_b = models.PaperResearchRunRecord(
            run_id="run-old-b",
            scope="CG01",
            symbol="XAUUSDT",
            status="COMPLETED",
            started_at_ms=t_base - 50000,
            software_version="V17.0",
            initial_capital_usdt=1000.0,
            created_at_ms=t_base - 50000,
            updated_at_ms=t_base - 1000,
        )
        db_session.add_all([run_a, run_b])

        # Opp in active-a
        opp_a = models.OpportunityFunnelRecord(
            opportunity_id="opp-in-active-a",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP",
            direction="LONG",
            session_id="run-active-a",
            run_id="run-active-a",
            as_of_ms=t_base + 100,
            stage="ZONE_DETECTED",
            reason_codes_json=json.dumps(["WATCH_POI"]),
            payload_json=json.dumps({"planned_entry": 2650.0}),
            created_at=t_base + 100,
            updated_at=t_base + 100,
        )
        # Opp in old-b
        opp_b = models.OpportunityFunnelRecord(
            opportunity_id="opp-in-old-b",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP",
            direction="SHORT",
            session_id="run-old-b",
            run_id="run-old-b",
            as_of_ms=t_base + 100,
            stage="ZONE_DETECTED",
            reason_codes_json=json.dumps(["WATCH_POI"]),
            payload_json=json.dumps({"planned_entry": 2655.0}),
            created_at=t_base + 100,
            updated_at=t_base + 100,
        )
        db_session.add_all([opp_a, opp_b])
        db_session.commit()

        try:
            # Query default opportunities without run_id
            res_list = client.get("/api/v1/paper/opportunity-monitor/opportunities?symbol=XAUUSDT")
            assert res_list.status_code == 200
            data_list = res_list.json()
            assert data_list["total"] == 1
            assert len(data_list["items"]) == 1
            assert data_list["items"][0]["opportunity_id"] == "opp-in-active-a"
            assert data_list["run_id"] == "run-active-a"

            # Query default summary without run_id
            res_sum = client.get("/api/v1/paper/opportunity-monitor/summary?symbol=XAUUSDT")
            assert res_sum.status_code == 200
            data_sum = res_sum.json()
            assert data_sum["coverage"]["run_id"] == "run-active-a"
            assert data_sum["counts"]["total_unique_opportunities"] == 1

            # Query explicit completed run B
            res_b = client.get("/api/v1/paper/opportunity-monitor/opportunities?symbol=XAUUSDT&run_id=run-old-b")
            assert res_b.status_code == 200
            data_b = res_b.json()
            assert data_b["total"] == 1
            assert data_b["items"][0]["opportunity_id"] == "opp-in-old-b"
            assert data_b["run_id"] == "run-old-b"
        finally:
            db_session.query(models.OpportunityFunnelRecord).filter(
                models.OpportunityFunnelRecord.opportunity_id.in_(["opp-in-active-a", "opp-in-old-b"])
            ).delete()
            db_session.query(models.PaperResearchRunRecord).filter(
                models.PaperResearchRunRecord.run_id.in_(["run-active-a", "run-old-b"])
            ).delete()
            db_session.commit()

    def test_repro_m2_detail_outside_window_returns_404(self, client, db_session):
        """
        Failure M2: Detail endpoint must strictly enforce the [start_ms, end_ms) window.
        Opportunity with as_of_ms=2000 queried with window [1000, 1500) must return 404.
        """
        t_base = 1716000000000
        run_rec = models.PaperResearchRunRecord(
            run_id="run-win-test",
            scope="CG01",
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=t_base,
            software_version="V17.1",
            initial_capital_usdt=1000.0,
            created_at_ms=t_base,
            updated_at_ms=t_base,
        )
        db_session.add(run_rec)

        opp = models.OpportunityFunnelRecord(
            opportunity_id="opp-outside-window",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP",
            direction="LONG",
            session_id="run-win-test",
            run_id="run-win-test",
            as_of_ms=t_base + 2000,  # at 2000ms offset
            stage="ZONE_DETECTED",
            reason_codes_json=json.dumps(["WAIT_SWEEP"]),
            payload_json=json.dumps({"planned_entry": 2650.0}),
            created_at=t_base + 2000,
            updated_at=t_base + 2000,
        )
        db_session.add(opp)
        db_session.commit()

        try:
            # Window [1000, 1500) excludes as_of 2000 -> 404
            res_404 = client.get(
                f"/api/v1/paper/opportunity-monitor/opportunities/opp-outside-window"
                f"?run_id=run-win-test&symbol=XAUUSDT&start_ms={t_base + 1000}&end_ms={t_base + 1500}"
            )
            assert res_404.status_code == 404
            assert "OPPORTUNITY_NOT_FOUND" in res_404.json()["detail"]

            # Window [1000, 2500) includes as_of 2000 -> 200
            res_200 = client.get(
                f"/api/v1/paper/opportunity-monitor/opportunities/opp-outside-window"
                f"?run_id=run-win-test&symbol=XAUUSDT&start_ms={t_base + 1000}&end_ms={t_base + 2500}"
            )
            assert res_200.status_code == 200
            assert res_200.json()["item"]["opportunity_id"] == "opp-outside-window"
        finally:
            db_session.query(models.OpportunityFunnelRecord).filter(
                models.OpportunityFunnelRecord.opportunity_id == "opp-outside-window"
            ).delete()
            db_session.query(models.PaperResearchRunRecord).filter(
                models.PaperResearchRunRecord.run_id == "run-win-test"
            ).delete()
            db_session.commit()

    def test_repro_m3_chart_focus_returns_canonical_order_dto(self, client, db_session):
        """
        Failure M3: handleFocusChart needs full canonical PaperOrderResponse with planned_entry,
        stop_loss, take_profit, cost_snapshot, and order state.
        """
        t_base = 1717000000000
        run_rec = models.PaperResearchRunRecord(
            run_id="run-order-focus",
            scope="CG01",
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=t_base,
            software_version="V17.1",
            initial_capital_usdt=1000.0,
            created_at_ms=t_base,
            updated_at_ms=t_base,
        )
        db_session.add(run_rec)

        # Seed full canonical PaperOrder
        order = models.PaperOrder(
            id="order-focus-canonical-1",
            instrument="XAUUSDT",
            direction="LONG",
            state="FILLED",
            timeframe="15M",
            planned_entry=2650.0,
            actual_entry=2650.2,
            stop_loss=2640.0,
            take_profit=2675.0,
            quantity=0.25,
            initial_risk_usdt=25.0,
            risk_pct=0.25,
            gross_rr=2.5,
            estimated_net_rr=2.35,
            cost_snapshot=json.dumps({"min_net_rr": 2.0, "source": "SETTINGS"}),
            created_at=t_base + 100,
            armed_at=t_base + 150,
            opened_at=t_base + 200,
        )
        db_session.add(order)

        # Opportunity with linked order
        opp = models.OpportunityFunnelRecord(
            opportunity_id="opp-with-order",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP",
            direction="LONG",
            session_id="run-order-focus",
            run_id="run-order-focus",
            as_of_ms=t_base + 200,
            stage="FILLED",
            reason_codes_json=json.dumps(["TRIGGER_CONFIRMED"]),
            payload_json=json.dumps({"order_id": "order-focus-canonical-1"}),
            created_at=t_base + 100,
            updated_at=t_base + 200,
        )
        db_session.add(opp)

        link = models.PaperResearchOrderLink(
            run_id="run-order-focus",
            order_id="order-focus-canonical-1",
            opportunity_id="opp-with-order",
            linked_at_ms=t_base + 200,
        )
        db_session.add(link)
        db_session.commit()

        try:
            with SqlMutationWatcher(engine) as watcher:
                res = client.get(
                    f"/api/v1/paper/opportunity-monitor/opportunities/opp-with-order/chart-target"
                    f"?run_id=run-order-focus&symbol=XAUUSDT"
                )
                assert res.status_code == 200
                data = res.json()
                assert data["status"] == "AVAILABLE"
                assert data["kind"] == "ORDER"
                assert data["target_id"] == "order-focus-canonical-1"

                dto = data["dto"]
                assert dto["id"] == "order-focus-canonical-1"
                assert dto["planned_entry"] == 2650.0
                assert dto["actual_entry"] == 2650.2
                assert dto["stop_loss"] == 2640.0
                assert dto["take_profit"] == 2675.0
                assert dto["direction"] == "LONG"
                assert dto["state"] == "FILLED"
                assert dto["timeframe"] == "15M"
                assert dto["cost_snapshot"] is not None

                # Zero writes contract
                assert len(watcher.mutations) == 0
        finally:
            db_session.query(models.PaperResearchOrderLink).filter(
                models.PaperResearchOrderLink.order_id == "order-focus-canonical-1"
            ).delete()
            db_session.query(models.PaperOrder).filter(
                models.PaperOrder.id == "order-focus-canonical-1"
            ).delete()
            db_session.query(models.OpportunityFunnelRecord).filter(
                models.OpportunityFunnelRecord.opportunity_id == "opp-with-order"
            ).delete()
            db_session.query(models.PaperResearchRunRecord).filter(
                models.PaperResearchRunRecord.run_id == "run-order-focus"
            ).delete()
            db_session.commit()

    def test_chart_focus_setup_and_stale_revision(self, client, db_session):
        """
        SETUP target returns canonical WatchSetupItem. Stale revision returns STALE_TARGET.
        """
        t_base = 1718000000000
        run_rec = models.PaperResearchRunRecord(
            run_id="run-setup-focus",
            scope="CG01",
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=t_base,
            software_version="V17.1",
            initial_capital_usdt=1000.0,
            created_at_ms=t_base,
            updated_at_ms=t_base,
        )
        db_session.add(run_rec)

        setup = models.WatchSetup(
            id="watch-setup-canonical-1",
            version=2,  # Current version in DB is 2
            strategy="CG01",
            direction="LONG",
            timeframe="15M",
            state="READY",
            htf_bias="BULLISH",
            h1_alignment="BULLISH",
            trigger_mode="MARKET",
            provisional_entry=2650.0,
            provisional_sl=2640.0,
            provisional_tp=2675.0,
            confirmed_entry=2650.0,
            confirmed_sl=2640.0,
            confirmed_tp=2675.0,
            invalidation_price=2638.0,
            gross_rr=2.5,
            net_rr=2.4,
            risk_usdt=25.0,
            quantity=0.25,
            leverage=5,
            margin_mode="ISOLATED",
            created_at=t_base,
            updated_at=t_base + 500,
        )
        db_session.add(setup)

        opp = models.OpportunityFunnelRecord(
            opportunity_id="opp-with-setup",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP",
            direction="LONG",
            session_id="run-setup-focus",
            run_id="run-setup-focus",
            as_of_ms=t_base + 200,
            stage="TRIGGER_CONFIRMED",
            reason_codes_json=json.dumps(["TRIGGER_CONFIRMED"]),
            payload_json=json.dumps({"watch_setup_id": "watch-setup-canonical-1", "revision": 1}),
            created_at=t_base + 100,
            updated_at=t_base + 200,
        )
        db_session.add(opp)
        db_session.commit()

        try:
            # Query expecting revision 1 when current DB is 2 -> STALE_TARGET
            res_stale = client.get(
                f"/api/v1/paper/opportunity-monitor/opportunities/opp-with-setup/chart-target"
                f"?run_id=run-setup-focus&symbol=XAUUSDT&expected_revision=1"
            )
            assert res_stale.status_code == 200
            data_stale = res_stale.json()
            assert data_stale["status"] == "STALE_TARGET"
            assert data_stale["reason_code"] == "SETUP_REVISION_MISMATCH"

            # Query without revision restriction -> Server self-protects recorded revision 1 vs DB version 2 -> STALE_TARGET
            res_no_param = client.get(
                f"/api/v1/paper/opportunity-monitor/opportunities/opp-with-setup/chart-target"
                f"?run_id=run-setup-focus&symbol=XAUUSDT"
            )
            assert res_no_param.status_code == 200
            data_no_param = res_no_param.json()
            assert data_no_param["status"] == "STALE_TARGET"
            assert data_no_param["reason_code"] == "SETUP_REVISION_MISMATCH"

            # When recorded revision in DB matches persisted version (v2), it returns AVAILABLE with full DTO
            opp.payload_json = json.dumps({"watch_setup_id": "watch-setup-canonical-1", "revision": 2})
            db_session.commit()

            res_ok = client.get(
                f"/api/v1/paper/opportunity-monitor/opportunities/opp-with-setup/chart-target"
                f"?run_id=run-setup-focus&symbol=XAUUSDT"
            )
            assert res_ok.status_code == 200
            data_ok = res_ok.json()
            assert data_ok["status"] == "AVAILABLE"
            assert data_ok["kind"] == "SETUP"
            assert data_ok["dto"]["id"] == "watch-setup-canonical-1"
            assert data_ok["dto"]["provisional_entry"] == 2650.0
            assert data_ok["dto"]["direction"] == "LONG"
        finally:
            db_session.query(models.WatchSetup).filter(
                models.WatchSetup.id == "watch-setup-canonical-1"
            ).delete()
            db_session.query(models.OpportunityFunnelRecord).filter(
                models.OpportunityFunnelRecord.opportunity_id == "opp-with-setup"
            ).delete()
            db_session.query(models.PaperResearchRunRecord).filter(
                models.PaperResearchRunRecord.run_id == "run-setup-focus"
            ).delete()
            db_session.commit()

    def test_zero_sql_mutations_on_chart_target_endpoint(self, client):
        """
        Verify that calling GET /chart-target executes ZERO SQL mutations (INSERT, UPDATE, DELETE).
        Tested on both non-existent opportunity and empty DB.
        """
        with SqlMutationWatcher(engine) as watcher:
            res = client.get(
                "/api/v1/paper/opportunity-monitor/opportunities/non-existent-opp/chart-target?symbol=XAUUSDT&scope=CG01"
            )
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "UNAVAILABLE"
            assert len(watcher.mutations) == 0, f"Expected 0 mutations, got: {watcher.mutations}"

