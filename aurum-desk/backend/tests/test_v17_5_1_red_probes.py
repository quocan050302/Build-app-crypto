import copy
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, SystemConfig
from services.stop_placement_types import StopPlacementPolicy, StopAnchorResult, StopATRResult
from services.stop_placement_service import resolve_stop_anchor, compute_stop_atr
from services.scalp_m1_model_service import apply_stop_placement_to_candidate
from services.scalp_m1_types import get_scalp_config
from services.stop_placement_research_service import advance_stop_comparison
from services.stop_placement_policy_service import update_stop_placement_policy


def test_structural_missing_anchor_must_not_produce_executable_legacy_calc():
    result = apply_stop_placement_to_candidate(
        model_id='SMC_M1_CONTINUATION',
        direction='SHORT',
        entry_price=4000,
        baseline_sl=4005,
        baseline_target=3970,
        closed_1m=[],
        now_ms=900000,
        cfg=get_scalp_config(),
        policy_settings={'capital_usdt': 1000, 'risk_pct': 0.25, 'leverage': 5},
        stop_policy=StopPlacementPolicy(mode='PAPER_STRUCTURAL', version=1),
        stop_context={'atr_m1': StopATRResult(status='UNAVAILABLE', value=0, period=14, timeframe='1M', method='SMA_TR_V1', last_closed_at_ms=0, bars_count=0)},
        candidate_key='missing_anchor',
        anchor_result=StopAnchorResult(status='WAITING_EVIDENCE', anchor=None),
        atr_1m_val=1,
        bid=4000,
        ask=4000.1,
    )
    # Result could be tuple or StopApplicationResult
    if hasattr(result, "allowed"):
        assert not result.allowed
        assert result.calculation is None or not result.calculation.can_execute
    else:
        sl, tp, calc, decision, _ = result
        assert not calc.can_execute, f'C mode missing anchor still executable: SL={sl}, status={decision.status}, applied={decision.applied_variant}'


def test_fvg_anchor_cannot_be_a_pivot_after_selected_break():
    old = {'id': 'origin_before_break', 'price': 101, 'occurred_at_ms': 60000, 'known_at_ms': 180000}
    later = {'id': 'unrelated_after_break', 'price': 100.2, 'occurred_at_ms': 420000, 'known_at_ms': 540000}
    result = resolve_stop_anchor(
        model_id='SMC_M1_FVG_CONTINUATION',
        direction='SHORT',
        closed_bars=[],
        as_of_ms=600000,
        selected_break={'id': 'break_at_240000', 'broken_at_ms': 240000, 'known_at_ms': 300000},
        selected_fvg={'id': 'fvg_this_leg', 'start_ms': 240000, 'confirmed_at': 360000},
        confirmed_swings=([old, later], []),
    )
    assert result.anchor is None or result.anchor.occurred_at_ms <= 240000, f'Picked post-break pivot as leg origin: {result}'


def test_m5_atr_reports_true_close_time_after_production_prefilter():
    from services.ny_quota_helpers import closed_candles_at
    bars = [SimpleNamespace(open_time=1000000 + i * 300000, timestamp=1000000 + i * 300000, is_closed=True, open=100, high=102, low=99, close=101) for i in range(16)]
    cutoff = bars[-1].open_time + 60000
    filtered = closed_candles_at(bars, now_ms=cutoff, duration_ms=300000)
    result = compute_stop_atr(filtered, period=14, timeframe='5M', as_of_ms=cutoff)
    assert result.status == 'AVAILABLE'
    actual_close = filtered[-1].open_time + 300000
    assert result.last_closed_at_ms == actual_close, f'M5 closure recorded 4 minutes early: {result.last_closed_at_ms} vs {actual_close}'


def test_research_advance_is_pure():
    state = {'direction': 'SHORT', 'planned_entry': 100, 'take_profit': 90, 'variant_a': {'can_execute': True, 'meets_min_rr': True, 'stop_loss': 105}}
    before = copy.deepcopy(state)
    advance_stop_comparison(state, {'bid': 100, 'ask': 100.1}, as_of_ms=100000)
    assert state == before, 'advance mutates nested state of caller'


def test_research_invalid_quote_cannot_open_position():
    state = {'direction': 'SHORT', 'planned_entry': 100, 'take_profit': 90, 'as_of_ms': 1000,
        'variant_a': {'can_execute': True, 'meets_min_rr': True, 'stop_loss': 105}}
    result = advance_stop_comparison(state, {}, as_of_ms=2000)
    assert result['variant_a'].get('execution_state', 'WAIT_ENTRY') == 'WAIT_ENTRY', result


def test_policy_stale_cached_session_cannot_overwrite_current_version(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'cas.db'))
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, autoflush=False)
    with Factory() as db:
        update_stop_placement_policy(db, {'mode': 'LEGACY'}, expected_version=1, now_ms=1000)
    with Factory() as s1, Factory() as s2:
        held_rows = s2.query(SystemConfig).all()  # Keep stale ORM identities strongly referenced.
        assert held_rows
        update_stop_placement_policy(s1, {'atr_period': 20}, expected_version=2, now_ms=2000)
        try:
            update_stop_placement_policy(s2, {'min_buffer_ticks': 10}, expected_version=2, now_ms=3000)
        except ValueError:
            return
        pytest.fail('Stale expected_version=2 accepted after version3 was committed; no DB CAS fence')
