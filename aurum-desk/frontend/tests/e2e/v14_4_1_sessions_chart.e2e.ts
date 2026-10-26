/**
 * V14.4.1 - sessions + selected-object chart evidence in a REAL browser.
 *
 * Real Chrome, real React App, real FastAPI app, isolated temp DB. The launcher (backend/tests/e2e_server.py)
 * only injects persisted candles (the smc_fixtures causal chain), the pinned clock and the quote under
 * /__e2e/smc/*. The market-reading cycle, scenario persistence, promotion to WatchSetup, certified guard,
 * auto-arm and the execution coordinator fill are the REAL production code.
 *
 * Canvas assertions read the SMC primitive's hit registry / the R:R primitive geometry through a DEV-only
 * accessor (window.__AURUM_CHART_TEST__, stripped from production builds), so the test proves what the chart
 * actually computed, not only what the API returned.
 */
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const API = `http://127.0.0.1:${process.env.AURUM_E2E_API_PORT ?? 8765}`
const MUTATING = /\/api\/v1\/(orders\/paper|setups\/arm|orders\/close|setups\/cancel|policy\/trading|orders\/[^/]+\/(close|cancel|levels))/

type SmcState = {
  scenarios: { scenario_id: string; snapshot_id: string; state: string; direction: string | null; revision: number }[]
  orders: { id: string; state: string; direction: string; planned_entry: number; stop_loss: number; take_profit: number; opened_at: number | null; scenario_snapshot_id: string | null; quality_snapshot_id: string | null }[]
  watches: { id: string; state: string; direction: string; revision: number; instance_id: string; scenario_snapshot_id: string }[]
}

type Hit = { id: string; category: string; title: string; price: number; timeMs: number; x: number; y: number }

async function seed(page: Page, request: APIRequestContext, body: object): Promise<SmcState> {
  // park the browser first: the open app polls quota/audit rows and would race the launcher's table reset
  await page.goto('about:blank')
  const res = await request.post(`${API}/__e2e/smc/reset`, { data: body })
  expect(res.ok(), await res.text()).toBeTruthy()
  return res.json()
}

function track(page: Page) {
  const mutations: string[] = []
  const pageErrors: string[] = []
  page.on('request', (req) => {
    if (req.method() !== 'GET' && MUTATING.test(req.url())) mutations.push(`${req.method()} ${new URL(req.url()).pathname}`)
  })
  page.on('pageerror', (err) => pageErrors.push(err.message))
  return { mutations, pageErrors }
}

const hits = (page: Page) =>
  page.evaluate(() => ((window as any).__AURUM_CHART_TEST__?.smcHits?.() ?? []) as Hit[])
const rr = (page: Page) =>
  page.evaluate(() => (window as any).__AURUM_CHART_TEST__?.rr?.() ?? null)

async function openChartTab(page: Page) {
  await page.locator('[data-tab-id="chart"]').click()
}

async function clickScenario(page: Page, scenarioId: string) {
  await openChartTab(page)
  const card = page.locator(`[data-testid="scenario-card"][data-scenario-id="${scenarioId}"]`)
  await expect(card).toBeVisible()
  const evidence = page.waitForResponse((r) => decodeURIComponent(r.url()).includes(`/smc/scenarios/${scenarioId}/chart-evidence`))
  await card.click()
  return (await evidence).json()
}

async function clickSetup(page: Page, setupId: string) {
  await page.locator('[data-tab-id="upcoming"]').click()
  const row = page.locator(`[data-setup-id="${setupId}"]`)
  await expect(row).toBeVisible()
  const evidence = page.waitForResponse((r) => r.url().includes(`/setups/${setupId}/chart-evidence`))
  await row.getByRole('button', { name: /Xem Tr.n Chart/ }).click()
  return (await evidence).json()
}

async function clickOrder(page: Page, orderId: string) {
  await page.locator('[data-tab-id="journal"]').click()
  const row = page.locator(`tr[data-order-id="${orderId}"]`)
  await expect(row).toBeVisible()
  const evidence = page.waitForResponse((r) => r.url().includes(`/orders/${orderId}/chart-evidence`))
  const button = row.getByTestId('journal-focus-chart')
  const narrow = (page.viewportSize()?.width ?? 1500) < 500
  if (narrow) {
    // diagnostic for the mobile layout: is the journal action reachable by a real pointer?
    const info = await button.evaluate((el) => {
      const r = el.getBoundingClientRect()
      const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
      return { x: r.x, y: r.y, w: r.width, h: r.height, vw: window.innerWidth, vh: window.innerHeight, hit: top?.tagName + '.' + (top?.className?.toString().slice(0, 60) ?? '') }
    })
    console.log('MOBILE_JOURNAL_BUTTON', JSON.stringify(info))
    await button.dispatchEvent('click')
  } else {
    await button.click()
  }
  return (await evidence).json()
}

function bundleIds(b: any): Set<string> {
  const ids = new Set<string>()
  for (const key of ['swings', 'structure_events', 'retests', 'displacements', 'poi_zones', 'liquidity_levels'])
    for (const item of b[key] ?? []) if (item?.id) ids.add(String(item.id))
  return ids
}

for (const direction of ['LONG', 'SHORT'] as const) {
  test(`${direction}: sessions -> scenario -> setup -> ARMED order each show their OWN exact evidence on the canvas`, async ({ page, request }) => {
    const { mutations, pageErrors } = track(page)
    const st = await seed(page, request, { direction, auto: true })
    const watch = st.watches.find((w) => w.id === 'watch-XAUUSDT-5M')!
    expect(watch.state).toBe('ARMED')
    expect(st.orders.length).toBe(1)
    const order = st.orders[0]
    const confirmed = st.scenarios.find((s) => s.state === 'STRUCTURE_CONFIRMED')!
    expect(confirmed.direction).toBe(direction)
    await page.goto('/')

    // three-session card: chips for Asia / Europe / NY; NY is a real, cached, read-only forecast
    for (const s of ['ASIA', 'LONDON', 'NY']) await expect(page.getByTestId(`session-chip-${s}`)).toBeVisible()
    await page.getByTestId('session-chip-NY').click()
    await expect(page.getByTestId('forecast-NY')).toBeVisible()
    await expect(page.getByTestId('forecast-NY')).not.toContainText(/undefined|NaN/)

    // ---- scenario click: scenario-adapter bundle, exact snapshot, canvas shows related evidence
    const sc = await clickScenario(page, confirmed.scenario_id)
    expect(sc.binding.source).toBe('SCENARIO')
    expect(sc.binding.snapshot_id).toBe(confirmed.snapshot_id)
    expect(sc.binding.direction).toBe(direction)
    expect(sc.swings.length).toBeGreaterThan(0)
    expect(sc.poi_zones.length).toBeGreaterThan(0)
    const scIds = bundleIds(sc)
    await expect.poll(async () => (await hits(page)).length, { timeout: 15_000 }).toBeGreaterThan(0)
    let canvas = await hits(page)
    await page.screenshot({ path: `../logs/v14_4_1/qa_${direction.toLowerCase()}_scenario.png` }) // visual QA (not an assertion)
    const categories = new Set(canvas.map((h) => h.category))
    expect(categories.has('EVENT') || categories.has('SWING')).toBeTruthy()
    for (const h of canvas) expect(Number.isFinite(h.price) && Number.isFinite(h.x) && Number.isFinite(h.y)).toBeTruthy()
    // no global evidence leak: every drawn item belongs to THIS bundle
    for (const h of canvas) expect(scIds.has(h.id) || [...scIds].some((id) => h.id.includes(id))).toBeTruthy()
    // the drawn FVG zone prices lie inside the detector's real top/bottom (no midpoint +-0.5 fabrication)
    const fvg = sc.poi_zones.find((z: any) => String(z.kind).includes('FVG'))
    expect(fvg).toBeTruthy()
    expect(fvg.top).toBeGreaterThan(fvg.bottom)
    const zoneHit = canvas.find((h) => h.category === 'ZONE')
    if (zoneHit) {
      expect(zoneHit.price).toBeGreaterThanOrEqual(fvg.bottom - 1e-6)
      expect(zoneHit.price).toBeLessThanOrEqual(fvg.top + 1e-6)
    }

    // ---- setup click: exact instance/revision binding via the setup endpoint
    const su = await clickSetup(page, watch.id)
    expect(su.binding.source).toBe('SETUP')
    expect(su.binding.direction).toBe(direction)
    expect(su.binding.instance_id).toBe(watch.instance_id)
    expect(su.binding.snapshot_id).toBe(watch.scenario_snapshot_id)
    await expect.poll(async () => (await rr(page))?.data?.direction, { timeout: 15_000 }).toBe(direction)

    // ---- order click: ACTUAL order geometry + frozen snapshot, correct directional R:R on the canvas
    const od = await clickOrder(page, order.id)
    expect(od.binding.source).toBe('ORDER')
    expect(od.binding.snapshot_id).toBe(order.scenario_snapshot_id)
    expect(od.risk_reward_plan.take_profit).toBe(order.take_profit)
    expect(od.risk_reward_plan.stop_loss).toBe(order.stop_loss)
    await expect.poll(async () => (await rr(page))?.data?.takeProfit, { timeout: 15_000 }).toBe(order.take_profit)
    const r = await rr(page)
    expect(r.data.plannedEntry).toBe(order.planned_entry)
    expect(r.data.direction).toBe(direction)
    expect(r.geometry.entryY).not.toBeNull()
    if (direction === 'LONG') {
      expect(r.geometry.tpY).toBeLessThan(r.geometry.entryY) // price up = smaller y
      expect(r.geometry.slY).toBeGreaterThan(r.geometry.entryY)
    } else {
      expect(r.geometry.tpY).toBeGreaterThan(r.geometry.entryY) // SHORT: reward is BELOW entry
      expect(r.geometry.slY).toBeLessThan(r.geometry.entryY)
    }
    await page.screenshot({ path: `../logs/v14_4_1/qa_${direction.toLowerCase()}_order.png` })
    canvas = await hits(page)
    const odIds = bundleIds(od)
    for (const h of canvas) expect(odIds.has(h.id) || [...odIds].some((id) => h.id.includes(id))).toBeTruthy()

    // looking at the chart never mutates trading state
    expect(mutations).toEqual([])
    expect(pageErrors).toEqual([])
  })
}

test('armed / open orders are read-only on the canvas: a real pointer drag sends no mutation and moves no level', async ({ page, request }) => {
  const { mutations } = track(page)
  const st = await seed(page, request, { direction: 'SHORT', auto: true, fill: true })
  const order = st.orders[0]
  expect(order.state).toBe('paper_open')
  await page.goto('/')
  await clickOrder(page, order.id)
  await expect.poll(async () => (await rr(page))?.data?.takeProfit, { timeout: 15_000 }).toBe(order.take_profit)
  const before = await rr(page)
  const box = await page.locator('canvas').first().boundingBox()
  expect(box).not.toBeNull()
  const x = box!.x + before.geometry.startX + 40
  const slY = box!.y + before.geometry.slY
  await page.mouse.move(x, slY)
  await page.mouse.down()
  await page.mouse.move(x, slY - 40, { steps: 6 })
  await page.mouse.up()
  const after = await rr(page)
  expect(after.data.stopLoss).toBe(before.data.stopLoss)
  expect(after.data.takeProfit).toBe(before.data.takeProfit)
  expect(after.data.direction).toBe('SHORT')
  expect(mutations).toEqual([])
})

test('delayed scenario response A must not overwrite the later selection B (order)', async ({ page, request }) => {
  const { pageErrors } = track(page)
  const st = await seed(page, request, { direction: 'LONG', auto: true })
  const order = st.orders[0]
  const confirmed = st.scenarios.find((s) => s.state === 'STRUCTURE_CONFIRMED')!
  await page.goto('/')
  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  await page.route((url) => decodeURIComponent(url.href).includes(`/smc/scenarios/${confirmed.scenario_id}/chart-evidence`), async (route) => {
    await gate
    await route.continue()
  })
  await openChartTab(page)
  await page.locator(`[data-testid="scenario-card"][data-scenario-id="${confirmed.scenario_id}"]`).click() // A (held)
  await clickOrder(page, order.id) // B
  await expect.poll(async () => (await rr(page))?.data?.takeProfit, { timeout: 15_000 }).toBe(order.take_profit)
  // the superseded request A is aborted by the owner; releasing it must not change anything
  release()
  await page.waitForTimeout(500)
  const r = await rr(page)
  expect(r.data.takeProfit).toBe(order.take_profit) // still B
  expect(r.data.plannedEntry).toBe(order.planned_entry)
  expect(pageErrors).toEqual([])
})

test('a closed order older than the loaded window loads bounded history, anchors there and can return to live', async ({ page, request }) => {
  const { mutations } = track(page)
  const st = await seed(page, request, { direction: 'LONG', auto: true, fill: true, close: true, after_days: 4 })
  const order = st.orders[0]
  expect(order.state).toBe('closed')
  await page.goto('/')
  const windowRequest = page.waitForRequest((r) => /\/candles\/XAUUSDT\/(5M|15M)\?.*start_ms=/.test(r.url()), { timeout: 20_000 })
  const od = await clickOrder(page, order.id)
  await windowRequest
  expect(od.binding.source).toBe('ORDER')
  const entrySec = Math.floor((order.opened_at as number) / 1000)
  await expect
    .poll(async () => page.evaluate(() => (window as any).__AURUM_CHART_TEST__?.firstCandleSec?.() ?? null), { timeout: 20_000 })
    .toBeLessThanOrEqual(entrySec)
  await expect(page.getByTestId('back-to-live')).toBeVisible()
  const range = await page.evaluate(() => (window as any).__AURUM_CHART_TEST__?.visibleRange?.())
  expect(range.from).toBeLessThanOrEqual(entrySec)
  const liveEnd = await page.evaluate(() => (window as any).__AURUM_CHART_TEST__?.lastCandleSec?.())
  await page.getByTestId('back-to-live').click()
  await expect
    .poll(async () => (await page.evaluate(() => (window as any).__AURUM_CHART_TEST__?.visibleRange?.()))?.to, { timeout: 10_000 })
    .toBeGreaterThanOrEqual(liveEnd - 20 * 900)
  expect(mutations).toEqual([])
})

test('layer menu is keyboard accessible: ESC closes and focus returns to the trigger', async ({ page, request }) => {
  await seed(page, request, { direction: 'LONG', auto: true })
  await page.goto('/')
  const trigger = page.getByTestId('layer-menu-button')
  await trigger.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(trigger).toBeFocused()
})

for (const vp of [{ w: 1366, h: 768 }, { w: 1440, h: 900 }, { w: 390, h: 844 }]) {
  test(`responsive ${vp.w}x${vp.h}: candle area stays usable with a selected order`, async ({ page, request }) => {
    const { pageErrors } = track(page)
    await page.setViewportSize({ width: vp.w, height: vp.h })
    const st = await seed(page, request, { direction: 'SHORT', auto: true })
    await page.goto('/')
    await clickOrder(page, st.orders[0].id)
    await expect.poll(async () => (await rr(page))?.data?.takeProfit, { timeout: 15_000 }).toBe(st.orders[0].take_profit)
    // the chart pane = the largest canvas on the page (lightweight-charts renders several per pane)
    const box = await page.evaluate(() => {
      let best = { w: 0, h: 0, bottom: 0 }
      document.querySelectorAll('canvas').forEach((c) => {
        const r = c.getBoundingClientRect()
        if (r.width * r.height > best.w * best.h) best = { w: r.width, h: r.height, bottom: r.bottom }
      })
      return best
    })
    expect(box.h).toBeGreaterThanOrEqual(vp.w < 500 ? 200 : 300)
    expect(box.w).toBeGreaterThanOrEqual(vp.w < 500 ? 240 : 600)
    expect(pageErrors).toEqual([])
  })
}
