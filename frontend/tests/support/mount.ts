import { createApp, defineComponent, h, shallowRef, type Component } from 'vue';
import { vi } from 'vitest';
import type { OverviewContext } from '../../src/features/overview/model.ts';

interface ViewGuards {
  canLeave?(): boolean;
  isDirty?(): boolean;
  isBusy?(): boolean;
}
export interface ViewHost<C> {
  updateContext(context: C): boolean;
  dispose(): void;
  canLeave(): boolean;
  isDirty(): boolean;
  isBusy(): boolean;
}
/** A host owns exactly one Vue root; route replacement always disposes it first. */
export function mountView<C extends object>(
  root: HTMLElement,
  component: Component,
  initial: C,
): ViewHost<C> {
  const context = shallowRef(initial);
  const instance = shallowRef<ViewGuards>();
  const app = createApp(
    defineComponent({ setup: () => () => h(component, { context: context.value, ref: instance }) }),
  );
  root.replaceChildren();
  root.dataset.vueOwned = '';
  app.mount(root);
  let active = true;
  return {
    canLeave: () => instance.value?.canLeave?.() ?? true,
    isDirty: () => instance.value?.isDirty?.() ?? false,
    isBusy: () => instance.value?.isBusy?.() ?? false,
    updateContext(value) {
      if (!active) return false;
      if (value === context.value) return true;
      if (instance.value?.canLeave && !instance.value.canLeave()) return false;
      context.value = value;
      return true;
    },
    dispose() {
      if (active) {
        active = false;
        app.unmount();
        delete root.dataset.vueOwned;
      }
    },
  };
}
/** An overview screen over synthetic payloads: `/api/{view}` reads come from `payload`, analytics
 * from `analytics` (a failed read without it), and anything else stays pending. */
export function mountOverview(
  component: Component,
  view: OverviewContext['view'],
  payload: (read: number) => unknown,
  { period = '2026-Q3', analytics }: { period?: string; analytics?: unknown } = {},
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
    if (url.startsWith('/api/analytics?'))
      return analytics
        ? Promise.resolve(analytics)
        : Promise.reject(new Error('Synthetic no chart'));
    return new Promise(() => {}); // RETA stays pending: not under test
  });
  let release!: () => void;
  const context: OverviewContext = {
    view,
    period,
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
  return {
    root,
    context,
    reads,
    release: () => release(),
    unmount() {
      host.dispose();
      root.remove();
    },
  };
}
