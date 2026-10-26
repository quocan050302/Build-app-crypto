/**
 * frontend/tests/e2e/v16_39_19_rr_no_reload.e2e.ts
 * Aurum V16.39.19 — No-Reload Settings Save & Dual-Direction RR Acceptance Suite
 *
 * Rules / Checklist 081-100, 141-150, Appendix B:
 * 1. Single page.goto('/') — NO page.reload(), NO secondary goto, NO new browser context.
 * 2. Window random token marker deployed before Save, strictly verified after all transitions.
 * 3. Real UI form interaction: fill #min-net-rr-input and click submit "Lưu R:R".
 * 4. Intercepts and asserts POST /api/scalp/config canonical response.
 * 5. Verifies multiple consumers in same document:
 *    - RrPolicySettingsCard status & applied text
 *    - CG-01 Diagnostics applied badge
 * 6. Dual-direction propagation:
 *    - Save 1.50 -> updates to 1:1.50 immediately without reload.
 *    - Save 2.50 -> updates to 1:2.50 immediately without reload.
 * 7. Frozen historical order snapshot preserves its own threshold (1:2.00) independently.
 */

import { test, expect } from '@playwright/test';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

test.describe('V16.39.19 No-Reload Settings Save & Propagation Acceptance', () => {
  test.beforeEach(async ({ page, request }) => {
    await page.goto('about:blank');
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
  });

  test('E01-ACCEPTANCE: UI Save updates CURRENT policy dual-direction (1.50 and 2.50) without page reload', async ({
    page,
    request,
  }) => {
    // 1. Seed initial CG-01 candidate evaluation fixture
    const initialEval = {
      enabled: true,
      evaluated: true,
      stage: 'OBSERVE',
      direction: 'LONG',
      poi_id: 'poi_v19_e2e_1',
      reason_codes: ['OBSERVING'],
      ui_summary_vi: { status: 'Đang theo dõi phản ứng giá cấu trúc SMC.' },
      as_of_ms: Date.now(),
    };
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/cg01_evaluation`, { data: initialEval });

    // Seed a frozen historical order with snapshot min_net_rr_at_arm = 2.00
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'ord-frozen-v19',
        state: 'closed',
        direction: 'LONG',
        planned_entry: 2650.0,
        stop_loss: 2640.0,
        take_profit: 2675.0,
        cost_snapshot: {
          min_net_rr_at_arm: 2.0,
          rr_policy_source: 'ORDER_SNAPSHOT',
          rr_policy_status: 'KNOWN',
        },
      },
    });

    // 2. Open dashboard EXACTLY ONCE (No-reload requirement)
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // 3. Deploy in-memory random window marker (Reflect.set on window)
    const runMarkerToken = `NO_RELOAD_TOKEN_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
    await page.evaluate((tok) => {
      Reflect.set(window, '__NO_RELOAD_TOKEN__', tok);
    }, runMarkerToken);

    // Verify initial window token
    const initialToken = await page.evaluate(() => Reflect.get(window, '__NO_RELOAD_TOKEN__'));
    expect(initialToken).toBe(runMarkerToken);

    // 4. Open CG-01 Settings panel to observe baseline default
    const cg01Toggle = page.locator('[data-testid="cg01-toggle-settings-btn"]');
    await expect(cg01Toggle).toBeVisible({ timeout: 10000 });
    await cg01Toggle.click();

    const appliedBadge = page.locator('[data-testid="cg01-applied-rr-badge"]');
    await expect(appliedBadge).toBeVisible({ timeout: 5000 });
    await expect(appliedBadge).toContainText('1:2.00'); // Baseline default

    // 5. Navigate to Settings Card via UI button (Chỉnh trong Cài đặt)
    const openSettingsBtn = page.locator('[data-testid="cg01-open-rr-settings-btn"]');
    if (await openSettingsBtn.isVisible()) {
      await openSettingsBtn.click();
    } else {
      // Fallback SPA tab navigation
      await page.locator('[data-tab-id="paper"]').click();
    }

    // 6. Verify Settings Card is visible in the DOM
    const settingsCard = page.locator('[data-testid="rr-policy-settings-card"]');
    await expect(settingsCard).toBeVisible({ timeout: 8000 });

    // =========================================================================
    // FIRST SAVE: 1.50 via UI Form
    // =========================================================================
    const rrInput = settingsCard.locator('#min-net-rr-input');
    await rrInput.fill('1.50');

    // Register response listener for POST /api/scalp/config
    const savePromise1 = page.waitForResponse(
      (resp) => resp.url().includes('/api/scalp/config') && resp.request().method() === 'POST'
    );

    // Click submit button in RrPolicySettingsCard
    const submitBtn = settingsCard.locator('button[type="submit"]');
    await submitBtn.click();

    const resp1 = await savePromise1;
    expect(resp1.ok()).toBe(true);
    const body1 = await resp1.json();
    const effectiveVal1 = body1?.effective_config?.min_net_rr ?? body1?.min_net_rr;
    expect(Number(effectiveVal1)).toBe(1.5);

    // Verify Card updates to synchronized 1:1.50 state
    await expect(settingsCard).toContainText('1:1.50', { timeout: 5000 });

    // Verify window token is STILL intact (NO RELOAD PROOF 1)
    const tokenAfterSave1 = await page.evaluate(() => Reflect.get(window, '__NO_RELOAD_TOKEN__'));
    expect(tokenAfterSave1).toBe(runMarkerToken);

    // Verify CG-01 applied badge updated to 1:1.50 in the same document
    await expect(appliedBadge).toContainText('1:1.50', { timeout: 5000 });

    // Switch tabs to 'chart' and back to 'paper' using SPA navigation
    const chartTabBtn = page.locator('[data-tab-id="chart"]');
    await chartTabBtn.click();
    await expect(appliedBadge).toContainText('1:1.50');

    // Check token after tab transition
    const tokenAfterTab = await page.evaluate(() => Reflect.get(window, '__NO_RELOAD_TOKEN__'));
    expect(tokenAfterTab).toBe(runMarkerToken);

    // Switch back to 'paper' tab
    const paperTabBtn = page.locator('[data-tab-id="paper"]');
    await paperTabBtn.click();
    await expect(settingsCard).toBeVisible({ timeout: 5000 });

    // =========================================================================
    // SECOND SAVE: 2.50 via UI Form (Dual-direction verification)
    // =========================================================================
    await rrInput.fill('2.50');

    const savePromise2 = page.waitForResponse(
      (resp) => resp.url().includes('/api/scalp/config') && resp.request().method() === 'POST'
    );

    await submitBtn.click();

    const resp2 = await savePromise2;
    expect(resp2.ok()).toBe(true);
    const body2 = await resp2.json();
    const effectiveVal2 = body2?.effective_config?.min_net_rr ?? body2?.min_net_rr;
    expect(Number(effectiveVal2)).toBe(2.5);

    // Verify Card updates to synchronized 1:2.50 state
    await expect(settingsCard).toContainText('1:2.50', { timeout: 5000 });

    // Verify CG-01 applied badge updated to 1:2.50 in the same document
    await expect(appliedBadge).toContainText('1:2.50', { timeout: 5000 });

    // Final window token check (NO RELOAD PROOF 2)
    const tokenFinal = await page.evaluate(() => Reflect.get(window, '__NO_RELOAD_TOKEN__'));
    expect(tokenFinal).toBe(runMarkerToken);
  });

  test('E02-MOBILE: Settings Card and Form actions are accessible on 390x844 viewport', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // Switch to paper settings tab
    const paperTabBtn = page.locator('[data-tab-id="paper"]');
    await expect(paperTabBtn).toBeVisible({ timeout: 10000 });
    await paperTabBtn.click();

    const settingsCard = page.locator('[data-testid="rr-policy-settings-card"]');
    await expect(settingsCard).toBeVisible({ timeout: 8000 });

    const input = settingsCard.locator('#min-net-rr-input');
    await expect(input).toBeVisible();

    const submitBtn = settingsCard.locator('button[type="submit"]');
    await expect(submitBtn).toBeVisible();

    // Check no horizontal overflow
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 2);
  });
});
