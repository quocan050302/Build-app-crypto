import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Setup paths in environment BEFORE any backend module is imported
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

RUNTIME_DB_PATH = BACKEND_DIR / "aurum_desk.db"

# V17.7.4 Section 09.1: Test run ownership bootstrap before module imports
CURRENT_RUN_ID = os.getenv("AURUM_TEST_RUN_ID")
if not CURRENT_RUN_ID:
    CURRENT_RUN_ID = f"run_{int(time.time()*1000)}_{os.getpid()}"
    os.environ["AURUM_TEST_RUN_ID"] = CURRENT_RUN_ID

# Check inherited environment variables and detect conflicts
test_db_override = os.getenv("AURUM_TEST_DB_PATH")
db_path_override = os.getenv("AURUM_DB_PATH")
if test_db_override and db_path_override and test_db_override != db_path_override:
    raise RuntimeError(f"CONFLICTING_TEST_DB_TARGETS: AURUM_TEST_DB_PATH={test_db_override} vs AURUM_DB_PATH={db_path_override}")

# Initialize canonical test-owned root with schema_version 1 marker
test_root_env = os.getenv("AURUM_TEST_ROOT")
test_root_path = None
if test_root_env:
    candidate = Path(test_root_env).resolve()
    marker = candidate / ".aurum_test_owned"
    if candidate.is_dir() and marker.is_file():
        try:
            m_data = json.loads(marker.read_text(encoding="utf-8"))
            if m_data.get("schema_version") == 1 and m_data.get("test_owner") == "aurum_test_framework":
                test_root_path = candidate
        except Exception:
            pass

if not test_root_path:
    temp_dir = tempfile.mkdtemp(prefix="aurum_test_root_")
    test_root_path = Path(temp_dir).resolve()
    marker = test_root_path / ".aurum_test_owned"
    marker_data = {
        "schema_version": 1,
        "run_id": CURRENT_RUN_ID,
        "canonical_root": str(test_root_path),
        "test_owner": "aurum_test_framework",
        "created_at": time.time(),
        "pid": os.getpid(),
    }
    marker.write_text(json.dumps(marker_data), encoding="utf-8")
    os.environ["AURUM_TEST_ROOT"] = str(test_root_path)

target_override = test_db_override or db_path_override
if target_override:
    # Fail closed before mutation if unsafe override
    from database import assert_test_database_isolated
    assert_test_database_isolated(target_override)
    TEST_DB_PATH = target_override
else:
    TEST_DB_PATH = str(test_root_path / f"test_aurum_{CURRENT_RUN_ID}.db")

os.environ["AURUM_DB_PATH"] = TEST_DB_PATH
os.environ["TESTING"] = "1"

import models
from models import Base
from database import get_db, run_schema_migrations
from main import app
from fastapi.testclient import TestClient

@pytest.fixture(scope="session", autouse=True)
def guard_runtime_database():
    """Sentinel fixture: proves runtime database is never touched or truncated by test suites (V17.7.4 Section 09.3)."""
    runtime_exists = RUNTIME_DB_PATH.exists()
    baseline_stat = RUNTIME_DB_PATH.stat() if runtime_exists else None

    # V17.7.4 Section 09.3: If runtime app is actively writing to DB, distinguish concurrent
    # legitimate writes from test mutation; do not make unconditioned byte-equality claims.
    is_actively_written = False
    if runtime_exists:
        try:
            mtime_1 = RUNTIME_DB_PATH.stat().st_mtime
            # If written within the last 30 seconds, it's actively maintained by a concurrent background process
            if (time.time() - mtime_1) < 30.0:
                is_actively_written = True
        except Exception:
            pass

    baseline_hash = None
    if runtime_exists and not is_actively_written:
        try:
            with open(RUNTIME_DB_PATH, "rb") as f:
                baseline_hash = hashlib.sha256(f.read()).hexdigest()
        except Exception:
            pass

    yield

    if runtime_exists:
        assert RUNTIME_DB_PATH.exists(), "CRITICAL: Runtime database was deleted by tests!"
        current_stat = RUNTIME_DB_PATH.stat()
        assert current_stat.st_ino == baseline_stat.st_ino, "CRITICAL: Runtime DB file replaced!"
        assert current_stat.st_size > 0, "CRITICAL: Runtime DB file was truncated!"
        if baseline_hash is not None and not is_actively_written:
            with open(RUNTIME_DB_PATH, "rb") as f:
                current_hash = hashlib.sha256(f.read()).hexdigest()
            assert current_hash == baseline_hash, "CRITICAL: Runtime database content was modified by tests!"
    else:
        assert not RUNTIME_DB_PATH.exists(), "CRITICAL: Tests created runtime database at RUNTIME_DB_PATH!"

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


def _register_basetemp_root(basetemp_path: Path):
    bt_path = basetemp_path.resolve()
    marker = bt_path / ".aurum_test_owned"
    current_run_id = os.getenv("AURUM_TEST_RUN_ID")
    if not marker.exists():
        marker_data = {
            "schema_version": 1,
            "run_id": current_run_id,
            "canonical_root": str(bt_path),
            "test_owner": "aurum_test_framework",
            "created_at": time.time(),
            "pid": os.getpid(),
        }
        marker.write_text(json.dumps(marker_data), encoding="utf-8")
    from database import register_trusted_test_root
    register_trusted_test_root(bt_path)


def pytest_configure(config):
    config.addinivalue_line("markers", "certified_entry: force certified STANDARD_SMC entry (V14.4.1)")
    config.addinivalue_line("markers", "legacy_entry: force LEGACY_UNCERTIFIED entry mode")

    # V17.7.4 Section 09.2: Register pytest session basetemp with current run ownership
    tmp_factory = getattr(config, "_tmp_path_factory", None)
    if tmp_factory:
        basetemp = tmp_factory.getbasetemp()
        if basetemp:
            _register_basetemp_root(Path(str(basetemp)))


@pytest.fixture(autouse=True)
def register_test_tmp_path_root(request):
    """V17.7.4 Section 09.2: Ensure pytest's session basetemp is registered as a trusted test root with marker."""
    tmp_factory = getattr(request.config, "_tmp_path_factory", None)
    if tmp_factory:
        basetemp = tmp_factory.getbasetemp()
        if basetemp:
            _register_basetemp_root(Path(str(basetemp)))


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


@pytest.fixture(autouse=True)
def reset_execution_consumer():
    """V17.6.4.3: Ensure execution_consumer, bitget_ws_service, and collector_service do not leak state across test boundaries."""
    from services.execution_consumer import execution_consumer
    from services.collector_service import collector_service
    from services.bitget_ws_service import bitget_ws_service

    execution_consumer.has_execution_gap = False
    execution_consumer.is_lagging = False
    execution_consumer.dropped_count = 0
    collector_service.latest_ticker = None
    bitget_ws_service.latest_quote = None
    bitget_ws_service.transport_state = "DISCONNECTED"

    yield

    execution_consumer.has_execution_gap = False
    execution_consumer.is_lagging = False
    execution_consumer.dropped_count = 0
    collector_service.latest_ticker = None
    bitget_ws_service.latest_quote = None
    bitget_ws_service.transport_state = "DISCONNECTED"

