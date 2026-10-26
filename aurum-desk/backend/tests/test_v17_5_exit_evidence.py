import pytest
import time
import json
from database import engine, SessionLocal
from models import Base, PaperOrder
from services.shared_exit_evaluator import evaluate_order_exit
from services.trade_lifecycle_service import TradeLifecycleService


@pytest.fixture(autouse=True)
def clean_orders():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.query(PaperOrder).filter(PaperOrder.instrument == "XAUUSDT_TEST_EXIT").delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(PaperOrder).filter(PaperOrder.instrument == "XAUUSDT_TEST_EXIT").delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_short_exit_evaluated_against_ask():
    """
    SHORT SL hit is triggered by ASK price exceeding or meeting SL.
    If LAST is below SL but ASK has touched SL, exit evaluator triggers SL_HIT.
    """
    order_dict = {
        "direction": "SHORT",
        "planned_entry": 4000.0,
        "stop_loss": 4010.0,
        "take_profit": 3980.0,
    }

    # Case 1: ASK is 4010.50 (>= SL), but LAST is 4008.0 (< SL)
    # Must trigger SL_HIT because buyer/closer pays ASK!
    quote = {
        "bid": 4007.50,
        "ask": 4010.50,
        "last": 4008.0,
        "evidence_id": "tick_1",
    }
    res = evaluate_order_exit(order=order_dict, executable_quote=quote)
    assert res.triggered is True
    assert res.cause == "SL_HIT"
    assert res.price_basis == "ASK"
    assert res.applied_exit_price >= 4010.0


def test_long_exit_evaluated_against_bid():
    """
    LONG SL hit is triggered by BID price falling to or below SL.
    If LAST is above SL but BID has dropped to SL, exit evaluator triggers SL_HIT.
    """
    order_dict = {
        "direction": "LONG",
        "planned_entry": 4000.0,
        "stop_loss": 3990.0,
        "take_profit": 4020.0,
    }

    # BID is 3989.50 (<= SL), but LAST is 3991.0 (> SL)
    # Must trigger SL_HIT because seller/closer sells at BID!
    quote = {
        "bid": 3989.50,
        "ask": 3992.0,
        "last": 3991.0,
        "evidence_id": "tick_2",
    }
    res = evaluate_order_exit(order=order_dict, executable_quote=quote)
    assert res.triggered is True
    assert res.cause == "SL_HIT"
    assert res.price_basis == "BID"
    assert res.applied_exit_price <= 3990.0


def test_execute_close_persists_exit_evidence():
    """
    When TradeLifecycleService.execute_close runs with exit_evidence,
    it persists the evidence dictionary into order.cost_snapshot['exit_evidence'].
    """
    db = SessionLocal()
    now_ms = int(time.time() * 1000)
    try:
        order = PaperOrder(
            id="order_test_exit_evidence_1",
            instrument="XAUUSDT_TEST_EXIT",
            direction="SHORT",
            order_type="MARKET",
            quantity=0.1,
            planned_entry=4000.0,
            actual_entry=4000.0,
            stop_loss=4010.0,
            take_profit=3980.0,
            initial_risk_usdt=10.0,
            state="paper_open",
            created_at=now_ms,
            opened_at=now_ms,
            cost_snapshot=json.dumps({"min_net_rr_at_arm": 2.0}),
        )
        db.add(order)
        db.commit()

        evidence = {
            "price_basis": "QUOTE_ASK",
            "trigger_level": 4010.0,
            "applied_exit_price": 4010.25,
            "quote_timestamp_ms": now_ms,
            "evidence_reference": "TICK_QUOTE",
        }

        closed_order = TradeLifecycleService.execute_close(
            db=db,
            order_id="order_test_exit_evidence_1",
            exit_price=4010.25,
            exit_cause="SL_HIT",
            exit_evidence=evidence,
        )

        assert closed_order is not None
        assert closed_order.state == "closed"
        assert closed_order.cost_snapshot is not None
        cost_snap = json.loads(closed_order.cost_snapshot) if isinstance(closed_order.cost_snapshot, str) else closed_order.cost_snapshot
        assert "exit_evidence" in cost_snap
        saved_ev = cost_snap["exit_evidence"]
        assert saved_ev["price_basis"] == "QUOTE_ASK"
        assert saved_ev["trigger_level"] == 4010.0
    finally:
        db.close()
