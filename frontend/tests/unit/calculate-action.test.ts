import { nextTick, type Component } from 'vue';
import { afterEach, expect, test, vi } from 'vitest';
import DashboardView from '../../src/features/overview/DashboardView.vue';
import TaxesView from '../../src/features/overview/TaxesView.vue';
import { mountView } from '../support/mount.ts';
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
) {
  // jsdom has no dialog methods; ChartHost closes its expanded view on unmount.
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
  const reads: string[] = [];
  const request = vi.fn((url: string): Promise<unknown> => {
    if (url.startsWith(`/api/${view}?`)) {
      reads.push(url);
      return Promise.resolve(payload(reads.length));
    }
    if (url.startsWith('/api/analytics?')) return Promise.reject(new Error('Synthetic no chart'));
    return new Promise(() => {}); // RETA stays pending: not under test
  });
  let release!: () => void;
  const context: OverviewContext = {
    view,
    period: '2026-Q3',
    services: { request, navigate: vi.fn() },
    copyTarget: null,
    settled: vi.fn(),
    refreshCalculation: vi.fn(() => new Promise<void>((resolve) => (release = resolve))),
    copy: vi.fn(),
    notify: vi.fn(),
  };
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, component, context);
  unmounts.push(() => {
    host.dispose();
    root.remove();
  });
  const buttons = (selector = '.calculate-button') =>
    [...root.querySelectorAll<HTMLButtonElement>(selector)].map((button) => [
      button.textContent!.trim(),
      button.disabled,
    ]);
  return { root, context, reads, buttons, release: () => release() };
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

test('an uncalculated quarter offers Calculate on the dashboard Modelo cards', async () => {
  const view = mount(DashboardView, 'dashboard', (read) => dashboard(read > 1));
  await settle();
  const idle = [common['common.calculate'], false];
  expect(view.buttons()).toEqual([idle, idle]);
  view.root.querySelector<HTMLButtonElement>('.calculate-button')!.click();
  await settle();
  expect(view.context.refreshCalculation).toHaveBeenCalledTimes(1);
  const busy = [common['common.calculating'], true];
  expect(view.buttons()).toEqual([busy, busy]);
  view.release();
  await settle();
  expect(view.context.notify).toHaveBeenCalledWith({
    key: 'refresh.done',
    params: { period: 'III квартал 2026' },
  });
  expect(view.reads).toHaveLength(2);
  expect(view.buttons()).toEqual([]);
});

const taxes = (phase: string) => ({
  period: '2026-Q3',
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

test('a quarter that has not started has no Calculate in the headline', async () => {
  const view = mount(TaxesView, 'taxes', () => taxes('future'));
  await settle();
  expect(view.buttons('.tax-hero-copy .calculate-button')).toEqual([]);
});
