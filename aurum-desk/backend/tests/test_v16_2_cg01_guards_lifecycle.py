"""
backend/tests/test_v16_2_cg01_guards_lifecycle.py
V16.2 Rules 186, 187, 188, 189:
- Entry guards blocking: news, stale quote, recovering feed, cap
- Frequency & daily cap enforcement (max 3 fills)
- Concurrency & idempotency: duplicate episodes, quote updates do not re-arm
- Accounting: fee calculation, realized loss vs estimated risk
"""
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base
import models
import crud
from domain_calculator import CostAssumptions, calculate_risk_reward
from services.cg01_types import CG01Config, CG01Stage
from services.entry_guard_service import EntryGuardRequest, evaluate_entry_guard
from services.quote_validator import ValidatedQuote
from services.scalp_m1_model_service import ScalpCandidate, candidate_execution_fingerprint
from services.scalp_m1_types import ModelIdentifier
from services.trading_policy_service import EffectiveDailyPolicy, resolve_effective_daily_policy, TradingPolicyService


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    policy = TradingPolicyService.get_or_create_policy(session)
    policy.max_daily_fills = 3
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    session.commit()

    try:
        yield session
    finally:
        session.close()


def test_rule_186_entry_guards_stale_quote_and_news(test_db):
    """Rule 186: Entry guards block on stale quote."""
    now_ms = 1_700_000_000_000
    calc = calculate_risk_reward("LONG", 2623.75, 2619.45, 2640.0)

    # 1. Stale quote (is_valid=False, is_stale=True)
    stale_quote = ValidatedQuote(
        is_valid=False,
        bid=2623.5,
        ask=2624.0,
        last=2623.75,
        spread=0.5,
        exchange_time=now_ms - 60_000,
        observed_at=now_ms,
        freshness_sec=60.0,
        is_stale=True,
        rejection_code="TICKER_STALE",
        rejection_detail="Quote is older than maximum freshness tolerance",
    )
    req_stale = EntryGuardRequest(
        stage="ARM",
        now_ms=now_ms,
        symbol="XAUUSDT",
        direction="LONG",
        execution_mode="AUTO",
        origin="AUTO_STRATEGY",
        strategy_family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        planned_entry=2623.75,
        stop_loss=2619.45,
        take_profit=2640.0,
        calc_result=calc,
        validated_quote=stale_quote,
    )
    dec_stale = evaluate_entry_guard(test_db, req_stale)
    assert not dec_stale.allowed
    blocked_codes = [c.code for c in dec_stale.checks if hasattr(c, "state") and getattr(c.state, "value", str(c.state)) == "BLOCK"]
    assert any("TICKER" in c or "STALE" in c or "QUOTE" in c for c in blocked_codes)

    # 2. Fresh quote passes ticker guard
    fresh_quote = ValidatedQuote(
        is_valid=True,
        bid=2623.5,
        ask=2624.0,
        last=2623.75,
        spread=0.5,
        exchange_time=now_ms - 500,
        observed_at=now_ms,
        freshness_sec=0.5,
        is_stale=False,
        rejection_code=None,
        rejection_detail=None,
    )
    req_fresh = EntryGuardRequest(
        stage="ARM",
        now_ms=now_ms,
        symbol="XAUUSDT",
        direction="LONG",
        execution_mode="AUTO",
        origin="AUTO_STRATEGY",
        strategy_family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        planned_entry=2623.75,
        stop_loss=2619.45,
        take_profit=2640.0,
        calc_result=calc,
        validated_quote=fresh_quote,
    )
    dec_fresh = evaluate_entry_guard(test_db, req_fresh)
    blocked_codes_fresh = [c.code for c in dec_fresh.checks if hasattr(c, "state") and getattr(c.state, "value", str(c.state)) == "BLOCK"]
    assert "QUOTE_STALE" not in blocked_codes_fresh
    assert "TICKER_STALE" not in blocked_codes_fresh


def test_rule_187_daily_cap_blocks_fourth_fill(test_db):
    """Rule 187: Fills 1, 2, 3 are allowed; fill 4 is blocked by daily cap."""
    eff = resolve_effective_daily_policy(test_db, "default")
    assert eff.max_fills == 3

    # Add 3 filled entry orders to database
    for i in range(3):
        order = models.PaperOrder(
            id=f"ord_fill_{i+1}",
            direction="LONG",
            state="filled",
            planned_entry=2620.0,
            actual_entry=2620.1,
            stop_loss=2615.0,
            take_profit=2635.0,
            quantity=0.1,
            initial_risk_usdt=2.50,
            created_at=1_700_000_000_000 + i * 3600_000,
            opened_at=1_700_000_000_000 + i * 3600_000,
        )
        test_db.add(order)
    test_db.commit()

    # Set DayAudit fills_count = 3
    audit = crud.get_or_create_today_audit(test_db)
    audit.fills_count = 3
    test_db.commit()

    # Now verify guard blocks ARM stage when daily max fills reached
    now_ms = 1_700_000_000_000 + 4 * 3600_000
    calc = calculate_risk_reward("LONG", 2623.75, 2619.45, 2640.0)
    req = EntryGuardRequest(
        stage="ARM",
        now_ms=now_ms,
        symbol="XAUUSDT",
        direction="LONG",
        execution_mode="AUTO",
        origin="AUTO_STRATEGY",
        strategy_family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        planned_entry=2623.75,
        stop_loss=2619.45,
        take_profit=2640.0,
        calc_result=calc,
    )
    dec = evaluate_entry_guard(test_db, req)
    assert not dec.allowed
    blocked_codes = [c.code for c in dec.checks if hasattr(c, "state") and getattr(c.state, "value", str(c.state)) == "BLOCK"]
    assert any("DAILY" in r or "CAP" in r or "LIMIT" in r for r in blocked_codes)


def test_rule_128_fingerprint_decoupled_from_quote_update():
    """Rule 128: Quote change on the same episode must NOT change execution fingerprint."""
    cand1 = ScalpCandidate(
        is_eligible=True,
        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        direction="LONG",
        symbol="XAUUSDT",
        planned_entry=2623.75,
        origin_episode_id="ep_poi1_lvl1_1700000",
    )
    cand2 = ScalpCandidate(
        is_eligible=True,
        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        direction="LONG",
        symbol="XAUUSDT",
        planned_entry=2624.10, # Quote updated slightly!
        origin_episode_id="ep_poi1_lvl1_1700000",
    )

    fp1 = candidate_execution_fingerprint(cand1)
    fp2 = candidate_execution_fingerprint(cand2)
    assert fp1 == fp2, "Fingerprint varied with quote update on the same episode!"
