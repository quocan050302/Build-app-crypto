import { describe, it, expect } from 'vitest';
import {
  selectedChartReducer,
  initialSelectedChartState,
} from './hooks/useSelectedChart';
import type { UseSelectedChartState, SelectedChartAction } from './hooks/useSelectedChart';
import type { ChartBinding } from './types/smcScenario';
import { applyScenarioUpdate, parseScenarioUpdated } from './utils/scenarioEvents';
import { validateSelectedBundle } from './utils/chartEvidence';

describe('V14.4.2 Selection and WS Lifecycle Unit Tests (Part 122)', () => {
  const sampleBindingA: ChartBinding = {
    source: 'SETUP',
    id: 'setup-A',
    instanceId: 'inst-A',
    revision: 1,
    symbol: 'XAUUSDT',
    direction: 'LONG',
    planFingerprint: 'plan-A',
    snapshotId: 'snap-A',
  };

  const sampleBindingB: ChartBinding = {
    source: 'SETUP',
    id: 'setup-B',
    instanceId: 'inst-B',
    revision: 1,
    symbol: 'XAUUSDT',
    direction: 'SHORT',
    planFingerprint: 'plan-B',
    snapshotId: 'snap-B',
  };

  it('SELECT_CONTEXT clears previous bundle and increments generation', () => {
    const stateWithOldBundle: UseSelectedChartState = {
      ...initialSelectedChartState,
      binding: sampleBindingA,
      bundle: { binding: sampleBindingA, events: [], zones: [] } as any,
      rawBundle: { id: 'old' } as any,
      returnedBinding: { source: 'SETUP', id: 'setup-A' },
      generation: 1,
    };

    const action: SelectedChartAction = {
      type: 'SELECT_CONTEXT',
      binding: sampleBindingB,
      generation: 2,
    };

    const nextState = selectedChartReducer(stateWithOldBundle, action);
    expect(nextState.binding).toEqual(sampleBindingB);
    expect(nextState.bundle).toBeNull();
    expect(nextState.rawBundle).toBeNull();
    expect(nextState.returnedBinding).toBeNull();
    expect(nextState.isLoading).toBe(true);
    expect(nextState.generation).toBe(2);
  });

  it('A -> B -> A race condition: stale response from prior generation is discarded', () => {
    // User selects A (gen 1), then selects B (gen 2), then selects A (gen 3)
    let state = selectedChartReducer(initialSelectedChartState, {
      type: 'SELECT_CONTEXT',
      binding: sampleBindingA,
      generation: 1,
    });
    state = selectedChartReducer(state, {
      type: 'SELECT_CONTEXT',
      binding: sampleBindingB,
      generation: 2,
    });
    state = selectedChartReducer(state, {
      type: 'SELECT_CONTEXT',
      binding: sampleBindingA,
      generation: 3,
    });

    // Response from gen 1 (stale A) arrives late:
    const staleAction: SelectedChartAction = {
      type: 'LOAD_EVIDENCE_SUCCESS',
      generation: 1,
      bundle: { binding: sampleBindingA, events: [] } as any,
      rawBundle: { version: 'stale' },
    };
    const afterStale = selectedChartReducer(state, staleAction);
    expect(afterStale.bundle).toBeNull();
    expect(afterStale.rawBundle).toBeNull();
    expect(afterStale.generation).toBe(3);

    // Response from gen 3 (current A) arrives:
    const validAction: SelectedChartAction = {
      type: 'LOAD_EVIDENCE_SUCCESS',
      generation: 3,
      bundle: { binding: sampleBindingA, events: ['valid'] } as any,
      rawBundle: { version: 'fresh' },
    };
    const afterValid = selectedChartReducer(afterStale, validAction);
    expect(afterValid.bundle).not.toBeNull();
    expect(afterValid.rawBundle).toEqual({ version: 'fresh' });
    expect(afterValid.isLoading).toBe(false);
  });

  it('CLEAR_SELECTION zeroes all bindings and evidence', () => {
    const activeState: UseSelectedChartState = {
      ...initialSelectedChartState,
      binding: sampleBindingA,
      bundle: { id: 'x' } as any,
      rawBundle: { id: 'y' } as any,
      isLoading: false,
      generation: 5,
    };

    const nextState = selectedChartReducer(activeState, {
      type: 'CLEAR_SELECTION',
      generation: 6,
    });
    expect(nextState.binding).toBeNull();
    expect(nextState.bundle).toBeNull();
    expect(nextState.rawBundle).toBeNull();
    expect(nextState.generation).toBe(6);
  });

  it('validateSelectedBundle rejects instance and revision mismatches', () => {
    const rawData = {
      binding: {
        source: 'SETUP',
        id: 'setup-A',
        instance_id: 'inst-WRONG',
        revision: 2,
        symbol: 'XAUUSDT',
        direction: 'LONG',
      },
    };

    const verdict = validateSelectedBundle(rawData, sampleBindingA);
    expect(verdict.is_valid).toBe(false);
    expect(verdict.reason).toBe('INSTANCE_MISMATCH');
  });

  it('applyScenarioUpdate handles jumped revision gap by triggering resync', () => {
    const seenDigests = new Map<string, string>();
    const currentScenarios = [
      {
        scenario_id: 'scen-1',
        instance_id: 'inst-1',
        revision: 1,
        symbol: 'XAUUSDT',
        timeframe: '5M',
        strategy_family: 'SMC',
        direction: 'LONG' as const,
        state: 'WAITING_SWEEP',
        as_of_ms: 1000,
      },
    ];

    // Jump from revision 1 to revision 4
    const jumpPayload = {
      scenario_id: 'scen-1',
      instance_id: 'inst-1',
      snapshot_id: null,
      revision: 4,
      symbol: 'XAUUSDT',
      session_id: null,
      state: 'STRUCTURE_CONFIRMED',
      direction: 'LONG' as const,
      digest: null,
    };

    const result = applyScenarioUpdate(currentScenarios, jumpPayload, seenDigests);
    // Gap detected -> resync must be requested
    expect(result.resync).toBe(true);
  });

  it('parseScenarioUpdated parses DOMAIN_EVENT envelope correctly', () => {
    const msg = {
      type: 'DOMAIN_EVENT',
      event: {
        event_type: 'scenario.updated',
        payload: {
          scenario_id: 'scen-test',
          instance_id: 'inst-test',
          symbol: 'XAUUSDT',
          revision: 2,
        },
      },
    };

    const parsed = parseScenarioUpdated(msg, 'XAUUSDT');
    expect(parsed.kind).toBe('UPDATE');
    if (parsed.kind === 'UPDATE') {
      expect(parsed.payload.scenario_id).toBe('scen-test');
      expect(parsed.payload.instance_id).toBe('inst-test');
    }
  });
});
