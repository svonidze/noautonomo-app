import { nextTick } from 'vue';
import { afterEach, expect, test, vi } from 'vitest';
import DashboardView from '../../src/features/overview/DashboardView.vue';
import { eur, formatDateText } from '../../src/core/format.ts';
import { mountOverview } from '../support/mount.ts';
import type { DashboardData, FormView } from '../../src/features/overview/model.ts';

// Synthetic payloads only; the figure cards read the dashboard payload as the server sends it.
const none = { count: 0, missing_amount_count: 0, amount_eur: '0.00', known_amount_minor: 0 };
const kinds = { posted: none, approved: none, future_approved: none };
function dashboard(overrides: Partial<DashboardData> = {}): DashboardData {
  return {
    totals: { actual: { income_eur: '0.00', income_transaction_count: 0 }, forecast: {} },
    expense_summary: { purchase: kinds, amortization: kinds },
    obligations: [],
    tax_forms: {},
    recent_transactions: [],
    open_issues: [],
    ...overrides,
  };
}
const unmounts: (() => void)[] = [];
afterEach(() => {
  unmounts.splice(0).forEach((unmount) => unmount());
  vi.unstubAllGlobals();
});
async function card(data: DashboardData, label: string, period = '2026-Q3') {
  const view = mountOverview(DashboardView, 'dashboard', () => data, { period });
  unmounts.push(view.unmount);
  for (let index = 0; index < 4; index++) {
    await new Promise((resolve) => setTimeout(resolve));
    await nextTick();
  }
  return [...view.root.querySelectorAll<HTMLElement>('.metric')].find(
    (metric) => metric.querySelector(':scope > span')!.textContent === label,
  )!;
}
/** [amount or null, note] per tax line of the «Налог к уплате» card. */
const taxLines = (metric: HTMLElement) =>
  [...metric.querySelectorAll('dd')].map((line) => [
    line.querySelector('strong')?.textContent ?? null,
    line.querySelector('small')!.textContent,
  ]);
const preview = (code: string, headline_value: string | null): FormView => ({
  form_code: code,
  display_state: 'preview',
  values: {},
  headline_value,
  preview_as_of: '2026-10-01',
});

test('a tax line shows its amount, or «—» only when nothing explains the gap', async () => {
  const tax = await card(
    dashboard({
      tax_forms: { modelo130: preview('130', '120.00'), modelo303: preview('303', null) },
    }),
    'Налог к уплате',
  );
  const note = `Предварительный расчёт на ${formatDateText('2026-10-01', 'ru')}`;
  expect(taxLines(tax)).toEqual([
    [eur('120.00', 'ru'), note],
    ['—', note],
  ]);
  expect(tax.querySelectorAll('.calculate-button')).toHaveLength(0);
});

test('a missing calculation is said in words, and a future quarter says it has not started', async () => {
  const unavailable = await card(dashboard(), 'Налог к уплате');
  expect(taxLines(unavailable)).toEqual([
    [null, 'Расчёт не выполнен'],
    [null, 'Расчёт не выполнен'],
  ]);
  expect(unavailable.querySelectorAll('.calculate-button')).toHaveLength(1);
  const future = await card(dashboard(), 'Налог к уплате', '2999-Q1');
  expect(taxLines(future)).toEqual([
    [null, 'Квартал ещё не начался'],
    [null, 'Квартал ещё не начался'],
  ]);
  expect(future.querySelectorAll('.calculate-button')).toHaveLength(0);
});

test('reviewed income names its amount, keeps a known part beside rows without EUR, else counts', async () => {
  const line = async (forecast: DashboardData['totals']['forecast']) => {
    const income = await card(
      dashboard({
        totals: { actual: { income_eur: '0.00', income_transaction_count: 0 }, forecast },
      }),
      'Доходы, проведено',
    );
    return [...income.querySelectorAll('small')].map((small) => small.textContent).slice(1);
  };
  const reviewed = (value: string) => [`Проверено, ещё не проведено: ${value}`];
  expect(
    await line({
      income_transaction_count: 2,
      income_known_amount_minor: 50000,
      income_missing_amount_count: 0,
    }),
  ).toEqual(reviewed(eur('500.00', 'ru')));
  expect(
    await line({
      income_transaction_count: 3,
      income_known_amount_minor: 50000,
      income_missing_amount_count: 1,
    }),
  ).toEqual(reviewed(`${eur('500.00', 'ru')} и 1 операция без суммы в EUR`));
  // Nothing known: every row lacks a EUR amount, or the server sends no amounts at all.
  expect(
    await line({
      income_transaction_count: 2,
      income_known_amount_minor: 0,
      income_missing_amount_count: 2,
    }),
  ).toEqual(reviewed('2 операции'));
  expect(await line({ income_transaction_count: 2 })).toEqual(reviewed('2 операции'));
  expect(await line({})).toEqual([]);
});
