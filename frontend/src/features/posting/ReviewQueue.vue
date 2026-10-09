<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateText, formatPeriod, taxCodeLabel } from '../../core/format.ts';
import { followSpaLink } from '../../vue/services.ts';
import StatusCell from '../../components/StatusCell.vue';
import StatusHelpButton from '../../components/StatusHelpButton.vue';
import ChartHost from '../../charts/ChartHost.vue';
import type { ReviewRow, ReviewOverviewContext } from './model.ts';
const props = defineProps<{ rows: ReviewRow[]; context: ReviewOverviewContext }>();
const { locale, t } = useLocale();
// The queue states the server sends; any other state reads as unknown, never as blocked.
const labels = {
  needs_review: 'statuses.labels.needs_review',
  ready: 'statuses.labels.ready',
  deferred: 'statuses.labels.deferred',
  blocked: 'statuses.labels.blocked',
  unknown: 'statuses.labels.unknown',
} as const;
type QueueState = keyof typeof labels;
const stateOf = (row: ReviewRow): QueueState => {
  const state = String(row.ui_context?.state);
  return Object.hasOwn(labels, state) ? (state as QueueState) : 'unknown';
};
const counts = computed(() => {
  const result = { needs_review: 0, ready: 0, deferred: 0, blocked: 0, unknown: 0 };
  for (const row of props.rows) result[stateOf(row)]++;
  return result;
});
// The summary cards filter the table; with none pressed «Все» shows every row.
const selected = ref(new Set<QueueState>());
watch(
  () => props.rows,
  () => selected.value.clear(),
);
const filters = computed(() =>
  (Object.keys(labels) as QueueState[]).filter(
    (state) => state !== 'unknown' || counts.value.unknown,
  ),
);
const shown = computed(() =>
  selected.value.size ? props.rows.filter((row) => selected.value.has(stateOf(row))) : props.rows,
);
function toggle(state: QueueState) {
  if (!selected.value.delete(state)) selected.value.add(state);
}
const result = (row: ReviewRow) => {
  const state = stateOf(row),
    posting = row.ui_context.posting as { posting_deferred_until?: string } | undefined;
  return state === 'deferred' && posting?.posting_deferred_until
    ? t('statuses.deferredUntil', {
        date: formatDateText(posting.posting_deferred_until, locale.value),
      })
    : t(labels[state]);
};
/** One next action per row. A blocked row opens its review when the server offers one, otherwise
 * the explanation of what blocks it; a deferred row explains its date. */
function action(row: ReviewRow) {
  const state = stateOf(row),
    actions = row.ui_context.actions as { kind?: string }[] | undefined;
  if (state === 'ready') return 'post';
  if (
    state === 'needs_review' ||
    (state !== 'deferred' && actions?.some((a) => a.kind === 'review'))
  )
    return 'review';
  return state === 'blocked' ? 'blocks' : 'why';
}
const period = () => encodeURIComponent(props.context.period);
// Below ten queued rows the aging chart has too little to show; one line names the oldest row.
const oldestDays = computed(() => {
  const oldest = props.rows
    .map((row) => row.transaction_date)
    .filter(Boolean)
    .sort()[0];
  if (!oldest) return null;
  const today = new Date();
  today.setHours(12, 0, 0, 0);
  return Math.max(
    0,
    Math.round((today.getTime() - new Date(`${oldest}T12:00:00`).getTime()) / 86_400_000),
  );
});
</script>
<template>
  <section class="panel review-summary-panel">
    <header class="panel-header">
      <h2>{{ t('review.summary') }}</h2>
      <small>{{ formatPeriod(context.period, locale) }}</small>
    </header>
    <div class="review-summary-grid" role="group" :aria-label="t('review.filterLabel')">
      <button
        type="button"
        class="review-summary-card"
        :aria-pressed="!selected.size"
        @click="selected.clear()"
      >
        <span>{{ t('review.filterAll') }}</span
        ><strong>{{ rows.length }}</strong>
      </button>
      <button
        v-for="state in filters"
        :key="state"
        type="button"
        class="review-summary-card"
        :class="state"
        :aria-pressed="selected.has(state)"
        :disabled="!counts[state]"
        @click="toggle(state)"
      >
        <span>{{ t(labels[state]) }}</span
        ><strong>{{ counts[state] }}</strong>
      </button>
    </div>
  </section>
  <section class="panel">
    <header class="panel-header">
      <h2>{{ t('review.transactions') }}</h2>
      <small>{{ shown.length }}</small>
    </header>
    <div class="table-wrap">
      <table class="review-queue-table">
        <thead>
          <tr>
            <th>{{ t('transactions.date') }}</th>
            <th>{{ t('transactions.counterpartyDocument') }}</th>
            <th>{{ t('review.taxDecision') }}</th>
            <th>{{ t('review.result') }}</th>
            <th>{{ t('transactions.amount') }}</th>
            <th>
              <span class="visually-hidden">{{ t('tables.actions') }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in shown" :key="row.transaction_id">
            <td :data-label="t('transactions.date')">
              {{ formatDateText(row.transaction_date, locale) }}
            </td>
            <td class="cell-primary" :data-label="t('transactions.counterpartyDocument')">
              <strong>{{
                row.counterparty_name || row.description || t('transactions.noCounterparty')
              }}</strong
              ><small
                >{{ row.document_number || row.description || ''
                }}<template v-if="row.document_id">
                  <a
                    class="text-button inline-button"
                    :href="`/api/document/${encodeURIComponent(row.document_id)}/content`"
                    target="_blank"
                    rel="noreferrer"
                    >{{ t('common.openDocument') }}</a
                  ></template
                ></small
              >
            </td>
            <td :data-label="t('review.taxDecision')"><StatusCell :context="row.ui_context" /></td>
            <td class="cell-primary" :data-label="t('review.result')">
              <strong>{{ result(row) }}</strong
              ><small>{{ taxCodeLabel(row.tax_code, locale) }}</small>
            </td>
            <td class="amount" :data-label="t('transactions.amount')">
              {{
                row.amount_eur
                  ? eur(row.amount_eur, locale)
                  : `${row.amount_original || '—'} ${row.currency || ''}`
              }}
            </td>
            <td class="table-actions">
              <a
                v-if="action(row) === 'review'"
                class="secondary-button compact-button"
                :href="`/review/${encodeURIComponent(row.transaction_id)}?period=${period()}`"
                :data-open-review-id="`transaction:${row.transaction_id}`"
                @click="followSpaLink($event, context.services.navigate)"
                >{{ t('review.openWorkspace') }}</a
              ><a
                v-else-if="action(row) === 'post'"
                class="secondary-button compact-button"
                :href="`/review?period=${period()}&tab=posting#posting-row-${encodeURIComponent(row.transaction_id)}`"
                @click="followSpaLink($event, context.services.navigate)"
                >{{ t('review.postRow') }}</a
              ><StatusHelpButton
                v-else
                :context="row.ui_context"
                :label="t(action(row) === 'blocks' ? 'review.whatBlocks' : 'help.words.why')"
              />
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="6">
              <div class="empty-state">{{ t('review.queueEmpty') }}</div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="rows.length < 10 && oldestDays !== null" class="review-queue-age">
      {{ t('review.oldestInQueue', { days: oldestDays }) }}
    </p>
  </section>
  <section v-if="rows.length >= 10" class="panel">
    <ChartHost
      id="chart-review-aging"
      kind="reviewAging"
      :period="context.period"
      :request="context.services.request"
    />
  </section>
</template>
