import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import React from 'react';
import { buildChartEvidenceBundle, normalizeDirection } from './utils/chartEvidence';
import { projectMsToBar, clipSpanX } from './utils/chartTimeProjection';
import { applyScenarioUpdate, parseScenarioUpdated } from './utils/scenarioEvents';
import { buildForecastLines, dataStatusLabel } from './utils/forecastLines';
import { defaultSessionKey } from './types/sessionForecast';
import type { SessionForecastView } from './types/sessionForecast';
import { SMCProbabilityBadge } from './components/SMCProbabilityBadge';
import { createOrderSelection, createSetupSelection } from './utils/selectedChartContext';
import { selectedChartReducer, initialSelectedChartState } from './hooks/useSelectedChart';
import type { SMCScenarioView } from './types/smcScenario';

const T0 = 1_790_000_000_000;
const M5 = 300_000;

/** Shape produced by backend chart_evidence_service.build_selected_chart_bundle_from_dict. */
function backendBundle(): any {
  return {
    bundle_version: 'v14.4.1',
    binding: { source: 'SETUP', id: 's1', instance_id: 'i1', revision: 2, symbol: 'XAUUSDT', direction: 'LONG', plan_fingerprint: 'fp', snapshot_id: 'snap' },
    as_of_ms: T0 + 40 * M5,
    evidence_cutoff_ms: T0 + 40 * M5,
    state: 'STRUCTURE_CONFIRMED',
    summary_vi: 'Đã xác nhận cấu trúc',
    swings: [
      { id: 'sw-low', kind: 'SWING_LOW', price: 2400.5, label: 'SL', role: 'SWEPT', occurred_at_ms: T0 + 2 * M5, known_at_ms: T0 + 5 * M5, source_timeframe: '5M' },
      { id: 'sw-high', kind: 'SWING_HIGH', price: 2410, role: 'BROKEN', occurred_at_ms: T0 + 8 * M5, known_at_ms: T0 + 11 * M5, source_timeframe: '5M' },
      { id: 'sw-bad', kind: 'SWING_HIGH', price: null, occurred_at_ms: T0, known_at_ms: T0 },
    ],
    structure_events: [
      { id: 'sweep1', type: 'SWEEP', kind: 'SWEEP', direction: 'BULLISH', price: 2400.5, wick_extreme: 2398.2, close_back: 2401.4, occurred_at_ms: T0 + 12 * M5, known_at_ms: T0 + 13 * M5, detail: 'Quét đáy Á' },
      { id: 'bos1', type: 'BOS', kind: 'BOS', direction: 'BULLISH', price: 2410, occurred_at_ms: T0 + 18 * M5, known_at_ms: T0 + 19 * M5, start_at_ms: T0 + 8 * M5, start_price: 2410 },
      { id: 'bos2', type: 'MSS', kind: 'MSS', direction: null, price: 2412, occurred_at_ms: T0 + 20 * M5, known_at_ms: T0 + 21 * M5, start_at_ms: null },
      { id: 'rt1', type: 'RETEST', kind: 'RETEST', direction: 'BULLISH', price: 2405, occurred_at_ms: T0 + 30 * M5, known_at_ms: T0 + 31 * M5 },
    ],
    retests: [{ id: 'rt1', type: 'RETEST', direction: 'BULLISH', price: 2405, location: 'closed_candle', occurred_at_ms: T0 + 30 * M5, known_at_ms: T0 + 31 * M5 }],
    displacements: [
      { id: 'disp-bos1', candle_time_ms: T0 + 18 * M5, direction: 'BULLISH', level: 2410, is_qualified: true, body_atr_ratio: 1.8 },
      { id: 'disp-bos2', candle_time_ms: T0 + 20 * M5, direction: 'BULLISH', level: 2412, is_qualified: false, body_atr_ratio: 0.6 },
    ],
    poi_zones: [
      { id: 'fvg1', kind: 'FVG', top: 2408, bottom: 2404, start_at_ms: T0 + 16 * M5, end_at_ms: null, display_end_ms: T0 + 40 * M5, direction: 'BULLISH', state: 'ACTIVE', occurred_at_ms: T0 + 18 * M5, known_at_ms: T0 + 19 * M5 },
      { id: 'fvg-unanchored', kind: 'FVG', top: 2420, bottom: 2418, start_at_ms: null, display_end_ms: T0 + 40 * M5, direction: 'BULLISH' },
    ],
    liquidity_levels: [
      { id: 'asia-low', name: 'Đáy Á', price: 2400.5, side: 'SELL_SIDE', type: 'SESSION_LOW', state: 'SWEPT', occurred_at_ms: T0, is_swept: true, is_consumed: true },
      { id: 'tgt', name: 'Đỉnh Âu', price: 2430, side: 'BUY_SIDE', state: 'UNTOUCHED', occurred_at_ms: T0 + 3 * M5, is_target: true },
    ],
    scenario_path: [
      { from_time_ms: T0 + 12 * M5, from_price: 2400.5, to_time_ms: T0 + 18 * M5, to_price: 2410, is_confirmed: true, is_projection: false },
      { from_time_ms: T0 + 30 * M5, from_price: 2405, to_time_ms: null, to_price: 2430, is_confirmed: false, is_projection: true },
    ],
    risk_reward_plan: { planned_entry: 2405, stop_loss: 2397, take_profit: 2430, net_rr: 2.4 },
    chain: { verified: true, reasons: [], advisories: [] },
    probability: { status: 'UNAVAILABLE', p_tp_before_sl: null, support_n: null },
    blockers: [],
    warnings: [],
    is_empty: false,
  };
}

describe('V14.4.1 server bundle -> renderer inputs (mapper)', () => {
  const b = buildChartEvidenceBundle(backendBundle(), 'key-1');

  it('maps swings with the pivot candle time and keeps confirmation time separate', () => {
    expect(b.swings.map((s) => s.id)).toEqual(['sw-low', 'sw-high']);
    const low = b.swings[0];
    expect(low.timestamp).toBe(T0 + 2 * M5);
    expect(low.confirmed_at).toBe(T0 + 5 * M5);
    expect(low.type).toBe('LOW');
    expect(b.warnings).toContain('SWING_DROPPED:sw-bad');
  });

  it('draws the break line from the broken swing; no anchor => no start (marker only) and no default direction', () => {
    const bos = b.structureEvents.find((e) => e.id === 'bos1')!;
    expect(bos.start_at).toBe(T0 + 8 * M5);
    expect(bos.timestamp).toBe(T0 + 18 * M5);
    expect(bos.direction).toBe('BULLISH');
    const mss = b.structureEvents.find((e) => e.id === 'bos2')!;
    expect(mss.start_at).toBeUndefined();
    expect(mss.direction).toBeUndefined(); // unknown stays unknown, never BEARISH
  });

  it('keeps wick extreme / close-back of a sweep and routes retests to their own array', () => {
    const sweep = b.structureEvents.find((e) => e.id === 'sweep1')!;
    expect(sweep.wick_extreme).toBe(2398.2);
    expect(sweep.close_back).toBe(2401.4);
    expect(b.structureEvents.some((e) => e.id === 'rt1')).toBe(false);
    expect(b.retests).toHaveLength(1);
    expect(b.retests![0].confirmed).toBe(true);
  });

  it('displacement qualification comes from the server measurement (never hardcoded true)', () => {
    const [d1, d2] = b.displacements!;
    expect(d1.is_qualified).toBe(true);
    expect(d2.is_qualified).toBe(false);
    expect(d2.label).toBe('D?');
  });

  it('FVG zones use real source times and bounds; unanchored zones are dropped with a warning', () => {
    expect(b.poiZones).toHaveLength(1);
    expect(b.poiZones[0]).toMatchObject({ id: 'fvg1', top: 2408, bottom: 2404, startAtMs: T0 + 16 * M5, endAtMs: T0 + 40 * M5 });
    expect(b.warnings).toContain('ZONE_UNANCHORED:fvg-unanchored');
  });

  it('liquidity levels keep target/consumed state; path projection has no timestamp', () => {
    expect(b.liquidityLevels!.find((l) => l.id === 'tgt')!.is_target).toBe(true);
    expect(b.liquidityLevels!.find((l) => l.id === 'asia-low')!.state).toBe('CONSUMED');
    const proj = b.scenarioPath!.points.find((p) => p.isProjection)!;
    expect(proj.xTime).toBeUndefined();
    expect(b.direction).toBe('LONG');
    expect(b.probability.status).toBe('UNAVAILABLE');
  });

  it('direction stays null when the server did not decide one', () => {
    const raw = backendBundle();
    raw.binding.direction = null;
    expect(buildChartEvidenceBundle(raw, 'k').direction).toBeNull();
    expect(normalizeDirection(null)).toBeNull();
    expect(normalizeDirection('SHORT')).toBe('BEARISH');
  });
});

describe('V14.4.1 time projection and clipping', () => {
  const starts = Array.from({ length: 10 }, (_, i) => T0 + i * M5);

  it('snaps an event to its containing bar and reports outside-window times explicitly', () => {
    expect(projectMsToBar(starts, M5, T0 + 3 * M5 + 17)).toEqual({ kind: 'IN', barIndex: 3, barStartMs: T0 + 3 * M5 });
    expect(projectMsToBar(starts, M5, T0 - 1)).toEqual({ kind: 'BEFORE' });
    expect(projectMsToBar(starts, M5, T0 + 10 * M5)).toEqual({ kind: 'AFTER' });
    expect(projectMsToBar([], M5, T0)).toBeNull();
    expect(projectMsToBar(starts, M5, -5)).toBeNull();
  });

  it('a gap bar snaps to the latest bar at or before the time', () => {
    const gapped = [T0, T0 + M5, T0 + 5 * M5];
    expect(projectMsToBar(gapped, M5, T0 + 3 * M5)).toEqual({ kind: 'IN', barIndex: 1, barStartMs: T0 + M5 });
  });

  it('zone spans clip to the viewport but are never stretched across the whole pane', () => {
    const before = projectMsToBar(starts, M5, T0 - 100 * M5)!;
    const inside = projectMsToBar(starts, M5, T0 + 4 * M5)!;
    const after = projectMsToBar(starts, M5, T0 + 99 * M5)!;
    // starts before the window, ends inside: clipped at the left edge only
    expect(clipSpanX(before, null, inside, 400, 1000)).toEqual({ x1: 0, x2: 400 });
    // real zone continuing past the window: clipped to the right edge
    expect(clipSpanX(inside, 200, after, null, 1000)).toEqual({ x1: 200, x2: 1000 });
    // entirely outside the loaded window on either side: nothing is drawn
    expect(clipSpanX(after, null, after, null, 1000)).toBeNull();
    expect(clipSpanX(before, null, before, null, 1000)).toBeNull();
    // inside the window but without a coordinate: not drawn (no x0/full-width fallback)
    expect(clipSpanX(inside, null, inside, 300, 1000)).toBeNull();
    // negative / zero-width spans
    expect(clipSpanX(inside, 500, inside, 500, 1000)).toBeNull();
    expect(clipSpanX(inside, 100, inside, 200, 0)).toBeNull();
  });
});

describe('V14.4.1 scenario.updated envelope and reducer', () => {
  const envelope = (payload: Record<string, unknown>, eventType = 'scenario.updated') => ({
    type: 'DOMAIN_EVENT',
    event: { event_type: eventType, aggregate_id: 'sc-1', payload },
  });
  const base: SMCScenarioView = {
    scenario_id: 'sc-1', instance_id: 'inst-1', revision: 1, symbol: 'XAUUSDT', timeframe: '5M',
    strategy_family: 'STANDARD_SMC', direction: null, state: 'WAITING_SWEEP', as_of_ms: T0,
  };
  const payload = (over: Record<string, unknown> = {}) => ({
    scenario_id: 'sc-1', instance_id: 'inst-1', snapshot_id: 'snap-2', revision: 2, symbol: 'XAUUSDT',
    session_id: 'NY-2026-10-12', state: 'SWEEP_DETECTED', direction: 'LONG', digest: 'd2', ...over,
  });

  it('parses only the real DOMAIN_EVENT envelope, never a bare scenario.updated type', () => {
    expect(parseScenarioUpdated({ type: 'scenario.updated', payload: payload() }, 'XAUUSDT')).toEqual({ kind: 'IGNORED', reason: 'NOT_DOMAIN_EVENT' });
    expect(parseScenarioUpdated(envelope(payload(), 'order.filled'), 'XAUUSDT').kind).toBe('IGNORED');
    expect(parseScenarioUpdated(envelope(payload({ symbol: 'BTCUSDT' })), 'XAUUSDT')).toEqual({ kind: 'IGNORED', reason: 'OTHER_SYMBOL' });
    expect(parseScenarioUpdated(envelope(payload({ revision: 0 })), 'XAUUSDT')).toEqual({ kind: 'IGNORED', reason: 'REVISION_INVALID' });
    const ok = parseScenarioUpdated(envelope(payload()), 'XAUUSDT');
    expect(ok.kind).toBe('UPDATE');
  });

  it('applies a newer revision, ignores old revisions and duplicate digests, resyncs on gaps/unknown', () => {
    const seen = new Map<string, string>();
    const parsed = (p: Record<string, unknown>) => {
      const r = parseScenarioUpdated(envelope(p), 'XAUUSDT');
      if (r.kind !== 'UPDATE') throw new Error('unexpected');
      return r.payload;
    };
    const first = applyScenarioUpdate([base], parsed(payload()), seen);
    expect(first.ignored).toBe(false);
    expect(first.scenarios[0]).toMatchObject({ revision: 2, state: 'SWEEP_DETECTED', direction: 'LONG', current_snapshot_id: 'snap-2' });
    expect(first.resync).toBe(true); // full fields only come from the GET

    const dup = applyScenarioUpdate(first.scenarios, parsed(payload()), seen);
    expect(dup.ignored).toBe(true);

    const old = applyScenarioUpdate(first.scenarios, parsed(payload({ revision: 1, digest: 'd1' })), seen);
    expect(old.ignored).toBe(true);

    const gap = applyScenarioUpdate(first.scenarios, parsed(payload({ revision: 5, digest: 'd5' })), seen);
    expect(gap.resync).toBe(true);

    const unknown = applyScenarioUpdate([], parsed(payload({ scenario_id: 'sc-9', digest: 'd9' })), seen);
    expect(unknown.resync).toBe(true);
    expect(unknown.scenarios).toEqual([]);
  });
});

describe('V14.4.1 session forecast lines', () => {
  const forecast = (over: Partial<SessionForecastView> = {}): SessionForecastView => ({
    session: 'LONDON', session_label_vi: 'Âu', session_id: 'LONDON-2026-10-12', as_of_ms: T0, data_status: 'PARTIAL', is_partial: true,
    hypothesis_type: 'UNKNOWN', facts: ['Biên Á 2398.00–2412.00'], primary: null, alternative: null, watched_liquidity: [],
    target: null, confirmation: [], invalidation: [], summary_vi: '', probability: { status: 'UNAVAILABLE' },
    entry_eligible: false, entry_note_vi: 'Chỉ NY được phép vào lệnh theo chính sách.', ...over,
  });

  it('shows QUAN SÁT for a null direction, "chưa xác định" for a missing target and never invents values', () => {
    const lines = buildForecastLines(forecast());
    expect(lines.length).toBeGreaterThanOrEqual(4);
    expect(lines.length).toBeLessThanOrEqual(6 + 1);
    const text = lines.map((l) => `${l.label} ${l.text}`).join('\n');
    expect(text).toContain('QUAN SÁT');
    expect(text).toContain('Mục tiêu chưa xác định');
    expect(text).not.toMatch(/undefined|NaN|null/);
  });

  it('uses the conditional thesis and real watched liquidity when present', () => {
    const f = forecast({
      primary: { direction: 'LONG', thesis_vi: 'Nếu giữ trên đáy Á thì theo dõi tiếp diễn', conditions_vi: [], invalidation_vi: null },
      watched_liquidity: [{ id: 'a', price: 2398, label_vi: 'Đáy Á', state: 'UNTOUCHED', side: 'SELL_SIDE', source_kind: 'SESSION_LOW', is_partial: false, known_at_ms: T0 }],
      target: { price: 2412, label_vi: 'Đỉnh Á' }, confirmation: ['Đóng nến M5 trên 2405'],
    });
    const joined = buildForecastLines(f).map((l) => l.text).join(' | ');
    expect(joined).toContain('Nếu giữ trên đáy Á');
    expect(joined).toContain('Đáy Á 2398.00');
    expect(joined).toContain('Đỉnh Á 2412.00');
  });

  it('labels partial / missing sessions and picks the default session from NY time', () => {
    expect(dataStatusLabel(forecast())).toBe('Một phần');
    expect(dataStatusLabel(forecast({ data_status: 'DATA_UNAVAILABLE', is_partial: true }))).toBe('Thiếu dữ liệu');
    expect(dataStatusLabel(forecast({ data_status: 'OK', is_partial: false }))).toBeNull();
    // 2026-10-12 12:30 UTC = 08:30 New York (EDT)
    expect(defaultSessionKey(Date.UTC(2026, 9, 12, 12, 30))).toBe('NY');
    // 2026-10-12 08:00 UTC = 04:00 New York -> Europe research window
    expect(defaultSessionKey(Date.UTC(2026, 9, 12, 8, 0))).toBe('LONDON');
    // 2026-10-12 01:00 UTC = 21:00 previous day New York -> Asia
    expect(defaultSessionKey(Date.UTC(2026, 9, 12, 1, 0))).toBe('ASIA');
  });
});

describe('V14.4.1 probability badge honesty', () => {
  it('never renders a percentage without a validated estimate', () => {
    const none = renderToStaticMarkup(React.createElement(SMCProbabilityBadge, { probability: { status: 'UNAVAILABLE' } }));
    expect(none).toContain('Chưa có xác suất được kiểm định');
    expect(none).not.toContain('%');
    const fake = renderToStaticMarkup(React.createElement(SMCProbabilityBadge, { probability: { status: 'NOT_VERIFIED', p_tp_before_sl: 0.65 } }));
    expect(fake).not.toContain('%');
  });

  it('a validated estimate is labelled as TP-before-SL for its horizon, not as winrate', () => {
    const html = renderToStaticMarkup(React.createElement(SMCProbabilityBadge, {
      probability: { status: 'VALIDATED', p_tp_before_sl: 0.58, horizon: '24 nến M5', support_n: 412 },
    }));
    expect(html).toContain('Xác suất TP trước SL');
    expect(html).toContain('24 nến M5');
    expect(html).toContain('58%');
    expect(html.toLowerCase()).not.toContain('winrate');
  });
});

describe('V14.4.1 selection factories and reducer returned binding', () => {
  it('setup/order selection bind only the SCENARIO snapshot id (other ids belong to other tables)', () => {
    const setup = createSetupSelection({ id: 's', setup_instance_id: 'i', version: 3, symbol: 'XAUUSDT', direction: 'LONG', evidence_snapshot_id: 'ev-1', scenario_snapshot_id: null }, null);
    expect(setup.snapshotId).toBeNull();
    expect(setup.revision).toBe(3);
    const order = createOrderSelection({ id: 'o', instrument: 'XAUUSDT', direction: 'SHORT', quality_snapshot_id: 'q-1', evidence_snapshot_id: 'ev-1', scenario_snapshot_id: 'sn-1' });
    expect(order.snapshotId).toBe('sn-1');
    const legacyOrder = createOrderSelection({ id: 'o2', instrument: 'XAUUSDT', direction: 'SHORT', quality_snapshot_id: 'q-1' });
    expect(legacyOrder.snapshotId).toBeNull(); // legacy: no scenario ref, nothing is filled from the latest watch
  });

  it('keeps the server-returned binding apart from the requested one and drops it on reselect', () => {
    const binding = { source: 'ORDER' as const, id: 'o1', instanceId: null, revision: null, symbol: 'XAUUSDT', direction: 'LONG' as const, planFingerprint: null, snapshotId: null };
    let s = selectedChartReducer(initialSelectedChartState, { type: 'SELECT_CONTEXT', binding });
    s = selectedChartReducer(s, {
      type: 'LOAD_EVIDENCE_SUCCESS', generation: s.generation, bundle: null,
      returnedBinding: { source: 'ORDER', id: 'o1', snapshot_id: 'snap-X', plan_fingerprint: 'fp' },
    });
    expect(s.returnedBinding?.snapshot_id).toBe('snap-X');
    expect(s.binding?.snapshotId).toBeNull(); // the request is not rewritten
    s = selectedChartReducer(s, { type: 'SELECT_CONTEXT', binding: { ...binding, id: 'o2' } });
    expect(s.returnedBinding).toBeNull();
    expect(s.bundle).toBeNull();
  });
});
