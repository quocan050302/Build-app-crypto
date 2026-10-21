// @vitest-environment happy-dom
/**
 * frontend/src/__tests__/useCandidateScoreDetail.test.ts
 * V16.39.12 Candidate Quality Score Detail Hook Tests
 *
 * Rules 071-083, 137-139:
 * - Single owner fetch with AbortController and generation counter.
 * - Selection change clears prior detail immediately (Rule 078).
 * - Stale response A does not overwrite subsequent selection B (Rule 077).
 * - Server returning wrong order_id triggers DETAIL_BINDING_MISMATCH (Rule 079, 138).
 * - Assessment ID mismatch triggers binding verification error (Rule 079, 139).
 * - Target null / disabled leaves detail null without API requests.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { api } from '../api/client';
import {
  useCandidateScoreDetail,
  type UseCandidateScoreDetailOptions,
  type UseCandidateScoreDetailResult,
} from '../hooks/useCandidateScoreDetail';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;
let latestResult: UseCandidateScoreDetailResult | null = null;

function HookHarness(props: UseCandidateScoreDetailOptions) {
  latestResult = useCandidateScoreDetail(props);
  return null;
}

describe('useCandidateScoreDetail Hook (Rules 071-083, 137-139)', () => {
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    latestResult = null;
    vi.restoreAllMocks();
  });

  afterEach(() => {
    if (root) {
      act(() => {
        root?.unmount();
      });
    }
    if (container && container.parentNode) {
      container.parentNode.removeChild(container);
    }
  });

  it('remains idle and returns null when disabled or orderId is null', async () => {
    const spy = vi.spyOn(api, 'getOrderQualityScore');

    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: null, enabled: true }));
    });

    expect(spy).not.toHaveBeenCalled();
    expect(latestResult?.detail).toBeNull();
    expect(latestResult?.loading).toBe(false);
    expect(latestResult?.error).toBeNull();
  });

  it('fetches detail and populates data on valid orderId', async () => {
    const mockDetail = {
      order_id: 'ord-100',
      assessment_id: 'assess-100',
      score: 78.0,
      status: 'AVAILABLE' as const,
      factors: [
        {
          code: 'FVG_ALIGNMENT',
          group: 'STRUCTURE',
          earned_points: 18.0,
          max_points: 20.0,
          status: 'FULL',
          explanation_vi: 'Khối FVG tăng M15 hợp lệ',
        },
      ],
    };

    vi.spyOn(api, 'getOrderQualityScore').mockResolvedValue(mockDetail as any);

    await act(async () => {
      root?.render(
        React.createElement(HookHarness, {
          orderId: 'ord-100',
          expectedAssessmentId: 'assess-100',
          enabled: true,
        })
      );
    });

    expect(latestResult?.loading).toBe(false);
    expect(latestResult?.error).toBeNull();
    expect(latestResult?.returnedOrderId).toBe('ord-100');
    expect(latestResult?.detail?.score).toBe(78.0);
    expect(latestResult?.detail?.factors?.[0]?.code).toBe('FVG_ALIGNMENT');
  });

  it('prevents race conditions: slow response A does not overwrite selection B (Rules 077, 078)', async () => {
    let resolveA: (val: any) => void;
    const promiseA = new Promise((resolve) => {
      resolveA = resolve;
    });

    const mockDetailB = {
      order_id: 'ord-B',
      assessment_id: 'assess-B',
      score: 64.0,
      status: 'AVAILABLE' as const,
      factors: [],
    };

    vi.spyOn(api, 'getOrderQualityScore').mockImplementation((id: string) => {
      if (id === 'ord-A') return promiseA as any;
      return Promise.resolve(mockDetailB as any);
    });

    // 1. Select A
    await act(async () => {
      root?.render(
        React.createElement(HookHarness, {
          orderId: 'ord-A',
          enabled: true,
        })
      );
    });
    expect(latestResult?.loading).toBe(true);

    // 2. Switch to B while A is still pending
    await act(async () => {
      root?.render(
        React.createElement(HookHarness, {
          orderId: 'ord-B',
          enabled: true,
        })
      );
    });

    // B resolves immediately
    expect(latestResult?.loading).toBe(false);
    expect(latestResult?.returnedOrderId).toBe('ord-B');
    expect(latestResult?.detail?.score).toBe(64.0);

    // 3. Now slow A finally resolves
    await act(async () => {
      resolveA!({
        order_id: 'ord-A',
        assessment_id: 'assess-A',
        score: 78.0,
        status: 'AVAILABLE' as const,
        factors: [],
      });
    });

    // State MUST STILL belong to B (Rule 077)
    expect(latestResult?.returnedOrderId).toBe('ord-B');
    expect(latestResult?.detail?.score).toBe(64.0);
  });

  it('rejects response when server returns mismatched orderId (Rules 079, 138)', async () => {
    vi.spyOn(api, 'getOrderQualityScore').mockResolvedValue({
      order_id: 'wrong-order-id',
      assessment_id: 'assess-100',
      score: 78.0,
      status: 'AVAILABLE' as const,
    } as any);

    await act(async () => {
      root?.render(
        React.createElement(HookHarness, {
          orderId: 'requested-ord-id',
          enabled: true,
        })
      );
    });

    expect(latestResult?.loading).toBe(false);
    expect(latestResult?.error).toBe('DETAIL_BINDING_MISMATCH');
    expect(latestResult?.detail).toBeNull();
  });

  it('rejects response when expected assessmentId mismatches returned assessmentId (Rules 079, 139)', async () => {
    vi.spyOn(api, 'getOrderQualityScore').mockResolvedValue({
      order_id: 'ord-100',
      assessment_id: 'different-assess-id',
      score: 78.0,
      status: 'AVAILABLE' as const,
    } as any);

    await act(async () => {
      root?.render(
        React.createElement(HookHarness, {
          orderId: 'ord-100',
          expectedAssessmentId: 'expected-assess-id',
          enabled: true,
        })
      );
    });

    expect(latestResult?.loading).toBe(false);
    expect(latestResult?.error).toBe('DETAIL_ASSESSMENT_MISMATCH');
    expect(latestResult?.detail).toBeNull();
  });

  it('sets error state when network request rejects (Rule 082)', async () => {
    vi.spyOn(api, 'getOrderQualityScore').mockRejectedValue(new Error('Network connection error'));

    await act(async () => {
      root?.render(
        React.createElement(HookHarness, {
          orderId: 'ord-err',
          enabled: true,
        })
      );
    });

    expect(latestResult?.loading).toBe(false);
    expect(latestResult?.error).toBe('Network connection error');
    expect(latestResult?.detail).toBeNull();
  });
});
