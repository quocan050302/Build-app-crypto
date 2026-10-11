/**
 * V14.2 full-App browser tests (real Chrome, real React App, real FastAPI app, isolated temp DB).
 *
 * The backend is backend/tests/e2e_server.py: no background loops, no network, no Telegram.
 * Test-only controls are under /__e2e/* on that launcher (reset, news, tick, state).
 *
 * Covered here: focus LONG -> SHORT, provisional/tight plan, news blackout chip + disabled CTA,
 * copy-to-draft, "no trading mutation on focus/copy", slow preview race, resize, UI arm, then the REAL
 * coordinator fill and the "news appears after arm -> reject once" path.
 * NOT covered here (limitation): canvas drag of SL/TP handles (covered by V14.1 unit tests + harness).
 */
import { expect, test, type Page, type APIRequestContext } from '@playwright/test'

const API = `http://127.0.0.1:${process.env.AURUM_E2E_API_PORT ?? 8765}`
const MUTATING = /\/api\/v1\/(orders\/paper|setups\/arm|orders\/close|setups\/cancel)/

type BackendState = {
  orders_by_state: Record<string, number>
  orders: number
  fills: number
  opened_events: number
  rejected_events: number
  outbox: number
  rejection_reasons: string[]
}

async function backend(request: APIRequestContext, path: string, body?: object): Promise<BackendState> {
  const res = body === undefined && path.includes('state')
    ? await request.get(`${API}${path}`)
    : await request.post(`${API}${path}`, { data: body ?? {} })
  expect(res.ok()).toBeTruthy()
  return res.json()
}

function trackMutations(page: Page) {
  const calls: string[] = []
  page.on('request', (req) => {
    if (req.method() === 'POST' && MUTATING.test(req.url())) calls.push(`${req.method()} ${new URL(req.url()).pathname}`)
  })
  return calls
}

async function openUpcoming(page: Page) {
  await page.getByRole('button', { name: /Kế Hoạch & Lệnh Dự Kiến/ }).click()
  await expect(page.locator('[data-setup-id="e2e-long"]')).toBeVisible()
}

async function focusSetup(page: Page, setupId: string) {
  await openUpcoming(page)
  await page.locator(`[data-setup-id="${setupId}"]`).getByRole('button', { name: /Xem Trên Chart/ }).click()
  await expect(page.locator('#entry-decision-status')).toBeVisible()
}

test.beforeEach(async ({ page, request }) => {
  await backend(request, '/__e2e/reset')
  await page.goto('/')
  await expect(page.getByRole('button', { name: /Kế Hoạch & Lệnh Dự Kiến/ })).toBeVisible()
})

test('focus LONG then SHORT switches direction/decision without any trading mutation', async ({ page, request }) => {
  const calls = trackMutations(page)

  await focusSetup(page, 'e2e-long')
  await expect(page.locator('#entry-decision-status')).toHaveText('Có thể đặt lệnh')
  await expect(page.locator('#entry-submit-cta')).toContainText('LONG')
  await expect(page.locator('#entry-submit-cta')).toHaveAttribute('data-can-submit', 'true')

  await focusSetup(page, 'e2e-short')
  await expect(page.locator('#entry-submit-cta')).toContainText('SHORT')
  await expect(page.locator('#entry-submit-cta')).not.toContainText('LONG')

  expect(calls).toEqual([])
  const st = await backend(request, '/__e2e/state')
  expect(st.orders).toBe(0)
  expect(st.fills).toBe(0)
})

test('provisional tight-target SHORT is shown as unconfirmed/blocked with signed negative R:R and no CTA', async ({ page, request }) => {
  const calls = trackMutations(page)
  await focusSetup(page, 'e2e-tight-short')

  const cta = page.locator('#entry-submit-cta')
  await expect(cta).toBeDisabled()
  await expect(cta).toHaveAttribute('data-can-submit', 'false')
  await expect(page.locator('#entry-decision-status')).not.toHaveText('Có thể đặt lệnh')
  // negative net reward / R:R must be visible as a negative number (never rounded to 0 or hidden)
  await expect(page.locator('body')).toContainText(/-\s?0\.\d{2}/)
  // the entry is explicitly unconfirmed, not a "0.00 to Entry" live badge
  await expect(page.locator('body')).toContainText('Điểm vào chưa xác nhận')

  expect(calls).toEqual([])
  expect((await backend(request, '/__e2e/state')).orders).toBe(0)
})

test('red news blackout disables the CTA and shows the pause chip', async ({ page, request }) => {
  await backend(request, '/__e2e/news', { title: 'Core CPI m/m', currency: 'USD', impact: 'High', offset_min: 0 })
  await page.reload()
  await focusSetup(page, 'e2e-long')

  await expect(page.locator('#entry-news-chip')).toContainText('Tạm dừng vào lệnh')
  await expect(page.locator('#entry-submit-cta')).toBeDisabled()
  await expect(page.locator('#entry-decision-status')).toHaveText('Bị chặn')
})

test('news outside the window (or non-USD) does not disable a valid plan', async ({ page, request }) => {
  await backend(request, '/__e2e/news', { title: 'Retail Sales', currency: 'USD', impact: 'High', offset_min: 300 })
  await backend(request, '/__e2e/news', { title: 'ECB Rate Decision', currency: 'EUR', impact: 'High', offset_min: 0 })
  await page.reload()
  await focusSetup(page, 'e2e-long')
  await expect(page.locator('#entry-submit-cta')).toHaveAttribute('data-can-submit', 'true')
})

test('copy to draft does not arm or create anything', async ({ page, request }) => {
  const calls = trackMutations(page)
  await openUpcoming(page)
  await page.locator('[data-setup-id="e2e-long"]').getByRole('button', { name: /Chép sang Bản Nháp/ }).click()
  await page.waitForTimeout(800)
  expect(calls).toEqual([])
  expect((await backend(request, '/__e2e/state')).orders).toBe(0)
})

test('a slow quality preview never lets a stale reply override the newly focused setup', async ({ page }) => {
  await page.route('**/api/v1/quality/preview', async (route) => {
    await new Promise((r) => setTimeout(r, 1500))
    await route.continue()
  })
  await focusSetup(page, 'e2e-long')
  await focusSetup(page, 'e2e-short')
  await page.waitForTimeout(2200) // let the slow reply arrive
  await expect(page.locator('#entry-submit-cta')).toContainText('SHORT')
  await expect(page.locator('#entry-submit-cta')).not.toContainText('LONG')
})

test('sidebar stays usable after resizing to a low laptop viewport', async ({ page }) => {
  await focusSetup(page, 'e2e-long')
  await page.setViewportSize({ width: 1280, height: 640 })
  await expect(page.locator('#entry-decision-status')).toBeVisible()
  await page.locator('#entry-submit-cta').scrollIntoViewIfNeeded()
  await expect(page.locator('#entry-submit-cta')).toBeVisible()
})

test('arm from the UI then fill through the real coordinator opens exactly one position', async ({ page, request }) => {
  const calls = trackMutations(page)
  await focusSetup(page, 'e2e-long')
  await page.locator('#entry-submit-cta').click()
  await expect.poll(async () => (await backend(request, '/__e2e/state')).orders_by_state['armed'] ?? 0).toBe(1)
  expect(calls.filter((c) => c.includes('/setups/arm')).length).toBe(1)

  const filled = await backend(request, '/__e2e/tick', { direction: 'LONG' })
  expect(filled.orders_by_state['paper_open']).toBe(1)
  expect(filled.fills).toBe(1)
  expect(filled.opened_events).toBe(1)
})

test('news published after arming rejects the pending order once, before any fill', async ({ page, request }) => {
  await focusSetup(page, 'e2e-short')
  await page.locator('#entry-submit-cta').click()
  await expect.poll(async () => (await backend(request, '/__e2e/state')).orders_by_state['armed'] ?? 0).toBe(1)

  await backend(request, '/__e2e/news', { title: 'FOMC Statement', currency: 'USD', impact: 'High', offset_min: 0 })
  const first = await backend(request, '/__e2e/tick', { direction: 'SHORT' })
  expect(first.orders_by_state['rejected']).toBe(1)
  expect(first.orders_by_state['paper_open'] ?? 0).toBe(0)
  expect(first.fills).toBe(0)
  expect(first.opened_events).toBe(0)
  expect(first.rejected_events).toBe(1)
  expect(first.rejection_reasons.join(' ')).toContain('NEWS_BLACKOUT')

  const again = await backend(request, '/__e2e/tick', { direction: 'SHORT' })
  expect(again.rejected_events).toBe(1)
  expect(again.outbox).toBe(first.outbox)
  expect(again.fills).toBe(0)
})
