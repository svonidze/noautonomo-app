import { nextTick } from 'vue';
import { test, expect, vi } from 'vitest';
import { mountView } from '../support/mount.ts';
import ReviewOverviewView from '../../src/features/posting/ReviewOverviewView.vue';
import { postingState } from '../../src/features/posting/state.ts';
import type { ReviewOverviewContext } from '../../src/features/posting/model.ts';
import ru from '../../src/locales/ru/review.json' with { type: 'json' };
import statuses from '../../src/locales/ru/statuses.json' with { type: 'json' };
import help from '../../src/locales/ru/help.json' with { type: 'json' };
const ID = '11111111-1111-4111-8111-111111111111';
const ready = {
  transaction_id: ID,
  expected_row_version: 3,
  description: 'Synthetic frozen row',
  preview_bucket: 'ready',
  amount_eur: '12.00',
};
function setup() {
  postingState.busy.value = false;
  postingState.stale.clear();
  postingState.lastResult.value = undefined;
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.open = true;
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.open = false;
    },
  });
}
async function flush() {
  for (let count = 0; count < 8; count++) {
    await Promise.resolve();
    await nextTick();
  }
}
const preview = (period: string) => ({
  period,
  period_status: 'open',
  ready: [ready],
  deferred: [],
  blocked: [],
});
function context(
  period: string,
  request: ReviewOverviewContext['services']['request'],
): ReviewOverviewContext {
  return {
    period,
    tab: 'posting',
    services: { request, navigate: vi.fn() },
    selectTab: vi.fn(),
    settled: vi.fn(),
    refreshCalculation: vi.fn(async () => ({})),
    notify: vi.fn(),
  };
}
for (const same of [true, false])
  test(`frozen batch remains owned when context changes to ${same ? 'the same' : 'another'} period`, async () => {
    setup();
    let release!: (value: unknown) => void;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const writes: unknown[] = [];
    const first = context('2026-Q3', async (url, options) => {
      if (url === '/api/review/post-ready') {
        writes.push(JSON.parse(String(options?.body)));
        return gate;
      }
      return url.includes('posting-preview') ? preview('2026-Q3') : [];
    });
    const root = document.createElement('div');
    document.body.append(root);
    const host = mountView(root, ReviewOverviewView, first);
    await flush();
    (root.querySelector('[data-posting-action="open-confirm"]') as HTMLButtonElement).click();
    await flush();
    const dialog = document.querySelector('#vue-posting-confirm-dialog') as HTMLDialogElement;
    dialog
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await flush();
    const latest = context(same ? '2026-Q3' : '2026-Q2', async (url) =>
      url.includes('posting-preview')
        ? { ...preview(same ? '2026-Q3' : '2026-Q2'), ready: [] }
        : [],
    );
    host.updateContext(latest);
    await flush();
    expect(postingState.busy.value).toBe(true);
    if (same) {
      expect(dialog.open).toBe(true);
      expect(dialog.textContent).toContain('Synthetic frozen row');
    } else expect(dialog.open).toBe(false);
    release({
      status: 'completed',
      summary: { posted_count: 1 },
      results: [{ transaction_id: ID, outcome: 'posted' }],
    });
    await flush();
    expect(writes).toEqual([
      { period: '2026-Q3', items: [{ transaction_id: ID, expected_row_version: 3 }] },
    ]);
    expect(first.refreshCalculation).not.toHaveBeenCalled();
    if (same) {
      expect(latest.refreshCalculation).toHaveBeenCalledWith('2026-Q3');
      expect(postingState.stale.size).toBe(0);
    } else {
      expect(latest.refreshCalculation).not.toHaveBeenCalled();
      expect(postingState.stale.has('2026-Q3')).toBe(true);
      expect(postingState.stale.has('2026-Q2')).toBe(false);
    }
    expect(postingState.lastResult.value?.period).toBe('2026-Q3');
    expect(postingState.busy.value).toBe(false);
    host.dispose();
    root.remove();
    postingState.stale.clear();
    postingState.lastResult.value = undefined;
    vi.unstubAllGlobals();
  });

test('the posting tab lists ready rows first, marks the row the queue opened and keeps an unknown EUR amount unknown', async () => {
  setup();
  // jsdom has no scrolling; the tab scrolls the row named by the address fragment into view.
  const scrolled = vi.fn();
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    configurable: true,
    value: scrolled,
  });
  const DEFERRED = '22222222-2222-4222-8222-222222222222',
    BLOCKED = '33333333-3333-4333-8333-333333333333';
  history.replaceState(null, '', `/review?period=2026-Q3&tab=posting#posting-row-${ID}`);
  const request = async (url: string) =>
    url.includes('posting-preview')
      ? {
          period: '2026-Q3',
          period_status: 'open',
          ready: [ready],
          deferred: [
            {
              transaction_id: DEFERRED,
              description: 'Synthetic later row',
              preview_bucket: 'deferred',
              posting_deferred_until: '2026-11-02',
              amount_eur: '5.00',
              blockers: [{ code: 'future_dated' }],
            },
          ],
          blocked: [
            {
              transaction_id: BLOCKED,
              description: 'Synthetic USD row',
              preview_bucket: 'blocked',
              amount_eur: null,
              effective_amount_eur_minor: null,
              blockers: [{ code: 'fx_missing' }],
            },
          ],
          summary: {
            approved_count: 3,
            ready_count: 1,
            deferred_count: 1,
            blocked_count: 1,
            ready_total_eur: '12.00',
            cleanup_count: 2,
            cleanup_blocked_count: 1,
          },
        }
      : [];
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, ReviewOverviewView, context('2026-Q3', request));
  await flush();
  const text = (node: Element | null) => node!.textContent!.replace(/\s+/g, ' ').trim();
  expect([...root.querySelectorAll('.posting-section-label')].map(text)).toEqual([
    statuses['statuses.labels.ready'],
    statuses['statuses.labels.deferred'],
    statuses['statuses.labels.blocked'],
  ]);
  const target = root.querySelector(`#posting-row-${ID}`)!;
  expect(text(target)).toContain('Synthetic frozen row');
  expect(target.classList.contains('posting-row-target')).toBe(true);
  expect(root.querySelectorAll('.posting-row-target')).toHaveLength(1);
  expect(scrolled).toHaveBeenCalledTimes(1);
  expect(scrolled.mock.contexts[0]).toBe(target);
  // A ready row has nothing to explain; it is not «manual review required».
  expect(text(target.querySelector('.posting-result-message'))).toBe('');
  expect(target.querySelector('[data-status-help]')).toBeNull();
  expect(text(root.querySelector(`#posting-row-${BLOCKED} [data-status-help]`))).toBe(
    help['help.words.whatToDo'],
  );
  // Unknown is not zero: no amount and the reason, while a known amount stays an amount.
  const unknown = root.querySelector(`#posting-row-${BLOCKED} td.amount`)!;
  expect(text(unknown)).toBe(`—${ru['review.noEurAmount']}`);
  expect(text(unknown.querySelector('small'))).toBe(ru['review.noEurAmount']);
  expect(text(root.querySelector(`#posting-row-${DEFERRED} td.amount`))).toContain('5,00');
  // Archive-transfer counts are technical: folded below the four readiness counts.
  expect(
    root.querySelectorAll('.posting-panel > .posting-summary-grid .posting-stat'),
  ).toHaveLength(4);
  const technical = root.querySelector<HTMLDetailsElement>('details.posting-technical')!;
  expect(text(technical.querySelector('summary'))).toBe(ru['review.technicalStats']);
  expect(technical.open).toBe(false);
  expect(text(technical)).toContain(ru['review.postingCleanup']);
  expect(technical.querySelectorAll('.posting-stat')).toHaveLength(2);
  // The confirmation dialog lists the same row without a second anchor.
  (root.querySelector('[data-posting-action="open-confirm"]') as HTMLButtonElement).click();
  await flush();
  expect(document.querySelectorAll(`[id="posting-row-${ID}"]`)).toHaveLength(1);
  host.dispose();
  root.remove();
  history.replaceState(null, '', '/');
  delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
});
