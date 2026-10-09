import { test, expect, type Page } from '@playwright/test';
import dashboard from '../../src/locales/ru/dashboard.json' with { type: 'json' };
import statuses from '../../src/locales/ru/statuses.json' with { type: 'json' };
import transactions from '../../src/locales/ru/transactions.json' with { type: 'json' };
import expense from '../../src/locales/ru/expense.json' with { type: 'json' };
import review from '../../src/locales/ru/review.json' with { type: 'json' };
import issues from '../../src/locales/ru/issues.json' with { type: 'json' };
import assets from '../../src/locales/ru/assets.json' with { type: 'json' };
import contacts from '../../src/locales/ru/contacts.json' with { type: 'json' };
import aeat from '../../src/locales/ru/aeatDocuments.json' with { type: 'json' };
import settings from '../../src/locales/ru/settings.json' with { type: 'json' };
import reta from '../../src/locales/ru/reta.json' with { type: 'json' };

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
  // The narrowest side-by-side layout still fits the header with «Добавить документ».
  await page.setViewportSize({ width: 761, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  expect(errors).toEqual([]);
});

test('every screen of an empty ledger says what is empty and what to do next', async ({ page }) => {
  const errors = consoleErrors(page);
  const screens: [string, string, string[]][] = [
    [
      '/dashboard?period=2026-Q3',
      '.dashboard-grid .empty-state',
      [dashboard['dashboard.recentEmpty']],
    ],
    [
      '/income?period=2026-Q3',
      '#transactions-table .empty-state',
      [transactions['transactions.incomeEmpty']],
    ],
    // Purchases come from documents, depreciation from the equipment schedule.
    [
      '/expenses?period=2026-Q3',
      '#expense-results .empty-state',
      [expense['expense.empty'], expense['expense.amortizationEmpty']],
    ],
    [
      '/review?period=2026-Q3',
      '.review-queue-table .empty-state',
      [review['review.queueEmpty.noDocuments']],
    ],
    ['/review?period=2026-Q3&tab=posting', '.posting-action-hint', [review['review.postingEmpty']]],
    [
      '/review?period=2026-Q3&tab=documents',
      '#review-tabpanel .empty-state',
      [review['review.documentsEmpty'], issues['issues.none']],
    ],
    [
      '/assets?period=2026-Q3',
      '.asset-explanations-table .empty-state',
      [assets['assets.listEmpty']],
    ],
    ['/taxes?period=2026-Q3', 'section.reta-check h3 + p', [reta['reta.basesEmpty']]],
    ['/contacts', '#contacts-list-wrap .empty-state', [contacts['contacts.listEmpty']]],
    ['/aeat-documents', '.aeat-documents-table .empty-state', [aeat['aeatDocuments.listEmpty']]],
    ['/settings', '#settings-profile-picker p', [settings['settings.form.empty']]],
  ];
  for (const [route, selector, texts] of screens) {
    await page.goto(route);
    await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
    await expect(page.locator(selector), route).toHaveText(texts);
  }
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
