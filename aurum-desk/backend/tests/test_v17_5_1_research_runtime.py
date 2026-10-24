"""
Aurum V17.5.1 — Research Runtime Integration Tests
Verifies that ExecutionConsumer.evaluate_quote_sync calls advance_active_stop_comparisons,
advancing simulated research states through WAIT_ENTRY -> OPEN -> CLOSED even when
no active production order is open.
"""
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, PaperObservation
from services.execution_consumer import ExecutionConsumer
from services.stop_placement_research_service import persist_stop_comparison


@pytest.fixture
def test_db(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "research_runtime.db"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    return Session


def test_research_advances_through_execution_consumer_without_active_order(test_db):
    now_ms = 1000000
    plan_dict = {
        "plan_id": "test_plan_runtime_1",
        "opportunity_key": "opp_runtime_1",
        "symbol": "XAUUSDT",
        "direction": "SHORT",
        "planned_entry": 2000.0,
        "take_profit": 1980.0,
        "as_of_ms": now_ms,
        "plan_cutoff_ms": now_ms,
        "variant_a": {
            "can_execute": True,
            "meets_min_rr": True,
            "stop_loss": 2005.0,
            "planned_entry": 2000.0,
            "take_profit": 1980.0,
            "execution_state": "WAIT_ENTRY",
            "quantity": 1.0,
        },
        "variant_b": {
            "can_execute": True,
            "meets_min_rr": True,
            "stop_loss": 2008.0,
            "planned_entry": 2000.0,
            "take_profit": 1980.0,
            "execution_state": "WAIT_ENTRY",
            "quantity": 1.0,
        },
        "variant_c": {
            "can_execute": True,
            "meets_min_rr": True,
            "stop_loss": 2010.0,
            "planned_entry": 2000.0,
            "take_profit": 1980.0,
            "execution_state": "WAIT_ENTRY",
            "quantity": 1.0,
        },
    }

    # 1. Persist the comparison plan
    with test_db() as db:
        persist_stop_comparison(db, plan_dict, now_ms=now_ms)
        db.commit()

    consumer = ExecutionConsumer()

    # 2. Feed quote that touches entry price (2000.0) -> transitions WAIT_ENTRY -> OPEN
    from services.quote_validator import CanonicalQuote
    entry_quote = CanonicalQuote(
        symbol="XAUUSDT",
        last=2000.1,
        bid=2000.0,
        ask=2000.2,
        exchange_ts_ms=now_ms + 1000,
        received_at_ms=now_ms + 1000,
        status="VALID",
    )

    with test_db() as db:
        # evaluate_quote_sync returns early if no active orders, but invokes research dispatch
        consumer.evaluate_quote_sync(db, entry_quote, now_ms=now_ms + 1000)
        db.commit()

    with test_db() as db:
        row = db.query(PaperObservation).filter_by(event_kind="COMPARISON_PLAN").one()
        state = json.loads(row.payload_json)
        assert state["variant_a"]["execution_state"] == "OPEN", f"Expected OPEN, got {state['variant_a']}"
        assert state["variant_a"]["actual_entry"] == 2000.0

    # 3. Feed quote that hits Variant A stop loss (2005.0) -> Variant A CLOSED, B and C stay OPEN
    sl_quote_a = CanonicalQuote(
        symbol="XAUUSDT",
        last=2005.6,
        bid=2005.5,
        ask=2005.7,  # Short stop hit on ASK > 2005.0
        exchange_ts_ms=now_ms + 5000,
        received_at_ms=now_ms + 5000,
        status="VALID",
    )

    with test_db() as db:
        consumer.evaluate_quote_sync(db, sl_quote_a, now_ms=now_ms + 5000)
        db.commit()

    with test_db() as db:
        row = db.query(PaperObservation).filter_by(event_kind="COMPARISON_PLAN").one()
        state = json.loads(row.payload_json)
        assert state["variant_a"]["execution_state"] == "CLOSED"
        assert state["variant_a"]["exit_cause"] == "SL_HIT"
        # Variant B (SL 2008) and Variant C (SL 2010) must remain OPEN
        assert state["variant_b"]["execution_state"] == "OPEN"
        assert state["variant_c"]["execution_state"] == "OPEN"
