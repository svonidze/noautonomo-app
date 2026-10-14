import { nextTick } from 'vue';
import { afterEach, expect, test, vi } from 'vitest';
import {
  buildStartTasks,
  buildTasks,
  firstRun,
  type DashboardTask,
} from '../../src/features/overview/tasks.ts';
import DashboardView from '../../src/features/overview/DashboardView.vue';
import { messageText, type Locale } from '../../src/core/i18n.ts';
import { eur, formatDateText, isFuturePeriod } from '../../src/core/format.ts';
import { mountOverview } from '../support/mount.ts';
import type { DashboardData, FormView } from '../../src/features/overview/model.ts';
import ru from '../../src/locales/ru/dashboard.json' with { type: 'json' };
import statuses from '../../src/locales/ru/statuses.json' with { type: 'json' };
import intake from '../../src/locales/ru/intake.json' with { type: 'json' };

// Synthetic payloads only. 9 October 2026 falls in the fourth quarter of 2026.
const today = new Date('2026-10-09T12:00:00');
const none = { count: 0, missing_amount_count: 0, amount_eur: '0.00', known_amount_minor: 0 };
const kinds = { posted: none, approved: none, future_approved: none };
const form = (code: string, display_state: string) => ({
  form_code: code,
  display_state,
  values: {},
});
const issue = { ui_context: { domain: 'issue', state: 'warning', reasons: [] } };
function dashboard(overrides: Partial<DashboardData> = {}): DashboardData {
  return {
    totals: { actual: { income_eur: '0.00', income_transaction_count: 0 }, forecast: {} },
    expense_summary: { purchase: kinds, amortization: kinds },
    obligations: [],
    tax_forms: { modelo130: form('130', 'preview'), modelo303: form('303', 'preview') },
    posting_summary: { needs_review: 0, ready: 0, later: 0, blocked: 0 },
    recent_transactions: [],
    open_issues: [],
    ...overrides,
  };
}
const build = (data: DashboardData, options: { period?: string; locale?: Locale } = {}) =>
  buildTasks({ data, period: '2026-Q3', today, locale: 'ru', ...options });
const ids = (tasks: DashboardTask[]) => tasks.map((task) => task.id);
const text = (task: DashboardTask, locale: Locale = 'ru') => [
  messageText(task.title, locale),
  messageText(task.hint, locale),
  messageText(task.tag, locale),
];

test('an empty ledger asks only for the calculation; a calculated quiet quarter has no tasks', () => {
  const [calculate, ...rest] = build(dashboard({ tax_forms: {} }));
  expect(rest).toEqual([]);
  expect(calculate).toMatchObject({ id: 'calculate', action: 'calculate', tone: 'pending' });
  expect(calculate.href).toBeUndefined();
  expect(text(calculate)).toEqual([
    ru['dashboard.task.calculate.title'],
    ru['dashboard.task.calculate.hint'],
    statuses['statuses.labels.calculation_unavailable'],
  ]);
  expect(build(dashboard())).toEqual([]);
});

test('a review queue becomes review, post and blocked rows that open the review screen', () => {
  const tasks = build(
    dashboard({
      posting_summary: { needs_review: 3, ready: 2, later: 1, blocked: 1 },
      posting_preview_summary: { ready_count: 2, ready_total_eur: '242.00' },
    }),
  );
  expect(ids(tasks)).toEqual(['review', 'post', 'blocked']);
  expect(tasks.map((task) => task.href)).toEqual([
    '/review?period=2026-Q3',
    '/review?period=2026-Q3&tab=posting',
    '/review?period=2026-Q3',
  ]);
  expect(tasks.map((task) => task.tone)).toEqual(['pending', 'positive', 'attention']);
  expect(tasks.map((task) => text(task)[0])).toEqual([
    'Проверить документы (3)',
    `Провести проверенные операции (2, ${eur('242.00', 'ru')})`,
    'Выяснить, что мешает провести операции (1)',
  ]);
  expect(tasks.map((task) => text(task)[2])).toEqual([
    statuses['statuses.labels.needs_review'],
    statuses['statuses.labels.ready'],
    statuses['statuses.labels.blocked'],
  ]);
  // Without a server total the row names the count only, never a zero amount.
  const [post] = build(
    dashboard({ posting_summary: { needs_review: 0, ready: 1, later: 0, blocked: 0 } }),
  );
  expect(text(post)[0]).toBe('Провести проверенные операции (1)');
});

test('a past quarter without a calculation offers the calculate action', () => {
  const missing: Record<string, FormView>[] = [
    {},
    { modelo130: form('130', 'unavailable') },
    { modelo130: form('130', 'unavailable'), modelo303: form('303', 'unavailable') },
  ];
  for (const tax_forms of missing)
    expect(ids(build(dashboard({ tax_forms }), { period: '2026-Q2' }))).toEqual(['calculate']);
  const calculated: Record<string, FormView>[] = [
    { modelo130: form('130', 'preview'), modelo303: form('303', 'unavailable') },
    { modelo130: form('130', 'filed'), modelo303: form('303', 'filed') },
  ];
  for (const tax_forms of calculated)
    expect(build(dashboard({ tax_forms }), { period: '2026-Q2' })).toEqual([]);
});

test('a future quarter never offers a calculation', () => {
  expect(build(dashboard({ tax_forms: {} }), { period: '2027-Q1' })).toEqual([]);
  expect(ids(build(dashboard({ tax_forms: {} }), { period: '2026-Q4' }))).toEqual(['calculate']);
  expect(
    ['2026-Q3', '2026-Q4', '2027-Q1', '2030-Q2', 'invalid'].map((key) =>
      isFuturePeriod(key, today),
    ),
  ).toEqual([false, false, true, true, false]);
});

test('the first due, unfiled return becomes a filing row with its deadline', () => {
  const tasks = build(
    dashboard({
      obligations: [
        {
          obligation_code: '303',
          determination: 'due',
          filing_status: 'filed',
          statutory_due_on: '2026-10-20',
        },
        {
          obligation_code: '130',
          determination: 'due',
          filing_status: 'unfiled',
          statutory_due_on: '2026-10-20',
        },
        { obligation_code: '349', determination: 'unknown', filing_status: 'unfiled' },
      ],
    }),
  );
  expect(tasks).toHaveLength(1);
  expect(tasks[0]).toMatchObject({ id: 'file', href: '/taxes?period=2026-Q3', tone: 'pending' });
  expect(text(tasks[0])).toEqual([
    'Подать Modelo 130',
    ru['dashboard.task.file.hint'],
    `Нужно подать до ${formatDateText('2026-10-20', 'ru')}`,
  ]);
  const [dateless] = build(
    dashboard({
      obligations: [{ obligation_code: '130', determination: 'due', filing_status: 'unfiled' }],
    }),
  );
  expect(text(dateless)[2]).toBe(statuses['statuses.labels.due']);
});

test('open questions and missing exchange rates link to the review screen', () => {
  const tasks = build(
    dashboard({ open_issues: Array(6).fill(issue), open_issue_count: 9, missing_fx_count: 2 }),
  );
  expect(ids(tasks)).toEqual(['issues', 'fx']);
  expect(tasks.map((task) => task.href)).toEqual([
    '/review?period=2026-Q3&tab=documents',
    '/review?period=2026-Q3',
  ]);
  expect(tasks.map((task) => task.tone)).toEqual(['attention', 'attention']);
  // The dashboard lists at most six questions; the row counts all of them.
  expect(tasks.map((task) => text(task)[0])).toEqual([
    'Разобрать открытые вопросы (9)',
    'Указать курс для 2 операций',
  ]);
  expect(text(build(dashboard({ open_issues: [issue] }))[0])[0]).toBe(
    'Разобрать открытые вопросы (1)',
  );
  expect(build(dashboard({ missing_fx_count: 0 }))).toEqual([]);
});

test('a blocked row without a rate is counted once: in the blocked row, not again as a rate row', () => {
  const queue = { needs_review: 0, ready: 0, later: 0, blocked: 1 };
  const [blocked, ...rest] = build(
    dashboard({ posting_summary: queue, missing_fx_count: 1, missing_fx_blocked_count: 1 }),
  );
  expect(rest).toEqual([]);
  expect(blocked.id).toBe('blocked');
  expect(text(blocked)[1]).toBe(
    'Операции проверены, но провести их пока нельзя: причина указана в очереди. Из них без курса: 1.',
  );
  expect(text(blocked, 'en')[1]).toBe(
    'These transactions are reviewed but cannot be posted yet; the queue names the reason. Of these, without an exchange rate: 1.',
  );
  // Rows still in review keep their own rate row, counted without the blocked ones.
  const tasks = build(
    dashboard({ posting_summary: queue, missing_fx_count: 3, missing_fx_blocked_count: 1 }),
  );
  expect(ids(tasks)).toEqual(['blocked', 'fx']);
  expect(text(tasks[1])[0]).toBe('Указать курс для 2 операций');
  // Without a blocked row nothing is folded: the rate row counts every row.
  expect(
    build(dashboard({ missing_fx_count: 2, missing_fx_blocked_count: 2 })).map(
      (task) => text(task)[0],
    ),
  ).toEqual(['Указать курс для 2 операций']);
  const [plain] = build(dashboard({ posting_summary: queue, missing_fx_count: 0 }));
  expect(text(plain)[1]).toBe(ru['dashboard.task.blocked.hint']);
});

test('at most five rows stay, in priority order', () => {
  const tasks = build(
    dashboard({
      posting_summary: { needs_review: 1, ready: 1, later: 0, blocked: 1 },
      tax_forms: {},
      obligations: [{ obligation_code: '130', determination: 'due', filing_status: 'unfiled' }],
      open_issues: [issue],
      missing_fx_count: 1,
    }),
    { period: '2026-Q2' },
  );
  expect(ids(tasks)).toEqual(['review', 'post', 'blocked', 'calculate', 'file']);
});

test('row titles follow the plural categories of both languages', () => {
  const fx = (count: number, locale: Locale) =>
    text(build(dashboard({ missing_fx_count: count }), { locale })[0], locale)[0];
  expect([1, 2, 5, 21].map((count) => fx(count, 'ru'))).toEqual([
    'Указать курс для 1 операции',
    'Указать курс для 2 операций',
    'Указать курс для 5 операций',
    'Указать курс для 21 операции',
  ]);
  expect([1, 2].map((count) => fx(count, 'en'))).toEqual([
    'Set the exchange rate for 1 transaction',
    'Set the exchange rate for 2 transactions',
  ]);
});

const unmounts: (() => void)[] = [];
afterEach(() => {
  unmounts.splice(0).forEach((unmount) => unmount());
  vi.unstubAllGlobals();
});
const settle = async () => {
  for (let index = 0; index < 4; index++) {
    await new Promise((resolve) => setTimeout(resolve));
    await nextTick();
  }
};
async function show(data: DashboardData) {
  const view = mountOverview(DashboardView, 'dashboard', () => data);
  unmounts.push(view.unmount);
  await settle();
  return view;
}
const lines = (root: HTMLElement, selector: string) =>
  [...root.querySelectorAll(selector)].map((node) =>
    node.textContent!.replace(/[ \t\n]+/g, ' ').trim(),
  );

test('the overview opens with the task list, then three figure cards, without banners', async () => {
  const { root, context } = await show(
    dashboard({
      totals: {
        actual: { income_eur: '1250.00', income_transaction_count: 1 },
        forecast: { income_transaction_count: 2 },
      },
      expense_summary: {
        purchase: {
          posted: { ...none, count: 1, amount_eur: '100.00', known_amount_minor: 10000 },
          approved: {
            count: 3,
            missing_amount_count: 1,
            amount_eur: null,
            known_amount_minor: 24200,
          },
          future_approved: none,
        },
        amortization: kinds,
      },
      tax_forms: {},
      posting_summary: { needs_review: 0, ready: 1, later: 0, blocked: 0 },
      posting_preview_summary: { ready_count: 1, ready_total_eur: '121.00' },
    }),
  );
  const order = [
    ...root.querySelectorAll('.task-list, .metric-grid, .dashboard-grid, .analytics-details'),
  ];
  expect(order.map((node) => node.className)).toEqual([
    'task-list',
    'metric-grid',
    'dashboard-grid',
    'analytics-details',
  ]);
  expect(root.querySelector('.task-list h2')!.textContent).toBe(ru['dashboard.tasksTitle']);
  const post = root.querySelector<HTMLAnchorElement>('a.task-row')!;
  expect(post.getAttribute('href')).toBe('/review?period=2026-Q3&tab=posting');
  expect(post.querySelector('strong')!.textContent).toBe(
    `Провести проверенные операции (1, ${eur('121.00', 'ru')})`,
  );
  expect(post.querySelector('.badge')!.className).toBe('badge status-positive');
  post.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
  expect(context.services.navigate).toHaveBeenCalledWith('/review?period=2026-Q3&tab=posting');
  const calculate = root.querySelector<HTMLButtonElement>('button.task-row')!;
  calculate.click();
  await nextTick();
  expect(context.refreshCalculation).toHaveBeenCalledTimes(1);
  // While it runs, the disabled row says why.
  expect([calculate.disabled, calculate.querySelector('.badge')!.textContent]).toEqual([
    true,
    'Считаем…',
  ]);
  expect(
    root.querySelectorAll('.posting-banner, #dashboard-ready-banner, .deadline-strip'),
  ).toHaveLength(0);
  expect(root.querySelectorAll('.metric')).toHaveLength(3);
  expect(lines(root, '.metric small')).toEqual(
    expect.arrayContaining([
      'Проверено, ещё не проведено: 2 операции',
      `Проверено, ещё не проведено: ${eur('242.00', 'ru')} и 1 операция без суммы в EUR`,
    ]),
  );
  expect(lines(root, '.metric dt')).toEqual([
    'Покупки, услуги и прочие расходы',
    'Амортизация техники',
    'IRPF (Modelo 130)',
    'IVA (Modelo 303)',
  ]);
  // No open questions: the panel is not rendered at all.
  expect(lines(root, '.dashboard-grid h2')).toEqual(['Последние операции']);
  // The section heading stays outside the disclosure; the summary only toggles the charts.
  const heading = root.querySelector('#dashboard-analytics-title')!;
  expect([heading.tagName, heading.closest('details')]).toEqual(['H2', null]);
  expect(heading.nextElementSibling!.className).toBe('analytics-details');
  // Open on a wide screen, the summary offers to hide the charts; closed, to show them.
  const analytics = root.querySelector<HTMLDetailsElement>('.analytics-details')!;
  expect([analytics.open, lines(root, '.analytics-details > summary')]).toEqual([
    true,
    ['Скрыть графики'],
  ]);
  analytics.open = false;
  analytics.dispatchEvent(new Event('toggle'));
  await nextTick();
  expect(lines(root, '.analytics-details > summary')).toEqual(['Показать графики']);
});

test('a quiet quarter says so and names its calculation date', async () => {
  const { root } = await show(dashboard({ forecast_as_of: '2026-10-08' }));
  expect(root.querySelectorAll('.task-row')).toHaveLength(0);
  expect(lines(root, '.task-list p')).toEqual([
    ru['dashboard.tasksEmpty'],
    `Предварительный расчёт на ${formatDateText('2026-10-08', 'ru')}`,
  ]);
});

// The first run: a ledger without a posting in any quarter leads with four first steps instead.
const added = {
  transaction_id: 'synthetic-row',
  entry_type: 'expense',
  lifecycle_status: 'needs_review',
  transaction_date: '2026-07-02',
  period_key: '2026-Q3',
  ui_context: { domain: 'transaction', state: 'needs_review' },
};
const start = (
  data: DashboardData,
  options: { profileConfigured?: boolean; canAdd?: boolean } = {},
) =>
  buildStartTasks({
    data,
    period: '2026-Q3',
    profileConfigured: false,
    canAdd: true,
    ...options,
  });
const tags = (tasks: DashboardTask[]) => tasks.map((task) => [task.tone, text(task)[2]]);
const done = ['positive', statuses['statuses.steps.done']],
  canStart = ['pending', statuses['statuses.steps.canStart']],
  notStarted = ['neutral', statuses['statuses.steps.notStarted']];

test('only a ledger the server says has never posted gets the start list', () => {
  expect(firstRun(dashboard({ ledger_has_postings: false }))).toBe(true);
  expect(firstRun(dashboard({ ledger_has_postings: true }))).toBe(false);
  // An unknown answer keeps the regular list: unknown is not "never posted".
  expect(firstRun(dashboard())).toBe(false);
});

test('a new ledger starts with the profile, a first document, its review and the quarter', () => {
  const steps = start(dashboard({ ledger_has_postings: false, tax_forms: {} }));
  expect(ids(steps)).toEqual(['profile', 'document', 'review', 'taxes']);
  expect(steps.map((task) => task.href)).toEqual([
    '/settings',
    undefined,
    '/review?period=2026-Q3',
    '/taxes?period=2026-Q3',
  ]);
  expect(steps[1]).toMatchObject({ action: 'intake', disabled: false });
  expect(steps.map((task) => text(task).slice(0, 2))).toEqual(
    ['profile', 'document', 'review', 'taxes'].map((key) => [
      ru[`dashboard.start.${key}.title` as keyof typeof ru],
      ru[`dashboard.start.${key}.hint` as keyof typeof ru],
    ]),
  );
  // Nothing to review yet, and the calculation waits for a posting.
  expect(tags(steps)).toEqual([canStart, canStart, notStarted, notStarted]);
});

test('each step is done, can start, or waits for an earlier one', () => {
  const queued = dashboard({
    ledger_has_postings: false,
    posting_summary: { needs_review: 1, ready: 0, later: 0, blocked: 0 },
    recent_transactions: [added],
  });
  expect(tags(start(queued, { profileConfigured: true }))).toEqual([
    done,
    done,
    canStart,
    notStarted,
  ]);
  // A reviewed row waits for posting: the third step is still the one to do.
  const reviewed = dashboard({
    ledger_has_postings: false,
    posting_summary: { needs_review: 0, ready: 1, later: 0, blocked: 0 },
    recent_transactions: [added],
  });
  expect(tags(start(reviewed))).toEqual([canStart, done, canStart, notStarted]);
  // A document whose amount intake could not read has no transaction yet: it was still added,
  // and it waits for review.
  const documentOnly = dashboard({ ledger_has_postings: false, document_counts: { extracted: 1 } });
  expect(tags(start(documentOnly))).toEqual([canStart, done, canStart, notStarted]);
  // With intake switched off on the server the document step cannot start and says why.
  const [, closed] = start(dashboard({ ledger_has_postings: false }), { canAdd: false });
  expect(closed).toMatchObject({ action: 'intake', disabled: true });
  expect(tags([closed])).toEqual([notStarted]);
  expect(text(closed)[1]).toBe(intake['intake.disabledReason']);
  // Once a document is in, the done step keeps its own hint.
  const [, kept] = start(documentOnly, { canAdd: false });
  expect(kept).toMatchObject({ action: 'intake', disabled: true });
  expect(tags([kept])).toEqual([done]);
  expect(text(kept)[1]).toBe(ru['dashboard.start.document.hint']);
});

test('the overview shows the start list until the first posting, then the regular list', async () => {
  const { root, context } = await show(dashboard({ ledger_has_postings: false, tax_forms: {} }));
  expect(root.querySelector('.task-list h2')!.textContent).toBe(ru['dashboard.start.title']);
  expect(lines(root, '.task-row strong')).toEqual(
    ['profile', 'document', 'review', 'taxes'].map(
      (key) => ru[`dashboard.start.${key}.title` as keyof typeof ru],
    ),
  );
  // The document step is the only action row: it opens the intake dialog, not a calculation.
  const document = root.querySelector<HTMLButtonElement>('button.task-row')!;
  document.click();
  expect(context.openIntake).toHaveBeenCalledTimes(1);
  expect(context.refreshCalculation).not.toHaveBeenCalled();
  // The tax card's own Calculate busies only calculation rows.
  root.querySelector<HTMLButtonElement>('.calculate-button')!.click();
  await nextTick();
  expect([document.disabled, document.querySelector('.badge')!.textContent]).toEqual([
    false,
    statuses['statuses.steps.canStart'],
  ]);
  const regular = await show(dashboard({ ledger_has_postings: true, tax_forms: {} }));
  expect(regular.root.querySelector('.task-list h2')!.textContent).toBe(ru['dashboard.tasksTitle']);
  expect(lines(regular.root, '.task-row strong')).toEqual([ru['dashboard.task.calculate.title']]);
});

test('open issues retain their visible status badge after the clarity port', async () => {
  const { root } = await show(dashboard({ open_issues: [issue] }));
  expect(root.querySelector('.issues-list .status-context .badge')?.textContent).toBe(
    statuses['statuses.labels.warning'],
  );
});
