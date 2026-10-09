<script setup lang="ts">
import { onMounted } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { helpAdapter } from '../../vue/help.ts';
import { codeLabel, eur, formatDateText } from '../../core/format.ts';
import StatusCell from '../../components/StatusCell.vue';
import TermHelp from '../../components/TermHelp.vue';
import { helpContext } from './normalize.ts';
import type { PostingItem } from './model.ts';
/** `anchored`: the posting tab's own lists give each row the id `posting-row-<transaction id>`; the
 * dialog and the results reuse rows of those lists, so they stay without ids. */
const props = defineProps<{ title: string; rows: PostingItem[]; anchored?: boolean }>();
const { locale, t } = useLocale();
const help = helpAdapter();
const anchor = (row: PostingItem) =>
  props.anchored && row.transactionId ? `posting-row-${row.transactionId}` : undefined;
// The queue's «Провести» opens this tab at `#posting-row-<id>`. A router (pushState) navigation
// does not update `:target`, so the row the fragment names is marked and scrolled to here.
const target = props.anchored ? window.location.hash.slice(1) : '';
onMounted(() => {
  if (target && props.rows.some((row) => anchor(row) === target))
    document.getElementById(target)?.scrollIntoView({ block: 'center' });
});
const explanation = (row: PostingItem) => {
  void locale.value;
  if (row.structuredReasons.length)
    return row.structuredReasons.map((reason) => help.text(help.reasonInfo(reason)[0])).join(' · ');
  // A ready or posted row has nothing to explain.
  return ['ready', 'posted'].includes(row.previewBucket || row.rowStatus)
    ? ''
    : t('issues.default');
};
</script>
<template>
  <div v-if="rows.length">
    <h3 class="posting-section-label">{{ title }}</h3>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{{ t('transactions.date') }}</th>
            <th>{{ t('transactions.counterpartyDocument') }}</th>
            <th>{{ t('transactions.status') }} <TermHelp term="posting" /></th>
            <th>{{ t('transactions.amount') }}</th>
            <th>{{ t('review.postingResultMessage') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, index) in rows"
            :key="`${row.transactionId || row.reviewId}:${index}`"
            :id="anchor(row)"
            :class="{ 'posting-row-target': !!target && anchor(row) === target }"
          >
            <td :data-label="t('transactions.date')">
              {{ formatDateText(row.transactionDate, locale) }}
            </td>
            <td class="cell-primary" :data-label="t('transactions.counterpartyDocument')">
              <strong>{{
                row.description || row.reviewId || row.transactionId || t('common.noId')
              }}</strong
              ><small>{{ codeLabel('transactions.types', row.entryType, locale) || '—' }}</small>
            </td>
            <td :data-label="t('transactions.status')">
              <StatusCell :context="helpContext(row)" />
            </td>
            <td class="amount" :data-label="t('transactions.amount')">
              <template v-if="row.amountEur">{{ eur(row.amountEur, locale) }}</template
              ><template v-else
                >—<small class="value-detail">{{ t('review.noEurAmount') }}</small></template
              >
            </td>
            <td class="posting-result-message" :data-label="t('review.postingResultMessage')">
              {{ explanation(row) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
