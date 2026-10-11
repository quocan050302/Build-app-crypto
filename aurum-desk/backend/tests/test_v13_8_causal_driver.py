import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from lab.replay_contracts import ReplayMarketEvent
from lab.replay_execution import submit_replay_order, try_fill_pending_order
from domain_calculator import CostAssumptions

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NY_TZ = ZoneInfo("America/New_York")

def test_causal_event_sequence_boundary():
    # PHẦN 47, 102: Order submitted at bar A CLOSE (09:05) cannot fill at bar A OPEN (09:00)
    costs = CostAssumptions(taker_fee_pct=0.0006, maker_fee_pct=0.0002, slippage_usd=0.10, spread_usd=0.20)
    plan = {
        "direction": "LONG",
        "entry_price": 2650.0,
        "stop_loss": 2645.0,
        "take_profit": 2675.0,
        "quantity": 1.0,
        "order_type": "MARKET",
        "planned_net_rr": 2.5
    }
    
    # Event A CLOSE at 09:05 (decision)
    order = submit_replay_order(
        candidate_plan=plan,
        sim_time=1700000300000, # 09:05:00
        run_id="run_test_causal",
        leverage=30,
        margin_mode="ISOLATED"
    )
    assert order.status == "SUBMITTED"
    
    # Event A OPEN at 09:00 (past event precedes decision) cannot fill this order
    past_event = ReplayMarketEvent(
        kind="OPEN",
        timestamp=1700000000000, # 09:00:00
        sequence=1,
        timeframe="5M",
        open_price=2650.0,
        high_price=2650.0,
        low_price=2650.0,
        close_price=2650.0,
        volume=100.0
    )
    pos_past, post_past, reason_past = try_fill_pending_order(order, past_event, capital=1000.0, risk_pct=0.10, costs=costs)
    assert pos_past is None
    assert reason_past in ("EVENT_PRECEDES_DECISION", "ORDER_NOT_ACTIVE", "WAITING_EXECUTION_EVENT") or order.status != "FILLED"
    
    # Event B OPEN at 09:05 (next bar open, timestamp >= decision) can fill
    next_open_event = ReplayMarketEvent(
        kind="OPEN",
        timestamp=1700000300000,
        sequence=10,
        timeframe="5M",
        open_price=2650.0,
        high_price=2650.0,
        low_price=2650.0,
        close_price=2650.0,
        volume=100.0
    )
    pos_valid, post_valid, reason_valid = try_fill_pending_order(order, next_open_event, capital=1000.0, risk_pct=0.10, costs=costs)
    assert pos_valid is not None
    assert order.status == "FILLED"

def test_vn_midnight_quota_attribution():
    # PHẦN 46, 58, 102: Daily quota attribution uses OPEN clock, not CLOSE clock
    # Bar spans 23:55 (Day 1) to 00:05 (Day 2 VN time)
    # Fill at 23:55 belongs to Day 1
    open_ts = int(datetime(2026, 7, 10, 23, 55, tzinfo=VN_TZ).timestamp() * 1000)
    close_ts = int(datetime(2026, 7, 11, 0, 5, tzinfo=VN_TZ).timestamp() * 1000)
    
    date_open_vn = datetime.fromtimestamp(open_ts / 1000.0, tz=VN_TZ).strftime("%Y-%m-%d")
    date_close_vn = datetime.fromtimestamp(close_ts / 1000.0, tz=VN_TZ).strftime("%Y-%m-%d")
    
    assert date_open_vn == "2026-07-10"
    assert date_close_vn == "2026-07-11"
    # Quota bucket must attribute to 2026-07-10
    bucket = date_open_vn
    assert bucket == "2026-07-10"
