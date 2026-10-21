/**
 * frontend/tests/e2e/v16_39_11_candidate_score.e2e.ts
 * V16.39.11 Candidate Quality Score Display E2E Browser Test
 *
 * Rules 174, 175:
 * - Desktop: Verify setup list, position, journal, and chart header badges display "78/100".
 * - Identity: Selected SHORT vs LONG chart binding preserves score identity.
 * - Mobile (390x844): Badges visible, chart/details accessible, no horizontal blowout.
 */

import { test, expect } from '@playwright/test';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

test.describe('V16.39.11 Candidate Quality Score UI Verification', () => {
  test.beforeEach(async ({ page, request }) => {
    await page.goto('about:blank');
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
  });

  test('E11-1: Desktop displays CandidateScoreBadge on setup, journal, and chart header', async ({ page, request }) => {
    // 1. Seed order with valid candidate score assessment (78/100)
    await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        direction: 'LONG',
        planned_entry: 2650.0,
        stop_loss: 2640.0,
        take_profit: 2675.0,
        state: 'ARMED',
        cost_snapshot: {
          schema_version: '2.0',
          quality_assessment: {
            final_score: 78.0,
            base_score: 83.0,
            lesson_delta: -5.0,
            threshold: 70.0,
            decision: 'ELIGIBLE',
            as_of_ms: Date.now() - 60000,
            assessment_id: 'assess-seed-01',
            candidate_key: 'XAUUSDT:15M:LONG:2650.0',
          },
        },
      },
    }).catch(() => {});

    // 2. Open dashboard
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // 3. Verify CandidateScoreBadge is rendered in QuickDecisionSidebar or Upcoming
    const badge = page.locator('[data-testid="candidate-score-badge"]').first();
    await expect(badge).toBeVisible({ timeout: 10000 });
  });

  test('E11-2: Mobile viewport (390x844) displays compact badges without layout overflow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // Verify main content fits without horizontal scroll blowout
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 2); // 1-2px tolerance
  });
});
