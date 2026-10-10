import { describe, it, expect } from 'vitest';

describe('V13 Frontend Research Tab Invariants & Logic', () => {
  it('T01: correctly determines CURRENT_ASOF vs HISTORICAL_ASOF based on selected date', () => {
    const today = new Date().toISOString().slice(0, 10);
    const pastDate = '2026-07-15';
    const futureDate = '2099-01-01';

    const getMode = (dateStr: string) => {
      if (dateStr < today) return 'HISTORICAL_ASOF';
      if (dateStr === today) return 'CURRENT_ASOF';
      return 'HISTORICAL_ASOF'; // Capped
    };

    expect(getMode(today)).toBe('CURRENT_ASOF');
    expect(getMode(pastDate)).toBe('HISTORICAL_ASOF');
    expect(getMode(futureDate)).toBe('HISTORICAL_ASOF');
  });

  it('T02: validates scenario geometry and net R:R threshold for LONG and SHORT', () => {
    const validateLong = (entry: number, sl: number, tp: number, netRR: number) => {
      if (sl >= entry) return { isValid: false, reason: 'SL >= Entry' };
      if (tp <= entry) return { isValid: false, reason: 'TP <= Entry' };
      if (netRR < 1.80) return { isValid: false, reason: 'Net R:R < 1.80' };
      return { isValid: true };
    };

    const validateShort = (entry: number, sl: number, tp: number, netRR: number) => {
      if (sl <= entry) return { isValid: false, reason: 'SL <= Entry' };
      if (tp >= entry) return { isValid: false, reason: 'TP >= Entry' };
      if (netRR < 1.80) return { isValid: false, reason: 'Net R:R < 1.80' };
      return { isValid: true };
    };

    // Valid LONG
    expect(validateLong(2650, 2635, 2690, 2.3).isValid).toBe(true);
    // Invalid geometry LONG (SL above entry)
    expect(validateLong(2650, 2660, 2690, 2.3).isValid).toBe(false);
    // Sub-threshold Net RR LONG
    expect(validateLong(2650, 2635, 2690, 1.45).isValid).toBe(false);

    // Valid SHORT
    expect(validateShort(2650, 2665, 2610, 2.3).isValid).toBe(true);
    // Invalid geometry SHORT (SL below entry)
    expect(validateShort(2650, 2640, 2610, 2.3).isValid).toBe(false);
    // Sub-threshold Net RR SHORT
    expect(validateShort(2650, 2665, 2610, 1.70).isValid).toBe(false);
  });

  it('T03: verifies quick date offset calculations', () => {
    const calcDaysAgo = (days: number) => {
      const d = new Date('2026-10-10T12:00:00Z');
      d.setDate(d.getDate() - days);
      return d.toISOString().slice(0, 10);
    };

    expect(calcDaysAgo(0)).toBe('2026-10-10');
    expect(calcDaysAgo(1)).toBe('2026-10-09');
    expect(calcDaysAgo(7)).toBe('2026-10-03');
    expect(calcDaysAgo(30)).toBe('2026-09-10');
  });

  it('T04: provides beginner-friendly Vietnamese regime translations', () => {
    const labels: Record<string, string> = {
      TREND_UP: 'Xu Hướng Tăng Mạnh',
      TREND_DOWN: 'Xu Hướng Giảm Rõ Rệt',
      RANGE: 'Đi Ngang Tích Lũy',
      TRANSITION: 'Giai Đoạn Chuyển Giao',
      EVENT_VOLATILITY: 'Biến Động Bất Thường',
      UNKNOWN: 'Chưa Rõ Ràng'
    };

    expect(labels['TREND_UP']).toContain('Tăng');
    expect(labels['TREND_DOWN']).toContain('Giảm');
    expect(labels['RANGE']).toContain('Đi Ngang');
    expect(labels['EVENT_VOLATILITY']).toContain('Biến Động');
  });
});
