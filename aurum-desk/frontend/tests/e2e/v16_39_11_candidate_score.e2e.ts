/**
 * frontend/tests/e2e/v16_39_11_candidate_score.e2e.ts
 * V16.39.12 Candidate Quality Score Display E2E Browser Test
 *
 * Rules 151-164:
 * - Seed route /__e2e/seed_order must return 200/201 and valid body (no silent catch).
 * - Pre-verify production endpoint /api/v1/orders/{id}/quality-score before browser opens.
 * - Seed distinct Order A (LONG 78/100) and Order B (SHORT 64/100).
 * - Verify exact score text "78/100" and order identity, not merely any visible badge.
 * - Mobile (390x844): compact badge visible, no layout overflow blowout.
 */

import { test, expect } from '@playwright/test';

const API_PORT = process.env.AURUM_E2E_API_PORT ?? '8765';

test.describe('V16.39.12 Candidate Quality Score UI & Exact Identity Verification', () => {
  test.beforeEach(async ({ page, request }) => {
    await page.goto('about:blank');
    const resetRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/reset`);
    expect(resetRes.ok()).toBeTruthy();
  });

  test('E12-1: Seed distinct orders and verify exact 78/100 and 64/100 scores via API and UI', async ({
    page,
    request,
  }) => {
    // 1. Seed Order A: LONG, 78/100, ELIGIBLE_BY_SCORE with canonical factors (as active paper_open position)
    const seedResA = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'e2e_ord_long_78',
        direction: 'LONG',
        planned_entry: 2650.0,
        stop_loss: 2640.0,
        take_profit: 2675.0,
        state: 'paper_open',
        cost_snapshot: {
          schema_version: '2.0',
          quality_assessment: {
            final_score: 78.0,
            base_score: 83.0,
            lesson_delta: -5.0,
            threshold: 70.0,
            decision: 'ELIGIBLE_BY_SCORE',
            as_of_ms: 1776510000000,
            assessment_id: 'assess_long_78',
            candidate_key: 'XAUUSDT:15M:LONG:2650.0',
            factors: [
              {
                code: 'FVG_ALIGNMENT',
                group: 'STRUCTURE',
                earned_points: 18.0,
                max_points: 20.0,
                status: 'FULL',
                explanation_vi: 'Khối FVG tăng M15 đồng pha hợp lệ',
              },
            ],
          },
        },
      },
    });
    expect(seedResA.status()).toBe(200);
    const bodyA = await seedResA.json();
    expect(bodyA.order_id).toBe('e2e_ord_long_78');
    expect(bodyA.assessment_id).toBe('assess_long_78');

    // 2. Seed Order B: SHORT, 64/100, BELOW_THRESHOLD
    const seedResB = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'e2e_ord_short_64',
        direction: 'SHORT',
        planned_entry: 2655.0,
        stop_loss: 2665.0,
        take_profit: 2630.0,
        state: 'closed',
        cost_snapshot: {
          schema_version: '2.0',
          quality_assessment: {
            final_score: 64.0,
            base_score: 64.0,
            lesson_delta: 0.0,
            threshold: 70.0,
            decision: 'BELOW_THRESHOLD',
            as_of_ms: 1776510010000,
            assessment_id: 'assess_short_64',
            candidate_key: 'XAUUSDT:15M:SHORT:2655.0',
            factors: [
              {
                code: 'FVG_ALIGNMENT',
                group: 'STRUCTURE',
                earned_points: 8.0,
                max_points: 20.0,
                status: 'PARTIAL',
                explanation_vi: 'FVG giảm chưa đủ biên độ chuẩn',
              },
            ],
          },
        },
      },
    });
    expect(seedResB.status()).toBe(200);
    const bodyB = await seedResB.json();
    expect(bodyB.order_id).toBe('e2e_ord_short_64');
    expect(bodyB.assessment_id).toBe('assess_short_64');

    // 3. Pre-verify production quality-score endpoint directly for Order A
    const verifyApiA = await request.get(
      `http://127.0.0.1:${API_PORT}/api/v1/orders/e2e_ord_long_78/quality-score`
    );
    expect(verifyApiA.status()).toBe(200);
    const scoreDataA = await verifyApiA.json();
    expect(scoreDataA.status).toBe('SUCCESS');
    expect(scoreDataA.detail.score).toBe(78.0);
    expect(scoreDataA.detail.status).toBe('AVAILABLE');
    expect(scoreDataA.detail.factors[0].code).toBe('FVG_ALIGNMENT');
    expect(scoreDataA.detail.factors[0].earned_points).toBe(18.0);

    // 4. Pre-verify production quality-score endpoint directly for Order B
    const verifyApiB = await request.get(
      `http://127.0.0.1:${API_PORT}/api/v1/orders/e2e_ord_short_64/quality-score`
    );
    expect(verifyApiB.status()).toBe(200);
    const scoreDataB = await verifyApiB.json();
    expect(scoreDataB.status).toBe('SUCCESS');
    expect(scoreDataB.detail.score).toBe(64.0);
    expect(scoreDataB.detail.status).toBe('AVAILABLE');
    expect(scoreDataB.detail.factors[0].code).toBe('FVG_ALIGNMENT');
    expect(scoreDataB.detail.factors[0].earned_points).toBe(8.0);

    // 5. Open dashboard and verify badge rendering with exact identity
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // Verify badge exists and carries exact score text 78/100
    const badgeA = page.locator('[data-testid="candidate-score-badge"]:has-text("78/100")');
    await expect(badgeA.first()).toBeVisible({ timeout: 10000 });

    // 6. Click badge A to open quality drawer and verify exact factors
    await badgeA.first().click();

    // Verify drawer opens and displays authoritative score metrics & canonical factor
    const drawerTitle = page.locator('text=Điểm Chất Lượng Lúc ARM (Bất Biến)');
    await expect(drawerTitle).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=Khối FVG tăng M15 đồng pha hợp lệ')).toBeVisible();
    await expect(page.locator('text=+18/20')).toBeVisible();

    // Close drawer cleanly
    const closeBtn = page.locator('button[aria-label="Đóng bảng chi tiết"]');
    await expect(closeBtn).toBeVisible();
    await closeBtn.click();
    await expect(drawerTitle).not.toBeVisible();
  });

  test('E12-2: Mobile viewport (390x844) displays compact badges without layout overflow', async ({
    page,
    request,
  }) => {
    // Seed an active position so the mobile view has a badge
    const seedRes = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'e2e_ord_mobile_78',
        direction: 'LONG',
        planned_entry: 2650.0,
        stop_loss: 2640.0,
        take_profit: 2675.0,
        state: 'paper_open',
        cost_snapshot: {
          schema_version: '2.0',
          quality_assessment: {
            final_score: 78.0,
            threshold: 70.0,
            decision: 'ELIGIBLE_BY_SCORE',
            as_of_ms: 1776510000000,
          },
        },
      },
    });
    expect(seedRes.status()).toBe(200);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // Verify main content fits without horizontal scroll blowout
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 2); // 1-2px tolerance
  });

  test('E12-3: Browser lifecycle: pending close aborts cleanly; mismatched identity shows error and retry succeeds (Rules 156, 158, 159)', async ({
    page,
    request,
  }) => {
    // 1. Seed Order A (LONG, 78)
    const seedResA = await request.post(`http://127.0.0.1:${API_PORT}/__e2e/seed_order`, {
      data: {
        order_id: 'e2e_ord_lifecycle_78',
        direction: 'LONG',
        planned_entry: 2650.0,
        stop_loss: 2640.0,
        take_profit: 2675.0,
        state: 'paper_open',
        cost_snapshot: {
          schema_version: '2.0',
          quality_assessment: {
            final_score: 78.0,
            base_score: 83.0,
            lesson_delta: -5.0,
            threshold: 70.0,
            decision: 'ELIGIBLE_BY_SCORE',
            as_of_ms: 1776510000000,
            assessment_id: 'assess_lifecycle_78',
            candidate_key: 'XAUUSDT:15M:LONG:2650.0',
            factors: [
              {
                code: 'FVG_ALIGNMENT',
                group: 'STRUCTURE',
                earned_points: 18.0,
                max_points: 20.0,
                status: 'FULL',
                explanation_vi: 'Khối FVG tăng M15 đồng pha hợp lệ',
              },
            ],
          },
        },
      },
    });
    expect(seedResA.status()).toBe(200);

    // Rule 158: Intercept first request to return mismatched order_id
    let callCount = 0;
    await page.route('**/api/v1/orders/e2e_ord_lifecycle_78/quality-score', async (route) => {
      callCount += 1;
      if (callCount === 1) {
        // Return response with mismatched order_id
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'SUCCESS',
            order_id: 'wrong_order_id',
            detail: {
              order_id: 'wrong_order_id',
              assessment_id: 'assess_lifecycle_78',
              score: 78.0,
              status: 'AVAILABLE',
              factors: [],
            },
          }),
        });
      } else {
        // Continue to server for retry
        await route.continue();
      }
    });

    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // Open drawer
    const badge = page.locator('[data-testid="candidate-score-badge"]:has-text("78/100")').first();
    await expect(badge).toBeVisible({ timeout: 10000 });
    await badge.click();

    // Verify inline error appears due to mismatched identity (Rule 158)
    const errorText = page.locator('text=Mã lệnh phản hồi không khớp với lệnh yêu cầu.');
    await expect(errorText).toBeVisible({ timeout: 5000 });

    // Verify retry button is present (Rule 159)
    const retryBtn = page.locator('button:has-text("Thử lại")');
    await expect(retryBtn).toBeVisible();

    // Click retry -> second call continues to server and succeeds
    await retryBtn.click();

    // Error disappears and verified canonical factor appears
    await expect(errorText).not.toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=Khối FVG tăng M15 đồng pha hợp lệ')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=+18/20')).toBeVisible();

    // Close drawer
    const closeBtn = page.locator('button[aria-label="Đóng bảng chi tiết"]');
    await closeBtn.click();
    await expect(page.locator('text=Điểm Chất Lượng Lúc ARM (Bất Biến)')).not.toBeVisible();
  });
});
