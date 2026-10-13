# Public database-backed demo

The public demo runs the normal packaged frontend and `LocalAccountingApp`
against an isolated SQLite ledger. Screens make real HTTP requests under
`/demo/api/`; accounting services read that database. There are no bundled API
responses, browser fixture adapters or generated document HTML pages.

## Prepare the data once

Use Python 3.11 with the backend installed. Preparation requires an explicit
output directory, refuses existing data, and never reads personal configuration.
For a local source checkout:

```sh
python -m autonomo_taxes.public_demo_data --output /absolute/demo-data \
  --reference-root /absolute/checkout/reference
```

The prepared directory contains `demo.sqlite`, `demo-document.pdf` and
`demo-manifest.json`. The ledger uses the normal schema, accounting services and
synthetic scenarios for Q1–Q3 2026. Q3 is the default, and demonstration calculations
use 2026-09-30. Names and descriptions are stored once in English. The normal
application retains its current date and account configuration behavior.

The PDF is a single page with an English explanation of the fictional, read-only
demo and the shared example file. Every document and filing
link opens this same placeholder. Document identifiers stay distinct; shared
file content uses the existing attachment catalogue. The PDF is not an invoice,
receipt or evidence of a real transaction.

Prepare a fresh version when changing data or schema. Do not seed or migrate from
the web server, overwrite an existing prepared directory, or mount a working
account as demo data. Container preparation and runtime use the same `/data`
mount path so registered local file paths remain valid.

## Run the real application

Build the ordinary UI once with the pinned Node/npm versions, then run:

```sh
npm --prefix frontend run build
python -m autonomo_taxes.local_web --public-demo-root /absolute/demo-data \
  --host 127.0.0.1 --port 8766
```

Open `http://127.0.0.1:8766/demo/`. The explicit public-demo mode bypasses private
configuration discovery, validates the prepared data and fails if the database
or PDF is missing or invalid. It does not silently recreate data or substitute
snapshots. The server publishes non-secret runtime settings in the HTML shell;
the same frontend build selects read-only controls, prefixed HTTP/document links
and a hash router from those settings.

English is the default, with Spanish and Russian through `?lang=es` and
`?lang=ru`. Language changes translate interface text, help, dates and number
formats while keeping database text and PDF bytes unchanged. Hash routes retain
record/period/query state. Account preferences and accounting drafts are not read
or written in browser storage by the public demo.

## Read-only and isolation boundaries

The public HTTP handler allows the required GET/HEAD routes and rejects every
write method before reading its body or dispatching an operation. That check also
applies when the reverse proxy is bypassed. Business states such as reviewable or
ready to post stay accurate; permission to perform the action is independently
blocked by the server and the shared UI.

All runtime database connections and the deployed data mount are read-only.
Calculations use the common accounting reader, not saved JSON API results.
Document reads require a valid database identifier and the verified local PDF.
Remote document retrieval, ECB lookups, Google Picker and other integrations are
not available in public-demo mode. The container has no private mounts, tokens,
published backend port or external network route.

Caddy routes only `/demo/` to the internal backend. Root `/api/` and private paths
remain unavailable. The backend supplies endpoint-specific security headers:
same-origin API connections and PDF previews are allowed, external embedding and
form submissions are not. Do not apply the old static-demo `connect-src 'none'`
and `frame-src 'none'` policy over the real HTTP service.

## Validation and deployment

Run the normal type, locale, unit, browser and packaged-application checks plus
`npm --prefix frontend run test:demo`. The demo browser launcher prepares an
isolated SQLite once and runs the real backend; it does not intercept API replies.
Backend tests cover preparation, actual SQL-driven responses, all HTTP method
boundaries, identical explanatory PDF responses, missing data and data integrity.

Prove the database boundary by changing an isolated test ledger before startup
and verifying the changed value through HTTP and the UI without rebuilding the
frontend. Recompute the test manifest after that deliberate fixture edit. Compare
database bytes and logical records before and after browsing and rejected writes.

The Docker image consumes the verified ordinary frontend artifact associated
with the exact source SHA. See `ops/demo/` for image preparation and the landing
repository's `deploy/` for Caddy/Compose integration. Seed data in a separate
preparation container, then mount it read-only into the long-running backend.

Check a candidate before switching `/demo/`; retain the previous configuration
until public acceptance passes. Verify all nine screens, three languages, PDF
preview, real API reads and write rejection over HTTPS. Attach the resulting SHA,
SQL/API evidence and screenshots to NOA-19 before marking it complete.
