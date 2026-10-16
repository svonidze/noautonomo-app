import type { ViewServices } from '../../vue/services.ts';
import type { UiMessage } from '../../core/i18n.ts';
export interface CaseRow {
  decision_outcome?: string | null;
  case_id: string;
  authority_code: string;
  title: string;
  subject: string;
  primary_reference: string;
  current_status: string;
  work_state: string;
  row_version: number;
}
export interface CaseAction {
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
export interface CaseDetail extends CaseRow {
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
  rows: CaseRow[];
  actions: CaseAction[];
  reminders: ReminderHealth;
}
export interface GovernmentContext {
  services: ViewServices;
  settled(): void;
  notify(value: UiMessage | string, error?: boolean): void;
}
