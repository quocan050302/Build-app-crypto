import os
import sys
from pathlib import Path
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Ensure absolute path to backend/aurum_desk.db
BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DEFAULT_DB_PATH = str(BASE_DIR / "aurum_desk.db")

# Strict Test Isolation Guard:
# If running under pytest and AURUM_DB_PATH is not explicitly set,
# use an isolated temp DB path so tests NEVER touch the runtime database!
is_pytest = "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST") is not None or os.getenv("TESTING") == "1"

if is_pytest and not os.getenv("AURUM_DB_PATH"):
    import tempfile
    DB_PATH = os.path.join(tempfile.gettempdir(), f"aurum_isolated_test_{os.getpid()}.db")
else:
    DB_PATH = os.getenv("AURUM_DB_PATH", RUNTIME_DEFAULT_DB_PATH)

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 15},
    poolclass=NullPool
)

# Enable WAL mode and busy timeout for SQLite to prevent locking
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()

def run_schema_migrations(target_engine=None):
    """Safely adds missing V6.1 and V7 columns and tables to SQLite without dropping data."""
    eng = target_engine or engine
    try:
        with eng.connect() as conn:
            # 1. paper_orders
            result = conn.execute(text("PRAGMA table_info(paper_orders)"))
            cols = {row[1] for row in result.fetchall()}
            if cols:  # table exists
                v6_cols = [
                    ("last_processed_market_timestamp", "BIGINT"),
                    ("recovery_status", "VARCHAR(30) DEFAULT 'NONE'"),
                    ("recovery_run_id", "VARCHAR(36)"),
                    ("last_recovery_attempt", "BIGINT"),
                    ("resolved_through", "BIGINT"),
                    ("recovery_confidence", "VARCHAR(30)"),
                    ("discovered_at", "BIGINT"),
                    ("occurred_at", "BIGINT"),
                ]
                v7_cols = [
                    ("strategy_family", "VARCHAR(30) DEFAULT 'STANDARD_SMC'"),
                    ("session_instance_id", "VARCHAR(60)"),
                    ("policy_config_version", "INTEGER DEFAULT 1"),
                    ("evidence_snapshot_id", "VARCHAR(60)"),
                    ("requested_risk_pct", "FLOAT DEFAULT 0.25"),
                    ("effective_risk_pct", "FLOAT DEFAULT 0.25"),
                    ("risk_profile", "VARCHAR(30) DEFAULT 'STANDARD'"),
                    ("cost_snapshot", "TEXT"),
                ]
                for col_name, col_type in (v6_cols + v7_cols):
                    if col_name not in cols:
                        conn.execute(text(f"ALTER TABLE paper_orders ADD COLUMN {col_name} {col_type}"))
                conn.commit()

            # 2. watch_setups
            ws_res = conn.execute(text("PRAGMA table_info(watch_setups)"))
            ws_cols = {row[1] for row in ws_res.fetchall()}
            if ws_cols:
                v7_ws_cols = [
                    ("strategy_family", "VARCHAR(30) DEFAULT 'STANDARD_SMC'"),
                    ("policy_config_version", "INTEGER DEFAULT 1"),
                    ("evidence_snapshot_id", "VARCHAR(60)"),
                    ("requested_risk_pct", "FLOAT DEFAULT 0.25"),
                    ("effective_risk_pct", "FLOAT DEFAULT 0.25"),
                    ("risk_profile", "VARCHAR(30) DEFAULT 'STANDARD'"),
                    ("cost_snapshot", "TEXT"),
                ]
                for col_name, col_type in v7_ws_cols:
                    if col_name not in ws_cols:
                        conn.execute(text(f"ALTER TABLE watch_setups ADD COLUMN {col_name} {col_type}"))
                conn.commit()

            # 3. lessons
            ls_res = conn.execute(text("PRAGMA table_info(lessons)"))
            ls_cols = {row[1] for row in ls_res.fetchall()}
            if ls_cols:
                v7_ls_cols = [
                    ("strategy_family", "VARCHAR(30) DEFAULT 'STANDARD_SMC'"),
                    ("facts_snapshot", "TEXT"),
                    ("compliance_snapshot", "TEXT"),
                    ("mfe_mae_snapshot", "TEXT"),
                    ("action_candidate", "TEXT"),
                ]
                for col_name, col_type in v7_ls_cols:
                    if col_name not in ls_cols:
                        conn.execute(text(f"ALTER TABLE lessons ADD COLUMN {col_name} {col_type}"))
                conn.commit()

            # 4. Create V7 tables if not exist
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS trading_policies (
                    id VARCHAR(50) PRIMARY KEY,
                    version INTEGER DEFAULT 1,
                    mode VARCHAR(20) DEFAULT 'PAPER',
                    symbol VARCHAR(20) DEFAULT 'XAUUSDT',
                    max_daily_fills INTEGER DEFAULT 3,
                    daily_timezone VARCHAR(50) DEFAULT 'Asia/Ho_Chi_Minh',
                    entry_session_policy VARCHAR(40) DEFAULT 'NY_ONLY',
                    ny_timezone VARCHAR(50) DEFAULT 'America/New_York',
                    ny_entry_start VARCHAR(10) DEFAULT '08:00',
                    ny_entry_end VARCHAR(10) DEFAULT '11:00',
                    ny_min_fills INTEGER DEFAULT 1,
                    reserve_ny_slot BOOLEAN DEFAULT 1,
                    ny_fallback_enabled BOOLEAN DEFAULT 1,
                    ny_fallback_start VARCHAR(10) DEFAULT '10:30',
                    ny_fallback_risk_pct_cap FLOAT DEFAULT 0.10,
                    min_net_rr FLOAT DEFAULT 2.0,
                    max_open_positions INTEGER DEFAULT 1,
                    max_armed_orders INTEGER DEFAULT 1,
                    updated_at BIGINT NOT NULL
                );
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS session_quotas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_namespace VARCHAR(50) DEFAULT 'default',
                    symbol VARCHAR(20) DEFAULT 'XAUUSDT',
                    session_instance_id VARCHAR(60) UNIQUE NOT NULL,
                    session_date_utc VARCHAR(10) NOT NULL,
                    session_date_vn VARCHAR(10) NOT NULL,
                    session_date_ny VARCHAR(10) NOT NULL,
                    window_start_ms BIGINT NOT NULL,
                    window_end_ms BIGINT NOT NULL,
                    fallback_start_ms BIGINT NOT NULL,
                    target_fills INTEGER DEFAULT 1,
                    standard_fills INTEGER DEFAULT 0,
                    fallback_fills INTEGER DEFAULT 0,
                    total_fills INTEGER DEFAULT 0,
                    quota_status VARCHAR(30) DEFAULT 'NOT_STARTED',
                    last_status_reason VARCHAR(255),
                    last_evaluated_at BIGINT,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL
                );
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS strategy_evidences (
                    id VARCHAR(60) PRIMARY KEY,
                    strategy_family VARCHAR(30) DEFAULT 'STANDARD_SMC',
                    strategy_version VARCHAR(20) DEFAULT '7.0.0',
                    setup_id VARCHAR(60),
                    setup_instance_id VARCHAR(100),
                    direction VARCHAR(10) NOT NULL,
                    htf_context TEXT,
                    h1_alignment VARCHAR(20) DEFAULT 'UNKNOWN',
                    sweep_evidence TEXT,
                    displacement_evidence TEXT,
                    fvg_evidence TEXT,
                    retest_evidence TEXT,
                    missing_evidence TEXT,
                    cost_snapshot TEXT,
                    risk_snapshot TEXT,
                    created_at BIGINT NOT NULL
                );
            """))
            conn.commit()
    except Exception:
        pass

run_schema_migrations(engine)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

