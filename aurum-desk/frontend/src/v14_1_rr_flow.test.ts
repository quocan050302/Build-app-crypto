import { describe, expect, it } from 'vitest';
import { calculateClientRiskReward } from './utils/calculator';
import { formatRR, resolveSetupPriceLevels } from './utils/planLevels';
import { RiskRewardPrimitive } from './plugins/RiskRewardPrimitive';
import type { RiskRewardData } from './plugins/RiskRewardPrimitive';

const short = (): RiskRewardData => ({
  id: 'short-image', direction: 'SHORT', state: 'candidate',
  plannedEntry: 4199.79, stopLoss: 4204.79, takeProfit: 4198,
  quantity: 0.29, initialRiskUsdt: 2.48, riskPct: 0.25,
  grossRR: 0.358, estimatedNetRR: -0.194,
});

describe('V14.1 selected R:R flow', () => {
  it('reproduces the screenshot without inventing a farther TP', () => {
    const calc = calculateClientRiskReward('SHORT', 4199.79, 4204.79, 4198, 999.57, 0.25, 2, 0.29, 30);
    expect(calc.stopDistance).toBe(5);
    expect(calc.targetDistance).toBe(1.79);
    expect(calc.grossRR).toBeCloseTo(0.358, 4);
    expect(calc.grossLossUsdt).toBe(1.45);
    expect(calc.grossRewardUsdt).toBe(0.52);
    expect(calc.netRewardUsdt).toBe(-0.48);
    expect(calc.netRiskUsdt).toBe(2.48);
    expect(calc.canExecute).toBe(false);
    expect(calc.takeProfit).toBe(4198);
  });
  it('keeps a zero or negative net ratio visible', () => {
    expect(formatRR(0)).toBe('1:0.00');
    expect(formatRR(-0.194)).toBe('1:-0.19');
    expect(formatRR(undefined)).toBe('—');
    expect(formatRR(NaN)).toBe('—');
  });
  it('rejects partial confirmed prices instead of creating a hybrid plan', () => {
    const result = resolveSetupPriceLevels({
      direction: 'SHORT', confirmed_entry: 4190,
      provisional_entry: 4199.79, provisional_sl: 4204.79, provisional_tp: 4198,
    });
    expect(result.isValid).toBe(false);
  });
  it('falls back atomically from stale unarmed LONG prices to a SHORT triple', () => {
    const result = resolveSetupPriceLevels({
      direction: 'SHORT', state: 'WATCHING', confirmed_entry: 4000, confirmed_sl: 3990, confirmed_tp: 4030,
      provisional_entry: 4199.79, provisional_sl: 4204.79, provisional_tp: 4198,
    });
    expect(result).toEqual({ isValid: true, plan: {
      direction: 'SHORT', entry: 4199.79, stopLoss: 4204.79, takeProfit: 4198, levelSource: 'provisional',
    } });
  });
  it('does not replace an invalid armed plan or invent a direction', () => {
    expect(resolveSetupPriceLevels({ direction: undefined }).isValid).toBe(false);
    expect(resolveSetupPriceLevels({ direction: 'SHORT', state: 'ARMED',
      confirmed_entry: 4000, confirmed_sl: 3990, confirmed_tp: 4030,
      provisional_entry: 4199.79, provisional_sl: 4204.79, provisional_tp: 4198,
    }).isValid).toBe(false);
  });
  it('includes SHORT SL above the candle range and excludes liquidation from scale', () => {
    const primitive = new RiskRewardPrimitive({ ...short(), estimatedLiquidation: 99999 });
    const scale = primitive.autoscaleInfo();
    expect(scale?.priceRange?.maxValue).toBeGreaterThan(4204.79);
    expect(scale?.priceRange?.minValue).toBeLessThan(4198);
    expect(scale?.priceRange?.maxValue).toBeLessThan(4210);
    expect(new RiskRewardPrimitive({ ...short(), stopLoss: NaN }).autoscaleInfo()).toBeNull();
  });
  it('maps SHORT price bands to the correct coordinates and locks a filled trade', () => {
    const primitive = new RiskRewardPrimitive(short());
    primitive.attached({
      chart: { timeScale: () => ({ getVisibleLogicalRange: () => ({ from: 0, to: 50 }),
        logicalToCoordinate: (n: number) => n * 10, timeToCoordinate: () => 300 }) },
      series: { priceToCoordinate: (p: number) => (4210 - p) * 20 },
      requestUpdate: () => {},
    } as never);
    const geometry = primitive.getGeometry();
    expect(geometry.slY!).toBeLessThan(geometry.entryY!);
    expect(geometry.tpY!).toBeGreaterThan(geometry.entryY!);
    expect(primitive.hitTest(geometry.startX + 16, geometry.entryY!)).not.toBeNull();
    primitive.setData({ ...short(), state: 'paper_open', readOnly: true });
    expect(primitive.hitTest(geometry.startX + 16, geometry.entryY!)).toBeNull();
  });
});
