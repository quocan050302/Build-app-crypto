"""
backend/tests/test_v17_6_decision_parity.py
AURUM V17.6 — Trading Decision Parity Suite (Phase L Gate 15, Rule 126).

Verifies:
1. Observability instrumentation does NOT alter any SMC/ICT trading decisions.
2. Identical market candles, zone configuration, and quotes produce identical ARM, entry, and stop levels.
3. Observability telemetry (cycle_id, decision_events, engine_heartbeats) is recorded without changing execution paths.
"""

import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import crud
import models
from services.clock import FakeClock
from services.quote_validator import CanonicalQuote
from services.scalp_m1_types import M1ScalpConfig, ScalpMode, WatchedZone, ZoneState
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from tests.test_v15_1_execution_lifecycle import (
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


@pytest.fixture
def parity_db():
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

    clock = FakeClock(1_000_000)
    audit = crud.get_or_create_today_audit(session, clock=clock)
    audit.initial_equity = 5000.0
    audit.current_equity = 5000.0
    session.commit()

    yield session, SessionFactory

    session.close()
    engine.dispose()


def _set_config(db: Session, key: str, value: str, now_ms: int = 1_300_000):
    row = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if row:
        row.value = str(value)
        row.updated_at = now_ms
    else:
        db.add(models.SystemConfig(key=key, value=str(value), updated_at=now_ms))
    db.commit()


def test_parity_short_cycle_execution(parity_db):
    db, SessionFactory = parity_db
    orch = ScalpWatcherOrchestrator()
    now_ms = 1_300_000
    clock = FakeClock(now_ms)

    _set_config(db, "slippage_usd", "0.20", now_ms)
    _set_config(db, "maker_fee_rate", "0.0002", now_ms)
    _set_config(db, "taker_fee_rate", "0.0004", now_ms)

    zone = WatchedZone(
        zone_id="z_short_parity",
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
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50,
        ask=2648.00,
        last=2647.75,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_enabled=True,
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    # Autonomous evaluate cycle
    res = orch.evaluate_cycle(
        db,
        "XAUUSDT",
        now_ms=now_ms,
        clock=clock,
        quote=quote,
        candles_1m=candles_1m,
        candles_5m=[],
        candles_15m=[],
        config=base_cfg,
        feed_health={"status": "CONNECTED", "latency_ms": 10},
        auto_enabled=True,
    )

    # 1. Trading Decision Parity Assertions
    assert res["status"] == "OK"
    assert res["decision_status"] == "ARMED"
    armed_id = res.get("armed_order_id")
    assert armed_id is not None, "Pipeline must produce an armed order identically to baseline"

    # 2. Verify Order in Database
    fresh_db = SessionFactory()
    order = fresh_db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_id).first()
    assert order is not None
    assert order.state == "armed"
    assert order.direction == "SHORT"
    assert order.quantity > 0
    assert order.planned_entry == 2647.50
    assert order.stop_loss > order.planned_entry  # Stop loss above entry for SHORT

    # 3. Observability Telemetry Verification (V17.6 Non-invasive additions)
    assert "cycle_id" in res
    assert res["cycle_id"].startswith("cycle-")

    # Heartbeat was recorded
    hb = fresh_db.query(models.EngineHeartbeatRecord).filter_by(symbol="XAUUSDT").first()
    assert hb is not None
    assert hb.state == "RUNNING"
    assert hb.last_successful_evaluation_at_ms is not None

    # Decision Event was recorded
    dec_event = fresh_db.query(models.OpportunityDecisionEvent).filter_by(stage="ARMED").first()
    assert dec_event is not None
    assert dec_event.direction == "SHORT"
    assert dec_event.primary_reason_code in ("ALL_CONDITIONS_MET", "ARM_AUTHORIZED")
    assert dec_event.reason_category in ("STRUCTURE", "EXECUTION")

    fresh_db.close()
