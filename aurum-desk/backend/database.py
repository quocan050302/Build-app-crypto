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
    """Safely adds missing V6.1 columns to SQLite tables without dropping data."""
    eng = target_engine or engine
    try:
        with eng.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(paper_orders)"))
            cols = {row[1] for row in result.fetchall()}
            if cols:  # table exists
                new_cols = [
                    ("last_processed_market_timestamp", "BIGINT"),
                    ("recovery_status", "VARCHAR(30) DEFAULT 'NONE'"),
                    ("recovery_run_id", "VARCHAR(36)"),
                    ("last_recovery_attempt", "BIGINT"),
                    ("resolved_through", "BIGINT"),
                    ("recovery_confidence", "VARCHAR(30)"),
                    ("discovered_at", "BIGINT"),
                    ("occurred_at", "BIGINT"),
                ]
                for col_name, col_type in new_cols:
                    if col_name not in cols:
                        conn.execute(text(f"ALTER TABLE paper_orders ADD COLUMN {col_name} {col_type}"))
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

