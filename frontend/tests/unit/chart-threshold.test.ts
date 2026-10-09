import { createApp, nextTick } from 'vue';
import { afterEach, expect, test, vi } from 'vitest';
import ChartHost from '../../src/charts/ChartHost.vue';
import { chartValueCount, createChartBuilders, type ChartKind } from '../../src/charts/builders.ts';
import type { Analytics, ChartValue } from '../../src/charts/types.ts';
import fixture from '../fixtures/analytics.json' with { type: 'json' };
import charts from '../../src/locales/ru/charts.json' with { type: 'json' };

// Synthetic analytics: one quarterly series, so each bucket holds one value.
function amortization(totals: ChartValue[]) {
  const data = structuredClone(fixture) as unknown as Analytics;
  data.datasets.amortization = {
    points: totals.map((total_minor, index) => ({ period_key: `2026-Q${index + 1}`, total_minor })),
  };
  return data;
}
const spec = (totals: ChartValue[]) => createChartBuilders('ru').amortization(amortization(totals));
// Quarterly tax due: [Modelo 130, Modelo 303] payable per quarter.
function taxDue(payables: [ChartValue, ChartValue][]) {
  const data = structuredClone(fixture) as unknown as Analytics;
  data.datasets.quarterly_tax_due = {
    points: payables.map(([m130, m303], index) => {
      const period_key = `2026-Q${index + 1}`;
      return {
        period_key,
        modelo130: { period_key, payable_minor: m130, source: 'preview' },
        modelo303: { period_key, payable_minor: m303, source: 'preview' },
      };
    }),
  };
  return data;
}

test('a chart counts its non-zero plotted values: zeros and gaps do not count', () => {
  expect(
    [
      [6500, 0, 0],
      [6500, 0, 6500],
      [0, 0, 0],
      [null, 6500, undefined],
      [-1200, 6500],
    ].map((totals) => chartValueCount(spec(totals))),
  ).toEqual([1, 2, 0, 1, 2]);
  expect(
    chartValueCount(
      createChartBuilders('ru').taxDue(
        taxDue([
          [26391, 0],
          [null, null],
        ]),
      ),
    ),
  ).toBe(1);
  // Two series in one month are two values: income beside a deductible expense is a comparison.
  const business = createChartBuilders('ru').businessResult(fixture as unknown as Analytics);
  for (const series of business.series!) series.values = series.values.map(() => 0);
  business.series![0].values[0] = 10000;
  expect(chartValueCount(business)).toBe(1);
  business.series![3].values[0] = 5000;
  expect(chartValueCount(business)).toBe(2);
  expect(spec([6500, 6500]).buckets).toEqual(['I кв. 2026', 'II кв. 2026']);
});

test('a phone-width quarter axis names the year once, in the column header', () => {
  const compact = (locale: 'ru' | 'en', data: Analytics) =>
    createChartBuilders(locale, { compactQuarters: true }).amortization(data);
  const year = compact('ru', amortization([1, 2, 3, 4]));
  expect([year.bucketLabel, year.buckets]).toEqual([
    'Квартал, 2026',
    ['I кв.', 'II кв.', 'III кв.', 'IV кв.'],
  ]);
  expect(compact('en', amortization([1, 2])).buckets).toEqual(['Q1', 'Q2']);
  // Quarters of two years keep their years under the bars.
  const span = amortization([1, 2]);
  span.datasets.amortization.points[1]!.period_key = '2027-Q1';
  expect([compact('ru', span).bucketLabel, compact('ru', span).buckets]).toEqual([
    'Квартал',
    ['I кв. 2026', 'I кв. 2027'],
  ]);
});

const roots: (() => void)[] = [];
afterEach(() => {
  roots.splice(0).forEach((dispose) => dispose());
  vi.unstubAllGlobals();
});
async function mount(analytics: Analytics, kind: ChartKind = 'amortization') {
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value(this: HTMLDialogElement) {
      this.open = false;
    },
  });
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp(ChartHost, { kind, period: '2026-Q3', analytics });
  app.mount(root);
  roots.push(() => {
    app.unmount();
    root.remove();
  });
  for (let index = 0; index < 3; index++) await nextTick();
  return root;
}

const line = (root: HTMLElement) =>
  [...root.querySelectorAll('.chart-empty')].map((node) => node.textContent!.trim());

test('without a non-zero value the slot is one line with the chart empty message', async () => {
  const root = await mount(amortization([0, 0, 0]));
  expect(line(root)).toEqual([
    `${charts['charts.amortization.title']}. ${charts['charts.amortization.empty']}`,
  ]);
  expect(root.querySelectorAll('svg, figure, .chart-expand-button')).toHaveLength(0);
});

test('one value is not "no data": the line says a chart needs a second one', async () => {
  for (const [data, kind] of [
    [amortization([6500, 0, 0]), 'amortization'],
    [
      taxDue([
        [26391, 0],
        [null, null],
      ]),
      'taxDue',
    ],
  ] as const) {
    const root = await mount(data, kind);
    expect(line(root), kind).toEqual([
      `${kind === 'taxDue' ? 'Налоги к оплате по кварталам' : 'Амортизация по кварталам'}: пока одно значение, для графика нужно хотя бы два`,
    ]);
    expect(root.querySelectorAll('svg, figure, .chart-expand-button'), kind).toHaveLength(0);
  }
});

test('from two non-zero values the chart, its table and its expand control are drawn', async () => {
  const root = await mount(amortization([6500, 0, 6500]));
  expect(root.querySelectorAll('.chart-empty')).toHaveLength(0);
  expect(root.querySelectorAll('svg.chart-svg')).toHaveLength(1);
  expect(root.querySelectorAll('.chart-expand-button')).toHaveLength(1);
  expect(root.querySelector('table')!.textContent).toContain('III кв. 2026');
});

test('cumulative purchases with zero known totals still say that rows lack an exchange rate', async () => {
  const data = structuredClone(fixture) as unknown as Analytics;
  data.datasets.cumulative_expenses = {
    buckets: ['2026-01', '2026-02'],
    gross_actual_minor: [0, 0],
    gross_projected_minor: [0, 0],
    deductible_actual_minor: [0, 0],
    deductible_projected_minor: [0, 0],
    missing_fx_transaction_count: 1,
  };
  const root = await mount(data, 'cumulativeExpenses');
  expect(root.querySelector('.chart-empty')!.textContent!.trim()).toBe(
    `${charts['charts.cumulativeExpenses.title']}. ${charts['charts.empty.missingFx']}`,
  );
});
