/**
 * E2E Browser Playwright Test for CG-01 Realtime Diagnostic UI (V16.29).
 * Verifies stage, direction, POI display, live WS transition, and responsive layout.
 * Conforms to prompt rules 111-118, 159-161.
 */

import { test, expect } from '@playwright/test';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

test.describe('V16.29 CG-01 Realtime UI Closure', () => {
  test.beforeEach(async ({ page, request }) => {
    await page.goto('about:blank');
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
  });

  test('E01: CG-01 status card renders stage, direction, compact POI and Vietnamese reason', async ({ page, request }) => {
    // 1. Seed initial CG-01 state via test endpoint with structured object ui_summary_vi (Rule 089)
    const initialEval = {
      enabled: true,
      evaluated: true,
      stage: 'WAIT_MSS',
      direction: 'LONG',
      poi_id: 'poi_xauusdt_15m_demand_test_01',
      reason_codes: ['WAITING_MSS_CONFIRMATION'],
      ui_summary_vi: { status: 'Đã có quét thanh khoản; đang chờ nến đóng xác nhận phá cấu trúc và lực đẩy.' },
      as_of_ms: Date.now(),
    };
    const seedRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/cg01_evaluation`, { data: initialEval });
    expect(seedRes.ok()).toBe(true);

    // 2. Open dashboard
    await page.goto('/', { waitUntil: 'domcontentloaded' });


    // 3. Verify CG-01 Diagnostics card is rendered
    const card = page.locator('[data-testid="cg01-diagnostic-card"]');
    await expect(card).toBeVisible({ timeout: 10000 });

    // 4. Verify stage label and direction
    const statusBadge = page.locator('[data-testid="cg01-status-badge"]');
    await expect(statusBadge).toContainText('Chờ Phá Cấu Trúc (MSS)');

    const directionBadge = page.locator('[data-testid="cg01-direction-badge"]');
    await expect(directionBadge).toContainText('LONG');

    // 5. Verify Vietnamese explanation from object status
    const descText = page.locator('[data-testid="cg01-description-text"]');
    await expect(descText).toContainText('phá cấu trúc');

    // 6. Verify expandable details drawer
    const expandBtn = page.locator('[data-testid="cg01-expand-details-btn"]');
    await expandBtn.click();

    const techDetails = page.locator('[data-testid="cg01-technical-details"]');
    await expect(techDetails).toBeVisible();
    await expect(techDetails).toContainText('WAITING_MSS_CONFIRMATION');
    await expect(techDetails).toContainText('poi_xauusdt_15m_demand_test_01');
  });

  test('E02: CG-01 dynamically updates on live WS ARMED broadcast without page reload', async ({ page, request }) => {
    // 1. Initial state: WAIT_FVG
    const initialEval = {
      enabled: true,
      evaluated: true,
      stage: 'WAIT_FVG',
      direction: 'SHORT',
      poi_id: 'poi_m15_supply_test_02',
      reason_codes: ['NO_FVG_ON_LEG'],
      as_of_ms: Date.now(),
    };
    const seed1 = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/cg01_evaluation`, { data: initialEval });
    expect(seed1.ok()).toBe(true);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const statusBadge = page.locator('[data-testid="cg01-status-badge"]');
    await expect(statusBadge).toContainText('Chờ Tạo FVG', { timeout: 10000 });

    // 2. Live transition to ARMED
    const armedEval = {
      enabled: true,
      evaluated: true,
      stage: 'ARMED',
      direction: 'SHORT',
      poi_id: 'poi_m15_supply_test_02',
      armed_order_id: 'order_cg01_e2e_777',
      eligible: true,
      reason_codes: ['EXECUTABLE'],
      as_of_ms: Date.now() + 1000,
    };
    const seed2 = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/cg01_evaluation`, { data: armedEval });
    expect(seed2.ok()).toBe(true);

    // 3. UI immediately updates to ARMED
    await expect(statusBadge).toContainText('Đã Đặt Lệnh Chờ', { timeout: 5000 });
    const descText = page.locator('[data-testid="cg01-description-text"]');
    await expect(descText).toContainText('order_cg01_e2e_777');
  });

  test('E03: Compact rendering on mobile viewport without overflow', async ({ page, request }) => {
    await page.setViewportSize({ width: 390, height: 844 });

    const evalData = {
      enabled: true,
      evaluated: true,
      stage: 'WAIT_MSS',
      direction: 'LONG',
      poi_id: 'poi_very_long_identifier_that_should_be_compacted_nicely_000',
      reason_codes: ['WAITING_MSS_CONFIRMATION'],
      as_of_ms: Date.now(),
    };
    const seedRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/cg01_evaluation`, { data: evalData });
    expect(seedRes.ok()).toBe(true);

    await page.goto('/', { waitUntil: 'domcontentloaded' });

    const card = page.locator('[data-testid="cg01-diagnostic-card"]');
    await expect(card).toBeVisible({ timeout: 10000 });

    // POI badge is compacted and does not overflow
    const poiBadge = page.locator('[data-testid="cg01-poi-badge"]');
    await expect(poiBadge).toBeVisible();
    const text = await poiBadge.textContent();
    expect(text).toContain('...');

    // Rule 091: Measure bounds to verify card fits within mobile viewport width
    const cardBox = await card.boundingBox();
    expect(cardBox).not.toBeNull();
    expect(cardBox!.x + cardBox!.width).toBeLessThanOrEqual(390 + 5);
  });

});
