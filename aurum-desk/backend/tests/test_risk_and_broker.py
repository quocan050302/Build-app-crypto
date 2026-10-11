import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models, schemas, crud
from paper_broker import PaperBroker

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


def test_daily_fills_limit(test_db):
    """Max 3 fills per day UTC+7"""
    audit = crud.get_or_create_today_audit(test_db)
    assert audit.fills_count == 0

    # Fill 1
    crud.record_trade_fill_audit(test_db)
    can_open, _ = PaperBroker.can_open_position(test_db, 5.0)
    assert can_open is True

    # Fill 2
    crud.record_trade_fill_audit(test_db)
    can_open, _ = PaperBroker.can_open_position(test_db, 5.0)
    assert can_open is True

    # Fill 3
    crud.record_trade_fill_audit(test_db)
    can_open, reason = PaperBroker.can_open_position(test_db, 5.0)
    assert can_open is False
    assert "tối đa 3 lệnh" in reason


def test_two_consecutive_losses_rule(test_db):
    """Stop trading for the day after 2 consecutive losses"""
    # Loss 1 (-$5.00)
    crud.record_trade_close_audit(test_db, -5.0)
    audit = crud.get_or_create_today_audit(test_db)
    assert audit.consecutive_losses == 1
    assert audit.is_blocked is False

    # Loss 2 (-$5.00)
    crud.record_trade_close_audit(test_db, -5.0)
    audit = crud.get_or_create_today_audit(test_db)
    assert audit.consecutive_losses == 2
    assert audit.is_blocked is True
    assert "2 lệnh lỗ liên tiếp" in audit.block_reason

    can_open, reason = PaperBroker.can_open_position(test_db, 5.0)
    assert can_open is False


def test_daily_loss_cap_rule(test_db):
    """1.5% daily loss cap on $1000 starting equity = $15.00"""
    # Realized loss of $16.00 exceeds $15.00 cap
    crud.record_trade_close_audit(test_db, -16.0)
    audit = crud.get_or_create_today_audit(test_db)
    assert audit.is_blocked is True
    assert "1.5%" in audit.block_reason


def test_ambiguous_bar_sl_first(test_db):
    """
    Prompt requirement:
    Ambiguous bar touching both TP and SL assumes conservative SL-first.
    """
    order_create = schemas.PaperOrderCreate(
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
    # Execute paper entry
    order = PaperBroker.execute_market_order(test_db, order_create, current_bid=4000.0, current_ask=4000.2)
    assert order.state == "paper_open"

    # Candle spikes up to 4040 (TP is 4035) and down to 3985 (SL is 3990)
    closed = PaperBroker.process_price_tick(
        test_db,
        current_bid=4005.0,
        current_ask=4005.2,
        candle_high=4040.0,
        candle_low=3985.0
    )
    assert closed is not None
    assert closed.state == "closed"
    assert closed.exit_cause == "AMBIGUOUS_BAR_SL_FIRST"
    assert closed.actual_exit == 3990.0
    assert closed.realized_pnl_net < 0
