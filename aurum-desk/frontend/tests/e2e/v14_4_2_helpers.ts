import { expect, type APIRequestContext, type Page } from '@playwright/test';

export const API_PORT = Number(process.env.AURUM_E2E_API_PORT ?? 8765);
export const API = `http://127.0.0.1:${API_PORT}`;

export const MUTATING_REGEX =
  /\/api\/v1\/(orders\/paper|setups\/arm|orders\/close|setups\/cancel|policy\/trading|orders\/[^/]+\/(close|cancel|levels))/;

export interface SmcState {
  scenarios: {
    scenario_id: string;
    snapshot_id: string;
    state: string;
    direction: string | null;
    revision: number;
  }[];
  orders: {
    id: string;
    state: string;
    direction: string;
    planned_entry: number;
    stop_loss: number;
    take_profit: number;
    opened_at: number | null;
    scenario_snapshot_id: string | null;
    quality_snapshot_id: string | null;
  }[];
  watches: {
    id: string;
    state: string;
    direction: string;
    revision: number;
    instance_id: string;
    scenario_snapshot_id: string;
  }[];
}

export interface HitItem {
  id: string;
  category: string;
  title: string;
  price: number;
  timeMs: number;
  x: number;
  y: number;
}

export interface RRState {
  data: {
    id: string;
    direction: 'LONG' | 'SHORT';
    plannedEntry: number;
    stopLoss: number;
    takeProfit: number;
    quantity?: number;
    projectedBars?: number;
    readOnly?: boolean;
    state?: string;
  };
  geometry: {
    startX: number;
    endX: number;
    slY: number | null;
    tpY: number | null;
    entryY: number | null;
  };
}

export async function seedSmtFixture(
  page: Page,
  request: APIRequestContext,
  body: {
    direction?: 'LONG' | 'SHORT';
    auto?: boolean;
    fill?: boolean;
    close?: boolean;
    invalidate?: boolean;
    after_days?: number;
    now_ms?: number;
  } = {}
): Promise<SmcState> {
  // Park page to prevent background polling race during DB reset
  await page.goto('about:blank');
  const res = await request.post(`${API}/__e2e/smc/reset`, { data: body });
  expect(res.ok(), await res.text()).toBeTruthy();
  return res.json();
}

export function trackTradingMutations(page: Page) {
  const mutations: string[] = [];
  const previewCalls: string[] = [];
  const pageErrors: string[] = [];

  page.on('request', (req) => {
    const url = req.url();
    if (req.method() !== 'GET' && MUTATING_REGEX.test(url)) {
      mutations.push(`${req.method()} ${new URL(url).pathname}`);
    }
    if (url.includes('/quality/preview')) {
      previewCalls.push(`${req.method()} ${new URL(url).pathname}`);
    }
  });

  page.on('pageerror', (err) => {
    pageErrors.push(err.message);
  });

  return { mutations, previewCalls, pageErrors };
}

export async function readRR(page: Page): Promise<RRState | null> {
  return page.evaluate(() => {
    const testApi = (window as any).__AURUM_CHART_TEST__;
    return testApi?.rr?.() ?? null;
  });
}

export async function readHits(page: Page): Promise<HitItem[]> {
  return page.evaluate(() => {
    const testApi = (window as any).__AURUM_CHART_TEST__;
    return testApi?.smcHits?.() ?? [];
  });
}

export async function waitForHits(page: Page): Promise<HitItem[]> {
  await expect.poll(async () => {
    const hits = await readHits(page);
    return hits.length > 0;
  }, {
    message: 'Waiting for SMC chart hits to be populated',
    timeout: 10_000,
    intervals: [100, 250, 500],
  }).toBeTruthy();
  return readHits(page);
}

export async function chartPaneBounds(page: Page) {
  const pane = page.getByTestId('chart-pane');
  await expect(pane).toBeVisible({ timeout: 10_000 });
  const bounds = await pane.boundingBox();
  expect(bounds).not.toBeNull();
  return bounds!;
}

export async function waitForRRGeometry(page: Page, level?: 'sl' | 'tp' | 'entry'): Promise<RRState> {
  await expect.poll(async () => {
    const rr = await readRR(page);
    if (!rr) return false;
    if (level === 'sl') return rr.geometry.slY !== null && rr.geometry.startX !== null;
    if (level === 'tp') return rr.geometry.tpY !== null && rr.geometry.startX !== null;
    if (level === 'entry') return rr.geometry.entryY !== null && rr.geometry.startX !== null;
    return rr.geometry.slY !== null && rr.geometry.tpY !== null;
  }, {
    message: `Waiting for RR geometry ${level ?? 'sl/tp'} to be non-null`,
    timeout: 10_000,
    intervals: [100, 250, 500],
  }).toBeTruthy();
  return (await readRR(page))!;
}

export async function dragRRLevel(page: Page, level: 'sl' | 'tp', deltaY: number) {
  const before = await waitForRRGeometry(page, level);
  const bounds = await chartPaneBounds(page);

  const localY = level === 'sl' ? before.geometry.slY : before.geometry.tpY;
  expect(localY, `Geometry ${level}Y must be non-null`).not.toBeNull();

  // Handles are located at startX + 16 inside the chart pane
  const x = bounds.x + before.geometry.startX + 16;
  const y = bounds.y + localY!;

  expect(x).toBeGreaterThanOrEqual(bounds.x);
  expect(x).toBeLessThanOrEqual(bounds.x + bounds.width);
  expect(y).toBeGreaterThanOrEqual(bounds.y);
  expect(y).toBeLessThanOrEqual(bounds.y + bounds.height);

  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + deltaY, { steps: 6 });
  await page.mouse.up();

  return before;
}

export async function dragRRBody(page: Page, deltaY: number) {
  const before = await waitForRRGeometry(page);
  const bounds = await chartPaneBounds(page);

  // Pick center between entry and SL
  const entryY = before.geometry.entryY ?? bounds.height / 2;
  const slY = before.geometry.slY ?? bounds.height / 2;
  const centerY = (entryY + slY) / 2;

  const x = bounds.x + before.geometry.startX + 40;
  const y = bounds.y + centerY;

  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + deltaY, { steps: 6 });
  await page.mouse.up();

  return before;
}

export async function dragRREdge(page: Page, deltaX: number) {
  const before = await waitForRRGeometry(page);
  const bounds = await chartPaneBounds(page);

  const x = bounds.x + before.geometry.endX;
  const y = bounds.y + (before.geometry.entryY ?? bounds.height / 2);

  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + deltaX, y, { steps: 6 });
  await page.mouse.up();

  return before;
}

export async function openChartTab(page: Page) {
  await page.locator('[data-tab-id="chart"]').click();
}

export async function openUpcomingTab(page: Page) {
  await page.locator('[data-tab-id="upcoming"]').click();
}

export async function openJournalTab(page: Page) {
  await page.locator('[data-tab-id="journal"]').click();
}

export async function clickScenario(page: Page, scenarioId: string) {
  await openChartTab(page);
  const card = page.locator(`[data-testid="scenario-card"][data-scenario-id="${scenarioId}"]`);
  await expect(card).toBeVisible();
  const evidence = page.waitForResponse((r) =>
    decodeURIComponent(r.url()).includes(`/smc/scenarios/${scenarioId}/chart-evidence`)
  );
  await card.click();
  return (await evidence).json();
}

export async function clickSetup(page: Page, setupId: string) {
  await openUpcomingTab(page);
  const row = page.locator(`[data-setup-id="${setupId}"]`);
  await expect(row).toBeVisible();
  const evidence = page.waitForResponse((r) => r.url().includes(`/setups/${setupId}/chart-evidence`));
  await row.getByRole('button', { name: /Xem Tr.n Chart|Xem trên Chart/i }).click();
  return (await evidence).json();
}

export async function clickOrder(page: Page, orderId: string) {
  await openJournalTab(page);
  const row = page.locator(`tr[data-order-id="${orderId}"]`);
  await expect(row).toBeVisible();
  const evidence = page.waitForResponse((r) => r.url().includes(`/orders/${orderId}/chart-evidence`));
  const button = row.getByTestId('journal-focus-chart');
  await expect(button).toBeVisible();
  await button.click();
  return (await evidence).json();
}
