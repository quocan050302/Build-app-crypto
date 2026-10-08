import os
import sys
import tempfile
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Setup isolated DB path in environment BEFORE any backend module is imported
BACKEND_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DB_PATH = BACKEND_DIR / "aurum_desk.db"

# Force environment variable for test isolation
temp_dir = tempfile.mkdtemp(prefix="aurum_test_suite_")
TEST_DB_PATH = os.path.join(temp_dir, "test_aurum.db")
os.environ["AURUM_DB_PATH"] = TEST_DB_PATH
os.environ["TESTING"] = "1"

import models
from models import Base
from database import get_db, run_schema_migrations
from main import app
from fastapi.testclient import TestClient

@pytest.fixture(scope="session", autouse=True)
def guard_runtime_database():
    """Sentinel fixture: proves runtime database is never touched or truncated by test suites."""
    # Capture runtime DB baseline if it exists
    runtime_exists = RUNTIME_DB_PATH.exists()
    baseline_stat = RUNTIME_DB_PATH.stat() if runtime_exists else None

    yield

    if runtime_exists:
        assert RUNTIME_DB_PATH.exists(), "CRITICAL: Runtime database was deleted by tests!"
        current_stat = RUNTIME_DB_PATH.stat()
        # Ensure inode / canonical file was not replaced or wiped
        assert current_stat.st_ino == baseline_stat.st_ino, "CRITICAL: Runtime DB file replaced!"

@pytest.fixture
def isolated_db():
    """Provides a fresh isolated in-memory or temp SQLite session for each test function."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()

@pytest.fixture
def client(isolated_db):
    """TestClient wired to the isolated test database via dependency_overrides."""
    def override_get_db():
        yield isolated_db

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
