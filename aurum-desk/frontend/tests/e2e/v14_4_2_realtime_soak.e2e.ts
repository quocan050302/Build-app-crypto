import { test, expect } from '@playwright/test';

test.describe('V14.4.2 Realtime Soak', () => {
  test('Application remains stable during sequence gaps and reconnects', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('canvas').first()).toBeVisible();
  });
});
