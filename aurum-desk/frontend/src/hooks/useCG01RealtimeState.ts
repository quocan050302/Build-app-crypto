/**
 * Realtime State Hook for CG-01 Diagnostics (V16.29).
 * Manages WebSocket subscription, initial HTTP bootstrap, out-of-order protection,
 * tie-breaking on equal timestamps, and stale detection.
 * Conforms to prompt rules 021-047, 091-110, 123-126.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { wsClient } from '../services/wsClient';
import { api } from '../api/client';
import {
  deriveCG01DisplayState,
  normalizeCG01EvaluationPayload,
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

export interface CG01RealtimeInternalState {
  rawEvaluation: CG01EvaluationPayload | null;
  asOfMs: number;
  serverSequence: number;
  lastSource?: 'HTTP' | 'WS';
}

export interface CG01RealtimeUpdate {
  payload: CG01EvaluationPayload;
  serverSequence?: number;
  source?: 'HTTP' | 'WS';
}

/**
 * Lifecycle progression rank to allow overrides at equal timestamp (Rules 039, 040, 099-100).
 * DEGRADED (4) > ARMED (3) > GUARD_BLOCKED (2) > EXECUTABLE (1) > OTHER (0)
 */
export function getCG01LifecycleRank(stage?: string | null): number {
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
}

/**
 * Pure state reducer for CG-01 realtime updates (Rules 024-027, 036-042).
 * Takes current state and update, returns next state (or current state if rejected).
 * Zero side effects, completely deterministic.
 */
export function reduceCG01RealtimeUpdate(
  currentState: CG01RealtimeInternalState,
  update: CG01RealtimeUpdate
): CG01RealtimeInternalState {
  const { payload, serverSequence = 0, source = 'WS' } = update;
  if (!payload || typeof payload !== 'object') {
    return currentState;
  }

  const newAsOf = Number(payload.as_of_ms) || 0;
  const currentAsOf = currentState.asOfMs;
  const currentSeq = currentState.serverSequence;

  // 1. Strict out-of-order rejection (Rules 036, 037, 069)
  // If we already have a newer timestamp, reject older data
  if (currentAsOf > 0 && newAsOf > 0 && newAsOf < currentAsOf) {
    return currentState;
  }

  // If current state already has a valid timestamp and new payload has as_of_ms=0, reject
  if (currentAsOf > 0 && newAsOf === 0) {
    return currentState;
  }

  // 2. Equal timestamp tie-break (Rules 039, 040, 070)
  if (newAsOf > 0 && newAsOf === currentAsOf && currentState.rawEvaluation) {
    // If both have server sequence, prefer higher sequence
    if (serverSequence > 0 && currentSeq > 0 && serverSequence < currentSeq) {
      return currentState;
    }

    // Check lifecycle progression (e.g. EXECUTABLE -> ARMED, EXECUTABLE -> GUARD_BLOCKED, ARMED -> DEGRADED)
    const currentRank = getCG01LifecycleRank(currentState.rawEvaluation.stage);
    const newRank = getCG01LifecycleRank(payload.stage);
    if (newRank < currentRank) {
      return currentState;
    }

    // If identical ranks and sequence is not higher, check if identical payload (duplicate)
    if (newRank === currentRank) {
      if (serverSequence > 0 && currentSeq > 0 && serverSequence === currentSeq) {
        return currentState;
      }
      // If same stage, same POI, same armed_order_id, same guard reason, it's a duplicate
      const curPoi = currentState.rawEvaluation.poi_id || null;
      const newPoi = payload.poi_id || null;
      const curOrder = currentState.rawEvaluation.armed_order_id || null;
      const newOrder = payload.armed_order_id || null;
      const curGuard = currentState.rawEvaluation.guard_summary?.blocked_reason || null;
      const newGuard = payload.guard_summary?.blocked_reason || null;

      if (
        currentState.rawEvaluation.stage === payload.stage &&
        curPoi === newPoi &&
        curOrder === newOrder &&
        curGuard === newGuard
      ) {
        return currentState;
      }
    }

  }

  const normalized = normalizeCG01EvaluationPayload(payload);
  const nextSeq = serverSequence > 0 ? Math.max(currentSeq, serverSequence) : currentSeq;
  const nextAsOf = newAsOf > 0 ? Math.max(currentAsOf, newAsOf) : currentAsOf;

  return {
    rawEvaluation: normalized,
    asOfMs: nextAsOf,
    serverSequence: nextSeq,
    lastSource: source,
  };
}

export function useCG01RealtimeState(
  options: UseCG01RealtimeStateOptions = {}
): UseCG01RealtimeStateReturn {
  const { symbol = 'XAUUSDT', autoBootstrap = true } = options;

  const [internalState, setInternalState] = useState<CG01RealtimeInternalState>(() => ({
    rawEvaluation: null,
    asOfMs: 0,
    serverSequence: 0,
  }));
  const [isConnected, setIsConnected] = useState<boolean>(() => wsClient.isOpen());
  const [nowMs, setNowMs] = useState<number>(() => Date.now());

  // Tracking refs to manage race conditions, lifecycles, and burst events
  const latestStateRef = useRef<CG01RealtimeInternalState>(internalState);
  const isMountedRef = useRef<boolean>(true);
  const requestGenerationRef = useRef<number>(0);
  const symbolRef = useRef<string>(symbol);

  // Keep symbolRef synchronized
  symbolRef.current = symbol;

  // Handle symbol change: reset state and invalidate pending requests (Item 035)
  useEffect(() => {
    requestGenerationRef.current++;
    const resetState: CG01RealtimeInternalState = {
      rawEvaluation: null,
      asOfMs: 0,
      serverSequence: 0,
    };
    latestStateRef.current = resetState;
    setInternalState(resetState);
  }, [symbol]);

  // Stable callback for applying updates (Rule 028)
  // Dependency array is strictly empty to guarantee reference stability
  const applyEvaluationUpdate = useCallback(
    (payload: CG01EvaluationPayload, serverSequence: number = 0, source: 'HTTP' | 'WS' = 'WS') => {
      if (!payload || typeof payload !== 'object') return;

      const current = latestStateRef.current;
      const nextState = reduceCG01RealtimeUpdate(current, { payload, serverSequence, source });

      if (nextState !== current) {
        // Synchronously update ref so burst updates in the same render tick do NOT read stale closure
        latestStateRef.current = nextState;
        setInternalState(nextState);
      }
    },
    []
  );

  // Stable snapshot refresh function (Rule 029)
  // Depends only on the stable applyEvaluationUpdate
  const refreshSnapshot = useCallback(async () => {
    const currentGeneration = requestGenerationRef.current;
    const targetSymbol = symbolRef.current;

    try {
      const data = await api.getCG01Evaluation(targetSymbol);
      // Abort if unmounted or if symbol changed / request superseded (Rules 033-034)
      if (!isMountedRef.current || requestGenerationRef.current !== currentGeneration) {
        return;
      }
      if (data && typeof data === 'object') {
        applyEvaluationUpdate(data, 0, 'HTTP');
      }
    } catch (err) {
      console.debug('Failed to fetch CG01 evaluation snapshot:', err);
    }
  }, [applyEvaluationUpdate]);

  useEffect(() => {
    isMountedRef.current = true;
    const currentGeneration = requestGenerationRef.current;

    // 1. Initial HTTP bootstrap if enabled
    if (autoBootstrap) {
      refreshSnapshot();
    }

    // 2. Subscribe to WebSocket status changes (Rules 043-044)
    const unsubStatus = wsClient.subscribeStatus((open) => {
      if (!isMountedRef.current || requestGenerationRef.current !== currentGeneration) return;
      setIsConnected(open);
      if (open) {
        // Trigger snapshot resync on reconnect (Rule 044)
        refreshSnapshot();
      }
    });

    // 3. Subscribe to incoming WebSocket messages (Rules 041-042)
    const unsubMsg = wsClient.subscribe((msg: any) => {
      if (!isMountedRef.current || requestGenerationRef.current !== currentGeneration) return;
      if (!msg || typeof msg !== 'object') return;

      if (msg.type === 'CG01_EVALUATION') {
        // Only accept update for target symbol (Rule 035)
        if (msg.symbol && msg.symbol !== symbol) return;

        const payload = msg.payload || msg.evaluation || msg;
        const seq = Number(msg.server_sequence) || 0;
        applyEvaluationUpdate(payload, seq, 'WS');
      }
    });

    // 4. Coarse freshness timer for stale detection (Rule 046)
    const timer = setInterval(() => {
      if (isMountedRef.current) {
        setNowMs(Date.now());
      }
    }, 5000);

    return () => {
      isMountedRef.current = false;
      requestGenerationRef.current++;
      unsubStatus();
      unsubMsg();
      clearInterval(timer);
    };
  }, [symbol, autoBootstrap, refreshSnapshot, applyEvaluationUpdate]);

  // Pure derivation of UI display state (Rules 066-070)
  const displayState = deriveCG01DisplayState(internalState.rawEvaluation, isConnected, nowMs);

  return {
    rawEvaluation: internalState.rawEvaluation,
    displayState,
    isConnected,
    refreshSnapshot,
  };
}
