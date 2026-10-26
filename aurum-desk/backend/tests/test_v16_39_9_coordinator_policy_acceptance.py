"""
backend/tests/test_v16_39_9_coordinator_policy_acceptance.py
V16.39.9 — FINAL ACCEPTANCE ALIGNMENT AND FORWARD PAPER READINESS
TEST-FIRST, DETERMINISTIC GATES, PRESERVE CURRENT TRADING FLOW

Verifies the complete execution coordinator call graph under real DB conditions:
evaluate_cycle -> ARM order ->
ExecutionCoordinator.evaluate_orders_sync -> execute_fill ->
TradeLifecycleService.process_exit_tick -> close / review / lesson.

Covers:
1. Coordinator LONG Limit Fill & Idempotency (Rules 031, 039, 057, 062)
2. Coordinator SHORT Limit Fill & Geometry Invariance (Rules 058, 059)
3. Active Trading Policy: NY Window Allows Fill (Rules 033, 034, 046, 047, 068)
4. Active Trading Policy: Outside NY Window Blocks Fill (Rules 048, 069, 072)
5. Active Trading Policy: Daily Fill Cap (3 Fills) Blocks Fill (Rules 048, 070)
6. Active Trading Policy: News Blackout Blocks Fill (Rules 036, 071)
"""
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

import models, crud
from models import Base, PaperOrder, Lesson, PostTradeReviewRecord, EconomicNews
from services.execution_coordinator import ExecutionCoordinator
from services.lesson_rule_service import LessonRuleService
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trade_lifecycle_service import TradeLifecycleService
from services.trading_policy_service import TradingPolicyService, IClock
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series,
    build_short_fixture_series,
    make_valid_quote,
)


# ==============================================================================
# TEST ENVIRONMENT FIXTURES & HELPERS
# ==============================================================================

class MockClock(IClock):
    """Deterministic injectable clock for session, quota, and audit calculations."""
    def __init__(self, dt: datetime):
        self._dt = dt

    def now(self) -> datetime:
        return self._dt

    def now_datetime(self) -> datetime:
        return self._dt

    def now_ms(self) -> int:
        return int(self._dt.timestamp() * 1000)

    def monotonic_s(self) -> float:
        return self._dt.timestamp()

    def set_datetime(self, dt: datetime):
        self._dt = dt


def make_ticker_dict(bid: float, ask: float, now_ms: int) -> Dict[str, Any]:
    """
    Constructs an authoritative raw ticker dictionary compatible with
    ExecutionCoordinator.evaluate_orders_sync and QuoteValidator.validate_ticker.
    """
    return {
        "bid": float(bid),
        "ask": float(ask),
        "last": float((bid + ask) / 2.0),
        "server_time": int(now_ms),
        "observed_at": int(now_ms),
    }


class IsolatedCoordinatorPolicyEnv:
    """Isolated test environment managing in-memory SQLite with StaticPool."""
    def __init__(self, policy_active: bool = False, entry_session_policy: str = "ALL_SESSIONS_WITH_NY_RESERVE"):
        self.policy_active = policy_active
        self.entry_session_policy = entry_session_policy
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        init_db = self.SessionFactory()
        try:
            policy = TradingPolicyService.get_or_create_policy(init_db)
            policy.symbol = "XAUUSDT"
            policy.mode = "PAPER"
            policy.entry_session_policy = entry_session_policy
            policy.ny_timezone = "America/New_York"
            policy.daily_timezone = "Asia/Ho_Chi_Minh"
            policy.ny_entry_start = "08:00"
            policy.ny_entry_end = "11:00"
            policy.ny_fallback_start = "10:30"
            policy.max_daily_fills = 3
            policy.daily_min_goal = 2
            policy.ny_min_fills = 1
            policy.reserve_ny_slot = True
            policy.is_active = policy_active
            init_db.commit()
        finally:
            init_db.close()

        LessonRuleService.invalidate_cache()

    def make_session(self) -> Session:
        session = self.SessionFactory()
        policy = TradingPolicyService.get_or_create_policy(session)
        policy.is_active = self.policy_active
        policy.entry_session_policy = self.entry_session_policy
        return session

    def close(self):
        LessonRuleService.invalidate_cache()
        self.engine.dispose()


def make_test_scalp_config(
    quality_arm_threshold: float = 70.0,
    min_net_rr: float = 1.5,
) -> M1ScalpConfig:
    """Constructs effective production M1ScalpConfig for CG01."""
    return M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        timing_profile="CG01_NY_PAPER_EXPANDED",
        daily_max_cap=3,
        min_net_rr=min_net_rr,
        quality_scoring_enabled=True,
        lesson_score_adjustment_enabled=True,
        quality_arm_threshold=quality_arm_threshold,
    )


# ==============================================================================
# TESTS
# ==============================================================================

def test_010_coordinator_long_limit_fill_and_no_double_fill():
    """
    Rules 031, 039, 057, 062:
    Verifies full call graph for LONG LIMIT order:
    1. evaluate_cycle ARMs order with planned_entry.
    2. ExecutionCoordinator.evaluate_orders_sync processes quote (ask <= planned_entry) -> fills to paper_open.
    3. Re-evaluating with same/new quote returns early without double filling.
    4. process_exit_tick closes position at TP with positive net PnL.
    5. PostTradeReviewRecord and unapproved pending Lesson are created in DB.
    """
    env = IsolatedCoordinatorPolicyEnv(policy_active=False)
    session = env.make_session()

    try:
        ny_tz = ZoneInfo("America/New_York")
        dt = datetime(2026, 10, 20, 10, 0, tzinfo=ny_tz)
        clock = MockClock(dt)
        t_start = int(dt.timestamp() * 1000)
        now_ms = t_start + 600_000

        c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
        quote_arm = make_valid_quote(2625.5, 2625.7, now_ms)
        poi = WatchedZone(
            zone_id="poi_coord_long",
            symbol="XAUUSDT",
            timeframe="15M",
            zone_type="FVG",
            direction="BULLISH",
            top=2625.0,
            bottom=2620.0,
            created_at=t_start - 3600_000,
            known_at=t_start - 1800_000,
            touch_count=0,
        )
        cfg = make_test_scalp_config(quality_arm_threshold=70.0)

        orch = ScalpWatcherOrchestrator()
        orch.set_active_zones("XAUUSDT", [poi])
        orch.evaluate_cycle(
            session,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote_arm,
            candles_1m=c_1m,
            candles_5m=[],
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
            auto_enabled=True,
        )

        order = session.query(PaperOrder).filter(PaperOrder.state == "armed").first()
        assert order is not None, "Candidate must ARM"
        planned_entry = order.planned_entry
        planned_sl = order.stop_loss
        planned_tp = order.take_profit
        assert order.direction == "LONG"
        assert planned_tp > planned_entry > planned_sl

        # Step 2: Call ExecutionCoordinator.evaluate_orders_sync with ask hitting planned entry
        fill_time = now_ms + 15_000
        # For LONG LIMIT: triggered when current_ask <= planned_entry
        fill_ticker = make_ticker_dict(bid=planned_entry - 0.1, ask=planned_entry, now_ms=fill_time)

        coord = ExecutionCoordinator()
        coord.evaluate_orders_sync(session, ticker_override=fill_ticker, clock=clock, now_ms=fill_time)

        session.refresh(order)
        assert order.state == "paper_open", f"Order must transition to paper_open, got {order.state}"
        assert order.actual_entry == planned_entry, "Actual entry must match fill calculation"
        assert order.opened_at == fill_time, "Opened at time must be recorded"

        pos = crud.get_active_position(session)
        assert pos is not None, "Active position must exist after coordinator fill"
        assert pos.id == order.id

        # Step 3: Idempotency / No Double Fill (Rule 062)
        coord.evaluate_orders_sync(session, ticker_override=fill_ticker, clock=clock, now_ms=fill_time + 5_000)
        session.refresh(order)
        assert order.state == "paper_open", "State must remain paper_open"
        all_open = session.query(PaperOrder).filter(PaperOrder.state == "paper_open").all()
        assert len(all_open) == 1, "Must never create double position"

        # Step 4: Process exit tick at Take Profit (Rule 063)
        exit_time = fill_time + 30_000
        closed_order = TradeLifecycleService.process_exit_tick(
            session,
            current_bid=planned_tp + 0.5,
            current_ask=planned_tp + 0.7,
            now_ms=exit_time,
        )

        assert closed_order is not None
        assert closed_order.state == "closed"
        assert closed_order.exit_cause in ("TP_HIT", "TAKE_PROFIT")
        assert closed_order.realized_pnl_net > 0, "Net realized PnL must be positive on TP"

        # Step 5: Post-trade review and pending lesson
        review = session.query(PostTradeReviewRecord).filter(PostTradeReviewRecord.order_id == order.id).first()
        assert review is not None, "PostTradeReviewRecord must be created"

        pending_lessons = session.query(Lesson).filter(Lesson.related_trade_id == order.id).all()
        assert len(pending_lessons) > 0, "Pending lesson must be generated from trade"
        for l in pending_lessons:
            assert l.is_approved is False, "New lesson must remain unapproved until reviewed"
            assert l.enabled is False, "New lesson must remain disabled until reviewed"
    finally:
        session.close()
        env.close()


def test_020_coordinator_short_limit_fill_and_geometry():
    """
    Rules 058, 059:
    Verifies full coordinator fill for BEARISH SHORT candidate:
    1. evaluate_cycle creates ARMED SHORT order (TP < planned_entry < SL).
    2. ExecutionCoordinator.evaluate_orders_sync (bid >= planned_entry) triggers SHORT LIMIT fill.
    3. Order transitions to paper_open, direction remains strictly SHORT.
    4. TP exit processed via process_exit_tick.
    """
    env = IsolatedCoordinatorPolicyEnv(policy_active=False)
    session = env.make_session()

    try:
        ny_tz = ZoneInfo("America/New_York")
        dt = datetime(2026, 10, 15, 10, 15, tzinfo=ny_tz)
        clock = MockClock(dt)
        t_start = int(dt.timestamp() * 1000)
        now_ms = t_start + 540_000

        c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
        quote = make_valid_quote(2651.0, 2651.2, now_ms)
        poi_short = WatchedZone(
            zone_id="poi_coord_short",
            symbol="XAUUSDT",
            timeframe="15M",
            zone_type="FVG",
            direction="BEARISH",
            top=2655.0,
            bottom=2650.0,
            created_at=t_start - 3600_000,
            known_at=t_start - 1800_000,
            touch_count=0,
        )
        cfg = make_test_scalp_config(quality_arm_threshold=70.0)

        orch = ScalpWatcherOrchestrator()
        orch.set_active_zones("XAUUSDT", [poi_short])
        orch.evaluate_cycle(
            session,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote,
            candles_1m=c_1m,
            candles_5m=[],
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
            auto_enabled=True,
        )

        order = session.query(PaperOrder).filter(PaperOrder.state == "armed").first()
        assert order is not None, "Bearish candidate must ARM"
        assert order.direction == "SHORT"
        planned_entry = order.planned_entry
        planned_sl = order.stop_loss
        planned_tp = order.take_profit
        # Invariant: SHORT geometry TP < planned_entry < SL
        assert planned_tp < planned_entry < planned_sl, f"Geometry violated: TP={planned_tp}, Entry={planned_entry}, SL={planned_sl}"

        # Step 2: Trigger SHORT fill through ExecutionCoordinator
        fill_time = now_ms + 10_000
        # For SHORT LIMIT: triggered when current_bid >= planned_entry
        fill_ticker = make_ticker_dict(bid=planned_entry, ask=planned_entry + 0.2, now_ms=fill_time)

        coord = ExecutionCoordinator()
        coord.evaluate_orders_sync(session, ticker_override=fill_ticker, clock=clock, now_ms=fill_time)

        session.refresh(order)
        assert order.state == "paper_open", f"Order must fill, state={order.state}"
        assert order.direction == "SHORT", "Direction must remain SHORT"
        assert order.actual_entry == planned_entry

        # Step 3: Exit at TP for SHORT (current_ask <= planned_tp)
        exit_time = fill_time + 20_000
        closed_order = TradeLifecycleService.process_exit_tick(
            session,
            current_bid=planned_tp - 0.2,
            current_ask=planned_tp,
            now_ms=exit_time,
        )
        assert closed_order is not None
        assert closed_order.state == "closed"
        assert closed_order.exit_cause in ("TP_HIT", "TAKE_PROFIT")
        assert closed_order.realized_pnl_net > 0
    finally:
        session.close()
        env.close()


def test_030_policy_active_ny_window_allows_fill():
    """
    Rules 033, 034, 046, 047, 068:
    Verifies that when TradingPolicy is ACTIVE with NY_ONLY:
    1. During valid NY entry window (e.g. 10:15 NY time), policy.is_active=True allows entry.
    2. ExecutionCoordinator.evaluate_orders_sync successfully fills ARMED order.
    """
    env = IsolatedCoordinatorPolicyEnv(policy_active=True, entry_session_policy="NY_ONLY")
    session = env.make_session()

    try:
        ny_tz = ZoneInfo("America/New_York")
        # 10:00 AM EDT is safely inside 08:00 - 11:00 NY entry window
        dt = datetime(2026, 10, 20, 10, 0, tzinfo=ny_tz)
        clock = MockClock(dt)
        t_start = int(dt.timestamp() * 1000)
        now_ms = t_start + 600_000

        # Assert policy is active and resolves NY window as allowed
        policy = TradingPolicyService.get_policy(session)
        assert policy.is_active is True
        assert policy.entry_session_policy == "NY_ONLY"

        window = TradingPolicyService.get_ny_session_window(policy, now_ms=now_ms)
        assert window["is_in_window"] is True, f"Must be in window: {window}"

        policy_eval = TradingPolicyService.evaluate_entry_policy(session, clock=clock, now_ms=now_ms)
        assert policy_eval["allowed"] is True, f"Policy must allow entry: {policy_eval}"

        # Create ARMED order via orchestrator
        c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
        quote_arm = make_valid_quote(2625.5, 2625.7, now_ms)
        poi = WatchedZone(
            zone_id="poi_policy_allow",
            symbol="XAUUSDT",
            timeframe="15M",
            zone_type="FVG",
            direction="BULLISH",
            top=2625.0,
            bottom=2620.0,
            created_at=t_start - 3600_000,
            known_at=t_start - 1800_000,
            touch_count=0,
        )
        cfg = make_test_scalp_config(quality_arm_threshold=70.0)

        orch = ScalpWatcherOrchestrator()
        orch.set_active_zones("XAUUSDT", [poi])
        orch.evaluate_cycle(
            session,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote_arm,
            candles_1m=c_1m,
            candles_5m=[],
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
            auto_enabled=True,
        )

        order = session.query(PaperOrder).filter(PaperOrder.state == "armed").first()
        assert order is not None, "Candidate must ARM"
        planned_entry = order.planned_entry

        # Fill via ExecutionCoordinator
        fill_time = now_ms + 10_000
        fill_ticker = make_ticker_dict(bid=planned_entry - 0.1, ask=planned_entry, now_ms=fill_time)

        coord = ExecutionCoordinator()
        coord.evaluate_orders_sync(session, ticker_override=fill_ticker, clock=clock, now_ms=fill_time)

        session.refresh(order)
        assert order.state == "paper_open", f"Order must fill under active policy in window, got {order.state}"
        assert order.actual_entry == planned_entry
    finally:
        session.close()
        env.close()


def test_040_policy_active_outside_window_blocks_fill():
    """
    Rules 048, 069, 072:
    Verifies that when TradingPolicy is ACTIVE with NY_ONLY:
    1. Outside the NY window (e.g. 13:00 NY time, AFTER_WINDOW), policy blocks execution.
    2. ExecutionCoordinator.evaluate_orders_sync calls execute_reject with OUTSIDE_ENTRY_WINDOW reason.
    3. Order is marked rejected, no position is opened.
    """
    env = IsolatedCoordinatorPolicyEnv(policy_active=True, entry_session_policy="NY_ONLY")
    session = env.make_session()

    try:
        ny_tz = ZoneInfo("America/New_York")
        # 10:00 AM EDT: inside window to arm order
        dt_arm = datetime(2026, 10, 20, 10, 0, tzinfo=ny_tz)
        clock_arm = MockClock(dt_arm)
        t_start = int(dt_arm.timestamp() * 1000)
        now_arm_ms = t_start + 600_000

        c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
        quote_arm = make_valid_quote(2625.5, 2625.7, now_arm_ms)
        poi = WatchedZone(
            zone_id="poi_policy_block",
            symbol="XAUUSDT",
            timeframe="15M",
            zone_type="FVG",
            direction="BULLISH",
            top=2625.0,
            bottom=2620.0,
            created_at=t_start - 3600_000,
            known_at=t_start - 1800_000,
            touch_count=0,
        )
        cfg = make_test_scalp_config(quality_arm_threshold=70.0)

        orch = ScalpWatcherOrchestrator()
        orch.set_active_zones("XAUUSDT", [poi])
        orch.evaluate_cycle(
            session,
            symbol="XAUUSDT",
            now_ms=now_arm_ms,
            quote=quote_arm,
            candles_1m=c_1m,
            candles_5m=[],
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
            auto_enabled=True,
        )

        order = session.query(PaperOrder).filter(PaperOrder.state == "armed").first()
        assert order is not None, "Order must ARM inside window"
        planned_entry = order.planned_entry

        # Try to fill at 13:00 NY time (1:00 PM EDT: AFTER_WINDOW)
        dt_fill = datetime(2026, 10, 20, 13, 0, tzinfo=ny_tz)
        clock_fill = MockClock(dt_fill)
        fill_time = int(dt_fill.timestamp() * 1000)
        # Ensure order does not prematurely expire at fill_time so policy rejection is observed
        order.expires_at = fill_time + 600_000
        session.commit()

        policy = TradingPolicyService.get_policy(session)
        window = TradingPolicyService.get_ny_session_window(policy, now_ms=fill_time)
        assert window["is_in_window"] is False, "Must be outside NY window at 13:00"

        fill_ticker = make_ticker_dict(bid=planned_entry - 0.1, ask=planned_entry, now_ms=fill_time)
        coord = ExecutionCoordinator()
        coord.evaluate_orders_sync(session, ticker_override=fill_ticker, clock=clock_fill, now_ms=fill_time)

        session.refresh(order)
        assert order.state == "rejected", f"Order must be rejected outside window, got {order.state}"
        assert "OUTSIDE_ENTRY_WINDOW" in (order.invalidation_reason or "")

        pos = crud.get_active_position(session)
        assert pos is None, "No open position must exist"
    finally:
        session.close()
        env.close()


def test_050_policy_active_daily_cap_blocks_fill():
    """
    Rules 048, 070:
    Verifies that when daily fill cap (3 fills/day) is reached:
    1. TradingPolicy rejects further entry via MAX_DAILY_ENTRIES.
    2. ExecutionCoordinator.evaluate_orders_sync marks order rejected without opening position.
    """
    env = IsolatedCoordinatorPolicyEnv(policy_active=True, entry_session_policy="ALL_SESSIONS_WITH_NY_RESERVE")
    session = env.make_session()

    try:
        ny_tz = ZoneInfo("America/New_York")
        dt = datetime(2026, 10, 20, 10, 0, tzinfo=ny_tz)
        clock = MockClock(dt)
        now_ms = clock.now_ms()

        # Set DayAudit fills_count = 3
        day_audit = crud.get_or_create_today_audit(session, clock=clock)
        day_audit.fills_count = 3
        session.commit()

        policy_eval = TradingPolicyService.evaluate_entry_policy(session, clock=clock, now_ms=now_ms)
        assert policy_eval["allowed"] is False
        assert "MAX_DAILY_ENTRIES" in policy_eval["reason_codes"]

        # Directly seed an armed order
        order = PaperOrder(
            id="po_cap_test",
            instrument="XAUUSDT",
            direction="LONG",
            order_type="LIMIT",
            planned_entry=2625.0,
            stop_loss=2620.0,
            take_profit=2635.0,
            quantity=1.0,
            initial_risk_usdt=10.0,
            state="armed",
            created_at=now_ms,
            cost_snapshot=json.dumps({"min_net_rr_at_arm": 1.5, "slippage_usd": 0.10}),
        )
        session.add(order)
        session.commit()

        fill_ticker = make_ticker_dict(bid=2624.9, ask=2625.0, now_ms=now_ms + 1000)
        coord = ExecutionCoordinator()
        coord.evaluate_orders_sync(session, ticker_override=fill_ticker, clock=clock, now_ms=now_ms + 1000)

        session.refresh(order)
        assert order.state == "rejected"
        assert "MAX_DAILY_ENTRIES" in (order.invalidation_reason or "")
    finally:
        session.close()
        env.close()


def test_060_policy_active_news_blackout_blocks_fill():
    """
    Rules 036, 071:
    Verifies that during High Impact news blackout:
    1. TradingPolicy detects NEWS_BLACKOUT.
    2. ExecutionCoordinator.evaluate_orders_sync rejects order, preserving capital.
    """
    env = IsolatedCoordinatorPolicyEnv(policy_active=True)
    session = env.make_session()

    try:
        ny_tz = ZoneInfo("America/New_York")
        dt = datetime(2026, 10, 20, 10, 0, tzinfo=ny_tz)
        clock = MockClock(dt)
        now_ms = clock.now_ms()

        # Seed high-impact USD economic news event in blackout window
        news = EconomicNews(
            id=101,
            title="Non-Farm Payrolls",
            currency="USD",
            country="USD",
            impact="High",
            scheduled_at=now_ms,
            received_at=now_ms - 60_000,
        )
        session.add(news)
        session.commit()

        news_gate = TradingPolicyService._news_gate(session, now_ms)
        assert news_gate["blocked"] is True
        assert news_gate["reason_code"] == "NEWS_BLACKOUT"

        # Seed an armed order
        order = PaperOrder(
            id="po_news_test",
            instrument="XAUUSDT",
            direction="LONG",
            order_type="LIMIT",
            planned_entry=2625.0,
            stop_loss=2620.0,
            take_profit=2635.0,
            quantity=1.0,
            initial_risk_usdt=10.0,
            state="armed",
            created_at=now_ms,
            cost_snapshot=json.dumps({"min_net_rr_at_arm": 1.5, "slippage_usd": 0.10}),
        )
        session.add(order)
        session.commit()

        fill_ticker = make_ticker_dict(bid=2624.9, ask=2625.0, now_ms=now_ms + 1000)
        coord = ExecutionCoordinator()
        coord.evaluate_orders_sync(session, ticker_override=fill_ticker, clock=clock, now_ms=now_ms + 1000)

        session.refresh(order)
        assert order.state == "rejected"
        assert "NEWS_BLACKOUT" in (order.invalidation_reason or "")
    finally:
        session.close()
        env.close()
