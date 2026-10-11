import pytest
from services.smc_liquidity_service import (
    cluster_equal_levels,
    build_liquidity_map,
    select_observation_anchor,
    select_unconsumed_target,
    update_liquidity_state,
)
from services.smc_scenario_types import LiquidityLevel

def test_equal_highs_clustering():
    swings = [
        {'id': 'sh-1', 'price': 2650.00, 'timestamp': 1000, 'confirmed_at': 1300},
        {'id': 'sh-2', 'price': 2650.05, 'timestamp': 2000, 'confirmed_at': 2300}, # Within 0.10 tolerance
        {'id': 'sh-3', 'price': 2660.00, 'timestamp': 3000, 'confirmed_at': 3300}, # Far away
    ]
    clusters = cluster_equal_levels(swings, side='BUY_SIDE', tolerance_price=0.10)
    assert len(clusters) == 1
    eqh = clusters[0]
    assert eqh.kind == 'EQH'
    assert eqh.price == 2650.03
    assert list(eqh.source_ids) == ['sh-1', 'sh-2']

def test_consumed_target_not_reselected():
    lvl1 = LiquidityLevel(
        id='target-1', label_vi='Target 1', source_kind='SWING_HIGH', side='BUY_SIDE',
        price=2660.0, occurred_at_ms=1000, known_at_ms=1300, source_ids=('sh-1',),
        state='CONSUMED' # Already consumed!
    )
    lvl2 = LiquidityLevel(
        id='target-2', label_vi='Target 2', source_kind='SWING_HIGH', side='BUY_SIDE',
        price=2670.0, occurred_at_ms=2000, known_at_ms=2300, source_ids=('sh-2',),
        state='UNTOUCHED' # Fresh target
    )
    levels = [lvl1, lvl2]

    chosen = select_unconsumed_target(levels, direction='LONG', entry_price=2650.0)
    assert chosen is not None
    assert chosen.id == 'target-2'
    assert chosen.price == 2670.0

def test_short_target_must_be_below_entry():
    lvl_above = LiquidityLevel(
        id='lvl-above', label_vi='Swing Low Above', source_kind='SWING_LOW', side='SELL_SIDE',
        price=2655.0, occurred_at_ms=1000, known_at_ms=1300, source_ids=('sl-1',),
        state='UNTOUCHED'
    )
    lvl_below = LiquidityLevel(
        id='lvl-below', label_vi='Swing Low Below', source_kind='SWING_LOW', side='SELL_SIDE',
        price=2635.0, occurred_at_ms=2000, known_at_ms=2300, source_ids=('sl-2',),
        state='UNTOUCHED'
    )
    levels = [lvl_above, lvl_below]

    # For SHORT with entry 2650.0, only lvl_below (2635.0 < 2650.0) is valid
    chosen = select_unconsumed_target(levels, direction='SHORT', entry_price=2650.0)
    assert chosen is not None
    assert chosen.id == 'lvl-below'
    assert chosen.price == 2635.0

def test_no_valid_target_returns_none():
    lvl_above = LiquidityLevel(
        id='lvl-above', label_vi='Swing Low Above', source_kind='SWING_LOW', side='SELL_SIDE',
        price=2655.0, occurred_at_ms=1000, known_at_ms=1300, source_ids=('sl-1',),
        state='UNTOUCHED'
    )
    levels = [lvl_above]

    # For SHORT with entry 2650.0, no target below 2650.0 exists
    chosen = select_unconsumed_target(levels, direction='SHORT', entry_price=2650.0)
    assert chosen is None
