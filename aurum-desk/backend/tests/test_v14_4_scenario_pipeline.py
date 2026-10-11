import pytest
from services.smc_scenario_types import (
    EvidenceNode,
    ScenarioState,
    validate_reversal_order,
)
from services.smc_chain_service import (
    validate_reversal_chain,
    validate_entry_evidence_bundle,
)
from services.smc_scenario_plan import build_scenario_plan
from services.smc_scenario_config import ScenarioConfig

def test_long_reversal_confirmed_pipeline():
    config = ScenarioConfig()
    now_ms = 10000

    # 1. Confirmed sell-side sweep
    sweep = EvidenceNode(
        id='sw-long-1', kind='SWEEP_LOW', symbol='XAUUSDT', timeframe='5M',
        direction='LONG', occurred_at_ms=1000, known_at_ms=1300, level=2640.0
    )
    # 2. Bullish CHoCH displacement break linked to sweep
    brk = EvidenceNode(
        id='brk-long-1', kind='CHOCH', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=2000, known_at_ms=2300, source_ids=('sw-long-1',), level=2648.0
    )
    # 3. Bullish FVG linked to break
    fvg = EvidenceNode(
        id='fvg-long-1', kind='FVG', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=2300, known_at_ms=2600, source_ids=('brk-long-1',), level=2645.0
    )
    # 4. Bullish retest linked to FVG
    retest = EvidenceNode(
        id='ret-long-1', kind='RETEST', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=3000, known_at_ms=3300, source_ids=('fvg-long-1',), level=2645.2
    )

    check = validate_reversal_chain(
        symbol='XAUUSDT', direction='LONG', as_of_ms=now_ms,
        sweep=sweep, brk=brk, fvg=fvg, retest=retest, config=config
    )
    assert check.verified is True
    assert len(check.reasons) == 0

    # 5. Plan generation
    plan = build_scenario_plan(
        symbol='XAUUSDT', direction='LONG',
        retest_price=2645.2, sweep_extreme=2640.0, target_level=2665.0,
        config=config, now_ms=now_ms
    )
    assert plan.is_valid is True
    assert plan.direction == 'LONG'
    assert plan.stop_loss < plan.planned_entry < plan.take_profit
    assert plan.net_rr >= config.min_net_rr

def test_short_reversal_confirmed_pipeline():
    config = ScenarioConfig()
    now_ms = 10000

    # 1. Confirmed buy-side sweep
    sweep = EvidenceNode(
        id='sw-short-1', kind='SWEEP_HIGH', symbol='XAUUSDT', timeframe='5M',
        direction='SHORT', occurred_at_ms=1000, known_at_ms=1300, level=2660.0
    )
    # 2. Bearish CHoCH displacement break linked to sweep
    brk = EvidenceNode(
        id='brk-short-1', kind='CHOCH', symbol='XAUUSDT', timeframe='5M',
        direction='BEARISH', occurred_at_ms=2000, known_at_ms=2300, source_ids=('sw-short-1',), level=2652.0
    )
    # 3. Bearish FVG linked to break
    fvg = EvidenceNode(
        id='fvg-short-1', kind='FVG', symbol='XAUUSDT', timeframe='5M',
        direction='BEARISH', occurred_at_ms=2300, known_at_ms=2600, source_ids=('brk-short-1',), level=2655.0
    )
    # 4. Bearish retest linked to FVG
    retest = EvidenceNode(
        id='ret-short-1', kind='RETEST', symbol='XAUUSDT', timeframe='5M',
        direction='BEARISH', occurred_at_ms=3000, known_at_ms=3300, source_ids=('fvg-short-1',), level=2654.8
    )

    check = validate_reversal_chain(
        symbol='XAUUSDT', direction='SHORT', as_of_ms=now_ms,
        sweep=sweep, brk=brk, fvg=fvg, retest=retest, config=config
    )
    assert check.verified is True
    assert len(check.reasons) == 0

    # 5. Plan generation: SHORT TP < Entry < SL
    plan = build_scenario_plan(
        symbol='XAUUSDT', direction='SHORT',
        retest_price=2654.8, sweep_extreme=2660.0, target_level=2635.0,
        config=config, now_ms=now_ms
    )
    assert plan.is_valid is True
    assert plan.direction == 'SHORT'
    assert plan.take_profit < plan.planned_entry < plan.stop_loss
    assert plan.net_rr >= config.min_net_rr
