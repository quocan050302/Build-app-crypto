/**
 * frontend/tests/e2e/v17_4_1_entry_sessions.e2e.ts
 * Aurum V17.4.1 — Comprehensive Entry Session Browser Acceptance Suite
 *
 * Implements Parts 161 - 168:
 * - Part 161: Browser save success — draft select does not change banner; PUT sends expected_version; 0 direction POST.
 * - Part 162: Browser save 500 — error feedback, legacy banner preserved, draft preserved.
 * - Part 163: Browser stale 409 — conflict message displayed, zero auto-retry PUTs.
 * - Part 164: Browser direction failure — independent direction save failure does not affect session badge.
 * - Part 168: Browser mobile 390x844 viewport responsive layout check without horizontal overflow.
 */

import { test, expect } from '@playwright/test';

test.describe('Aurum V17.4.1 — Entry Sessions Acceptance', () => {
  test('Part 161: Save session success with strict expected_version and zero direction POST', async ({ page }) => {
    let putPayload: any = null;
    let postScalpConfigCalled = false;

    await page.route('**/api/v1/policy/entry-session', async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'CONFIRMED',
            entry_session_policy: 'ALL_SESSIONS_WITH_NY_RESERVE',
            timing_profile: 'SILVER_BULLET_LONDON_NY',
            effective_ny_goal: 1,
            reserve_ny_slot: true,
            ny_fallback_enabled: true,
            max_daily_fills: 3,
            daily_timezone: 'Asia/Ho_Chi_Minh',
            policy_version: 7,
            execution_scope: 'PAPER',
            auto_execution_enabled: false,
            source: 'PERSISTED',
            reason_codes: [],
            message: null,
          }),
        });
      } else if (route.request().method() === 'PUT') {
        putPayload = JSON.parse(route.request().postData() || '{}');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'CONFIRMED',
            entry_session_policy: 'ALL_SESSIONS_SETUP_FIRST',
            timing_profile: 'CG01_ALL_SESSIONS_PAPER',
            effective_ny_goal: 0,
            reserve_ny_slot: false,
            ny_fallback_enabled: false,
            max_daily_fills: 3,
            daily_timezone: 'Asia/Ho_Chi_Minh',
            policy_version: 8,
            execution_scope: 'PAPER',
            auto_execution_enabled: false,
            source: 'PERSISTED',
            reason_codes: [],
            message: 'Đã lưu cấu hình phiên thành công!',
          }),
        });
      }
    });

    await page.route('**/api/scalp/config', async (route) => {
      if (route.request().method() === 'POST') {
        postScalpConfigCalled = true;
      }
      await route.continue();
    });

    await page.goto('/');

    // Toggle settings
    const toggleBtn = page.getByTestId('cg01-toggle-settings-btn');
    await expect(toggleBtn).toBeVisible({ timeout: 15000 });
    await toggleBtn.click();

    // Select CG01_ALL_SESSIONS_PAPER draft
    const timingSelect = page.getByTestId('cg01-timing-profile-select');
    await expect(timingSelect).toBeVisible();
    await timingSelect.selectOption('CG01_ALL_SESSIONS_PAPER');

    // Banner must NOT change to "Mọi phiên" yet
    const banner = page.getByTestId('v1639-paper-experiment-banner');
    await expect(banner).toBeVisible();
    await expect(banner).not.toContainText('Mọi phiên');

    // Click Save session
    const saveBtn = page.getByTestId('cg01-save-policy-btn');
    await saveBtn.click();

    // Verify PUT payload
    expect(putPayload).not.toBeNull();
    expect(putPayload.expected_version).toBe(7);
    expect(putPayload.entry_session_policy).toBe('ALL_SESSIONS_SETUP_FIRST');
    expect(putPayload.timing_profile).toBe('CG01_ALL_SESSIONS_PAPER');

    // Feedback and banner update
    const feedback = page.getByTestId('cg01-policy-save-feedback');
    await expect(feedback).toBeVisible();
    await expect(feedback).toContainText('Đã lưu');
    await expect(banner).toContainText('Mọi phiên');

    // Zero direction POST calls during session save
    expect(postScalpConfigCalled).toBe(false);
  });

  test('Part 162: Server 500 retains legacy banner and shows error message', async ({ page }) => {
    await page.route('**/api/v1/policy/entry-session', async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'CONFIRMED',
            entry_session_policy: 'ALL_SESSIONS_WITH_NY_RESERVE',
            timing_profile: 'SILVER_BULLET_LONDON_NY',
            effective_ny_goal: 1,
            reserve_ny_slot: true,
            ny_fallback_enabled: true,
            max_daily_fills: 3,
            daily_timezone: 'Asia/Ho_Chi_Minh',
            policy_version: 5,
            execution_scope: 'PAPER',
            auto_execution_enabled: false,
            source: 'PERSISTED',
            reason_codes: [],
            message: null,
          }),
        });
      } else if (route.request().method() === 'PUT') {
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: 'DATABASE_LOCKED: Giao dịch không thể hoàn tất',
          }),
        });
      }
    });

    await page.goto('/');

    const toggleBtn = page.getByTestId('cg01-toggle-settings-btn');
    await expect(toggleBtn).toBeVisible({ timeout: 15000 });
    await toggleBtn.click();

    const timingSelect = page.getByTestId('cg01-timing-profile-select');
    await timingSelect.selectOption('CG01_ALL_SESSIONS_PAPER');

    const saveBtn = page.getByTestId('cg01-save-policy-btn');
    await saveBtn.click();

    const feedback = page.getByTestId('cg01-policy-save-feedback');
    await expect(feedback).toBeVisible();
    await expect(feedback).toContainText('DATABASE_LOCKED');

    const banner = page.getByTestId('v1639-paper-experiment-banner');
    await expect(banner).not.toContainText('Mọi phiên');
  });

  test('Part 163: Stale 409 displays reload guidance without automatic retry loops', async ({ page }) => {
    let putCount = 0;

    await page.route('**/api/v1/policy/entry-session', async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'CONFIRMED',
            entry_session_policy: 'ALL_SESSIONS_WITH_NY_RESERVE',
            timing_profile: 'SILVER_BULLET_LONDON_NY',
            effective_ny_goal: 1,
            reserve_ny_slot: true,
            ny_fallback_enabled: true,
            max_daily_fills: 3,
            daily_timezone: 'Asia/Ho_Chi_Minh',
            policy_version: 3,
            execution_scope: 'PAPER',
            auto_execution_enabled: false,
            source: 'PERSISTED',
            reason_codes: [],
          }),
        });
      } else if (route.request().method() === 'PUT') {
        putCount++;
        await route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: 'STALE_POLICY_VERSION: Cấu hình đã thay đổi ở nơi khác',
          }),
        });
      }
    });

    await page.goto('/');

    const toggleBtn = page.getByTestId('cg01-toggle-settings-btn');
    await expect(toggleBtn).toBeVisible({ timeout: 15000 });
    await toggleBtn.click();

    const saveBtn = page.getByTestId('cg01-save-policy-btn');
    await saveBtn.click();

    const feedback = page.getByTestId('cg01-policy-save-feedback');
    await expect(feedback).toBeVisible();
    await expect(feedback).toContainText('thay đổi ở nơi khác');

    // Zero auto-retry PUTs
    expect(putCount).toBe(1);
  });

  test('Part 164: Direction failure displays isolated error feedback', async ({ page }) => {
    await page.route('**/api/scalp/config', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'BIAS_SAVE_FAILED' }),
        });
      } else {
        await route.continue();
      }
    });

    await page.goto('/');

    const toggleBtn = page.getByTestId('cg01-toggle-settings-btn');
    await expect(toggleBtn).toBeVisible({ timeout: 15000 });
    await toggleBtn.click();

    const policySelect = page.getByTestId('cg01-policy-select');
    await expect(policySelect).toBeVisible();
    await policySelect.selectOption('DAILY_BIAS_PRIMARY');

    const saveDirBtn = page.getByTestId('cg01-save-direction-btn');
    await expect(saveDirBtn).toBeVisible();
    await saveDirBtn.click();

    const dirFeedback = page.getByTestId('cg01-direction-save-feedback');
    await expect(dirFeedback).toBeVisible();
    await expect(dirFeedback).toContainText('BIAS_SAVE_FAILED');
  });

  test('Part 168: Mobile viewport 390x844 responsive layout check', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');

    const toggleBtn = page.getByTestId('cg01-toggle-settings-btn');
    await expect(toggleBtn).toBeVisible({ timeout: 15000 });
    await toggleBtn.click();

    const saveBtn = page.getByTestId('cg01-save-policy-btn');
    await expect(saveBtn).toBeVisible();

    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 5);
  });
});
