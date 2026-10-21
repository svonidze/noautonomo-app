# Government cases and correspondence

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

## Correspondence with other Spanish authorities

The authority field accepts an existing authority or a new name. Cases can be
created before an official reference is assigned. An optional parent links a
separate procedure, such as a delay complaint, without combining its decision,
actions or deadlines with the parent. The parent can be changed with a sourced
state update; self-links and cycles are rejected.

**Add correspondence** records an incoming or outgoing email, portal message or
paper letter. Store the subject, participants, full plain text, summary, source
URL and originals. The single timeline includes correspondence, earlier evidence,
decisions and action history. Search includes the complete text and participants.
**Add action** links an action to its source message; receiving an email never
confirms legal receipt or completes an action automatically.

Use **Record correction** to append a replacement interpretation or transcription.
The original remains visible, marked as superseded. Corrections retain their
original attachment links. Source identity is provider + account + message ID,
not the email thread or subject. A retry with the same identity and contents is
idempotent; different contents or a different case require explicit review.

The CLI uses the same service:

```
autonomo-tax government-cases correspondence --case-id CASE_UUID --input PRIVATE_MESSAGE_JSON --actor operator
```

Message JSON fields are `expected_row_version`, `direction` (`incoming` or
`outgoing`), `channel` (`email`, `portal`, `paper`), `occurred_at` (ISO 8601 with
timezone), `date_precision` (`instant`, the default, or `day` when only a date is known),
`sender`, `recipients` (string array), `subject`, `body_text`, `summary`,
`source_url` (optional HTTPS URL), `source_provider`, `source_account`,
`source_message_id`, `reply_to_event_id`, `supersedes_event_id` and
`attachment_names` (unique filenames). Optional string fields default to empty;
optional arrays default to empty. A source message ID requires provider and
account. Subject and either text, originals or a source URL are required.
The HTTP endpoint is `POST /api/government-cases/{id}/correspondence`; request
size is limited to 1 MiB on both transports. `GET` case details returns
`correspondence`, `related_cases` and the existing events/documents/actions.
The list response includes `authorities`, `last_message` and `next_action` per
case. Case creation accepts `authority_name` and `parent_case_id`. State updates
also accept `parent_case_id`, `title`, `subject` and `primary_reference`, with
a sourced before/after history and duplicate-reference protection. Actions accept `correspondence_event_id`.

Attach each original with the existing `government-cases document` CLI or
`POST /api/government-cases/{id}/documents`. For a correspondence attachment,
metadata contains `expected_row_version`, `correspondence_event_id` and, for CLI,
`filename`. HTTP takes the filename from the uploaded file. The returned
`correspondence_event_id` identifies a newly recorded message; read the latest
case version after each upload. Files are limited to 30 MB each. Identical files
reuse the existing document and archived bytes. A conflicting filename/hash is
rejected. Expected files not yet uploaded remain listed under
`missing_attachments`; reload and use **Add message attachments** to resume.

Email originals (`.eml`) and attachments retain their bytes and SHA-256. Verified
PDF/JPEG/PNG can be previewed; other formats are downloaded, never rendered as
HTML or executed. The app does not fetch remote images or source URLs. All text,
files, source IDs and import reports belong in the configured private root.
Importing correspondence does not write accounting entries, send mail or access
government portals.
