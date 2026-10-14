import { createApp, h, nextTick, type Component } from 'vue';
import { afterEach, expect, test, vi } from 'vitest';
import TaxFormCard from '../../src/features/overview/TaxFormCard.vue';
import ReviewQueue from '../../src/features/posting/ReviewQueue.vue';
import ReviewOverviewView from '../../src/features/posting/ReviewOverviewView.vue';
import type { ReviewOverviewContext, ReviewRow } from '../../src/features/posting/model.ts';
import { formatMessage } from '../../src/core/i18n.ts';
import { disposeHelp } from '../../src/vue/help.ts';

const cleanup: Array<() => void> = [];
afterEach(() => {
  cleanup.splice(0).forEach((unmount) => unmount());
  disposeHelp();
  vi.useRealTimers();
});
function mount(component: Component, props: object) {
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({ render: () => h(component, props) });
  app.mount(root);
  cleanup.push(() => {
    app.unmount();
    root.remove();
  });
  return root;
}

test('the relocated tax calculation action rejects forged clicks in read-only mode', async () => {
  const refresh = vi.fn();
  const root = mount(TaxFormCard, {
    code: '130',
    form: { form_code: '130', display_state: 'unavailable', values: {} },
    refreshing: false,
    readOnly: true,
    onRefresh: refresh,
  });
  const buttons = root.querySelectorAll<HTMLButtonElement>('.calculate-button');
  expect(buttons).toHaveLength(1);
  const button = buttons[0];
  expect(button.closest('.tax-form-amount')).not.toBeNull();
  expect(button.disabled).toBe(true);
  expect(button.title).toBe(formatMessage('demo.readOnlyReason'));
  button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  await nextTick();
  expect(refresh).not.toHaveBeenCalled();
});

test('queue document, review and posting links keep the demo URL helpers', () => {
  const href = vi.fn((url: string) => '/demo/?lang=en#' + url);
  const documentUrl = vi.fn((id: string) => `/demo/api/document/${id}/content`);
  const context: ReviewOverviewContext = {
    period: '2026-Q3',
    tab: 'queue',
    services: { readOnly: true, request: vi.fn(), navigate: vi.fn(), href, documentUrl },
    settled: vi.fn(),
    selectTab: vi.fn(),
    refreshCalculation: vi.fn(),
    notify: vi.fn(),
  };
  const rows: ReviewRow[] = [
    {
      transaction_id: 'synthetic-review-row',
      entry_type: 'expense',
      lifecycle_status: 'needs_review',
      transaction_date: '2026-09-10',
      period_key: '2026-Q3',
      document_id: 'synthetic-review-document',
      ui_context: {
        domain: 'transaction',
        state: 'needs_review',
        actions: [
          {
            kind: 'review',
            transaction_id: '00000000-0000-4000-8000-000000000003',
            period: '2026-Q2',
          },
        ],
      },
    },
    {
      transaction_id: 'synthetic-ready-row',
      entry_type: 'expense',
      lifecycle_status: 'approved',
      transaction_date: '2026-09-11',
      period_key: '2026-Q3',
      document_id: 'synthetic-ready-document',
      ui_context: { domain: 'transaction', state: 'ready' },
    },
  ];
  const root = mount(ReviewQueue, { context, rows, documents: 'allReviewed' });
  const renderedRows = root.querySelectorAll('tbody tr');
  expect(renderedRows).toHaveLength(2);
  for (const [index, row] of rows.entries()) {
    expect(renderedRows[index].querySelector('a.inline-button')?.getAttribute('href')).toBe(
      `/demo/api/document/${row.document_id}/content`,
    );
    expect(documentUrl).toHaveBeenCalledWith(row.document_id, false);
    expect(renderedRows[index].querySelectorAll('td.table-actions > *')).toHaveLength(1);
  }
  const review = '/review/00000000-0000-4000-8000-000000000003?period=2026-Q2';
  const posting = '/review?period=2026-Q3&tab=posting#posting-row-synthetic-ready-row';
  expect(renderedRows[0].querySelector('td.table-actions a')?.getAttribute('href')).toBe(
    '/demo/?lang=en#' + review,
  );
  expect(renderedRows[1].querySelector('td.table-actions a')?.getAttribute('href')).toBe(
    '/demo/?lang=en#' + posting,
  );
  expect(href).toHaveBeenCalledWith(review);
  expect(href).toHaveBeenCalledWith(posting);
});

test('small queue age uses creation and the server date even when the browser clock is later', async () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-14T12:00:00Z'));
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value() {},
  });
  const rows: ReviewRow[] = [
    {
      transaction_id: 'synthetic-aging-row',
      entry_type: 'expense',
      lifecycle_status: 'needs_review',
      transaction_date: '2025-01-01',
      created_at: '2026-09-20T23:59:59Z',
      period_key: '2026-Q3',
      ui_context: { domain: 'transaction', state: 'needs_review' },
    },
  ];
  const request = vi.fn(async (url: string) => {
    if (url.startsWith('/api/transactions?')) return rows;
    if (url.startsWith('/api/review/posting-preview?'))
      return { period: '2026-Q3', period_status: 'open', as_of: '2026-09-30' };
    return [];
  });
  const context: ReviewOverviewContext = {
    period: '2026-Q3',
    tab: 'queue',
    services: { readOnly: true, request, navigate: vi.fn() },
    settled: vi.fn(),
    selectTab: vi.fn(),
    refreshCalculation: vi.fn(),
    notify: vi.fn(),
  };
  const root = mount(ReviewOverviewView, { context });
  for (let index = 0; index < 8; index++) {
    await Promise.resolve();
    await nextTick();
  }
  expect(root.querySelector('.review-queue-age')?.textContent).toBe(
    formatMessage('review.oldestInQueue', { days: 10 }),
  );
});
