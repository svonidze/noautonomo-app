import { test, expect, type Page } from '@playwright/test';
import { nativeFixture, REVIEW_ID as ID, DOCUMENT_ID as DOC } from '../fixtures/review.ts';
import analyticsFixture from '../fixtures/analytics.json' with { type: 'json' };
import nav from '../../src/locales/ru/nav.json' with { type: 'json' };
import charts from '../../src/locales/ru/charts.json' with { type: 'json' };

const PHONE = { width: 375, height: 812 };
const overflows = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
const visibleCharts = (page: Page) => page.locator('svg.chart-svg:visible').count();

/** The expense review of the shared fixture: the route answers from the fixture, not the ledger. */
async function expenseReview(page: Page) {
  await page.route(`**/api/transactions/${ID}`, (route) =>
    route.fulfill({
      json: {
        transaction: {
          transaction_id: ID,
          entry_type: 'expense',
          lifecycle_status: 'needs_review',
          currency: 'EUR',
        },
        period: { period_key: '2026-Q3' },
      },
    }),
  );
  await page.route(`**/api/document/${DOC}/preview`, (route) =>
    route.fulfill({ contentType: 'text/plain', body: 'Synthetic document' }),
  );
  await page.route(`**/api/expense-workflows/${ID}`, (route) =>
    route.fulfill({ json: nativeFixture() }),
  );
}

test('on a phone each screen opens under a short header, folds its menu and never scrolls sideways', async ({
  page,
}) => {
  await expenseReview(page);
  await page.setViewportSize(PHONE);
  const screens: [string, string][] = [
    ['/dashboard?period=2026-Q3', nav['nav.dashboard']],
    ['/review?period=2026-Q3', nav['nav.review']],
    ['/expenses?period=2026-Q3', nav['nav.expenses']],
    ['/taxes?period=2026-Q3', nav['nav.taxes']],
    [`/review/${ID}?period=2026-Q3`, nav['nav.review']],
  ];
  for (const [route, section] of screens) {
    await page.goto(route);
    await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
    const header = await page.locator('.sidebar').boundingBox();
    expect(header!.height, route).toBeLessThanOrEqual(112);
    await expect(page.locator('.nav-current'), route).toHaveText(section);
    await expect(page.locator('.nav-toggle'), route).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#primary-nav'), route).toBeHidden();
    expect(await overflows(page), route).toBe(false);
    expect(await visibleCharts(page), route).toBe(0);
  }
});

test('the phone menu opens and closes from the keyboard and closes on a route change', async ({
  page,
}) => {
  await page.setViewportSize(PHONE);
  await page.goto('/dashboard?period=2026-Q3');
  const toggle = page.locator('.nav-toggle'),
    menu = page.locator('#primary-nav');
  await expect(toggle).toHaveAttribute('aria-controls', 'primary-nav');
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(menu).toBeVisible();
  await expect(menu.locator('a[data-view][data-spa]')).toHaveCount(9);
  await page.keyboard.press('Tab');
  await expect(menu.locator('a[data-view="dashboard"]')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(toggle).toBeFocused();
  // Open, the menu starts with the brand name and the owner; folded, they stay in the
  // accessibility tree without a visible box.
  const brand = page.locator('.brand > span:last-child');
  expect(await brand.evaluate((node) => node.getBoundingClientRect().width)).toBeLessThanOrEqual(1);
  await expect(page.getByRole('complementary')).toContainText('Autónomo');
  await page.keyboard.press('Enter');
  const [name, links] = [(await brand.boundingBox())!, (await menu.boundingBox())!];
  expect(name.height).toBeGreaterThan(20);
  expect(name.y + name.height).toBeLessThanOrEqual(links.y);
  await expect(page.locator('#profile-name')).toBeVisible();
  await menu.locator('a[data-view="review"]').click();
  await expect(page).toHaveURL(/\/review\?period=2026-Q3$/);
  await expect(menu).toBeHidden();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('.nav-current')).toHaveText(nav['nav.review']);
  // A route change from outside the menu closes it too: Back returns to the overview.
  await toggle.click();
  await expect(menu).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/dashboard\?period=2026-Q3$/);
  await expect(menu).toBeHidden();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(await overflows(page)).toBe(false);
});

test('without a section to name, the phone bar keeps «Меню» at its end', async ({ page }) => {
  await page.setViewportSize(PHONE);
  await page.goto('/dashboard?period=2026-Q3');
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  await page.evaluate(() => {
    history.pushState(null, '', '/nowhere');
    dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(page.locator('.nav-current')).toHaveCount(0);
  const [bar, toggle] = [
    (await page.locator('.sidebar').boundingBox())!,
    (await page.locator('.nav-toggle').boundingBox())!,
  ];
  expect(toggle.width).toBeLessThan(120);
  expect(Math.abs(bar.x + bar.width - 12 - (toggle.x + toggle.width))).toBeLessThanOrEqual(1);
});

test('on a phone a chart opens as its data table and draws only on request', async ({ page }) => {
  const data = structuredClone(analyticsFixture);
  // Four quarters of one year: the widest axis labels a phone chart has to fit.
  data.datasets.quarterly_tax_due.points = [1, 2, 3, 4].map((quarter) => ({
    period_key: `2026-Q${quarter}`,
    modelo130: { result_minor: quarter * 10000, payable_minor: quarter * 10000, source: 'filed' },
    modelo303: { result_minor: quarter * 5000, payable_minor: quarter * 5000, source: 'filed' },
  }));
  await page.route('**/api/analytics?**', (route) => route.fulfill({ json: data }));
  await page.setViewportSize(PHONE);
  await page.goto('/dashboard?period=2026-Q3');
  await page.locator('details.analytics-details > summary').click();
  const chart = page.locator('#chart-tax-due');
  await expect(chart.locator('details.chart-data')).toHaveAttribute('open', '');
  await expect(chart.locator('details.chart-data tbody tr')).toHaveCount(4);
  const plot = chart.locator('details.chart-plot');
  await expect(plot).not.toHaveAttribute('open');
  await expect(plot.locator(':scope > summary')).toHaveText(charts['charts.showChart']);
  expect(await visibleCharts(page)).toBe(0);
  await plot.locator(':scope > summary').click();
  await expect(chart.locator('svg.chart-svg')).toBeVisible();
  await expect(plot.locator(':scope > summary')).toHaveText(charts['charts.hideChart']);
  // The quarter labels under the bars stand apart.
  const labels = await chart
    .locator('svg.chart-svg text.chart-axis-text[text-anchor="middle"]')
    .evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().toJSON()));
  expect(labels).toHaveLength(4);
  for (let index = 1; index < labels.length; index++)
    expect(labels[index].left).toBeGreaterThanOrEqual(labels[index - 1].right);
  expect(await overflows(page)).toBe(false);
});

test('on a wide screen the AEAT filters stay inside their panel and short headers stay whole', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/aeat-documents');
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  const panel = (await page.locator('.aeat-filter-grid').locator('xpath=..').boundingBox())!;
  const fields = await page
    .locator('.aeat-filter-grid :is(input, select)')
    .evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().toJSON()));
  expect(fields).toHaveLength(5);
  for (const field of fields) {
    expect(field.left).toBeGreaterThanOrEqual(panel.x + 8);
    expect(field.right).toBeLessThanOrEqual(panel.x + panel.width - 8);
  }
  const lines = (cell: HTMLElement) => {
    const range = document.createRange();
    range.selectNodeContents(cell);
    return new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size;
  };
  await expect(page.locator('.nav-toggle')).toBeHidden();
  await expect(page.locator('#primary-nav')).toBeVisible();
  expect(
    await page.locator('.aeat-documents-table th', { hasText: 'Modelo' }).evaluate(lines),
  ).toBe(1);
  // The queue's action column fits its one control on one line.
  await page.goto('/review?period=2026-Q3');
  const actions = page.locator('.review-queue-table td.table-actions > *');
  await expect(actions).toHaveCount(2);
  for (const control of await actions.all()) expect(await control.evaluate(lines)).toBe(1);
});
