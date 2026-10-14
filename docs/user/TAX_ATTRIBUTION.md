# Separate IRPF and IVA recognition

An invoice's issue date, a source transaction's date and the dates used for taxes
can differ. A reviewed tax attribution supplies the dates and EUR amounts for
IRPF (130 and business support for 100) and IVA (303 and its 390 reconciliation).
It does not change the invoice, source transaction, its original currency or a
filed return. Rows without an attribution retain their existing behavior.

The application shows the saved dates, bases, pending IVA and realized exchange
results in transaction review and posted transaction detail. Source document
turnover remains a document-date view. The tax cards and annual business figures
use the reviewed tax dates.

## Operator review

A source transaction and its document must already be reviewed. Attribution does
not post or approve the source. Ordinary actuals still require posting; accepted
historical-book policy and explicitly requested approved forecasts are preserved.

Prepare a private packet using the installed compatible CLI:

```sh
autonomo-tax tax-attribution prepare TRANSACTION_ID --db /private-accounting/ledger.sqlite --out /private-accounting/review.json
```

Keep `transaction_id`, `expected_revision` and `source_snapshot_hash`. Review the
complete decision, `reason` and `evidence_ref`. Supply references to the invoice,
service-period evidence and reviewed currency observations. The application does
not infer the correct legal timing or fetch exchange rates during calculation.

An independently invented decision example:

```json
{
  "irpf": {
    "date": "2025-09-22",
    "income_minor": 92000,
    "deductible_minor": 0,
    "withholding_minor": 0,
    "evidence_ref": "document:TEST-ACCRUAL-A"
  },
  "iva": {
    "date": "2025-11-10",
    "base_minor": null,
    "vat_minor": null,
    "deductible_minor": null,
    "pending_reason": "Await reviewed currency observation",
    "evidence_ref": "document:TEST-VAT-A"
  },
  "fx": []
}
```

Money is integer EUR cents, never binary floating-point input. An income uses
`income_minor`; an expense uses `deductible_minor`. An attribution cannot enable
a tax excluded by the reviewed source treatment. Credit notes continue through
the existing correction workflow.

A null IRPF or IVA object keeps that tax's source date and amounts. A pending IVA
object has a date and reason but **all three amounts must be null**. It blocks a
calculation only when that IVA date belongs to the selected window. It does not
block an earlier quarter or a calculation of IRPF. Replace it with verified
amounts in a new revision when the evidence becomes available.

Realized receivable exchange differences belong to the source income transaction:

```json
{
  "event_key": "TEST-SETTLEMENT-A",
  "date": "2025-10-14",
  "amount_minor": -300,
  "evidence_ref": "document:TEST-SETTLEMENT-PROOF"
}
```

Each event key is unique within its reviewed revision. Positive values produce
IRPF income; negative values produce an IRPF expense. Neither creates another
invoice, principal income or payment. Keep event keys stable when revising a
review. The latest revision replaces the previous event set; revisions are not
summed together.

Apply the complete packet and refresh the affected calculations:

```sh
autonomo-tax tax-attribution apply --db /private-accounting/ledger.sqlite --input /private-accounting/review.json --actor operator
```

The CLI returns `refresh_required`. Refresh calculations in the application, or
run `period dashboard` with the configured cache output directory. The authenticated
API supports `GET /api/tax-attributions/{transaction_id}` and a same-origin JSON
`POST` to that route; GET returns the review packet and current decision. POST
attempts refreshes and reports `refresh_pending` separately from the committed
review. Old cached figures are rejected after attribution or source changes.

A repeated identical packet returns the existing revision. A changed packet with
an old revision, or one prepared before source facts changed, is rejected. Reload
and review it; do not replace its snapshot hash by hand. A complete empty decision
(`irpf: null`, `iva: null`, `fx: []`) withdraws the attribution in a new revision.

## Historical corrections

An attribution affecting a closed or filed period requires explicit
`historical_correction: true` and a reason. It creates a **corrected calculation
view**, not an amendment or replacement of a filed snapshot. The old return and
its evidence remain unchanged. Determining whether an earlier return needs a
separate correction remains an independent accounting decision.

## Registers and other forms

`period prepare` and accounting-book exports include `irpf-register.csv`,
`iva-register.csv`, `tax-registers.json` and `tax-registers-review.xlsx` when
reviewed attributions are present.
IRPF is cumulative from January 1; the IVA register covers the selected period.
The JSON contains an original-to-reviewed bridge, source document hashes and
references for the exchange results. The source `income.csv` and `expense.csv`
remain exports of the preserved transactions and treatments; the manifest labels
their basis explicitly.

The XLSX is a native review export. Its `REVIEW` sheet gives each tax's date
window, completeness and totals before relief and form-specific adjustments.
`IRPF` and `IVA` contain the effective rows used by the calculations. `SOURCES`
contains each preserved source transaction once; `ATTRIBUTIONS` links reviewed
dates, bases and FX to that source. Their amounts are traceability values and
must not be added to the tax register totals. Document identifiers, issue dates,
hashes and evidence references remain available in the workbook.
The attribution sheet retains the separate IRPF and IVA evidence references,
alongside the common review reference and evidence for each FX event.

`BLOCKERS` shows every affected row's tax date, quarter and reason. A blocked
register remains empty, its totals stay blank and the other tax can still be
complete. Pending IVA amounts and stale reviewed amounts stay blank in the
attribution sheet. IVA completeness also requires the same classification and
rate validation as Modelo 303, including ordinary rows mixed into the export.
Sources and attribution bridges retain every selected operation even when a
different row blocks that tax's monetary register. An annual export covers
January through December for both
taxes. The filing manifest records the workbook hash, scopes, completeness and
`aeat_import_supported: false`. It can be regenerated and verified without
changing the source records or filed snapshots.

An existing final snapshot does not establish that the current attributed view
matches the filed return. Such packages remain `draft_review` with
`tax_attribution_snapshot_review_required`: the exporter does not bind the
current attributed state to that snapshot, so it requires independent review.
This includes corrections that remove an operation from the selected tax window.
The check uses retained attribution revisions and FX dates in the IRPF
year-to-date and IVA period windows, even after the latest decision removes the
source from that year's loader or withdraws its attribution. A period outside
that history retains its previous readiness behavior.
Complete registers and successful package verification certify export
reproducibility, not replacement of the filed snapshot.

Generate the native books with the installed compatible CLI, using a private
output directory:

```sh
autonomo-tax books build --db /private-accounting/ledger.sqlite --period 2025-Q4 --out-dir /private-accounting/books
```

The [AEAT 2026 specification](https://sede.agenciatributaria.gob.es/static_files/Sede/Tema/IVA/Fact_registro/Libros_registro/Formato_Electronico_Comun_Libros_Registro_IVA_IRPF.pdf)
permits separate official books. Their normalized columns and legal recognition
rules differ from this native review workbook. Mapping all reviewed dates and FX
to those columns has not been validated. Do not upload the review workbook as
an AEAT import file.

The unified template downloaded from AEAT on 14 October 2026 is pinned to SHA-256
`1011b811d71e5074dea08b946d2be2189343e419bcb668441d6e74fd1f639008`.
Its received-invoice total formula includes the reverse-charge flag when deciding
whether to add IVA to the invoice total. The previous template remains identified
in the contract for historical evidence; new bytes are not accepted automatically.
Two synthetic expense rows were generated through the existing writer, recalculated
in LibreOffice, and accepted by the official validator (`resultado.valido=true`).
The ordinary-IVA and reverse-charge totals were respectively 121 and 100 for a
base of 100 and IVA of 21. The response also retained a generic warning to check
column layout and the income-sheet name. This checks these two format cases, not every supported tax operation,
source completeness, or the separate attributed-register format. Recalculation is
still required before validation; the writer itself does not evaluate formulas.

The unified AEAT workbook has one operation date and amount set. Its projection
is blocked with `separate_tax_registers_required` for attributed views; it never
exports stale rows as corrected figures or invents duplicate invoices. Separate
registers and calculated form values remain available. They are review exports,
not a claim of an AEAT-importable workbook or permission to file.

349, 347 and withholding forms retain source-date semantics and say so in their
warnings when attributions are present. Their timing is not silently changed by
an IRPF/IVA review. RETA business-income support uses the IRPF projection.

## Storage and release

Schema 28 adds append-only attribution revisions and their FX-event children.
The migration does not populate decisions or modify accounting records. Future
or historical decisions must be entered through the reviewed operation. Follow
the normal backup, migration and rollback runbooks for deployment; do not open a
schema-28 database with an older application. All operational packets, originals
and replay data stay outside Git.
