<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateText, formatPeriod, isFuturePeriod } from '../../core/format.ts';
import { errorDescriptor, errorMessage } from '../../core/error-message.ts';
import { ApiError } from '../../core/http.ts';
import { message } from '../../core/i18n.ts';
import ChartHost from '../../charts/ChartHost.vue';
import type { Analytics } from '../../charts/types.ts';
import type { ChartKind } from '../../charts/builders.ts';
import { viewHref } from '../../vue/services.ts';
import RecentTransactions from './RecentTransactions.vue';
import TaskList from './TaskList.vue';
import { buildStartTasks, buildTasks, firstRun, type DashboardTask } from './tasks.ts';
import { createFinancePresentation } from './presentation.ts';
import { yearToDate } from './yearToDate.ts';
import type { OverviewContext, DashboardData, ExpenseScope } from './model.ts';
const props = defineProps<{ context: OverviewContext }>();
const { locale, t } = useLocale();
const data = shallowRef<DashboardData>(),
  analytics = shallowRef<Analytics>(),
  error = shallowRef<unknown>();
const chartFailure = ref(false),
  loading = ref(true),
  busy = ref(false),
  calculating = ref(false);
let active = true,
  generation = 0;
const current = (revision: number) => active && revision === generation;
async function load(refresh = false) {
  const revision = ++generation,
    context = props.context;
  busy.value = true;
  loading.value = !data.value;
  error.value = undefined;
  try {
    const [result, chart] = await Promise.all([
      context.services.request(
        `/api/dashboard?period=${encodeURIComponent(context.period)}`,
      ) as Promise<DashboardData>,
      context.services
        .request(`/api/analytics?period=${encodeURIComponent(context.period)}`)
        .then((value) => ({ ok: true as const, value: value as Analytics }))
        .catch(() => ({ ok: false as const })),
    ]);
    if (!current(revision) || (refresh && document.querySelector('dialog[open]'))) return;
    data.value = result;
    analytics.value = chart.ok ? chart.value : undefined;
    chartFailure.value = !chart.ok;
  } catch (failure) {
    if (current(revision)) error.value = failure;
  } finally {
    if (current(revision)) {
      busy.value = false;
      loading.value = false;
      context.settled();
    }
  }
}
watch(
  () => props.context,
  () => {
    data.value = undefined;
    analytics.value = undefined;
    void load();
  },
  { immediate: true },
);
function refreshVisible() {
  if (
    !busy.value &&
    document.visibilityState === 'visible' &&
    !document.querySelector('dialog[open]')
  )
    void load(true);
}
const interval = setInterval(refreshVisible, 60000);
document.addEventListener('visibilitychange', refreshVisible);
// Analytics is folded on phone widths and open on wide ones; crossing the breakpoint (rotation, a
// window resize) resets it, and the summary names what a click will do.
const narrowQuery =
  typeof window.matchMedia === 'function' ? window.matchMedia('(max-width: 760px)') : undefined;
const analyticsOpen = ref(!narrowQuery?.matches);
const followNarrow = () => (analyticsOpen.value = !narrowQuery?.matches);
const followToggle = (event: Event) =>
  (analyticsOpen.value = (event.currentTarget as HTMLDetailsElement).open);
narrowQuery?.addEventListener('change', followNarrow);
onBeforeUnmount(() => {
  active = false;
  generation++;
  clearInterval(interval);
  document.removeEventListener('visibilitychange', refreshVisible);
  narrowQuery?.removeEventListener('change', followNarrow);
});
const presentation = computed(() => createFinancePresentation(locale.value));
const future = computed(() => isFuturePeriod(props.context.period));
// Until the first posting anywhere in the ledger the four first steps replace the regular list.
const start = computed(() => !!data.value && firstRun(data.value));
const tasks = computed(() =>
  (!data.value
    ? []
    : start.value
      ? buildStartTasks({
          data: data.value,
          period: props.context.period,
          profileConfigured: props.context.profileConfigured,
          canAdd: !props.context.services.readOnly && !!props.context.openIntake,
        })
      : buildTasks({
          data: data.value,
          period: props.context.period,
          today: new Date(),
          locale: locale.value,
        })
  ).map((task) => ({
    ...task,
    ...(task.href ? { href: viewHref(props.context.services, task.href) } : {}),
    ...(props.context.services.readOnly && task.action
      ? { disabled: true, hint: message('demo.readOnlyReason') }
      : {}),
  })),
);
function act(action: NonNullable<DashboardTask['action']>) {
  if (props.context.services.readOnly) return;
  if (action === 'intake') props.context.openIntake?.();
  else void calculate();
}
/** The amount, or its known part and the number of rows without a EUR amount: never a bare dash. */
const amount = (scope?: ExpenseScope) =>
  scope?.missing_amount_count
    ? t('dashboard.partialAmount', {
        amount: eur((scope.known_amount_minor ?? NaN) / 100, locale.value),
        count: scope.missing_amount_count,
      })
    : eur(scope?.amount_eur, locale.value);
/** Reviewed, unposted income: its amount, or the count alone when no amount is known. */
const reviewedIncome = computed(() => {
  const forecast = data.value?.totals.forecast,
    count = forecast?.income_transaction_count ?? 0,
    known = forecast?.income_known_amount_minor,
    missing = forecast?.income_missing_amount_count ?? 0;
  if (!count) return '';
  if (known == null || missing >= count) return t('common.counts.operations', { count });
  return amount({
    amount_eur: known / 100,
    known_amount_minor: known,
    missing_amount_count: missing,
  });
});
const taxLines = computed(() =>
  (['130', '303'] as const).map((code) => {
    const obligation = data.value?.obligations.find((row) => String(row.obligation_code) === code);
    const form = presentation.value.formCardData(
      data.value?.tax_forms?.[`modelo${code}`],
      obligation,
      { warnOnMissingHeadline: true },
    );
    const unavailable = form.display_state === 'unavailable';
    return {
      code,
      form,
      // A missing amount with a known cause is said in words below it; only an unexplained one is «—».
      value:
        form.headline_value != null
          ? eur(form.headline_value, locale.value)
          : unavailable || form.display_state === 'filed_without_values'
            ? ''
            : '—',
      note:
        unavailable && future.value
          ? t('taxes.headlineNotStarted')
          : presentation.value.formSubtitle(form, obligation),
    };
  }),
);
const ytd = computed(() => yearToDate(analytics.value));
const taxAccent = computed(() =>
  taxLines.value.some((line) => presentation.value.formAccentClass(line.form)) ? 'warning' : '',
);
const calculable = computed(
  () => !future.value && taxLines.value.some((line) => line.form.display_state === 'unavailable'),
);
const charts: { id: string; kind: ChartKind }[] = [
  { id: 'chart-business-result', kind: 'businessResult' },
  { id: 'chart-tax-due', kind: 'taxDue' },
  { id: 'chart-iva-position', kind: 'ivaPosition' },
  { id: 'chart-tax-reserve', kind: 'reserve' },
  { id: 'chart-cumulative-net', kind: 'cumulativeNet' },
  { id: 'chart-cumulative-expenses', kind: 'cumulativeExpenses' },
];
async function calculate() {
  if (props.context.services.readOnly || calculating.value) return;
  const context = props.context,
    owns = () => active && props.context === context;
  calculating.value = true;
  try {
    await context.refreshCalculation();
    if (owns()) {
      context.notify(
        message('refresh.done', { period: formatPeriod(context.period, locale.value) }),
      );
      await load();
    }
  } catch (failure) {
    if (owns()) context.notify(errorDescriptor(failure), true);
  } finally {
    if (active) calculating.value = false;
  }
}
const forbidden = () => error.value instanceof ApiError && error.value.code === 'session_forbidden';
const retry = () => (forbidden() ? window.location.reload() : void load(!!data.value));
</script>
<template>
  <div v-if="loading" class="empty-state" role="status">{{ t('common.loading') }}</div>
  <div v-else>
    <div
      v-if="error"
      id="dashboard-expense-refresh-status"
      class="expense-refresh-status"
      role="alert"
    >
      <div id="dashboard-expense-refresh-message">
        <p>
          {{
            t(
              forbidden()
                ? 'common.sessionExpired'
                : data
                  ? 'expense.refreshFailed'
                  : 'common.loadFailed',
            )
          }}
        </p>
        <p>{{ errorMessage(error, locale) }}</p>
      </div>
      <button
        id="dashboard-expense-refresh-retry"
        class="secondary-button"
        :disabled="busy"
        @click="retry"
      >
        {{ t(forbidden() ? 'common.reload' : 'expense.refreshData') }}
      </button>
    </div>
    <template v-if="data">
      <p v-if="data.has_tax_attributions" class="period-note">
        {{ t('taxAttribution.documentBasis') }}
      </p>
      <TaskList
        :tasks="tasks"
        :empty-text="t('dashboard.tasksEmpty')"
        :note="
          data.forecast_as_of
            ? t('dashboard.calculatedAsOf', { date: formatDateText(data.forecast_as_of, locale) })
            : undefined
        "
        :busy="calculating"
        :start="start"
        @navigate="context.services.navigate"
        @action="act"
      />
      <section class="metric-grid" aria-labelledby="dashboard-figures-title">
        <h2 id="dashboard-figures-title" class="visually-hidden">
          {{ t('dashboard.figuresTitle') }}
        </h2>
        <div class="metric accent">
          <span>{{ t('dashboard.incomePosted') }}</span
          ><strong>{{
            amount({
              amount_eur: data.totals.actual.income_eur,
              known_amount_minor: data.totals.actual.income_known_amount_minor,
              missing_amount_count: data.totals.actual.income_missing_amount_count,
            })
          }}</strong
          ><small>{{
            t('common.counts.operations', { count: data.totals.actual.income_transaction_count })
          }}</small>
          <small v-if="reviewedIncome">{{
            t('dashboard.reviewedNotPosted', { value: reviewedIncome })
          }}</small>
        </div>
        <div
          v-if="ytd"
          id="dashboard-year-to-date"
          class="metric"
          :class="ytd.unavailable ? 'warning' : 'accent'"
        >
          <span>{{ t('dashboard.yearToDate', { year: ytd.year }) }}</span>
          <dl class="metric-rows">
            <div>
              <dt>{{ t('dashboard.ytdIncome') }}</dt>
              <dd>{{ eur(ytd.income, locale) }}</dd>
            </div>
            <div>
              <dt>{{ t('dashboard.ytdExpenses') }}</dt>
              <dd>{{ eur(ytd.expenses, locale) }}</dd>
            </div>
            <div>
              <dt>{{ t('dashboard.ytdNet') }}</dt>
              <dd>
                <strong>{{ eur(ytd.net, locale) }}</strong>
              </dd>
            </div>
          </dl>
          <small v-if="ytd.unavailable" role="status">{{ t('dashboard.ytdUnavailable') }}</small>
          <small>{{
            ytd.fullYear
              ? t('dashboard.ytdFullYear')
              : t('dashboard.ytdAsOf', { date: formatDateText(ytd.asOf, locale) })
          }}</small>
        </div>
        <div class="metric">
          <span>{{ t('dashboard.expensesPosted') }}</span>
          <dl>
            <div v-for="kind in ['purchase', 'amortization'] as const" :key="kind">
              <dt>{{ t(kind === 'purchase' ? 'expense.purchase' : 'expense.amortization') }}</dt>
              <dd>
                <strong>{{ amount(data.expense_summary[kind]?.posted) }}</strong
                ><small v-if="data.expense_summary[kind]?.approved?.count">{{
                  t('dashboard.reviewedNotPosted', {
                    value: amount(data.expense_summary[kind].approved),
                  })
                }}</small
                ><small v-if="data.expense_summary[kind]?.future_approved?.count">{{
                  t('expense.futureAmount', {
                    amount: amount(data.expense_summary[kind].future_approved),
                  })
                }}</small>
              </dd>
            </div>
          </dl>
        </div>
        <div class="metric" :class="taxAccent">
          <span>{{ t('dashboard.taxDue') }}</span>
          <dl>
            <div v-for="line in taxLines" :key="line.code">
              <dt>{{ t(line.code === '130' ? 'dashboard.irpfLine' : 'dashboard.ivaLine') }}</dt>
              <dd>
                <strong v-if="line.value">{{ line.value }}</strong
                ><small>{{ line.note }}</small>
              </dd>
            </div>
          </dl>
          <button
            v-if="calculable"
            type="button"
            class="secondary-button calculate-button"
            :disabled="context.services.readOnly || calculating"
            @click="calculate"
          >
            {{ t(calculating ? 'common.calculating' : 'common.calculate') }}
          </button>
        </div>
      </section>
      <div class="dashboard-grid">
        <section class="panel">
          <header class="panel-header">
            <h2>{{ t('dashboard.recentTransactions') }}</h2>
            <small>{{ formatPeriod(context.period, locale) }}</small>
          </header>
          <RecentTransactions
            :services="context.services"
            :rows="data.recent_transactions"
            :period="context.period"
            :target="context.copyTarget"
            :navigate="context.services.navigate"
            :copy="context.copy"
          />
        </section>
        <section v-if="data.open_issues.length" class="panel">
          <header class="panel-header">
            <h2>{{ t('dashboard.openIssues') }}</h2>
            <small>{{ data.open_issue_count ?? data.open_issues.length }}</small>
          </header>
          <ul class="issues-list">
            <li
              v-for="(issue, index) in data.open_issues"
              :key="issue.ui_context.subject_id || index"
            >
              <StatusCell :context="issue.ui_context" />
            </li>
          </ul>
        </section>
      </div>
      <h2 id="dashboard-analytics-title">{{ t('dashboard.analyticsTitle') }}</h2>
      <details class="analytics-details" :open="analyticsOpen" @toggle="followToggle">
        <summary>
          {{ t(analyticsOpen ? 'dashboard.analyticsToggleOpen' : 'dashboard.analyticsToggle') }}
        </summary>
        <section
          class="charts-panel"
          id="dashboard-charts"
          aria-labelledby="dashboard-analytics-title"
        >
          <ChartHost
            v-for="chart in charts"
            :key="chart.kind"
            :id="chart.id"
            :kind="chart.kind"
            :period="context.period"
            :analytics="analytics"
            :failed="chartFailure"
          />
        </section>
      </details>
    </template>
  </div>
</template>
