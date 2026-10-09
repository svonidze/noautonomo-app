import { test, expect, type Page } from '@playwright/test';
const ID = '11111111-1111-4111-8111-111111111111',
  SECOND = '22222222-2222-4222-8222-222222222222';
const ready = (id = ID) => ({
  transaction_id: id,
  review_id: 'transaction:' + id,
  expected_row_version: id === ID ? 3 : 4,
  transaction_date: '2026-07-02',
  entry_type: 'expense',
  description: id === ID ? 'Synthetic first' : 'Synthetic second',
  amount_eur: '121.00',
  preview_bucket: 'ready',
});
const initial = (rows = [ready(), ready(SECOND)], period = '2026-Q3') => ({
  period,
  period_status: 'open',
  generated_at: '2026-09-01T12:00:00Z',
  ready: rows,
  deferred: [],
  blocked: [],
  summary: {
    ready_count: rows.length,
    approved_count: rows.length,
    ready_total_eur: '242.00',
    cleanup_count: 1,
  },
});
async function overview(page: Page) {
  await page.route(/\/api\/transactions\?.*status=review/, (route) => route.fulfill({ json: [] }));
  await page.route('**/api/issues?**', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/documents?**', (route) => route.fulfill({ json: [] }));
}
test('batch confirmation freezes rows and locale changes do not resend; refresh retry never reposts', async ({
  page,
}) => {
  await overview(page);
  let completed = false,
    refreshes = 0;
  const writes: unknown[] = [];
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/review/posting-preview?**', (route) =>
    route.fulfill({ json: initial(completed ? [] : undefined) }),
  );
  await page.route('**/api/review/post-ready', async (route) => {
    writes.push(route.request().postDataJSON());
    await gate;
    completed = true;
    await route.fulfill({
      json: {
        status: 'partial',
        summary: { posted_count: 1 },
        results: [
          { transaction_id: ID, outcome: 'posted' },
          {
            transaction_id: SECOND,
            outcome: 'blocked',
            blockers: [{ code: 'stale_row_version', message: 'Synthetic stale row' }],
          },
        ],
      },
    });
  });
  await page.route('**/api/dashboard/refresh', (route) =>
    ++refreshes === 1
      ? route.fulfill({ status: 503, json: { error: 'Synthetic refresh unavailable' } })
      : route.fulfill({ json: { status: 'ok' } }),
  );
  await page.goto('/review?period=2026-Q3&tab=posting');
  await page.locator('[data-posting-action="open-confirm"]').click();
  await expect(page.locator('#vue-posting-confirm-dialog tbody tr')).toHaveCount(2);
  await page.locator('#vue-confirm-posting').click();
  await page.locator('[data-locale="en"]').evaluate((button: HTMLElement) => button.click());
  await expect(page.locator('#vue-confirm-posting')).toBeDisabled();
  expect(writes).toHaveLength(1);
  release();
  await expect(page.locator('#vue-posting-confirm-dialog')).not.toBeVisible();
  await expect(page.locator('.posting-results')).toContainText('Synthetic first');
  await expect(page.locator('[data-posting-action="retry-refresh"]')).toBeVisible();
  expect(writes[0]).toEqual({
    period: '2026-Q3',
    items: [
      { transaction_id: ID, expected_row_version: 3 },
      { transaction_id: SECOND, expected_row_version: 4 },
    ],
  });
  await page.locator('[data-posting-action="retry-refresh"]').click();
  await expect(page.locator('[data-posting-action="retry-refresh"]')).toHaveCount(0);
  expect(writes).toHaveLength(1);
  expect(refreshes).toBe(2);
});
test('a late batch result does not refresh another page and records the captured period', async ({
  page,
}) => {
  await overview(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let posts = 0,
    refreshes = 0;
  await page.route('**/api/review/posting-preview?**', (route) =>
    route.fulfill({ json: initial() }),
  );
  await page.route('**/api/review/post-ready', async (route) => {
    posts++;
    await gate;
    await route.fulfill({
      json: {
        status: 'completed',
        summary: { posted_count: 2 },
        results: [
          { transaction_id: ID, outcome: 'posted' },
          { transaction_id: SECOND, outcome: 'posted' },
        ],
      },
    });
  });
  await page.route('**/api/dashboard/refresh', (route) => {
    refreshes++;
    return route.fulfill({ json: { status: 'ok' } });
  });
  await page.goto('/review?period=2026-Q3&tab=posting');
  await page.locator('[data-posting-action="open-confirm"]').click();
  await page.locator('#vue-confirm-posting').click();
  await expect.poll(() => posts).toBe(1);
  await page.locator('#vue-posting-confirm-dialog .dialog-header > button').click();
  await page.locator('a[data-view="income"]').click();
  release();
  await expect(page.locator('#transaction-search')).toBeVisible();
  expect(refreshes).toBe(0);
  await page.goBack();
  await expect(page.locator('.posting-results')).toBeVisible();
  await expect(page.locator('[data-posting-action="retry-refresh"]')).toBeVisible();
  expect(refreshes).toBe(0);
  expect(posts).toBe(1);
});
test('closed or mismatched posting periods cannot open confirmation and tab keys preserve navigation', async ({
  page,
}) => {
  await overview(page);
  await page.route('**/api/review/posting-preview?**', (route) =>
    route.fulfill({ json: { ...initial(), period_status: 'closed' } }),
  );
  await page.goto('/review?period=2026-Q3&tab=posting');
  await expect(page.locator('[data-posting-action="open-confirm"]')).toBeDisabled();
  await page.locator('#review-tab-queue').click();
  await expect(page).not.toHaveURL(/tab=posting/);
  await page.locator('#review-tab-queue').focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('#review-tab-posting')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/tab=posting/);
  await page.unroute('**/api/review/posting-preview?**');
  await page.route('**/api/review/posting-preview?**', (route) =>
    route.fulfill({ json: initial([ready()], '2026-Q2') }),
  );
  await page.reload();
  await expect(page.locator('.review-shell [role="alert"]')).toBeVisible();
  await expect(page.locator('[data-posting-action="open-confirm"]')).toHaveCount(0);
});
test('a blocked approved row in the queue says what blocks it', async ({ page }) => {
  // The shared fixture's USD expense is reviewed but has no EUR amount; the server offers no review.
  await page.goto('/review?period=2026-Q3');
  const rows = page.locator('#review-tabpanel tbody tr');
  const blocked = rows.filter({ hasText: 'Example USD expense' });
  await expect(blocked).toContainText('Проверено, проведение заблокировано');
  // Its one action opens the explanation, so its status cell does not repeat that link.
  await expect(blocked.locator('.status-context small')).toHaveText('Не хватает суммы в евро');
  await expect(blocked.locator('[data-status-help]')).toHaveCount(1);
  const ready = rows.filter({ hasText: 'Example expense' });
  await expect(ready.locator('.badge')).toHaveText('Проверено, можно провести');
  await expect(ready.locator('.status-context small')).toHaveCount(0);
  const action = blocked.locator('td.table-actions > *');
  await expect(action).toHaveCount(1);
  await expect(action).toHaveText('Что мешает');
  await action.click();
  const help = page.locator('#status-help-dialog');
  await expect(help).toBeVisible();
  await expect(help).toContainText('Не хватает суммы в евро');
  await page.locator('#status-help-close').click();
  await expect(help).not.toBeVisible();
  await expect(action).toBeFocused();
  // Every other row has its one action too.
  for (const row of await rows.all())
    await expect(row.locator('td.table-actions > *')).toHaveCount(1);
});
test('the queue opens posting at its ready row; ready rows come first and a missing EUR amount stays missing', async ({
  page,
}) => {
  const preview = page.waitForResponse((response) =>
    response.url().includes('/api/review/posting-preview?'),
  );
  await page.goto('/review?period=2026-Q3');
  // The server sends no amount for the USD row, not a zero.
  const blockedItem = ((await (await preview).json()).blocked as Record<string, unknown>[]).find(
    (row) => row.description === 'Example USD expense',
  );
  expect(blockedItem).toMatchObject({ amount_eur: null, effective_amount_eur_minor: null });
  const ready = page.locator('#review-tabpanel tbody tr').filter({ hasText: 'Example expense' });
  await ready.locator('td.table-actions > a').click();
  await expect(page).toHaveURL(/\/review\?period=2026-Q3&tab=posting#posting-row-[0-9a-f-]{36}$/);
  const target = page.locator(new URL(page.url()).hash);
  await expect(target).toHaveClass('posting-row-target');
  await expect(target).toContainText('Example expense');
  await expect(target).toBeInViewport();
  // Ready to post as it is: nothing to explain, so no link beside its badge.
  await expect(target.locator('[data-status-help]')).toHaveCount(0);
  // Ready rows are listed before anything is posted, then the blocked ones.
  await expect(page.locator('.posting-section-label')).toHaveText([
    'Проверено, можно провести',
    'Проверено, проведение заблокировано',
  ]);
  const usd = page.locator('#review-tabpanel tbody tr').filter({ hasText: 'Example USD expense' });
  await expect(usd.locator('td.amount')).toHaveText('—нет суммы в EUR');
  await expect(page.locator('details.posting-technical > summary')).toHaveText(
    'Технические детали',
  );
  await expect(page.locator('.primary-button:visible')).toHaveText(['Провести']);
});
