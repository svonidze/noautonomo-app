import { test, expect } from '@playwright/test';
import messages from '../../src/locales/en/workflow.json' with { type: 'json' };
import analyticsFixture from '../fixtures/analytics.json' with { type: 'json' };
import toolbar from '../../src/locales/ru/toolbar.json' with { type: 'json' };
import ruCharts from '../../src/locales/ru/charts.json' with { type: 'json' };
import enCharts from '../../src/locales/en/charts.json' with { type: 'json' };
import ruDashboard from '../../src/locales/ru/dashboard.json' with { type: 'json' };
import statuses from '../../src/locales/ru/statuses.json' with { type: 'json' };
const ASSET = '55555555-5555-4555-8555-555555555555',
  ENTRY = '66666666-6666-4666-8666-666666666666';
const asset = {
  asset_id: ASSET,
  asset_code: 'SYN-ASSET',
  description: 'Synthetic equipment',
  cost_minor: 12100,
  amortizable_base_minor: 10000,
  business_use_percent: 100,
  annual_rate_percent: 25,
  ui_context: { domain: 'asset', state: 'ready', reasons: [] },
};
const schedule = {
  native: true,
  rows: [
    {
      amortization_entry_id: ENTRY,
      period_key: '2026-Q3',
      amount_minor: 2500,
      can_post: true,
      row_version: 3,
    },
  ],
};
test('depreciation retry retains request ID without storage and does not repost after refresh failure', async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'sessionStorage', {
      get() {
        throw new Error('Disabled synthetic storage');
      },
    });
    localStorage.setItem('autonomo.locale', 'en');
  });
  let writes = 0,
    reads = 0;
  const bodies: { request_id: string; expected_version: number }[] = [];
  await page.route('**/api/assets?**', (route) => route.fulfill({ json: [asset] }));
  await page.route(`**/api/assets/${ASSET}/schedule`, (route) =>
    ++reads === 1
      ? route.fulfill({ json: schedule })
      : route.fulfill({ status: 503, json: { error: 'Synthetic schedule unavailable' } }),
  );
  await page.route(`**/api/depreciation/${ENTRY}/post`, (route) => {
    bodies.push(route.request().postDataJSON());
    return ++writes === 1
      ? route.fulfill({ status: 503, json: { error: 'Synthetic transient failure' } })
      : route.fulfill({ json: { follow_up_pending: false } });
  });
  await page.goto('/assets?period=2026-Q3');
  await page.locator('#asset-plan-select').selectOption(ASSET);
  const post = page.locator('[data-recognize]');
  await post.click();
  await expect(page.locator('#wf-schedule-status')).toHaveText('Synthetic transient failure');
  await expect(post).toBeEnabled();
  await post.click();
  await expect(page.locator('#wf-schedule-status')).toHaveText(
    messages['workflow.depreciationPostedReloadThePageToRetrieveCurrentData'],
  );
  await expect(post).toBeDisabled();
  expect(writes).toBe(2);
  expect(bodies[0]).toEqual(bodies[1]);
  expect(bodies[0]!.expected_version).toBe(3);
});
test('pending depreciation survives locale changes and late completion does not replace another screen', async ({
  page,
}) => {
  let release!: () => void,
    writes = 0;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/assets?**', (route) => route.fulfill({ json: [asset] }));
  await page.route(`**/api/assets/${ASSET}/schedule`, (route) => route.fulfill({ json: schedule }));
  await page.route(`**/api/depreciation/${ENTRY}/post`, async (route) => {
    writes++;
    await gate;
    await route.fulfill({ json: { follow_up_pending: false } });
  });
  await page.goto('/assets?period=2026-Q3');
  await page.locator('#asset-plan-select').selectOption(ASSET);
  await page.locator('[data-recognize]').click();
  await page.locator('[data-locale="en"]').click();
  await expect(page.locator('[data-recognize]')).toBeDisabled();
  await expect(page.locator('#asset-plan-select')).toHaveValue(ASSET);
  await page.locator('a[data-view="income"]').click();
  release();
  await expect(page.locator('#transaction-search')).toBeVisible();
  await expect(page.locator('#asset-plan-detail')).toHaveCount(0);
  expect(writes).toBe(1);
});
test('tax payable zero and carry-forward retain distinct meanings through locale changes', async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem('autonomo.locale', 'en'));
  await page.route('**/api/taxes?**', (route) =>
    route.fulfill({
      json: {
        period: '2026-Q3',
        period_state: { phase: 'current' },
        obligations: [{ obligation_code: '303', determination: 'due', filing_status: 'filed' }],
        tax_forms: {
          modelo303: {
            form_code: '303',
            display_state: 'filed',
            values: { '71': 0, compensation_carryforward: 50 },
            headline_value: 0,
          },
        },
        tax_summary: {
          settlement_status: 'no_payment_required',
          total_payable_minor: 0,
          total_confirmed_paid_minor: 0,
          outstanding_minor: 0,
          forms: {
            '303': {
              settlement_status: 'no_payment_required',
              determination: 'due',
              filing_status: 'filed',
              payable_minor: 0,
              disposition: 'carryforward',
              carryforward_minor: 5000,
            },
          },
        },
      },
    }),
  );
  await page.goto('/taxes?period=2026-Q3');
  const iva = page
    .locator('.tax-form-card')
    .filter({ has: page.getByRole('heading', { name: 'Modelo 303' }) });
  await expect(iva.locator('.tax-form-amount > strong')).toHaveText('€0.00');
  await expect(iva).toContainText('€50.00');
  await iva.locator('summary').click();
  await expect(iva.locator('.casilla-grid')).toContainText('€0.00');
  await page.locator('[data-locale="ru"]').click();
  await expect(iva.locator('details')).toHaveAttribute('open', '');
  await expect(iva.locator('.tax-form-amount > strong')).toContainText('0,00');
});
test('undecided returns stay distinct from filing exemptions in both locales', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('autonomo.locale', 'en'));
  await page.route('**/api/taxes?**', (route) =>
    route.fulfill({
      json: {
        period: '2026-Q3',
        period_state: { phase: 'current' },
        obligations: [
          { obligation_code: '130', determination: 'unknown' },
          { obligation_code: '303', determination: 'not_due' },
          { obligation_code: '349', determination: 'unknown' },
          { obligation_code: '390', determination: 'not_due' },
        ],
        tax_forms: {},
        tax_summary: {
          settlement_status: 'undetermined',
          forms: {
            '130': { determination: 'unknown', settlement_status: 'undetermined' },
            '303': { determination: 'not_due', settlement_status: 'not_due' },
          },
        },
      },
    }),
  );
  await page.goto('/taxes?period=2026-Q3');
  const irpf = page
    .locator('.tax-form-card')
    .filter({ has: page.getByRole('heading', { name: 'Modelo 130' }) });
  const iva = page
    .locator('.tax-form-card')
    .filter({ has: page.getByRole('heading', { name: 'Modelo 303' }) });
  const undecided = page.locator('.tax-other-forms').filter({ hasText: 'Modelo 349' });
  const exempt = page.locator('.tax-other-forms').filter({ hasText: 'Modelo 390' });
  await expect(irpf.locator('.tax-form-facts')).toContainText('Filing decision pending');
  await expect(iva.locator('.tax-form-facts')).toContainText('Filing not required');
  await expect(undecided.locator('summary')).toHaveText(
    'Returns without a decision — review the obligations',
  );
  await expect(undecided).toHaveAttribute('open', '');
  await expect(exempt).not.toContainText('Modelo 349');
  await page.locator('[data-locale="ru"]').click();
  await expect(irpf.locator('.tax-form-facts')).toContainText('Решение по подаче не принято');
  await expect(iva.locator('.tax-form-facts')).toContainText('Подача не требуется');
  await expect(undecided.locator('summary')).toHaveText(
    'Декларации без решения — проверьте обязательства',
  );
});
test('cumulative purchases follow business net; zero totals with missing FX read as one line in both locales', async ({
  page,
}) => {
  const data = structuredClone(analyticsFixture);
  data.datasets.cumulative_expenses = {
    ...data.datasets.cumulative_expenses,
    gross_actual_minor: [0, 0],
    gross_projected_minor: [0, 0],
    deductible_actual_minor: [0, 0],
    deductible_projected_minor: [0, 0],
    missing_fx_transaction_count: 1,
  };
  let reads = 0;
  await page.route('**/api/analytics?**', (route) => {
    reads++;
    return route.fulfill({ json: data });
  });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/dashboard?period=2026-Q3');
  const analytics = page.locator('details.analytics-details');
  await expect(analytics).not.toHaveAttribute('open');
  await analytics.locator(':scope > summary').click();
  const chart = page.locator('#chart-cumulative-expenses');
  await expect(chart.locator('.chart-empty')).toHaveText(
    `${ruCharts['charts.cumulativeExpenses.title']}. ${ruCharts['charts.empty.missingFx']}`,
  );
  await expect(chart.locator('svg, .chart-expand-button')).toHaveCount(0);
  expect(
    await page.evaluate(() => {
      const net = document.querySelector('#chart-cumulative-net')!;
      const purchases = document.querySelector('#chart-cumulative-expenses')!;
      return Boolean(net.compareDocumentPosition(purchases) & Node.DOCUMENT_POSITION_FOLLOWING);
    }),
  ).toBe(true);
  const before = reads;
  await page.locator('[data-locale="en"]').evaluate((button: HTMLElement) => button.click());
  await expect(chart.locator('.chart-empty')).toHaveText(
    `${enCharts['charts.cumulativeExpenses.title']}. ${enCharts['charts.empty.missingFx']}`,
  );
  await expect(analytics).toHaveAttribute('open', '');
  expect(reads).toBe(before);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
});

test('Vue expanded chart is CSP-safe, uses cached locale data and closes on route disposal', async ({
  page,
}) => {
  let reads = 0;
  await page.addInitScript(() => {
    const observed: string[] = [];
    (window as unknown as { csp: string[] }).csp = observed;
    document.addEventListener('securitypolicyviolation', (event) =>
      observed.push(event.violatedDirective),
    );
  });
  await page.route('**/api/analytics?**', async (route) => {
    reads++;
    await route.continue();
  });
  await page.goto('/dashboard?period=2026-Q3');
  await page.locator('#chart-business-result .chart-expand-button').click();
  await expect(page.locator('#vue-chart-businessResult')).toBeVisible();
  const before = reads;
  await page.locator('[data-locale="en"]').evaluate((button: HTMLElement) => button.click());
  await expect(page.locator('#vue-chart-businessResult h2')).toHaveText(
    'Income and deductible expenses by month',
  );
  expect(reads).toBe(before);
  await page.keyboard.press('Escape');
  await expect(page.locator('#chart-business-result .chart-expand-button')).toBeFocused();
  await page.locator('a[data-view="income"]').click();
  await expect(page.locator('#vue-chart-businessResult')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { csp: string[] }).csp)).toEqual([]);
});
test('Calculate on the uncalculated tax card refreshes the quarter and says so', async ({
  page,
}) => {
  let reads = 0;
  await page.route('**/api/dashboard?**', async (route) => {
    const data = await (await route.fetch()).json();
    await route.fulfill({ json: { ...data, tax_forms: {} } });
    reads++;
  });
  const posts: unknown[] = [];
  await page.route('**/api/dashboard/refresh', (route) => {
    posts.push(route.request().postDataJSON());
    return route.fulfill({ json: { status: 'refreshed', period: '2026-Q3' } });
  });
  await page.goto('/dashboard?period=2026-Q3');
  const calculate = page.locator('.metric .calculate-button');
  await expect(calculate).toHaveCount(1);
  await calculate.first().click();
  await expect(page.locator('#toast-message')).toHaveText('Расчёт за III квартал 2026 обновлён');
  expect(posts).toMatchObject([{ period: '2026-Q3' }]);
  await expect.poll(() => reads).toBe(2);
  await expect(calculate.first()).toBeEnabled();
});
test('the overview starts with what to do now: the reviewed expense waits in a post row', async ({
  page,
}) => {
  await page.goto('/dashboard?period=2026-Q3');
  const post = page
    .locator('.task-list .task-row')
    .filter({ hasText: 'Провести проверенные операции (1' });
  await expect(post).toHaveAttribute('href', '/review?period=2026-Q3&tab=posting');
  await expect(post.locator('.badge')).toHaveText(statuses['statuses.labels.ready']);
  await expect(
    page.locator('.posting-banner, #dashboard-ready-banner, .deadline-strip'),
  ).toHaveCount(0);
  const order = await page
    .locator('.task-list, .metric-grid, .dashboard-grid, .analytics-details')
    .evaluateAll((nodes) => nodes.map((node) => node.className));
  expect(order).toEqual(['task-list', 'metric-grid', 'dashboard-grid', 'analytics-details']);
});
test('a review queue, a blocked row and a due return each link where the work is', async ({
  page,
}) => {
  await page.route('**/api/dashboard?**', async (route) => {
    const data = await (await route.fetch()).json();
    const preview = (code: string) => ({
      form_code: code,
      display_state: 'preview',
      values: {},
      headline_value: '0.00',
      preview_as_of: '2026-10-01',
    });
    await route.fulfill({
      json: {
        ...data,
        posting_summary: { needs_review: 2, ready: 0, later: 0, blocked: 1 },
        obligations: [
          {
            obligation_code: '130',
            determination: 'due',
            filing_status: 'unfiled',
            statutory_due_on: '2026-10-20',
          },
        ],
        tax_forms: { modelo130: preview('130'), modelo303: preview('303') },
      },
    });
  });
  await page.goto('/dashboard?period=2026-Q3');
  const rows = page.locator('.task-list .task-row');
  await expect(rows.locator('strong')).toHaveText([
    'Проверить документы (2)',
    'Выяснить, что мешает провести операции (1)',
    'Подать Modelo 130',
  ]);
  expect(await rows.evaluateAll((links) => links.map((link) => link.getAttribute('href')))).toEqual(
    ['/review?period=2026-Q3', '/review?period=2026-Q3', '/taxes?period=2026-Q3'],
  );
  await expect(rows.nth(2).locator('.badge')).toContainText('Нужно подать до');
  await expect(rows.nth(2).locator('.badge')).toContainText('2026');
  await rows.nth(2).click();
  await expect(page).toHaveURL(/\/taxes\?period=2026-Q3$/);
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
});
test('the calculate row refreshes the quarter and leaves the list once the quarter has a calculation', async ({
  page,
}) => {
  let calculated = false;
  await page.route('**/api/dashboard?**', async (route) => {
    const data = await (await route.fetch()).json();
    const preview = (code: string) => ({
      form_code: code,
      display_state: 'preview',
      values: {},
      headline_value: '0.00',
      preview_as_of: '2026-10-09',
    });
    await route.fulfill({
      json: {
        ...data,
        tax_forms: calculated ? { modelo130: preview('130'), modelo303: preview('303') } : {},
      },
    });
  });
  const posts: unknown[] = [];
  await page.route('**/api/dashboard/refresh', (route) => {
    posts.push(route.request().postDataJSON());
    calculated = true;
    return route.fulfill({ json: { status: 'refreshed', period: '2026-Q3' } });
  });
  await page.goto('/dashboard?period=2026-Q3');
  const calculate = page.locator('button.task-row');
  await expect(calculate.locator('strong')).toHaveText(
    ruDashboard['dashboard.task.calculate.title'],
  );
  await expect(calculate.locator('.badge')).toHaveText(
    statuses['statuses.labels.calculation_unavailable'],
  );
  await calculate.click();
  await expect(page.locator('#toast-message')).toHaveText('Расчёт за III квартал 2026 обновлён');
  expect(posts).toMatchObject([{ period: '2026-Q3' }]);
  await expect(calculate).toHaveCount(0);
  const tax = page.locator('.metric').filter({ hasText: ruDashboard['dashboard.taxDue'] });
  await expect(tax.locator('dd strong')).toHaveText([/0,00/, /0,00/]);
  await expect(tax.locator('.calculate-button')).toHaveCount(0);
});
test('on a phone the first screen lists tasks and draws no chart until Analytics is opened', async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/dashboard?period=2026-Q3');
  await expect(page.locator('.task-row').first()).toBeVisible();
  const firstScreen = await page.evaluate(() => {
    const onScreen = (selector: string) =>
      [...document.querySelectorAll(selector)].filter((node) => {
        const box = node.getBoundingClientRect();
        return box.width > 0 && box.height > 0 && box.top < window.innerHeight;
      }).length;
    return { tasks: onScreen('.task-row'), charts: onScreen('svg.chart-svg') };
  });
  expect(firstScreen.tasks).toBeGreaterThan(0);
  expect(firstScreen.charts).toBe(0);
  const analytics = page.locator('details.analytics-details');
  await expect(analytics).not.toHaveAttribute('open');
  await analytics.locator(':scope > summary').click();
  await expect(page.locator('svg.chart-svg').first()).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
});
test('the refresh shortcut is captioned on wide screens and stays hidden where it does not apply', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/taxes?period=2026-Q3');
  const refresh = page.locator('#refresh-button');
  await expect(refresh.locator('.refresh-label')).toHaveText(toolbar['toolbar.refresh']);
  await expect(refresh).toHaveAccessibleName(toolbar['toolbar.refresh']);
  await page.setViewportSize({ width: 1000, height: 800 });
  await expect(refresh.locator('.refresh-label')).toBeHidden();
  await expect(refresh).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.locator('a[data-view="settings"]').click();
  await expect(page.locator('#settings-profile-form')).toBeVisible();
  await expect(refresh).toBeHidden();
});
test('RETA check is its own read-only section after the tax forms, separate from the headline', async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem('autonomo.locale', 'en'));
  await page.goto('/taxes?period=2026-Q3');
  const card = page.locator('section.reta-check');
  // The synthetic server has no taxpayer profile or table: unknown, never an error page.
  await expect(card.locator('.badge')).toHaveText('Not determined');
  await expect(card.locator('.badge')).toHaveClass(/status-neutral/);
  await expect(card.locator('.reta-check-reasons li')).toHaveText([
    'The check needs exactly one taxpayer profile.',
    'The RETA table of this year is not imported yet.',
  ]);
  const order = await page
    .locator('.tax-page > *')
    .evaluateAll((nodes) => nodes.map((node) => node.className));
  const at = (name: string) => order.findIndex((value) => value.includes(name));
  expect(at('tax-form-grid')).toBeLessThan(at('reta-check'));
  expect(at('reta-check')).toBeLessThan(at('tax-analytics-panel'));
  await expect(page.locator('.tax-hero .reta-check')).toHaveCount(0);

  let reads = 0;
  await page.route('**/api/reta-check?**', (route) => {
    reads++;
    return route.fulfill({
      json: {
        year: 2026,
        through: '2026-08-31',
        status: 'below_bracket',
        reasons: [],
        income: { monthly_average_minor: 250000 },
        bracket: { table: 'general', tramo: 8, min_base_minor: 143791, max_base_minor: 400000 },
        boundary_sensitive: false,
        average_provisional_base_minor: 95098,
        estimated_additional_minor: 92030,
        estimated_refund_minor: 0,
        additional_locked_in_minor: 46015,
        next_base_change: { effective_on: '2026-11-01', request_by: '2026-10-31' },
        elections: [
          {
            effective_from: '2026-01-01',
            regime: 'base',
            monthly_base_minor: 95098,
            worker_kind: 'individual',
            source_reference: 'Synthetic TGSS resolution',
          },
        ],
      },
    });
  });
  await page.reload();
  await expect(card.locator('.badge')).toHaveText('Below the bracket');
  await expect(card.locator('[data-reta="additional"]')).toHaveText('€920.30');
  await expect(card.locator('[data-reta="refund"]')).toHaveText('€0.00');
  await expect(card.locator('[data-reta="locked"]')).toHaveText('€460.15');
  await expect(card.locator('tbody td').nth(4)).toHaveText('Synthetic TGSS resolution');
  await expect(page.locator('.tax-hero')).not.toContainText('920');
  await page.locator('[data-locale="ru"]').click();
  await expect(card.locator('.badge')).toHaveText('Ниже ступени');
  await expect(card.locator('[data-reta="additional"]')).toContainText('920,30');
  expect(reads).toBe(1);
});
