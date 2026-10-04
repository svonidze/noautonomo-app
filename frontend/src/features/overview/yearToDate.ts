import type { Analytics, ChartValue } from '../../charts/types.ts';

export interface YearToDate {
  year: number;
  income: number | null;
  expenses: number | null;
  net: number | null;
  fullYear: boolean;
  asOf: string | null;
}

const euros = (minor: ChartValue) => (minor === null || minor === undefined ? null : minor / 100);

// Income, expenses and net of the selected period's year under the Renta rules:
// expenses include the difficult-to-justify allowance, so the figures match Modelo 130.
export function yearToDate(analytics: Analytics | undefined): YearToDate | undefined {
  const current = analytics?.datasets.ytd_comparison?.current_year;
  if (!current) return undefined;
  const asOf = analytics?.scope?.as_of ?? null;
  return {
    year: current.year,
    income: euros(current.renta_income_minor),
    expenses: euros(current.renta_expense_minor),
    net: euros(current.renta_net_minor),
    fullYear: asOf !== null && Number(asOf.slice(0, 4)) > current.year,
    asOf,
  };
}
