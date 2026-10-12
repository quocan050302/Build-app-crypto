import { useReducer, useRef, useCallback, useEffect } from 'react';
import { chartSelectionKey } from '../types/smcScenario';
import type { ChartBinding, ChartEvidenceBundleV144 } from '../types/smcScenario';
import { api } from '../api/client';
import { buildChartEvidenceBundle, validateSelectedBundle } from '../utils/chartEvidence';
import type { ChartEvidenceBundle } from '../utils/chartEvidence';

/** Server-verified binding of the loaded bundle (never the client's request echoed back). */
export interface ReturnedBinding {
  source: string;
  id: string;
  instance_id?: string | null;
  revision?: number | null;
  symbol?: string;
  direction?: 'LONG' | 'SHORT' | null;
  plan_fingerprint?: string | null;
  snapshot_id?: string | null;
  digest?: string | null;
}

export interface UseSelectedChartState {
  binding: ChartBinding | null;
  returnedBinding?: ReturnedBinding | null;
  bundle: ChartEvidenceBundle | null;
  rawBundle: ChartEvidenceBundleV144 | null;
  isLoading: boolean;
  error: string | null;
  generation: number;
  asOfMs?: number;
}

export type SelectedChartAction =
  | { type: 'SELECT_CONTEXT'; binding: ChartBinding | null; asOfMs?: number; generation?: number }
  | { type: 'LOAD_EVIDENCE_SUCCESS'; generation: number; bundle: ChartEvidenceBundle | null; rawBundle?: any; returnedBinding?: ReturnedBinding | null }
  | { type: 'LOAD_EVIDENCE_ERROR'; generation: number; error: string }
  | { type: 'CLEAR_SELECTION'; generation?: number };

export const initialSelectedChartState: UseSelectedChartState = {
  binding: null,
  bundle: null,
  rawBundle: null,
  isLoading: false,
  error: null,
  generation: 0,
};

export function selectedChartReducer(
  state: UseSelectedChartState,
  action: SelectedChartAction
): UseSelectedChartState {
  switch (action.type) {
    case 'SELECT_CONTEXT':
      return {
        ...state,
        binding: action.binding,
        returnedBinding: null,
        bundle: null, // Clear evidence of previous trade to prevent mismatch
        rawBundle: null,
        isLoading: Boolean(action.binding),
        error: null,
        // the owner's generation is authoritative (StrictMode re-runs effects: two counters would drift)
        generation: action.generation ?? state.generation + 1,
        asOfMs: action.asOfMs,
      };

    case 'LOAD_EVIDENCE_SUCCESS':
      // Discard stale responses from prior generations
      if (action.generation !== state.generation) {
        return state;
      }
      return {
        ...state,
        bundle: action.bundle,
        rawBundle: action.rawBundle || null,
        returnedBinding: action.returnedBinding ?? null,
        isLoading: false,
        error: null,
      };

    case 'LOAD_EVIDENCE_ERROR':
      if (action.generation !== state.generation) {
        return state;
      }
      return {
        ...state,
        isLoading: false,
        error: action.error,
        bundle: null,
        rawBundle: null,
      };

    case 'CLEAR_SELECTION':
      return {
        ...state,
        binding: null,
        bundle: null,
        rawBundle: null,
        isLoading: false,
        error: null,
        generation: action.generation ?? state.generation + 1,
      };

    default:
      return state;
  }
}

export function useSelectedChart() {
  const [state, dispatch] = useReducer(selectedChartReducer, initialSelectedChartState);
  const abortControllerRef = useRef<AbortController | null>(null);
  const generationRef = useRef<number>(0);

  const selectContext = useCallback(async (binding: ChartBinding | null) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }

    if (!binding) {
      generationRef.current += 1;
      dispatch({ type: 'CLEAR_SELECTION', generation: generationRef.current });
      return;
    }

    const currentGen = ++generationRef.current;
    const controller = new AbortController();
    abortControllerRef.current = controller;

    const selKey = chartSelectionKey(binding);
    dispatch({ type: 'SELECT_CONTEXT', binding, generation: currentGen });

    try {
      let rawData: any = null;
      if (binding.source === 'SETUP') {
        // Identity = id + instance + revision. The scenario plan fingerprint is verified server-side; the guard's
        // eligibility fingerprint is a different hash and must never be sent here.
        rawData = await api.getSetupChartEvidence(binding.id, {
          setup_instance_id: binding.instanceId || undefined,
          expected_revision: binding.revision ?? undefined,
          signal: controller.signal,
        });
      } else if (binding.source === 'ORDER') {
        rawData = await api.getOrderChartEvidence(binding.id, controller.signal);
      } else if (binding.source === 'SCENARIO') {
        rawData = await api.getScenarioChartEvidence(binding.id, binding.snapshotId, controller.signal);
      } else {
        // DRAFT carries its own edited plan: no certified evidence is attached.
        dispatch({ type: 'LOAD_EVIDENCE_SUCCESS', generation: currentGen, bundle: null, rawBundle: null });
        return;
      }

      if (generationRef.current !== currentGen) {
        return;
      }

      const verdict = validateSelectedBundle(rawData, binding);
      if (!verdict.is_valid) {
        dispatch({
          type: 'LOAD_EVIDENCE_ERROR',
          generation: currentGen,
          error: `Bằng chứng không khớp lựa chọn (${verdict.reason})`,
        });
        return;
      }

      const bundle = buildChartEvidenceBundle(rawData, selKey);
      dispatch({
        type: 'LOAD_EVIDENCE_SUCCESS',
        generation: currentGen,
        bundle,
        rawBundle: rawData,
        returnedBinding: rawData.binding as ReturnedBinding,
      });
    } catch (err: any) {
      if (err?.name === 'AbortError' || err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED') {
        return;
      }
      if (generationRef.current !== currentGen) {
        return;
      }
      const status = err?.response?.status;
      dispatch({
        type: 'LOAD_EVIDENCE_ERROR',
        generation: currentGen,
        error: status === 409
          ? 'Phiên bản setup/kịch bản đã thay đổi — tải lại để xem bằng chứng mới'
          : err?.message || 'Lỗi khi tải bằng chứng biểu đồ',
      });
    }
  }, []);

  // Cleanup: no request may outlive the owner.
  useEffect(() => {
    return () => {
      generationRef.current += 1;
      abortControllerRef.current?.abort();
      abortControllerRef.current = null;
    };
  }, []);

  const clearSelection = useCallback(() => {
    selectContext(null);
  }, [selectContext]);

  return {
    binding: state.binding,
    returnedBinding: state.returnedBinding ?? null,
    bundle: state.bundle,
    rawBundle: state.rawBundle,
    isLoading: state.isLoading,
    error: state.error,
    selectContext,
    clearSelection,
  };
}
