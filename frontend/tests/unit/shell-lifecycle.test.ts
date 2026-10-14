import { createApp, defineComponent, h, nextTick } from 'vue';
import { expect, test, vi } from 'vitest';
vi.mock('../../src/shell/router.ts', async () => {
  const { createRouter, createMemoryHistory } = await import('vue-router');
  return {
    createProductionRouter: () =>
      createRouter({
        history: createMemoryHistory(),
        routes: [{ path: '/:pathMatch(.*)*', component: { render: () => null } }],
      }),
  };
});
vi.mock('../../src/shell/request.ts', () => ({
  request: vi.fn(async () => ({
    periods: [{ period_key: '2026-Q3', status: 'open' }],
    default_period: '2026-Q3',
    profile_name: 'Synthetic',
    intake_enabled: false,
  })),
  refreshCalculation: vi.fn(),
}));
import { createProductionRouter } from '../../src/shell/router.ts';
import { appRuntimeKey } from '../../src/shell/runtime.ts';
import { createProductionRuntime } from '../../src/shell/production-runtime.ts';
const router = createProductionRouter();
const productionRuntime = createProductionRuntime(router);
import { refreshCalculation } from '../../src/shell/request.ts';
import { useShell } from '../../src/shell/controller.ts';
import { markStale, postingState } from '../../src/features/posting/state.ts';
import type { OverviewContext } from '../../src/features/overview/model.ts';
async function flush() {
  for (let i = 0; i < 15; i++) {
    await Promise.resolve();
    await nextTick();
  }
}
test('a delayed canonical navigation continuation cannot reclaim a newer route', async () => {
  await router.push('/dashboard?period=2026-Q3');
  let shell!: ReturnType<typeof useShell>;
  const app = createApp(
    defineComponent({
      setup() {
        shell = useShell();
        return () => h('div');
      },
    }),
  );
  const root = document.createElement('div');
  document.body.append(root);
  app.provide(appRuntimeKey, productionRuntime);
  app.mount(root);
  await vi.waitFor(() => expect(shell.route.value?.view).toBe('dashboard'));
  let release!: () => void,
    waiting = false;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const replace = router.replace.bind(router);
  vi.spyOn(router, 'replace').mockImplementation(async (to) => {
    const result = await replace(to);
    if (typeof to === 'object' && 'path' in to && to.path === '/expenses') {
      waiting = true;
      await gate;
    }
    return result;
  });
  await router.push('/expenses');
  await vi.waitFor(() => expect(waiting).toBe(true));
  await router.push('/settings');
  await flush();
  expect(shell.route.value?.view).toBe('settings');
  const context = shell.screen.value;
  release();
  await flush();
  expect(shell.route.value?.view).toBe('settings');
  expect(shell.screen.value).toBe(context);
  expect(router.currentRoute.value.path).toBe('/settings');
  app.unmount();
  root.remove();
  vi.unstubAllGlobals();
});

test('shell teardown releases help listeners and remount creates a fresh registry', async () => {
  const { helpAdapter } = await import('../../src/vue/help.ts');
  const component = defineComponent({
    setup() {
      useShell();
      return () => h('div');
    },
  });
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp(component);
  app.provide(appRuntimeKey, productionRuntime);
  app.mount(root);
  await flush();
  const first = helpAdapter(),
    dispose = vi.spyOn(first, 'dispose');
  const old = first.createScope({ domain: 'transaction', state: 'ready' });
  const remove = vi.spyOn(document, 'removeEventListener');
  app.unmount();
  expect(dispose).toHaveBeenCalledOnce();
  expect(remove.mock.calls.length).toBeGreaterThanOrEqual(7);
  const next = createApp(component);
  next.provide(appRuntimeKey, productionRuntime);
  next.mount(root);
  await flush();
  const second = helpAdapter();
  expect(second).not.toBe(first);
  expect(second.createScope({ domain: 'transaction', state: 'ready' }).id).not.toBe(old.id);
  next.unmount();
  root.remove();
});

test('an in-page Calculate runs under the shell busy flag, after a running one, and clears the stale marker', async () => {
  await router.push('/dashboard?period=2026-Q3');
  let shell!: ReturnType<typeof useShell>;
  const app = createApp(
    defineComponent({
      setup() {
        shell = useShell();
        return () => h('div');
      },
    }),
  );
  const root = document.createElement('div');
  document.body.append(root);
  app.provide(appRuntimeKey, productionRuntime);
  app.mount(root);
  await vi.waitFor(() => expect(shell.screen.value).toBeTruthy());
  const context = shell.screen.value!.context as OverviewContext;
  const releases: (() => void)[] = [];
  vi.mocked(refreshCalculation).mockImplementation(
    () => new Promise<void>((resolve) => releases.push(resolve)),
  );
  markStale('2026-Q3');
  const first = context.refreshCalculation(),
    second = context.refreshCalculation();
  await flush();
  expect(shell.busy.value).toBe(true);
  expect(releases).toHaveLength(1);
  releases[0]();
  await flush();
  expect(postingState.stale.has('2026-Q3')).toBe(false);
  expect(releases).toHaveLength(2);
  expect(shell.busy.value).toBe(true);
  releases[1]();
  await Promise.all([first, second]);
  expect(shell.busy.value).toBe(false);
  expect(vi.mocked(refreshCalculation).mock.calls).toEqual([['2026-Q3'], ['2026-Q3']]);
  app.unmount();
  root.remove();
});
