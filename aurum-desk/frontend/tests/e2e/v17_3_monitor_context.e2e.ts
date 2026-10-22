/**
 * frontend/tests/e2e/v17_3_monitor_context.e2e.ts
 * Aurum V17.3 — Monitor Context Lifecycle E2E Suite
 *
 * Verifies end-to-end browser acceptance gates (Section P: 151-160):
 * 1. R1: Delayed Focus Response Followed by Refresh (chart is not updated, drawer remains open; subsequent focus succeeds).
 * 2. R2: Delayed Report Response Followed by Filter Change (no stale download; subsequent export on new filter succeeds).
 * 3. Mobile Viewport 390x844 & Read-only Invariant (zero orders mutated, zero outbox messages).
 */

import { test, expect, Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

async function resetAndSeedDatabase(page: Page): Promise<void> {
  const resetRes = await page.request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
  expect(resetRes.ok()).toBe(true);

  const seedRes = await page.request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_monitor`, {
    data: {
      run_id: 'run_e2e_v17_3',
      symbol: 'XAUUSDT',
      scope: 'CG01',
    },
  });
  expect(seedRes.ok()).toBe(true);
}

test.describe('Aurum V17.3 — Monitor Context Lifecycle & Freshness Acceptance', () => {
  test.beforeEach(async ({ page }) => {
    await resetAndSeedDatabase(page);
  });

  test('V17.3 Gate 1 (R1): In-flight Focus Chart is invalidated by Refresh', async ({ page }) => {
    await page.setViewportSize({ width: 1512, height: 857 });
    await page.goto('/');

    const ctaBtn = page.getByTestId('cg01-open-monitor-btn');
    await expect(ctaBtn).toBeVisible({ timeout: 15000 });
    await ctaBtn.click();

    const drawer = page.getByTestId('paper-opportunity-monitor-drawer');
    await expect(drawer).toBeVisible({ timeout: 5000 });

    // Open detail of opp_e2e_001
    const rowOpp1 = drawer.getByTestId('monitor-row-opp_e2e_001');
    await expect(rowOpp1).toBeVisible();
    await rowOpp1.click();

    const detailView = drawer.getByTestId('monitor-detail-view');
    await expect(detailView).toBeVisible();

    // Intercept chart-target API and delay it
    let routePromiseResolver: () => void = () => {};
    const routeGate = new Promise<void>((resolve) => {
      routePromiseResolver = resolve;
    });

    let chartTargetCallCount = 0;
    await page.route('**/api/v1/paper/opportunity-monitor/opportunities/*/chart-target*', async (route) => {
      chartTargetCallCount++;
      if (chartTargetCallCount === 1) {
        await routeGate;
      }
      await route.continue();
    });

    // 1. Click Focus button (triggers delayed request 1)
    const focusBtn = drawer.getByTestId('monitor-focus-chart-btn');
    await expect(focusBtn).toBeVisible();
    await focusBtn.click();

    // Wait until request reached the route handler
    await expect.poll(() => chartTargetCallCount).toBe(1);

    // 2. User clicks Refresh button while focus request is in flight
    const refreshBtn = drawer.getByTestId('monitor-refresh-btn');
    await expect(refreshBtn).toBeVisible();
    await refreshBtn.click();

    // 3. Now release the delayed focus response 1
    routePromiseResolver();

    // Give time for microtasks / response settlement
    await page.waitForTimeout(400);

    // Stale response must NOT close drawer
    await expect(drawer).toBeVisible();

    // 4. Positive control: click Focus again after refresh, it should succeed and close drawer
    await focusBtn.click();
    await expect(drawer).not.toBeVisible({ timeout: 5000 });
  });

  test('V17.3 Gate 2 (R2): In-flight Report Download is invalidated by Filter Change', async ({ page }) => {
    await page.setViewportSize({ width: 1512, height: 857 });
    await page.goto('/');

    const ctaBtn = page.getByTestId('cg01-open-monitor-btn');
    await expect(ctaBtn).toBeVisible({ timeout: 15000 });
    await ctaBtn.click();

    const drawer = page.getByTestId('paper-opportunity-monitor-drawer');
    await expect(drawer).toBeVisible({ timeout: 5000 });

    let routePromiseResolver: () => void = () => {};
    const routeGate = new Promise<void>((resolve) => {
      routePromiseResolver = resolve;
    });

    let reportCallCount = 0;
    await page.route('**/api/v1/paper/opportunity-monitor/report.md*', async (route) => {
      reportCallCount++;
      if (reportCallCount === 1) {
        await routeGate;
      }
      await route.continue();
    });

    // Track download events
    let downloadTriggered = false;
    page.on('download', () => {
      downloadTriggered = true;
    });

    // 1. Click download report button (request 1 in flight)
    const downloadBtn = drawer.getByTestId('monitor-download-report-btn');
    await expect(downloadBtn).toBeVisible();
    await downloadBtn.click();

    await expect.poll(() => reportCallCount).toBe(1);

    // 2. User changes filter to 7d while export is in flight
    const period7dBtn = drawer.getByTestId('filter-period-7d');
    await expect(period7dBtn).toBeVisible();
    await period7dBtn.click();

    // 3. Release stale report request 1
    routePromiseResolver();

    await page.waitForTimeout(400);

    // Expect no download was triggered for request 1
    expect(downloadTriggered).toBe(false);

    // 4. Positive control: click download report under 7d filter
    const downloadPromise = page.waitForEvent('download');
    await downloadBtn.click();

    const download = await downloadPromise;
    const filePath = await download.path();
    expect(filePath).not.toBeNull();

    const content = await readFile(filePath!, 'utf8');
    expect(content).toContain('BÁO CÁO THEO DÕI CƠ HỘI PAPER');
    expect(content).toContain('run_e2e_v17_3');
  });

  test('V17.3 Gate 3: Mobile Viewport 390x844 & Read-Only Invariance', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });

    const stateBeforeRes = await page.request.get(`http://127.0.0.1:${API_PORT}/__e2e/state`);
    const stateBefore = await stateBeforeRes.json();

    await page.goto('/');

    const ctaBtn = page.getByTestId('cg01-open-monitor-btn');
    await expect(ctaBtn).toBeVisible({ timeout: 15000 });
    await ctaBtn.click();

    const drawer = page.getByTestId('paper-opportunity-monitor-drawer');
    await expect(drawer).toBeVisible({ timeout: 5000 });

    // Verify mobile responsiveness: no horizontal scroll
    const isOverflowing = await page.evaluate(() => {
      return document.documentElement.scrollWidth > window.innerWidth;
    });
    expect(isOverflowing).toBe(false);

    // Verify Read-Only Gate: zero order mutations, zero outbox
    const stateAfterRes = await page.request.get(`http://127.0.0.1:${API_PORT}/__e2e/state`);
    const stateAfter = await stateAfterRes.json();
    expect(stateAfter.outbox).toBe(0);
    expect(stateAfter.orders).toBe(stateBefore.orders);
  });
});
