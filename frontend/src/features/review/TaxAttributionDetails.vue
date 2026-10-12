<script setup lang="ts">
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateText } from '../../core/format.ts';
import type { TaxAttribution } from './tax-attribution-model.ts';
defineProps<{
  attribution?: TaxAttribution | null;
  sourceDate?: string;
  entryType: string;
}>();
const { locale, t } = useLocale();
const money = (value: number | null) => eur(value == null ? null : value / 100, locale.value);
</script>

<template>
  <section v-if="attribution" class="panel review-panel" data-testid="tax-attribution">
    <header class="panel-header">
      <h2>{{ t('taxAttribution.title') }}</h2>
    </header>
    <div class="expense-panel-body">
      <p v-if="attribution.stale" role="alert">{{ t('taxAttribution.stale') }}</p>
      <dl class="review-facts-list">
        <div>
          <dt>{{ t('fields.transactionDate') }}</dt>
          <dd>{{ formatDateText(sourceDate, locale) }}</dd>
        </div>
        <div>
          <dt>{{ t('taxAttribution.irpfDate') }}</dt>
          <dd>{{ formatDateText(attribution.decision.irpf?.date || sourceDate, locale) }}</dd>
        </div>
        <div v-if="attribution.decision.irpf">
          <dt>
            {{ t(entryType === 'income' ? 'taxAttribution.income' : 'transactions.irpfDeduction') }}
          </dt>
          <dd>
            {{
              money(
                entryType === 'income'
                  ? attribution.decision.irpf.income_minor
                  : attribution.decision.irpf.deductible_minor,
              )
            }}
          </dd>
        </div>
        <div>
          <dt>{{ t('taxAttribution.ivaDate') }}</dt>
          <dd>{{ formatDateText(attribution.decision.iva?.date || sourceDate, locale) }}</dd>
        </div>
        <div v-if="attribution.decision.iva">
          <dt>{{ t('taxAttribution.ivaBase') }}</dt>
          <dd>
            {{
              attribution.decision.iva.pending_reason
                ? t('taxAttribution.pending')
                : money(attribution.decision.iva.base_minor)
            }}
          </dd>
        </div>
      </dl>
      <template v-if="attribution.decision.fx.length">
        <h3>{{ t('taxAttribution.fx') }}</h3>
        <ul>
          <li v-for="event in attribution.decision.fx" :key="event.event_key">
            {{ formatDateText(event.date, locale) }} · {{ money(event.amount_minor) }}
          </li>
        </ul>
      </template>
      <p>{{ attribution.reason }}</p>
      <small v-if="attribution.historical_correction">{{ t('taxAttribution.history') }}</small>
    </div>
  </section>
</template>
