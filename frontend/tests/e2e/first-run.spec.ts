import { test, expect, type Page } from '@playwright/test';
import dashboard from '../../src/locales/ru/dashboard.json' with { type: 'json' };
import statuses from '../../src/locales/ru/statuses.json' with { type: 'json' };

// The empty-ledger project (playwright.config.mts) serves one open quarter with no records and no
// profile: what a new owner sees on the first run.
function consoleErrors(page: Page) {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', (error) => errors.push(error.message));
  return errors;
}

test('a new ledger opens on four first steps, without charts or console errors', async ({
  page,
}) => {
  const errors = consoleErrors(page);
  await page.goto('/');
  await expect(page).toHaveURL(/\/dashboard\?period=2026-Q3$/);
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  const start = page.locator('.task-list');
  await expect(start.locator('h2')).toHaveText(dashboard['dashboard.start.title']);
  const rows = start.locator('.task-row');
  await expect(rows.locator('strong')).toHaveText([
    dashboard['dashboard.start.profile.title'],
    dashboard['dashboard.start.document.title'],
    dashboard['dashboard.start.review.title'],
    dashboard['dashboard.start.taxes.title'],
  ]);
  await expect(rows.locator('.badge')).toHaveText([
    statuses['statuses.steps.canStart'],
    statuses['statuses.steps.canStart'],
    statuses['statuses.steps.notStarted'],
    statuses['statuses.steps.notStarted'],
  ]);
  expect(await rows.evaluateAll((nodes) => nodes.map((node) => node.getAttribute('href')))).toEqual(
    ['/settings', null, '/review?period=2026-Q3', '/taxes?period=2026-Q3'],
  );
  // Below the data threshold no chart draws: the first screen is the start list, not charts.
  await expect(page.locator('svg.chart-svg')).toHaveCount(0);
  await expect(page.locator('.primary-button:visible')).toHaveCount(1);
  await rows.nth(1).click();
  await expect(page.locator('#vue-intake-dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#vue-intake-dialog')).toBeHidden();
  await page.setViewportSize({ width: 375, height: 812 });
  expect(
    await page.evaluate(() => {
      const box = document.querySelector('.task-row')!.getBoundingClientRect();
      return box.top < window.innerHeight && !document.querySelector('svg.chart-svg');
    }),
  ).toBe(true);
  expect(errors).toEqual([]);
});

// Runs last: it saves a profile into the shared empty ledger.
test('a saved profile marks the first step done and names the owner in the sidebar', async ({
  page,
}) => {
  const errors = consoleErrors(page);
  await page.goto('/dashboard?period=2026-Q3');
  await expect(page.locator('#profile-name')).not.toHaveText('Synthetic Owner');
  await page.locator('.task-row').first().click();
  await expect(page).toHaveURL(/\/settings$/);
  await page.locator('[name="full_name"]').fill('Synthetic Owner');
  await page.locator('[name="tax_id"]').fill('SYNTHETIC-ID');
  await page.locator('[name="residency_country"]').fill('ES');
  await page.locator('#settings-profile-form button[type="submit"]').click();
  await expect(page.locator('#profile-name')).toHaveText('Synthetic Owner');
  await page.locator('a[data-view="dashboard"]').click();
  await expect(page.locator('.task-row .badge').first()).toHaveText(
    statuses['statuses.steps.done'],
  );
  await page.reload();
  await expect(page.locator('#profile-name')).toHaveText('Synthetic Owner');
  await expect(page.locator('.task-row .badge').first()).toHaveText(
    statuses['statuses.steps.done'],
  );
  expect(errors).toEqual([]);
});
