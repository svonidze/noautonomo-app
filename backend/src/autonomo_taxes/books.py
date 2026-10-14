from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sqlite3
from typing import Any

from .ledger_db import LedgerDB


INCOME_FIELDS = (
    "period_key",
    "transaction_id",
    "treatment_id",
    "transaction_date",
    "booking_date",
    "external_key",
    "entry_type",
    "description",
    "counterparty_name",
    "document_number",
    "document_id",
    "included_snapshot_id",
    "tax_code",
    "rule_version_id",
    "fx_rate_id",
    "currency",
    "gross_amount_minor",
    "filed_taxable_base_minor",
    "recomputed_taxable_base_minor",
    "filed_vat_minor",
    "recomputed_vat_minor",
    "withholding_minor",
    "source_hash",
)

EXPENSE_FIELDS = (
    "period_key",
    "transaction_id",
    "treatment_id",
    "transaction_date",
    "booking_date",
    "external_key",
    "entry_type",
    "description",
    "counterparty_name",
    "document_number",
    "document_id",
    "included_snapshot_id",
    "tax_code",
    "rule_version_id",
    "fx_rate_id",
    "currency",
    "gross_amount_minor",
    "filed_taxable_base_minor",
    "recomputed_taxable_base_minor",
    "filed_deductible_irpf_minor",
    "recomputed_deductible_irpf_minor",
    "filed_deductible_vat_minor",
    "recomputed_deductible_vat_minor",
    "source_hash",
)

ASSET_FIELDS = (
    "period_key",
    "asset_id",
    "asset_code",
    "acquisition_transaction_id",
    "acquisition_transaction_date",
    "description",
    "document_number",
    "placed_in_service_on",
    "depreciation_method",
    "useful_life_months",
    "business_use_ratio",
    "annual_rate_basis_points",
    "cost_minor",
    "amortizable_base_minor",
    "current_amortization_entry_id",
    "filed_period_amortization_minor",
    "recomputed_period_amortization_minor",
    "iva_treatment",
    "advisor_decision",
    "source_hash",
    "disposed_on",
    "disposal_reason",
    "disposal_evidence_ref",
    "accumulated_start_minor",
    "accumulated_end_minor",
    "remaining_amortizable_minor",
    "calculation_status",
)

PAYMENT_FIELDS = (
    "period_key",
    "payment_id",
    "paid_on",
    "transaction_id",
    "obligation_id",
    "obligation_code",
    "original_reference",
    "match_status",
    "currency",
    "fee_currency",
    "filed_amount_minor",
    "recomputed_amount_minor",
    "fee_minor",
    "source_hash",
)

BOOK_FILENAMES = {
    "income": "income.csv",
    "expense": "expense.csv",
    "assets": "assets.csv",
    "payments": "payments.csv",
}


class BookExportError(ValueError):
    """Raised when LedgerDB rows cannot be exported into deterministic books."""


@dataclass(frozen=True)
class BookExportResult:
    period_key: str
    output_dir: Path
    files: dict[str, Path]
    row_counts: dict[str, int]


def write_accounting_books(database: LedgerDB, output_dir: str | Path, *, period_key: str) -> BookExportResult:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    period = _fetch_period(database.connection, period_key)
    income_rows = _build_transaction_rows(database.connection, period, kind="income")
    expense_rows = _build_transaction_rows(database.connection, period, kind="expense")
    asset_rows = _build_asset_rows(database.connection, period)
    payment_rows = _build_payment_rows(database.connection, period)

    files = {
        "income": root / BOOK_FILENAMES["income"],
        "expense": root / BOOK_FILENAMES["expense"],
        "assets": root / BOOK_FILENAMES["assets"],
        "payments": root / BOOK_FILENAMES["payments"],
    }
    _write_csv(files["income"], INCOME_FIELDS, income_rows)
    _write_csv(files["expense"], EXPENSE_FIELDS, expense_rows)
    _write_csv(files["assets"], ASSET_FIELDS, asset_rows)
    _write_csv(files["payments"], PAYMENT_FIELDS, payment_rows)
    counts = {"income": len(income_rows), "expense": len(expense_rows),
              "assets": len(asset_rows), "payments": len(payment_rows)}
    from .tax_row_loader import load_tax_rows
    from .tax_attribution import register_projection
    tax_rows = load_tax_rows(database, int(period_key[:4]), allow_authoritative_history=True)
    from .tax_register_workbook import REVIEW_XLSX_FILENAME, write_tax_register_review_xlsx
    projection = register_projection(
        tax_rows, year=int(period_key[:4]), through=date.fromisoformat(period["ends_on"]),
        iva_starts_on=date.fromisoformat(period["starts_on"]),
        for_books=True,
    )
    from .register_content_check import check_register_content
    content_check = check_register_content(projection)
    files["register_content_check"] = root / "register-content-check.json"
    files["register_content_check"].write_text(json.dumps(content_check, default=str, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    counts["register_content_check"] = len(content_check["blockers"])
    files["tax_registers"] = root / "tax-registers.json"
    files["tax_registers"].write_text(json.dumps(projection, default=str, sort_keys=True,
                                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts["tax_registers"] = len(projection["bridge"])
    fields = ("transaction_id", "source_transaction_id", "component", "attribution_id", "tax_date",
              "document_id", "document_number", "issued_on", "document_source_hash", "counterparty_name",
              "counterparty_tax_id", "vat_id", "country_code",
              "counterparty_identifier", "counterparty_id_type", "counterparty_identity_country", "amortization_entry_id",
              "receipt_number", "receipt_series", "receipt_year", "registered_on", "operation_date", "document_type",
              "correction_of_transaction_id", "correction_kind",
              "vat_rate_percent", "invoice_type", "operation_key", "operation_qualification",
              "exempt_operation", "reverse_charge",
              "include_modelo130", "include_modelo303",
              "kind", "amount_eur", "taxable_base_eur", "vat_eur", "deductible_irpf_eur",
              "deductible_vat_eur", "withholding_eur")
    for basis, register in projection["registers"].items():
        files[basis] = root / f"{basis}-register.csv"
        _write_csv(files[basis], fields, [{k: str(row[k]) if row[k] is not None else '' for k in fields}
                                          for row in register["rows"]])
        counts[basis] = len(register["rows"])
    files['tax_registers_xlsx'] = write_tax_register_review_xlsx(root / REVIEW_XLSX_FILENAME, projection)
    counts['tax_registers_xlsx'] = sum(len(register['rows']) for register in projection['registers'].values())
    return BookExportResult(
        period_key=period_key,
        output_dir=root,
        files=files,
        row_counts=counts,
    )


def _build_transaction_rows(
    connection: sqlite3.Connection,
    period: sqlite3.Row,
    *,
    kind: str,
) -> list[dict[str, str]]:
    if kind not in {"income", "expense"}:
        raise ValueError(f"Unsupported book kind: {kind}")
    entry_prefix = f"{kind}%"
    transactions = _fetch_all(
        connection,
        """
        SELECT
            t.transaction_id,
            t.external_key,
            t.transaction_date,
            t.booking_date,
            t.entry_type,
            t.description,
            t.amount_minor,
            t.currency,
            t.amount_eur_minor,
            t.fx_rate_id,
            t.document_id,
            t.included_snapshot_id,
            t.source_hash,
            d.document_number,
            c.display_name AS counterparty_name,
            (EXISTS (SELECT 1 FROM expense_actions action
                     WHERE action.action_kind='expense' AND action.subject_id=t.transaction_id)
             OR EXISTS (SELECT 1 FROM amortization_entries ae
                        JOIN asset_depreciation_plans plan ON plan.asset_id=ae.asset_id
                        WHERE ae.recognition_transaction_id=t.transaction_id)) AS workflow_reviewed
        FROM transactions t
        JOIN periods p ON p.period_id = t.period_id
        LEFT JOIN documents d ON d.document_id = t.document_id
        LEFT JOIN counterparties c ON c.counterparty_id = t.counterparty_id
        WHERE p.period_key = ? AND t.entry_type LIKE ?
          AND t.lifecycle_status IN ('posted', 'included_in_snapshot')
        ORDER BY t.transaction_date, t.booking_date, t.transaction_id
        """,
        (period["period_key"], entry_prefix),
    )
    if not transactions:
        return []

    transaction_ids = [row["transaction_id"] for row in transactions]
    treatments = _fetch_treatments(connection, transaction_ids)
    orphan_labels = [
        transaction["external_key"] or transaction["transaction_id"]
        for transaction in transactions
        if transaction["transaction_id"] not in treatments
    ]
    if orphan_labels:
        joined = ", ".join(orphan_labels)
        raise BookExportError(f"{kind} book export requires tax treatments for every transaction: {joined}")

    rows: list[dict[str, str]] = []
    for transaction in transactions:
        gross_amount_minor = _gross_amount_minor(transaction)
        treatment_rows = treatments[transaction["transaction_id"]]
        for treatment in treatment_rows:
            if kind == "income":
                rows.append(
                    {
                        "period_key": period["period_key"],
                        "transaction_id": transaction["transaction_id"],
                        "treatment_id": treatment["treatment_id"],
                        "transaction_date": transaction["transaction_date"],
                        "booking_date": transaction["booking_date"],
                        "external_key": _text(transaction["external_key"]),
                        "entry_type": transaction["entry_type"],
                        "description": transaction["description"],
                        "counterparty_name": _text(transaction["counterparty_name"]),
                        "document_number": _text(transaction["document_number"]),
                        "document_id": _text(transaction["document_id"]),
                        "included_snapshot_id": _text(transaction["included_snapshot_id"]),
                        "tax_code": treatment["tax_code"],
                        "rule_version_id": _text(treatment["rule_version_id"]),
                        "fx_rate_id": _text(transaction["fx_rate_id"]),
                        "currency": transaction["currency"],
                        "gross_amount_minor": str(gross_amount_minor),
                        "filed_taxable_base_minor": _minor_text(treatment["taxable_base_minor"], fallback=gross_amount_minor),
                        "recomputed_taxable_base_minor": str(gross_amount_minor),
                        "filed_vat_minor": _minor_text(treatment["vat_minor"]),
                        "recomputed_vat_minor": str(_recomputed_vat_minor(gross_amount_minor, treatment)),
                        "withholding_minor": _minor_text(treatment["withholding_minor"]),
                        "source_hash": transaction["source_hash"],
                    }
                )
            else:
                # Native commands explicitly reconcile and approve separate IRPF
                # and IVA amounts. The historical diagnostic ratio must not
                # re-deduct an acquisition or overwrite that reviewed split.
                if transaction["workflow_reviewed"]:
                    recomputed_base = treatment["taxable_base_minor"] or 0
                    recomputed_irpf = (treatment["deductible_irpf_minor"] or 0) if treatment["include_modelo130"] else 0
                    recomputed_vat = (treatment["deductible_vat_minor"] or 0) if treatment["include_modelo303"] else 0
                else:
                    recomputed_base = _recomputed_taxable_base_minor(gross_amount_minor, treatment)
                    recomputed_irpf = _recomputed_deductible_minor(recomputed_base, treatment["deductible_ratio"])
                    recomputed_vat = _recomputed_deductible_minor(treatment["vat_minor"] or 0, treatment["deductible_ratio"])
                rows.append(
                    {
                        "period_key": period["period_key"],
                        "transaction_id": transaction["transaction_id"],
                        "treatment_id": treatment["treatment_id"],
                        "transaction_date": transaction["transaction_date"],
                        "booking_date": transaction["booking_date"],
                        "external_key": _text(transaction["external_key"]),
                        "entry_type": transaction["entry_type"],
                        "description": transaction["description"],
                        "counterparty_name": _text(transaction["counterparty_name"]),
                        "document_number": _text(transaction["document_number"]),
                        "document_id": _text(transaction["document_id"]),
                        "included_snapshot_id": _text(transaction["included_snapshot_id"]),
                        "tax_code": treatment["tax_code"],
                        "rule_version_id": _text(treatment["rule_version_id"]),
                        "fx_rate_id": _text(transaction["fx_rate_id"]),
                        "currency": transaction["currency"],
                        "gross_amount_minor": str(gross_amount_minor),
                        "filed_taxable_base_minor": _minor_text(treatment["taxable_base_minor"], fallback=gross_amount_minor),
                        "recomputed_taxable_base_minor": str(recomputed_base),
                        "filed_deductible_irpf_minor": _minor_text(treatment["deductible_irpf_minor"]),
                        "recomputed_deductible_irpf_minor": str(recomputed_irpf),
                        "filed_deductible_vat_minor": _minor_text(treatment["deductible_vat_minor"]),
                        "recomputed_deductible_vat_minor": str(recomputed_vat),
                        "source_hash": transaction["source_hash"],
                    }
                )
    return rows


def _build_asset_rows(connection: sqlite3.Connection, period: sqlite3.Row) -> list[dict[str, str]]:
    rows = _fetch_all(
        connection,
        """
        SELECT
            a.asset_id,
            a.asset_code,
            a.acquisition_transaction_id,
            a.placed_in_service_on,
            a.cost_minor,
            a.currency,
            a.depreciation_method,
            a.useful_life_months,
            a.amortizable_base_minor,
            a.iva_treatment,
            a.business_use_ratio,
            a.annual_rate_basis_points,
            a.advisor_decision,
            a.source_hash,
            acq.transaction_date AS acquisition_transaction_date,
            acq.description AS acquisition_description,
            d.document_number,
            current.amortization_entry_id AS current_amortization_entry_id,
            current.amount_minor AS current_amortization_minor
        FROM assets a
        LEFT JOIN transactions acq ON acq.transaction_id = a.acquisition_transaction_id
        LEFT JOIN documents d ON d.document_id = a.document_id
        LEFT JOIN (
            SELECT GROUP_CONCAT(ae.amortization_entry_id, ';') AS amortization_entry_id,
                   ae.asset_id, SUM(ae.amount_minor) AS amount_minor
            FROM amortization_entries ae
            JOIN periods ap ON ap.period_id = ae.period_id
            WHERE ap.starts_on >= ? AND ap.ends_on <= ?
              AND ae.include_in_books = 1
              AND ae.entry_kind IN ('quarter_schedule', 'adjustment')
            GROUP BY ae.asset_id
        ) AS current ON current.asset_id = a.asset_id
        WHERE (a.placed_in_service_on <= ? OR (a.placed_in_service_on IS NULL
               AND (acq.transaction_date <= ? OR acq.transaction_date IS NULL))) AND NOT EXISTS (
            SELECT 1 FROM asset_disposals disposal WHERE disposal.asset_id=a.asset_id AND disposal.disposed_on<?)
        ORDER BY a.asset_code, a.asset_id
        """,
        (period["starts_on"], period["ends_on"], period["ends_on"], period["ends_on"], period["starts_on"]),
    )
    orphan_entries = _fetch_all(
        connection,
        """
        SELECT ae.amortization_entry_id, a.asset_code
        FROM amortization_entries ae
        JOIN periods p ON p.period_id = ae.period_id
        JOIN assets a ON a.asset_id = ae.asset_id
        WHERE p.period_key = ?
          AND ae.include_in_books = 1
          AND ae.entry_kind IN ('quarter_schedule', 'adjustment')
          AND a.acquisition_transaction_id IS NULL
        ORDER BY ae.amortization_entry_id
        """,
        (period["period_key"],),
    )
    if orphan_entries:
        joined = ", ".join(
            f"{entry['amortization_entry_id']}:{entry['asset_code']}" for entry in orphan_entries
        )
        raise BookExportError(f"assets book export refuses orphan amortization rows: {joined}")

    months_in_period = _month_span(period["starts_on"], period["ends_on"])
    export_rows: list[dict[str, str]] = []
    for row in rows:
        base_minor = row["amortizable_base_minor"] if row["amortizable_base_minor"] is not None else row["cost_minor"]
        from .depreciation import accumulated_at, native_year_summary
        accumulated_start = accumulated_at(connection, row["asset_id"],
            (date.fromisoformat(period["starts_on"]) - timedelta(days=1)).isoformat())
        accumulated_end = accumulated_at(connection, row["asset_id"], period["ends_on"])
        disposal = connection.execute("SELECT * FROM asset_disposals WHERE asset_id=? AND disposed_on<=?",
                                      (row["asset_id"], period["ends_on"])).fetchone()
        recomputed_minor = _recomputed_amortization_minor(
            base_minor=base_minor,
            months_in_period=months_in_period,
            active_period_ratio=_active_period_ratio(
                period["starts_on"],
                period["ends_on"],
                row["placed_in_service_on"],
            ),
            useful_life_months=row["useful_life_months"],
            business_use_ratio=row["business_use_ratio"],
            annual_rate_basis_points=row["annual_rate_basis_points"],
        )
        native = native_year_summary(connection, row["asset_id"], int(period["ends_on"][:4]), through_date=period["ends_on"])
        if native is not None:
            planned = connection.execute("""SELECT COALESCE(SUM(ae.amount_minor),0) FROM amortization_entries ae JOIN transactions t ON t.transaction_id=ae.recognition_transaction_id WHERE ae.asset_id=? AND t.transaction_date BETWEEN ? AND ? AND ae.include_in_books=1 AND t.lifecycle_status IN ('posted','included_in_snapshot')""", (row["asset_id"], period["starts_on"], period["ends_on"])).fetchone()[0]
            recomputed_minor = int(planned)
            if period["starts_on"] == period["ends_on"][:4] + "-01-01":
                recomputed_minor = native["current_minor"]
                accumulated_start = native["prior_minor"]
        calculation_status = "calculated"
        if disposal:
            calculation_status = "disposal_settlement_review_required"
            recomputed_minor = None
        elif not row["placed_in_service_on"]:
            calculation_status = "placed_in_service_date_missing"
            recomputed_minor = None
        elif native is not None and native["pending"]:
            calculation_status = "pending_native_depreciation"
        export_rows.append(
            {
                "period_key": period["period_key"],
                "asset_id": row["asset_id"],
                "asset_code": row["asset_code"],
                "calculation_status": calculation_status,
                "disposed_on": disposal["disposed_on"] if disposal else "",
                "disposal_reason": disposal["reason"] if disposal else "",
                "disposal_evidence_ref": disposal["evidence_ref"] if disposal else "",
                "accumulated_start_minor": str(accumulated_start),
                "accumulated_end_minor": str(accumulated_end),
                "remaining_amortizable_minor": (str(int((Decimal(base_minor) * Decimal(str(row["business_use_ratio"]))).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) - accumulated_end)
                                                if row["business_use_ratio"] is not None else ""),
                "acquisition_transaction_id": _text(row["acquisition_transaction_id"]),
                "acquisition_transaction_date": _text(row["acquisition_transaction_date"]),
                "description": _text(row["acquisition_description"]),
                "document_number": _text(row["document_number"]),
                "placed_in_service_on": _text(row["placed_in_service_on"]),
                "depreciation_method": row["depreciation_method"],
                "useful_life_months": _text(row["useful_life_months"]),
                "business_use_ratio": _ratio_text(row["business_use_ratio"]),
                "annual_rate_basis_points": _text(row["annual_rate_basis_points"]),
                "cost_minor": str(row["cost_minor"]),
                "amortizable_base_minor": str(base_minor),
                "current_amortization_entry_id": _text(row["current_amortization_entry_id"]),
                "filed_period_amortization_minor": _minor_text(row["current_amortization_minor"]),
                "recomputed_period_amortization_minor": str(recomputed_minor) if recomputed_minor is not None else "",
                "iva_treatment": row["iva_treatment"],
                "advisor_decision": _text(row["advisor_decision"]),
                "source_hash": row["source_hash"],
            }
        )
    return export_rows


def _build_payment_rows(connection: sqlite3.Connection, period: sqlite3.Row) -> list[dict[str, str]]:
    rows = _fetch_all(
        connection,
        """
        SELECT
            pay.payment_id,
            pay.paid_on,
            pay.transaction_id,
            pay.obligation_id,
            pay.original_reference,
            pay.match_status,
            pay.amount_minor,
            pay.currency,
            pay.fee_minor,
            pay.fee_currency,
            pay.source_hash,
            ob.obligation_code
        FROM payments pay
        LEFT JOIN obligations ob ON ob.obligation_id = pay.obligation_id
        WHERE pay.paid_on >= ? AND pay.paid_on <= ?
        ORDER BY pay.paid_on, pay.payment_id
        """,
        (period["starts_on"], period["ends_on"]),
    )
    export_rows: list[dict[str, str]] = []
    for row in rows:
        recomputed_minor = row["amount_minor"] - (row["fee_minor"] or 0)
        export_rows.append(
            {
                "period_key": period["period_key"],
                "payment_id": row["payment_id"],
                "paid_on": row["paid_on"],
                "transaction_id": _text(row["transaction_id"]),
                "obligation_id": _text(row["obligation_id"]),
                "obligation_code": _text(row["obligation_code"]),
                "original_reference": _text(row["original_reference"]),
                "match_status": row["match_status"],
                "currency": row["currency"],
                "fee_currency": _text(row["fee_currency"]),
                "filed_amount_minor": str(row["amount_minor"]),
                "recomputed_amount_minor": str(recomputed_minor),
                "fee_minor": _minor_text(row["fee_minor"]),
                "source_hash": row["source_hash"],
            }
        )
    return export_rows


def _fetch_period(connection: sqlite3.Connection, period_key: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM periods WHERE period_key = ?",
        (period_key,),
    ).fetchone()
    if row is None:
        raise BookExportError(f"Unknown period: {period_key}")
    return row


def _fetch_treatments(
    connection: sqlite3.Connection,
    transaction_ids: list[str],
) -> dict[str, list[sqlite3.Row]]:
    placeholders = ", ".join("?" for _ in transaction_ids)
    sql = f"""
        SELECT *
        FROM tax_treatments
        WHERE transaction_id IN ({placeholders})
        ORDER BY transaction_id, jurisdiction, treatment_type, treatment_id
    """
    rows = _fetch_all(connection, sql, tuple(transaction_ids))
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["transaction_id"], []).append(row)
    return grouped


def _fetch_all(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
    return list(connection.execute(sql, params).fetchall())


def _gross_amount_minor(row: sqlite3.Row) -> int:
    if row["amount_eur_minor"] is not None:
        return int(row["amount_eur_minor"])
    return int(row["amount_minor"])


def _recomputed_taxable_base_minor(gross_amount_minor: int, treatment: sqlite3.Row) -> int:
    vat_minor = treatment["vat_minor"] or 0
    return gross_amount_minor - int(vat_minor)


def _recomputed_vat_minor(gross_amount_minor: int, treatment: sqlite3.Row) -> int:
    filed_base = treatment["taxable_base_minor"]
    if filed_base is not None:
        return gross_amount_minor - int(filed_base)
    return int(treatment["vat_minor"] or 0)


def _recomputed_deductible_minor(base_minor: int, deductible_ratio: float | None) -> int:
    ratio = Decimal("1") if deductible_ratio is None else Decimal(str(deductible_ratio))
    return int((Decimal(base_minor) * ratio).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _recomputed_amortization_minor(
    *,
    base_minor: int,
    months_in_period: int,
    active_period_ratio: Decimal,
    useful_life_months: int | None,
    business_use_ratio: float | None,
    annual_rate_basis_points: int | None,
) -> int:
    ratio = Decimal("1") if business_use_ratio is None else Decimal(str(business_use_ratio))
    base = Decimal(base_minor) * ratio * active_period_ratio
    if useful_life_months:
        return int((base * Decimal(months_in_period) / Decimal(useful_life_months)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if annual_rate_basis_points:
        annual_rate = Decimal(annual_rate_basis_points) / Decimal("10000")
        return int((base * annual_rate * Decimal(months_in_period) / Decimal("12")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return 0


def _active_period_ratio(starts_on: str, ends_on: str, placed_in_service_on: str | None) -> Decimal:
    if not placed_in_service_on:
        return Decimal("1")
    start = date.fromisoformat(starts_on)
    end = date.fromisoformat(ends_on)
    service_date = date.fromisoformat(placed_in_service_on)
    if service_date <= start:
        return Decimal("1")
    if service_date > end:
        return Decimal("0")
    period_days = Decimal((end - start).days + 1)
    active_days = Decimal((end - service_date).days + 1)
    return active_days / period_days


def _month_span(starts_on: str, ends_on: str) -> int:
    start = date.fromisoformat(starts_on)
    end = date.fromisoformat(ends_on)
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def _minor_text(value: Any, *, fallback: int | None = None) -> str:
    if value is None:
        return "" if fallback is None else str(fallback)
    return str(value)


def _ratio_text(value: float | None) -> str:
    if value is None:
        return ""
    normalized = Decimal(str(value)).normalize()
    return format(normalized, "f")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
