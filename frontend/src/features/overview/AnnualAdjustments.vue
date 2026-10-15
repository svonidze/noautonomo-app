<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateText } from '../../core/format.ts';
import { documentHref, type ViewServices } from '../../vue/services.ts';

interface Adjustment {
  annual_adjustment_id: string;
  adjustment_kind: 'income' | 'expense';
  amount_minor: number;
  currency: string;
  recognized_on: string;
  counterparty_name: string | null;
  rationale: string;
  evidence_status: 'verified' | 'provisional';
  document_id: string | null;
  voided: number;
}
interface AnnualPreparation {
  annual_adjustments: Adjustment[];
  status: string;
}
const props = defineProps<{ year: number; services: ViewServices }>();
const { locale, t } = useLocale();
const data = shallowRef<AnnualPreparation>();
const failed = ref(false);
let active = true,
  revision = 0;
onBeforeUnmount(() => {
  active = false;
  revision++;
});
watch(
  () => [props.year, props.services] as const,
  async () => {
    const current = ++revision;
    data.value = undefined;
    failed.value = false;
    try {
      const result = (await props.services.request(
        `/api/annual-adjustments?year=${encodeURIComponent(props.year)}`,
      )) as AnnualPreparation;
      if (active && current === revision) data.value = result;
    } catch {
      if (active && current === revision) failed.value = true;
    }
  },
  { immediate: true },
);
const rows = computed(() =>
  (data.value?.annual_adjustments || []).filter(
    (row) =>
      !row.voided && row.currency === 'EUR' && ['income', 'expense'].includes(row.adjustment_kind),
  ),
);
const total = computed(() =>
  rows.value.reduce(
    (sum, row) => sum + (row.adjustment_kind === 'expense' ? -row.amount_minor : row.amount_minor),
    0,
  ),
);
</script>
<template>
  <section v-if="data?.annual_adjustments?.length" class="metric annual-adjustment-card">
    <details>
      <summary>
        {{ t('dashboard.annualAdjustments', { year }) }} ·
        {{ data?.status === 'blocked' ? '—' : eur(total / 100, locale) }}
      </summary>
      <p>{{ t('dashboard.annualScope') }}</p>
      <p v-if="data?.status === 'provisional'" role="status">
        {{ t('dashboard.annualProvisional') }}
      </p>
      <p v-if="failed || data?.status === 'blocked'" role="alert">
        {{ t('dashboard.ytdUnavailable') }}
      </p>
      <ul>
        <li v-for="row in rows" :key="row.annual_adjustment_id">
          {{ formatDateText(row.recognized_on, locale) }} ·
          {{ row.counterparty_name || row.rationale }} · {{ eur(row.amount_minor / 100, locale) }} ·
          {{
            t(
              row.evidence_status === 'verified'
                ? 'dashboard.annualVerified'
                : 'dashboard.annualPending',
            )
          }}
          <a
            v-if="row.document_id"
            :href="documentHref(services, row.document_id)"
            target="_blank"
            rel="noreferrer"
            >{{ t('common.openDocument') }}</a
          >
        </li>
      </ul>
    </details>
  </section>
</template>
<style scoped>
.annual-adjustment-card {
  grid-column: 1 / -1;
}
</style>
