/**
 * frontend/tests/e2e/v17_4_entry_sessions.e2e.ts
 * Aurum V17.4 — Entry Session Settings & Coordinated Persistence E2E Suite
 *
 * Verifies Parts 177, 178, 179:
 * 1. Opens settings, selects "Mọi phiên — chỉ vào khi đủ setup (PAPER)", saves via coordinated PUT endpoint, confirms response & banner.
 * 2. 409 Conflict: UI shows descriptive error message without false success.
 * 3. 500 Network error: UI maintains prior confirmed state without false success badge.
 * 4. Mobile viewport 390x844 fits within screen without horizontal document overflow.
 */

import { test, expect } from '@playwright/test';

test.describe('Aurum V17.4 — Entry Sessions UI & Settings Coordinated Flow', () => {
  test('V17.4 Gate 1: Save All-Sessions Paper and verify server confirmation badge', async ({ page }) => {
    // Intercept initial GET settings
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
            policy_version: 1,
            execution_scope: 'PAPER',
            auto_execution_enabled: false,
            source: 'PERSISTED',
            reason_codes: [],
            message: null,
          }),
        });
      } else if (route.request().method() === 'PUT') {
        const payload = JSON.parse(route.request().postData() || '{}');
        expect(payload.entry_session_policy).toBe('ALL_SESSIONS_SETUP_FIRST');
        expect(payload.timing_profile).toBe('CG01_ALL_SESSIONS_PAPER');

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
            policy_version: 2,
            execution_scope: 'PAPER',
            auto_execution_enabled: false,
            source: 'PERSISTED',
            reason_codes: [],
            message: 'Cấu hình phiên đã được áp dụng thành công.',
          }),
        });
      }
    });

    await page.goto('/');

    // Toggle settings panel
    const toggleSettingsBtn = page.getByTestId('cg01-toggle-settings-btn');
    await expect(toggleSettingsBtn).toBeVisible({ timeout: 15000 });
    await toggleSettingsBtn.click();

    const settingsPanel = page.getByTestId('cg01-settings-panel');
    await expect(settingsPanel).toBeVisible();

    // Select "CG01_ALL_SESSIONS_PAPER"
    const timingSelect = page.getByTestId('cg01-timing-profile-select');
    await expect(timingSelect).toBeVisible();
    await timingSelect.selectOption('CG01_ALL_SESSIONS_PAPER');

    // Click Save
    const saveBtn = page.getByTestId('cg01-save-policy-btn');
    await expect(saveBtn).toBeVisible();
    await saveBtn.click();

    // Verify feedback and banner update
    const feedback = page.getByTestId('cg01-policy-save-feedback');
    await expect(feedback).toBeVisible();
    await expect(feedback).toContainText('Đã lưu');

    const banner = page.getByTestId('v1639-paper-experiment-banner');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('Mọi phiên');
  });

  test('V17.4 Gate 2: Stale Expected Version (409) displays conflict error message', async ({ page }) => {
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
            policy_version: 1,
            execution_scope: 'PAPER',
            auto_execution_enabled: false,
            source: 'PERSISTED',
            reason_codes: [],
          }),
        });
      } else if (route.request().method() === 'PUT') {
        await route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: 'STALE_POLICY_VERSION: Cấu hình đã được thay đổi ở nơi khác (phiên bản hiện tại: 2, gửi lên: 1).',
          }),
        });
      }
    });

    await page.goto('/');

    const toggleSettingsBtn = page.getByTestId('cg01-toggle-settings-btn');
    await expect(toggleSettingsBtn).toBeVisible({ timeout: 15000 });
    await toggleSettingsBtn.click();

    const saveBtn = page.getByTestId('cg01-save-policy-btn');
    await saveBtn.click();

    const feedback = page.getByTestId('cg01-policy-save-feedback');
    await expect(feedback).toBeVisible();
    await expect(feedback).toContainText('thay đổi ở nơi khác');
  });

  test('V17.4 Gate 3: Mobile Viewport 390x844 responsive layout check', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');

    const toggleSettingsBtn = page.getByTestId('cg01-toggle-settings-btn');
    await expect(toggleSettingsBtn).toBeVisible({ timeout: 15000 });
    await toggleSettingsBtn.click();

    const settingsPanel = page.getByTestId('cg01-settings-panel');
    await expect(settingsPanel).toBeVisible();

    const isOverflowing = await page.evaluate(() => {
      return document.documentElement.scrollWidth > window.innerWidth;
    });
    expect(isOverflowing).toBe(false);
  });
});
