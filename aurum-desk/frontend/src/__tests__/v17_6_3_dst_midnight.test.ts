import { describe, it, expect } from 'vitest';
import { getTodayMidnightMs } from '../hooks/useObservationDashboard';

describe('V17.6.3: selected timezone calendar-day midnight across DST', () => {
  it('uses EDT midnight before the November 2026 fall-back', () => {
    // Later in the same NY day, after clocks moved from EDT to EST.
    const now = Date.UTC(2026, 10, 1, 18, 30);
    expect(getTodayMidnightMs(now, 'NY')).toBe(Date.UTC(2026, 10, 1, 4));
  });

  it('uses EST midnight before the March 2026 spring-forward', () => {
    // Later in the same NY day, after clocks moved from EST to EDT.
    const now = Date.UTC(2026, 2, 8, 18, 30);
    expect(getTodayMidnightMs(now, 'NY')).toBe(Date.UTC(2026, 2, 8, 5));
  });

  it('keeps Vietnam midnight independent of the browser timezone', () => {
    const now = Date.UTC(2026, 10, 1, 18, 30);
    expect(getTodayMidnightMs(now, 'VN')).toBe(Date.UTC(2026, 10, 1, 17));
  });
});
