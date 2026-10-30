import { nextTick } from 'vue';
import { ApiError } from '../../src/core/http.ts';
import { afterEach, expect, test, vi } from 'vitest';

afterEach(() => {
  window.history.replaceState({}, '', '/');
});
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
  window.history.replaceState({}, '', `/government-cases?case=${caseRow.case_id}`);
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

test('retrying interrupted originals resumes the existing message without a duplicate', async () => {
  window.history.replaceState({}, '', `/government-cases?case=${caseRow.case_id}`);
  let messages = 0,
    uploads = 0;
  const eventId = '33333333-3333-4333-8333-333333333333';
  const current = {
    ...detail,
    correspondence: [],
    correspondence_event_id: eventId,
    row_version: 3,
  };
  const request = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/government-cases') return list;
    if (options?.method === 'POST' && url.endsWith('/correspondence')) {
      messages++;
      return current;
    }
    if (options?.method === 'POST' && url.endsWith('/documents')) {
      uploads++;
      expect(JSON.parse(String((options.body as FormData).get('metadata')))).toMatchObject({
        correspondence_event_id: eventId,
      });
      if (uploads === 1) throw new Error('Synthetic interrupted upload');
      return { ...current, row_version: 4 };
    }
    return structuredClone(detail);
  });
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, GovernmentCasesView, {
    services: { request, navigate: vi.fn() },
    settled: vi.fn(),
    notify: vi.fn(),
  });
  await flush();
  const summary = [...root.querySelectorAll('summary')].find(
    (s) => s.textContent?.trim() === 'Добавить переписку',
  )!;
  const form = summary.parentElement!.querySelector('form')!;
  const file = form.querySelector('input[type=file]')!;
  Object.defineProperty(file, 'files', {
    configurable: true,
    value: [new File(['Original'], 'source.eml', { type: 'message/rfc822' })],
  });
  file.dispatchEvent(new Event('change', { bubbles: true }));
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  expect(messages).toBe(1);
  expect(uploads).toBe(1);
  expect(form.textContent).toContain('Повторите загрузку оставшихся вложений');
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  expect(messages).toBe(1);
  expect(uploads).toBe(2);
  expect(form.textContent).not.toContain('Повторите загрузку оставшихся вложений');
  host.dispose();
  root.remove();
});

test('detail remains available when the parent case list fails and retry preserves edits', async () => {
  window.history.replaceState({}, '', `/government-cases?case=${caseRow.case_id}`);
  let failList = true;
  const request = vi.fn(async (url: string) => {
    if (url === '/api/government-cases') {
      if (failList) throw new Error('Synthetic list outage');
      return list;
    }
    return structuredClone(detail);
  });
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, GovernmentCasesView, {
    services: { request, navigate: vi.fn() },
    settled: vi.fn(),
    notify: vi.fn(),
  });
  await flush();
  expect(root.textContent).toContain('Synthetic list outage');
  expect(root.textContent).toContain('Отказано');
  expect(root.textContent).not.toContain('Ближайшие действия по делам');
  const title = root.querySelector('input[maxlength="200"]') as HTMLInputElement;
  title.value = 'Synthetic unsaved edit';
  title.dispatchEvent(new Event('input', { bubbles: true }));
  expect(host.isDirty()).toBe(true);
  failList = false;
  root
    .querySelector('[role=alert] button')!
    .dispatchEvent(new MouseEvent('click', { bubbles: true }));
  await flush();
  expect(title.value).toBe('Synthetic unsaved edit');
  expect(root.textContent).not.toContain('Synthetic list outage');
  host.dispose();
  root.remove();
});

test('invalid or duplicate case parameters show an error and keep a way back', async () => {
  window.history.replaceState({}, '', '/government-cases?case=bad&case=bad');
  const request = vi.fn(async () => list);
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, GovernmentCasesView, {
    services: { request, navigate: vi.fn() },
    settled: vi.fn(),
    notify: vi.fn(),
  });
  await flush();
  expect(root.querySelector('[role=alert]')).not.toBeNull();
  expect(root.querySelector('a[href="/government-cases"]')).not.toBeNull();
  expect(root.textContent).not.toContain('Ближайшие действия по делам');
  expect(request).toHaveBeenCalledTimes(1);
  host.dispose();
  root.remove();
});

test('partial attachment upload keeps pending files and retries only the remainder', async () => {
  window.history.replaceState({}, '', `/government-cases?case=${caseRow.case_id}`);
  const eventId = '33333333-3333-4333-8333-333333333333';
  const current = {
    ...detail,
    correspondence: [{ event_id: eventId, subject: 'Synthetic message' }],
  };
  const uploaded: string[] = [];
  let attempts = 0;
  const request = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/government-cases') return list;
    if (options?.method === 'POST') {
      const file = (options.body as FormData).get('file') as File;
      uploaded.push(file.name);
      if (++attempts === 2) throw new Error('Synthetic upload failure');
      return current;
    }
    return structuredClone(current);
  });
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, GovernmentCasesView, {
    services: { request, navigate: vi.fn() },
    settled: vi.fn(),
    notify: vi.fn(),
  });
  await flush();
  const summary = [...root.querySelectorAll('summary')].find(
    (s) => s.textContent?.trim() === 'Добавить вложения к сообщению',
  )!;
  const form = summary.parentElement!.querySelector('form')!;
  const select = form.querySelector('select')!;
  select.value = eventId;
  select.dispatchEvent(new Event('change', { bubbles: true }));
  const input = form.querySelector('input[type=file]')!;
  Object.defineProperty(input, 'files', {
    configurable: true,
    value: [new File(['a'], 'a.txt'), new File(['b'], 'b.txt')],
  });
  input.dispatchEvent(new Event('change', { bubbles: true }));
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  expect(uploaded).toEqual(['a.txt', 'b.txt']);
  expect(host.isDirty()).toBe(true);
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  expect(uploaded).toEqual(['a.txt', 'b.txt', 'b.txt']);
  expect(host.isDirty()).toBe(false);
  host.dispose();
  root.remove();
});

test('stale case conflict offers refresh while preserving edits when cancelled', async () => {
  window.history.replaceState({}, '', `/government-cases?case=${caseRow.case_id}`);
  const conflict = new ApiError('Synthetic stale case');
  conflict.code = 'stale_case_version';
  const request = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/government-cases') return list;
    if (options?.method === 'POST') throw conflict;
    return structuredClone(detail);
  });
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, GovernmentCasesView, {
    services: { request, navigate: vi.fn() },
    settled: vi.fn(),
    notify: vi.fn(),
  });
  await flush();
  const title = root.querySelector('input[maxlength="200"]') as HTMLInputElement;
  title.value = 'Synthetic unsaved edit';
  title.dispatchEvent(new Event('input', { bubbles: true }));
  title.closest('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  [...root.querySelectorAll('button')]
    .find((button) => button.textContent?.trim() === 'Обновить дело')!
    .click();
  expect(confirm).toHaveBeenCalledOnce();
  expect(title.value).toBe('Synthetic unsaved edit');
  expect(host.isDirty()).toBe(true);
  confirm.mockRestore();
  host.dispose();
  root.remove();
});

test('older case-list response cannot overwrite a newer refresh', async () => {
  window.history.replaceState({}, '', `/government-cases?case=${caseRow.case_id}`);
  let firstList!: (value: typeof list) => void;
  let listCalls = 0;
  const latestList = {
    ...list,
    rows: [
      {
        ...caseRow,
        case_id: '44444444-4444-4444-8444-444444444444',
        title: 'Synthetic newest parent',
      },
    ],
  };
  const request = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/government-cases') {
      if (++listCalls === 1)
        return new Promise<typeof list>((resolve) => {
          firstList = resolve;
        });
      return latestList;
    }
    if (options?.method === 'POST') return structuredClone(detail);
    return structuredClone(detail);
  });
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, GovernmentCasesView, {
    services: { request, navigate: vi.fn() },
    settled: vi.fn(),
    notify: vi.fn(),
  });
  await flush();
  const actionForm = [...root.querySelectorAll('form')].find((form) =>
    form.textContent?.includes('Новое действие'),
  )!;
  actionForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  expect(root.textContent).toContain('Synthetic newest parent');
  firstList(list);
  await flush();
  expect(root.textContent).toContain('Synthetic newest parent');
  host.dispose();
  root.remove();
});
