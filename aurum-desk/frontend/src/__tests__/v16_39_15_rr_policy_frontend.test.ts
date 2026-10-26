import { describe, it, expect } from 'vitest';
import { resolvePrimitiveRrStatus, type RiskRewardData } from '../plugins/RiskRewardPrimitive';
import { calculateClientRiskReward } from '../utils/calculator';

describe('V16.39.15 Frontend RR Policy Unification (Rules 112-115, 173-179)', () => {
  it('A12.1: resolvePrimitiveRrStatus correctly passes when net RR >= minNetRR (1.50)', () => {
    const data: RiskRewardData = {
      id: 'test-rr-1',
      plannedEntry: 2700,
      stopLoss: 2690,
      takeProfit: 2720,
      direction: 'LONG',
      quantity: 1.0,
      initialRiskUsdt: 10,
      riskPct: 0.25,
      grossRR: 2.0,
      estimatedNetRR: 1.75,
      minNetRR: 1.5,
      state: 'draft',
    };

    const status = resolvePrimitiveRrStatus(data);
    expect(status.hasKnownThreshold).toBe(true);
    expect(status.meetsMinRR).toBe(true);
    expect(status.isPass).toBe(true);
    expect(status.rrPassText).toBe('');
    expect(status.strokeColor).toBe('#26a69a'); // LONG green
    expect(status.textColor).toBe('#f4f4f5');
  });

  it('A12.2: resolvePrimitiveRrStatus fails when net RR < minNetRR (1.6 < 2.0)', () => {
    const data: RiskRewardData = {
      id: 'test-rr-2',
      plannedEntry: 2700,
      stopLoss: 2690,
      takeProfit: 2720,
      direction: 'LONG',
      quantity: 1.0,
      initialRiskUsdt: 10,
      riskPct: 0.25,
      grossRR: 2.0,
      estimatedNetRR: 1.75,
      minNetRR: 2.0,
      state: 'draft',
    };

    const status = resolvePrimitiveRrStatus(data);
    expect(status.hasKnownThreshold).toBe(true);
    expect(status.meetsMinRR).toBe(false);
    expect(status.isPass).toBe(false);
    expect(status.rrPassText).toBe(' · Chưa đạt R:R');
    expect(status.strokeColor).toBe('#eab308'); // Warning yellow
    expect(status.textColor).toBe('#fef08a');
  });

  it('A12.3: resolvePrimitiveRrStatus handles unknown historical threshold neutrally (Rule 114)', () => {
    const data: RiskRewardData = {
      id: 'test-rr-3',
      plannedEntry: 2700,
      stopLoss: 2710,
      takeProfit: 2680,
      direction: 'SHORT',
      quantity: 1.0,
      initialRiskUsdt: 10,
      riskPct: 0.25,
      grossRR: 2.0,
      estimatedNetRR: 1.75,
      minNetRR: undefined, // Legacy without stored snapshot
      state: 'paper_open',
    };

    const status = resolvePrimitiveRrStatus(data);
    expect(status.hasKnownThreshold).toBe(false);
    expect(status.meetsMinRR).toBe(false);
    expect(status.isPass).toBe(false);
    expect(status.rrPassText).toBe(' · Ngưỡng chưa xác định');
    expect(status.strokeColor).toBe('#a1a1aa'); // Neutral gray
    expect(status.textColor).toBe('#d4d4d8');
  });

  it('A12.4: calculateClientRiskReward honors minNetRR=1.5 without premature rounding', () => {
    // With entry=2700, sl=2690 (dist 10), tp=2725 (dist 25):
    // netRisk ~ 2.47, netReward ~ 4.55, netRR ~ 1.84
    const calc = calculateClientRiskReward(
      'LONG',
      2700,
      2690,
      2725,
      1000,
      0.25,
      1.5,
      undefined,
      5,
      'ISOLATED'
    );

    expect(calc.minNetRR).toBe(1.5);
    expect(calc.estimatedNetRR).toBeGreaterThanOrEqual(1.5);
    expect(calc.estimatedNetRR).toBeLessThan(2.0);
    expect(calc.meetsMinRR).toBe(true);
    expect(calc.canExecute).toBe(true);

    // Same geometry with minNetRR=2.0 fails meetsMinRR since net RR (~1.84) < 2.0
    const calc2 = calculateClientRiskReward(
      'LONG',
      2700,
      2690,
      2725,
      1000,
      0.25,
      2.0,
      undefined,
      5,
      'ISOLATED'
    );
    expect(calc2.minNetRR).toBe(2.0);
    expect(calc2.meetsMinRR).toBe(false);
    expect(calc2.canExecute).toBe(false);
    expect(calc2.skipReason).toContain('NET_RR_TOO_LOW');
  });

  it('A12.5: SHORT direction geometry with net RR in [1.5, 2.0)', () => {
    // entry=2700, sl=2710 (dist 10), tp=2675 (dist 25): netRR ~ 1.84 in [1.5, 2.0)
    const calcShort = calculateClientRiskReward(
      'SHORT',
      2700,
      2710, // SL above entry (dist 10)
      2675, // TP below entry (dist 25, net RR ~ 1.84)
      1000,
      0.25,
      1.5,
      undefined,
      5,
      'ISOLATED'
    );

    expect(calcShort.direction).toBe('SHORT');
    expect(calcShort.estimatedNetRR).toBeGreaterThanOrEqual(1.5);
    expect(calcShort.estimatedNetRR).toBeLessThan(2.0);
    expect(calcShort.meetsMinRR).toBe(true);
    expect(calcShort.canExecute).toBe(true);
  });
});
