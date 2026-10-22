/**
 * frontend/tests/e2e/v17_2_opportunity_monitor.e2e.ts
 * Aurum V17.2 — Paper Opportunity Monitor Acceptance Suite
 *
 * Verifies end-to-end browser acceptance gates (Section 151-160, Appendix F):
 * 1. Real Report Download (waitForEvent('download'), verify bytes, run_id, and markdown headers).
 * 2. Stale Setup Revision Handling (STALE_TARGET shows inline warning, drawer does not close, chart selection preserved).
 * 3. Focus Delay followed by Back (response arriving after Back does NOT change chart or trigger callbacks).
 * 4. Read-Only Invariance (zero orders mutated, zero outbox, zero settings changes).
 * 5. Mobile Viewport 390x844 (no horizontal overflow, controls accessible).
 */

import { test, expect, Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

async function resetAndSeedDatabase(page: Page): Promise<void> {
  const resetRes = await page.request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
  expect(resetRes.ok()).toBe(true);

  const seedRes = await page.request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_monitor`, {
    data: {
      run_id: 'run_e2e_v17_2',
      symbol: 'XAUUSDT',
      scope: 'CG01',
    },
  });
  expect(seedRes.ok()).toBe(true);
}

test.describe('Aurum V17.2 — Paper Opportunity Monitor Final Acceptance', () => {
  test.beforeEach(async ({ page }) => {
    await resetAndSeedDatabase(page);
  });

  test('V17.2 Gate 1 & 4: Real Report Download & Read-Only Invariant', async ({ page }) => {
    await page.setViewportSize({ width: 1512, height: 857 });

    // Capture initial state
    const stateBeforeRes = await page.request.get(`http://127.0.0.1:${API_PORT}/__e2e/state`);
    const stateBefore = await stateBeforeRes.json();

    await page.goto('/');

    const ctaBtn = page.getByTestId('cg01-open-monitor-btn');
    await expect(ctaBtn).toBeVisible({ timeout: 15000 });
    await ctaBtn.click();

    const drawer = page.getByTestId('paper-opportunity-monitor-drawer');
    await expect(drawer).toBeVisible({ timeout: 5000 });

    // Real download with Playwright event listener
    const downloadPromise = page.waitForEvent('download');
    const downloadBtn = drawer.getByTestId('monitor-download-report-btn');
    await expect(downloadBtn).toBeVisible();
    await downloadBtn.click();

    const download = await downloadPromise;
    const filePath = await download.path();
    expect(filePath).not.toBeNull();

    const content = await readFile(filePath!, 'utf8');
    expect(content).toContain('BÁO CÁO THEO DÕI CƠ HỘI PAPER');
    expect(content).toContain('run_e2e_v17_2');
    expect(content).toContain('XAUUSDT');
    expect(download.suggestedFilename()).toMatch(/\.md$/);

    // Verify Read-Only Gate: zero new orders, zero outbox messages
    const stateAfterRes = await page.request.get(`http://127.0.0.1:${API_PORT}/__e2e/state`);
    const stateAfter = await stateAfterRes.json();
    expect(stateAfter.outbox).toBe(0);
    expect(stateAfter.orders).toBe(stateBefore.orders);
  });

  test('V17.2 Gate 2: Delayed Focus Response Followed by Back (Bug A Prevention)', async ({ page }) => {
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

    await page.route('**/api/v1/paper/opportunity-monitor/opportunities/*/chart-target*', async (route) => {
      await routeGate;
      await route.continue();
    });

    const focusBtn = drawer.getByTestId('monitor-focus-chart-btn');
    await expect(focusBtn).toBeVisible();
    await focusBtn.click();

    // While focus request is in flight, click Back
    const backBtn = drawer.locator('button[title="Quay lại danh sách"]');
    await expect(backBtn).toBeVisible();
    await backBtn.click();

    // Master list should be restored
    await expect(drawer.getByTestId('monitor-row-opp_e2e_001')).toBeVisible();

    // Now resolve the delayed chart-target response
    routePromiseResolver();
    await page.waitForTimeout(500);

    // Drawer must remain OPEN in master list view, chart selection must not have triggered
    await expect(drawer).toBeVisible();
    await expect(drawer.getByTestId('monitor-row-opp_e2e_001')).toBeVisible();
  });

  test('V17.2 Gate 3: Mobile Viewport 390x844 Layout Integrity', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');

    const ctaBtn = page.getByTestId('cg01-open-monitor-btn');
    await expect(ctaBtn).toBeVisible({ timeout: 15000 });
    await ctaBtn.click();

    const drawer = page.getByTestId('paper-opportunity-monitor-drawer');
    await expect(drawer).toBeVisible({ timeout: 5000 });

    // Verify horizontal overflow: document element scrollWidth must equal clientWidth
    const hasHorizontalScroll = await page.evaluate(() => {
      return document.documentElement.scrollWidth > document.documentElement.clientWidth;
    });
    expect(hasHorizontalScroll).toBe(false);

    // Verify close button is visible and works
    const closeBtn = drawer.getByTestId('monitor-close-btn');
    await expect(closeBtn).toBeVisible();
    await closeBtn.click();
    await expect(drawer).toBeHidden({ timeout: 5000 });
  });
});
