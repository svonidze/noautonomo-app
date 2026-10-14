"""Reviewed depreciation schedules and posted-recognition summaries."""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

CALCULATION_VERSION = "calendar_daily_v1"
METHODS = {"immediate", "linear"}


def minor(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def quarter_end(value: date) -> date:
    month = ((value.month - 1) // 3 + 1) * 3
    return date(value.year, month, calendar.monthrange(value.year, month)[1])


def schedule(*, basis_minor: int, business_use_ratio: float, annual_rate_basis_points: int,
             placed_in_service_on: str, method: str, opening_accumulated_minor: int = 0,
             recognition_starts_on: str | None = None) -> list[dict[str, Any]]:
    if method not in METHODS:
        raise ValueError("Choose immediate or linear depreciation")
    if type(basis_minor) is not int or basis_minor <= 0:
        raise ValueError("Amortizable basis must be a positive integer in EUR minor units")
    ratio = Decimal(str(business_use_ratio))
    if not ratio.is_finite() or not 0 < ratio <= 1:
        raise ValueError("Business use ratio must be above zero and at most one")
    if type(annual_rate_basis_points) is not int or not 100 <= annual_rate_basis_points <= 10000:
        raise ValueError("Annual rate must be between 1% and 100%")
    start = date.fromisoformat(placed_in_service_on)
    basis = minor(Decimal(basis_minor) * ratio)
    if basis <= 0:
        raise ValueError("Effective amortizable basis rounds to zero")
    if type(opening_accumulated_minor) is not int or not 0 <= opening_accumulated_minor <= basis:
        raise ValueError("Opening depreciation must be within the effective asset basis")
    if recognition_starts_on:
        recognition_start = date.fromisoformat(recognition_starts_on)
        if recognition_start < start or method != "linear":
            raise ValueError("Continuation requires linear depreciation starting after acquisition")
        start = recognition_start
    if method == "immediate":
        if opening_accumulated_minor:
            raise ValueError("Immediate depreciation cannot have an opening balance")
        return [{"period_key": f"{start.year}-Q{(start.month-1)//3+1}",
                 "recognition_on": start.isoformat(), "amount_minor": basis}]
    annual = Decimal(basis) * Decimal(annual_rate_basis_points) / 10000
    accumulated = Decimal(opening_accumulated_minor)
    rounded = opening_accumulated_minor
    cursor = start
    rows = []
    while rounded < basis:
        end = quarter_end(cursor)
        accumulated += annual * Decimal((end - cursor).days + 1) / (366 if calendar.isleap(cursor.year) else 365)
        target = min(basis, minor(accumulated))
        if target > rounded:
            rows.append({"period_key": f"{end.year}-Q{(end.month-1)//3+1}",
                         "recognition_on": end.isoformat(), "amount_minor": target - rounded})
        rounded = target
        cursor = end + timedelta(days=1)
    return rows


def native_year_summary(connection, asset_id: str, year: int, *, through_date: str | None = None) -> dict[str, Any] | None:
    """Posted recognition for native plans and legacy free-method assets."""
    import hashlib
    import json
    plan = connection.execute("SELECT * FROM asset_depreciation_plans WHERE asset_id=?", (asset_id,)).fetchone()
    free_asset = None
    if plan is None:
        free_asset = connection.execute(
            "SELECT placed_in_service_on FROM assets WHERE asset_id=? AND depreciation_method='free'",
            (asset_id,),
        ).fetchone()
        if free_asset is None:
            return None
    cutoff = through_date or f"{year}-12-31"
    parameters = json.loads(plan["parameters_json"]) if plan is not None else {}
    opening = parameters.get("opening")
    if opening and cutoff <= opening["as_of"]:
        return None
    rows = connection.execute("""SELECT ae.*,t.lifecycle_status AS recognition_status,t.transaction_date,
        tt.deductible_irpf_minor FROM amortization_entries ae
        LEFT JOIN transactions t ON t.transaction_id=ae.recognition_transaction_id
        LEFT JOIN tax_treatments tt ON tt.transaction_id=t.transaction_id AND tt.treatment_type='invoice_review' AND tt.jurisdiction='ES'
        WHERE ae.asset_id=? ORDER BY ae.recognition_on,ae.amortization_entry_id""", (asset_id,)).fetchall()
    prior, current, pending = 0, 0, []
    proof = []
    if opening:
        opening_year = int(opening["as_of"][:4])
        prior = opening["prior_year_minor"] + (opening["current_year_minor"] if year > opening_year else 0)
        current = opening["current_year_minor"] if year == opening_year else 0
        proof.append(["opening", opening, plan["source_hash"]])
    disposal = connection.execute("SELECT * FROM asset_disposals WHERE asset_id=? AND disposed_on<=?", (asset_id, cutoff)).fetchone()
    for row in rows:
        if opening and row["recognition_on"] and row["recognition_on"] <= opening["as_of"]:
            continue
        recognized = row["recognition_status"] in {"posted", "included_in_snapshot"}
        valid = recognized and row["deductible_irpf_minor"] == row["amount_minor"] and row["transaction_date"] == row["recognition_on"]
        if disposal and row["recognition_on"] and row["recognition_on"] > disposal["last_depreciable_on"]:
            continue
        if row["tax_year"] == year and row["recognition_on"] and row["recognition_on"] <= cutoff and (not valid or not row["include_in_books"]):
            pending.append(row["amortization_entry_id"])
        elif free_asset is not None and row["tax_year"] <= year and (not row["recognition_on"] or row["recognition_on"] <= cutoff) and (not valid or not row["include_in_books"]):
            pending.append(row["amortization_entry_id"])
        if valid and row["transaction_date"] <= cutoff:
            if row["tax_year"] < year:
                prior += row["amount_minor"]
            elif row["tax_year"] == year:
                current += row["amount_minor"]
            proof.append([row["recognition_transaction_id"], row["amount_minor"], row["source_hash"]])
    if free_asset is not None and not proof and (
        not free_asset["placed_in_service_on"] or free_asset["placed_in_service_on"] <= cutoff
    ):
        pending.append("free_depreciation_recognition_missing")
    if disposal and int(disposal["disposed_on"][:4]) == year:
        pending.append("disposal_settlement_review_required")
    return {"prior_minor": prior, "current_minor": current, "pending": pending,
            "source_hash": hashlib.sha256(json.dumps(proof).encode()).hexdigest(),
            "source_kind": "native_posted_journals"}


def accumulated_at(connection, asset_id: str, through: str) -> int:
    year = int(through[:4])
    native = native_year_summary(connection, asset_id, year, through_date=through)
    if native is not None:
        return native["prior_minor"] + native["current_minor"]
    prior = connection.execute("""SELECT COALESCE(SUM(amount_minor),0) FROM amortization_entries
        WHERE asset_id=? AND entry_kind='annual_evidence' AND tax_year<?""", (asset_id,year)).fetchone()[0]
    current = connection.execute("""SELECT COALESCE(SUM(ae.amount_minor),0) FROM amortization_entries ae
        JOIN periods p USING(period_id) WHERE ae.asset_id=? AND ae.tax_year=? AND ae.include_in_books=1
        AND ae.entry_kind IN ('quarter_schedule','adjustment') AND p.ends_on<=?""", (asset_id,year,through)).fetchone()[0]
    if through.endswith("-12-31"):
        annual = connection.execute("""SELECT SUM(amount_minor) FROM amortization_entries
            WHERE asset_id=? AND entry_kind='annual_evidence' AND tax_year=?""", (asset_id,year)).fetchone()[0]
        if annual is not None:
            current = annual
    return int(prior + current)
