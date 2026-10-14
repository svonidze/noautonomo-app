import { nextTick } from 'vue';
import { afterEach, expect, test, vi } from 'vitest';
import { mountView } from '../support/mount.ts';
import ExpenseWorkflowView from '../../src/features/review/ExpenseWorkflowView.vue';
import type { WorkflowContext } from '../../src/features/review/workflow-model.ts';
import type { JsonOptions } from '../../src/core/http.ts';
import { nativeFixture, REVIEW_ID } from '../fixtures/review.ts';

const cleanup: Array<() => void> = [];
afterEach(() => {
  cleanup.splice(0).forEach((dispose) => dispose());
  document.body.replaceChildren();
});
async function flush() {
  for (let count = 0; count < 8; count++) {
    await Promise.resolve();
    await nextTick();
  }
}

test('an existing expense preview cannot confirm or recalculate after its context becomes read-only', async () => {
  const draft = nativeFixture();
  const request = vi.fn(async (url: string, options?: JsonOptions) => {
    if (url === '/api/counterparties') return [];
    if (url.endsWith('/save'))
      return { ...draft, payload: JSON.parse(String(options?.body)).payload, draft_version: 2 };
    if (url.endsWith('/preview'))
      return {
        preview_token: 'synthetic-token',
        supplier: 'Synthetic',
        period: '2026-Q3',
        currency: 'EUR',
        gross_minor: 12100,
        deductible_vat_minor: 2100,
        deductible_irpf_minor: 10000,
        future_depreciation_minor: 0,
        schedule: [],
      };
    if (options?.method === 'POST') throw new Error('Unexpected write');
    return draft;
  });
  const context: WorkflowContext = {
    transactionId: REVIEW_ID,
    period: '2026-Q3',
    services: { request, navigate: vi.fn() },
    onPosted: vi.fn(),
    onLegacy: vi.fn(),
    settled: vi.fn(),
  };
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, ExpenseWorkflowView, context);
  cleanup.push(() => host.dispose());
  await flush();
  const form = root.querySelector('#wf-form')!;
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  const confirm = root.querySelector<HTMLButtonElement>('#wf-confirm')!;
  expect(confirm.disabled).toBe(false);
  const writesBefore = request.mock.calls.filter(([, options]) => options?.method === 'POST');
  expect(writesBefore.map(([url]) => url.split('/').at(-1))).toEqual(['save', 'preview']);

  host.updateContext({ ...context, services: { ...context.services, readOnly: true } });
  await flush();
  expect(root.querySelector('#wf-form')).toBe(form);
  expect(root.querySelector('#wf-confirm')).toBe(confirm);
  expect(confirm.disabled).toBe(true);
  expect((form.querySelector('button[type="submit"]') as HTMLButtonElement).disabled).toBe(true);
  confirm.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  root
    .querySelector('#wf-save')!
    .dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  await flush();
  expect(request.mock.calls.filter(([, options]) => options?.method === 'POST')).toEqual(
    writesBefore,
  );
  expect(root.querySelector('#wf-confirm')).toBe(confirm);
  expect(context.onPosted).not.toHaveBeenCalled();
});
