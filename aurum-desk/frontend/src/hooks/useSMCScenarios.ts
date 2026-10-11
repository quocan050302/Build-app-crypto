import { useState, useEffect, useCallback, useRef } from 'react';
import type { SMCScenarioView } from '../types/smcScenario';
import { api } from '../api/client';
import { wsClient } from '../services/wsClient';

export function useSMCScenarios(symbol: string = 'XAUUSDT') {
  const [scenarios, setScenarios] = useState<SMCScenarioView[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<number>(0);
  const isMountedRef = useRef(true);

  const fetchScenarios = useCallback(async () => {
    try {
      setIsLoading(true);
      const data = await api.getScenarios(symbol, 5);
      if (isMountedRef.current && Array.isArray(data)) {
        setScenarios(data);
        setLastUpdated(Date.now());
      }
    } catch (err) {
      console.warn('Failed to fetch SMC scenarios:', err);
    } finally {
      if (isMountedRef.current) {
        setIsLoading(false);
      }
    }
  }, [symbol]);

  useEffect(() => {
    isMountedRef.current = true;
    fetchScenarios();

    // Periodic polling fallback (every 8 seconds)
    const interval = setInterval(() => {
      fetchScenarios();
    }, 8000);

    // WebSocket listener for real-time scenario updates
    const unsubscribe = wsClient.subscribe((msg: any) => {
      if (msg?.type === 'scenario.updated' && msg.payload) {
        const updated = msg.payload;
        if (updated.symbol === symbol && isMountedRef.current) {
          setScenarios((prev) => {
            const idx = prev.findIndex((s) => s.scenario_id === updated.scenario_id);
            if (idx >= 0) {
              const next = [...prev];
              next[idx] = { ...next[idx], ...updated };
              return next;
            }
            return [updated, ...prev].slice(0, 5);
          });
          setLastUpdated(Date.now());
        }
      }
    });

    return () => {
      isMountedRef.current = false;
      clearInterval(interval);
      unsubscribe();
    };
  }, [symbol, fetchScenarios]);

  return {
    scenarios,
    primaryScenario: scenarios[0] || null,
    summaryVi: scenarios[0]?.summary_vi,
    isLoading,
    lastUpdated,
    refresh: fetchScenarios,
  };
}
