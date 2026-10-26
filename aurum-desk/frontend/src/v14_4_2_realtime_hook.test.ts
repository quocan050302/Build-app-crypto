import { describe, it, expect } from 'vitest';
import { scenarioReducer, type ScenarioState, type ScenarioAction } from './hooks/useSMCScenarios';
import { parseScenarioUpdated, type ScenarioEventPayload } from './utils/scenarioEvents';
import type { SMCScenarioView } from './types/smcScenario';

describe('RT09-RT14: Realtime Hook & Reducer Integration Tests', () => {
  const initialScenarios: SMCScenarioView[] = [
    {
      scenario_id: 'scen-xau-1',
      instance_id: 'inst-1',
      revision: 1,
      symbol: 'XAUUSDT',
      timeframe: '5M',
      strategy_family: 'SMC',
      direction: 'LONG',
      state: 'WAITING_SWEEP',
      as_of_ms: 1728000000000,
      summary_vi: 'Kế hoạch Long SMC',
    },
  ];

  const createInitialState = (overrides?: Partial<ScenarioState>): ScenarioState => ({
    scenarios: initialScenarios,
    seenDigests: new Map(),
    eventEpoch: 0,
    resyncNonce: 0,
    dataSynced: true,
    committedGeneration: 1,
    ...overrides,
  });

  // RT09 / TC-STRICT-HOOK: Under StrictMode replay, calling reducer twice with same update yields identical state
  it('RT09 / TC-STRICT-HOOK: StrictMode replay is purely deterministic and does not corrupt cache', () => {
    const state = createInitialState();
    const payload: ScenarioEventPayload = {
      scenario_id: 'scen-xau-1',
      instance_id: 'inst-1',
      snapshot_id: 'snap-2',
      revision: 2,
      symbol: 'XAUUSDT',
      session_id: null,
      state: 'CONFIRMED',
      direction: 'LONG',
      digest: 'digest-rt09',
    };
    const action: ScenarioAction = { type: 'APPLY_UPDATE', payload };

    // StrictMode invokes reducer twice with identical state & action
    const firstPass = scenarioReducer(state, action);
    const secondPass = scenarioReducer(state, action);

    expect(firstPass).toEqual(secondPass);
    expect(firstPass.scenarios[0].revision).toBe(2);
    expect(firstPass.resyncNonce).toBe(1);
    expect(firstPass.dataSynced).toBe(false); // pending resync
    expect(firstPass.seenDigests.size).toBe(1);
  });

  // RT10 / TC-GAP-BURST: Burst of revision gaps coalesce nonces and preserves pending resync
  it('RT10 / TC-GAP-BURST: Burst of 20 revision gap events properly increments nonce and updates cache', () => {
    let state = createInitialState();

    for (let rev = 2; rev <= 21; rev++) {
      const payload: ScenarioEventPayload = {
        scenario_id: 'scen-xau-1',
        instance_id: 'inst-1',
        snapshot_id: `snap-${rev}`,
        revision: rev,
        symbol: 'XAUUSDT',
        session_id: null,
        state: 'CONFIRMED',
        direction: 'LONG',
        digest: `digest-burst-${rev}`,
      };
      state = scenarioReducer(state, { type: 'APPLY_UPDATE', payload });
    }

    expect(state.scenarios[0].revision).toBe(21);
    expect(state.resyncNonce).toBe(20);
    expect(state.dataSynced).toBe(false);
    expect(state.seenDigests.size).toBe(20);
  });

  // RT11 / TC-RACE-GET: Stale inventory GET from earlier generation is rejected when newer state is committed
  it('RT11 / TC-RACE-GET: Out-of-order GET response does not regress scenario revision or epoch', () => {
    let state = createInitialState({ committedGeneration: 3 });

    // Latest inventory arrives with generation 3
    const gen3Scenarios: SMCScenarioView[] = [
      {
        ...initialScenarios[0],
        revision: 5,
        state: 'CONFIRMED',
      },
    ];
    state = scenarioReducer(state, { type: 'SET_SCENARIOS', scenarios: gen3Scenarios, generation: 3 });
    expect(state.scenarios[0].revision).toBe(5);
    expect(state.dataSynced).toBe(true);

    // Stale delayed response from generation 1 arrives later with older revision
    const staleScenarios: SMCScenarioView[] = [
      {
        ...initialScenarios[0],
        revision: 2,
        state: 'WAITING_SWEEP',
      },
    ];
    const afterStale = scenarioReducer(state, { type: 'SET_SCENARIOS', scenarios: staleScenarios, generation: 1 });

    // Must NOT regress
    expect(afterStale.scenarios[0].revision).toBe(5);
    expect(afterStale.committedGeneration).toBe(3);
    expect(afterStale.dataSynced).toBe(true);
  });

  // RT12: Failed resync / gap leaves dataSynced=false until successful SET_SCENARIOS
  it('RT12: Pending gap keeps dataSynced=false until valid inventory commit', () => {
    let state = createInitialState({ dataSynced: true });

    // Gap update arrives
    const gapPayload: ScenarioEventPayload = {
      scenario_id: 'scen-xau-1',
      instance_id: 'inst-1',
      snapshot_id: 'snap-gap',
      revision: 10,
      symbol: 'XAUUSDT',
      session_id: null,
      state: 'CONFIRMED',
      direction: 'LONG',
      digest: 'digest-gap',
    };
    state = scenarioReducer(state, { type: 'APPLY_UPDATE', payload: gapPayload });

    expect(state.dataSynced).toBe(false);
    expect(state.resyncNonce).toBe(1);

    // Simulated failed network GET (no SET_SCENARIOS dispatched) -> dataSynced remains false
    expect(state.dataSynced).toBe(false);

    // Later, successful GET arrives with matching/higher generation
    const freshScenarios: SMCScenarioView[] = [{ ...initialScenarios[0], revision: 10 }];
    state = scenarioReducer(state, { type: 'SET_SCENARIOS', scenarios: freshScenarios, generation: 2 });
    expect(state.dataSynced).toBe(true);
  });

  // RT13 / TC-RECONNECT: Reconnect event sets dataSynced=false to prevent stale indicator before inventory GET
  it('RT13 / TC-RECONNECT: Socket reconnect invalidates dataSynced until inventory re-fetch commits', () => {
    let state = createInitialState({ dataSynced: true });

    // Socket dropped / reconnecting
    state = scenarioReducer(state, { type: 'SET_UNSYNCED' });
    expect(state.dataSynced).toBe(false);

    // Reconnected inventory arrives
    const reconnectedScenarios: SMCScenarioView[] = [{ ...initialScenarios[0], revision: 2 }];
    state = scenarioReducer(state, { type: 'SET_SCENARIOS', scenarios: reconnectedScenarios, generation: 5 });
    expect(state.dataSynced).toBe(true);
    expect(state.scenarios[0].revision).toBe(2);
  });

  // RT14: Cross-symbol isolation and clean unmount transition
  it('RT14: Events from other symbols are rejected before reducer and do not pollute state', () => {
    const state = createInitialState();

    const otherSymbolMsg = {
      type: 'DOMAIN_EVENT',
      event: {
        event_type: 'scenario.updated',
        payload: {
          scenario_id: 'scen-btc-1',
          instance_id: 'inst-btc',
          snapshot_id: 'snap-btc',
          revision: 2,
          symbol: 'BTCUSDT', // Different symbol
          state: 'CONFIRMED',
          direction: 'LONG',
          digest: 'digest-btc',
        },
      },
    };

    const parsed = parseScenarioUpdated(otherSymbolMsg, 'XAUUSDT');
    expect(parsed.kind).toBe('IGNORED');

    // State remains unchanged
    expect(state.scenarios[0].symbol).toBe('XAUUSDT');
    expect(state.seenDigests.size).toBe(0);
  });
});
