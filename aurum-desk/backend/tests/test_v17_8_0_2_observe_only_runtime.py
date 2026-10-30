import asyncio
import contextlib
import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import crud
import database
import main
import models
from services.execution_consumer import execution_consumer
from services.execution_coordinator import execution_coordinator
from services.exit_monitor import exit_monitor
from services.position_recovery_service import PositionRecoveryService
from services.quote_validator import QuoteValidator
from services.runtime_profile_service import (
    ALL_BACKGROUND_TRADING_WORKERS,
    ENV_BE_RUNTIME_PROFILE,
    PROFILE_OBSERVE_ONLY,
    PROFILE_TRADING,
    get_runtime_safety_counters,
    get_runtime_safety_snapshot,
    is_observe_only_runtime,
    reset_runtime_safety_counters,
    resolve_be_runtime_profile,
)
from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
from services.strategy_service import strategy_service
from services.trade_lifecycle_service import TradeLifecycleService
from services.trading_policy_service import apply_runtime_contract_upgrades


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture
def disposable_observe_db(monkeypatch):
    """
    Creates a disposable SQLite database inside the verified test-owned root,
    seeds schema and baseline state while in TRADING mode, checkpoints and switches
    journal_mode=DELETE so there are no sidecar WAL frames, and yields (db_path, TestingSessionLocal, engine).
    """
    monkeypatch.delenv(ENV_BE_RUNTIME_PROFILE, raising=False)
    reset_runtime_safety_counters()

    test_root = database.get_or_create_test_root()
    db_file = test_root / f"v17_8_0_2_observe_only_{id(monkeypatch)}.db"
    if db_file.exists():
        db_file.unlink()

    eng = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @database.event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, conn_rec):
        database.set_sqlite_pragma(dbapi_conn, conn_rec)

    database.assert_test_database_isolated(target_engine=eng)
    models.Base.metadata.create_all(bind=eng)
    database.run_schema_migrations(eng)

    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=eng)
    s = TestingSession()
    try:
        crud.get_or_create_today_audit(s)
        s.commit()
    finally:
        s.close()
    eng.dispose()

    # Finalize SQLite file into deterministic single-file state before taking SHA-256 baseline
    raw_conn = sqlite3.connect(str(db_file))
    try:
        raw_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        raw_conn.execute("PRAGMA journal_mode=DELETE;")
        raw_conn.commit()
    finally:
        raw_conn.close()

    def _override_get_db():
        db = TestingSession()
        if is_observe_only_runtime():
            db.autoflush = False
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[main.get_db] = _override_get_db
    monkeypatch.setattr(main, "SessionLocal", TestingSession)

    yield db_file, TestingSession, eng

    monkeypatch.delenv(ENV_BE_RUNTIME_PROFILE, raising=False)
    main.app.dependency_overrides.pop(main.get_db, None)
    eng.dispose()
    for suffix in ("", "-wal", "-shm"):
        p = Path(f"{db_file}{suffix}")
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


def test_runtime_profile_resolution_and_invalid_profile_fails_closed(monkeypatch):
    """Invalid AURUM_BE_RUNTIME_PROFILE must fail closed with RuntimeError."""
    monkeypatch.delenv(ENV_BE_RUNTIME_PROFILE, raising=False)
    assert resolve_be_runtime_profile() == PROFILE_TRADING
    assert is_observe_only_runtime() is False

    monkeypatch.setenv(ENV_BE_RUNTIME_PROFILE, "observe_only")
    assert resolve_be_runtime_profile() == PROFILE_OBSERVE_ONLY
    assert is_observe_only_runtime() is True

    monkeypatch.setenv(ENV_BE_RUNTIME_PROFILE, "SHADOW_UNSAFE_INVALID")
    with pytest.raises(RuntimeError, match="INVALID_BE_RUNTIME_PROFILE"):
        resolve_be_runtime_profile()
    with pytest.raises(RuntimeError, match="INVALID_BE_RUNTIME_PROFILE"):
        is_observe_only_runtime()

    async def _attempt_lifespan():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(RuntimeError, match="INVALID_BE_RUNTIME_PROFILE"):
        asyncio.run(_attempt_lifespan())


def test_observe_only_lifespan_startup_shutdown_and_negative_control(monkeypatch, disposable_observe_db):
    """
    Verifies OBSERVE_ONLY lifespan starts 0 trading workers and completes shutdown cleanly without leaked tasks,
    and includes a negative control proving normal TRADING lifespan starts all 15 background workers.
    """
    db_file, _, _ = disposable_observe_db
    baseline_sha = _sha256_file(db_file)

    monkeypatch.setenv(ENV_BE_RUNTIME_PROFILE, PROFILE_OBSERVE_ONLY)
    reset_runtime_safety_counters()

    async def _run_observe_only_lifespan():
        before_tasks = set(asyncio.all_tasks())
        async with main.lifespan(main.app):
            startup_res = main.get_last_startup_result()
            assert startup_res["status"] == "HEALTHY_OBSERVE_ONLY"
            assert startup_res["runtime_profile"] == PROFILE_OBSERVE_ONLY
            assert startup_res["observe_only"] is True
            assert startup_res["allowed_workers"] == []
            assert startup_res["blocked_trading_workers"] == list(ALL_BACKGROUND_TRADING_WORKERS)
            assert startup_res["background_trading_workers_started"] == 0
            during_tasks = set(asyncio.all_tasks()) - before_tasks
            assert len(during_tasks) == 0

        shutdown_res = main.get_last_shutdown_result()
        assert shutdown_res["status"] == "COMPLETED"
        assert shutdown_res["runtime_profile"] == PROFILE_OBSERVE_ONLY
        assert shutdown_res["active_background_tasks_remaining"] == 0
        after_tasks = set(asyncio.all_tasks()) - before_tasks
        assert len(after_tasks) == 0

    asyncio.run(_run_observe_only_lifespan())
    assert _sha256_file(db_file) == baseline_sha
    counters = get_runtime_safety_counters()
    assert counters["BACKGROUND_TRADING_WORKERS_STARTED"] == 0
    assert counters["PAPER_ORDERS_CREATED"] == 0
    assert counters["PAPER_ORDERS_FILLED"] == 0
    assert counters["PAPER_POSITIONS_CLOSED"] == 0
    assert counters["LIVE_EXCHANGE_ORDERS_SENT"] == 0

    # Negative control: When AURUM_BE_RUNTIME_PROFILE=TRADING, normal lifespan starts all 15 background workers
    monkeypatch.setenv(ENV_BE_RUNTIME_PROFILE, PROFILE_TRADING)
    reset_runtime_safety_counters()

    async def _dummy_loop(*args, **kwargs):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return

    async def _run_trading_negative_control():
        patches = [
            patch.object(main, "reconcile_stuck_orders"),
            patch("services.trading_policy_service.apply_runtime_contract_upgrades"),
            patch("services.position_recovery_service.position_recovery_service.check_and_recover_offline_positions"),
            patch("services.cg01_telemetry_service.start_trace_persistence_worker", new=AsyncMock(return_value=True)),
            patch("services.cg01_telemetry_service.drain_trace_persistence", new=AsyncMock(return_value={"status": "COMPLETED"})),
            patch("services.cg01_telemetry_service.stop_trace_persistence_worker"),
            patch("services.cg01_telemetry_service.wait_trace_persistence_worker_stopped", new=AsyncMock(return_value=True)),
            patch("services.cg01_telemetry_service.get_trace_worker_stop_error", return_value=None),
            patch("services.bitget_ws_service.bitget_ws_service.run_ws_loop", new=_dummy_loop),
            patch("services.execution_consumer.execution_consumer.run_consumer_loop", new=_dummy_loop),
            patch("services.candle_cache_service.candle_cache_service.run_persistence_worker", new=_dummy_loop),
            patch("services.collector_service.collector_service.run_collector_loop", new=_dummy_loop),
            patch("services.strategy_service.strategy_service.run_strategy_loop", new=_dummy_loop),
            patch("services.proximity_service.proximity_service.run_proximity_loop", new=_dummy_loop),
            patch("services.execution_coordinator.execution_coordinator.run_coordinator_loop", new=_dummy_loop),
            patch("services.exit_monitor.exit_monitor.run_exit_monitor_loop", new=_dummy_loop),
            patch("services.market_reconciliation_service.market_reconciliation_service.run_late_reconciliation_loop", new=_dummy_loop),
            patch.object(main, "process_notification_outbox", new=_dummy_loop),
            patch("services.forward_repair_worker.forward_repair_worker.run_repair_loop", new=_dummy_loop),
            patch("services.lesson_repair_worker.lesson_repair_worker.run_repair_loop", new=_dummy_loop),
            patch("services.scalp_watcher_orchestrator.scalp_watcher_orchestrator.run_watcher_loop", new=_dummy_loop),
            patch("services.cg01_scheduler_service.cg01_evaluation_scheduler.run", new=_dummy_loop),
            patch("services.cg01_projection_worker.cg01_projection_worker.run", new=_dummy_loop),
        ]
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            async with main.lifespan(main.app):
                startup_res = main.get_last_startup_result()
                assert startup_res["runtime_profile"] == PROFILE_TRADING
                assert startup_res["observe_only"] is False
                assert startup_res["background_trading_workers_started"] == len(ALL_BACKGROUND_TRADING_WORKERS) == 15

    asyncio.run(_run_trading_negative_control())
    assert get_runtime_safety_counters()["BACKGROUND_TRADING_WORKERS_STARTED"] == 15


def test_observe_only_blocks_worker_and_order_execution_boundaries(monkeypatch, disposable_observe_db):
    """
    Verifies that direct invocations of strategy loop, scalp watcher ARM/loop,
    execution coordinator FILL/loop, execution consumer, trade lifecycle fill/close/exit,
    runtime contract upgrades, offline recovery, and stuck order reconciliation are blocked in OBSERVE_ONLY.
    """
    db_file, TestingSession, eng = disposable_observe_db
    baseline_sha = _sha256_file(db_file)

    monkeypatch.setenv(ENV_BE_RUNTIME_PROFILE, PROFILE_OBSERVE_ONLY)
    reset_runtime_safety_counters()

    db = TestingSession()
    try:
        # 1. Startup mutation functions blocked
        assert database.run_schema_migrations(eng) is False
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            main.reconcile_stuck_orders()
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            apply_runtime_contract_upgrades(db)
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            PositionRecoveryService.check_and_recover_offline_positions(db)

        # 2. Strategy & Watcher ARM boundaries blocked
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            asyncio.run(strategy_service.run_strategy_loop())
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            strategy_service.set_auto_state(db, True)
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            asyncio.run(scalp_watcher_orchestrator.run_watcher_loop())
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            scalp_watcher_orchestrator._arm_paper_candidate(
                db,
                None,  # type: ignore[arg-type]
                now_ms=1700000000000,
                clock=None,
                quote=None,
                policy_settings={},
            )

        # 3. Execution Coordinator & Consumer FILL boundaries blocked
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            execution_coordinator.evaluate_orders_sync(db)
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            asyncio.run(execution_coordinator.run_coordinator_loop())

        dummy_quote = QuoteValidator.validate_ticker(
            {
                "symbol": "XAUUSDT",
                "bid": 4020.0,
                "ask": 4020.2,
                "last": 4020.1,
                "ts": 1700000000000,
            },
            now_ms=1700000000010,
        )
        assert execution_consumer.enqueue_quote(dummy_quote) is False
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            execution_consumer.evaluate_quote_sync(db, dummy_quote)
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            asyncio.run(execution_consumer.run_consumer_loop())

        # 4. Exit Monitor & TradeLifecycleService boundaries blocked
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            asyncio.run(exit_monitor.check_active_position())
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            asyncio.run(exit_monitor.run_exit_monitor_loop())
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            TradeLifecycleService.execute_fill(db, None, 4020.0, None)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            TradeLifecycleService.execute_close(db, "ord-1", 4020.0, "SL_HIT")
        with pytest.raises(RuntimeError, match="OBSERVE_ONLY_BLOCKED"):
            TradeLifecycleService.process_exit_tick(db, 4020.0, 4020.2)
    finally:
        db.close()

    counters = get_runtime_safety_counters()
    assert counters["BLOCKED_STARTUP_MUTATION_ATTEMPTS"] >= 4
    assert counters["BLOCKED_ARM_ATTEMPTS"] >= 1
    assert counters["BLOCKED_FILL_ATTEMPTS"] >= 3
    assert counters["BLOCKED_CLOSE_ATTEMPTS"] >= 3
    assert counters["BLOCKED_WORKER_START_ATTEMPTS"] >= 4
    assert counters["PAPER_ORDERS_CREATED"] == 0
    assert counters["PAPER_ORDERS_FILLED"] == 0
    assert counters["PAPER_POSITIONS_CLOSED"] == 0
    assert counters["LIVE_EXCHANGE_ORDERS_SENT"] == 0
    assert _sha256_file(db_file) == baseline_sha


def test_observe_only_api_read_allowlist_and_db_immutability(monkeypatch, disposable_observe_db):
    """
    Verifies that under OBSERVE_ONLY:
    1. Health and allowlisted read endpoints succeed with HTTP 200.
    2. Write/mutation endpoints (POST/PUT/PATCH/DELETE and GET /api/v1/reports/generate) are rejected with HTTP 403.
    3. Direct SQLite / ORM write attempts are rejected at the DB boundary.
    4. The SQLite database file SHA-256 remains byte-for-byte identical before and after all operations.
    """
    db_file, TestingSession, _ = disposable_observe_db
    baseline_sha = _sha256_file(db_file)
    baseline_bytes = db_file.read_bytes()

    monkeypatch.setenv(ENV_BE_RUNTIME_PROFILE, PROFILE_OBSERVE_ONLY)
    reset_runtime_safety_counters()

    with TestClient(main.app) as client:
        # 1. Health & Runtime Profile endpoints
        r_health = client.get("/health")
        assert r_health.status_code == 200
        r_health_v1 = client.get("/api/v1/health")
        assert r_health_v1.status_code == 200

        r_prof = client.get("/api/v1/runtime/profile")
        assert r_prof.status_code == 200
        prof_data = r_prof.json()
        assert prof_data["runtime_profile"] == PROFILE_OBSERVE_ONLY
        assert prof_data["observe_only"] is True
        assert prof_data["trading_enabled"] is False
        assert prof_data["startup_result"]["status"] == "HEALTHY_OBSERVE_ONLY"

        # 2. Read-only policy, observation, and orders endpoints
        for read_path in (
            "/api/v1/policy/stop-placement",
            "/api/v1/observation/status",
            "/api/v1/observation/summary",
            "/api/v1/paper/orders",
            "/api/v1/orders/pending",
            "/api/v1/journal",
            "/api/v1/account/status",
            "/api/v1/account/settings",
            "/api/scalp/daily-status",
            "/api/v1/policy/ny-session",
        ):
            resp = client.get(read_path)
            assert resp.status_code == 200, f"Expected 200 for {read_path}, got {resp.status_code}: {resp.text}"

        # 3. Mutating HTTP endpoints must be blocked with 403 OBSERVE_ONLY_MUTATION_BLOCKED
        mutating_calls = [
            ("POST", "/api/v1/orders/paper", {"instrument": "XAUUSDT", "direction": "LONG", "order_type": "MARKET", "planned_entry": 4020.0, "stop_loss": 4010.0, "take_profit": 4045.0, "quantity": 0.1}),
            ("POST", "/api/v1/setups/arm", {"setup_id": "watch-XAUUSDT-15M"}),
            ("PUT", "/api/v1/policy/stop-placement", {"mode": "PAPER_STRUCTURAL", "expected_version": 1}),
            ("POST", "/api/scalp/config", {"scalp_m1_mode": "AUTO"}),
            ("POST", "/api/v1/auto/state", {"enabled": True}),
            ("POST", "/api/v1/candles/sync", {}),
            ("GET", "/api/v1/reports/generate", None),
        ]
        for method, path, payload in mutating_calls:
            if method == "GET":
                m_resp = client.get(path)
            elif method == "PUT":
                m_resp = client.put(path, json=payload)
            else:
                m_resp = client.post(path, json=payload)
            assert m_resp.status_code == 403, f"Expected 403 for {method} {path}, got {m_resp.status_code}"
            assert m_resp.json()["detail"]["code"] == "OBSERVE_ONLY_MUTATION_BLOCKED"

        # 4. Direct SQL and ORM write attempts must fail closed at DB boundary
        db = TestingSession()
        try:
            with pytest.raises(RuntimeError, match="OBSERVE_ONLY_DB_WRITE_BLOCKED"):
                db.execute(text("INSERT INTO system_config (key, value, updated_at) VALUES ('k', 'v', 1)"))
            with pytest.raises(RuntimeError, match="OBSERVE_ONLY_DB_WRITE_BLOCKED"):
                db.execute(text("UPDATE day_audits SET fills_count = 99"))
            with pytest.raises(RuntimeError, match="OBSERVE_ONLY_DB_WRITE_BLOCKED"):
                db.add(models.SystemConfig(key="illegal_write", value="1", updated_at=1))
                db.flush()
            db.rollback()
        finally:
            db.close()

    after_sha = _sha256_file(db_file)
    assert after_sha == baseline_sha
    assert db_file.read_bytes() == baseline_bytes

    snap = get_runtime_safety_snapshot()
    assert snap["counters"]["BLOCKED_HTTP_MUTATION_ATTEMPTS"] == len(mutating_calls)
    assert snap["counters"]["BLOCKED_DB_WRITE_ATTEMPTS"] >= 3
    assert snap["counters"]["UNAUTHORIZED_DB_WRITES"] == 0
    assert snap["counters"]["PAPER_ORDERS_CREATED"] == 0
    assert snap["counters"]["PAPER_ORDERS_FILLED"] == 0
    assert snap["counters"]["PAPER_POSITIONS_CLOSED"] == 0
    assert snap["counters"]["LIVE_EXCHANGE_ORDERS_SENT"] == 0
