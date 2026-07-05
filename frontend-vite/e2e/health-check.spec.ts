/**
 * Playwright E2E test — Health Check.
 *
 * Verifies that the application loads correctly in the browser,
 * the header is visible, and the health status from the dashboard
 * is displayed.
 */

import { test, expect } from '@playwright/test';

test.describe('Health Check', () => {
  test('navigates to the home page and verifies the header is visible', async ({ page }) => {
    await page.goto('/');

    // Verify the header contains the expected title.
    const header = page.locator('header');
    await expect(header).toBeVisible();

    await expect(page.locator('h1')).toContainText('Medical Handwriting OCR');
  });

  test('displays health status on the dashboard', async ({ page }) => {
    await page.goto('/');

    // The dashboard should be visible with its heading.
    await expect(page.locator('text=System Dashboard')).toBeVisible();

    // Wait for the health check to complete and display the status badge.
    // The backend may take a moment, so we wait with a generous timeout.
    await expect(page.locator('.status-badge')).toBeVisible({ timeout: 15_000 });
  });
});
