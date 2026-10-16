# Government cases and reminders

Extend the existing evidence registry for AEAT and TGSS. Preserve legacy IDs and
immutable evidence, and keep decision outcomes separate from operator work state.
Cases have versioned actions, reviewed source-backed deadlines, append-only events,
PDF originals and Google Drive references. Missing/unconfirmed deadlines remain
visible and never masquerade as verified legal dates.

The UI, HTTP API and installed CLI share Python operations. Legacy AEAT routes
remain AEAT-only. The dashboard lists case actions. Server reminders use verified
TLS SMTP and an hourly systemd timer, with durable deduplication, failure records,
Madrid calendar dates, and an explicit uncertainty state after ambiguous SMTP
outcomes. No government-portal acceptance, automatic filing, legal-date calculation
or accounting posting is part of this feature.

Acceptance covers populated schema migration, immutable history and original
hashes, legacy API scope, conflicts, rejection with open work, action completion,
localized UI, SMTP scheduling/restarts/failures, authentication and CSRF boundaries.
A private source-backed case import is prepared outside Git. Production migration,
SMTP provisioning, delivery acceptance and activation are separate release steps.
