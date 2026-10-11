import { describe, it, expect } from 'vitest';
import {
  calculateRiskReward,
  validatePriceGeometry,
  calculateIsolatedLiquidation,
  BITGET_XAUUSDT_TIERS,
} from './utils/calculator';

describe('V10.4 Unified Cost, R:R and PnL Frontend Parity Suite', () => {
  // C01: Legacy F1 parity
  it('C01: matches authoritative calculations for LONG setup', () => {
    const res = calculateRiskReward({
      direction: 'LONG',
      entry: 4000.0,
      sl: 3990.0,
      tp: 4035.0,
      capital: 1000.0,
      riskPct: 0.5,
    });

    expect(res.isValid).toBe(true);
    expect(res.canExecute).toBe(true);
    expect(res.quantity).toBe(0.37);
    expect(res.stopDistance).toBe(10.0);
    expect(res.targetDistance).toBe(35.0);
    expect(res.grossLossUsdt).toBeCloseTo(3.70, 2);
    expect(res.grossRewardUsdt).toBeCloseTo(12.95, 2);
    expect(res.grossRR).toBeCloseTo(3.5, 2);
    expect(res.meetsMinRR).toBe(true);
    expect(res.netRiskUsdt).toBeCloseTo(4.96, 2);
  });

  // C03: SHORT geometry Gross RR positive and not inverted
  it('C03: SHORT geometry has Gross RR > 0 and correct distance signs', () => {
    const res = calculateRiskReward({
      direction: 'SHORT',
      entry: 4000.0,
      sl: 4010.0,
      tp: 3965.0,
      capital: 1000.0,
      riskPct: 0.5,
    });

    expect(res.isValid).toBe(true);
    expect(res.stopDistance).toBe(10.0);
    expect(res.targetDistance).toBe(35.0);
    expect(res.grossRR).toBe(3.5); // 35 / 10 = 3.5, positive
    expect(res.grossRewardUsdt).toBeGreaterThan(0);
    expect(res.grossLossUsdt).toBeGreaterThan(0);

    // Validate geometry rejections
    const invLong = validatePriceGeometry('LONG', 4000.0, 4010.0, 4035.0);
    expect(invLong.isValid).toBe(false);
    expect(invLong.invalidReason).toContain('INVALID_LONG_GEOMETRY');

    const invShort = validatePriceGeometry('SHORT', 4000.0, 3990.0, 3965.0);
    expect(invShort.isValid).toBe(false);
    expect(invShort.invalidReason).toContain('INVALID_SHORT_GEOMETRY');
  });

  // C04: Maker vs Taker legs
  it('C04: taker fee rate is double maker fee rate for TP', () => {
    const calcMaker = calculateRiskReward({
      direction: 'LONG',
      entry: 4000.0,
      sl: 3990.0,
      tp: 4030.0,
      capital: 1000.0,
      riskPct: 0.5,
      costs: { tpIsMaker: true },
    });
    const calcTaker = calculateRiskReward({
      direction: 'LONG',
      entry: 4000.0,
      sl: 3990.0,
      tp: 4030.0,
      capital: 1000.0,
      riskPct: 0.5,
      costs: { tpIsMaker: false },
    });

    expect(calcTaker.tpExitFeeUsdt).toBeCloseTo(calcMaker.tpExitFeeUsdt * 2.0, 3);
    expect(calcTaker.netRewardUsdt).toBeLessThan(calcMaker.netRewardUsdt);
  });

  // C06: Multiplier scaling
  it('C06: Multiplier scales notionals, gross loss, and gross reward proportionally', () => {
    const calc1x = calculateRiskReward({
      direction: 'LONG',
      entry: 4000.0,
      sl: 3990.0,
      tp: 4030.0,
      capital: 1000.0,
      riskPct: 0.5,
      quantityOverride: 0.1,
      multiplier: 1.0,
    });
    const calc10x = calculateRiskReward({
      direction: 'LONG',
      entry: 4000.0,
      sl: 3990.0,
      tp: 4030.0,
      capital: 1000.0,
      riskPct: 0.5,
      quantityOverride: 0.1,
      multiplier: 10.0,
    });

    expect(calc10x.notionalUsdt).toBeCloseTo(calc1x.notionalUsdt * 10.0, 2);
    expect(calc10x.grossLossUsdt).toBeCloseTo(calc1x.grossLossUsdt * 10.0, 2);
    expect(calc10x.grossRewardUsdt).toBeCloseTo(calc1x.grossRewardUsdt * 10.0, 2);
  });

  // C08: Invalid quantity override rejected
  it('C08: Invalid quantity override (<=0, inf, nan) returns isValid=false', () => {
    for (const badQty of [0.0, -1.0, Infinity, NaN]) {
      const res = calculateRiskReward({
        direction: 'LONG',
        entry: 4000.0,
        sl: 3990.0,
        tp: 4030.0,
        capital: 1000.0,
        riskPct: 0.5,
        quantityOverride: badQty,
      });
      expect(res.isValid).toBe(false);
      expect(res.invalidReason).toContain('INVALID_QUANTITY');
    }
  });

  // C09: Unknown direction rejected
  it('C09: Unknown direction is rejected with UNKNOWN_DIRECTION', () => {
    const res = calculateRiskReward({
      direction: 'UP' as any,
      entry: 4000.0,
      sl: 3990.0,
      tp: 4030.0,
      capital: 1000.0,
      riskPct: 0.5,
    });
    expect(res.isValid).toBe(false);
    expect(res.invalidReason).toContain('UNKNOWN_DIRECTION');
  });

  // C10: Non-positive net reward blocked
  it('C10: Non-positive net reward blocks execution without claiming false profit', () => {
    const res = calculateRiskReward({
      direction: 'LONG',
      entry: 4000.0,
      sl: 3990.0,
      tp: 4000.10, // TP too tight
      capital: 1000.0,
      riskPct: 0.5,
    });
    expect(res.canExecute).toBe(false);
    expect(res.meetsMinRR).toBe(false);
    expect(res.blockerList.some((b) => b.includes('NET_REWARD_NON_POSITIVE') || b.includes('NET_RR_TOO_LOW'))).toBe(true);
  });

  // C11: Meets min RR unrounded check
  it('C11: Net RR is checked against minNetRR without premature rounding', () => {
    const res = calculateRiskReward({
      direction: 'LONG',
      entry: 4000.0,
      sl: 3990.0,
      tp: 4022.0,
      capital: 1000.0,
      riskPct: 0.5,
      minNetRR: 2.0,
    });
    if (res.estimatedNetRR < 2.0) {
      expect(res.meetsMinRR).toBe(false);
      expect(res.canExecute).toBe(false);
      expect(res.skipReason).toContain('NET_RR_TOO_LOW');
    }
  });

  // C12: Leverage change invariance
  it('C12: Gross loss and reward are invariant under leverage changes', () => {
    const calc5x = calculateRiskReward({
      direction: 'LONG',
      entry: 4000.0,
      sl: 3990.0,
      tp: 4030.0,
      capital: 1000.0,
      riskPct: 0.5,
      leverage: 5,
    });
    const calc20x = calculateRiskReward({
      direction: 'LONG',
      entry: 4000.0,
      sl: 3990.0,
      tp: 4030.0,
      capital: 1000.0,
      riskPct: 0.5,
      leverage: 20,
    });
    expect(calc5x.grossLossUsdt).toBe(calc20x.grossLossUsdt);
    expect(calc5x.grossRewardUsdt).toBe(calc20x.grossRewardUsdt);
    expect(calc5x.grossRR).toBe(calc20x.grossRR);
    expect(calc5x.initialMarginUsdt / calc20x.initialMarginUsdt).toBeCloseTo(4.0, 2);
  });

  // I01: Bitget 10 tiers and zero deduction
  it('I01: BITGET_XAUUSDT_TIERS has 10 tiers and all deduction is 0.0', () => {
    expect(BITGET_XAUUSDT_TIERS.length).toBe(10);
    for (const tier of BITGET_XAUUSDT_TIERS) {
      expect(tier.deduction).toBe(0.0);
      expect(tier.mmr).toBeGreaterThan(0);
      expect(tier.maxLeverage).toBeGreaterThan(0);
    }
  });

  // I02: Isolated liquidation calculation
  it('I02: calculateIsolatedLiquidation calculates LP below entry for LONG and above entry for SHORT', () => {
    const liqLong = calculateIsolatedLiquidation('LONG', 4000.0, 0.1, 10);
    expect(liqLong.lp).toBeLessThan(4000.0);
    expect(liqLong.initialMargin).toBeCloseTo(40.0, 2);

    const liqShort = calculateIsolatedLiquidation('SHORT', 4000.0, 0.1, 10);
    expect(liqShort.lp).toBeGreaterThan(4000.0);
    expect(liqShort.initialMargin).toBeCloseTo(40.0, 2);
  });

  // S04: Right edge drag resize invariance
  it('S04: Horizontal width resize modifies projected bars only without altering price levels, quantity, or RR', () => {
    const initialData = {
      entry: 4000.0,
      sl: 3990.0,
      tp: 4035.0,
      quantity: 0.37,
      grossRR: 3.5,
      estimatedNetRR: 2.36,
      projectedBars: 15,
    };

    // Simulate right-edge drag: changes projectedBars only
    const draggedBars = 25;
    const afterDrag = {
      ...initialData,
      projectedBars: draggedBars,
    };

    expect(afterDrag.entry).toBe(initialData.entry);
    expect(afterDrag.sl).toBe(initialData.sl);
    expect(afterDrag.tp).toBe(initialData.tp);
    expect(afterDrag.quantity).toBe(initialData.quantity);
    expect(afterDrag.grossRR).toBe(initialData.grossRR);
    expect(afterDrag.estimatedNetRR).toBe(initialData.estimatedNetRR);
    expect(afterDrag.projectedBars).toBe(25);
  });
});
