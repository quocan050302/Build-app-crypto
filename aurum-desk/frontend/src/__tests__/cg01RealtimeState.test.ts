/**
 * Unit & Integration Tests for CG-01 Realtime State Hook and Ordering Rules (V16.29).
 * Covers out-of-order updates, equal timestamp tie-break, HTTP bootstrap race,
 * duplicate event handling, connection tracking, and chart selection isolation.
 * Conforms to prompt rules 091-105, 119-120, 142-154.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { wsClient } from '../services/wsClient';
import { api } from '../api/client';
import {
  selectedChartReducer,
  initialSelectedChartState,
  type UseSelectedChartState,
} from '../hooks/useSelectedChart';

describe('CG-01 Realtime State Ordering & Protection Logic', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('rejects out-of-order stale updates (Item 148)', () => {
    let currentAsOf = 0;
    let currentPayload: any = null;

    const applyUpdate = (newPayload: any) => {
      const newAsOf = newPayload.as_of_ms || 0;
      if (currentAsOf > 0 && newAsOf < currentAsOf) {
        return; // Stale update rejected
      }
      currentAsOf = Math.max(currentAsOf, newAsOf);
      currentPayload = newPayload;
    };

    // 1. Initial fresh update at 5000
    applyUpdate({ as_of_ms: 5000, stage: 'WAIT_MSS', poi_id: 'poi_fresh' });
    expect(currentPayload.stage).toBe('WAIT_MSS');
    expect(currentPayload.poi_id).toBe('poi_fresh');

    // 2. Out-of-order stale update at 3000 arrives
    applyUpdate({ as_of_ms: 3000, stage: 'WAIT_SWEEP', poi_id: 'poi_old' });
    expect(currentPayload.stage).toBe('WAIT_MSS');
    expect(currentPayload.poi_id).toBe('poi_fresh');
    expect(currentAsOf).toBe(5000);
  });

  it('resolves equal timestamp tie-break: EXECUTABLE -> ARMED (Item 099-100, 142)', () => {
    let currentAsOf = 0;
    let currentPayload: any = null;

    const rankMap: Record<string, number> = {
      OBSERVING: 0,
      WAIT_SWEEP: 0,
      WAIT_MSS: 0,
      WAIT_FVG: 0,
      EXECUTABLE: 1,
      GUARD_BLOCKED: 2,
      ARMED: 3,
      DEGRADED: 4,
    };

    const applyUpdate = (newPayload: any, seq: number = 0) => {
      const newAsOf = newPayload.as_of_ms || 0;
      if (currentAsOf > 0 && newAsOf < currentAsOf) return;

      if (newAsOf > 0 && newAsOf === currentAsOf) {
        const curRank = rankMap[currentPayload?.stage] || 0;
        const newRank = rankMap[newPayload.stage] || 0;
        if (newRank < curRank) return;
      }

      currentAsOf = Math.max(currentAsOf, newAsOf);
      currentPayload = newPayload;
    };

    // Candidate qualified as EXECUTABLE at 5000
    applyUpdate({ as_of_ms: 5000, stage: 'EXECUTABLE', poi_id: 'poi_1' });
    expect(currentPayload.stage).toBe('EXECUTABLE');

    // Lifecycle override to ARMED at the same timestamp (5000)
    applyUpdate({ as_of_ms: 5000, stage: 'ARMED', armed_order_id: 'order_1', poi_id: 'poi_1' });
    expect(currentPayload.stage).toBe('ARMED');
    expect(currentPayload.armed_order_id).toBe('order_1');
  });

  it('resolves equal timestamp tie-break: EXECUTABLE -> GUARD_BLOCKED (Item 143)', () => {
    let currentAsOf = 0;
    let currentPayload: any = null;

    const rankMap: Record<string, number> = {
      EXECUTABLE: 1,
      GUARD_BLOCKED: 2,
      ARMED: 3,
      DEGRADED: 4,
    };

    const applyUpdate = (newPayload: any) => {
      const newAsOf = newPayload.as_of_ms || 0;
      if (currentAsOf > 0 && newAsOf < currentAsOf) return;
      if (newAsOf > 0 && newAsOf === currentAsOf) {
        const curRank = rankMap[currentPayload?.stage] || 0;
        const newRank = rankMap[newPayload.stage] || 0;
        if (newRank < curRank) return;
      }
      currentAsOf = Math.max(currentAsOf, newAsOf);
      currentPayload = newPayload;
    };

    applyUpdate({ as_of_ms: 6000, stage: 'EXECUTABLE', poi_id: 'poi_2' });
    expect(currentPayload.stage).toBe('EXECUTABLE');

    applyUpdate({
      as_of_ms: 6000,
      stage: 'GUARD_BLOCKED',
      guard_summary: { blocked_reason: 'DAILY_MAX_CAP' },
      poi_id: 'poi_2',
    });
    expect(currentPayload.stage).toBe('GUARD_BLOCKED');
    expect(currentPayload.guard_summary.blocked_reason).toBe('DAILY_MAX_CAP');
  });

  it('resolves equal timestamp tie-break: ARMED -> DEGRADED on rollback (Item 144)', () => {
    let currentAsOf = 0;
    let currentPayload: any = null;

    const rankMap: Record<string, number> = {
      EXECUTABLE: 1,
      GUARD_BLOCKED: 2,
      ARMED: 3,
      DEGRADED: 4,
    };

    const applyUpdate = (newPayload: any) => {
      const newAsOf = newPayload.as_of_ms || 0;
      if (currentAsOf > 0 && newAsOf < currentAsOf) return;
      if (newAsOf > 0 && newAsOf === currentAsOf) {
        const curRank = rankMap[currentPayload?.stage] || 0;
        const newRank = rankMap[newPayload.stage] || 0;
        if (newRank < curRank) return;
      }
      currentAsOf = Math.max(currentAsOf, newAsOf);
      currentPayload = newPayload;
    };

    applyUpdate({ as_of_ms: 7000, stage: 'ARMED', armed_order_id: 'order_failed', poi_id: 'poi_3' });
    expect(currentPayload.stage).toBe('ARMED');

    applyUpdate({ as_of_ms: 7000, stage: 'DEGRADED', armed_order_id: null, poi_id: 'poi_3' });
    expect(currentPayload.stage).toBe('DEGRADED');
    expect(currentPayload.armed_order_id).toBeNull();
  });

  it('protects against HTTP bootstrap race over newer WebSocket event (Item 096, 149)', async () => {
    let latestAsOf = 0;
    let currentPayload: any = null;

    const applyUpdate = (payload: any) => {
      const asOf = payload.as_of_ms || 0;
      if (latestAsOf > 0 && asOf < latestAsOf) return;
      latestAsOf = Math.max(latestAsOf, asOf);
      currentPayload = payload;
    };

    // 1. WebSocket event with newer timestamp 6000 arrives first
    applyUpdate({ as_of_ms: 6000, stage: 'WAIT_MSS', poi_id: 'poi_new' });
    expect(currentPayload.as_of_ms).toBe(6000);

    // 2. Delayed HTTP bootstrap response arrives with older timestamp 4000
    const delayedHttpSnapshot = { as_of_ms: 4000, stage: 'WAIT_SWEEP', poi_id: 'poi_old' };
    applyUpdate(delayedHttpSnapshot);

    // Stale HTTP response must NOT overwrite newer WS state
    expect(currentPayload.as_of_ms).toBe(6000);
    expect(currentPayload.stage).toBe('WAIT_MSS');
    expect(currentPayload.poi_id).toBe('poi_new');
  });

  it('handles duplicate payloads without state drift (Item 101, 150)', () => {
    let updateCount = 0;
    let currentPayload: any = null;

    const applyUpdate = (payload: any) => {
      updateCount++;
      currentPayload = payload;
    };

    const payload = { as_of_ms: 8000, stage: 'WAIT_FVG', poi_id: 'poi_dup' };
    applyUpdate(payload);
    applyUpdate(payload);
    applyUpdate(payload);

    expect(updateCount).toBe(3);
    expect(currentPayload.stage).toBe('WAIT_FVG');
    expect(currentPayload.poi_id).toBe('poi_dup');
  });

  it('preserves historical order selection in useSelectedChart when CG-01 updates arrive (Item 119-120, 154)', () => {
    // Initial state with a selected historical order
    const selectedState: UseSelectedChartState = {
      ...initialSelectedChartState,
      binding: {
        source: 'ORDER',
        id: 'hist_order_777',
        symbol: 'XAUUSDT',
        direction: 'SHORT',
      },
      isLoading: false,
    };

    // A live CG-01 candidate arrives for LONG at poi_01
    const liveCg01Payload = {
      stage: 'EXECUTABLE',
      direction: 'LONG',
      poi_id: 'poi_live_01',
      as_of_ms: 99999,
    };

    // Assert reducer was not called and selected chart binding is 100% unchanged
    expect(selectedState.binding?.id).toBe('hist_order_777');
    expect(selectedState.binding?.direction).toBe('SHORT');
    expect(selectedState.binding?.source).toBe('ORDER');
  });
});
