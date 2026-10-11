import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models
from database import run_schema_migrations
from services.entry_guard_service import evaluate_entry_guard, EntryGuardRequest
from services.quote_validator import QuoteValidator
from domain_calculator import calculate_risk_reward

@pytest.fixture
def session_factory():
    engine = create_engine('sqlite:///:memory:')
    models.Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine)
    def _create():
        return Session()
    return _create

def test_stale_quote_blocks_arm(session_factory):
    db = session_factory()
    now_ms = 1788220800000

    # Stale quote (from 30s ago)
    stale_quote = QuoteValidator.validate_ticker(
        {'bid': 2650.0, 'ask': 2650.2, 'last': 2650.0, 'timestamp': now_ms - 30000},
        now_ms=now_ms
    )

    calc = calculate_risk_reward(
        direction='LONG', entry=2650.0, sl=2640.0, tp=2680.0,
        capital=1000.0, risk_pct=0.25, now_ms=now_ms
    )

    req = EntryGuardRequest(
        stage='ARM',
        now_ms=now_ms,
        symbol='XAUUSDT',
        direction='LONG',
        execution_mode='AUTO',
        origin='AUTO_STRATEGY',
        planned_entry=2650.0,
        stop_loss=2640.0,
        take_profit=2680.0,
        calc_result=calc,
        validated_quote=stale_quote,
        strategy_family='STANDARD_SMC',
        setup_state='READY',
        structure_chain_verified=True,
    )

    decision = evaluate_entry_guard(db, req)
    assert decision.allowed is False
    assert any(b['code'] == 'QUOTE_STALE' for b in decision.blockers)

def test_unconfirmed_chain_blocks_certified_arm(session_factory):
    db = session_factory()
    now_ms = 1788220800000

    fresh_quote = QuoteValidator.validate_ticker(
        {'bid': 2650.0, 'ask': 2650.2, 'last': 2650.0, 'timestamp': now_ms - 1000},
        now_ms=now_ms
    )

    calc = calculate_risk_reward(
        direction='LONG', entry=2650.0, sl=2640.0, tp=2680.0,
        capital=1000.0, risk_pct=0.25, now_ms=now_ms
    )

    # structure_chain_verified is explicitly False
    req = EntryGuardRequest(
        stage='ARM',
        now_ms=now_ms,
        symbol='XAUUSDT',
        direction='LONG',
        execution_mode='AUTO',
        origin='AUTO_STRATEGY',
        planned_entry=2650.0,
        stop_loss=2640.0,
        take_profit=2680.0,
        calc_result=calc,
        validated_quote=fresh_quote,
        strategy_family='STANDARD_SMC',
        setup_state='READY',
        structure_chain_verified=False,
    )

    decision = evaluate_entry_guard(db, req)
    assert decision.allowed is False
    assert any(b['code'] == 'UNCONFIRMED_STRUCTURE_CHAIN' for b in decision.blockers)
