/**
 * frontend/src/hooks/useObservationDashboard.ts
 * AURUM V17.6 — Autonomous Observation & Decision Intelligence Data Hook.
 *
 * Responsibilities:
 * - Bootstraps authoritative current snapshot from /api/v1/observation/summary.
 * - Subscribes to real-time events via wsClient.
 * - Manages time window selection (Today, 7D, 14D, 30D) and timezone formatting (VN vs NY).
 * - Implements safe reconciliation polling with visibility detection and AbortController.
 * - Fail-closed: Never exposes undefined or placeholder numbers as verified facts.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { api, extractErrorMessage } from '../api/client';
import { wsClient } from '../services/wsClient';
import type { ObservationDashboardSnapshot } from '../types/observation';

export type ObservationTimeRange = 'TODAY' | '7D' | '14D' | '30D';
export type ObservationTimezone = 'VN' | 'NY';

export interface UseObservationDashboardOptions {
  symbol?: string;
  scope?: string;
  runId?: string | null;
  pollingIntervalMs?: number;
  enabled?: boolean;
}

export interface UseObservationDashboardResult {
  snapshot: ObservationDashboardSnapshot | null;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  timeRange: ObservationTimeRange;
  timezone: ObservationTimezone;
  lastUpdatedMs: number | null;
  setTimeRange: (range: ObservationTimeRange) => void;
  setTimezone: (tz: ObservationTimezone) => void;
  refresh: () => Promise<void>;
  formatTimestamp: (ts?: number | null) => string;
}

export function useObservationDashboard({
  symbol = 'XAUUSDT',
  scope = 'CG01',
  runId = null,
  pollingIntervalMs = 20000,
  enabled = true,
}: UseObservationDashboardOptions = {}): UseObservationDashboardResult {
  const [snapshot, setSnapshot] = useState<ObservationDashboardSnapshot | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [timeRange, setTimeRange] = useState<ObservationTimeRange>('TODAY');
  const [timezone, setTimezone] = useState<ObservationTimezone>('VN');
  const [lastUpdatedMs, setLastUpdatedMs] = useState<number | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  const isMountedRef = useRef<boolean>(true);

  // Calculate window start_ms based on timeRange
  const getWindowTimestamps = useCallback((range: ObservationTimeRange): { startMs: number; endMs: number } => {
    const now = Date.now();
    let startMs = now - 24 * 3600 * 1000;

    if (range === 'TODAY') {
      const d = new Date(now);
      d.setHours(0, 0, 0, 0);
      startMs = d.getTime();
    } else if (range === '7D') {
      startMs = now - 7 * 24 * 3600 * 1000;
    } else if (range === '14D') {
      startMs = now - 14 * 24 * 3600 * 1000;
    } else if (range === '30D') {
      startMs = now - 30 * 24 * 3600 * 1000;
    }

    return { startMs, endMs: now };
  }, []);

  // Fetch Snapshot
  const fetchSnapshot = useCallback(
    async (isManualRefresh = false) => {
      if (!enabled) return;

      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      const controller = new AbortController();
      abortControllerRef.current = controller;

      if (isManualRefresh) {
        setRefreshing(true);
      }

      try {
        const { startMs, endMs } = getWindowTimestamps(timeRange);
        const data = await api.getObservationSummary(
          {
            symbol,
            scope,
            run_id: runId,
            start_ms: startMs,
            end_ms: endMs,
            now_ms: Date.now(),
          },
          controller.signal
        );

        if (isMountedRef.current) {
          setSnapshot(data);
          setError(null);
          setLastUpdatedMs(Date.now());
          setLoading(false);
          setRefreshing(false);
        }
      } catch (err: any) {
        if (err?.name === 'CanceledError' || err?.name === 'AbortError') {
          return;
        }
        if (isMountedRef.current) {
          setError(extractErrorMessage(err));
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [enabled, symbol, scope, runId, timeRange, getWindowTimestamps]
  );

  // Initial fetch and on parameter changes
  useEffect(() => {
    isMountedRef.current = true;
    setLoading(true);
    fetchSnapshot(false);

    return () => {
      isMountedRef.current = false;
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [fetchSnapshot]);

  // Periodic polling fallback
  useEffect(() => {
    if (!enabled || pollingIntervalMs <= 0) return;

    const interval = setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState === 'visible') {
        fetchSnapshot(false);
      }
    }, pollingIntervalMs);

    return () => clearInterval(interval);
  }, [enabled, pollingIntervalMs, fetchSnapshot]);

  // Debounced snapshot refresh for real-time WebSocket events (OBS-08)
  const debounceTimerRef = useRef<any>(null);

  useEffect(() => {
    if (!enabled) return;

    const extractEventType = (raw: any): string | null => {
      if (!raw || typeof raw !== 'object') return null;
      const candidates = [
        raw.type,
        raw.event_type,
        raw.event?.event_type,
        raw.event?.type,
        raw.payload?.event_type,
        raw.payload?.type,
        raw.data?.event_type,
        raw.data?.type,
      ];
      for (const c of candidates) {
        if (typeof c === 'string' && c.trim().length > 0) {
          return c.trim();
        }
      }
      return null;
    };

    const isRelevantObservationEvent = (eventType: string | null): boolean => {
      if (!eventType) return false;
      const lower = eventType.toLowerCase();
      const upper = eventType.toUpperCase();
      return (
        lower.startsWith('order.') ||
        lower.startsWith('cycle.') ||
        lower.startsWith('opportunity.') ||
        lower.startsWith('feed.') ||
        lower.startsWith('trade.') ||
        upper === 'CG01_EVALUATION' ||
        upper === 'CANDLE_CLOSED' ||
        upper.startsWith('ORDER_') ||
        upper.startsWith('CYCLE_') ||
        upper.startsWith('OPPORTUNITY_') ||
        upper.startsWith('FEED_') ||
        upper.startsWith('TRADE_')
      );
    };

    const unsubscribe = wsClient.subscribe((event: any) => {
      const eventType = extractEventType(event);
      if (isRelevantObservationEvent(eventType)) {
        if (debounceTimerRef.current) {
          clearTimeout(debounceTimerRef.current);
        }
        debounceTimerRef.current = setTimeout(() => {
          if (isMountedRef.current) {
            fetchSnapshot(false);
          }
        }, 300);
      }
    });

    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
      if (typeof unsubscribe === 'function') {
        unsubscribe();
      }
    };
  }, [enabled, fetchSnapshot]);

  const refresh = useCallback(async () => {
    await fetchSnapshot(true);
  }, [fetchSnapshot]);

  const formatTimestamp = useCallback(
    (ts?: number | null): string => {
      if (!ts || ts <= 0) return 'N/A';
      try {
        const date = new Date(ts);
        const tzString = timezone === 'VN' ? 'Asia/Ho_Chi_Minh' : 'America/New_York';
        const tzLabel = timezone === 'VN' ? 'ICT' : 'ET';

        const formatted = new Intl.DateTimeFormat('vi-VN', {
          timeZone: tzString,
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: false,
        }).format(date);

        return `${formatted} (${tzLabel})`;
      } catch {
        return new Date(ts).toLocaleString();
      }
    },
    [timezone]
  );

  return {
    snapshot,
    loading,
    refreshing,
    error,
    timeRange,
    timezone,
    lastUpdatedMs,
    setTimeRange,
    setTimezone,
    refresh,
    formatTimestamp,
  };
}
