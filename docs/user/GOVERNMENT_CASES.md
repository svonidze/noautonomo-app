# AEAT and TGSS cases

The **Cases and notices** page records government interactions separately from
invoices and bookkeeping. Existing AEAT identifiers, evidence and status history
are preserved. **Filed returns and ROI registration** opens the compatible AEAT
receipt workflow.

Create a case with its authority, expediente/reference, subject, first event date
and evidence reference. The UI displays its browser timezone and converts native
date/time inputs to unambiguous instants. CLI/API timestamps require ISO 8601 with
an explicit timezone. Each event retains its actual date and the time it was recorded. Correct a
mistake by adding a **Correction** referring to the earlier event, not by rewriting
history. A **Drive document** event links an existing HTTPS Google Drive file;
the application does not import or expose third-party HTML.

Upload original PDFs without modifying their bytes. The hash prevents duplicate
registration. Notification placement, receipt and access expiry are independent
optional source-backed timestamps. Uploading a PDF does not accept a notification
on a government portal. It also does not record a payment in the books.

## Decisions and work

A rejected appeal is a decision outcome, not a case closure. Keep the work state
as **Action required** until the next decision has been made. A case can be closed
only explicitly and after its open actions have been completed or cancelled.

Each action has a description, optional due date, evidence source, confirmed flag
and open/completed/cancelled state. Changing its deadline is audited. An unconfirmed
or missing deadline remains visible as requiring review; it does not generate a
speculative legal-deadline email. The owner must verify legal dates against the
original document and applicable calendar. The application does not calculate them.

## CLI and HTTP

All writes call the same Python services as the UI. The CLI uses the configured
private root and never initializes a missing existing ledger:

```
autonomo-tax government-cases list
autonomo-tax government-cases show --case-id CASE_UUID
autonomo-tax government-cases create --input PRIVATE_CASE_JSON --actor operator
autonomo-tax government-cases event --case-id CASE_UUID --input PRIVATE_EVENT_JSON --actor operator
autonomo-tax government-cases state --case-id CASE_UUID --input PRIVATE_STATE_JSON --actor operator
autonomo-tax government-cases action --case-id CASE_UUID --input PRIVATE_ACTION_JSON --actor operator
autonomo-tax government-cases document --case-id CASE_UUID --evidence PRIVATE_PDF --input PRIVATE_METADATA_JSON --actor operator
autonomo-tax government-cases reminders --dry-run
```

Use `--action-id ACTION_UUID` to update an action. Case writes carry
`expected_row_version`; action updates also carry `action_version`. Reload on a
conflict. JSON request files are private evidence and must not be committed.

HTTP routes use the existing authentication and same-origin protections:
`GET/POST /api/government-cases`, `GET /api/government-cases/{id}`,
`POST /api/government-cases/{id}/state`, `/events`, `/actions`,
`/actions/{action_id}`, and `/documents`. PDF upload is multipart with one `file`
and a `metadata` JSON field. Other writes use JSON. Legacy `/api/aeat-*` reads and
writes remain scoped to AEAT.

## Server reminders

The hourly job reads the database even when no browser is open. It sends current
actions at 09:00 Europe/Madrid, seven, three, one and zero days before the deadline,
and daily when overdue. Newly registered urgent actions can be included on the
next hourly tick. One digest combines eligible actions; it contains no original
attachments. After downtime the next run sends the current digest, not a backlog
of old daily emails.

The page shows mail configuration, the latest worker run, last successful send and
recent delivery results. A stale worker warning means the job has not checked in
for two hours. Completed/cancelled actions and closed cases are not mailed.

SMTP has an unavoidable uncertainty window if its DATA response is lost. Such a
message is marked **uncertain**, not automatically retried. Check the mail server
or recipient and record the outcome with the evidence field and confirmation
buttons. The equivalent CLI is `government-cases resolve-reminder --input
PRIVATE_RESOLUTION_JSON --actor operator`; fields are `message_id`, `outcome`
(`sent` or `failed`) and `evidence_reference`. A confirmed failure becomes eligible
for the next hourly retry. Do not mark an unverified email delivered.

The job does not discover new notifications in government inboxes. A separate
portal-monitoring workflow is required for that coverage.
