import { useEffect, useRef, useState, useCallback } from 'react';
import { api } from '../api/client';
import type { SessionForecastsResponse } from '../types/sessionForecast';

const FORECAST_POLL_MS = 15000;

/**
 * Cached session forecasts. The endpoint is read-only (it never runs the engine), so a modest bounded poll plus
 * a refresh whenever scenarios change is enough; the timer is cleaned up on unmount.
 */
export function useSessionForecasts(symbol: string = 'XAUUSDT', refreshKey: number = 0) {
  const [data, setData] = useState<SessionForecastsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  const load = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const res = await api.getSessionForecasts(symbol, undefined, controller.signal);
      if (mountedRef.current && !controller.signal.aborted) {
        setData(res as SessionForecastsResponse);
        setError(null);
      }
    } catch (err: any) {
      if (controller.signal.aborted || err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED') return;
      if (mountedRef.current) setError('Chưa tải được dự đoán ba phiên');
    }
  }, [symbol]);

  useEffect(() => {
    mountedRef.current = true;
    void load();
    const timer = setInterval(() => void load(), FORECAST_POLL_MS);
    return () => {
      mountedRef.current = false;
      clearInterval(timer);
      abortRef.current?.abort();
    };
  }, [load]);

  useEffect(() => {
    if (refreshKey > 0) void load();
  }, [refreshKey, load]);

  return { data, error, refresh: load };
}
