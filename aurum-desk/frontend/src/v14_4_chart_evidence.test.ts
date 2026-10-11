import { describe, it, expect } from 'vitest';
import {
  chartSelectionKey,
  sameChartBinding,
  validEvidenceTime,
  type ChartBinding,
  type EvidenceTime,
} from './types/smcScenario';
import {
  validateSelectedBundle,
} from './utils/chartEvidence';
import { selectedChartReducer, initialSelectedChartState } from './hooks/useSelectedChart';

describe('V14.4 SMC Chart Evidence Contract & Selection Tests', () => {
  it('builds deterministic selection keys that are distinct from timeframe', () => {
    const bindingA: ChartBinding = {
      source: 'SETUP',
      id: 'setup-100',
      instanceId: 'inst-1',
      revision: 1,
      symbol: 'XAUUSDT',
      direction: 'LONG',
      planFingerprint: 'LONG:2650.00:2640.00:2670.00',
      snapshotId: 'snap-1',
    };

    const bindingB: ChartBinding = {
      ...bindingA,
      revision: 2,
    };

    const keyA = chartSelectionKey(bindingA);
    const keyB = chartSelectionKey(bindingB);

    expect(keyA).not.toBe('15M');
    expect(keyA).not.toBe('5M');
    expect(keyA).not.toBe(keyB);
    expect(sameChartBinding(bindingA, bindingA)).toBe(true);
    expect(sameChartBinding(bindingA, bindingB)).toBe(false);
  });

  it('validates causal evidence timestamps against as_of_ms cutoff', () => {
    const asOf = 1710000000000;

    const validNode: EvidenceTime = {
      occurredAtMs: 1709999000000,
      knownAtMs: 1709999500000,
      sourceTimeframe: '5M',
    };

    const futureKnownNode: EvidenceTime = {
      occurredAtMs: 1709999000000,
      knownAtMs: asOf + 5000, // in future
      sourceTimeframe: '5M',
    };

    const backwardNode: EvidenceTime = {
      occurredAtMs: 1709999500000,
      knownAtMs: 1709999000000, // known before occurred!
      sourceTimeframe: '5M',
    };

    expect(validEvidenceTime(validNode, asOf)).toBe(true);
    expect(validEvidenceTime(futureKnownNode, asOf)).toBe(false);
    expect(validEvidenceTime(backwardNode, asOf)).toBe(false);
  });

  it('handles A -> B -> A selection generation race conditions cleanly', () => {
    let state = initialSelectedChartState;

    // User selects Trade A
    const bindingA: ChartBinding = {
      source: 'ORDER',
      id: 'order-1',
      instanceId: null,
      revision: null,
      symbol: 'XAUUSDT',
      direction: 'LONG',
      planFingerprint: null,
      snapshotId: 'snap-A',
    };

    state = selectedChartReducer(state, {
      type: 'SELECT_CONTEXT',
      binding: bindingA,
      asOfMs: 1000,
    });
    expect(state.generation).toBe(1);
    expect(state.binding?.id).toBe('order-1');

    // User rapidly switches to Trade B
    const bindingB: ChartBinding = {
      source: 'ORDER',
      id: 'order-2',
      instanceId: null,
      revision: null,
      symbol: 'XAUUSDT',
      direction: 'SHORT',
      planFingerprint: null,
      snapshotId: 'snap-B',
    };

    state = selectedChartReducer(state, {
      type: 'SELECT_CONTEXT',
      binding: bindingB,
      asOfMs: 2000,
    });
    expect(state.generation).toBe(2);
    expect(state.binding?.id).toBe('order-2');

    // Stale response from A arrives (generation 1) - reducer should ignore stale bundle load
    state = selectedChartReducer(state, {
      type: 'LOAD_EVIDENCE_SUCCESS',
      generation: 1, // old generation
      bundle: {
        selectionKey: chartSelectionKey(bindingA),
        snapshotId: 'snap-A',
        symbol: 'XAUUSDT',
        timeframe: '15M',
        direction: 'LONG',
        swings: [],
        structureEvents: [],
        poiZones: [],
      },
    });

    // Evidence must still be null because generation 1 was discarded
    expect(state.bundle).toBeNull();

    // Correct response for B arrives (generation 2)
    state = selectedChartReducer(state, {
      type: 'LOAD_EVIDENCE_SUCCESS',
      generation: 2,
      bundle: {
        selectionKey: chartSelectionKey(bindingB),
        snapshotId: 'snap-B',
        symbol: 'XAUUSDT',
        timeframe: '15M',
        direction: 'SHORT',
        swings: [],
        structureEvents: [],
        poiZones: [],
      },
    });

    expect(state.bundle).not.toBeNull();
    expect(state.bundle?.direction).toBe('SHORT');
  });

  it('rejects mismatched bundles when validating selected bundle against binding', () => {
    const binding: ChartBinding = {
      source: 'SETUP',
      id: 'setup-42',
      instanceId: 'inst-42',
      revision: 1,
      symbol: 'XAUUSDT',
      direction: 'LONG',
      planFingerprint: 'LONG:2650.00:2640.00:2670.00',
      snapshotId: 'snap-42',
    };

    const matchingBundle = {
      binding,
      structure_events: [],
      poi_zones: [],
    };

    const wrongDirectionBundle = {
      binding: { ...binding, direction: 'SHORT' },
      structure_events: [],
      poi_zones: [],
    };

    expect(validateSelectedBundle(matchingBundle, binding).is_valid).toBe(true);
    expect(validateSelectedBundle(wrongDirectionBundle, binding).is_valid).toBe(false);
    expect(validateSelectedBundle(null, binding).is_valid).toBe(false);
  });
});
