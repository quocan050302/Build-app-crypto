/**
 * frontend/src/hooks/useCandidateScoreDetail.ts
 * V16.39.12 Candidate Quality Score Detail Hook
 *
 * Rules 071-083:
 * - Single owner for fetching candidate score details.
 * - Prevents race conditions with generation ref and AbortController.
 * - Verifies returned orderId and expectedAssessmentId against requested target.
 * - Immediately clears detail on target change (Rule 078).
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../api/client';
import type { CandidateScoreDetail } from '../types/candidateScore';

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

export function useCandidateScoreDetail({
  orderId,
  expectedAssessmentId,
  enabled = true,
}: UseCandidateScoreDetailOptions): UseCandidateScoreDetailResult {
  const [detail, setDetail] = useState<CandidateScoreDetail | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [returnedOrderId, setReturnedOrderId] = useState<string | null>(null);

  const reqGenRef = useRef(0);

  const fetchData = useCallback(
    async (signal?: AbortSignal) => {
      if (!enabled || !orderId) {
        setDetail(null);
        setLoading(false);
        setError(null);
        setReturnedOrderId(null);
        return;
      }

      const currentGen = ++reqGenRef.current;
      setLoading(true);
      setError(null);
      // Rule 078: Clear immediately on selection change so factors from previous order don't linger
      setDetail(null);

      try {
        const res = await api.getOrderQualityScore(orderId, signal);
        if (reqGenRef.current !== currentGen) return; // Stale request, ignore

        // Some endpoints return { status: 'SUCCESS', order_id, detail: CandidateScoreDetail }
        // while direct detail object might also be returned or mocked:
        const returnedDetail: CandidateScoreDetail | null =
          res?.detail && typeof res.detail === 'object'
            ? res.detail
            : res && typeof res === 'object' && ('score' in res || 'status' in res)
              ? (res as CandidateScoreDetail)
              : null;

        const returnedOrderId = res?.order_id || returnedDetail?.order_id || orderId;

        // Rule 079, 138: Verify returned order_id matches requested
        if (returnedOrderId && returnedOrderId !== orderId) {
          setError('DETAIL_BINDING_MISMATCH');
          setLoading(false);
          return;
        }

        // Rule 079, 139: Verify returned assessment_id matches expected if specified
        if (
          expectedAssessmentId &&
          returnedDetail?.assessment_id &&
          returnedDetail.assessment_id !== expectedAssessmentId
        ) {
          setError('DETAIL_ASSESSMENT_MISMATCH');
          setLoading(false);
          return;
        }

        if (!returnedDetail && res?.status !== 'SUCCESS') {
          setError(typeof res?.detail === 'string' ? res.detail : 'FAILED_TO_LOAD_DETAIL');
          setLoading(false);
          return;
        }

        setDetail(returnedDetail);
        setReturnedOrderId(returnedOrderId);
        setLoading(false);
      } catch (err: any) {
        if (signal?.aborted || err?.name === 'AbortError' || err?.name === 'CanceledError') {
          return;
        }
        if (reqGenRef.current === currentGen) {
          setError(err?.response?.data?.detail || err?.message || 'FAILED_TO_LOAD_DETAIL');
          setLoading(false);
        }
      }
    },
    [orderId, expectedAssessmentId, enabled]
  );

  useEffect(() => {
    const controller = new AbortController();
    fetchData(controller.signal);

    return () => {
      controller.abort();
    };
  }, [fetchData]);

  const refetch = useCallback(() => {
    const controller = new AbortController();
    fetchData(controller.signal);
  }, [fetchData]);

  return {
    detail,
    loading,
    error,
    returnedOrderId,
    refetch,
  };
}
