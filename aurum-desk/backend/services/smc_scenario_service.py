import time
import math
import logging
from typing import Dict, Any, List, Optional, Tuple, Set
from zoneinfo import ZoneInfo
from datetime import datetime
from sqlalchemy.orm import Session

from services.smc_scenario_types import (
    ScenarioSnapshot,
    ScenarioState,
    EvidenceNode,
    LiquidityLevel,
    ChainCheck,
    make_evidence_id,
    make_scenario_identity,
)
from services.smc_scenario_config import ScenarioConfig, active_scenario_config
from services.smc_session_context import (
    SessionWindowConfig,
    build_session_range,
    build_previous_day_levels,
)
from services.smc_regime_service import classify_regime, build_dealing_range
from services.smc_liquidity_service import (
    build_liquidity_map,
    select_observation_anchor,
    select_unconsumed_target,
    update_liquidity_state,
)
from services.smc_chain_service import (
    validate_reversal_chain,
    qualify_displacement,
    bind_fvg_to_leg,
    confirm_closed_retest,
    confirm_quote_retest,
)
from services.smc_scenario_plan import build_scenario_plan, ScenarioPlan
from services.smc_scenario_summary import build_scenario_summary_vi
from services.smc_scenario_repository import upsert_scenario_revision, get_active_scenarios
from services.ny_quota_helpers import closed_candles_at
import smc_engine

logger = logging.getLogger('smc_scenario_service')

TF_DURATIONS_MS = {
    '1M': 60 * 1000,
    '5M': 5 * 60 * 1000,
    '15M': 15 * 60 * 1000,
    '1H': 60 * 60 * 1000,
    '4H': 4 * 60 * 60 * 1000,
    'D': 24 * 60 * 60 * 1000,
}

def validate_context_coverage(
    candles: List[Any],
    timeframe: str,
    now_ms: int,
    min_bars: int = 20,
) -> Dict[str, Any]:
    """
    Validates candle contiguous coverage for a timeframe without lookahead.
    """
    duration = TF_DURATIONS_MS.get(timeframe, 5 * 60 * 1000)
    if not candles:
        return {
            'is_valid': False,
            'reason': 'NO_CANDLES',
            'bar_count': 0,
            'last_timestamp': None,
        }

    closed_bars = [c for c in candles if getattr(c, 'is_closed', True) and c.timestamp + duration <= now_ms]
    if len(closed_bars) < min_bars:
        return {
            'is_valid': False,
            'reason': f'INSUFFICIENT_BARS: {len(closed_bars)} < {min_bars}',
            'bar_count': len(closed_bars),
            'last_timestamp': closed_bars[-1].timestamp if closed_bars else None,
        }

    # Check for excessive gaps (> 3 missing bars between consecutive closed candles)
    gaps = 0
    for idx in range(1, len(closed_bars)):
        diff = closed_bars[idx].timestamp - closed_bars[idx - 1].timestamp
        if diff > 4 * duration:
            gaps += 1

    return {
        'is_valid': gaps == 0,
        'reason': f'DATA_GAPS_DETECTED: {gaps} gaps' if gaps > 0 else 'OK',
        'bar_count': len(closed_bars),
        'last_timestamp': closed_bars[-1].timestamp,
    }

def load_closed_context(
    candles_by_tf: Dict[str, List[Any]],
    now_ms: int,
    config: ScenarioConfig,
) -> Dict[str, Any]:
    """
    Filters all available timeframe candles strictly to closed bars at now_ms.
    """
    context = {}
    for tf in ('D', '4H', '1H', '15M', '5M'):
        raw = candles_by_tf.get(tf, [])
        dur = TF_DURATIONS_MS.get(tf, 5 * 60 * 1000)
        try:
            closed = closed_candles_at(raw, now_ms=now_ms, duration_ms=dur)
        except Exception:
            closed = [c for c in raw if getattr(c, 'is_closed', True) and c.timestamp + dur <= now_ms]
        context[tf] = closed
    return context

def compute_scenario_expiry(
    as_of_ms: int,
    config: ScenarioConfig,
    ny_session_end_ms: Optional[int] = None,
) -> int:
    """
    Computes scenario expiration timestamp.
    """
    default_ttl_ms = config.scenario_expiry_bars * 5 * 60 * 1000
    candidate = as_of_ms + default_ttl_ms
    if ny_session_end_ms is not None and ny_session_end_ms > as_of_ms:
        return min(candidate, ny_session_end_ms)
    return candidate

def evaluate_scenario_invalidation(
    scenario: ScenarioSnapshot,
    current_price: float,
    closed_candles: List[Any],
) -> Tuple[bool, Optional[str]]:
    """
    Checks if an existing scenario has been structurally invalidated.
    """
    if scenario.state in (ScenarioState.INVALIDATED, ScenarioState.EXPIRED, ScenarioState.SUPERSEDED):
        return True, 'ALREADY_TERMINAL'

    inv_price = scenario.invalidation_price
    if inv_price is not None and inv_price > 0:
        if scenario.direction == 'LONG' and current_price <= inv_price:
            return True, f'PRICE_VIOLATED_INVALIDATION_LOW: {current_price:.2f} <= {inv_price:.2f}'
        elif scenario.direction == 'SHORT' and current_price >= inv_price:
            return True, f'PRICE_VIOLATED_INVALIDATION_HIGH: {current_price:.2f} >= {inv_price:.2f}'

    return False, None

def transition_scenario(
    current: ScenarioSnapshot,
    next_state: ScenarioState,
    as_of_ms: int,
    reason: Optional[str] = None,
) -> ScenarioSnapshot:
    """
    Transitions a scenario to a new state with strict graph guards.
    """
    allowed_transitions: Dict[ScenarioState, Set[ScenarioState]] = {
        ScenarioState.OBSERVING: {
            ScenarioState.WAITING_LIQUIDITY, ScenarioState.WAITING_SWEEP, ScenarioState.BLOCKED,
            ScenarioState.INVALIDATED, ScenarioState.EXPIRED, ScenarioState.SUPERSEDED
        },
        ScenarioState.WAITING_LIQUIDITY: {
            ScenarioState.WAITING_SWEEP, ScenarioState.BLOCKED, ScenarioState.INVALIDATED,
            ScenarioState.EXPIRED, ScenarioState.SUPERSEDED
        },
        ScenarioState.WAITING_SWEEP: {
            ScenarioState.WAITING_BREAK, ScenarioState.BLOCKED, ScenarioState.INVALIDATED,
            ScenarioState.EXPIRED, ScenarioState.SUPERSEDED
        },
        ScenarioState.WAITING_BREAK: {
            ScenarioState.WAITING_FVG, ScenarioState.BLOCKED, ScenarioState.INVALIDATED,
            ScenarioState.EXPIRED, ScenarioState.SUPERSEDED
        },
        ScenarioState.WAITING_FVG: {
            ScenarioState.WAITING_RETEST, ScenarioState.BLOCKED, ScenarioState.INVALIDATED,
            ScenarioState.EXPIRED, ScenarioState.SUPERSEDED
        },
        ScenarioState.WAITING_RETEST: {
            ScenarioState.STRUCTURE_CONFIRMED, ScenarioState.BLOCKED, ScenarioState.INVALIDATED,
            ScenarioState.EXPIRED, ScenarioState.SUPERSEDED
        },
        ScenarioState.STRUCTURE_CONFIRMED: {
            ScenarioState.BLOCKED, ScenarioState.INVALIDATED, ScenarioState.EXPIRED, ScenarioState.SUPERSEDED
        },
        ScenarioState.BLOCKED: {
            ScenarioState.STRUCTURE_CONFIRMED, ScenarioState.WAITING_RETEST, ScenarioState.INVALIDATED,
            ScenarioState.EXPIRED, ScenarioState.SUPERSEDED
        },
        ScenarioState.INVALIDATED: set(),
        ScenarioState.EXPIRED: set(),
        ScenarioState.SUPERSEDED: set(),
    }

    if current.state in (ScenarioState.INVALIDATED, ScenarioState.EXPIRED, ScenarioState.SUPERSEDED):
        return current # Terminal states cannot transition

    allowed = allowed_transitions.get(current.state, set())
    if next_state not in allowed and next_state != current.state:
        logger.warning(f'Illegal transition from {current.state} to {next_state}; forcing INVALIDATED')
        next_state = ScenarioState.INVALIDATED

    blockers = list(current.blockers)
    if reason and reason not in blockers:
        blockers.append(reason)

    return ScenarioSnapshot(
        scenario_id=current.scenario_id,
        instance_id=current.instance_id,
        revision=current.revision + 1,
        symbol=current.symbol,
        setup_timeframe=current.setup_timeframe,
        context_timeframes=current.context_timeframes,
        strategy_family=current.strategy_family,
        direction=current.direction,
        state=next_state,
        as_of_ms=as_of_ms,
        evidence_cutoff_ms=as_of_ms,
        evidence_ids=current.evidence_ids,
        evidence_nodes=current.evidence_nodes,
        liquidity_anchor=current.liquidity_anchor,
        target_anchor=current.target_anchor,
        chain_check=current.chain_check,
        plan=current.plan,
        invalidation_price=current.invalidation_price,
        summary_vi=current.summary_vi,
        config_version=current.config_version,
        detector_version=current.detector_version,
        blockers=tuple(blockers),
    )

def select_primary_scenario(
    scenarios: List[ScenarioSnapshot],
) -> Optional[ScenarioSnapshot]:
    """
    Selects primary scenario deterministically based on state progression and freshness.
    """
    if not scenarios:
        return None

    state_priority = {
        ScenarioState.STRUCTURE_CONFIRMED: 10,
        ScenarioState.WAITING_RETEST: 9,
        ScenarioState.WAITING_FVG: 8,
        ScenarioState.WAITING_BREAK: 7,
        ScenarioState.WAITING_SWEEP: 6,
        ScenarioState.WAITING_LIQUIDITY: 5,
        ScenarioState.OBSERVING: 4,
        ScenarioState.BLOCKED: 3,
        ScenarioState.INVALIDATED: 1,
        ScenarioState.EXPIRED: 0,
        ScenarioState.SUPERSEDED: 0,
    }

    # Sort descending by priority, then by revision, then by as_of_ms
    active = [s for s in scenarios if s.state not in (ScenarioState.INVALIDATED, ScenarioState.EXPIRED, ScenarioState.SUPERSEDED)]
    if not active:
        return scenarios[0]

    sorted_active = sorted(
        active,
        key=lambda s: (state_priority.get(s.state, 0), s.revision, s.as_of_ms),
        reverse=True
    )
    return sorted_active[0]

def compute_scenario_proximity(
    scenario: ScenarioSnapshot,
    current_price: Optional[float],
    atr: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Calculates live distance to liquidity/entry/target in price and ATR units.
    """
    if current_price is None or current_price <= 0:
        return {'distance_price': None, 'distance_atr': None}

    ref_target = None
    if scenario.state == ScenarioState.STRUCTURE_CONFIRMED and scenario.plan:
        ref_target = scenario.plan.planned_entry
    elif scenario.liquidity_anchor:
        ref_target = scenario.liquidity_anchor.price

    if ref_target is None:
        return {'distance_price': None, 'distance_atr': None}

    dist_price = round(abs(current_price - ref_target), 2)
    dist_atr = round(dist_price / atr, 2) if (atr and atr > 0) else None

    return {
        'distance_price': dist_price,
        'distance_atr': dist_atr,
        'current_price': current_price,
        'target_level': ref_target,
    }

def evaluate_scenarios(
    symbol: str,
    context: Dict[str, List[Any]],
    previous_scenarios: List[ScenarioSnapshot],
    config: ScenarioConfig,
    now_ms: int,
    quote: Optional[Dict[str, Any]] = None,
    session_instance_id: Optional[str] = None,
) -> List[ScenarioSnapshot]:
    """
    Pure orchestration: Closed bar context + Quote -> ScenarioSnapshots.
    Does not mutate DB or make network calls.
    """
    m5_candles = context.get('5M', [])
    m15_candles = context.get('15M', [])
    h1_candles = context.get('1H', [])
    h4_candles = context.get('4H', [])

    if len(m5_candles) < 20:
        return []

    # 1. Regimes
    h4_regime = classify_regime(h4_candles, timeframe='4H') if h4_candles else 'UNKNOWN'
    h1_regime = classify_regime(h1_candles, timeframe='1H') if h1_candles else 'UNKNOWN'

    # 2. Swings & Sweeps on M5
    sh_m5, sl_m5 = smc_engine.identify_pivots(m5_candles, timeframe='5M')
    atr_val = smc_engine.compute_atr(m5_candles, period=config.displacement_atr_period)
    events, summary, latest_sweep, trend = smc_engine.detect_sweeps_and_breaks_causal(
        m5_candles, sh_m5, sl_m5, timeframe='5M', atr=atr_val
    )
    fvgs = smc_engine.detect_fvgs(m5_candles, timeframe='5M')

    # 3. Liquidity Map & Swings
    liq_map = build_liquidity_map(
        swings_high=sh_m5,
        swings_low=sl_m5,
        session_ranges=[],
        pd_levels=None,
        now_ms=now_ms,
        symbol=symbol,
    )

    # 4. Check potential reversal chains for LONG and SHORT
    results: List[ScenarioSnapshot] = []

    for direction in ('LONG', 'SHORT'):
        anchor = select_observation_anchor(liq_map, direction=direction, current_price=m5_candles[-1].close)
        target = select_unconsumed_target(
            liq_map,
            direction=direction,
            entry_level=m5_candles[-1].close,
            as_of_ms=now_ms
        )

        # Build evidence nodes if events exist
        # Filter sweeps and breaks for this direction
        sweep_events = [e for e in events if e.get('event_type') == 'SWEEP' and (
            (direction == 'LONG' and e.get('type') == 'SWEEP_LOW') or
            (direction == 'SHORT' and e.get('type') == 'SWEEP_HIGH')
        )]
        break_events = [e for e in events if e.get('event_type') in ('BOS', 'CHOCH', 'MSS') and (
            (direction == 'LONG' and e.get('direction') == 'BULLISH') or
            (direction == 'SHORT' and e.get('direction') == 'BEARISH')
        )]

        best_sweep = sweep_events[-1] if sweep_events else None
        best_break = break_events[-1] if break_events else None

        sweep_node = None
        break_node = None
        fvg_node = None
        retest_node = None
        chain_check = None
        plan = None
        state = ScenarioState.WAITING_LIQUIDITY

        if anchor is not None:
            state = ScenarioState.WAITING_SWEEP

        if best_sweep is not None:
            state = ScenarioState.WAITING_BREAK
            s_time = best_sweep.get('timestamp', now_ms)
            s_conf = best_sweep.get('confirmed_at', s_time + 5 * 60 * 1000)
            sweep_node = EvidenceNode(
                id=best_sweep.get('id', make_evidence_id(symbol, '5M', best_sweep.get('type', 'SWEEP'), s_time)),
                kind=best_sweep.get('type', 'SWEEP_LOW' if direction == 'LONG' else 'SWEEP_HIGH'),
                symbol=symbol,
                timeframe='5M',
                direction=direction,
                occurred_at_ms=s_time,
                known_at_ms=s_conf,
                level=best_sweep.get('level'),
                source_candle_open_ms=s_time,
            )

        if sweep_node and best_break:
            b_time = best_break.get('timestamp', now_ms)
            b_conf = best_break.get('confirmed_at', b_time + 5 * 60 * 1000)
            state = ScenarioState.WAITING_FVG
            break_node = EvidenceNode(
                id=best_break.get('id', make_evidence_id(symbol, '5M', best_break.get('event_type', 'CHOCH'), b_time)),
                kind=best_break.get('event_type', 'CHOCH'),
                symbol=symbol,
                timeframe='5M',
                direction='BULLISH' if direction == 'LONG' else 'BEARISH',
                occurred_at_ms=b_time,
                known_at_ms=b_conf,
                source_ids=(sweep_node.id,),
                level=best_break.get('level'),
                source_candle_open_ms=b_time,
            )

        if sweep_node and break_node:
            # Look for linked FVG
            matching_fvgs = [
                f for f in fvgs if f.get('direction') == ('BULLISH' if direction == 'LONG' else 'BEARISH')
                and f.get('start_at_ms', f.get('timestamp', 0)) >= sweep_node.occurred_at_ms
            ]
            if matching_fvgs:
                best_fvg = matching_fvgs[-1]
                f_time = best_fvg.get('start_at_ms', best_fvg.get('timestamp', now_ms))
                f_conf = best_fvg.get('confirmed_at', f_time + 5 * 60 * 1000)
                state = ScenarioState.WAITING_RETEST
                fvg_node = EvidenceNode(
                    id=best_fvg.get('id', make_evidence_id(symbol, '5M', 'FVG', f_time)),
                    kind='FVG',
                    symbol=symbol,
                    timeframe='5M',
                    direction='BULLISH' if direction == 'LONG' else 'BEARISH',
                    occurred_at_ms=f_time,
                    known_at_ms=f_conf,
                    source_ids=(break_node.id,),
                    level=(best_fvg.get('top', 0) + best_fvg.get('bottom', 0)) / 2.0,
                    source_candle_open_ms=f_time,
                )

        if sweep_node and break_node and fvg_node:
            # Check retest
            fvg_top = fvg_node.level + 0.5
            fvg_bottom = fvg_node.level - 0.5
            retest_found = False
            retest_time = 0
            retest_price = fvg_node.level

            # Check closed candles after FVG confirmed
            for c in m5_candles:
                if c.timestamp >= fvg_node.known_at_ms:
                    if direction == 'LONG' and c.low <= fvg_top and c.close >= fvg_bottom:
                        retest_found = True
                        retest_time = c.timestamp
                        retest_price = c.low
                        break
                    elif direction == 'SHORT' and c.high >= fvg_bottom and c.close <= fvg_top:
                        retest_found = True
                        retest_time = c.timestamp
                        retest_price = c.high
                        break

            if retest_found:
                state = ScenarioState.STRUCTURE_CONFIRMED
                retest_node = EvidenceNode(
                    id=make_evidence_id(symbol, '5M', 'RETEST', retest_time),
                    kind='RETEST',
                    symbol=symbol,
                    timeframe='5M',
                    direction='BULLISH' if direction == 'LONG' else 'BEARISH',
                    occurred_at_ms=retest_time,
                    known_at_ms=retest_time + 5 * 60 * 1000,
                    source_ids=(fvg_node.id,),
                    level=retest_price,
                    source_candle_open_ms=retest_time,
                )

                chain_check = validate_reversal_chain(
                    symbol=symbol,
                    direction=direction,
                    as_of_ms=now_ms,
                    sweep=sweep_node,
                    brk=break_node,
                    fvg=fvg_node,
                    retest=retest_node,
                    config=config,
                    htf_context={'h4': h4_regime, 'h1': h1_regime},
                )

                if chain_check.verified:
                    target_lvl = target.price if target else (
                        (retest_price + 20.0) if direction == 'LONG' else (retest_price - 20.0)
                    )
                    sweep_ext = sweep_node.level if sweep_node.level else retest_price - 5.0
                    plan = build_scenario_plan(
                        symbol=symbol,
                        direction=direction,
                        retest_price=retest_price,
                        sweep_extreme=sweep_ext,
                        target_level=target_lvl,
                        config=config,
                        invalidation_price=sweep_ext,
                        now_ms=now_ms,
                    )
                else:
                    state = ScenarioState.BLOCKED

        evidence_nodes = tuple(n for n in (sweep_node, break_node, fvg_node, retest_node) if n is not None)
        evidence_ids = tuple(n.id for n in evidence_nodes)

        anchor_level_val = anchor.price if anchor else (sweep_node.level if sweep_node else None)
        anchor_name = anchor.name if anchor else (
            f"Swing {'Đáy' if direction == 'LONG' else 'Đỉnh'}" if sweep_node else None
        )

        target_price = plan.take_profit if (plan and plan.is_valid) else (target.price if target else None)
        invalidation_p = plan.invalidation_price if (plan and plan.is_valid) else (
            sweep_node.level if sweep_node else None
        )

        summary_vi = build_scenario_summary_vi(
            symbol=symbol,
            direction=direction,
            state=state.value,
            h4_regime=h4_regime,
            h1_regime=h1_regime,
            anchor_desc=anchor_name,
            anchor_price=anchor_level_val,
            sweep_desc=sweep_node.kind if sweep_node else None,
            break_desc=break_node.kind if break_node else None,
            fvg_desc=fvg_node.kind if fvg_node else None,
            retest_desc=retest_node.kind if retest_node else None,
            target_desc=target.name if target else None,
            target_price=target_price,
            invalidation_price=invalidation_p,
            blockers=list(chain_check.reasons) if (chain_check and not chain_check.verified) else None,
            timeframe='5M',
        )

        scenario_id, instance_id = make_scenario_identity(
            symbol=symbol,
            session_or_date=session_instance_id or datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%Y-%m-%d'),
            family='STANDARD_SMC',
            direction=direction,
            anchor_id=anchor.level_id if anchor else (sweep_node.id if sweep_node else 'unanchored'),
        )

        snapshot = ScenarioSnapshot(
            scenario_id=scenario_id,
            instance_id=instance_id,
            revision=1,
            symbol=symbol,
            setup_timeframe='5M',
            context_timeframes=('D', '4H', '1H', '15M'),
            strategy_family='STANDARD_SMC',
            direction=direction,
            state=state,
            as_of_ms=now_ms,
            evidence_cutoff_ms=now_ms,
            evidence_ids=evidence_ids,
            evidence_nodes=evidence_nodes,
            liquidity_anchor=anchor,
            target_anchor=target,
            chain_check=chain_check,
            plan=plan,
            invalidation_price=invalidation_p,
            summary_vi=summary_vi,
            config_version=config.detector_version,
            detector_version=config.detector_version,
            blockers=chain_check.reasons if (chain_check and not chain_check.verified) else (),
        )
        results.append(snapshot)

    return results

def evaluate_scenario_cycle(
    db: Session,
    symbol: str,
    candles_by_tf: Dict[str, List[Any]],
    quote: Optional[Dict[str, Any]],
    now_ms: int,
    config: Optional[ScenarioConfig] = None,
    session_instance_id: Optional[str] = None,
) -> List[ScenarioSnapshot]:
    """
    Production cycle wrapper: loads context, evaluates scenarios, persists revisions,
    and returns the evaluated snapshots.
    """
    active_cfg = config or active_scenario_config
    closed_ctx = load_closed_context(candles_by_tf, now_ms=now_ms, config=active_cfg)

    # Evaluate scenarios
    snapshots = evaluate_scenarios(
        symbol=symbol,
        context=closed_ctx,
        previous_scenarios=[],
        config=active_cfg,
        now_ms=now_ms,
        quote=quote,
        session_instance_id=session_instance_id,
    )

    persisted_snapshots = []
    for s in snapshots:
        # Upsert into repository
        scenario_row, snap_row, _ = upsert_scenario_revision(
            db=db,
            snapshot=s,
            is_material_change=(s.state in (ScenarioState.STRUCTURE_CONFIRMED, ScenarioState.WAITING_RETEST, ScenarioState.BLOCKED)),
        )
        persisted_snapshots.append(s)

    return persisted_snapshots
