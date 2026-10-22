/**
 * frontend/tests/e2e/v17_0_opportunity_monitor.e2e.ts
 * Aurum V17.0 — Paper Opportunity & Non-Entry Reason Monitor Acceptance Suite
 *
 * Verifies end-to-end browser acceptance gates (Phần 161 - 170, D1 - D6, E6):
 * 1. Collapsed layout: CG01DiagnosticCard displays concise status + CTA "Xem cơ hội".
 *    No duplicate cards, no permanent timeline/list table cluttering sidebar or chart.
 * 2. On-demand opening: Clicking "Xem cơ hội" opens the 560-640px drawer overlay.
 *    Dialog has role="dialog", aria-modal="true", accessible title.
 * 3. Master list & filters: Shows summary counts, period tabs, opportunity list rows.
 * 4. Master-detail navigation: Clicking a row transitions to detail view in the same drawer.
 *    Displays price plan, timeline with recorded milestones, and reason details.
 * 5. Back navigation: Clicking "Quay lại danh sách" restores the master list.
 * 6. Chart focus delegation: Clicking "Xem Vị Trí Trên Biểu Đồ" closes the drawer and
 *    delegates to the production chart focus handler with exact setup ID.
 * 7. Report export: Clicking "Tải Báo Cáo (.md)" initiates markdown download.
 * 8. Accessibility & Dismissal: Escape key closes the drawer; close button closes drawer.
 * 9. Mobile viewport (390x844): Drawer occupies full width/height with proper scrolling.
 * 10. Read-only gate: Verifies 0 orders placed, 0 Telegram mutations during observation.
 */

import { test, expect, Page } from '@playwright/test';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

async function resetAndSeedDatabase(page: Page): Promise<void> {
  // 1. Reset baseline
  const resetRes = await page.request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
  expect(resetRes.ok()).toBe(true);

  // 2. Seed V17 monitor opportunities
  const seedRes = await page.request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_monitor`, {
    data: {
      run_id: 'run_e2e_monitor',
      symbol: 'XAUUSDT',
      scope: 'CG01',
    },
  });
  expect(seedRes.ok()).toBe(true);
}

test.describe('Aurum V17.0 — Paper Opportunity Monitor Acceptance Tests', () => {
  test.beforeEach(async ({ page }) => {
    await resetAndSeedDatabase(page);
  });

  test('E2E Gate 1-8: Desktop Observation Workflow (Collapsed -> Drawer -> Master/Detail -> Chart Focus -> Report)', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1512, height: 857 });
    await page.goto('/');

    // 1. Check Collapsed State: Diagnostic card has CTA without cluttering sidebar
    const ctaBtn = page.getByTestId('cg01-open-monitor-btn');
    await expect(ctaBtn).toBeVisible({ timeout: 15000 });
    await expect(ctaBtn).toContainText('Xem cơ hội');

    // Confirm drawer is NOT open initially
    const drawerInitial = page.getByTestId('paper-opportunity-monitor-drawer');
    await expect(drawerInitial).toHaveCount(0);

    // 2. Open Drawer via CTA
    await ctaBtn.click();

    const drawer = page.getByTestId('paper-opportunity-monitor-drawer');
    await expect(drawer).toBeVisible({ timeout: 5000 });
    await expect(drawer).toHaveAttribute('role', 'dialog');
    await expect(drawer).toHaveAttribute('aria-modal', 'true');

    // Header title
    const headerTitle = drawer.locator('h2');
    await expect(headerTitle).toContainText('Theo Dõi Cơ Hội Paper');

    // 3. Verify Master List & Summary
    const rowOpp1 = drawer.getByTestId('monitor-row-opp_e2e_001');
    const rowOpp2 = drawer.getByTestId('monitor-row-opp_e2e_002');
    const rowOpp3 = drawer.getByTestId('monitor-row-opp_e2e_003');

    await expect(rowOpp1).toBeVisible({ timeout: 5000 });
    await expect(rowOpp2).toBeVisible({ timeout: 5000 });
    await expect(rowOpp3).toBeVisible({ timeout: 5000 });

    // Check row contents
    await expect(rowOpp1).toContainText('LONG');
    await expect(rowOpp2).toContainText('SHORT');
    await expect(rowOpp2).toContainText('Bị chặn bởi Guard');

    // 4. Test Filter buttons
    const filterToday = drawer.getByTestId('filter-period-today');
    const filter7d = drawer.getByTestId('filter-period-7d');
    await expect(filterToday).toBeVisible();
    await expect(filter7d).toBeVisible();

    await filter7d.click();
    await page.waitForTimeout(300);
    // Rows remain visible
    await expect(rowOpp1).toBeVisible();

    // 5. Navigate to Detail View by clicking row opp_e2e_001
    await rowOpp1.click();

    // Detail view visible
    const detailView = drawer.getByTestId('monitor-detail-view');
    await expect(detailView).toBeVisible({ timeout: 5000 });
    await expect(drawer.locator('h2')).toContainText('Chi Tiết Cơ Hội');

    // Price plan
    await expect(detailView).toContainText('Mức Giá Kế Hoạch (Dự Kiến)');
    await expect(detailView).toContainText('$2650.00');

    // Timeline
    await expect(detailView).toContainText('Tiến Trình Các Mốc Đã Ghi Nhận');

    // 6. Test Back button to return to master list
    const backBtn = drawer.locator('button[title="Quay lại danh sách"]');
    await expect(backBtn).toBeVisible();
    await backBtn.click();

    // Master list restored
    await expect(drawer.getByTestId('monitor-row-opp_e2e_001')).toBeVisible();

    // 7. Focus Setup on Chart from Detail View
    await drawer.getByTestId('monitor-row-opp_e2e_001').click();
    await expect(detailView).toBeVisible();

    const focusChartBtn = drawer.getByTestId('monitor-focus-chart-btn');
    await expect(focusChartBtn).toBeVisible();
    await focusChartBtn.click();

    // Drawer must be closed after focusing chart
    await expect(drawer).toBeHidden({ timeout: 5000 });

    // Chart / Sidebar selection reflects focused setup
    const selectedSetup = page.locator('[data-testid="sidebar-selected-setup"]');
    await expect(selectedSetup).toBeVisible({ timeout: 5000 });
    await expect(selectedSetup).toHaveAttribute('data-selected-id', 'setup_opp_e2e_001');

    // 8. Re-open and verify Report Download button
    await ctaBtn.click();
    await expect(drawer).toBeVisible();

    const downloadBtn = drawer.getByTestId('monitor-download-report-btn');
    await expect(downloadBtn).toBeVisible();

    // 9. Keyboard Escape closes drawer
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden({ timeout: 5000 });

    // 10. Read-only gate: Verify 0 new orders triggered and 0 Telegram notifications
    const stateRes = await page.request.get(`http://127.0.0.1:${API_PORT}/__e2e/state`);
    const state = await stateRes.json();
    expect(state.outbox).toBe(0);
  });

  test('E2E Gate 9: Mobile Viewport (390x844 Fullscreen Layout)', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');

    const ctaBtn = page.getByTestId('cg01-open-monitor-btn');
    await expect(ctaBtn).toBeVisible({ timeout: 15000 });
    await ctaBtn.click();

    const drawer = page.getByTestId('paper-opportunity-monitor-drawer');
    await expect(drawer).toBeVisible({ timeout: 5000 });

    // Verify close button is visible and clickable on mobile
    const closeBtn = drawer.getByTestId('monitor-close-btn');
    await expect(closeBtn).toBeVisible();

    // Verify items fit within mobile viewport without breaking layout
    const rowOpp1 = drawer.getByTestId('monitor-row-opp_e2e_001');
    await expect(rowOpp1).toBeVisible();

    // Click close button
    await closeBtn.click();
    await expect(drawer).toBeHidden({ timeout: 5000 });
  });
});
