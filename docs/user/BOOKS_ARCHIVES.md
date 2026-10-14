# Books and retained export versions

`filing-package build` requires a new output directory. Reusing a directory,
including a symlink or an empty directory, fails without changing its contents.
A failed export removes only the newly created incomplete directory. Successful
packages keep the manifest as their completion marker. Retain each version and
its manifest hash separately; filesystem permissions alone are not a WORM store.

```sh
autonomo-tax filing-package build --db /private/ledger.sqlite \
  --period 2024-Q1 --out-dir /private/archives/2024-Q1-review-001 \
  --supplemental-evidence /private/confirmations/2024-Q1.json
```

The optional confirmation file records the taxpayer's actual statement:

```json
{
  "period_key": "2024-Q1",
  "confirmed_on": "2024-04-15",
  "confirmed_by": "Taxpayer",
  "evidence_ref": "private-archive:confirmation-001",
  "has_movements": false
}
```

A confirmation must cover the requested period, be dated after its end, and
identify its author and retained evidence. `false` creates the empty
`provisiones-suplidos.csv` with the fields from Orden HAC/773/2019 article 6.3,
an explicit zero total and the confirmation. Missing evidence remains
unconfirmed. `true` records a blocker: accounting for movements is not yet
supported. An absent Xolo sheet is never evidence of no movements. Annual
confirmation is separate from quarter confirmation and cannot include future
periods. Do not infer client disbursements from invoice/tax-base differences.

Manifest version 3 separates three questions:

- `integrity_ok`: the retained artifact names, sizes and hashes agree. The
  database-independent `filing-package integrity --manifest PATH
  --expected-manifest-sha256 HASH` also verifies the externally retained manifest
  hash. Symlinks, traversal, duplicate artifact names and empty inventories fail.
- `currentness_ok` / legacy `manifest_matches`: regenerating from today's
  database and exporter gives the same manifest. A later correction or exporter
  version may change this without damaging the historical archive.
- `mandatory_books_ready`: currently false. Native exports have not passed all
  mandatory content checks. A closed period or filed snapshot does not prove
  this. Packages remain `draft_review`; an integrity success is not a tax
  compliance or filing approval.

`filing-package verify` still returns legacy `ok` only when integrity and
currentness both pass. Use `integrity` to verify old archives independently.
The confirmation is retained and reused for regeneration. This does not submit
returns, post transactions or modify historical filing snapshots.

The separate IRPF/IVA review exports now retain counterparty tax IDs and VAT
IDs, including FX rows linked to the source transaction. They remain review
exports, not accepted AEAT import files. Remaining acceptance includes historical
receipt-number evidence, full mandatory VAT/IRPF fields,
asset continuation/disposal evidence, reconciliation and completeness acceptance.
Do not substitute generated source-row IDs for invoice receipt registration
numbers. Preserve the original Xolo registers and their numbering evidence.

## Receipt registration (schema 29)

`receipt-register create-series --db PATH --payload series.json` records the
activity, calendar year, series code, first ordinal, justification and evidence.
The JSON fields are `business_activity_id`, `tax_year`, `code`, `first_number`,
`reason` and `evidence_ref`. A first ordinal above one requires retained evidence
of the predecessor register; this command does not certify that evidence.

`receipt-register assign --db PATH --payload receipt.json` accepts `document_id`,
`series_id`, `registered_on` and `evidence_ref`. Optional `ordinal` must be the
next consecutive number; optional `receipt_number` preserves the exact original
printed identifier, including leading zeroes. Without a printed identifier the
new identifier is `YEAR/SERIES/ORDINAL`. `receipt-register list --db PATH` reads
the register. Assignment is transactional and idempotent. Historical numbers
and series cannot be reset, edited or deleted through this interface.

Migration creates empty numbering tables and never backfills guessed numbers.
The supplier's invoice number and a generated source-row ID are not evidence of
a receipt registration number. Unknown historical numbers remain blank for
review. A changed source-document hash invalidates its registration in exports.

Separate review registers are produced for ordinary transactions as well as
explicit tax-date attributions. Invoices remain in books even when excluded from
both declarations; their original inclusion flags remain visible and IVA validation
still runs. They include operation and issue dates, receipt
series/year/number, VAT rate and classification, and correction links. The
`REQUIRED_FIELDS` worksheet and `register-content-check.json` identify missing
fields. `TOTALS` separates income, expenses and adjustments by quarter and for
the selected scope; blocked calculations have blank totals, never invented zeroes.
These checks do not certify source completeness or AEAT import compatibility.

For operational retention, archive original documents, a consistent database
copy, exporter source/release, dependency versions and reproduction instructions
alongside the package in a new private archive object. Verify an isolated restore.
The basic export command does not build the recovery bundle. The separate archive
command below retains it locally; remote retention remains the storage operator's
responsibility. Never overwrite a submitted version with a new calculation.

Sources: [IRPF register requirements](https://www.boe.es/buscar/act.php?id=BOE-A-2019-10487),
[received invoice register](https://sede.agenciatributaria.gob.es/Sede/iva/facturacion-registro/libros-registro-iva/libro-registro-facturas-recibidas.html).

## Continuing assets and recording disposal (schema 30)

`assets continue --db PATH --payload opening.json` accepts `asset_id`, `as_of`
(completed quarter end), `prior_year_minor`, `current_year_minor`, `evidence_ref`
and `reason`. Amounts are the reviewed business depreciation already accumulated,
in EUR cents. The asset must have its reviewed EUR basis, linear method, rate and
category. The continuation uses the original annual basis/rate and caps new
charges at the remaining balance. It preserves historical entries and does not
post anything. Existing later schedule entries require reconciliation; they are
never silently replaced. Recognition uses the existing reviewed posting command.

`assets dispose --db PATH --payload disposal.json` accepts `asset_id`, `disposed_on`,
`last_depreciable_on`, `reason` and `evidence_ref`. Both dates are explicit: the
command does not guess the last deductible day. It retains disposal evidence and
prevents subsequent depreciation posting or direct automatic schedule entries.
Already recognized depreciation after that date requires an explicit historical
correction. Disposal details appear in the asset CSV.

Final partial-period depreciation, disposal proceeds, gains/losses and applicable
VAT adjustments still require a reviewed settlement. Until supported and reviewed,
the affected package reports `asset_disposal_settlement_review_required`. Recording
disposal alone does not settle its tax consequences or change filed returns.

## Complete retained archive

`filing-package archive --db PATH --period YEAR-QN --out-dir NEW_DIRECTORY
--dependency-dir WHEEL_DIRECTORY` snapshots the committed database, exports books
from that copy, retains selected source documents and source-book files, and copies
the actual exporter package and matching installed dependency versions. Supply
`--legacy-path-map PATH` for old source paths, repeat `--source-file PATH` for
additional original evidence, and use `--supplemental-evidence PATH` for the
period's explicit empty-book confirmation.

Generation and verification run through the staged program copy, with its source
fingerprint retained in the manifest. Filing receipts and referenced snapshot
artifacts are included. Authority documents without period links are retained
conservatively and labeled as unperioded, rather than guessed into a tax quarter.
For imported books, a canonical row/document provenance hash is never treated as
the original file's byte hash. Unverified historical content identity remains an
explicit warning in the manifest; retaining it does not certify original evidence.

Prepare the dependency wheel directory for the same Python version and operating
system. Missing dependency versions, missing originals, altered reviewed document
hashes or an unreproducible export fail the operation and remove the incomplete new
directory. Existing versions are never reused. No network package installation or
production database migration is performed by the archive command.
Wheel platform and Python tags must be compatible with the capturing runtime.

Keep the returned manifest SHA-256 separately, then upload the new directory to
the configured encrypted storage without overwriting earlier versions.
`filing-package archive-verify --manifest PATH --expected-manifest-sha256 HASH`
verifies every retained file without the live database. Paths escaping the archive,
symlinks, unlisted files, duplicate inventory entries and altered bytes are rejected. Follow the
included `REPRODUCE.txt` in an isolated environment to install from retained wheels
without internet and rerun verification using the retained program and database.
Create the replay environment outside the retained directory and disable bytecode
writes so execution does not add unlisted files to the archive.
`runtime_replay_verified` stays false until that separate execution has actually
been performed; an intact archive is not proof of mandatory-book completeness.
