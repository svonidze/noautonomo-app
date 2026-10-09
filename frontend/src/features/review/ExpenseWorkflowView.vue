<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { errorMessage } from '../../core/error-message.ts';
import { ApiError } from '../../core/http.ts';
import {
  message,
  messageText,
  formatMessage,
  messageIds,
  type UiMessage,
} from '../../core/i18n.ts';
import { operationRequest } from '../../core/operation-request.ts';
import {
  aeatCodeLabel,
  eur,
  formatDateText,
  formatPeriod,
  taxCodeLabel,
} from '../../core/format.ts';
import { focusById } from '../../core/focus.ts';
import { followSpaLink } from '../../vue/services.ts';
import { fxNoteText } from './guided-helpers.ts';
import type { Counterparty } from '../contacts/model.ts';
import WorkflowField from './WorkflowField.vue';
import RemainingList, { type RemainingItem } from './RemainingList.vue';
import {
  chooseRule,
  copyProposal,
  defaults,
  explanationValues,
  proposalInputs,
  factsFields,
  taxFields,
  technicalFields,
  fieldId,
  fieldValue,
  setField,
  type ExpensePayload,
  type WorkflowContext,
  type WorkflowDraft,
  type WorkflowPreview,
  type PostedExpense,
  type Scalar,
  type FieldSpec,
} from './workflow-model.ts';
const props = defineProps<{ context: WorkflowContext }>();
const { locale, t } = useLocale();
const root = ref<HTMLElement>();
const draft = shallowRef<WorkflowDraft>(),
  payload = ref<ExpensePayload>();
const suppliers = shallowRef<Counterparty[]>([]),
  proposed = shallowRef<WorkflowPreview>(),
  posted = shallowRef<PostedExpense>();
const error = shallowRef<unknown>(),
  notice = shallowRef<UiMessage | string>(''),
  busy = ref(false),
  confirming = ref(false);
const supplierSearch = ref(''),
  settlementRate = ref(''),
  settlementReference = ref(''),
  technicalOpen = ref(false),
  moreOpen = ref(false),
  ruleOpen = ref(false),
  savedInputs = ref('');
// A matched supplier or activity, and the expense type, read as text until «Изменить» opens them.
const shown = reactive({ supplier: false, activity: false, kind: false });
let active = true,
  generation = 0,
  request: ReturnType<typeof operationRequest> | null = null;
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
const base = () => `/api/expense-workflows/${encodeURIComponent(props.context.transactionId)}`;
const queueUrl = computed(() => `/review?${new URLSearchParams({ period: props.context.period })}`);
const current = (revision: number) => active && generation === revision;
const steps = [
  { id: 'wf-document', label: 'workflow.steps.document' },
  { id: 'wf-taxes', label: 'workflow.steps.taxes' },
  { id: 'wf-preview', label: 'workflow.steps.preview' },
] as const;
const step = ref<string>(steps[0].id);
let observer: IntersectionObserver | undefined;
onBeforeUnmount(() => {
  active = false;
  generation++;
  observer?.disconnect();
});
function goToStep(id: string) {
  step.value = id;
  focusById(`${id}-title`, 'start');
}
// The highlighted step follows the reading position where the browser reports it; the links work
// without it.
watch(
  () => !!(draft.value && payload.value) && !posted.value,
  async (visible) => {
    observer?.disconnect();
    observer = undefined;
    if (!visible || typeof IntersectionObserver !== 'function') return;
    await nextTick();
    if (!active) return;
    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) if (entry.isIntersecting) step.value = entry.target.id;
      },
      { rootMargin: '-30% 0px -60% 0px' },
    );
    for (const { id } of steps) {
      const section = document.getElementById(id);
      if (section) observer.observe(section);
    }
  },
);
async function load() {
  const revision = ++generation,
    context = props.context;
  draft.value = undefined;
  payload.value = undefined;
  proposed.value = undefined;
  posted.value = undefined;
  error.value = undefined;
  try {
    const [value, parties] = await Promise.all([
      context.services.request(base()) as Promise<WorkflowDraft>,
      context.services.request('/api/counterparties') as Promise<Counterparty[]>,
    ]);
    if (!current(revision)) return;
    if (value.source.assets.length) {
      context.onLegacy();
      return;
    }
    if (!value.editable) {
      context.onPosted({ transaction_id: context.transactionId });
      return;
    }
    draft.value = value;
    payload.value = clone(value.payload);
    suppliers.value = parties;
    // Before browser defaults: the server proposal was calculated without them.
    savedInputs.value = proposalInputs(payload.value);
    defaults(payload.value, value.deduction_rules);
    shown.supplier = !matchedSupplier.value;
    shown.activity = !chosenActivity.value;
    shown.kind = false;
    ruleOpen.value = !!payload.value.deduction;
  } catch (failure) {
    if (current(revision)) error.value = failure;
  } finally {
    if (current(revision)) context.settled();
  }
}
watch(
  () => props.context,
  (context, previous) => {
    if (draft.value && payload.value && previous?.transactionId === context.transactionId) return;
    busy.value = false;
    notice.value = '';
    request = null;
    supplierSearch.value = '';
    settlementRate.value = '';
    settlementReference.value = '';
    technicalOpen.value = false;
    moreOpen.value = false;
    void load();
  },
  { immediate: true },
);
const retryLoad = () =>
  error.value instanceof ApiError && error.value.code === 'session_forbidden'
    ? window.location.reload()
    : void load();
const tax = computed(() => payload.value!.decision.tax_treatment);
const MANUAL_ISSUES = ['document_structural_review', 'document_classification_review'];
const manual = computed(() =>
  draft.value?.source.issues.some((issue) => MANUAL_ISSUES.includes(issue.issue_code)),
);
// The server previews verified PDF, JPEG and PNG originals only; anything else is a link.
const previewable = computed(() =>
  ['application/pdf', 'image/jpeg', 'image/png'].includes(
    draft.value?.source.document.mime_type || '',
  ),
);
const matchedSupplier = computed(() =>
  payload.value?.facts.counterparty_id && !payload.value.supplier
    ? suppliers.value.find(
        (party) => party.counterparty_id === payload.value!.facts.counterparty_id,
      )
    : undefined,
);
const chosenActivity = computed(() =>
  draft.value?.activities.find(
    (activity) => activity.business_activity_id === payload.value?.facts.business_activity_id,
  ),
);
async function change(part: keyof typeof shown, id: string) {
  shown[part] = true;
  await nextTick();
  document.getElementById(id)?.focus();
}
// The server asks for this reason when an approval is withdrawn or a source fact is corrected, so
// a first review shows it only after such a correction (or when it already holds text).
const sourceFacts = [
  'document_number',
  'issued_on',
  'transaction_date',
  'booking_date',
  'currency',
  'gross_minor',
  'counterparty_id',
];
const changeReasonShown = computed(
  () =>
    !['received', 'extracted', 'needs_review'].includes(
      draft.value?.source.transaction?.lifecycle_status || '',
    ) ||
    !!payload.value?.change_reason ||
    sourceFacts.some(
      (key) =>
        (payload.value?.facts[key] ?? null) !== (draft.value?.source_values.facts[key] ?? null),
    ),
);
// The currency and the booking date are usually EUR and the transaction date; other values unfold.
const factField = (path: string) => factsFields.find((field) => field.path === path)!;
const mainFacts = [
  'facts.document_number',
  'facts.gross_minor',
  'facts.issued_on',
  'facts.transaction_date',
].map(factField);
const moreFacts = ['facts.currency', 'facts.booking_date'].map(factField);
watch(
  () =>
    !!payload.value &&
    (payload.value.facts.currency !== 'EUR' ||
      payload.value.facts.booking_date !== payload.value.facts.transaction_date),
  (unusual) => {
    if (unusual) moreOpen.value = true;
  },
);
const sourceUrl = computed(
  () =>
    `/api/document/${encodeURIComponent(draft.value?.source.document.document_id || '')}/content`,
);
// The document's amounts with the rate beside its IVA, then the reviewer's deductions.
const taxField = (name: string) =>
  taxFields.find((field) => field.path === `decision.tax_treatment.${name}`)!;
const taxFacts = ['taxable_base_minor', 'vat_minor', 'rate_basis_points'].map(taxField);
const taxDecisions = computed(() =>
  ['deductible_vat_minor', ...(payload.value?.asset ? [] : ['deductible_irpf_minor'])].map(
    taxField,
  ),
);
const allowedTaxes = computed(() =>
  (draft.value?.allowed_values.tax_code || []).filter(
    (code) =>
      ![
        'domestic_output',
        'domestic_output_zero',
        'eu_goods_income',
        'eu_service_income',
        'export',
        'outside_scope',
      ].includes(code),
  ),
);
function invalidate() {
  proposed.value = undefined;
  request = null;
}
function update(path: string, value: Scalar) {
  if (busy.value || !payload.value) return;
  setField(payload.value, path, value);
  invalidate();
  if (path === 'decision.tax_treatment.tax_code') {
    tax.value.include_modelo130 = !payload.value.asset;
    tax.value.include_modelo303 = true;
    tax.value.include_modelo347 = true;
    tax.value.aeat_operation_key = ['eu_service_expense', 'eu_goods_expense'].includes(
      String(value),
    )
      ? '09'
      : '01';
    tax.value.aeat_reverse_charge = [
      'eu_service_expense',
      'eu_goods_expense',
      'non_eu_service_expense',
      'domestic_reverse_charge_expense',
    ].includes(String(value));
  }
  if (path === 'facts.counterparty_id') payload.value.supplier = null;
  if (path === 'asset.method' && payload.value.asset)
    payload.value.asset.annual_rate_basis_points = value === 'immediate' ? 10000 : null;
  if (path === 'facts.currency') {
    payload.value.facts.currency = String(value).toUpperCase();
    payload.value.fx = null;
  }
}
function select(path: string, event: Event, boolean = false) {
  const value = (event.target as HTMLSelectElement).value;
  update(path, value === '' ? (boolean ? null : '') : boolean ? value === 'true' : value);
}
function check(path: string, event: Event) {
  update(path, (event.target as HTMLInputElement).checked);
}
function setKind(event: Event) {
  if (!payload.value || busy.value) return;
  payload.value.asset =
    (event.target as HTMLSelectElement).value === 'asset'
      ? {
          description: '',
          basis_minor: tax.value.taxable_base_minor,
          business_use_ratio: 1,
          annual_rate_basis_points: 10000,
          placed_in_service_on: payload.value.facts.transaction_date,
          method: 'immediate',
          new_equipment: false,
          aeat_asset_type: '23',
        }
      : null;
  invalidate();
}
function newSupplier() {
  if (!payload.value || busy.value) return;
  payload.value.facts.counterparty_id = null;
  payload.value.supplier = { display_name: '', tax_id: '', vat_id: '', country_code: '' };
  invalidate();
}
async function task(callback: (revision: number) => Promise<void>) {
  if (busy.value || !active) return;
  const revision = generation;
  busy.value = true;
  error.value = undefined;
  try {
    await callback(revision);
  } catch (failure) {
    if (current(revision)) error.value = failure;
  } finally {
    if (current(revision)) {
      busy.value = false;
      await nextTick();
      if (error.value) root.value?.querySelector<HTMLElement>('[role="alert"]')?.focus();
    }
  }
}
async function save(revision: number) {
  const result = (await props.context.services.request(base() + '/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      payload: payload.value,
      expected_version: draft.value!.draft_version,
      source_snapshot_hash: draft.value!.source_snapshot_hash,
    }),
  })) as WorkflowDraft;
  if (!current(revision)) return false;
  draft.value = result;
  payload.value = clone(result.payload);
  savedInputs.value = proposalInputs(payload.value);
  notice.value = message('workflow.draftSavedOnTheServer');
  return true;
}
const saveOnly = () =>
  task(async (revision) => {
    if (await save(revision)) invalidate();
  });
async function preview() {
  await task(async (revision) => {
    if (!(await save(revision))) return;
    const value = (await props.context.services.request(base() + '/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_version: draft.value!.draft_version }),
    })) as WorkflowPreview;
    if (!current(revision)) return;
    if (typeof value.preview_token !== 'string' || !value.preview_token)
      throw new Error('review.invalidWorkItem');
    proposed.value = value;
    request = operationRequest(
      'expense-post:' + props.context.transactionId,
      value.preview_token,
      draft.value!.draft_version,
    );
  });
  if (!proposed.value || error.value || busy.value || !active) return;
  await nextTick();
  step.value = 'wf-preview';
  focusById('wf-preview-title', 'start');
}
async function confirm() {
  if (busy.value) return;
  confirming.value = true;
  try {
    await task(async (revision) => {
      if (!request || !proposed.value) return;
      const result = (await props.context.services.request(base() + '/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      })) as PostedExpense;
      if (current(revision)) {
        if (result.transaction_id !== props.context.transactionId)
          throw new Error('review.invalidWorkItem');
        posted.value = result;
        notice.value = '';
      }
    });
  } finally {
    confirming.value = false;
  }
}
const followUp = () =>
  task(async (revision) => {
    const result = (await props.context.services.request(base() + '/follow-up', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })) as Partial<PostedExpense>;
    if (current(revision)) posted.value = { ...posted.value!, ...result };
  });
function reset() {
  if (
    !draft.value ||
    busy.value ||
    !window.confirm(t('workflow.replaceFieldsWithCurrentSourceValuesTheSavedDraft'))
  )
    return;
  payload.value = clone(draft.value.source_values);
  draft.value = {
    ...draft.value,
    source_snapshot_hash: draft.value.current_snapshot_hash,
    conflict: false,
  };
  defaults(payload.value, draft.value.deduction_rules);
  invalidate();
}
const rules = computed(() => draft.value?.deduction_rules ?? []);
const rule = computed(() =>
  rules.value.find((item) => item.id === payload.value?.deduction?.rule_id),
);
const parserRule = computed(() => draft.value?.source_values.deduction?.rule_id ?? null);
// The saved proposal describes the saved draft; any unsaved input makes it stale.
const stale = computed(
  () => !!payload.value && proposalInputs(payload.value) !== savedInputs.value,
);
const proposal = computed(() => {
  const value = draft.value?.deduction_proposal;
  return value && value.rule_id === payload.value?.deduction?.rule_id ? value : undefined;
});
const ruleText = (key: string, values: Record<string, unknown> = {}) =>
  messageIds.includes(key) ? formatMessage(key, values, locale.value) : key;
const ruleName = (id: string) => ruleText(`expenseRules.rule.${id}`);
function onRule(event: Event) {
  if (!payload.value || busy.value) return;
  chooseRule(payload.value, rules.value, (event.target as HTMLSelectElement).value);
  invalidate();
}
function setCount(name: string, event: Event) {
  const raw = (event.target as HTMLInputElement).value;
  const value = Math.trunc(Number(raw));
  update(
    `deduction.facts.${name}`,
    raw === '' || !Number.isFinite(value) ? null : Math.min(366, Math.max(0, value)),
  );
}
const riskBadge: Record<string, string> = {
  low: 'status-positive',
  medium: 'status-pending',
  high: 'status-attention',
};
const copyPair = computed(() => {
  const pair = proposal.value?.suggested;
  if (!pair || stale.value) return '';
  const irpf = eur(pair.irpf_minor / 100, locale.value);
  return pair.vat_minor == null
    ? t('expenseRules.copyPairIrpfOnly', { irpf })
    : t('expenseRules.copyPair', { irpf, vat: eur(pair.vat_minor / 100, locale.value) });
});
// An area rule owns the share; the tax grid does not repeat it under another label.
const visibleTechnical = computed(() =>
  technicalFields.filter(
    (field) =>
      !rule.value?.facts.includes('area_share') || !field.path.endsWith('deductible_ratio'),
  ),
);
function copyIntoFields() {
  if (!payload.value || !proposal.value || stale.value || busy.value) return;
  if (!copyProposal(payload.value, proposal.value)) return;
  invalidate();
  notice.value = message('expenseRules.copied');
}
const previewRule = computed(() => {
  const value = proposed.value?.deduction;
  if (!value || value.irpf_minor == null || !proposed.value) return '';
  const gap = (ceiling: number, entered: number) => eur((ceiling - entered) / 100, locale.value);
  const irpf = t('expenseRules.previewIrpf', {
    rule: ruleName(value.rule_id),
    gap: gap(value.irpf_minor, proposed.value.deductible_irpf_minor),
  });
  const vat =
    value.vat_minor == null
      ? t('expenseRules.previewVatManual')
      : t('expenseRules.previewVat', {
          gap: gap(value.vat_minor, proposed.value.deductible_vat_minor),
        });
  return `${irpf}; ${vat}`;
});
const fxTarget = computed(() =>
  ['exact', 'prior'].includes(draft.value?.fx_suggestion?.status || '')
    ? 'wf-use-fx'
    : 'wf-settlement-rate',
);
const factPaths: Record<string, string> = {
  transaction_date: 'facts.transaction_date',
  taxable_base_minor: 'decision.tax_treatment.taxable_base_minor',
  vat_minor: 'decision.tax_treatment.vat_minor',
  area_share: 'decision.tax_treatment.deductible_ratio',
};
const remaining = computed<RemainingItem[]>(() => {
  if (!draft.value || !payload.value) return [];
  const items = draft.value.source.issues.map((issue) => {
    const key = `issues.messages.${issue.issue_code}`;
    return {
      id: fieldId(
        MANUAL_ISSUES.includes(issue.issue_code) ? 'manual_review_reason' : 'decision.reason',
      ),
      text: formatMessage(messageIds.includes(key) ? key : 'issues.default', {}, locale.value),
    };
  });
  if (payload.value.decision.tax_treatment.rate_basis_points == null)
    items.push({
      id: fieldId('decision.tax_treatment.rate_basis_points'),
      text: t('workflow.remaining.ivaRate'),
    });
  const rate = payload.value.facts.currency !== 'EUR' && !payload.value.fx;
  if (rate) items.push({ id: fxTarget.value, text: t('workflow.remaining.rate') });
  for (const name of stale.value ? [] : (proposal.value?.missing ?? []))
    if (!(rate && name === 'eur_amount'))
      items.push({
        id:
          name === 'eur_amount'
            ? fxTarget.value
            : fieldId(factPaths[name] ?? `deduction.facts.${name}`),
        text: t('workflow.remaining.fact', { fact: ruleText(`expenseRules.fact.${name}`) }),
      });
  return items;
});
function useFx(settlement = false) {
  if (!payload.value || busy.value) return;
  const suggestion = draft.value?.fx_suggestion;
  if (!settlement && !['exact', 'prior'].includes(suggestion?.status || '')) return;
  payload.value.fx = settlement
    ? {
        rate_date: payload.value.facts.transaction_date,
        rate: settlementRate.value,
        rate_source: 'actual_settlement',
        source_reference: settlementReference.value,
        raw_observation: JSON.stringify({ operator_confirmed: true }),
        supersedes_rate_id: null,
      }
    : {
        rate_date: suggestion?.rate_date,
        rate: suggestion?.eur_per_unit,
        rate_source: 'ecb',
        source_reference: suggestion?.source_reference,
        raw_observation: suggestion?.raw_observation,
        supersedes_rate_id: null,
      };
  invalidate();
}
/** EUR as everywhere else («121,00 €»); a purchase in another currency keeps its code. */
const money = (value: number | null | undefined, currency = 'EUR') =>
  value == null
    ? '—'
    : currency === 'EUR'
      ? eur(Number(value) / 100, locale.value)
      : `${(Number(value) / 100).toFixed(2)} ${currency}`;
const supplierFields: FieldSpec[] = [
  { path: 'supplier.display_name', label: 'workflow.name' },
  { path: 'supplier.country_code', label: 'workflow.countryEsUs' },
  { path: 'supplier.tax_id', label: 'workflow.taxIdentifier' },
  { path: 'supplier.vat_id', label: 'fields.vatId' },
];
const purposeFields: FieldSpec[] = [
  { path: 'decision.business_purpose', label: 'workflow.businessPurpose' },
  { path: 'decision.reason', label: 'workflow.decisionReason' },
];
const changeReasonField: FieldSpec = {
  path: 'change_reason',
  label: 'workflow.reasonForFactCorrectionInvalidatingApproval',
};
const assetFields: FieldSpec[] = [
  { path: 'asset.description', label: 'workflow.description' },
  { path: 'asset.placed_in_service_on', label: 'workflow.inServiceDate', type: 'date' },
  {
    path: 'asset.basis_minor',
    label: 'workflow.amortizableBasisBeforeBusinessShareEur',
    kind: 'money',
    type: 'number',
  },
  {
    path: 'asset.business_use_ratio',
    label: 'workflow.confirmedBusinessUse',
    kind: 'ratio',
    type: 'number',
  },
];
const supplierHidden = (party: Counterparty) =>
  !!supplierSearch.value &&
  party.counterparty_id !== payload.value?.facts.counterparty_id &&
  !`${party.display_name} · ${party.tax_id || party.vat_id || '—'}`
    .toLocaleLowerCase()
    .includes(supplierSearch.value.toLocaleLowerCase());
</script>
<template>
  <section ref="root" class="section-stack expense-workflow">
    <a
      class="text-button back-link"
      :href="queueUrl"
      @click="followSpaLink($event, context.services.navigate)"
      ><span aria-hidden="true">←</span> {{ t('workflow.backToQueue') }}</a
    >
    <header class="panel-header">
      <h2>{{ t('workflow.reviewAndPostExpense') }}</h2>
      <span>{{ formatPeriod(draft?.source.period.period_key, locale) }}</span>
    </header>
    <div class="wf-message" role="status">{{ messageText(notice, locale) }}</div>
    <div v-if="error" class="review-alert error" role="alert" tabindex="-1">
      {{ errorMessage(error, locale)
      }}<button v-if="!draft" type="button" class="secondary-button" @click="retryLoad">
        {{
          t(
            error instanceof ApiError && error.code === 'session_forbidden'
              ? 'common.reload'
              : 'common.retry',
          )
        }}
      </button>
    </div>
    <p v-if="!draft && !error" role="status">{{ t('common.loading') }}</p>
    <div v-if="draft?.conflict" class="review-alert warning">
      {{ t('workflow.sourceChangedTheSavedDraftIsRetainedCompareIt')
      }}<button
        type="button"
        id="wf-reset"
        class="secondary-button"
        :disabled="busy"
        @click="reset"
      >
        {{ t('workflow.useCurrentFacts') }}
      </button>
    </div>
    <section v-if="posted" class="panel wf-fields">
      <h2>{{ t('workflow.postedTitle') }}</h2>
      <p>{{ t('workflow.thisIsNotAPaymentOrTaxFiling') }}</p>
      <template v-if="posted.follow_up_pending"
        ><p>{{ t('workflow.refreshOrInboxCleanupNeedsRetryDoNotRepost') }}</p>
        <button
          id="wf-follow-up"
          type="button"
          class="secondary-button"
          :disabled="busy"
          @click="followUp"
        >
          {{ t('workflow.retryFollowUp') }}
        </button></template
      ><button
        id="wf-open-posted"
        type="button"
        class="primary-button"
        :disabled="busy"
        @click="context.onPosted(posted)"
      >
        {{ t('workflow.openExpense') }}
      </button>
    </section>
    <template v-else-if="draft && payload">
      <nav class="wf-steps" :aria-label="t('workflow.steps.label')">
        <ol>
          <li v-for="(item, index) in steps" :key="item.id">
            <a
              :href="`#${item.id}`"
              :aria-current="step === item.id ? 'step' : undefined"
              @click.prevent="goToStep(item.id)"
              ><span class="wf-step-number">{{ index + 1 }}</span> {{ t(item.label) }}</a
            >
          </li>
        </ol>
      </nav>
      <RemainingList :items="remaining" />
      <div class="wf-layout" :class="{ 'wf-layout-compact': !previewable }">
        <aside class="panel wf-source">
          <h3>{{ t('workflow.sourceTitle') }}</h3>
          <a :href="sourceUrl" target="_blank" rel="noreferrer">{{ t('common.openDocument') }}</a
          ><iframe
            v-if="previewable"
            :title="t('workflow.sourceDocument')"
            :src="sourceUrl.replace(/content$/, 'preview')"
          ></iframe>
        </aside>
        <form id="wf-form" class="panel wf-fields" @submit.prevent="preview">
          <fieldset :disabled="busy">
            <section id="wf-document" class="wf-step">
              <h2 id="wf-document-title" tabindex="-1">
                {{ t('workflow.steps.documentTitle') }}
              </h2>
              <div class="wf-grid">
                <WorkflowField
                  v-for="field in mainFacts"
                  :key="field.path"
                  :field="field"
                  :currency="
                    field.path === 'facts.gross_minor'
                      ? String(payload.facts.currency || '')
                      : undefined
                  "
                  :model-value="fieldValue(payload, field.path)"
                  @update:model-value="update(field.path, $event)"
                />
              </div>
              <details
                class="wf-more"
                :open="moreOpen"
                @toggle="moreOpen = ($event.target as HTMLDetailsElement).open"
              >
                <summary>
                  {{
                    t('workflow.moreFacts', {
                      currency: String(payload.facts.currency || '—'),
                      date: formatDateText(payload.facts.booking_date, locale),
                    })
                  }}
                </summary>
                <div class="wf-grid">
                  <WorkflowField
                    v-for="field in moreFacts"
                    :key="field.path"
                    :field="field"
                    :model-value="fieldValue(payload, field.path)"
                    @update:model-value="update(field.path, $event)"
                  />
                </div>
              </details>
              <p v-if="matchedSupplier && !shown.supplier" class="wf-chosen">
                <span>{{ t('workflow.supplier') }}</span>
                <strong
                  >{{ matchedSupplier.display_name }} ·
                  {{ matchedSupplier.tax_id || matchedSupplier.vat_id || '—' }}</strong
                >
                <button
                  type="button"
                  class="text-button"
                  data-reveals="wf-supplier-search"
                  :aria-label="t('workflow.changeSupplier')"
                  @click="change('supplier', 'wf-supplier-search')"
                >
                  {{ t('workflow.change') }}
                </button>
              </p>
              <template v-else>
                <label
                  ><span>{{ t('workflow.findSupplierByNameTaxId') }}</span
                  ><input id="wf-supplier-search" v-model="supplierSearch" type="search"
                /></label>
                <label
                  ><span>{{ t('workflow.supplier') }}</span
                  ><select
                    :id="fieldId('facts.counterparty_id')"
                    data-p="facts.counterparty_id"
                    :value="payload.facts.counterparty_id ?? ''"
                    @input="select('facts.counterparty_id', $event)"
                  >
                    <option value=""></option>
                    <option
                      v-for="party in suppliers"
                      :key="party.counterparty_id"
                      :value="party.counterparty_id"
                      :hidden="supplierHidden(party)"
                    >
                      {{ party.display_name }} · {{ party.tax_id || party.vat_id || '—' }}
                    </option>
                  </select></label
                >
                <button
                  id="wf-new-supplier"
                  class="secondary-button"
                  type="button"
                  @click="newSupplier"
                >
                  {{ t('workflow.newSupplier') }}
                </button>
                <div v-if="payload.supplier" class="wf-grid">
                  <WorkflowField
                    v-for="field in supplierFields"
                    :key="field.path"
                    :field="field"
                    :model-value="fieldValue(payload, field.path)"
                    @update:model-value="update(field.path, $event)"
                  />
                </div>
              </template>
              <p v-if="chosenActivity && !shown.activity" class="wf-chosen">
                <span>{{ t('workflow.businessActivity') }}</span>
                <strong>{{ chosenActivity.description }}</strong>
                <button
                  type="button"
                  class="text-button"
                  data-reveals="wf-facts-business_activity_id"
                  :aria-label="t('workflow.changeActivity')"
                  @click="change('activity', fieldId('facts.business_activity_id'))"
                >
                  {{ t('workflow.change') }}
                </button>
              </p>
              <label v-else
                ><span>{{ t('workflow.businessActivity') }}</span
                ><select
                  :id="fieldId('facts.business_activity_id')"
                  data-p="facts.business_activity_id"
                  :value="payload.facts.business_activity_id ?? ''"
                  @input="select('facts.business_activity_id', $event)"
                >
                  <option value=""></option>
                  <option
                    v-for="activity in draft.activities"
                    :key="activity.business_activity_id"
                    :value="activity.business_activity_id"
                  >
                    {{ activity.description }}
                  </option>
                </select></label
              >
              <label class="wf-check"
                ><input
                  :id="fieldId('decision.document_valid')"
                  type="checkbox"
                  data-p="decision.document_valid"
                  :checked="payload.decision.document_valid === true"
                  @input="check('decision.document_valid', $event)"
                /><span>{{
                  t('workflow.originalIsReadableFactsAndAmountsHaveBeenChecked')
                }}</span></label
              >
              <WorkflowField
                v-if="manual"
                :field="{
                  path: 'manual_review_reason',
                  label: 'workflow.manualReviewExplanationAfterExtractionIssue',
                }"
                :model-value="payload.manual_review_reason"
                @update:model-value="update('manual_review_reason', $event)"
              />
              <WorkflowField
                v-if="changeReasonShown"
                :field="changeReasonField"
                :model-value="payload.change_reason"
                @update:model-value="update('change_reason', $event)"
              />
            </section>
            <section id="wf-taxes" class="wf-step">
              <h2 id="wf-taxes-title" tabindex="-1">{{ t('workflow.steps.taxesTitle') }}</h2>
              <p v-if="!shown.kind" class="wf-chosen">
                <span>{{ t('workflow.expenseType') }}</span>
                <strong>{{
                  t(payload.asset ? 'workflow.equipment' : 'workflow.ordinaryExpense')
                }}</strong>
                <button
                  type="button"
                  class="text-button"
                  data-reveals="wf-kind"
                  :aria-label="t('workflow.changeExpenseType')"
                  @click="change('kind', 'wf-kind')"
                >
                  {{ t('workflow.change') }}
                </button>
              </p>
              <label v-else
                ><span>{{ t('workflow.expenseType') }}</span
                ><select
                  id="wf-kind"
                  :value="payload.asset ? 'asset' : 'ordinary'"
                  @change="setKind"
                >
                  <option value="ordinary">{{ t('workflow.ordinaryExpense') }}</option>
                  <option value="asset">{{ t('workflow.equipment') }}</option>
                </select></label
              >
              <label
                ><span>{{ t('workflow.taxClassification') }}</span
                ><select
                  :id="fieldId('decision.tax_treatment.tax_code')"
                  data-p="decision.tax_treatment.tax_code"
                  :value="tax.tax_code ?? ''"
                  @input="select('decision.tax_treatment.tax_code', $event)"
                >
                  <option value=""></option>
                  <option v-for="code in allowedTaxes" :key="code" :value="code">
                    {{ taxCodeLabel(code, locale) }}
                  </option>
                </select></label
              >
              <div class="wf-grid wf-grid-facts">
                <WorkflowField
                  v-for="field in taxFacts"
                  :key="field.path"
                  :field="field"
                  :model-value="fieldValue(payload, field.path)"
                  @update:model-value="update(field.path, $event)"
                />
              </div>
              <div class="wf-grid">
                <WorkflowField
                  v-for="field in taxDecisions"
                  :key="field.path"
                  :field="field"
                  :model-value="fieldValue(payload, field.path)"
                  @update:model-value="update(field.path, $event)"
                />
              </div>
              <details
                v-if="rules.length"
                id="wf-rule"
                class="wf-rule"
                :open="ruleOpen"
                @toggle="ruleOpen = ($event.target as HTMLDetailsElement).open"
              >
                <summary>
                  {{
                    t('expenseRules.summary', {
                      rule: rule ? ruleName(rule.id) : t('expenseRules.noRule'),
                    })
                  }}
                </summary>
                <div class="wf-rule-body">
                  <p>{{ t('expenseRules.intro') }}</p>
                  <label
                    ><span>{{ t('expenseRules.select') }}</span
                    ><select
                      id="wf-rule-select"
                      :value="payload.deduction?.rule_id ?? ''"
                      @change="onRule"
                    >
                      <option value="">{{ t('expenseRules.noRule') }}</option>
                      <option v-for="item in rules" :key="item.id" :value="item.id">
                        {{
                          item.id === parserRule
                            ? t('expenseRules.fromParser', { rule: ruleName(item.id) })
                            : ruleName(item.id)
                        }}
                      </option>
                    </select></label
                  >
                  <div v-if="rule && payload.deduction" class="wf-grid">
                    <template v-for="name in rule.facts" :key="name">
                      <WorkflowField
                        v-if="name === 'area_share'"
                        :field="{
                          path: 'decision.tax_treatment.deductible_ratio',
                          label: 'expenseRules.fact.area_share',
                          kind: 'ratio',
                          type: 'number',
                        }"
                        :model-value="tax.deductible_ratio"
                        @update:model-value="
                          update('decision.tax_treatment.deductible_ratio', $event)
                        "
                      />
                      <label v-else-if="['persons', 'persons_disabled', 'days'].includes(name)"
                        ><span>{{ ruleText(`expenseRules.fact.${name}`) }}</span
                        ><input
                          :id="fieldId(`deduction.facts.${name}`)"
                          type="number"
                          min="0"
                          max="366"
                          step="1"
                          :data-p="`deduction.facts.${name}`"
                          :value="payload.deduction.facts[name] ?? ''"
                          @input="setCount(name, $event)"
                      /></label>
                      <label v-else-if="name === 'evidence_confirmed'" class="wf-check"
                        ><input
                          :id="fieldId('deduction.facts.evidence_confirmed')"
                          type="checkbox"
                          data-p="deduction.facts.evidence_confirmed"
                          :checked="payload.deduction.facts.evidence_confirmed === true"
                          @input="check('deduction.facts.evidence_confirmed', $event)"
                        /><span>{{ t('expenseRules.fact.evidence_confirmed') }}</span></label
                      >
                      <label v-else
                        ><span>{{ ruleText(`expenseRules.fact.${name}`) }}</span
                        ><select
                          :id="fieldId(`deduction.facts.${name}`)"
                          :data-p="`deduction.facts.${name}`"
                          :value="
                            payload.deduction.facts[name] == null
                              ? ''
                              : String(payload.deduction.facts[name])
                          "
                          @input="select(`deduction.facts.${name}`, $event, true)"
                        >
                          <option value=""></option>
                          <option value="true">{{ t('expenseRules.yes') }}</option>
                          <option value="false">{{ t('expenseRules.no') }}</option>
                        </select></label
                      >
                    </template>
                  </div>
                  <small v-if="rule?.facts.includes('area_share')">{{
                    t('expenseRules.areaHint')
                  }}</small>
                  <small v-if="rule?.facts.includes('evidence_confirmed')">{{
                    t('expenseRules.evidencePrompt')
                  }}</small>
                  <div v-if="rule" id="wf-rule-card" class="wf-rule-card">
                    <div class="wf-rule-live" aria-live="polite">
                      <template v-if="proposal">
                        <p>
                          <strong>{{ ruleText(`expenseRules.status.${proposal.status}`) }}</strong>
                          <span
                            v-if="proposal.risk"
                            class="badge wf-risk"
                            :class="riskBadge[proposal.risk]"
                            >{{ ruleText(`expenseRules.risk.${proposal.risk}`) }}</span
                          >
                        </p>
                        <dl v-if="proposal.irpf_minor != null" class="review-facts-list">
                          <div>
                            <dt>{{ t('expenseRules.irpfCeiling') }}</dt>
                            <dd>{{ eur(proposal.irpf_minor / 100, locale) }}</dd>
                          </div>
                          <div>
                            <dt>{{ t('expenseRules.vatCeiling') }}</dt>
                            <dd>
                              {{
                                proposal.vat_minor == null
                                  ? t('expenseRules.vatManual')
                                  : eur(proposal.vat_minor / 100, locale)
                              }}
                            </dd>
                          </div>
                        </dl>
                      </template>
                      <p v-else>{{ t('expenseRules.saveToCalculate') }}</p>
                      <p v-if="proposal && stale" class="wf-rule-stale">
                        {{ t('expenseRules.saveToRecalculate') }}
                      </p>
                    </div>
                    <template v-if="proposal">
                      <template v-if="proposal.missing.length"
                        ><p>{{ t('expenseRules.missingTitle') }}</p>
                        <ul class="wf-rule-missing">
                          <li v-for="name in proposal.missing" :key="name">
                            {{ ruleText(`expenseRules.fact.${name}`) }}
                          </li>
                        </ul></template
                      >
                      <ul class="wf-rule-explanation">
                        <li v-for="(note, index) in proposal.explanation" :key="index">
                          {{
                            ruleText(
                              `expenseRules.code.${note.code}`,
                              explanationValues(note.params, locale),
                            )
                          }}
                        </li>
                      </ul>
                      <p class="wf-rule-risk">
                        {{ ruleText(`expenseRules.riskReason.${proposal.rule_id}`) }}
                      </p>
                      <details v-if="proposal.sources?.length">
                        <summary>{{ t('expenseRules.sources') }}</summary>
                        <ul>
                          <li v-for="source in proposal.sources" :key="source.url + source.title">
                            <a :href="source.url" target="_blank" rel="noreferrer">{{
                              source.title
                            }}</a>
                            ·
                            {{
                              t('expenseRules.sourceChecked', {
                                date: formatDateText(source.checked_on, locale),
                              })
                            }}
                            <template v-if="source.source_kind === 'secondary'">
                              · {{ t('expenseRules.secondary') }}</template
                            >
                          </li>
                        </ul>
                      </details>
                      <p v-if="copyPair" class="wf-rule-pair">{{ copyPair }}</p>
                      <button
                        id="wf-rule-copy"
                        type="button"
                        class="secondary-button"
                        :disabled="stale || !proposal.suggested"
                        @click="copyIntoFields"
                      >
                        {{ t('expenseRules.copy') }}
                      </button>
                    </template>
                  </div>
                </div>
              </details>
              <h3 id="wf-vat-title">{{ t('review.vatInvestment') }}</h3>
              <select
                :id="fieldId('decision.tax_treatment.vat_investment_good')"
                data-p="decision.tax_treatment.vat_investment_good"
                aria-labelledby="wf-vat-title"
                :value="tax.vat_investment_good == null ? '' : String(tax.vat_investment_good)"
                @input="select('decision.tax_treatment.vat_investment_good', $event, true)"
              >
                <option value=""></option>
                <option value="false">{{ t('workflow.currentPurchaseForIva') }}</option>
                <option value="true">{{ t('workflow.ivaInvestmentGood') }}</option></select
              ><small>{{
                t('workflow.irpfDepreciationDoesNotDetermineTheIvaInvestmentClassification')
              }}</small>
              <WorkflowField
                v-for="field in purposeFields"
                :key="field.path"
                :field="field"
                :model-value="fieldValue(payload, field.path)"
                @update:model-value="update(field.path, $event)"
              />
              <template v-if="payload.asset"
                ><h3>{{ t('workflow.equipmentAndSchedule') }}</h3>
                <div class="wf-grid">
                  <WorkflowField
                    v-for="field in assetFields"
                    :key="field.path"
                    :field="field"
                    :model-value="fieldValue(payload, field.path)"
                    @update:model-value="update(field.path, $event)"
                  />
                  <label
                    ><span>{{ t('workflow.assetCategory') }}</span
                    ><select
                      :id="fieldId('asset.aeat_asset_type')"
                      data-p="asset.aeat_asset_type"
                      :value="payload.asset.aeat_asset_type ?? ''"
                      @input="select('asset.aeat_asset_type', $event)"
                    >
                      <option value=""></option>
                      <option
                        v-for="item in [
                          ['23', 'workflow.computerElectronic'],
                          ['21', 'workflow.machinery'],
                          ['24', 'workflow.furniture'],
                          ['25', 'workflow.installations'],
                          ['28', 'workflow.tools'],
                          ['29', 'workflow.otherEquipment'],
                        ]"
                        :key="item[0]"
                        :value="item[0]"
                      >
                        {{ formatMessage(item[1], {}, locale) }}
                      </option>
                    </select></label
                  >
                  <label
                    ><span>{{ t('workflow.depreciation') }}</span
                    ><select
                      :id="fieldId('asset.method')"
                      data-p="asset.method"
                      :value="payload.asset.method ?? ''"
                      @input="select('asset.method', $event)"
                    >
                      <option value=""></option>
                      <option value="immediate">
                        {{ t('workflow.immediateNewLowValueEquipment') }}
                      </option>
                      <option value="linear">{{ t('workflow.linearSchedule') }}</option>
                    </select></label
                  >
                  <WorkflowField
                    v-if="payload.asset.method === 'linear'"
                    :field="{
                      path: 'asset.annual_rate_basis_points',
                      label: 'workflow.reviewedAnnualRate',
                      kind: 'rate',
                      type: 'number',
                    }"
                    :model-value="payload.asset.annual_rate_basis_points"
                    @update:model-value="update('asset.annual_rate_basis_points', $event)"
                  />
                </div>
                <label class="wf-check"
                  ><input
                    :id="fieldId('asset.new_equipment')"
                    type="checkbox"
                    data-p="asset.new_equipment"
                    :checked="payload.asset.new_equipment === true"
                    @input="check('asset.new_equipment', $event)"
                  /><span>{{ t('workflow.equipmentWasAcquiredNew') }}</span></label
                ><small>{{
                  t('workflow.futureDepreciationRemainsPlannedUntilExplicitlyPosted')
                }}</small></template
              >
              <section v-if="payload.facts.currency !== 'EUR'">
                <h3>{{ t('workflow.eurConversion') }}</h3>
                <p>
                  {{
                    draft.fx_suggestion?.note
                      ? fxNoteText(draft.fx_suggestion, locale)
                      : draft.fx_suggestion?.eur_per_unit ||
                        t('workflow.saveTheDraftToRefreshTheRateSuggestion')
                  }}
                </p>
                <button
                  id="wf-use-fx"
                  type="button"
                  class="secondary-button"
                  :disabled="!['exact', 'prior'].includes(draft.fx_suggestion?.status || '')"
                  @click="useFx()"
                >
                  {{ t('workflow.confirmSuggestedEcbRate') }}</button
                ><label
                  ><span>{{ t('workflow.documentedSettlementRate') }}</span
                  ><input
                    id="wf-settlement-rate"
                    v-model="settlementRate"
                    type="number"
                    step="0.000001" /></label
                ><label
                  ><span>{{ t('workflow.settlementEvidenceReference') }}</span
                  ><input id="wf-settlement-ref" v-model="settlementReference" /></label
                ><button
                  id="wf-settlement"
                  type="button"
                  class="secondary-button"
                  @click="useFx(true)"
                >
                  {{ t('workflow.useSettlementRate') }}</button
                ><small>{{
                  payload.fx ? t('workflow.rateSelectedCheckTaxAmountsInEur') : ''
                }}</small>
              </section>
              <details
                :open="technicalOpen"
                @toggle="technicalOpen = ($event.target as HTMLDetailsElement).open"
              >
                <summary>{{ t('workflow.additionalTaxDecisionFields') }}</summary>
                <div class="wf-grid">
                  <label
                    ><span>{{ t('workflow.aeatDocumentType') }}</span
                    ><select
                      :id="fieldId('decision.tax_treatment.aeat_invoice_type')"
                      data-p="decision.tax_treatment.aeat_invoice_type"
                      :value="tax.aeat_invoice_type ?? ''"
                      @input="select('decision.tax_treatment.aeat_invoice_type', $event)"
                    >
                      <option value=""></option>
                      <option
                        v-for="value in draft.allowed_values.aeat_invoice_type || []"
                        :key="value"
                        :value="value"
                      >
                        {{ aeatCodeLabel('invoiceType', value, locale) }}
                      </option>
                    </select></label
                  ><WorkflowField
                    v-for="field in visibleTechnical"
                    :key="field.path"
                    :field="field"
                    :model-value="fieldValue(payload, field.path)"
                    @update:model-value="update(field.path, $event)"
                  />
                </div>
                <label class="wf-check"
                  ><input
                    :id="fieldId('decision.tax_treatment.aeat_reverse_charge')"
                    type="checkbox"
                    data-p="decision.tax_treatment.aeat_reverse_charge"
                    :checked="tax.aeat_reverse_charge === true"
                    @input="check('decision.tax_treatment.aeat_reverse_charge', $event)"
                  /><span>{{ t('workflow.reverseCharge') }}</span></label
                ><label v-for="form in [130, 303, 347]" :key="form" class="wf-check"
                  ><input
                    :id="fieldId(`decision.tax_treatment.include_modelo${form}`)"
                    type="checkbox"
                    :data-p="`decision.tax_treatment.include_modelo${form}`"
                    :checked="tax[`include_modelo${form}`] === true"
                    @input="check(`decision.tax_treatment.include_modelo${form}`, $event)"
                  /><span>Modelo {{ form }}</span></label
                ><WorkflowField
                  :field="{
                    path: 'decision.tax_treatment.notes',
                    label: 'workflow.decisionNoteAndSource',
                  }"
                  :model-value="tax.notes"
                  @update:model-value="update('decision.tax_treatment.notes', $event)"
                />
              </details>
            </section>
            <footer class="wf-actions">
              <button type="button" id="wf-save" class="secondary-button" @click="saveOnly">
                {{ t('workflow.saveDraft') }}</button
              ><button
                type="submit"
                :class="proposed ? 'secondary-button' : 'primary-button'"
                :disabled="draft.conflict"
              >
                {{ t(proposed ? 'workflow.recalculate' : 'workflow.previewResult') }}
              </button>
            </footer>
          </fieldset>
        </form>
      </div>
      <section id="wf-preview" class="panel wf-fields wf-step">
        <h2 id="wf-preview-title" tabindex="-1">{{ t('workflow.steps.previewTitle') }}</h2>
        <template v-if="proposed">
          <p>{{ proposed.supplier }} · {{ formatPeriod(proposed.period, locale) }}</p>
          <dl class="review-facts-list">
            <div>
              <dt>{{ t('workflow.purchase') }}</dt>
              <dd>{{ money(proposed.gross_minor, proposed.currency) }}</dd>
            </div>
            <div>
              <dt>IVA</dt>
              <dd>{{ money(proposed.deductible_vat_minor) }}</dd>
            </div>
            <div>
              <dt>{{ t('workflow.irpfDeductionNow') }}</dt>
              <dd>{{ money(proposed.deductible_irpf_minor) }}</dd>
            </div>
            <div>
              <dt>{{ t('workflow.laterDepreciation') }}</dt>
              <dd>{{ money(proposed.future_depreciation_minor) }}</dd>
            </div>
            <div v-if="previewRule" id="wf-preview-rule">
              <dt>{{ t('expenseRules.title') }}</dt>
              <dd>{{ previewRule }}</dd>
            </div>
          </dl>
          <div v-if="proposed.schedule.length" class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{{ t('workflow.period') }}</th>
                  <th>{{ t('workflow.recognitionDate') }}</th>
                  <th>EUR</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, index) in proposed.schedule" :key="index">
                  <td>{{ formatPeriod(row.period_key, locale) }}</td>
                  <td>{{ formatDateText(row.recognition_on, locale) }}</td>
                  <td>{{ money(row.amount_minor) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p>{{ t('workflow.onlyThisExpenseAndTheShownDepreciationNoPayment') }}</p>
          <button
            id="wf-confirm"
            class="primary-button"
            type="button"
            :disabled="busy"
            @click="confirm"
          >
            {{ t(confirming ? 'review.submitting' : 'workflow.confirmAndPost') }}
          </button>
        </template>
        <p v-else>{{ t('workflow.steps.previewPending') }}</p>
      </section>
    </template>
  </section>
</template>
