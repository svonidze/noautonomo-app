"""Derived from serve_fixture.py: serves the real application with richer ephemeral synthetic data for design review.

Synthetic values only; nothing here is a real record.
"""
from pathlib import Path
import hashlib
import signal
import sys
import tempfile

from autonomo_taxes.ui_assets import default_ui_root
from autonomo_taxes import local_web
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingServer, LocalWebConfig
from autonomo_taxes.storage_service import register_local_source_replica

SEED_ONLY = "--seed-only" in sys.argv


def terminate(_signum: int, _frame: object) -> None:
    raise SystemExit(0)


def attempt(label, fn):
    try:
        result = fn()
        print(f"seed ok: {label}", flush=True)
        return result
    except Exception as exc:  # demo only: keep the server usable when an optional seed fails
        print(f"seed skipped: {label}: {exc!r}", flush=True)
        return None


def seed(db: LedgerDB, archive_root: Path) -> None:
    supplier = attempt("supplier", lambda: db.upsert_counterparty(
        external_key="demo-supplier", display_name="Ejemplo Servicios Digitales SL", country_code="ES"))
    customer = attempt("customer", lambda: db.upsert_counterparty(
        external_key="demo-customer", display_name="Northwind Consulting OÜ", country_code="EE"))
    supplier_id = supplier["counterparty_id"] if supplier else None
    customer_id = customer["counterparty_id"] if customer else None

    # Q3 2026: a closed-looking quarter with posted rows.
    for i, (date, amount) in enumerate([("2026-07-03", 180000), ("2026-08-04", 180000), ("2026-09-02", 240000)]):
        db.add_transaction(external_key=f"demo-income-q3-{i}", period_key="2026-Q3", transaction_date=date,
            booking_date=date, entry_type="income", description=f"Consulting invoice 2026-{7+i:02d}",
            amount_minor=amount, amount_eur_minor=amount, direction="credit", lifecycle_status="posted",
            counterparty_id=customer_id)
    q3_expense = db.add_transaction(external_key="demo-expense-q3", period_key="2026-Q3", transaction_date="2026-07-20",
        booking_date="2026-07-20", entry_type="expense", description="Coworking July", amount_minor=24200,
        amount_eur_minor=24200, direction="debit", lifecycle_status="posted", counterparty_id=supplier_id)
    db.add_detailed_tax_treatment(transaction_id=q3_expense["transaction_id"], treatment_type="expense",
        tax_code="G03", taxable_base_minor=20000, vat_minor=4200, deductible_irpf_minor=20000,
        deductible_vat_minor=4200, include_modelo130=True, include_modelo303=True)

    # Q4 2026 (current quarter): one posted income, one approved expense, one needs_review expense,
    # one received document, one foreign-currency approved expense without FX.
    db.add_transaction(external_key="demo-income-q4", period_key="2026-Q4", transaction_date="2026-10-01",
        booking_date="2026-10-01", entry_type="income", description="Consulting invoice 2026-10",
        amount_minor=250000, amount_eur_minor=250000, direction="credit", lifecycle_status="posted",
        counterparty_id=customer_id)
    approved = db.add_transaction(external_key="demo-expense-approved", period_key="2026-Q4",
        transaction_date="2026-10-02", booking_date="2026-10-02", entry_type="expense",
        description="Coworking October", amount_minor=24200, amount_eur_minor=24200, direction="debit",
        lifecycle_status="approved", counterparty_id=supplier_id)
    db.add_detailed_tax_treatment(transaction_id=approved["transaction_id"], treatment_type="expense",
        tax_code="G03", taxable_base_minor=20000, vat_minor=4200, deductible_irpf_minor=20000,
        deductible_vat_minor=4200, include_modelo130=True, include_modelo303=True)

    review_doc = db.upsert_document(external_key="demo-review-document", document_type="expense_invoice",
        document_number="INV-2026-0912", issued_on="2026-10-03", period_key="2026-Q4",
        lifecycle_status="needs_review", currency="EUR")
    review_tx = db.add_transaction(external_key="demo-expense-review", period_key="2026-Q4",
        transaction_date="2026-10-03", booking_date="2026-10-03", entry_type="expense",
        description="Software subscription", amount_minor=12100, amount_eur_minor=12100, direction="debit",
        lifecycle_status="needs_review", document_id=review_doc["document_id"], counterparty_id=supplier_id)
    db.add_detailed_tax_treatment(transaction_id=review_tx["transaction_id"], treatment_type="invoice_review",
        tax_code="domestic_input", taxable_base_minor=10000, vat_minor=2100, deductible_irpf_minor=10000,
        include_modelo130=True)

    attempt("received document", lambda: db.upsert_document(external_key="demo-received-document",
        document_type="expense_invoice", document_number="R-77", issued_on="2026-10-04",
        period_key="2026-Q4", lifecycle_status="received", currency="EUR"))

    attempt("foreign expense without FX", lambda: db.add_transaction(external_key="demo-expense-usd",
        period_key="2026-Q4", transaction_date="2026-10-02", booking_date="2026-10-02", entry_type="expense",
        description="Cloud hosting (USD)", amount_minor=9900, currency="USD", amount_original_minor=9900,
        original_currency="USD", amount_eur_minor=None, direction="debit", lifecycle_status="approved"))

    attempt("asset", lambda: db.add_asset(asset_code="DEMO-LAPTOP-1", cost_minor=150000, currency="EUR",
        depreciation_method="linear", source_hash=hashlib.sha256(b"demo-asset").hexdigest(),
        placed_in_service_on="2026-07-15", useful_life_months=48, amortizable_base_minor=150000,
        business_use_ratio=1.0, annual_rate_basis_points=2500))

    # AEAT document with a synthetic receipt, as in the browser fixture.
    receipt = archive_root / "aeat" / "2026" / "submission_receipt" / "synthetic.pdf"
    receipt.parent.mkdir(parents=True)
    receipt.write_bytes(b"%PDF-synthetic demo receipt")
    digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
    document = db.upsert_document(document_type="aeat_official", document_number="0367000000001",
        issued_on="2026-04-03", currency="EUR", lifecycle_status="approved", source_hash=digest)
    document = db.set_document_storage(document["document_id"], source_path=str(receipt),
        mime_type="application/pdf", expected_row_version=document["row_version"])
    register_local_source_replica(db, document_id=document["document_id"], source_path=receipt,
        media_type="application/pdf", storage_root=archive_root)
    db.create_aeat_case_with_document(document_id=document["document_id"], source_hash=digest,
        title="Synthetic ROI registration", procedure_kind="roi_registration", procedure_code="G322",
        form_code="036", document_kind="submission_receipt", status="submitted",
        occurred_at="2026-04-03T10:20:30Z", requested_effective_on="2026-04-10",
        primary_reference="2026C3600000001A", submission_reference="2026C3600000001A",
        justificante_number="0367000000001", verification_code="SYNTHETICCSV0001",
        notes="Synthetic demo fixture", actor="demo-fixture")


def main() -> None:
    signal.signal(signal.SIGTERM, terminate)
    with tempfile.TemporaryDirectory(prefix="autonomo-design-demo-") as directory:
        root = Path(directory)
        package = Path(local_web.__file__).resolve().parent
        config = LocalWebConfig(project_root=root, database=root / "autonomo.sqlite",
            inbox_root=root / "Inbox", archive_root=root / "Evidence", cache_root=root / "cache",
            static_root=default_ui_root(), private_root=root, allow_test_ui=False)
        with LedgerDB.initialize(config.database) as db:
            seed(db, config.archive_root)
        if SEED_ONLY:
            print("SEED COMPLETE", flush=True)
            return
        app = LocalAccountingApp(config, session_token="synthetic-demo-session")
        print("serving http://127.0.0.1:8765", flush=True)
        with LocalAccountingServer(("127.0.0.1", 8765), app) as server:
            server.serve_forever()


if __name__ == "__main__":
    main()
