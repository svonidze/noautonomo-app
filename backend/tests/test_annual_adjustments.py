from datetime import date
from decimal import Decimal
import hashlib
import json
import sqlite3
from uuid import uuid4

import pytest

from autonomo_taxes.annual_adjustments import (
    AnnualAdjustmentError, adjustment_rows, annual_preview, list_adjustments,
    record_adjustment, void_adjustment,
)
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.tax_engine import CalculationBlocked, calculate_modelo130_rows
from autonomo_taxes.tax_row_loader import load_tax_rows


@pytest.fixture
def annual_source(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        original = tmp_path / "source.txt"
        original.write_text("Synthetic earnings statement: income EUR 100, withholding zero, accrued June 2026.")
        doc = db.upsert_document(period_key="2026", document_type="payment_receipt",
            issued_on="2026-06-06", source_hash=hashlib.sha256(original.read_bytes()).hexdigest())
        db.set_document_storage(doc["document_id"], source_path=str(original), mime_type="text/plain",
            expected_row_version=doc["row_version"])
        source = db.add_transaction(period_key="2026", transaction_date="2026-06-06",
            booking_date="2026-10-15", entry_type="income", description="Synthetic annual income",
            amount_minor=10000, amount_eur_minor=10000, document_id=doc["document_id"])
        request = {"request_id": str(uuid4()), "year": 2026,
            "source_transaction_id": source["transaction_id"], "expected_row_version": source["row_version"],
            "recognized_on": "2026-06-06", "kind": "income", "amount_eur_minor": 10000,
            "evidence_status": "verified", "evidence_ref": str(original), "rationale": "Reviewed synthetic earnings."}
        yield db, source, request, original


def test_annual_income_changes_renta_but_not_quarterly_rows_or_snapshots(annual_source):
    db, source, request, _ = annual_source
    db.ensure_period("2026-Q3")
    db.close_period("2026-Q3")
    snapshot = db.create_filing_snapshot("2026-Q3", form_code="130", status="filed",
        payload={"values": {"01": "0.00"}}, snapshot_hash="synthetic-filed")
    before = list(db.connection.execute("SELECT * FROM transactions"))
    recorded = record_adjustment(db, request, actor="test")
    assert record_adjustment(db, request, actor="test")["annual_adjustment_id"] == recorded["annual_adjustment_id"]
    assert list(db.connection.execute("SELECT * FROM transactions")) == before
    assert dict(db.connection.execute("SELECT * FROM filing_snapshots").fetchone()) == snapshot
    report = annual_preview(db, 2026, through=date(2026, 9, 30))
    assert report["values"]["business_income"] == "100.00"
    assert report["values"]["business_net_income"] == "95.00"
    assert report["filing_ready"] is False
    assert report["lineage"]["business_income"] == (source["transaction_id"],)
    assert load_tax_rows(db, 2026) == []
    _, quarterly = calculate_modelo130_rows([], year=2026, quarter=3,
        difficult_expenses_rate=Decimal("0.05"),
        previous_positive_casilla_07=Decimal(0), previous_negative_carry=Decimal(0), reduction=Decimal(0))
    assert quarterly.values["01"] == Decimal("0.00")


def test_provisional_income_is_counted_only_in_explicit_preparation_preview(annual_source):
    db, _, request, _ = annual_source
    request["evidence_status"] = "provisional"
    record_adjustment(db, request, actor="test")
    report = annual_preview(db, 2026, through=date(2026, 6, 30))
    assert report["status"] == "provisional" and not report["filing_ready"]
    assert report["values"]["business_income"] == "100.00"
    with pytest.raises(CalculationBlocked, match="earnings/withholding"):
        adjustment_rows(db, 2026)
    assert annual_preview(db, 2026, through=date(2026, 5, 31))["values"]["business_income"] == "0.00"


def test_conflicting_retries_duplicate_sources_and_stale_versions_are_rejected(annual_source):
    db, _, request, _ = annual_source
    stale = dict(request, expected_row_version=99)
    with pytest.raises(AnnualAdjustmentError, match="Source changed"):
        record_adjustment(db, stale, actor="test")
    record_adjustment(db, request, actor="test")
    with pytest.raises(AnnualAdjustmentError, match="different facts"):
        record_adjustment(db, dict(request, amount_eur_minor=20000), actor="test")
    with pytest.raises(AnnualAdjustmentError, match="already has"):
        record_adjustment(db, dict(request, request_id=str(uuid4())), actor="test")
    assert len(list_adjustments(db, 2026)) == 1


def test_withdrawal_is_append_only_and_removes_annual_contribution(annual_source):
    db, _, request, _ = annual_source
    row = record_adjustment(db, request, actor="test")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.connection.execute("UPDATE annual_adjustments SET amount_minor=1")
    db.connection.rollback()
    void_adjustment(db, row["annual_adjustment_id"], reason="Synthetic correction", actor="test")
    void_adjustment(db, row["annual_adjustment_id"], reason="Synthetic correction", actor="test")
    assert list_adjustments(db, 2026)[0]["voided"] == 1
    assert annual_preview(db, 2026, through=date(2026, 12, 31))["values"]["business_income"] == "0.00"
    replacement = record_adjustment(db, dict(request, request_id=str(uuid4())), actor="test")
    assert replacement["annual_adjustment_id"] != row["annual_adjustment_id"]


def test_changed_source_or_missing_original_blocks_confirmation(annual_source):
    db, source, request, original = annual_source
    original.write_text("Changed synthetic evidence")
    with pytest.raises(AnnualAdjustmentError, match="archived source"):
        record_adjustment(db, request, actor="test")


@pytest.mark.parametrize("field,value", [("year", True), ("amount_eur_minor", True),
    ("amount_eur_minor", -1), ("recognized_on", "2025-12-31"), ("evidence_ref", ""),
    ("evidence_status", "approved"), ("kind", []), ("evidence_status", [])])
def test_invalid_annual_requests_do_not_write(annual_source, field, value):
    db, _, request, _ = annual_source
    with pytest.raises(AnnualAdjustmentError):
        record_adjustment(db, dict(request, **{field: value}), actor="test")
    assert list_adjustments(db, 2026) == []


def test_native_cli_records_and_previews_annual_income(annual_source, tmp_path, capsys):
    from autonomo_taxes.cli import main
    db, _, request, _ = annual_source
    payload = tmp_path / "annual-request.json"
    payload.write_text(json.dumps(request))
    assert main(["annual-adjustments", "record", "--db", str(db.path),
        "--input", str(payload), "--actor", "test"]) == 0
    assert json.loads(capsys.readouterr().out)["amount_minor"] == 10000
    assert main(["annual-adjustments", "preview", "--db", str(db.path),
        "--year", "2026", "--as-of", "2026-09-30"]) == 0
    assert json.loads(capsys.readouterr().out)["values"]["annual_income_adjustments"] == "100.00"


def test_foreign_source_uses_the_existing_production_fx_policy(annual_source):
    db, source, request, _ = annual_source
    rate = db.add_fx_rate(rate_date="2026-06-05", base_currency="USD", quote_currency="EUR",
        rate="0.8", rate_source="ecb", source_hash="synthetic-rate")
    db.connection.execute("UPDATE transactions SET currency='USD', original_currency='USD', "
        "amount_original_minor=12500, fx_rate_id=? WHERE transaction_id=?",
        (rate["fx_rate_id"], source["transaction_id"]))
    db.connection.commit()
    record_adjustment(db, request, actor="test")
    assert annual_preview(db, 2026)["values"]["business_income"] == "100.00"


@pytest.mark.web
def test_http_annual_write_requires_session_and_origin_then_updates_preview(annual_source, tmp_path):
    from dataclasses import replace
    from http.client import HTTPConnection
    import threading
    from autonomo_test_support.local_web import _config
    from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingServer
    db, _, request, _ = annual_source
    config = replace(_config(tmp_path), database=db.path)
    app = LocalAccountingApp(config, session_token="test-token")
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    headers = {"Content-Type": "application/json", "Cookie": "autonomo_session=test-token"}
    try:
        connection.request("POST", "/api/annual-adjustments", json.dumps(request), headers)
        response = connection.getresponse()
        response.read()
        assert response.status == 403
        headers["Origin"] = f"http://127.0.0.1:{server.server_address[1]}"
        connection.request("POST", "/api/annual-adjustments", json.dumps(request), headers)
        response = connection.getresponse()
        recorded = json.loads(response.read())
        assert response.status == 201, recorded
        connection.request("GET", "/api/annual-adjustments?year=2026", headers=headers)
        response = connection.getresponse()
        report = json.loads(response.read())
        assert response.status == 200 and report["values"]["business_income"] == "100.00"
        assert report["annual_adjustments"][0]["annual_adjustment_id"] == recorded["annual_adjustment_id"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
