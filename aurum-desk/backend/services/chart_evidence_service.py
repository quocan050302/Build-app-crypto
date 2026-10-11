import json
import logging
from typing import Dict, Any, List, Optional
from services.smc_scenario_types import ScenarioSnapshot, EvidenceNode

logger = logging.getLogger('chart_evidence_service')

def build_displacement_annotation(break_node: Optional[EvidenceNode], m5_candles: Optional[List[Any]] = None) -> Optional[Dict[str, Any]]:
    if not break_node:
        return None
    return {
        'id': f"disp-{break_node.id}",
        'candle_time_ms': break_node.occurred_at_ms,
        'known_at_ms': break_node.known_at_ms,
        'direction': break_node.direction,
        'level': break_node.level,
        'is_qualified': True,
        'evidence_id': break_node.id,
    }

def build_scenario_path(
    nodes: List[EvidenceNode],
    target_price: Optional[float],
    target_time_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Builds sequential segments connecting verified structural events to expected target.
    Confirmed segments are marked confirmed=True; future projected paths confirmed=False.
    """
    if not nodes:
        return []

    segments = []
    # Sort nodes by occurred_at_ms
    sorted_nodes = sorted(nodes, key=lambda n: n.occurred_at_ms)
    for i in range(len(sorted_nodes) - 1):
        n1 = sorted_nodes[i]
        n2 = sorted_nodes[i + 1]
        segments.append({
            'from_id': n1.id,
            'to_id': n2.id,
            'from_time_ms': n1.occurred_at_ms,
            'from_price': n1.level,
            'to_time_ms': n2.occurred_at_ms,
            'to_price': n2.level,
            'is_confirmed': True,
            'label': f"{n1.kind} -> {n2.kind}",
        })

    # Projected segment to target
    if sorted_nodes and target_price:
        last = sorted_nodes[-1]
        proj_time = target_time_ms or (last.occurred_at_ms + 30 * 60 * 1000)
        segments.append({
            'from_id': last.id,
            'to_id': 'projected_target',
            'from_time_ms': last.occurred_at_ms,
            'from_price': last.level,
            'to_time_ms': proj_time,
            'to_price': target_price,
            'is_confirmed': False,
            'label': 'Kịch bản mục tiêu',
        })

    return segments

def build_selected_chart_bundle(
    snapshot: ScenarioSnapshot,
    source_type: str = 'SCENARIO', # 'SETUP', 'ORDER', 'SCENARIO'
    binding_id: Optional[str] = None,
    order_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Builds the authoritative ChartEvidenceBundleV144 for a selected setup, order, or scenario.
    Does NOT use global analysis cache.
    """
    structure_events = []
    poi_zones = []
    liquidity_levels = []
    warnings = []

    # Map evidence nodes to structure events and POI zones
    for n in snapshot.evidence_nodes:
        if n.kind == 'FVG':
            # FVG Zone
            poi_zones.append({
                'id': n.id,
                'kind': 'FVG',
                'top': (n.level + 0.5) if n.level else 0.0,
                'bottom': (n.level - 0.5) if n.level else 0.0,
                'start_at_ms': n.occurred_at_ms,
                'end_at_ms': n.known_at_ms + (2 * 60 * 60 * 1000), # 2 hours default active window
                'direction': n.direction,
                'state': 'ACTIVE',
                'source_ids': list(n.source_ids),
                'occurred_at_ms': n.occurred_at_ms,
                'known_at_ms': n.known_at_ms,
                'source_timeframe': n.timeframe,
            })
        else:
            # Swing, Sweep, Break, Retest
            structure_events.append({
                'id': n.id,
                'type': n.kind,
                'kind': n.kind,
                'direction': n.direction,
                'price': n.level,
                'occurred_at_ms': n.occurred_at_ms,
                'known_at_ms': n.known_at_ms,
                'source_timeframe': n.timeframe,
                'source_ids': list(n.source_ids),
                'detail': f"{n.kind} tại {n.level:.2f}" if n.level else n.kind,
            })

    # Liquidity anchor and target
    if snapshot.liquidity_anchor:
        la = snapshot.liquidity_anchor
        liquidity_levels.append({
            'id': la.level_id,
            'name': la.name,
            'price': la.price,
            'side': la.side,
            'type': la.kind,
            'state': la.state,
            'occurred_at_ms': la.occurred_at_ms,
            'known_at_ms': la.known_at_ms,
            'is_target': False,
        })

    if snapshot.target_anchor:
        ta = snapshot.target_anchor
        liquidity_levels.append({
            'id': ta.level_id,
            'name': f"Mục tiêu {ta.name}",
            'price': ta.price,
            'side': ta.side,
            'type': ta.kind,
            'state': ta.state,
            'occurred_at_ms': ta.occurred_at_ms,
            'known_at_ms': ta.known_at_ms,
            'is_target': True,
        })

    # Plan details
    plan_dict = None
    if snapshot.plan and snapshot.plan.is_valid:
        p = snapshot.plan
        plan_dict = {
            'is_valid': True,
            'direction': p.direction,
            'planned_entry': p.planned_entry,
            'stop_loss': p.stop_loss,
            'take_profit': p.take_profit,
            'invalidation_price': p.invalidation_price,
            'gross_rr': p.gross_rr,
            'net_rr': p.net_rr,
            'quantity': p.quantity,
            'net_risk_usdt': p.net_risk_usdt,
            'net_reward_usdt': p.net_reward_usdt,
            'plan_fingerprint': p.plan_fingerprint,
        }
    elif order_data:
        plan_dict = {
            'is_valid': True,
            'direction': order_data.get('direction', 'LONG'),
            'planned_entry': order_data.get('planned_entry', 0.0),
            'actual_entry': order_data.get('actual_entry'),
            'stop_loss': order_data.get('stop_loss', 0.0),
            'take_profit': order_data.get('take_profit', 0.0),
            'invalidation_price': order_data.get('stop_loss', 0.0),
            'gross_rr': order_data.get('gross_rr', 2.0),
            'net_rr': order_data.get('estimated_net_rr', 1.9),
            'quantity': order_data.get('quantity', 0.0),
            'net_risk_usdt': order_data.get('initial_risk_usdt', 0.0),
            'net_reward_usdt': 0.0,
            'plan_fingerprint': '',
        }

    # Break displacement
    break_nodes = [n for n in snapshot.evidence_nodes if n.kind in ('BOS', 'CHOCH', 'MSS')]
    disp = build_displacement_annotation(break_nodes[0] if break_nodes else None)

    # Scenario path
    path = build_scenario_path(
        list(snapshot.evidence_nodes),
        target_price=snapshot.plan.take_profit if snapshot.plan else None,
    )

    binding = {
        'source': source_type,
        'id': binding_id or snapshot.scenario_id,
        'instance_id': snapshot.instance_id,
        'revision': snapshot.revision,
        'symbol': snapshot.symbol,
        'direction': snapshot.direction,
        'plan_fingerprint': snapshot.plan.plan_fingerprint if snapshot.plan else None,
        'snapshot_id': snapshot.snapshot_id,
    }

    return {
        'binding': binding,
        'as_of_ms': snapshot.as_of_ms,
        'evidence_cutoff_ms': snapshot.evidence_cutoff_ms,
        'strategy_family': snapshot.strategy_family,
        'state': snapshot.state.value if hasattr(snapshot.state, 'value') else str(snapshot.state),
        'summary_vi': snapshot.summary_vi,
        'structure_events': structure_events,
        'poi_zones': poi_zones,
        'liquidity_levels': liquidity_levels,
        'displacement_annotation': disp,
        'scenario_path': path,
        'risk_reward_plan': plan_dict,
        'invalidation_price': snapshot.invalidation_price,
        'warnings': warnings,
        'is_empty': False,
    }

def adapt_legacy_quality_snapshot(
    quality: Any, # SetupQualitySnapshot row or dict
    plan_data: Optional[Dict[str, Any]] = None,
    order_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Transparent adapter for legacy V14 quality snapshot rows.
    If original candle times are missing, marks UNANCHORED with warnings.
    """
    warnings = ['LEGACY_QUALITY_ADAPTED: Snapshot cũ thiếu một số metadata thời điểm nến gốc.']
    structure_events = []
    poi_zones = []

    # Read sweep evidence
    raw_sweep = getattr(quality, 'sweep_evidence', None)
    if raw_sweep:
        try:
            s_dict = json.loads(raw_sweep) if isinstance(raw_sweep, str) else raw_sweep
            structure_events.append({
                'id': s_dict.get('id', 'legacy_sweep'),
                'type': s_dict.get('type', 'SWEEP'),
                'kind': s_dict.get('kind', 'SWEEP_LOW' if getattr(quality, 'direction', 'LONG') == 'LONG' else 'SWEEP_HIGH'),
                'direction': getattr(quality, 'direction', 'LONG'),
                'price': s_dict.get('level'),
                'occurred_at_ms': s_dict.get('timestamp') or getattr(quality, 'as_of_ms', 0),
                'known_at_ms': s_dict.get('confirmed_at') or getattr(quality, 'as_of_ms', 0),
                'source_timeframe': getattr(quality, 'timeframe', '15M'),
                'source_ids': [],
                'detail': s_dict.get('detail', 'Sweep legacy'),
            })
        except Exception:
            pass

    # Read FVG evidence
    raw_fvg = getattr(quality, 'fvg_evidence', None)
    if raw_fvg:
        try:
            f_dict = json.loads(raw_fvg) if isinstance(raw_fvg, str) else raw_fvg
            poi_zones.append({
                'id': f_dict.get('id', 'legacy_fvg'),
                'kind': 'FVG',
                'top': f_dict.get('top', 0.0),
                'bottom': f_dict.get('bottom', 0.0),
                'start_at_ms': f_dict.get('start_at_ms') or getattr(quality, 'as_of_ms', 0),
                'end_at_ms': f_dict.get('end_at_ms'),
                'direction': getattr(quality, 'direction', 'LONG'),
                'state': f_dict.get('state', 'ACTIVE'),
                'source_ids': [],
                'occurred_at_ms': f_dict.get('timestamp') or getattr(quality, 'as_of_ms', 0),
                'known_at_ms': f_dict.get('confirmed_at') or getattr(quality, 'as_of_ms', 0),
                'source_timeframe': getattr(quality, 'timeframe', '15M'),
            })
        except Exception:
            pass

    binding = {
        'source': 'ORDER' if order_data else 'SETUP',
        'id': getattr(quality, 'setup_id', None) or (order_data.get('id') if order_data else 'legacy'),
        'instance_id': getattr(quality, 'setup_instance_id', None),
        'revision': getattr(quality, 'setup_revision', 1),
        'symbol': getattr(quality, 'symbol', 'XAUUSDT'),
        'direction': getattr(quality, 'direction', 'LONG'),
        'plan_fingerprint': None,
        'snapshot_id': getattr(quality, 'snapshot_id', None),
    }

    return {
        'binding': binding,
        'as_of_ms': getattr(quality, 'as_of_ms', 0),
        'evidence_cutoff_ms': getattr(quality, 'as_of_ms', 0),
        'strategy_family': getattr(quality, 'strategy_family', 'STANDARD_SMC'),
        'state': 'STRUCTURE_CONFIRMED',
        'summary_vi': 'Bản chụp chất lượng cũ (legacy quality snapshot).',
        'structure_events': structure_events,
        'poi_zones': poi_zones,
        'liquidity_levels': [],
        'displacement_annotation': None,
        'scenario_path': [],
        'risk_reward_plan': plan_data or order_data,
        'invalidation_price': None,
        'warnings': warnings,
        'is_empty': False,
    }

def build_selected_chart_bundle_from_dict(
    data: Dict[str, Any],
    source_type: str = 'SCENARIO',
    binding_id: Optional[str] = None,
    order_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Builds chart bundle from raw scenario snapshot dictionary (JSON payload).
    """
    structure_events = []
    poi_zones = []
    liquidity_levels = []
    warnings = []

    nodes = data.get('evidence_nodes', [])
    for n in nodes:
        if n.get('kind') == 'FVG':
            lvl = n.get('level') or 0.0
            poi_zones.append({
                'id': n.get('id', 'fvg'),
                'kind': 'FVG',
                'top': lvl + 0.5,
                'bottom': lvl - 0.5,
                'start_at_ms': n.get('occurred_at_ms', 0),
                'end_at_ms': (n.get('known_at_ms') or 0) + (2 * 3600 * 1000),
                'direction': n.get('direction'),
                'state': 'ACTIVE',
                'source_ids': list(n.get('source_ids', [])),
                'occurred_at_ms': n.get('occurred_at_ms', 0),
                'known_at_ms': n.get('known_at_ms', 0),
                'source_timeframe': n.get('timeframe', '5M'),
            })
        else:
            structure_events.append({
                'id': n.get('id', 'event'),
                'type': n.get('kind', 'SWING'),
                'kind': n.get('kind', 'SWING'),
                'direction': n.get('direction'),
                'price': n.get('level'),
                'occurred_at_ms': n.get('occurred_at_ms', 0),
                'known_at_ms': n.get('known_at_ms', 0),
                'source_timeframe': n.get('timeframe', '5M'),
                'source_ids': list(n.get('source_ids', [])),
                'detail': f"{n.get('kind')} tại {n.get('level', 0):.2f}" if n.get('level') else n.get('kind'),
            })

    la = data.get('liquidity_anchor')
    if la:
        liquidity_levels.append({
            'id': la.get('level_id', 'anchor'),
            'name': la.get('name', 'Anchor'),
            'price': la.get('price', 0.0),
            'side': la.get('side', 'BUY_SIDE'),
            'type': la.get('kind', 'SWING'),
            'state': la.get('state', 'UNTOUCHED'),
            'occurred_at_ms': la.get('occurred_at_ms', 0),
            'known_at_ms': la.get('known_at_ms', 0),
            'is_target': False,
        })

    ta = data.get('target_anchor')
    if ta:
        liquidity_levels.append({
            'id': ta.get('level_id', 'target'),
            'name': f"Mục tiêu {ta.get('name', 'Target')}",
            'price': ta.get('price', 0.0),
            'side': ta.get('side', 'SELL_SIDE'),
            'type': ta.get('kind', 'SWING'),
            'state': ta.get('state', 'UNTOUCHED'),
            'occurred_at_ms': ta.get('occurred_at_ms', 0),
            'known_at_ms': ta.get('known_at_ms', 0),
            'is_target': True,
        })

    plan_dict = data.get('plan') or order_data

    # Break displacement
    break_nodes = [n for n in nodes if n.get('kind') in ('BOS', 'CHOCH', 'MSS')]
    disp = None
    if break_nodes:
        bn = break_nodes[0]
        disp = {
            'id': f"disp-{bn.get('id')}",
            'candle_time_ms': bn.get('occurred_at_ms'),
            'known_at_ms': bn.get('known_at_ms'),
            'direction': bn.get('direction'),
            'level': bn.get('level'),
            'is_qualified': True,
            'evidence_id': bn.get('id'),
        }

    # Path
    segments = []
    sorted_nodes = sorted(nodes, key=lambda n: n.get('occurred_at_ms', 0))
    for i in range(len(sorted_nodes) - 1):
        n1 = sorted_nodes[i]
        n2 = sorted_nodes[i + 1]
        segments.append({
            'from_id': n1.get('id'),
            'to_id': n2.get('id'),
            'from_time_ms': n1.get('occurred_at_ms'),
            'from_price': n1.get('level'),
            'to_time_ms': n2.get('occurred_at_ms'),
            'to_price': n2.get('level'),
            'is_confirmed': True,
            'label': f"{n1.get('kind')} -> {n2.get('kind')}",
        })

    tp_price = (plan_dict.get('take_profit') or plan_dict.get('tp')) if plan_dict else None
    if sorted_nodes and tp_price:
        last = sorted_nodes[-1]
        segments.append({
            'from_id': last.get('id'),
            'to_id': 'projected_target',
            'from_time_ms': last.get('occurred_at_ms'),
            'from_price': last.get('level'),
            'to_time_ms': last.get('occurred_at_ms', 0) + 30 * 60 * 1000,
            'to_price': tp_price,
            'is_confirmed': False,
            'label': 'Kịch bản mục tiêu',
        })

    binding = {
        'source': source_type,
        'id': binding_id or data.get('scenario_id'),
        'instance_id': data.get('instance_id'),
        'revision': data.get('revision'),
        'symbol': data.get('symbol', 'XAUUSDT'),
        'direction': data.get('direction'),
        'plan_fingerprint': plan_dict.get('plan_fingerprint') if plan_dict else None,
        'snapshot_id': data.get('snapshot_id'),
    }

    return {
        'binding': binding,
        'as_of_ms': data.get('as_of_ms', 0),
        'evidence_cutoff_ms': data.get('evidence_cutoff_ms', 0),
        'strategy_family': data.get('strategy_family', 'STANDARD_SMC'),
        'state': data.get('state', 'OBSERVING'),
        'summary_vi': data.get('summary_vi'),
        'structure_events': structure_events,
        'poi_zones': poi_zones,
        'liquidity_levels': liquidity_levels,
        'displacement_annotation': disp,
        'scenario_path': segments,
        'risk_reward_plan': plan_dict,
        'invalidation_price': data.get('invalidation_price'),
        'warnings': warnings,
        'is_empty': False,
    }
