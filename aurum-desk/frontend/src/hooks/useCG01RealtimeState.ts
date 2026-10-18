/**
 * Realtime State Hook for CG-01 Diagnostics (V16.29).
 * Manages WebSocket subscription, initial HTTP bootstrap, out-of-order protection,
 * tie-breaking on equal timestamps, and stale detection.
 * Conforms to prompt rules 042, 091-110, 123-126.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { wsClient } from '../services/wsClient';
import { api } from '../api/client';
import {
  deriveCG01DisplayState,
  type CG01EvaluationPayload,
  type CG01DisplayState,
} from '../utils/cg01DisplayState';

export interface UseCG01RealtimeStateOptions {
  symbol?: string;
  autoBootstrap?: boolean;
}

export interface UseCG01RealtimeStateReturn {
  rawEvaluation: CG01EvaluationPayload | null;
  displayState: CG01DisplayState;
  isConnected: boolean;
  refreshSnapshot: () => Promise<void>;
}

export function useCG01RealtimeState(
  options: UseCG01RealtimeStateOptions = {}
): UseCG01RealtimeStateReturn {
  const { symbol = 'XAUUSDT', autoBootstrap = true } = options;

  const [rawEvaluation, setRawEvaluation] = useState<CG01EvaluationPayload | null>(null);
  const [isConnected, setIsConnected] = useState<boolean>(() => wsClient.isOpen());
  const [nowMs, setNowMs] = useState<number>(() => Date.now());

  // Tracking refs to manage race conditions and tie-breaks across async boundaries
  const latestAsOfMsRef = useRef<number>(0);
  const latestSequenceRef = useRef<number>(0);
  const isMountedRef = useRef<boolean>(true);

  // Lifecycle progression rank to allow overrides at equal timestamp (Rules 099-100)
  const getLifecycleRank = useCallback((stage?: string): number => {
    switch (stage) {
      case 'DEGRADED':
        return 4;
      case 'ARMED':
        return 3;
      case 'GUARD_BLOCKED':
        return 2;
      case 'EXECUTABLE':
        return 1;
      default:
        return 0;
    }
  }, []);

  const applyEvaluationUpdate = useCallback(
    (payload: CG01EvaluationPayload, serverSequence: number = 0) => {
      if (!payload || typeof payload !== 'object') return;

      const newAsOf = Number(payload.as_of_ms) || 0;
      const currentAsOf = latestAsOfMsRef.current;
      const currentSeq = latestSequenceRef.current;

      // 1. Strict out-of-order rejection (Rule 096, 098)
      if (currentAsOf > 0 && newAsOf < currentAsOf) {
        return;
      }

      // 2. Equal timestamp tie-break (Rules 099-100)
      if (newAsOf > 0 && newAsOf === currentAsOf) {
        // If server_sequence is available, prefer higher sequence
        if (serverSequence > 0 && currentSeq > 0 && serverSequence < currentSeq) {
          return;
        }

        // Check lifecycle progression (e.g. EXECUTABLE -> ARMED or GUARD_BLOCKED)
        const currentRank = getLifecycleRank(rawEvaluation?.stage);
        const newRank = getLifecycleRank(payload.stage);
        if (newRank < currentRank) {
          return;
        }
      }

      // Update refs and state
      latestAsOfMsRef.current = Math.max(latestAsOfMsRef.current, newAsOf);
      if (serverSequence > 0) {
        latestSequenceRef.current = Math.max(latestSequenceRef.current, serverSequence);
      }
      setRawEvaluation(payload);
    },
    [rawEvaluation?.stage, getLifecycleRank]
  );

  const refreshSnapshot = useCallback(async () => {
    try {
      const data = await api.getCG01Evaluation(symbol);
      if (!isMountedRef.current) return;
      if (data && typeof data === 'object') {
        applyEvaluationUpdate(data, 0);
      }
    } catch (err) {
      // Quietly log, retain existing snapshot
      console.debug('Failed to fetch CG01 evaluation snapshot:', err);
    }
  }, [symbol, applyEvaluationUpdate]);

  useEffect(() => {
    isMountedRef.current = true;

    // 1. Bootstrap snapshot from HTTP API if requested
    if (autoBootstrap) {
      refreshSnapshot();
    }

    // 2. Subscribe to WebSocket connection status changes (Rule 102)
    const unsubStatus = wsClient.subscribeStatus((open) => {
      if (!isMountedRef.current) return;
      setIsConnected(open);
      if (open) {
        // On reconnect, trigger snapshot resync (Rule 104)
        refreshSnapshot();
      }
    });

    // 3. Subscribe to incoming messages (Rules 093, 095)
    const unsubMsg = wsClient.subscribe((msg: any) => {
      if (!isMountedRef.current) return;
      if (!msg || typeof msg !== 'object') return;

      if (msg.type === 'CG01_EVALUATION') {
        // Only accept update for target symbol (Rule 042)
        if (msg.symbol && msg.symbol !== symbol) return;

        const payload = msg.payload || msg.evaluation || msg;
        const seq = Number(msg.server_sequence) || 0;
        applyEvaluationUpdate(payload, seq);
      }
    });

    // 4. Coarse freshness timer for stale detection (every 5s, single cleanup) (Rule 108)
    const timer = setInterval(() => {
      if (isMountedRef.current) {
        setNowMs(Date.now());
      }
    }, 5000);

    return () => {
      isMountedRef.current = false;
      unsubStatus();
      unsubMsg();
      clearInterval(timer);
    };
  }, [symbol, autoBootstrap, refreshSnapshot, applyEvaluationUpdate]);

  // Pure derivation of UI display state (Rules 066-070)
  const displayState = deriveCG01DisplayState(rawEvaluation, isConnected, nowMs);

  return {
    rawEvaluation,
    displayState,
    isConnected,
    refreshSnapshot,
  };
}
