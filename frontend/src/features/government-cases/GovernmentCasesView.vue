<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { formatMessage } from '../../core/i18n.ts';
import { errorMessage } from '../../core/error-message.ts';
import type { GovernmentContext, CaseList, CaseDetail, CaseAction, CaseEvent } from './model.ts';
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
  loading = ref(true);
const filters = reactive({ authority: '', state: '', query: '' });
const create = reactive({
  authority_code: 'TGSS',
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
  selectionGeneration = 0;
onBeforeUnmount(() => {
  active = false;
  generation++;
});
const rows = computed(() =>
  (list.value?.rows || []).filter(
    (r) =>
      (!filters.authority || r.authority_code === filters.authority) &&
      (!filters.state || r.work_state === filters.state) &&
      `${r.title} ${r.subject} ${r.primary_reference}`
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
  if (row.kind === 'workflow')
    return `${label('state', payload.from)} → ${label('state', payload.to)}`;
  return '';
}
async function load() {
  const revision = ++generation;
  loading.value = true;
  error.value = '';
  try {
    const result = (await props.context.services.request('/api/government-cases')) as CaseList;
    if (!active || revision !== generation) return;
    list.value = result;
    const id = new URLSearchParams(window.location.search).get('case');
    if (id) await select(id);
  } catch (failure) {
    if (active && revision === generation) error.value = errorMessage(failure, locale.value);
  } finally {
    if (active && revision === generation) {
      loading.value = false;
      props.context.settled();
    }
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
async function select(id: string) {
  const revision = ++selectionGeneration;
  detail.value = undefined;
  try {
    const result = (await props.context.services.request(
      `/api/government-cases/${encodeURIComponent(id)}`,
    )) as CaseDetail;
    if (active && revision === selectionGeneration) {
      detail.value = result;
      resetAction();
    }
  } catch (failure) {
    if (active && revision === selectionGeneration)
      error.value = errorMessage(failure, locale.value);
  }
}

async function write(path: string, payload: unknown, multipart = false) {
  if (saving.value || props.context.services.readOnly) return;
  saving.value = true;
  error.value = '';
  try {
    const result = (await props.context.services.request(path, {
      method: 'POST',
      body: multipart ? (payload as FormData) : JSON.stringify(payload),
      ...(!multipart ? { headers: { 'Content-Type': 'application/json' } } : {}),
    })) as CaseDetail;
    if (!active) return;
    if (result.case_id) detail.value = result;
    list.value = (await props.context.services.request('/api/government-cases')) as CaseList;
    props.context.notify(t('government.saved'));
    return true;
  } catch (failure) {
    if (active) error.value = errorMessage(failure, locale.value);
  } finally {
    if (active) saving.value = false;
  }
}
async function createCase() {
  if (await write('/api/government-cases', { ...create, opened_at: instant(create.opened_at) })) {
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
  editId.value = '';
  actionVersion.value = 0;
  Object.assign(action, {
    kind: 'respond',
    description: '',
    due_on: '',
    deadline_source: '',
    confirmed: false,
    status: 'open',
  });
}
function editAction(row: CaseAction) {
  editId.value = row.action_id;
  actionVersion.value = row.row_version;
  Object.assign(action, row, { due_on: row.due_on || '', confirmed: Boolean(row.confirmed) });
}
async function saveAction() {
  if (!detail.value) return;
  const payload = {
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
      work_state: detail.value.work_state,
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
  await write('/api/government-reminders/resolve', {
    message_id: messageId,
    outcome,
    evidence_reference: resolutionSource.value,
  });
}
</script>
<template>
  <div class="section-stack government-cases-page">
    <p class="period-note">{{ t('government.timezoneHint', { zone: browserZone }) }}</p>
    <div v-if="error" role="alert" class="panel">
      <p>{{ error }}</p>
      <button class="secondary-button" :disabled="saving" @click="load">
        {{ t('common.reload') }}
      </button>
    </div>
    <p v-if="loading" role="status">{{ t('common.loading') }}</p>
    <template v-if="list">
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
                v-if="message.status === 'uncertain'"
                @submit.prevent="resolve(message.message_id, 'sent')"
              >
                <label
                  >{{ t('government.evidence')
                  }}<input v-model="resolutionSource" required /></label
                ><button :disabled="saving">{{ t('government.deliveryConfirmed') }}</button
                ><button
                  type="button"
                  :disabled="saving || !resolutionSource.trim()"
                  @click="resolve(message.message_id, 'failed')"
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
            <button class="secondary-button" :disabled="saving" @click="select(row.case_id)">
              {{ row.title }}: {{ row.description }}
            </button>
            · {{ dateText(row.due_on) }}
            <strong v-if="!row.confirmed">{{ t('government.unconfirmed') }}</strong>
          </li>
        </ul>
      </section>
      <section class="panel">
        <h2>{{ t('government.cases') }}</h2>
        <p>
          <a href="/aeat-documents">{{ t('government.legacyAeat') }}</a>
        </p>
        <div class="government-form-grid">
          <label
            >{{ t('government.authority')
            }}<select v-model="filters.authority">
              <option value="">{{ t('government.all') }}</option>
              <option>AEAT</option>
              <option>TGSS</option>
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
            <button class="secondary-button" :disabled="saving" @click="select(row.case_id)">
              {{ row.authority_code }} · {{ row.title }} · {{ row.primary_reference }}
            </button>
            · {{ label('state', row.work_state) }}
          </li>
        </ul>
        <details>
          <summary>{{ t('government.create') }}</summary>
          <form class="government-form-grid" @submit.prevent="createCase">
            <label
              >{{ t('government.authority')
              }}<select v-model="create.authority_code">
                <option>AEAT</option>
                <option>TGSS</option>
              </select></label
            >
            <label
              >{{ t('government.title')
              }}<input v-model="create.title" required maxlength="200" /></label
            ><label
              >{{ t('government.reference')
              }}<input v-model="create.primary_reference" required maxlength="200" /></label
            ><label
              >{{ t('government.subject')
              }}<textarea v-model="create.subject" maxlength="4000" /></label
            ><label
              >{{ t('government.occurred')
              }}<input v-model="create.opened_at" type="datetime-local" step="1" required /></label
            ><label
              >{{ t('government.evidence')
              }}<input v-model="create.evidence_reference" required /></label
            ><button class="primary-button" :disabled="saving">{{ t('government.save') }}</button>
          </form>
        </details>
      </section>
      <section v-if="detail" class="panel" aria-live="polite">
        <h2>{{ detail.authority_code }} · {{ detail.title }}</h2>
        <p>{{ detail.primary_reference }} · {{ detail.subject }}</p>
        <p>
          {{ t('government.outcome') }}:
          {{
            ['approved', 'rejected'].includes(detail.current_status)
              ? label('outcome', detail.current_status)
              : t('government.noDecision')
          }}
        </p>
        <form class="government-form-grid" @submit.prevent="saveState">
          <label
            >{{ t('government.workState')
            }}<select v-model="detail.work_state">
              <option v-for="state in workStates" :key="state" :value="state">
                {{ label('state', state) }}
              </option>
            </select></label
          ><label>{{ t('government.evidence') }}<input v-model="stateSource" required /></label
          ><button :disabled="saving">{{ t('government.saveState') }}</button>
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
        <form class="government-form-grid" @submit.prevent="saveAction">
          <label
            >{{ t('government.actionKind')
            }}<select v-model="action.kind">
              <option v-for="kind in actionKinds" :key="kind" :value="kind">
                {{ label('actionKind', kind) }}
              </option>
            </select></label
          ><label
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
          <form class="government-form-grid" @submit.prevent="uploadPdf">
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
        <h3>{{ t('government.history') }}</h3>
        <ol class="government-history">
          <li v-for="row in detail.events" :key="row.event_id">
            <strong>{{ label('eventKind', row.kind) }}</strong> · {{ dateText(row.occurred_at) }}
            <p>{{ eventSummary(row) }}</p>
            <p>{{ row.notes }}</p>
            <a
              v-if="safeLink(row.evidence_reference)"
              :href="safeLink(row.evidence_reference)"
              target="_blank"
              rel="noopener"
              >{{ t('government.evidence') }}</a
            >
            <p v-else>{{ row.evidence_reference }}</p>
            <small
              >{{ t('government.recorded') }}: {{ dateText(row.recorded_at) }} ·
              {{ row.event_id }}</small
            >
          </li>
        </ol>
        <details>
          <summary>{{ t('government.addEvent') }}</summary>
          <form class="government-form-grid" @submit.prevent="addEvent">
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
    </template>
  </div>
</template>
