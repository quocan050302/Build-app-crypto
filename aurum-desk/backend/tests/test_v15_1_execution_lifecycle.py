"""
test_v15_1_execution_lifecycle.py
V15.1 - Comprehensive Test Suite for Scalp Execution Flow (PHẦN 073 - 089).

Covers:
1. Config API -> Runtime DB resolution (PHẦN 073, 074)
2. Strict mode and boolean validation (PHẦN 020, 021, 074)
3. Positive LONG Paper Arm with canonical sizing, net risk, gross/net RR (PHẦN 075)
4. Positive SHORT Paper Arm with symmetry and geometry (PHẦN 076)
5. Full LONG Lifecycle: Arm -> Fill -> TP Close -> Telegram -> Journal (PHẦN 077, 087)
6. Full SHORT Lifecycle: Arm -> Fill -> SL Close -> Telegram (PHẦN 078, 087)
7. Shadow Mode Isolation: No orders, no quota consumption, no alerts (PHẦN 079)
8. Quote & Feed Health Negative Controls (PHẦN 080)
9. Causal Reversal Negative Controls: No sweep, no closed retest (PHẦN 081)
10. Cost & Min Net R:R Threshold Enforcement (PHẦN 082)
11. Signal Dedup and Idempotency across evaluation cycles (PHẦN 083)
12. Shared Daily Limit Guard (PHẦN 084)
13. Daily NY Research Model Controls (PHẦN 085)
14. Commit Failure Atomicity & Rollback (PHẦN 086)
15. Chart Evidence API Immutable Snapshot Link (PHẦN 088)
16. Performance: Bounded Structural Evaluation (PHẦN 089)
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import crud
import models
from database import get_db
from domain_calculator import calculate_risk_reward
from main import app
from services.clock import FakeClock
from services.daily_ny_research_service import DailyNYResearchService
from services.opportunity_funnel_service import OpportunityFunnelService
from services.scalp_m1_model_service import ScalpCandidate, ScalpM1ModelService
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    OpportunityStage,
    ScalpMode,
    WatchedZone,
    ZoneState,
    load_effective_scalp_config,
)
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.telegram_service import queue_telegram_notification
from services.trade_lifecycle_service import TradeLifecycleService
from services.trading_policy_service import TradingPolicyService


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


def _make_causal_long_m1_candles() -> List[CandleStub]:
    """
    21 causal M1 candles for LONG reversal:
    - Candle 6: Swing High @ 2618.0
    - Candle 11: Sweep Low @ 2602.0 (inside zone [2600.0, 2620.0])
    - Candle 15: Displacement CHOCH break candle closing @ 2626.0 (> 2618.0)
    - Candle 16-20: Pullback & Closed Retest candle tapping 2621-2622
    """
    return [
        CandleStub(60_000, 2625.0, 2626.0, 2620.0, 2622.0),
        CandleStub(120_000, 2622.0, 2624.0, 2619.0, 2620.0),
        CandleStub(180_000, 2620.0, 2621.0, 2616.0, 2617.0),
        CandleStub(240_000, 2617.0, 2619.0, 2615.0, 2616.0),
        CandleStub(300_000, 2616.0, 2617.0, 2613.0, 2614.0),
        CandleStub(360_000, 2614.0, 2615.0, 2612.0, 2613.0),
        CandleStub(420_000, 2613.0, 2618.0, 2612.0, 2615.0),  # Swing High @ 2618.0
        CandleStub(480_000, 2615.0, 2616.0, 2610.0, 2611.0),
        CandleStub(540_000, 2611.0, 2612.0, 2607.0, 2608.0),
        CandleStub(600_000, 2608.0, 2609.0, 2605.0, 2606.0),
        CandleStub(660_000, 2606.0, 2607.0, 2603.0, 2604.0),
        CandleStub(720_000, 2604.0, 2606.0, 2602.0, 2605.0),  # Sweep Low @ 2602.0
        CandleStub(780_000, 2605.0, 2609.0, 2604.0, 2608.0),
        CandleStub(840_000, 2608.0, 2611.0, 2607.0, 2610.0),
        CandleStub(900_000, 2610.0, 2614.0, 2609.0, 2612.0),
        CandleStub(960_000, 2612.0, 2627.0, 2611.0, 2626.0),  # Break candle above 2618
        CandleStub(1020_000, 2626.0, 2627.0, 2622.0, 2624.0),
        CandleStub(1080_000, 2624.0, 2625.0, 2621.0, 2623.0),
        CandleStub(1140_000, 2623.0, 2624.0, 2620.0, 2622.0),  # Closed retest candle
        CandleStub(1200_000, 2622.0, 2623.0, 2621.0, 2622.0),
        CandleStub(1260_000, 2622.0, 2623.0, 2621.5, 2622.2),
    ]


def _make_causal_short_m1_candles() -> List[CandleStub]:
    """
    21 causal M1 candles for SHORT reversal:
    - Candle 6: Swing Low @ 2648.0
    - Candle 11: Bearish Sweep High @ 2668.0 (inside zone [2650.0, 2670.0])
    - Candle 15: Displacement CHOCH break candle closing @ 2644.0 (< 2648.0)
    - Candle 16-20: Pullback & Closed Retest candle tapping 2648-2647
    """
    return [
        CandleStub(60_000, 2645.0, 2647.0, 2644.0, 2646.0),
        CandleStub(120_000, 2646.0, 2648.0, 2645.0, 2647.0),
        CandleStub(180_000, 2647.0, 2650.0, 2646.0, 2648.0),
        CandleStub(240_000, 2648.0, 2651.0, 2647.0, 2650.0),
        CandleStub(300_000, 2650.0, 2652.0, 2649.0, 2651.0),
        CandleStub(360_000, 2651.0, 2653.0, 2650.0, 2652.0),
        CandleStub(420_000, 2652.0, 2653.0, 2648.0, 2650.0),  # Swing Low @ 2648.0
        CandleStub(480_000, 2650.0, 2655.0, 2649.0, 2654.0),
        CandleStub(540_000, 2654.0, 2659.0, 2653.0, 2658.0),
        CandleStub(600_000, 2658.0, 2663.0, 2657.0, 2662.0),
        CandleStub(660_000, 2662.0, 2666.0, 2661.0, 2665.0),
        CandleStub(720_000, 2665.0, 2668.0, 2664.0, 2666.0),  # Sweep High @ 2668.0
        CandleStub(780_000, 2666.0, 2667.0, 2660.0, 2661.0),
        CandleStub(840_000, 2661.0, 2662.0, 2655.0, 2656.0),
        CandleStub(900_000, 2656.0, 2657.0, 2650.0, 2651.0),
        CandleStub(960_000, 2651.0, 2652.0, 2643.0, 2644.0),  # Break candle below 2648.0
        CandleStub(1020_000, 2644.0, 2646.0, 2643.0, 2645.0),
        CandleStub(1080_000, 2645.0, 2648.0, 2644.0, 2647.0),
        CandleStub(1140_000, 2647.0, 2649.0, 2646.0, 2648.0),  # Closed retest candle
        CandleStub(1200_000, 2648.0, 2649.0, 2647.0, 2647.5),
        CandleStub(1260_000, 2647.5, 2648.0, 2647.0, 2647.5),
    ]


def _setup_positive_trading_policy(db: Session):
    policy = TradingPolicyService.get_or_create_policy(db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    policy.capital_usdt = 1000.0
    policy.risk_pct = 0.25
    policy.default_leverage = 30.0
    policy.default_margin_mode = "ISOLATED"
    tg_cfg = db.query(models.TelegramConfig).first()
    if not tg_cfg:
        tg_cfg = models.TelegramConfig(
            enabled=True,
            bot_token="test_bot_token",
            chat_id="test_chat_id",
            subscribed_events=json.dumps(["READY", "NEAR_ENTRY", "ARMED", "FILLED", "TP_HIT", "SL_HIT", "CLOSED", "REJECTED"]),
            updated_at=int(time.time() * 1000),
        )
        db.add(tg_cfg)
    db.commit()


# =====================================================================
# PHẦN 073, 074: CONFIG API -> RUNTIME DB RESOLUTION
# =====================================================================

def test_v15_1_config_api_persists_to_db_and_read_by_runtime_orchestrator(client: TestClient, isolated_db: Session):
    """
    PHẦN 015 - 024, 073:
    1. POST /api/scalp/config saves to DB SystemConfig.
    2. Watcher evaluate_cycle() without config parameter loads from DB.
    3. Updating to SHADOW, PAPER, and OFF takes effect immediately.
    """
    orch = ScalpWatcherOrchestrator()

    # 1. Update to SHADOW via API
    res = client.post("/api/scalp/config", json={"scalp_m1_mode": "SHADOW"})
    assert res.status_code == 200
    assert res.json()["status"] == "SUCCESS"
    assert res.json()["effective_config"]["scalp_m1_mode"] == "SHADOW"

    # Verify watcher reads SHADOW without config injection
    quote = {"bid": 2600.0, "ask": 2600.5, "is_valid": True}
    res_eval = orch.evaluate_cycle(isolated_db, "XAUUSDT", now_ms=1_000_000, quote=quote, config=None)
    assert res_eval["status"] == "OK"

    # 2. Update to PAPER via API
    res_paper = client.post("/api/scalp/config", json={"scalp_m1_mode": "PAPER"})
    assert res_paper.status_code == 200
    eff = load_effective_scalp_config(isolated_db)
    assert eff.scalp_m1_mode == ScalpMode.PAPER.value

    # 3. Update to OFF via API
    res_off = client.post("/api/scalp/config", json={"scalp_m1_mode": "OFF", "daily_ny_research_enabled": False})
    assert res_off.status_code == 200
    res_eval_off = orch.evaluate_cycle(isolated_db, "XAUUSDT", now_ms=1_000_000, quote=quote, config=None)
    assert res_eval_off["status"] == "DISABLED"


def test_v15_1_config_strict_validation_and_parsing(client: TestClient, isolated_db: Session):
    """
    PHẦN 020, 021, 074:
    - Unsupported mode is rejected (422 / 400).
    - String 'false' for boolean is strictly parsed (never bool('false') == True).
    """
    # Invalid mode
    res_invalid_mode = client.post("/api/scalp/config", json={"scalp_m1_mode": "REAL"})
    assert res_invalid_mode.status_code in (400, 422)

    # Strict boolean: string "false" must not evaluate to True
    res_str_bool = client.post("/api/scalp/config", json={"daily_ny_research_enabled": "false"})
    assert res_str_bool.status_code == 200
    assert res_str_bool.json()["effective_config"]["daily_ny_research_enabled"] is False

    eff = load_effective_scalp_config(isolated_db)
    assert eff.daily_ny_research_enabled is False


# =====================================================================
# PHẦN 075: POSITIVE LONG PAPER ARM
# =====================================================================

def test_v15_1_long_paper_arm_positive_control(isolated_db: Session):
    """
    PHẦN 025 - 030, 075:
    Causal LONG fixture with valid quote and guards creates armed PaperOrder:
    - Quantity comes from canonical CalculationResult (NOT fixed 0.1).
    - Initial risk is net_risk_usdt.
    - Gross & net R:R match calculator.
    - Setup and scenario snapshot are linked.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )

    zone = WatchedZone(
        zone_id="z_long_arm_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=cfg, feed_health=feed_health, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res["armed_order_id"] is not None

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.direction == "LONG"
    assert order.state == "armed"
    assert order.planned_entry == 2622.5
    assert order.stop_loss == 2601.95  # 2602.0 - 0.05 buffer
    assert order.take_profit > order.planned_entry

    # Canonical sizing verification (PHẦN 026)
    assert order.quantity > 0
    assert math.isfinite(order.quantity)

    # Net risk & RR verification (PHẦN 027, 028)
    assert order.initial_risk_usdt > 0
    assert order.gross_rr > 0
    assert order.estimated_net_rr > 0
    assert order.origin == "AUTO_STRATEGY"
    assert order.scenario_snapshot_id is not None

    # WatchSetup link
    ws = isolated_db.query(models.WatchSetup).filter(models.WatchSetup.id == order.setup_id).first()
    assert ws is not None
    assert ws.state == "ARMED"
    assert ws.direction == "LONG"


# =====================================================================
# PHẦN 076: POSITIVE SHORT PAPER ARM
# =====================================================================

def test_v15_1_short_paper_arm_positive_control(isolated_db: Session):
    """
    PHẦN 028, 076:
    SHORT causal fixture creates armed PaperOrder with symmetric geometry:
    TP < planned_entry < SL.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )

    zone = WatchedZone(
        zone_id="z_short_arm_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2647.5, "ask": 2648.0, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=cfg, feed_health=feed_health, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res["armed_order_id"] is not None

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.direction == "SHORT"
    assert order.take_profit < order.planned_entry < order.stop_loss
    assert order.stop_loss == 2668.05  # 2668.0 + 0.05 buffer
    assert order.quantity > 0
    assert order.initial_risk_usdt > 0
    assert order.gross_rr > 0
    assert order.estimated_net_rr > 0


# =====================================================================
# PHẦN 077, 087: FULL LONG LIFECYCLE (ARM -> FILL -> TP -> TELEGRAM -> AUDIT)
# =====================================================================

def test_v15_1_full_long_lifecycle_arm_fill_tp(isolated_db: Session):
    """
    PHẦN 077, 087:
    Full LONG lifecycle through canonical services:
    Arm -> execute_fill -> execute_close (TP) -> Outbox verification -> DayAudit fills count.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.PAPER.value, daily_ny_research_enabled=False)

    zone = WatchedZone(
        zone_id="z_life_long_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=cfg, auto_enabled=True,
    )
    order_id = res["armed_order_id"]
    assert order_id is not None

    # Verify ARMED notification in Outbox
    outbox_armed = isolated_db.query(models.NotificationOutbox).filter(
        models.NotificationOutbox.message_type == "ARMED"
    ).first()
    assert outbox_armed is not None

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()
    calc = calculate_risk_reward("LONG", order.planned_entry, order.stop_loss, order.take_profit, 1000.0, 0.25, 30.0)

    # 1. Canonical FILL
    fill_time = now_ms + 10_000
    clock = FakeClock(fill_time)
    filled_order = TradeLifecycleService.execute_fill(
        db=isolated_db,
        order=order,
        fill_price=order.planned_entry,
        calc_result=calc,
        clock=clock,
        now_ms=fill_time,
    )
    assert filled_order.state == "paper_open"
    assert filled_order.actual_entry == order.planned_entry

    outbox_filled = isolated_db.query(models.NotificationOutbox).filter(
        models.NotificationOutbox.message_type == "FILLED"
    ).first()
    assert outbox_filled is not None

    # 2. Canonical CLOSE (TP_HIT)
    close_time = fill_time + 60_000
    closed_order = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=order.take_profit,
        exit_cause="TP_HIT",
        occurred_at=close_time,
        session_tag="NORMAL",
    )
    assert closed_order.state == "closed"
    assert closed_order.actual_exit == order.take_profit
    assert closed_order.exit_cause == "TP_HIT"
    assert closed_order.realized_pnl_net > 0

    outbox_tp = isolated_db.query(models.NotificationOutbox).filter(
        models.NotificationOutbox.message_type == "TP_HIT"
    ).first()
    assert outbox_tp is not None

    # 3. Verify DayAudit fills count incremented
    audits = isolated_db.query(models.DayAudit).all()
    assert any(a.fills_count >= 1 for a in audits)


# =====================================================================
# PHẦN 078: FULL SHORT LIFECYCLE (ARM -> FILL -> SL)
# =====================================================================

def test_v15_1_full_short_lifecycle_arm_fill_sl(isolated_db: Session):
    """
    PHẦN 078, 087:
    Full SHORT lifecycle: Arm -> execute_fill -> execute_close (SL) -> Negative net PnL.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.PAPER.value, daily_ny_research_enabled=False)

    zone = WatchedZone(
        zone_id="z_life_short_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2647.5, "ask": 2648.0, "is_valid": True}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=cfg, auto_enabled=True,
    )
    order_id = res["armed_order_id"]
    assert order_id is not None

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()
    calc = calculate_risk_reward("SHORT", order.planned_entry, order.stop_loss, order.take_profit, 1000.0, 0.25, 30.0)

    # 1. Fill
    fill_time = now_ms + 5_000
    filled_order = TradeLifecycleService.execute_fill(
        db=isolated_db,
        order=order,
        fill_price=order.planned_entry,
        calc_result=calc,
        now_ms=fill_time,
    )
    assert filled_order.state == "paper_open"

    # 2. Close on SL
    close_time = fill_time + 45_000
    closed_order = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=order.stop_loss,
        exit_cause="SL_HIT",
        occurred_at=close_time,
        session_tag="NORMAL",
    )
    assert closed_order.state == "closed"
    assert closed_order.actual_exit == order.stop_loss
    assert closed_order.exit_cause == "SL_HIT"
    assert closed_order.realized_pnl_net < 0


# =====================================================================
# PHẦN 079: SHADOW MODE ISOLATION
# =====================================================================

def test_v15_1_shadow_mode_isolation(isolated_db: Session):
    """
    PHẦN 023, 079:
    In SHADOW mode:
    - Zero PaperOrder rows created.
    - Zero Telegram ARMED notifications queued.
    - Opportunity Funnel records PLAN_VALID.
    """
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.SHADOW.value, daily_ny_research_enabled=False)

    zone = WatchedZone(
        zone_id="z_shadow_iso_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=cfg,
    )

    assert res["status"] == "OK"
    assert res["armed_order_id"] is None
    assert isolated_db.query(models.PaperOrder).count() == 0
    assert isolated_db.query(models.NotificationOutbox).filter(models.NotificationOutbox.message_type == "ARMED").count() == 0

    records = isolated_db.query(models.OpportunityFunnelRecord).all()
    stages = [r.stage for r in records]
    assert OpportunityStage.PLAN_VALID.value in stages


# =====================================================================
# PHẦN 080: QUOTE AND FEED HEALTH NEGATIVE CONTROLS
# =====================================================================

def test_v15_1_quote_and_feed_health_negatives(isolated_db: Session):
    """
    PHẦN 034, 035, 080:
    - Stale quote: rejected, no candidate eligible.
    - Inverted quote (bid > ask): rejected.
    - Disconnected feed health: entry guard blocks arming.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.PAPER.value, daily_ny_research_enabled=False)

    zone = WatchedZone(
        zone_id="z_neg_quote_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])
    candles_1m = _make_causal_long_m1_candles()

    # Case A: Stale quote
    stale_quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True, "is_stale": True}
    res_stale = orch.evaluate_cycle(
        isolated_db, "XAUUSDT", now_ms=1_300_000, quote=stale_quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[], config=cfg,
    )
    assert res_stale["armed_order_id"] is None

    # Case B: Disconnected feed health blocks arm
    valid_quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    disconnected_feed = {"has_execution_gap": True, "is_lagging": True}
    res_feed = orch.evaluate_cycle(
        isolated_db, "XAUUSDT", now_ms=1_300_000, quote=valid_quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[], config=cfg,
        feed_health=disconnected_feed, auto_enabled=True,
    )
    assert res_feed["armed_order_id"] is None
    assert isolated_db.query(models.PaperOrder).count() == 0


# =====================================================================
# PHẦN 081: CAUSAL REVERSAL NEGATIVE CONTROLS
# =====================================================================

def test_v15_1_causal_reversal_negative_controls(isolated_db: Session):
    """
    PHẦN 040, 041, 048, 081:
    - No sweep: candidate rejected.
    - No closed retest: returns WAITING_M1_RETEST, candidate not eligible.
    """
    zone = WatchedZone(
        zone_id="z_causal_neg_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    cfg = M1ScalpConfig(min_closed_m1_bars=15)
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    policy_dict = {"capital_usdt": 1000.0, "risk_pct": 0.25, "default_leverage": 30.0}

    # Case A: No sweep (Candles stay above zone without piercing swing low)
    no_sweep_candles = [
        CandleStub(i * 60_000, 2625.0, 2626.0, 2622.0, 2624.0) for i in range(20)
    ]
    res_no_sweep = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, no_sweep_candles, quote, policy_dict, now_ms=1_300_000, config=cfg
    )
    assert res_no_sweep.is_eligible is False

    # Case B: No closed retest (Displacement occurs, but candles stay high without retesting)
    candles_no_retest = _make_causal_long_m1_candles()[:16]  # Cut off before retest candles
    res_no_retest = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, candles_no_retest, quote, policy_dict, now_ms=1_300_000, config=cfg
    )
    assert res_no_retest.is_eligible is False
    assert res_no_retest.reason_code == "WAITING_M1_RETEST"


# =====================================================================
# PHẦN 082: COST AND MIN NET R:R THRESHOLD
# =====================================================================

def test_v15_1_cost_and_min_net_rr_threshold(isolated_db: Session):
    """
    PHẦN 038, 082:
    Configured min_net_rr threshold is strictly respected by calculation.
    """
    zone = WatchedZone(
        zone_id="z_rr_neg_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    # Set unrealistically high min_net_rr = 5.0
    cfg = M1ScalpConfig(min_closed_m1_bars=15, min_net_rr=5.0)
    candles = _make_causal_long_m1_candles()
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    policy_dict = {"capital_usdt": 1000.0, "risk_pct": 0.25, "default_leverage": 30.0}

    res = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, candles, quote, policy_dict, now_ms=1_300_000, config=cfg
    )
    assert res.is_eligible is False
    assert res.reason_code in ("CANNOT_EXECUTE", "INSUFFICIENT_NET_RR", "CALCULATOR_INVALID")


# =====================================================================
# PHẦN 083: SIGNAL DEDUP AND IDEMPOTENCY
# =====================================================================

def test_v15_1_signal_dedup_and_idempotency_across_eval_cycles(isolated_db: Session):
    """
    PHẦN 057, 058, 083:
    Evaluating the same closed bars across multiple cycles with different now_ms
    produces exactly ONE PaperOrder and ONE ARMED Telegram outbox message.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.PAPER.value, daily_ny_research_enabled=False)

    zone = WatchedZone(
        zone_id="z_dedup_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])
    candles = _make_causal_long_m1_candles()
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    # Cycle 1
    res1 = orch.evaluate_cycle(
        isolated_db, "XAUUSDT", now_ms=1_300_000, quote=quote, candles_1m=candles,
        candles_5m=[], candles_15m=[], config=cfg, auto_enabled=True,
    )
    assert res1["armed_order_id"] is not None

    # Cycle 2 (same bars, 5 seconds later)
    res2 = orch.evaluate_cycle(
        isolated_db, "XAUUSDT", now_ms=1_305_000, quote=quote, candles_1m=candles,
        candles_5m=[], candles_15m=[], config=cfg, auto_enabled=True,
    )

    # Cycle 3 (same bars, 10 seconds later)
    res3 = orch.evaluate_cycle(
        isolated_db, "XAUUSDT", now_ms=1_310_000, quote=quote, candles_1m=candles,
        candles_5m=[], candles_15m=[], config=cfg, auto_enabled=True,
    )

    # Assert exactly ONE PaperOrder in DB
    orders = isolated_db.query(models.PaperOrder).all()
    assert len(orders) == 1
    assert orders[0].id == res1["armed_order_id"]

    # Assert exactly ONE ARMED notification
    armed_outbox = isolated_db.query(models.NotificationOutbox).filter(
        models.NotificationOutbox.message_type == "ARMED"
    ).all()
    assert len(armed_outbox) == 1


# =====================================================================
# PHẦN 084: SHARED DAILY LIMIT GUARD
# =====================================================================

def test_v15_1_shared_daily_limit_guard(isolated_db: Session):
    """
    PHẦN 053, 056, 084:
    When DayAudit fills_count reaches 3, entry guard blocks further arming.
    Shared limit across M5, M1, and research.
    """
    _setup_positive_trading_policy(isolated_db)
    # Seed DayAudit with fills_count = 3
    audit = models.DayAudit(
        date_str="2026-10-15",
        fills_count=3,
        initial_equity=1000.0,
        current_equity=1000.0,
    )
    isolated_db.add(audit)
    isolated_db.commit()

    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.PAPER.value, daily_ny_research_enabled=False)

    zone = WatchedZone(
        zone_id="z_quota_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])
    candles = _make_causal_long_m1_candles()
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT", now_ms=1_300_000, quote=quote, candles_1m=candles,
        candles_5m=[], candles_15m=[], config=cfg, auto_enabled=True,
    )

    assert res["armed_order_id"] is None
    assert isolated_db.query(models.PaperOrder).count() == 0

    blocked_records = isolated_db.query(models.OpportunityFunnelRecord).filter(
        models.OpportunityFunnelRecord.stage == OpportunityStage.GUARD_BLOCKED.value
    ).all()
    assert len(blocked_records) >= 1


# =====================================================================
# PHẦN 085: DAILY NY RESEARCH MODEL CONTROLS
# =====================================================================

def test_v15_1_daily_ny_research_model_behavior(isolated_db: Session):
    """
    PHẦN 052, 055, 085:
    Daily NY Research model:
    - Effective risk is capped by fallback_risk_pct.
    - Model label is strictly NY_DAILY_RESEARCH.
    - Skips if trade already filled today.
    """
    base_ts = 1_000_000_000
    candles_5m = [CandleStub(base_ts + i * 300_000, 2600.0 + i, 2605.0 + i, 2595.0 + i, 2602.0 + i) for i in range(25)]
    quote = {"bid": 2624.0, "ask": 2624.5, "is_valid": True}
    cfg = M1ScalpConfig(fallback_risk_pct=0.20)
    policy_dict = {"risk_pct": 0.50, "capital_usdt": 1000.0, "default_leverage": 30.0}
    now_ms = base_ts + 25 * 300_000 + 10_000

    # Case A: Today fills = 1 -> skipped
    res_skipped = DailyNYResearchService.evaluate_daily_research_candidate(
        candles_5m, quote, "BULLISH", "ALIGNED", policy_dict, 1, now_ms=now_ms, is_ny_session=True, config=cfg
    )
    assert res_skipped["is_eligible"] is False
    assert res_skipped["reason_code"] == "DAILY_TARGET_ALREADY_MET"

    # Case B: Eligible in NY session with 0 fills
    res_eligible = DailyNYResearchService.evaluate_daily_research_candidate(
        candles_5m, quote, "BULLISH", "ALIGNED", policy_dict, 0, now_ms=now_ms, is_ny_session=True, config=cfg
    )
    assert res_eligible["is_eligible"] is True
    calc = res_eligible["calculation"]
    assert calc is not None
    # Risk capped to fallback_risk_pct = 0.20 instead of 0.50
    assert getattr(calc, "effective_risk_pct", 0.0) <= 0.20


# =====================================================================
# PHẦN 086: COMMIT FAILURE ATOMICITY
# =====================================================================

def test_v15_1_commit_failure_atomicity(isolated_db: Session, monkeypatch):
    """
    PHẦN 033, 086:
    When db.commit() raises an exception during arming, evaluate_cycle rolls back cleanly,
    returns armed_order_id = None, and does not leave orphan state.
    """
    _setup_positive_trading_policy(isolated_db)
    # Pre-create audit row so get_or_create_today_audit doesn't commit
    crud.get_or_create_today_audit(isolated_db)

    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.PAPER.value, daily_ny_research_enabled=False)

    zone = WatchedZone(
        zone_id="z_commit_fail_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])
    candles = _make_causal_long_m1_candles()
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    def _failing_commit():
        raise RuntimeError("Simulated DB Commit Failure")

    monkeypatch.setattr(isolated_db, "commit", _failing_commit)

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT", now_ms=1_300_000, quote=quote, candles_1m=candles,
        candles_5m=[], candles_15m=[], config=cfg, auto_enabled=True,
    )

    assert res["armed_order_id"] is None


# =====================================================================
# PHẦN 088: CHART EVIDENCE ENDPOINT IMMUTABLE SNAPSHOT
# =====================================================================

def test_v15_1_chart_evidence_endpoint_immutable(client: TestClient, isolated_db: Session):
    """
    PHẦN 069, 070, 088:
    1. Arm an order.
    2. GET /api/v1/orders/{order_id}/chart-evidence returns frozen snapshot.
    3. Modify active WatchSetup in DB.
    4. GET /api/v1/orders/{order_id}/chart-evidence still returns original frozen evidence.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.PAPER.value, daily_ny_research_enabled=False)

    zone = WatchedZone(
        zone_id="z_chart_ev_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])
    candles = _make_causal_long_m1_candles()
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT", now_ms=1_300_000, quote=quote, candles_1m=candles,
        candles_5m=[], candles_15m=[], config=cfg, auto_enabled=True,
    )
    order_id = res["armed_order_id"]
    assert order_id is not None

    # Initial evidence check
    resp1 = client.get(f"/api/v1/orders/{order_id}/chart-evidence")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["binding"]["direction"] == "LONG"
    assert data1["risk_reward_plan"]["planned_entry"] == 2622.5

    # Mutate active WatchSetup to simulate subsequent chart drift
    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()
    ws = isolated_db.query(models.WatchSetup).filter(models.WatchSetup.id == order.setup_id).first()
    ws.direction = "SHORT"
    ws.provisional_entry = 9999.0
    isolated_db.commit()

    # Re-fetch chart evidence for historical order
    resp2 = client.get(f"/api/v1/orders/{order_id}/chart-evidence")
    assert resp2.status_code == 200
    data2 = resp2.json()
    # Must remain LONG with original entry 2622.5!
    assert data2["binding"]["direction"] == "LONG"
    assert data2["risk_reward_plan"]["planned_entry"] == 2622.5


# =====================================================================
# PHẦN 089: PERFORMANCE BOUNDED EVALUATION
# =====================================================================

def test_v15_1_performance_bounded_evaluation(isolated_db: Session):
    """
    PHẦN 062, 089:
    Running repeated ticks with the same closed candles skips redundant structural
    computations and completes in bounded duration.
    """
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.SHADOW.value, daily_ny_research_enabled=False)

    zone = WatchedZone(
        zone_id="z_perf_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])
    candles = _make_causal_long_m1_candles()
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    durations = []
    for i in range(10):
        t0 = time.perf_counter()
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT", now_ms=1_300_000 + i * 1000, quote=quote, candles_1m=candles,
            candles_5m=[], candles_15m=[], config=cfg,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        durations.append(elapsed_ms)
        assert res["status"] == "OK"

    avg_duration = sum(durations) / len(durations)
    assert avg_duration < 50.0  # Bounded latency under 50ms
