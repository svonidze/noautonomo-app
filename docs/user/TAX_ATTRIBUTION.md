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
`iva-register.csv` and `tax-registers.json` when reviewed attributions are present.
IRPF is cumulative from January 1; the IVA register covers the selected period.
The JSON contains an original-to-reviewed bridge, source document hashes and
references for the exchange results. The source `income.csv` and `expense.csv`
remain exports of the preserved transactions and treatments; the manifest labels
their basis explicitly.

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
