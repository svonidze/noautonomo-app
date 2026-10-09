import { createApp, nextTick } from 'vue';
import { afterEach, expect, test, vi } from 'vitest';
import ChartHost from '../../src/charts/ChartHost.vue';
import { createChartBuilders, hasEnoughData, type ChartKind } from '../../src/charts/builders.ts';
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

test('a chart is drawn from two non-zero values, never from one, zeros or gaps', () => {
  expect(hasEnoughData(spec([6500, 0, 0]))).toBe(false);
  expect(hasEnoughData(spec([6500, 0, 6500]))).toBe(true);
  expect(hasEnoughData(spec([0, 0, 0]))).toBe(false);
  expect(hasEnoughData(spec([null, 6500, undefined]))).toBe(false);
  expect(hasEnoughData(spec([-1200, 6500]))).toBe(true);
  // Two series in one month are two values: income beside a deductible expense is a comparison.
  const business = createChartBuilders('ru').businessResult(fixture as unknown as Analytics);
  for (const series of business.series!) series.values = series.values.map(() => 0);
  business.series![0].values[0] = 10000;
  expect(hasEnoughData(business)).toBe(false);
  business.series![3].values[0] = 5000;
  expect(hasEnoughData(business)).toBe(true);
  expect(spec([6500, 6500]).buckets).toEqual(['I кв. 2026', 'II кв. 2026']);
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

test('below the threshold the slot is one line of text without a chart or an expand control', async () => {
  const root = await mount(amortization([6500, 0, 0]));
  expect([...root.querySelectorAll('.chart-empty')].map((line) => line.textContent)).toEqual([
    `${charts['charts.amortization.title']}. ${charts['charts.amortization.empty']}`,
  ]);
  expect(root.querySelectorAll('svg, figure, .chart-expand-button')).toHaveLength(0);
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
