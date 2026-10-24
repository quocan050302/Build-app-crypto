// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, describe, it, expect } from 'vitest';
import { StopPlacementDetails } from '../components/StopPlacementDetails';
import { StopComparisonModal } from '../components/StopComparisonModal';

describe('Aurum V17.5.1 — Stop Contract & UI Integration Suite', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    host.remove();
  });

  it('renders StopPlacementDetails with structural verified badge (C) when anchor is valid and variant is C', async () => {
    const order = {
      id: 'order_1',
      stop_loss: 2009.50,
      initial_risk_usdt: 2.50,
      estimated_net_rr: 2.15,
      cost_snapshot: {
        stop_placement: {
          status: 'VALID',
          mode: 'PAPER_STRUCTURAL',
          policy_version: 1,
          applied_variant: 'C',
          anchor: {
            kind: 'PROTECTED_LEG_HIGH',
            price: 2008.0,
            timeframe: '1M',
            price_basis: 'CANDLE_LAST',
          },
          buffer: {
            buffer_price: 1.50,
            dominant_source: 'atr_m1',
          },
          metrics: {
            price_distance: 9.50,
            risk_usdt: 2.50,
          },
          volatility_context: {
            atr_m1: {
              value: 1.50,
              method: 'SMA_TR_V1',
            },
          },
        },
      },
    };

    await act(async () => {
      root.render(<StopPlacementDetails order={order} />);
    });

    expect(host.textContent).toContain('PAPER_STRUCTURAL v1');
    expect(host.textContent).toContain('2009.50');
    expect(host.textContent).toContain('PROTECTED_LEG_HIGH tại 2008.00 (1M)');
    expect(host.textContent).toContain('1.50 (chính: atr_m1)');
    expect(host.textContent).toContain('9.50 (ATR: 1.50)');
    expect(host.textContent).toContain('2.50 USDT');
    expect(host.textContent).toContain('2.15');
    expect(host.textContent).toContain('Xác minh cấu trúc (C)');
  });

  it('renders legacy valid badge (A) when applied variant is A even if status is VALID', async () => {
    const order = {
      id: 'order_2',
      stop_loss: 2005.00,
      initial_risk_usdt: 2.50,
      estimated_net_rr: 1.95,
      cost_snapshot: {
        stop_placement: {
          status: 'VALID',
          mode: 'SHADOW_COMPARE',
          policy_version: 1,
          applied_variant: 'A',
          anchor: null,
          buffer: null,
          metrics: {
            price_distance: 5.00,
            risk_usdt: 2.50,
          },
        },
      },
    };

    await act(async () => {
      root.render(<StopPlacementDetails order={order} />);
    });

    expect(host.textContent).toContain('SHADOW_COMPARE v1');
    expect(host.textContent).toContain('2005.00');
    expect(host.textContent).toContain('SL Legacy hợp lệ (A)');
  });

  it('StopComparisonModal renders actual variant_a, variant_b, and variant_c from backend plan', async () => {
    const stopComparison = {
      schema_version: 'stop-comparison.v1',
      plan_id: 'plan_123',
      variant_a: {
        can_execute: true,
        stop_loss: 2005.00,
        price_distance: 5.00,
        quantity: 0.50,
        estimated_net_rr: 2.50,
      },
      variant_b: {
        can_execute: true,
        stop_loss: 2008.50,
        price_distance: 8.50,
        quantity: 0.29,
        estimated_net_rr: 2.05,
      },
      variant_c: {
        can_execute: true,
        stop_loss: 2009.50,
        price_distance: 9.50,
        quantity: 0.26,
        estimated_net_rr: 1.95,
      },
    };

    await act(async () => {
      root.render(
        <StopComparisonModal
          isOpen={true}
          onClose={() => {}}
          stopComparison={stopComparison}
          stopPlacement={{ mode: 'SHADOW_COMPARE' }}
        />
      );
    });

    expect(host.textContent).toContain('2005.00');
    expect(host.textContent).toContain('2008.50');
    expect(host.textContent).toContain('2009.50');
  });
});
