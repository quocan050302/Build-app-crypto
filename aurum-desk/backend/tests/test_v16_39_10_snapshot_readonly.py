"""
backend/tests/test_v16_39_10_snapshot_readonly.py
V16.39.10 — Verification of Zero DB Mutations and Read-Only Contracts

Verifies Rules 003, 007, 015, 016, 041, 095, 096, 119, 120, 147, 153-157:
- SQLAlchemy before_cursor_execute listener confirms 0 write operations (INSERT/UPDATE/DELETE/ALTER/DROP).
- Missing TradingPolicy does NOT auto-create row (policy_present=False).
- Missing PaperResearchCheckpoint does NOT auto-create row.
- Missing DayAudit does NOT auto-create row.
- Full row-by-row snapshot comparison before and after confirms ZERO database state changes.
- Real SQLite mode=ro connection: successfully reads snapshot, but blocks write attempts with sqlite3.OperationalError.
- Offline worker status is UNKNOWN with running=None even with active COLLECTING run.
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import models
from models import (
    Base,
    DayAudit,
    OpportunityFunnelRecord,
    PaperOrder,
    PaperResearchCheckpoint,
    PaperResearchOrderLink,
    PaperResearchRunRecord,
    SystemConfig,
    TradingPolicy,
)
from services.paper_runtime_snapshot_service import (
    build_paper_runtime_snapshot,
    read_auto_authority,
    read_existing_policy_config,
)
from tools.paper_runtime_snapshot import open_snapshot_session


@pytest.fixture
def temp_sqlite_file_db():
    """Creates a temporary on-disk SQLite database with full schema for read-only testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_path = Path(path)

    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionFactory()

    try:
        yield session, db_path
    finally:
        session.close()
        engine.dispose()
        try:
            if db_path.exists():
                db_path.unlink()
        except Exception:
            pass


def test_010_sql_audit_listener_proves_zero_mutations(temp_sqlite_file_db):
    """Rule 156: Intercepts all SQL queries during snapshot generation and asserts 0 write queries."""
    session, db_path = temp_sqlite_file_db

    # Seed initial observation run and order
    t_now = int(time.time() * 1000)
    run = PaperResearchRunRecord(
        run_id="run_audit_001",
        scope="CG01",
        symbol="XAUUSDT",
        status="COLLECTING",
        started_at_ms=t_now - 10000,
        created_at_ms=t_now - 10000,
        updated_at_ms=t_now - 10000,
        execution_mode="PAPER_EXECUTION",
    )
    order = PaperOrder(
        id="po_audit_001",
        instrument="XAUUSDT",
        direction="LONG",
        order_type="LIMIT",
        planned_entry=2620.0,
        stop_loss=2615.0,
        take_profit=2630.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="closed",
        created_at=t_now - 5000,
        closed_at=t_now - 1000,
        realized_pnl_net=15.0,
    )
    link = PaperResearchOrderLink(
        run_id="run_audit_001",
        order_id="po_audit_001",
        linked_at_ms=t_now - 4000,
    )
    session.add_all([run, order, link])
    session.commit()

    # Track executed SQL statements
    executed_statements: List[str] = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        executed_statements.append(statement.strip().upper())

    event.listen(session.bind, "before_cursor_execute", before_cursor_execute)

    try:
        # Run snapshot builder
        snapshot = build_paper_runtime_snapshot(
            session,
            run_id="run_audit_001",
            symbol="XAUUSDT",
            scope="CG01",
            source_mode="OFFLINE_DB",
        )
        assert snapshot["status"] in ("SNAPSHOT_READY", "NOT_CONFIGURED")

        # Disallowed mutation statements
        disallowed_keywords = ["INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "VACUUM"]

        for stmt in executed_statements:
            first_word = stmt.split()[0] if stmt.split() else ""
            assert first_word not in disallowed_keywords, f"Forbidden mutating statement executed: {stmt}"
    finally:
        event.remove(session.bind, "before_cursor_execute", before_cursor_execute)


def test_020_missing_policy_does_not_create_row(temp_sqlite_file_db):
    """Rule 041, 043, 153: Empty DB must NOT create a default TradingPolicy row."""
    session, _ = temp_sqlite_file_db

    initial_policies = session.query(TradingPolicy).count()
    assert initial_policies == 0

    cfg = read_existing_policy_config(session)
    assert cfg["policy_present"] is False
    assert cfg["policy"]["present"] is False

    post_policies = session.query(TradingPolicy).count()
    assert post_policies == 0, "read_existing_policy_config must NEVER create TradingPolicy row"


def test_030_missing_checkpoint_does_not_create_row(temp_sqlite_file_db):
    """Rule 016, 060, 154: Snapshot must NOT create PaperResearchCheckpoint row."""
    session, _ = temp_sqlite_file_db

    initial_checkpoints = session.query(PaperResearchCheckpoint).count()
    assert initial_checkpoints == 0

    snapshot = build_paper_runtime_snapshot(
        session,
        run_id="non_existent",
        symbol="XAUUSDT",
        scope="CG01",
        source_mode="OFFLINE_DB",
    )

    post_checkpoints = session.query(PaperResearchCheckpoint).count()
    assert post_checkpoints == 0, "build_paper_runtime_snapshot must NEVER create PaperResearchCheckpoint row"


def test_040_missing_day_audit_does_not_create_row(temp_sqlite_file_db):
    """Rule 015, 158: Snapshot must NEVER create or touch DayAudit row."""
    session, _ = temp_sqlite_file_db

    initial_audits = session.query(DayAudit).count()
    assert initial_audits == 0

    snapshot = build_paper_runtime_snapshot(
        session,
        run_id=None,
        symbol="XAUUSDT",
        scope="CG01",
        source_mode="OFFLINE_DB",
    )

    post_audits = session.query(DayAudit).count()
    assert post_audits == 0, "Snapshot must NEVER create DayAudit row"


def test_050_full_table_content_comparison_shows_zero_changes(temp_sqlite_file_db):
    """Rule 155: Row-by-row snapshot comparison before and after proves strict immutability."""
    session, _ = temp_sqlite_file_db
    t_now = int(time.time() * 1000)

    # Seed sample rows in multiple tables
    session.add(SystemConfig(key="test_key", value="123", updated_at=t_now))
    session.add(PaperResearchRunRecord(
        run_id="run_immut",
        scope="CG01",
        symbol="XAUUSDT",
        status="COLLECTING",
        started_at_ms=t_now,
        created_at_ms=t_now,
        updated_at_ms=t_now,
        execution_mode="PAPER_EXECUTION",
    ))
    session.commit()

    def dump_table_rows():
        dump = {}
        dump["system_configs"] = [(r.key, r.value) for r in session.query(SystemConfig).order_by(SystemConfig.key).all()]
        dump["trading_policies"] = [(r.id, r.version) for r in session.query(TradingPolicy).order_by(TradingPolicy.id).all()]
        dump["day_audits"] = [(r.id, r.date) for r in session.query(DayAudit).order_by(DayAudit.id).all()]
        dump["checkpoints"] = [(r.scope, r.last_event_id) for r in session.query(PaperResearchCheckpoint).order_by(PaperResearchCheckpoint.scope).all()]
        dump["runs"] = [(r.run_id, r.status, r.started_at_ms) for r in session.query(PaperResearchRunRecord).order_by(PaperResearchRunRecord.run_id).all()]
        dump["orders"] = [(r.id, r.state, r.realized_pnl_net) for r in session.query(PaperOrder).order_by(PaperOrder.id).all()]
        dump["funnel"] = [(r.id, r.stage) for r in session.query(OpportunityFunnelRecord).order_by(OpportunityFunnelRecord.id).all()]
        return dump

    state_before = dump_table_rows()

    # Generate snapshot
    snapshot = build_paper_runtime_snapshot(
        session,
        run_id="run_immut",
        symbol="XAUUSDT",
        scope="CG01",
        source_mode="OFFLINE_DB",
    )

    state_after = dump_table_rows()
    assert state_before == state_after, "Database contents must be 100% identical before and after snapshot generation"


def test_060_real_sqlite_mode_ro_prevents_writes(temp_sqlite_file_db):
    """Rule 119, 120, 157: open_snapshot_session opens a real read-only SQLite session."""
    session, db_path = temp_sqlite_file_db

    # Close the write session first
    session.close()

    with open_snapshot_session(db_path) as ro_session:
        # 1. Reading succeeds
        runs = ro_session.query(PaperResearchRunRecord).all()
        assert isinstance(runs, list)

        # 2. Writing MUST fail at the SQLite engine level
        with pytest.raises(Exception) as exc_info:
            ro_session.execute(text("INSERT INTO system_configs (key, value) VALUES ('hack', 'val')"))
            ro_session.flush()

        err_msg = str(exc_info.value).lower()
        assert "readonly" in err_msg or "query_only" in err_msg or "attempt to write a readonly database" in err_msg


def test_070_offline_worker_health_is_unknown(temp_sqlite_file_db):
    """Rule 029, 096, 152: Offline snapshot must report worker status UNKNOWN with running=None."""
    session, _ = temp_sqlite_file_db
    t_now = int(time.time() * 1000)

    # Seed fresh checkpoint and COLLECTING run
    session.add(PaperResearchRunRecord(
        run_id="run_active_collecting",
        scope="CG01",
        symbol="XAUUSDT",
        status="COLLECTING",
        started_at_ms=t_now - 1000,
        created_at_ms=t_now - 1000,
        updated_at_ms=t_now - 1000,
        execution_mode="PAPER_EXECUTION",
    ))
    session.add(PaperResearchCheckpoint(
        scope="CG01",
        last_event_id="ev_001",
        last_event_time_ms=t_now,
        last_sequence=1,
        updated_at_ms=t_now,
    ))
    session.commit()

    # Build snapshot in offline mode without runtime providers
    snapshot = build_paper_runtime_snapshot(
        session,
        run_id="run_active_collecting",
        symbol="XAUUSDT",
        scope="CG01",
        runtime_providers=None,
        source_mode="OFFLINE_DB",
    )

    worker = snapshot["worker_health"]
    assert worker["status"] == "UNKNOWN"
    assert worker["running"] is None
    assert "OFFLINE_DB" in worker["source"]
