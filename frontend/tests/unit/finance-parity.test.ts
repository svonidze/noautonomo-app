import { createApp, nextTick } from 'vue';
import { expect, test } from 'vitest';
import TaxFormCard from '../../src/features/overview/TaxFormCard.vue';
import { formatDateText } from '../../src/core/format.ts';
import { createFinancePresentation } from '../../src/features/overview/presentation.ts';
import { operationRequest } from '../../src/core/operation-request.ts';
import { normalizeLocale, t } from '../../src/core/i18n.ts';
import expected from '../fixtures/finance-expected.json' with { type: 'json' };
for (const row of expected)
  test(`tax status and evidence match reviewed baseline in ${row.locale}`, () => {
    const presentation = createFinancePresentation(normalizeLocale(row.locale));
    for (const value of row.headlines)
      expect(presentation.taxHeadlineModel({ phase: value.phase }, value.summary)).toEqual(
        value.expected,
      );
    for (const value of row.forms) {
      expect(
        presentation.formCardData(value.form, value.obligation, { warnOnMissingHeadline: true }),
      ).toEqual(value.expected);
      expect(presentation.formSubtitle(value.form, value.obligation)).toBe(value.subtitle);
    }
  });
test('depreciation retries keep their request ID without storage and rotate on a new version', () => {
  let generated = 0;
  const denied = () => {
    throw new Error('storage disabled');
  };
  const uuid = () => `synthetic-request-${++generated}`;
  const first = operationRequest('synthetic-depreciation-key', 'depreciation', 1, denied, uuid);
  expect(operationRequest('synthetic-depreciation-key', 'depreciation', 1, denied, uuid)).toBe(
    first,
  );
  expect(
    operationRequest('synthetic-depreciation-key', 'depreciation', 2, denied, uuid).request_id,
  ).not.toBe(first.request_id);
  expect(generated).toBe(2);
});

test('missing filed headlines warn only on dashboard, while zero stays a value', () => {
  const presentation = createFinancePresentation('en');
  const obligation = { obligation_code: '130', determination: 'due', filing_status: 'filed' };
  const form = { form_code: '130', display_state: 'filed', values: {}, headline_value: null };
  expect(
    presentation.formCardData(form, obligation, { warnOnMissingHeadline: true }).display_state,
  ).toBe('filed_without_values');
  expect(presentation.formCardData(form, obligation).display_state).toBe('filed');
  expect(
    presentation.formCardData({ ...form, display_state: 'snapshot_only' }, obligation)
      .display_state,
  ).toBe('snapshot_only');
  expect(
    presentation.formCardData({ ...form, headline_value: 0 }, obligation, {
      warnOnMissingHeadline: true,
    }).display_state,
  ).toBe('filed');
});

test('a due, unfiled return names its deadline in the filing cell and keeps the deadline row', async () => {
  const form = { form_code: '303', display_state: 'preview', values: {}, headline_value: null };
  for (const [statutory, filing] of [
    ['2026-10-20', t('statuses.filingDueBy', { date: formatDateText('2026-10-20', 'ru') }, 'ru')],
    [null, t('statuses.labels.due', {}, 'ru')],
  ] as const) {
    const root = document.createElement('div');
    document.body.append(root);
    const app = createApp(TaxFormCard, {
      code: '303',
      form,
      summary: { determination: 'due', filing_status: 'unfiled', statutory_due_on: statutory },
      refreshing: false,
    });
    app.mount(root);
    await nextTick();
    const cells = [...root.querySelectorAll('.tax-form-facts dd')].map((cell) => cell.textContent);
    expect(cells[0]).toBe(filing);
    expect(cells[1]).toBe(formatDateText(statutory, 'ru'));
    app.unmount();
    root.remove();
  }
});
