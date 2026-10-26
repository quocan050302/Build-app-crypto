import { describe, expect, it } from 'vitest';
import { readStructuralReferencePrice } from './types/smcScenario';
import type { HtfContextDTO, StructuralReferenceDTO } from './types/smcScenario';

describe('V16.13 HTF Evidence and Structural Reference', () => {
  it('reads valid structural reference price from object', () => {
    const ref: StructuralReferenceDTO = {
      id: 'sw_h_1',
      price: 2560.5,
      occurred_at_ms: 1700000000000,
      known_at_ms: 1700000060000,
    };
    expect(readStructuralReferencePrice(ref)).toBe(2560.5);
  });

  it('reads legacy primitive number price', () => {
    expect(readStructuralReferencePrice(2520.25)).toBe(2520.25);
  });

  it('returns null for null, undefined, or missing price', () => {
    expect(readStructuralReferencePrice(null)).toBeNull();
    expect(readStructuralReferencePrice(undefined)).toBeNull();
    expect(readStructuralReferencePrice({ id: 'sw_missing_price' } as any)).toBeNull();
  });

  it('strictly returns null for non-finite values (prevents $NaN and crash)', () => {
    expect(readStructuralReferencePrice({ price: NaN } as any)).toBeNull();
    expect(readStructuralReferencePrice({ price: Infinity } as any)).toBeNull();
    expect(readStructuralReferencePrice({ price: -Infinity } as any)).toBeNull();
    expect(readStructuralReferencePrice(NaN)).toBeNull();
    expect(readStructuralReferencePrice(Infinity)).toBeNull();
  });

  it('prevents parseFloat(String(object)) evaluation that produced NaN in V16.12', () => {
    const rawObject = { id: 'sw_1', price: 2520.0 };
    // In V16.12: Number(rawObject) -> NaN -> `$${Number(rawObject).toFixed(2)}` -> "$NaN"
    expect(Number(rawObject)).toBeNaN();

    const price = readStructuralReferencePrice(rawObject);
    expect(price).toBe(2520.0);
    expect(`$${price!.toFixed(2)}`).toBe('$2520.00');
  });

  it('validates HtfContextDTO structure with schema v16.13', () => {
    const htf: HtfContextDTO = {
      schema_version: 'v16.13',
      evaluation_as_of_ms: 1700000000000,
      h1_snapshot: {
        symbol: 'ETHUSDT',
        timeframe: '1H',
        as_of_ms: 1700000000000,
        bias: 'BULLISH',
        phase: 'ESTABLISHED',
        data_status: 'READY',
        protected_low: { id: 'sw_l_1', price: 2520.0, occurred_at_ms: 1700000000000, known_at_ms: 1700000060000 },
        effective_settings: { min_bars: 12, tick_size: 0.1 },
        settings_fingerprint: 'a1b2c3d4e5f6',
      },
      direction_context: {
        relation: 'ALIGNED',
        executable_permission: true,
        bias: 'BULLISH',
      },
    };

    expect(htf.schema_version).toBe('v16.13');
    const pLow = readStructuralReferencePrice(htf.h1_snapshot?.protected_low);
    expect(pLow).toBe(2520.0);
    expect(pLow!.toFixed(2)).toBe('2520.00');
  });
});
