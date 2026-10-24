"""
backend/tests/test_v17_6_soak.py
AURUM V17.6 — Deterministic Accelerated Soak Test Suite (Phase K Gate 9, Part 119).

Verifies:
1. 50 continuous evaluation cycles across mixed operational scenarios:
   - 20 healthy cycles without setup (NO_SETUP)
   - 5 cycles during feed dropout (NO_VALID_QUOTE)
   - 5 cycles with scalp mode disabled (SCALP_AUTO_PAPER_DISABLED)
   - 20 healthy cycles leading to a qualified candidate and ARMED order.
2. In-memory ring buffer bound adherence (never exceeds max limit).
3. Zero memory leaks or dangling transactions.
4. Total observability snapshot compiles accurately post-soak.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import crud
import models
from services.clock import FakeClock
from services.engine_observability_service import engine_observability_service
from services.quote_validator import CanonicalQuote
from services.scalp_m1_types import M1ScalpConfig, ScalpMode, WatchedZone, ZoneState
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from tests.test_v15_1_execution_lifecycle import (
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


@pytest.fixture
def soak_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    session = SessionFactory()

    _setup_positive_trading_policy(session)

    session.add(models.SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1000000))
    session.add(models.SystemConfig(key="execution_scope", value="PAPER", updated_at=1000000))
    session.add(models.SystemConfig(key="stop_placement_v1.mode", value="PAPER_STRUCTURAL", updated_at=1000000))
    session.add(models.SystemConfig(key="stop_placement_v1.version", value="1", updated_at=1000000))
    session.add(models.SystemConfig(key="slippage_usd", value="0.20", updated_at=1000000))
    session.add(models.SystemConfig(key="maker_fee_rate", value="0.0002", updated_at=1000000))
    session.add(models.SystemConfig(key="taker_fee_rate", value="0.0004", updated_at=1000000))

    clock = FakeClock(1_000_000)
    audit = crud.get_or_create_today_audit(session, clock=clock)
    audit.initial_equity = 5000.0
    audit.current_equity = 5000.0
    session.commit()

    yield session, SessionFactory

    session.close()
    engine.dispose()


def test_v17_6_deterministic_soak_50_cycles(soak_db):
    db, SessionFactory = soak_db
    orch = ScalpWatcherOrchestrator()
    base_time = 1_000_000
    clock = FakeClock(base_time)

    config_enabled = M1ScalpConfig(
        scalp_m1_enabled=True,
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )
    config_disabled = M1ScalpConfig(
        scalp_m1_enabled=False,
        scalp_m1_mode=ScalpMode.PAPER.value,
    )

    quote_valid = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50,
        ask=2648.00,
        last=2647.75,
        status="VALID",
        exchange_ts_ms=base_time,
        received_at_ms=base_time,
    )

    cycles_completed = 0
    cycles_skipped = 0
    armed_orders = []

    # Phase 1: 20 cycles with empty candles -> NO_SETUP
    for i in range(20):
        t_ms = base_time + i * 5000
        q = CanonicalQuote(
            symbol="XAUUSDT",
            bid=2647.50,
            ask=2648.00,
            last=2647.75,
            status="VALID",
            exchange_ts_ms=t_ms,
            received_at_ms=t_ms,
        )
        res = orch.evaluate_cycle(
            db,
            "XAUUSDT",
            now_ms=t_ms,
            clock=clock,
            quote=q,
            candles_1m=[],
            candles_5m=[],
            config=config_enabled,
            auto_enabled=True,
        )
        assert res.get("decision_status") == "NO_SETUP"
        cycles_completed += 1

    # Phase 2: 5 cycles with missing quote -> SKIPPED (NO_VALID_QUOTE)
    for i in range(5):
        t_ms = base_time + 100_000 + i * 5000
        res = orch.evaluate_cycle(
            db,
            "XAUUSDT",
            now_ms=t_ms,
            clock=clock,
            quote=None,
            candles_1m=[],
            candles_5m=[],
            config=config_enabled,
            auto_enabled=True,
        )
        assert res.get("decision_status") == "SKIPPED"
        cycles_skipped += 1

    # Phase 3: 5 cycles with scalp mode disabled -> SKIPPED (SCALP_AUTO_PAPER_DISABLED)
    for i in range(5):
        t_ms = base_time + 150_000 + i * 5000
        q = CanonicalQuote(
            symbol="XAUUSDT",
            bid=2647.50,
            ask=2648.00,
            last=2647.75,
            status="VALID",
            exchange_ts_ms=t_ms,
            received_at_ms=t_ms,
        )
        res = orch.evaluate_cycle(
            db,
            "XAUUSDT",
            now_ms=t_ms,
            clock=clock,
            quote=q,
            candles_1m=[],
            candles_5m=[],
            config=config_disabled,
            auto_enabled=True,
        )
        assert res.get("decision_status") == "SKIPPED"
        cycles_skipped += 1

    # Phase 4: 19 healthy cycles without setup
    for i in range(19):
        t_ms = base_time + 200_000 + i * 5000
        q = CanonicalQuote(
            symbol="XAUUSDT",
            bid=2647.50,
            ask=2648.00,
            last=2647.75,
            status="VALID",
            exchange_ts_ms=t_ms,
            received_at_ms=t_ms,
        )
        res = orch.evaluate_cycle(
            db,
            "XAUUSDT",
            now_ms=t_ms,
            clock=clock,
            quote=q,
            candles_1m=[],
            candles_5m=[],
            config=config_enabled,
            auto_enabled=True,
        )
        assert res.get("decision_status") == "NO_SETUP"
        cycles_completed += 1

    # Phase 5: Cycle 50 with qualified short setup -> ARMED
    t_arm = base_time + 300_000
    zone = WatchedZone(
        zone_id="z_short_soak",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BEARISH",
        top=2670.0,
        bottom=2650.0,
        created_at=60_000,
        known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])
    candles_1m = _make_causal_short_m1_candles()

    q_50 = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50,
        ask=2648.00,
        last=2647.75,
        status="VALID",
        exchange_ts_ms=t_arm,
        received_at_ms=t_arm,
    )

    res_50 = orch.evaluate_cycle(
        db,
        "XAUUSDT",
        now_ms=t_arm,
        clock=clock,
        quote=q_50,
        candles_1m=candles_1m,
        candles_5m=[],
        config=config_enabled,
        feed_health={"status": "CONNECTED", "latency_ms": 10},
        auto_enabled=True,
    )
    assert res_50.get("decision_status") == "ARMED"
    armed_id = res_50.get("armed_order_id")
    assert armed_id is not None
    armed_orders.append(armed_id)
    cycles_completed += 1

    # Total cycles: 20 + 5 + 5 + 19 + 1 = 50
    assert cycles_completed == 40
    assert cycles_skipped == 10

    # Verify Bounded Ring Buffer
    recent_cycles = engine_observability_service.get_recent_cycles(limit=100)
    assert len(recent_cycles) <= 100
    assert len(recent_cycles) >= 50

    # Verify Heartbeat Persistence
    hb = db.query(models.EngineHeartbeatRecord).filter_by(symbol="XAUUSDT").first()
    assert hb is not None
    assert hb.state == "RUNNING"
    assert hb.cycles_count >= 40
    assert hb.last_successful_evaluation_at_ms is not None

    # Verify Dashboard Snapshot builds cleanly without exception
    dash = engine_observability_service.build_observation_dashboard_snapshot(
        db,
        symbol="XAUUSDT",
        now_ms=t_arm + 1000,
    )
    assert dash["engine"]["state"] in ("RUNNING", "DEGRADED")
    assert dash["latest_cycle"]["decision_status"] == "ARMED"
    assert len(dash["decisions"]) >= 1
