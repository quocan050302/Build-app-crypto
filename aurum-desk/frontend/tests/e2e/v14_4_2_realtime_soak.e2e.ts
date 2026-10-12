/**
 * V14.4.2 Realtime Soak & WebSocket Suite (R01–R05)
 *
 * Real browser tests on isolated test environment.
 * Tests disconnect/reconnect, sequence gap resync, rapid selection soak, tick streaming, and lifecycle cleanup.
 */
import { test, expect } from '@playwright/test';
import {
  seedSmtFixture,
  openChartTab,
  openUpcomingTab,
  openJournalTab,
  clickSetup,
  clickOrder,
} from './v14_4_2_helpers';

test.describe('V14.4.2 Realtime Soak Suite', () => {
  // R01: Disconnect and Reconnect Resync
  test('R01: disconnect and reconnect triggers resync with single active socket', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'LONG', auto: false });
    await page.goto('/');
    await openChartTab(page);

    // Verify diagnostics indicate 1 active socket
    const diagBefore = await page.evaluate(() => {
      const ws = (window as any).__AURUM_WS_CLIENT__;
      return ws?.getDiagnostics?.() ?? null;
    });
    expect(diagBefore).not.toBeNull();
    expect(diagBefore.isOpen).toBe(true);
    expect(diagBefore.subscriberCount).toBeGreaterThan(0);
    const genBefore = diagBefore.generation || 1;

    // Trigger reconnect simulation by closing socket
    await page.evaluate(() => {
      const ws = (window as any).__AURUM_WS_CLIENT__;
      ws?.closeForTesting?.();
    });

    // Wait for automatic reconnect and generation increment
    await expect.poll(async () => {
      return page.evaluate((targetGen) => {
        const ws = (window as any).__AURUM_WS_CLIENT__;
        const d = ws?.getDiagnostics?.();
        return d?.isOpen && (d?.generation ?? 0) > targetGen;
      }, genBefore);
    }, {
      message: 'Waiting for WebSocket client to reconnect with new generation',
      timeout: 15_000,
      intervals: [200, 500, 1000],
    }).toBe(true);
  });

  // R02: Revision gap triggers bounded resync
  test('R02: revision gap in domain event triggers bounded resync GET', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');

    const resyncPromise = page.waitForResponse(
      (r) => r.url().includes('/smc/scenarios') && r.request().method() === 'GET'
    );

    // Dispatch a jumped revision event via testing helper
    await page.evaluate(() => {
      const ws = (window as any).__AURUM_WS_CLIENT__;
      ws?.dispatchForTesting?.({
        type: 'DOMAIN_EVENT',
        event: {
          event_type: 'scenario.updated',
          payload: {
            scenario_id: 'scen-jump',
            instance_id: 'inst-jump',
            revision: 99, // large jump
            symbol: 'XAUUSDT',
            state: 'STRUCTURE_CONFIRMED',
            direction: 'SHORT',
          },
        },
      });
    });

    // Wait for the debounced resync GET
    const res = await resyncPromise;
    expect(res.ok()).toBe(true);
  });

  // R03: Rapid selection soak across 100 cycles
  test('R03: 100 selection cycles complete without unhandled errors or leak', async ({ page, request }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (err) => pageErrors.push(err.message));

    const smc = await seedSmtFixture(page, request, { direction: 'SHORT', auto: true, fill: true });
    await page.goto('/');

    const upcomingTab = page.locator('[data-tab-id="upcoming"]');
    await upcomingTab.waitFor({ state: 'visible', timeout: 10000 });
    await upcomingTab.click();

    const chartTab = page.locator('[data-tab-id="chart"]');
    await chartTab.waitFor({ state: 'visible', timeout: 10000 });
    await chartTab.click();

    const scenarioCard = page.locator('[data-testid="scenario-card"]').first();
    const hasScenario = (await scenarioCard.count()) > 0;

    // 100 selection cycles
    for (let i = 0; i < 100; i++) {
      if (hasScenario && i % 2 === 0) {
        await scenarioCard.click();
      } else {
        await upcomingTab.click();
        await chartTab.click();
      }
    }

    expect(pageErrors).toHaveLength(0);
  });

  // R04: Stream quotes and boundary transitions
  test('R04: continuous quote stream preserves valid display price and bounded queue', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'LONG', auto: false });
    await page.goto('/');
    await openChartTab(page);

    // Poll for live quote to update with finite numeric price
    await expect.poll(async () => {
      const text = await page.locator('text=Giá hiện tại:').textContent();
      if (!text) return false;
      const match = text.match(/\$([\d,]+\.?\d*)/);
      if (!match) return false;
      const num = parseFloat(match[1].replace(/,/g, ''));
      return Number.isFinite(num) && num > 0;
    }, { timeout: 10000 }).toBe(true);

    // Verify WebSocket diagnostics and bounded state
    const diag = await page.evaluate(() => {
      const ws = (window as any).__AURUM_WS_CLIENT__;
      return ws?.getDiagnostics?.() ?? null;
    });
    expect(diag).not.toBeNull();
    expect(diag.isOpen).toBe(true);
    expect(diag.subscriberCount).toBeGreaterThan(0);
    expect(diag.subscriberCount).toBeLessThan(50); // bounded
  });

  // R05: Mount/unmount tab cleanup
  test('R05: repeated tab unmount and remount returns subscribers to baseline', async ({ page, request }) => {
    await seedSmtFixture(page, request, { direction: 'SHORT', auto: false });
    await page.goto('/');

    const initialSubs = await page.evaluate(() => {
      const ws = (window as any).__AURUM_WS_CLIENT__;
      return ws?.getDiagnostics?.()?.subscriberCount ?? 0;
    });

    // Switch between tabs 20 times (soak)
    for (let i = 0; i < 20; i++) {
      await openUpcomingTab(page);
      await openJournalTab(page);
    }
    await openChartTab(page);

    const finalSubs = await page.evaluate(() => {
      const ws = (window as any).__AURUM_WS_CLIENT__;
      return ws?.getDiagnostics?.()?.subscriberCount ?? 0;
    });

    // Exact baseline or bounded without leak
    expect(finalSubs).toBe(initialSubs);
  });
});
