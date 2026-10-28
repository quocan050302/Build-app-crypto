// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, describe, it, expect } from 'vitest';
import { SLTPIntelligenceView } from '../components/SLTPIntelligenceView';

describe('Aurum V17.8.0.1 — SLTPIntelligenceView Component Suite', () => {
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

  it('renders full V17.8.0.1 Unicorn structural stop evidence with provenance and financials', async () => {
    const evidenceData = {
      status: 'AVAILABLE',
      order_id: 'ord_v17_8_0_1_unicorn_short',
      direction: 'SHORT',
      actual_entry: 4021.08,
      actual_sl: 4024.80,
      executed_sl: 4024.80,
      take_profit: 4002.34,
      legacy_sl: 4021.67,
      structural_sl: 4024.80,
      unicorn_sl_candidate: 4024.80,
      min_protective_distance: 1.38,
      protective_expansion_applied: false,
      stop_mode_at_arm: 'PAPER_STRUCTURAL',
      applied_variant: 'C',
      quantity: 0.35,
      initial_risk_usdt: 2.50,
      estimated_net_rr: 2.27,
      stop_placement: {
        status: 'VALID',
        mode: 'PAPER_STRUCTURAL',
        policy_version: 2,
        applied_variant: 'C',
        anchor: {
          id: 'anc_unicorn_brk_short_1m',
          kind: 'BREAKER_DISTAL_BOUNDARY',
          price: 4023.42,
          timeframe: '1M',
        },
        buffer: {
          buffer_price: 1.38,
          dominant_source: 'atr_m1',
        },
        volatility_context: {
          atr_m1: { value: 2.10, method: 'SMA_TR_V1' },
          atr_m5: { value: 4.50, method: 'SMA_TR_V1' },
          spread: 0.30,
        },
      },
      unicorn_evidence: {
        is_valid: true,
        validity_status: 'CONFIRMED',
        breaker_id: 'brk_short_1m_1793102160000',
        selected_fvg_id: 'fvg-bear-1M-1793102220000',
        setup_episode_id: 'ep_SHORT_1M_fvg-bear-1M-1793102220000',
        invalidation_price: 4023.42,
        overlap_low: 4021.10,
        overlap_high: 4021.62,
      },
      ui_explanation_vi: 'Cấu trúc Unicorn xác nhận Breaker hợp lệ.',
    };

    await act(async () => {
      root.render(<SLTPIntelligenceView evidenceData={evidenceData} />);
    });

    expect(host.textContent).toContain('V17.8.0.1');
    expect(host.textContent).toContain('PAPER_STRUCTURAL');
    expect(host.textContent).toContain('4021.08');
    expect(host.textContent).toContain('4024.80');
    expect(host.textContent).toContain('4002.34');
    expect(host.textContent).toContain('1.38 USD');
    expect(host.textContent).toContain('0.35 oz');
    expect(host.textContent).toContain('2.50 USDT');
    expect(host.textContent).toContain('2.27R');
    expect(host.textContent).toContain('UNICORN_CONFIRMED');
    expect(host.textContent).toContain('brk_short_1m_1793102160000');
    expect(host.textContent).toContain('fvg-bear-1M-1793102220000');
    expect(host.textContent).toContain('4023.42');
    expect(host.textContent).toContain('2.1000');
    expect(host.textContent).toContain('4.5000');
    expect(host.textContent).toContain('0.3000');
  });

  it('distinguishes executed Legacy SL (A) from research candidate Unicorn SL in SHADOW_COMPARE mode', async () => {
    const shadowData = {
      status: 'AVAILABLE',
      order_id: 'ord_shadow_short',
      direction: 'SHORT',
      actual_entry: 4021.08,
      actual_sl: 4021.67,
      executed_sl: 4021.67,
      take_profit: 4002.34,
      legacy_sl: 4021.67,
      structural_sl: 4024.80,
      unicorn_sl_candidate: 4024.80,
      min_protective_distance: 1.38,
      stop_mode_at_arm: 'SHADOW_COMPARE',
      applied_variant: 'A',
      quantity: 0.64,
      initial_risk_usdt: 2.48,
      estimated_net_rr: 3.12,
      stop_placement: {
        status: 'VALID',
        mode: 'SHADOW_COMPARE',
        applied_variant: 'A',
      },
    };

    await act(async () => {
      root.render(<SLTPIntelligenceView evidenceData={shadowData} />);
    });

    const modeBadge = host.querySelector('[data-testid="sltp-mode-badge"]');
    const variantBadge = host.querySelector('[data-testid="sltp-applied-variant-badge"]');
    expect(modeBadge?.textContent).toBe('SHADOW_COMPARE');
    expect(variantBadge?.textContent).toBe('A');
    expect(host.textContent).toContain('A (Legacy FVG) · [Đã thực thi]');
    expect(host.textContent).toContain('C (Unicorn SL) · [Ứng viên nghiên cứu]');
    expect(host.textContent).toContain('CHẾ ĐỘ ĐỐI CHIẾU / LEGACY');
  });

  it('never fabricates applied_variant="C" or stop_mode_at_arm="SHADOW_COMPARE" when fields are missing', async () => {
    const partialData = {
      status: 'AVAILABLE',
      order_id: 'ord_missing_fields',
      actual_entry: 2650.0,
      actual_sl: 2660.0,
      take_profit: 2625.0,
      stop_placement: {},
    };

    await act(async () => {
      root.render(<SLTPIntelligenceView evidenceData={partialData} />);
    });

    const modeBadge = host.querySelector('[data-testid="sltp-mode-badge"]');
    const variantBadge = host.querySelector('[data-testid="sltp-applied-variant-badge"]');
    const directionBadge = host.querySelector('[data-testid="sltp-direction-badge"]');
    expect(modeBadge?.textContent).toBe('NOT_RECORDED');
    expect(variantBadge?.textContent).toBe('NOT_RECORDED');
    expect(directionBadge?.textContent).toBe('UNKNOWN');
  });

  it('renders UNAVAILABLE_LEGACY and NOT_RECORDED for historical orders lacking V17.8 evidence', async () => {
    const legacyData = {
      status: 'UNAVAILABLE_LEGACY',
      order_id: 'ord_legacy_old',
      stop_mode_at_arm: 'NOT_RECORDED',
      applied_variant: 'NOT_RECORDED',
    };

    await act(async () => {
      root.render(<SLTPIntelligenceView evidenceData={legacyData} />);
    });

    expect(host.querySelector('[data-testid="sltp-intelligence-legacy"]')).not.toBeNull();
    expect(host.textContent).toContain('UNAVAILABLE_LEGACY');
    expect(host.textContent).toContain('NOT_RECORDED');
    expect(host.textContent).toContain('ord_legacy_old');
  });
});

