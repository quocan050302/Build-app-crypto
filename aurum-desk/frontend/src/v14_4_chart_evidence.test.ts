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

  const serverBundle = (overrides: Record<string, unknown> = {}, bindingOverrides: Record<string, unknown> = {}) => ({
    binding: {
      source: 'SETUP',
      id: 'setup-42',
      instance_id: 'inst-42',
      revision: 1,
      symbol: 'XAUUSDT',
      direction: 'LONG',
      plan_fingerprint: 'fp-scenario-1',
      snapshot_id: 'snap-42',
      ...bindingOverrides,
    },
    as_of_ms: 1710000000000,
    evidence_cutoff_ms: 1710000000000,
    structure_events: [],
    poi_zones: [],
    ...overrides,
  });

  it('validates a server bundle against the requested binding field by field', () => {
    const binding: ChartBinding = {
      source: 'SETUP',
      id: 'setup-42',
      instanceId: 'inst-42',
      revision: 1,
      symbol: 'XAUUSDT',
      direction: 'LONG',
      planFingerprint: null, // not yet known by the client: not compared
      snapshotId: null,      // not yet known: bound afterwards from the returned ref
    };

    expect(validateSelectedBundle(serverBundle(), binding).is_valid).toBe(true);
    expect(validateSelectedBundle(null, binding).is_valid).toBe(false);
    expect(validateSelectedBundle({ as_of_ms: 1 }, binding).reason).toBe('BINDING_MISSING');
    expect(validateSelectedBundle(serverBundle({}, { direction: 'SHORT' }), binding).reason).toBe('DIRECTION_MISMATCH');
    expect(validateSelectedBundle(serverBundle({}, { id: 'setup-43' }), binding).reason).toBe('ID_MISMATCH');
    expect(validateSelectedBundle(serverBundle({}, { instance_id: 'inst-99' }), binding).reason).toBe('INSTANCE_MISMATCH');
    expect(validateSelectedBundle(serverBundle({}, { revision: 2 }), binding).reason).toBe('REVISION_MISMATCH');
    expect(validateSelectedBundle(serverBundle({}, { symbol: 'BTCUSDT' }), binding).reason).toBe('SYMBOL_MISMATCH');
    expect(validateSelectedBundle(serverBundle({}, { source: 'ORDER' }), binding).reason).toBe('SOURCE_MISMATCH');
  });

  it('compares a non-null requested snapshot / fingerprint and rejects evidence after the cutoff', () => {
    const binding: ChartBinding = {
      source: 'SCENARIO', id: 'setup-42', instanceId: null, revision: null, symbol: 'XAUUSDT', direction: null,
      planFingerprint: 'fp-other', snapshotId: 'snap-other',
    };
    expect(validateSelectedBundle(serverBundle({}, { source: 'SCENARIO' }), binding).reason).toBe('PLAN_FINGERPRINT_MISMATCH');
    expect(validateSelectedBundle(serverBundle({}, { source: 'SCENARIO', plan_fingerprint: 'fp-other' }), binding).reason).toBe('SNAPSHOT_MISMATCH');

    const open: ChartBinding = { ...binding, planFingerprint: null, snapshotId: null };
    const future = serverBundle(
      { structure_events: [{ id: 'e1', known_at_ms: 1710000000001 }] },
      { source: 'SCENARIO' }
    );
    expect(validateSelectedBundle(future, open).reason).toBe('EVIDENCE_AFTER_CUTOFF');
    expect(validateSelectedBundle(serverBundle({ evidence_cutoff_ms: 1710000000001 }, { source: 'SCENARIO' }), open).reason).toBe('CUTOFF_INVALID');
    expect(validateSelectedBundle(serverBundle({ as_of_ms: 0 }, { source: 'SCENARIO' }), open).reason).toBe('AS_OF_INVALID');
  });
});