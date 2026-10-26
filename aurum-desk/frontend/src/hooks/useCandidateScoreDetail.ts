/**
 * frontend/src/hooks/useCandidateScoreDetail.ts
 * V16.39.13 Candidate Quality Score Detail Hook
 *
 * Rules 021-047, 061-075, 077-083, 111-137:
 * - Single owner for fetching candidate score details with AbortController and strict generation tracking.
 * - Stable structured targetKey prevents delimiter collisions and eliminates stale target bleed.
 * - Invalidation increments generation on disabled, unmount, target switch, clear, and retry.
 * - Zero fallback to requested orderId or unverified assessmentId (strict validation).
 * - Immediate visible state reset on target key switch, avoiding 1-frame factor flashes.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../api/client';
import type { CandidateScoreDetail } from '../types/candidateScore';
import { makeScoreRequestKey, validateQualityScoreResponse } from '../utils/candidateScore';

export interface UseCandidateScoreDetailOptions {
  orderId?: string | null;
  expectedAssessmentId?: string | null;
  enabled?: boolean;
}

export interface UseCandidateScoreDetailResult {
  detail: CandidateScoreDetail | null;
  loading: boolean;
  error: string | null;
  returnedOrderId: string | null;
  refetch: () => void;
}

interface DataState {
  targetKey: string | null;
  detail: CandidateScoreDetail | null;
  returnedOrderId: string | null;
  error: string | null;
}

export function useCandidateScoreDetail({
  orderId,
  expectedAssessmentId,
  enabled = true,
}: UseCandidateScoreDetailOptions): UseCandidateScoreDetailResult {
  // Rules 021-023: Stable request key. Null when disabled or orderId is empty/null.
  const currentTargetKey = enabled ? makeScoreRequestKey(orderId, expectedAssessmentId) : null;

  const [dataState, setDataState] = useState<DataState>({
    targetKey: null,
    detail: null,
    returnedOrderId: null,
    error: null,
  });
  const [loading, setLoading] = useState<boolean>(false);
  const [retryRevision, setRetryRevision] = useState<number>(0);

  // Rules 024, 027-029: Single owner ref tracking generation, active controller, and target key
  const activeRequestRef = useRef<{
    generation: number;
    controller: AbortController | null;
    targetKey: string | null;
  }>({
    generation: 0,
    controller: null,
    targetKey: null,
  });

  const invalidateActiveRequest = useCallback(() => {
    activeRequestRef.current.generation += 1;
    if (activeRequestRef.current.controller) {
      activeRequestRef.current.controller.abort();
      activeRequestRef.current.controller = null;
    }
    activeRequestRef.current.targetKey = null;
  }, []);

  // Rules 025, 030-034: Effect is the single initiator of network requests
  useEffect(() => {
    // Invalidate any in-flight request from previous render
    invalidateActiveRequest();

    if (!currentTargetKey || !orderId) {
      setLoading(false);
      setDataState({
        targetKey: null,
        detail: null,
        returnedOrderId: null,
        error: null,
      });
      return;
    }

    const currentGen = activeRequestRef.current.generation;
    const controller = new AbortController();
    activeRequestRef.current.controller = controller;
    activeRequestRef.current.targetKey = currentTargetKey;

    setLoading(true);
    // Rule 078: Reset detail immediately for new target
    setDataState({
      targetKey: currentTargetKey,
      detail: null,
      returnedOrderId: null,
      error: null,
    });

    const runFetch = async () => {
      try {
        const res = await api.getOrderQualityScore(orderId, controller.signal);

        // Rule 030-032: Strict post-await guards
        if (controller.signal.aborted) return;
        if (activeRequestRef.current.generation !== currentGen) return;
        if (activeRequestRef.current.targetKey !== currentTargetKey) return;

        // Rules 062-075: Pure validation against expected orderId and expectedAssessmentId
        const validation = validateQualityScoreResponse(res, {
          orderId,
          expectedAssessmentId,
        });

        if (!validation.valid) {
          setLoading(false);
          setDataState({
            targetKey: currentTargetKey,
            detail: null,
            returnedOrderId: null,
            error: validation.errorCode,
          });
          return;
        }

        setLoading(false);
        setDataState({
          targetKey: currentTargetKey,
          detail: validation.detail,
          returnedOrderId: validation.orderId,
          error: null,
        });
      } catch (err: any) {
        if (controller.signal.aborted || err?.name === 'AbortError' || err?.name === 'CanceledError') {
          return;
        }
        if (activeRequestRef.current.generation !== currentGen) return;

        setLoading(false);
        setDataState({
          targetKey: currentTargetKey,
          detail: null,
          returnedOrderId: null,
          error: err?.response?.data?.detail || err?.message || 'FAILED_TO_LOAD_DETAIL',
        });
      }
    };

    void runFetch();

    return () => {
      if (activeRequestRef.current.generation === currentGen) {
        invalidateActiveRequest();
      }
    };
  }, [currentTargetKey, orderId, expectedAssessmentId, retryRevision, invalidateActiveRequest]);

  // Rules 025, 026, 039: refetch only increments retryRevision for current target
  const refetch = useCallback(() => {
    if (!currentTargetKey) return;
    setRetryRevision((prev) => prev + 1);
  }, [currentTargetKey]);

  // Rule 034: Derive visible result: if state.targetKey differs from currentTargetKey, project null immediately
  const isKeyMatching = Boolean(currentTargetKey && dataState.targetKey === currentTargetKey);

  return {
    detail: isKeyMatching ? dataState.detail : null,
    loading: Boolean(currentTargetKey && loading),
    error: isKeyMatching ? dataState.error : null,
    returnedOrderId: isKeyMatching ? dataState.returnedOrderId : null,
    refetch,
  };
}
