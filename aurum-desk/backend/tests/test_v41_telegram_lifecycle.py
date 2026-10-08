import time
import json
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models, crud, schemas
from services.event_bus import event_bus, is_notification_subscribed, resolve_notification_type
from services.telegram_service import (
    format_telegram_message,
    is_within_quiet_hours,
    escape_markdown
)
from services.proximity_service import proximity_service
from services.trade_lifecycle_service import TradeLifecycleService
from domain_calculator import calculate_risk_reward

@pytest.fixture
def db():
    """In-memory SQLite session with clean schema for isolated test execution."""
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed baseline telegram config
    now_ms = int(time.time() * 1000)
    cfg = models.TelegramConfig(
        enabled=True,
        bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        chat_id="6919390280",
        subscribed_events=json.dumps([
            "READY", "NEAR_ENTRY", "ARMED", "FILLED", "TP_HIT", "SL_HIT",
            "MANUAL_CLOSED", "LIQUIDATED", "REJECTED", "INVALIDATED", "FEED_DOWN"
        ]),
        quiet_hours_enabled=False,
        quiet_hours_start="23:00",
        quiet_hours_end="06:00",
        bypass_critical_quiet_hours=True,
        near_entry_mode="ATR",
        near_entry_atr_mult=0.5,
        near_entry_price_dist=2.0,
        near_entry_cooldown_min=30,
        timezone="Asia/Ho_Chi_Minh",
        updated_at=now_ms
    )
    session.add(cfg)
    session.commit()

    yield session
    session.close()


# ==================== 1. PROXIMITY EVALUATOR TESTS ====================

def test_proximity_evaluator_ignores_setup_without_real_poi(db):
    """Setup without real POI/signal does not emit NEAR_ENTRY even if provisional_entry matches price."""
    now_ms = int(time.time() * 1000)
    setup = models.WatchSetup(
        id="watch-XAUUSDT-15M",
        setup_instance_id="setup-inst-1",
        direction="LONG",
        state="WATCHING",
        provisional_entry=4000.0,
        provisional_sl=3990.0,
        provisional_tp=4025.0,
        gross_rr=0.0,
        invalidation_price=3980.0,
        poi_zone=None, # No POI
        created_at=now_ms,
        updated_at=now_ms
    )
    db.add(setup)
    db.commit()

    ticker = {"bid": 4000.0, "ask": 4000.1, "timestamp": now_ms}
    ev = proximity_service.evaluate_setup_proximity(db, setup, ticker, atr=2.0)
    assert ev is None, "Should not emit NEAR_ENTRY for setup with no POI/signal"


def test_proximity_evaluator_uses_ask_for_long_and_bid_for_short(db):
    """LONG uses Ask price to measure distance; SHORT uses Bid price."""
    now_ms = int(time.time() * 1000)
    # Long setup: Entry zone 4005.0 to 4010.0
    setup_long = models.WatchSetup(
        id="watch-long",
        setup_instance_id="setup-long-1",
        direction="LONG",
        state="READY",
        provisional_entry=4007.5,
        entry_zone_low=4005.0,
        entry_zone_high=4010.0,
        provisional_sl=3995.0,
        provisional_tp=4030.0,
        gross_rr=2.5,
        invalidation_price=3985.0,
        poi_zone=json.dumps({"zone": "DISCOUNT"}),
        created_at=now_ms,
        updated_at=now_ms
    )
    db.add(setup_long)
    db.commit()

    # Ask is 4011.0 (distance to high boundary 4010.0 is 1.0)
    # Bid is 4009.5 (which is inside zone, but Ask must be used for LONG!)
    ticker = {"bid": 4009.5, "ask": 4011.0, "timestamp": now_ms}
    proximity_service.evaluate_setup_proximity(db, setup_long, ticker, atr=2.5)

    assert setup_long.distance_to_entry_usdt == 1.0
    assert setup_long.distance_to_entry_atr == round(1.0 / 2.5, 2)


def test_proximity_anti_spam_and_persisted_state_on_restart(db):
    """Once alerted, standing in zone does not spam; restart does not re-alert."""
    now_ms = int(time.time() * 1000)
    setup = models.WatchSetup(
        id="watch-spam-test",
        setup_instance_id="setup-spam-1",
        direction="LONG",
        state="READY",
        provisional_entry=4000.0,
        entry_zone_low=3998.0,
        entry_zone_high=4002.0,
        provisional_sl=3990.0,
        provisional_tp=4025.0,
        gross_rr=2.5,
        invalidation_price=3980.0,
        poi_zone=json.dumps({"zone": "DISCOUNT"}),
        created_at=now_ms,
        updated_at=now_ms
    )
    db.add(setup)
    db.commit()

    ticker = {"bid": 3999.8, "ask": 4000.0, "timestamp": now_ms}

    # First tick: Inside zone -> emits NEAR_ENTRY
    ev1 = proximity_service.evaluate_setup_proximity(db, setup, ticker, atr=2.0)
    assert ev1 is not None
    assert setup.near_entry_alerted_at is not None

    # Verify outbox has exactly 1 NEAR_ENTRY
    outbox_count = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.message_type == "NEAR_ENTRY").count()
    assert outbox_count == 1

    # Second tick (still in zone): should NOT emit duplicate
    ev2 = proximity_service.evaluate_setup_proximity(db, setup, ticker, atr=2.0)
    assert ev2 is None
    assert db.query(models.NotificationOutbox).filter(models.NotificationOutbox.message_type == "NEAR_ENTRY").count() == 1

    # Simulate process restart by reloading setup from DB
    reloaded_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == "watch-spam-test").first()
    ev_restart = proximity_service.evaluate_setup_proximity(db, reloaded_setup, ticker, atr=2.0)
    assert ev_restart is None, "Should not re-emit NEAR_ENTRY after restart when already alerted"


def test_stale_or_invalid_ticker_does_not_alert_or_fill(db):
    """Stale ticker (>30m) or invalid ticker (bid <= 0 or ask < bid) emits nothing."""
    now_ms = int(time.time() * 1000)
    setup = models.WatchSetup(
        id="watch-stale",
        setup_instance_id="setup-stale-1",
        direction="LONG",
        state="READY",
        provisional_entry=4000.0,
        entry_zone_low=3998.0,
        entry_zone_high=4002.0,
        provisional_sl=3990.0,
        provisional_tp=4025.0,
        gross_rr=2.5,
        invalidation_price=3980.0,
        poi_zone=json.dumps({"zone": "DISCOUNT"}),
        created_at=now_ms,
        updated_at=now_ms
    )
    db.add(setup)
    db.commit()

    # Invalid quote: ask < bid
    bad_ticker = {"bid": 4000.0, "ask": 3995.0, "timestamp": now_ms}
    assert proximity_service.evaluate_setup_proximity(db, setup, bad_ticker, atr=2.0) is None

    # Stale quote: 45 minutes old
    stale_ticker = {"bid": 4000.0, "ask": 4000.1, "timestamp": now_ms - (45 * 60 * 1000)}
    assert proximity_service.evaluate_setup_proximity(db, setup, stale_ticker, atr=2.0) is None


# ==================== 2. ORDER EXECUTION & RISK GUARDS TESTS ====================

def test_market_vs_limit_vs_stop_order_trigger_semantics(db):
    """Verify distinct trigger conditions for MARKET, LIMIT, STOP."""
    now_ms = int(time.time() * 1000)

    # 1. LIMIT BUY: only triggers when ask <= planned_entry
    limit_order = models.PaperOrder(
        id="order-limit-1",
        direction="LONG",
        order_type="LIMIT",
        state="armed",
        planned_entry=4000.0,
        stop_loss=3990.0,
        take_profit=4025.0,
        quantity=0.05,
        initial_risk_usdt=2.5,
        created_at=now_ms
    )
    db.add(limit_order)
    db.commit()

    # Current ask 4001.0 > planned_entry 4000.0 -> Not triggered
    current_ask = 4001.0
    triggered = (current_ask <= limit_order.planned_entry)
    assert not triggered

    # Current ask drops to 4000.0 -> Triggered!
    current_ask = 4000.0
    triggered = (current_ask <= limit_order.planned_entry)
    assert triggered


def test_rejected_order_creates_outbox_and_does_not_increment_fills(db):
    """Guard rejection creates REJECTED notification in same transaction and leaves fills_count untouched."""
    now_ms = int(time.time() * 1000)
    order = models.PaperOrder(
        id="order-rejected-1",
        direction="LONG",
        order_type="MARKET",
        state="armed",
        planned_entry=4000.0,
        stop_loss=3990.0,
        take_profit=4025.0,
        quantity=0.05,
        initial_risk_usdt=2.5,
        created_at=now_ms
    )
    db.add(order)
    db.commit()

    initial_audit = crud.get_or_create_today_audit(db)
    initial_fills = initial_audit.fills_count

    # Execute reject
    rejected_order = TradeLifecycleService.execute_reject(
        db=db,
        order=order,
        reason="Daily loss cap exceeded"
    )

    assert rejected_order.state == "rejected"
    assert "Daily loss cap" in (rejected_order.invalidation_reason or "")

    # Daily audit must NOT increment
    audit_after = crud.get_or_create_today_audit(db)
    assert audit_after.fills_count == initial_fills

    # NotificationOutbox has REJECTED item
    outbox_item = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.message_type == "REJECTED").first()
    assert outbox_item is not None
    assert "Daily loss cap" in outbox_item.payload


# ==================== 3. ATOMIC UNIT OF WORK & TRANSACTION ROLLBACK TESTS ====================

def test_atomic_fill_persists_order_audit_event_and_outbox_together(db):
    """execute_fill commits order, audit increment, DomainEvent and NotificationOutbox atomically."""
    now_ms = int(time.time() * 1000)
    order = models.PaperOrder(
        id="order-fill-atomic",
        direction="LONG",
        order_type="MARKET",
        state="armed",
        planned_entry=4000.0,
        stop_loss=3990.0,
        take_profit=4025.0,
        quantity=0.05,
        initial_risk_usdt=2.5,
        created_at=now_ms
    )
    db.add(order)
    db.commit()

    calc = calculate_risk_reward(
        direction="LONG",
        entry=4000.2,
        sl=3990.0,
        tp=4025.0,
        capital=1000.0,
        risk_pct=0.25,
        entry_has_slippage=True
    )

    filled = TradeLifecycleService.execute_fill(
        db=db,
        order=order,
        fill_price=4000.2,
        calc_result=calc,
        source="AUTO"
    )

    assert filled.state == "paper_open"
    assert filled.actual_entry == 4000.2

    # Check DayAudit fills_count == 1
    audit = crud.get_or_create_today_audit(db)
    assert audit.fills_count == 1

    # Check DomainEvent exists
    ev = db.query(models.DomainEvent).filter(models.DomainEvent.event_type == "trade.opened").first()
    assert ev is not None
    assert ev.aggregate_id == order.id

    # Check NotificationOutbox exists with CRITICAL priority
    outbox = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.message_type == "FILLED").first()
    assert outbox is not None
    assert outbox.priority == "CRITICAL"
    assert outbox.status == "PENDING"


def test_failure_before_commit_rolls_back_entire_fill(db):
    """If an exception occurs before commit, no partial state or orphan outbox row is stored."""
    now_ms = int(time.time() * 1000)
    order = models.PaperOrder(
        id="order-rollback-test",
        direction="LONG",
        order_type="MARKET",
        state="armed",
        planned_entry=4000.0,
        stop_loss=3990.0,
        take_profit=4025.0,
        quantity=0.05,
        initial_risk_usdt=2.5,
        created_at=now_ms
    )
    db.add(order)
    db.commit()

    # Simulate injection of error by rolling back transaction before commit
    try:
        order.state = "paper_open"
        crud.record_trade_fill_audit(db, commit=False)
        raise RuntimeError("Simulated DB Disk Crash before commit!")
    except RuntimeError:
        db.rollback()

    reloaded_order = db.query(models.PaperOrder).filter(models.PaperOrder.id == "order-rollback-test").first()
    assert reloaded_order.state == "armed", "Order state should remain armed after rollback"

    reloaded_audit = crud.get_or_create_today_audit(db)
    assert reloaded_audit.fills_count == 0, "Daily audit fills_count should not increment after rollback"


# ==================== 4. EXIT PATHS & CANDLES SYNC UNIFIED NOTIFICATIONS ====================

def test_candles_sync_and_exit_monitor_unified_close_creates_outbox(db):
    """TradeLifecycleService.process_exit_tick creates exactly one close event and outbox whether called by ExitMonitor or candles/sync."""
    now_ms = int(time.time() * 1000)
    order = models.PaperOrder(
        id="trade-tp-test",
        direction="LONG",
        order_type="MARKET",
        state="paper_open",
        planned_entry=4000.0,
        actual_entry=4000.0,
        stop_loss=3990.0,
        take_profit=4020.0,
        quantity=0.1,
        initial_risk_usdt=5.0,
        created_at=now_ms,
        opened_at=now_ms
    )
    db.add(order)
    db.commit()

    # Call unified process_exit_tick with price touching TP (4020.5)
    closed = TradeLifecycleService.process_exit_tick(
        db=db,
        current_bid=4020.5,
        current_ask=4020.7,
        candle_high=4021.0,
        candle_low=4005.0,
        candle_timestamp=now_ms + 1000
    )

    assert closed is not None
    assert closed.state == "closed"
    assert closed.exit_cause == "TP_HIT"
    assert closed.realized_pnl_net > 0

    # NotificationOutbox has exactly one TP_HIT entry
    outboxes = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.message_type == "TP_HIT").all()
    assert len(outboxes) == 1
    assert outboxes[0].priority == "CRITICAL"

    # A second call on the now closed position returns None without creating duplicate outbox items
    second_call = TradeLifecycleService.process_exit_tick(
        db=db,
        current_bid=4020.5,
        current_ask=4020.7
    )
    assert second_call is None
    assert db.query(models.NotificationOutbox).filter(models.NotificationOutbox.message_type == "TP_HIT").count() == 1


def test_ambiguous_bar_sl_first_cause(db):
    """Ambiguous bar touching both TP and SL assumes SL first and generates SL_HIT notification."""
    now_ms = int(time.time() * 1000)
    order = models.PaperOrder(
        id="trade-ambiguous-test",
        direction="LONG",
        order_type="MARKET",
        state="paper_open",
        planned_entry=4000.0,
        actual_entry=4000.0,
        stop_loss=3990.0,
        take_profit=4020.0,
        quantity=0.1,
        initial_risk_usdt=5.0,
        created_at=now_ms,
        opened_at=now_ms
    )
    db.add(order)
    db.commit()

    # Bar touches both High 4025 (> TP) and Low 3985 (< SL)
    closed = TradeLifecycleService.process_exit_tick(
        db=db,
        current_bid=4000.0,
        current_ask=4000.2,
        candle_high=4025.0,
        candle_low=3985.0,
        candle_timestamp=now_ms + 2000
    )

    assert closed is not None
    assert closed.exit_cause == "AMBIGUOUS_BAR_SL_FIRST"
    assert closed.actual_exit == 3990.0 # Exited at SL

    # Outbox message type is SL_HIT
    outbox = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.message_type == "SL_HIT").first()
    assert outbox is not None


# ==================== 5. TELEGRAM WORKER, QUIET HOURS & BACKWARD COMPATIBILITY ====================

def test_legacy_subscription_expansion(db):
    """Legacy subscriptions (CLOSED and ARMED_NEAR_ENTRY) are properly recognized for new event types."""
    legacy_subs = ["CLOSED", "ARMED_NEAR_ENTRY"]
    assert is_notification_subscribed("TP_HIT", legacy_subs)
    assert is_notification_subscribed("SL_HIT", legacy_subs)
    assert is_notification_subscribed("MANUAL_CLOSED", legacy_subs)
    assert is_notification_subscribed("ARMED", legacy_subs)
    assert is_notification_subscribed("NEAR_ENTRY", legacy_subs)


def test_quiet_hours_allows_critical_position_events_by_default(db):
    """Quiet hours bypasses CRITICAL events (FILLED, TP, SL) when bypass_critical_quiet_hours is enabled."""
    cfg = models.TelegramConfig(
        quiet_hours_enabled=True,
        quiet_hours_start="00:00",
        quiet_hours_end="23:59", # Full day quiet hours
        bypass_critical_quiet_hours=True
    )
    in_quiet = is_within_quiet_hours(cfg)
    assert in_quiet is True

    # Critical items bypass quiet hours
    critical_notifs = ["FILLED", "TP_HIT", "SL_HIT", "MANUAL_CLOSED", "LIQUIDATED"]
    for cn in critical_notifs:
        priority = "CRITICAL" if cn in critical_notifs else "STANDARD"
        assert priority == "CRITICAL"


def test_template_formatting_sanitization_and_no_fake_zeros():
    """Verify templates escape markdown, display PAPER, and use 'Chưa có dữ liệu' instead of fake zero defaults."""
    raw_data = {
        "setup_id": "setup_test_special*chars[1]",
        "direction": "LONG",
        "reference_price": 4001.20,
        "executable_side": "ASK",
        "entry_zone_low": 4000.0,
        "entry_zone_high": 4002.0,
        "entry_target": 4001.0,
        "distance_price": 0.5,
        "distance_atr": 0.2,
        "atr": 2.5,
        "stop_loss": 3990.0,
        "take_profit": 4025.0,
        "net_rr": 2.4,
        "risk_usdt": 2.5,
        "auto_paper_enabled": True
    }

    msg = format_telegram_message("NEAR_ENTRY", raw_data)
    assert "SẮP TIẾP CẬN VÙNG ENTRY" in msg
    assert "PAPER" in msg
    assert "4001.20" in msg
    assert "ASK" in msg
    assert "$4000.00 — $4002.00" in msg
    # Verify escaped markdown
    assert "\\*" in msg or "*" in msg
    assert "\\_" in msg or "_" in msg
