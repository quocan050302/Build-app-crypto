import { useState, useEffect, useCallback, useRef, useReducer } from 'react';
import type { SMCScenarioView } from '../types/smcScenario';
import { api } from '../api/client';
import { wsClient } from '../services/wsClient';
import { applyScenarioUpdate, parseScenarioUpdated } from '../utils/scenarioEvents';

const POLL_FALLBACK_MS = 8000; // only while the WebSocket is down (bounded 5-10 s)
const RESYNC_DEBOUNCE_MS = 400;

interface ScenarioState {
  scenarios: SMCScenarioView[];
  resyncNonce: number;
}

type ScenarioAction =
  | { type: 'SET_SCENARIOS'; scenarios: SMCScenarioView[] }
  | { type: 'APPLY_UPDATE'; payload: any; seenDigests: Map<string, string> };

function scenarioReducer(state: ScenarioState, action: ScenarioAction): ScenarioState {
  switch (action.type) {
    case 'SET_SCENARIOS':
      return {
        scenarios: action.scenarios,
        resyncNonce: state.resyncNonce,
      };
    case 'APPLY_UPDATE': {
      const result = applyScenarioUpdate(state.scenarios, action.payload, action.seenDigests);
      return {
        scenarios: result.ignored ? state.scenarios : result.scenarios,
        resyncNonce: result.resync ? state.resyncNonce + 1 : state.resyncNonce,
      };
    }
    default:
      return state;
  }
}

export function useSMCScenarios(symbol: string = 'XAUUSDT') {
  const [state, dispatch] = useReducer(scenarioReducer, { scenarios: [], resyncNonce: 0 });
  const [isLoading, setIsLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<number>(0);
  const [wsLive, setWsLive] = useState<boolean>(() => wsClient.isOpen());
  const isMountedRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  const seenDigestsRef = useRef<Map<string, string>>(new Map());
  const lastHandledNonceRef = useRef(0);

  const fetchScenarios = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      setIsLoading(true);
      const data = await api.getScenarios(symbol, 5, controller.signal);
      if (isMountedRef.current && !controller.signal.aborted && Array.isArray(data)) {
        dispatch({ type: 'SET_SCENARIOS', scenarios: data });
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

  // Initial load + WS status tracking. A reconnect always re-reads the current inventory (resync).
  useEffect(() => {
    isMountedRef.current = true;
    void fetchScenarios();
    setWsLive(wsClient.isOpen());
    const unsubscribeStatus = wsClient.subscribeStatus((open) => {
      if (!isMountedRef.current) return;
      setWsLive(open);
      if (open) void fetchScenarios();
    });
    return () => {
      isMountedRef.current = false;
      abortRef.current?.abort();
      unsubscribeStatus();
    };
  }, [symbol, fetchScenarios]);

  // Polling fallback ONLY while the socket is down; cancelled the moment it recovers.
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
      dispatch({ type: 'APPLY_UPDATE', payload: parsed.payload, seenDigests: seenDigestsRef.current });
      setLastUpdated(Date.now());
    });
    return () => {
      unsubscribe();
    };
  }, [symbol]);

  // Committed resync flag / nonce triggers debounced GET
  useEffect(() => {
    if (state.resyncNonce > lastHandledNonceRef.current) {
      lastHandledNonceRef.current = state.resyncNonce;
      const resyncTimer = setTimeout(() => {
        void fetchScenarios();
      }, RESYNC_DEBOUNCE_MS);
      return () => clearTimeout(resyncTimer);
    }
  }, [state.resyncNonce, fetchScenarios]);

  return {
    scenarios: state.scenarios,
    primaryScenario: state.scenarios[0] || null,
    summaryVi: state.scenarios[0]?.summary_vi,
    isLoading,
    isSynced: wsLive,
    lastUpdated,
    refresh: fetchScenarios,
  };
}
