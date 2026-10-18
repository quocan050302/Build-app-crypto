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

@pytest.fixture(autouse=True)
def entry_certification_mode(request):
    """V14.4.1: certified STANDARD_SMC entry is ON by default and blocks READY without a verified chain.

    Suites written before V14.4.1 (news/RR/lesson/fill/telegram behaviour) seed chain-less READY setups.
    They run in the explicit LEGACY_UNCERTIFIED mode (flag OFF) so that they keep testing what they were
    written for. Suites named test_v14_4* always run certified (flag ON) and must prove a real chain;
    a test may force either mode with @pytest.mark.certified_entry / @pytest.mark.legacy_entry.
    """
    import dataclasses
    import services.smc_scenario_config as scfg
    name = request.module.__name__.split(".")[-1]
    if request.node.get_closest_marker("certified_entry"):
        certified = True
    elif request.node.get_closest_marker("legacy_entry"):
        certified = False
    else:
        certified = name.startswith("test_v14_4")
    previous = scfg.active_scenario_config
    scfg.active_scenario_config = dataclasses.replace(previous, certified_smc_entry_enabled=certified)
    try:
        yield
    finally:
        scfg.active_scenario_config = previous


def pytest_configure(config):
    config.addinivalue_line("markers", "certified_entry: force certified STANDARD_SMC entry (V14.4.1)")
    config.addinivalue_line("markers", "legacy_entry: force LEGACY_UNCERTIFIED entry mode")


@pytest.fixture
def anyio_backend():
    return "asyncio"


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


@pytest.fixture
def fresh_ticker():
    """
    V14.2: entry guards require a valid, fresh quote (stale/missing quotes block entry).
    Provides a fresh XAUUSDT ticker on the collector singleton and restores the previous value.
    Tests that arm/fill orders must use this (or inject a validated quote explicitly).
    """
    import time as _time
    from services.collector_service import collector_service

    previous = collector_service.latest_ticker

    def _set(bid=2650.0, ask=2650.2, now_ms=None):
        ts = now_ms if now_ms is not None else int(_time.time() * 1000)
        collector_service.latest_ticker = {
            "symbol": "XAUUSDT", "bid": bid, "ask": ask, "last": bid, "timestamp": ts, "observed_at": ts,
        }
        return collector_service.latest_ticker

    _set()
    try:
        yield _set
    finally:
        collector_service.latest_ticker = previous
