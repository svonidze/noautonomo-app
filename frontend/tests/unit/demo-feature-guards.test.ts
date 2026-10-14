import { afterEach, expect, test, vi } from 'vitest';
import { createApp, h, nextTick, ref, type Component } from 'vue';
import { mountView } from '../support/mount.ts';
import { setLocale } from '../../src/core/i18n.ts';
import type { ViewServices } from '../../src/vue/services.ts';
import GuidedReviewView from '../../src/features/review/GuidedReviewView.vue';
import ExpenseWorkflowView from '../../src/features/review/ExpenseWorkflowView.vue';
import ReviewOverviewView from '../../src/features/posting/ReviewOverviewView.vue';
import SettingsView from '../../src/features/settings/SettingsView.vue';
import AeatDocumentsView from '../../src/features/aeat-documents/AeatDocumentsView.vue';
import AssetSchedule from '../../src/features/overview/AssetSchedule.vue';
import RenameDialog from '../../src/features/contacts/RenameDialog.vue';
import IntakeDialog from '../../src/features/intake/IntakeDialog.vue';
import IncomeTable from '../../src/features/transactions/IncomeTable.vue';
import DocumentPreview from '../../src/components/DocumentPreview.vue';
import { guidedFixture, nativeFixture, REVIEW_ID, DOCUMENT_ID } from '../fixtures/review.ts';

const cleanup: Array<() => void> = [];
afterEach(() => {
  cleanup.splice(0).forEach((dispose) => dispose());
  document.body.replaceChildren();
  vi.restoreAllMocks();
  setLocale('ru');
});
async function flush() {
  for (let n = 0; n < 8; n++) {
    await Promise.resolve();
    await nextTick();
  }
}
function root() {
  const element = document.createElement('div');
  document.body.append(element);
  return element;
}
function mount(component: Component, context: object) {
  const element = root();
  const host = mountView(element, component, context);
  cleanup.push(() => host.dispose());
  return element;
}
function leaf(component: Component, props: object) {
  const element = root();
  const app = createApp({ render: () => h(component, props) });
  app.mount(element);
  cleanup.push(() => app.unmount());
  return element;
}
function services(request: ViewServices['request']): ViewServices {
  return {
    readOnly: true,
    request,
    navigate: vi.fn(),
    href: (url) => (url.startsWith('/api/') ? '/demo' + url : '/demo/?lang=en#' + url),
    documentUrl: (id, preview = false) =>
      `/demo/api/document/${id}/${preview ? 'preview' : 'content'}`,
  };
}
const submit = (form: Element) =>
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
const click = (button: Element) =>
  button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
const writes = (request: ReturnType<typeof vi.fn>) =>
  request.mock.calls.filter(([, options]) => options?.method && options.method !== 'GET');

test('guided demo never reads or persists local review drafts and rejects forged submits', async () => {
  const storage = ['getItem', 'setItem', 'removeItem'].map((method) =>
    vi.spyOn(Storage.prototype, method as 'getItem').mockImplementation(() => {
      throw new Error('storage must stay untouched');
    }),
  );
  const request = vi.fn(async () => guidedFixture());
  const element = mount(GuidedReviewView, {
    transactionId: REVIEW_ID,
    returnUrl: '/review',
    knownPeriods: ['2026-Q3'],
    services: services(request),
    resolved: () => '/review',
    settled: vi.fn(),
    complete: vi.fn(),
    posted: vi.fn(),
    notify: vi.fn(),
  });
  await flush();
  expect(element.querySelector('#review-primary-button')).not.toBeNull();
  expect((element.querySelector('#review-primary-button') as HTMLButtonElement).disabled).toBe(
    true,
  );
  submit(element.querySelector('#review-form')!);
  click(element.querySelector('#review-reject-button')!);
  await flush();
  expect(writes(request)).toEqual([]);
  storage.forEach((spy) => expect(spy).not.toHaveBeenCalled());
  expect(element.querySelector('a[href="/demo/?lang=en#/review"]')).not.toBeNull();
});

test('expense demo uses the real HTTP document preview, freezes fields and keeps search usable', async () => {
  const draft = nativeFixture();
  draft.source.document.mime_type = 'application/pdf';
  const request = vi.fn(async (url: string) => (url === '/api/counterparties' ? [] : draft));
  const element = mount(ExpenseWorkflowView, {
    transactionId: REVIEW_ID,
    period: '2026-Q3',
    services: services(request),
    onPosted: vi.fn(),
    onLegacy: vi.fn(),
    settled: vi.fn(),
  });
  await flush();
  expect(element.querySelector('iframe')?.getAttribute('src')).toBe(
    `/demo/api/document/${DOCUMENT_ID}/preview`,
  );
  expect(
    (element.querySelector('input[data-p="facts.document_number"]') as HTMLInputElement).readOnly,
  ).toBe(true);
  expect((element.querySelector('#wf-supplier-search') as HTMLInputElement).readOnly).toBe(false);
  expect((element.querySelector('#wf-save') as HTMLButtonElement).disabled).toBe(true);
  submit(element.querySelector('#wf-form')!);
  click(element.querySelector('#wf-save')!);
  click(element.querySelector('#wf-new-supplier')!);
  await flush();
  expect(writes(request)).toEqual([]);
  expect(element.querySelector('input[data-p="supplier.display_name"]')).toBeNull();
});

test('settings demo leaves locale selection available while forged saves are blocked', async () => {
  const request = vi.fn(async () => ({
    profiles: [
      {
        taxpayer_profile_id: 'demo',
        row_version: 1,
        full_name: 'Synthetic profile',
        tax_id: 'DEMO',
        residency_country: 'ES',
        activities: [],
      },
    ],
    backups: { available: true, revision: 'demo', daily_keep: 7, monthly_keep: 3 },
  }));
  const element = mount(SettingsView, {
    services: services(request),
    settled: vi.fn(),
    onProfile: vi.fn(),
    onLocale: vi.fn(),
    reload: vi.fn(),
  });
  await flush();
  expect(
    (element.querySelector('#settings-profile-form fieldset') as HTMLFieldSetElement).disabled,
  ).toBe(true);
  expect(
    (element.querySelector('#settings-backup-form fieldset') as HTMLFieldSetElement).disabled,
  ).toBe(true);
  expect((element.querySelector('#settings-locale') as HTMLSelectElement).disabled).toBe(false);
  submit(element.querySelector('#settings-profile-form')!);
  submit(element.querySelector('#settings-backup-form')!);
  await flush();
  expect(writes(request)).toEqual([]);
});

test('AEAT demo blocks uploads and status transitions without disabling registry filters', async () => {
  const request = vi.fn(async () => ({
    rows: [
      {
        record_source: 'aeat_document',
        record_id: 'aeat-demo',
        aeat_case_id: 'case-demo',
        case_row_version: 1,
        title: 'Example',
        document_kind: 'submission_receipt',
        procedure_kind: 'roi_registration',
        status: 'submitted',
        occurred_at: '2026-07-01',
        content_url: '/api/aeat-documents/aeat-demo/content',
        source_available: true,
      },
    ],
    total: 1,
  }));
  const element = mount(AeatDocumentsView, {
    services: services(request),
    settled: vi.fn(),
    notify: vi.fn(),
  });
  await flush();
  expect((element.querySelector('input[type="search"]') as HTMLInputElement).readOnly).toBe(false);
  expect((element.querySelector('input[type="file"]') as HTMLInputElement).disabled).toBe(true);
  const status = element.querySelector('.aeat-actions button') as HTMLButtonElement;
  expect(status.disabled).toBe(true);
  click(status);
  element.querySelectorAll('form').forEach(submit);
  await flush();
  expect(writes(request)).toEqual([]);
  expect(
    element.querySelector('a[href="/demo/api/aeat-documents/aeat-demo/content"]'),
  ).not.toBeNull();
});

test('asset depreciation remains visible but cannot post from a forged click', async () => {
  const request = vi.fn(async () => ({
    native: true,
    rows: [
      {
        amortization_entry_id: 'demo-row',
        row_version: 1,
        period_key: '2026-Q3',
        amount_minor: 1000,
        can_post: true,
      },
    ],
  }));
  const element = leaf(AssetSchedule, {
    assetId: 'demo-asset',
    request,
    readOnly: true,
    refreshAssets: vi.fn(),
  });
  await flush();
  const button = element.querySelector('[data-recognize]') as HTMLButtonElement;
  expect(button).not.toBeNull();
  expect(button.disabled).toBe(true);
  click(button);
  await flush();
  expect(writes(request)).toEqual([]);
});

test('document previews use the same iframe with the runtime API prefix', () => {
  const production = leaf(DocumentPreview, { documentId: 'original', title: 'Document' });
  expect(production.querySelector('iframe')?.getAttribute('src')).toBe(
    '/api/document/original/preview',
  );
  const demo = leaf(DocumentPreview, {
    documentId: 'original',
    title: 'Document',
    services: services(vi.fn()),
  });
  expect(demo.querySelector('iframe')?.getAttribute('src')).toBe(
    '/demo/api/document/original/preview',
  );
});

test('income actions keep document links in demo and cannot copy even through a forged click', async () => {
  const copy = vi.fn();
  const element = leaf(IncomeTable, {
    services: services(vi.fn()),
    copy,
    query: '',
    target: '2026-Q4',
    rows: [
      {
        transaction_id: 'demo-income',
        entry_type: 'income',
        lifecycle_status: 'posted',
        document_id: DOCUMENT_ID,
        ui_context: { domain: 'transaction', state: 'posted', subject_id: 'demo-income' },
      },
    ],
  });
  const button = element.querySelector('.copy-action-button') as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  click(button);
  expect(copy).not.toHaveBeenCalled();
  expect(
    element.querySelector(`a[href="/demo/api/document/${DOCUMENT_ID}/content"]`),
  ).not.toBeNull();
});

test('ready posting batches remain visible without opening a write confirmation in demo', async () => {
  const openDialog = vi.fn();
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: openDialog,
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: vi.fn(),
  });
  const request = vi.fn(async (url: string) =>
    url.startsWith('/api/review/posting-preview')
      ? {
          period: '2026-Q3',
          period_status: 'open',
          ready: [{ transaction_id: REVIEW_ID, expected_row_version: 1, amount_eur: '12.00' }],
          deferred: [],
          blocked: [],
        }
      : [],
  );
  const element = mount(ReviewOverviewView, {
    services: services(request),
    period: '2026-Q3',
    tab: 'posting',
    selectTab: vi.fn(),
    settled: vi.fn(),
    refreshCalculation: vi.fn(),
    notify: vi.fn(),
  });
  await flush();
  const button = element.querySelector('[data-posting-action="open-confirm"]') as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  click(button);
  await flush();
  document.querySelectorAll('form').forEach(submit);
  await flush();
  expect(writes(request)).toEqual([]);
  expect(openDialog).not.toHaveBeenCalled();
});

test('read-only accounting records stay unchanged when the interface language changes', async () => {
  setLocale('en');
  const draft = nativeFixture();
  draft.payload.facts.document_number = 'English stored document';
  const workflowRoot = mount(ExpenseWorkflowView, {
    transactionId: REVIEW_ID,
    period: '2026-Q3',
    services: services(vi.fn(async (url: string) => (url === '/api/counterparties' ? [] : draft))),
    onPosted: vi.fn(),
    onLegacy: vi.fn(),
    settled: vi.fn(),
  });
  await flush();
  const field = workflowRoot.querySelector(
    'input[data-p="facts.document_number"]',
  ) as HTMLInputElement;
  expect(field.value).toBe('English stored document');
  setLocale('es');
  await flush();
  expect(field.value).toBe('English stored document');
  expect(workflowRoot.querySelector('input[data-p="facts.document_number"]')).toBe(field);
});

test('read-only rename cannot open or write through its exposed method', async () => {
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: vi.fn(),
  });
  const request = vi.fn();
  const dialog = ref<InstanceType<typeof RenameDialog>>();
  const app = createApp({
    render: () => h(RenameDialog, { ref: dialog, services: services(request) }),
  });
  const element = root();
  app.mount(element);
  cleanup.push(() => app.unmount());
  await dialog.value!.open(
    {
      counterparty_id: 'demo',
      display_name: 'Example',
      row_version: 1,
      ui_context: { domain: 'counterparty', state: 'ready', subject_id: 'demo' },
    },
    document.createElement('button'),
  );
  await flush();
  expect(element.querySelector('form')).toBeNull();
  expect(request).not.toHaveBeenCalled();
});

test('read-only intake never restores or saves a browser draft, even through exposed open', async () => {
  const storage = ['getItem', 'setItem', 'removeItem'].map((method) =>
    vi.spyOn(Storage.prototype, method as 'getItem').mockImplementation(() => {
      throw new Error('storage must stay untouched');
    }),
  );
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: vi.fn(),
  });
  const dialog = ref<InstanceType<typeof IntakeDialog>>();
  const request = vi.fn();
  const app = createApp({
    render: () =>
      h(IntakeDialog, {
        ref: dialog,
        services: {
          readOnly: true,
          request,
          notify: vi.fn(),
          locationKey: () => '/demo/',
          accepted: vi.fn(),
        },
      }),
  });
  const element = root();
  app.mount(element);
  cleanup.push(() => app.unmount());
  await dialog.value!.open('expense_invoice', { targetPeriodKey: '2026-Q3' });
  document.querySelectorAll('form').forEach(submit);
  await flush();
  cleanup.pop()?.();
  expect(request).not.toHaveBeenCalled();
  storage.forEach((spy) => expect(spy).not.toHaveBeenCalled());
});
