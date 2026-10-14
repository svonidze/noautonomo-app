import { test, expect } from '@playwright/test';
import intake from '../../src/locales/ru/intake.json' with { type: 'json' };
import routes from '../../src/locales/ru/routes.json' with { type: 'json' };
import common from '../../src/locales/ru/common.json' with { type: 'json' };

test('real backend serves routes, scripts and guarded history under CSP', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => {
    (window as any).__cspViolations = [];
    document.addEventListener('securitypolicyviolation', (event) => {
      (window as any).__cspViolations.push(event.violatedDirective);
    });
  });
  const response = await page.goto('/expenses?period=2026-Q3');
  expect(response?.headers()['content-security-policy']).toContain("script-src 'self'");
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#app')).toContainText('Example expense');
  const globals = await page.evaluate(() =>
    [
      'AccountingHelp',
      'AutonomoCharts',
      'ExpenseWorkflow',
      'AutonomoSettings',
      'AutonomoCore',
      'AutonomoViews',
      '__AUTONOMO_WEB_UI_TEST_HOOKS__',
    ].some((name) => name in window),
  );
  expect(globals).toBe(false);
  // The fixture's reviewed USD expense has a reason, so its status links to the explanation.
  const help = page
    .locator('#app tr')
    .filter({ hasText: 'Example USD expense' })
    .locator('[data-status-help]');
  await help.click();
  await expect(page.locator('#status-help-dialog')).toBeVisible();
  await page.goBack();
  await expect(page.locator('#status-help-dialog')).not.toBeVisible();
  await expect(page).toHaveURL(/\/expenses\?period=2026-Q3$/);
  await page.goForward();
  await expect(page.locator('#status-help-dialog')).toBeVisible();
  await page.goBack();
  await page.locator('nav a[data-view="income"]').click();
  await expect(page.locator('#app')).toContainText('Example income');
  await page.goBack();
  await expect(page.locator('#app')).toContainText('Example expense');
  await page.reload();
  await expect(page.locator('#app')).toContainText('Example expense');
  expect(await page.evaluate(() => (window as any).__cspViolations)).toEqual([]);
  expect(errors).toEqual([]);
});

test('intake keeps selected file and typed fields across a locale change', async ({ page }) => {
  await page.goto('/income?period=2026-Q3');
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  await page.locator('#new-entry-button').click();
  const dialog = page.locator('#vue-intake-dialog');
  await expect(dialog).toBeVisible();
  await dialog.locator('[name="document_number"]').fill('SYNTHETIC-UI-1');
  await dialog.locator('input[type="file"]').setInputFiles({
    name: 'synthetic.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('Synthetic browser test document'),
  });
  // The blocking dialog makes the background switch unreachable to a normal click.
  // Invoke its real registered handler to cover an asynchronous locale change.
  await page
    .locator('[data-locale="en"]')
    .first()
    .evaluate((element: HTMLElement) => element.click());
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('[name="document_number"]')).toHaveValue('SYNTHETIC-UI-1');
  expect(
    await dialog
      .locator('input[type="file"]')
      .evaluate((element: HTMLInputElement) => element.files?.[0]?.name),
  ).toBe('synthetic.txt');
});

test('unknown routes and missing resources never masquerade as a working page', async ({
  request,
}) => {
  await request.get('/'); // Acquire the same session cookie as a browser page.
  expect((await request.get('/does-not-exist')).status()).toBe(404);
  expect((await request.get('/missing-script.js')).status()).toBe(404);
});

test('AEAT document registry is global, searchable and responsive', async ({ page }) => {
  await page.goto('/aeat-documents');
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#page-title')).toHaveText('Документы AEAT');
  await expect(page.locator('.period-control')).toBeHidden();
  await expect(page.locator('.aeat-documents-table')).toContainText('Synthetic ROI registration');
  await page.locator('input[type="search"]').fill('036');
  await expect(page.locator('.aeat-documents-table tbody tr')).toHaveCount(1);
  await page.locator('.aeat-upload-panel summary').click();
  await expect(page.locator('.aeat-upload-panel input[type="file"]')).toBeVisible();
  await page.setViewportSize({ width: 375, height: 800 });
  await expect(page.locator('.aeat-documents-table thead')).toBeHidden();
  await expect(page.locator('.aeat-documents-table tbody tr').first()).toBeVisible();
});

test('every screen states its purpose and offers exactly one primary action', async ({ page }) => {
  const routes = [
    '/dashboard?period=2026-Q3',
    '/income?period=2026-Q3',
    '/expenses?period=2026-Q3',
    '/review?period=2026-Q3',
    '/review?period=2026-Q3&tab=posting',
    '/assets?period=2026-Q3',
    '/taxes?period=2026-Q3',
    '/contacts',
    '/aeat-documents',
    '/settings',
  ];
  const seen = [];
  for (const route of routes) {
    await page.goto(route);
    await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
    await expect(page.locator('dialog[open]')).toHaveCount(0);
    const lead = (await page.locator('#page-lead').allTextContents()).join('').trim();
    seen.push({
      route,
      primaries: await page.locator('.primary-button:visible').count(),
      lead: !!lead,
    });
  }
  expect(seen).toEqual(routes.map((route) => ({ route, primaries: 1, lead: true })));
});

test('an unknown address inside the app offers one way back, not the add button too', async ({
  page,
}) => {
  await page.goto('/dashboard?period=2026-Q3');
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  // Only the router can reach an unknown path: the server answers 404 for a typed one.
  await page.evaluate(() => {
    history.pushState(null, '', '/nowhere');
    dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(page.locator('#app')).toContainText(routes['routes.unknownTitle']);
  await expect(page.locator('.new-entry')).toBeHidden();
  await expect(page.locator('.primary-button:visible')).toHaveText([
    routes['routes.backToDashboard'],
  ]);
});

test('opening the AEAT upload hands the primary action to saving and closing takes it back', async ({
  page,
}) => {
  await page.goto('/aeat-documents');
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  const upload = page.locator('.aeat-upload-panel'),
    summary = upload.locator(':scope > summary');
  await expect(summary).toHaveClass(/primary-button/);
  await summary.click();
  await expect(upload).toHaveAttribute('open', '');
  await expect(summary).not.toHaveClass(/primary-button/);
  const primary = page.locator('.primary-button:visible');
  await expect(primary).toHaveCount(1);
  await expect(primary).toHaveAttribute('type', 'submit');
  // Saving waits for a preview of the chosen PDF.
  await expect(primary).toBeDisabled();
  await summary.click();
  await expect(summary).toHaveClass(/primary-button/);
  await expect(page.locator('.primary-button:visible')).toHaveCount(1);
});

test('a failed calculation says why and lets the owner try again', async ({ page }) => {
  let calls = 0;
  await page.route('**/api/dashboard/refresh', (route) => {
    calls++;
    return route.fulfill({ status: 503, json: { error: 'Synthetic calculation failure' } });
  });
  await page.goto('/dashboard?period=2026-Q3');
  const calculate = page.locator('.metric .calculate-button').first();
  await calculate.click();
  await expect(page.locator('#toast')).toHaveClass(/error/);
  await expect(page.locator('#toast-message')).toHaveText('Synthetic calculation failure');
  // On the dark toast the close button rings in its own white text, not the page's green.
  await page.keyboard.press('Tab');
  const close = page.locator('#toast-close');
  await close.focus();
  expect(
    await close.evaluate((node) => [
      node.matches(':focus-visible'),
      getComputedStyle(node).outlineColor,
    ]),
  ).toEqual([true, 'rgb(255, 255, 255)']);
  await expect(calculate).toBeEnabled();
  await expect(calculate).toHaveText(common['common.calculate']);
  await calculate.click();
  await expect.poll(() => calls).toBe(2);
});

test('a disabled add button says why in visible text', async ({ page }) => {
  await page.route('**/api/bootstrap', async (route) => {
    const data = await (await route.fetch()).json();
    await route.fulfill({ json: { ...data, intake_enabled: false } });
  });
  await page.goto('/income?period=2026-Q3');
  const add = page.locator('#new-entry-button');
  await expect(add).toBeDisabled();
  await expect(add).not.toHaveAttribute('title');
  await expect(page.locator('#new-entry-disabled')).toHaveText(intake['intake.disabledReason']);
  await expect(add).toHaveAccessibleDescription(intake['intake.disabledReason']);
});

test('settings preserves a private draft when leaving is declined', async ({ page }) => {
  await page.goto('/settings');
  const name = page.locator('#app [name="full_name"]');
  await expect(name).toBeVisible();
  await name.fill('Synthetic unsaved profile');
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.locator('nav a[data-view="income"]').click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(name).toHaveValue('Synthetic unsaved profile');
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.locator('[data-locale="en"]').first().click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
  await expect(name).toHaveValue('Synthetic unsaved profile');
  expect(
    await page.evaluate(() => Object.keys(localStorage).filter((key) => key !== 'autonomo.locale')),
  ).toEqual([]);
});
