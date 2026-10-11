import { useReducer, useRef, useCallback } from 'react';
import { chartSelectionKey } from '../types/smcScenario';
import type { ChartBinding, ChartEvidenceBundleV144 } from '../types/smcScenario';
import { api } from '../api/client';
import { buildChartEvidenceBundle } from '../utils/chartEvidence';
import type { ChartEvidenceBundle } from '../utils/chartEvidence';

export interface UseSelectedChartState {
  binding: ChartBinding | null;
  bundle: ChartEvidenceBundle | null;
  rawBundle: ChartEvidenceBundleV144 | null;
  isLoading: boolean;
  error: string | null;
  generation: number;
  asOfMs?: number;
}

export type SelectedChartAction =
  | { type: 'SELECT_CONTEXT'; binding: ChartBinding | null; asOfMs?: number }
  | { type: 'LOAD_EVIDENCE_SUCCESS'; generation: number; bundle: ChartEvidenceBundle | null; rawBundle?: any }
  | { type: 'LOAD_EVIDENCE_ERROR'; generation: number; error: string }
  | { type: 'CLEAR_SELECTION' };

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
        bundle: null, // Clear evidence of previous trade to prevent mismatch
        rawBundle: null,
        isLoading: Boolean(action.binding),
        error: null,
        generation: state.generation + 1,
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
        generation: state.generation + 1,
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
      dispatch({ type: 'CLEAR_SELECTION' });
      return;
    }

    const currentGen = ++generationRef.current;
    const controller = new AbortController();
    abortControllerRef.current = controller;

    const selKey = chartSelectionKey(binding);
    dispatch({ type: 'SELECT_CONTEXT', binding });

    try {
      let rawData: any = null;
      if (binding.source === 'SETUP') {
        rawData = await api.getSetupChartEvidence(
          binding.id,
          {
            setup_instance_id: binding.instanceId || undefined,
            expected_revision: binding.revision || undefined,
            plan_fingerprint: binding.planFingerprint || undefined,
            signal: controller.signal,
          }
        );
      } else if (binding.source === 'ORDER') {
        rawData = await api.getOrderChartEvidence(binding.id, controller.signal);
      } else if (binding.source === 'SCENARIO') {
        if (binding.snapshotId) {
          rawData = await api.getScenarioSnapshot(binding.id, binding.snapshotId, controller.signal);
        }
      }

      if (generationRef.current !== currentGen) {
        return;
      }

      const bundle = buildChartEvidenceBundle(rawData, selKey);
      dispatch({
        type: 'LOAD_EVIDENCE_SUCCESS',
        generation: currentGen,
        bundle,
        rawBundle: rawData,
      });
    } catch (err: any) {
      if (err.name === 'AbortError' || err.code === 'ERR_CANCELED') {
        return;
      }
      if (generationRef.current !== currentGen) {
        return;
      }
      dispatch({
        type: 'LOAD_EVIDENCE_ERROR',
        generation: currentGen,
        error: err.message || 'Lỗi khi tải bằng chứng biểu đồ',
      });
    }
  }, []);

  const clearSelection = useCallback(() => {
    selectContext(null);
  }, [selectContext]);

  return {
    binding: state.binding,
    bundle: state.bundle,
    rawBundle: state.rawBundle,
    isLoading: state.isLoading,
    error: state.error,
    selectContext,
    clearSelection,
  };
}
