"""
test_v15_0_scalp_orchestrator.py
V15.0 - Unit & Integration tests for Autonomous Scalp Watcher Orchestrator (PHẦN 021, 024, 025, 054 - 060, 091 - 093).
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services.scalp_m1_types import M1ScalpConfig, ModelIdentifier, OpportunityStage, ScalpMode, WatchedZone, ZoneState
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@dataclass
class CandleStub:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 100.0
    is_closed: bool = True

    @property
    def open_time(self) -> int:
        return self.timestamp


def test_orchestrator_disabled_when_off(db_session):
    """PHẦN 021, 022: Orchestrator returns DISABLED when scalp mode is OFF and daily NY research disabled."""
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.OFF.value, daily_ny_research_enabled=False)
    quote = {"bid": 2600.0, "ask": 2600.5, "is_valid": True}

    res = orch.evaluate_cycle(db_session, "XAUUSDT", now_ms=1_000_000, quote=quote, config=cfg)
    assert res["status"] == "DISABLED"
    assert res["zones_count"] == 0
    assert res["candidates_count"] == 0


def test_orchestrator_shadow_mode_isolation(db_session):
    """PHẦN 024, 092: SHADOW mode discovers setup, records PLAN_VALID funnel, but NEVER creates live/paper orders."""
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.SHADOW.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )

    # Pre-seed an active Bullish FVG zone
    zone = WatchedZone(
        zone_id="z_shadow_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    # Build 20 M1 candles with reversal pattern
    candles_1m = [
        CandleStub(60_000, 2625.0, 2626.0, 2620.0, 2622.0),
        CandleStub(120_000, 2622.0, 2624.0, 2619.0, 2620.0),
        CandleStub(180_000, 2620.0, 2621.0, 2616.0, 2617.0),
        CandleStub(240_000, 2617.0, 2619.0, 2615.0, 2616.0),
        CandleStub(300_000, 2616.0, 2617.0, 2613.0, 2614.0),
        CandleStub(360_000, 2614.0, 2615.0, 2612.0, 2613.0),
        CandleStub(420_000, 2613.0, 2618.0, 2612.0, 2615.0), # Swing High @ 2618
        CandleStub(480_000, 2615.0, 2616.0, 2610.0, 2611.0),
        CandleStub(540_000, 2611.0, 2612.0, 2607.0, 2608.0),
        CandleStub(600_000, 2608.0, 2609.0, 2605.0, 2606.0),
        CandleStub(660_000, 2606.0, 2607.0, 2603.0, 2604.0),
        CandleStub(720_000, 2604.0, 2606.0, 2602.0, 2605.0), # Sweep Low @ 2602 inside zone
        CandleStub(780_000, 2605.0, 2609.0, 2604.0, 2608.0),
        CandleStub(840_000, 2608.0, 2611.0, 2607.0, 2610.0),
        CandleStub(900_000, 2610.0, 2614.0, 2609.0, 2612.0),
        CandleStub(960_000, 2612.0, 2626.0, 2611.0, 2625.0), # Displacement CHOCH break above 2618!
        CandleStub(1020_000, 2625.0, 2626.0, 2622.0, 2624.0),
        CandleStub(1080_000, 2624.0, 2625.0, 2621.0, 2623.0),
        CandleStub(1140_000, 2623.0, 2624.0, 2620.0, 2622.0),
        CandleStub(1200_000, 2622.0, 2623.0, 2621.0, 2622.0),
    ]

    now_ms = 1300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    res = orch.evaluate_cycle(
        db_session, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=cfg,
    )

    assert res["status"] == "OK"
    assert res["armed_order_id"] is None  # NO order armed in shadow mode!

    # Verify zero PaperOrders created
    orders_count = db_session.query(models.PaperOrder).count()
    assert orders_count == 0

    # Verify Opportunity Funnel transition recorded PLAN_VALID
    records = db_session.query(models.OpportunityFunnelRecord).all()
    stages = [r.stage for r in records]
    assert OpportunityStage.TRIGGER_CONFIRMED.value in stages
    assert OpportunityStage.PLAN_VALID.value in stages


def test_orchestrator_near_zone_hysteresis_and_telegram_dedup(db_session):
    """PHẦN 039, 070: Near zone proximity triggers outbox entry and dedups on oscillation."""
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.SHADOW.value,
        near_zone_enter_atr=0.5,
        near_zone_exit_atr=0.8,
    )

    # Create zone at [2600, 2610]
    zone = WatchedZone(
        zone_id="z_hysteresis", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2610.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WATCHING.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    now_ms = 1_000_000
    # Price is 2610.8 (within 0.5 ATR of top 2610 with ATR=2.0)
    quote = {"bid": 2610.8, "ask": 2611.2, "is_valid": True}

    # Cycle 1: Price enters NEAR
    res1 = orch.evaluate_cycle(
        db_session, "XAUUSDT", now_ms=now_ms, quote=quote,
        candles_1m=[], candles_5m=[], candles_15m=[], config=cfg
    )

    # Verify Telegram notification outbox row queued
    outbox_rows = db_session.query(models.NotificationOutbox).filter(models.NotificationOutbox.message_type == "NEAR_ENTRY").all()
    assert len(outbox_rows) == 1

    # Cycle 2: Same price 10 seconds later -> should NOT create duplicate outbox row
    res2 = orch.evaluate_cycle(
        db_session, "XAUUSDT", now_ms=now_ms + 10_000, quote=quote,
        candles_1m=[], candles_5m=[], candles_15m=[], config=cfg
    )
    outbox_rows2 = db_session.query(models.NotificationOutbox).filter(models.NotificationOutbox.message_type == "NEAR_ENTRY").all()
    assert len(outbox_rows2) == 1  # Dedup preserved!
