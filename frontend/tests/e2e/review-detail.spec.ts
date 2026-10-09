import { test, expect, type Page } from '@playwright/test';
import {
  guidedFixture,
  nativeFixture,
  ruleFixture,
  utilityProposal,
  REVIEW_ID as ID,
  DOCUMENT_ID as DOC,
} from '../fixtures/review.ts';
import type { ExpensePayload } from '../../src/features/review/workflow-model.ts';
import issues from '../../src/locales/ru/issues.json' with { type: 'json' };
import workflow from '../../src/locales/ru/workflow.json' with { type: 'json' };
const path = `/review/${ID}?period=2026-Q3`;
async function detail(page: Page, kind = 'income', posted = () => false) {
  await page.route(`**/api/transactions/${ID}`, (route) =>
    route.fulfill({
      json: {
        transaction: {
          transaction_id: ID,
          entry_type: kind,
          lifecycle_status: posted() ? 'posted' : 'needs_review',
          currency: 'EUR',
        },
        period: { period_key: '2026-Q3' },
      },
    }),
  );
}
async function native(page: Page) {
  await detail(page, 'expense');
  await page.route(`**/api/document/${DOC}/preview`, (route) =>
    route.fulfill({ contentType: 'text/plain', body: 'Synthetic document' }),
  );
}
test('native save/preview/confirm preserves units and independent IVA choice, then retries one request', async ({
  page,
}) => {
  await native(page);
  const draft = nativeFixture();
  let saved: ExpensePayload | undefined;
  const writes: unknown[] = [];
  let confirmations = 0;
  await page.route(`**/api/expense-workflows/${ID}`, (route) => route.fulfill({ json: draft }));
  await page.route(`**/api/expense-workflows/${ID}/save`, (route) => {
    const body = route.request().postDataJSON();
    saved = body.payload;
    expect(body.expected_version).toBe(1);
    return route.fulfill({ json: { ...draft, payload: saved, draft_version: 2 } });
  });
  await page.route(`**/api/expense-workflows/${ID}/preview`, (route) => {
    expect(route.request().postDataJSON()).toEqual({ expected_version: 2 });
    return route.fulfill({
      json: {
        preview_token: 'synthetic-token',
        supplier: 'Synthetic supplier',
        period: '2026-Q3',
        currency: 'EUR',
        gross_minor: 12100,
        deductible_vat_minor: 2100,
        deductible_irpf_minor: 0,
        future_depreciation_minor: 10000,
        schedule: [],
      },
    });
  });
  await page.route(`**/api/expense-workflows/${ID}/confirm`, (route) => {
    writes.push(route.request().postDataJSON());
    return ++confirmations === 1
      ? route.fulfill({ status: 503, json: { error: 'Synthetic transient confirmation failure' } })
      : route.fulfill({ json: { transaction_id: ID, follow_up_pending: true } });
  });
  await page.route(`**/api/expense-workflows/${ID}/follow-up`, (route) =>
    route.fulfill({ json: { follow_up_pending: false } }),
  );
  await page.goto(path);
  await page.locator('[data-reveals="wf-kind"]').click();
  await page.locator('#wf-kind').selectOption('asset');
  await page.locator('[data-p="asset.method"]').selectOption('linear');
  await page.locator('[data-p="asset.annual_rate_basis_points"]').fill('25.50');
  await page.locator('[data-p="decision.tax_treatment.vat_investment_good"]').selectOption('false');
  await page
    .locator('[data-p="decision.tax_treatment.tax_code"]')
    .selectOption('eu_service_expense');
  await page.locator('#wf-form button[type="submit"]').click();
  await expect(page.locator('#wf-confirm')).toBeVisible();
  expect(saved?.asset?.annual_rate_basis_points).toBe(2550);
  expect(saved?.decision.tax_treatment.vat_investment_good).toBe(false);
  expect(saved?.decision.tax_treatment.include_modelo130).toBe(false);
  expect(saved?.decision.tax_treatment.aeat_reverse_charge).toBe(true);
  await page.locator('#wf-confirm').click();
  await expect(page.locator('.expense-workflow [role="alert"]')).toContainText(
    'Synthetic transient',
  );
  await page.locator('#wf-confirm').click();
  await expect(page.locator('#wf-open-posted')).toBeVisible();
  expect(writes).toHaveLength(2);
  expect(writes[0]).toEqual(writes[1]);
  await page.locator('#wf-follow-up').click();
  await expect(page.locator('#wf-follow-up')).toHaveCount(0);
  const before = await page.evaluate(() => history.length);
  await page.locator('#wf-open-posted').click();
  await expect(page).toHaveURL(new RegExp(`/expenses/${ID}\\?`));
  expect(await page.evaluate(() => history.length)).toBe(before + 1);
});
test('a new reviewer sees what remains, the result in view and reaches the confirmation by keyboard', async ({
  page,
}) => {
  await native(page);
  const draft = nativeFixture();
  draft.source.issues = [{ issue_code: 'document_classification_review' }];
  let saved: ExpensePayload | undefined;
  await page.route(`**/api/expense-workflows/${ID}`, (route) => route.fulfill({ json: draft }));
  await page.route(`**/api/expense-workflows/${ID}/save`, (route) => {
    saved = route.request().postDataJSON().payload;
    return route.fulfill({ json: { ...draft, payload: saved, draft_version: 2 } });
  });
  // Like the server, the preview refuses a draft without an IVA rate.
  await page.route(`**/api/expense-workflows/${ID}/preview`, (route) =>
    saved?.decision.tax_treatment.rate_basis_points !== 2100
      ? route.fulfill({
          status: 400,
          json: { error: 'Explicit rate_basis_points in EUR is required', code: 'expense_invalid' },
        })
      : route.fulfill({
          json: {
            preview_token: 'synthetic-token',
            supplier: 'Synthetic supplier',
            period: '2026-Q3',
            currency: 'EUR',
            gross_minor: 12100,
            deductible_vat_minor: 2100,
            deductible_irpf_minor: 10000,
            future_depreciation_minor: 0,
            schedule: [],
          },
        }),
  );
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto(path);
    await expect(page.locator('nav.wf-steps a')).toHaveText(['1 Документ', '2 Налоги', '3 Итог']);
    const remaining = page.locator('.wf-remaining');
    await expect(remaining.locator('h3')).toHaveText('Что осталось');
    const question = remaining.getByRole('link', {
      name: issues['issues.messages.document_classification_review'],
    });
    await expect(question).toHaveAttribute('href', '#wf-manual_review_reason');
    await question.click();
    await expect(page.locator('#wf-manual_review_reason')).toBeFocused();
    // Intake leaves the IVA rate empty: it is visible beside the invoice IVA and named until typed.
    const rate = page.locator('[data-p="decision.tax_treatment.rate_basis_points"]');
    await expect(rate).toBeVisible();
    const rateItem = remaining.getByRole('link', { name: workflow['workflow.remaining.ivaRate'] });
    await rateItem.click();
    await expect(rate).toBeFocused();
    await rate.fill('21');
    await expect(rateItem).toHaveCount(0);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    ).toBe(true);
    await page.screenshot({
      path: test.info().outputPath(`expense-review-${width}.png`),
      fullPage: true,
    });
    const preview = page.locator('#wf-preview');
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(preview).not.toBeInViewport();
    await page.locator('#wf-form button[type="submit"]').click();
    await expect(preview.locator('#wf-confirm')).toBeVisible();
    await expect(preview).toBeInViewport();
    await expect(page.locator('#wf-preview-title')).toBeFocused();
    await expect(page.locator('nav.wf-steps a').nth(2)).toHaveAttribute('aria-current', 'step');
    await page.keyboard.press('Tab');
    await expect(page.locator('#wf-confirm')).toBeFocused();
    await page.screenshot({
      path: test.info().outputPath(`expense-review-result-${width}.png`),
      fullPage: true,
    });
  }
  // The whole form is reachable in reading order from the step links to the confirmation.
  await page.locator('nav.wf-steps a').first().focus();
  const visited: string[] = [];
  for (let press = 0; press < 200 && visited.at(-1) !== 'wf-confirm'; press++) {
    await page.keyboard.press('Tab');
    visited.push(
      await page.evaluate(() => {
        const active = document.activeElement as HTMLElement | null;
        return active?.dataset.p || active?.id || '';
      }),
    );
  }
  expect(visited.at(-1)).toBe('wf-confirm');
  const order = [
    'facts.document_number',
    'manual_review_reason',
    'decision.tax_treatment.tax_code',
    'decision.tax_treatment.rate_basis_points',
    'decision.business_purpose',
    'wf-save',
  ].map((name) => visited.indexOf(name));
  expect(order.every((position) => position >= 0)).toBe(true);
  expect(order).toEqual([...order].sort((a, b) => a - b));
});
test('native conflict reset is explicit and edits invalidate a posting preview', async ({
  page,
}) => {
  await native(page);
  const draft = nativeFixture();
  draft.conflict = true;
  draft.current_snapshot_hash = 'new-source';
  draft.source_values.facts.document_number = 'SYN-CURRENT';
  draft.payload.facts.document_number = 'SYN-SAVED';
  await page.route(`**/api/expense-workflows/${ID}`, (route) => route.fulfill({ json: draft }));
  await page.route(`**/api/expense-workflows/${ID}/save`, (route) => {
    const body = route.request().postDataJSON();
    expect(body.source_snapshot_hash).toBe('new-source');
    return route.fulfill({
      json: {
        ...draft,
        conflict: false,
        source_snapshot_hash: 'new-source',
        payload: body.payload,
        draft_version: 2,
      },
    });
  });
  await page.route(`**/api/expense-workflows/${ID}/preview`, (route) =>
    route.fulfill({
      json: {
        preview_token: 'synthetic-new-token',
        supplier: 'Synthetic',
        period: '2026-Q3',
        currency: 'EUR',
        gross_minor: 12100,
        deductible_vat_minor: 2100,
        deductible_irpf_minor: 10000,
        future_depreciation_minor: 0,
        schedule: [],
      },
    }),
  );
  await page.goto(path);
  await expect(page.locator('[data-p="facts.document_number"]')).toHaveValue('SYN-SAVED');
  await expect(page.locator('#wf-form button[type="submit"]')).toBeDisabled();
  page.once('dialog', (dialog) => dialog.accept());
  await page.locator('#wf-reset').click();
  await expect(page.locator('[data-p="facts.document_number"]')).toHaveValue('SYN-CURRENT');
  await page.locator('#wf-form button[type="submit"]').click();
  await expect(page.locator('#wf-confirm')).toBeVisible();
  await page.locator('[data-p="facts.document_number"]').fill('SYN-EDITED');
  await expect(page.locator('#wf-confirm')).toHaveCount(0);
});
test('home supplies rule: parser pre-selection, area fact, saved proposal, copy and preview row', async ({
  page,
}) => {
  await native(page);
  const draft = ruleFixture();
  draft.payload.decision.tax_treatment.deductible_ratio = null;
  draft.deduction_proposal = {
    ...utilityProposal(),
    status: 'needs_facts',
    irpf_minor: null,
    vat_minor: null,
    suggested: null,
    missing: ['area_share'],
    explanation: [],
  };
  const saves: ExpensePayload[] = [];
  await page.route(`**/api/expense-workflows/${ID}`, (route) => route.fulfill({ json: draft }));
  await page.route(`**/api/expense-workflows/${ID}/save`, (route) => {
    const body = route.request().postDataJSON();
    expect(body.expected_version).toBe(draft.draft_version + saves.length);
    saves.push(body.payload);
    return route.fulfill({
      json: {
        ...draft,
        payload: body.payload,
        draft_version: draft.draft_version + saves.length,
        deduction_proposal: utilityProposal(),
      },
    });
  });
  await page.route(`**/api/expense-workflows/${ID}/preview`, (route) => {
    const tax = saves.at(-1)!.decision.tax_treatment;
    return route.fulfill({
      json: {
        preview_token: 'synthetic-rule-token',
        supplier: 'Synthetic supplier',
        period: '2026-Q3',
        currency: 'EUR',
        gross_minor: 12100,
        deductible_vat_minor: tax.deductible_vat_minor,
        deductible_irpf_minor: tax.deductible_irpf_minor,
        future_depreciation_minor: 0,
        schedule: [],
        deduction: utilityProposal(),
      },
    });
  });
  await page.goto(path);
  await page.locator('[data-locale="en"]').evaluate((button: HTMLElement) => button.click());
  const select = page.locator('#wf-rule-select');
  await expect(select).toHaveValue('home_utility_partial_dwelling');
  await expect(select.locator('option:checked')).toContainText('(from document parser)');
  const card = page.locator('#wf-rule-card');
  await expect(card.locator('.wf-rule-live')).toHaveAttribute('aria-live', 'polite');
  await expect(card).toContainText('Floor-area share used for the activity');
  await page.locator('#wf-rule [data-p="decision.tax_treatment.deductible_ratio"]').fill('25');
  await expect(card).toContainText('save the draft to recalculate');
  await page.locator('#wf-save').click();
  await expect(card).toContainText('€7.50');
  await expect(card).toContainText(
    'Copy writes IRPF €7.50 into its field; IVA stays as you entered it.',
  );
  await expect(card).toContainText('Medium risk');
  await expect(select).toHaveAccessibleName('Rule for this expense');
  await expect(page.locator('#wf-rule-copy')).toHaveAccessibleName('Copy into fields');
  await page.locator('#wf-rule-copy').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('[data-p="decision.tax_treatment.deductible_irpf_minor"]')).toHaveValue(
    '7.5',
  );
  await expect(page.locator('#wf-rule-copy')).toBeDisabled();
  await page.locator('#wf-form button[type="submit"]').click();
  await expect(page.locator('#wf-preview-rule')).toContainText(
    'IRPF €0.00 below the ceiling; IVA is your decision',
  );
  expect(saves.at(-1)?.deduction?.rule_id).toBe('home_utility_partial_dwelling');
  expect(saves.at(-1)?.decision.tax_treatment).toMatchObject({
    deductible_ratio: 0.25,
    deductible_irpf_minor: 750,
    deductible_vat_minor: 525,
  });
});
test('expense rule pseudolocale fits a narrow screen and keeps manual IVA on keyboard copy', async ({
  page,
}) => {
  test.skip(process.env.AUTONOMO_PSEUDO !== '1', 'Run npm run test:pseudo for a test-only locale');
  await native(page);
  const draft = ruleFixture();
  await page.route(`**/api/expense-workflows/${ID}`, (route) => route.fulfill({ json: draft }));
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(path);
  await page.locator('[data-locale="qps"]').evaluate((button: HTMLElement) => button.click());
  await expect(page.locator('#wf-rule-card')).toContainText('⟦');
  await expect(page.locator('#wf-rule-card .wf-rule-live')).toHaveAttribute('aria-live', 'polite');
  const copy = page.locator('#wf-rule-copy');
  await expect(copy).toHaveAccessibleName(/⟦/);
  await copy.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('[data-p="decision.tax_treatment.deductible_irpf_minor"]')).toHaveValue(
    '7.5',
  );
  await expect(page.locator('[data-p="decision.tax_treatment.deductible_vat_minor"]')).toHaveValue(
    '5.25',
  );
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
});

test('late guided confirmation preserves a newer draft and uses additive localized validation', async ({
  page,
}) => {
  await detail(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let writes = 0;
  await page.route(/\/api\/review\/work-item\?/, (route) =>
    route.fulfill({ json: guidedFixture() }),
  );
  await page.route('**/api/review/confirm', async (route) => {
    if (++writes === 1) {
      await gate;
      return route.fulfill({ json: { status: 'ok' } });
    }
    return route.fulfill({
      status: 400,
      json: {
        error: 'decision.business_purpose is required',
        code: 'invalid_review',
        message_code: 'review.validationBusinessPurpose',
        field: 'business_purpose',
      },
    });
  });
  await page.goto(path);
  await page.locator('[data-decision-path="business_purpose"]').fill('First draft');
  await page.locator('#review-primary-button').click();
  await expect.poll(() => writes).toBe(1);
  await page.locator('a[data-view="income"]').click();
  await page.goBack();
  await expect(page.locator('[data-decision-path="business_purpose"]')).toBeEditable();
  await page.locator('[data-decision-path="business_purpose"]').fill('Newer draft');
  release();
  await expect(page).toHaveURL(new RegExp(`/review/${ID}\\?`));
  await expect
    .poll(() =>
      page.evaluate(
        (id) =>
          JSON.parse(localStorage.getItem('autonomo.review-draft:' + id) || '{}').decision
            ?.business_purpose,
        ID,
      ),
    )
    .toBe('Newer draft');
  await page.locator('#review-primary-button').click();
  await page.locator('[data-locale="en"]').click();
  await expect(page.locator('.review-guided-field [role="alert"]')).toHaveText(
    'Describe the business purpose of this transaction.',
  );
  await expect(page.locator('[data-decision-path="business_purpose"]')).toHaveValue('Newer draft');
});
test('snapshot changes retain only counterparty facts; blocked approval still permits explicit rejection', async ({
  page,
}) => {
  await detail(page);
  const fixture = guidedFixture();
  fixture.packet.snapshot_hash = 'current-snapshot';
  fixture.packet.state.counterparty = { country_code: 'ZZ' };
  fixture.packet.decision.business_purpose = 'Current source purpose';
  fixture.ui_context.state = 'deferred';
  fixture.review_allowed = false;
  fixture.posting_context = { preview_bucket: 'deferred', posting_deferred_until: '2026-10-01' };
  await page.addInitScript(
    ({ id }) =>
      localStorage.setItem(
        'autonomo.review-draft:' + id,
        JSON.stringify({
          review_id: 'transaction:' + id,
          transaction_id: id,
          snapshot_hash: 'old-snapshot',
          decision: {
            business_purpose: 'Old draft purpose',
            tax_treatment: { tax_code: 'reverse_charge' },
            counterparty_changes: { country_code: 'US' },
          },
        }),
      ),
    { id: ID },
  );
  await page.route(/\/api\/review\/work-item\?/, (route) => route.fulfill({ json: fixture }));
  const posted: unknown[] = [];
  await page.route('**/api/review/confirm', (route) => {
    posted.push(route.request().postDataJSON());
    return route.fulfill({ json: { status: 'ok' } });
  });
  await page.goto(path);
  await expect(page.locator('[data-decision-path="business_purpose"]')).toHaveValue(
    'Current source purpose',
  );
  await expect(
    page.locator('[data-decision-path="counterparty_changes.country_code"]'),
  ).toHaveValue('US');
  await expect(page.locator('[data-decision-path="tax_treatment.tax_code"]')).toHaveValue(
    'domestic_output',
  );
  await expect(page.locator('#review-primary-button')).toBeDisabled();
  await page.locator('.review-reject-panel > summary').click();
  await page.locator('#review-reject-button').click();
  expect(posted).toHaveLength(0);
  await page.locator('#review-reject-document-valid').selectOption('false');
  await page.locator('#review-reject-reason').fill('Synthetic duplicate document');
  await page.locator('#review-reject-button').click();
  await expect(page.locator('#review-form')).toHaveCount(0);
  expect(posted).toHaveLength(1);
  expect(posted[0]).toMatchObject({
    packet: {
      decision: {
        outcome: 'reject',
        document_valid: false,
        reason: 'Synthetic duplicate document',
        counterparty_changes: {},
        tax_treatment: { tax_code: null },
      },
    },
    fx: null,
  });
});
test('a blocked approved row in the queue opens its review when the server offers one', async ({
  page,
}) => {
  await native(page);
  await page.route(`**/api/expense-workflows/${ID}`, (route) =>
    route.fulfill({ json: nativeFixture() }),
  );
  await page.route(/\/api\/transactions\?.*status=review/, (route) =>
    route.fulfill({
      json: [
        {
          transaction_id: ID,
          entry_type: 'expense',
          lifecycle_status: 'approved',
          transaction_date: '2026-07-03',
          period_key: '2026-Q3',
          description: 'Synthetic USD expense',
          currency: 'USD',
          amount_original: '99.00',
          amount_eur: null,
          ui_context: {
            domain: 'transaction',
            subject_id: ID,
            state: 'blocked',
            lifecycle_status: 'approved',
            reasons: [],
            posting: { preview_bucket: 'blocked', blockers: [{ code: 'fx_missing' }] },
            actions: [{ kind: 'review', transaction_id: ID, period: '2026-Q3' }],
          },
        },
      ],
    }),
  );
  await page.route('**/api/issues?**', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/documents?**', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/review/posting-preview?**', (route) =>
    route.fulfill({
      json: { period: '2026-Q3', period_status: 'open', ready: [], deferred: [], blocked: [] },
    }),
  );
  await page.goto('/review?period=2026-Q3');
  const row = page
    .locator('#review-tabpanel tbody tr')
    .filter({ hasText: 'Synthetic USD expense' });
  await expect(row).toContainText('Проверено, проведение заблокировано');
  await expect(row.locator('.status-context [data-status-help]')).toHaveText('Что сделать');
  const action = row.locator('td.table-actions > *');
  await expect(action).toHaveCount(1);
  await expect(action).toHaveText('Открыть проверку');
  await expect(action).toHaveClass('secondary-button compact-button');
  await action.click();
  await expect(page).toHaveURL(new RegExp(`/review/${ID}\\?period=2026-Q3$`));
  await expect(page.locator('#wf-form')).toBeVisible();
});
