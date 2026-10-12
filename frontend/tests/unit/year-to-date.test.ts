import { expect, test, vi } from 'vitest';
import type { Analytics } from '../../src/charts/types.ts';
import { setLocale } from '../../src/core/i18n.ts';
import DashboardView from '../../src/features/overview/DashboardView.vue';
import type { DashboardData, OverviewContext } from '../../src/features/overview/model.ts';
import { yearToDate } from '../../src/features/overview/yearToDate.ts';
import { mountView } from '../support/mount.ts';
import fixture from '../fixtures/analytics.json' with { type: 'json' };

vi.mock('../../src/charts/ChartHost.vue', () => ({ default: { render: () => null } }));

function analytics(year: number, asOf: string): Analytics {
  const data = structuredClone(fixture) as unknown as Analytics;
  data.scope = { year, as_of: asOf };
  data.datasets.ytd_comparison.current_year = {
    year,
    taxable_income_minor: 1_000_000,
    deductible_expense_minor: 400_000,
    net_minor: 600_000,
    renta_income_minor: 1_000_000,
    difficult_expense_minor: 30_000,
    renta_expense_minor: 430_000,
    renta_net_minor: 570_000,
  };
  return data;
}

test('the current year shows Renta-rule expenses and net as of today', () => {
  expect(yearToDate(analytics(2026, '2026-10-04'))).toEqual({
    year: 2026,
    income: 10000,
    expenses: 4300,
    net: 5700,
    fullYear: false,
    asOf: '2026-10-04',
    unavailable: false,
  });
});

test('a past year covers the whole year', () => {
  expect(yearToDate(analytics(2025, '2026-10-04'))?.fullYear).toBe(true);
});

test('missing analytics or Renta fields leave the figures empty instead of guessing', () => {
  expect(yearToDate(undefined)).toBeUndefined();
  const legacy = structuredClone(fixture) as unknown as Analytics;
  expect(yearToDate(legacy)).toMatchObject({
    year: 2026,
    income: null,
    expenses: null,
    net: null,
    fullYear: false,
    asOf: null,
    unavailable: false,
  });
});

test('blocked tax data cannot appear as ordinary yearly totals', () => {
  const blocked = analytics(2026, '2026-10-04');
  blocked.datasets.ytd_comparison.current_year.renta_status = 'blocked';
  expect(yearToDate(blocked)).toMatchObject({
    income: null,
    expenses: null,
    net: null,
    unavailable: true,
  });
});

test.each([
  ['ru', 'Годовые суммы недоступны'],
  ['en', 'Yearly figures unavailable'],
] as const)('the dashboard explains unavailable yearly amounts in %s', async (locale, message) => {
  const blocked = analytics(2026, '2026-10-04');
  blocked.datasets.ytd_comparison.current_year.renta_status = 'blocked';
  const data: DashboardData = {
    totals: { actual: { income_eur: 10000, income_transaction_count: 1 }, forecast: {} },
    expense_summary: {
      purchase: { posted: {}, approved: {}, future_approved: {} },
      amortization: { posted: {}, approved: {}, future_approved: {} },
    },
    obligations: [],
    recent_transactions: [],
    open_issues: [],
  };
  const context: OverviewContext = {
    view: 'dashboard',
    period: '2026-Q3',
    services: {
      request: async (url) => (url.startsWith('/api/analytics') ? blocked : data),
      navigate: vi.fn(),
    },
    copyTarget: null,
    settled: vi.fn(),
    refreshCalculation: async () => {},
    copy: vi.fn(),
    notify: vi.fn(),
  };
  const root = document.createElement('div');
  document.body.append(root);
  setLocale(locale);
  const host = mountView(root, DashboardView, context);
  try {
    await vi.waitFor(() => {
      const card = root.querySelector('#dashboard-year-to-date');
      expect(card?.classList.contains('warning')).toBe(true);
      expect(card?.querySelector('[role="status"]')?.textContent).toContain(message);
      expect([...card!.querySelectorAll('dd')].map((cell) => cell.textContent?.trim())).toEqual([
        '—',
        '—',
        '—',
      ]);
    });
  } finally {
    host.dispose();
    root.remove();
    setLocale('ru');
  }
});
