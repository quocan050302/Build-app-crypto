import { describe, it, expect } from 'vitest';
import {
  computeEvidenceFocusRange,
  flattenEvidenceBundle,
  buildEvidenceAnchorIndex,
  type EvidenceAnchor,
} from './utils/evidenceFocus';

describe('F01-F06: Evidence Focus Range and Anchor Index Tests', () => {
  const stepMs = 5 * 60 * 1000; // 5M bar = 300,000 ms

  it('F01: event range computes correct fromSec and toSec with 16 bars padding', () => {
    const anchor: EvidenceAnchor = {
      id: 'ev-sweep-1',
      kind: 'SWEEP',
      occurredAtMs: 1728000000000,
      knownAtMs: 1728000000000,
      startAtMs: 1728000000000,
      endAtMs: null,
      price: 2650.5,
      hasCoordinates: true,
    };

    const range = computeEvidenceFocusRange(anchor, stepMs, 16);
    expect(range).not.toBeNull();
    if (range) {
      const expectedPadSec = (16 * stepMs) / 1000;
      const centerSec = 1728000000000 / 1000;
      expect(range.fromSec).toBe(centerSec - expectedPadSec);
      expect(range.toSec).toBe(centerSec + expectedPadSec);
    }
  });

  it('F02: FVG zone with start and end spans full width with padding', () => {
    const anchor: EvidenceAnchor = {
      id: 'zone-fvg-1',
      kind: 'FVG',
      occurredAtMs: null,
      knownAtMs: null,
      startAtMs: 1728000000000,
      endAtMs: 1728001500000, // 5 bars later
      price: 2655.0,
      hasCoordinates: true,
    };

    const range = computeEvidenceFocusRange(anchor, stepMs, 10);
    expect(range).not.toBeNull();
    if (range) {
      expect(range.fromSec).toBe((1728000000000 - 10 * stepMs) / 1000);
      expect(range.toSec).toBe((1728001500000 + 10 * stepMs) / 1000);
    }
  });

  it('F03: ms to sec conversion occurs strictly at the chart boundary', () => {
    const anchor: EvidenceAnchor = {
      id: 'ev-1',
      kind: 'MSS',
      occurredAtMs: 1728447000000,
      knownAtMs: 1728447000000,
      startAtMs: 1728447000000,
      endAtMs: null,
      price: 2660.0,
      hasCoordinates: true,
    };

    const range = computeEvidenceFocusRange(anchor, stepMs, 0);
    expect(range).not.toBeNull();
    if (range) {
      expect(range.fromSec).toBe(1728447000);
      expect(range.toSec).toBe(1728447000);
    }
  });

  it('F04: missing anchor timestamps returns null range and reports reason', () => {
    const anchorNoTime: EvidenceAnchor = {
      id: 'liq-no-time',
      kind: 'LIQUIDITY',
      occurredAtMs: null,
      knownAtMs: null,
      startAtMs: null,
      endAtMs: null,
      price: 2670.0,
      hasCoordinates: false,
      missingReason: 'Thiếu thời gian gốc',
    };

    const range = computeEvidenceFocusRange(anchorNoTime, stepMs, 16);
    expect(range).toBeNull();
  });

  it('F05: flattenEvidenceBundle includes displacements and retests from raw bundle', () => {
    const rawBundle = {
      structure_events: [
        { id: 'ev-bos-1', event_type: 'BOS', level: 2650, timestamp: 1728000000000 },
      ],
      poi_zones: [
        { id: 'zone-fvg-1', kind: 'FVG', top: 2655, bottom: 2652, start_at: 1728000300000, end_at: 1728000900000 },
      ],
      liquidity_levels: [
        { id: 'liq-1', price: 2640, start_at: 1727999000000, label: 'BSL' },
      ],
      displacements: [
        { id: 'disp-1', candle_time: 1728000600000, level: 2651 },
      ],
      retests: [
        { id: 'retest-1', touch_time_ms: 1728001200000, touch_price: 2652 },
      ],
    };

    const flat = flattenEvidenceBundle(rawBundle);
    expect(flat.length).toBe(5);

    const index = buildEvidenceAnchorIndex(rawBundle);
    expect(index.has('ev-bos-1')).toBe(true);
    expect(index.has('zone-fvg-1')).toBe(true);
    expect(index.has('liq-1')).toBe(true);
    expect(index.has('disp-1')).toBe(true);
    expect(index.has('retest-1')).toBe(true);

    const disp = index.get('disp-1')!;
    expect(disp.kind).toBe('DISPLACEMENT');
    expect(disp.hasCoordinates).toBe(true);
  });

  it('F06: two distinct IDs of the same kind do not collide or overwrite', () => {
    const rawBundle = {
      structure_events: [
        { id: 'sweep-high-1', event_type: 'SWEEP', level: 2680, timestamp: 1728000000000 },
        { id: 'sweep-high-2', event_type: 'SWEEP', level: 2685, timestamp: 1728000300000 },
      ],
    };

    const index = buildEvidenceAnchorIndex(rawBundle);
    expect(index.size).toBe(2);
    expect(index.get('sweep-high-1')?.price).toBe(2680);
    expect(index.get('sweep-high-2')?.price).toBe(2685);
  });
});
