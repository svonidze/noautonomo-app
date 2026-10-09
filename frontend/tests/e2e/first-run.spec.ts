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
import taxes from '../../src/locales/ru/taxes.json' with { type: 'json' };

// The empty-ledger project (playwright.config.mts) serves a ledger straight from `db init`: no
// quarter, no record, no profile. The server offers the current quarter and the one before it
// without creating them. The tests share that ledger and run in file order: the read-only ones
// first, then the two that write to it. The @previous-quarter test runs alone on a second fresh
// ledger (project empty-ledger-previous): its document would end the offer of the current quarter.
test.describe.configure({ mode: 'serial' });
const today = new Date();
const quarter = `${today.getFullYear()}-Q${Math.floor(today.getMonth() / 3) + 1}`;
/** Console and page errors, plus any request that would ask for the period `null`. */
function consoleErrors(page: Page) {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (request) => {
    if (request.url().includes('period=null')) errors.push(`request ${request.url()}`);
  });
  return errors;
}

test('a new ledger opens on four first steps, without charts or console errors', async ({
  page,
}) => {
  const errors = consoleErrors(page);
  await page.goto('/');
  await expect(page).toHaveURL(`/dashboard?period=${quarter}`);
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
    ['/settings', null, `/review?period=${quarter}`, `/taxes?period=${quarter}`],
  );
  // Below the data threshold no chart draws: the first screen is the start list, not charts.
  await expect(page.locator('svg.chart-svg')).toHaveCount(0);
  await expect(page.locator('.primary-button:visible')).toHaveCount(1);
  // The header button and the document step both open intake for the offered quarter.
  const dialog = page.locator('#vue-intake-dialog');
  for (const opener of [page.locator('#new-entry-button'), rows.nth(1)]) {
    await opener.click();
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('[name="period"]')).toHaveValue(quarter);
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  }
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
      `/dashboard?period=${quarter}`,
      '.dashboard-grid .empty-state',
      [dashboard['dashboard.recentEmpty']],
    ],
    [
      `/income?period=${quarter}`,
      '#transactions-table .empty-state',
      [transactions['transactions.incomeEmpty']],
    ],
    // Purchases come from documents, depreciation from the equipment schedule.
    [
      `/expenses?period=${quarter}`,
      '#expense-results .empty-state',
      [expense['expense.empty'], expense['expense.amortizationEmpty']],
    ],
    [
      `/review?period=${quarter}`,
      '.review-queue-table .empty-state',
      [review['review.queueEmpty.noDocuments']],
    ],
    [
      `/review?period=${quarter}&tab=posting`,
      '.posting-action-hint',
      [review['review.postingEmpty']],
    ],
    [
      `/review?period=${quarter}&tab=documents`,
      '#review-tabpanel .empty-state',
      [review['review.documentsEmpty'], issues['issues.none']],
    ],
    [
      `/assets?period=${quarter}`,
      '.asset-explanations-table .empty-state',
      [assets['assets.listEmpty']],
    ],
    [`/taxes?period=${quarter}`, 'section.reta-check h3 + p', [reta['reta.basesEmpty']]],
    ['/contacts', '#contacts-list-wrap .empty-state', [contacts['contacts.listEmpty']]],
    ['/aeat-documents', '.aeat-documents-table .empty-state', [aeat['aeatDocuments.listEmpty']]],
    ['/settings', '#settings-profile-picker p', [settings['settings.form.empty']]],
  ];
  for (const [route, selector, texts] of screens) {
    await page.goto(route);
    await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
    await expect(page.locator(selector), route).toHaveText(texts);
  }
  // The posting module previews the absent quarter itself, timestamp included.
  await page.goto(`/review?period=${quarter}&tab=posting`);
  await expect(page.locator('#app')).toContainText(
    review['review.postingGeneratedAt'].replace(' {date}', ''),
  );
  await expect(page.locator('#app')).not.toContainText(review['review.postingGeneratedUnknown']);
  expect(errors).toEqual([]);
});

test('Calculate on a quarter without records says what to do instead of failing', async ({
  page,
}) => {
  const errors = consoleErrors(page);
  await page.goto(`/dashboard?period=${quarter}`);
  await page.locator('.metric .calculate-button').click();
  await expect(page.locator('#toast-message')).toHaveText(taxes['taxes.periodEmpty']);
  // The refused request is the only console line: the browser logs the 409 itself.
  expect(errors).toEqual([expect.stringMatching(/status of 409 \(Conflict\)$/)]);
});

// Writes to the shared ledger: it saves a profile.
test('a saved profile marks the first step done and names the owner in the sidebar', async ({
  page,
}) => {
  const errors = consoleErrors(page);
  await page.goto(`/dashboard?period=${quarter}`);
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

// Writes to the shared ledger, last: it adds the first document and so creates the quarter.
test('the first document opens its quarter and moves the start list on to its review', async ({
  page,
}) => {
  const errors = consoleErrors(page);
  const day = [today.getFullYear(), today.getMonth() + 1, today.getDate()]
    .map((part) => String(part).padStart(2, '0'))
    .join('-');
  await page.goto(`/dashboard?period=${quarter}`);
  await page.locator('#new-entry-button').click();
  const form = page.locator('#vue-intake-form');
  await page.locator('#vue-intake-file').setInputFiles({
    name: 'synthetic-first-invoice.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from(`Synthetic invoice SYN-FIRST-1 dated ${day}. Total EUR 121.00.`),
  });
  await form.locator('[name="issued_on"]').fill(day);
  await form.locator('[name="document_number"]').fill('SYN-FIRST-1');
  await form.locator('[name="counterparty_name"]').fill('Synthetic Supplier');
  await form.locator('[name="gross"]').fill('121.00');
  await page.locator('#vue-submit-intake').click();
  // An expense opens its review: intake created the quarter and its first transaction.
  await expect(page).toHaveURL(new RegExp(`/review/[0-9a-f-]{36}\\?period=${quarter}$`));
  await page.locator('a[data-view="dashboard"]').click();
  const tags = page.locator('.task-row .badge');
  await expect(tags.nth(1)).toHaveText(statuses['statuses.steps.done']);
  await expect(tags.nth(2)).toHaveText(statuses['statuses.steps.canStart']);
  expect(errors).toEqual([]);
});

test(
  'a first document dated in the quarter that just ended goes to that quarter',
  { tag: '@previous-quarter' },
  async ({ page }) => {
    const errors = consoleErrors(page);
    // The last day of the quarter before the current one.
    const last = new Date(today.getFullYear(), Math.floor(today.getMonth() / 3) * 3, 0);
    const previous = `${last.getFullYear()}-Q${Math.floor(last.getMonth() / 3) + 1}`;
    const day = [last.getFullYear(), last.getMonth() + 1, last.getDate()]
      .map((part) => String(part).padStart(2, '0'))
      .join('-');
    await page.goto('/');
    await expect(page).toHaveURL(`/dashboard?period=${quarter}`);
    const select = page.locator('#period-select');
    expect(
      await select
        .locator('option')
        .evaluateAll((nodes) => nodes.map((node) => (node as HTMLOptionElement).value)),
    ).toEqual([quarter, previous]);
    await select.selectOption(previous);
    await expect(page).toHaveURL(`/dashboard?period=${previous}`);
    await page.locator('#new-entry-button').click();
    const form = page.locator('#vue-intake-form');
    await expect(form.locator('[name="period"]')).toHaveValue(previous);
    await page.locator('#vue-intake-file').setInputFiles({
      name: 'synthetic-previous-invoice.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from(`Synthetic invoice SYN-PREV-1 dated ${day}. Total EUR 121.00.`),
    });
    await form.locator('[name="issued_on"]').fill(day);
    await form.locator('[name="document_number"]').fill('SYN-PREV-1');
    await form.locator('[name="counterparty_name"]').fill('Synthetic Supplier');
    await form.locator('[name="gross"]').fill('121.00');
    await page.locator('#vue-submit-intake').click();
    await expect(page).toHaveURL(new RegExp(`/review/[0-9a-f-]{36}\\?period=${previous}$`));
    expect(errors).toEqual([]);
  },
);
