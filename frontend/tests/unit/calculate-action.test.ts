import { nextTick, type Component } from 'vue';
import { afterEach, expect, test, vi } from 'vitest';
import DashboardView from '../../src/features/overview/DashboardView.vue';
import TaxesView from '../../src/features/overview/TaxesView.vue';
import { mountOverview } from '../support/mount.ts';
import type { OverviewContext } from '../../src/features/overview/model.ts';
import common from '../../src/locales/ru/common.json' with { type: 'json' };

const unmounts: (() => void)[] = [];
afterEach(() => {
  unmounts.splice(0).forEach((unmount) => unmount());
  vi.unstubAllGlobals();
});
const settle = async () => {
  for (let index = 0; index < 4; index++) {
    await new Promise((resolve) => setTimeout(resolve));
    await nextTick();
  }
};
function mount(
  component: Component,
  view: OverviewContext['view'],
  payload: (read: number) => unknown,
  period = '2026-Q3',
) {
  const mounted = mountOverview(component, view, payload, { period });
  unmounts.push(mounted.unmount);
  const buttons = (selector = '.calculate-button') =>
    [...mounted.root.querySelectorAll<HTMLButtonElement>(selector)].map((button) => [
      button.textContent!.trim(),
      button.disabled,
    ]);
  return { ...mounted, buttons };
}
const scope = { posted: {}, approved: {}, future_approved: {} };
const dashboard = (calculated: boolean) => ({
  totals: { actual: { income_eur: 1000, income_transaction_count: 1 }, forecast: {} },
  expense_summary: { purchase: scope, amortization: scope },
  obligations: [],
  tax_forms: calculated
    ? Object.fromEntries(
        ['130', '303'].map((code) => [
          `modelo${code}`,
          { form_code: code, display_state: 'preview', values: { '1': 10 }, headline_value: 10 },
        ]),
      )
    : {},
  recent_transactions: [],
  open_issues: [],
});

test('an uncalculated quarter offers Calculate on the tax card and in the task list', async () => {
  const view = mount(DashboardView, 'dashboard', (read) => dashboard(read > 1));
  await settle();
  expect(view.buttons()).toEqual([[common['common.calculate'], false]]);
  expect(view.buttons('button.task-row')).toHaveLength(1);
  view.root.querySelector<HTMLButtonElement>('.calculate-button')!.click();
  await settle();
  expect(view.context.refreshCalculation).toHaveBeenCalledTimes(1);
  expect(view.buttons()).toEqual([[common['common.calculating'], true]]);
  expect(view.buttons('button.task-row').map(([, disabled]) => disabled)).toEqual([true]);
  view.release();
  await settle();
  expect(view.context.notify).toHaveBeenCalledWith({
    key: 'refresh.done',
    params: { period: 'III квартал 2026' },
  });
  expect(view.reads).toHaveLength(2);
  expect(view.buttons()).toEqual([]);
  expect(view.buttons('button.task-row')).toEqual([]);
});

test('a quarter that has not started offers no Calculate on the dashboard', async () => {
  const view = mount(DashboardView, 'dashboard', () => dashboard(false), '2999-Q1');
  await settle();
  expect(view.buttons()).toEqual([]);
  expect(view.buttons('button.task-row')).toEqual([]);
});

const taxes = (phase: string, period = '2026-Q3') => ({
  period,
  period_state: { phase },
  obligations: [{ obligation_code: '130', determination: 'due' }],
  tax_forms: {},
  tax_summary: { settlement_status: phase === 'future' ? 'not_started' : 'undetermined' },
});

test('the taxes headline and form cards offer Calculate outside the folded details', async () => {
  const view = mount(TaxesView, 'taxes', () => taxes('current'));
  await settle();
  const idle = [common['common.calculate'], false];
  expect(view.buttons('.tax-hero-copy .calculate-button')).toEqual([idle]);
  expect(view.buttons('.tax-form-amount .calculate-button')).toEqual([idle, idle]);
  expect(view.buttons('details .calculate-button')).toEqual([]);
  view.root.querySelector<HTMLButtonElement>('.tax-hero-copy .calculate-button')!.click();
  await settle();
  expect(view.context.refreshCalculation).toHaveBeenCalledTimes(1);
  expect(view.buttons()).toEqual(Array(3).fill([common['common.calculating'], true]));
  view.release();
  await settle();
  expect(view.context.notify).toHaveBeenCalledWith({
    key: 'refresh.done',
    params: { period: 'III квартал 2026' },
  });
  expect(view.reads).toHaveLength(2);
});

test('a quarter that has not started has no Calculate in the headline or the form cards', async () => {
  for (const [phase, period] of [
    ['future', '2026-Q3'],
    ['current', '2999-Q1'],
  ]) {
    const view = mount(TaxesView, 'taxes', () => taxes(phase, period), period);
    await settle();
    expect(view.buttons(), `${phase} ${period}`).toEqual([]);
  }
});
