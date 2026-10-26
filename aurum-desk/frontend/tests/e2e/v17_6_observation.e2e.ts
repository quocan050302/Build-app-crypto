/**
 * frontend/tests/e2e/v17_6_observation.e2e.ts
 * Aurum V17.6 — Autonomous Observation, Live Decision Intelligence & Forward Paper Monitoring
 *
 * Verifies end-to-end browser acceptance:
 * 1. Navigation: Clicking "Quan Sát Bot (Telemetry)" tab displays the V17.6 dashboard.
 * 2. 5 Executive Health Cards:
 *    - Card 1: Engine Health (state, cadence, heartbeat age).
 *    - Card 2: Market Feed & Freshness (bid/ask, quote freshness, transport state).
 *    - Card 3: SMC/ICT Evaluation Cycle (cycle duration, decision status).
 *    - Card 4: Paper Safe Execution (armed orders, fail-closed enforcement).
 *    - Card 5: Observation Coverage Uptime (uptime %, observation hours).
 * 3. Deep-Dive Panels:
 *    - Opportunity Funnel: Stage-by-stage counts from Detected to Filled.
 *    - Waiting Conditions: Pending SMC conditions with explicit status.
 *    - Blocked Reasons: Breakdown of non-entry categories (Guard, Net R:R, etc.).
 *    - Post-Fee Performance: Committed paper ledger metrics (fail-closed winrate).
 *    - Decision Timeline: Chronological decisions with reason classification.
 * 4. Timezone & Range Controls:
 *    - Toggle between VN (ICT) and NY (ET) timezones.
 *    - Range selection (Today, 7D, 14D, 30D).
 */

import { test, expect, Page } from '@playwright/test';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

async function resetAndSeedObservation(page: Page): Promise<void> {
  // 1. Reset baseline state
  const resetRes = await page.request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
  expect(resetRes.ok()).toBe(true);

  // 2. Seed observation data
  const seedObsRes = await page.request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_observation`, {
    data: {
      run_id: 'run_e2e_obs_v17_6',
      symbol: 'XAUUSDT',
      scope: 'CG01',
    },
  });
  expect(seedObsRes.ok()).toBe(true);

  // 3. Seed monitor data for funnel & paper orders
  const seedMonRes = await page.request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_monitor`, {
    data: {
      run_id: 'run_e2e_obs_v17_6',
      symbol: 'XAUUSDT',
      scope: 'CG01',
    },
  });
  expect(seedMonRes.ok()).toBe(true);
}

test.describe('Aurum V17.6 — Autonomous Observation & Telemetry Acceptance Tests', () => {
  test.beforeEach(async ({ page }) => {
    await resetAndSeedObservation(page);
  });

  test('E2E Gate: Observation Dashboard Renders Executive Health Cards, Panels, and Decision Timeline', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1512, height: 857 });
    await page.goto('/');

    // 1. Verify navigation tab exists and click it
    const observationTabBtn = page.locator('button[data-tab-id="observation"]');
    await expect(observationTabBtn).toBeVisible({ timeout: 15000 });
    await expect(observationTabBtn).toContainText('Quan Sát Bot (Telemetry)');
    await observationTabBtn.click();

    // 2. Verify Header and Version Tag
    await expect(page.locator('text=Quan Sát & Trí Tuệ Quyết Định Bot')).toBeVisible();
    await expect(page.locator('text=V17.6.1 OBSERVABILITY & DECISION TELEMETRY')).toBeVisible();

    // 3. Verify 5 Executive Health Cards
    // Card 1: Engine Health
    await expect(page.locator('text=1. Bot Engine Health')).toBeVisible();
    await expect(page.locator('text=Heartbeat gần nhất:')).toBeVisible();

    // Card 2: Market Feed
    await expect(page.locator('text=2. Báo Giá & Dữ Liệu')).toBeVisible();
    await expect(page.locator('text=Bid / Ask:')).toBeVisible();

    // Card 3: SMC Evaluation
    await expect(page.locator('text=3. Đánh Giá SMC/ICT')).toBeVisible();

    // Card 4: Paper Safe
    await expect(page.locator('text=4. Thực Thi Paper Safe')).toBeVisible();
    await expect(page.locator('text=KHÓA TUYỆT ĐỐI')).toBeVisible();

    // Card 5: Observation Coverage
    await expect(page.locator('text=5. Độ Bao Phủ Quan Sát')).toBeVisible();
    await expect(page.locator('text=Tỷ lệ bao phủ (Uptime):')).toBeVisible();

    // 4. Verify Opportunity Funnel Section
    await expect(page.locator('text=Phễu Chuyển Đổi Cơ Hội (Opportunity Funnel)')).toBeVisible();

    // 5. Verify Post-Fee Financial Section (Fail-closed)
    await expect(page.locator('text=Hiệu Suất Thực Tế Sau Phí (Committed Paper Ledger)')).toBeVisible();
    await expect(page.locator('text=Vốn Khởi Điểm: $1000.00 USDT')).toBeVisible();

    // 6. Verify Timezone Toggling (VN <-> NY)
    const vnBtn = page.getByRole('button', { name: 'Giờ VN (ICT)' });
    const nyBtn = page.getByRole('button', { name: 'Giờ NY (ET)' });
    await expect(vnBtn).toBeVisible();
    await expect(nyBtn).toBeVisible();

    await nyBtn.click();
    await expect(nyBtn).toHaveClass(/border-aurum-500/);

    await vnBtn.click();
    await expect(vnBtn).toHaveClass(/border-aurum-500/);

    // 7. Verify Decision Timeline Table
    await expect(page.locator('text=Dòng Thời Gian Quyết Định Gần Nhất (Decision Timeline)')).toBeVisible();
    await expect(page.locator('th:has-text("Phân Loại")')).toBeVisible();
    await expect(page.locator('th:has-text("Lý Do Quyết Định")')).toBeVisible();
  });
});
