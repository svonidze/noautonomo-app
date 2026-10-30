<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue';
import { followSpaLink } from '../../vue/services.ts';
import { useLocale } from '../../vue/locale.ts';
import { formatMessage } from '../../core/i18n.ts';
import { errorMessage } from '../../core/error-message.ts';
import { ApiError } from '../../core/http.ts';
import type {
  GovernmentContext,
  CaseList,
  CaseDetail,
  CaseAction,
  CaseEvent,
  CorrespondenceMessage,
} from './model.ts';
const props = defineProps<{ context: GovernmentContext }>();
const { t, locale } = useLocale();
const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
const localNow = () => {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
};
const instant = (value: string) => (value ? new Date(value).toISOString() : '');
const list = shallowRef<CaseList>();
const detail = shallowRef<CaseDetail>();
const error = ref(''),
  saving = ref(false),
  loading = ref(true),
  conflict = ref(false);
const savedFilters = window.history.state?.governmentFilters;
const filters = reactive({
  authority: savedFilters?.authority || '',
  state: savedFilters?.state || '',
  query: savedFilters?.query || '',
});
watch(filters, () => {
  window.history.replaceState({ ...window.history.state, governmentFilters: { ...filters } }, '');
});
const params = new URLSearchParams(window.location.search);
const caseParams = params.getAll('case');
const caseId = caseParams.length === 1 ? caseParams[0].trim().toLowerCase() : '';
const invalidCase =
  caseParams.length > 1 ||
  (!!caseId && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(caseId));
const showingCase = Boolean(caseId) || invalidCase;
const detailError = ref('');
const listError = ref('');
const heading = ref<HTMLElement>();
const stateForm = ref<HTMLFormElement>();
const savedTitle = ref('');
const caseFields = [
  'title',
  'subject',
  'primary_reference',
  'work_state',
  'parent_case_id',
] as const;
type CaseFields = Pick<CaseDetail, (typeof caseFields)[number]>;
const caseBaseline = shallowRef<CaseFields>();
function caseValues(value: CaseDetail): CaseFields {
  return {
    title: value.title,
    subject: value.subject,
    primary_reference: value.primary_reference,
    work_state: value.work_state,
    parent_case_id: value.parent_case_id ?? null,
  };
}
function mayReplace(form?: HTMLFormElement | null) {
  return !form || !dirtyForms.has(form) || window.confirm(t('government.leaveUnsaved'));
}
const dirtyForms = new Set<HTMLFormElement>();
const formVersions = new WeakMap<HTMLFormElement, number>();
const dirty = ref(false);
let submittedForm: HTMLFormElement | null = null;
function markDirty(event: Event) {
  const form = (event.target as HTMLElement).closest('form');
  if (form) {
    dirtyForms.add(form);
    formVersions.set(form, (formVersions.get(form) || 0) + 1);
    dirty.value = true;
  }
}
function submitted(event: Event) {
  submittedForm = (event.target as HTMLElement).closest('form');
}
function isDirty() {
  return dirty.value || !!pendingMessageId.value || extraFiles.value.length > 0;
}
function canLeave() {
  if (saving.value) return false;
  return !isDirty() || window.confirm(t('government.leaveUnsaved'));
}
defineExpose({ isDirty, isBusy: () => saving.value, canLeave });
const caseUrl = (id: string) => `/government-cases?case=${encodeURIComponent(id)}`;
function follow(event: MouseEvent) {
  followSpaLink(event, props.context.services.navigate);
}
void nextTick(() => heading.value?.focus());
const create = reactive({
  authority_code: 'TGSS',
  parent_case_id: '',
  title: '',
  subject: '',
  primary_reference: '',
  opened_at: localNow(),
  evidence_reference: '',
});
const event = reactive({
  kind: 'note',
  occurred_at: localNow(),
  evidence_reference: '',
  notes: '',
  outcome: '',
  supersedes_event_id: '',
});
const action = reactive({
  kind: 'respond',
  description: '',
  due_on: '',
  deadline_source: '',
  confirmed: false,
  status: 'open',
  correspondence_event_id: '',
});
const editId = ref(''),
  actionVersion = ref(0),
  stateSource = ref(''),
  resolutionSource = ref('');
const file = shallowRef<File>();
const pdf = reactive({
  document_kind: 'resolution',
  occurred_at: localNow(),
  placed_at: '',
  received_at: '',
  access_expires_at: '',
  notes: '',
});
let active = true,
  generation = 0,
  selectionGeneration = 0,
  listGeneration = 0;
onBeforeUnmount(() => {
  active = false;
  generation++;
});
const rows = computed(() =>
  (list.value?.rows || []).filter(
    (r) =>
      (!filters.authority || r.authority_code === filters.authority) &&
      (!filters.state || r.work_state === filters.state) &&
      (r.search_text || `${r.title} ${r.subject} ${r.primary_reference}`)
        .toLowerCase()
        .includes(filters.query.toLowerCase()),
  ),
);
const actionKinds = ['receive', 'respond', 'pay', 'decide_appeal', 'check_response'];
const workStates = ['action_required', 'waiting_authority', 'closed'];
const outcomes = [
  'submitted',
  'information_requested',
  'responded',
  'approved',
  'rejected',
  'closed',
];
const eventKinds = [
  'submission',
  'notification',
  'response',
  'decision',
  'payment',
  'correction',
  'note',
  'document_link',
];
const documentKinds = [
  'submission_receipt',
  'authority_request',
  'response_receipt',
  'resolution',
  'certificate',
  'other',
];
const label = (group: string, value: string) =>
  formatMessage(`government.${group}.${value}`, {}, locale.value);
function dateText(value?: string | null) {
  return value
    ? new Intl.DateTimeFormat(locale.value, {
        dateStyle: 'medium',
        timeZone: browserZone,
        ...(value.length > 10 ? { timeStyle: 'short' as const } : {}),
      }).format(new Date(value.length === 10 ? value + 'T12:00:00Z' : value))
    : t('government.notRecorded');
}
function safeLink(value: string) {
  try {
    const u = new URL(value);
    return u.protocol === 'https:' && ['drive.google.com', 'docs.google.com'].includes(u.hostname)
      ? value
      : undefined;
  } catch {
    return undefined;
  }
}
function eventSummary(row: CaseEvent) {
  if (row.status) return label('outcome', row.status);
  const payload = JSON.parse(row.payload_json || '{}');
  if (payload.outcome) return label('outcome', payload.outcome);
  if (payload.after)
    return `${payload.before ? dateText(payload.before.due_on) + ' → ' : ''}${dateText(payload.after.due_on)} · ${label('actionStatus', payload.after.status)}`;
  if (row.kind === 'workflow') {
    const fields: Record<string, string> = {
      title: t('government.title'),
      subject: t('government.subject'),
      primary_reference: t('government.reference'),
    };
    const changes = Object.entries(payload.metadata_after || {}).map(
      ([key, value]) =>
        `${fields[key] || key}: ${payload.metadata_before?.[key] || t('government.notRecorded')} → ${value || t('government.notRecorded')}`,
    );
    return [`${label('state', payload.from)} → ${label('state', payload.to)}`, ...changes].join(
      ' · ',
    );
  }
  return '';
}
async function loadList() {
  const revision = ++listGeneration;
  listError.value = '';
  try {
    const result = (await props.context.services.request('/api/government-cases')) as CaseList;
    if (active && revision === listGeneration) list.value = result;
  } catch (failure) {
    if (active && revision === listGeneration)
      listError.value = errorMessage(failure, locale.value);
  }
}
async function load() {
  const revision = ++generation;
  loading.value = true;
  error.value = '';
  detailError.value = '';
  const listRequest = invalidCase ? Promise.resolve() : loadList();
  if (invalidCase) detailError.value = t('government.invalidCase');
  else if (caseId) await select(caseId);
  if (!showingCase) await listRequest;
  if (active && revision === generation) {
    loading.value = false;
    props.context.settled();
  }
}
async function select(id: string) {
  pendingMessageId.value = '';
  messageFiles.value = [];
  extraFiles.value = [];
  attachmentTarget.value = '';
  Object.assign(correspondence, blankMessage());
  const revision = ++selectionGeneration;
  detail.value = undefined;
  try {
    const result = (await props.context.services.request(
      `/api/government-cases/${encodeURIComponent(id)}`,
    )) as CaseDetail;
    if (active && revision === selectionGeneration) {
      detail.value = result;
      savedTitle.value = result.title;
      caseBaseline.value = caseValues(result);
      resetAction();
    }
  } catch (failure) {
    if (active && revision === selectionGeneration)
      detailError.value = errorMessage(failure, locale.value);
  }
}

async function write(path: string, payload: unknown, multipart = false) {
  if (saving.value || conflict.value || props.context.services.readOnly) return;
  const focusBefore =
    document.activeElement instanceof HTMLElement && document.activeElement.closest('form')
      ? document.activeElement
      : null;
  saving.value = true;
  const savedForm = submittedForm;
  const savedVersion = savedForm ? formVersions.get(savedForm) : undefined;
  error.value = '';
  conflict.value = false;
  try {
    const result = (await props.context.services.request(path, {
      method: 'POST',
      body: multipart ? (payload as FormData) : JSON.stringify(payload),
      ...(!multipart ? { headers: { 'Content-Type': 'application/json' } } : {}),
    })) as CaseDetail;
    if (!active) return;
    const changedDuringSave = !!savedForm && formVersions.get(savedForm) !== savedVersion;
    if (result.case_id && caseId === result.case_id) {
      savedTitle.value = result.title;
      const latest = caseValues(result);
      if (
        stateForm.value &&
        dirtyForms.has(stateForm.value) &&
        (savedForm !== stateForm.value || changedDuringSave) &&
        detail.value &&
        caseBaseline.value
      ) {
        const draft = caseValues(detail.value);
        const changed = caseFields.filter((key) => draft[key] !== caseBaseline.value![key]);
        const overlapping = changed.some((key) => latest[key] !== caseBaseline.value![key]);
        const patch = Object.fromEntries(changed.map((key) => [key, draft[key]]));
        detail.value = { ...result, ...patch };
        if (overlapping) {
          conflict.value = true;
          error.value = t('government.concurrentEdit');
        }
      } else detail.value = result;
      caseBaseline.value = latest;
    }
    if (savedForm && !changedDuringSave) dirtyForms.delete(savedForm);
    dirty.value = dirtyForms.size > 0;
    void loadList();
    props.context.notify(t('government.saved'));
    return result;
  } catch (failure) {
    if (active) {
      error.value = errorMessage(failure, locale.value);
      conflict.value = failure instanceof ApiError && failure.code === 'stale_case_version';
    }
  } finally {
    submittedForm = null;
    if (active) {
      saving.value = false;
      await nextTick();
      if (focusBefore?.isConnected && document.activeElement === document.body) focusBefore.focus();
    }
  }
}
function reloadAfterConflict() {
  if (isDirty() && !window.confirm(t('government.leaveUnsaved'))) return;
  dirtyForms.clear();
  dirty.value = false;
  pendingMessageId.value = '';
  extraFiles.value = [];
  window.location.reload();
}
async function createCase() {
  const created = await write('/api/government-cases', {
    ...create,
    opened_at: instant(create.opened_at),
  });
  if (created) {
    props.context.services.navigate(caseUrl(created.case_id));
    create.title = '';
    create.primary_reference = '';
    create.evidence_reference = '';
  }
}
async function addEvent() {
  if (!detail.value) return;
  const payload = {
    expected_row_version: detail.value.row_version,
    ...event,
    occurred_at: instant(event.occurred_at),
    outcome: ['decision', 'correction'].includes(event.kind)
      ? event.outcome || undefined
      : undefined,
    supersedes_event_id:
      event.kind === 'correction' ? event.supersedes_event_id || undefined : undefined,
  };
  if (await write(`/api/government-cases/${detail.value.case_id}/events`, payload)) {
    event.notes = '';
    event.evidence_reference = '';
  }
}
function resetAction() {
  if (actionForm.value) dirtyForms.delete(actionForm.value);
  dirty.value = dirtyForms.size > 0;
  editId.value = '';
  actionVersion.value = 0;
  Object.assign(action, {
    kind: 'respond',
    description: '',
    due_on: '',
    deadline_source: '',
    confirmed: false,
    status: 'open',
    correspondence_event_id: '',
  });
}
function editAction(row: CaseAction) {
  if (!mayReplace(actionForm.value)) return;
  editId.value = row.action_id;
  actionVersion.value = row.row_version;
  Object.assign(action, row, { due_on: row.due_on || '', confirmed: Boolean(row.confirmed) });
}
async function saveAction() {
  if (!detail.value) return;
  const payload = {
    correspondence_event_id: action.correspondence_event_id || null,
    kind: action.kind,
    description: action.description,
    due_on: action.due_on || null,
    deadline_source: action.deadline_source,
    confirmed: action.confirmed,
    status: action.status,
    expected_row_version: detail.value.row_version,
    ...(editId.value ? { action_version: actionVersion.value } : {}),
  };
  if (
    await write(
      `/api/government-cases/${detail.value.case_id}/actions${editId.value ? '/' + editId.value : ''}`,
      payload,
    )
  )
    resetAction();
}
async function saveState() {
  if (detail.value)
    await write(`/api/government-cases/${detail.value.case_id}/state`, {
      expected_row_version: detail.value.row_version,
      title: detail.value.title,
      subject: detail.value.subject,
      primary_reference: detail.value.primary_reference || '',
      work_state: detail.value.work_state,
      parent_case_id: detail.value.parent_case_id || null,
      evidence_reference: stateSource.value,
    });
}
async function uploadPdf() {
  if (!detail.value || !file.value) return;
  const body = new FormData();
  body.append('file', file.value);
  body.append(
    'metadata',
    JSON.stringify({
      ...pdf,
      occurred_at: instant(pdf.occurred_at),
      placed_at: instant(pdf.placed_at),
      received_at: instant(pdf.received_at),
      access_expires_at: instant(pdf.access_expires_at),
      expected_row_version: detail.value.row_version,
    }),
  );
  if (await write(`/api/government-cases/${detail.value.case_id}/documents`, body, true))
    file.value = undefined;
}
async function resolve(messageId: string, outcome: string) {
  if (
    await write('/api/government-reminders/resolve', {
      message_id: messageId,
      outcome,
      evidence_reference: resolutionSource.value,
    })
  ) {
    resolutionSource.value = '';
    for (const form of dirtyForms)
      if (form.hasAttribute('data-reminder-form')) dirtyForms.delete(form);
    dirty.value = dirtyForms.size > 0;
  }
}
const blankMessage = () => ({
  date_precision: 'instant',
  direction: 'incoming',
  channel: 'email',
  occurred_at: localNow(),
  sender: '',
  recipients: '',
  subject: '',
  body_text: '',
  summary: '',
  source_url: '',
  source_provider: 'manual',
  source_account: 'operator',
  source_message_id: crypto.randomUUID(),
  reply_to_event_id: '',
  supersedes_event_id: '',
});
const correspondence = reactive(blankMessage());
const messageFiles = shallowRef<File[]>([]);
const pendingMessageId = ref('');
const extraFiles = shallowRef<File[]>([]);
const attachmentTarget = ref('');
const messageForm = ref<HTMLDetailsElement>();
const actionForm = ref<HTMLFormElement>();
const messageById = computed(
  () => new Map((detail.value?.correspondence || []).map((m) => [m.event_id, m])),
);
function messageLink(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? value : undefined;
  } catch {
    return undefined;
  }
}
function selectFiles(input: Event, target: 'message' | 'extra') {
  const files = Array.from((input.target as HTMLInputElement).files || []);
  if (files.some((f) => f.size > 30 * 1024 * 1024)) {
    error.value = t('government.fileTooLarge');
    (input.target as HTMLInputElement).value = '';
    return;
  }
  if (target === 'message') messageFiles.value = files;
  else extraFiles.value = files;
}
async function attachMessageFile(id: string, value: File) {
  if (!detail.value) return;
  const body = new FormData();
  body.append('file', value);
  body.append(
    'metadata',
    JSON.stringify({
      correspondence_event_id: id,
      expected_row_version: detail.value.row_version,
    }),
  );
  return write(`/api/government-cases/${detail.value.case_id}/documents`, body, true);
}
async function addCorrespondence() {
  if (!detail.value) return;
  if (!pendingMessageId.value) {
    const previous = messageById.value.get(correspondence.supersedes_event_id);
    const payload = {
      ...correspondence,
      occurred_at:
        correspondence.date_precision === 'day'
          ? correspondence.occurred_at.slice(0, 10) + 'T12:00:00Z'
          : instant(correspondence.occurred_at),
      recipients: correspondence.recipients
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
      attachment_names: [
        ...new Set([
          ...(previous?.attachment_names || []),
          ...messageFiles.value.map((f) => f.name),
        ]),
      ],
      expected_row_version: detail.value.row_version,
    };
    if (new TextEncoder().encode(JSON.stringify(payload)).length > 1024 * 1024) {
      error.value = t('government.messageTooLarge');
      return;
    }
    const saved = await write(
      `/api/government-cases/${detail.value.case_id}/correspondence`,
      payload,
    );
    if (!saved?.correspondence_event_id) return;
    pendingMessageId.value = saved.correspondence_event_id;
  }
  while (messageFiles.value.length) {
    if (!(await attachMessageFile(pendingMessageId.value, messageFiles.value[0]))) return;
    messageFiles.value = messageFiles.value.slice(1);
  }
  pendingMessageId.value = '';
  messageFiles.value = [];
  Object.assign(correspondence, blankMessage());
  if (messageForm.value) messageForm.value.open = false;
}
function correctMessage(row: CorrespondenceMessage) {
  if (pendingMessageId.value || !mayReplace(messageForm.value?.querySelector('form'))) return;
  Object.assign(correspondence, row, {
    recipients: row.recipients.join('\n'),
    occurred_at:
      row.date_precision === 'day'
        ? row.occurred_at.slice(0, 10)
        : new Date(
            new Date(row.occurred_at).getTime() -
              new Date(row.occurred_at).getTimezoneOffset() * 60000,
          )
            .toISOString()
            .slice(0, 19),
    supersedes_event_id: row.event_id,
  });
  // Copy only editable fields: identifiers and attachments are retained separately.
  for (const key of Object.keys(correspondence))
    if (!(key in blankMessage()))
      delete (correspondence as unknown as Record<string, unknown>)[key];
  messageFiles.value = [];
  if (messageForm.value) {
    const form = messageForm.value.querySelector('form');
    if (form) {
      dirtyForms.add(form);
      dirty.value = true;
    }
    messageForm.value.open = true;
    messageForm.value.scrollIntoView?.({ block: 'nearest' });
  }
}
function actionForMessage(row: CorrespondenceMessage) {
  if (!mayReplace(actionForm.value)) return;
  resetAction();
  if (actionForm.value) {
    dirtyForms.add(actionForm.value);
    dirty.value = true;
  }
  action.description = row.subject;
  action.correspondence_event_id = row.event_id;
  action.deadline_source = row.source_url || row.event_id;
  actionForm.value?.scrollIntoView?.({ block: 'nearest' });
}
async function attachExisting() {
  while (extraFiles.value.length) {
    if (!(await attachMessageFile(attachmentTarget.value, extraFiles.value[0]))) return;
    extraFiles.value = extraFiles.value.slice(1);
  }
}
watch(
  () => props.context,
  () => {
    detail.value = undefined;
    void load();
  },
  { immediate: true },
);
</script>
<template>
  <div
    class="section-stack government-cases-page"
    @input="markDirty"
    @change="markDirty"
    @submit.capture="submitted"
  >
    <h2 ref="heading" tabindex="-1">
      {{ showingCase ? savedTitle || t('government.caseDetail') : t('government.cases') }}
    </h2>
    <span class="visually-hidden" aria-live="polite">{{ savedTitle }}</span>
    <a v-if="showingCase" href="/government-cases" @click="follow">{{
      t('government.backToCases')
    }}</a>
    <div v-if="detailError" class="panel" role="alert">
      {{ invalidCase ? t('government.invalidCase') : t('government.caseUnavailable') }}
      <button v-if="!invalidCase" @click="load">{{ t('common.reload') }}</button>
    </div>
    <div v-if="listError" class="panel" role="alert">
      {{ listError }} <button @click="loadList">{{ t('common.reload') }}</button>
    </div>
    <p class="period-note">{{ t('government.timezoneHint', { zone: browserZone }) }}</p>
    <div v-if="error" role="alert" class="panel">
      <p>{{ error }}</p>
      <button
        v-if="conflict && caseId"
        class="secondary-button"
        :disabled="saving"
        @click="reloadAfterConflict"
      >
        {{ t('government.refreshCase') }}
      </button>
      <button v-if="!conflict" class="secondary-button" :disabled="saving" @click="error = ''">
        {{ t('common.close') }}
      </button>
    </div>
    <p v-if="loading" role="status">{{ t('common.loading') }}</p>
    <p v-if="saving" role="status">{{ t('government.saving') }}</p>
    <template v-if="list && !showingCase">
      <section class="panel">
        <h2>{{ t('government.reminders') }}</h2>
        <p>
          {{ t(list.reminders.configured ? 'government.smtpReady' : 'government.smtpMissing') }}
        </p>
        <p v-if="list.reminders.worker_stale" role="status">{{ t('government.workerStale') }}</p>
        <p>
          {{ t('government.lastRun') }}:
          {{
            list.reminders.last_run
              ? dateText(list.reminders.last_run.started_at)
              : t('government.never')
          }}
          · {{ list.reminders.last_run ? label('delivery', list.reminders.last_run.status) : '' }}
        </p>
        <p>
          {{ t('government.lastSent') }}:
          {{
            list.reminders.last_sent_at
              ? dateText(list.reminders.last_sent_at)
              : t('government.never')
          }}
        </p>
        <p v-if="list.reminders.last_run?.error">{{ list.reminders.last_run.error }}</p>
        <details v-if="list.reminders.messages.length">
          <summary>{{ t('government.deliveryLog') }}</summary>
          <ul>
            <li v-for="message in list.reminders.messages" :key="message.message_id">
              {{ dateText(message.created_at) }} · {{ label('delivery', message.status) }}
              <span v-if="message.error">{{ message.error }}</span>
              <form
                :inert="saving"
                v-if="message.status === 'uncertain'"
                data-reminder-form
                @submit.prevent="resolve(message.message_id, 'sent')"
              >
                <label
                  >{{ t('government.evidence')
                  }}<input v-model="resolutionSource" required /></label
                ><button :disabled="saving">{{ t('government.deliveryConfirmed') }}</button
                ><button
                  type="button"
                  :disabled="saving || !resolutionSource.trim()"
                  @click="
                    submitted($event);
                    resolve(message.message_id, 'failed');
                  "
                >
                  {{ t('government.retryConfirmed') }}
                </button>
              </form>
            </li>
          </ul>
        </details>
      </section>
      <section class="panel">
        <h2>{{ t('government.nextActions') }}</h2>
        <p v-if="!list.actions.length">{{ t('government.noActions') }}</p>
        <ul>
          <li v-for="row in list.actions" :key="row.action_id">
            <a class="secondary-button" :href="caseUrl(row.case_id)" @click="follow">
              {{ row.title }}: {{ row.description }}
            </a>
            · {{ dateText(row.due_on) }}
            <strong v-if="!row.confirmed">{{ t('government.unconfirmed') }}</strong>
          </li>
        </ul>
      </section>
      <section class="panel">
        <p>
          <a href="/aeat-documents">{{ t('government.legacyAeat') }}</a>
        </p>
        <div class="government-form-grid">
          <label
            >{{ t('government.authority')
            }}<select v-model="filters.authority">
              <option value="">{{ t('government.all') }}</option>
              <option
                v-for="a in list.authorities || [
                  { authority_code: 'AEAT', name: 'AEAT' },
                  { authority_code: 'TGSS', name: 'TGSS' },
                ]"
                :key="a.authority_code"
                :value="a.authority_code"
              >
                {{ a.name }}
              </option>
            </select></label
          ><label
            >{{ t('government.workState')
            }}<select v-model="filters.state">
              <option value="">{{ t('government.all') }}</option>
              <option v-for="state in workStates" :key="state" :value="state">
                {{ label('state', state) }}
              </option>
            </select></label
          ><label
            >{{ t('government.search') }}<input v-model="filters.query" type="search"
          /></label>
        </div>
        <p v-if="!rows.length">{{ t('government.empty') }}</p>
        <ul>
          <li v-for="row in rows" :key="row.case_id">
            <a class="secondary-button" :href="caseUrl(row.case_id)" @click="follow">
              {{ row.authority_name || row.authority_code }} · {{ row.title }} ·
              {{ row.primary_reference }}
            </a>
            · {{ label('state', row.work_state) }}
            <p v-if="row.last_message">
              {{ t('government.lastMessage') }}:
              {{
                dateText(
                  row.last_message.date_precision === 'day'
                    ? row.last_message.occurred_at.slice(0, 10)
                    : row.last_message.occurred_at,
                )
              }}
              ·
              {{ row.last_message.subject }}
            </p>
            <p v-if="row.next_action">
              {{ row.next_action.description }} · {{ dateText(row.next_action.due_on) }}
            </p>
          </li>
        </ul>
        <details>
          <summary>{{ t('government.create') }}</summary>
          <form :inert="saving" class="government-form-grid" @submit.prevent="createCase">
            <label
              >{{ t('government.authority')
              }}<input
                v-model="create.authority_code"
                list="government-authorities"
                required
                maxlength="200"
              />
              <datalist id="government-authorities">
                <option
                  v-for="a in list.authorities || []"
                  :key="a.authority_code"
                  :value="a.authority_code"
                >
                  {{ a.name }}
                </option>
              </datalist></label
            >
            <label
              >{{ t('government.title')
              }}<input v-model="create.title" required maxlength="200" /></label
            ><label
              >{{ t('government.reference')
              }}<input v-model="create.primary_reference" maxlength="200" /></label
            ><label
              >{{ t('government.subject')
              }}<textarea v-model="create.subject" maxlength="4000" /></label
            ><label
              >{{ t('government.occurred')
              }}<input v-model="create.opened_at" type="datetime-local" step="1" required /></label
            ><label
              >{{ t('government.evidence')
              }}<input v-model="create.evidence_reference" required /></label
            ><label
              >{{ t('government.parentCase')
              }}<select v-model="create.parent_case_id">
                <option value="">{{ t('government.noParent') }}</option>
                <option v-for="r in list.rows" :key="r.case_id" :value="r.case_id">
                  {{ r.title }}
                </option>
              </select></label
            >
            <button class="primary-button" :disabled="saving">{{ t('government.save') }}</button>
          </form>
        </details>
      </section>
    </template>
    <section v-if="detail && showingCase" class="panel" aria-live="polite">
      <p>{{ detail.authority_name || detail.authority_code }}</p>
      <p>{{ detail.primary_reference }} · {{ detail.subject }}</p>
      <p>
        {{ t('government.outcome') }}:
        {{
          ['approved', 'rejected'].includes(detail.current_status)
            ? label('outcome', detail.current_status)
            : t('government.noDecision')
        }}
      </p>
      <nav v-if="detail.related_cases?.length" :aria-label="t('government.relatedCases')">
        <h3>{{ t('government.relatedCases') }}</h3>
        <ul>
          <li v-for="r in detail.related_cases" :key="r.case_id">
            <a class="secondary-button" :href="caseUrl(r.case_id)" @click="follow">
              {{ r.title }} · {{ r.primary_reference }}
            </a>
          </li>
        </ul>
      </nav>
      <form
        :inert="saving"
        ref="stateForm"
        class="government-form-grid"
        @submit.prevent="saveState"
      >
        <label
          >{{ t('government.title') }}<input v-model="detail.title" required maxlength="200"
        /></label>
        <label
          >{{ t('government.reference') }}<input v-model="detail.primary_reference" maxlength="200"
        /></label>
        <label
          >{{ t('government.subject') }}<textarea v-model="detail.subject" maxlength="4000" />
        </label>
        <label
          >{{ t('government.workState')
          }}<select v-model="detail.work_state">
            <option v-for="state in workStates" :key="state" :value="state">
              {{ label('state', state) }}
            </option>
          </select></label
        ><label
          >{{ t('government.parentCase')
          }}<select
            v-model="detail.parent_case_id"
            :disabled="!list"
            :aria-label="t('government.parentCase')"
          >
            <option :value="null">{{ t('government.noParent') }}</option>
            <option
              v-for="r in (list?.rows || []).filter((r) => r.case_id !== detail?.case_id)"
              :key="r.case_id"
              :value="r.case_id"
            >
              {{ r.title }}
            </option>
          </select></label
        >
        <label>{{ t('government.evidence') }}<input v-model="stateSource" required /></label
        ><button :disabled="saving || !list">{{ t('government.saveState') }}</button>
      </form>
      <h3>{{ t('government.actions') }}</h3>
      <p v-if="!detail.actions.length">{{ t('government.noActions') }}</p>
      <ul>
        <li v-for="row in detail.actions" :key="row.action_id">
          {{ row.description }} · {{ dateText(row.due_on) }} ·
          {{ label('actionStatus', row.status) }}
          <strong v-if="!row.confirmed">{{ t('government.unconfirmed') }}</strong
          ><button class="secondary-button" :disabled="saving" @click="editAction(row)">
            {{ t('government.edit') }}
          </button>
        </li>
      </ul>
      <form
        :inert="saving"
        ref="actionForm"
        class="government-form-grid"
        @submit.prevent="saveAction"
      >
        <label
          >{{ t('government.actionKind')
          }}<select v-model="action.kind">
            <option v-for="kind in actionKinds" :key="kind" :value="kind">
              {{ label('actionKind', kind) }}
            </option>
          </select></label
        ><label
          >{{ t('government.sourceMessage')
          }}<select
            v-model="action.correspondence_event_id"
            :aria-label="t('government.sourceMessage')"
          >
            <option value="">{{ t('government.notRecorded') }}</option>
            <option v-for="m in detail.correspondence || []" :key="m.event_id" :value="m.event_id">
              {{ m.subject }}
            </option>
          </select></label
        >
        <label
          >{{ t('government.description')
          }}<input v-model="action.description" required maxlength="1000" /></label
        ><label>{{ t('government.dueOn') }}<input v-model="action.due_on" type="date" /></label
        ><label
          >{{ t('government.deadlineSource')
          }}<input v-model="action.deadline_source" :required="action.confirmed" /></label
        ><label
          ><input v-model="action.confirmed" type="checkbox" />{{
            t('government.confirmed')
          }}</label
        ><label
          >{{ t('government.actionStatus')
          }}<select v-model="action.status">
            <option
              v-for="status in ['open', 'completed', 'cancelled']"
              :key="status"
              :value="status"
            >
              {{ label('actionStatus', status) }}
            </option>
          </select></label
        ><button class="primary-button" :disabled="saving">
          {{ t(editId ? 'government.saveAction' : 'government.addAction') }}</button
        ><button type="button" class="secondary-button" :disabled="saving" @click="resetAction">
          {{ t('government.reset') }}
        </button>
      </form>
      <h3>{{ t('government.documents') }}</h3>
      <ul>
        <li v-for="document in detail.documents" :key="document.aeat_document_id">
          <a :href="document.content_url" target="_blank" rel="noopener"
            >{{ label('documentKind', document.document_kind) }} ·
            {{ dateText(document.occurred_at) }}</a
          >
          <dl v-if="document.placed_at || document.received_at || document.access_expires_at">
            <dt>{{ t('government.placed') }}</dt>
            <dd>{{ dateText(document.placed_at) }}</dd>
            <dt>{{ t('government.received') }}</dt>
            <dd>{{ dateText(document.received_at) }}</dd>
            <dt>{{ t('government.accessExpires') }}</dt>
            <dd>{{ dateText(document.access_expires_at) }}</dd>
          </dl>
        </li>
      </ul>
      <details>
        <summary>{{ t('government.addPdf') }}</summary>
        <form :inert="saving" class="government-form-grid" @submit.prevent="uploadPdf">
          <label
            >{{ t('government.pdf')
            }}<input
              type="file"
              accept="application/pdf"
              required
              @change="file = ($event.target as HTMLInputElement).files?.[0]" /></label
          ><label
            >{{ t('government.documentKind')
            }}<select v-model="pdf.document_kind">
              <option v-for="kind in documentKinds" :key="kind" :value="kind">
                {{ label('documentKind', kind) }}
              </option>
            </select></label
          ><label
            >{{ t('government.occurred')
            }}<input v-model="pdf.occurred_at" type="datetime-local" step="1" required /></label
          ><label
            >{{ t('government.placed')
            }}<input v-model="pdf.placed_at" type="datetime-local" step="1" /></label
          ><label
            >{{ t('government.received')
            }}<input v-model="pdf.received_at" type="datetime-local" step="1" /></label
          ><label
            >{{ t('government.accessExpires')
            }}<input v-model="pdf.access_expires_at" type="datetime-local" step="1" /></label
          ><label>{{ t('government.notes') }}<textarea v-model="pdf.notes" /></label
          ><button :disabled="saving || !file">{{ t('government.save') }}</button>
        </form>
      </details>
      <details ref="messageForm">
        <summary>{{ t('government.addCorrespondence') }}</summary>
        <form :inert="saving" class="government-form-grid" @submit.prevent="addCorrespondence">
          <fieldset :disabled="saving || !!pendingMessageId" class="government-form-grid">
            <label
              >{{ t('government.direction')
              }}<select v-model="correspondence.direction">
                <option value="incoming">{{ t('government.direction.incoming') }}</option>
                <option value="outgoing">{{ t('government.direction.outgoing') }}</option>
              </select></label
            >
            <label
              >{{ t('government.channel')
              }}<select v-model="correspondence.channel">
                <option v-for="c in ['email', 'portal', 'paper']" :key="c" :value="c">
                  {{ label('channel', c) }}
                </option>
              </select></label
            >
            <label
              >{{ t('government.datePrecision')
              }}<select
                v-model="correspondence.date_precision"
                @change="
                  correspondence.occurred_at =
                    correspondence.date_precision === 'day'
                      ? correspondence.occurred_at.slice(0, 10)
                      : ''
                "
              >
                <option value="instant">{{ t('government.datePrecision.instant') }}</option>
                <option value="day">{{ t('government.datePrecision.day') }}</option>
              </select></label
            >
            <label
              >{{ t('government.occurred')
              }}<input
                v-model="correspondence.occurred_at"
                :type="correspondence.date_precision === 'day' ? 'date' : 'datetime-local'"
                step="1"
                required
            /></label>
            <label
              >{{ t('government.sender') }}<input v-model="correspondence.sender" maxlength="4000"
            /></label>
            <label
              >{{ t('government.recipients')
              }}<textarea
                v-model="correspondence.recipients"
                :placeholder="t('government.onePerLine')"
              />
            </label>
            <label
              >{{ t('government.messageSubject')
              }}<input v-model="correspondence.subject" required maxlength="4000"
            /></label>
            <label
              >{{ t('government.fullText')
              }}<textarea v-model="correspondence.body_text" rows="10" />
            </label>
            <label
              >{{ t('government.summary')
              }}<textarea v-model="correspondence.summary" maxlength="4000" />
            </label>
            <label
              >{{ t('government.openSource')
              }}<input v-model="correspondence.source_url" type="url" maxlength="4000"
            /></label>
            <label
              >{{ t('government.replyTo')
              }}<select v-model="correspondence.reply_to_event_id">
                <option value="">{{ t('government.notRecorded') }}</option>
                <option
                  v-for="m in detail.correspondence || []"
                  :key="m.event_id"
                  :value="m.event_id"
                >
                  {{ m.subject }}
                </option>
              </select></label
            >
            <label
              >{{ t('government.attachments')
              }}<input
                :key="correspondence.source_message_id"
                type="file"
                multiple
                @change="selectFiles($event, 'message')"
            /></label>
          </fieldset>
          <p v-if="correspondence.supersedes_event_id">{{ t('government.correctMessage') }}</p>
          <p v-if="pendingMessageId" role="status">{{ t('government.resumeAttachments') }}</p>
          <button class="primary-button" :disabled="saving">
            {{ t('government.saveCorrespondence') }}
          </button>
        </form>
      </details>
      <details>
        <summary>{{ t('government.addAttachments') }}</summary>
        <form :inert="saving" class="government-form-grid" @submit.prevent="attachExisting">
          <label
            >{{ t('government.sourceMessage')
            }}<select v-model="attachmentTarget" required>
              <option value="">{{ t('government.choose') }}</option>
              <option
                v-for="m in detail.correspondence || []"
                :key="m.event_id"
                :value="m.event_id"
              >
                {{ m.subject }}
              </option>
            </select></label
          >
          <label
            >{{ t('government.attachments')
            }}<input type="file" multiple required @change="selectFiles($event, 'extra')"
          /></label>
          <button :disabled="saving || !extraFiles.length">{{ t('government.save') }}</button>
        </form>
      </details>
      <h3>{{ t('government.history') }}</h3>
      <ol class="government-history">
        <li v-for="row in detail.events" :key="row.event_id">
          <strong>{{ label('eventKind', row.kind) }}</strong> ·
          {{
            dateText(
              messageById.get(row.event_id)?.date_precision === 'day'
                ? row.occurred_at.slice(0, 10)
                : row.occurred_at,
            )
          }}
          <template v-if="messageById.has(row.event_id)">
            <template v-for="m in [messageById.get(row.event_id)!]" :key="m.event_id">
              <p>
                <strong>{{ m.subject }}</strong> · {{ label('direction', m.direction) }} ·
                {{ label('channel', m.channel) }}
              </p>
              <p>
                {{ t('government.sender') }}: {{ m.sender || t('government.notRecorded') }} ·
                {{ t('government.recipients') }}:
                {{ m.recipients.join(', ') || t('government.notRecorded') }}
              </p>
              <p v-if="m.superseded">{{ t('government.superseded') }}</p>
              <p v-if="m.reply_to_event_id">
                {{ t('government.replyTo') }}: {{ messageById.get(m.reply_to_event_id)?.subject }}
              </p>
              <p>{{ m.summary }}</p>
              <details>
                <summary>{{ t('government.fullText') }}</summary>
                <pre class="correspondence-text">{{ m.body_text }}</pre>
              </details>
              <a
                v-if="messageLink(m.source_url)"
                :href="messageLink(m.source_url)"
                target="_blank"
                rel="noopener noreferrer"
                >{{ t('government.openSource') }}</a
              >
              <ul>
                <li v-for="a in m.attachments" :key="a.filename">
                  <a :href="a.content_url" target="_blank" rel="noopener">{{ a.filename }}</a>
                </li>
              </ul>
              <p v-if="m.missing_attachments.length" role="status">
                {{ t('government.missingAttachments') }}: {{ m.missing_attachments.join(', ') }}
              </p>
              <button class="secondary-button" :disabled="saving" @click="actionForMessage(m)">
                {{ t('government.addAction') }}
              </button>
              <button
                class="secondary-button"
                :disabled="saving || m.superseded || !!pendingMessageId"
                @click="correctMessage(m)"
              >
                {{ t('government.correctMessage') }}
              </button>
            </template>
          </template>
          <template v-else
            ><p>{{ eventSummary(row) }}</p>
            <p>{{ row.notes }}</p></template
          >
          <a
            v-if="!messageById.has(row.event_id) && safeLink(row.evidence_reference)"
            :href="safeLink(row.evidence_reference)"
            target="_blank"
            rel="noopener"
            >{{ t('government.evidence') }}</a
          >
          <p v-else-if="!messageById.has(row.event_id)">{{ row.evidence_reference }}</p>
          <small
            >{{ t('government.recorded') }}: {{ dateText(row.recorded_at) }} ·
            <span v-if="!messageById.has(row.event_id)">{{ row.event_id }}</span></small
          >
        </li>
      </ol>
      <details>
        <summary>{{ t('government.addEvent') }}</summary>
        <form :inert="saving" class="government-form-grid" @submit.prevent="addEvent">
          <label
            >{{ t('government.eventKind')
            }}<select v-model="event.kind">
              <option v-for="kind in eventKinds" :key="kind" :value="kind">
                {{ label('eventKind', kind) }}
              </option>
            </select></label
          ><label
            >{{ t('government.occurred')
            }}<input v-model="event.occurred_at" type="datetime-local" step="1" required /></label
          ><label
            >{{ t('government.evidence')
            }}<input v-model="event.evidence_reference" required /></label
          ><label>{{ t('government.notes') }}<textarea v-model="event.notes" /></label
          ><label v-if="['decision', 'correction'].includes(event.kind)"
            >{{ t('government.outcome')
            }}<select v-model="event.outcome">
              <option value="">{{ t('government.unchanged') }}</option>
              <option v-for="outcome in outcomes" :key="outcome" :value="outcome">
                {{ label('outcome', outcome) }}
              </option>
            </select></label
          ><label v-if="event.kind === 'correction'"
            >{{ t('government.corrects')
            }}<select v-model="event.supersedes_event_id" required>
              <option value="">{{ t('government.choose') }}</option>
              <option v-for="row in detail.events" :key="row.event_id" :value="row.event_id">
                {{ label('eventKind', row.kind) }} · {{ dateText(row.occurred_at) }}
              </option>
            </select></label
          ><button class="primary-button" :disabled="saving">{{ t('government.save') }}</button>
        </form>
      </details>
    </section>
  </div>
</template>
