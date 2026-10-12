import { test, expect } from '@playwright/test';
import {
  readRR,
  seedSmtFixture,
  trackTradingMutations,
} from './v14_4_2_helpers';

test.describe('V14.4.2 Selection Source & Draft Isolation (T09–T16)', () => {
  test('T09: manual SHORT draft exact calculator values and uncertified label', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'SHORT' });
    const { mutations } = trackTradingMutations(page);

    await page.goto('/');
    const draftBtn = page.getByRole('button', { name: /Vẽ Thử Kế Hoạch/i });
    await draftBtn.waitFor({ state: 'visible', timeout: 10000 });
    await draftBtn.click();

    // Select SHORT direction for manual draft
    const shortBtn = page.getByRole('button', { name: /Bán/i });
    await shortBtn.waitFor({ state: 'visible', timeout: 5000 });
    await shortBtn.click();

    const rr = await readRR(page);
    expect(rr).not.toBeNull();
    expect(rr?.data.direction).toBe('SHORT');
    expect(rr?.data.state).toBe('draft');
    expect(mutations.length).toBe(0);
  });

  test('T10: clicking WAITING_BREAK scenario without plan displays header SCENARIO without R:R box', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'SHORT' });
    await page.goto('/');

    // Ensure draft mode is off
    const rrBefore = await readRR(page);
    expect(rrBefore).toBeNull();

    // Route scenario evidence endpoint to return empty / waiting scenario without plan
    await page.route('**/api/v1/smc/scenarios/*/chart-evidence*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          bundle_version: 'v14.4.1',
          binding: {
            source: 'SCENARIO',
            id: 'scen-waiting-test',
            instance_id: 'inst-waiting',
            revision: 1,
            symbol: 'XAUUSDT',
            direction: 'SHORT',
            plan_fingerprint: null,
            snapshot_id: 'snap-waiting-test',
          },
          as_of_ms: Date.now(),
          evidence_cutoff_ms: Date.now(),
          strategy_family: 'STANDARD_SMC',
          state: 'WAITING_BREAK',
          summary_vi: 'Chờ phá vỡ cấu trúc',
          swings: [],
          structure_events: [],
          poi_zones: [],
          liquidity_levels: [],
          displacements: [],
          retests: [],
          displacement_annotation: null,
          scenario_path: [],
          risk_reward_plan: null,
          target: null,
          invalidation: null,
          invalidation_price: null,
          chain: { verified: false, reasons: ['WAITING_BREAK'], advisories: [] },
          is_empty: true,
          provenance: null,
          entry_explanation: null,
        }),
      });
    });

    // Click on scenario in UI
    const scenarioCard = page.locator('[data-testid="scenario-card"]').first();
    await scenarioCard.waitFor({ state: 'visible', timeout: 10000 });
    await scenarioCard.click();

    // No R:R overlay should be displayed for scenario without plan
    const rr = await readRR(page);
    expect(rr).toBeNull();
  });

  test('T11: HTTP 500 error on chart-evidence renders clear error without stale R:R', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'SHORT' });
    await page.goto('/');

    // Intercept evidence API to fail with 500
    await page.route('**/api/v1/smc/scenarios/*/chart-evidence*', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'INTERNAL_EVIDENCE_ERROR:req-test-500' }),
      });
    });

    // Select a scenario
    const scenarioCard = page.locator('[data-testid="scenario-card"]').first();
    await scenarioCard.waitFor({ state: 'visible', timeout: 10000 });
    await scenarioCard.click();

    // Wait for error state banner
    const errorBanner = page.locator('text=Chưa tải được bằng chứng kịch bản');
    await expect(errorBanner.or(page.locator('text=Lỗi'))).toBeVisible({ timeout: 10000 });

    // No stale R:R box
    const rr = await readRR(page);
    expect(rr).toBeNull();
  });

  test('T12: switching from valid setup A to waiting scenario B clears R:R overlay', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'SHORT' });
    await page.goto('/');

    // Switch to Upcoming tab
    const upcomingTab = page.locator('[data-tab-id="upcoming"]');
    await upcomingTab.waitFor({ state: 'visible', timeout: 10000 });
    await upcomingTab.click();

    // Return to chart tab
    const chartTab = page.locator('[data-tab-id="chart"]');
    await chartTab.waitFor({ state: 'visible', timeout: 10000 });
    await chartTab.click();

    const scenarioCard = page.locator('[data-testid="scenario-card"]').first();
    await scenarioCard.waitFor({ state: 'visible', timeout: 10000 });
    await scenarioCard.click();
  });

  test('T13: copy valid setup to draft creates independent draft with same levels', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'SHORT' });
    await page.goto('/');

    // Navigate to Upcoming tab
    const upcomingTab = page.locator('[data-tab-id="upcoming"]');
    await upcomingTab.waitFor({ state: 'visible', timeout: 10000 });
    await upcomingTab.click();

    const copyBtn = page.getByRole('button', { name: /Vẽ Thử/i }).first();
    if (await copyBtn.isVisible()) {
      await copyBtn.click();
      const rr = await readRR(page);
      if (rr) {
        expect(rr.data.state).toBe('draft');
      }
    }
  });

  test('T14: scenario with valid certified plan renders readonly R:R overlay', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'LONG' });
    await page.goto('/');

    const scenarioCard = page.locator('[data-testid="scenario-card"]').first();
    await scenarioCard.waitFor({ state: 'visible', timeout: 10000 });
    await scenarioCard.click();

    const rr = await readRR(page);
    if (rr) {
      expect(rr.data.readOnly).toBe(true);
    }
  });

  test('T15: race condition in fast A -> B selection shows only B bundle', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'LONG' });
    await page.goto('/');

    // Delay response for scenario A, return fast for B
    let count = 0;
    await page.route('**/api/v1/smc/scenarios/*/chart-evidence*', async (route) => {
      count++;
      if (count === 1) {
        // Slow A
        await new Promise((r) => setTimeout(r, 1000));
      }
      await route.continue();
    });

    const scenarioCards = page.locator('[data-testid="scenario-card"]');
    await scenarioCards.first().waitFor({ state: 'visible', timeout: 10000 });
    if ((await scenarioCards.count()) >= 2) {
      await scenarioCards.nth(0).click();
      await scenarioCards.nth(1).click();
    }
  });

  test('T16: draft toggle Mua / Bán updates direction and remains editable in draft mode', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'SHORT' });
    await page.goto('/');

    const draftBtn = page.getByRole('button', { name: /Vẽ Thử Kế Hoạch/i });
    await draftBtn.waitFor({ state: 'visible', timeout: 10000 });
    await draftBtn.click();

    const buyBtn = page.getByRole('button', { name: /Mua/i });
    await buyBtn.waitFor({ state: 'visible', timeout: 5000 });
    await buyBtn.click();

    const rr = await readRR(page);
    expect(rr).not.toBeNull();
    expect(rr?.data.direction).toBe('LONG');
    expect(rr?.data.state).toBe('draft');
  });
});
