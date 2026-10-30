import { test, expect } from '@playwright/test';
import ru from '../../src/locales/ru/government.json' with { type: 'json' };
import en from '../../src/locales/en/government.json' with { type: 'json' };
import es from '../../src/locales/es/government.json' with { type: 'json' };

test('government correspondence preserves text, relates procedures and remains usable on mobile', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/government-cases');
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  const create = async (title: string, parent_case_id?: string) => {
    const response = await page.request.post('/api/government-cases', {
      headers: { Origin: 'http://127.0.0.1:8765' },
      data: {
        authority_code: 'TEST-MUNICIPALITY',
        authority_name: 'Synthetic municipality',
        title,
        parent_case_id,
        opened_at: '2032-01-01T10:00:00Z',
        evidence_reference: 'Synthetic original',
      },
    });
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  const parent = await create('Synthetic parent procedure');
  const child = await create('Synthetic correspondence procedure', parent.case_id);
  await page.goto(`/government-cases?case=${child.case_id}`);
  await expect(page.getByRole('navigation', { name: ru['government.relatedCases'] })).toContainText(
    parent.title,
  );
  const form = page.locator('details').filter({
    has: page.locator(':scope > summary', { hasText: ru['government.addCorrespondence'] }),
  });
  await form.locator('summary').click();
  await form.getByLabel(ru['government.messageSubject']).fill('Synthetic browser correspondence');
  const text =
    '<img src="https://example.invalid/no-load" onerror="alert(1)">\nFull text\n  preserved whitespace  \n';
  await form.getByLabel(ru['government.fullText']).fill(text);
  await form.getByLabel(ru['government.sender'], { exact: true }).fill('Synthetic sender');
  await form
    .getByLabel(ru['government.recipients'], { exact: true })
    .fill('synthetic@example.invalid');
  await form
    .getByRole('button', { name: ru['government.saveCorrespondence'], exact: true })
    .click();
  const history = page.locator('.government-history');
  await expect(history).toContainText('Synthetic browser correspondence');
  await history.getByText(ru['government.fullText'], { exact: true }).click();
  await expect(history.locator('pre')).toHaveText(text);
  await expect(history.locator('img')).toHaveCount(0);
  const detail = await (await page.request.get(`/api/government-cases/${child.case_id}`)).json();
  expect(detail.correspondence[0].body_text).toBe(text);
  expect(detail.decision_outcome).toBeNull();
  await history.getByRole('button', { name: ru['government.addAction'], exact: true }).click();
  await expect(page.getByLabel(ru['government.description'], { exact: true })).toHaveValue(
    'Synthetic browser correspondence',
  );
  await expect(
    page.getByLabel(ru['government.sourceMessage'], { exact: true }).first(),
  ).toHaveValue(detail.correspondence[0].event_id);
  for (const [locale, messages] of [
    ['en', en],
    ['es', es],
    ['ru', ru],
  ] as const) {
    await page
      .locator(`[data-locale="${locale}"]`)
      .first()
      .evaluate((element: HTMLElement) => element.click());
    await expect(page.locator('html')).toHaveAttribute('lang', locale);
    await expect(history).toContainText(messages['government.sender']);
  }
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  const summary = history.locator('details > summary');
  await summary.focus();
  await page.keyboard.press('Enter');
  await expect(history.locator('pre')).toBeHidden();
  await page.keyboard.press('Enter');
  await expect(history.locator('pre')).toBeVisible();
  await page.screenshot({
    path: 'test-results/government-correspondence-mobile.png',
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test('case links isolate detail and restore filtered list through history', async ({ page }) => {
  await page.goto('/government-cases');
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  const create = async (title: string, parent_case_id?: string) => {
    const response = await page.request.post('/api/government-cases', {
      headers: { Origin: 'http://127.0.0.1:8765' },
      data: {
        authority_code: 'TEST-MUNICIPALITY',
        authority_name: 'Synthetic municipality',
        title,
        parent_case_id,
        opened_at: '2032-01-01T10:00:00Z',
        evidence_reference: 'Synthetic original',
      },
    });
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  const parent = await create('Synthetic filtered parent');
  const child = await create('Synthetic filtered child', parent.case_id);
  await page.reload();
  const search = page.getByLabel(ru['government.search']);
  await search.fill('Synthetic filtered child');
  await page.getByRole('link', { name: /Synthetic filtered child/ }).click();
  await expect(page).toHaveURL(new RegExp(`case=${child.case_id}`));
  await expect(page.getByRole('heading', { name: ru['government.reminders'] })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: ru['government.history'] })).toBeVisible();
  const description = page.getByLabel(ru['government.description'], { exact: true });
  await description.fill('Synthetic unsaved action');
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.getByRole('link', { name: ru['government.backToCases'] }).click();
  await expect(page).toHaveURL(new RegExp(`case=${child.case_id}`));
  await expect(description).toHaveValue('Synthetic unsaved action');
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.evaluate(() => history.back());
  await expect(page).toHaveURL(new RegExp(`case=${child.case_id}`));
  await expect(description).toHaveValue('Synthetic unsaved action');
  page.once('dialog', (dialog) => dialog.accept());
  await page
    .getByRole('navigation', { name: ru['government.relatedCases'] })
    .getByRole('link', { name: /Synthetic filtered parent/ })
    .click();
  await expect(page).toHaveURL(new RegExp(`case=${parent.case_id}`));
  await page.reload();
  await page.getByRole('link', { name: ru['government.backToCases'] }).click();
  await expect(search).toHaveValue('Synthetic filtered child');
  expect(page.url()).not.toContain('Synthetic');
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`case=${parent.case_id}`));
  await page.goForward();
  await expect(search).toHaveValue('Synthetic filtered child');
});
