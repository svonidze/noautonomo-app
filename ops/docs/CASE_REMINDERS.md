# Government-case reminder deployment

This is a separate production operation. Review the release, verify a restorable
private-root/database backup, and migrate a copy first. Schema 32 preserves legacy
AEAT IDs and immutable evidence, expands authority to TGSS, and adds independent
work state, actions, audit events and delivery records. An older core cannot open
schema 32: rollback requires a compatible release or the verified pre-migration
backup, taking care to preserve any subsequent case updates privately.

Configure SMTP in the existing private runtime environment (for SOPS, in its
encrypted runtime generation). See `ops/runtime.env.example` for names. Use port
587 with `AUTONOMO_SMTP_TLS=starttls`, or port 465 with `implicit`. TLS certificate
verification is mandatory. No SMTP credentials or real recipient addresses belong
in Git. `AUTONOMO_CASE_BASE_URL` is the authenticated private application's HTTPS
base URL. The recipient is explicitly configured with `AUTONOMO_SMTP_TO`.
`AUTONOMO_CASE_MAIL_LOCALE` selects `ru` (default), `en` or `es` for mail labels.

Deploy and migrate through the existing private deployment runbook. Before any
mailing run `autonomo-tax government-cases reminders --dry-run` with the service's
actual environment, and inspect the due actions and destination privately. A dry
run sends nothing and does not update delivery state.

Render `ops/systemd/autonomo-case-reminders.service.template` with the reviewed
release/private roots and exact `@RUNTIME_ENV@` used by that deployment. For SOPS
use the managed runtime environment generation path. Install that rendered unit
and `ops/systemd/autonomo-case-reminders.timer` in the user's systemd unit directory;
reload systemd. `ops/install-case-reminder-units.sh RELEASE_ROOT PRIVATE_ROOT RUNTIME_ENV`
performs only this rendering/install/reload step and never enables the timer. Do not run a general provisioning installer or alter backup timers.

Before enabling the timer, send a controlled synthetic, confirmed due action to
the configured recipient and verify arrival, the application link and the saved
`sent` delivery result. Then explicitly enable/start only
`autonomo-case-reminders.timer`. Confirm its next hourly run, run timestamp and
successful delivery visibility in the UI. Persistent timers catch up after a
restart. A failure exits nonzero and invokes the existing operational alert unit;
the application also retains failed/uncertain/unconfigured results.

To stop reminders, stop/disable only this timer. SMTP failure before DATA is
retryable; ambiguous delivery is held for an evidence-backed operator resolution.
The queue/journal is included in ordinary database backups. Avoid running this
worker against an unsanitized cloned database with production SMTP credentials.
