"""
backend/tests/test_migration_runtime_upgrade.py
Tests real database migration and upgrade contracts (MIG01-MIG06).
"""
import os
import sqlite3
import tempfile
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from migrate import run_migration, resolve_migration_db_path, backup_sqlite_database
from database import run_schema_migrations
import models


@pytest.fixture
def temp_mig_db():
    fd, path = tempfile.mkstemp(suffix="_old_aurum.db")
    os.close(fd)
    yield Path(path)
    for p in (path, path + "-wal", path + "-shm"):
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def create_v3_legacy_schema(db_path: Path):
    """Creates a legacy database without day_audits.updated_at_ms or forecast_observations."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE day_audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_str VARCHAR(10) UNIQUE,
            initial_equity FLOAT DEFAULT 1000.0,
            current_equity FLOAT DEFAULT 1000.0,
            realized_pnl_today FLOAT DEFAULT 0.0,
            fills_count INTEGER DEFAULT 0,
            consecutive_losses INTEGER DEFAULT 0,
            cooldown_until BIGINT,
            is_blocked BOOLEAN DEFAULT 0,
            block_reason VARCHAR(255)
        );
    """)
    cur.execute("""
        INSERT INTO day_audits (date_str, initial_equity, current_equity, fills_count)
        VALUES ('2026-10-12', 1000.0, 1050.0, 1);
    """)
    cur.execute("""
        CREATE TABLE paper_orders (
            id VARCHAR(36) PRIMARY KEY,
            setup_id VARCHAR(50),
            instrument VARCHAR(20) DEFAULT 'XAUUSDT',
            direction VARCHAR(10) NOT NULL,
            state VARCHAR(20) DEFAULT 'candidate',
            order_type VARCHAR(10) DEFAULT 'MARKET',
            planned_entry FLOAT NOT NULL,
            stop_loss FLOAT NOT NULL,
            take_profit FLOAT NOT NULL,
            quantity FLOAT NOT NULL,
            initial_risk_usdt FLOAT NOT NULL,
            created_at BIGINT NOT NULL
        );
    """)
    cur.execute("""
        INSERT INTO paper_orders (id, setup_id, direction, state, planned_entry, stop_loss, take_profit, quantity, initial_risk_usdt, created_at)
        VALUES ('order-legacy-1', 'setup-1', 'LONG', 'armed', 2640.0, 2635.0, 2650.0, 0.1, 25.0, 1776120000000);
    """)
    conn.commit()
    conn.close()


def test_mig01_old_to_new_upgrade(temp_mig_db):
    """MIG01: Upgrade legacy DB missing day_audits.updated_at_ms and forecast_observations."""
    create_v3_legacy_schema(temp_mig_db)

    run_migration(db_path=temp_mig_db)

    conn = sqlite3.connect(str(temp_mig_db))
    cur = conn.cursor()

    # Verify day_audits.updated_at_ms column was added
    cur.execute("PRAGMA table_info(day_audits);")
    cols = [r[1] for r in cur.fetchall()]
    assert "updated_at_ms" in cols

    # Verify forecast_observations table exists with expected schema
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='forecast_observations';")
    assert cur.fetchone() is not None

    cur.execute("PRAGMA table_info(forecast_observations);")
    fc_cols = [r[1] for r in cur.fetchall()]
    assert "session_id" in fc_cols
    assert "content_digest" in fc_cols
    assert "outcome_status" in fc_cols

    # Verify legacy data preserved
    cur.execute("SELECT date_str, current_equity FROM day_audits WHERE date_str='2026-10-12';")
    audit_row = cur.fetchone()
    assert audit_row == ("2026-10-12", 1050.0)

    cur.execute("SELECT id, state FROM paper_orders WHERE id='order-legacy-1';")
    order_row = cur.fetchone()
    assert order_row == ("order-legacy-1", "armed")
    conn.close()


def test_mig02_rerun_idempotency(temp_mig_db):
    """MIG02: Re-running migration multiple times on upgraded DB succeeds without errors."""
    create_v3_legacy_schema(temp_mig_db)

    # First run
    run_migration(db_path=temp_mig_db)

    # Second run (idempotent)
    run_migration(db_path=temp_mig_db)

    conn = sqlite3.connect(str(temp_mig_db))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM day_audits;")
    assert cur.fetchone()[0] == 1
    conn.close()


def test_mig03_orm_models_match(temp_mig_db):
    """MIG03: ORM queries and inserts operate cleanly on upgraded DB."""
    create_v3_legacy_schema(temp_mig_db)
    run_migration(db_path=temp_mig_db)

    engine = create_engine(f"sqlite:///{temp_mig_db}")
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Query existing audit
        audit = session.query(models.DayAudit).filter_by(date_str="2026-10-12").first()
        assert audit is not None
        assert audit.current_equity == 1050.0

        # Insert a new forecast observation using ORM
        obs = models.ForecastObservation(
            id="fc-test-orm-1",
            forecast_id="fc-test-orm",
            revision=1,
            symbol="XAUUSDT",
            session="NEW_YORK",
            session_id="fc:ny:2026-10-12",
            as_of_ms=1776120000000,
            context_cutoff_ms=1776120000000,
            content_digest="digest123456",
            hypothesis_type="CONTINUATION",
            direction="LONG",
            target_price=2680.0,
            outcome_status="PENDING",
            payload_json="{}",
            created_at=1776120000000,
        )
        session.add(obs)
        session.commit()

        queried = session.query(models.ForecastObservation).filter_by(id="fc-test-orm-1").first()
        assert queried is not None
        assert queried.target_price == 2680.0
    finally:
        session.close()
        engine.dispose()


def test_mig04_resolve_migration_db_path(monkeypatch, temp_mig_db):
    """MIG04: DB path resolver prioritizes explicit > env > test isolation guard."""
    explicit = temp_mig_db
    resolved = resolve_migration_db_path(explicit_path=str(explicit))
    assert resolved == explicit.resolve()

    # Env path wins over default
    monkeypatch.setenv("AURUM_DB_PATH", str(temp_mig_db))
    resolved_env = resolve_migration_db_path()
    assert resolved_env == temp_mig_db.resolve()

    # When testing flag is 1 and no explicit or env path, isolated temp DB is returned
    monkeypatch.delenv("AURUM_DB_PATH", raising=False)
    monkeypatch.setenv("TESTING", "1")
    resolved_isolated = resolve_migration_db_path()
    assert "aurum_mig_isolated" in str(resolved_isolated)
    assert not str(resolved_isolated).endswith("backend/aurum_desk.db")


def test_mig05_wal_consistent_backup(temp_mig_db):
    """MIG05: backup_sqlite_database captures active transaction pages consistently."""
    create_v3_legacy_schema(temp_mig_db)

    conn = sqlite3.connect(str(temp_mig_db))
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("INSERT INTO day_audits (date_str, current_equity) VALUES ('2026-10-13', 2000.0);")
    conn.commit()

    bck_path = temp_mig_db.parent / f"{temp_mig_db.name}.test_backup.db"
    try:
        backup_sqlite_database(conn, bck_path)
        assert bck_path.exists()
        assert bck_path.stat().st_size > 0

        # Read back from backup
        bck_conn = sqlite3.connect(str(bck_path))
        bck_cur = bck_conn.cursor()
        bck_cur.execute("SELECT current_equity FROM day_audits WHERE date_str='2026-10-13';")
        row = bck_cur.fetchone()
        assert row == (2000.0,)
        bck_conn.close()
    finally:
        conn.close()
        if bck_path.exists():
            bck_path.unlink()


def test_mig06_run_schema_migrations_parity(temp_mig_db):
    """MIG06: Both database.run_schema_migrations and migrate.run_migration work cooperatively."""
    create_v3_legacy_schema(temp_mig_db)
    engine = create_engine(f"sqlite:///{temp_mig_db}")

    try:
        # First run schema migration via database engine
        run_schema_migrations(target_engine=engine)
    finally:
        engine.dispose()

    # Then run full migration via migrate.run_migration
    run_migration(db_path=temp_mig_db)

    conn = sqlite3.connect(str(temp_mig_db))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trading_policies';")
    assert cur.fetchone() is not None
    conn.close()
