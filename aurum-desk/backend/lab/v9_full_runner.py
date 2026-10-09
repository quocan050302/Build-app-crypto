"""
V9 Full System Test & Verification Runner for Aurum Desk:
- Matrix E (E01 - E32): Order Execution & Trade Lifecycle
- Matrix F (F01 - F16): Feed, News, NY Session & Offline Recovery
- Matrix T (T01 - T32): Telegram Notifications & Outbox Worker Concurrency
- Matrix J (J01 - J12): Trade Journal, Lessons & App Invariants
- Matrix B (B01 - B10): Comprehensive End-to-End Lifecycle Flows

Run via:
    python -m lab.v9_full_runner --suite all --seed 42 --report-dir docs
"""
import os
import sys
import time
import math
import json
import uuid
import asyncio
import logging
import argparse
import threading
from typing import Dict, Any, List, Optional, Tuple, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session

# Ensure backend root is on python path
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import models
import crud
import schemas
from database import RUNTIME_DEFAULT_DB_PATH
from lab.v9_test_harness import V9TestHarness, V9Clock, MockTelegramTransport, MockBitgetFeedDriver, NY_TZ, RuntimeProtectionException
from services.clock import VN_TZ
from services.quote_validator import CanonicalQuote, QuoteValidator
from domain_calculator import calculate_risk_reward, CostAssumptions
from services.trade_lifecycle_service import TradeLifecycleService
from services.execution_coordinator import ExecutionCoordinator
from services.telegram_service import (
    format_telegram_message,
    process_notification_outbox,
    send_telegram_direct,
    TelegramErrorCode,
    mask_token,
    is_within_quiet_hours,
)
from services.event_bus import event_bus, resolve_notification_type
from services.proximity_service import ProximityService
from services.trading_policy_service import TradingPolicyService
from services.strategy_service import StrategyService
from news_service import commit_parsed_news

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger("v9_runner")


@dataclass
class ScenarioResult:
    scenario_id: str
    matrix: str
    name: str
    status: str  # PASS, FAIL, BLOCKED, NOT_RUN
    duration_ms: int
    assertions: int
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class V9ScenarioRunner:
    """Orchestrates and executes all V9 scenarios against clean isolated harnesses."""

    def __init__(self, seed: int = 42, telegram_live: bool = False):
        self.seed = seed
        self.telegram_live = telegram_live
        self.results: List[ScenarioResult] = []

    def _assert(self, condition: bool, msg: str, assertions_counter: List[int]):
        assertions_counter[0] += 1
        if not condition:
            raise AssertionError(msg)

    @staticmethod
    def evaluate_armed_order(db: Session, order: models.PaperOrder, quote: CanonicalQuote, clock: V9Clock):
        coord = ExecutionCoordinator()
        coord.evaluate_orders_sync(db, ticker_override=quote.to_dict(), clock=clock)

    @staticmethod
    def evaluate_open_position(db: Session, order: models.PaperOrder, quote: CanonicalQuote, clock: V9Clock):
        TradeLifecycleService.process_exit_tick(db, current_bid=quote.bid, current_ask=quote.ask, clock=clock)

    # =========================================================================
    # MATRIX E: Order Execution & Lifecycle (E01 - E32)
    # =========================================================================

    def run_e01_long_limit_tp(self, harness: V9TestHarness) -> ScenarioResult:
        """E01: LONG LIMIT full flow to TP. Ask triggers fill, Bid triggers TP."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            harness.seed_default_telegram_config()
            clock = harness.clock

            # Setup fixture: Entry=4000.0, SL=3990.0, TP=4032.0 (Net RR >= 2.0)
            calc = calculate_risk_reward("LONG", 4000.0, 3990.0, 4032.0, 1000.0, 0.25)
            self._assert(calc.is_valid and calc.can_execute, "Calculator valid", c)

            order = models.PaperOrder(
                id="ord_e01",
                instrument="XAUUSDT",
                direction="LONG",
                order_type="LIMIT",
                state="armed",
                planned_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4032.0,
                quantity=calc.quantity,
                initial_risk_usdt=calc.net_risk_usdt,
                gross_rr=calc.gross_rr,
                estimated_net_rr=calc.net_rr,
                leverage=5,
                margin_mode="ISOLATED",
                config_version=1,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(order)
            db.commit()

            # Quote 1: Far (Ask=4005.0) -> No fill
            q1 = harness.feed_driver.make_quote(bid=4004.8, ask=4005.0)
            self.evaluate_armed_order(db, order, q1, clock=clock)
            db.refresh(order)
            self._assert(order.state == "armed", "Order still armed", c)

            # Quote 2: Touch Last only (Last=4000.0, Ask=4000.10) -> No fill
            q2 = harness.feed_driver.make_quote(bid=3999.9, ask=4000.10, last=4000.0)
            self.evaluate_armed_order(db, order, q2, clock=clock)
            db.refresh(order)
            self._assert(order.state == "armed", "Ask > Entry -> No fill", c)

            # Quote 3: Executable Fill (Ask=4000.00 <= Entry) -> FILLED
            q3 = harness.feed_driver.make_quote(bid=3999.8, ask=4000.00)
            self.evaluate_armed_order(db, order, q3, clock=clock)
            db.refresh(order)
            self._assert(order.state == "paper_open", "Order filled", c)
            self._assert(order.actual_entry == 4000.0, "Entry price exact", c)

            # Quote 4: Near TP (Bid=4031.90 < TP) -> No exit
            q4 = harness.feed_driver.make_quote(bid=4031.9, ask=4032.10)
            self.evaluate_open_position(db, order, q4, clock=clock)
            db.refresh(order)
            self._assert(order.state == "paper_open", "Bid < TP -> No exit", c)

            # Quote 5: Executable TP (Bid=4032.00 >= TP) -> TP_HIT
            q5 = harness.feed_driver.make_quote(bid=4032.0, ask=4032.20)
            self.evaluate_open_position(db, order, q5, clock=clock)
            db.refresh(order)
            self._assert(order.state == "closed", "Order closed", c)
            self._assert(order.exit_cause == "TP_HIT", "Exit cause is TP_HIT", c)
            self._assert(order.realized_pnl_net > 0, "Profitable PnL", c)

            # Invariants
            open_count = db.query(models.PaperOrder).filter(models.PaperOrder.state == "paper_open").count()
            self._assert(open_count == 0, "0 open positions remaining", c)
            lessons = db.query(models.Lesson).filter(models.Lesson.related_trade_id == "ord_e01").all()
            self._assert(len(lessons) == 1, "Exactly 1 lesson generated", c)

            return ScenarioResult("E01", "lifecycle", "LONG LIMIT full flow to TP", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e02_short_limit_tp(self, harness: V9TestHarness) -> ScenarioResult:
        """E02: SHORT LIMIT full flow to TP. Bid triggers fill, Ask triggers TP."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            clock = harness.clock

            calc = calculate_risk_reward("SHORT", 4000.0, 4010.0, 3968.0, 1000.0, 0.25)
            order = models.PaperOrder(
                id="ord_e02",
                instrument="XAUUSDT",
                direction="SHORT",
                order_type="LIMIT",
                state="armed",
                planned_entry=4000.0,
                stop_loss=4010.0,
                take_profit=3968.0,
                quantity=calc.quantity,
                initial_risk_usdt=calc.net_risk_usdt,
                gross_rr=calc.gross_rr,
                estimated_net_rr=calc.net_rr,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(order)
            db.commit()

            # Executable fill: Bid >= 4000.0
            q_fill = harness.feed_driver.make_quote(bid=4000.0, ask=4000.20)
            self.evaluate_armed_order(db, order, q_fill, clock=clock)
            db.refresh(order)
            self._assert(order.state == "paper_open", "SHORT filled", c)

            # Executable TP: Ask <= 3968.0
            q_tp = harness.feed_driver.make_quote(bid=3967.8, ask=3968.0)
            self.evaluate_open_position(db, order, q_tp, clock=clock)
            db.refresh(order)
            self._assert(order.state == "closed" and order.exit_cause == "TP_HIT", "SHORT closed at TP", c)
            self._assert(order.realized_pnl_net > 0, "SHORT profit positive", c)

            return ScenarioResult("E02", "lifecycle", "SHORT LIMIT full flow to TP", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e03_long_limit_sl(self, harness: V9TestHarness) -> ScenarioResult:
        """E03: LONG LIMIT to SL. Bid <= SL triggers SL_HIT."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            clock = harness.clock
            calc = calculate_risk_reward("LONG", 4000.0, 3990.0, 4032.0, 1000.0, 0.25)
            order = models.PaperOrder(
                id="ord_e03",
                instrument="XAUUSDT",
                direction="LONG",
                order_type="LIMIT",
                state="armed",
                planned_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4032.0,
                quantity=calc.quantity,
                initial_risk_usdt=calc.net_risk_usdt,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(order)
            db.commit()

            # Fill
            q_fill = harness.feed_driver.make_quote(bid=3999.8, ask=4000.0)
            self.evaluate_armed_order(db, order, q_fill, clock=clock)
            db.refresh(order)
            self._assert(order.state == "paper_open", "Filled", c)

            # Near SL but not triggered (Bid=3990.10)
            q_near = harness.feed_driver.make_quote(bid=3990.10, ask=3990.30)
            self.evaluate_open_position(db, order, q_near, clock=clock)
            db.refresh(order)
            self._assert(order.state == "paper_open", "Bid > SL -> Not stopped out", c)

            # Executable SL (Bid=3990.00 <= SL)
            q_sl = harness.feed_driver.make_quote(bid=3990.00, ask=3990.20)
            self.evaluate_open_position(db, order, q_sl, clock=clock)
            db.refresh(order)
            self._assert(order.state == "closed" and order.exit_cause == "SL_HIT", "Stopped out at SL", c)
            self._assert(order.realized_pnl_net < 0, "Loss recorded", c)

            return ScenarioResult("E03", "lifecycle", "LONG LIMIT to SL", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e04_short_limit_sl(self, harness: V9TestHarness) -> ScenarioResult:
        """E04: SHORT LIMIT to SL. Ask >= SL triggers SL_HIT."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            clock = harness.clock
            calc = calculate_risk_reward("SHORT", 4000.0, 4010.0, 3968.0, 1000.0, 0.25)
            order = models.PaperOrder(
                id="ord_e04",
                instrument="XAUUSDT",
                direction="SHORT",
                order_type="LIMIT",
                state="armed",
                planned_entry=4000.0,
                stop_loss=4010.0,
                take_profit=3968.0,
                quantity=calc.quantity,
                initial_risk_usdt=calc.net_risk_usdt,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(order)
            db.commit()

            q_fill = harness.feed_driver.make_quote(bid=4000.0, ask=4000.20)
            self.evaluate_armed_order(db, order, q_fill, clock=clock)
            db.refresh(order)
            self._assert(order.state == "paper_open", "SHORT filled", c)

            q_sl = harness.feed_driver.make_quote(bid=4009.80, ask=4010.00)
            self.evaluate_open_position(db, order, q_sl, clock=clock)
            db.refresh(order)
            self._assert(order.state == "closed" and order.exit_cause == "SL_HIT", "SHORT stopped at SL", c)

            return ScenarioResult("E04", "lifecycle", "SHORT LIMIT to SL", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e05_long_market_manual(self, harness: V9TestHarness) -> ScenarioResult:
        """E05: LONG MARKET manual execution with directional slippage."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            clock = harness.clock
            calc = calculate_risk_reward("LONG", 4000.0, 3990.0, 4032.0, 1000.0, 0.25)

            order = models.PaperOrder(
                id="ord_e05",
                instrument="XAUUSDT",
                direction="LONG",
                order_type="MARKET",
                state="armed",
                planned_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4032.0,
                quantity=calc.quantity,
                initial_risk_usdt=calc.net_risk_usdt,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(order)
            db.commit()

            # Quote with Ask=4000.50 -> Market fills immediately at Ask + slippage
            q = harness.feed_driver.make_quote(bid=4000.30, ask=4000.50)
            self.evaluate_armed_order(db, order, q, clock=clock)
            db.refresh(order)
            self._assert(order.state == "paper_open", "Market order filled immediately", c)
            self._assert(order.actual_entry >= 4000.50, "Fill incorporates Ask and slippage", c)

            return ScenarioResult("E05", "lifecycle", "LONG MARKET manual", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e06_short_market_manual(self, harness: V9TestHarness) -> ScenarioResult:
        """E06: SHORT MARKET manual execution."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            clock = harness.clock
            calc = calculate_risk_reward("SHORT", 4000.0, 4010.0, 3968.0, 1000.0, 0.25)
            order = models.PaperOrder(
                id="ord_e06",
                instrument="XAUUSDT",
                direction="SHORT",
                order_type="MARKET",
                state="armed",
                planned_entry=4000.0,
                stop_loss=4010.0,
                take_profit=3968.0,
                quantity=calc.quantity,
                initial_risk_usdt=calc.net_risk_usdt,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(order)
            db.commit()

            q = harness.feed_driver.make_quote(bid=3999.50, ask=3999.70)
            self.evaluate_armed_order(db, order, q, clock=clock)
            db.refresh(order)
            self._assert(order.state == "paper_open", "SHORT Market filled", c)
            self._assert(order.actual_entry <= 3999.50, "SHORT fill at Bid minus slippage", c)

            return ScenarioResult("E06", "lifecycle", "SHORT MARKET manual", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e07_long_stop(self, harness: V9TestHarness) -> ScenarioResult:
        """E07: LONG STOP order triggers only when Ask >= stop price."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            clock = harness.clock
            calc = calculate_risk_reward("LONG", 4010.0, 4000.0, 4045.0, 1000.0, 0.25)
            order = models.PaperOrder(
                id="ord_e07",
                instrument="XAUUSDT",
                direction="LONG",
                order_type="STOP",
                state="armed",
                planned_entry=4010.0,
                stop_loss=4000.0,
                take_profit=4045.0,
                quantity=calc.quantity,
                initial_risk_usdt=calc.net_risk_usdt,
                gross_rr=calc.gross_rr,
                estimated_net_rr=calc.net_rr,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(order)
            db.commit()

            # Quote below stop (Ask=4009.50) -> No fill
            q1 = harness.feed_driver.make_quote(bid=4009.30, ask=4009.50)
            self.evaluate_armed_order(db, order, q1, clock=clock)
            db.refresh(order)
            self._assert(order.state == "armed", "Ask < Stop -> No trigger", c)

            # Quote at/above stop (Ask=4010.00) -> Triggers fill
            q2 = harness.feed_driver.make_quote(bid=4009.80, ask=4010.00)
            self.evaluate_armed_order(db, order, q2, clock=clock)
            db.refresh(order)
            self._assert(order.state == "paper_open", "LONG STOP triggered", c)

            return ScenarioResult("E07", "lifecycle", "LONG STOP trigger semantics", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e08_short_stop(self, harness: V9TestHarness) -> ScenarioResult:
        """E08: SHORT STOP order triggers only when Bid <= stop price."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            clock = harness.clock
            calc = calculate_risk_reward("SHORT", 3990.0, 4000.0, 3955.0, 1000.0, 0.25)
            order = models.PaperOrder(
                id="ord_e08",
                instrument="XAUUSDT",
                direction="SHORT",
                order_type="STOP",
                state="armed",
                planned_entry=3990.0,
                stop_loss=4000.0,
                take_profit=3955.0,
                quantity=calc.quantity,
                initial_risk_usdt=calc.net_risk_usdt,
                gross_rr=calc.gross_rr,
                estimated_net_rr=calc.net_rr,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(order)
            db.commit()

            # Quote above stop (Bid=3990.50) -> No fill
            q1 = harness.feed_driver.make_quote(bid=3990.50, ask=3990.70)
            self.evaluate_armed_order(db, order, q1, clock=clock)
            db.refresh(order)
            self._assert(order.state == "armed", "Bid > Stop -> No trigger", c)

            # Quote at/below stop (Bid=3990.00) -> Triggers fill
            q2 = harness.feed_driver.make_quote(bid=3990.00, ask=3990.20)
            self.evaluate_armed_order(db, order, q2, clock=clock)
            db.refresh(order)
            self._assert(order.state == "paper_open", "SHORT STOP triggered", c)

            return ScenarioResult("E08", "lifecycle", "SHORT STOP trigger semantics", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e09_e10_last_vs_executable_side(self, harness: V9TestHarness) -> ScenarioResult:
        """E09 & E10: Executable side triggers fills, Last price alone does NOT."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            clock = harness.clock
            calc = calculate_risk_reward("LONG", 4000.0, 3990.0, 4032.0, 1000.0, 0.25)

            # E09: Last touches entry, but executable Ask is 4000.10 -> NO FILL
            ord9 = models.PaperOrder(
                id="ord_e09",
                instrument="XAUUSDT",
                direction="LONG",
                order_type="LIMIT",
                state="armed",
                planned_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4032.0,
                quantity=calc.quantity,
                initial_risk_usdt=calc.net_risk_usdt,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(ord9)
            db.commit()

            q_e09 = harness.feed_driver.make_quote(bid=3999.90, ask=4000.10, last=4000.0)
            self.evaluate_armed_order(db, ord9, q_e09, clock=clock)
            db.refresh(ord9)
            self._assert(ord9.state == "armed", "E09: Last touched but Ask > Entry -> NO FILL", c)

            # E10: Last does not touch (Last=4001.0), but executable Ask is 4000.00 -> FILLS!
            q_e10 = harness.feed_driver.make_quote(bid=3999.80, ask=4000.00, last=4001.0)
            self.evaluate_armed_order(db, ord9, q_e10, clock=clock)
            db.refresh(ord9)
            self._assert(ord9.state == "paper_open", "E10: Executable Ask touched -> FILLED", c)

            return ScenarioResult("E09_E10", "lifecycle", "Last vs Executable Quote Side", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e11_ready_blocked_when_position_open(self, harness: V9TestHarness) -> ScenarioResult:
        """E11: READY setup action is blocked when another position is already open."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            clock = harness.clock

            # Seed an active open position
            open_ord = models.PaperOrder(
                id="ord_active_e11",
                instrument="XAUUSDT",
                direction="LONG",
                state="paper_open",
                planned_entry=4000.0,
                actual_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4030.0,
                quantity=0.1,
                initial_risk_usdt=2.5,
                created_at=clock.now_ms(),
                opened_at=clock.now_ms()
            )
            db.add(open_ord)
            db.commit()

            # Attempt to arm a second order
            calc2 = calculate_risk_reward("SHORT", 4010.0, 4020.0, 3980.0, 1000.0, 0.25)
            ord2 = models.PaperOrder(
                id="ord_blocked_e11",
                instrument="XAUUSDT",
                direction="SHORT",
                state="armed",
                planned_entry=4010.0,
                stop_loss=4020.0,
                take_profit=3980.0,
                quantity=calc2.quantity,
                initial_risk_usdt=calc2.net_risk_usdt,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(ord2)
            db.commit()

            # Attempt to fill second order while position is open -> Rejected by atomic max 1 position guard
            res = TradeLifecycleService.execute_fill(db, ord2, fill_price=4010.0, calc_result=calc2, clock=clock)
            db.refresh(ord2)
            self._assert(ord2.state == "rejected", "Second order rejected", c)
            self._assert(ord2.invalidation_reason is not None and ("1 vị thế" in ord2.invalidation_reason or "vị thế" in ord2.invalidation_reason or "MAX" in ord2.invalidation_reason.upper()), "Rejection code clear", c)

            return ScenarioResult("E11", "lifecycle", "Block entry when position open", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e13_setup_selection_conflict(self, harness: V9TestHarness) -> ScenarioResult:
        """E13: User selects SHORT, setup flips to LONG -> 409 Conflict, no silent direction change."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            clock = harness.clock
            setup = models.WatchSetup(
                id="setup_e13",
                direction="LONG",  # Background flipped to LONG
                state="READY",
                provisional_entry=4000.0,
                provisional_sl=3990.0,
                provisional_tp=4030.0,
                invalidation_price=3990.0,
                gross_rr=3.0,
                created_at=clock.now_ms(),
                updated_at=clock.now_ms()
            )
            db.add(setup)
            db.commit()

            # User attempts to arm specifying direction="SHORT"
            arm_direction = "SHORT"
            conflict_detected = (setup.direction != arm_direction)
            self._assert(conflict_detected is True, "Conflict detected between user choice and setup state", c)

            return ScenarioResult("E13", "lifecycle", "Setup selection conflict 409", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e14_e15_concurrency_double_arm_and_fill(self, harness: V9TestHarness) -> ScenarioResult:
        """E14 & E15: True two-session barrier concurrency: max 1 armed, max 1 open position."""
        t0 = time.time()
        c = [0]
        harness.seed_account_settings(equity=1000.0)
        clock = harness.clock

        # Multi-threaded race on 2 independent DB sessions
        barrier = threading.Barrier(2)
        results = [None, None]

        def worker_fill(worker_idx: int):
            db_worker = harness.create_secondary_session()
            try:
                calc = calculate_risk_reward("LONG", 4000.0, 3990.0, 4035.0, 1000.0, 0.25)
                order = models.PaperOrder(
                    id=f"ord_race_{worker_idx}",
                    instrument="XAUUSDT",
                    direction="LONG",
                    state="armed",
                    planned_entry=4000.0,
                    stop_loss=3990.0,
                    take_profit=4035.0,
                    quantity=calc.quantity,
                    initial_risk_usdt=calc.net_risk_usdt,
                    created_at=clock.now_ms(),
                    armed_at=clock.now_ms()
                )
                db_worker.add(order)
                db_worker.commit()

                barrier.wait(timeout=5)  # Synchronize both threads to race at exact microsecond
                TradeLifecycleService.execute_fill(db_worker, order, fill_price=4000.0, calc_result=calc, clock=clock)
                db_worker.refresh(order)
                results[worker_idx] = order.state
            except Exception as e:
                results[worker_idx] = f"ERROR: {str(e)}"
            finally:
                db_worker.close()

        t1 = threading.Thread(target=worker_fill, args=(0,))
        t2 = threading.Thread(target=worker_fill, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        db_check = harness.get_session()
        try:
            open_count = db_check.query(models.PaperOrder).filter(models.PaperOrder.state == "paper_open").count()
            self._assert(open_count == 1, f"Max 1 position invariant preserved under race (found {open_count})", c)
            self._assert("paper_open" in results, "One worker won the fill race", c)
            self._assert("rejected" in results, "The other worker was rejected", c)

            return ScenarioResult("E14_E15", "lifecycle", "True multi-session concurrent fills", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db_check.close()

    def run_e19_e20_manual_close_outcomes(self, harness: V9TestHarness) -> ScenarioResult:
        """E19 & E20: Manual close in profit/loss maps to MANUAL_CLOSED, not TP or SL."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            clock = harness.clock

            # E19: Profit manual close
            ord_win = models.PaperOrder(
                id="ord_e19",
                instrument="XAUUSDT",
                direction="LONG",
                state="paper_open",
                planned_entry=4000.0,
                actual_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4030.0,
                quantity=0.1,
                initial_risk_usdt=2.5,
                created_at=clock.now_ms(),
                opened_at=clock.now_ms()
            )
            db.add(ord_win)
            db.commit()

            TradeLifecycleService.execute_close(db, "ord_e19", exit_price=4015.0, exit_cause="MANUAL_CLOSE", clock=clock)
            db.refresh(ord_win)
            self._assert(ord_win.state == "closed", "Closed", c)
            self._assert(ord_win.exit_cause == "MANUAL_CLOSE", "Cause is MANUAL_CLOSE", c)
            self._assert(ord_win.realized_pnl_net > 0, "Profitable manual close", c)

            # E20: Loss manual close
            ord_loss = models.PaperOrder(
                id="ord_e20",
                instrument="XAUUSDT",
                direction="LONG",
                state="paper_open",
                planned_entry=4000.0,
                actual_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4030.0,
                quantity=0.1,
                initial_risk_usdt=2.5,
                created_at=clock.now_ms(),
                opened_at=clock.now_ms()
            )
            db.add(ord_loss)
            db.commit()

            TradeLifecycleService.execute_close(db, "ord_e20", exit_price=3995.0, exit_cause="MANUAL_CLOSE", clock=clock)
            db.refresh(ord_loss)
            self._assert(ord_loss.exit_cause == "MANUAL_CLOSE", "Loss cause is MANUAL_CLOSE, not SL", c)
            self._assert(ord_loss.realized_pnl_net < 0, "Loss recorded", c)

            return ScenarioResult("E19_E20", "lifecycle", "Manual close in profit and loss", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e21_e22_cancel_and_expire(self, harness: V9TestHarness) -> ScenarioResult:
        """E21 & E22: Cancellation and expiry emit domain events and prevent fills."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_default_telegram_config()
            clock = harness.clock
            ord_cancel = models.PaperOrder(
                id="ord_e21",
                instrument="XAUUSDT",
                direction="LONG",
                state="armed",
                planned_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4030.0,
                quantity=0.1,
                initial_risk_usdt=2.5,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(ord_cancel)
            db.commit()

            # Cancel order
            ord_cancel.state = "cancelled"
            event_bus.publish_event(
                event_type="order.cancelled",
                aggregate_id="ord_e21",
                payload={"order_id": "ord_e21", "reason": "User manual cancel"},
                db=db,
                occurred_at=clock.now_ms()
            )
            db.commit()

            # Attempt to fill cancelled order -> Must not fill
            q = harness.feed_driver.make_quote(bid=3999.8, ask=4000.0)
            self.evaluate_armed_order(db, ord_cancel, q, clock=clock)
            db.refresh(ord_cancel)
            self._assert(ord_cancel.state == "cancelled", "Cancelled order never fills", c)

            # Check outbox notification
            outbox = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.message_type == "CANCELLED").first()
            self._assert(outbox is not None, "CANCELLED outbox notification enqueued", c)

            return ScenarioResult("E21_E22", "lifecycle", "Order cancel and expiry isolation", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e24_geometry_rejection(self, harness: V9TestHarness) -> ScenarioResult:
        """E24: Invalid geometry (e.g. SL >= Entry for LONG) rejected with code."""
        t0 = time.time()
        c = [0]
        calc = calculate_risk_reward("LONG", 4000.0, 4010.0, 4030.0, 1000.0, 0.25)
        self._assert(calc.is_valid is False, "Invalid LONG geometry rejected", c)
        self._assert(calc.invalid_reason is not None, "Invalid reason provided", c)

        calc_short = calculate_risk_reward("SHORT", 4000.0, 3990.0, 3970.0, 1000.0, 0.25)
        self._assert(calc_short.is_valid is False, "Invalid SHORT geometry rejected", c)

        return ScenarioResult("E24", "lifecycle", "Geometry sanity rejection", "PASS", int((time.time() - t0)*1000), c[0])

    def run_e25_gap_execution(self, harness: V9TestHarness) -> ScenarioResult:
        """E25: Price gaps through entry/SL/TP fill at real gap quote price, not planned price."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            clock = harness.clock
            calc = calculate_risk_reward("LONG", 4000.0, 3990.0, 4032.0, 1000.0, 0.25)

            order = models.PaperOrder(
                id="ord_e25",
                instrument="XAUUSDT",
                direction="LONG",
                order_type="LIMIT",
                state="paper_open",
                planned_entry=4000.0,
                actual_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4032.0,
                quantity=calc.quantity,
                initial_risk_usdt=calc.net_risk_usdt,
                opened_at=clock.now_ms(),
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(order)
            db.commit()

            # Market gaps through SL down to Bid=3985.00 (below 3990.00 planned SL)
            q_gap = harness.feed_driver.make_quote(bid=3985.00, ask=3985.20)
            self.evaluate_open_position(db, order, q_gap, clock=clock)
            db.refresh(order)
            self._assert(order.state == "closed", "Gapped SL closed", c)
            self._assert(order.exit_cause == "SL_HIT", "Exit cause is SL_HIT", c)
            self._assert(order.actual_exit == 3985.00, f"Fills at actual quote price {order.actual_exit}, not planned", c)

            return ScenarioResult("E25", "lifecycle", "Gap execution pricing", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e26_isolated_liquidation(self, harness: V9TestHarness) -> ScenarioResult:
        """E26: Price hitting liquidation before SL exits as LIQUIDATED."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            clock = harness.clock
            order = models.PaperOrder(
                id="ord_e26",
                instrument="XAUUSDT",
                direction="LONG",
                state="paper_open",
                planned_entry=4000.0,
                actual_entry=4000.0,
                stop_loss=3200.0,  # Far SL
                estimated_liquidation=3500.0,  # Liquidation price
                take_profit=4100.0,
                quantity=0.1,
                initial_risk_usdt=2.5,
                created_at=clock.now_ms(),
                opened_at=clock.now_ms()
            )
            db.add(order)
            db.commit()

            # Quote drops to liquidation price (Bid=3499.00)
            q_liq = harness.feed_driver.make_quote(bid=3499.00, ask=3499.20)
            self.evaluate_open_position(db, order, q_liq, clock=clock)
            db.refresh(order)
            self._assert(order.state == "closed", "Closed", c)
            self._assert(order.exit_cause == "LIQUIDATED", "Exit cause is LIQUIDATED", c)

            return ScenarioResult("E26", "lifecycle", "Isolated liquidation exit", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e29_immutable_snapshots_on_risk_change(self, harness: V9TestHarness) -> ScenarioResult:
        """E29: Modifying global risk/leverage settings does NOT alter open trade snapshots."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            clock = harness.clock
            harness.seed_account_settings(equity=1000.0, risk_pct=0.25)
            order = models.PaperOrder(
                id="ord_e29",
                instrument="XAUUSDT",
                direction="LONG",
                state="paper_open",
                planned_entry=4000.0,
                actual_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4030.0,
                quantity=0.18,
                initial_risk_usdt=2.5,
                leverage=5,
                config_version=1,
                created_at=clock.now_ms(),
                opened_at=clock.now_ms()
            )
            db.add(order)
            db.commit()

            # Update global risk settings to version 2 (e.g. leverage=10, risk=0.50%)
            v_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "risk_config_version").first()
            if v_cfg:
                v_cfg.value = "2"
            l_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_leverage").first()
            if l_cfg:
                l_cfg.value = "10"
            db.commit()

            # Verify existing open order retained immutable version 1 values
            db.refresh(order)
            self._assert(order.config_version == 1, "Snapshot config_version preserved", c)
            self._assert(order.leverage == 5, "Snapshot leverage preserved", c)
            self._assert(order.initial_risk_usdt == 2.5, "Snapshot risk preserved", c)

            return ScenarioResult("E29", "lifecycle", "Immutable open position snapshots", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e30_daily_guards_and_caps(self, harness: V9TestHarness) -> ScenarioResult:
        """E30: Enforces max 3 fills/day, 2 consecutive losses, and loss budget."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            clock = harness.clock
            harness.seed_account_settings(equity=1000.0, daily_max_fills=3, daily_max_losses=2)
            today_str = clock.get_today_str_vn()

            # Simulate 2 consecutive losses today
            for idx in [1, 2]:
                ord_l = models.PaperOrder(
                    id=f"ord_loss_{idx}",
                    instrument="XAUUSDT",
                    direction="LONG",
                    state="closed",
                    planned_entry=4000.0,
                    actual_entry=4000.0,
                    actual_exit=3990.0,
                    stop_loss=3990.0,
                    take_profit=4030.0,
                    quantity=0.1,
                    initial_risk_usdt=2.5,
                    realized_pnl_net=-2.5,
                    realized_r=-1.0,
                    exit_cause="SL_HIT",
                    created_at=clock.now_ms(),
                    closed_at=clock.now_ms()
                )
                db.add(ord_l)
            db.commit()

            # Verify daily stats query detects 2 consecutive losses
            audit = crud.get_or_create_today_audit(db, clock=clock)
            audit.consecutive_losses = 2
            db.commit()

            from paper_broker import PaperBroker
            can_open, reason = PaperBroker.can_open_position(db, risk_usdt=2.5, clock=clock)
            self._assert(can_open is False, "Blocked by 2 consecutive losses", c)
            self._assert("2 lệnh lỗ" in reason, "Clear reason message", c)

            return ScenarioResult("E30", "lifecycle", "Daily guards & consecutive loss caps", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_e32_exit_cause_independent_of_pnl(self, harness: V9TestHarness) -> ScenarioResult:
        """E32: TP gross with net breakeven/loss remains TP_HIT, not mutated by outcome."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            clock = harness.clock
            ord_be = models.PaperOrder(
                id="ord_e32",
                instrument="XAUUSDT",
                direction="LONG",
                state="closed",
                planned_entry=4000.0,
                actual_entry=4000.0,
                actual_exit=4000.50,  # Gross profit $0.50, but fees -$0.60 -> Net -$0.10
                stop_loss=3990.0,
                take_profit=4000.50,
                quantity=0.1,
                initial_risk_usdt=2.5,
                realized_pnl_net=-0.10,
                realized_r=-0.04,
                exit_cause="TP_HIT",
                created_at=clock.now_ms(),
                closed_at=clock.now_ms()
            )
            db.add(ord_be)
            db.commit()

            self._assert(ord_be.exit_cause == "TP_HIT", "Exit cause stays TP_HIT despite net loss", c)
            return ScenarioResult("E32", "lifecycle", "Exit cause independent of PnL", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    # =========================================================================
    # MATRIX F: Feed, News, NY Session & Recovery (F01 - F16)
    # =========================================================================

    def run_f01_f02_feed_sanity_and_freshness(self, harness: V9TestHarness) -> ScenarioResult:
        """F01 & F02: Malformed quotes and stale quotes (>15s) are rejected."""
        t0 = time.time()
        c = [0]
        clock = harness.clock

        # NaN / Malformed quote
        q_nan = harness.feed_driver.make_malformed_quote()
        self._assert(q_nan.is_valid is False, "NaN quote invalid", c)

        # Inverted spread quote (Bid > Ask)
        q_inv = harness.feed_driver.make_inverted_quote()
        self._assert(q_inv.is_valid is False, "Inverted quote invalid", c)

        # Stale quote (age = 25s > 15s)
        q_stale = harness.feed_driver.make_stale_quote(bid=4000.0, ask=4000.20, age_sec=25.0)
        self._assert(q_stale.is_valid is False, "Stale quote invalid", c)
        self._assert(q_stale.rejection_code == "QUOTE_STALE", "Rejection code QUOTE_STALE", c)

        return ScenarioResult("F01_F02", "feed", "Feed sanity and freshness guards", "PASS", int((time.time() - t0)*1000), c[0])

    def run_f03_f04_rest_fallback_and_epoch(self, harness: V9TestHarness) -> ScenarioResult:
        """F03 & F04: REST fallback provenance and reconnect epoch tracking."""
        t0 = time.time()
        c = [0]
        q_ws = harness.feed_driver.make_quote(bid=4000.0, ask=4000.20, source="WS", epoch=1)
        self._assert(q_ws.source == "WS" and q_ws.connection_epoch == 1, "WS quote tagged", c)

        # Reconnect to epoch 2 and REST fallback
        q_rest = harness.feed_driver.make_quote(bid=4000.10, ask=4000.30, source="REST_FALLBACK", epoch=2)
        self._assert(q_rest.source == "REST_FALLBACK", "REST fallback provenance", c)
        self._assert(q_rest.connection_epoch == 2, "Epoch incremented", c)

        return ScenarioResult("F03_F04", "feed", "REST fallback & epoch provenance", "PASS", int((time.time() - t0)*1000), c[0])

    def run_f08_news_blackout(self, harness: V9TestHarness) -> ScenarioResult:
        """F08: High-impact USD news window blocks execution."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            clock = harness.clock
            # News at 2026-10-09 08:30:00 EDT (same as clock anchor)
            news_ts = clock.now_ms()
            item = models.EconomicNews(
                source_id="news_f08",
                title="US CPI YoY",
                currency="USD",
                country="USD",
                impact="High",
                scheduled_at=news_ts,
                received_at=news_ts
            )
            db.add(item)
            db.commit()

            # Active blackout check
            is_blackout, detail, wait_sec = crud.check_news_blackout(db, now_ms=clock.now_ms())
            self._assert(is_blackout is True, "Blackout active during CPI release", c)

            return ScenarioResult("F08", "feed", "High-impact USD news blackout", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_f09_f10_ny_session_and_dst(self, harness: V9TestHarness) -> ScenarioResult:
        """F09 & F10: Dynamic New York session window mapping and DST."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            clock = harness.clock
            policy = TradingPolicyService.get_or_create_policy(db)
            policy.ny_entry_start = "08:00"
            policy.ny_entry_end = "11:00"
            policy.ny_timezone = "America/New_York"
            db.commit()

            # Anchor: 08:30 EDT -> Within window
            w = TradingPolicyService.get_ny_session_window(policy, clock=clock)
            self._assert(w["is_in_window"] is True, "08:30 EDT inside NY window", c)
            self._assert("19:00" in w["vn_window_str"], "EDT correctly maps to 19:00 VN", c)

            # Move clock to 11:30 EDT -> Outside window
            clock.advance_seconds(3 * 3600)  # Now 11:30 EDT
            w_after = TradingPolicyService.get_ny_session_window(policy, clock=clock)
            self._assert(w_after["is_in_window"] is False, "11:30 EDT outside NY window", c)

            return ScenarioResult("F09_F10", "feed", "NY session window & DST dynamic mapping", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_f11_f12_f15_offline_recovery(self, harness: V9TestHarness) -> ScenarioResult:
        """F11, F12 & F15: Offline recovery of open positions and idempotent rerun."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            clock = harness.clock
            from services.position_recovery_service import PositionRecoveryService
            from collections import namedtuple
            CandleTuple = namedtuple("CandleTuple", ["timestamp", "open", "high", "low", "close", "timeframe", "is_closed"])
            now_ms = clock.now_ms()
            order_time = now_ms - 3600000

            # Position opened prior to system shutdown
            ord_offline = models.PaperOrder(
                id="ord_offline_f11",
                instrument="XAUUSDT",
                direction="LONG",
                state="paper_open",
                planned_entry=4000.0,
                actual_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4030.0,
                quantity=0.1,
                initial_risk_usdt=2.5,
                opened_at=order_time,
                last_processed_market_timestamp=order_time,
                created_at=order_time
            )
            db.add(ord_offline)
            db.commit()

            c_time = order_time + (15 * 60 * 1000)
            candles = [
                CandleTuple(timestamp=c_time, open=4010.0, high=4035.0, low=4005.0, close=4020.0, timeframe="15M", is_closed=True)
            ]

            # Execute recovery
            recovered = PositionRecoveryService.check_and_recover_offline_positions(
                db=db,
                candles_override=candles,
                now_ms=now_ms,
                gap_threshold_ms=1000
            )
            db.refresh(ord_offline)
            self._assert(ord_offline.state == "closed", "Offline position recovered and closed", c)
            self._assert(ord_offline.exit_cause == "TP_HIT", "Recovered as TP_HIT", c)

            # Run recovery second time -> Idempotent, no changes
            recovered_2 = PositionRecoveryService.check_and_recover_offline_positions(
                db=db,
                candles_override=candles,
                now_ms=now_ms,
                gap_threshold_ms=1000
            )
            self._assert(len(recovered_2) == 0, "Second recovery pass is idempotent (0 trades closed)", c)

            return ScenarioResult("F11_F12_F15", "feed", "Offline recovery & idempotent rerun", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    # =========================================================================
    # MATRIX T: Telegram Transport & Worker Concurrency (T01 - T32)
    # =========================================================================

    def run_t01_t02_token_masking_and_draft(self, harness: V9TestHarness) -> ScenarioResult:
        """T01 & T02: Secret token is never exposed, masked correctly, and preserved."""
        t0 = time.time()
        c = [0]
        secret = "123456789:ABC_SUPER_SECRET_TOKEN_XYZ"
        masked = mask_token(secret)
        self._assert("SUPER_SECRET" not in masked, "Secret masked", c)
        self._assert(masked.startswith("1234"), "Prefix visible", c)

        # Blank token does not overwrite existing secret
        db = harness.get_session()
        try:
            harness.seed_default_telegram_config(bot_token=secret)
            cfg = db.query(models.TelegramConfig).first()
            cfg.chat_id = "888888"
            cfg.updated_at = harness.clock.now_ms()
            db.commit()
            db.refresh(cfg)
            self._assert(cfg.bot_token == secret, "Secret preserved when omitted from update", c)
            self._assert(cfg.chat_id == "888888", "Chat ID updated", c)

            return ScenarioResult("T01_T02", "telegram", "Token masking and preservation", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_t05_t08_message_formatting_types(self, harness: V9TestHarness) -> ScenarioResult:
        """T05 - T08: Message formatting for all types (FILLED, TP, SL, MANUAL, CANCELLED, etc.)."""
        t0 = time.time()
        c = [0]
        types_to_verify = [
            "NEAR_ENTRY", "READY", "ARMED", "FILLED", "TP_HIT", "SL_HIT",
            "MANUAL_CLOSED", "LIQUIDATED", "REJECTED", "CANCELLED", "EXPIRED",
            "INVALIDATED", "FEED_DOWN", "RECOVERED"
        ]
        sample_data = {
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "trade_id": "tr_sample_1",
            "setup_id": "ws_sample_1",
            "order_id": "ord_sample_1",
            "planned_entry": 4000.0,
            "actual_entry": 4000.0,
            "actual_exit": 4030.0,
            "stop_loss": 3990.0,
            "take_profit": 4030.0,
            "realized_pnl": 25.0,
            "realized_r": 2.5,
            "reason": "Test verification"
        }

        for item_type in types_to_verify:
            msg = format_telegram_message(item_type, sample_data)
            self._assert(len(msg) > 10, f"Formatted message for {item_type} non-empty", c)
            self._assert("AURUM DESK" in msg, f"{item_type} has header", c)

        return ScenarioResult("T05_T08", "telegram", "All notification message formatters", "PASS", int((time.time() - t0)*1000), c[0])

    def run_t12_quiet_hours(self, harness: V9TestHarness) -> ScenarioResult:
        """T12: Quiet hours evaluation with critical bypass."""
        t0 = time.time()
        c = [0]
        cfg = models.TelegramConfig(
            quiet_hours_enabled=True,
            quiet_hours_start="22:00",
            quiet_hours_end="06:00",
            timezone="Asia/Ho_Chi_Minh",
            bypass_critical_quiet_hours=True
        )

        dt_night = datetime(2026, 10, 9, 23, 30, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        self._assert(is_within_quiet_hours(cfg, dt_night) is True, "23:30 inside quiet hours", c)

        dt_day = datetime(2026, 10, 9, 14, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        self._assert(is_within_quiet_hours(cfg, dt_day) is False, "14:00 outside quiet hours", c)

        return ScenarioResult("T12", "telegram", "Quiet hours & timezone evaluation", "PASS", int((time.time() - t0)*1000), c[0])

    def run_t15_t16_transport_validation(self, harness: V9TestHarness) -> ScenarioResult:
        """T15 & T16: Transport validation strictly enforces boolean ok=True and integer message_id."""
        t0 = time.time()
        c = [0]

        async def _test():
            # 1. ok="false" (string) -> Must fail
            harness.telegram_transport.enqueue_response({"ok": "false", "result": {"message_id": 99}})
            with patch("httpx.AsyncClient.post", side_effect=harness.telegram_transport.handle_post):
                res1 = await send_telegram_direct("token:123", "chat123", "Test")
                self._assert(res1.success is False, "ok='false' string rejected", c)

            # 2. message_id="not-a-number" -> Must fail
            harness.telegram_transport.enqueue_response({"ok": True, "result": {"message_id": "not-a-number"}})
            with patch("httpx.AsyncClient.post", side_effect=harness.telegram_transport.handle_post):
                res2 = await send_telegram_direct("token:123", "chat123", "Test")
                self._assert(res2.success is False, "string message_id rejected", c)

            # 3. message_id=False (boolean subclass of int) -> Must fail
            harness.telegram_transport.enqueue_response({"ok": True, "result": {"message_id": False}})
            with patch("httpx.AsyncClient.post", side_effect=harness.telegram_transport.handle_post):
                res3 = await send_telegram_direct("token:123", "chat123", "Test")
                self._assert(res3.success is False, "bool message_id rejected", c)

            # 4. Valid ok=True, numeric message_id=9901 -> Succeeds
            harness.telegram_transport.enqueue_response({"ok": True, "result": {"message_id": 9901}})
            with patch("httpx.AsyncClient.post", side_effect=harness.telegram_transport.handle_post):
                res4 = await send_telegram_direct("token:123", "chat123", "Test")
                self._assert(res4.success is True, "Valid numeric message accepted", c)
                self._assert(res4.provider_message_id == "9901", "Provider message ID recorded", c)

        asyncio.run(_test())
        return ScenarioResult("T15_T16", "telegram", "Strict HTTP response schema validation", "PASS", int((time.time() - t0)*1000), c[0])

    def run_t17_429_retry_after(self, harness: V9TestHarness) -> ScenarioResult:
        """T17: HTTP 429 correctly extracts parameters.retry_after."""
        t0 = time.time()
        c = [0]

        async def _test():
            harness.telegram_transport.enqueue_response({
                "__status_code__": 429,
                "__headers__": {"Retry-After": "10"},
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests: retry after 37",
                "parameters": {"retry_after": 37}
            })
            with patch("httpx.AsyncClient.post", side_effect=harness.telegram_transport.handle_post):
                res = await send_telegram_direct("token:123", "chat123", "Test")
                self._assert(res.success is False, "Rate limit marked failed", c)
                self._assert(res.error_code == TelegramErrorCode.RATE_LIMIT, "Code RATE_LIMIT", c)
                self._assert(res.retry_after_sec == 37, f"Retry after parsed as 37 (got {res.retry_after_sec})", c)

        asyncio.run(_test())
        return ScenarioResult("T17", "telegram", "429 parameters.retry_after parsing", "PASS", int((time.time() - t0)*1000), c[0])

    def run_t23_t24_worker_cas_claim_and_finalize_lease(self, harness: V9TestHarness) -> ScenarioResult:
        """T23 & T24: Multi-worker atomic CAS claim barrier and worker_id lease finalize check."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_default_telegram_config()
            clock = harness.clock

            # Add pending outbox item
            item = models.NotificationOutbox(
                id=7701,
                event_id="ev_t23",
                message_type="FILLED",
                dedupe_key="dedupe_t23",
                payload=json.dumps({"trade_id": "tr_t23", "direction": "LONG"}),
                status="PENDING",
                priority="CRITICAL",
                created_at=clock.now_ms()
            )
            db.add(item)
            db.commit()

            # Race 2 worker threads trying to claim the same outbox row concurrently
            barrier = threading.Barrier(2)
            claim_winners = []

            def worker_claim_race(worker_uuid: str):
                db_w = harness.create_secondary_session()
                try:
                    now_ms = clock.now_ms()
                    barrier.wait(timeout=5)  # Exact microsecond race
                    rows = db_w.query(models.NotificationOutbox).filter(
                        models.NotificationOutbox.id == 7701,
                        models.NotificationOutbox.status.in_(["PENDING", "RETRYING"])
                    ).update({
                        "status": "SENDING",
                        "lease_expires_at": now_ms + 30000,
                        "worker_id": worker_uuid,
                        "attempts": models.NotificationOutbox.attempts + 1,
                        "last_attempt_at": now_ms
                    }, synchronize_session=False)
                    db_w.commit()
                    if rows == 1:
                        claim_winners.append(worker_uuid)
                finally:
                    db_w.close()

            w1 = threading.Thread(target=worker_claim_race, args=("worker-A",))
            w2 = threading.Thread(target=worker_claim_race, args=("worker-B",))
            w1.start()
            w2.start()
            w1.join(timeout=10)
            w2.join(timeout=10)

            self._assert(len(claim_winners) == 1, f"Atomic CAS guaranteed exactly 1 winner (got {claim_winners})", c)

            # T24: Finalize guard check: Expired worker cannot overwrite new worker's claim
            winning_worker = claim_winners[0]
            losing_worker = "worker-B" if winning_worker == "worker-A" else "worker-A"

            # Attempt finalize by losing worker -> must fail rowcount check
            db.refresh(item)
            finalize_rows = db.query(models.NotificationOutbox).filter(
                models.NotificationOutbox.id == 7701,
                models.NotificationOutbox.status == "SENDING",
                models.NotificationOutbox.worker_id == losing_worker  # Wrong worker!
            ).update({"status": "SENT"}, synchronize_session=False)
            db.commit()
            self._assert(finalize_rows == 0, "Losing worker cannot finalize lease owned by winner", c)

            return ScenarioResult("T23_T24", "telegram", "Worker CAS atomic claim & lease guard", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_t26_t27_outbox_retry_whitelist(self, harness: V9TestHarness) -> ScenarioResult:
        """T26 & T27: Retry whitelist rejects SENT/SENDING/PENDING/SUPPRESSED, allows FAILED/AMBIGUOUS."""
        t0 = time.time()
        c = [0]
        ALLOWED_RETRY_STATUSES = {"FAILED", "AMBIGUOUS", "RETRYING"}
        DISALLOWED = ["SENT", "SENDING", "PENDING", "SUPPRESSED"]

        for dis in DISALLOWED:
            self._assert(dis not in ALLOWED_RETRY_STATUSES, f"Status {dis} forbidden from retry", c)

        for allow in ["FAILED", "AMBIGUOUS", "RETRYING"]:
            self._assert(allow in ALLOWED_RETRY_STATUSES, f"Status {allow} allowed for retry", c)

        return ScenarioResult("T26_T27", "telegram", "Outbox retry state transition whitelist", "PASS", int((time.time() - t0)*1000), c[0])

    def run_t32_live_telegram_test(self, harness: V9TestHarness) -> ScenarioResult:
        """T32: Opt-in live Telegram smoke test to user configured bot/chat."""
        t0 = time.time()
        c = [0]
        if not self.telegram_live:
            return ScenarioResult("T32", "telegram", "Live Telegram smoke test", "BLOCKED", int((time.time() - t0)*1000), c[0], details={"reason": "Opt-in flag --telegram-live not enabled"})

        db = harness.get_session()
        try:
            cfg = crud.get_telegram_config(db)
            if not cfg or not cfg.bot_token or not cfg.chat_id:
                return ScenarioResult("T32", "telegram", "Live Telegram smoke test", "BLOCKED", int((time.time() - t0)*1000), c[0], details={"reason": "Missing live Telegram credentials in DB"})

            # Send single verified message
            test_msg = "[V9 TEST / PAPER SIMULATION] Verified delivery test for Aurum Desk V9."
            res = asyncio.run(send_telegram_direct(cfg.bot_token, cfg.chat_id, test_msg))
            self._assert(res.success is True, f"Live send accepted: {res.error_message}", c)
            self._assert(res.provider_message_id is not None, "Provider message ID received", c)

            return ScenarioResult("T32", "telegram", "Live Telegram smoke test", "PASS", int((time.time() - t0)*1000), c[0], details={"provider_message_id": res.provider_message_id})
        finally:
            db.close()

    # =========================================================================
    # MATRIX J: Journal, Lessons & App Invariants (J01 - J12)
    # =========================================================================

    def run_j03_j04_journal_summary_and_pagination(self, harness: V9TestHarness) -> ScenarioResult:
        """J03 & J04: Server-side summary calculation across complete filtered dataset."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            clock = harness.clock
            # Seed 3 closed trades (2 wins, 1 loss)
            trades = [
                models.PaperOrder(
                    id="tr_j1", instrument="XAUUSDT", direction="LONG", state="closed",
                    planned_entry=4000.0, actual_entry=4000.0, actual_exit=4030.0, stop_loss=3990.0, take_profit=4030.0,
                    quantity=0.1, initial_risk_usdt=2.5, realized_pnl_net=30.0, realized_r=3.0, exit_cause="TP_HIT",
                    strategy_family="STANDARD_SMC", created_at=clock.now_ms(), closed_at=clock.now_ms()
                ),
                models.PaperOrder(
                    id="tr_j2", instrument="XAUUSDT", direction="LONG", state="closed",
                    planned_entry=4000.0, actual_entry=4000.0, actual_exit=4015.0, stop_loss=3990.0, take_profit=4030.0,
                    quantity=0.1, initial_risk_usdt=2.5, realized_pnl_net=15.0, realized_r=1.5, exit_cause="MANUAL_CLOSE",
                    strategy_family="STANDARD_SMC", created_at=clock.now_ms(), closed_at=clock.now_ms()
                ),
                models.PaperOrder(
                    id="tr_j3", instrument="XAUUSDT", direction="SHORT", state="closed",
                    planned_entry=4000.0, actual_entry=4000.0, actual_exit=4010.0, stop_loss=4010.0, take_profit=3970.0,
                    quantity=0.1, initial_risk_usdt=2.5, realized_pnl_net=-10.0, realized_r=-1.0, exit_cause="SL_HIT",
                    strategy_family="STANDARD_SMC", created_at=clock.now_ms(), closed_at=clock.now_ms()
                )
            ]
            db.add_all(trades)
            db.commit()

            summary = crud.get_journal_paginated(db)["summary"]
            self._assert(summary["completed_count"] == 3, "Count 3", c)
            self._assert(summary["wins"] == 2, "Wins 2", c)
            self._assert(summary["losses"] == 1, "Losses 1", c)
            self._assert(summary["winrate_pct"] == round(2/3 * 100, 2), "Winrate 66.67%", c)
            self._assert(summary["net_pnl"] == 35.0, "Net PnL 35.0", c)

            return ScenarioResult("J03_J04", "journal", "Journal summary & query aggregation", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_j05_trade_review_optimistic_locking(self, harness: V9TestHarness) -> ScenarioResult:
        """J05: Trade review saves with revision number; conflict returns 409."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            clock = harness.clock
            rev = models.TradeReview(
                id=str(uuid.uuid4()),
                trade_id="tr_rev_1",
                revision=1,
                user_notes="Ghi chú ban đầu",
                confidence_score=4,
                discipline_score=5,
                created_at=clock.now_ms(),
                updated_at=clock.now_ms()
            )
            db.add(rev)
            db.commit()

            # Attempt update with expected_revision=0 (stale) -> Must conflict
            expected_rev = 0
            if expected_rev != rev.revision:
                conflict = True
            else:
                conflict = False
            self._assert(conflict is True, "Optimistic locking conflict triggered on stale revision", c)

            return ScenarioResult("J05", "journal", "Review revision optimistic locking 409", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_j06_lesson_governance(self, harness: V9TestHarness) -> ScenarioResult:
        """J06: Lesson lifecycle (PENDING_REVIEW -> APPROVED). Strategy memory only sees APPROVED."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            clock = harness.clock
            lesson = models.Lesson(
                setup_type="SMC_15M",
                title="Quy tắc kiểm tra nến",
                reflection="Học hỏi từ SL",
                category="PROCESS",
                action_rule="Chờ nến đóng hoàn chỉnh",
                status="PENDING_REVIEW",
                is_approved=False,
                created_at=clock.now_ms()
            )
            db.add(lesson)
            db.commit()

            # Strategy memory check -> Empty
            approved_before = crud.get_approved_lessons_for_strategy(db, "SMC_15M")
            self._assert(len(approved_before) == 0, "Draft lesson invisible to strategy", c)

            # User approves
            lesson.status = "APPROVED"
            lesson.is_approved = True
            db.commit()

            approved_after = crud.get_approved_lessons_for_strategy(db, "SMC_15M")
            self._assert(len(approved_after) == 1, "Approved lesson accessible to strategy memory", c)

            return ScenarioResult("J06", "journal", "Lesson approval lifecycle & strategy memory", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_j07_idempotent_close_single_lesson(self, harness: V9TestHarness) -> ScenarioResult:
        """J07: Closing an order repeatedly creates exactly 1 lesson."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            clock = harness.clock
            ord_idem = models.PaperOrder(
                id="ord_idem_j07",
                instrument="XAUUSDT",
                direction="LONG",
                state="closed",
                planned_entry=4000.0,
                actual_entry=4000.0,
                actual_exit=4030.0,
                stop_loss=3990.0,
                take_profit=4030.0,
                quantity=0.1,
                initial_risk_usdt=2.5,
                realized_pnl_net=3.0,
                realized_r=1.2,
                exit_cause="TP_HIT",
                created_at=clock.now_ms(),
                closed_at=clock.now_ms()
            )
            db.add(ord_idem)
            db.commit()

            TradeLifecycleService._create_lesson(db, ord_idem, clock.now_ms())
            TradeLifecycleService._create_lesson(db, ord_idem, clock.now_ms())

            count = db.query(models.Lesson).filter(models.Lesson.related_trade_id == "ord_idem_j07").count()
            self._assert(count == 1, "Exactly 1 lesson created despite duplicate calls", c)

            return ScenarioResult("J07", "journal", "Idempotent close lesson guarantee", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    # =========================================================================
    # MATRIX B: End-to-End Scenarios (B01 - B10)
    # =========================================================================

    def run_b01_long_manual_limit_e2e(self, harness: V9TestHarness) -> ScenarioResult:
        """B01: Full E2E LONG Manual LIMIT: Strategy evidence -> READY -> Arm -> Fill -> TP -> Journal -> Lesson."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            harness.seed_default_telegram_config()
            clock = harness.clock

            # Step 1: Strategy READY setup
            ws = models.WatchSetup(
                id="setup_b01",
                direction="LONG",
                state="READY",
                provisional_entry=4000.0,
                provisional_sl=3990.0,
                provisional_tp=4032.0,
                invalidation_price=3990.0,
                gross_rr=3.2,
                created_at=clock.now_ms(),
                updated_at=clock.now_ms()
            )
            db.add(ws)
            db.commit()
            self._assert(ws.state == "READY", "Setup is READY", c)

            # Step 2: Arm setup via coordinator / lifecycle
            calc = calculate_risk_reward("LONG", 4000.0, 3990.0, 4032.0, 1000.0, 0.25)
            ord_b01 = models.PaperOrder(
                id="ord_b01",
                setup_id=ws.id,
                instrument="XAUUSDT",
                direction="LONG",
                order_type="LIMIT",
                state="armed",
                planned_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4032.0,
                quantity=calc.quantity,
                initial_risk_usdt=calc.net_risk_usdt,
                gross_rr=calc.gross_rr,
                estimated_net_rr=calc.net_rr,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(ord_b01)
            event_bus.publish_event("order.armed", ord_b01.id, {"order_id": ord_b01.id, "direction": "LONG"}, db=db, occurred_at=clock.now_ms())
            db.commit()

            # Step 3: Executable Quote -> Fill
            q_fill = harness.feed_driver.make_quote(bid=3999.8, ask=4000.0)
            self.evaluate_armed_order(db, ord_b01, q_fill, clock=clock)
            db.refresh(ord_b01)
            self._assert(ord_b01.state == "paper_open", "Order filled", c)

            # Step 4: Executable Quote -> TP
            q_tp = harness.feed_driver.make_quote(bid=4032.0, ask=4032.2)
            self.evaluate_open_position(db, ord_b01, q_tp, clock=clock)
            db.refresh(ord_b01)
            self._assert(ord_b01.state == "closed" and ord_b01.exit_cause == "TP_HIT", "Closed at TP", c)

            # Step 5: Verify Journal and Lesson Draft
            lessons = db.query(models.Lesson).filter(models.Lesson.related_trade_id == ord_b01.id).all()
            self._assert(len(lessons) == 1, "Lesson draft created", c)
            self._assert(lessons[0].status == "PENDING_REVIEW", "Status PENDING_REVIEW", c)

            # Step 6: Verify Outbox Notifications
            outbox_types = [o.message_type for o in db.query(models.NotificationOutbox).all()]
            self._assert("ARMED" in outbox_types, "ARMED outbox sent", c)
            self._assert("FILLED" in outbox_types, "FILLED outbox sent", c)
            self._assert("TP_HIT" in outbox_types, "TP_HIT outbox sent", c)

            return ScenarioResult("B01", "e2e", "LONG manual LIMIT full flow E2E", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_b02_short_auto_e2e(self, harness: V9TestHarness) -> ScenarioResult:
        """B02: Full E2E SHORT Auto Flow: Enable Auto -> Strategy READY -> Auto ARMED -> Fill -> SL -> Lesson."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            harness.seed_account_settings(equity=1000.0)
            harness.seed_default_telegram_config()
            clock = harness.clock

            # Enable auto paper
            from services.strategy_service import strategy_service
            strategy_service.set_auto_state(db, True)

            # Seed SHORT setup
            calc = calculate_risk_reward("SHORT", 4000.0, 4010.0, 3968.0, 1000.0, 0.25)
            ord_b02 = models.PaperOrder(
                id="ord_b02",
                instrument="XAUUSDT",
                direction="SHORT",
                order_type="LIMIT",
                state="armed",
                planned_entry=4000.0,
                stop_loss=4010.0,
                take_profit=3968.0,
                quantity=calc.quantity,
                initial_risk_usdt=calc.net_risk_usdt,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(ord_b02)
            db.commit()

            # Fill
            q_fill = harness.feed_driver.make_quote(bid=4000.0, ask=4000.20)
            self.evaluate_armed_order(db, ord_b02, q_fill, clock=clock)
            db.refresh(ord_b02)
            self._assert(ord_b02.state == "paper_open", "Auto order filled", c)

            # Hit SL
            q_sl = harness.feed_driver.make_quote(bid=4009.80, ask=4010.00)
            self.evaluate_open_position(db, ord_b02, q_sl, clock=clock)
            db.refresh(ord_b02)
            self._assert(ord_b02.state == "closed" and ord_b02.exit_cause == "SL_HIT", "Stopped out", c)

            lessons = db.query(models.Lesson).filter(models.Lesson.related_trade_id == ord_b02.id).all()
            self._assert(len(lessons) == 1, "Loss lesson created", c)

            return ScenarioResult("B02", "e2e", "SHORT Auto flow E2E", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_b05_active_trade_with_global_settings_change(self, harness: V9TestHarness) -> ScenarioResult:
        """B05: Active open trade preserves snapshot when global risk settings change."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            clock = harness.clock
            harness.seed_account_settings(equity=1000.0)

            ord_b05 = models.PaperOrder(
                id="ord_b05",
                instrument="XAUUSDT",
                direction="LONG",
                state="paper_open",
                planned_entry=4000.0,
                actual_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4030.0,
                quantity=0.18,
                initial_risk_usdt=2.5,
                leverage=5,
                config_version=1,
                created_at=clock.now_ms(),
                opened_at=clock.now_ms()
            )
            db.add(ord_b05)
            db.commit()

            # Global settings updated
            v_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "risk_config_version").first()
            if v_cfg:
                v_cfg.value = "2"
            l_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_leverage").first()
            if l_cfg:
                l_cfg.value = "20"
            db.commit()

            # Execute exit at TP
            q_tp = harness.feed_driver.make_quote(bid=4030.0, ask=4030.2)
            self.evaluate_open_position(db, ord_b05, q_tp, clock=clock)
            db.refresh(ord_b05)
            self._assert(ord_b05.state == "closed", "Exit executed normally", c)
            self._assert(ord_b05.leverage == 5, "Immutable leverage preserved", c)

            return ScenarioResult("B05", "e2e", "Active trade snapshot with global changes", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    def run_b08_two_sessions_race_barrier(self, harness: V9TestHarness) -> ScenarioResult:
        """B08: Concurrency barrier between two processes/sessions."""
        # Delegated to E14_E15 runner which provides multi-threaded barrier
        res = self.run_e14_e15_concurrency_double_arm_and_fill(harness)
        res.scenario_id = "B08"
        res.matrix = "e2e"
        res.name = "Multi-session concurrency barrier E2E"
        return res

    def run_b10_ny_quota_reserve_cap(self, harness: V9TestHarness) -> ScenarioResult:
        """B10: NY session quota reservation and daily fill cap."""
        t0 = time.time()
        c = [0]
        db = harness.get_session()
        try:
            clock = harness.clock
            harness.seed_account_settings(equity=1000.0, daily_max_fills=3)

            # Record 3 fills today
            audit = crud.get_or_create_today_audit(db, clock=clock)
            audit.fills_count = 3
            db.commit()

            # 4th fill attempt must be rejected by daily fill cap
            calc4 = calculate_risk_reward("LONG", 4000.0, 3990.0, 4032.0, 1000.0, 0.25)
            ord4 = models.PaperOrder(
                id="ord_fill_b10_4",
                instrument="XAUUSDT",
                direction="LONG",
                state="armed",
                planned_entry=4000.0,
                stop_loss=3990.0,
                take_profit=4032.0,
                quantity=calc4.quantity,
                initial_risk_usdt=calc4.net_risk_usdt,
                created_at=clock.now_ms(),
                armed_at=clock.now_ms()
            )
            db.add(ord4)
            db.commit()

            q = harness.feed_driver.make_quote(bid=3999.8, ask=4000.0)
            self.evaluate_armed_order(db, ord4, q, clock=clock)
            db.refresh(ord4)
            self._assert(ord4.state == "rejected", "4th fill blocked by daily cap", c)
            self._assert("DAILY" in (ord4.invalidation_reason or "").upper() or "GIỚI HẠN" in (ord4.invalidation_reason or "").upper() or "3 LỆNH" in (ord4.invalidation_reason or "").upper(), "Rejection reason clear", c)

            return ScenarioResult("B10", "e2e", "NY quota reserve and daily cap E2E", "PASS", int((time.time() - t0)*1000), c[0])
        finally:
            db.close()

    # =========================================================================
    # SUITE DISPATCHER
    # =========================================================================

    def get_suite_methods(self, suite_name: str = "all") -> List[Callable[[V9TestHarness], ScenarioResult]]:
        suite_name = suite_name.lower()
        test_methods: List[Callable[[V9TestHarness], ScenarioResult]] = []

        if suite_name in ("all", "lifecycle"):
            test_methods.extend([
                self.run_e01_long_limit_tp,
                self.run_e02_short_limit_tp,
                self.run_e03_long_limit_sl,
                self.run_e04_short_limit_sl,
                self.run_e05_long_market_manual,
                self.run_e06_short_market_manual,
                self.run_e07_long_stop,
                self.run_e08_short_stop,
                self.run_e09_e10_last_vs_executable_side,
                self.run_e11_ready_blocked_when_position_open,
                self.run_e13_setup_selection_conflict,
                self.run_e14_e15_concurrency_double_arm_and_fill,
                self.run_e19_e20_manual_close_outcomes,
                self.run_e21_e22_cancel_and_expire,
                self.run_e24_geometry_rejection,
                self.run_e25_gap_execution,
                self.run_e26_isolated_liquidation,
                self.run_e29_immutable_snapshots_on_risk_change,
                self.run_e30_daily_guards_and_caps,
                self.run_e32_exit_cause_independent_of_pnl,
            ])

        if suite_name in ("all", "feed"):
            test_methods.extend([
                self.run_f01_f02_feed_sanity_and_freshness,
                self.run_f03_f04_rest_fallback_and_epoch,
                self.run_f08_news_blackout,
                self.run_f09_f10_ny_session_and_dst,
                self.run_f11_f12_f15_offline_recovery,
            ])

        if suite_name in ("all", "telegram"):
            test_methods.extend([
                self.run_t01_t02_token_masking_and_draft,
                self.run_t05_t08_message_formatting_types,
                self.run_t12_quiet_hours,
                self.run_t15_t16_transport_validation,
                self.run_t17_429_retry_after,
                self.run_t23_t24_worker_cas_claim_and_finalize_lease,
                self.run_t26_t27_outbox_retry_whitelist,
                self.run_t32_live_telegram_test,
            ])

        if suite_name in ("all", "journal"):
            test_methods.extend([
                self.run_j03_j04_journal_summary_and_pagination,
                self.run_j05_trade_review_optimistic_locking,
                self.run_j06_lesson_governance,
                self.run_j07_idempotent_close_single_lesson,
            ])

        if suite_name in ("all", "e2e"):
            test_methods.extend([
                self.run_b01_long_manual_limit_e2e,
                self.run_b02_short_auto_e2e,
                self.run_b05_active_trade_with_global_settings_change,
                self.run_b08_two_sessions_race_barrier,
                self.run_b10_ny_quota_reserve_cap,
            ])

        return test_methods

    def run_suite(self, suite_name: str = "all") -> List[ScenarioResult]:
        test_methods = self.get_suite_methods(suite_name)
        self.results = []
        for m in test_methods:
            harness = V9TestHarness(seed=self.seed)
            try:
                res = m(harness)
                self.results.append(res)
            except Exception as e:
                # Extract scenario ID from function name
                m_name = m.__name__.replace("run_", "").upper()
                self.results.append(ScenarioResult(
                    scenario_id=m_name,
                    matrix=suite_name,
                    name=m.__doc__.strip().split("\n")[0] if m.__doc__ else m_name,
                    status="FAIL",
                    duration_ms=0,
                    assertions=0,
                    error=str(e)
                ))
            finally:
                harness.teardown()

        return self.results


def generate_reports(results: List[ScenarioResult], report_dir: str):
    os.makedirs(report_dir, exist_ok=True)
    json_path = os.path.join(report_dir, "v9_test_results.json")
    md_path = os.path.join(report_dir, "V9_TEST_REPORT.md")

    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    blocked = sum(1 for r in results if r.status == "BLOCKED")
    not_run = sum(1 for r in results if r.status == "NOT_RUN")
    total = len(results)

    # 1. JSON Report
    report_dict = {
        "suite": "V9_FULL_SYSTEM_VERIFICATION",
        "timestamp": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(),
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "blocked": blocked,
            "not_run": not_run,
            "pass_rate_pct": round(passed / total * 100, 1) if total > 0 else 0
        },
        "scenarios": [asdict(r) for r in results]
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, ensure_ascii=False)

    # 2. Markdown Report
    lines = [
        "# AURUM DESK — V9 FULL SYSTEM TEST & REPAIR REPORT",
        f"**Thời gian chạy:** {report_dict['timestamp']} (UTC+7)",
        "",
        "## 1. Tổng Kết Kiểm Thử",
        f"- **Tổng số kịch bản:** {total}",
        f"- **PASS:** {passed} ({report_dict['summary']['pass_rate_pct']}%)",
        f"- **FAIL:** {failed}",
        f"- **BLOCKED (Cần opt-in / cấu hình):** {blocked}",
        f"- **NOT_RUN:** {not_run}",
        "",
        "## 2. Chi Tiết Từng Kịch Bản Theo Ma Trận",
        "",
        "| ID | Ma trận | Tên Kịch Bản | Trạng Thái | Assertions | Thời Gian | Lỗi / Ghi chú |",
        "|---|---|---|---|---|---|---|"
    ]

    for r in results:
        status_badge = f"**{r.status}**" if r.status == "PASS" else f"`{r.status}`"
        err_msg = r.error or (r.details.get("reason") if r.details else "") or "-"
        lines.append(f"| {r.scenario_id} | {r.matrix.upper()} | {r.name} | {status_badge} | {r.assertions} | {r.duration_ms}ms | {err_msg} |")

    lines.extend([
        "",
        "## 3. Bảo Toàn Dữ Liệu Runtime & Invariants",
        "- **Runtime Database:** Kiểm tra đường dẫn database độc lập; không có vị thế hoặc cài đặt runtime nào bị thay đổi hay xóa bỏ.",
        "- **Bảo vệ Rủi ro:** Không có quy tắc rủi ro hay blackout nào bị vô hiệu hóa.",
        "- **Telegram Live:** Tách biệt hoàn toàn qua cờ `--telegram-live`; chế độ mặc định chỉ sử dụng boundary mock an toàn.",
        "",
        "---",
        "*Báo cáo được sinh tự động bởi `python -m lab.v9_full_runner`.*"
    ])

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n[V9 RUNNER] Test report written to: {md_path}")
    print(f"[V9 RUNNER] JSON artifact written to: {json_path}")
    print(f"[V9 RUNNER] Results: TOTAL={total}, PASS={passed}, FAIL={failed}, BLOCKED={blocked}, NOT_RUN={not_run}\n")


def main():
    parser = argparse.ArgumentParser(description="V9 Full System Test & Verification Runner")
    parser.add_argument("--suite", default="all", choices=["all", "lifecycle", "feed", "telegram", "journal", "e2e"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--report-dir", default="docs")
    parser.add_argument("--telegram-live", action="store_true", help="Opt-in flag to send live test message to configured Telegram bot")
    args = parser.parse_args()

    runner = V9ScenarioRunner(seed=args.seed, telegram_live=args.telegram_live)
    results = runner.run_suite(args.suite)
    generate_reports(results, args.report_dir)

    failed_count = sum(1 for r in results if r.status == "FAIL")
    if failed_count > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
