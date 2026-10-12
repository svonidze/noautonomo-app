from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from autonomo_taxes.annual_readiness import _best_modelo303_evidence
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.tax_result_view import build_tax_summary, select_filed_form_snapshot


_PERIOD = {
    "period_key": "2031-Q2",
    "period_type": "quarter",
    "status": "open",
    "starts_on": "2031-04-01",
    "ends_on": "2031-06-30",
}


def _summary(tmp_path: Path, obligations: list[dict]) -> dict:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        _, summary = build_tax_summary(
            db.connection,
            period=_PERIOD,
            obligations=obligations,
            tax_forms={},
            cached={},
            as_of=date(2031, 5, 15),
        )
    return summary


def test_quarter_without_obligation_rows_is_undecided_not_exempt(tmp_path: Path) -> None:
    summary = _summary(tmp_path, [])

    assert summary["unresolved_obligations"] == ["130", "303"]
    assert summary["total_payable_minor"] is None
    assert summary["settlement_status"] == "undetermined"
    assert summary["calculation_source"] == "unavailable"
    assert summary["forms"]["130"]["determination"] == "unknown"
    assert summary["forms"]["303"]["determination"] == "unknown"


def test_missing_core_return_stays_unresolved_next_to_a_reviewed_one(tmp_path: Path) -> None:
    summary = _summary(
        tmp_path,
        [
            {
                "obligation_id": "obligation-303",
                "obligation_code": "303",
                "determination": "not_due",
                "filing_status": "waived",
            }
        ],
    )

    assert summary["unresolved_obligations"] == ["130"]
    assert summary["total_payable_minor"] is None
    assert summary["forms"]["303"]["calculation_source"] == "not_required"


@pytest.mark.parametrize("schema", ["modelo303_v4", "modelo303_v5", "modelo303_v6"])
def test_modelo303_schema_keeps_blanks_and_evidence_priority(
    tmp_path: Path, schema: str,
) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        db.ensure_period("2031-Q2")
        db.create_filing_snapshot(
            "2031-Q2", form_code="303", status="baseline", filed_on="2031-07-20",
            snapshot_hash="extracted",
            payload={
                "value_extraction_schema": schema,
                "filed_values": {"result": "-84.00"},
                "blank_casillas": ["78"],
            },
        )
        db.create_filing_snapshot(
            "2031-Q2", form_code="303", status="baseline", filed_on="2031-07-20",
            snapshot_hash="calculated", payload={"values": {"result": "-99.00"}},
        )
        selected = select_filed_form_snapshot(
            db.connection, period_key="2031-Q2", form_code="303",
        )
        annual = _best_modelo303_evidence(db, "2031-Q2")
        assert selected["snapshot_hash"] == "extracted"
        assert selected["values"] == {"result": "-84.00", "78": "0.00"}
        assert annual["snapshot_hash"] == "extracted"
        assert annual["values"] == selected["values"]
        assert annual["source_type"] == f"{schema}_extraction"
        assert "78" in annual["explicit_keys"]

        db.create_filing_snapshot(
            "2031-Q2", form_code="303", status="baseline", filed_on="2031-07-20",
            snapshot_hash="matched", payload={
                "values": {"result": "-85.00"},
                "receipt_verification": {"status": "matched"},
            },
        )
        db.create_filing_snapshot(
            "2031-Q2", form_code="303", status="baseline", filed_on="2031-07-20",
            snapshot_hash="later-extracted", payload={
                "value_extraction_schema": schema,
                "filed_values": {"result": "-84.00"},
            },
        )
        selected = select_filed_form_snapshot(
            db.connection, period_key="2031-Q2", form_code="303",
        )
        annual = _best_modelo303_evidence(db, "2031-Q2")
        assert selected["snapshot_hash"] == "matched"
        assert selected["values"] == {"result": "-85.00"}
        assert annual["snapshot_hash"] == "matched"
        assert annual["values"] == selected["values"]
        assert annual["source_type"] == "matched_filing_receipt"
