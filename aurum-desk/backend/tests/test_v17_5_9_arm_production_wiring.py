"""
backend/tests/test_v17_5_9_arm_production_wiring.py
AURUM V17.5.9 — Production ARM Invariants Wiring & Gate Matrix Suite (D03, Section 05 & Section 09 Parts 073-074).

Audits and proves:
1. Production invocation: _arm_paper_candidate() actively calls validate_arm_invariants() before persisting any PaperOrder.
2. Invariants enforcement:
   - A01: Valid candidate reaches ARM
   - A02: Invalid direction cannot ARM (INVALID_DIRECTION)
   - A03: Missing price cannot ARM (INVALID_PRICE_GEOMETRY)
   - A04: NaN SL cannot ARM (INVALID_PRICE_GEOMETRY)
   - A05: Invalid quantity cannot ARM (INVALID_QUANTITY)
   - A06: Expired candidate cannot ARM (TRIGGER_EXPIRED)
   - A07: Stale quote cannot ARM (QUOTE_INVALID_OR_STALE)
   - A08: Daily cap cannot be bypassed (DAILY_CAP_REACHED)
   - A09: Existing active position blocks where required
   - A10: Same candidate cannot ARM twice (idempotency)
   - A11: Invalid structural evidence cannot ARM
   - A12: Invalid costs cannot ARM (INVALID_COST_CONFIGURATION)
"""

import json
from unittest.mock import patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import crud
import models
from services.clock import FakeClock
from services.quote_validator import CanonicalQuote
from services.scalp_m1_types import M1ScalpConfig, ScalpMode, WatchedZone, ZoneState
from services.scalp_watcher_orchestrator import (
    ScalpWatcherOrchestrator,
    validate_arm_invariants,
)
from tests.test_v15_1_execution_lifecycle import (
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


@pytest.fixture
def isolated_arm_db():
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


def _set_config(db: Session, key: str, value: str, now_ms: int = 1_000_000):
    row = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if row:
        row.value = str(value)
        row.updated_at = now_ms
    else:
        db.add(models.SystemConfig(key=key, value=str(value), updated_at=now_ms))
    db.commit()


def test_a00_validate_arm_invariants_is_actively_invoked_in_production(isolated_arm_db):
    """Part 074: Spy on validate_arm_invariants to prove it is called by _arm_paper_candidate in production flow."""
    db, _ = isolated_arm_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()

    _set_config(db, "slippage_usd", "0.20", now_ms)
    _set_config(db, "maker_fee_rate", "0.0002", now_ms)
    _set_config(db, "taker_fee_rate", "0.0004", now_ms)

    zone = WatchedZone(
        zone_id="z_spy", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    fresh_cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50,
        ask=2648.00,
        last=2647.75,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    # Use patch.spy equivalent by wrapping validate_arm_invariants
    call_log = []
    real_fn = validate_arm_invariants

    def spy_validate_arm_invariants(*args, **kwargs):
        res = real_fn(*args, **kwargs)
        call_log.append((kwargs.get("now_ms"), res))
        return res

    with patch("services.scalp_watcher_orchestrator.validate_arm_invariants", side_effect=spy_validate_arm_invariants):
        res = orch.evaluate_cycle(
            db, "XAUUSDT",
            now_ms=now_ms, clock=clock, quote=fresh_cq,
            candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    assert len(call_log) >= 1, "validate_arm_invariants MUST be invoked during production ARM pipeline"
    assert call_log[0][1][0] is True, "validate_arm_invariants passed for valid candidate"
    assert res.get("armed_order_id") is not None


def test_a01_valid_candidate_reaches_arm(isolated_arm_db):
    """A01: Valid candidate passes all barriers and creates 1 armed PaperOrder."""
    db, _ = isolated_arm_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()

    zone = WatchedZone(
        zone_id="z_a01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50, ask=2648.00, last=2647.75,
        status="VALID", exchange_ts_ms=now_ms, received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    res = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res.get("armed_order_id") is not None
    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.state == "armed"


def test_a02_invalid_direction_cannot_arm():
    """A02: Unsupported direction cannot pass validate_arm_invariants."""
    class DummyCalc:
        is_valid = True
        can_execute = True
        quantity = 0.5
        stop_loss = 2660.0

    class InvalidDirCandidate:
        is_valid = True
        direction = "SIDEWAYS"
        planned_entry = 2647.0
        take_profit = 2635.0
        stop_loss = 2660.0

    class DummyQuote:
        is_valid = True
        is_stale = False

    valid, reason = validate_arm_invariants(
        candidate=InvalidDirCandidate(),
        calculation=DummyCalc(),
        quote=DummyQuote(),
        now_ms=10000,
    )
    assert valid is False
    assert reason == "INVALID_DIRECTION"


def test_a03_missing_price_cannot_arm():
    """A03: Missing planned_entry or stop_loss cannot ARM."""
    class DummyCalc:
        is_valid = True
        can_execute = True
        quantity = 0.5
        stop_loss = 2660.0

    class MissingPriceCandidate:
        is_valid = True
        direction = "SHORT"
        planned_entry = None
        take_profit = 2635.0
        stop_loss = 2660.0

    class DummyQuote:
        is_valid = True
        is_stale = False

    valid, reason = validate_arm_invariants(
        candidate=MissingPriceCandidate(),
        calculation=DummyCalc(),
        quote=DummyQuote(),
        now_ms=10000,
    )
    assert valid is False
    assert reason == "INVALID_PRICE_GEOMETRY"


def test_a04_nan_sl_cannot_arm():
    """A04: NaN stop loss cannot ARM."""
    class DummyCalc:
        is_valid = True
        can_execute = True
        quantity = 0.5
        stop_loss = float("nan")

    class NaNSlCandidate:
        is_valid = True
        direction = "SHORT"
        planned_entry = 2647.0
        take_profit = 2635.0
        stop_loss = float("nan")

    class DummyQuote:
        is_valid = True
        is_stale = False

    valid, reason = validate_arm_invariants(
        candidate=NaNSlCandidate(),
        calculation=DummyCalc(),
        quote=DummyQuote(),
        now_ms=10000,
    )
    assert valid is False
    assert reason == "INVALID_PRICE_GEOMETRY"


def test_a05_invalid_quantity_cannot_arm():
    """A05: Non-positive or infinite quantity cannot ARM."""
    class DummyCalc:
        is_valid = True
        can_execute = True
        quantity = -0.1
        stop_loss = 2660.0

    class DummyCandidate:
        is_valid = True
        direction = "SHORT"
        planned_entry = 2647.0
        take_profit = 2635.0
        stop_loss = 2660.0

    class DummyQuote:
        is_valid = True
        is_stale = False

    valid, reason = validate_arm_invariants(
        candidate=DummyCandidate(),
        calculation=DummyCalc(),
        quote=DummyQuote(),
        now_ms=10000,
    )
    assert valid is False
    assert reason == "INVALID_QUANTITY"


def test_a06_expired_candidate_cannot_arm():
    """A06: Candidate with trigger expired before now_ms cannot ARM."""
    class DummyCalc:
        is_valid = True
        can_execute = True
        quantity = 0.5
        stop_loss = 2660.0

    class DummyCandidate:
        is_valid = True
        direction = "SHORT"
        planned_entry = 2647.0
        take_profit = 2635.0
        stop_loss = 2660.0

    class DummyQuote:
        is_valid = True
        is_stale = False

    valid, reason = validate_arm_invariants(
        candidate=DummyCandidate(),
        calculation=DummyCalc(),
        quote=DummyQuote(),
        now_ms=10500,
        expires_at_ms=10000,
    )
    assert valid is False
    assert reason == "TRIGGER_EXPIRED"


def test_a07_stale_quote_cannot_arm(isolated_arm_db):
    """A07: Stale quote blocks ARM, zero orders created."""
    db, _ = isolated_arm_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()

    zone = WatchedZone(
        zone_id="z_a07", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    stale_quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50, ask=2648.00, last=2647.75,
        status="VALID", exchange_ts_ms=now_ms - 500_000, received_at_ms=now_ms - 500_000,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    res = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=stale_quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res.get("armed_order_id") is None
    assert db.query(models.PaperOrder).count() == 0


def test_a08_daily_cap_cannot_be_bypassed(isolated_arm_db):
    """A08: When daily cap is reached, ARM is blocked with DAILY_CAP_REACHED."""
    db, _ = isolated_arm_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()

    # Pre-populate audit with 3 fills (max daily cap = 3)
    audit = crud.get_or_create_today_audit(db, clock=clock)
    audit.fills_count = 3
    db.commit()

    zone = WatchedZone(
        zone_id="z_a08", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50, ask=2648.00, last=2647.75,
        status="VALID", exchange_ts_ms=now_ms, received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
        daily_max_cap=3,
    )

    res = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res.get("armed_order_id") is None
    assert db.query(models.PaperOrder).count() == 0


def test_a09_existing_armed_position_blocks_further_arm_when_at_cap(isolated_arm_db):
    """A09: Active armed order counts towards capacity limit."""
    class DummyCalc:
        is_valid = True
        can_execute = True
        quantity = 0.5
        stop_loss = 2660.0

    class DummyCandidate:
        is_valid = True
        direction = "SHORT"
        planned_entry = 2647.0
        take_profit = 2635.0
        stop_loss = 2660.0

    class DummyQuote:
        is_valid = True
        is_stale = False

    # 1 fill + 2 active armed = 3 >= cap(3)
    valid, reason = validate_arm_invariants(
        candidate=DummyCandidate(),
        calculation=DummyCalc(),
        quote=DummyQuote(),
        now_ms=10000,
        daily_fills_count=1,
        active_armed_count=2,
        daily_max_cap=3,
    )
    assert valid is False
    assert reason == "DAILY_CAP_REACHED"


def test_a10_same_candidate_cannot_arm_twice(isolated_arm_db):
    """A10: Idempotency check prevents duplicate order persistence for same candidate."""
    db, _ = isolated_arm_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()

    zone = WatchedZone(
        zone_id="z_a10", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50, ask=2648.00, last=2647.75,
        status="VALID", exchange_ts_ms=now_ms, received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    res1 = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res1.get("armed_order_id") is not None
    assert db.query(models.PaperOrder).count() == 1

    # Second evaluation of same candidate does not create another order
    res2 = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms + 1000, clock=FakeClock(now_ms + 1000), quote=quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert db.query(models.PaperOrder).count() == 1


def test_a11_stop_plan_mismatch_cannot_arm():
    """A11: Candidate SL and Calculator SL mismatch fails closed with STOP_PLAN_MISMATCH."""
    class CalcWithMismatchSl:
        is_valid = True
        can_execute = True
        quantity = 0.5
        stop_loss = 2670.0  # differs from candidate SL 2660.0

    class DummyCandidate:
        is_valid = True
        direction = "SHORT"
        planned_entry = 2647.0
        take_profit = 2635.0
        stop_loss = 2660.0

    class DummyQuote:
        is_valid = True
        is_stale = False

    valid, reason = validate_arm_invariants(
        candidate=DummyCandidate(),
        calculation=CalcWithMismatchSl(),
        quote=DummyQuote(),
        now_ms=10000,
    )
    assert valid is False
    assert reason == "STOP_PLAN_MISMATCH"


def test_a12_invalid_cost_configuration_cannot_arm(isolated_arm_db):
    """A12: When cost settings are corrupt in DB, evaluate_cycle fails closed and does not arm."""
    db, _ = isolated_arm_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()

    # Corrupt slippage_usd with non-numeric string
    _set_config(db, "slippage_usd", "CORRUPT_NOT_FLOAT", now_ms)

    zone = WatchedZone(
        zone_id="z_a12", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50, ask=2648.00, last=2647.75,
        status="VALID", exchange_ts_ms=now_ms, received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    res = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res.get("armed_order_id") is None
    assert db.query(models.PaperOrder).count() == 0
