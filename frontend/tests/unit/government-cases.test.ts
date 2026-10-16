import { nextTick } from 'vue';
import { expect, test, vi } from 'vitest';
import GovernmentCasesView from '../../src/features/government-cases/GovernmentCasesView.vue';
import type { GovernmentContext } from '../../src/features/government-cases/model.ts';
import { mountView } from '../support/mount.ts';

async function flush() {
  for (let i = 0; i < 8; i++) {
    await Promise.resolve();
    await nextTick();
  }
}
const caseRow = {
  case_id: '11111111-1111-4111-8111-111111111111',
  authority_code: 'TGSS',
  title: 'Synthetic appeal',
  subject: 'Synthetic subject',
  primary_reference: 'SYN-001',
  current_status: 'rejected',
  work_state: 'action_required',
  row_version: 2,
};
const detail = {
  ...caseRow,
  actions: [
    {
      action_id: '22222222-2222-4222-8222-222222222222',
      case_id: caseRow.case_id,
      kind: 'decide_appeal',
      description: 'Synthetic next decision',
      due_on: null,
      deadline_source: '',
      confirmed: 0,
      status: 'open',
      row_version: 1,
    },
  ],
  documents: [],
  events: [],
};
const list = {
  rows: [caseRow],
  actions: detail.actions.map((a) => ({ ...a, title: caseRow.title })),
  reminders: { configured: false, worker_stale: true, messages: [] },
};

test('case page keeps a rejection actionable and submits reviewed action metadata', async () => {
  const request = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/government-cases') return list;
    if (options?.method === 'POST') return { ...detail, row_version: 3 };
    return structuredClone(detail);
  });
  const context: GovernmentContext = {
    services: { request, navigate: vi.fn() },
    settled: vi.fn(),
    notify: vi.fn(),
  };
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, GovernmentCasesView, context);
  await flush();
  const select = [...root.querySelectorAll('button')].find((b) =>
    b.textContent?.includes('TGSS · Synthetic appeal'),
  )!;
  select.click();
  await flush();
  expect(root.textContent).toContain('Отказано');
  expect(root.textContent).toContain('Срок требует проверки');
  const edit = [...root.querySelectorAll('button')].find(
    (b) => b.textContent?.trim() === 'Изменить',
  )!;
  edit.click();
  await flush();
  const save = [...root.querySelectorAll('button')].find(
    (b) => b.textContent?.trim() === 'Сохранить действие',
  )!;
  const form = save.closest('form')!;
  const due = form.querySelector('input[type=date]') as HTMLInputElement;
  due.value = '2032-04-10';
  due.dispatchEvent(new Event('input', { bubbles: true }));
  const checkbox = form.querySelector('input[type=checkbox]') as HTMLInputElement;
  expect(checkbox.checked).toBe(false);
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  const write = request.mock.calls.find(([, options]) => options?.method === 'POST')!;
  expect(JSON.parse(String(write[1]?.body))).toMatchObject({
    expected_row_version: 2,
    action_version: 1,
    confirmed: false,
    due_on: '2032-04-10',
  });
  host.dispose();
  root.remove();
});

test('case loading failure has a retry and does not show an empty successful result', async () => {
  const request = vi.fn().mockRejectedValue(new Error('Synthetic outage'));
  const context: GovernmentContext = {
    services: { request, navigate: vi.fn() },
    settled: vi.fn(),
    notify: vi.fn(),
  };
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, GovernmentCasesView, context);
  await flush();
  expect(root.querySelector('[role=alert]')?.textContent).toContain('Synthetic outage');
  expect(root.textContent).not.toContain('Открытых действий нет');
  root.querySelector('button')!.click();
  await flush();
  expect(request).toHaveBeenCalledTimes(2);
  host.dispose();
  root.remove();
});
