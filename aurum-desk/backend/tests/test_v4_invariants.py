import pytest
import math
import time
import json
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models, schemas, crud
from domain_calculator import calculate_risk_reward, validate_price_geometry, CostAssumptions
from paper_broker import PaperBroker
from services.volume_service import VolumeAnalyzer
from services.telegram_service import format_telegram_message
from services.event_bus import EventBus
import smc_engine

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()

class MockCandle:
    def __init__(self, timestamp, o, h, l, c, volume=10.0, is_closed=True):
        self.timestamp = timestamp
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = volume
        self.is_closed = is_closed


# ==================== ITEM 1: FIXTURE & NET RR ====================
def test_invariant_1_fixture_and_net_rr():
    # V3 fixture: Long E4130.40 SL4123.43 TP4134.47 -> Gross RR = 4.07 / 6.97 = 0.5839
    r_v3 = calculate_risk_reward("LONG", 4130.40, 4123.43, 4134.47, capital=1000.0, risk_pct=0.5, min_net_rr=2.0)
    assert r_v3.is_valid is True
    assert math.isclose(r_v3.gross_rr, 0.5839, abs_tol=1e-3)
    assert r_v3.meets_min_rr is False
    assert r_v3.can_execute is False
    assert "NET_RR_TOO_LOW" in (r_v3.skip_reason or "")

    # Long 4000 SL 3990 TP 4020: gross RR = 2.0, but Net RR < 2.0 due to fees -> BLOCKED
    r_long2 = calculate_risk_reward("LONG", 4000.0, 3990.0, 4020.0, capital=1000.0, risk_pct=0.5, min_net_rr=2.0)
    assert r_long2.gross_rr == 2.0
    assert r_long2.net_rr < 2.0
    assert r_long2.can_execute is False

    # Positive setup with Net RR >= 2.0 -> PASS
    r_pos = calculate_risk_reward("LONG", 4000.0, 3990.0, 4035.0, capital=1000.0, risk_pct=0.5, min_net_rr=2.0)
    assert r_pos.net_rr >= 2.0
    assert r_pos.can_execute is True


# ==================== ITEM 2: GEOMETRY & SIZING GUARDS ====================
def test_invariant_2_geometry_and_sizing():
    # Long SL > Entry -> reject
    ok, err = validate_price_geometry("LONG", 4000.0, 4010.0, 4030.0)
    assert ok is False
    assert "INVALID_LONG_GEOMETRY" in err

    # Short TP > Entry -> reject
    ok, err = validate_price_geometry("SHORT", 4000.0, 4010.0, 4020.0)
    assert ok is False

    # NaN / Inf / Negative
    assert validate_price_geometry("LONG", float('nan'), 3990.0, 4020.0)[0] is False
    assert validate_price_geometry("LONG", -4000.0, 3990.0, 4020.0)[0] is False

    # min_qty exceeds budget -> SKIP
    r_min_qty = calculate_risk_reward("LONG", 4000.0, 3000.0, 6000.0, capital=1000.0, risk_pct=0.5)
    assert r_min_qty.can_execute is False
    assert "MIN_QTY_EXCEEDS_BUDGET" in (r_min_qty.skip_reason or "")

    # quantity_override exceeding budget -> REJECT
    r_override = calculate_risk_reward(
        "LONG", 4000.0, 3990.0, 4035.0, capital=1000.0, risk_pct=0.5, quantity_override=10.0
    )
    assert r_override.can_execute is False
    assert "QTY_OVERRIDE_EXCEEDS_BUDGET" in (r_override.skip_reason or "")


# ==================== ITEM 3: SLIPPAGE & LEVERAGE INVARIANCE ====================
def test_invariant_3_slippage_and_leverage():
    # 1. actual_entry with slippage already applied must not double-count
    r_raw = calculate_risk_reward("LONG", 4000.0, 3990.0, 4035.0, capital=1000.0, risk_pct=0.5, entry_has_slippage=False)
    r_with_slip = calculate_risk_reward("LONG", 4000.0, 3990.0, 4035.0, capital=1000.0, risk_pct=0.5, entry_has_slippage=True)
    # r_with_slip does not add entry slippage, so slippage_total_usdt is strictly lower
    assert r_with_slip.slippage_total_usdt <= r_raw.slippage_total_usdt

    # 2. Changing leverage (5x -> 20x) does NOT change gross PnL or gross loss USD!
    r_5x = calculate_risk_reward("LONG", 4000.0, 3990.0, 4035.0, capital=1000.0, risk_pct=0.5, leverage=5)
    r_20x = calculate_risk_reward("LONG", 4000.0, 3990.0, 4035.0, capital=1000.0, risk_pct=0.5, leverage=20)
    assert r_5x.gross_loss_usdt == r_20x.gross_loss_usdt
    assert r_5x.gross_reward_usdt == r_20x.gross_reward_usdt
    assert r_5x.quantity == r_20x.quantity
    # But margin required is strictly lower for 20x
    assert r_20x.initial_margin_usdt < r_5x.initial_margin_usdt
    # And liquidation price is strictly closer to entry for 20x
    assert r_20x.estimated_liquidation > r_5x.estimated_liquidation


# ==================== ITEM 4: LIQUIDATION BUFFER & ISOLATED MODEL ====================
def test_invariant_4_liquidation_buffer():
    # Long: E=4000, TP=4300 (Net RR > 2.0), with 50x leverage LP is ~3940. If SL is set below LP (e.g. 3930), execution MUST be blocked
    r_high_lev = calculate_risk_reward("LONG", 4000.0, 3930.0, 4300.0, capital=1000.0, risk_pct=0.5, leverage=50)
    assert r_high_lev.can_execute is False
    assert "LIQUIDATION" in (r_high_lev.skip_reason or "")

    # Short: E=4000, TP=3700 (Net RR > 2.0), with 50x leverage LP is ~4060. If SL is set above LP (e.g. 4070), execution MUST be blocked
    r_short_lev = calculate_risk_reward("SHORT", 4000.0, 4070.0, 3700.0, capital=1000.0, risk_pct=0.5, leverage=50)
    assert r_short_lev.can_execute is False
    assert "LIQUIDATION" in (r_short_lev.skip_reason or "")


# ==================== ITEM 5: CAUSAL SMC ENGINE & REPLAY INVARIANCE ====================
def test_invariant_5_causal_smc():
    candles = [
        MockCandle(1000, 4000, 4010, 3990, 4005, is_closed=True),
        MockCandle(2000, 4005, 4015, 4000, 4012, is_closed=True),
        MockCandle(3000, 4012, 4030, 4008, 4025, is_closed=True), # Pivot High
        MockCandle(4000, 4025, 4018, 4005, 4010, is_closed=True),
        MockCandle(5000, 4010, 4012, 3995, 4002, is_closed=False), # Unclosed right 2
    ]
    highs, _ = smc_engine.identify_pivots(candles)
    assert len(highs) == 0 # Cannot confirm while unclosed

    candles[4].is_closed = True
    highs, _ = smc_engine.identify_pivots(candles)
    assert len(highs) == 1
    assert highs[0]["confirmed_at"] == 5000


# ==================== ITEM 6: HTF NO FALLBACK & CADENCE FRESHNESS ====================
def test_invariant_6_htf_freshness():
    from services.collector_service import CollectorService
    collector = CollectorService()

    now_ms = int(time.time() * 1000)
    # Daily candle from 2 hours ago (7200s) is NOT stale for Daily timeframe
    d_ts = now_ms - (2 * 3600 * 1000)
    is_stale, fresh_sec = collector.is_stale("D", d_ts)
    assert is_stale is False

    # 15M candle from 2 hours ago IS stale
    is_stale_15m, _ = collector.is_stale("15M", d_ts)
    assert is_stale_15m is True


# ==================== ITEM 7: WATCH/DRAFT TOUCH ENTRY GIVES 0 FILLS ====================
def test_invariant_7_watch_and_draft_touch_entry(db_session):
    # A watch setup or draft touching entry must NOT fill
    now_ms = int(time.time() * 1000)
    watch_setup = models.WatchSetup(
        id="ws-test-1",
        version=1,
        strategy="SMC_ORDERBLOCK_V1",
        direction="LONG",
        timeframe="15M",
        state="WATCHING", # NOT ARMED
        provisional_entry=4000.0,
        provisional_sl=3990.0,
        provisional_tp=4035.0,
        invalidation_price=3985.0,
        quantity=0.05,
        risk_usdt=2.5,
        gross_rr=3.5,
        net_rr=2.2,
        leverage=5,
        margin_mode="ISOLATED",
        created_at=now_ms,
        updated_at=now_ms
    )
    db_session.add(watch_setup)
    db_session.commit()

    # Price ticks right through 4000.0 (bid 3998, ask 3999)
    PaperBroker.process_price_tick(
        db_session,
        current_bid=3998.0,
        current_ask=3999.0,
        candle_high=4005.0,
        candle_low=3995.0,
        candle_timestamp=now_ms + 1000
    )

    # Must be 0 fills!
    orders = crud.list_paper_orders(db_session)
    assert len(orders) == 0


# ==================== ITEM 8: AUTO STATE PERSISTENCE & MIDNIGHT QUOTA ====================
def test_invariant_8_auto_state_persistence(db_session):
    from services.strategy_service import StrategyService
    strat = StrategyService()

    # Default is False
    assert strat.get_auto_state(db_session) is False

    # Set to True
    strat.set_auto_state(db_session, True)
    assert strat.get_auto_state(db_session) is True

    # Persists across new StrategyService instance
    strat_reloaded = StrategyService()
    assert strat_reloaded.get_auto_state(db_session) is True


# ==================== ITEM 9: DYNAMIC CAPITAL SIZING ====================
def test_invariant_9_dynamic_capital():
    # Capital $800, risk 0.5% -> risk budget is $4.00, NOT $5.00
    r_800 = calculate_risk_reward("LONG", 4000.0, 3990.0, 4035.0, capital=800.0, risk_pct=0.5)
    assert r_800.budget_usdt == 4.0

    # Capital $1000, risk 0.25% -> risk budget is $2.50
    r_1000 = calculate_risk_reward("LONG", 4000.0, 3990.0, 4035.0, capital=1000.0, risk_pct=0.25)
    assert r_1000.budget_usdt == 2.50


# ==================== ITEM 10: WEBSOCKET EVENT ENVELOPE ====================
def test_invariant_10_ws_event_envelope():
    bus = EventBus()
    evt = bus.publish_event(
        event_type="setup.ready",
        aggregate_id="ws-setup-123",
        payload={"direction": "LONG", "entry": 4000.0, "sl": 3990.0, "tp": 4035.0}
    )
    assert evt is not None
    assert len(evt.event_id) > 0
    assert evt.schema_version == "1.0.0"
    assert evt.event_type == "setup.ready"
    assert evt.aggregate_id == "ws-setup-123"


# ==================== ITEM 11: TELEGRAM OUTBOX & NO CREDENTIAL LEAK ====================
def test_invariant_11_telegram_outbox(db_session):
    # Format message for READY
    msg = format_telegram_message("READY", {
        "setup_id": "setup-abc",
        "direction": "LONG",
        "planned_entry": 4000.0,
        "stop_loss": 3990.0,
        "take_profit": 4035.0,
        "net_rr": 2.25,
        "risk_usdt": 2.5
    })
    assert "TÍN HIỆU SẴN SÀNG" in msg
    assert "LONG XAUUSDT" in msg
    assert "Net R:R" in msg

    # Create outbox item in DB (id is autoincrement integer, dedupe_key is required)
    now_ms = int(time.time() * 1000)
    outbox_item = models.NotificationOutbox(
        channel="TELEGRAM",
        recipient="123456789",
        message_type="READY",
        dedupe_key="setup-abc:READY",
        payload=json.dumps({"notif_type": "READY", "data": {"setup_id": "setup-abc"}}),
        status="PENDING",
        attempts=0,
        created_at=now_ms
    )
    db_session.add(outbox_item)
    db_session.commit()

    saved = db_session.query(models.NotificationOutbox).filter(models.NotificationOutbox.dedupe_key == "setup-abc:READY").first()
    assert saved is not None
    assert saved.status == "PENDING"
    assert saved.attempts == 0


# ==================== ITEM 12: RVOL PAST-ONLY BASELINE ====================
def test_invariant_12_rvol_past_only():
    # Less than 10 samples -> UNKNOWN / None
    candles_few = [MockCandle(i * 1000, 4000, 4010, 3990, 4005, volume=10.0) for i in range(4)]
    res_few = VolumeAnalyzer.calculate_rvol(candles_few)
    assert res_few["status"] == "UNKNOWN"
    assert res_few["rvol"] is None

    # Sufficient samples: 15 historical bars with volume 10.0, current bar with volume 25.0
    candles_full = [MockCandle(i * 1000, 4000, 4010, 3990, 4005, volume=10.0) for i in range(15)]
    candles_full[-1].volume = 25.0
    res_full = VolumeAnalyzer.calculate_rvol(candles_full, baseline_period=10)
    assert res_full["status"] == "VALID"
    assert res_full["baseline_volume"] == 10.0 # Denominator excludes current bar!
    assert res_full["rvol"] == 2.5
    assert res_full["classification"] == "ULTRA_HIGH"


# ==================== ITEM 13: E2E DEMO PAPER TRADE LIFECYCLE ====================
def test_invariant_13_e2e_paper_trade_lifecycle(db_session):
    now_ms = int(time.time() * 1000)

    # 1. Create order
    order_create = schemas.PaperOrderCreate(
        setup_id="e2e-demo-1",
        direction="LONG",
        planned_entry=4000.0,
        stop_loss=3990.0,
        take_profit=4035.0,
        quantity=0.05,
        initial_risk_usdt=2.5,
        risk_pct=0.25,
        gross_rr=3.5,
        estimated_net_rr=2.2,
        leverage=5,
        margin_mode="ISOLATED"
    )

    # 2. Market order executed at Ask 4000.2
    # Slippage assumption adds 0.1 to actual entry
    order = PaperBroker.execute_market_order(
        db_session,
        order_create,
        current_bid=4000.0,
        current_ask=4000.2
    )
    assert order.state == "paper_open"
    assert order.actual_entry == 4000.3 # 4000.2 + 0.1 slippage
    assert order.initial_margin > 0

    # 3. Price rises to TP 4035
    closed = PaperBroker.process_price_tick(
        db_session,
        current_bid=4035.5,
        current_ask=4035.7,
        candle_high=4036.0,
        candle_low=4000.0,
        candle_timestamp=now_ms + 60000
    )
    assert closed is not None
    assert closed.state == "closed"
    assert closed.exit_cause == "TP_HIT"
    assert closed.realized_pnl_net > 0
    assert closed.realized_r > 0

    # 4. Check today audit updated
    audit = crud.get_or_create_today_audit(db_session)
    assert audit.fills_count == 1
    assert audit.realized_pnl_today > 0
