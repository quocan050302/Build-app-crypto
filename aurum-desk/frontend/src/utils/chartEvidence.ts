/**
 * frontend/src/utils/chartEvidence.ts
 * V14.4 — Chart Evidence Utilities & Normalizers (Appendix B, C).
 */

import type { UTCTimestamp } from 'lightweight-charts';
import type { SMCStructureEvent, SMCSwing } from '../plugins/SMCStructurePrimitive';
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
  swings: SMCSwing[];
  structureEvents: SMCStructureEvent[];
  poiZones: ChartPoiZoneV144[];
  liquidityLevels?: any[];
  displacementAnnotation?: any;
  scenarioPath?: any[];
  riskRewardPlan?: any;
  invalidationPrice?: number;
  warnings?: string[];
  isEmpty?: boolean;
}

export function validateSelectedBundle(
  bundle: any,
  expectedBinding?: ChartBinding
): { is_valid: boolean; reason?: string } {
  if (!bundle) return { is_valid: false, reason: 'BUNDLE_NULL' };
  if (expectedBinding && bundle.binding) {
    if (bundle.binding.id !== expectedBinding.id) {
      return { is_valid: false, reason: 'ID_MISMATCH' };
    }
    if (expectedBinding.direction && bundle.binding.direction && bundle.binding.direction !== expectedBinding.direction) {
      return { is_valid: false, reason: 'DIRECTION_MISMATCH' };
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

  // If this is already a V14.4 bundle from backend
  if (snapshot.structure_events || snapshot.poi_zones) {
    const rawEvents = snapshot.structure_events || [];
    for (const ev of rawEvents) {
      const canonicalKind = normalizeStructureKind(ev.type || ev.kind);
      if (canonicalKind && ev.price) {
        structureEvents.push({
          id: ev.id,
          event_type: canonicalKind as any,
          level: ev.price,
          timestamp: ev.occurred_at_ms || ev.timestamp,
          confirmed_at: ev.known_at_ms || ev.confirmed_at,
          direction: ev.direction === 'BULLISH' || ev.direction === 'LONG' ? 'BULLISH' : 'BEARISH',
          detail: ev.detail || undefined,
        });
      }
    }

    const rawZones = snapshot.poi_zones || [];
    for (const z of rawZones) {
      poiZones.push({
        id: z.id,
        kind: z.kind || 'FVG',
        bottom: z.bottom,
        top: z.top,
        label: 'FVG',
        startAtMs: z.start_at_ms || z.occurred_at_ms,
        endAtMs: z.end_at_ms,
        occurredAtMs: z.occurred_at_ms,
        knownAtMs: z.known_at_ms,
        direction: z.direction,
        state: z.state || 'ACTIVE',
      });
    }

    return {
      selectionKey,
      snapshotId: snapshot.binding?.snapshot_id || snapshot.snapshot_id || '',
      symbol: snapshot.binding?.symbol || snapshot.symbol || 'XAUUSDT',
      timeframe: snapshot.timeframe || '5M',
      direction: snapshot.binding?.direction || snapshot.direction || null,
      swings,
      structureEvents,
      poiZones,
      liquidityLevels: snapshot.liquidity_levels || [],
      displacementAnnotation: snapshot.displacement_annotation,
      scenarioPath: snapshot.scenario_path || [],
      riskRewardPlan: snapshot.risk_reward_plan,
      invalidationPrice: snapshot.invalidation_price,
      warnings: snapshot.warnings || [],
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
          direction: normDir || 'BEARISH',
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
