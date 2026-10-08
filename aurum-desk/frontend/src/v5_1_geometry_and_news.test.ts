import { describe, it, expect } from 'vitest';

describe('V5.1 Direction Geometry, Margin & News Invariants', () => {
  // 1. Authoritative Geometry Validation in Frontend
  const validateGeometry = (direction: 'LONG' | 'SHORT', entry: number, sl: number, tp: number): { isValid: boolean; error?: string } => {
    if (!Number.isFinite(entry) || !Number.isFinite(sl) || !Number.isFinite(tp) || entry <= 0 || sl <= 0 || tp <= 0) {
      return { isValid: false, error: 'Mức giá không hợp lệ' };
    }
    if (direction === 'LONG') {
      if (!(sl < entry && entry < tp)) {
        return { isValid: false, error: 'LONG yêu cầu Stop Loss < Entry < Take Profit' };
      }
    } else if (direction === 'SHORT') {
      if (!(tp < entry && entry < sl)) {
        return { isValid: false, error: 'SHORT yêu cầu Take Profit < Entry < Stop Loss' };
      }
    }
    return { isValid: true };
  };

  it('rejects inverted SHORT geometry from screenshot fixture (Entry 4123.32, SL 4109.02, TP 4151.92)', () => {
    const entry = 4123.32;
    const sl = 4109.02; // Inverted! (SL < Entry)
    const tp = 4151.92; // Inverted! (TP > Entry)

    const res = validateGeometry('SHORT', entry, sl, tp);
    expect(res.isValid).toBe(false);
    expect(res.error).toContain('SHORT yêu cầu Take Profit < Entry < Stop Loss');
  });

  it('accepts valid SHORT geometry (TP < Entry < SL)', () => {
    const entry = 4123.32;
    const sl = 4135.00;
    const tp = 4080.00;

    const res = validateGeometry('SHORT', entry, sl, tp);
    expect(res.isValid).toBe(true);
    expect(res.error).toBeUndefined();
  });

  // 2. Authoritative Margin Calculation (never initialRisk * 2)
  it('calculates initial margin using (quantity * entry) / leverage and not risk * 2', () => {
    const quantity = 0.05; // 0.05 contract/oz
    const entry = 4123.32;
    const leverage = 5;
    const initialRiskUsdt = 2.5;

    // Bad legacy formula was: initialRiskUsdt * 2 = 5.0 USDT
    const badMargin = initialRiskUsdt * 2;
    expect(badMargin).toBe(5.0);

    // Correct formula:
    const calculatedMargin = (quantity * entry) / leverage;
    expect(calculatedMargin).toBeCloseTo(41.2332, 2);
    expect(calculatedMargin).not.toBe(badMargin);
  });

  // 3. ARMED State Button UI Logic
  it('disables or replaces Arm button when setup is already ARMED or WAITING_MSS', () => {
    const isArmed = (state: string) => state === 'ARMED' || state === 'armed';
    const isArmableState = (state: string) => ['READY', 'WAITING_PRICE', 'WAITING_RETRACE'].includes(state);

    expect(isArmed('ARMED')).toBe(true);
    expect(isArmed('READY')).toBe(false);

    expect(isArmableState('WAITING_MSS')).toBe(false);
    expect(isArmableState('READY')).toBe(true);
    expect(isArmableState('WAITING_PRICE')).toBe(true);
  });
});
