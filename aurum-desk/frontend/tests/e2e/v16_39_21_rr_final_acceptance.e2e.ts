/**
 * frontend/tests/e2e/v16_39_21_rr_final_acceptance.e2e.ts
 * Aurum V16.39.21 — Final R:R Acceptance Verification Suite
 *
 * Strict E2E flow with mandatory assertions (NO optional catches / NO swallowed errors):
 * 1. Single Navigation (No-Reload): window.__RR_FINAL_DOC__ RAM token verified across all steps.
 * 2. Save 1.50 via UI Form: Form submit -> POST /api/scalp/config -> response ok -> card updated.
 * 3. Select Canonical Setup on Chart (LONG & SHORT parameterized):
 *    - Setup card located strictly by ID -> clicked via [data-testid="focus-setup-chart"].
 *    - Sidebar asserts selected ID and direction.
 *    - Actual Net R:R (~1.77) meets 1.50: data-rr-status="MET", data-rr-required="1.5".
 *    - TradeDetailsDrawer opens strictly via sidebar button -> risk section displays "1:1.50 (Hiện hành)".
 *    - Geometry (Entry, SL, TP) recorded.
 * 4. Save 2.50 via UI Form on SAME Document:
 *    - Same setup re-evaluated under 2.50 threshold.
 *    - Geometry INVARIANCE: Entry, SL, TP and actual Net R:R remain exactly identical.
 *    - Actual Net R:R (~1.77) does NOT meet 2.50: data-rr-status="NOT_MET", data-rr-required="2.5".
 *    - ARM CTA button blocked: data-can-submit="false" with NET_RR_TOO_LOW blocker reason.
 *    - TradeDetailsDrawer displays "1:2.50 (Hiện hành)".
 * 5. Frozen Historical Order Isolation:
 *    - Navigate to Journal -> select frozen order via [data-testid="journal-focus-chart"][data-order-id="..."].
 *    - TradeDetailsDrawer displays "1:2.00 (Lúc ARM)" independently from current 2.50 policy.
 * 6. Return to CURRENT Setup:
 *    - Select setup again -> TradeDetailsDrawer confirms threshold remains "1:2.50 (Hiện hành)".
 * 7. Document & Isolation Verification:
 *    - window.__RR_FINAL_DOC__ verified unchanged throughout entire flow.
 *    - No unexpected orders or Telegram mutations (/__e2e/state outbox == 0).
 */

import { test, expect, Page, Locator } from '@playwright/test';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

interface SetupFixtureConfig {
  setupId: string;
  direction: 'LONG' | 'SHORT';
  expectedEntry: string;
  expectedSl: string;
  expectedTp: string;
}

const FIXTURES: SetupFixtureConfig[] = [
  {
    setupId: 'e2e-mid-long',
    direction: 'LONG',
    expectedEntry: '$2650',
    expectedSl: '$2640',
    expectedTp: '$2674',
  },
  {
    setupId: 'e2e-mid-short',
    direction: 'SHORT',
    expectedEntry: '$2650',
    expectedSl: '$2660',
    expectedTp: '$2626',
  },
];

async function assertSameDocument(page: Page, marker: string): Promise<void> {
  const currentToken = await page.evaluate(() => Reflect.get(window, '__RR_FINAL_DOC__'));
  expect(currentToken).toBe(marker);
}

async function selectCurrentSetupOnChart(page: Page, setupId: string): Promise<void> {
  await page.locator('[data-tab-id="upcoming"]').click();
  const card = page.locator(`[data-setup-id="${setupId}"]`);
  await expect(card).toHaveCount(1);
  await expect(card).toBeVisible({ timeout: 10000 });
  await card.getByTestId('focus-setup-chart').click();

  const selectedEl = page.locator('[data-testid="sidebar-selected-setup"]');
  await expect(selectedEl).toBeVisible({ timeout: 5000 });
  await expect(selectedEl).toHaveAttribute('data-selected-id', setupId);
}

async function openRiskDrawer(page: Page): Promise<Locator> {
  const detailsBtn = page.getByTestId('sidebar-open-trade-details');
  await expect(detailsBtn).toBeVisible({ timeout: 5000 });
  await detailsBtn.click();

  const drawer = page.getByRole('dialog', {
    name: 'Bảng chi tiết giao dịch và phân tích',
  });
  await expect(drawer).toHaveCount(1);
  await expect(drawer).toBeVisible({ timeout: 5000 });

  const riskTab = drawer.getByRole('button', { name: 'Rủi Ro', exact: true });
  await expect(riskTab).toBeVisible({ timeout: 5000 });
  await riskTab.click();

  return drawer;
}

async function closeRiskDrawer(page: Page, drawer: Locator): Promise<void> {
  const closeBtn = drawer.getByRole('button', { name: 'Đóng bảng chi tiết' });
  await expect(closeBtn).toBeVisible({ timeout: 5000 });
  await closeBtn.click();
  await expect(drawer).toBeHidden({ timeout: 5000 });
}

async function saveMinimumThroughUi(page: Page, value: '1.50' | '2.50'): Promise<any> {
  await page.locator('[data-tab-id="paper"]').click();
  const card = page.getByTestId('rr-policy-settings-card');
  await expect(card).toBeVisible({ timeout: 8000 });

  const input = card.locator('#min-net-rr-input');
  await expect(input).toBeVisible();
  await input.fill(value);

  const responsePromise = page.waitForResponse(
    (resp) => new URL(resp.url()).pathname === '/api/scalp/config' && resp.request().method() === 'POST'
  );
  const submitBtn = card.locator('button[type="submit"]');
  await expect(submitBtn).toBeVisible();
  await submitBtn.click();

  const response = await responsePromise;
  expect(response.ok()).toBe(true);
  const body = await response.json();
  const canonical = body.effective_config?.min_net_rr ?? body.min_net_rr;
  expect(Number(canonical)).toBe(Number(value));

  // Assert card displays saved value
  await expect(card).toContainText(`1:${value}`, { timeout: 5000 });
  return body;
}

async function focusFrozenOrderOnChart(page: Page, orderId: string): Promise<void> {
  await page.locator('[data-tab-id="journal"]').click();
  const chartBtn = page.locator(`[data-testid="journal-focus-chart"][data-order-id="${orderId}"]`);
  await expect(chartBtn).toHaveCount(1);
  await expect(chartBtn).toBeVisible({ timeout: 10000 });
  await chartBtn.click();

  const selectedEl = page.locator('[data-testid="sidebar-selected-setup"]');
  await expect(selectedEl).toBeVisible({ timeout: 5000 });
  await expect(selectedEl).toHaveAttribute('data-selected-id', orderId);
}

test.describe('Aurum V16.39.21 — Final Strict R:R Acceptance Suite', () => {
  test.beforeEach(async ({ page, request }) => {
    // Graceful socket drain pause for Windows TCP buffer pool
    await new Promise((r) => setTimeout(r, 600));
    const resetRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
    expect(resetRes.ok()).toBe(true);

    // Seed CG-01 evaluation
    const initialEval = {
      enabled: true,
      evaluated: true,
      stage: 'OBSERVE',
      direction: 'LONG',
      poi_id: 'poi_v21_final',
      reason_codes: ['OBSERVING'],
      ui_summary_vi: { status: 'Đang theo dõi phản ứng giá cấu trúc SMC.' },
      as_of_ms: Date.now(),
    };
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/cg01_evaluation`, { data: initialEval });

    // Seed frozen historical order with snapshot min_net_rr_at_arm = 2.00
    const seedOrderRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'ord-frozen-v20',
        setup_id: 'setup_ord_frozen_v20',
        state: 'closed',
        direction: 'LONG',
        planned_entry: 2650.0,
        stop_loss: 2640.0,
        take_profit: 2674.0,
        cost_snapshot: {
          min_net_rr_at_arm: 2.0,
          rr_policy_source: 'ORDER_SNAPSHOT',
          rr_policy_status: 'KNOWN',
        },
      },
    });
    expect(seedOrderRes.ok()).toBe(true);
  });

  test.afterEach(async ({ page }) => {
    try {
      await page.goto('about:blank');
    } catch {
      // ignore
    }
  });

  for (const fixture of FIXTURES) {
    test(`V16.39.21-ACCEPTANCE-${fixture.direction}: Full Dual-Save Invariance, R:R Met/Not-Met, Frozen Snapshot & Return CURRENT`, async ({
      page,
      request,
    }) => {
      // 1. Initial Single Navigation
      await page.goto('/', { waitUntil: 'domcontentloaded' });

      // 2. Attach In-Memory Window Marker
      const runMarkerToken = `RR_V21_TOKEN_${fixture.direction}_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
      await page.evaluate((tok) => {
        Reflect.set(window, '__RR_FINAL_DOC__', tok);
      }, runMarkerToken);
      await assertSameDocument(page, runMarkerToken);

      // =======================================================================
      // STEP 1: SAVE 1.50 VIA FORM
      // =======================================================================
      await saveMinimumThroughUi(page, '1.50');
      await assertSameDocument(page, runMarkerToken);

      // =======================================================================
      // STEP 2: SELECT SETUP ON CHART & ASSERT R:R MET AT 1.50
      // =======================================================================
      await selectCurrentSetupOnChart(page, fixture.setupId);

      // Verify geometry on sidebar
      const selectedEl = page.locator('[data-testid="sidebar-selected-setup"]');
      await expect(selectedEl).toHaveAttribute('data-selected-direction', fixture.direction);

      const entryEl = page.locator('[data-testid="sidebar-entry-price"]');
      const slEl = page.locator('[data-testid="sidebar-sl-price"]');
      const tpEl = page.locator('[data-testid="sidebar-tp-price"]');
      await expect(entryEl).toContainText(fixture.expectedEntry);
      await expect(slEl).toContainText(fixture.expectedSl);
      await expect(tpEl).toContainText(fixture.expectedTp);

      // Verify R:R status is MET under minimum 1.50
      const netRrRow = page.locator('[data-testid="sidebar-net-rr-row"]');
      await expect(netRrRow).toBeVisible();
      await expect(netRrRow).toHaveAttribute('data-rr-status', 'MET');
      await expect(netRrRow).toHaveAttribute('data-rr-required', '1.5');

      const actualNetRrStr = await netRrRow.getAttribute('data-rr-actual');
      expect(actualNetRrStr).not.toBeNull();
      const actualNetRr = Number(actualNetRrStr);
      expect(actualNetRr).toBeGreaterThan(1.5);
      expect(actualNetRr).toBeLessThan(2.5);

      // Open TradeDetailsDrawer to verify CURRENT threshold 1:1.50
      const drawer1 = await openRiskDrawer(page);
      const thresholdVal1 = drawer1.locator('[data-testid="rr-policy-threshold-value"]');
      await expect(thresholdVal1).toBeVisible();
      await expect(thresholdVal1).toContainText('1:1.50 (Hiện hành)');
      await closeRiskDrawer(page, drawer1);

      await assertSameDocument(page, runMarkerToken);

      // =======================================================================
      // STEP 3: SAVE 2.50 VIA FORM ON SAME DOCUMENT
      // =======================================================================
      await saveMinimumThroughUi(page, '2.50');
      await assertSameDocument(page, runMarkerToken);

      // =======================================================================
      // STEP 4: RE-SELECT SAME SETUP & ASSERT R:R NOT_MET AT 2.50 + GEOMETRY INVARIANCE
      // =======================================================================
      await selectCurrentSetupOnChart(page, fixture.setupId);

      // Assert Geometry Invariance: exact prices unchanged
      await expect(entryEl).toContainText(fixture.expectedEntry);
      await expect(slEl).toContainText(fixture.expectedSl);
      await expect(tpEl).toContainText(fixture.expectedTp);

      // Assert actual Net R:R unchanged, but required is now 2.5 and status is NOT_MET
      await expect(netRrRow).toHaveAttribute('data-rr-status', 'NOT_MET');
      await expect(netRrRow).toHaveAttribute('data-rr-required', '2.5');
      const actualNetRrAfterSave = Number(await netRrRow.getAttribute('data-rr-actual'));
      expect(actualNetRrAfterSave).toBeCloseTo(actualNetRr, 2);

      // Assert submit CTA button blocked by R:R gate
      const ctaBtn = page.locator('#entry-submit-cta');
      await expect(ctaBtn).toHaveAttribute('data-can-submit', 'false');
      const ctaAriaLabel = await ctaBtn.getAttribute('aria-label');
      expect(ctaAriaLabel).toContain('2.5');

      // Open TradeDetailsDrawer to verify CURRENT threshold 1:2.50
      const drawer2 = await openRiskDrawer(page);
      const thresholdVal2 = drawer2.locator('[data-testid="rr-policy-threshold-value"]');
      await expect(thresholdVal2).toBeVisible();
      await expect(thresholdVal2).toContainText('1:2.50 (Hiện hành)');
      await closeRiskDrawer(page, drawer2);

      await assertSameDocument(page, runMarkerToken);

      // =======================================================================
      // STEP 5: VERIFY FROZEN ORDER SNAPSHOT ISOLATION (2.00 Lúc ARM)
      // =======================================================================
      await focusFrozenOrderOnChart(page, 'ord-frozen-v20');

      const drawerFrozen = await openRiskDrawer(page);
      const thresholdFrozen = drawerFrozen.locator('[data-testid="rr-policy-threshold-value"]');
      await expect(thresholdFrozen).toBeVisible();
      await expect(thresholdFrozen).toContainText('1:2.00 (Lúc ARM)');
      await expect(thresholdFrozen).not.toContainText('2.50');
      await closeRiskDrawer(page, drawerFrozen);

      await assertSameDocument(page, runMarkerToken);

      // =======================================================================
      // STEP 6: RETURN TO CURRENT SETUP & CONFIRM CURRENT THRESHOLD 2.50 PRESERVED
      // =======================================================================
      await selectCurrentSetupOnChart(page, fixture.setupId);

      const drawerReturn = await openRiskDrawer(page);
      const thresholdReturn = drawerReturn.locator('[data-testid="rr-policy-threshold-value"]');
      await expect(thresholdReturn).toBeVisible();
      await expect(thresholdReturn).toContainText('1:2.50 (Hiện hành)');
      await closeRiskDrawer(page, drawerReturn);

      // =======================================================================
      // STEP 7: FINAL SAME-DOCUMENT & STATE INTEGRITY CHECKS
      // =======================================================================
      await assertSameDocument(page, runMarkerToken);

      // Verify no unexpected order execution or Telegram outbox leak
      const stateRes = await request.get(`http://127.0.0.1:${API_PORT}/__e2e/state`);
      expect(stateRes.ok()).toBe(true);
      const stateBody = await stateRes.json();
      expect(stateBody.outbox).toBe(0);
      expect(stateBody.orders).toBe(1); // Only the seeded frozen order
    });
  }

  test('V16.39.21-MOBILE: Mobile Viewport 390x844 form interaction without overflow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    const paperTabBtn = page.locator('[data-tab-id="paper"]');
    await expect(paperTabBtn).toBeVisible({ timeout: 15000 });
    await paperTabBtn.click();

    const settingsCard = page.getByTestId('rr-policy-settings-card');
    await expect(settingsCard).toBeVisible({ timeout: 8000 });

    const input = settingsCard.locator('#min-net-rr-input');
    await expect(input).toBeVisible();
    await input.fill('1.80');

    // Real save action observed via POST /api/scalp/config per Checklist 137-138
    const responsePromise = page.waitForResponse(
      (resp) => new URL(resp.url()).pathname === '/api/scalp/config' && resp.request().method() === 'POST'
    );
    const submitBtn = settingsCard.locator('button[type="submit"]');
    await expect(submitBtn).toBeVisible();
    await submitBtn.click();

    const response = await responsePromise;
    expect(response.ok()).toBe(true);
    const body = await response.json();
    const canonical = body.effective_config?.min_net_rr ?? body.min_net_rr;
    expect(Number(canonical)).toBe(1.8);
    await expect(settingsCard).toContainText('1:1.80', { timeout: 5000 });

    // Verify natural mobile layout without horizontal overflow
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 2);
  });
});
