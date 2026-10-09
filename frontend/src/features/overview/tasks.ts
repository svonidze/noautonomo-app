import { message, type Locale, type UiMessage } from '../../core/i18n.ts';
import { eur, formatDateText, isFuturePeriod } from '../../core/format.ts';
import { waitingDocumentStates } from '../posting/model.ts';
import type { DashboardData } from './model.ts';

export interface DashboardTask {
  id:
    | 'review'
    | 'post'
    | 'blocked'
    | 'calculate'
    | 'file'
    | 'issues'
    | 'fx'
    | 'profile'
    | 'document'
    | 'taxes';
  title: UiMessage;
  hint: UiMessage;
  tone: 'pending' | 'positive' | 'attention' | 'neutral';
  tag: UiMessage;
  href?: string;
  action?: 'calculate' | 'intake';
  disabled?: boolean;
}

const positive = (value: unknown) => (typeof value === 'number' && value > 0 ? value : 0);
const known = (value: unknown) =>
  (typeof value === 'number' || (typeof value === 'string' && value !== '')) &&
  Number.isFinite(Number(value));

/** «Что сделать сейчас»: the five most urgent rows, built from the server's counts and states only.
 * `locale` formats the amounts and dates inside the messages. */
export function buildTasks({
  data,
  period,
  today,
  locale,
}: {
  data: DashboardData;
  period: string;
  today: Date;
  locale: Locale;
}): DashboardTask[] {
  const review = (tab?: string) =>
    `/review?${new URLSearchParams({ period, ...(tab ? { tab } : {}) })}`;
  const queue = data.posting_summary;
  const tasks: DashboardTask[] = [];
  const needsReview = positive(queue?.needs_review);
  if (needsReview)
    tasks.push({
      id: 'review',
      title: message('dashboard.task.review.title', { count: needsReview }),
      hint: message('dashboard.task.review.hint'),
      tone: 'pending',
      tag: message('statuses.labels.needs_review'),
      href: review(),
    });
  const ready = positive(queue?.ready),
    total = data.posting_preview_summary?.ready_total_eur;
  // Without the server's total the row names the count only, never a zero amount.
  let post: UiMessage = message('dashboard.task.post.title', { count: ready });
  if (known(total))
    post = message('dashboard.task.post.titleAmount', { count: ready, amount: eur(total, locale) });
  if (ready)
    tasks.push({
      id: 'post',
      title: post,
      hint: message('dashboard.task.post.hint'),
      tone: 'positive',
      tag: message('statuses.labels.ready'),
      href: review('posting'),
    });
  const blocked = positive(queue?.blocked);
  // An approved row without a rate is blocked by it: the blocked row counts it once and names the
  // missing rate in its hint, so the rate row below keeps only the rows still in review.
  const blockedWithoutRate = blocked ? positive(data.missing_fx_blocked_count) : 0;
  let blockedHint: UiMessage = message('dashboard.task.blocked.hint');
  if (blockedWithoutRate)
    blockedHint = message('dashboard.task.blocked.hintFx', { count: blockedWithoutRate });
  if (blocked)
    tasks.push({
      id: 'blocked',
      title: message('dashboard.task.blocked.title', { count: blocked }),
      hint: blockedHint,
      tone: 'attention',
      tag: message('statuses.labels.blocked'),
      href: review(),
    });
  const states = ['modelo130', 'modelo303'].map(
    (key) => data.tax_forms?.[key]?.display_state ?? 'unavailable',
  );
  if (!isFuturePeriod(period, today) && states.every((state) => state === 'unavailable'))
    tasks.push({
      id: 'calculate',
      title: message('dashboard.task.calculate.title'),
      hint: message('dashboard.task.calculate.hint'),
      tone: 'pending',
      tag: message('statuses.labels.calculation_unavailable'),
      action: 'calculate',
    });
  const due = data.obligations.find(
    (row) => row.determination === 'due' && row.filing_status !== 'filed',
  );
  if (due) {
    let deadline: UiMessage = message('statuses.labels.due');
    if (due.statutory_due_on)
      deadline = message('statuses.filingDueBy', {
        date: formatDateText(due.statutory_due_on, locale),
      });
    tasks.push({
      id: 'file',
      title: message('dashboard.task.file.title', { code: due.obligation_code }),
      hint: message('dashboard.task.file.hint'),
      tone: 'pending',
      tag: deadline,
      href: `/taxes?${new URLSearchParams({ period })}`,
    });
  }
  if (data.open_issues.length)
    tasks.push({
      id: 'issues',
      title: message('dashboard.task.issues.title', {
        count: data.open_issue_count ?? data.open_issues.length,
      }),
      hint: message('dashboard.task.issues.hint'),
      tone: 'attention',
      tag: message('statuses.labels.warning'),
      href: review('documents'),
    });
  // The quarter's own count: the analytics quality count spans two years and would send the row
  // to a queue that has no such transaction.
  const missingRates = positive(data.missing_fx_count) - blockedWithoutRate;
  if (missingRates > 0)
    tasks.push({
      id: 'fx',
      title: message('dashboard.task.fx.title', { count: missingRates }),
      hint: message('dashboard.task.fx.hint'),
      tone: 'attention',
      tag: message('expense.fxUnconfirmed'),
      href: review(),
    });
  return tasks.slice(0, 5);
}

/** A ledger the server says has no posting in any quarter: the overview starts with first steps. */
export const firstRun = (data: DashboardData) => data.ledger_has_postings === false;

const steps = { done: 'positive', canStart: 'pending', notStarted: 'neutral' } as const;
type Step = keyof typeof steps;

/** «Начало работы»: profile, a first document, its review and posting, the quarter's calculation.
 * A step is done, can start, or waits for an earlier one; the steps read this quarter's data. */
export function buildStartTasks({
  data,
  period,
  profileConfigured,
  canAdd,
}: {
  data: DashboardData;
  period: string;
  profileConfigured: boolean;
  /** The server accepts documents; otherwise the intake dialog cannot open. */
  canAdd: boolean;
}): DashboardTask[] {
  const row = (id: 'profile' | 'document' | 'review' | 'taxes', step: Step) => ({
    id,
    title: message(`dashboard.start.${id}.title`),
    hint: message(`dashboard.start.${id}.hint`),
    tone: steps[step],
    tag: message(`statuses.steps.${step}`),
  });
  const query = new URLSearchParams({ period }),
    queue = data.posting_summary,
    documents = data.document_counts ?? {};
  // A document counts even when intake could not draft its transaction (no readable amount).
  const added =
    data.recent_transactions.length > 0 || Object.values(documents).some((count) => count > 0);
  const queued =
    positive(queue?.needs_review) +
    positive(queue?.ready) +
    positive(queue?.later) +
    positive(queue?.blocked) +
    waitingDocumentStates.reduce((sum, state) => sum + positive(documents[state]), 0);
  const document = row('document', added ? 'done' : canAdd ? 'canStart' : 'notStarted');
  return [
    { ...row('profile', profileConfigured ? 'done' : 'canStart'), href: '/settings' },
    // A done step keeps its hint; only a step still to do says why intake is closed.
    canAdd || added
      ? { ...document, action: 'intake', disabled: !canAdd }
      : { ...document, hint: message('intake.disabledReason'), action: 'intake', disabled: true },
    { ...row('review', queued ? 'canStart' : 'notStarted'), href: `/review?${query}` },
    // The calculation counts postings: it follows the first one, when the regular list offers it.
    { ...row('taxes', 'notStarted'), href: `/taxes?${query}` },
  ];
}
