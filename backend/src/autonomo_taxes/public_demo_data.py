"""Create one persistent, synthetic public-demo ledger; never run during serving.

Only explicit public reference input is read. The output is an empty/new directory
outside Git, containing the SQLite database, an explanatory PDF and its integrity manifest.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import itertools
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from . import expense_workflow, ledger_db, review_packet, storage_service
from .ledger_db import LedgerDB, LATEST_SCHEMA_VERSION
from .reta import parse_reta_table

AS_OF = "2026-09-30"
PERIODS = ("2026-Q1", "2026-Q2", "2026-Q3")
DEFAULT_PERIOD = "2026-Q3"
DATABASE_NAME = "demo.sqlite"
DOCUMENT_NAME = "demo-document.pdf"
MANIFEST_NAME = "demo-manifest.json"
CANONICAL_DOCUMENT = "/data/" + DOCUMENT_NAME
STAMP = AS_OF + "T12:00:00Z"


def uid(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, "https://example.invalid/noa-demo/" + key))


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


class _SeedDate(date):
    @classmethod
    def today(cls):
        return cls.fromisoformat(AS_OF)


@contextmanager
def _seed_identity():
    # Offline initialization only: native accounting operations get stable IDs and dates.
    sequence = itertools.count()
    with ExitStack() as stack:
        stack.enter_context(patch.object(ledger_db, "_new_id", side_effect=lambda: uid("generated-" + str(next(sequence)))))
        stack.enter_context(patch.object(expense_workflow, "uuid4", side_effect=lambda: uuid5(NAMESPACE_URL, "demo-audit-" + str(next(sequence)))))
        for module in (ledger_db, review_packet, storage_service):
            stack.enter_context(patch.object(module, "_utc_now", return_value=STAMP))
        stack.enter_context(patch.object(expense_workflow, "stamp", return_value=STAMP))
        for module in (expense_workflow, review_packet):
            stack.enter_context(patch.object(module, "date", _SeedDate))
        yield


def _create_document(db: LedgerDB, *, key, party, kind,
                     number, day, description, base, vat, status):
    document_id = uid("document-" + key)
    provenance = json.dumps({"synthetic": True, "key": key, "number": number,
                            "date": day, "description": description, "base": base, "vat": vat}, sort_keys=True)
    # Documents describe distinct scenarios. The one PDF is only their shared display file.
    row = db.upsert_document(document_id=document_id, external_key="demo-document-" + key,
        counterparty_id=party["counterparty_id"], document_type="aeat_official" if kind == "aeat" else kind + "_invoice",
        document_number=number, issued_on=day, period_key=f"2026-Q{(int(day[5:7])+2)//3}",
        currency="EUR", total_minor=None if base is None else base + (vat or 0), lifecycle_status=status,
        source_hash=digest(provenance))
    return db.set_document_storage(document_id, source_path=CANONICAL_DOCUMENT, mime_type="application/pdf",
                                   expected_row_version=row["row_version"])


def _seed_ledger(db: LedgerDB, work_root: Path, reta_table: bytes) -> None:
    profile = db.upsert_taxpayer_profile(tax_id="DEMO-NOT-A-TAX-ID", full_name="Marina Example (demo)")
    activity = db.upsert_business_activity(taxpayer_profile_id=profile["taxpayer_profile_id"],
        activity_key="demo-development", aeat_activity_code="A", aeat_activity_type="05", iae_section="2",
        iae_group_epigraph="763", description="Software development", starts_on="2026-01-01",
        source_reference="Synthetic source, no official validity")
    db.import_reta_table(reta_table)
    db.add_reta_base_election(effective_from="2026-01-01", regime="base", monthly_base_minor=120000,
        worker_kind="individual", source_reference="Synthetic source, no official validity")
    parties = {}
    for name in ("Atlas Demo Studio", "Brisa Demo Lab", "Nube Demo Software", "Patio Demo Workspace", "Demo Social Security", "Demo Equipment Store"):
        parties[name] = db.upsert_counterparty(external_key="demo-" + name, display_name=name,
            country_code="ES", tax_id="DEMO-NOT-A-TAX-ID-" + str(len(parties)+1))
    def transaction(key, month, day, kind, name, description, base, rate=21, status="posted", concept="G24"):
        vat = base * rate // 100
        date_text = f"2026-{month:02d}-{day:02d}"
        document = _create_document(db, key=key, party=parties[name], kind=kind,
            number="DEMO-" + key.upper(), day=date_text, description=description, base=base, vat=vat, status=status)
        row = db.add_transaction(transaction_id=uid("transaction-"+key), external_key="demo-"+key,
            period_key=f"2026-Q{(month+2)//3}", transaction_date=date_text, booking_date=date_text, entry_type=kind,
            description=description, amount_minor=base+vat, amount_original_minor=base+vat, original_currency="EUR",
            amount_eur_minor=base+vat, lifecycle_status=status, direction="credit" if kind=="income" else "debit",
            document_id=document["document_id"], counterparty_id=parties[name]["counterparty_id"], business_activity_id=activity["business_activity_id"])
        db.add_detailed_tax_treatment(transaction_id=row["transaction_id"], treatment_type="invoice_review",
            tax_code="domestic_output" if kind=="income" else "domestic_input", taxable_base_minor=base, vat_minor=vat,
            deductible_irpf_minor=base if kind=="expense" else None, deductible_vat_minor=vat if kind=="expense" else None,
            rate_basis_points=rate*100, deductible_ratio=1, withholding_minor=0,
            aeat_invoice_type="F1", aeat_operation_key="01", aeat_operation_qualification="S1", aeat_reverse_charge=False,
            aeat_expense_concept=concept if kind=="expense" else None, vat_investment_good=False,
            include_modelo130=True, include_modelo303=bool(rate), notes="Synthetic source, no official validity")
        if status == "needs_review":
            db.add_validation_issue(period_key=row["period_key"] if "period_key" in row else f"2026-Q{(month+2)//3}",
                issue_code="transaction_tax_review", severity="warning", message="Review the invoice against its example source",
                subject_table="transactions", subject_id=row["transaction_id"], blocking=True, source_hash=digest(key))
        return row
    for month in range(1, 10):
        transaction(f"{month}-atlas", month, 5, "income", "Atlas Demo Studio", "Application development", 260000+month*8500)
        transaction(f"{month}-brisa", month, 12, "income", "Brisa Demo Lab", "Product consulting", 95000+month*2500,
                    status="needs_review" if month==9 else "posted")
        transaction(f"{month}-software", month, 16, "expense", "Nube Demo Software", "Work software subscription", 12000+month*300,
                    status="needs_review" if month==9 else "posted")
        transaction(f"{month}-workspace", month, 21, "expense", "Patio Demo Workspace", "Workspace rental", 31000,
                    status="approved" if month==9 else "posted", concept="G06")
        transaction(f"{month}-reta", month, 28, "expense", "Demo Social Security", "Social security contribution", 32000, rate=0, concept="G45")
    # Native reviewed equipment workflow: schedule and recognition amounts come from the engine.
    equipment = transaction("laptop", 1, 15, "expense", "Demo Equipment Store", "Professional laptop", 180000, status="needs_review", concept="G03")
    draft = expense_workflow.get_draft(db, equipment["transaction_id"])
    payload = deepcopy(draft["payload"])
    payload["decision"].update(outcome="approve", reason="Reviewed example business expense", business_purpose="Used exclusively for the example professional activity",
        document_valid=True, asset_decision="current_expense", asset_id=None, counterparty_changes={})
    payload["decision"]["tax_treatment"].update(tax_code="domestic_input", aeat_invoice_type="F1", aeat_operation_key="01",
        aeat_operation_qualification="S1", aeat_exemption_code=None, aeat_reverse_charge=False, vat_investment_good=False,
        aeat_expense_concept="G03", rate_basis_points=2100, deductible_ratio=1, taxable_base_minor=180000, vat_minor=37800,
        deductible_irpf_minor=180000, deductible_vat_minor=37800, withholding_minor=0, include_modelo130=True, include_modelo303=True, include_modelo347=False,
        rule_version_id=None, notes="Reviewed against the synthetic original")
    for resolution in payload["decision"]["issue_resolutions"]:
        resolution.update(action="resolve", reason="Reviewed against the synthetic original")
    payload["asset"] = dict(description="Professional laptop", basis_minor=180000, business_use_ratio=1.0,
        annual_rate_basis_points=2500, placed_in_service_on="2026-01-15", method="linear", new_equipment=True, aeat_asset_type="23")
    payload["change_reason"] = "Reviewed against the synthetic original"
    saved = expense_workflow.save_draft(db, equipment["transaction_id"], payload=payload, expected_version=draft["draft_version"],
        source_snapshot_hash=draft["source_snapshot_hash"], actor="demo-generator")
    preview = expense_workflow.preview(db, equipment["transaction_id"], expected_version=saved["draft_version"])
    result = expense_workflow.confirm_and_post(db, equipment["transaction_id"], expected_version=saved["draft_version"],
        preview_token=preview["preview_token"], request_id=uid("laptop-request"), actor="demo-generator", archive_root=work_root)
    for entry in db.connection.execute("SELECT * FROM amortization_entries WHERE asset_id=? ORDER BY recognition_on", (result["asset_id"],)).fetchall():
        if entry["recognition_on"] <= AS_OF and not entry["recognition_transaction_id"]:
            expense_workflow.recognize_period(db, entry["amortization_entry_id"], expected_version=entry["row_version"],
                request_id=uid("recognize-"+entry["period_id"]), actor="demo-generator", archive_root=work_root)
    _create_document(db, key="received", party=parties["Nube Demo Software"], kind="expense",
        number="DEMO-RECEIVED", day="2026-09-29", description="Example invoice awaiting extraction", base=5000, vat=1050, status="received")
    official = _create_document(db, key="aeat", party=parties["Demo Social Security"], kind="aeat", number="DEMO-ROI-036",
        day="2026-04-03", description="Example ROI registration", base=None, vat=None, status="approved")
    db.create_aeat_case_with_document(document_id=official["document_id"], source_hash=official["source_hash"],
        title="Example ROI registration", procedure_kind="roi_registration", procedure_code="G322", form_code="036",
        document_kind="submission_receipt", status="submitted", occurred_at="2026-04-03T10:00:00Z",
        requested_effective_on="2026-04-10", primary_reference="DEMO-ROI-2026", submission_reference="DEMO-ROI-2026",
        justificante_number="DEMO-036", verification_code="DEMO-NOT-A-CSV", notes="Synthetic source, no official validity", actor="demo-generator")
    for period in PERIODS:
        for form in ("130", "303"):
            db.add_obligation(period_key=period, obligation_code=form, determination="due", filing_status="due",
                due_on={"2026-Q1":"2026-04-20", "2026-Q2":"2026-07-20", "2026-Q3":"2026-10-20"}[period],
                notes="Example tax obligation, not a submitted return", explanation="Example tax obligation, not a submitted return")


def _seed_filing_snapshots(db: LedgerDB) -> None:
    from .dashboard_service import build_dashboard_from_db

    for period in PERIODS[:2]:
        dashboard = build_dashboard_from_db(db, period_key=period, as_of=date.fromisoformat(AS_OF))
        for form in ("130", "303"):
            values = dashboard["tax_arithmetic_preview"]["posted_actual"]["modelo" + form]["values"]
            # The native builder returns Decimal amounts; snapshots use the same JSON representation as the CLI.
            values = json.loads(json.dumps(values, default=str))
            filed_on = "2026-04-18" if period == "2026-Q1" else "2026-07-18"
            fingerprint = digest("demo-filing-" + period + form + json.dumps(values, sort_keys=True))
            snapshot = db.create_filing_snapshot(period,
                payload={"form": form, "values": values, "baseline_kind": "synthetic_demo"},
                status="baseline", filed_on=filed_on, form_code=form,
                submission_reference=f"DEMO-{form}-{period}", justificante_number=f"DEMO-{form}",
                source_reference=CANONICAL_DOCUMENT, source_hash=fingerprint, snapshot_hash=fingerprint)
            # A filing ID also owns a real catalogue attachment; unknown IDs never receive a fallback PDF.
            db.upsert_document(document_id=snapshot["filing_snapshot_id"],
                external_key="demo-filing-document-" + period + form, document_type="tax_report",
                document_number=f"DEMO-{form}-{period}", issued_on=filed_on, period_key=period,
                total_minor=int(Decimal(values["19" if form == "130" else "71"]) * 100),
                lifecycle_status="approved", source_hash=digest("document-" + fingerprint))
            obligation = next(row for row in db.list_obligations(period_key=period) if row["obligation_code"] == form)
            db.add_obligation(period_key=period, obligation_code=form, determination="due", filing_status="filed",
                filed_at=filed_on, due_on=obligation["due_on"], notes="Synthetic source, no official validity",
                explanation="Synthetic source, no official validity", expected_row_version=obligation["row_version"])


def _attach_shared_pdf(db: LedgerDB, document_path: Path) -> None:
    # Replace only newly seeded physical evidence, retaining logical source hashes and accounting history.
    db.connection.execute("DELETE FROM document_attachments")
    db.connection.execute("DELETE FROM file_replicas")
    db.connection.execute("DELETE FROM files")
    db.connection.execute("DELETE FROM storage_backends")
    db.connection.execute("""UPDATE documents SET source_path=?,mime_type='application/pdf'
        WHERE source_path IS NOT ? OR mime_type IS NOT 'application/pdf'""", (CANONICAL_DOCUMENT, CANONICAL_DOCUMENT))
    file = db.upsert_file(content_sha256=digest(document_path.read_bytes()),
                          byte_size=document_path.stat().st_size, media_type="application/pdf", file_id=uid("shared-pdf"))
    backend = db.upsert_storage_backend(backend_key="public_demo", display_name="Public demo document",
        driver_key="filesystem", provider_key="local", access_mode="read_only",
        config={"schema_version": 1, "root": "/data"}, storage_backend_id=uid("storage"))
    db.register_file_replica(file_id=file["file_id"], storage_backend_id=backend["storage_backend_id"],
        provider_locator=DOCUMENT_NAME, is_primary=True, last_verified_at=STAMP, file_replica_id=uid("replica"))
    for row in db.connection.execute("SELECT document_id FROM documents ORDER BY document_id"):
        db.attach_file_to_document(document_id=row["document_id"], file_id=file["file_id"],
            attachment_role="source", display_name=DOCUMENT_NAME, document_attachment_id=uid("attachment-" + row["document_id"]))
    db.connection.commit()


def prepare_demo(root: Path, *, reference_root: Path = Path("/app/reference")) -> dict:
    """Initialize a new release directory once. Existing output is never overwritten."""
    root = Path(root)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("Demo output must be an absolute, non-symlink directory outside Git")
    root = root.resolve()
    if any((parent / ".git").exists() for parent in (root, *root.parents)):
        raise ValueError("Generated demo data must remain outside Git")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise FileExistsError("Demo output must be new or empty; refusing to overwrite data")
    reference_root = Path(reference_root)
    if not reference_root.is_absolute():
        raise ValueError("The public reference directory must be absolute")
    reta_table = (reference_root / "reta/2026.json").read_bytes()
    parse_reta_table(reta_table)
    root.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also rejects concurrent initialization of the same empty directory.
    marker = root / ".initializing"
    with marker.open("x"):
        pass
    document_path = root / DOCUMENT_NAME
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })}),
    })
    content = DecodedStreamObject()
    content.set_data(b"""BT
/F1 22 Tf 72 754 Td
(NoAutonomo.ai demo document) Tj
/F1 11 Tf 0 -34 Td 18 TL
(This file is a shared example document for the public demo.) Tj T*
(Every document link in the demo opens this same PDF.) Tj T* T*
(The demo uses fictional accounting data and is read-only.) Tj T*
(This PDF contains no real invoices, receipts or personal information.) Tj T* T*
(Use it to try opening, previewing and downloading a document.) Tj T*
(Viewing this file does not change any accounting or tax records.) Tj
ET
""")
    page.replace_contents(content)
    writer.add_metadata({"/Title": "Demo document"})
    with document_path.open("xb") as stream:
        writer.write(stream)
    with tempfile.TemporaryDirectory(prefix=".seed-", dir=root) as work, _seed_identity():
        with LedgerDB.initialize(root / DATABASE_NAME) as db:
            db.connection.execute("PRAGMA journal_mode=DELETE")
            _seed_ledger(db, Path(work), reta_table)
            _seed_filing_snapshots(db)
            _attach_shared_pdf(db, document_path)
            if db.connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Synthetic database failed its integrity check")
            if db.connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Synthetic database has invalid foreign keys")
            db.connection.commit()
            db.connection.execute("VACUUM")
    manifest = {"format": 1, "mode": "public_demo", "synthetic": True,
                "schema_version": LATEST_SCHEMA_VERSION, "as_of": AS_OF,
                "default_period": DEFAULT_PERIOD, "periods": list(PERIODS),
                "sha256": {name: digest((root / name).read_bytes()) for name in (DATABASE_NAME, DOCUMENT_NAME)}}
    with (root / MANIFEST_NAME).open("x") as stream:
        stream.write(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    marker.unlink()
    if {path.name for path in root.iterdir()} != {DATABASE_NAME, DOCUMENT_NAME, MANIFEST_NAME}:
        raise ValueError("Unexpected files in prepared demo directory")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New or empty absolute data directory outside Git")
    parser.add_argument("--reference-root", type=Path, default=Path("/app/reference"), help="Public reference directory (default: /app/reference)")
    args = parser.parse_args(argv)
    try:
        manifest = prepare_demo(args.output, reference_root=args.reference_root)
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(f"Prepared synthetic demo data: {args.output} ({manifest['default_period']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
