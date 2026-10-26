import { describe, it, expect } from 'vitest';
import {
  selectedChartReducer,
  initialSelectedChartState,
} from './hooks/useSelectedChart';
import type { UseSelectedChartState, SelectedChartAction } from './hooks/useSelectedChart';
import type { ChartBinding } from './types/smcScenario';
import { applyScenarioUpdate, parseScenarioUpdated, pruneSeenDigests } from './utils/scenarioEvents';
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

  describe('RT01-RT08: Deterministic and Bounded Scenario Reducer Tests', () => {
    const makeScenario = (overrides?: Partial<any>) => ({
      scenario_id: 'scen-1',
      instance_id: 'i-1',
      revision: 1,
      symbol: 'XAUUSDT',
      timeframe: '5M',
      strategy_family: 'SMC',
      direction: 'LONG' as const,
      state: 'WAITING_SWEEP',
      as_of_ms: 1000,
      ...overrides,
    });

    const makeEvent = (overrides?: Partial<any>) => ({
      scenario_id: 'scen-1',
      instance_id: 'i-1',
      snapshot_id: 'snap-1',
      revision: 2,
      symbol: 'XAUUSDT',
      session_id: null,
      state: 'CONFIRMED',
      direction: 'LONG' as const,
      digest: 'digest-2',
      ...overrides,
    });

    it('RT01 / TC-STRICT-DIGEST: same input including digest remains deterministic and pure', () => {
      const input = Object.freeze([makeScenario({ revision: 1, instance_id: 'i-1' })]);
      const event = makeEvent({ revision: 2, instance_id: 'i-1', digest: 'digest-2' });
      const cache = new Map<string, string>();
      const frozenCache = Object.freeze(cache);

      const a = applyScenarioUpdate(input, event, frozenCache);
      const b = applyScenarioUpdate(input, event, frozenCache);

      expect(cache.size).toBe(0);
      expect(input[0].revision).toBe(1);
      expect(a).toEqual(b);
      expect(a.resync).toBe(true);
      expect(a.ignored).toBe(false);
      expect(a.nextSeenDigests.size).toBe(1);
    });

    it('RT02: duplicate event with same revision and digest is ignored without mutation', () => {
      const input = [makeScenario({ revision: 2, instance_id: 'i-1' })];
      const event = makeEvent({ revision: 2, instance_id: 'i-1', digest: 'digest-2' });
      const key = `XAUUSDT|scen-1|i-1|2`;
      const cache = new Map<string, string>([[key, 'digest-2']]);

      const res = applyScenarioUpdate(input, event, cache);
      expect(res.ignored).toBe(true);
      expect(res.resync).toBe(false);
      expect(res.scenarios).toEqual(input);
      expect(cache.size).toBe(1);
    });

    it('RT03: older revision is ignored without lowering revision', () => {
      const input = [makeScenario({ revision: 5, instance_id: 'i-1' })];
      const event = makeEvent({ revision: 3, instance_id: 'i-1', digest: 'digest-old' });
      const cache = new Map<string, string>();

      const res = applyScenarioUpdate(input, event, cache);
      expect(res.ignored).toBe(true);
      expect(res.resync).toBe(false);
      expect(res.scenarios[0].revision).toBe(5);
    });

    it('RT04: jumped revision sets resync=true and updates badge', () => {
      const input = [makeScenario({ revision: 1, instance_id: 'i-1' })];
      const event = makeEvent({ revision: 5, instance_id: 'i-1', digest: 'digest-5' });
      const cache = new Map<string, string>();

      const res = applyScenarioUpdate(input, event, cache);
      expect(res.resync).toBe(true);
      expect(res.ignored).toBe(false);
      expect(res.scenarios[0].revision).toBe(5);
    });

    it('RT05: same revision with different digest triggers resync verification', () => {
      const input = [makeScenario({ revision: 2, instance_id: 'i-1' })];
      const key = `XAUUSDT|scen-1|i-1|2`;
      const cache = new Map<string, string>([[key, 'digest-original']]);
      const event = makeEvent({ revision: 2, instance_id: 'i-1', digest: 'digest-diverged' });

      const res = applyScenarioUpdate(input, event, cache);
      expect(res.resync).toBe(true);
      expect(res.ignored).toBe(false);
      expect(res.nextSeenDigests.get(key)).toBe('digest-diverged');
    });

    it('RT06: changed instance ID triggers resync', () => {
      const input = [makeScenario({ revision: 1, instance_id: 'i-OLD' })];
      const event = makeEvent({ revision: 2, instance_id: 'i-NEW', digest: 'digest-new' });

      const res = applyScenarioUpdate(input, event, new Map());
      expect(res.resync).toBe(true);
      expect(res.ignored).toBe(false);
    });

    it('RT07: cache capacity is bounded to 256 keys FIFO', () => {
      let cache: ReadonlyMap<string, string> = new Map();

      for (let i = 0; i < 300; i++) {
        cache = pruneSeenDigests(cache, `key-${i}`, `digest-${i}`, 256);
      }

      expect(cache.size).toBe(256);
      expect(cache.has('key-0')).toBe(false);
      expect(cache.has('key-43')).toBe(false);
      expect(cache.has('key-44')).toBe(true);
      expect(cache.has('key-299')).toBe(true);
    });

    it('RT08: symbol mismatch is rejected by parseScenarioUpdated', () => {
      const msg = {
        type: 'DOMAIN_EVENT',
        event: {
          event_type: 'scenario.updated',
          payload: {
            scenario_id: 'scen-1',
            instance_id: 'i-1',
            symbol: 'BTCUSDT',
            revision: 2,
          },
        },
      };

      const parsed = parseScenarioUpdated(msg, 'XAUUSDT');
      expect(parsed.kind).toBe('IGNORED');
      if (parsed.kind === 'IGNORED') {
        expect(parsed.reason).toBe('OTHER_SYMBOL');
      }
    });
  });
});
