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

## Choosing an export

| Need | Output and limits |
|---|---|
| Review the accounting and missing fields | Native CSV/JSON and `tax-registers-review.xlsx`; these are not AEAT import files. |
| Prepare separate cumulative IVA/IRPF files | `books aeat-separate-write`; follow the [separate XLSX procedure](TAX_ATTRIBUTION.md#separate-iva-and-irpf-xlsx). Requires the reviewed template, evidenced numbering and any reviewed operation-date decisions. |
| Retain evidence and the exporter for replay | `filing-package archive` in a new directory; see [Complete retained archive](#complete-retained-archive). A separate isolated replay is still required. |

The separate exporter writes values from January through the selected quarter,
with the appropriate C/D sheets. Unsupported attributed expenses, missing
numbering or stale evidence block generation. The unified template has a
separate contract and cannot represent independently attributed IRPF/IVA views.
Successful format checks do not establish that every source document is present,
that a return is ready, or that the real-data file has been accepted by AEAT.
The command does not upload records to AEAT. Existing filed snapshots and earlier
retained exports remain unchanged.

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

The normalized AEAT workbook also requires these evidenced receipt numbers for
invoice expenses and assets; non-invoice SF/AJ expenses are exempt. Asset receipt
numbers follow the linked acquisition invoice, even when the asset's own document
is an annual source-book record. Missing or stale numbers block XLSX generation.

Separate review registers are produced for ordinary transactions as well as
explicit tax-date attributions. Invoices remain in books even when excluded from
both declarations; their original inclusion flags remain visible and IVA validation
still runs. They include operation and issue dates, receipt
series/year/number, VAT rate and classification, and correction links. The
`REQUIRED_FIELDS` worksheet and `register-content-check.json` identify missing
fields. `TOTALS` separates income, expenses and adjustments by quarter and for
the selected scope; blocked calculations have blank totals, never invented zeroes.
These checks do not certify source completeness or AEAT import compatibility.

The AEAT attribution fallback uses the same books scope: invoice rows stay visible
even when their tax-return inclusion flags are false. Use those preserved flags
when reconciling book totals to filed returns.

For separate official imports, AEAT's V.18.02.2026 specification defines a C file
for IVA (`EXPEDIDAS`, `RECIBIDAS`) and a D file for IRPF (`INGRESOS`, `GASTOS`).
Both import files must cover January through the selected quarter and preserve
the shared column positions, leaving only the other tax's exclusive fields blank.
Common invoice base and VAT fields are not automatically blank in the IRPF file.
The native review workbook's quarter-only IVA view and review sheet layout do not
implement this import contract. Use the separate exporter above for the implemented C/D projection. Asset
mapping, mandatory content and date/amount reconciliation retain their own
acceptance requirements; merely renaming review tabs is insufficient. See the [official specification](https://sede.agenciatributaria.gob.es/static_files/Sede/Tema/IVA/Fact_registro/Libros_registro/Formato_Electronico_Comun_Libros_Registro_IVA_IRPF.pdf)
and [AEAT import FAQ](https://sede.agenciatributaria.gob.es/Sede/iva/facturacion-registro/preguntas-frecuentes.html).

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

Legacy assets with method `free` use their validated posted depreciation entries,
even when they predate versioned depreciation plans. Exports do not prorate these
charges as linear depreciation or repeat them in later quarters. Accumulated
amounts carry into later years. Missing or inconsistent recognition stays pending;
the exporter does not invent a write-off from the asset's rate or acquisition date.

For imported source-book assets, the stored acquisition basis and ratio may encode
the allocation of a multi-item purchase to one asset. Compare the effective basis
(stored basis multiplied by the ratio) with the source asset book before assuming
a mismatch. That importer-derived ratio alone does not establish personal versus
business use. Preserve the original invoice and the source-book allocation.

For inherited assets, prior annual controls establish the opening balance; the
current period uses uniquely linked, posted G03 charges through its end date.
This includes a reviewed acquisition carrying a G03 charge alongside invoice VAT.
Unposted derived schedules and the full-year source control are not additional
recognized expenses. Both the CSV and AEAT preview use this same calculation.
Missing opening years, ambiguous links, conflicting treatments and unhandled
IRPF date attributions remain pending. At year end, posted charges must reconcile
with the annual source control. A future continuation plan does not reclassify
the earlier historical book. For periods covered by a native plan, the AEAT
method must agree with that reviewed plan before its asset row can be exported.

The normalized AEAT asset export uses this effective basis for inherited assets
as well as native plans. Acquisition value remains the explicitly stored cost;
the exporter does not reconstruct it from a rounded VAT rate. An incorrect legacy
cost requires a separately evidenced accounting correction, not an export-time
substitution. Effective basis above that stored cost blocks the row.

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
