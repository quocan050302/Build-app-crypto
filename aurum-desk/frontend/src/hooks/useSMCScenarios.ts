import { useState, useEffect, useCallback, useRef } from 'react';
import type { SMCScenarioView } from '../types/smcScenario';
import { api } from '../api/client';
import { wsClient } from '../services/wsClient';
import { applyScenarioUpdate, parseScenarioUpdated } from '../utils/scenarioEvents';

const POLL_FALLBACK_MS = 8000; // only while the WebSocket is down (bounded 5-10 s)
const RESYNC_DEBOUNCE_MS = 400;

export function useSMCScenarios(symbol: string = 'XAUUSDT') {
  const [scenarios, setScenarios] = useState<SMCScenarioView[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<number>(0);
  const [wsLive, setWsLive] = useState<boolean>(() => wsClient.isOpen());
  const isMountedRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  const seenDigestsRef = useRef<Map<string, string>>(new Map());

  const fetchScenarios = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      setIsLoading(true);
      const data = await api.getScenarios(symbol, 5, controller.signal);
      if (isMountedRef.current && !controller.signal.aborted && Array.isArray(data)) {
        setScenarios(data);
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
    let resyncTimer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = wsClient.subscribe((msg: any) => {
      const parsed = parseScenarioUpdated(msg, symbol);
      if (parsed.kind !== 'UPDATE' || !isMountedRef.current) return;
      let needsResync = false;
      setScenarios((prev) => {
        const result = applyScenarioUpdate(prev, parsed.payload, seenDigestsRef.current);
        needsResync = result.resync;
        return result.ignored ? prev : result.scenarios;
      });
      setLastUpdated(Date.now());
      if (needsResync && resyncTimer === null) {
        resyncTimer = setTimeout(() => {
          resyncTimer = null;
          void fetchScenarios();
        }, RESYNC_DEBOUNCE_MS);
      }
    });
    return () => {
      if (resyncTimer !== null) clearTimeout(resyncTimer);
      unsubscribe();
    };
  }, [symbol, fetchScenarios]);

  return {
    scenarios,
    primaryScenario: scenarios[0] || null,
    summaryVi: scenarios[0]?.summary_vi,
    isLoading,
    isSynced: wsLive,
    lastUpdated,
    refresh: fetchScenarios,
  };
}
