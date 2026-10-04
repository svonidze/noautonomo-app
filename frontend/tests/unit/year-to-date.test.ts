import { expect, test } from 'vitest';
import type { Analytics } from '../../src/charts/types.ts';
import { yearToDate } from '../../src/features/overview/yearToDate.ts';
import fixture from '../fixtures/analytics.json' with { type: 'json' };

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
  });
});
