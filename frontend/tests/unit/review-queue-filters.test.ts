import { createApp, h, nextTick } from 'vue';
import { afterEach, expect, test, vi } from 'vitest';
import ReviewQueue from '../../src/features/posting/ReviewQueue.vue';
import type { ReviewOverviewContext, ReviewRow } from '../../src/features/posting/model.ts';
import ru from '../../src/locales/ru/review.json' with { type: 'json' };
import statuses from '../../src/locales/ru/statuses.json' with { type: 'json' };
import help from '../../src/locales/ru/help.json' with { type: 'json' };
import common from '../../src/locales/ru/common.json' with { type: 'json' };

// Synthetic queue rows only; 9 October 2026 is "today".
const id = (index: number) => `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`;
function row(index: number, state: string | undefined, extra: Partial<ReviewRow> = {}): ReviewRow {
  return {
    transaction_id: id(index),
    entry_type: 'expense',
    lifecycle_status: state === 'needs_review' ? 'needs_review' : 'approved',
    transaction_date: `2026-09-${String(10 + index).padStart(2, '0')}`,
    period_key: '2026-Q3',
    description: `Synthetic row ${index}`,
    amount_eur: '10.00',
    ui_context: { domain: 'transaction', ...(state ? { state } : {}), reasons: [] } as never,
    ...extra,
  };
}
// The review the server offers for a row (status_context.review_actions).
const review = (index: number, period = '2026-Q3') => [
  { kind: 'review', transaction_id: id(index), period },
];
const rows = [
  // The link is built from the action's own ids, here a period other than the screen's.
  row(1, 'needs_review', {
    ui_context: {
      domain: 'transaction',
      state: 'needs_review',
      actions: review(1, '2026-Q2'),
    } as never,
  }),
  // In review, but the server offers no review of it (e.g. not exactly one invoice review).
  row(2, 'needs_review'),
  row(3, 'ready', {
    ui_context: { domain: 'transaction', state: 'ready', actions: review(3) } as never,
  }),
  row(4, 'deferred', {
    ui_context: {
      domain: 'transaction',
      state: 'deferred',
      posting: {
        preview_bucket: 'deferred',
        posting_deferred_until: '2026-11-02',
        blockers: [{ code: 'future_dated' }],
      },
      actions: review(4),
    } as never,
  }),
  row(5, 'blocked', {
    ui_context: {
      domain: 'transaction',
      state: 'blocked',
      posting: { preview_bucket: 'blocked', blockers: [{ code: 'fx_missing' }] },
    } as never,
  }),
  row(6, 'blocked', {
    ui_context: { domain: 'transaction', state: 'blocked', actions: review(6) } as never,
  }),
  row(7, undefined),
];
const mounted: (() => void)[] = [];
afterEach(() => {
  mounted.splice(0).forEach((unmount) => unmount());
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
function show(items: ReviewRow[], hasDocuments = true) {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-09T12:00:00'));
  // jsdom has neither; the aging chart's host needs both once it renders.
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value(this: HTMLDialogElement) {
      this.open = false;
    },
  });
  const request = vi.fn(() => new Promise<never>(() => {}));
  const context = {
    period: '2026-Q3',
    tab: 'queue',
    services: { request, navigate: vi.fn() },
    settled: vi.fn(),
    selectTab: vi.fn(),
    refreshCalculation: vi.fn(),
    notify: vi.fn(),
  } satisfies ReviewOverviewContext;
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({
    render: () => h(ReviewQueue, { rows: items, context, hasDocuments }),
  });
  app.mount(root);
  mounted.push(() => {
    app.unmount();
    root.remove();
  });
  return { root, request };
}
const cards = (root: HTMLElement) => [
  ...root.querySelectorAll<HTMLButtonElement>('[role="group"] > button'),
];
const card = (root: HTMLElement, label: string) =>
  cards(root).find((button) => button.querySelector('span')!.textContent === label)!;
const listed = (root: HTMLElement) =>
  [...root.querySelectorAll('tbody tr')].map((tr) => tr.querySelector('strong')!.textContent);

test('the summary cards are toggle buttons that filter the queue; «Все» resets', async () => {
  const { root } = show(rows);
  expect(root.querySelector('[role="group"]')!.getAttribute('aria-label')).toBe(
    ru['review.filterLabel'],
  );
  expect(
    cards(root).map((button) => [
      button.querySelector('span')!.textContent,
      button.querySelector('strong')!.textContent,
      button.getAttribute('aria-pressed'),
    ]),
  ).toEqual([
    [ru['review.filterAll'], '7', 'true'],
    [statuses['statuses.labels.needs_review'], '2', 'false'],
    [statuses['statuses.labels.ready'], '1', 'false'],
    [statuses['statuses.labels.deferred'], '1', 'false'],
    [statuses['statuses.labels.blocked'], '2', 'false'],
    // A missing state is its own neutral card, never counted as blocked.
    [statuses['statuses.labels.unknown'], '1', 'false'],
  ]);
  expect(listed(root)).toHaveLength(7);

  card(root, statuses['statuses.labels.blocked']).click();
  await nextTick();
  expect(card(root, statuses['statuses.labels.blocked']).getAttribute('aria-pressed')).toBe('true');
  expect(card(root, ru['review.filterAll']).getAttribute('aria-pressed')).toBe('false');
  expect(listed(root)).toEqual(['Synthetic row 5', 'Synthetic row 6']);

  card(root, statuses['statuses.labels.needs_review']).click();
  await nextTick();
  expect(listed(root)).toEqual([
    'Synthetic row 1',
    'Synthetic row 2',
    'Synthetic row 5',
    'Synthetic row 6',
  ]);

  card(root, statuses['statuses.labels.blocked']).click();
  await nextTick();
  expect(listed(root)).toEqual(['Synthetic row 1', 'Synthetic row 2']);

  card(root, ru['review.filterAll']).click();
  await nextTick();
  expect(listed(root)).toHaveLength(7);
  expect(cards(root).map((button) => button.getAttribute('aria-pressed'))).toEqual([
    'true',
    'false',
    'false',
    'false',
    'false',
    'false',
  ]);
});

test('a state without rows cannot be chosen and the unknown card appears only when needed', () => {
  const { root } = show(rows.slice(0, 3));
  expect(cards(root).map((button) => [button.textContent, button.disabled])).toEqual([
    [`${ru['review.filterAll']}3`, false],
    [`${statuses['statuses.labels.needs_review']}2`, false],
    [`${statuses['statuses.labels.ready']}1`, false],
    [`${statuses['statuses.labels.deferred']}0`, true],
    [`${statuses['statuses.labels.blocked']}0`, true],
  ]);
});

test('every queued row offers exactly one next action: a review only where the server offers it and work is due', () => {
  const { root } = show(rows);
  const actions = [...root.querySelectorAll('tbody tr')].map((tr) => {
    const controls = [...tr.querySelectorAll('td.table-actions > *')];
    expect(controls).toHaveLength(1);
    const [control] = controls as HTMLElement[];
    return [
      control.tagName,
      control.className,
      control.textContent!.trim(),
      control.getAttribute('href'),
    ];
  });
  const open = (index: number, period = '2026-Q3') => `/review/${id(index)}?period=${period}`;
  const explain = (label: string) => ['BUTTON', 'text-button status-details-button', label, null];
  expect(actions).toEqual([
    ['A', 'secondary-button compact-button', ru['review.openWorkspace'], open(1, '2026-Q2')],
    explain(ru['review.whatBlocks']),
    // A ready row is posted, even where the server would also open its review.
    [
      'A',
      'secondary-button compact-button',
      ru['review.postRow'],
      `/review?period=2026-Q3&tab=posting#posting-row-${id(3)}`,
    ],
    explain(help['help.words.why']),
    explain(ru['review.whatBlocks']),
    // The server offers a review for this blocked row, so the row opens it directly.
    ['A', 'secondary-button compact-button', ru['review.openWorkspace'], open(6)],
    explain(help['help.words.why']),
  ]);
  expect(root.querySelector('[data-open-review-id]')!.getAttribute('data-open-review-id')).toBe(
    `transaction:${id(1)}`,
  );
  for (const button of root.querySelectorAll<HTMLElement>('td.table-actions button'))
    expect(button.dataset.statusHelp).toBeTruthy();
  // Where the row's action opens the explanation, the status cell does not repeat that link.
  const links = [...root.querySelectorAll('tbody tr')].map((tr) =>
    [...tr.querySelectorAll('[data-status-help]')].map((link) => link.textContent!.trim()),
  );
  expect(links).toEqual([
    [help['help.words.whatToDo']],
    [ru['review.whatBlocks']],
    // Ready and nothing to explain: the server's review is navigation, not a task.
    [],
    [help['help.words.why']],
    [ru['review.whatBlocks']],
    [help['help.words.whatToDo']],
    [help['help.words.why']],
  ]);
  // The deferred row's result names its date.
  expect(root.querySelectorAll('tbody tr')[3].textContent).toContain(
    'Проверено, провести после 02 нояб. 2026 г.',
  );
  // The unknown row's result reads as unknown, not as blocked.
  expect(root.querySelectorAll('tbody tr')[6].textContent).toContain(
    statuses['statuses.labels.unknown'],
  );
});

test('the document link reads apart from the document number', () => {
  const { root } = show([row(1, 'needs_review', { document_id: id(9), document_number: 'SYN-1' })]);
  const line = root.querySelector('tbody td.cell-primary small')!;
  expect(line.textContent).toBe(`SYN-1 ${common['common.openDocument']}`);
  expect(line.querySelector('a.text-button')!.getAttribute('href')).toBe(
    `/api/document/${id(9)}/content`,
  );
});

test('fewer than ten queued rows name the oldest one instead of drawing the aging chart', () => {
  const { root, request } = show(rows);
  expect(root.querySelector('#chart-review-aging')).toBeNull();
  // The oldest row is dated 11 September: 28 days before 9 October.
  expect(root.querySelector('.review-queue-age')!.textContent).toBe(
    'Самая старая операция в очереди: 28 дней',
  );
  expect(request).not.toHaveBeenCalled();
});

test('ten queued rows draw the aging chart; an empty queue says nothing about age', () => {
  const many = Array.from({ length: 10 }, (_, index) => row(index + 1, 'needs_review'));
  const { root, request } = show(many);
  expect(root.querySelector('#chart-review-aging')).toBeTruthy();
  expect(root.querySelector('.review-queue-age')).toBeNull();
  expect(request).toHaveBeenCalledWith('/api/analytics?period=2026-Q3');
  const empty = show([]);
  expect(empty.root.querySelector('.review-queue-age')).toBeNull();
  expect(empty.root.querySelector('#chart-review-aging')).toBeNull();
});

test('an empty queue says whether the quarter has no documents or has them all reviewed', () => {
  const text = (root: HTMLElement) => root.querySelector('tbody .empty-state')!.textContent!.trim();
  expect(text(show([], false).root)).toBe(ru['review.queueEmpty.noDocuments']);
  expect(text(show([], true).root)).toBe(ru['review.queueEmpty.allReviewed']);
  expect(show(rows, true).root.querySelector('tbody .empty-state')).toBeNull();
});
