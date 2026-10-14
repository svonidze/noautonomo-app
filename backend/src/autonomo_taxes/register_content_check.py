"""Structural register checks; these do not certify completeness of source evidence."""
from __future__ import annotations

from decimal import Decimal
from typing import Any


AMOUNTS = ("amount_eur", "taxable_base_eur", "vat_eur", "deductible_irpf_eur",
           "deductible_vat_eur", "withholding_eur")


def check_register_content(projection: dict[str, Any]) -> dict[str, Any]:
    blockers = []
    totals = {}
    for basis, register in projection["registers"].items():
        grouped: dict[str, dict[str, dict[str, Decimal]]] = {}
        for row in register["rows"]:
            day = row["tax_date"]
            quarter = f"{day.year}-Q{(day.month - 1) // 3 + 1}"
            amounts = grouped.setdefault(quarter, {}).setdefault(row["kind"], {field: Decimal(0) for field in AMOUNTS})
            for field in AMOUNTS:
                amounts[field] += row[field] or Decimal(0)
            missing = []
            if row["component"].startswith("fx_"):
                if not row["fx_evidence_ref"]:
                    missing.append("fx_evidence_ref")
            elif row.get("amortization_entry_id") or row["document_type"] in {"social_security_evidence", "bank_fee_evidence"}:
                missing.extend(field for field in ("document_number", "document_source_hash") if not row[field])
            elif row["document_type"] in {"invoice", "income_invoice", "expense_invoice", "ingresos_book", "gastos_book"}:
                missing.extend(field for field in ("document_number", "issued_on", "counterparty_name") if not row[field])
                if not (row["counterparty_identifier"] or row["counterparty_tax_id"] or row["vat_id"]):
                    missing.append("counterparty_identifier")
                if row["kind"] == "expense" and row["invoice_type"] not in {"SF","AJ"} and not row["receipt_number"]:
                    missing.append("receipt_registration")
                if basis == "iva" and row["vat_eur"] and row["vat_rate_percent"] is None:
                    missing.append("vat_rate_percent")
                if row["invoice_type"].startswith("R") and not row["correction_of_transaction_id"]:
                    missing.append("corrected_transaction")
            else:
                missing.append("non_invoice_register_evidence_review")
            if missing:
                blockers.append({"register": basis, "transaction_id": row["transaction_id"],
                                 "missing_fields": missing})
        kinds = {kind for quarter in grouped.values() for kind in quarter}
        scope = {kind: {field: sum((quarter.get(kind, {}).get(field, Decimal(0))
                                  for quarter in grouped.values()), Decimal(0)) for field in AMOUNTS} for kind in kinds}
        if register["blocked"]:
            scope = {"unavailable": {field: None for field in AMOUNTS}}
        totals[basis] = {"quarters": grouped, "scope_total": scope,
                         "starts_on": register["starts_on"], "through": register["through"]}
    return {"checked_fields_ready": not blockers and not any(r["blocked"] for r in projection["registers"].values()),
            "source_completeness_verified": False, "blockers": blockers, "totals": totals}
