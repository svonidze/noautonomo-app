import json
import sqlite3
from unittest.mock import patch

import pytest

from autonomo_taxes.ledger_db import initialize, open_database, LATEST_SCHEMA_VERSION
from autonomo_taxes.receipt_register import create_series, register_receipt, list_receipts
from autonomo_taxes.tax_row_loader import load_tax_rows


def activity(db, key="main"):
    owner = db.upsert_taxpayer_profile(tax_id="TEST-OWNER", full_name="Example Taxpayer")
    return db.upsert_business_activity(taxpayer_profile_id=owner["taxpayer_profile_id"],
        activity_key=key, aeat_activity_code="A", aeat_activity_type="05", iae_section="2",
        iae_group_epigraph="763", description="Synthetic services", starts_on="2024-01-01",
        source_reference="synthetic:activity")["business_activity_id"]


def expense(db, number, activity_id, *, kind="expense"):
    party = db.upsert_counterparty(external_key="supplier", display_name="Example Supplier", tax_id="TEST-TAX")
    doc = db.upsert_document(external_key=number, document_type="expense_invoice",
        document_number=number, issued_on="2024-02-01", period_key="2024-Q1",
        lifecycle_status="posted", source_hash="synthetic-" + number, counterparty_id=party["counterparty_id"])
    tx = db.add_transaction(external_key=number, period_key="2024-Q1", transaction_date="2024-02-01",
        booking_date="2024-02-03", entry_type=kind, description="Synthetic service", amount_minor=12100,
        amount_eur_minor=12100, lifecycle_status="posted", document_id=doc["document_id"],
        counterparty_id=party["counterparty_id"], business_activity_id=activity_id)
    db.add_detailed_tax_treatment(transaction_id=tx["transaction_id"], treatment_type="invoice_review",
        tax_code="domestic_input", rate_basis_points=2100, taxable_base_minor=10000, vat_minor=2100,
        deductible_irpf_minor=10000, deductible_vat_minor=2100, include_modelo130=True, include_modelo303=True,
        aeat_invoice_type="F1", aeat_operation_key="01", aeat_reverse_charge=False)
    return doc["document_id"]


def series(db, activity_id, **changes):
    return create_series(db, **dict(business_activity_id=activity_id, tax_year=2024, code="RECEIVED",
        first_number=41, reason="Continue retained predecessor register", evidence_ref="synthetic:prior-register", **changes))


def assign(db, document_id, series_id, **changes):
    return register_receipt(db, **(dict(document_id=document_id, series_id=series_id,
        registered_on="2024-02-03", evidence_ref="synthetic:received") | changes))


def test_continues_preserved_numbers_idempotently_without_touching_financial_rows(tmp_path):
    with initialize(tmp_path / "ledger.sqlite") as db:
        aid = activity(db); doc = expense(db, "SUPPLIER-999", aid)
        original = [tuple(r) for r in db.connection.execute("SELECT * FROM transactions")]
        s = series(db, aid)
        first = assign(db, doc, s["series_id"], ordinal=41, receipt_number="Original-00041")
        assert assign(db, doc, s["series_id"]) == first
        assert series(db, aid) == s
        doc2 = expense(db, "SUPPLIER-003", aid)
        assert assign(db, doc2, s["series_id"])["ordinal"] == 42
        assert list_receipts(db)[0]["receipt_number"] == "Original-00041"
        assert tuple(db.connection.execute("SELECT * FROM transactions WHERE external_key='SUPPLIER-999'").fetchone()) == original[0]
        rows = load_tax_rows(db, 2024)
        row = next(r for r in rows if r.document_number == "SUPPLIER-999")
        assert row.receipt_number == "Original-00041"
        assert row.vat_rate_percent == 21 and row.reverse_charge is False
        assert row.operation_date.isoformat() == "2024-02-01"
        for statement in ("UPDATE receipt_registrations SET ordinal=99", "DELETE FROM receipt_registrations",
                          "UPDATE receipt_registration_series SET first_number=99"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.connection.execute(statement)
            db.connection.rollback()


def test_rejects_gaps_renumbering_dates_wrong_activity_and_income(tmp_path):
    with initialize(tmp_path / "ledger.sqlite") as db:
        aid = activity(db); s = series(db, aid); doc = expense(db, "one", aid)
        with pytest.raises(ValueError, match="consecutive"):
            assign(db, doc, s["series_id"], ordinal=43)
        assign(db, doc, s["series_id"])
        with pytest.raises(ValueError, match="immutable"):
            assign(db, doc, s["series_id"], receipt_number="replacement")
        doc2 = expense(db, "two", aid)
        with pytest.raises(ValueError, match="chronological"):
            assign(db, doc2, s["series_id"], registered_on="2024-02-02")
        with pytest.raises(ValueError, match="series year"):
            assign(db, doc2, s["series_id"], registered_on="2025-02-03")
        other = activity(db, "other")
        with pytest.raises(ValueError, match="another activity"):
            assign(db, expense(db, "other", other), s["series_id"])
        with pytest.raises(ValueError, match="expense transactions"):
            assign(db, expense(db, "income", aid, kind="income"), s["series_id"])
        with pytest.raises(ValueError, match="reset"):
            create_series(db, business_activity_id=aid, tax_year=2024, code="RECEIVED", first_number=1,
                          reason="Reset", evidence_ref="synthetic:reset")


def test_migration_preserves_existing_rows_and_does_not_invent_registration(tmp_path):
    path = tmp_path / "ledger.sqlite"
    with patch("autonomo_taxes.ledger_db.LATEST_SCHEMA_VERSION", 28), initialize(path) as db:
        aid = activity(db); expense(db, "legacy", aid)
        before = [tuple(r) for r in db.connection.execute("SELECT * FROM transactions")]
    with open_database(path, apply_migrations=True) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
        assert [tuple(r) for r in db.connection.execute("SELECT * FROM transactions")] == before
        assert list_receipts(db) == []
        assert load_tax_rows(db, 2024)[0].receipt_number == ""
        assert not db.connection.execute("PRAGMA foreign_key_check").fetchall()


def test_regular_exports_include_registration_and_require_missing_fields(tmp_path):
    from autonomo_taxes.books import write_accounting_books
    from test_tax_register_workbook import read_workbook

    with initialize(tmp_path / "ledger.sqlite") as db:
        aid = activity(db); doc = expense(db, "SUP-1", aid); s = series(db, aid)
        before = write_accounting_books(db, tmp_path / "before", period_key="2024-Q1")
        check = json.loads(before.files["register_content_check"].read_text())
        assert check["checked_fields_ready"] is False
        assert all(item["missing_fields"] == ["receipt_registration"] for item in check["blockers"])
        assigned = assign(db, doc, s["series_id"], receipt_number="Preserved-0041")
        exported = write_accounting_books(db, tmp_path / "after", period_key="2024-Q1")
        check = json.loads(exported.files["register_content_check"].read_text())
        assert check["checked_fields_ready"] is True
        assert check["source_completeness_verified"] is False
        assert check["totals"]["iva"]["quarters"]["2024-Q1"]["expense"]["vat_eur"] == "21"
        workbook = read_workbook(exported.files["tax_registers_xlsx"])
        for register in ("IRPF", "IVA"):
            assert workbook[register][0]["Receipt registration number"] == assigned["receipt_number"]
            assert workbook[register][0]["Document number"] == "SUP-1"
            assert workbook[register][0]["VAT rate (%)"] == 21
            assert workbook[register][0]["Receipt series"] == "RECEIVED"
        assert workbook["REQUIRED_FIELDS"] == []
        db.connection.execute("UPDATE documents SET source_hash='changed' WHERE document_id=?", (doc,))
        assert load_tax_rows(db, 2024)[0].receipt_number == ""


def test_concurrent_assignments_cannot_reuse_a_number(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "ledger.sqlite"
    with initialize(path) as db:
        aid = activity(db); s = series(db, aid)
        documents = [expense(db, "concurrent-" + str(i), aid) for i in range(2)]
    def run(document):
        with open_database(path) as db:
            return assign(db, document, s["series_id"])["ordinal"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, documents)) == [41, 42]


def test_cli_preserves_explicit_original_number(tmp_path, capsys):
    from autonomo_taxes.cli import main
    path = tmp_path / "ledger.sqlite"
    with initialize(path) as db:
        aid = activity(db); s = series(db, aid); doc = expense(db, "cli-source", aid)
    payload = tmp_path / "assignment.json"
    payload.write_text(json.dumps(dict(document_id=doc,series_id=s["series_id"],ordinal=41,
        receipt_number="000041",registered_on="2024-02-03",evidence_ref="synthetic:register")))
    args = ["receipt-register", "assign", "--db", str(path), "--payload", str(payload)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["receipt_number"] == "000041"
    assert main(args) == 0
    capsys.readouterr()
    assert main(["receipt-register", "list", "--db", str(path)]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_unreported_invoices_remain_in_books_with_inclusion_flags(tmp_path):
    from autonomo_taxes.books import write_accounting_books
    with initialize(tmp_path / "ledger.sqlite") as db:
        aid = activity(db); expense(db,"NONDEDUCTIBLE",aid)
        db.connection.execute("UPDATE tax_treatments SET include_modelo130=0,include_modelo303=0,deductible_irpf_minor=0,deductible_vat_minor=0")
        export = write_accounting_books(db,tmp_path / "books",period_key="2024-Q1")
        projection = json.loads(export.files["tax_registers"].read_text())
        assert projection["scope_type"] == "accounting_books"
        for register in projection["registers"].values():
            assert len(register["rows"]) == 1
            assert register["rows"][0]["document_number"] == "NONDEDUCTIBLE"
            assert register["rows"][0]["include_modelo130"] is False
            assert register["rows"][0]["include_modelo303"] is False
        assert json.loads(export.files["register_content_check"].read_text())["checked_fields_ready"] is False


def test_unreported_invoice_still_runs_iva_validation(tmp_path):
    from autonomo_taxes.books import write_accounting_books
    with initialize(tmp_path / "ledger.sqlite") as db:
        aid = activity(db); doc = expense(db,"UNCLASSIFIED",aid); s = series(db,aid)
        assign(db,doc,s["series_id"])
        db.connection.execute("UPDATE tax_treatments SET include_modelo130=0,include_modelo303=0,tax_code='unknown'")
        export = write_accounting_books(db,tmp_path / "books",period_key="2024-Q1")
        projection = json.loads(export.files["tax_registers"].read_text())
        assert projection["registers"]["iva"]["blocked"] is True
        assert json.loads(export.files["register_content_check"].read_text())["checked_fields_ready"] is False


@pytest.mark.parametrize("native", [False, True])
def test_depreciation_is_an_irpf_charge_not_a_new_received_invoice(tmp_path, native):
    from dataclasses import replace
    from datetime import date
    from decimal import Decimal
    from autonomo_taxes.register_content_check import check_register_content
    from autonomo_taxes.tax_attribution import register_projection

    with initialize(tmp_path / "ledger.sqlite") as db:
        aid = activity(db); expense(db, "ORIGINAL-PURCHASE", aid)
        original = load_tax_rows(db, 2024)[0]
        charge = replace(original, document_type="gastos_book", asset_id="reviewed-asset",
                         amortization_entry_id="recognized-entry" if native else "",
                         tax_code="amortization" if native else "historical_g03",
                         vat_eur=Decimal(0), deductible_vat_eur=Decimal(0),
                         include_modelo303=False)
        projection = register_projection([charge], year=2024, through=date(2024, 3, 31), for_books=True)
        assert len(projection["registers"]["irpf"]["rows"]) == 1
        assert projection["registers"]["iva"]["rows"] == []
        assert projection["registers"]["iva"]["blocked"] is False
        assert check_register_content(projection)["checked_fields_ready"] is True

        # An asset purchase still belongs in IVA and needs a receipt number.
        purchase = register_projection([replace(original, asset_id="reviewed-asset", include_modelo303=False)],
                                      year=2024, through=date(2024, 3, 31), for_books=True)
        assert len(purchase["registers"]["iva"]["rows"]) == 1
        assert any("receipt_registration" in b["missing_fields"]
                   for b in check_register_content(purchase)["blockers"])
        # The imported tax code alone cannot exempt an unlinked source row.
        unlinked = register_projection([replace(charge, asset_id="", amortization_entry_id="")],
                                      year=2024, through=date(2024, 3, 31), for_books=True)
        assert unlinked["registers"]["iva"]["blocked"] is True
        for vat_field in ("vat_eur", "deductible_vat_eur"):
            contradictory = register_projection([replace(charge, **{vat_field: Decimal(21)})],
                                                year=2024, through=date(2024, 3, 31), for_books=True)
            assert contradictory["registers"]["iva"]["blocked"] is True
            assert check_register_content(contradictory)["checked_fields_ready"] is False
