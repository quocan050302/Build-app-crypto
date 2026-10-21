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
    const mockResponse = {
      status: 'SUCCESS',
      order_id: 'ord-100',
      detail: {
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
      },
    };

    vi.spyOn(api, 'getOrderQualityScore').mockResolvedValue(mockResponse as any);

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

    const mockResponseB = {
      status: 'SUCCESS',
      order_id: 'ord-B',
      detail: {
        order_id: 'ord-B',
        assessment_id: 'assess-B',
        score: 64.0,
        status: 'AVAILABLE' as const,
        factors: [],
      },
    };

    vi.spyOn(api, 'getOrderQualityScore').mockImplementation((id: string) => {
      if (id === 'ord-A') return promiseA as any;
      return Promise.resolve(mockResponseB as any);
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
        status: 'SUCCESS',
        order_id: 'ord-A',
        detail: {
          order_id: 'ord-A',
          assessment_id: 'assess-A',
          score: 78.0,
          status: 'AVAILABLE' as const,
          factors: [],
        },
      });
    });

    // State MUST STILL belong to B (Rule 077)
    expect(latestResult?.returnedOrderId).toBe('ord-B');
    expect(latestResult?.detail?.score).toBe(64.0);
  });

  it('rejects response when server returns mismatched orderId (Rules 079, 138)', async () => {
    vi.spyOn(api, 'getOrderQualityScore').mockResolvedValue({
      status: 'SUCCESS',
      order_id: 'wrong-order-id',
      detail: {
        order_id: 'wrong-order-id',
        assessment_id: 'assess-100',
        score: 78.0,
        status: 'AVAILABLE' as const,
      },
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
      status: 'SUCCESS',
      order_id: 'ord-100',
      detail: {
        order_id: 'ord-100',
        assessment_id: 'different-assess-id',
        score: 78.0,
        status: 'AVAILABLE' as const,
      },
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

  it('Bug A: Target becomes null/disabled while request A is pending; late response A must NOT setDetail', async () => {
    let resolveA: (val: any) => void;
    const promiseA = new Promise((resolve) => {
      resolveA = resolve;
    });

    vi.spyOn(api, 'getOrderQualityScore').mockReturnValue(promiseA as any);

    // 1. Render with orderId = 'ord-A'
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-A', enabled: true }));
    });
    expect(latestResult?.loading).toBe(true);

    // 2. Target changes to orderId = null
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: null, enabled: true }));
    });
    expect(latestResult?.loading).toBe(false);
    expect(latestResult?.detail).toBeNull();

    // 3. Late response A resolves
    await act(async () => {
      resolveA!({
        status: 'SUCCESS',
        order_id: 'ord-A',
        detail: {
          score: 78.0,
          status: 'AVAILABLE',
          assessment_id: 'assess-A',
          factors: [],
        },
      });
    });

    // In baseline, this fails because response A overwrote detail!
    expect(latestResult?.detail).toBeNull();
    expect(latestResult?.returnedOrderId).toBeNull();
  });

  it('Bug B: Response missing order_id or missing assessment_id must be rejected (not defaulted)', async () => {
    // Response missing order_id and assessment_id
    vi.spyOn(api, 'getOrderQualityScore').mockResolvedValue({
      status: 'SUCCESS',
      // order_id is missing!
      detail: {
        score: 91.0,
        status: 'AVAILABLE',
        // assessment_id is missing!
      },
    } as any);

    await act(async () => {
      root?.render(
        React.createElement(HookHarness, {
          orderId: 'ord-A',
          expectedAssessmentId: 'qa',
          enabled: true,
        })
      );
    });

    // In baseline, hook defaulted returnedOrderId to 'ord-A' and accepted 91.0!
    expect(latestResult?.error).toBe('DETAIL_BINDING_MISSING');
    expect(latestResult?.detail).toBeNull();
    expect(latestResult?.returnedOrderId).toBeNull();
  });

  it('Rule 121: Cancel-to-disabled with ID retained does not set detail upon late resolve', async () => {
    let resolveA: (val: any) => void;
    const promiseA = new Promise((resolve) => {
      resolveA = resolve;
    });

    vi.spyOn(api, 'getOrderQualityScore').mockReturnValue(promiseA as any);

    // 1. Mount with orderId = 'ord-A', enabled = true
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-A', enabled: true }));
    });
    expect(latestResult?.loading).toBe(true);

    // 2. Change enabled = false while orderId remains 'ord-A'
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-A', enabled: false }));
    });
    expect(latestResult?.loading).toBe(false);
    expect(latestResult?.detail).toBeNull();

    // 3. Late resolve of A
    await act(async () => {
      resolveA!({
        status: 'SUCCESS',
        order_id: 'ord-A',
        detail: {
          order_id: 'ord-A',
          score: 78.0,
          status: 'AVAILABLE',
          factors: [],
        },
      });
    });

    expect(latestResult?.detail).toBeNull();
    expect(latestResult?.returnedOrderId).toBeNull();
    expect(latestResult?.loading).toBe(false);
    expect(latestResult?.error).toBeNull();
  });

  it('Rule 122: Clear then re-enable same ID: resolve 1 after 2 does not overwrite detail 2', async () => {
    let resolveA1: (val: any) => void;
    const promiseA1 = new Promise((resolve) => {
      resolveA1 = resolve;
    });

    let resolveA2: (val: any) => void;
    const promiseA2 = new Promise((resolve) => {
      resolveA2 = resolve;
    });

    let callCount = 0;
    vi.spyOn(api, 'getOrderQualityScore').mockImplementation(() => {
      callCount += 1;
      return (callCount === 1 ? promiseA1 : promiseA2) as any;
    });

    // Request 1: ord-A, assess-1
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-A', expectedAssessmentId: 'assess-1', enabled: true }));
    });

    // Disable (clear)
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-A', expectedAssessmentId: 'assess-1', enabled: false }));
    });

    // Re-enable (Request 2): ord-A, assess-2
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-A', expectedAssessmentId: 'assess-2', enabled: true }));
    });

    // Resolve A2 first
    await act(async () => {
      resolveA2!({
        status: 'SUCCESS',
        order_id: 'ord-A',
        detail: {
          order_id: 'ord-A',
          assessment_id: 'assess-2',
          score: 82.0,
          status: 'AVAILABLE',
          factors: [],
        },
      });
    });

    expect(latestResult?.detail?.score).toBe(82.0);
    expect(latestResult?.detail?.assessment_id).toBe('assess-2');

    // Late resolve of A1 with old score and assessment
    await act(async () => {
      resolveA1!({
        status: 'SUCCESS',
        order_id: 'ord-A',
        detail: {
          order_id: 'ord-A',
          assessment_id: 'assess-1',
          score: 71.0,
          status: 'AVAILABLE',
          factors: [],
        },
      });
    });

    // Must still be 82.0 from request 2
    expect(latestResult?.detail?.score).toBe(82.0);
    expect(latestResult?.detail?.assessment_id).toBe('assess-2');
  });

  it('Rule 124: A -> B -> A sequence: response A1 arriving last does not overwrite request A2', async () => {
    let resolveA1: (val: any) => void;
    const promiseA1 = new Promise((resolve) => {
      resolveA1 = resolve;
    });

    let resolveA2: (val: any) => void;
    const promiseA2 = new Promise((resolve) => {
      resolveA2 = resolve;
    });

    const mockResponseB = {
      status: 'SUCCESS',
      order_id: 'ord-B',
      detail: {
        order_id: 'ord-B',
        score: 64.0,
        status: 'AVAILABLE',
        factors: [],
      },
    };

    let aCount = 0;
    vi.spyOn(api, 'getOrderQualityScore').mockImplementation((id: string) => {
      if (id === 'ord-B') return Promise.resolve(mockResponseB as any);
      aCount += 1;
      return (aCount === 1 ? promiseA1 : promiseA2) as any;
    });

    // 1. Select A (call 1)
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-A', enabled: true }));
    });

    // 2. Select B
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-B', enabled: true }));
    });

    // 3. Select A again (call 2)
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-A', enabled: true }));
    });

    // Resolve A2 first
    await act(async () => {
      resolveA2!({
        status: 'SUCCESS',
        order_id: 'ord-A',
        detail: {
          order_id: 'ord-A',
          score: 85.0,
          status: 'AVAILABLE',
          factors: [],
        },
      });
    });

    expect(latestResult?.detail?.score).toBe(85.0);

    // Resolve A1 (stale generation)
    await act(async () => {
      resolveA1!({
        status: 'SUCCESS',
        order_id: 'ord-A',
        detail: {
          order_id: 'ord-A',
          score: 55.0,
          status: 'AVAILABLE',
          factors: [],
        },
      });
    });

    // Detail must remain 85.0 from A2, NOT 55.0 from stale A1
    expect(latestResult?.detail?.score).toBe(85.0);
  });

  it('Rule 125: Stale rejection: A pending, B success, then A rejects: state of B is preserved', async () => {
    let rejectA: (err: any) => void;
    const promiseA = new Promise((_, reject) => {
      rejectA = reject;
    });

    const mockResponseB = {
      status: 'SUCCESS',
      order_id: 'ord-B',
      detail: {
        order_id: 'ord-B',
        score: 64.0,
        status: 'AVAILABLE',
        factors: [],
      },
    };

    vi.spyOn(api, 'getOrderQualityScore').mockImplementation((id: string) => {
      if (id === 'ord-A') return promiseA as any;
      return Promise.resolve(mockResponseB as any);
    });

    // Start A
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-A', enabled: true }));
    });

    // Switch to B
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-B', enabled: true }));
    });

    expect(latestResult?.detail?.score).toBe(64.0);
    expect(latestResult?.returnedOrderId).toBe('ord-B');
    expect(latestResult?.error).toBeNull();

    // Now A rejects with 500 error
    await act(async () => {
      rejectA!(new Error('Internal Server Error'));
    });

    // State of B must not be polluted with error or detail null
    expect(latestResult?.detail?.score).toBe(64.0);
    expect(latestResult?.returnedOrderId).toBe('ord-B');
    expect(latestResult?.error).toBeNull();
  });

  it('Rule 126: Retry same target: aborts previous controller and commits new retry response', async () => {
    let resolveA1: (val: any) => void;
    const promiseA1 = new Promise((resolve) => {
      resolveA1 = resolve;
    });

    let resolveA2: (val: any) => void;
    const promiseA2 = new Promise((resolve) => {
      resolveA2 = resolve;
    });

    let callCount = 0;
    const capturedSignals: AbortSignal[] = [];
    vi.spyOn(api, 'getOrderQualityScore').mockImplementation((_, signal) => {
      if (signal) capturedSignals.push(signal);
      callCount += 1;
      return (callCount === 1 ? promiseA1 : promiseA2) as any;
    });

    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-A', enabled: true }));
    });

    expect(capturedSignals.length).toBe(1);
    expect(capturedSignals[0].aborted).toBe(false);

    // Call refetch()
    await act(async () => {
      latestResult?.refetch();
    });

    // Controller 1 must be aborted
    expect(capturedSignals[0].aborted).toBe(true);
    expect(capturedSignals.length).toBe(2);
    expect(capturedSignals[1].aborted).toBe(false);

    // Resolve retry response (A2)
    await act(async () => {
      resolveA2!({
        status: 'SUCCESS',
        order_id: 'ord-A',
        detail: {
          order_id: 'ord-A',
          score: 79.0,
          status: 'AVAILABLE',
          factors: [],
        },
      });
    });

    expect(latestResult?.detail?.score).toBe(79.0);

    // Resolve old A1
    await act(async () => {
      resolveA1!({
        status: 'SUCCESS',
        order_id: 'ord-A',
        detail: {
          order_id: 'ord-A',
          score: 40.0,
          status: 'AVAILABLE',
          factors: [],
        },
      });
    });

    expect(latestResult?.detail?.score).toBe(79.0);
  });

  it('Rule 127: Retry then select another: late response of A after retry is discarded for B', async () => {
    let resolveA2: (val: any) => void;
    const promiseA2 = new Promise((resolve) => {
      resolveA2 = resolve;
    });

    let callCount = 0;
    vi.spyOn(api, 'getOrderQualityScore').mockImplementation((id: string) => {
      callCount += 1;
      if (id === 'ord-B') {
        return Promise.resolve({
          status: 'SUCCESS',
          order_id: 'ord-B',
          detail: { order_id: 'ord-B', score: 62.0, status: 'AVAILABLE', factors: [] },
        } as any);
      }
      return promiseA2 as any;
    });

    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-A', enabled: true }));
    });

    // Trigger retry for A
    await act(async () => {
      latestResult?.refetch();
    });

    // While retry A is in flight, user switches to B
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-B', enabled: true }));
    });

    expect(latestResult?.returnedOrderId).toBe('ord-B');
    expect(latestResult?.detail?.score).toBe(62.0);

    // Now retry A resolves
    await act(async () => {
      resolveA2!({
        status: 'SUCCESS',
        order_id: 'ord-A',
        detail: { order_id: 'ord-A', score: 99.0, status: 'AVAILABLE', factors: [] },
      });
    });

    // Still belongs to B
    expect(latestResult?.returnedOrderId).toBe('ord-B');
    expect(latestResult?.detail?.score).toBe(62.0);
  });

  it('Rule 128: Retry while disabled is a no-op with 0 API requests', async () => {
    const spy = vi.spyOn(api, 'getOrderQualityScore');

    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: null, enabled: false }));
    });

    expect(spy).not.toHaveBeenCalled();

    // Call refetch() while target is null/disabled
    await act(async () => {
      latestResult?.refetch();
    });

    expect(spy).not.toHaveBeenCalled();
    expect(latestResult?.detail).toBeNull();
    expect(latestResult?.loading).toBe(false);
  });

  it('Rule 129 & 137: Unmount aborts controller and ignores late resolution without setState warnings', async () => {
    let resolveA: (val: any) => void;
    const promiseA = new Promise((resolve) => {
      resolveA = resolve;
    });

    let capturedSignal: AbortSignal | undefined;
    vi.spyOn(api, 'getOrderQualityScore').mockImplementation((_, signal) => {
      capturedSignal = signal;
      return promiseA as any;
    });

    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-unmount', enabled: true }));
    });

    expect(capturedSignal).toBeDefined();
    expect(capturedSignal?.aborted).toBe(false);

    // Unmount
    await act(async () => {
      root?.unmount();
    });

    expect(capturedSignal?.aborted).toBe(true);

    // Resolve late after unmount
    await act(async () => {
      resolveA!({
        status: 'SUCCESS',
        order_id: 'ord-unmount',
        detail: { order_id: 'ord-unmount', score: 80.0, status: 'AVAILABLE', factors: [] },
      });
    });

    // No error or crash thrown
  });

  it('Rule 130: StrictMode: setup/cleanup/setup replay retains active controller and populates data', async () => {
    const mockResponse = {
      status: 'SUCCESS',
      order_id: 'ord-strict',
      detail: {
        order_id: 'ord-strict',
        score: 77.0,
        status: 'AVAILABLE' as const,
        factors: [],
      },
    };

    vi.spyOn(api, 'getOrderQualityScore').mockResolvedValue(mockResponse as any);

    await act(async () => {
      root?.render(
        React.createElement(
          React.StrictMode,
          null,
          React.createElement(HookHarness, { orderId: 'ord-strict', enabled: true })
        )
      );
    });

    expect(latestResult?.returnedOrderId).toBe('ord-strict');
    expect(latestResult?.detail?.score).toBe(77.0);
    expect(latestResult?.loading).toBe(false);
  });

  it('Rule 134: Error on target A clears immediately when selecting target B', async () => {
    vi.spyOn(api, 'getOrderQualityScore').mockImplementation((id: string) => {
      if (id === 'ord-err') {
        return Promise.reject(new Error('500 Server Error'));
      }
      return new Promise(() => {}); // pending for ord-B
    });

    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-err', enabled: true }));
    });

    expect(latestResult?.error).toBe('500 Server Error');

    // Switch to ord-B (pending)
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-B', enabled: true }));
    });

    // Error from ord-err must not bleed into ord-B
    expect(latestResult?.error).toBeNull();
    expect(latestResult?.loading).toBe(true);
    expect(latestResult?.detail).toBeNull();
  });

  it('Rule 135: Changing expectedAssessmentId on same order invalidates previous pending request', async () => {
    let resolveQA: (val: any) => void;
    const promiseQA = new Promise((resolve) => {
      resolveQA = resolve;
    });

    let resolveQB: (val: any) => void;
    const promiseQB = new Promise((resolve) => {
      resolveQB = resolve;
    });

    let callCount = 0;
    vi.spyOn(api, 'getOrderQualityScore').mockImplementation(() => {
      callCount += 1;
      return (callCount === 1 ? promiseQA : promiseQB) as any;
    });

    // 1. Request ord-A with assess-qa
    await act(async () => {
      root?.render(
        React.createElement(HookHarness, {
          orderId: 'ord-A',
          expectedAssessmentId: 'assess-qa',
          enabled: true,
        })
      );
    });

    // 2. Change expectedAssessmentId to assess-qb
    await act(async () => {
      root?.render(
        React.createElement(HookHarness, {
          orderId: 'ord-A',
          expectedAssessmentId: 'assess-qb',
          enabled: true,
        })
      );
    });

    // Resolve QB
    await act(async () => {
      resolveQB!({
        status: 'SUCCESS',
        order_id: 'ord-A',
        detail: {
          order_id: 'ord-A',
          assessment_id: 'assess-qb',
          score: 88.0,
          status: 'AVAILABLE',
          factors: [],
        },
      });
    });

    expect(latestResult?.detail?.score).toBe(88.0);
    expect(latestResult?.detail?.assessment_id).toBe('assess-qb');

    // Late resolve of QA
    await act(async () => {
      resolveQA!({
        status: 'SUCCESS',
        order_id: 'ord-A',
        detail: {
          order_id: 'ord-A',
          assessment_id: 'assess-qa',
          score: 66.0,
          status: 'AVAILABLE',
          factors: [],
        },
      });
    });

    expect(latestResult?.detail?.score).toBe(88.0);
    expect(latestResult?.detail?.assessment_id).toBe('assess-qb');
  });

  it('Rule 136: Parent re-renders with identical primitives do not trigger extra fetches', async () => {
    const spy = vi.spyOn(api, 'getOrderQualityScore').mockResolvedValue({
      status: 'SUCCESS',
      order_id: 'ord-dedupe',
      detail: { order_id: 'ord-dedupe', score: 75.0, status: 'AVAILABLE', factors: [] },
    } as any);

    // Initial render
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-dedupe', enabled: true }));
    });
    expect(spy).toHaveBeenCalledTimes(1);

    // Re-render 1 with same props
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-dedupe', enabled: true }));
    });

    // Re-render 2 with same props
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-dedupe', enabled: true }));
    });

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('Rule 132: Render identity projection returns detail null immediately on render of target B before effect runs', async () => {
    const mockResponseA = {
      status: 'SUCCESS',
      order_id: 'ord-A',
      detail: {
        order_id: 'ord-A',
        score: 95.0,
        status: 'AVAILABLE' as const,
        factors: [
          {
            code: 'FVG_A',
            earned_points: 20,
            max_points: 20,
            status: 'FULL',
          },
        ],
      },
    };

    vi.spyOn(api, 'getOrderQualityScore').mockImplementation((id: string) => {
      if (id === 'ord-A') return Promise.resolve(mockResponseA as any);
      return new Promise(() => {}); // ord-B pending
    });

    // 1. Initial render with ord-A
    await act(async () => {
      root?.render(React.createElement(HookHarness, { orderId: 'ord-A', enabled: true }));
    });

    expect(latestResult?.detail?.score).toBe(95.0);
    expect(latestResult?.returnedOrderId).toBe('ord-A');

    // 2. We render ord-B and capture the synchronous hook return value during render
    let detailDuringRenderOfB: any = 'untested';
    function HarnessWithRenderCheck(props: UseCandidateScoreDetailOptions) {
      const res = useCandidateScoreDetail(props);
      if (props.orderId === 'ord-B') {
        detailDuringRenderOfB = res.detail;
      }
      return null;
    }

    await act(async () => {
      root?.render(React.createElement(HarnessWithRenderCheck, { orderId: 'ord-B', enabled: true }));
    });

    // During the render of ord-B, res.detail must be null, never factors of ord-A!
    expect(detailDuringRenderOfB).toBeNull();
  });
});
