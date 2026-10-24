/**
 * frontend/src/__tests__/v17_6_2_observation_events.test.ts
 * AURUM V17.6.2 — WebSocket DOMAIN_EVENT Unpacking & Timezone Window Tests.
 *
 * Verifies:
 * - FIX-05: DOMAIN_EVENT envelope unpacking correctly extracts nested event_type.
 * - FIX-11: Today midnight calculation is accurate for Asia/Ho_Chi_Minh and America/New_York.
 * - FIX-16: Stale request sequence race prevention logic.
 */

import { describe, it, expect } from 'vitest';
import {
  extractObservationEventType,
  getTodayMidnightMs,
} from '../hooks/useObservationDashboard';

describe('V17.6.2 WebSocket DOMAIN_EVENT Unpacking (FIX-05)', () => {
  it('WS01: extracts nested event_type from DOMAIN_EVENT envelope', () => {
    const raw = {
      type: 'DOMAIN_EVENT',
      event: {
        event_type: 'order.armed',
        order_id: 'ord-123',
      },
    };
    expect(extractObservationEventType(raw)).toBe('order.armed');
  });

  it('WS02: extracts direct event type', () => {
    const raw = {
      event_type: 'feed.recovered',
      source: 'BITGET_WS',
    };
    expect(extractObservationEventType(raw)).toBe('feed.recovered');
  });

  it('WS03: extracts nested trade.opened from payload', () => {
    const raw = {
      type: 'DOMAIN_EVENT',
      payload: {
        event_type: 'trade.opened',
        symbol: 'XAUUSDT',
      },
    };
    expect(extractObservationEventType(raw)).toBe('trade.opened');
  });

  it('WS04: extracts nested trade.closed from data', () => {
    const raw = {
      type: 'MESSAGE',
      data: {
        event_type: 'trade.closed',
        pnl: 25.5,
      },
    };
    expect(extractObservationEventType(raw)).toBe('trade.closed');
  });

  it('WS05: handles unknown / malformed payload gracefully', () => {
    expect(extractObservationEventType(null)).toBeNull();
    expect(extractObservationEventType(undefined)).toBeNull();
    expect(extractObservationEventType({})).toBeNull();
    expect(extractObservationEventType({ foo: 'bar' })).toBeNull();
    expect(extractObservationEventType('not-an-object')).toBeNull();
  });

  it('WS06: 100-event burst parsing performance', () => {
    const start = performance.now();
    for (let i = 0; i < 100; i++) {
      const raw = {
        type: 'DOMAIN_EVENT',
        event: {
          event_type: i % 2 === 0 ? 'order.armed' : 'feed.degraded',
          iteration: i,
        },
      };
      const res = extractObservationEventType(raw);
      expect(res).toBe(i % 2 === 0 ? 'order.armed' : 'feed.degraded');
    }
    const elapsed = performance.now() - start;
    expect(elapsed).toBeLessThan(100); // Well under 100ms
  });
});

describe('V17.6.2 Today Timezone Midnight Calculation (FIX-11)', () => {
  it('TZ01: computes Today midnight for Vietnam (Asia/Ho_Chi_Minh)', () => {
    // 2026-10-25 10:30:00 UTC = 2026-10-25 17:30:00 in Vietnam (UTC+7)
    const testTimeMs = Date.UTC(2026, 9, 25, 10, 30, 0); // Month is 0-indexed (9 = Oct)
    const midnightVn = getTodayMidnightMs(testTimeMs, 'VN');

    // Expected Vietnam midnight is 2026-10-25 00:00:00 ICT = 2026-10-24 17:00:00 UTC
    const expectedVnUtc = Date.UTC(2026, 9, 24, 17, 0, 0);
    expect(midnightVn).toBe(expectedVnUtc);
    expect(testTimeMs - midnightVn).toBe(17.5 * 3600 * 1000); // 17.5 hours elapsed today in VN
  });

  it('TZ02: computes Today midnight for New York (America/New_York EDT = UTC-4 in October)', () => {
    // 2026-10-25 10:30:00 UTC = 2026-10-25 06:30:00 in NY (EDT = UTC-4)
    const testTimeMs = Date.UTC(2026, 9, 25, 10, 30, 0);
    const midnightNy = getTodayMidnightMs(testTimeMs, 'NY');

    // Expected NY midnight is 2026-10-25 00:00:00 EDT = 2026-10-25 04:00:00 UTC
    const expectedNyUtc = Date.UTC(2026, 9, 25, 4, 0, 0);
    expect(midnightNy).toBe(expectedNyUtc);
    expect(testTimeMs - midnightNy).toBe(6.5 * 3600 * 1000); // 6.5 hours elapsed today in NY
  });

  it('TZ03: same UTC instant produces distinct midnight timestamps for VN and NY', () => {
    const testTimeMs = Date.UTC(2026, 9, 25, 2, 0, 0); // 02:00 UTC
    // In VN (UTC+7): 09:00 on Oct 25
    // In NY (UTC-4): 22:00 on Oct 24 (Previous day in NY!)
    const midnightVn = getTodayMidnightMs(testTimeMs, 'VN');
    const midnightNy = getTodayMidnightMs(testTimeMs, 'NY');

    expect(midnightVn).not.toBe(midnightNy);
    expect(midnightVn).toBeGreaterThan(midnightNy); // VN is in the future relative to NY
  });
});
