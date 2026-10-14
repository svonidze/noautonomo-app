"""Shared accounting operations; no HTTP, CLI dispatch or UI dependency."""

from __future__ import annotations
from typing import Any, Mapping
from pathlib import Path
from dataclasses import asdict
from datetime import date
from decimal import Decimal, InvalidOperation
import sys
from ..fx_policy import ManualRateCheck, FXRateDeviationError, check_manual_rate
from ..fx_reference import fetch_eur_rate
from ..ledger_db import open as open_ledger_db
from .operation_utils import _parse_review_id


def apply_fx(database: Path, payload: Mapping[str, Any], *, allow_unverified: bool = False):
    expected_fields = {
        "review_id",
        "expected_row_version",
        "rate_date",
        "rate",
        "rate_source",
        "source_reference",
    }
    unexpected = sorted(set(payload) - expected_fields)
    missing = sorted(expected_fields - set(payload))
    if unexpected or missing:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise ValueError("Invalid FX review request (" + "; ".join(details) + ")")
    review_kind, subject_id = _parse_review_id(str(payload["review_id"]))
    with open_ledger_db(database) as db:
        transaction_id = subject_id
        if review_kind == "document":
            rows = db.connection.execute(
                "SELECT transaction_id FROM transactions WHERE document_id = ?",
                (subject_id,),
            ).fetchall()
            if len(rows) != 1:
                raise ValueError(
                    "Document FX review requires exactly one linked transaction; "
                    f"found {len(rows)}"
                )
            transaction_id = str(rows[0]["transaction_id"])
        source_reference = payload["source_reference"]
        if not isinstance(source_reference, str) or not source_reference.strip():
            raise ValueError("FX review requires a nonblank source_reference")
        source_reference = source_reference.strip()
        check = None
        transaction = db.connection.execute(
            "SELECT original_currency, currency FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        if transaction is not None:
            check = _check_manual_fx_rate(
                str(payload["rate"]),
                currency=str(transaction["original_currency"] or transaction["currency"]),
                rate_date=str(payload["rate_date"]),
                rate_source=str(payload["rate_source"]),
                allow_unverified=allow_unverified,
            )
            if check.source_reference_note:
                source_reference = f"{source_reference} ({check.source_reference_note})"
        result = db.review_transaction_fx_rate(
            transaction_id,
            expected_row_version=int(payload["expected_row_version"]),
            rate_date=str(payload["rate_date"]),
            rate=str(payload["rate"]),
            rate_source=str(payload["rate_source"]),
            source_reference=source_reference,
        )
    return ({**result, "fx_rate_check": asdict(check) if check else None}, 0)


def _check_manual_fx_rate(
    rate: str,
    *,
    currency: str,
    rate_date: str,
    rate_source: str,
    allow_unverified: bool,
) -> ManualRateCheck:
    """Return the reference outcome for both the audit record and CLI/API clients.

    The reference lookup runs before any ledger write so an unreachable ECB
    service neither holds a database lock nor blocks offline use.
    """

    normalized_currency = currency.strip().upper()
    if normalized_currency == "EUR":
        return ManualRateCheck("exempt")
    try:
        decimal_rate = Decimal(rate)
    except InvalidOperation as exc:
        raise ValueError("FX rate must be a valid Decimal") from exc
    try:
        check = check_manual_rate(
            decimal_rate,
            currency=normalized_currency,
            rate_date=date.fromisoformat(rate_date),
            rate_source=rate_source.strip(),
            reference_lookup=fetch_eur_rate,
            allow_unverified=allow_unverified,
        )
    except FXRateDeviationError as exc:
        raise FXRateDeviationError(
            f"{exc}; pass --allow-unverified-rate to record it anyway"
        ) from exc
    if check.status == "unavailable":
        print(f"warning: {check.detail}", file=sys.stderr)
    return check
