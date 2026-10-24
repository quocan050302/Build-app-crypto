/**
 * frontend/tests/e2e/v17_5_6_chart_evidence.e2e.ts
 * AURUM V17.5.6 — Mandatory Finite Chart Geometry & Test Mode Overlay Evidence
 * Covers Parts 061–070, 161–190, Appendix D:
 * 1. Seed isolated test orders (SHORT, LONG).
 * 2. Focus order on chart from Journal table.
 * 3. Mandatory finite geometry polling via waitForReadyOverlayGeometry: NO conditional if-skip.
 * 4. SHORT geometry assertions: tpY > entryY > slY strictly finite.
 * 5. LONG geometry assertions: slY > entryY > tpY strictly finite.
 * 6. Read-only safety: diagnostics returns clean read-only snapshot without execution APIs.
 * 7. Rapid selection race: old overlay cleanly detaches, new overlay attaches.
 * 8. Mobile viewport check (390x844).
 */

import { test, expect, Page } from '@playwright/test';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

/**
 * Appendix D2: Authoritative waitForReadyOverlayGeometry helper.
 * Polls until the actual RiskRewardPrimitive has finite projected canvas coordinates.
 * Strictly asserts Number.isFinite on entryY, slY, tpY. Fails if null after timeout.
 */
async function waitForReadyOverlayGeometry(page: Page, expectedOrderId: string) {
  await expect.poll(async () => {
    return page.evaluate((id) => {
      const read = (window as any).__AURUM_CHART_OVERLAY_DIAGNOSTICS__;
      if (typeof read !== 'function') return false;
      const d = read();
      if (!d?.hasPrimitive || d.data?.id !== id) return false;
      const g = d.geometry;
      return (
        !!g &&
        [g.entryY, g.slY, g.tpY].every(
          (v: any) => typeof v === 'number' && Number.isFinite(v)
        )
      );
    }, expectedOrderId);
  }, { timeout: 15000, intervals: [300, 500, 1000] }).toBe(true);

  const d = await page.evaluate(() => (window as any).__AURUM_CHART_OVERLAY_DIAGNOSTICS__());
  expect(d).not.toBeNull();
  expect(d.data.id).toBe(expectedOrderId);
  expect(d.hasPrimitive).toBe(true);
  expect(d.geometry).not.toBeNull();
  expect(typeof d.geometry.entryY).toBe('number');
  expect(typeof d.geometry.slY).toBe('number');
  expect(typeof d.geometry.tpY).toBe('number');
  expect(Number.isFinite(d.geometry.entryY)).toBe(true);
  expect(Number.isFinite(d.geometry.slY)).toBe(true);
  expect(Number.isFinite(d.geometry.tpY)).toBe(true);
  return d;
}

test.describe('V17.5.6 Mandatory Chart Geometry & Test Mode Verification', () => {
  test.beforeEach(async ({ page, request }) => {
    await page.goto('about:blank');
    const resetRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
    expect(resetRes.ok()).toBe(true);
  });

  test('P01: SHORT order renders primitive with strictly finite canvas geometry (tpY > entryY > slY)', async ({ page, request }) => {
    const shortCostSnap = {
      stop_placement: {
        status: 'VALID',
        mode: 'PAPER_STRUCTURAL',
        policy_version: 1,
        applied_variant: 'C',
        anchor: {
          id: 'anc_v17_5_6_short',
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
        order_id: 'ord_v17_5_6_short',
        direction: 'SHORT',
        state: 'paper_open',
        planned_entry: 2650.0,
        stop_loss: 2660.0,
        take_profit: 2625.0,
        cost_snapshot: shortCostSnap,
      },
    });
    expect(seedRes.status()).toBe(200);

    // 2. Open Journal and focus order on chart
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const journalTab = page.locator('[data-tab-id="journal"]');
    await expect(journalTab).toBeVisible({ timeout: 10000 });
    await journalTab.click();

    const focusBtn = page.locator('button[data-testid="journal-focus-chart"][data-order-id="ord_v17_5_6_short"]');
    await expect(focusBtn).toBeVisible({ timeout: 10000 });
    await focusBtn.click();

    // 3. Switch to Chart tab if needed
    const chartTab = page.locator('[data-tab-id="chart"]');
    if (await chartTab.isVisible()) {
      await chartTab.click();
    }

    // 4. Mandatory finite geometry check (Appendix D2)
    const diag = await waitForReadyOverlayGeometry(page, 'ord_v17_5_6_short');

    // Prices: takeProfit (2625.0) < plannedEntry (2650.0) < stopLoss (2660.0)
    expect(diag.data.takeProfit).toBeLessThan(diag.data.plannedEntry);
    expect(diag.data.plannedEntry).toBeLessThan(diag.data.stopLoss);

    // Canvas coordinates: higher price -> lower Y. Therefore tpY > entryY > slY.
    // Unconditional assertions: NO if-skipping!
    expect(diag.geometry.tpY).toBeGreaterThan(diag.geometry.entryY);
    expect(diag.geometry.entryY).toBeGreaterThan(diag.geometry.slY);

    // Read-only check
    expect(diag.data.readOnly).toBe(true);
  });

  test('P02: LONG order renders primitive with strictly finite canvas geometry (slY > entryY > tpY)', async ({ page, request }) => {
    const longCostSnap = {
      stop_placement: {
        status: 'VALID',
        mode: 'PAPER_STRUCTURAL',
        policy_version: 1,
        applied_variant: 'C',
        anchor: {
          id: 'anc_v17_5_6_long',
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
        order_id: 'ord_v17_5_6_long',
        direction: 'LONG',
        state: 'paper_open',
        planned_entry: 2645.0,
        stop_loss: 2635.0,
        take_profit: 2670.0,
        cost_snapshot: longCostSnap,
      },
    });
    expect(seedRes.status()).toBe(200);

    // 2. Open Journal and focus order on chart
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const journalTab = page.locator('[data-tab-id="journal"]');
    await expect(journalTab).toBeVisible({ timeout: 10000 });
    await journalTab.click();

    const focusBtn = page.locator('button[data-testid="journal-focus-chart"][data-order-id="ord_v17_5_6_long"]');
    await expect(focusBtn).toBeVisible({ timeout: 10000 });
    await focusBtn.click();

    // 3. Switch to Chart tab if needed
    const chartTab = page.locator('[data-tab-id="chart"]');
    if (await chartTab.isVisible()) {
      await chartTab.click();
    }

    // 4. Mandatory finite geometry check (Appendix D2)
    const diag = await waitForReadyOverlayGeometry(page, 'ord_v17_5_6_long');

    // Prices: stopLoss (2635.0) < plannedEntry (2645.0) < takeProfit (2670.0)
    expect(diag.data.stopLoss).toBeLessThan(diag.data.plannedEntry);
    expect(diag.data.plannedEntry).toBeLessThan(diag.data.takeProfit);

    // Canvas coordinates: higher price -> lower Y. Therefore slY > entryY > tpY.
    // Unconditional assertions: NO if-skipping!
    expect(diag.geometry.slY).toBeGreaterThan(diag.geometry.entryY);
    expect(diag.geometry.entryY).toBeGreaterThan(diag.geometry.tpY);

    // Read-only check
    expect(diag.data.readOnly).toBe(true);
  });

  test('P03: Diagnostics bridge is read-only and does not expose setters or trading APIs', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const bridge = await page.evaluate(() => {
      const fn = (window as any).__AURUM_CHART_OVERLAY_DIAGNOSTICS__;
      if (typeof fn !== 'function') return null;
      const res = fn();
      return {
        isFunction: typeof fn === 'function',
        hasSetters: Object.keys(fn).some(k => k.toLowerCase().includes('set') || k.toLowerCase().includes('exec')),
        keys: Object.keys(res || {}),
      };
    });

    expect(bridge).not.toBeNull();
    expect(bridge?.isFunction).toBe(true);
    expect(bridge?.hasSetters).toBe(false);
  });

  test('P04: Rapid selection switching cleanly detaches prior primitive and attaches newest', async ({ page, request }) => {
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'ord_v17_rapid_a',
        direction: 'SHORT',
        state: 'closed',
        planned_entry: 2650.0,
        stop_loss: 2660.0,
        take_profit: 2625.0,
      },
    });
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'ord_v17_rapid_b',
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

    // Select A
    const btnA = page.locator('button[data-testid="journal-focus-chart"][data-order-id="ord_v17_rapid_a"]');
    await expect(btnA).toBeVisible({ timeout: 5000 });
    await btnA.click();

    // Confirm A is active
    await waitForReadyOverlayGeometry(page, 'ord_v17_rapid_a');

    // Switch back to Journal and select B
    await journalTab.click();
    const btnB = page.locator('button[data-testid="journal-focus-chart"][data-order-id="ord_v17_rapid_b"]');
    await expect(btnB).toBeVisible({ timeout: 5000 });
    await btnB.click();

    const chartTab = page.locator('[data-tab-id="chart"]');
    if (await chartTab.isVisible()) {
      await chartTab.click();
    }

    // Confirm B is active and A is detached
    const diagB = await waitForReadyOverlayGeometry(page, 'ord_v17_rapid_b');
    expect(diagB.data.id).toBe('ord_v17_rapid_b');
  });

  test('P05: Mobile viewport (390x844) renders without horizontal document overflow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    await page.waitForTimeout(1000);
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 2);
  });
});
