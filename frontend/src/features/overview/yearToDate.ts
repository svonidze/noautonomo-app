import type { Analytics, ChartValue } from '../../charts/types.ts';

export interface YearToDate {
  year: number;
  income: number | null;
  expenses: number | null;
  net: number | null;
  fullYear: boolean;
  asOf: string | null;
  unavailable: boolean;
  provisional: boolean;
}

const euros = (minor: ChartValue) => (minor === null || minor === undefined ? null : minor / 100);

// Income, expenses and net of the selected period's year under the Renta rules:
// Annual adjustments belong to Renta; they do not change quarterly Modelo 130.
export function yearToDate(analytics: Analytics | undefined): YearToDate | undefined {
  const current = analytics?.datasets.ytd_comparison?.current_year;
  if (!current) return undefined;
  const asOf = analytics?.scope?.as_of ?? null;
  const unavailable = current.renta_status === 'blocked';
  return {
    year: current.year,
    income: unavailable ? null : euros(current.renta_income_minor),
    expenses: unavailable ? null : euros(current.renta_expense_minor),
    net: unavailable ? null : euros(current.renta_net_minor),
    fullYear: asOf !== null && Number(asOf.slice(0, 4)) > current.year,
    asOf,
    unavailable,
    provisional: current.renta_status === 'provisional',
  };
}
