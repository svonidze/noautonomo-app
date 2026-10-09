<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import {
  aeatCodeLabel,
  codeLabel,
  eur,
  formatDateText,
  formatPeriod,
  taxCodeLabel,
} from '../../core/format.ts';
import { errorMessage } from '../../core/error-message.ts';
import { ApiError } from '../../core/http.ts';
import {
  formatMessage,
  message,
  messageText,
  messageIds,
  localeTag,
  type UiMessage,
} from '../../core/i18n.ts';
import { readReviewDraft, writeReviewDraft, removeReviewDraft } from '../../core/drafts.ts';
import { transactionDetail } from '../expense-detail/model.ts';
import { followSpaLink } from '../../vue/services.ts';
import StatusCell from '../../components/StatusCell.vue';
import DecisionField, { type DecisionFieldSpec } from './DecisionField.vue';
import FxCard from './FxCard.vue';
import RemainingList from './RemainingList.vue';
import {
  clone,
  merge,
  setDecision,
  getDecision,
  evaluate,
  answers,
  autoResolve,
  errorTarget,
  needsFx,
  initialFx,
  confirmFx,
} from './guided-helpers.ts';
import { reviewUi, type ReviewUi } from './review-ui.ts';
import type {
  GuidedContext,
  WorkItem,
  ReviewDecision,
  FxChoice,
  ReviewPacket,
} from './guided-model.ts';
const props = defineProps<{ context: GuidedContext }>();
const { locale, t } = useLocale();
const backUrl = ref(props.context.returnUrl);
const root = ref<HTMLElement>(),
  item = ref<WorkItem>(),
  ui = shallowRef<ReviewUi>();
const fx = ref<FxChoice | null>(null),
  busy = ref(false),
  loading = ref(true),
  loadError = shallowRef<unknown>();
const failure = shallowRef<{ target: string; message?: UiMessage; error?: unknown }>();
let active = true,
  generation = 0;
const current = (revision: number) => active && revision === generation;
onBeforeUnmount(() => {
  active = false;
  generation++;
});
function persist(packet: ReviewPacket, factsOnly = false) {
  writeReviewDraft(() => localStorage, {
    review_id: packet.review_id,
    transaction_id: packet.state.transaction.transaction_id,
    snapshot_hash: packet.snapshot_hash,
    decision: clone(
      factsOnly
        ? { counterparty_changes: packet.decision.counterparty_changes || {} }
        : packet.decision,
    ),
  });
}
async function load(factsOnly = false) {
  const revision = ++generation,
    context = props.context;
  loading.value = !item.value;
  loadError.value = undefined;
  failure.value = undefined;
  try {
    if (factsOnly && item.value) {
      const detail = transactionDetail(
        await context.services.request(
          `/api/transactions/${encodeURIComponent(context.transactionId)}`,
        ),
        context.transactionId,
      );
      if (!current(revision)) return;
      if (
        detail.transaction.entry_type === 'expense' &&
        ['posted', 'included_in_snapshot'].includes(detail.transaction.lifecycle_status)
      ) {
        props.context.posted(context.transactionId, detail.period.period_key);
        return;
      }
    }
    const result = (await context.services.request(
      `/api/review/work-item?review_id=${encodeURIComponent('transaction:' + context.transactionId)}`,
    )) as WorkItem;
    if (!current(revision)) return;
    const packet = clone(result.packet);
    if (
      packet.review_id !== 'transaction:' + context.transactionId ||
      packet.state.transaction.transaction_id !== context.transactionId ||
      !props.context.knownPeriods.includes(packet.state.period.period_key)
    )
      throw new Error('review.invalidWorkItem');
    if (packet.state.transaction.entry_type === 'expense' && packet.state.assets?.length === 1) {
      packet.decision.asset_decision = 'asset';
      packet.decision.asset_id = packet.state.assets[0].asset_id;
    }
    const draft = readReviewDraft(() => localStorage, context.transactionId);
    if (draft)
      packet.decision = merge(
        packet.decision,
        !factsOnly && draft.snapshot_hash === packet.snapshot_hash
          ? draft.decision
          : { counterparty_changes: draft.decision.counterparty_changes || {} },
      );
    item.value = { ...result, packet };
    fx.value = initialFx(result.fx_suggestion);
    ui.value = reactive(reviewUi(context.transactionId, packet.decision.reason || ''));
    persist(packet, factsOnly);
    backUrl.value = props.context.resolved(packet.state.period);
  } catch (error) {
    if (current(revision)) {
      if (item.value) failure.value = { target: 'general', error };
      else loadError.value = error;
    }
  } finally {
    if (current(revision)) {
      loading.value = false;
      props.context.settled();
    }
  }
}
watch(
  () => props.context,
  (context, previous) => {
    if (item.value && context.transactionId === previous?.transactionId) {
      backUrl.value = context.returnUrl;
      return;
    }
    busy.value = false;
    item.value = undefined;
    void load(!!context.factsOnly);
  },
  { immediate: true },
);
const packet = computed(() => item.value!.packet),
  decision = computed(() => packet.value.decision),
  state = computed(() => packet.value.state);
const evaluation = computed(() => (item.value ? evaluate(item.value) : null));
const disabled = computed(() => busy.value || !evaluation.value?.canApply);
// A missing rate is named once in «Что осталось»; the inline alert waits for a confirmation attempt.
const remaining = computed(() =>
  item.value &&
  needsFx(state.value.transaction) &&
  item.value.fx_suggestion &&
  item.value.fx_suggestion.status !== 'existing' &&
  !confirmFx(fx.value, item.value.fx_suggestion)
    ? [{ id: 'review-fx-settlement-rate', text: t('workflow.remaining.rate') }]
    : [],
);
const errorText = computed(() =>
  failure.value?.message
    ? messageText(failure.value.message, locale.value)
    : errorMessage(failure.value?.error, locale.value),
);
const inline = (id: string) => (failure.value?.target === id ? errorText.value : '');
function update(path: string, value: unknown) {
  if (disabled.value) return;
  setDecision(decision.value, path, value);
  failure.value = undefined;
  persist(packet.value);
}
const options = (values: string[], label: (value: string) => string, blank = true) => [
  ...(blank ? [{ value: '', label: '' }] : []),
  ...values.map((value) => ({ value, label: label(value) })),
];
const aeatOptions = (group: 'invoiceType' | 'qualification' | 'exemption', field: string) =>
  options(packet.value.allowed_values?.[field] || [], (value) =>
    aeatCodeLabel(group, value, locale.value),
  );
const partyOptions = (group: 'roiStatus' | 'legalForms', values: string[]) =>
  options(values, (value) => formatMessage(`parties.${group}.${value}`, {}, locale.value), false);
const boolOptions = computed(() => [
  { value: '', label: '' },
  { value: 'true', label: t('common.yes') },
  { value: 'false', label: t('common.no') },
]);
const guidedFields = computed<DecisionFieldSpec[]>(() => [
  { path: 'business_purpose', label: 'fields.businessPurpose', type: 'textarea' },
  ...(state.value.transaction.entry_type === 'expense'
    ? ([
        {
          path: 'asset_decision',
          label: 'review.assetDecision',
          type: 'select',
          options: [
            { value: '', label: '' },
            { value: 'current_expense', label: t('review.assetCurrentExpense') },
            { value: 'asset', label: t('review.assetAsset') },
          ],
        },
        {
          path: 'tax_treatment.vat_investment_good',
          label: 'review.vatInvestment',
          type: 'select',
          valueType: 'nullable-boolean',
          descriptionId: 'vat-investment-help',
          options: [
            { value: '', label: '' },
            { value: 'false', label: t('review.vatCurrent') },
            { value: 'true', label: t('review.vatAsset') },
          ],
        },
        {
          path: 'tax_treatment.deductible_irpf_minor',
          label: 'fields.deductibleIrpfMinor',
          type: 'number',
          valueType: 'integer',
          minor: true,
        },
      ] as DecisionFieldSpec[])
    : []),
  {
    path: 'tax_treatment.tax_code',
    label: 'fields.taxCode',
    type: 'select',
    options: [
      { value: '', label: '' },
      ...(
        packet.value.allowed_values?.tax_code || [
          'domestic_input',
          'domestic_output',
          'domestic_output_zero',
          'eu_goods_income',
          'eu_service_income',
          'export',
          'outside_scope',
          'reverse_charge',
          'professional_withholding',
          'rent_withholding',
          'unknown',
        ]
      ).map((value) => ({ value, label: taxCodeLabel(value, locale.value) })),
    ],
  },
  ...(['', 'ZZ'].includes(String(state.value.counterparty?.country_code || ''))
    ? ([
        { path: 'counterparty_changes.country_code', label: 'fields.counterpartyCountry' },
      ] as DecisionFieldSpec[])
    : []),
  { path: 'reason', label: 'fields.reason', type: 'textarea', full: true },
]);
const technicalFields = computed<DecisionFieldSpec[]>(() => [
  {
    path: 'tax_treatment.aeat_invoice_type',
    label: 'fields.invoiceType',
    type: 'select',
    options: aeatOptions('invoiceType', 'aeat_invoice_type'),
  },
  { path: 'tax_treatment.aeat_operation_key', label: 'fields.operationKey' },
  {
    path: 'tax_treatment.aeat_operation_qualification',
    label: 'fields.operationQualification',
    type: 'select',
    valueType: 'nullable-string',
    options: aeatOptions('qualification', 'aeat_operation_qualification'),
  },
  {
    path: 'tax_treatment.aeat_exemption_code',
    label: 'fields.exemptionCode',
    type: 'select',
    valueType: 'nullable-string',
    options: aeatOptions('exemption', 'aeat_exemption_code'),
  },
  {
    path: 'tax_treatment.aeat_reverse_charge',
    label: 'fields.reverseCharge',
    type: 'select',
    valueType: 'nullable-boolean',
    options: boolOptions.value,
  },
  {
    path: 'tax_treatment.aeat_expense_concept',
    label: 'fields.expenseConcept',
    valueType: 'nullable-string',
  },
  ...(
    ['taxable_base_minor', 'vat_minor', 'deductible_vat_minor', 'withholding_minor'] as const
  ).map(
    (key, index) =>
      ({
        path: 'tax_treatment.' + key,
        label: (
          [
            'fields.taxableBaseMinor',
            'fields.vatMinor',
            'fields.deductibleVatMinor',
            'fields.withholdingMinor',
          ] as const
        )[index],
        type: 'number',
        valueType: 'integer',
        minor: true,
      }) as DecisionFieldSpec,
  ),
  {
    path: 'tax_treatment.deductible_ratio',
    label: 'fields.deductibleRatio',
    type: 'number',
    min: '0',
    max: '1',
    step: '0.01',
    valueType: 'number',
  },
  {
    path: 'tax_treatment.rate_basis_points',
    label: 'fields.rateBasisPoints',
    type: 'number',
    min: '0',
    valueType: 'integer',
    basisPoints: true,
  },
  ...([130, 303, 347] as const).map(
    (code, index) =>
      ({
        path: `tax_treatment.include_modelo${code}`,
        label: (
          ['fields.includeModelo130', 'fields.includeModelo303', 'fields.includeModelo347'] as const
        )[index],
        type: 'checkbox',
        valueType: 'boolean',
      }) as DecisionFieldSpec,
  ),
  { path: 'tax_treatment.notes', label: 'fields.notes', type: 'textarea', full: true },
  { path: 'counterparty_changes.tax_id', label: 'fields.taxId', valueType: 'nullable-string' },
  { path: 'counterparty_changes.vat_id', label: 'fields.vatId', valueType: 'nullable-string' },
  {
    path: 'counterparty_changes.roi_status',
    label: 'fields.roiStatus',
    type: 'select',
    valueType: 'nullable-string',
    options: partyOptions('roiStatus', ['unknown', 'registered', 'not_registered']),
  },
  {
    path: 'counterparty_changes.legal_form',
    label: 'fields.legalForm',
    type: 'select',
    valueType: 'nullable-string',
    options: partyOptions('legalForms', ['unknown', 'individual', 'legal_entity', 'public_body']),
  },
]);
function value(path: string) {
  const result = getDecision(decision.value, path);
  return (
    result ??
    (path.startsWith('counterparty_changes.')
      ? (state.value.counterparty?.[path.split('.')[1]] ??
        (path.endsWith('roi_status') || path.endsWith('legal_form') ? 'unknown' : ''))
      : result)
  );
}
const question = (path: string) =>
  path === 'counterparty_changes.country_code' ? 'counterparty_country' : path.split('.').at(-1)!;
const chips = computed(() => {
  const filled = item.value?.guidance?.auto_filled || {},
    result: { label: string; value: string; source: string }[] = [];
  if (filled.business_purpose)
    result.push({
      label: t('fields.businessPurpose'),
      value: filled.business_purpose,
      source: t('review.autoFilledIntake'),
    });
  if (filled.deductible_irpf_minor != null)
    result.push({
      label: t('fields.deductibleIrpfMinor'),
      value: eur(filled.deductible_irpf_minor / 100, locale.value),
      source: t('review.autoFilledExtraction'),
    });
  const names: Record<string, string> = {
    current_expense: t('review.assetCurrentExpense'),
    asset: t('review.assetAsset'),
    not_applicable: t('review.assetNotApplicable'),
  };
  if (filled.asset_decision && names[filled.asset_decision])
    result.push({
      label: t('review.assetDecision'),
      value: names[filled.asset_decision],
      source: t('review.autoFilledDefault'),
    });
  return result;
});
const issueRows = computed(() =>
  (state.value.issues || []).map((issue, index) => {
    const found = issue.validation_issue_id
      ? (decision.value.issue_resolutions?.findIndex(
          (row) => row.issue_id === issue.validation_issue_id,
        ) ?? -1)
      : index;
    const target = found < 0 ? index : found,
      resolution = decision.value.issue_resolutions?.[target],
      coverage = item.value?.guidance?.issue_coverage?.[issue.issue_code] || [],
      answered = answers(decision.value, state.value, fx.value);
    const key = `issues.messages.${issue.issue_code}`;
    return {
      issue,
      target,
      resolution,
      auto: coverage.length > 0 && coverage.every((key) => answered[key]),
      count: coverage.length,
      label: formatMessage(messageIds.includes(key) ? key : 'issues.default', {}, locale.value),
    };
  }),
);
async function submit(outcome: 'approve' | 'reject') {
  if (!item.value || busy.value || (outcome === 'approve' && !evaluation.value?.canApply)) return;
  const context = props.context,
    revision = generation,
    original = packet.value,
    submitted = clone(original);
  const priorDraft = readReviewDraft(() => localStorage, context.transactionId);
  let fxSpec = null;
  if (outcome === 'approve') {
    fxSpec = needsFx(original.state.transaction)
      ? confirmFx(fx.value, item.value.fx_suggestion)
      : null;
    if (needsFx(original.state.transaction) && !fxSpec) {
      failure.value = { target: 'fx_rate', message: message('review.fxNeeded') };
      return;
    }
    submitted.decision.outcome = 'approve';
    submitted.decision.document_valid = true;
    submitted.decision.counterparty_changes ||= {};
    autoResolve(
      submitted,
      item.value.guidance?.issue_coverage || {},
      answers(submitted.decision, submitted.state, fx.value),
      locale.value,
    );
  } else {
    if (!['true', 'false'].includes(ui.value!.rejectDocumentValid)) {
      failure.value = { target: 'reject', message: message('review.rejectDocumentRequired') };
      return;
    }
    if (!ui.value!.rejectReason.trim()) {
      failure.value = { target: 'reject', message: message('review.rejectReasonRequired') };
      return;
    }
    submitted.decision.outcome = 'reject';
    submitted.decision.document_valid = ui.value!.rejectDocumentValid === 'true';
    submitted.decision.reason = ui.value!.rejectReason.trim();
    submitted.decision.counterparty_changes = {};
    submitted.decision.tax_treatment = { ...submitted.decision.tax_treatment, tax_code: null };
  }
  busy.value = true;
  failure.value = undefined;
  try {
    await context.services.request('/api/review/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ packet: submitted, fx: fxSpec }),
    });
    if (
      JSON.stringify(readReviewDraft(() => localStorage, context.transactionId)) ===
      JSON.stringify(priorDraft)
    )
      removeReviewDraft(() => localStorage, context.transactionId);
    if (current(revision)) props.context.complete(outcome);
  } catch (error) {
    if (current(revision)) {
      failure.value = { target: outcome === 'reject' ? 'reject' : errorTarget(error), error };
      await nextTick();
      root.value?.querySelector<HTMLElement>('[role="alert"]')?.focus();
    }
  } finally {
    if (current(revision)) busy.value = false;
  }
}
const originalAmount = computed(() => {
  const transaction = state.value.transaction;
  return transaction.amount_original_minor != null
    ? `${new Intl.NumberFormat(localeTag(locale.value), { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(transaction.amount_original_minor / 100)} ${transaction.original_currency || transaction.currency || ''}`
    : '—';
});
const changeFx = (choice: FxChoice) => {
  if (!disabled.value) {
    fx.value = choice;
    failure.value = undefined;
  }
};
const retry = () =>
  loadError.value instanceof ApiError && loadError.value.code === 'session_forbidden'
    ? window.location.reload()
    : void load();
</script>
<template>
  <div ref="root" class="review-workspace">
    <a
      class="text-button back-link"
      :href="backUrl"
      @click="followSpaLink($event, context.services.navigate)"
      ><span aria-hidden="true">←</span> {{ t('review.workspaceBack') }}</a
    >
    <p v-if="loading" role="status">{{ t('common.loading') }}</p>
    <div v-else-if="loadError" class="empty-state state-error" role="alert">
      <p>{{ errorMessage(loadError, locale) }}</p>
      <button class="secondary-button" @click="retry">
        {{
          t(
            loadError instanceof ApiError && loadError.code === 'session_forbidden'
              ? 'common.reload'
              : 'common.retry',
          )
        }}
      </button>
    </div>
    <template v-else-if="item && ui && evaluation">
      <div class="review-workspace-header">
        <div class="review-workspace-title">
          <h2>
            {{
              state.counterparty?.display_name ||
              state.document?.document_number ||
              t('review.workspaceTitle')
            }}
          </h2>
          <p>
            {{
              state.document?.document_number
                ? t('review.invoiceLabel', { number: state.document.document_number })
                : t('review.workspaceTitle')
            }}
          </p>
        </div>
        <StatusCell class="review-workspace-status" :context="item.ui_context" />
      </div>
      <div v-if="!evaluation.supported" class="review-alert error" role="alert">
        <strong>{{ t('review.unsupported') }}</strong>
        <p v-if="evaluation.reasonKey && evaluation.reasonKey !== 'review.unsupported'">
          {{ formatMessage(evaluation.reasonKey, {}, locale) }}
        </p>
      </div>
      <div
        v-if="['later', 'blocked'].includes(evaluation.category)"
        class="review-alert warning"
        role="alert"
      >
        <p v-if="evaluation.availableOn">
          {{ t('review.future', { date: formatDateText(evaluation.availableOn, locale) }) }}
        </p>
        <p>{{ t('review.formDisabledHint') }}</p>
      </div>
      <p v-else-if="!evaluation.canApply && evaluation.availableOn" class="review-alert warning">
        {{ t('review.future', { date: formatDateText(evaluation.availableOn, locale) }) }}
      </p>
      <div
        v-if="failure && ['general', 'document_valid'].includes(failure.target)"
        class="review-alert error"
        role="alert"
      >
        {{ errorText }}
      </div>
      <RemainingList :items="remaining" />
      <form id="review-form" class="review-form" @submit.prevent="submit('approve')">
        <section class="panel review-panel">
          <header class="panel-header">
            <h3>{{ t('review.factsTitle') }}</h3>
            <small>{{ formatPeriod(state.period.period_key, locale) }}</small>
          </header>
          <div class="review-facts-summary">
            <dl class="review-facts-list">
              <div>
                <dt>{{ t('fields.transactionDate') }}</dt>
                <dd>{{ formatDateText(state.transaction.transaction_date, locale) }}</dd>
              </div>
              <div>
                <dt>{{ t('transactions.counterpartyDocument') }}</dt>
                <dd>
                  {{ state.counterparty?.display_name || '—' }} ·
                  {{ state.document?.document_number || '—' }}
                </dd>
              </div>
              <div>
                <dt>{{ t('fields.currency') }}</dt>
                <dd>
                  {{
                    state.transaction.original_currency ||
                    state.transaction.currency ||
                    state.document?.currency ||
                    'EUR'
                  }}
                </dd>
              </div>
              <div>
                <dt>{{ t('fields.amount') }}</dt>
                <dd>
                  {{ originalAmount
                  }}<template v-if="state.transaction.amount_eur_minor != null">
                    → {{ eur(state.transaction.amount_eur_minor / 100, locale) }}</template
                  >
                </dd>
              </div>
              <div>
                <dt>{{ t('fields.issuedOn') }}</dt>
                <dd>{{ formatDateText(state.document?.issued_on, locale) }}</dd>
              </div>
            </dl>
            <div v-if="chips.length" class="review-autofilled">
              <span class="review-autofilled-title">{{ t('review.autoFilledTitle') }}</span
              ><span
                v-for="chip in chips"
                :key="chip.label"
                class="review-autofilled-chip"
                :title="chip.source"
                ><strong>{{ chip.label }}:</strong> {{ chip.value
                }}<small>{{ chip.source }}</small></span
              >
            </div>
            <a
              v-if="state.document?.document_id"
              class="text-button"
              :href="`/api/document/${encodeURIComponent(state.document.document_id)}/content`"
              target="_blank"
              rel="noreferrer"
              >{{ t('common.openDocument') }}</a
            >
          </div>
        </section>
        <section class="panel review-panel">
          <header class="panel-header">
            <h3>{{ t('review.questionsTitle') }}</h3>
            <small>{{
              codeLabel('transactions.types', state.transaction.entry_type, locale)
            }}</small>
          </header>
          <div class="review-guided-grid">
            <template v-for="field in guidedFields" :key="field.path"
              ><DecisionField
                :field="field"
                :value="value(field.path)"
                :disabled="disabled"
                guided
                :error="inline(question(field.path))"
                @change="update(field.path, $event)"
                ><small
                  v-if="field.path === 'tax_treatment.vat_investment_good'"
                  id="vat-investment-help"
                  >{{ t('review.vatInvestmentHint') }}</small
                ></DecisionField
              ></template
            >
          </div>
          <FxCard
            v-if="item.fx_suggestion"
            :suggestion="item.fx_suggestion"
            :choice="fx"
            :disabled="disabled"
            :error="inline('fx_rate')"
            @change="changeFx"
          />
          <div v-if="issueRows.length" class="review-issues-grid">
            <article
              v-for="row in issueRows"
              :key="row.issue.validation_issue_id || row.target"
              class="review-issue-card"
              :class="{ resolved: row.resolution?.action === 'resolve', 'auto-closable': row.auto }"
            >
              <header>
                <strong>{{ row.label }}</strong
                ><span>{{
                  t(
                    row.resolution?.action === 'resolve'
                      ? 'review.issueResolved'
                      : 'review.issueOpen',
                  )
                }}</span>
              </header>
              <details v-if="row.issue.message">
                <summary>{{ t('issues.serverMessage') }}</summary>
                <p>{{ row.issue.message }}</p>
              </details>
              <p v-if="row.auto" class="review-auto-close-hint">
                {{ t('review.issueAutoResolveHint', { labels: row.count }) }}
              </p>
              <DecisionField
                :field="{
                  path: `issue_resolutions.${row.target}.action`,
                  label: 'fields.reviewAction',
                  type: 'select',
                  valueType: 'nullable-string',
                  options: [
                    { value: '', label: '' },
                    { value: 'resolve', label: t('review.actionResolve') },
                  ],
                }"
                :value="row.resolution?.action"
                :disabled="disabled"
                @change="update(`issue_resolutions.${row.target}.action`, $event)"
              /><DecisionField
                :field="{
                  path: `issue_resolutions.${row.target}.reason`,
                  label: 'fields.reviewReason',
                  type: 'textarea',
                }"
                :value="row.resolution?.reason"
                :disabled="disabled"
                :error="inline('issues')"
                @change="update(`issue_resolutions.${row.target}.reason`, $event)"
              />
            </article>
          </div>
          <details
            class="review-technical-details"
            :open="ui.technicalOpen"
            @toggle="ui.technicalOpen = ($event.target as HTMLDetailsElement).open"
          >
            <summary>{{ t('review.technicalDetails') }}</summary>
            <div class="form-grid compact-grid">
              <DecisionField
                v-for="field in technicalFields"
                :key="field.path"
                :field="field"
                :value="value(field.path)"
                :disabled="disabled"
                @change="update(field.path, $event)"
              />
            </div>
          </details>
        </section>
        <section class="panel review-panel">
          <header class="panel-header">
            <h3>{{ t('review.result') }}</h3>
            <small>{{ t('review.confirmHint') }}</small>
          </header>
          <div class="review-actions">
            <button
              type="button"
              class="secondary-button"
              id="review-refresh-button"
              :disabled="busy"
              @click="load(true)"
            >
              {{ t('common.refresh') }}</button
            ><button
              type="submit"
              class="primary-button"
              id="review-primary-button"
              :disabled="disabled"
              :data-locked="!evaluation.canApply || undefined"
            >
              {{ t(busy ? 'review.submitting' : 'review.confirmAction') }}
            </button>
          </div>
          <details
            class="review-reject-panel"
            :open="ui.rejectOpen"
            @toggle="ui.rejectOpen = ($event.target as HTMLDetailsElement).open"
          >
            <summary>{{ t('review.rejectAction') }}</summary>
            <div class="review-reject-body">
              <p class="muted-copy">{{ t('review.rejectLead') }}</p>
              <label
                ><span>{{ t('fields.documentValid') }}</span
                ><select
                  id="review-reject-document-valid"
                  v-model="ui.rejectDocumentValid"
                  :disabled="busy"
                >
                  <option value=""></option>
                  <option value="true">{{ t('common.yes') }}</option>
                  <option value="false">{{ t('common.no') }}</option>
                </select></label
              ><label
                ><span>{{ t('fields.reason') }}</span
                ><textarea
                  id="review-reject-reason"
                  v-model="ui.rejectReason"
                  rows="2"
                  :disabled="busy"
                ></textarea>
              </label>
              <p v-if="inline('reject')" class="review-inline-error" role="alert">
                {{ inline('reject') }}
              </p>
              <div class="review-reject-actions">
                <button
                  type="button"
                  class="danger-button"
                  id="review-reject-button"
                  :disabled="busy"
                  @click="submit('reject')"
                >
                  {{ t(busy ? 'review.submitting' : 'review.rejectConfirm') }}
                </button>
              </div>
            </div>
          </details>
        </section>
      </form>
    </template>
  </div>
</template>
