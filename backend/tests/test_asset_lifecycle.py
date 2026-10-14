from datetime import date
import json
from uuid import uuid4
from unittest.mock import patch

import pytest

from autonomo_taxes.asset_lifecycle import continue_asset, record_disposal
from autonomo_taxes.depreciation import schedule, native_year_summary
from autonomo_taxes.expense_workflow import recognize_period, ExpenseWorkflowError
from autonomo_taxes.ledger_db import open as open_db
from autonomo_test_support.expense_workflow import setup_draft
from test_expense_workflow import commit


class AcquisitionDay(date):
    @classmethod
    def today(cls):
        return cls(2024, 1, 2)


def imported_asset(tmp_path):
    fixture, draft = setup_draft(tmp_path, method="linear", transaction_date="2024-01-02")
    with open_db(fixture["database"]) as db:
        with patch("autonomo_taxes.expense_workflow.date", AcquisitionDay):
            result = commit(db, fixture, draft, tmp_path)
        asset_id = result["asset_id"]
        # Model an imported asset: retain acquisition evidence, substitute historical annual control.
        db.connection.execute("DELETE FROM amortization_entries WHERE asset_id=?", (asset_id,))
        db.connection.execute("DELETE FROM asset_depreciation_plans WHERE asset_id=?", (asset_id,))
        db.connection.commit()
        db.add_amortization_entry(asset_id=asset_id, period_key="2024", amount_minor=2493,
            entry_kind="annual_evidence", source_hash="synthetic:annual-control")
    return fixture, asset_id


def opening(db, asset_id, **changes):
    return continue_asset(db, **(dict(asset_id=asset_id, as_of="2024-12-31", prior_year_minor=0,
        current_year_minor=2493, evidence_ref="synthetic:opening-control", reason="Reviewed historical continuation") | changes))


def test_opening_continues_full_basis_rate_without_rewriting_history(tmp_path):
    import csv
    from autonomo_taxes.books import write_accounting_books
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture["database"]) as db:
        before = dict(db.connection.execute("SELECT * FROM amortization_entries WHERE asset_id=?", (asset_id,)).fetchone())
        result = opening(db, asset_id)
        assert opening(db, asset_id) == result
        assert dict(db.connection.execute("SELECT * FROM amortization_entries WHERE amortization_entry_id=?", (before["amortization_entry_id"],)).fetchone()) == before
        rows = db.connection.execute("SELECT * FROM amortization_entries WHERE asset_id=? AND recognition_on IS NOT NULL ORDER BY recognition_on", (asset_id,)).fetchall()
        assert sum(r["amount_minor"] for r in rows) == 7507
        assert rows[0]["amount_minor"] == 616
        assert native_year_summary(db.connection, asset_id, 2024) is None
        entry = rows[0]
        posted = recognize_period(db, entry["amortization_entry_id"], expected_version=entry["row_version"],
            request_id=str(uuid4()), actor="synthetic-user", archive_root=tmp_path / "archive")
        assert posted["posted"] is True
        summary = native_year_summary(db.connection, asset_id, 2025, through_date="2025-03-31")
        assert summary["prior_minor"] == 2493 and summary["current_minor"] == 616
        assert summary["pending"] == []
        db.ensure_period("2025")
        exported = write_accounting_books(db, tmp_path / "annual", period_key="2025")
        with exported.files["assets"].open() as handle:
            asset_row = next(csv.DictReader(handle))
        assert asset_row["filed_period_amortization_minor"] == asset_row["recomputed_period_amortization_minor"] == "616"
        assert asset_row["accumulated_start_minor"] == "2493"
        assert asset_row["accumulated_end_minor"] == "3109"
        assert asset_row["remaining_amortizable_minor"] == "6891"


def test_invalid_or_overlapping_opening_rolls_back(tmp_path):
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture["database"]) as db:
        before = "\n".join(db.connection.iterdump())
        for changes in ({"current_year_minor":10001}, {"as_of":"2024-12-30"}, {"evidence_ref":""}):
            with pytest.raises(ValueError):
                opening(db, asset_id, **changes)
            assert "\n".join(db.connection.iterdump()) == before
        db.add_amortization_entry(asset_id=asset_id,period_key="2025-Q1",amount_minor=616,source_hash="synthetic:existing")
        with pytest.raises(ValueError, match="reconciliation"):
            opening(db, asset_id)
        assert db.connection.execute("SELECT count(*) FROM asset_depreciation_plans").fetchone()[0] == 0


def test_disposal_blocks_future_posting_and_exports_reason(tmp_path):
    from autonomo_taxes.books import write_accounting_books
    from autonomo_taxes.filing_package import write_filing_package
    import csv

    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture["database"]) as db:
        opening(db, asset_id)
        values = dict(asset_id=asset_id,disposed_on="2025-02-15",last_depreciable_on="2025-02-14",
                      reason="Synthetic retirement",evidence_ref="synthetic:disposal")
        assert record_disposal(db, **values) == values
        assert record_disposal(db, **values) == values
        entry = db.connection.execute("SELECT * FROM amortization_entries WHERE asset_id=? AND recognition_on='2025-03-31'", (asset_id,)).fetchone()
        with pytest.raises(ExpenseWorkflowError, match="after disposal"):
            recognize_period(db, entry["amortization_entry_id"], expected_version=entry["row_version"],
                request_id=str(uuid4()),actor="synthetic-user",archive_root=tmp_path / "archive")
        with pytest.raises(ValueError, match="after disposal"):
            db.add_amortization_entry(asset_id=asset_id,period_key="2025-Q2",amount_minor=100,source_hash="synthetic:blocked")
        export = write_accounting_books(db, tmp_path / "books", period_key="2025-Q1")
        with export.files["assets"].open() as f:
            row = next(csv.DictReader(f))
        assert row["disposed_on"] == values["disposed_on"] and row["disposal_reason"] == values["reason"]
        manifest = write_filing_package(db, tmp_path / "package", period_key="2025-Q1")
        assert "asset_disposal_settlement_review_required" in json.loads(manifest.read_text())["readiness_blockers"]


def test_opening_is_bounded_and_uses_original_annual_basis():
    rows = schedule(basis_minor=100000,business_use_ratio=0.5,annual_rate_basis_points=2500,
        placed_in_service_on="2020-01-01",method="linear",opening_accumulated_minor=40000,
        recognition_starts_on="2025-01-01")
    assert sum(r["amount_minor"] for r in rows) == 10000
    assert rows[0]["amount_minor"] == 3082


def test_continuation_does_not_change_a_historical_quarter(tmp_path):
    import csv
    from autonomo_taxes.books import write_accounting_books
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture["database"]) as db:
        db.add_amortization_entry(asset_id=asset_id, period_key="2024-Q1", amount_minor=618,
                                  source_hash="synthetic:historical-quarter")
        before = write_accounting_books(db,tmp_path / "before",period_key="2024-Q1")
        with before.files["assets"].open() as f:
            original = list(csv.DictReader(f))
        opening(db, asset_id)
        after = write_accounting_books(db,tmp_path / "after",period_key="2024-Q1")
        with after.files["assets"].open() as f:
            assert list(csv.DictReader(f)) == original


@pytest.mark.parametrize("unknown", ["service_date", "disposal"])
def test_imported_asset_never_disappears_or_guesses_settlement(tmp_path, unknown):
    import csv
    from autonomo_taxes.books import write_accounting_books
    from autonomo_taxes.filing_package import write_filing_package
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture["database"]) as db:
        db.ensure_period("2025-Q1")
        if unknown == "service_date":
            db.connection.execute("UPDATE assets SET placed_in_service_on=NULL WHERE asset_id=?", (asset_id,))
        else:
            record_disposal(db,asset_id=asset_id,disposed_on="2025-02-15",last_depreciable_on="2025-02-14",
                            reason="Synthetic retirement",evidence_ref="synthetic:retirement")
        exported = write_accounting_books(db,tmp_path / "books",period_key="2025-Q1")
        with exported.files["assets"].open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["recomputed_period_amortization_minor"] == ""
        assert rows[0]["calculation_status"] != "calculated"
        manifest = write_filing_package(db,tmp_path / "package",period_key="2025-Q1")
        assert "asset_recomputation_unavailable" in json.loads(manifest.read_text())["readiness_blockers"]


def test_annual_export_includes_a_midyear_opening(tmp_path):
    import csv
    from autonomo_taxes.books import write_accounting_books
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture["database"]) as db:
        db.add_amortization_entry(asset_id=asset_id,period_key="2024-Q1",amount_minor=625,
                                  source_hash="synthetic:opening-quarter")
        opening(db,asset_id,as_of="2024-03-31",current_year_minor=625)
        exported = write_accounting_books(db,tmp_path / "annual",period_key="2024")
        with exported.files["assets"].open() as handle:
            row = next(csv.DictReader(handle))
        assert row["filed_period_amortization_minor"] == row["recomputed_period_amortization_minor"] == "625"
        assert row["accumulated_end_minor"] == "625"
        assert row["calculation_status"] == "pending_native_depreciation"
