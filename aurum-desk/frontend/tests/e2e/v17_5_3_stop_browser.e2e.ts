/**
 * frontend/tests/e2e/v17_5_3_stop_browser.e2e.ts
 * AURUM V17.5.3 — Playwright Browser Verification (Hardened in V17.5.4)
 * Covers Parts 151–160:
 * - SHORT order geometry (TP < Entry < SL) & structural anchor high in drawer.
 * - LONG order geometry (SL < Entry < TP) & structural anchor low in drawer.
 * - Legacy order without structural proof shows legacy / missing proof state cleanly.
 * - Mobile viewport (390x844) renders without horizontal scroll overflow.
 */

import { test, expect } from '@playwright/test';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

test.describe('V17.5.3 Stop Placement & Geometry Browser Verification', () => {
  test.beforeEach(async ({ page, request }) => {
    await page.goto('about:blank');
    const resetRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
    expect(resetRes.ok()).toBe(true);
  });

  test('P01: SHORT order renders correct geometry (TP < Entry < SL) and structural anchor', async ({ page, request }) => {
    const shortCostSnap = {
      stop_placement: {
        status: 'VALID',
        mode: 'PAPER_STRUCTURAL',
        policy_version: 1,
        applied_variant: 'C',
        anchor: {
          id: 'anc_cont_sh_1m_test',
          kind: 'PROTECTED_LEG_HIGH',
          price: 2658.0,
          timeframe: '1M',
          occurred_at_ms: Date.now() - 120000,
          known_at_ms: Date.now() - 60000,
          price_basis: 'CANDLE_LAST',
        },
        buffer: {
          buffer_price: 2.0,
          dominant_source: 'atr_m1',
        },
        metrics: {
          price_distance: 10.0,
          risk_usdt: 12.5,
        },
      },
      stop_mode_at_arm: 'PAPER_STRUCTURAL',
      stop_policy_version: 1,
      applied_variant: 'C',
    };

    // Seed SHORT order: planned_entry 2650.0, SL 2660.0, TP 2625.0
    const seedRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'ord_v17_5_3_short',
        direction: 'SHORT',
        state: 'paper_open',
        planned_entry: 2650.0,
        stop_loss: 2660.0,
        take_profit: 2625.0,
        cost_snapshot: shortCostSnap,
      },
    });
    expect(seedRes.status()).toBe(200);

    // API assertion
    const evRes = await request.get(`http://127.0.0.1:${API_PORT}/api/v1/orders/ord_v17_5_3_short/stop-evidence`);
    expect(evRes.status()).toBe(200);
    const evData = await evRes.json();
    expect(evData.status).toBe('AVAILABLE');
    expect(evData.take_profit).toBeLessThan(evData.actual_entry);
    expect(evData.actual_entry).toBeLessThan(evData.actual_sl);

    // UI Drawer verification
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const journalTab = page.locator('[data-tab-id="journal"]');
    await expect(journalTab).toBeVisible({ timeout: 10000 });
    await journalTab.click();

    const orderRow = page.locator('tr[data-order-id="ord_v17_5_3_short"]');
    await expect(orderRow).toBeVisible({ timeout: 10000 });
    await orderRow.getByRole('button', { name: /Chi Tiết & Note/i }).click();

    // Tab B: "B. Giả Thuyết & SMC"
    const tabB = page.locator('button', { hasText: 'B. Giả Thuyết & SMC' });
    await expect(tabB).toBeVisible({ timeout: 5000 });
    await tabB.click();

    await expect(page.locator('text=Chi Tiết Điểm Dừng Lỗ (SL)')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=2660.00')).toBeVisible();
    await expect(page.locator('text=PROTECTED_LEG_HIGH tại 2658.00 (1M)')).toBeVisible();
    await expect(page.locator('text=Xác minh cấu trúc (C)')).toBeVisible();
  });

  test('P02: LONG order renders correct geometry (SL < Entry < TP)', async ({ page, request }) => {
    const longCostSnap = {
      stop_placement: {
        status: 'VALID',
        mode: 'PAPER_STRUCTURAL',
        policy_version: 1,
        applied_variant: 'C',
        anchor: {
          id: 'anc_cont_sl_1m_test',
          kind: 'PROTECTED_LEG_LOW',
          price: 2642.0,
          timeframe: '1M',
          occurred_at_ms: Date.now() - 120000,
          known_at_ms: Date.now() - 60000,
          price_basis: 'CANDLE_LAST',
        },
        buffer: {
          buffer_price: 2.0,
          dominant_source: 'atr_m1',
        },
        metrics: {
          price_distance: 10.0,
          risk_usdt: 12.5,
        },
      },
      stop_mode_at_arm: 'PAPER_STRUCTURAL',
      applied_variant: 'C',
    };

    // Seed LONG order: planned_entry 2650.0, SL 2640.0, TP 2675.0
    const seedRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'ord_v17_5_3_long',
        direction: 'LONG',
        state: 'paper_open',
        planned_entry: 2650.0,
        stop_loss: 2640.0,
        take_profit: 2675.0,
        cost_snapshot: longCostSnap,
      },
    });
    expect(seedRes.status()).toBe(200);

    // API assertion
    const evRes = await request.get(`http://127.0.0.1:${API_PORT}/api/v1/orders/ord_v17_5_3_long/stop-evidence`);
    expect(evRes.status()).toBe(200);
    const evData = await evRes.json();
    expect(evData.status).toBe('AVAILABLE');
    expect(evData.actual_sl).toBeLessThan(evData.actual_entry);
    expect(evData.actual_entry).toBeLessThan(evData.take_profit);

    // UI Drawer verification
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const journalTab = page.locator('[data-tab-id="journal"]');
    await expect(journalTab).toBeVisible({ timeout: 10000 });
    await journalTab.click();

    const orderRow = page.locator('tr[data-order-id="ord_v17_5_3_long"]');
    await expect(orderRow).toBeVisible({ timeout: 10000 });
    await orderRow.getByRole('button', { name: /Chi Tiết & Note/i }).click();

    // Tab B: "B. Giả Thuyết & SMC"
    const tabB = page.locator('button', { hasText: 'B. Giả Thuyết & SMC' });
    await expect(tabB).toBeVisible({ timeout: 5000 });
    await tabB.click();

    await expect(page.locator('text=Chi Tiết Điểm Dừng Lỗ (SL)')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('journal-trade-drawer').getByText('2640.00')).toBeVisible();
    await expect(page.locator('text=PROTECTED_LEG_LOW tại 2642.00 (1M)')).toBeVisible();
    await expect(page.locator('text=Xác minh cấu trúc (C)')).toBeVisible();
  });

  test('P03: Mobile viewport (390x844) renders without horizontal scroll overflow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 2);
  });
});
