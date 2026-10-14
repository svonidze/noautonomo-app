"""Evidence-backed receipt numbering, independent of the supplier's invoice number."""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from .ledger_db import LedgerDB


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def create_series(db: LedgerDB, *, business_activity_id: str, tax_year: int, code: str,
                  first_number: int, reason: str, evidence_ref: str) -> dict[str, Any]:
    if type(tax_year) is not int or not 1900 <= tax_year <= 9999:
        raise ValueError("tax_year must be a calendar year")
    if type(first_number) is not int or first_number < 1:
        raise ValueError("first_number must be a positive integer")
    values = {"business_activity_id": _text(business_activity_id, "business_activity_id"),
              "tax_year": tax_year, "code": _text(code, "code"), "first_number": first_number,
              "reason": _text(reason, "reason"), "evidence_ref": _text(evidence_ref, "evidence_ref")}
    with db.transaction():
        activity = db.connection.execute("SELECT * FROM business_activities WHERE business_activity_id=?",
                                         (business_activity_id,)).fetchone()
        if activity is None or int(activity["starts_on"][:4]) > tax_year or (
            activity["ends_on"] and int(activity["ends_on"][:4]) < tax_year
        ):
            raise ValueError("Series requires an activity covering the registration year")
        existing = db.connection.execute("""SELECT * FROM receipt_registration_series
            WHERE business_activity_id=? AND tax_year=? AND code=?""",
            (values["business_activity_id"], tax_year, values["code"])).fetchone()
        if existing:
            if any(existing[key] != value for key, value in values.items()):
                raise ValueError("Existing receipt series differs; numbering cannot be reset")
            return dict(existing)
        values["series_id"] = str(uuid4())
        db.connection.execute("""INSERT INTO receipt_registration_series
            (series_id,business_activity_id,tax_year,code,first_number,reason,evidence_ref)
            VALUES (:series_id,:business_activity_id,:tax_year,:code,:first_number,:reason,:evidence_ref)""", values)
    return values


def register_receipt(db: LedgerDB, *, document_id: str, series_id: str, registered_on: str,
                     evidence_ref: str, ordinal: int | None = None,
                     receipt_number: str | None = None) -> dict[str, Any]:
    day = date.fromisoformat(registered_on)
    if registered_on != day.isoformat() or day > date.today():
        raise ValueError("Registration date must be ISO YYYY-MM-DD and not in the future")
    evidence_ref = _text(evidence_ref, "evidence_ref")
    if ordinal is not None and (type(ordinal) is not int or ordinal < 1):
        raise ValueError("ordinal must be a positive integer")
    if receipt_number is not None:
        receipt_number = _text(receipt_number, "receipt_number")
    with db.transaction():
        series = db.connection.execute("SELECT * FROM receipt_registration_series WHERE series_id=?", (series_id,)).fetchone()
        document = db.connection.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
        if series is None or document is None:
            raise ValueError("Existing receipt series and document are required")
        if day.year != series["tax_year"]:
            raise ValueError("Registration date must belong to the series year")
        activity = db.connection.execute("SELECT * FROM business_activities WHERE business_activity_id=?",
                                         (series["business_activity_id"],)).fetchone()
        if registered_on < activity["starts_on"] or activity["ends_on"] and registered_on > activity["ends_on"]:
            raise ValueError("Registration date is outside the activity")
        transactions = db.connection.execute("SELECT entry_type,business_activity_id FROM transactions WHERE document_id=?",
                                              (document_id,)).fetchall()
        if not transactions or any(not row["entry_type"].startswith("expense") for row in transactions):
            raise ValueError("Receipt numbering requires a document linked only to expense transactions")
        if any(row["business_activity_id"] not in (None, series["business_activity_id"]) for row in transactions):
            raise ValueError("Receipt series belongs to another activity")
        existing = db.connection.execute("SELECT * FROM receipt_registrations WHERE document_id=?", (document_id,)).fetchone()
        if existing:
            if (existing["series_id"] != series_id or existing["registered_on"] != registered_on
                or existing["document_source_hash"] != document["source_hash"]
                or ordinal is not None and ordinal != existing["ordinal"]
                or receipt_number is not None and receipt_number != existing["receipt_number"]):
                raise ValueError("Document already has a receipt number; historical numbering is immutable")
            return dict(existing)
        last = db.connection.execute("SELECT ordinal,registered_on FROM receipt_registrations WHERE series_id=? ORDER BY ordinal DESC LIMIT 1",
                                      (series_id,)).fetchone()
        next_number = last["ordinal"] + 1 if last else series["first_number"]
        if ordinal is not None and ordinal != next_number:
            raise ValueError(f"Expected consecutive receipt ordinal {next_number}; gaps or reuse are not allowed")
        if last and registered_on < last["registered_on"]:
            raise ValueError("Receipt registration dates must be chronological within a series")
        values = {"document_id": document_id, "series_id": series_id, "ordinal": next_number,
                  "receipt_number": receipt_number or f"{series['tax_year']}/{series['code']}/{next_number}",
                  "registered_on": registered_on, "document_source_hash": document["source_hash"],
                  "evidence_ref": evidence_ref}
        db.connection.execute("""INSERT INTO receipt_registrations
            (document_id,series_id,ordinal,receipt_number,registered_on,document_source_hash,evidence_ref)
            VALUES (:document_id,:series_id,:ordinal,:receipt_number,:registered_on,:document_source_hash,:evidence_ref)""", values)
    return values


def list_receipts(db: LedgerDB) -> list[dict[str, Any]]:
    return [dict(row) for row in db.connection.execute("""SELECT r.*,s.business_activity_id,s.tax_year,s.code,
        s.reason AS series_reason,s.evidence_ref AS series_evidence_ref FROM receipt_registrations r
        JOIN receipt_registration_series s USING(series_id) ORDER BY s.tax_year,s.code,r.ordinal""")]
