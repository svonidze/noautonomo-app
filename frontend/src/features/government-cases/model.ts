import type { ViewServices } from '../../vue/services.ts';
import type { UiMessage } from '../../core/i18n.ts';
export interface CaseRow {
  decision_outcome?: string | null;
  case_id: string;
  authority_code: string;
  authority_name?: string;
  parent_case_id?: string | null;
  search_text?: string;
  last_message?: { subject: string; occurred_at: string; date_precision?: string } | null;
  next_action?: CaseAction | null;
  title: string;
  subject: string;
  primary_reference: string;
  current_status: string;
  work_state: string;
  row_version: number;
}
export interface CaseAction {
  correspondence_event_id?: string | null;
  action_id: string;
  case_id: string;
  kind: string;
  description: string;
  due_on: string | null;
  deadline_source: string;
  confirmed: number;
  status: string;
  row_version: number;
  title?: string;
}
export interface CaseEvent {
  payload_json?: string;
  event_id: string;
  kind: string;
  occurred_at: string;
  recorded_at: string;
  evidence_reference: string;
  notes: string;
  status?: string;
}
export interface CaseDocument {
  aeat_document_id: string;
  document_kind: string;
  occurred_at: string;
  content_url: string;
  placed_at?: string;
  received_at?: string;
  access_expires_at?: string;
}
export interface CorrespondenceMessage {
  date_precision?: string;
  event_id: string;
  direction: string;
  channel: string;
  occurred_at: string;
  sender: string;
  recipients: string[];
  subject: string;
  body_text: string;
  summary: string;
  source_url: string;
  source_provider: string;
  source_account: string;
  source_message_id: string;
  reply_to_event_id: string;
  supersedes_event_id: string;
  superseded: boolean;
  attachment_names: string[];
  missing_attachments: string[];
  attachments: { filename: string; content_url: string; mime_type: string }[];
}
export interface CaseDetail extends CaseRow {
  correspondence?: CorrespondenceMessage[];
  related_cases?: CaseRow[];
  correspondence_event_id?: string;
  actions: CaseAction[];
  events: CaseEvent[];
  documents: CaseDocument[];
}
export interface ReminderHealth {
  configured: boolean;
  worker_stale: boolean;
  last_sent_at?: string;
  last_run?: { started_at: string; status: string; error?: string };
  messages: { message_id: string; status: string; error?: string; created_at: string }[];
}
export interface CaseList {
  authorities?: { authority_code: string; name: string }[];
  rows: CaseRow[];
  actions: CaseAction[];
  reminders: ReminderHealth;
}
export interface GovernmentContext {
  services: ViewServices;
  settled(): void;
  notify(value: UiMessage | string, error?: boolean): void;
}
