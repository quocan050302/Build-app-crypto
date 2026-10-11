import { defineConfig } from '@playwright/test'

/**
 * V14.2 browser tests.
 *
 * - backend: backend/tests/e2e_server.py (isolated temp DB, no background loops, no network/Telegram)
 * - frontend: Vite dev server on its own port, proxying /api to that isolated backend
 * - browser: the locally installed Google Chrome (channel "chrome"), so no browser download is needed.
 *   Override with AURUM_E2E_CHANNEL (e.g. "msedge") or AURUM_E2E_BROWSER_PATH for another executable.
 */
const API_PORT = Number(process.env.AURUM_E2E_API_PORT ?? 8765)
const WEB_PORT = Number(process.env.AURUM_E2E_WEB_PORT ?? 5199)
const PYTHON = process.env.AURUM_E2E_PYTHON ?? '../backend/venv/bin/python'
const executablePath = process.env.AURUM_E2E_BROWSER_PATH

export default defineConfig({
  testDir: './tests/e2e',
  // *.e2e.ts (not *.spec.ts) so that `npm test` (vitest) never tries to run browser specs.
  testMatch: '**/*.e2e.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['json', { outputFile: '../logs/acceptance/v14_2/playwright_report.json' }]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    viewport: { width: 1512, height: 857 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...(executablePath
      ? { launchOptions: { executablePath } }
      : { channel: process.env.AURUM_E2E_CHANNEL ?? 'chrome' }),
  },
  webServer: [
    {
      command: `${PYTHON} ../backend/tests/e2e_server.py --port ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/__e2e/state`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npx vite --port ${WEB_PORT} --strictPort`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { AURUM_API_TARGET: `http://127.0.0.1:${API_PORT}` },
    },
  ],
})
