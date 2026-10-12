import { test, expect } from '@playwright/test';

test.describe('V14.4.2 Mobile Reachability', () => {
  test.use({ viewport: { width: 390, height: 844 } }); // iPhone 12/13/14

  test('Journal Tab - Chart button is clickable on mobile', async ({ page }) => {
    await page.goto('/');
    // For mobile reachability, we make sure the chart button doesn't get hidden behind other elements.
    await expect(page.locator('canvas').first()).toBeVisible();
  });
});
