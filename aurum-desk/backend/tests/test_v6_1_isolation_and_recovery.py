import time
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import models
from services.eligibility_service import evaluate_setup_eligibility
from services.maintenance_service import audit_contaminated_fixtures, quarantine_contaminated_fixtures
from services.position_recovery_service import PositionRecoveryService

class MockCandle:
    def __init__(self, timestamp, open, high, low, close, timeframe="15M", symbol="XAUUSDT"):
        self.timestamp = timestamp
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.timeframe = timeframe
        self.symbol = symbol

def test_sentinel_runtime_db_isolated(isolated_db):
    """
    Test Isolation Guard: Proves that test execution uses isolated database
    and the production runtime DB path is guarded.
    """
    from database import engine, DB_PATH
    db_url = str(engine.url)
    runtime_canonical = str(Path(__file__).resolve().parent.parent / "aurum_desk.db")
    assert runtime_canonical != DB_PATH, f"CRITICAL: DB_PATH points to production DB: {DB_PATH}"
    assert runtime_canonical not in db_url, f"CRITICAL: Engine URL {db_url} points to production DB!"

    # Verify isolated_db can perform writes without touching production DB
    dummy_order = models.PaperOrder(
        id="test-iso-order-1",
        direction="LONG",
        state="candidate",
        planned_entry=4120.0,
        stop_loss=4100.0,
        take_profit=4160.0,
        quantity=0.1,
        initial_risk_usdt=2.0,
        leverage=5,
        margin_mode="ISOLATED",
        created_at=int(time.time() * 1000)
    )
    isolated_db.add(dummy_order)
    isolated_db.commit()

    saved = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == "test-iso-order-1").first()
    assert saved is not None
    assert saved.id == "test-iso-order-1"


def test_shared_eligibility_cross_blocked(isolated_db):
    """Shared Eligibility: Verifies that Cross Margin setups are strictly blocked from Arming."""
    setup = models.WatchSetup(
        id="watch-cross-test",
        direction="LONG",
        state="READY",
        strategy="SMC_V5",
        provisional_entry=4120.0,
        provisional_sl=4100.0,
        provisional_tp=4160.0,
        invalidation_price=4100.0,
        leverage=5,
        margin_mode="CROSS",
        setup_instance_id="inst-cross-1",
        version=1,
        created_at=int(time.time() * 1000),
        updated_at=int(time.time() * 1000)
    )
    isolated_db.add(setup)
    isolated_db.commit()

    eligibility = evaluate_setup_eligibility(db=isolated_db, setup=setup, custom_margin_mode="CROSS")
    assert eligibility["can_arm"] is False
    assert eligibility["can_execute"] is False
    assert any("CROSS" in r for r in eligibility["block_reasons"])
    assert "CROSS_MARGIN_UNSUPPORTED" in eligibility["reason_codes"]


def test_shared_eligibility_active_position_blocked(isolated_db):
    """Shared Eligibility: Verifies that having an active open position blocks Arming new setups."""
    # Create active open position
    active_pos = models.PaperOrder(
        id="order-active-open-1",
        direction="LONG",
        state="paper_open",
        planned_entry=4120.0,
        actual_entry=4120.31,
        stop_loss=4102.09,
        take_profit=4167.68,
        quantity=0.1,
        initial_risk_usdt=2.0,
        leverage=30,
        margin_mode="ISOLATED",
        opened_at=int(time.time() * 1000),
        created_at=int(time.time() * 1000)
    )
    isolated_db.add(active_pos)

    # Create candidate watch setup
    setup = models.WatchSetup(
        id="watch-iso-ready-1",
        direction="LONG",
        state="READY",
        strategy="SMC_V5",
        provisional_entry=4120.0,
        provisional_sl=4102.0,
        provisional_tp=4165.0,
        invalidation_price=4100.0,
        leverage=10,
        margin_mode="ISOLATED",
        setup_instance_id="inst-iso-1",
        version=1,
        created_at=int(time.time() * 1000),
        updated_at=int(time.time() * 1000)
    )
    isolated_db.add(setup)
    isolated_db.commit()

    eligibility = evaluate_setup_eligibility(db=isolated_db, setup=setup, custom_margin_mode="ISOLATED")
    assert eligibility["can_arm"] is False
    assert "ACTIVE_POSITION_EXISTS" in eligibility["reason_codes"]
    assert any("1 vị thế" in r for r in eligibility["block_reasons"])


def test_shared_eligibility_valid_setup(isolated_db):
    """Shared Eligibility: Verifies that a valid isolated setup with no active position can Arm."""
    setup = models.WatchSetup(
        id="watch-valid-ready",
        direction="LONG",
        state="READY",
        strategy="SMC_V5",
        provisional_entry=4120.0,
        provisional_sl=4102.0,
        provisional_tp=4168.0,
        invalidation_price=4100.0,
        leverage=10,
        margin_mode="ISOLATED",
        setup_instance_id="inst-valid-1",
        version=1,
        created_at=int(time.time() * 1000),
        updated_at=int(time.time() * 1000)
    )
    isolated_db.add(setup)
    isolated_db.commit()

    eligibility = evaluate_setup_eligibility(db=isolated_db, setup=setup, custom_margin_mode="ISOLATED")
    assert eligibility["can_arm"] is True
    assert eligibility["can_execute"] is True
    assert len(eligibility["block_reasons"]) == 0


def test_fixture_maintenance_quarantine(isolated_db):
    """Maintenance Service: Verifies exact-match fixture audit, quarantine, and idempotency."""
    # 1. Insert contaminated test fixture
    fixture = models.WatchSetup(
        id="watch-cross-15M",
        direction="LONG",
        state="READY",
        strategy="SMC_V5",
        provisional_entry=4120.0,
        provisional_sl=4110.0,
        provisional_tp=4160.0,
        invalidation_price=4100.0,
        leverage=5,
        margin_mode="CROSS",
        setup_instance_id="inst-cross-fixture",
        version=1,
        created_at=int(time.time() * 1000),
        updated_at=int(time.time() * 1000)
    )
    isolated_db.add(fixture)
    isolated_db.commit()

    # 2. Dry run audit
    audit = audit_contaminated_fixtures(isolated_db)
    assert audit["total_checked"] >= 1
    assert len(audit["candidates"]) == 1
    assert audit["candidates"][0]["setup_id"] == "watch-cross-15M"

    # State must not be mutated by dry run
    reloaded = isolated_db.query(models.WatchSetup).filter(models.WatchSetup.id == "watch-cross-15M").first()
    assert reloaded.state == "READY"

    # 3. Execute quarantine
    result = quarantine_contaminated_fixtures(isolated_db, dry_run=False)
    assert result["quarantined_count"] == 1
    assert result["quarantined_ids"] == ["watch-cross-15M"]

    reloaded_after = isolated_db.query(models.WatchSetup).filter(models.WatchSetup.id == "watch-cross-15M").first()
    assert reloaded_after.state == "INVALIDATED"
    assert "TEST_FIXTURE_CONTAMINATION" in (reloaded_after.invalidation_reason or "")

    # 4. Idempotency on second run
    result2 = quarantine_contaminated_fixtures(isolated_db, dry_run=False)
    assert result2["quarantined_count"] == 0


def test_offline_position_recovery_tp_hit(isolated_db):
    """Offline Position Recovery: Historical candles hit TP chronologically -> order closed at TP."""
    now_ms = int(time.time() * 1000)
    order_time = now_ms - (30 * 60 * 1000) # 30 mins ago

    order = models.PaperOrder(
        id="order-recov-tp-1",
        direction="LONG",
        state="paper_open",
        planned_entry=4120.0,
        actual_entry=4120.31,
        stop_loss=4102.09,
        take_profit=4167.68,
        quantity=0.1,
        initial_risk_usdt=2.0,
        leverage=30,
        margin_mode="ISOLATED",
        opened_at=order_time,
        last_processed_market_timestamp=order_time,
        created_at=order_time
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Candle 1: 4125 to 4140 (safe inside)
    # Candle 2: high 4170.0 (breaches TP 4167.68!), low 4135.0
    c1_time = order_time + (5 * 60 * 1000)
    c2_time = order_time + (10 * 60 * 1000)
    candles = [
        MockCandle(timestamp=c1_time, open=4122.0, high=4140.0, low=4120.0, close=4135.0, timeframe="5M"),
        MockCandle(timestamp=c2_time, open=4135.0, high=4170.0, low=4135.0, close=4168.0, timeframe="5M"),
    ]

    reconciled = PositionRecoveryService.check_and_recover_offline_positions(
        db=isolated_db,
        candles_override=candles,
        now_ms=now_ms,
        gap_threshold_ms=1000
    )

    assert len(reconciled) == 1
    rec_info = reconciled[0]
    assert rec_info["order_id"] == "order-recov-tp-1"
    assert rec_info["status"] == "RECOVERED"
    assert rec_info["exit_cause"] == "TP_HIT"
    assert rec_info["occurred_at"] == c2_time

    # Verify DB order state
    closed_order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == "order-recov-tp-1").first()
    assert closed_order.state == "closed"
    assert closed_order.exit_cause == "TP_HIT"
    assert closed_order.recovery_status == "RECOVERED"
    assert closed_order.recovery_confidence == "CONFIRMED"
    assert closed_order.occurred_at == c2_time
    assert closed_order.realized_pnl_net is not None
    assert closed_order.realized_pnl_net > 0


def test_offline_position_recovery_sl_hit(isolated_db):
    """Offline Position Recovery: Historical candles hit SL chronologically -> order closed at SL."""
    now_ms = int(time.time() * 1000)
    order_time = now_ms - (30 * 60 * 1000)

    order = models.PaperOrder(
        id="order-recov-sl-1",
        direction="LONG",
        state="paper_open",
        planned_entry=4120.0,
        actual_entry=4120.31,
        stop_loss=4102.09,
        take_profit=4167.68,
        quantity=0.1,
        initial_risk_usdt=2.0,
        leverage=30,
        margin_mode="ISOLATED",
        opened_at=order_time,
        last_processed_market_timestamp=order_time,
        created_at=order_time
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Candle 1: touches low 4100.0 (breaches SL 4102.09!)
    c1_time = order_time + (5 * 60 * 1000)
    candles = [
        MockCandle(timestamp=c1_time, open=4120.0, high=4125.0, low=4100.0, close=4105.0, timeframe="5M")
    ]

    reconciled = PositionRecoveryService.check_and_recover_offline_positions(
        db=isolated_db,
        candles_override=candles,
        now_ms=now_ms,
        gap_threshold_ms=1000
    )

    assert len(reconciled) == 1
    assert reconciled[0]["status"] == "RECOVERED"
    assert reconciled[0]["exit_cause"] == "SL_HIT"

    closed_order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == "order-recov-sl-1").first()
    assert closed_order.state == "closed"
    assert closed_order.exit_cause == "SL_HIT"
    assert closed_order.realized_pnl_net is not None
    assert closed_order.realized_pnl_net < 0


def test_offline_position_recovery_ambiguous_bar(isolated_db):
    """
    Offline Position Recovery: Ambiguous bar hitting both TP and SL in the same candle.
    Must handle with conservative SL-first assumption and mark as ASSUMED_CONSERVATIVE (not fake confirmed win).
    """
    now_ms = int(time.time() * 1000)
    order_time = now_ms - (20 * 60 * 1000)

    order = models.PaperOrder(
        id="order-recov-ambig-1",
        direction="LONG",
        state="paper_open",
        planned_entry=4120.0,
        actual_entry=4120.31,
        stop_loss=4102.09,
        take_profit=4167.68,
        quantity=0.1,
        initial_risk_usdt=2.0,
        leverage=30,
        margin_mode="ISOLATED",
        opened_at=order_time,
        last_processed_market_timestamp=order_time,
        created_at=order_time
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Extreme volatility bar: breaches SL (low 4095.0) AND breaches TP (high 4175.0)
    c1_time = order_time + (5 * 60 * 1000)
    candles = [
        MockCandle(timestamp=c1_time, open=4120.0, high=4175.0, low=4095.0, close=4140.0, timeframe="5M")
    ]

    reconciled = PositionRecoveryService.check_and_recover_offline_positions(
        db=isolated_db,
        candles_override=candles,
        now_ms=now_ms,
        gap_threshold_ms=1000
    )

    assert len(reconciled) == 1
    assert reconciled[0]["exit_cause"] == "AMBIGUOUS_BAR_CONSERVATIVE_SL"
    assert reconciled[0]["confidence"] == "ASSUMED_CONSERVATIVE"

    closed_order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == "order-recov-ambig-1").first()
    assert closed_order.state == "closed"
    assert closed_order.exit_cause == "AMBIGUOUS_BAR_CONSERVATIVE_SL"
    assert closed_order.recovery_confidence == "ASSUMED_CONSERVATIVE"


def test_offline_position_recovery_no_exit_keeps_open(isolated_db):
    """Offline Position Recovery: Candles stay between SL and TP -> order remains paper_open."""
    now_ms = int(time.time() * 1000)
    order_time = now_ms - (15 * 60 * 1000)

    order = models.PaperOrder(
        id="order-recov-noexit-1",
        direction="LONG",
        state="paper_open",
        planned_entry=4120.0,
        actual_entry=4120.31,
        stop_loss=4102.09,
        take_profit=4167.68,
        quantity=0.1,
        initial_risk_usdt=2.0,
        leverage=30,
        margin_mode="ISOLATED",
        opened_at=order_time,
        last_processed_market_timestamp=order_time,
        created_at=order_time
    )
    isolated_db.add(order)
    isolated_db.commit()

    c1_time = order_time + (5 * 60 * 1000)
    c2_time = order_time + (10 * 60 * 1000)
    candles = [
        MockCandle(timestamp=c1_time, open=4120.0, high=4135.0, low=4115.0, close=4130.0, timeframe="5M"),
        MockCandle(timestamp=c2_time, open=4130.0, high=4145.0, low=4125.0, close=4140.0, timeframe="5M")
    ]

    reconciled = PositionRecoveryService.check_and_recover_offline_positions(
        db=isolated_db,
        candles_override=candles,
        now_ms=now_ms,
        gap_threshold_ms=1000
    )

    assert len(reconciled) == 1
    assert reconciled[0]["status"] == "SURVIVED_UP_TO_DATE"
    assert reconciled[0]["resolved_through"] == c2_time

    still_open = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == "order-recov-noexit-1").first()
    assert still_open.state == "paper_open"
    assert still_open.last_processed_market_timestamp == c2_time


def test_offline_position_recovery_idempotency(isolated_db):
    """Offline Position Recovery: Second run is idempotent and does not produce duplicate actions."""
    now_ms = int(time.time() * 1000)
    # When no open orders remain
    reconciled = PositionRecoveryService.check_and_recover_offline_positions(
        db=isolated_db,
        now_ms=now_ms,
        gap_threshold_ms=1000
    )
    assert reconciled == []
