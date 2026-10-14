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
explicit tax-date attributions. They include operation and issue dates, receipt
series/year/number, VAT rate and classification, and correction links. The
`REQUIRED_FIELDS` worksheet and `register-content-check.json` identify missing
fields. `TOTALS` separates income, expenses and adjustments by quarter and for
the selected scope; blocked calculations have blank totals, never invented zeroes.
These checks do not certify source completeness or AEAT import compatibility.

For operational retention, archive original documents, a consistent database
copy, exporter source/release, dependency versions and reproduction instructions
alongside the package in a new private archive object. Verify an isolated restore.
The export command does not yet build that complete recovery bundle or enforce
remote retention. Never overwrite a submitted version with a new calculation.

Sources: [IRPF register requirements](https://www.boe.es/buscar/act.php?id=BOE-A-2019-10487),
[received invoice register](https://sede.agenciatributaria.gob.es/Sede/iva/facturacion-registro/libros-registro-iva/libro-registro-facturas-recibidas.html).
