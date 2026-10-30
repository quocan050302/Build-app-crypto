/**
 * frontend/tests/e2e/v17_8_0_1_sltp_intelligence.e2e.ts
 * AURUM V17.8.0.1 — Real Browser E2E Verification for SLTPIntelligenceView
 * Verifies DEF-178-12 closure:
 * - SLTPIntelligenceView is mounted and reachable in the real operator workflow (Journal Trade Drawer).
 * - Displays real backend stop-evidence (Unicorn validity, Breaker/FVG IDs, invalidation price, protective distance, ATR/spread, TP, financials).
 * - Clearly distinguishes executed SL vs research candidate SL in SHADOW_COMPARE mode.
 * - Never fabricates applied_variant='C' or stop_mode_at_arm='SHADOW_COMPARE' for legacy/unrecorded orders; displays UNAVAILABLE_LEGACY and NOT_RECORDED.
 */

import { test, expect } from '@playwright/test';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

test.describe('V17.8.0.1 SL/TP Intelligence View Browser E2E Verification', () => {
  test.beforeEach(async ({ page, request }) => {
    await page.goto('about:blank');
    const resetRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
    expect(resetRes.ok()).toBe(true);
  });

  test('P01: Renders full V17.8.0.1 Unicorn structural stop intelligence in Journal Drawer', async ({ page, request }) => {
    const unicornCostSnap = {
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
          occurred_at_ms: Date.now() - 120000,
          known_at_ms: Date.now() - 60000,
          price_basis: 'CANDLE_HIGH',
        },
        buffer: {
          buffer_price: 1.38,
          dominant_source: 'atr_m1',
        },
        metrics: {
          price_distance: 3.72,
          risk_usdt: 2.50,
          net_rr: 2.27,
        },
        volatility_context: {
          atr_m1: { value: 2.10, method: 'SMA_TR_V1' },
          atr_m5: { value: 4.50, method: 'SMA_TR_V1' },
          spread: 0.30,
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
      },
      stop_comparison: {
        variant_a: { stop_loss: 4021.67, can_execute: true },
        variant_b: { stop_loss: 4024.80, can_execute: true },
        variant_c: { stop_loss: 4024.80, can_execute: true },
      },
      stop_mode_at_arm: 'PAPER_STRUCTURAL',
      stop_policy_version: 2,
      applied_variant: 'C',
      min_protective_distance: 1.38,
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
    };

    const seedRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'ord_v17_8_0_1_unicorn_short',
        direction: 'SHORT',
        state: 'paper_open',
        planned_entry: 4021.08,
        stop_loss: 4024.80,
        take_profit: 4002.34,
        cost_snapshot: unicornCostSnap,
      },
    });
    expect(seedRes.status()).toBe(200);

    const evRes = await request.get(`http://127.0.0.1:${API_PORT}/api/v1/orders/ord_v17_8_0_1_unicorn_short/stop-evidence`);
    expect(evRes.status()).toBe(200);
    const evData = await evRes.json();
    expect(evData.status).toBe('AVAILABLE');
    expect(evData.applied_variant).toBe('C');
    expect(evData.stop_mode_at_arm).toBe('PAPER_STRUCTURAL');
    expect(evData.unicorn_evidence.is_valid).toBe(true);
    expect(evData.unicorn_evidence.selected_fvg_id).toBe('fvg-bear-1M-1793102220000');

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const journalTab = page.locator('[data-tab-id="journal"]');
    await expect(journalTab).toBeVisible({ timeout: 10000 });
    await journalTab.click();

    const orderRow = page.locator('tr[data-order-id="ord_v17_8_0_1_unicorn_short"]');
    await expect(orderRow).toBeVisible({ timeout: 10000 });
    await orderRow.getByRole('button', { name: /Chi Tiết & Note/i }).click();

    const view = page.getByTestId('sltp-intelligence-view');
    await expect(view).toBeVisible({ timeout: 5000 });
    await expect(view.getByTestId('sltp-mode-badge')).toHaveText('PAPER_STRUCTURAL');
    await expect(view.getByTestId('sltp-applied-variant-badge')).toHaveText('C');
    await expect(view.getByTestId('sltp-executed-sl')).toHaveText('4024.80');
    await expect(view.getByTestId('sltp-take-profit')).toHaveText('4002.34');
    await expect(view.getByTestId('sltp-protective-distance')).toHaveText('1.38 USD');
    await expect(view.getByTestId('sltp-unicorn-status-badge')).toHaveText('UNICORN_CONFIRMED');
    await expect(view.getByTestId('sltp-breaker-id')).toHaveText('brk_short_1m_1793102160000');
    await expect(view.getByTestId('sltp-fvg-id')).toHaveText('fvg-bear-1M-1793102220000');
    await expect(view.getByTestId('sltp-invalidation-price')).toHaveText('4023.42');
    await expect(view.getByTestId('sltp-atr-m1')).toHaveText('2.1000');
  });

  test('P02: Distinguishes executed Legacy SL (A) from research Unicorn candidate SL in SHADOW_COMPARE mode', async ({ page, request }) => {
    const shadowCostSnap = {
      stop_placement: {
        status: 'VALID',
        mode: 'SHADOW_COMPARE',
        policy_version: 2,
        applied_variant: 'A',
        anchor: {
          id: 'anc_shadow_short_1m',
          kind: 'BREAKER_DISTAL_BOUNDARY',
          price: 4023.42,
          timeframe: '1M',
        },
        buffer: {
          buffer_price: 1.38,
          dominant_source: 'atr_m1',
        },
      },
      stop_comparison: {
        variant_a: { stop_loss: 4021.67, can_execute: true },
        variant_b: { stop_loss: 4024.80, can_execute: true },
        variant_c: { stop_loss: 4024.80, can_execute: true },
      },
      stop_mode_at_arm: 'SHADOW_COMPARE',
      applied_variant: 'A',
      min_protective_distance: 1.38,
    };

    const seedRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'ord_v17_8_0_1_shadow_short',
        direction: 'SHORT',
        state: 'paper_open',
        planned_entry: 4021.08,
        stop_loss: 4021.67,
        take_profit: 4002.34,
        cost_snapshot: shadowCostSnap,
      },
    });
    expect(seedRes.status()).toBe(200);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.locator('[data-tab-id="journal"]').click();

    const orderRow = page.locator('tr[data-order-id="ord_v17_8_0_1_shadow_short"]');
    await expect(orderRow).toBeVisible({ timeout: 10000 });
    await orderRow.getByRole('button', { name: /Chi Tiết & Note/i }).click();

    const view = page.getByTestId('sltp-intelligence-view');
    await expect(view).toBeVisible({ timeout: 5000 });
    await expect(view.getByTestId('sltp-mode-badge')).toHaveText('SHADOW_COMPARE');
    await expect(view.getByTestId('sltp-applied-variant-badge')).toHaveText('A');
    await expect(view.getByTestId('sltp-executed-sl')).toHaveText('4021.67');
    await expect(view.getByTestId('sltp-variant-c-sl')).toHaveText('4024.80');
    await expect(view).toContainText('A (Legacy FVG) · [Đã thực thi]');
    await expect(view).toContainText('C (Unicorn SL) · [Ứng viên nghiên cứu]');
  });

  test('P03: Displays UNAVAILABLE_LEGACY and NOT_RECORDED for historical order without fabricating mode or variant', async ({ page, request }) => {
    const seedRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'ord_v17_8_0_1_legacy',
        direction: 'SHORT',
        state: 'paper_open',
        planned_entry: 4021.08,
        stop_loss: 4021.67,
        take_profit: 4002.34,
        cost_snapshot: {},
      },
    });
    expect(seedRes.status()).toBe(200);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.locator('[data-tab-id="journal"]').click();

    const orderRow = page.locator('tr[data-order-id="ord_v17_8_0_1_legacy"]');
    await expect(orderRow).toBeVisible({ timeout: 10000 });
    await orderRow.getByRole('button', { name: /Chi Tiết & Note/i }).click();

    const legacyBanner = page.getByTestId('sltp-intelligence-legacy');
    await expect(legacyBanner).toBeVisible({ timeout: 5000 });
    await expect(legacyBanner.getByTestId('sltp-legacy-status-badge')).toHaveText('UNAVAILABLE_LEGACY');
    await expect(legacyBanner.getByTestId('sltp-not-recorded-badge')).toHaveText('NOT_RECORDED');
  });

  test('P04: V17.8.0.3 MarketShadowPanel renders connected fresh feed, Unicorn opportunity, RESEARCH ONLY badge, and backtest results', async ({ page, request }) => {
    const seedShadowRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_market_shadow`, {
      data: {
        mode: 'FRESH',
      },
    });
    expect(seedShadowRes.status()).toBe(200);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const obsTab = page.locator('[data-tab-id="observation"]');
    await expect(obsTab).toBeVisible({ timeout: 10000 });
    await obsTab.click();

    const panel = page.getByTestId('market-shadow-panel');
    await expect(panel).toBeVisible({ timeout: 10000 });
    await expect(panel.getByTestId('shadow-research-only-badge')).toHaveText('RESEARCH ONLY — NO ORDER EXECUTED');
    await expect(panel.getByTestId('shadow-trading-controls-badge')).toContainText('TRADING_CONTROLS_DISABLED');
    await expect(panel.getByTestId('shadow-feed-status')).toHaveText('CONNECTED_FRESH');
    await expect(panel.getByTestId('shadow-m1-status')).toHaveText('READY');
    await expect(panel.getByTestId('shadow-m5-status')).toHaveText('READY');
    await expect(panel.getByTestId('shadow-m15-status')).toHaveText('READY');
    await expect(panel.getByTestId('shadow-opp-structural-sl').first()).toBeVisible();
    await expect(panel.getByTestId('shadow-opp-tp').first()).toBeVisible();
    await expect(panel.getByTestId('shadow-opp-research-badge').first()).toHaveText('RESEARCH ONLY — NO ORDER EXECUTED');
    await expect(panel.getByTestId('shadow-backtest-availability')).toHaveText('BACKTEST_AVAILABLE');
    await expect(panel.getByTestId('shadow-backtest-verdict')).toHaveText('REAL_HISTORICAL_BACKTEST_COMPLETED');
  });

  test('P05: V17.8.0.3 MarketShadowPanel renders DISCONNECTED feed and MISSING_BACKTEST_DATA honestly', async ({ page, request }) => {
    const seedShadowRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_market_shadow`, {
      data: {
        mode: 'DISCONNECTED',
        backtest_override: {
          available: false,
          backtest_verdict: 'REAL_HISTORICAL_BACKTEST_NOT_RUN',
          dataset_coverage: null,
        },
      },
    });
    expect(seedShadowRes.status()).toBe(200);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const obsTab = page.locator('[data-tab-id="observation"]');
    await expect(obsTab).toBeVisible({ timeout: 10000 });
    await obsTab.click();

    const panel = page.getByTestId('market-shadow-panel');
    await expect(panel).toBeVisible({ timeout: 10000 });
    await expect(panel.getByTestId('shadow-feed-status')).toHaveText('DISCONNECTED');
    await expect(panel.getByTestId('shadow-opportunities-empty')).toBeVisible();
    await expect(panel.getByTestId('shadow-backtest-availability')).toHaveText('MISSING_BACKTEST_DATA');
    await expect(panel.getByTestId('shadow-backtest-verdict')).toHaveText('REAL_HISTORICAL_BACKTEST_NOT_RUN');

    // Reset override
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_market_shadow`, {
      data: { mode: 'DISCONNECTED', backtest_override: null },
    });
  });
});

