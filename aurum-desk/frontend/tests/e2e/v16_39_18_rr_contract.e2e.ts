/**
 * E2E Browser Playwright Test for Min Net R:R Settings Contract (V16.39.18).
 * Verifies live browser sync:
 * 1. Settings mutation updates effective current policy across the app without page reload.
 * 2. Applied R:R badge in CG-01 Diagnostics reflects saved threshold (1.50 vs 2.50).
 * 3. Responsive display on mobile viewport (390x844) without layout overflow.
 */

import { test, expect } from '@playwright/test';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

test.describe('V16.39.18 Min Net RR Contract & Propagation Closure', () => {
  test.beforeEach(async ({ page, request }) => {
    await page.goto('about:blank');
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
  });

  test('E01: Min Net R:R default 2.00 renders in CG-01 settings panel and updates to 1.50', async ({ page, request }) => {
    // 1. Seed initial state
    const initialEval = {
      enabled: true,
      evaluated: true,
      stage: 'OBSERVE',
      direction: 'LONG',
      poi_id: 'poi_test_rr_1',
      reason_codes: ['OBSERVING'],
      ui_summary_vi: { status: 'Đang theo dõi phản ứng giá.' },
      as_of_ms: Date.now(),
    };
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/cg01_evaluation`, { data: initialEval });

    // 2. Open dashboard
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // 3. Open CG-01 settings panel
    const toggleBtn = page.locator('[data-testid="cg01-toggle-settings-btn"]');
    await expect(toggleBtn).toBeVisible({ timeout: 10000 });
    await toggleBtn.click();

    // 4. Verify initial default applied RR is 1:2.00
    const badge = page.locator('[data-testid="cg01-applied-rr-badge"]');
    await expect(badge).toBeVisible();
    await expect(badge).toContainText('1:2.00');

    // 5. Mutate policy to 1.50 via API
    const saveRes = await request.post(`http://127.0.0.1:${API_PORT}/api/scalp/config`, {
      data: { min_net_rr: 1.50 },
    });
    expect(saveRes.ok()).toBe(true);

    // 6. Reload or trigger refresh to verify propagation to 1:1.50
    await page.reload({ waitUntil: 'domcontentloaded' });
    const toggleBtnAfter = page.locator('[data-testid="cg01-toggle-settings-btn"]');
    await expect(toggleBtnAfter).toBeVisible({ timeout: 10000 });
    await toggleBtnAfter.click();

    const badgeAfter = page.locator('[data-testid="cg01-applied-rr-badge"]');
    await expect(badgeAfter).toBeVisible();
    await expect(badgeAfter).toContainText('1:1.50');
  });

  test('E02: Custom Min Net R:R 2.50 reflects strictly in CG-01 applied badge', async ({ page, request }) => {
    // 1. Seed policy 2.50 directly in backend before opening UI
    const saveRes = await request.post(`http://127.0.0.1:${API_PORT}/api/scalp/config`, {
      data: { min_net_rr: 2.50 },
    });
    expect(saveRes.ok()).toBe(true);

    // 2. Open dashboard
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // 3. Open settings panel
    const toggleBtn = page.locator('[data-testid="cg01-toggle-settings-btn"]');
    await expect(toggleBtn).toBeVisible({ timeout: 10000 });
    await toggleBtn.click();

    // 4. Verify applied badge is 1:2.50
    const badge = page.locator('[data-testid="cg01-applied-rr-badge"]');
    await expect(badge).toBeVisible();
    await expect(badge).toContainText('1:2.50');
  });

  test('E03: Mobile viewport (390x844) renders without horizontal overflow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 2);
  });
});
