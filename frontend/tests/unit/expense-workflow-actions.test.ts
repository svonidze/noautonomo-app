import { nextTick } from 'vue';
import { afterEach, expect, test, vi } from 'vitest';
import { mountView } from '../support/mount.ts';
import ReviewDetailView from '../../src/features/review/ReviewDetailView.vue';
import type { ReviewDetailContext } from '../../src/features/review/guided-model.ts';
import type { JsonOptions } from '../../src/core/http.ts';
import { REVIEW_ID, nativeFixture } from '../fixtures/review.ts';
import workflow from '../../src/locales/ru/workflow.json' with { type: 'json' };
import review from '../../src/locales/ru/review.json' with { type: 'json' };

async function flush() {
  for (let count = 0; count < 8; count++) {
    await Promise.resolve();
    await nextTick();
  }
}
afterEach(() => {
  document.body.replaceChildren();
});

test('the expense review keeps one primary action, says when it is sending and links to the queue', async () => {
  const draft = nativeFixture();
  let release!: (value: unknown) => void;
  const request = vi.fn(async (url: string, options?: JsonOptions) => {
    if (url.startsWith('/api/transactions/'))
      return {
        transaction: {
          transaction_id: REVIEW_ID,
          entry_type: 'expense',
          lifecycle_status: 'needs_review',
        },
        period: { period_key: '2026-Q3' },
      };
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
    if (url.endsWith('/confirm')) return new Promise((resolve) => (release = resolve));
    return draft;
  });
  // The detail was opened from the dashboard: the queue link still names the review queue.
  const context: ReviewDetailContext = {
    transactionId: REVIEW_ID,
    period: '2026-Q3',
    returnUrl: '/dashboard?period=2026-Q3',
    knownPeriods: ['2026-Q3'],
    services: { request, navigate: vi.fn() },
    resolved: () => '/dashboard?period=2026-Q3',
    complete: vi.fn(),
    posted: vi.fn(),
    settled: vi.fn(),
    notify: vi.fn(),
  };
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, ReviewDetailView, context);
  await flush();
  const back = root.querySelector('.expense-workflow > .back-link')!;
  expect([back.getAttribute('href'), back.textContent!.trim()]).toEqual([
    '/review?period=2026-Q3',
    `← ${workflow['workflow.backToQueue']}`,
  ]);
  const primaries = () =>
    [...root.querySelectorAll('.primary-button')].map((button) => button.textContent!.trim());
  expect(primaries()).toEqual([workflow['workflow.previewResult']]);
  root
    .querySelector('#wf-form')!
    .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  expect(root.querySelector('#wf-form button[type="submit"]')!.textContent!.trim()).toBe(
    workflow['workflow.recalculate'],
  );
  expect(primaries()).toEqual([workflow['workflow.confirmAndPost']]);
  root.querySelector<HTMLButtonElement>('#wf-confirm')!.click();
  await flush();
  expect(primaries()).toEqual([review['review.submitting']]);
  release({ transaction_id: REVIEW_ID });
  await flush();
  expect([...root.querySelectorAll('h2')].map((heading) => heading.textContent!.trim())).toContain(
    workflow['workflow.postedTitle'],
  );
  host.dispose();
});
