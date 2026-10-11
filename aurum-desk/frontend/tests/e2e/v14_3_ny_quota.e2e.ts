/**
 * V14.3 NY quota - real Chrome, real React App, real FastAPI app, isolated temp DB.
 *
 * The launcher (backend/tests/e2e_server.py) only injects the process clock, M5 candles and the quote under
 * /__e2e/ny/*. The NY cycle, fallback generator, dedicated upsert, V14.2 guards, auto-arm, execution coordinator
 * fill and the quota lifecycle are the REAL production code. The UI is asserted AFTER each backend lifecycle step.
 */
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const API = `http://127.0.0.1:${process.env.AURUM_E2E_API_PORT ?? 8765}`
const MUTATING = /\/api\/v1\/(orders\/paper|setups\/arm|orders\/close|setups\/cancel|policy\/trading)/

type NyState = {
  orders: number
  armed: number
  open_positions: number
  total_fills: number
  fallback_fills: number
  quota_status: string
  opened_events: number
}

async function ny(request: APIRequestContext, path: string, body: object = {}): Promise<NyState> {
  const res = await request.post(`${API}/__e2e/ny/${path}`, { data: body })
  expect(res.ok()).toBeTruthy()
  return res.json()
}

function trackMutations(page: Page) {
  const calls: string[] = []
  page.on('request', (req) => {
    if (req.method() !== 'GET' && MUTATING.test(req.url())) calls.push(`${req.method()} ${new URL(req.url()).pathname}`)
  })
  return calls
}

async function showBar(page: Page) {
  await page.reload()
  await expect(page.getByTestId('ny-quota-status')).toBeVisible()
}

test.beforeEach(async ({ page }) => {
  await page.goto('/')
})

for (const direction of ['LONG', 'SHORT'] as const) {
  test(`${direction}: standard waiting 0/1 -> fallback seeking -> ARMED 0/1 -> fill 1/1`, async ({ page, request }) => {
    const calls = trackMutations(page)

    await ny(request, 'reset', { time: '09:00' })
    await showBar(page)
    await expect(page.getByTestId('ny-quota-status')).toHaveText('ĐANG TÌM SMC')
    await expect(page.getByTestId('ny-quota-counter')).toContainText('0 / 1')

    // 10:31: fallback window open, but the tape has no setup yet -> searching, still 0/1
    await ny(request, 'clock', { time: '10:31' })
    let st = await ny(request, 'cycle', { direction, story: 'FLAT' })
    expect(st.orders).toBe(0)
    await showBar(page)
    await expect(page.getByTestId('ny-quota-status')).toContainText('ĐANG TÌM FALLBACK')
    await expect(page.getByTestId('ny-quota-counter')).toContainText('0 / 1')

    // 10:35: a real FVG retest exists -> the real chain ARMS (still 0/1: armed is not filled)
    await ny(request, 'clock', { time: '10:35' })
    st = await ny(request, 'cycle', { direction })
    expect(st.armed).toBe(1)
    expect(st.total_fills).toBe(0)
    expect(st.open_positions).toBe(0)
    await showBar(page)
    await expect(page.getByTestId('ny-quota-status')).toContainText('ĐÃ LÊN NÒNG')
    await expect(page.getByTestId('ny-quota-counter')).toContainText('0 / 1')
    await expect(page.getByTestId('ny-quota-counter')).not.toContainText('1 / 1')

    // the real coordinator fills it -> only now 1/1
    st = await ny(request, 'fill', { direction })
    expect(st.open_positions).toBe(1)
    expect(st.total_fills).toBe(1)
    expect(st.fallback_fills).toBe(1)
    expect(st.quota_status).toBe('FULFILLED')
    expect(st.opened_events).toBe(1)
    await showBar(page)
    await expect(page.getByTestId('ny-quota-status')).toContainText('HOÀN THÀNH')
    await expect(page.getByTestId('ny-quota-counter')).toContainText('1 / 1')

    expect(calls).toEqual([]) // the browser never mutated trading state; the backend lifecycle did
  })
}

test('blocked by red news: counter stays 0/1, no order, drawer explains the safety reason', async ({ page, request }) => {
  const calls = trackMutations(page)
  await ny(request, 'reset', { time: '10:20' })
  await ny(request, 'clock', { time: '10:35' })
  await ny(request, 'news', { title: 'Core CPI m/m', offset_min: 0 })
  const st = await ny(request, 'cycle', { direction: 'LONG' })
  expect(st.orders).toBe(0)
  expect(st.total_fills).toBe(0)

  await showBar(page)
  await expect(page.getByTestId('ny-quota-counter')).toContainText('0 / 1')
  await expect(page.getByTestId('ny-quota-status')).not.toContainText('HOÀN THÀNH')

  await page.getByRole('button', { name: /Phiên & Chính sách/ }).click()
  await expect(page.getByTestId('ny-diagnostics')).toBeVisible()
  // signal exists but was blocked: it is still in the funnel (not "no signal")
  await expect(page.getByTestId('ny-diagnostics-funnel')).toContainText('Tín hiệu 1')
  await expect(page.getByTestId('ny-diagnostics-funnel')).toContainText('khớp 0')
  await expect(page.getByTestId('ny-diagnostics-blockers')).toContainText('An toàn')
  expect(calls).toEqual([])
})

test('fallback disabled is never shown as active and creates no order', async ({ page, request }) => {
  await ny(request, 'reset', { time: '10:20', fallback_enabled: false })
  await ny(request, 'clock', { time: '10:35' })
  const st = await ny(request, 'cycle', { direction: 'LONG' })
  expect(st.orders).toBe(0)
  await showBar(page)
  await expect(page.getByTestId('ny-quota-status')).not.toContainText('ĐANG TÌM FALLBACK')
  await expect(page.getByTestId('ny-quota-status')).toContainText('FALLBACK TẮT')
  await expect(page.getByTestId('ny-quota-reason')).toContainText('Fallback đang tắt')
  await expect(page.getByTestId('ny-quota-counter')).toContainText('0 / 1')
})

test('MISSED window creates no order and the drawer reports why', async ({ page, request }) => {
  await ny(request, 'reset', { time: '10:20' })
  await ny(request, 'clock', { time: '10:35' })
  await ny(request, 'cycle', { direction: 'LONG', story: 'FLAT' })
  await ny(request, 'clock', { time: '11:00' })
  const st = await ny(request, 'cycle', { direction: 'LONG', story: 'FLAT' })
  expect(st.orders).toBe(0)
  expect(st.quota_status).toBe('MISSED')
  await showBar(page)
  await expect(page.getByTestId('ny-quota-status')).toContainText('KHÔNG CÓ LỆNH')
  await expect(page.getByTestId('ny-quota-counter')).toContainText('0 / 1')
  await page.getByRole('button', { name: /Phiên & Chính sách/ }).click()
  await expect(page.getByTestId('ny-diagnostics-missed')).toBeVisible()
})

test('1280x640: bar fits, drawer closed by default, opening it and changing date only issue GETs', async ({ page, request }) => {
  const calls = trackMutations(page)
  await page.setViewportSize({ width: 1280, height: 640 })
  await ny(request, 'reset', { time: '10:20' })
  await ny(request, 'clock', { time: '10:35' })
  await ny(request, 'cycle', { direction: 'LONG' })
  await showBar(page)

  for (const id of ['ny-quota-status', 'ny-quota-counter']) {
    const box = await page.getByTestId(id).boundingBox()
    expect(box).not.toBeNull()
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.y).toBeGreaterThanOrEqual(0)
    expect(box!.y + box!.height).toBeLessThanOrEqual(640)
  }
  await expect(page.getByTestId('ny-diagnostics')).toHaveCount(0) // no permanent telemetry panel

  await page.getByRole('button', { name: /Phiên & Chính sách/ }).click()
  await expect(page.getByTestId('ny-diagnostics')).toBeVisible()
  await page.getByTestId('ny-diagnostics-date').fill('2026-10-08')
  await expect(page.getByTestId('ny-diagnostics-funnel')).toContainText('Chưa có dữ liệu đo')
  await expect(page.getByTestId('ny-diagnostics-funnel')).not.toContainText('Tín hiệu 0')
  expect(calls).toEqual([])
})
