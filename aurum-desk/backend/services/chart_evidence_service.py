"""
backend/services/chart_evidence_service.py
V14.4.1 - Chart evidence bundle builder on the CANONICAL ScenarioSnapshot shape.

Every geometric fact comes from the stored evidence (FVG top/bottom + creator candle refs, sweep wick extreme,
break broken-swing anchor, displacement qualification, retest zone/touch). Nothing is invented:
  * no FVG midpoint +/- constant, no "+2h" zone end, no "+30min" target path;
  * displacement `is_qualified` is the MEASURED result stored with the break, never a hardcoded True;
  * a missing anchor is reported as a warning (the DTO field stays None) instead of a default.
The bundle shape is identical for SCENARIO / SETUP / ORDER / DRAFT selections (binding.source tells which).
"""
import json
import logging
import math
from typing import Any, Dict, List, Optional

from services.smc_scenario_config import ScenarioConfig, describe_model_requirements
from services.smc_scenario_types import EvidenceNode, ScenarioSnapshot

logger = logging.getLogger('chart_evidence_service')

BREAK_KINDS = ('MSS', 'CHOCH', 'BOS', 'BREAK_UNCLASSIFIED')
SWEEP_KINDS = ('SWEEP_HIGH', 'SWEEP_LOW')
SWING_KINDS = ('SWING_HIGH', 'SWING_LOW')
BUNDLE_VERSION = 'v14.4.1'


def read_snapshot_provenance(snapshot_dict: dict, current_config: ScenarioConfig) -> dict:
    """
    Extracts provenance information and compares the snapshot's config version,
    detector version, and requirement profile with the current active config.
    Returns canonical SnapshotProvenanceDTO fields with backwards-compatibility aliases.
    """
    snap_config_version = snapshot_dict.get('config_version')
    detector_version = snapshot_dict.get('detector_version')
    req_profile = snapshot_dict.get('requirement_profile')

    curr_config_v = current_config.config_version
    curr_detector_v = current_config.detector_version
    curr_profile = getattr(current_config, 'requirement_profile', 'DEFAULT') or 'DEFAULT'

    current_reqs = describe_model_requirements(current_config)

    # Missing legacy metadata -> unknown provenance if ANY required field is missing
    if snap_config_version is None or detector_version is None or req_profile is None:
        return {
            'is_current': None,
            'snapshot_detector_version': detector_version,
            'current_detector_version': curr_detector_v,
            'snapshot_config_version': snap_config_version,
            'current_config_version': curr_config_v,
            'snapshot_requirement_profile': req_profile,
            'current_requirement_profile': curr_profile,
            'historical_requirements': snapshot_dict.get('requirements'),
            'current_requirements': current_reqs,
            'version_label': "Chưa đủ thông tin phiên bản",
            'warning': "Chưa đủ thông tin phiên bản lúc vào lệnh",
            # Aliases
            'detector_version': detector_version,
            'requirement_profile': req_profile or 'DEFAULT',
            'is_outdated_version': False,
            'is_custom_profile': False,
        }

    # Normalize profile aliases: DEFAULT and V14.4_SMC_BASELINE are equivalent baseline rules
    def _normalize_profile(p: Optional[str]) -> str:
        if p in ("DEFAULT", "V14.4_SMC_BASELINE", None):
            return "DEFAULT"
        return p

    snap_norm = _normalize_profile(req_profile)
    curr_norm = _normalize_profile(curr_profile)

    version_diff = (snap_config_version != curr_config_v)
    detector_diff = bool(detector_version and detector_version != curr_detector_v)
    profile_diff = (snap_norm != curr_norm)

    is_outdated = version_diff or detector_diff or profile_diff
    is_current = not is_outdated

    historical_reqs = snapshot_dict.get('requirements') or {
        'config_version': snap_config_version,
        'detector_version': detector_version,
        'requirement_profile': req_profile or 'DEFAULT',
    }

    if is_current:
        version_label = "Phiên bản hiện tại"
        warning = None
    else:
        version_label = "Phiên bản lúc vào lệnh"
        warning = "Luật SMC đã thay đổi từ lúc ghi nhận snapshot"

    return {
        'is_current': is_current,
        'snapshot_detector_version': detector_version,
        'current_detector_version': curr_detector_v,
        'snapshot_config_version': snap_config_version,
        'current_config_version': curr_config_v,
        'snapshot_requirement_profile': req_profile or 'DEFAULT',
        'current_requirement_profile': curr_profile,
        'historical_requirements': historical_reqs,
        'current_requirements': current_reqs,
        'version_label': version_label,
        'warning': warning,
        # Aliases
        'detector_version': detector_version,
        'requirement_profile': req_profile or 'DEFAULT',
        'is_outdated_version': is_outdated,
        'is_custom_profile': (req_profile not in ("DEFAULT", "V14.4_SMC_BASELINE", None)),
    }


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _node_dict(n: Any) -> Dict[str, Any]:
    if isinstance(n, EvidenceNode):
        return n.to_dict()
    if isinstance(n, dict):
        return dict(n)
    if isinstance(n, str):
        return {'id': n, 'kind': 'UNKNOWN', 'occurred_at_ms': None, 'known_at_ms': None}
    return {}


def build_displacement_annotation(break_node: Any, m5_candles: Optional[List[Any]] = None) -> Optional[Dict[str, Any]]:
    """Annotation of the break candle with its MEASURED qualification. No qualification stored -> None."""
    if not break_node:
        return None
    n = _node_dict(break_node)
    disp = (n.get('extra') or {}).get('displacement')
    if not isinstance(disp, dict) or 'is_qualified' not in disp:
        return None
    return {
        'id': f"disp-{n['id']}",
        'candle_time_ms': n.get('occurred_at_ms'),
        'known_at_ms': n.get('known_at_ms'),
        'direction': n.get('direction'),
        'level': n.get('level'),
        'is_qualified': disp.get('is_qualified') is True,
        'close_beyond': disp.get('close_beyond'),
        'body_atr_ratio': disp.get('body_atr_ratio', disp.get('ratio')),
        'prefix_atr': (n.get('extra') or {}).get('prefix_atr'),
        'reason': disp.get('reason'),
        'evidence_id': n['id'],
    }


def build_scenario_path(nodes: List[Any], target_price: Optional[float] = None,
                        target_time_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    """Segments between REAL chain nodes (sorted by occurrence). The projection to the target is a logical
    display hint (`is_projection`, no timestamp): it never claims price arrives at a future time."""
    chain = []
    for n in nodes:
        d = _node_dict(n)
        if not d or not d.get('id'):
            continue
        if d.get('kind') in SWING_KINDS + ('POI', 'UNKNOWN'):
            continue
        if d.get('occurred_at_ms') is not None and _finite(d.get('occurred_at_ms')):
            chain.append(d)

    chain.sort(key=lambda n: n['occurred_at_ms'])
    segs: List[Dict[str, Any]] = []
    for a, b in zip(chain, chain[1:]):
        pa = a.get('level') if _finite(a.get('level')) else None
        pb = b.get('level') if _finite(b.get('level')) else None
        segs.append({'from_id': a['id'], 'to_id': b['id'], 'from_time_ms': a['occurred_at_ms'], 'from_price': pa,
                     'to_time_ms': b['occurred_at_ms'], 'to_price': pb, 'is_confirmed': True,
                     'is_projection': False, 'label': f"{a.get('kind')} -> {b.get('kind')}"})
    if chain and _finite(target_price):
        last = chain[-1]
        segs.append({'from_id': last['id'], 'to_id': 'projected_target', 'from_time_ms': last['occurred_at_ms'],
                     'from_price': last.get('level') if _finite(last.get('level')) else None,
                     'to_time_ms': target_time_ms, 'to_price': float(target_price), 'is_confirmed': False,
                     'is_projection': True, 'label': 'Kịch bản nếu xác nhận'})
    return segs


def _zone_from_node(n: Dict[str, Any], warnings: List[str], as_of_ms: int) -> Optional[Dict[str, Any]]:
    ex = n.get('extra') or {}
    top, bottom = ex.get('top'), ex.get('bottom')
    if not (_finite(top) and _finite(bottom)) or bottom >= top:
        warnings.append(f"MISSING_FVG_GEOMETRY:{n.get('id')}")   # UNANCHORED: no default zone is drawn
        return None
    state = str(ex.get('state') or 'UNKNOWN')
    mitigated = bool(ex.get('mitigated'))
    return {
        'id': n['id'], 'kind': 'FVG' if n.get('kind') == 'FVG' else n.get('kind'),
        'top': float(top), 'bottom': float(bottom),
        'start_at_ms': ex.get('first_candle_open_ms') or n.get('occurred_at_ms'),
        'middle_candle_open_ms': ex.get('middle_candle_open_ms'),
        'third_candle_open_ms': ex.get('third_candle_open_ms'),
        'confirmed_at_ms': ex.get('confirmed_at_ms') or n.get('known_at_ms'),
        'end_at_ms': None,                      # real end (mitigation time) is not stored => never invented
        'display_end_ms': as_of_ms,            # display-only horizon, labelled separately from facts
        'direction': n.get('direction'), 'state': 'MITIGATED' if mitigated else state,
        'is_active': (not mitigated) and state not in ('MITIGATED', 'INVALIDATED'),
        'source_ids': list(n.get('source_ids') or []),
        'occurred_at_ms': n.get('occurred_at_ms'), 'known_at_ms': n.get('known_at_ms'),
        'source_timeframe': n.get('timeframe'),
        'poi_kind': ex.get('poi_kind'),
    }


def build_selected_chart_bundle_from_dict(
    data: Dict[str, Any], source_type: str = 'SCENARIO', binding_id: Optional[str] = None,
    order_data: Optional[Dict[str, Any]] = None, include_all_levels: bool = False,
) -> Dict[str, Any]:
    """Canonical snapshot dict (ScenarioSnapshot.to_dict) -> selected chart bundle."""
    warnings: List[str] = []
    nodes: List[Dict[str, Any]] = [_node_dict(n) for n in (data.get('evidence') or []) if _node_dict(n).get('id')]
    as_of_ms = int(data.get('as_of_ms') or 0)
    by_id = {n['id']: n for n in nodes if 'id' in n}

    swing_roles: Dict[str, str] = {}
    for n in nodes:
        if n.get('kind') in BREAK_KINDS:
            bid = (n.get('extra') or {}).get('broken_swing_id')
            if bid:
                swing_roles[bid] = 'BROKEN'
        if n.get('kind') in SWEEP_KINDS:
            sid = (n.get('extra') or {}).get('source_level_id')
            if sid:
                swing_roles[sid] = 'SWEPT'

    swings, structure_events, poi_zones, retests, displacements = [], [], [], [], []
    for n in nodes:
        k = n.get('kind')
        ex = n.get('extra') or {}
        occ = n.get('occurred_at_ms') or as_of_ms
        known = n.get('known_at_ms') or occ
        if k in SWING_KINDS:
            if not _finite(n.get('level')):
                warnings.append(f"SWING_WITHOUT_PRICE:{n.get('id')}")
                continue
            swings.append({
                'id': n['id'], 'kind': k, 'price': n['level'], 'label': ex.get('label'),
                'role': swing_roles.get(n['id'], 'RELATED'),
                'occurred_at_ms': occ, 'known_at_ms': known,     # pivot vs confirmation
                'source_timeframe': n.get('timeframe'),
            })
        elif k in ('FVG', 'POI'):
            z = _zone_from_node(n, warnings, as_of_ms)
            if z:
                poi_zones.append(z)
        elif k == 'RETEST':
            r = {'id': n['id'], 'type': k, 'kind': k, 'direction': n.get('direction'), 'price': ex.get('touch_price'),
                 'zone_top': ex.get('zone_top'), 'zone_bottom': ex.get('zone_bottom'), 'location': ex.get('source'),
                 'occurred_at_ms': occ, 'known_at_ms': known,
                 'source_timeframe': n.get('timeframe'), 'source_ids': list(n.get('source_ids') or [])}
            retests.append(r)
            structure_events.append({**r, 'detail': f"Retest FVG tại {ex.get('touch_price')}"})
        elif k in SWEEP_KINDS:
            structure_events.append({
                'id': n['id'], 'type': k, 'kind': k, 'direction': n.get('direction'), 'price': n.get('level'),
                'wick_extreme': ex.get('wick_extreme'), 'close_back': ex.get('close_back'),
                'closed_back': ex.get('closed_back'), 'level_label': ex.get('level_label'),
                'source_level_id': ex.get('source_level_id'),
                'occurred_at_ms': occ, 'known_at_ms': known,
                'source_timeframe': n.get('timeframe'), 'source_ids': list(n.get('source_ids') or []),
                'detail': ex.get('level_label') or f"Quét thanh khoản tại {n.get('level')}"})
        elif k in BREAK_KINDS:
            anchor = by_id.get(ex.get('broken_swing_id'))
            ev = {
                'id': n['id'], 'type': k, 'kind': k, 'direction': n.get('direction'), 'price': n.get('level'),
                'occurred_at_ms': occ, 'known_at_ms': known,
                'source_timeframe': n.get('timeframe'), 'source_ids': list(n.get('source_ids') or []),
                'broken_swing_id': ex.get('broken_swing_id'),
                'start_at_ms': anchor.get('occurred_at_ms') if anchor else None,     # break line starts at the broken swing
                'start_price': anchor.get('level') if anchor else None,
                'detail': f"{k} tại {n.get('level')}"}
            if anchor is None:
                warnings.append(f"BREAK_ANCHOR_MISSING:{n['id']}")
            structure_events.append(ev)
            d = build_displacement_annotation(n)
            if d:
                displacements.append(d)
            else:
                warnings.append(f"DISPLACEMENT_NOT_MEASURED:{n['id']}")
        else:
            structure_events.append({'id': n['id'], 'type': k, 'kind': k, 'direction': n.get('direction'),
                                     'price': n.get('level'), 'occurred_at_ms': occ,
                                     'known_at_ms': known, 'source_timeframe': n.get('timeframe'),
                                     'source_ids': list(n.get('source_ids') or []), 'detail': str(k)})

    # --- liquidity: swept level + anchor + target + (optionally) the whole stored map
    lmap = data.get('liquidity_map') or {}
    target = data.get('target') or lmap.get('target')
    liquidity_levels: List[Dict[str, Any]] = []
    seen = set()

    def _add_level(l: Dict[str, Any], *, is_target=False, is_swept=False, is_anchor=False):
        if not isinstance(l, dict) or not _finite(l.get('price')) or l.get('id') in seen:
            return
        seen.add(l.get('id'))
        liquidity_levels.append({
            'id': l.get('id'), 'name': l.get('label_vi') or l.get('id'), 'price': l['price'],
            'side': l.get('side'), 'type': l.get('source_kind'), 'state': l.get('state', 'UNTOUCHED'),
            'occurred_at_ms': l.get('occurred_at_ms'), 'known_at_ms': l.get('known_at_ms'),
            'is_partial': bool(l.get('is_partial')), 'is_target': is_target, 'is_swept': is_swept,
            'is_anchor': is_anchor, 'is_consumed': l.get('state') in ('SWEPT', 'BROKEN', 'CONSUMED'),
        })

    all_levels = {l.get('id'): l for l in (lmap.get('levels') or []) if isinstance(l, dict)}
    swept = lmap.get('swept')
    if isinstance(swept, dict) and swept.get('id') in all_levels:
        _add_level(all_levels[swept['id']], is_swept=True)
    elif isinstance(swept, dict) and _finite(swept.get('price')):
        _add_level({**swept, 'source_kind': 'SWEPT'}, is_swept=True)
    if isinstance(target, dict):
        t = all_levels.get(target.get('id'), target)
        _add_level({**t, 'price': target.get('price', t.get('price'))}, is_target=True)
    if isinstance(lmap.get('anchor'), dict):
        _add_level(lmap['anchor'], is_anchor=True)
    if include_all_levels:
        for l in all_levels.values():
            _add_level(l)

    # --- plan / actual order geometry
    plan = data.get('plan') if isinstance(data.get('plan'), dict) else None
    plan_dict: Optional[Dict[str, Any]] = None
    entry_ms = None
    if retests:
        entry_ms = max(r['known_at_ms'] for r in retests)
    if order_data:
        plan_dict = dict(order_data)
        plan_dict.setdefault('is_valid', True)
        if plan_dict.get('entry_time_ms') is None and plan_dict.get('entry_time') is not None:
            plan_dict['entry_time_ms'] = int(plan_dict['entry_time']) * 1000
    elif plan and plan.get('is_valid'):
        plan_dict = {k: plan.get(k) for k in (
            'is_valid', 'direction', 'planned_entry', 'stop_loss', 'take_profit', 'invalidation_price', 'gross_rr',
            'net_rr', 'quantity', 'net_risk_usdt', 'net_reward_usdt', 'plan_fingerprint', 'entry_basis', 'can_execute')}
        plan_dict['entry_time_ms'] = entry_ms
        plan_dict['entry_time'] = (entry_ms // 1000) if entry_ms else None
        plan_dict['financial'] = plan.get('financial')
    elif plan and not plan.get('is_valid'):
        warnings.append(f"PLAN_INVALID:{plan.get('invalid_reason')}")

    inv = data.get('invalidation')
    inv_price = inv.get('price') if isinstance(inv, dict) else None
    if order_data and _finite(order_data.get('stop_loss')):
        inv_price = order_data.get('stop_loss')           # an open order keeps ITS stop, not the scenario's

    tgt_price = (plan_dict or {}).get('take_profit') if plan_dict else (target.get('price') if isinstance(target, dict) else None)
    path = build_scenario_path(nodes, tgt_price if data.get('state') != 'INVALIDATED' else None)

    prob = data.get('probability') or {'status': 'UNAVAILABLE', 'p_tp_before_sl': None, 'support_n': None}
    binding = {
        'source': source_type,
        'id': binding_id or data.get('scenario_id'),
        'instance_id': data.get('instance_id'),
        'revision': data.get('revision'),
        'symbol': data.get('symbol', 'XAUUSDT'),
        'direction': data.get('direction'),
        'plan_fingerprint': (plan or {}).get('plan_fingerprint') if plan else data.get('plan_fingerprint'),
        'snapshot_id': data.get('snapshot_id'),
        'digest': data.get('digest'),
        'detector_version': data.get('detector_version'),
        'config_version': data.get('config_version'),
        'schema_version': data.get('schema_version'),
    }
    from services.smc_scenario_config import active_scenario_config
    prov = read_snapshot_provenance(data, active_scenario_config)

    from services.smc_scenario_summary import build_entry_explanation
    try:
        guards = (order_data.get('guards') if order_data else None) or data.get('guard_audit')
        explanation = build_entry_explanation(data, plan_dict, guards)
    except Exception as exc:
        logger.warning(f"Could not build entry explanation: {exc}")
        explanation = None

    chain = data.get('trigger_chain') or {}
    return {
        'bundle_version': BUNDLE_VERSION,
        'binding': binding,
        'as_of_ms': as_of_ms,
        'evidence_cutoff_ms': int(data.get('evidence_cutoff_ms') or as_of_ms),
        'strategy_family': data.get('family') or data.get('strategy_family', 'STANDARD_SMC'),
        'state': data.get('state', 'OBSERVING'),
        'summary_vi': data.get('summary_vi'),
        'swings': swings,
        'structure_events': structure_events,
        'poi_zones': poi_zones,
        'liquidity_levels': liquidity_levels,
        'displacements': displacements,
        'retests': retests,
        'displacement_annotation': displacements[0] if displacements else None,
        'scenario_path': path,
        'risk_reward_plan': plan_dict,
        'target': target if isinstance(target, dict) else None,
        'invalidation': inv if isinstance(inv, dict) else None,
        'invalidation_price': inv_price if _finite(inv_price) else None,
        'chain': {'verified': bool(chain.get('verified')), 'reasons': list(chain.get('reasons') or []),
                  'advisories': list(chain.get('advisories') or [])},
        'probability': prob,
        'blockers': list(data.get('blockers') or []),
        'warnings': warnings,
        'provenance': prov,
        'entry_explanation': explanation,
        'is_empty': not nodes,
    }


def build_selected_chart_bundle(snapshot: ScenarioSnapshot, source_type: str = 'SCENARIO',
                                binding_id: Optional[str] = None, order_data: Optional[Dict[str, Any]] = None,
                                include_all_levels: bool = False) -> Dict[str, Any]:
    return build_selected_chart_bundle_from_dict(snapshot.to_dict(), source_type=source_type, binding_id=binding_id,
                                                 order_data=order_data, include_all_levels=include_all_levels)


def adapt_legacy_quality_snapshot(
    quality: Any, plan_data: Optional[Dict[str, Any]] = None, order_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Transparent adapter for legacy V14 quality snapshot rows/dicts. It never invents anchors: original candle
    times missing -> UNANCHORED warnings; no certified label."""
    def _get(k, default=None):
        return quality.get(k, default) if isinstance(quality, dict) else getattr(quality, k, default)

    warnings = ['LEGACY_QUALITY_ADAPTED: Snapshot cũ thiếu một số metadata thời điểm nến gốc.']
    structure_events: List[Dict[str, Any]] = []
    poi_zones: List[Dict[str, Any]] = []
    as_of = _get('as_of_ms', 0) or 0
    direction = _get('direction')

    def _load(v):
        if not v:
            return None
        try:
            return json.loads(v) if isinstance(v, str) else v
        except Exception:
            return None

    s_dict = _load(_get('sweep_evidence'))
    if isinstance(s_dict, dict):
        structure_events.append({
            'id': s_dict.get('id', 'legacy_sweep'), 'type': s_dict.get('type', 'SWEEP'),
            'kind': s_dict.get('kind') or ('SWEEP_LOW' if direction == 'LONG' else 'SWEEP_HIGH' if direction == 'SHORT' else 'SWEEP'),
            'direction': direction, 'price': s_dict.get('level'),
            'occurred_at_ms': s_dict.get('timestamp'), 'known_at_ms': s_dict.get('confirmed_at'),
            'source_timeframe': _get('timeframe', '15M'), 'source_ids': [], 'detail': s_dict.get('detail', 'Sweep legacy')})
        if not s_dict.get('timestamp'):
            warnings.append('UNANCHORED:sweep')
    f_dict = _load(_get('fvg_evidence'))
    if isinstance(f_dict, dict):
        top, bottom = f_dict.get('top'), f_dict.get('bottom')
        if _finite(top) and _finite(bottom) and bottom < top and f_dict.get('timestamp'):
            poi_zones.append({
                'id': f_dict.get('id', 'legacy_fvg'), 'kind': 'FVG', 'top': top, 'bottom': bottom,
                'start_at_ms': f_dict.get('timestamp'), 'end_at_ms': None, 'display_end_ms': as_of,
                'direction': direction, 'state': f_dict.get('state', 'UNKNOWN'), 'source_ids': [],
                'occurred_at_ms': f_dict.get('timestamp'), 'known_at_ms': f_dict.get('confirmed_at'),
                'source_timeframe': _get('timeframe', '15M')})
        else:
            warnings.append('UNANCHORED:fvg')

    binding = {
        'source': 'ORDER' if order_data else 'SETUP',
        'id': _get('setup_id') or (order_data.get('id') if order_data else 'legacy'),
        'instance_id': _get('setup_instance_id'), 'revision': _get('setup_revision', 1),
        'symbol': _get('symbol', 'XAUUSDT'), 'direction': direction, 'plan_fingerprint': None,
        'snapshot_id': _get('snapshot_id'), 'legacy': True,
    }
    return {
        'bundle_version': BUNDLE_VERSION, 'binding': binding, 'as_of_ms': as_of, 'evidence_cutoff_ms': as_of,
        'strategy_family': _get('strategy_family', 'STANDARD_SMC'), 'state': 'LEGACY_UNCERTIFIED',
        'summary_vi': 'Bản chụp chất lượng cũ (legacy quality snapshot), chưa được xác minh theo chuỗi SMC mới.',
        'swings': [], 'structure_events': structure_events, 'poi_zones': poi_zones, 'liquidity_levels': [],
        'displacements': [], 'retests': [], 'displacement_annotation': None, 'scenario_path': [],
        'risk_reward_plan': plan_data or order_data, 'target': None, 'invalidation': None,
        'invalidation_price': None, 'chain': {'verified': False, 'reasons': ['LEGACY_EVIDENCE'], 'advisories': []},
        'probability': {'status': 'UNAVAILABLE', 'p_tp_before_sl': None, 'support_n': None},
        'blockers': [], 'warnings': warnings, 'provenance': None, 'entry_explanation': None,
        'is_empty': not (structure_events or poi_zones),
    }
