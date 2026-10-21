# Government correspondence

Extend Cases and notices for personal procedures with Spanish authorities.
Reuse the evidence journal, private file archive, reviewed deadline actions and
existing UI/HTTP/CLI services. Preserve AEAT/TGSS IDs and legacy AEAT API scope.

Cases support a small authority catalogue, optional official references and
acyclic parent links. Each procedure retains its own outcome and work state.
Append-only correspondence records direction, channel, date, participants,
subject, complete plain text, summary, evidence URL and expected originals.
Corrections retain the original and file links. Source message identity, rather
than thread or subject, governs idempotent import and conflict detection.

Originals share verified archived bytes; partial uploads remain visible and
resumable. UI text is inert, and unsupported preview formats download as files.
Messages are limited to 1 MiB per request, files to 30 MB. No accounting postings,
background mailbox discovery, government-portal acceptance, automatic filing or
legal-date calculation are introduced.

Schema 33 preserves populated cases, actions, evidence and immutable history.
Upgrade coverage includes the complete historical schema-29 annual-evidence
branch; partial or differing layouts fail before adoption. Rehearsals and
historical import reports remain private, outside the public repository.

Acceptance covers migration and accounting preservation, independent linked
procedures, source/byte deduplication, corrections, upload recovery, search,
authentication/CSRF, original downloads, safe text display, RU/EN/ES, keyboard and
mobile use. Release and live import follow the existing private deployment and
backup workflow after review of the exact release.
