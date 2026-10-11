import pytest
from services.smc_scenario_types import EvidenceNode, validate_reversal_order
from services.smc_chain_service import (
    validate_reversal_chain,
    validate_chain_order,
    qualify_displacement,
)
from services.smc_scenario_config import ScenarioConfig

def test_validate_reversal_order_contract_positive():
    sweep = EvidenceNode(
        id='sw-1', kind='SWEEP_LOW', symbol='XAUUSDT', timeframe='5M',
        direction='LONG', occurred_at_ms=1000, known_at_ms=1300, level=2640.0
    )
    brk = EvidenceNode(
        id='brk-1', kind='CHOCH', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1500, known_at_ms=1800, source_ids=('sw-1',), level=2645.0
    )
    fvg = EvidenceNode(
        id='fvg-1', kind='FVG', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1800, known_at_ms=2100, source_ids=('brk-1',), level=2643.0
    )
    retest = EvidenceNode(
        id='ret-1', kind='RETEST', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=2200, known_at_ms=2500, source_ids=('fvg-1',), level=2643.2
    )

    check = validate_reversal_order(
        symbol='XAUUSDT', direction='LONG', as_of_ms=3000,
        sweep=sweep, brk=brk, fvg=fvg, retest=retest
    )
    assert check.verified is True
    assert len(check.reasons) == 0
    assert check.evidence_ids == ('sw-1', 'brk-1', 'fvg-1', 'ret-1')

def test_same_bar_ambiguous_rejection():
    # Sweep and break at the exact same candle timestamp
    sweep = EvidenceNode(
        id='sw-1', kind='SWEEP_LOW', symbol='XAUUSDT', timeframe='5M',
        direction='LONG', occurred_at_ms=1000, known_at_ms=1300, level=2640.0
    )
    brk = EvidenceNode(
        id='brk-1', kind='CHOCH', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1000, known_at_ms=1300, source_ids=('sw-1',), level=2645.0
    )
    fvg = EvidenceNode(
        id='fvg-1', kind='FVG', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1400, known_at_ms=1700, source_ids=('brk-1',), level=2643.0
    )
    retest = EvidenceNode(
        id='ret-1', kind='RETEST', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1800, known_at_ms=2100, source_ids=('fvg-1',), level=2643.2
    )

    check = validate_reversal_order(
        symbol='XAUUSDT', direction='LONG', as_of_ms=3000,
        sweep=sweep, brk=brk, fvg=fvg, retest=retest
    )
    assert check.verified is False
    assert 'SAME_BAR_OR_UNORDERED_SWEEP_BREAK' in check.reasons

def test_wrong_direction_rejection():
    sweep = EvidenceNode(
        id='sw-1', kind='SWEEP_HIGH', symbol='XAUUSDT', timeframe='5M', # High sweep for LONG is invalid
        direction='SHORT', occurred_at_ms=1000, known_at_ms=1300, level=2660.0
    )
    brk = EvidenceNode(
        id='brk-1', kind='CHOCH', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1500, known_at_ms=1800, source_ids=('sw-1',), level=2645.0
    )
    fvg = EvidenceNode(
        id='fvg-1', kind='FVG', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1800, known_at_ms=2100, source_ids=('brk-1',), level=2643.0
    )
    retest = EvidenceNode(
        id='ret-1', kind='RETEST', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=2200, known_at_ms=2500, source_ids=('fvg-1',), level=2643.2
    )

    check = validate_reversal_order(
        symbol='XAUUSDT', direction='LONG', as_of_ms=3000,
        sweep=sweep, brk=brk, fvg=fvg, retest=retest
    )
    assert check.verified is False
    assert 'SWEEP_SIDE_MISMATCH' in check.reasons

def test_unlinked_fvg_rejection():
    sweep = EvidenceNode(
        id='sw-1', kind='SWEEP_LOW', symbol='XAUUSDT', timeframe='5M',
        direction='LONG', occurred_at_ms=1000, known_at_ms=1300, level=2640.0
    )
    brk = EvidenceNode(
        id='brk-1', kind='CHOCH', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1500, known_at_ms=1800, source_ids=('sw-1',), level=2645.0
    )
    fvg = EvidenceNode(
        id='fvg-1', kind='FVG', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1800, known_at_ms=2100, source_ids=('different-id',), level=2643.0
    )
    retest = EvidenceNode(
        id='ret-1', kind='RETEST', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=2200, known_at_ms=2500, source_ids=('fvg-1',), level=2643.2
    )

    check = validate_reversal_order(
        symbol='XAUUSDT', direction='LONG', as_of_ms=3000,
        sweep=sweep, brk=brk, fvg=fvg, retest=retest
    )
    assert check.verified is False
    assert 'FVG_NOT_LINKED_TO_BREAK' in check.reasons

def test_retest_before_fvg_confirmation_rejection():
    sweep = EvidenceNode(
        id='sw-1', kind='SWEEP_LOW', symbol='XAUUSDT', timeframe='5M',
        direction='LONG', occurred_at_ms=1000, known_at_ms=1300, level=2640.0
    )
    brk = EvidenceNode(
        id='brk-1', kind='CHOCH', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1500, known_at_ms=1800, source_ids=('sw-1',), level=2645.0
    )
    fvg = EvidenceNode(
        id='fvg-1', kind='FVG', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1800, known_at_ms=2100, source_ids=('brk-1',), level=2643.0
    )
    # Retest occurs at 1900, before FVG is confirmed at 2100
    retest = EvidenceNode(
        id='ret-1', kind='RETEST', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1900, known_at_ms=2200, source_ids=('fvg-1',), level=2643.2
    )

    check = validate_reversal_order(
        symbol='XAUUSDT', direction='LONG', as_of_ms=3000,
        sweep=sweep, brk=brk, fvg=fvg, retest=retest
    )
    assert check.verified is False
    assert 'RETEST_BEFORE_FVG_CONFIRMATION' in check.reasons

def test_future_known_at_beyond_as_of_rejection():
    sweep = EvidenceNode(
        id='sw-1', kind='SWEEP_LOW', symbol='XAUUSDT', timeframe='5M',
        direction='LONG', occurred_at_ms=1000, known_at_ms=1300, level=2640.0
    )
    brk = EvidenceNode(
        id='brk-1', kind='CHOCH', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1500, known_at_ms=1800, source_ids=('sw-1',), level=2645.0
    )
    fvg = EvidenceNode(
        id='fvg-1', kind='FVG', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=1800, known_at_ms=2100, source_ids=('brk-1',), level=2643.0
    )
    retest = EvidenceNode(
        id='ret-1', kind='RETEST', symbol='XAUUSDT', timeframe='5M',
        direction='BULLISH', occurred_at_ms=2200, known_at_ms=3500, source_ids=('fvg-1',), level=2643.2
    )

    # as_of_ms is 3000, but retest.known_at_ms is 3500 (future lookahead)
    check = validate_reversal_order(
        symbol='XAUUSDT', direction='LONG', as_of_ms=3000,
        sweep=sweep, brk=brk, fvg=fvg, retest=retest
    )
    assert check.verified is False
    assert 'EVIDENCE_TIME_INVALID' in check.reasons
