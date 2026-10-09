import { defineComponent, h, nextTick, onBeforeUnmount } from 'vue';
import { test, expect, vi } from 'vitest';
import { mountView } from '../support/mount.ts';
import { useLocale } from '../../src/vue/locale.ts';
import { setLocale } from '../../src/core/i18n.ts';
import { helpAdapter } from '../../src/vue/help.ts';
import StatusCell from '../../src/components/StatusCell.vue';

test('host context and locale updates keep one component instance; disposal is idempotent', async () => {
  const disposed = vi.fn(),
    setup = vi.fn();
  const component = defineComponent({
    props: ['context'],
    setup(props) {
      setup();
      onBeforeUnmount(disposed);
      const { t } = useLocale();
      return () => h('span', `${props.context.name} ${t('common.yes')}`);
    },
  });
  const root = document.createElement('div');
  document.body.append(root);
  setLocale('ru');
  const host = mountView(root, component, { name: 'first' });
  host.updateContext({ name: 'next' });
  setLocale('en');
  await nextTick();
  expect(root.textContent).toBe('next Yes');
  expect(setup).toHaveBeenCalledTimes(1);
  host.dispose();
  host.dispose();
  expect(disposed).toHaveBeenCalledTimes(1);
  expect(root.hasAttribute('data-vue-owned')).toBe(false);
  root.remove();
  setLocale('ru');
});
test('Vue status cell owns a stable help record while it links to an explanation', async () => {
  const update = vi.fn(),
    dispose = vi.fn();
  const create = vi
    .spyOn(helpAdapter(), 'createScope')
    .mockReturnValue({ id: 'owned', update, dispose });
  const root = document.createElement('div');
  document.body.append(root);
  // mountView passes its context as the cell's typed context prop. A healthy record shows its
  // badge only: no link and no help record.
  const host = mountView(root, StatusCell, { domain: 'transaction', state: 'posted' });
  expect(root.querySelector('button')).toBeNull();
  expect(create).not.toHaveBeenCalled();
  const blocked = { domain: 'transaction', state: 'blocked', reasons: [{ code: 'fx_missing' }] };
  host.updateContext(blocked);
  await nextTick();
  const link = () => root.querySelector('button')!;
  expect(link().dataset.statusHelp).toBe('owned');
  expect(link().textContent!.trim()).toBe('Почему');
  const actionable = {
    ...blocked,
    actions: [{ kind: 'review', transaction_id: '11111111-1111-4111-8111-111111111111' }],
  };
  host.updateContext(actionable);
  await nextTick();
  expect(update).toHaveBeenCalledWith(actionable);
  expect(link().textContent!.trim()).toBe('Что сделать');
  expect(create).toHaveBeenCalledTimes(1);
  host.dispose();
  expect(dispose).toHaveBeenCalledTimes(1);
  root.remove();
  vi.unstubAllGlobals();
});

test('same-ID context replacement ignores its old request and uses new links/services', async () => {
  const { default: ExpenseDetail } =
    await import('../../src/features/expense-detail/ExpenseDetail.vue');
  const id = '11111111-1111-4111-8111-111111111111';
  let release!: (value: unknown) => void;
  const oldRequest = new Promise((resolve) => {
    release = resolve;
  });
  const oldResolved = vi.fn(),
    resolved = vi.fn(() => ({
      returnUrl: '/expenses?period=2026-Q2&q=new',
      wrongTypeUrl: '/income?period=2026-Q2',
    }));
  const detail = {
    transaction: {
      transaction_id: id,
      entry_type: 'expense',
      lifecycle_status: 'posted',
      currency: 'EUR',
    },
    period: { period_key: '2026-Q2' },
  };
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, ExpenseDetail, {
    transactionId: id,
    returnUrl: '/expenses?period=2026-Q3',
    services: { request: () => oldRequest, navigate: vi.fn() },
    resolved: oldResolved,
    settled: vi.fn(),
  });
  host.updateContext({
    transactionId: id,
    returnUrl: '/expenses?period=2026-Q2&q=new',
    services: { request: () => Promise.resolve(detail), navigate: vi.fn() },
    resolved,
    settled: vi.fn(),
  });
  await nextTick();
  await Promise.resolve();
  await nextTick();
  expect(resolved).toHaveBeenCalledTimes(1);
  release(detail);
  await Promise.resolve();
  await nextTick();
  expect(oldResolved).not.toHaveBeenCalled();
  expect(root.querySelector('.expense-detail > .back-link')?.getAttribute('href')).toBe(
    '/expenses?period=2026-Q2&q=new',
  );
  host.dispose();
  root.remove();
});

test('same-record context changes retain a pending write and refresh using current services', async () => {
  const { default: ExpenseDetail } =
    await import('../../src/features/expense-detail/ExpenseDetail.vue');
  const id = '11111111-1111-4111-8111-111111111111';
  let release!: (value: unknown) => void;
  const write = new Promise((resolve) => {
    release = resolve;
  });
  const detail = {
    transaction: {
      transaction_id: id,
      entry_type: 'expense',
      lifecycle_status: 'posted',
      currency: 'EUR',
    },
    period: { period_key: '2026-Q2' },
    workflow_follow_up: { follow_up_pending: true },
  };
  const firstRequest = vi.fn((_url: string, options?: { method?: string }) =>
    options?.method === 'POST' ? write : Promise.resolve(detail),
  );
  const latestRequest = vi.fn(() =>
    Promise.resolve({ ...detail, workflow_follow_up: { follow_up_pending: false } }),
  );
  const resolved = vi.fn(() => ({
    returnUrl: '/expenses?period=2026-Q2',
    wrongTypeUrl: '/income?period=2026-Q2',
  }));
  const root = document.createElement('div');
  document.body.append(root);
  const context = {
    transactionId: id,
    returnUrl: '/expenses?period=2026-Q2',
    services: { request: firstRequest, navigate: vi.fn() },
    resolved,
    settled: vi.fn(),
  };
  const host = mountView(root, ExpenseDetail, context);
  await Promise.resolve();
  await nextTick();
  (root.querySelector('#expense-follow-up') as HTMLButtonElement).click();
  await nextTick();
  host.updateContext({ ...context, services: { ...context.services, request: latestRequest } });
  await nextTick();
  const button = root.querySelector('#expense-follow-up') as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  button.click();
  expect(firstRequest).toHaveBeenCalledTimes(2);
  expect(latestRequest).not.toHaveBeenCalled();
  release({});
  await Promise.resolve();
  await Promise.resolve();
  await nextTick();
  await nextTick();
  expect(latestRequest).toHaveBeenCalledTimes(1);
  expect(root.querySelector('#expense-follow-up')).toBeNull();
  host.dispose();
  root.remove();
});
