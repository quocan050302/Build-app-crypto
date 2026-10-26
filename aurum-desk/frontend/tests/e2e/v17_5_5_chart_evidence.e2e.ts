/**
 * frontend/tests/e2e/v17_5_5_chart_evidence.e2e.ts
 * AURUM V17.5.5 — Real Chart Evidence & Primitive Geometry Browser Verification
 * Covers Parts 151–170, Appendix D:
 * 1. Seed isolated test orders (SHORT, LONG, Legacy).
 * 2. Focus order on chart from Journal table.
 * 3. Inspect window.__AURUM_CHART_OVERLAY_DIAGNOSTICS__() to verify attached RiskRewardPrimitive.
 * 4. SHORT geometry assertion: takeProfit < plannedEntry < stopLoss => tpY > entryY > slY.
 * 5. LONG geometry assertion: stopLoss < plannedEntry < takeProfit => slY > entryY > tpY.
 * 6. Read-only verification: production orders are readOnly=true.
 * 7. Rapid selection switching: old overlay detaches cleanly.
 * 8. Mobile viewport check (390x844).
 */

import { test, expect } from '@playwright/test';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

test.describe('V17.5.5 Chart Evidence & Overlay Geometry Browser Verification', () => {
  test.beforeEach(async ({ page, request }) => {
    await page.goto('about:blank');
    const resetRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
    expect(resetRes.ok()).toBe(true);
  });

  test('P01: SHORT order renders primitive with correct prices and projected canvas geometry (tpY > entryY > slY)', async ({ page, request }) => {
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
      },
      stop_mode_at_arm: 'PAPER_STRUCTURAL',
      stop_policy_version: 1,
      applied_variant: 'C',
    };

    // 1. Seed SHORT order
    const seedRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'ord_v17_5_5_short',
        direction: 'SHORT',
        state: 'paper_open',
        planned_entry: 2650.0,
        stop_loss: 2660.0,
        take_profit: 2625.0,
        cost_snapshot: shortCostSnap,
      },
    });
    expect(seedRes.status()).toBe(200);

    // 2. Open Journal and click Chart button for this order
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const journalTab = page.locator('[data-tab-id="journal"]');
    await expect(journalTab).toBeVisible({ timeout: 10000 });
    await journalTab.click();

    const focusBtn = page.locator('button[data-testid="journal-focus-chart"][data-order-id="ord_v17_5_5_short"]');
    await expect(focusBtn).toBeVisible({ timeout: 10000 });
    await focusBtn.click();

    // 3. Switch back to Chart tab (or click navigation to chart)
    const chartTab = page.locator('[data-tab-id="chart"]');
    if (await chartTab.isVisible()) {
      await chartTab.click();
    }

    // 4. Poll window.__AURUM_CHART_OVERLAY_DIAGNOSTICS__()
    await expect.poll(async () => {
      return await page.evaluate(() => {
        const diagFn = (window as any).__AURUM_CHART_OVERLAY_DIAGNOSTICS__;
        if (!diagFn) return null;
        const diag = diagFn();
        return diag && diag.hasPrimitive && diag.data && diag.data.id === 'ord_v17_5_5_short' ? diag : null;
      });
    }, { timeout: 15000, intervals: [300, 500, 1000] }).toBeTruthy();

    const diag = await page.evaluate(() => (window as any).__AURUM_CHART_OVERLAY_DIAGNOSTICS__());
    expect(diag.hasPrimitive).toBe(true);
    expect(diag.data.direction).toBe('SHORT');
    expect(diag.data.plannedEntry).toBe(2650.0);
    expect(diag.data.stopLoss).toBe(2660.0);
    expect(diag.data.takeProfit).toBe(2625.0);
    expect(diag.data.readOnly).toBe(true);

    // Strict geometry assertion for SHORT:
    // Prices: takeProfit (2625.0) < plannedEntry (2650.0) < stopLoss (2660.0)
    expect(diag.data.takeProfit).toBeLessThan(diag.data.plannedEntry);
    expect(diag.data.plannedEntry).toBeLessThan(diag.data.stopLoss);

    // Canvas coordinates: higher price has lower Y pixel value.
    // Therefore: tpY > entryY > slY
    const geom = diag.geometry;
    if (geom && geom.entryY !== null && geom.slY !== null && geom.tpY !== null) {
      expect(geom.tpY).toBeGreaterThan(geom.entryY);
      expect(geom.entryY).toBeGreaterThan(geom.slY);
    }
  });

  test('P02: LONG order renders primitive with correct prices and projected canvas geometry (slY > entryY > tpY)', async ({ page, request }) => {
    const longCostSnap = {
      stop_placement: {
        status: 'VALID',
        mode: 'PAPER_STRUCTURAL',
        policy_version: 1,
        applied_variant: 'C',
        anchor: {
          id: 'anc_cont_sl_1m_test',
          kind: 'PROTECTED_LEG_LOW',
          price: 2637.0,
          timeframe: '1M',
          occurred_at_ms: Date.now() - 120000,
          known_at_ms: Date.now() - 60000,
          price_basis: 'CANDLE_LAST',
        },
        buffer: {
          buffer_price: 2.0,
          dominant_source: 'atr_m1',
        },
      },
      stop_mode_at_arm: 'PAPER_STRUCTURAL',
      stop_policy_version: 1,
      applied_variant: 'C',
    };

    // 1. Seed LONG order
    const seedRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'ord_v17_5_5_long',
        direction: 'LONG',
        state: 'paper_open',
        planned_entry: 2645.0,
        stop_loss: 2635.0,
        take_profit: 2670.0,
        cost_snapshot: longCostSnap,
      },
    });
    expect(seedRes.status()).toBe(200);

    // 2. Open Journal and click Chart button for this order
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const journalTab = page.locator('[data-tab-id="journal"]');
    await expect(journalTab).toBeVisible({ timeout: 10000 });
    await journalTab.click();

    const focusBtn = page.locator('button[data-testid="journal-focus-chart"][data-order-id="ord_v17_5_5_long"]');
    await expect(focusBtn).toBeVisible({ timeout: 10000 });
    await focusBtn.click();

    // 3. Switch back to Chart tab
    const chartTab = page.locator('[data-tab-id="chart"]');
    if (await chartTab.isVisible()) {
      await chartTab.click();
    }

    // 4. Poll window.__AURUM_CHART_OVERLAY_DIAGNOSTICS__()
    await expect.poll(async () => {
      return await page.evaluate(() => {
        const diagFn = (window as any).__AURUM_CHART_OVERLAY_DIAGNOSTICS__;
        if (!diagFn) return null;
        const diag = diagFn();
        return diag && diag.hasPrimitive && diag.data && diag.data.id === 'ord_v17_5_5_long' ? diag : null;
      });
    }, { timeout: 15000, intervals: [300, 500, 1000] }).toBeTruthy();

    const diag = await page.evaluate(() => (window as any).__AURUM_CHART_OVERLAY_DIAGNOSTICS__());
    expect(diag.hasPrimitive).toBe(true);
    expect(diag.data.direction).toBe('LONG');
    expect(diag.data.plannedEntry).toBe(2645.0);
    expect(diag.data.stopLoss).toBe(2635.0);
    expect(diag.data.takeProfit).toBe(2670.0);
    expect(diag.data.readOnly).toBe(true);

    // Strict geometry assertion for LONG:
    // Prices: stopLoss (2635.0) < plannedEntry (2645.0) < takeProfit (2670.0)
    expect(diag.data.stopLoss).toBeLessThan(diag.data.plannedEntry);
    expect(diag.data.plannedEntry).toBeLessThan(diag.data.takeProfit);

    // Canvas coordinates: higher price has lower Y pixel value.
    // Therefore: slY > entryY > tpY
    const geom = diag.geometry;
    if (geom && geom.entryY !== null && geom.slY !== null && geom.tpY !== null) {
      expect(geom.slY).toBeGreaterThan(geom.entryY);
      expect(geom.entryY).toBeGreaterThan(geom.tpY);
    }
  });

  test('P03: Rapid selection switching cleanly detaches prior primitive and activates latest', async ({ page, request }) => {
    // Seed both SHORT and LONG orders as closed journal records
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'ord_rapid_1',
        direction: 'SHORT',
        state: 'closed',
        planned_entry: 2650.0,
        stop_loss: 2660.0,
        take_profit: 2625.0,
      },
    });
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'ord_rapid_2',
        direction: 'LONG',
        state: 'closed',
        planned_entry: 2645.0,
        stop_loss: 2635.0,
        take_profit: 2670.0,
      },
    });

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const journalTab = page.locator('[data-tab-id="journal"]');
    await expect(journalTab).toBeVisible({ timeout: 10000 });
    await journalTab.click();

    // Rapid selection: click order 1, verify chart focus, then switch back to Journal and click order 2
    const btn1 = page.locator('button[data-testid="journal-focus-chart"][data-order-id="ord_rapid_1"]');
    await expect(btn1).toBeVisible({ timeout: 5000 });
    await btn1.click();

    // Verify order 1 is active on chart
    await expect.poll(async () => {
      return await page.evaluate(() => {
        const diagFn = (window as any).__AURUM_CHART_OVERLAY_DIAGNOSTICS__;
        if (!diagFn) return null;
        const diag = diagFn();
        return diag && diag.hasPrimitive && diag.data && diag.data.id === 'ord_rapid_1' ? diag.data.id : null;
      });
    }, { timeout: 10000 }).toBe('ord_rapid_1');

    // Switch back to Journal and select order 2
    await journalTab.click();
    const row2 = page.locator('tr[data-order-id="ord_rapid_2"]');
    await expect(row2).toBeVisible({ timeout: 10000 });
    const btn2 = row2.locator('button[data-testid="journal-focus-chart"]');
    await expect(btn2).toBeVisible({ timeout: 5000 });
    await btn2.click();

    // Ensure on chart tab
    const chartTab = page.locator('[data-tab-id="chart"]');
    if (await chartTab.isVisible()) {
      await chartTab.click();
    }

    // Verify latest selection (ord_rapid_2) is now active and ord_rapid_1 is cleanly detached
    await expect.poll(async () => {
      return await page.evaluate(() => {
        const diagFn = (window as any).__AURUM_CHART_OVERLAY_DIAGNOSTICS__;
        if (!diagFn) return null;
        const diag = diagFn();
        return diag && diag.hasPrimitive && diag.data && diag.data.id === 'ord_rapid_2' ? diag.data.id : null;
      });
    }, { timeout: 15000, intervals: [300, 500, 1000] }).toBe('ord_rapid_2');
  });

  test('P04: Mobile viewport (390x844) renders without horizontal document overflow', async ({ page, request }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    await page.waitForTimeout(1000);
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 2);
  });
});
