/**
 * frontend/src/utils/chartEvidence.ts
 * V14 — Chart Evidence Utilities & Normalizers (Appendix B1, B2).
 */

import type { UTCTimestamp } from 'lightweight-charts';
import type { QualitySnapshot } from '../types/setupQuality';
import type { SMCStructureEvent, SMCSwing } from '../plugins/SMCStructurePrimitive';

export type StructureKind = 'BOS' | 'CHOCH' | 'SWEEP';

export function normalizeStructureKind(value: unknown): StructureKind | null {
  if (typeof value !== 'string') return null;
  const kind = value.trim().toUpperCase();
  if (kind === 'BOS' || kind === 'CHOCH' || kind === 'SWEEP') return kind;
  if (kind === 'CHOCH' || kind === 'CHOCH_BULL' || kind === 'CHOCH_BEAR') return 'CHOCH';
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
  if ([entry, stopLoss, takeProfit].some(val => positivePrice(val) === null)) {
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
  revision?: number | null;
  planFingerprint: string;
}): string {
  return JSON.stringify([
    identity.source,
    identity.id || '',
    identity.instance || null,
    identity.revision ?? null,
    identity.planFingerprint,
  ]);
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

export interface ChartEvidenceBundle {
  selectionKey: string;
  snapshotId: string;
  symbol: string;
  timeframe: string;
  direction: 'LONG' | 'SHORT';
  swings: SMCSwing[];
  structureEvents: SMCStructureEvent[];
  poiZones: Array<{
    bottom: number;
    top: number;
    label: string;
  }>;
}

export function buildChartEvidenceBundle(
  snapshot: QualitySnapshot,
  selectionKey: string
): ChartEvidenceBundle {
  const swings: SMCSwing[] = [];
  const structureEvents: SMCStructureEvent[] = [];
  const poiZones: Array<{ bottom: number; top: number; label: string }> = [];

  const evidenceList = snapshot.evidence || [];
  const relatedIds = new Set(snapshot.assessment?.related_evidence_ids || []);

  for (const item of evidenceList) {
    const isRelated = relatedIds.has(item.id);
    if (!isRelated && relatedIds.size > 0) continue;

    if (item.kind === 'SWING_HIGH' || item.kind === 'SWING_LOW') {
      if (item.level) {
        swings.push({
          index: 0,
          timestamp: item.known_at_ms,
          price: item.level,
          type: item.kind === 'SWING_HIGH' ? 'HIGH' : 'LOW',
          confirmed_at: item.known_at_ms,
          label: item.kind === 'SWING_HIGH' ? 'SH' : 'SL',
        });
      }
    } else if (item.kind === 'BOS' || item.kind === 'CHOCH' || item.kind === 'SWEEP') {
      const canonicalKind = normalizeStructureKind(item.kind);
      if (canonicalKind && item.level) {
        structureEvents.push({
          id: item.id,
          event_type: canonicalKind,
          level: item.level,
          timestamp: item.known_at_ms,
          confirmed_at: item.known_at_ms,
          direction: item.direction === 'BULLISH' || item.direction === 'LONG' ? 'BULLISH' : 'BEARISH',
          detail: item.detail || `${canonicalKind} at ${item.level.toFixed(2)}`,
        });
      }
    } else if (item.kind === 'FVG' && item.extra) {
      const b = item.extra.bottom;
      const t = item.extra.top;
      if (typeof b === 'number' && typeof t === 'number') {
        poiZones.push({
          bottom: b,
          top: t,
          label: `FVG [${b.toFixed(2)} - ${t.toFixed(2)}]`,
        });
      }
    }
  }

  return {
    selectionKey,
    snapshotId: snapshot.snapshot_id,
    symbol: snapshot.symbol,
    timeframe: snapshot.timeframe,
    direction: snapshot.direction,
    swings,
    structureEvents,
    poiZones,
  };
}
