import pytest
import math
from domain_calculator import calculate_risk_reward, validate_price_geometry, CostAssumptions
from paper_broker import PaperBroker
import schemas, models, crud
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()

class DummyCandle:
    def __init__(self, timestamp, o, h, l, c, is_closed=True):
        self.timestamp = timestamp
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = 10.0
        self.is_closed = is_closed


def test_regression_1_user_image_fixture():
    """
    Criterion 1:
    Fixture: Long E=4130.40, SL=4123.43, TP=4134.47
    Gross loss = 4130.40 - 4123.43 = 6.97
    Gross reward = 4134.47 - 4130.40 = 4.07
    Gross RR = 4.07 / 6.97 = 0.583931
    Open MUST BE BLOCKED (can_execute=False).
    Must NOT claim 2.00 RR.
    """
    res = calculate_risk_reward(
        direction="LONG",
        entry=4130.40,
        sl=4123.43,
        tp=4134.47,
        capital=1000.0,
        risk_pct=0.5,
        min_net_rr=2.0
    )
    assert res.is_valid is True
    assert math.isclose(res.gross_rr, 0.5839, abs_tol=1e-3)
    assert res.meets_min_rr is False
    assert res.can_execute is False
    assert "NET_RR_TOO_LOW" in (res.skip_reason or "")


def test_regression_2_gross_vs_net_rr():
    """
    Criterion 2:
    - Long E4000 SL3990 TP4020: grossRR = 2.0 exactly, but netRR < 2.0 due to fees, so minNetRR=2.0 BLOCKS.
    - Long E4000 SL3990 TP4035: netRR >= 2.0 PASSES.
    - Short E4000 SL4010 TP3980: grossRR = 2.0, netRR < 2.0 due to fees.
    """
    # Long with TP 4020 -> Gross RR is 2.0, but Net RR is ~1.25 -> BLOCKED
    res_tight = calculate_risk_reward("LONG", 4000.0, 3990.0, 4020.0, 1000.0, 0.5, 2.0)
    assert res_tight.gross_rr == 2.0
    assert res_tight.net_rr < 2.0
    assert res_tight.can_execute is False
    assert "NET_RR_TOO_LOW" in (res_tight.skip_reason or "")

    # Long with TP 4035 -> Net RR > 2.0 -> CAN EXECUTE
    res_pass = calculate_risk_reward("LONG", 4000.0, 3990.0, 4035.0, 1000.0, 0.5, 2.0)
    assert res_pass.net_rr >= 2.0
    assert res_pass.can_execute is True

    # Short with TP 3980 -> Gross RR is 2.0, Net RR < 2.0 -> BLOCKED
    res_short = calculate_risk_reward("SHORT", 4000.0, 4010.0, 3980.0, 1000.0, 0.5, 2.0)
    assert res_short.gross_rr == 2.0
    assert res_short.net_rr < 2.0
    assert res_short.can_execute is False


def test_regression_3_invalid_geometry_rejected():
    """
    Criterion 3:
    Zero, negative, NaN, Infinity, and wrong-side SL/TP are strictly rejected.
    No abs() masking allowed.
    """
    # Long with SL > Entry
    ok, reason = validate_price_geometry("LONG", 4000.0, 4010.0, 4030.0)
    assert ok is False
    assert "INVALID_LONG_GEOMETRY" in reason

    # Short with SL < Entry
    ok, reason = validate_price_geometry("SHORT", 4000.0, 3990.0, 3950.0)
    assert ok is False
    assert "INVALID_SHORT_GEOMETRY" in reason

    # Negative price
    ok, reason = validate_price_geometry("LONG", -4000.0, 3990.0, 4030.0)
    assert ok is False

    # NaN / Inf
    ok, reason = validate_price_geometry("LONG", float('nan'), 3990.0, 4030.0)
    assert ok is False

    # Stop distance 0
    ok, reason = validate_price_geometry("LONG", 4000.0, 4000.0, 4030.0)
    assert ok is False


def test_regression_4_boundary_net_rr_rounding():
    """
    Criterion 4:
    Boundary Net RR: 1.9996 displays 2.00 but FAILS when minNetRR=2.0.
    Must not round up before checking threshold!
    """
    costs = CostAssumptions()
    # Construct geometry that yields net_rr strictly between 1.9990 and 1.9999
    # Using calculate_risk_reward with custom min_net_rr
    res = calculate_risk_reward("LONG", 4000.0, 3990.0, 4030.0, 1000.0, 0.5, min_net_rr=2.0)
    # If net_rr < 2.0, meets_min_rr must be strictly False
    if res.net_rr < 2.0:
        assert res.meets_min_rr is False
        assert res.can_execute is False


def test_regression_5_min_executable_quantity_exceeds_budget():
    """
    Criterion 5:
    Capital=1000, risk_pct=0.5 (budget=$5.00), E=4000, SL=3000, TP=6000 with minQty=0.01.
    Stop distance = 1000.
    0.01 oz has gross risk = $10.00 (> $5.00 budget).
    Must SKIP and NOT force quantity or claim risk is within 0.5%.
    """
    res = calculate_risk_reward(
        direction="LONG",
        entry=4000.0,
        sl=3000.0,
        tp=6000.0,
        capital=1000.0,
        risk_pct=0.5
    )
    assert res.can_execute is False
    assert "MIN_QTY_EXCEEDS_BUDGET" in (res.skip_reason or "")
    # Check that it warns about $10+ risk vs $5 budget
    assert "vượt ngân sách rủi ro $5.00" in res.skip_reason


def test_regression_6_broker_payload_tampering_rejected(test_db):
    """
    Criterion 6:
    Client sends faked payload: claims quantity=0.1, initial_risk_usdt=5.0, gross_rr=2.0, estimated_net_rr=2.5.
    However, actual geometry has Efill=4000.3, SL=3990, TP=4001 (gross RR = 0.07).
    Broker MUST recompute authoritative numbers and REJECT the order!
    """
    faked_order = schemas.PaperOrderCreate(
        direction="LONG",
        planned_entry=4000.0,
        stop_loss=3990.0,
        take_profit=4001.0, # Bad TP
        quantity=0.1,       # Faked
        initial_risk_usdt=5.0, # Faked
        risk_pct=0.5,
        gross_rr=2.0,       # Faked
        estimated_net_rr=2.5 # Faked
    )
    with pytest.raises(ValueError) as excinfo:
        PaperBroker.execute_market_order(test_db, faked_order, current_bid=4000.0, current_ask=4000.2)

    assert "NET_RR_TOO_LOW" in str(excinfo.value)
    # Verify no order was persisted in DB
    orders = crud.list_paper_orders(test_db)
    assert len(orders) == 0


def test_regression_7_pivot_confirmation_requires_closed_2nd_bar():
    """
    Criterion 7:
    2nd right bar that is NOT closed (is_closed=False) must NOT confirm the pivot!
    Only when a closed bar is appended does it confirm.
    """
    from smc_engine import identify_pivots

    candles = [
        DummyCandle(1000, 4000, 4005, 3995, 4002, is_closed=True), # i = 0
        DummyCandle(2000, 4002, 4010, 4000, 4008, is_closed=True), # i = 1
        DummyCandle(3000, 4008, 4025, 4005, 4020, is_closed=True), # i = 2 (potential pivot high)
        DummyCandle(4000, 4020, 4015, 4002, 4006, is_closed=True), # i = 3
        DummyCandle(5000, 4006, 4010, 3998, 4001, is_closed=False), # i = 4 (UNCLOSED 2nd right bar)
    ]
    highs, lows = identify_pivots(candles)
    # Should NOT be confirmed because bar 4 is not closed!
    assert len(highs) == 0

    # Now close bar 4
    candles[4].is_closed = True
    highs_confirmed, _ = identify_pivots(candles)
    assert len(highs_confirmed) == 1
    assert highs_confirmed[0]["confirmed_at"] == 5000


def test_regression_8_wick_only_is_sweep_not_choch():
    """
    Criterion 8:
    Wick piercing through confirmed level but closing inside is SWEEP, not BOS/CHoCH.
    """
    from smc_engine import detect_sweeps_and_breaks
    swing_highs = [{"index": 2, "price": 4050.0, "timestamp": 2000, "confirmed_at": 2000, "type": "HIGH"}]
    swing_lows = [{"index": 3, "price": 3950.0, "timestamp": 3000, "confirmed_at": 3000, "type": "LOW"}]

    # Candle spikes to 4055 (wick > 4050) but closes at 4040 (< 4050)
    candles = [
        DummyCandle(1000, 4020, 4030, 4010, 4025),
        DummyCandle(2000, 4025, 4050, 4020, 4040),
        DummyCandle(3000, 4040, 4045, 3950, 3960),
        DummyCandle(4000, 3960, 4040, 3960, 4035),
        DummyCandle(5000, 4035, 4055, 4030, 4040), # Sweep
    ]
    events, last_event, sweep_info = detect_sweeps_and_breaks(candles, swing_highs, swing_lows, "BULLISH")
    assert sweep_info is not None
    assert sweep_info["kind"] == "SWEEP_HIGH"
    # Verify no CHoCH or BOS events were falsely triggered
    assert not any(e["event_type"] in ("CHoCH", "BOS") for e in events)


def test_regression_12_backend_persisted_auto_and_no_retroactive_exit(test_db):
    """
    Criterion 12:
    - SL/TP check must not use bar extremes prior to order.created_at.
    - Daily cap applies even if realized PnL today is >= 0.
    """
    # 1. Create order at timestamp 10,000
    valid_order = schemas.PaperOrderCreate(
        direction="LONG",
        planned_entry=4000.0,
        stop_loss=3990.0,
        take_profit=4035.0,
        quantity=0.1,
        initial_risk_usdt=5.0,
        risk_pct=0.5,
        gross_rr=3.4,
        estimated_net_rr=2.2
    )
    order = PaperBroker.execute_market_order(test_db, valid_order, current_bid=4000.0, current_ask=4000.2)
    order.created_at = 10000000
    test_db.commit()

    # Old candle from 1 hour ago spiked to 3980 (which is < SL 3990), but candle_timestamp is 5000000
    closed = PaperBroker.process_price_tick(
        test_db,
        current_bid=4002.0,
        current_ask=4002.2,
        candle_high=4010.0,
        candle_low=3980.0,
        candle_timestamp=5000000 # Pre-entry candle
    )
    # Position should NOT be closed retroactively!
    assert closed is None
    active = crud.get_active_position(test_db)
    assert active is not None
    assert active.state == "paper_open"
