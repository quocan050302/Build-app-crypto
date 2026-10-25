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

/**
 * Calculates Unix timestamp for 00:00:00 today in the target timezone (FIX-11 & PART 100-105).
 * DST-aware for America/New_York; strictly 00:00:00 local time for Asia/Ho_Chi_Minh.
 */
export function getTodayMidnightMs(nowMs: number, tz: ObservationTimezone): number {
  const timeZone = tz === 'VN' ? 'Asia/Ho_Chi_Minh' : 'America/New_York';
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  });

  const localParts = (instantMs: number) => {
    const parts = formatter.formatToParts(new Date(instantMs));
    const numbers: Record<string, number> = {};
    for (const part of parts) {
      if (['year', 'month', 'day', 'hour', 'minute', 'second'].includes(part.type)) {
        numbers[part.type] = Number(part.value);
      }
    }
    return numbers;
  };

  const currentLocalDate = localParts(nowMs);
  // Interpret the selected local date at midnight, not the elapsed wall-clock
  // seconds since midnight (a DST-transition day can be 23 or 25 hours long).
  const targetWallUtcMs = Date.UTC(
    currentLocalDate.year,
    currentLocalDate.month - 1,
    currentLocalDate.day,
  );
  let estimate = targetWallUtcMs;
  for (let attempt = 0; attempt < 4; attempt++) {
    const wall = localParts(estimate);
    const actualWallAsUtcMs = Date.UTC(
      wall.year, wall.month - 1, wall.day, wall.hour % 24,
      wall.minute, wall.second,
    );
    const correction = targetWallUtcMs - actualWallAsUtcMs;
    if (correction === 0) return estimate;
    estimate += correction;
  }
  return estimate;
}

/**
 * Unpacks the true semantic event type from direct and nested envelopes like DOMAIN_EVENT (FIX-05 & PART 094).
 */
export function extractObservationEventType(raw: unknown): string | null {
  if (!raw || typeof raw !== 'object') return null;
  const obj = raw as Record<string, any>;

  // 1. Detect known envelopes (DOMAIN_EVENT, MESSAGE, EVENT) and inspect nested fields first
  const envelopeType = typeof obj.type === 'string' ? obj.type.trim() : null;
  const isEnvelope = envelopeType === 'DOMAIN_EVENT' || envelopeType === 'MESSAGE' || envelopeType === 'EVENT';

  if (isEnvelope) {
    const nested =
      obj.event?.event_type ||
      obj.event?.type ||
      obj.payload?.event_type ||
      obj.payload?.type ||
      obj.data?.event_type ||
      obj.data?.type;
    if (typeof nested === 'string' && nested.trim().length > 0) {
      return nested.trim();
    }
  }

  // 2. Direct nested candidates
  const nestedCandidate =
    obj.event?.event_type ||
    obj.event?.type ||
    obj.payload?.event_type ||
    obj.payload?.type ||
    obj.data?.event_type ||
    obj.data?.type;
  if (typeof nestedCandidate === 'string' && nestedCandidate.trim().length > 0) {
    return nestedCandidate.trim();
  }

  // 3. Fall back to top-level event_type or type
  if (typeof obj.event_type === 'string' && obj.event_type.trim().length > 0) {
    return obj.event_type.trim();
  }
  if (typeof obj.type === 'string' && obj.type.trim().length > 0) {
    return obj.type.trim();
  }

  return null;
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
  const requestSeqRef = useRef<number>(0);

  // Calculate window start_ms based on timeRange and timezone (FIX-11)
  const getWindowTimestamps = useCallback(
    (range: ObservationTimeRange, tz: ObservationTimezone): { startMs: number; endMs: number } => {
      const now = Date.now();
      let startMs = now - 24 * 3600 * 1000;

      if (range === 'TODAY') {
        startMs = getTodayMidnightMs(now, tz);
      } else if (range === '7D') {
        startMs = now - 7 * 24 * 3600 * 1000;
      } else if (range === '14D') {
        startMs = now - 14 * 24 * 3600 * 1000;
      } else if (range === '30D') {
        startMs = now - 30 * 24 * 3600 * 1000;
      }

      return { startMs, endMs: now };
    },
    []
  );

  // Fetch Snapshot with sequence guard (FIX-16)
  const fetchSnapshot = useCallback(
    async (isManualRefresh = false) => {
      if (!enabled) return;

      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      const controller = new AbortController();
      abortControllerRef.current = controller;
      const seq = ++requestSeqRef.current;

      if (isManualRefresh) {
        setRefreshing(true);
      }

      try {
        const { startMs, endMs } = getWindowTimestamps(timeRange, timezone);
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

        if (isMountedRef.current && seq === requestSeqRef.current) {
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
        if (isMountedRef.current && seq === requestSeqRef.current) {
          setError(extractErrorMessage(err));
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [enabled, symbol, scope, runId, timeRange, timezone, getWindowTimestamps]
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
      const eventType = extractObservationEventType(event);
      if (isRelevantObservationEvent(eventType)) {
        // Run-aware filtering (FIX-05 & PART 097):
        // If event specifies a run_id and current dashboard is scoped to a different runId,
        // ignore non-global events.
        const eventRunId = event?.run_id || event?.event?.run_id || event?.payload?.run_id;
        if (runId && eventRunId && eventRunId !== runId) {
          const isGlobal =
            eventType!.startsWith('feed.') ||
            eventType!.startsWith('FEED_') ||
            eventType === 'CANDLE_CLOSED';
          if (!isGlobal) {
            return;
          }
        }

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
