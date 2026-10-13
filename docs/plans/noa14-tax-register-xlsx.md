# NOA-14: separate tax register XLSX

## Official contract checked on 2026-10-13

AEAT's [2026 electronic-book specification](https://sede.agenciatributaria.gob.es/static_files/Sede/Tema/IVA/Fact_registro/Libros_registro/Formato_Electronico_Comun_Libros_Registro_IVA_IRPF.pdf),
section 2 (page 5), permits separate IVA and IRPF files or a unified file.
Its [record design](https://sede.agenciatributaria.gob.es/static_files/AEAT/LSI.xlsx)
was downloaded for inspection, with SHA-256
`d35e92f2ebcdfba9d2d836da13f677758c27fd04f3903280accfd18ea6db497d`.
The design has one declaration year/period and operation date per row.
The issued-income sheet's validation 11 assigns the IVA period when unified
IRPF/IVA periods differ. Validation 12 retains the common columns for separate
books and leaves the other tax's exclusive columns blank. Validation 13 ties
computable income to the invoice base except for specified operations and
cash recognition. Cash dates are conditional legal fields, not arbitrary tax
date overrides. The design workbook and PDF still use different filename codes.

Separate AEAT books are therefore a possible future mapping, not a demonstrated
import contract for all current attributions. The current review decision does
not select a legal cash-recognition regime or AEAT concepts for FX. Pending IVA
has no confirmed amounts. Substituting dates, synthesizing invoice amounts or
duplicating an invoice to accommodate FX would not prove compatibility.

## Implementation boundary

1. Export `tax-registers-review.xlsx` with separate native IRPF/IVA sheets from
   the existing calculation projection. Keep the AEAT unified export blocked.
2. Match IRPF's year-to-date window and IVA's selected period (a year when an
   annual period is selected). Expose dates, identifiers, document hashes and
   evidence. Include original source rows once in a traceability sheet, without
   adding their amounts to the tax totals.
3. Preserve the existing all-or-nothing blocked-register behavior. Show each
   blocked row's tax date, quarter and reason. Pending/stale reviewed amounts
   and blocked totals stay blank. The other tax remains available.
4. Record native review support, completeness, scopes and lack of AEAT import
   support in projection metadata and filing-package manifests. Deterministic
   XLSX bytes allow package verification after regeneration.
5. Test late invoices, year/quarter boundaries, independent bases, FX gains and
   losses, pending/stale decisions, source/snapshot immutability, literal-text
   cells and unattributed export compatibility. Open and render a synthetic
   workbook. No actual accounting records or generated binary fixtures enter Git.

Official import validation is required before an AEAT import claim. This review
workbook makes no such claim and must not be uploaded as a normalized AEAT book.
