import { describe, it, expect } from 'vitest';
import {
  normalizeStructureKind,
  msToChartTime,
  positivePrice,
  hasValidGeometry,
  makeSelectionKey,
  buildPlanFingerprint,
  buildChartEvidenceBundle,
} from './utils/chartEvidence';
import type { QualitySnapshot } from './types/setupQuality';

describe('V14 Chart Evidence Normalizers & Geometry', () => {
  it('normalizes CHOCH, CHoCH, choch, BOS, and SWEEP correctly', () => {
    expect(normalizeStructureKind('CHOCH')).toBe('CHOCH');
    expect(normalizeStructureKind('CHoCH')).toBe('CHOCH');
    expect(normalizeStructureKind('choch')).toBe('CHOCH');
    expect(normalizeStructureKind('BOS')).toBe('BOS');
    expect(normalizeStructureKind('bos')).toBe('BOS');
    expect(normalizeStructureKind('SWEEP')).toBe('SWEEP');
    expect(normalizeStructureKind('sweep')).toBe('SWEEP');
    expect(normalizeStructureKind('UNKNOWN_EVENT')).toBeNull();
    expect(normalizeStructureKind(null)).toBeNull();
    expect(normalizeStructureKind(123)).toBeNull();
  });

  it('converts domain millisecond timestamps to lightweight-charts second timestamps', () => {
    expect(msToChartTime(1700000000000)).toBe(1700000000);
    expect(msToChartTime(0)).toBeNull();
    expect(msToChartTime(-100)).toBeNull();
    expect(msToChartTime(NaN)).toBeNull();
    expect(msToChartTime('1700000000000')).toBeNull();
  });

  it('validates positive prices and finite numbers', () => {
    expect(positivePrice(2650.5)).toBe(2650.5);
    expect(positivePrice(-10)).toBeNull();
    expect(positivePrice(0)).toBeNull();
    expect(positivePrice(Infinity)).toBeNull();
  });

  it('strictly validates LONG and SHORT geometry', () => {
    // Valid LONG: SL < Entry < TP
    expect(hasValidGeometry({
      direction: 'LONG',
      entry: 2650.0,
      stopLoss: 2640.0,
      takeProfit: 2670.0,
      levelSource: 'CONFIRMED'
    })).toBe(true);

    // Invalid LONG: SL > Entry
    expect(hasValidGeometry({
      direction: 'LONG',
      entry: 2650.0,
      stopLoss: 2655.0,
      takeProfit: 2670.0,
      levelSource: 'CONFIRMED'
    })).toBe(false);

    // Valid SHORT: TP < Entry < SL
    expect(hasValidGeometry({
      direction: 'SHORT',
      entry: 4123.0,
      stopLoss: 4133.0,
      takeProfit: 4103.0,
      levelSource: 'CONFIRMED'
    })).toBe(true);

    // Invalid SHORT: TP > Entry
    expect(hasValidGeometry({
      direction: 'SHORT',
      entry: 4123.0,
      stopLoss: 4133.0,
      takeProfit: 4135.0,
      levelSource: 'CONFIRMED'
    })).toBe(false);
  });

  it('builds deterministic selection keys and plan fingerprints', () => {
    const key1 = makeSelectionKey({
      source: 'WATCH_SETUP',
      id: 'setup-1',
      instance: 'inst-1',
      revision: 2,
      planFingerprint: 'SHORT:4123.00:4133.00:4103.00'
    });
    const key2 = makeSelectionKey({
      source: 'WATCH_SETUP',
      id: 'setup-1',
      instance: 'inst-1',
      revision: 2,
      planFingerprint: 'SHORT:4123.00:4133.00:4103.00'
    });
    expect(key1).toBe(key2);

    const fp = buildPlanFingerprint('short', 4123.0, 4133.0, 4103.0);
    expect(fp).toBe('SHORT:4123.00:4133.00:4103.00');
  });

  it('builds chart evidence bundle from quality snapshot', () => {
    const snapshot: QualitySnapshot = {
      schema_version: 'v14.0',
      snapshot_id: 'snap-short-4123',
      setup_id: 'setup-1',
      setup_instance_id: 'inst-1',
      setup_revision: 1,
      quality_revision: 1,
      symbol: 'XAUUSDT',
      timeframe: '15M',
      direction: 'SHORT',
      strategy_family: 'STANDARD_SMC',
      as_of_ms: 1700003600000,
      config_version: 'v1.0',
      detector_version: 'v1.0',
      plan_snapshot: {
        direction: 'SHORT',
        entry: 4123.0,
        stop_loss: 4133.0,
        take_profit: 4103.0,
        quantity: 0.05,
        risk_usdt: 2.5,
        gross_rr: 2.0,
        net_rr: 1.95,
        plan_fingerprint: 'SHORT:4123.00:4133.00:4103.00',
      },
      assessment: {
        status: 'SATISFIED',
        required_met_count: 5,
        required_total_count: 5,
        checks: [],
        cautions: [],
        related_evidence_ids: ['sh-1', 'sweep-1', 'choch-1'],
        summary: {
          primary_reason_vi: 'Đã quét đỉnh 4135, xuất hiện Bearish CHOCH tại 4125',
          caution_vi: 'Không có tin tức lớn trong phiên',
        },
      },
      evidence: [
        {
          id: 'sh-1',
          kind: 'SWING_HIGH',
          symbol: 'XAUUSDT',
          timeframe: '15M',
          known_at_ms: 1700001000000,
          level: 4135.0,
          direction: 'BEARISH',
        },
        {
          id: 'sweep-1',
          kind: 'SWEEP',
          symbol: 'XAUUSDT',
          timeframe: '15M',
          known_at_ms: 1700002000000,
          level: 4135.5,
          direction: 'BEARISH',
          detail: 'Sweep of swing high 4135.0',
        },
        {
          id: 'choch-1',
          kind: 'CHOCH',
          symbol: 'XAUUSDT',
          timeframe: '15M',
          known_at_ms: 1700003000000,
          level: 4125.0,
          direction: 'BEARISH',
          detail: 'Bearish CHOCH below 4125.0',
        },
      ],
    };

    const bundle = buildChartEvidenceBundle(snapshot, 'selection-key-1');
    expect(bundle.snapshotId).toBe('snap-short-4123');
    expect(bundle.direction).toBe('SHORT');
    expect(bundle.swings.length).toBe(1);
    expect(bundle.swings[0].price).toBe(4135.0);
    expect(bundle.swings[0].label).toBe('SH');

    expect(bundle.structureEvents.length).toBe(2); // sweep and choch
    const chochEvent = bundle.structureEvents.find(e => e.event_type === 'CHOCH');
    expect(chochEvent).toBeDefined();
    expect(chochEvent?.level).toBe(4125.0);

    const sweepEvent = bundle.structureEvents.find(e => e.event_type === 'SWEEP');
    expect(sweepEvent).toBeDefined();
    expect(sweepEvent?.level).toBe(4135.5);
  });
});
