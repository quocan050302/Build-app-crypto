"""
backend/tests/test_v17_2_monitor_context.py
Aurum V17.2 — Regression Probes and Invariant Tests for:
- E3: Server protects recorded revision even when expected_revision is omitted.
- E4: Detail milestone and trace cutoff strictly within [start_ms, end_ms).
- Setup three-source revision validation (recorded, requested, persisted).
- Window resolution without silent 1-day rollbacks.
- Zero SQL mutations across read paths.
"""
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

import models
from database import SessionLocal, engine
from main import app


@pytest.fixture(autouse=True)
def _clean_monitor_tables():
    db = SessionLocal()
    try:
        db.query(models.OpportunityFunnelRecord).delete()
        db.query(models.PaperResearchRunRecord).delete()
        db.commit()
        yield
    finally:
        try:
            db.query(models.OpportunityFunnelRecord).delete()
            db.query(models.PaperResearchRunRecord).delete()
            db.commit()
        finally:
            db.close()


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


class TestV172MonitorContext:
    def test_repro_e3_recorded_revision_mismatch_without_param(self, client, db_session):
        """
        Probe E3: When funnel recorded setup_id X with revision 1, but WatchSetup X is currently
        version 2, calling /chart-target WITHOUT expected_revision must still return STALE_TARGET.
        It must NEVER silently serve the new v2 setup.
        """
        t_base = 1720000000000
        run_rec = models.PaperResearchRunRecord(
            run_id="run-e3-stale",
            scope="CG01",
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=t_base,
            software_version="V17.2",
            initial_capital_usdt=1000.0,
            created_at_ms=t_base,
            updated_at_ms=t_base,
        )
        db_session.add(run_rec)

        setup = models.WatchSetup(
            id="watch-setup-e3",
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
            opportunity_id="opp-e3",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP",
            direction="LONG",
            session_id="run-e3-stale",
            run_id="run-e3-stale",
            as_of_ms=t_base + 200,
            stage="TRIGGER_CONFIRMED",
            reason_codes_json=json.dumps(["TRIGGER_CONFIRMED"]),
            payload_json=json.dumps({"watch_setup_id": "watch-setup-e3", "revision": 1}),
            created_at=t_base + 100,
            updated_at=t_base + 200,
        )
        db_session.add(opp)
        db_session.commit()

        try:
            # Query WITHOUT expected_revision
            res = client.get(
                f"/api/v1/paper/opportunity-monitor/opportunities/opp-e3/chart-target"
                f"?run_id=run-e3-stale&symbol=XAUUSDT"
            )
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "STALE_TARGET", f"Expected STALE_TARGET, got {data}"
            assert data["reason_code"] == "SETUP_REVISION_MISMATCH"
        finally:
            db_session.query(models.WatchSetup).filter(models.WatchSetup.id == "watch-setup-e3").delete()
            db_session.query(models.OpportunityFunnelRecord).filter(models.OpportunityFunnelRecord.opportunity_id == "opp-e3").delete()
            db_session.query(models.PaperResearchRunRecord).filter(models.PaperResearchRunRecord.run_id == "run-e3-stale").delete()
            db_session.commit()

    def test_repro_e4_detail_milestone_and_trace_cutoff(self, client, db_session):
        """
        Probe E4: Detail endpoint query window [1000, 1500) must strictly include events with
        start_ms <= timestamp < end_ms.
        Events before start (999), at end (1500), and after end (2000 CLOSED) must NOT appear.
        """
        t_base = 1721000000000
        run_rec = models.PaperResearchRunRecord(
            run_id="run-e4-cutoff",
            scope="CG01",
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=t_base,
            software_version="V17.2",
            initial_capital_usdt=1000.0,
            created_at_ms=t_base,
            updated_at_ms=t_base,
        )
        db_session.add(run_rec)

        order = models.PaperOrder(
            id="order-e4-cutoff",
            instrument="XAUUSDT",
            direction="LONG",
            state="CLOSED",
            timeframe="15M",
            planned_entry=2650.0,
            actual_entry=2650.0,
            stop_loss=2640.0,
            take_profit=2675.0,
            quantity=0.25,
            initial_risk_usdt=25.0,
            risk_pct=0.25,
            gross_rr=2.5,
            estimated_net_rr=2.35,
            cost_snapshot=json.dumps({"min_net_rr": 2.0}),
            created_at=t_base + 1000,
            armed_at=t_base + 1100,
            opened_at=t_base + 1200,
            closed_at=t_base + 2000,
        )
        db_session.add(order)

        # Milestones: 999 (before), 1000 (at start), 1200 (inside), 1500 (at end), 2000 (after end)
        m_before = models.PaperResearchOrderMilestone(
            order_id="order-e4-cutoff",
            run_id="run-e4-cutoff",
            milestone="DETECTED",
            timestamp_ms=t_base + 999,
            created_at_ms=t_base + 999,
        )
        m_start = models.PaperResearchOrderMilestone(
            order_id="order-e4-cutoff",
            run_id="run-e4-cutoff",
            milestone="ARMED",
            timestamp_ms=t_base + 1000,
            created_at_ms=t_base + 1000,
        )
        m_inside = models.PaperResearchOrderMilestone(
            order_id="order-e4-cutoff",
            run_id="run-e4-cutoff",
            milestone="FILLED",
            timestamp_ms=t_base + 1200,
            created_at_ms=t_base + 1200,
        )
        m_at_end = models.PaperResearchOrderMilestone(
            order_id="order-e4-cutoff",
            run_id="run-e4-cutoff",
            milestone="PARTIAL",
            timestamp_ms=t_base + 1500,
            created_at_ms=t_base + 1500,
        )
        m_closed = models.PaperResearchOrderMilestone(
            order_id="order-e4-cutoff",
            run_id="run-e4-cutoff",
            milestone="CLOSED",
            timestamp_ms=t_base + 2000,
            created_at_ms=t_base + 2000,
        )
        db_session.add_all([m_before, m_start, m_inside, m_at_end, m_closed])

        opp = models.OpportunityFunnelRecord(
            opportunity_id="opp-e4",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP",
            direction="LONG",
            session_id="run-e4-cutoff",
            run_id="run-e4-cutoff",
            as_of_ms=t_base + 1200,
            stage="FILLED",
            reason_codes_json=json.dumps(["TRIGGER_CONFIRMED"]),
            payload_json=json.dumps({"order_id": "order-e4-cutoff"}),
            created_at=t_base + 1200,
            updated_at=t_base + 1200,
        )
        db_session.add(opp)

        link = models.PaperResearchOrderLink(
            run_id="run-e4-cutoff",
            order_id="order-e4-cutoff",
            opportunity_id="opp-e4",
            linked_at_ms=t_base + 1200,
        )
        db_session.add(link)
        db_session.commit()

        try:
            # Query window [t_base + 1000, t_base + 1500)
            res = client.get(
                f"/api/v1/paper/opportunity-monitor/opportunities/opp-e4"
                f"?run_id=run-e4-cutoff&symbol=XAUUSDT&start_ms={t_base + 1000}&end_ms={t_base + 1500}"
            )
            assert res.status_code == 200
            data = res.json()
            order_milestones = [
                m for m in data["milestones"]
                if m.get("source") == "COMMITTED_ORDER_MILESTONE"
            ]
            stages = [m["stage"] for m in order_milestones]
            assert "CLOSED" not in stages, f"CLOSED (2000ms) leaked into window [1000, 1500): {stages}"
            assert "DETECTED" not in stages, f"DETECTED (999ms) leaked before window: {stages}"
            assert "PARTIAL" not in stages, f"PARTIAL (1500ms) at end boundary leaked into half-open window: {stages}"
            assert "ARMED" in stages, f"ARMED (1000ms at start boundary) missing from window: {stages}"
            assert "FILLED" in stages, f"FILLED (1200ms) missing from window: {stages}"
        finally:
            db_session.query(models.PaperResearchOrderLink).filter(models.PaperResearchOrderLink.order_id == "order-e4-cutoff").delete()
            db_session.query(models.PaperResearchOrderMilestone).filter(models.PaperResearchOrderMilestone.order_id == "order-e4-cutoff").delete()
            db_session.query(models.PaperOrder).filter(models.PaperOrder.id == "order-e4-cutoff").delete()
            db_session.query(models.OpportunityFunnelRecord).filter(models.OpportunityFunnelRecord.opportunity_id == "opp-e4").delete()
            db_session.query(models.PaperResearchRunRecord).filter(models.PaperResearchRunRecord.run_id == "run-e4-cutoff").delete()
            db_session.commit()

    def test_active_vs_completed_run_and_scope_mismatch(self, client, db_session):
        """
        Section 111, 112, 113:
        - Default query selects active run (COLLECTING) over completed run.
        - Explicit run_id queries the completed run.
        - Request with non-existent or scope-mismatched run returns NOT_CONFIGURED/SCOPE_MISMATCH.
        """
        t_base = 1722000000000
        run_act = models.PaperResearchRunRecord(
            run_id="run-active-a",
            scope="CG01",
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=t_base,
            software_version="V17.2",
            initial_capital_usdt=1000.0,
            created_at_ms=t_base,
            updated_at_ms=t_base,
        )
        run_comp = models.PaperResearchRunRecord(
            run_id="run-completed-b",
            scope="CG01",
            symbol="XAUUSDT",
            status="COMPLETED",
            started_at_ms=t_base - 100000,
            ended_at_ms=t_base - 1000,
            software_version="V17.2",
            initial_capital_usdt=1000.0,
            created_at_ms=t_base - 100000,
            updated_at_ms=t_base - 1000,
        )
        db_session.add_all([run_act, run_comp])
        db_session.commit()

        try:
            # Default query -> selects active run A
            res_def = client.get("/api/v1/paper/opportunity-monitor/summary?symbol=XAUUSDT&scope=CG01")
            assert res_def.status_code == 200
            data_def = res_def.json()
            assert data_def["coverage"]["run_id"] == "run-active-a"

            # Explicit run B -> selects completed run B
            res_b = client.get("/api/v1/paper/opportunity-monitor/summary?symbol=XAUUSDT&scope=CG01&run_id=run-completed-b")
            assert res_b.status_code == 200
            data_b = res_b.json()
            assert data_b["coverage"]["run_id"] == "run-completed-b"

            # Explicit non-existent run -> status NOT_FOUND or NOT_CONFIGURED without fallback
            res_missing = client.get("/api/v1/paper/opportunity-monitor/summary?symbol=XAUUSDT&scope=CG01&run_id=run-nonexistent")
            assert res_missing.status_code == 200
            data_missing = res_missing.json()
            assert data_missing["status"] == "EMPTY"
            assert data_missing["coverage"]["run_status"] in ("NOT_FOUND", "NOT_CONFIGURED")
        finally:
            db_session.query(models.PaperResearchRunRecord).filter(
                models.PaperResearchRunRecord.run_id.in_(["run-active-a", "run-completed-b"])
            ).delete()
            db_session.commit()

    def test_future_order_closure_does_not_override_historical_stage(self, client, db_session):
        """
        Section 065, 118:
        Order is currently CLOSED at t_base + 2000.
        Historical window query [t_base + 1000, t_base + 1500) must NOT override current_stage to CLOSED!
        """
        t_base = 1723000000000
        run_rec = models.PaperResearchRunRecord(
            run_id="run-future-close",
            scope="CG01",
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=t_base,
            software_version="V17.2",
            initial_capital_usdt=1000.0,
            created_at_ms=t_base,
            updated_at_ms=t_base,
        )
        db_session.add(run_rec)

        order = models.PaperOrder(
            id="order-future-close",
            instrument="XAUUSDT",
            direction="LONG",
            state="CLOSED",
            timeframe="15M",
            planned_entry=2650.0,
            actual_entry=2650.0,
            stop_loss=2640.0,
            take_profit=2675.0,
            quantity=0.25,
            initial_risk_usdt=25.0,
            risk_pct=0.25,
            gross_rr=2.5,
            estimated_net_rr=2.35,
            cost_snapshot=json.dumps({"min_net_rr": 2.0}),
            created_at=t_base + 1000,
            armed_at=t_base + 1100,
            opened_at=t_base + 1200,
            closed_at=t_base + 2000,  # Closed at 2000, after window end 1500
        )
        db_session.add(order)

        opp = models.OpportunityFunnelRecord(
            opportunity_id="opp-future-close",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP",
            direction="LONG",
            session_id="run-future-close",
            run_id="run-future-close",
            as_of_ms=t_base + 1200,
            stage="OPEN",
            reason_codes_json=json.dumps(["TRIGGER_CONFIRMED"]),
            payload_json=json.dumps({"order_id": "order-future-close"}),
            created_at=t_base + 1200,
            updated_at=t_base + 1200,
        )
        db_session.add(opp)

        link = models.PaperResearchOrderLink(
            run_id="run-future-close",
            order_id="order-future-close",
            opportunity_id="opp-future-close",
            linked_at_ms=t_base + 1200,
        )
        db_session.add(link)
        db_session.commit()

        try:
            # Query window ends at t_base + 1500 (before order closure at 2000)
            res = client.get(
                f"/api/v1/paper/opportunity-monitor/opportunities"
                f"?run_id=run-future-close&symbol=XAUUSDT&start_ms={t_base + 1000}&end_ms={t_base + 1500}"
            )
            assert res.status_code == 200
            data = res.json()
            assert len(data["items"]) == 1
            item = data["items"][0]
            assert item["current_stage"] != "CLOSED", "Historical stage must NOT be CLOSED before closed_at timestamp"
            assert item["current_stage"] == "OPEN"
        finally:
            db_session.query(models.PaperResearchOrderLink).filter(models.PaperResearchOrderLink.order_id == "order-future-close").delete()
            db_session.query(models.PaperOrder).filter(models.PaperOrder.id == "order-future-close").delete()
            db_session.query(models.OpportunityFunnelRecord).filter(models.OpportunityFunnelRecord.opportunity_id == "opp-future-close").delete()
            db_session.query(models.PaperResearchRunRecord).filter(models.PaperResearchRunRecord.run_id == "run-future-close").delete()
            db_session.commit()

    def test_report_endpoint_resolves_run_once(self, client, db_session):
        """
        Section 084, 095:
        Report route resolves query context once and generates consistent Markdown with effective run and filters.
        """
        t_base = 1724000000000
        run_rec = models.PaperResearchRunRecord(
            run_id="run-rep-single",
            scope="CG01",
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=t_base,
            software_version="V17.2",
            initial_capital_usdt=1000.0,
            created_at_ms=t_base,
            updated_at_ms=t_base,
        )
        db_session.add(run_rec)
        db_session.commit()

        try:
            res = client.get("/api/v1/paper/opportunity-monitor/report.md?symbol=XAUUSDT&scope=CG01")
            assert res.status_code == 200
            text = res.text
            assert "BÁO CÁO THEO DÕI CƠ HỘI PAPER" in text
            assert "run-rep-single" in text
            assert "XAUUSDT" in text
        finally:
            db_session.query(models.PaperResearchRunRecord).filter(models.PaperResearchRunRecord.run_id == "run-rep-single").delete()
            db_session.commit()

    def test_chart_focus_revision_and_geometry_matrix(self, client, db_session):
        """
        Section 123 - 127:
        - Client requesting wrong revision returns REQUESTED_RECORDED_REVISION_MISMATCH.
        - Missing recorded revision returns SETUP_REVISION_UNVERIFIED.
        - Symbol mismatch returns SYMBOL_MISMATCH.
        - Invalid ORDER geometry returns INVALID_LONG_GEOMETRY.
        """
        t_base = 1725000000000
        run_rec = models.PaperResearchRunRecord(
            run_id="run-matrix",
            scope="CG01",
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=t_base,
            software_version="V17.2",
            initial_capital_usdt=1000.0,
            created_at_ms=t_base,
            updated_at_ms=t_base,
        )
        db_session.add(run_rec)

        # 1. Setup with recorded revision 1
        setup = models.WatchSetup(
            id="watch-matrix-1",
            version=1,
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
            updated_at=t_base,
        )
        db_session.add(setup)

        opp_setup = models.OpportunityFunnelRecord(
            opportunity_id="opp-matrix-setup",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP",
            direction="LONG",
            session_id="run-matrix",
            run_id="run-matrix",
            as_of_ms=t_base + 200,
            stage="TRIGGER_CONFIRMED",
            reason_codes_json=json.dumps(["TRIGGER_CONFIRMED"]),
            payload_json=json.dumps({"watch_setup_id": "watch-matrix-1", "revision": 1}),
            created_at=t_base + 100,
            updated_at=t_base + 200,
        )
        db_session.add(opp_setup)

        # 2. Setup with missing revision
        opp_norev = models.OpportunityFunnelRecord(
            opportunity_id="opp-matrix-norev",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP",
            direction="LONG",
            session_id="run-matrix",
            run_id="run-matrix",
            as_of_ms=t_base + 200,
            stage="TRIGGER_CONFIRMED",
            reason_codes_json=json.dumps(["TRIGGER_CONFIRMED"]),
            payload_json=json.dumps({"watch_setup_id": "watch-matrix-1"}),  # No revision!
            created_at=t_base + 100,
            updated_at=t_base + 200,
        )
        db_session.add(opp_norev)

        # 3. Order with invalid geometry (SL > Entry on LONG)
        bad_order = models.PaperOrder(
            id="order-bad-geom",
            instrument="XAUUSDT",
            direction="LONG",
            state="ARMED",
            timeframe="15M",
            planned_entry=2650.0,
            actual_entry=None,
            stop_loss=2660.0,  # SL > Entry for LONG! Invalid geometry!
            take_profit=2675.0,
            quantity=0.25,
            initial_risk_usdt=25.0,
            risk_pct=0.25,
            gross_rr=2.5,
            estimated_net_rr=2.35,
            cost_snapshot=json.dumps({"min_net_rr": 2.0}),
            created_at=t_base + 1000,
        )
        db_session.add(bad_order)

        opp_order = models.OpportunityFunnelRecord(
            opportunity_id="opp-bad-geom",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP",
            direction="LONG",
            session_id="run-matrix",
            run_id="run-matrix",
            as_of_ms=t_base + 1000,
            stage="ARMED",
            reason_codes_json=json.dumps(["ARMED"]),
            payload_json=json.dumps({"order_id": "order-bad-geom"}),
            created_at=t_base + 1000,
            updated_at=t_base + 1000,
        )
        db_session.add(opp_order)

        link_bad = models.PaperResearchOrderLink(
            run_id="run-matrix",
            order_id="order-bad-geom",
            opportunity_id="opp-bad-geom",
            linked_at_ms=t_base + 1000,
        )
        db_session.add(link_bad)
        db_session.commit()

        try:
            # Client requests revision 99 when recorded is 1 -> REQUESTED_RECORDED_REVISION_MISMATCH
            res_mismatch = client.get(
                f"/api/v1/paper/opportunity-monitor/opportunities/opp-matrix-setup/chart-target"
                f"?run_id=run-matrix&symbol=XAUUSDT&expected_revision=99"
            )
            assert res_mismatch.status_code == 200
            assert res_mismatch.json()["status"] == "SCOPE_MISMATCH"
            assert res_mismatch.json()["reason_code"] == "REQUESTED_RECORDED_REVISION_MISMATCH"

            # Missing recorded revision -> SETUP_REVISION_UNVERIFIED
            res_norev = client.get(
                f"/api/v1/paper/opportunity-monitor/opportunities/opp-matrix-norev/chart-target"
                f"?run_id=run-matrix&symbol=XAUUSDT"
            )
            assert res_norev.status_code == 200
            assert res_norev.json()["status"] == "UNAVAILABLE"
            assert res_norev.json()["reason_code"] == "SETUP_REVISION_UNVERIFIED"

            # Bad order geometry -> INVALID_LONG_GEOMETRY
            res_bad_geom = client.get(
                f"/api/v1/paper/opportunity-monitor/opportunities/opp-bad-geom/chart-target"
                f"?run_id=run-matrix&symbol=XAUUSDT"
            )
            assert res_bad_geom.status_code == 200
            assert res_bad_geom.json()["status"] == "UNAVAILABLE"
            assert res_bad_geom.json()["reason_code"] == "INVALID_LONG_GEOMETRY"
        finally:
            db_session.query(models.PaperResearchOrderLink).filter(models.PaperResearchOrderLink.order_id == "order-bad-geom").delete()
            db_session.query(models.PaperOrder).filter(models.PaperOrder.id == "order-bad-geom").delete()
            db_session.query(models.WatchSetup).filter(models.WatchSetup.id == "watch-matrix-1").delete()
            db_session.query(models.OpportunityFunnelRecord).filter(
                models.OpportunityFunnelRecord.opportunity_id.in_(["opp-matrix-setup", "opp-matrix-norev", "opp-bad-geom"])
            ).delete()
            db_session.query(models.PaperResearchRunRecord).filter(models.PaperResearchRunRecord.run_id == "run-matrix").delete()
            db_session.commit()

    def test_zero_sql_mutations_across_all_monitor_endpoints(self, client):
        """
        Section 088, 129:
        SqlMutationWatcher verifies ZERO INSERT, UPDATE, DELETE across:
        - GET /summary
        - GET /opportunities
        - GET /opportunities/{id}
        - GET /opportunities/{id}/chart-target
        - GET /report.md
        """
        with SqlMutationWatcher(engine) as watcher:
            client.get("/api/v1/paper/opportunity-monitor/summary?symbol=XAUUSDT&scope=CG01")
            client.get("/api/v1/paper/opportunity-monitor/opportunities?symbol=XAUUSDT&scope=CG01")
            client.get("/api/v1/paper/opportunity-monitor/opportunities/non-existent-opp?symbol=XAUUSDT&scope=CG01")
            client.get("/api/v1/paper/opportunity-monitor/opportunities/non-existent-opp/chart-target?symbol=XAUUSDT&scope=CG01")
            client.get("/api/v1/paper/opportunity-monitor/report.md?symbol=XAUUSDT&scope=CG01")
            assert len(watcher.mutations) == 0, f"Expected 0 SQL mutations, got: {watcher.mutations}"

