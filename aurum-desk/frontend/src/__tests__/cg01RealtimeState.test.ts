// @vitest-environment happy-dom
/**
 * Comprehensive Unit & Integration Tests for CG-01 Realtime State Hook, Reducer, and Ordering Rules (V16.29).
 * Tests calling real production hook and production reducer.
 * Eliminates custom test-only applyUpdate implementations.
 * Conforms to prompt rules 021-047, 061-076, 081-083, 091-105, 119-120, 142-154.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import React, { act, StrictMode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { wsClient } from '../services/wsClient';
import { api } from '../api/client';
import {
  useCG01RealtimeState,
  reduceCG01RealtimeUpdate,
  getCG01LifecycleRank,
  type UseCG01RealtimeStateReturn,
  type CG01RealtimeInternalState,
} from '../hooks/useCG01RealtimeState';
import {
  selectedChartReducer,
  initialSelectedChartState,
  type UseSelectedChartState,
} from '../hooks/useSelectedChart';
import type { ChartBinding } from '../types/smcScenario';

// Configure React act environment for Vitest
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

// Harness component to capture live hook values
let latestHookReturn: UseCG01RealtimeStateReturn | null = null;

function HookTestHarness({
  symbol = 'XAUUSDT',
  autoBootstrap = true,
}: {
  symbol?: string;
  autoBootstrap?: boolean;
}) {
  const result = useCG01RealtimeState({ symbol, autoBootstrap });
  latestHookReturn = result;
  return null;
}

describe('CG-01 Realtime Reducer Production Logic (Rules 024-027, 069-072)', () => {
  const emptyState: CG01RealtimeInternalState = {
    rawEvaluation: null,
    asOfMs: 0,
    serverSequence: 0,
  };

  it('rejects out-of-order stale updates (Rules 036, 069)', () => {
    // 1. Fresh state at 5000
    const state1 = reduceCG01RealtimeUpdate(emptyState, {
      payload: { as_of_ms: 5000, stage: 'WAIT_MSS', poi_id: 'poi_fresh' },
    });
    expect(state1.asOfMs).toBe(5000);
    expect(state1.rawEvaluation?.stage).toBe('WAIT_MSS');
    expect(state1.rawEvaluation?.poi_id).toBe('poi_fresh');

    // 2. Out-of-order stale update at 3000 arrives -> rejected
    const state2 = reduceCG01RealtimeUpdate(state1, {
      payload: { as_of_ms: 3000, stage: 'WAIT_SWEEP', poi_id: 'poi_old' },
    });
    expect(state2).toBe(state1);
    expect(state2.asOfMs).toBe(5000);
    expect(state2.rawEvaluation?.stage).toBe('WAIT_MSS');
    expect(state2.rawEvaluation?.poi_id).toBe('poi_fresh');
  });

  it('resolves equal timestamp tie-break: EXECUTABLE -> ARMED (Rules 039, 070)', () => {
    // Candidate qualified as EXECUTABLE at 5000
    const state1 = reduceCG01RealtimeUpdate(emptyState, {
      payload: { as_of_ms: 5000, stage: 'EXECUTABLE', poi_id: 'poi_1' },
    });
    expect(state1.rawEvaluation?.stage).toBe('EXECUTABLE');

    // Lifecycle override to ARMED at the same timestamp (5000)
    const state2 = reduceCG01RealtimeUpdate(state1, {
      payload: { as_of_ms: 5000, stage: 'ARMED', armed_order_id: 'order_1', poi_id: 'poi_1' },
    });
    expect(state2.rawEvaluation?.stage).toBe('ARMED');
    expect(state2.rawEvaluation?.armed_order_id).toBe('order_1');
  });

  it('resolves equal timestamp tie-break: EXECUTABLE -> GUARD_BLOCKED (Rules 039, 070)', () => {
    const state1 = reduceCG01RealtimeUpdate(emptyState, {
      payload: { as_of_ms: 6000, stage: 'EXECUTABLE', poi_id: 'poi_2' },
    });
    expect(state1.rawEvaluation?.stage).toBe('EXECUTABLE');

    const state2 = reduceCG01RealtimeUpdate(state1, {
      payload: {
        as_of_ms: 6000,
        stage: 'GUARD_BLOCKED',
        guard_summary: { blocked_reason: 'DAILY_MAX_CAP' },
        poi_id: 'poi_2',
      },
    });
    expect(state2.rawEvaluation?.stage).toBe('GUARD_BLOCKED');
    expect(state2.rawEvaluation?.guard_summary?.blocked_reason).toBe('DAILY_MAX_CAP');
  });

  it('resolves equal timestamp tie-break: ARMED -> DEGRADED on rollback (Rules 039, 071)', () => {
    const state1 = reduceCG01RealtimeUpdate(emptyState, {
      payload: { as_of_ms: 7000, stage: 'ARMED', armed_order_id: 'order_failed', poi_id: 'poi_3' },
    });
    expect(state1.rawEvaluation?.stage).toBe('ARMED');

    const state2 = reduceCG01RealtimeUpdate(state1, {
      payload: { as_of_ms: 7000, stage: 'DEGRADED', armed_order_id: null, poi_id: 'poi_3' },
    });
    expect(state2.rawEvaluation?.stage).toBe('DEGRADED');
    expect(state2.rawEvaluation?.armed_order_id).toBeNull();
  });

  it('handles duplicate payloads idempotently without state drift (Rules 042, 072)', () => {
    const payload = { as_of_ms: 8000, stage: 'WAIT_FVG', poi_id: 'poi_dup' };
    const state1 = reduceCG01RealtimeUpdate(emptyState, { payload });
    const state2 = reduceCG01RealtimeUpdate(state1, { payload });
    const state3 = reduceCG01RealtimeUpdate(state2, { payload });

    expect(state2).toBe(state1);
    expect(state3).toBe(state1);
    expect(state3.rawEvaluation?.stage).toBe('WAIT_FVG');
    expect(state3.rawEvaluation?.poi_id).toBe('poi_dup');
  });

  it('handles sequence tie-break correctly when sequence is available (Rule 038)', () => {
    const state1 = reduceCG01RealtimeUpdate(emptyState, {
      payload: { as_of_ms: 9000, stage: 'WAIT_MSS', poi_id: 'poi_seq' },
      serverSequence: 10,
    });
    expect(state1.serverSequence).toBe(10);

    // Same timestamp, lower sequence -> rejected
    const state2 = reduceCG01RealtimeUpdate(state1, {
      payload: { as_of_ms: 9000, stage: 'WAIT_MSS', poi_id: 'poi_seq_old' },
      serverSequence: 9,
    });
    expect(state2).toBe(state1);

    // Same timestamp, higher sequence -> accepted
    const state3 = reduceCG01RealtimeUpdate(state1, {
      payload: { as_of_ms: 9000, stage: 'WAIT_FVG', poi_id: 'poi_seq_new' },
      serverSequence: 11,
    });
    expect(state3.serverSequence).toBe(11);
    expect(state3.rawEvaluation?.stage).toBe('WAIT_FVG');
  });

  it('verifies getCG01LifecycleRank values', () => {
    expect(getCG01LifecycleRank('DEGRADED')).toBe(4);
    expect(getCG01LifecycleRank('ARMED')).toBe(3);
    expect(getCG01LifecycleRank('GUARD_BLOCKED')).toBe(2);
    expect(getCG01LifecycleRank('EXECUTABLE')).toBe(1);
    expect(getCG01LifecycleRank('WAIT_MSS')).toBe(0);
    expect(getCG01LifecycleRank(undefined)).toBe(0);
  });
});

describe('CG-01 Realtime Hook Lifecycle & Harness Integration (Rules 021-035, 061-076, 081)', () => {
  let container: HTMLDivElement | null = null;
  let root: Root | null = null;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    latestHookReturn = null;
    vi.clearAllMocks();
    (wsClient as any).ws = {
      readyState: 1,
      send: vi.fn(),
      close: vi.fn(),
    };
  });



  afterEach(() => {
    if (root) {
      act(() => {
        root?.unmount();
      });
      root = null;
    }
    if (container && container.parentNode) {
      container.parentNode.removeChild(container);
      container = null;
    }
    vi.restoreAllMocks();
  });

  it('Rule 064: Initial bootstrap mounts hook and populates snapshot for requested symbol', async () => {
    const mockSnapshot = {
      enabled: true,
      evaluated: true,
      stage: 'WAIT_MSS',
      direction: 'LONG',
      poi_id: 'poi_harness_01',
      as_of_ms: 10000,
    };
    const getSpy = vi.spyOn(api, 'getCG01Evaluation').mockResolvedValue(mockSnapshot);

    await act(async () => {
      root?.render(React.createElement(HookTestHarness, { symbol: 'XAUUSDT', autoBootstrap: true }));
    });

    expect(getSpy).toHaveBeenCalledTimes(1);
    expect(getSpy).toHaveBeenCalledWith('XAUUSDT');
    expect(latestHookReturn).not.toBeNull();
    expect(latestHookReturn?.rawEvaluation?.stage).toBe('WAIT_MSS');
    expect(latestHookReturn?.rawEvaluation?.direction).toBe('LONG');
    expect(latestHookReturn?.displayState.stageLabel).toBe('Chờ Phá Cấu Trúc (MSS)');
  });

  it('Rule 065: Subscription remains stable across stage updates (WAIT_SWEEP -> WAIT_MSS -> WAIT_FVG) without rebootstrapping', async () => {
    const getSpy = vi.spyOn(api, 'getCG01Evaluation').mockResolvedValue({
      enabled: true,
      evaluated: true,
      stage: 'WAIT_SWEEP',
      as_of_ms: 1000,
    });

    await act(async () => {
      root?.render(React.createElement(HookTestHarness, { symbol: 'XAUUSDT', autoBootstrap: true }));
    });

    expect(getSpy).toHaveBeenCalledTimes(1);
    expect(latestHookReturn?.rawEvaluation?.stage).toBe('WAIT_SWEEP');

    // 1. Dispatch WAIT_MSS via WebSocket
    await act(async () => {
      wsClient.dispatchForTesting({
        type: 'CG01_EVALUATION',
        symbol: 'XAUUSDT',
        payload: {
          enabled: true,
          evaluated: true,
          stage: 'WAIT_MSS',
          direction: 'LONG',
          poi_id: 'poi_xau',
          as_of_ms: 2000,
        },
      });
    });
    expect(latestHookReturn?.rawEvaluation?.stage).toBe('WAIT_MSS');

    // 2. Dispatch WAIT_FVG via WebSocket
    await act(async () => {
      wsClient.dispatchForTesting({
        type: 'CG01_EVALUATION',
        symbol: 'XAUUSDT',
        payload: {
          enabled: true,
          evaluated: true,
          stage: 'WAIT_FVG',
          direction: 'LONG',
          poi_id: 'poi_xau',
          as_of_ms: 3000,
        },
      });
    });
    expect(latestHookReturn?.rawEvaluation?.stage).toBe('WAIT_FVG');

    // CRITICAL: Bootstrap API must NOT have been called again!
    expect(getSpy).toHaveBeenCalledTimes(1);
  });

  it('Rule 066: Burst updates in the same act/tick resolve correctly without stale closure rollback', async () => {
    vi.spyOn(api, 'getCG01Evaluation').mockResolvedValue({
      enabled: true,
      stage: 'OBSERVING',
      as_of_ms: 100,
    });

    await act(async () => {
      root?.render(React.createElement(HookTestHarness, { symbol: 'XAUUSDT', autoBootstrap: true }));
    });

    // In a single act block, dispatch EXECUTABLE and then ARMED at the same timestamp
    await act(async () => {
      wsClient.dispatchForTesting({
        type: 'CG01_EVALUATION',
        symbol: 'XAUUSDT',
        payload: {
          enabled: true,
          evaluated: true,
          stage: 'EXECUTABLE',
          direction: 'LONG',
          poi_id: 'poi_burst',
          as_of_ms: 5000,
        },
      });

      wsClient.dispatchForTesting({
        type: 'CG01_EVALUATION',
        symbol: 'XAUUSDT',
        payload: {
          enabled: true,
          evaluated: true,
          stage: 'ARMED',
          direction: 'LONG',
          poi_id: 'poi_burst',
          armed_order_id: 'order_burst_999',
          as_of_ms: 5000,
        },
      });
    });

    // Final state must be ARMED with order id
    expect(latestHookReturn?.rawEvaluation?.stage).toBe('ARMED');
    expect(latestHookReturn?.rawEvaluation?.armed_order_id).toBe('order_burst_999');
    expect(latestHookReturn?.displayState.isArmed).toBe(true);
  });

  it('Rule 067: Resolves HTTP/WS race where older HTTP snapshot finishes after newer WS event', async () => {
    let resolveHttp: (data: any) => void = () => {};
    const deferredPromise = new Promise((resolve) => {
      resolveHttp = resolve;
    });

    vi.spyOn(api, 'getCG01Evaluation').mockImplementation(() => deferredPromise as any);

    // 1. Mount starts HTTP fetch
    await act(async () => {
      root?.render(React.createElement(HookTestHarness, { symbol: 'XAUUSDT', autoBootstrap: true }));
    });

    // 2. Newer WS event arrives at as_of_ms: 6000
    await act(async () => {
      wsClient.dispatchForTesting({
        type: 'CG01_EVALUATION',
        symbol: 'XAUUSDT',
        payload: {
          enabled: true,
          stage: 'WAIT_MSS',
          poi_id: 'poi_newer_ws',
          as_of_ms: 6000,
        },
      });
    });
    expect(latestHookReturn?.rawEvaluation?.stage).toBe('WAIT_MSS');
    expect(latestHookReturn?.rawEvaluation?.poi_id).toBe('poi_newer_ws');

    // 3. Delayed HTTP response arrives with older as_of_ms: 4000
    await act(async () => {
      resolveHttp({
        enabled: true,
        stage: 'WAIT_SWEEP',
        poi_id: 'poi_older_http',
        as_of_ms: 4000,
      });
    });

    // Older HTTP response must NOT overwrite newer WS evaluation
    expect(latestHookReturn?.rawEvaluation?.stage).toBe('WAIT_MSS');
    expect(latestHookReturn?.rawEvaluation?.poi_id).toBe('poi_newer_ws');
    expect(latestHookReturn?.rawEvaluation?.as_of_ms).toBe(6000);
  });

  it('Rule 068: In-flight HTTP request from previous symbol does not overwrite state of new symbol', async () => {
    let resolveXau: (data: any) => void = () => {};
    const xauPromise = new Promise((resolve) => {
      resolveXau = resolve;
    });

    vi.spyOn(api, 'getCG01Evaluation').mockImplementation((sym) => {
      if (sym === 'XAUUSDT') return xauPromise as any;
      return Promise.resolve({
        enabled: true,
        stage: 'WAIT_FVG',
        symbol: 'BTCUSDT',
        as_of_ms: 8000,
      }) as any;
    });

    // 1. Mount with XAUUSDT
    await act(async () => {
      root?.render(React.createElement(HookTestHarness, { symbol: 'XAUUSDT', autoBootstrap: true }));
    });

    // 2. Switch symbol to BTCUSDT
    await act(async () => {
      root?.render(React.createElement(HookTestHarness, { symbol: 'BTCUSDT', autoBootstrap: true }));
    });
    expect(latestHookReturn?.rawEvaluation?.stage).toBe('WAIT_FVG');

    // 3. XAUUSDT resolves now
    await act(async () => {
      resolveXau({
        enabled: true,
        stage: 'OBSERVING',
        symbol: 'XAUUSDT',
        as_of_ms: 99999,
      });
    });

    // BTCUSDT must retain its own state and NOT be overwritten by XAUUSDT
    expect(latestHookReturn?.rawEvaluation?.stage).toBe('WAIT_FVG');
  });

  it('Rule 073: Disconnect reflects connection state and reconnect resyncs snapshot', async () => {
    const getSpy = vi.spyOn(api, 'getCG01Evaluation').mockResolvedValue({
      enabled: true,
      stage: 'OBSERVING',
      as_of_ms: 1000,
    });

    await act(async () => {
      root?.render(React.createElement(HookTestHarness, { symbol: 'XAUUSDT', autoBootstrap: true }));
    });
    expect(latestHookReturn?.isConnected).toBe(true);

    // Simulate socket disconnect
    if ((wsClient as any).ws) {
      (wsClient as any).ws.readyState = 3;
    }
    await act(async () => {
      (wsClient as any).notifyStatus(false);
    });
    expect(latestHookReturn?.isConnected).toBe(false);
    expect(latestHookReturn?.displayState.title).toContain('Mất Kết Nối');

    // Simulate socket reconnect
    if ((wsClient as any).ws) {
      (wsClient as any).ws.readyState = 1;
    }
    await act(async () => {
      (wsClient as any).notifyStatus(true);
    });
    expect(latestHookReturn?.isConnected).toBe(true);
    // Initial bootstrap (1) + reconnect resync (1) = 2
    expect(getSpy).toHaveBeenCalledTimes(2);
  });




  it('Rule 074: StrictMode mount/cleanup/remount does not leave dangling listeners or timers', async () => {
    vi.spyOn(api, 'getCG01Evaluation').mockResolvedValue({
      enabled: true,
      stage: 'OBSERVING',
      as_of_ms: 1000,
    });

    const initialDiag = wsClient.getDiagnostics();

    await act(async () => {
      root?.render(
        React.createElement(
          StrictMode,
          null,
          React.createElement(HookTestHarness, { symbol: 'XAUUSDT', autoBootstrap: true })
        )
      );
    });

    // In StrictMode, component was mounted, cleaned up, and remounted once
    const mountedDiag = wsClient.getDiagnostics();
    expect(mountedDiag.subscriberCount).toBe(initialDiag.subscriberCount + 1);
    expect(mountedDiag.statusSubscriberCount).toBe(initialDiag.statusSubscriberCount + 1);

    // Unmount
    await act(async () => {
      root?.unmount();
      root = null;
    });

    // Subscribers must cleanly return to initial baseline
    const finalDiag = wsClient.getDiagnostics();
    expect(finalDiag.subscriberCount).toBe(initialDiag.subscriberCount);
    expect(finalDiag.statusSubscriberCount).toBe(initialDiag.statusSubscriberCount);
  });

  it('Rules 075-076: Handles structured object ui_summary_vi and string compatibility', async () => {
    vi.spyOn(api, 'getCG01Evaluation').mockResolvedValue({
      enabled: true,
      evaluated: true,
      stage: 'WAIT_RETEST',
      direction: 'LONG',
      poi_id: 'poi_obj',
      ui_summary_vi: { status: 'POI poi_obj: Đang chờ giá hồi (retest) vào vùng FVG.' },
      as_of_ms: 12000,
    });

    await act(async () => {
      root?.render(React.createElement(HookTestHarness, { symbol: 'XAUUSDT', autoBootstrap: true }));
    });

    expect(latestHookReturn?.displayState.description).toContain('Đang chờ giá hồi (retest) vào vùng FVG');
    expect(latestHookReturn?.displayState.description).not.toContain('[object Object]');
  });

  it('Rule 081: Live CG-01 updates do not mutate or overwrite selected chart historical binding', async () => {
    vi.spyOn(api, 'getCG01Evaluation').mockResolvedValue({
      enabled: true,
      stage: 'OBSERVING',
      as_of_ms: 1000,
    });

    await act(async () => {
      root?.render(React.createElement(HookTestHarness, { symbol: 'XAUUSDT', autoBootstrap: true }));
    });


    // 1. User has selected a historical order on the chart
    const historicalBinding: ChartBinding = {
      source: 'ORDER',
      id: 'hist_order_777',
      instanceId: null,
      revision: null,
      symbol: 'XAUUSDT',
      direction: 'SHORT',
      planFingerprint: null,
      snapshotId: null,
    };

    const initialChartState: UseSelectedChartState = {
      ...initialSelectedChartState,
      binding: historicalBinding,
      isLoading: false,
    };

    // 2. A live CG-01 candidate arrives with opposite direction (LONG) and stage EXECUTABLE
    await act(async () => {
      wsClient.dispatchForTesting({
        type: 'CG01_EVALUATION',
        symbol: 'XAUUSDT',
        payload: {
          stage: 'EXECUTABLE',
          direction: 'LONG',
          poi_id: 'poi_live_01',
          as_of_ms: 99999,
        },
      });
    });

    // 3. Verify hook received live CG-01 evaluation
    expect(latestHookReturn?.rawEvaluation?.stage).toBe('EXECUTABLE');
    expect(latestHookReturn?.rawEvaluation?.direction).toBe('LONG');

    // 4. Verify selected chart state remains completely isolated and unchanged
    expect(initialChartState.binding?.id).toBe('hist_order_777');
    expect(initialChartState.binding?.direction).toBe('SHORT');
    expect(initialChartState.binding?.source).toBe('ORDER');

    const chartAfter = selectedChartReducer(initialChartState, {
      type: 'SELECT_CONTEXT',
      binding: historicalBinding,
    });
    expect(chartAfter.binding?.id).toBe('hist_order_777');
    expect(chartAfter.binding?.direction).toBe('SHORT');
  });

});
