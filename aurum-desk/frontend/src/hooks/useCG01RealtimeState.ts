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
import { extractCG01SummaryText } from '../utils/cg01ReasonText';

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
 * Checks if two CG-01 payloads are semantically identical (Rules 045-050).
 * Compares stage, POI, direction, armed order, guard blocked reason,
 * reason codes, and localized summary text.
 */
export function isCG01PayloadSemanticEqual(
  current: CG01EvaluationPayload | null,
  next: CG01EvaluationPayload
): boolean {
  if (!current) return false;
  if (current.stage !== next.stage) return false;
  if ((current.poi_id || null) !== (next.poi_id || null)) return false;
  if ((current.direction || null) !== (next.direction || null)) return false;
  if ((current.armed_order_id || null) !== (next.armed_order_id || null)) return false;
  if ((current.enabled ?? true) !== (next.enabled ?? true)) return false;
  if ((current.evaluated ?? false) !== (next.evaluated ?? false)) return false;

  const curGuard = current.guard_summary?.blocked_reason || null;
  const nextGuard = next.guard_summary?.blocked_reason || null;
  if (curGuard !== nextGuard) return false;

  // Compare reason_codes array (Rules 046, 047)
  const curReasons = current.reason_codes || [];
  const nextReasons = next.reason_codes || [];
  if (curReasons.length !== nextReasons.length) return false;
  for (let i = 0; i < curReasons.length; i++) {
    if (curReasons[i] !== nextReasons[i]) return false;
  }

  // Compare ui_summary_vi content (Rule 048)
  const curSummary = extractCG01SummaryText(current.ui_summary_vi);
  const nextSummary = extractCG01SummaryText(next.ui_summary_vi);
  if (curSummary !== nextSummary) return false;

  return true;
}

/**
 * Pure state reducer for CG-01 realtime updates (Rules 024-027, 035-052).
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

  const normalized = normalizeCG01EvaluationPayload(payload);
  const newAsOf = normalized.as_of_ms || 0;
  const currentAsOf = currentState.asOfMs;
  const currentSeq = currentState.serverSequence;

  // Initial state population
  if (!currentState.rawEvaluation) {
    return {
      rawEvaluation: normalized,
      asOfMs: newAsOf > 0 ? newAsOf : 0,
      serverSequence: serverSequence > 0 ? serverSequence : 0,
      lastSource: source,
    };
  }

  // 1. Strict out-of-order timestamp rejection (Rules 040, 069)
  if (currentAsOf > 0 && newAsOf > 0 && newAsOf < currentAsOf) {
    return currentState;
  }
  if (currentAsOf > 0 && newAsOf === 0) {
    return currentState;
  }

  // 2. Newer timestamp cycle (Rule 039)
  // Authoritative over older cycles; reset sequence ownership to this snapshot (Rule 043)
  if (newAsOf > currentAsOf) {
    return {
      rawEvaluation: normalized,
      asOfMs: newAsOf,
      serverSequence: serverSequence > 0 ? serverSequence : 0,
      lastSource: source,
    };
  }

  // 3. Equal timestamp ordering (Rules 035-042)
  if (newAsOf > 0 && newAsOf === currentAsOf) {
    // 3A. Authoritative serverSequence ordering (Rules 035-038)
    if (serverSequence > 0 && currentSeq > 0) {
      // Stale sequence rejected even if stage rank is higher (Rules 037, 058)
      if (serverSequence < currentSeq) {
        return currentState;
      }
      // Newer sequence accepted unconditionally (Rule 036)
      if (serverSequence > currentSeq) {
        return {
          rawEvaluation: normalized,
          asOfMs: currentAsOf,
          serverSequence,
          lastSource: source,
        };
      }
      // Same sequence: deduplicate if semantically identical (Rules 038, 045)
      if (isCG01PayloadSemanticEqual(currentState.rawEvaluation, normalized)) {
        return currentState;
      }
      return {
        rawEvaluation: normalized,
        asOfMs: currentAsOf,
        serverSequence,
        lastSource: source,
      };
    }

    // 3B. One side has sequence, the other does not (Rule 042)
    if (serverSequence > 0 && currentSeq === 0) {
      return {
        rawEvaluation: normalized,
        asOfMs: currentAsOf,
        serverSequence,
        lastSource: source,
      };
    }

    if (serverSequence === 0 && currentSeq > 0) {
      const currentRank = getCG01LifecycleRank(currentState.rawEvaluation.stage);
      const newRank = getCG01LifecycleRank(normalized.stage);
      if (newRank <= currentRank) {
        return currentState;
      }
    }

    // 3C. Neither has sequence (or fallback heuristic) (Rules 041, 045-050)
    if (source === 'HTTP' && currentState.lastSource === 'WS') {
      const currentRank = getCG01LifecycleRank(currentState.rawEvaluation.stage);
      const newRank = getCG01LifecycleRank(normalized.stage);
      if (newRank <= currentRank) {
        return currentState;
      }
    }

    const currentRank = getCG01LifecycleRank(currentState.rawEvaluation.stage);
    const newRank = getCG01LifecycleRank(normalized.stage);
    if (newRank < currentRank) {
      return currentState;
    }

    // If same rank, check for semantic equality before creating new state
    if (isCG01PayloadSemanticEqual(currentState.rawEvaluation, normalized)) {
      return currentState;
    }
  }

  const nextSeq = serverSequence > 0 ? serverSequence : currentSeq;
  const nextAsOf = newAsOf > 0 ? newAsOf : currentAsOf;

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
