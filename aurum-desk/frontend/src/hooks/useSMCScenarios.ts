import { useState, useEffect, useCallback, useRef, useReducer } from 'react';
import type { SMCScenarioView } from '../types/smcScenario';
import { api } from '../api/client';
import { wsClient } from '../services/wsClient';
import {
  applyScenarioUpdate,
  parseScenarioUpdated,
  type SeenDigests,
  type ScenarioEventPayload,
} from '../utils/scenarioEvents';

const POLL_FALLBACK_MS = 8000; // only while the WebSocket is down (bounded 5-10 s)
const RESYNC_DEBOUNCE_MS = 400;

export interface ScenarioState {
  scenarios: SMCScenarioView[];
  seenDigests: SeenDigests;
  eventEpoch: number;
  resyncNonce: number;
  dataSynced: boolean;
  committedGeneration: number;
}

export type ScenarioAction =
  | { type: 'SET_SCENARIOS'; scenarios: SMCScenarioView[]; generation: number; epochAtStart?: number }
  | { type: 'APPLY_UPDATE'; payload: ScenarioEventPayload }
  | { type: 'SET_UNSYNCED' }
  | { type: 'RESET_STATE' };

export function scenarioReducer(state: ScenarioState, action: ScenarioAction): ScenarioState {
  switch (action.type) {
    case 'SET_SCENARIOS': {
      // Discard superseded responses from earlier generations
      if (action.generation < state.committedGeneration) {
        return state;
      }
      // If an event arrived while this GET was in-flight, do not regress or claim synced!
      if (action.epochAtStart !== undefined && action.epochAtStart !== state.eventEpoch) {
        return {
          ...state,
          dataSynced: false,
          resyncNonce: state.resyncNonce + 1,
        };
      }
      return {
        ...state,
        scenarios: action.scenarios,
        dataSynced: true,
        committedGeneration: action.generation,
      };
    }
    case 'APPLY_UPDATE': {
      const result = applyScenarioUpdate(state.scenarios, action.payload, state.seenDigests);
      return {
        ...state,
        scenarios: result.ignored ? state.scenarios : result.scenarios,
        seenDigests: result.nextSeenDigests,
        resyncNonce: state.resyncNonce + (result.resync ? 1 : 0),
        dataSynced: result.resync ? false : state.dataSynced,
        eventEpoch: result.ignored ? state.eventEpoch : state.eventEpoch + 1,
      };
    }
    case 'SET_UNSYNCED': {
      return {
        ...state,
        dataSynced: false,
      };
    }
    case 'RESET_STATE': {
      return {
        scenarios: [],
        seenDigests: new Map(),
        eventEpoch: 0,
        resyncNonce: 0,
        dataSynced: false,
        committedGeneration: 0,
      };
    }
    default:
      return state;
  }
}

export function useSMCScenarios(symbol: string = 'XAUUSDT') {
  const [state, dispatch] = useReducer(scenarioReducer, {
    scenarios: [],
    seenDigests: new Map(),
    eventEpoch: 0,
    resyncNonce: 0,
    dataSynced: false,
    committedGeneration: 0,
  });

  const [isLoading, setIsLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<number>(0);
  const [wsLive, setWsLive] = useState<boolean>(() => wsClient.isOpen());
  const isMountedRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  const requestGenRef = useRef(0);
  const stateEpochRef = useRef(0);
  stateEpochRef.current = state.eventEpoch;
  const activeDebounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchScenarios = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const generation = ++requestGenRef.current;
    const epochAtStart = stateEpochRef.current;

    try {
      setIsLoading(true);
      const data = await api.getScenarios(symbol, 5, controller.signal);
      if (isMountedRef.current && !controller.signal.aborted && Array.isArray(data)) {
        dispatch({ type: 'SET_SCENARIOS', scenarios: data, generation, epochAtStart });
        setLastUpdated(Date.now());
      }
    } catch (err: any) {
      if (err?.name !== 'CanceledError' && err?.code !== 'ERR_CANCELED') {
        console.warn('Failed to fetch SMC scenarios:', err);
      }
    } finally {
      if (isMountedRef.current && abortRef.current === controller) {
        setIsLoading(false);
      }
    }
  }, [symbol]);

  // Initial load + WS status tracking. Reconnect sets dataSynced=false and re-fetches inventory.
  useEffect(() => {
    isMountedRef.current = true;
    dispatch({ type: 'SET_UNSYNCED' });
    void fetchScenarios();
    setWsLive(wsClient.isOpen());

    const unsubscribeStatus = wsClient.subscribeStatus((open) => {
      if (!isMountedRef.current) return;
      setWsLive(open);
      if (open) {
        dispatch({ type: 'SET_UNSYNCED' });
        void fetchScenarios();
      }
    });

    return () => {
      isMountedRef.current = false;
      abortRef.current?.abort();
      unsubscribeStatus();
    };
  }, [symbol, fetchScenarios]);

  // Polling fallback ONLY while the socket is down; cancelled immediately when live.
  useEffect(() => {
    if (wsLive) return;
    const interval = setInterval(() => void fetchScenarios(), POLL_FALLBACK_MS);
    return () => clearInterval(interval);
  }, [wsLive, fetchScenarios]);

  // Real events arrive wrapped as DOMAIN_EVENT { event: { event_type, payload } }.
  useEffect(() => {
    const unsubscribe = wsClient.subscribe((msg: any) => {
      const parsed = parseScenarioUpdated(msg, symbol);
      if (parsed.kind !== 'UPDATE' || !isMountedRef.current) return;
      dispatch({ type: 'APPLY_UPDATE', payload: parsed.payload });
    });
    return () => {
      unsubscribe();
    };
  }, [symbol]);

  // Committed resync flag / nonce triggers debounced GET (coalescing burst gaps).
  useEffect(() => {
    if (state.resyncNonce === 0) return;
    if (activeDebounceTimerRef.current !== null) {
      clearTimeout(activeDebounceTimerRef.current);
    }
    activeDebounceTimerRef.current = setTimeout(() => {
      if (isMountedRef.current) {
        void fetchScenarios();
      }
    }, RESYNC_DEBOUNCE_MS);

    return () => {
      if (activeDebounceTimerRef.current !== null) {
        clearTimeout(activeDebounceTimerRef.current);
        activeDebounceTimerRef.current = null;
      }
    };
  }, [state.resyncNonce, fetchScenarios]);

  return {
    scenarios: state.scenarios,
    primaryScenario: state.scenarios[0] || null,
    summaryVi: state.scenarios[0]?.summary_vi,
    isLoading,
    isSynced: wsLive && state.dataSynced,
    dataSynced: state.dataSynced,
    transportConnected: wsLive,
    lastUpdated,
    refresh: fetchScenarios,
  };
}
