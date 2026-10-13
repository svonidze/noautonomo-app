import { test, expect, type Page } from '@playwright/test';

const languages = ['en', 'es', 'ru'] as const;
const screens = {
  dashboard: '.metric-grid',
  income: '#transactions-table',
  expenses: '#expense-results',
  review: '#review-tabpanel',
  assets: '#asset-plan-select',
  taxes: '.tax-page',
  contacts: '#contacts-list-wrap',
  'aeat-documents': '.aeat-documents-page',
  settings: '#settings-profile-form',
} as const;

async function settled(page: Page) {
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#app [role=alert]')).toHaveCount(0);
}
async function open(page: Page, screen: string, language = 'en', query = '') {
  await page.goto(`/demo/?lang=${language}#/${screen}${query}`);
  await settled(page);
}

for (const language of languages) {
  for (const [screen, selector] of Object.entries(screens)) {
    test(`${language}: ${screen} is available through the real application navigation`, async ({
      page,
    }) => {
      await open(page, 'dashboard', language);
      await page.locator(`.primary-nav [data-view="${screen}"]`).click();
      await settled(page);
      await expect(page.locator('html')).toHaveAttribute('lang', language);
      await expect(page.locator(`.primary-nav [data-view="${screen}"]`)).toHaveClass(/active/);
      await expect(page.locator(selector)).toBeVisible();
      await expect(page.locator('.public-demo-banner')).toBeVisible();
      await expect(page.locator(`[data-locale="${language}"]`)).toHaveAttribute(
        'aria-pressed',
        'true',
      );
    });
  }
  test(`${language}: review tabs remain usable without enabling posting`, async ({ page }) => {
    await open(page, 'review', language);
    for (const tab of ['posting', 'documents', 'queue']) {
      await page.locator(`[data-review-tab="${tab}"]`).click();
      await settled(page);
      await expect(page.locator(`[data-review-tab="${tab}"]`)).toHaveAttribute(
        'aria-selected',
        'true',
      );
      if (tab === 'posting')
        await expect(page.locator('[data-posting-action="open-confirm"]')).toBeDisabled();
    }
  });
  test(`${language}: settings shows synthetic fields with saving disabled`, async ({ page }) => {
    await open(page, 'settings', language);
    await expect(page.locator('#settings-profile-form [name="full_name"]')).toBeDisabled();
    await expect(page.locator('#settings-profile-form button[type=submit]')).toBeDisabled();
    await expect(page.locator('#settings-locale')).toBeEnabled();
  });
  test(`${language}: mobile retains language controls and shared layout`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await open(page, 'dashboard', language);
    for (const locale of languages)
      await expect(page.locator(`[data-locale="${locale}"]`)).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });
}

for (const lang of [undefined, 'unknown']) {
  test(`demo defaults to English when lang is ${String(lang)}`, async ({ page }) => {
    await page.goto(lang ? `/demo/?lang=${lang}` : '/demo/');
    await settled(page);
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await expect(page.locator('[data-locale=en]')).toHaveAttribute('aria-pressed', 'true');
  });
}

test('switching language and reloading retains period and search', async ({ page }) => {
  await open(page, 'income', 'en', '?period=2026-Q1&q=Atlas');
  const route = new URL(page.url()).hash;
  await page.locator('[data-locale=es]').click();
  expect(new URL(page.url()).hash).toBe(route);
  expect(new URL(page.url()).searchParams.get('lang')).toBe('es');
  await page.reload();
  await settled(page);
  await expect(page.locator('#period-select')).toHaveValue('2026-Q1');
  await expect(page.locator('#transaction-search')).toHaveValue('Atlas');
  await expect(page.locator('html')).toHaveAttribute('lang', 'es');
});

test('language switching retains the open expense and its return context', async ({ page }) => {
  await open(page, 'expenses', 'en', '?period=2026-Q3');
  await page.locator('#expense-results a[href*="/expenses/"]').first().click();
  await settled(page);
  const route = new URL(page.url()).hash;
  await page.locator('[data-locale=ru]').click();
  expect(new URL(page.url()).hash).toBe(route);
  await page.reload();
  await settled(page);
  expect(new URL(page.url()).hash).toBe(route);
  await expect(page.locator('.review-workspace-header')).toBeVisible();
});

test('search supports empty results and recovery', async ({ page }) => {
  await open(page, 'income');
  const rows = page.locator('#transactions-table [data-transaction-id]');
  expect(await rows.count()).toBeGreaterThan(0);
  await page.locator('#transaction-search').fill('this-match-does-not-exist-987');
  await expect(rows).toHaveCount(0);
  await page.locator('#transaction-search').fill('');
  await expect(rows.first()).toBeVisible();
});

test('period selection updates the actual application route and survives reload', async ({
  page,
}) => {
  await open(page, 'dashboard');
  await page.locator('#period-select').selectOption('2026-Q1');
  await settled(page);
  await expect(page).toHaveURL(/period=2026-Q1/);
  await page.reload();
  await settled(page);
  await expect(page.locator('#period-select')).toHaveValue('2026-Q1');
});

test('income document links open the real backend PDF', async ({ page }) => {
  await open(page, 'income', 'es');
  const link = page.locator('#transactions-table a[target="_blank"]').first();
  await expect(link).toHaveAttribute('href', /^\/demo\/api\/document\//);
  const response = await page.request.get((await link.getAttribute('href'))!);
  expect(response.headers()['content-type']).toContain('application/pdf');
  expect((await response.body()).subarray(0, 5).toString()).toBe('%PDF-');
});

test('contact detail stays within the demo when opened in a new tab', async ({ page, context }) => {
  await open(page, 'contacts', 'ru');
  const href = await page.locator('[data-counterparty-row] a').first().getAttribute('href');
  expect(href).toMatch(/^\/demo\/\?lang=ru#\/contacts\//);
  const tab = await context.newPage();
  await tab.goto(href!);
  await settled(tab);
  await expect(tab.locator('#contact-identity')).toBeVisible();
  await expect(tab.locator('html')).toHaveAttribute('lang', 'ru');
  await tab.close();
});

test('all screens read the real demo backend without writes or browser storage changes', async ({
  page,
}) => {
  const unexpected: string[] = [],
    errors: string[] = [],
    reads: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/demo/api/')) reads.push(url.pathname);
    if (
      !['GET', 'HEAD'].includes(request.method()) ||
      url.origin !== 'http://127.0.0.1:8766' ||
      !/^\/demo\/(?:$|ui-assets\/|api\/)/.test(url.pathname)
    )
      unexpected.push(`${request.method()} ${request.url()}`);
  });
  await page.goto('/demo/');
  await settled(page);
  const before = await page.evaluate(() => ({
    local: { ...localStorage },
    session: { ...sessionStorage },
  }));
  for (const screen of Object.keys(screens)) {
    await page.locator(`.primary-nav [data-view="${screen}"]`).click();
    await settled(page);
  }
  for (const locale of languages) await page.locator(`[data-locale="${locale}"]`).click();
  expect(
    await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } })),
  ).toEqual(before);
  expect(unexpected).toEqual([]);
  expect(reads).toContain('/demo/api/bootstrap');
  expect(reads).toContain('/demo/api/dashboard');
  expect(reads).toContain('/demo/api/settings');
  expect(errors).toEqual([]);
});

test('direct form submission cannot write or change settings', async ({ page }) => {
  await open(page, 'settings');
  const before = await page.locator('#settings-profile-form [name="full_name"]').inputValue();
  await page.locator('#settings-profile-form').dispatchEvent('submit');
  await page.reload();
  await settled(page);
  await expect(page.locator('#settings-profile-form [name="full_name"]')).toHaveValue(before);
});

test('readonly CSP blocks external connections and the backend rejects write methods', async ({
  page,
  request,
}) => {
  await open(page, 'dashboard');
  const shell = await request.get('/demo/');
  expect(shell.headers()['content-security-policy']).toMatch(/connect-src 'self'(?:;|$)/);
  const result = await page.evaluate(async () => {
    try {
      await fetch('https://example.invalid/demo-probe', { method: 'POST', body: 'blocked' });
      return 'unexpected success';
    } catch {
      return 'blocked';
    }
  });
  expect(result).toBe('blocked');
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE']) {
    expect((await request.fetch('/demo/', { method })).status()).toBe(405);
    expect((await request.fetch('/demo/api/post', { method })).status()).toBe(405);
  }
  expect((await request.get('/demo/api/bootstrap')).status()).toBe(200);
  expect((await request.get('/demo/api/post')).status()).toBe(404);
});

for (const language of languages) {
  test(`${language}: expense review uses the real workflow with readonly synthetic document`, async ({
    page,
  }) => {
    const response = await page.request.get('/demo/api/transactions?period=2026-Q3&status=review');
    const rows = await response.json();
    const expense = rows.find((row: { entry_type: string }) => row.entry_type === 'expense');
    expect(expense).toBeTruthy();
    await open(page, `review/${expense.transaction_id}`, language, '?period=2026-Q3');
    await expect(page.locator('#wf-form')).toBeVisible();
    const preview = page.locator('iframe[src*="/demo/api/document/"]');
    await expect(preview).toBeVisible();
    const document = await page.request.get((await preview.getAttribute('src'))!);
    expect(document.status()).toBe(200);
    expect(document.headers()['content-type']).toContain('application/pdf');
    expect(document.headers()['content-security-policy'] || '').not.toContain(
      "frame-ancestors 'none'",
    );
    expect(document.headers()['x-frame-options']).not.toBe('DENY');
    expect((await document.body()).subarray(0, 5).toString()).toBe('%PDF-');
    await expect(page.locator('#wf-save')).toBeDisabled();
    await expect(page.locator('#wf-form [data-p="decision.document_valid"]')).toBeDisabled();
    await expect(page.locator('#wf-form button[type=submit]')).toBeDisabled();
    await expect(page.locator('input[type=file]')).toHaveCount(0);
  });
}

test('asset details expose the synthetic amortization schedule', async ({ page }) => {
  await open(page, 'assets');
  const asset = await page
    .locator('#asset-plan-select option[value]:not([value=""])')
    .first()
    .getAttribute('value');
  expect(asset).toBeTruthy();
  await page.locator('#asset-plan-select').selectOption(asset!);
  await expect(page.locator('#asset-plan-detail table')).toBeVisible();
});

test('chart dialog opens with the keyboard and returns focus on Escape', async ({ page }) => {
  await open(page, 'dashboard');
  const expand = page.locator('.chart-expand-button').first();
  await expand.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.chart-dialog[open]')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(expand).toBeFocused();
});

test('income copy actions remain visible but unavailable', async ({ page }) => {
  await open(page, 'income');
  const copy = page.locator('[data-copy-transaction-id]').first();
  await expect(copy).toBeVisible();
  await expect(copy).toBeDisabled();
  await expect(copy).toHaveAttribute('title', 'Unavailable in the read-only demo');
});

test('every source document uses the same one-page explanatory PDF', async ({ page, request }) => {
  await open(page, 'income');
  const links = page.locator('#transactions-table a[target="_blank"]');
  expect(await links.count()).toBeGreaterThan(1);
  const first = await request.get((await links.nth(0).getAttribute('href'))!);
  const second = await request.get((await links.nth(1).getAttribute('href'))!);
  expect(first.status()).toBe(200);
  expect(first.headers()['content-type']).toContain('application/pdf');
  const bytes = await first.body();
  expect(bytes.subarray(0, 5).toString()).toBe('%PDF-');
  expect(bytes.toString('latin1').match(/\/Type \/Page\b/g)).toHaveLength(1);
  expect(bytes.toString('latin1')).toContain('NoAutonomo.ai demo document');
  expect(await second.body()).toEqual(bytes);
});

test('the UI reads stored English data from the real backend under all languages', async ({
  page,
  request,
}) => {
  const response = await request.get('/demo/api/transactions?period=2026-Q3&entry_type=income');
  expect(response.status()).toBe(200);
  const rows = await response.json();
  const record = rows[0];
  expect(record.counterparty_name).toBeTruthy();
  await open(page, 'income');
  for (const language of languages) {
    await page.locator(`[data-locale="${language}"]`).click();
    await expect(page.locator(`[data-transaction-id="${record.transaction_id}"]`)).toContainText(
      record.counterparty_name,
    );
  }
});

test('language switching updates the heading on the same route', async ({ page }) => {
  await open(page, 'dashboard', 'en', '?period=2026-Q1');
  const route = new URL(page.url()).hash;
  for (const [language, heading] of [
    ['es', 'Resumen'],
    ['ru', 'Обзор'],
    ['en', 'Overview'],
  ]) {
    await page.locator(`[data-locale="${language}"]`).click();
    await expect(page.locator('#page-title')).toHaveText(heading);
    expect(new URL(page.url()).hash).toBe(route);
  }
});

test('browser back restores the language and route from the previous navigation', async ({
  page,
}) => {
  await open(page, 'income', 'en', '?period=2026-Q1&q=Atlas');
  const route = new URL(page.url()).hash;
  await page.locator('.primary-nav [data-view="expenses"]').click();
  await settled(page);
  await page.locator('[data-locale=es]').click();
  await page.goBack();
  await settled(page);
  expect(new URL(page.url()).hash).toBe(route);
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.locator('#transaction-search')).toHaveValue('Atlas');
});
