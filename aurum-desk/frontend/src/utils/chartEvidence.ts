/**
 * frontend/src/utils/chartEvidence.ts
 * V14.4 — Chart Evidence Utilities & Normalizers (Appendix B, C).
 */

import type { UTCTimestamp } from 'lightweight-charts';
import type {
  SMCStructureEvent,
  SMCSwing,
  SMCLiquidityLevel,
  SMCDisplacement,
  SMCRetest,
  SMCConditionalPath,
  SMCConditionalPathPoint,
} from '../plugins/SMCStructurePrimitive';
import { chartSelectionKey } from '../types/smcScenario';
import type { ChartBinding } from '../types/smcScenario';

export type StructureKind = 'BOS' | 'CHOCH' | 'MSS' | 'SWEEP' | 'RETEST';

export function normalizeStructureKind(value: unknown): StructureKind | null {
  if (typeof value !== 'string') return null;
  const kind = value.trim().toUpperCase();
  if (kind === 'BOS' || kind === 'CHOCH' || kind === 'MSS' || kind === 'SWEEP' || kind === 'RETEST') {
    return kind as StructureKind;
  }
  if (kind === 'CHOCH_BULL' || kind === 'CHOCH_BEAR') return 'CHOCH';
  if (kind === 'SWEEP_HIGH' || kind === 'SWEEP_LOW') return 'SWEEP';
  return null;
}

export function msToChartTime(value: unknown): UTCTimestamp | null {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value <= 0) {
    return null;
  }
  return Math.floor(value / 1000) as UTCTimestamp;
}

export function positivePrice(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? Number(value.toFixed(2))
    : null;
}

export interface ResolvedChartPlan {
  direction: 'LONG' | 'SHORT';
  entry: number;
  stopLoss: number;
  takeProfit: number;
  levelSource: 'CONFIRMED' | 'PROVISIONAL' | 'DRAFT' | 'ACTUAL_TRADE';
}

export function hasValidGeometry(plan: ResolvedChartPlan): boolean {
  const { direction, entry, stopLoss, takeProfit } = plan;
  if ([entry, stopLoss, takeProfit].some((val) => positivePrice(val) === null)) {
    return false;
  }
  return direction === 'LONG'
    ? stopLoss < entry && entry < takeProfit
    : direction === 'SHORT' && takeProfit < entry && entry < stopLoss;
}

export function makeSelectionKey(identity: {
  source: string;
  id?: string;
  instance?: string | null;
  instanceId?: string | null;
  revision?: number | null;
  symbol?: string;
  direction?: 'LONG' | 'SHORT' | null;
  planFingerprint: string | null;
  snapshotId?: string | null;
}): string {
  const binding: ChartBinding = {
    source: (identity.source as any) || 'SETUP',
    id: identity.id || '',
    instanceId: identity.instance || identity.instanceId || null,
    revision: identity.revision ?? null,
    symbol: identity.symbol || 'XAUUSDT',
    direction: identity.direction || null,
    planFingerprint: identity.planFingerprint,
    snapshotId: identity.snapshotId || null,
  };
  return chartSelectionKey(binding);
}

export function buildPlanFingerprint(
  direction: string,
  entry: number,
  sl: number,
  tp: number
): string {
  const normDir = direction.toUpperCase();
  const e = entry.toFixed(2);
  const s = sl.toFixed(2);
  const t = tp.toFixed(2);
  return `${normDir}:${e}:${s}:${t}`;
}

export interface ChartPoiZoneV144 {
  id?: string;
  kind?: string;
  bottom: number;
  top: number;
  label: string;
  startAtMs?: number;
  endAtMs?: number | null;
  occurredAtMs?: number;
  knownAtMs?: number;
  direction?: 'LONG' | 'SHORT' | null;
  state?: string;
}

export interface ChartEvidenceBundle {
  selectionKey: string;
  snapshotId: string;
  symbol: string;
  timeframe: string;
  direction: 'LONG' | 'SHORT' | null;
  asOfMs?: number;
  state?: string;
  summaryVi?: string;
  swings: SMCSwing[];
  structureEvents: SMCStructureEvent[];
  poiZones: ChartPoiZoneV144[];
  liquidityLevels?: SMCLiquidityLevel[];
  displacements?: SMCDisplacement[];
  displacementAnnotation?: any;
  retests?: SMCRetest[];
  scenarioPath?: SMCConditionalPath;
  riskRewardPlan?: any;
  target?: any;
  chain?: { verified: boolean; reasons: string[]; advisories: string[] };
  probability?: any;
  blockers?: string[];
  invalidationPrice?: number;
  warnings?: string[];
  isEmpty?: boolean;
}

function finiteNumber(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

function posMs(v: unknown): number | null {
  return typeof v === 'number' && Number.isSafeInteger(v) && v > 0 ? v : null;
}

export function normalizeDirection(v: unknown): 'BULLISH' | 'BEARISH' | null {
  if (v === 'BULLISH' || v === 'LONG') return 'BULLISH';
  if (v === 'BEARISH' || v === 'SHORT') return 'BEARISH';
  return null;
}

/**
 * Validate a server bundle against the REQUESTED binding. A request field that is null (not yet known by the
 * client, e.g. snapshot id of a fresh scenario) is not compared; every non-null request field must match what
 * the server returned. Returned ids are bound afterwards by the caller, never silently substituted.
 */
export function validateSelectedBundle(
  bundle: any,
  expectedBinding?: ChartBinding
): { is_valid: boolean; reason?: string } {
  if (!bundle || typeof bundle !== 'object') return { is_valid: false, reason: 'BUNDLE_NULL' };
  const b = bundle.binding;
  if (!b) return { is_valid: false, reason: 'BINDING_MISSING' };
  if (expectedBinding) {
    if (b.source && b.source !== expectedBinding.source) return { is_valid: false, reason: 'SOURCE_MISMATCH' };
    if (b.id !== expectedBinding.id) return { is_valid: false, reason: 'ID_MISMATCH' };
    if (expectedBinding.instanceId && b.instance_id && b.instance_id !== expectedBinding.instanceId) {
      return { is_valid: false, reason: 'INSTANCE_MISMATCH' };
    }
    if (expectedBinding.revision != null && b.revision != null && b.revision !== expectedBinding.revision) {
      return { is_valid: false, reason: 'REVISION_MISMATCH' };
    }
    if (expectedBinding.symbol && b.symbol && b.symbol !== expectedBinding.symbol) {
      return { is_valid: false, reason: 'SYMBOL_MISMATCH' };
    }
    if (expectedBinding.direction && b.direction && b.direction !== expectedBinding.direction) {
      return { is_valid: false, reason: 'DIRECTION_MISMATCH' };
    }
    if (expectedBinding.planFingerprint && b.plan_fingerprint && b.plan_fingerprint !== expectedBinding.planFingerprint) {
      return { is_valid: false, reason: 'PLAN_FINGERPRINT_MISMATCH' };
    }
    if (expectedBinding.snapshotId && b.snapshot_id && b.snapshot_id !== expectedBinding.snapshotId) {
      return { is_valid: false, reason: 'SNAPSHOT_MISMATCH' };
    }
  }
  const asOf = bundle.as_of_ms;
  const cutoff = bundle.evidence_cutoff_ms;
  if (posMs(asOf) === null) return { is_valid: false, reason: 'AS_OF_INVALID' };
  if (cutoff != null && (posMs(cutoff) === null || cutoff > asOf)) {
    return { is_valid: false, reason: 'CUTOFF_INVALID' };
  }
  // Evidence from the future of the cutoff can never be part of a causal explanation.
  const limit = cutoff ?? asOf;
  for (const ev of bundle.structure_events || []) {
    if (finiteNumber(ev?.known_at_ms) && ev.known_at_ms > limit) {
      return { is_valid: false, reason: 'EVIDENCE_AFTER_CUTOFF' };
    }
  }
  return { is_valid: true };
}

export function buildChartEvidenceBundle(
  snapshot: any,
  selectionKey: string
): ChartEvidenceBundle {
  const swings: SMCSwing[] = [];
  const structureEvents: SMCStructureEvent[] = [];
  const poiZones: ChartPoiZoneV144[] = [];

  if (!snapshot) {
    return {
      selectionKey,
      snapshotId: '',
      symbol: 'XAUUSDT',
      timeframe: '15M',
      direction: null,
      swings: [],
      structureEvents: [],
      poiZones: [],
      isEmpty: true,
    };
  }

  // Server bundle (V14.4.1 contract): typed arrays map 1:1 to renderer inputs, nothing is invented client-side.
  if (snapshot.structure_events || snapshot.poi_zones || snapshot.bundle_version) {
    const warnings: string[] = [...(snapshot.warnings || [])];
    const asOfMs = posMs(snapshot.as_of_ms) ?? undefined;
    const retests: SMCRetest[] = [];
    const displacements: SMCDisplacement[] = [];
    const liquidityLevels: SMCLiquidityLevel[] = [];

    for (const s of snapshot.swings || []) {
      const ts = posMs(s?.occurred_at_ms);
      if (ts === null || !finiteNumber(s?.price) || s.price <= 0) {
        warnings.push(`SWING_DROPPED:${s?.id}`);
        continue;
      }
      const isHigh = s.kind === 'SWING_HIGH';
      swings.push({
        id: s.id,
        index: 0,
        timestamp: ts,                                   // pivot candle (occurrence)
        price: s.price,
        type: isHigh ? 'HIGH' : 'LOW',
        confirmed_at: posMs(s.known_at_ms) ?? undefined, // confirmation (known_at) kept separate
        label: s.label || (isHigh ? 'SH' : 'SL'),
      });
    }

    for (const ev of snapshot.structure_events || []) {
      const canonicalKind = normalizeStructureKind(ev?.type || ev?.kind);
      if (canonicalKind === 'RETEST') continue;          // drawn from the typed retests array
      const ts = posMs(ev?.occurred_at_ms);
      if (!canonicalKind || ts === null || !finiteNumber(ev?.price) || ev.price <= 0) {
        warnings.push(`EVENT_DROPPED:${ev?.id}`);
        continue;
      }
      const dir = normalizeDirection(ev.direction);
      structureEvents.push({
        id: ev.id,
        event_type: canonicalKind as any,
        kind: ev.kind,
        level: ev.price,
        start_at: posMs(ev.start_at_ms) ?? undefined,    // broken swing anchor; missing => marker only
        timestamp: ts,
        confirmed_at: posMs(ev.known_at_ms) ?? undefined,
        direction: dir ?? undefined,
        detail: ev.detail || undefined,
        wick_extreme: finiteNumber(ev.wick_extreme) ? ev.wick_extreme : undefined,
        close_back: finiteNumber(ev.close_back) ? ev.close_back : undefined,
      });
    }

    for (const r of snapshot.retests || []) {
      const ts = posMs(r?.occurred_at_ms);
      if (ts === null || !finiteNumber(r?.price) || r.price <= 0) {
        warnings.push(`RETEST_DROPPED:${r?.id}`);
        continue;
      }
      retests.push({
        id: r.id,
        timestamp: ts,
        price: r.price,
        direction: normalizeDirection(r.direction) ?? undefined,
        confirmed: posMs(r.known_at_ms) !== null && (asOfMs === undefined || r.known_at_ms <= asOfMs),
        label: r.location || undefined,
      });
    }

    for (const d of snapshot.displacements || []) {
      const ts = posMs(d?.candle_time_ms);
      if (ts === null) {
        warnings.push(`DISPLACEMENT_UNANCHORED:${d?.id}`);
        continue;
      }
      displacements.push({
        id: d.id,
        candle_time: ts,
        level: finiteNumber(d.level) ? d.level : undefined,
        direction: normalizeDirection(d.direction) ?? undefined,
        ratio: finiteNumber(d.body_atr_ratio) ? d.body_atr_ratio : undefined,
        // qualification is the server's measurement; it is never hardcoded true here
        is_qualified: d.is_qualified === true,
        label: d.is_qualified === true ? 'D' : 'D?',
      });
    }

    const rawZones = snapshot.poi_zones || [];
    for (const z of rawZones) {
      const startAt = posMs(z?.start_at_ms);
      if (!finiteNumber(z?.top) || !finiteNumber(z?.bottom) || z.bottom >= z.top || startAt === null) {
        warnings.push(`ZONE_UNANCHORED:${z?.id}`);          // never drawn with default bounds/times
        continue;
      }
      poiZones.push({
        id: z.id,
        kind: z.kind || 'FVG',
        bottom: z.bottom,
        top: z.top,
        label: z.kind === 'FVG' ? 'FVG' : (z.poi_kind || 'POI'),
        startAtMs: startAt,
        endAtMs: posMs(z.display_end_ms) ?? posMs(z.end_at_ms),
        occurredAtMs: posMs(z.occurred_at_ms) ?? undefined,
        knownAtMs: posMs(z.known_at_ms) ?? undefined,
        direction: z.direction === 'BULLISH' || z.direction === 'LONG' ? 'LONG'
          : z.direction === 'BEARISH' || z.direction === 'SHORT' ? 'SHORT' : null,
        state: z.state || 'ACTIVE',
      });
    }

    for (const l of snapshot.liquidity_levels || []) {
      if (!finiteNumber(l?.price) || l.price <= 0) continue;
      const consumed = l.is_consumed === true;
      liquidityLevels.push({
        id: l.id,
        price: l.price,
        label: l.name || l.id,
        side: String(l.side || '').toUpperCase().includes('BUY') ? 'BUY_SIDE' : 'SELL_SIDE',
        start_at: posMs(l.occurred_at_ms) ?? undefined,
        state: consumed ? 'CONSUMED' : (l.state || 'UNTOUCHED'),
        is_target: l.is_target === true,
        is_partial: l.is_partial === true,
      });
    }

    const pathPoints: SMCConditionalPathPoint[] = [];
    let pathConfirmed = false;
    for (const seg of snapshot.scenario_path || []) {
      if (!pathPoints.length && finiteNumber(seg?.from_price) && posMs(seg?.from_time_ms) !== null) {
        pathPoints.push({ xTime: seg.from_time_ms, price: seg.from_price });
      }
      if (finiteNumber(seg?.to_price)) {
        pathPoints.push({
          xTime: posMs(seg.to_time_ms) ?? undefined,
          price: seg.to_price,
          isProjection: seg.is_projection === true,
        });
      }
      if (seg?.is_confirmed === true) pathConfirmed = true;
    }
    const scenarioPath: SMCConditionalPath | undefined = pathPoints.length >= 2
      ? { points: pathPoints, isConfirmed: pathConfirmed, label: pathConfirmed ? 'Kịch bản nếu xác nhận' : 'Kịch bản' }
      : undefined;

    return {
      selectionKey,
      snapshotId: snapshot.binding?.snapshot_id || snapshot.snapshot_id || '',
      symbol: snapshot.binding?.symbol || snapshot.symbol || 'XAUUSDT',
      timeframe: snapshot.timeframe || '5M',
      direction: snapshot.binding?.direction === 'LONG' || snapshot.binding?.direction === 'SHORT'
        ? snapshot.binding.direction : null,
      asOfMs,
      state: snapshot.state,
      summaryVi: snapshot.summary_vi || undefined,
      swings,
      structureEvents,
      poiZones,
      liquidityLevels,
      displacements,
      displacementAnnotation: snapshot.displacement_annotation,
      retests,
      scenarioPath,
      riskRewardPlan: snapshot.risk_reward_plan,
      target: snapshot.target ?? undefined,
      chain: snapshot.chain,
      probability: snapshot.probability,
      blockers: snapshot.blockers || [],
      invalidationPrice: finiteNumber(snapshot.invalidation_price) ? snapshot.invalidation_price : undefined,
      warnings,
      isEmpty: snapshot.is_empty ?? false,
    };
  }

  // Legacy QualitySnapshot adapter
  const evidenceList = snapshot.evidence || [];
  const relatedIds = new Set(snapshot.assessment?.related_evidence_ids || []);

  for (const item of evidenceList) {
    const isRelated = relatedIds.has(item.id);
    if (!isRelated && relatedIds.size > 0) continue;

    const occurredAt = item.occurred_at_ms || item.timestamp || item.known_at_ms;
    const knownAt = item.known_at_ms || item.confirmed_at || occurredAt;

    if (item.kind === 'SWING_HIGH' || item.kind === 'SWING_LOW') {
      if (item.level) {
        swings.push({
          index: 0,
          timestamp: occurredAt,
          price: item.level,
          type: item.kind === 'SWING_HIGH' ? 'HIGH' : 'LOW',
          confirmed_at: knownAt,
          label: item.kind === 'SWING_HIGH' ? 'SH' : 'SL',
        });
      }
    } else if (item.kind === 'BOS' || item.kind === 'CHOCH' || item.kind === 'MSS' || item.kind === 'SWEEP') {
      const canonicalKind = normalizeStructureKind(item.kind);
      if (canonicalKind && item.level) {
        const normDir =
          item.direction === 'BULLISH' || item.direction === 'LONG'
            ? 'BULLISH'
            : item.direction === 'BEARISH' || item.direction === 'SHORT'
            ? 'BEARISH'
            : null;
        structureEvents.push({
          id: item.id,
          event_type: canonicalKind as any,
          level: item.level,
          timestamp: occurredAt,
          confirmed_at: knownAt,
          direction: normDir ?? undefined,
          detail: item.detail || undefined,
        });
      }
    } else if (item.kind === 'FVG' && item.extra) {
      const b = item.extra.bottom;
      const t = item.extra.top;
      if (typeof b === 'number' && typeof t === 'number') {
        poiZones.push({
          bottom: b,
          top: t,
          label: 'FVG',
          startAtMs: item.extra.start_at_ms || occurredAt,
          endAtMs: item.extra.end_at_ms || null,
          occurredAtMs: occurredAt,
          knownAtMs: knownAt,
          direction: item.direction,
          state: item.extra.state || 'ACTIVE',
        });
      }
    }
  }

  return {
    selectionKey,
    snapshotId: snapshot.snapshot_id || '',
    symbol: snapshot.symbol || 'XAUUSDT',
    timeframe: snapshot.timeframe || '15M',
    direction: snapshot.direction,
    swings,
    structureEvents,
    poiZones,
    isEmpty: false,
  };
}
