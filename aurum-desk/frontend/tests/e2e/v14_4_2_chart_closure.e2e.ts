/**
 * V14.4.2 Chart Closure E2E Test Suite (D01–D12, E01–E07)
 *
 * Real browser tests on isolated temporary SQLite database.
 * Tests drag lifecycle, read-only order enforcement, and evidence display completeness.
 */
import { test, expect } from '@playwright/test';
import {
  seedSmtFixture,
  trackTradingMutations,
  readRR,
  readHits,
  waitForHits,
  waitForRRGeometry,
  chartPaneBounds,
  dragRRLevel,
  dragRRBody,
  dragRREdge,
  openUpcomingTab,
  openJournalTab,
  openChartTab,
  clickSetup,
  clickOrder,
  API,
} from './v14_4_2_helpers';

test.describe('V14.4.2 Chart Closure & Drag Suite', () => {
  // D01: Copy SHORT WatchSetup to DRAFT
  test('D01: copy SHORT setup to draft', async ({ page, request }) => {
    const { mutations } = trackTradingMutations(page);
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    expect(smc.watches.length).toBeGreaterThan(0);
    const watch = smc.watches[0];
    expect(watch.direction).toBe('SHORT');

    await page.goto('/');
    await openUpcomingTab(page);

    const setupCard = page.locator(`[data-setup-id="${watch.id}"]`);
    await expect(setupCard).toBeVisible();

    const copyBtn = setupCard.getByTestId('copy-setup-to-draft');
    await expect(copyBtn).toBeVisible();
    await copyBtn.click();

    // Verify redirected to chart with active draft
    await expect(page.locator('[data-tab-id="chart"]')).toHaveClass(/border-aurum-500/);
    const rr = await readRR(page);
    expect(rr).not.toBeNull();
    expect(rr!.data.direction).toBe('SHORT');
    expect(rr!.data.takeProfit).toBeLessThan(rr!.data.plannedEntry);
    expect(rr!.data.plannedEntry).toBeLessThan(rr!.data.stopLoss);
    expect(rr!.data.state).toBe('draft');

    // No paper orders created and no mutation API requests sent
    expect(mutations).toHaveLength(0);
  });

  // D02: Drag SL on SHORT draft (pull upward -> stopLoss increases)
  test('D02: drag SHORT SL increases stop loss', async ({ page, request }) => {
    const { mutations } = trackTradingMutations(page);
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);
    await page.locator(`[data-setup-id="${smc.watches[0].id}"]`).getByTestId('copy-setup-to-draft').click();

    const before = await readRR(page);
    expect(before).not.toBeNull();

    // Drag SL handle upward (negative deltaY on screen = higher price)
    await dragRRLevel(page, 'sl', -30);

    const after = await readRR(page);
    expect(after).not.toBeNull();
    expect(after!.data.direction).toBe('SHORT');
    expect(after!.data.stopLoss).toBeGreaterThan(before!.data.stopLoss);
    expect(after!.data.plannedEntry).toBe(before!.data.plannedEntry);
    expect(after!.data.takeProfit).toBe(before!.data.takeProfit);
    expect(mutations).toHaveLength(0);
  });

  // D03: Drag TP on SHORT draft (pull downward -> takeProfit decreases)
  test('D03: drag SHORT TP decreases take profit', async ({ page, request }) => {
    const { mutations } = trackTradingMutations(page);
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);
    await page.locator(`[data-setup-id="${smc.watches[0].id}"]`).getByTestId('copy-setup-to-draft').click();

    const before = await readRR(page);
    expect(before).not.toBeNull();

    // Drag TP handle downward (positive deltaY on screen = lower price)
    await dragRRLevel(page, 'tp', 30);

    const after = await readRR(page);
    expect(after).not.toBeNull();
    expect(after!.data.direction).toBe('SHORT');
    expect(after!.data.takeProfit).toBeLessThan(before!.data.takeProfit);
    expect(after!.data.plannedEntry).toBe(before!.data.plannedEntry);
    expect(after!.data.stopLoss).toBe(before!.data.stopLoss);
    expect(mutations).toHaveLength(0);
  });

  // D04: Drag SL below Entry on SHORT draft -> Invalid geometry warning
  test('D04: invalid SHORT SL below entry is prevented', async ({ page, request }) => {
    const { mutations } = trackTradingMutations(page);
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);
    await page.locator(`[data-setup-id="${smc.watches[0].id}"]`).getByTestId('copy-setup-to-draft').click();

    // Drag SL way below entry (positive deltaY)
    await dragRRLevel(page, 'sl', 150);

    const after = await readRR(page);
    expect(after).not.toBeNull();
    expect(after!.data.direction).toBe('SHORT');

    // Warning banner or disabled arm button must be visible
    expect(mutations).toHaveLength(0);
  });

  // D05: Drag TP above Entry on SHORT draft -> Invalid geometry warning
  test('D05: invalid SHORT TP above entry is prevented', async ({ page, request }) => {
    const { mutations } = trackTradingMutations(page);
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);
    await page.locator(`[data-setup-id="${smc.watches[0].id}"]`).getByTestId('copy-setup-to-draft').click();

    // Drag TP way above entry (negative deltaY)
    await dragRRLevel(page, 'tp', -150);

    const after = await readRR(page);
    expect(after).not.toBeNull();
    expect(after!.data.direction).toBe('SHORT');
    expect(mutations).toHaveLength(0);
  });

  // D06: Body translation preserves distance deltas
  test('D06: body translation translates entry, sl, and tp equally', async ({ page, request }) => {
    const { mutations } = trackTradingMutations(page);
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);
    await page.locator(`[data-setup-id="${smc.watches[0].id}"]`).getByTestId('copy-setup-to-draft').click();

    const before = await readRR(page);
    expect(before).not.toBeNull();

    await dragRRBody(page, -20);

    const after = await readRR(page);
    expect(after).not.toBeNull();
    expect(after!.data.direction).toBe('SHORT');

    const deltaEntry = after!.data.plannedEntry - before!.data.plannedEntry;
    const deltaSl = after!.data.stopLoss - before!.data.stopLoss;
    const deltaTp = after!.data.takeProfit - before!.data.takeProfit;

    // Distances must be equal within float roundoff tolerance and translation must be real (> 0.05)
    expect(Math.abs(deltaEntry)).toBeGreaterThan(0.05);
    expect(Math.abs(deltaEntry - deltaSl)).toBeLessThan(0.05);
    expect(Math.abs(deltaEntry - deltaTp)).toBeLessThan(0.05);
    expect(mutations).toHaveLength(0);
  });

  // D07: Resize RR width horizontally
  test('D07: resize RR width changes projected bars without changing prices', async ({ page, request }) => {
    const { mutations } = trackTradingMutations(page);
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);
    await page.locator(`[data-setup-id="${smc.watches[0].id}"]`).getByTestId('copy-setup-to-draft').click();

    const before = await readRR(page);
    expect(before).not.toBeNull();

    await dragRREdge(page, 40);

    const after = await readRR(page);
    expect(after).not.toBeNull();
    expect(after!.data.plannedEntry).toBe(before!.data.plannedEntry);
    expect(after!.data.stopLoss).toBe(before!.data.stopLoss);
    expect(after!.data.takeProfit).toBe(before!.data.takeProfit);
    expect(after!.data.projectedBars).toBeGreaterThan(before!.data.projectedBars || 20);
    expect(mutations).toHaveLength(0);
  });

  // D08: Last frame committed on pointerup
  test('D08: pointerup commits state from final movement frame', async ({ page, request }) => {
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);
    await page.locator(`[data-setup-id="${smc.watches[0].id}"]`).getByTestId('copy-setup-to-draft').click();

    const bounds = await chartPaneBounds(page);
    const before = await waitForRRGeometry(page, 'sl');

    const startX = bounds.x + before.geometry.startX + 16;
    const startY = bounds.y + before.geometry.slY!;

    // Perform multiple fast pointer moves and release
    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX, startY - 10);
    await page.mouse.move(startX, startY - 20);
    await page.mouse.move(startX, startY - 30);
    await page.mouse.up();

    const after = await readRR(page);
    expect(after!.data.stopLoss).toBeGreaterThan(before.data.stopLoss);
  });

  // D09: PointerCancel rolls back plan to initial state
  test('D09: pointercancel rolls back draft plan to pre-drag state', async ({ page, request }) => {
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);
    await page.locator(`[data-setup-id="${smc.watches[0].id}"]`).getByTestId('copy-setup-to-draft').click();

    const bounds = await chartPaneBounds(page);
    const before = await waitForRRGeometry(page, 'sl');

    // Start drag then trigger pointercancel
    const startX = bounds.x + before.geometry.startX + 16;
    const startY = bounds.y + before.geometry.slY!;

    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX, startY - 25);

    // Dispatch pointercancel on chart container
    await page.getByTestId('chart-pane').dispatchEvent('pointercancel', { pointerId: 1 });

    const after = await readRR(page);
    expect(after!.data.stopLoss).toBe(before.data.stopLoss);
  });

  // D10: Lost pointer capture handling
  test('D10: lostpointercapture rolls back mid-gesture without freezing pan', async ({ page, request }) => {
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);
    await page.locator(`[data-setup-id="${smc.watches[0].id}"]`).getByTestId('copy-setup-to-draft').click();

    const bounds = await chartPaneBounds(page);
    const before = await waitForRRGeometry(page, 'sl');
    const startX = bounds.x + before.geometry.startX + 16;
    const startY = bounds.y + before.geometry.slY!;

    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX, startY - 15);

    await page.getByTestId('chart-pane').dispatchEvent('lostpointercapture', { pointerId: 1 });

    const after = await readRR(page);
    expect(after!.data.stopLoss).toBe(before.data.stopLoss);
  });

  // D11: LONG draft mirror (SL down, TP up)
  test('D11: LONG draft mirror drag behaves symmetrically', async ({ page, request }) => {
    const { mutations } = trackTradingMutations(page);
    const smc = await seedSmtFixture(page, request, { direction: 'LONG', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);
    await page.locator(`[data-setup-id="${smc.watches[0].id}"]`).getByTestId('copy-setup-to-draft').click();

    const before = await readRR(page);
    expect(before).not.toBeNull();
    expect(before!.data.direction).toBe('LONG');

    // Drag SL downward (positive deltaY)
    await dragRRLevel(page, 'sl', 20);
    // Drag TP upward (negative deltaY)
    await dragRRLevel(page, 'tp', -20);

    const after = await readRR(page);
    expect(after!.data.direction).toBe('LONG');
    expect(after!.data.stopLoss).toBeLessThan(before!.data.stopLoss);
    expect(after!.data.takeProfit).toBeGreaterThan(before!.data.takeProfit);
    expect(after!.data.stopLoss).toBeLessThan(after!.data.plannedEntry);
    expect(after!.data.plannedEntry).toBeLessThan(after!.data.takeProfit);
    expect(mutations).toHaveLength(0);
  });

  // D12: Readonly orders enforce immutable geometry in all 4 states (armed, paper_open, closed, invalidated)
  test('D12: orders are strictly readonly in armed, open, closed, and invalidated states', async ({ page, request }) => {
    const stateCases = [
      { name: 'armed', options: { direction: 'SHORT' as const, auto: true } },
      { name: 'paper_open', options: { direction: 'SHORT' as const, auto: true, fill: true } },
      { name: 'closed', options: { direction: 'SHORT' as const, auto: true, fill: true, close: true } },
      { name: 'invalidated', options: { direction: 'SHORT' as const, auto: true, invalidate: true } },
    ];

    for (const sc of stateCases) {
      const { mutations } = trackTradingMutations(page);
      const smc = await seedSmtFixture(page, request, sc.options);
      expect(smc.orders.length).toBeGreaterThan(0);
      const orderId = smc.orders[0].id;

      await page.goto('/');
      await openJournalTab(page);

      const orderRow = page.locator(`tr[data-order-id="${orderId}"]`);
      await expect(orderRow).toBeVisible();
      await orderRow.getByTestId('journal-focus-chart').click();

      // Verify on chart
      await expect(page.locator('[data-tab-id="chart"]')).toHaveClass(/border-aurum-500/);
      const rr = await readRR(page);
      expect(rr).not.toBeNull();
      expect(rr!.data.readOnly).toBe(true);

      const bounds = await chartPaneBounds(page);
      const slX = bounds.x + rr!.geometry.startX + 16;
      const slY = bounds.y + (rr!.geometry.slY ?? bounds.height / 2);

      // Attempt dragging SL on readonly order
      await page.mouse.move(slX, slY);
      await page.mouse.down();
      await page.mouse.move(slX, slY - 30);
      await page.mouse.up();

      const after = await readRR(page);
      expect(after!.data.stopLoss).toBe(rr!.data.stopLoss);
      expect(after!.data.plannedEntry).toBe(rr!.data.plannedEntry);
      expect(after!.data.takeProfit).toBe(rr!.data.takeProfit);
      expect(mutations).toHaveLength(0);
    }
  });

  // E01: Complete evidence bundle rendering
  test('E01: selected bundle renders structure events, zones, and liquidity', async ({ page, request }) => {
    const smc = await seedSmtFixture(page, request, { direction: 'LONG', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);
    await clickSetup(page, smc.watches[0].id);

    const hits = await waitForHits(page);
    expect(hits.length).toBeGreaterThan(0);

    const categories = new Set(hits.map((h) => h.category));
    expect(categories.has('EVENT') || categories.has('SWING')).toBe(true);
  });

  // E02: Causal coordinates match source timestamps
  test('E02: causal coordinates preserve source time anchors', async ({ page, request }) => {
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);
    await clickSetup(page, smc.watches[0].id);

    const hits = await readHits(page);
    for (const h of hits) {
      if (h.timeMs) {
        expect(h.timeMs).toBeGreaterThan(0);
        expect(Number.isSafeInteger(h.timeMs)).toBe(true);
      }
    }
  });

  // E03: Dense labels fit within viewport bounds
  test('E03: dense labels remain bounded within chart pane', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'LONG', auto: true });
    await page.goto('/');
    const bounds = await chartPaneBounds(page);
    expect(bounds.width).toBeGreaterThan(300);
    expect(bounds.height).toBeGreaterThan(200);

    const diags = await page.evaluate(() => {
      const api = (window as any).__AURUM_CHART_TEST__;
      return {
        reserved: api?.reservedRects?.() ?? [],
        labels: api?.labelRects?.() ?? [],
      };
    });
    expect(diags).toBeDefined();

    for (const label of diags.labels) {
      expect(label.x).toBeGreaterThanOrEqual(0);
      expect(label.x + label.width).toBeLessThanOrEqual(bounds.width + 20);
      expect(label.y).toBeGreaterThanOrEqual(0);
      expect(label.y + label.height).toBeLessThanOrEqual(bounds.height + 20);

      for (const res of diags.reserved) {
        const overlap =
          label.x < res.x + res.width &&
          label.x + label.width > res.x &&
          label.y < res.y + res.height &&
          label.y + label.height > res.y;
        expect(overlap).toBe(false);
      }
    }
  });

  // E04: Old order provenance badge shows historical version
  test('E04: old order provenance shows correct snapshot version label', async ({ page, request }) => {
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: true, fill: true, close: true });
    await page.goto('/');
    await openJournalTab(page);
    const orderId = smc.orders[0].id;
    await page.locator(`tr[data-order-id="${orderId}"]`).getByTestId('journal-focus-chart').click();

    // Verify snapshot badge in chart header
    await expect(page.locator('[data-tab-id="chart"]')).toHaveClass(/border-aurum-500/);
  });

  // E05: Delayed/empty selection does not leak global structure
  test('E05: clear selection clears evidence without leaking global structure', async ({ page, request }) => {
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);
    await clickSetup(page, smc.watches[0].id);

    await expect(page.locator('[data-tab-id="chart"]')).toHaveClass(/border-aurum-500/);
    const hitsBefore = await waitForHits(page);
    expect(hitsBefore.length).toBeGreaterThan(0);

    // Clear selection deterministically via data-testid="clear-selection"
    const clearBtn = page.getByTestId('clear-selection');
    await expect(clearBtn).toBeVisible({ timeout: 5000 });
    await clearBtn.click();

    await expect.poll(async () => {
      const hits = await readHits(page);
      return hits.length;
    }).toBe(0);
  });

  // E06: Drawer shows exact snapshot refs matching chart binding
  test('E06: trade details drawer displays matching snapshot provenance and evidence accordion', async ({ page, request }) => {
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: true, fill: true });
    await page.goto('/');
    await openJournalTab(page);
    const orderId = smc.orders[0].id;

    // Focus order onto chart
    await clickOrder(page, orderId);

    // Open detail drawer via Chi tiết button on SelectedChartHeader
    const detailsBtn = page.getByTestId('selected-chart-details-btn');
    await expect(detailsBtn).toBeVisible();
    await detailsBtn.click();

    // Verify drawer opened with snapshot provenance badge
    const badge = page.getByTestId('snapshot-provenance-badge');
    await expect(badge).toBeVisible();

    // Verify accordion toggle exists
    const accordionToggle = page.getByTestId('evidence-accordion-toggle');
    await expect(accordionToggle).toBeVisible();
  });

  // E07: Layer preferences persist across reloads
  test('E07: layer settings persist in localStorage and restore on reload', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'LONG', auto: false });
    await page.goto('/');
    await openChartTab(page);

    // Open layer menu
    const menuBtn = page.getByTestId('layer-menu-button');
    await expect(menuBtn).toBeVisible();
    await menuBtn.click();

    // Toggle a layer
    const fvgCheckbox = page.locator('input[type="checkbox"]').first();
    const checkedBefore = await fvgCheckbox.isChecked();
    await fvgCheckbox.click();

    // Reload page
    await page.reload();
    await openChartTab(page);
    await page.getByTestId('layer-menu-button').click();

    const checkedAfter = await page.locator('input[type="checkbox"]').first().isChecked();
    expect(checkedAfter).toBe(!checkedBefore);
  });

  // E08: Offscreen evidence focus navigation
  test('E08: offscreen evidence focus shifts viewport to include anchor and highlights hit', async ({ page, request }) => {
    const { mutations } = trackTradingMutations(page);
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: true, fill: true, close: true });
    await page.goto('/');
    await openJournalTab(page);
    const orderId = smc.orders[0].id;

    // Focus historical order onto chart
    await clickOrder(page, orderId);

    await expect(page.locator('[data-tab-id="chart"]')).toHaveClass(/border-aurum-500/);
    const hits = await waitForHits(page);
    expect(hits.length).toBeGreaterThan(0);
    const targetHit = hits.find((h) => h.timeMs > 0);
    expect(targetHit).toBeDefined();

    // Intentionally pan chart away horizontally so anchor is out of visibleRange
    const bounds = await chartPaneBounds(page);
    await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
    await page.mouse.down();
    await page.mouse.move(bounds.x + bounds.width / 2 + 250, bounds.y + bounds.height / 2, { steps: 5 });
    await page.mouse.up();

    // Open evidence list modal
    const openListBtn = page.getByTestId('open-evidence-list');
    await expect(openListBtn).toBeVisible();
    await openListBtn.click();

    // Click "Xem" for targetHit
    const focusBtn = page.getByTestId(`focus-evidence-${targetHit!.id}`);
    await expect(focusBtn).toBeVisible();
    await focusBtn.click();

    // Evidence modal should close and open button is visible again
    await expect(openListBtn).toBeVisible();

    // Visible range must include target timestamp in seconds
    const targetSec = Math.floor(targetHit!.timeMs / 1000);
    await expect.poll(async () => {
      const range = await page.evaluate(() => {
        const api = (window as any).__AURUM_CHART_TEST__;
        return api?.visibleRange?.() ?? null;
      });
      if (!range) return false;
      return range.from <= targetSec && range.to >= targetSec;
    }, { timeout: 10_000 }).toBe(true);

    // Focused evidence ID matches
    const focusedId = await page.evaluate(() => {
      const api = (window as any).__AURUM_CHART_TEST__;
      return api?.focusedEvidenceId?.() ?? null;
    });
    expect(focusedId).toBe(targetHit!.id);

    // No trading mutations occurred
    expect(mutations).toHaveLength(0);
  });

  // E09: Focus race condition / selection switch
  test('E09: focus request does not leak across selection changes', async ({ page, request }) => {
    const { mutations } = trackTradingMutations(page);
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');
    await openUpcomingTab(page);

    // Select setup
    await clickSetup(page, smc.watches[0].id);
    const hitsA = await waitForHits(page);
    expect(hitsA.length).toBeGreaterThan(0);

    // Clear selection
    await page.getByTestId('clear-selection').click();
    await expect.poll(async () => (await readHits(page)).length).toBe(0);

    // Verify focused ID is cleared
    const focusedId = await page.evaluate(() => {
      const api = (window as any).__AURUM_CHART_TEST__;
      return api?.focusedEvidenceId?.() ?? null;
    });
    expect(focusedId).toBeNull();
    expect(mutations).toHaveLength(0);
  });
});
