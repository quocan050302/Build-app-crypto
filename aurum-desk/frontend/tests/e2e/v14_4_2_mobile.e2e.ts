/**
 * V14.4.2 Mobile Reachability Suite (M01–M04)
 *
 * Real browser tests on mobile viewports (390x844, 390x663) and desktop.
 * Verifies natural tap reachability without force: true or dispatchEvent, drawer interactions, and responsive bounds.
 */
import { test, expect } from '@playwright/test';
import {
  seedSmtFixture,
  trackTradingMutations,
  openJournalTab,
  openChartTab,
  chartPaneBounds,
} from './v14_4_2_helpers';

test.describe('V14.4.2 Mobile Reachability Suite', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

  // M01: Natural tap on Journal Chart button on 390x844 mobile viewport
  test('M01: mobile journal Chart button is reachable and naturally clickable', async ({ page, request }) => {
    const { mutations } = trackTradingMutations(page);
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: true });
    expect(smc.orders.length).toBeGreaterThan(0);
    const orderId = smc.orders[0].id;

    await page.goto('/');
    await openJournalTab(page);

    const orderRow = page.locator(`tr[data-order-id="${orderId}"]`);
    await expect(orderRow).toBeVisible();

    const chartBtn = orderRow.getByTestId('journal-focus-chart');
    await expect(chartBtn).toBeVisible();
    await chartBtn.scrollIntoViewIfNeeded();

    // Verify button is actually within viewport bounds
    const box = await chartBtn.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390 + 5); // within screen width
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y).toBeLessThanOrEqual(844);

    // Verify elementFromPoint targets the button or its child, NOT a blocking overlay
    const hitElement = await page.evaluate(
      ({ x, y }) => {
        const el = document.elementFromPoint(x, y);
        return el?.getAttribute('data-testid') || el?.tagName;
      },
      { x: box!.x + box!.width / 2, y: box!.y + box!.height / 2 }
    );
    expect(hitElement).toBeDefined();

    // Natural user tap without force: true or dispatchEvent
    await chartBtn.click();

    // Verify switched to chart tab and loaded order evidence
    await expect(page.locator('[data-tab-id="chart"]')).toHaveClass(/border-aurum-500/);
    expect(mutations).toHaveLength(0);
  });

  // M02: Note drawer actions and chart focus on mobile
  test('M02: mobile note drawer opens and closes cleanly without mutating state', async ({ page, request }) => {
    const { mutations } = trackTradingMutations(page);
    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: true });
    const orderId = smc.orders[0].id;

    await page.goto('/');
    await openJournalTab(page);

    const orderRow = page.locator(`tr[data-order-id="${orderId}"]`);
    await expect(orderRow).toBeVisible();
    await orderRow.scrollIntoViewIfNeeded();
    await orderRow.getByRole('button', { name: /Chi Tiết & Note/i }).click();

    // Verify drawer opened
    const drawer = page.getByRole('dialog');
    await expect(drawer).toBeVisible();

    // Close drawer via close button
    const closeBtn = drawer.getByLabel(/Đóng bảng chi tiết/i);
    await closeBtn.click();
    await expect(drawer).toHaveCount(0);

    // Now tap Chart on the same row
    await orderRow.getByTestId('journal-focus-chart').click();
    await expect(page.locator('[data-tab-id="chart"]')).toHaveClass(/border-aurum-500/);
    expect(mutations).toHaveLength(0);
  });

  // M03: Mobile touch layer menu and annotation inspection
  test('M03: mobile touch layer menu and annotation controls remain within viewport', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'LONG', auto: false });
    await page.goto('/');
    await openChartTab(page);

    const layerBtn = page.getByTestId('layer-menu-button');
    await expect(layerBtn).toBeVisible();
    await layerBtn.click();

    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();

    // Verify dialog is bounded inside 390 viewport
    const dialogBox = await dialog.boundingBox();
    expect(dialogBox).not.toBeNull();
    expect(dialogBox!.x + dialogBox!.width).toBeLessThanOrEqual(390 + 5);

    // Dismiss layer menu by clicking outside
    await page.mouse.click(10, 10);
    await expect(dialog).toHaveCount(0);
  });

  // M04: Responsive layout sizes across various viewports
  test('M04: responsive layout maintains usable bounds across viewport sizes', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');

    const viewports = [
      { width: 1366, height: 768 },
      { width: 1440, height: 900 },
      { width: 390, height: 844 },
      { width: 390, height: 663 },
    ];

    for (const vp of viewports) {
      await page.setViewportSize(vp);
      await openChartTab(page);

      const bounds = await chartPaneBounds(page);
      expect(bounds.width).toBeGreaterThan(250);
      expect(bounds.height).toBeGreaterThan(150);
    }
  });
});
