import { message, type Locale, type UiMessage } from '../../core/i18n.ts';
import { eur, formatDateText } from '../../core/format.ts';
import type { DashboardData } from './model.ts';

export interface DashboardTask {
  id: 'review' | 'post' | 'blocked' | 'calculate' | 'file' | 'issues' | 'fx';
  title: UiMessage;
  hint: UiMessage;
  tone: 'pending' | 'positive' | 'attention' | 'neutral';
  tag: UiMessage;
  href?: string;
  action?: 'calculate';
}

/** A quarter after today's: it has no calculation by design (DESIGN.md, Taxes-page headline). */
export function isFuturePeriod(period: string, today = new Date()): boolean {
  const match = /^(\d{4})-Q([1-4])$/.exec(period);
  const quarter = (year: number, number: number) => year * 4 + number;
  return (
    !!match &&
    quarter(Number(match[1]), Number(match[2])) >
      quarter(today.getFullYear(), Math.floor(today.getMonth() / 3) + 1)
  );
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
  if (blocked)
    tasks.push({
      id: 'blocked',
      title: message('dashboard.task.blocked.title', { count: blocked }),
      hint: message('dashboard.task.blocked.hint'),
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
  const missingRates = positive(data.missing_fx_count);
  if (missingRates)
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
