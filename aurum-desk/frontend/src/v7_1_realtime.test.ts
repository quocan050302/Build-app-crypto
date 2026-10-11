import { describe, it, expect } from 'vitest';
import { getNextRequestGeneration, isLatestGeneration } from './api/client';

describe('V7.1 Realtime Request Generation Scoping', () => {
  it('scopes generation counter per resource independently', () => {
    const genA1 = getNextRequestGeneration('chart_XAUUSDT_15M');
    const genB1 = getNextRequestGeneration('chart_XAUUSDT_5M');

    expect(genA1).toBe(1);
    expect(genB1).toBe(1);

    const genA2 = getNextRequestGeneration('chart_XAUUSDT_15M');
    expect(genA2).toBe(2);

    // Resource A advanced to 2, so genA1 is no longer latest for A
    expect(isLatestGeneration(genA1, 'chart_XAUUSDT_15M')).toBe(false);
    expect(isLatestGeneration(genA2, 'chart_XAUUSDT_15M')).toBe(true);

    // Resource B is still at 1, unaffected by Resource A
    expect(isLatestGeneration(genB1, 'chart_XAUUSDT_5M')).toBe(true);
  });
});
