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


def book_year_summary(connection, asset_id: str, year: int, *, through_date: str | None = None) -> dict[str, Any] | None:
    """Keep annual source controls separate from posted historical G03 charges."""
    native = native_year_summary(connection, asset_id, year, through_date=through_date)
    if native is not None:
        return native
    import hashlib
    import json
    from .ledger_db import LedgerDB
    cutoff = through_date or f"{year}-12-31"
    evidence = connection.execute("""SELECT tax_year, amount_minor, source_hash
        FROM amortization_entries WHERE asset_id=? AND entry_kind='annual_evidence'
        AND tax_year<=? ORDER BY tax_year""", (asset_id, year)).fetchall()
    if not evidence:
        return None
    asset = connection.execute("""SELECT a.*,d.counterparty_id,d.issued_on FROM assets a
        LEFT JOIN documents d ON d.document_id=a.document_id WHERE a.asset_id=?""", (asset_id,)).fetchone()
    effective_basis = minor(Decimal(asset["amortizable_base_minor"] or asset["cost_minor"]) * Decimal(str(asset["business_use_ratio"] if asset["business_use_ratio"] is not None else 1)))
    prior = sum(row["amount_minor"] for row in evidence if row["tax_year"] < year)
    current, pending, recognized = 0, [], {}
    if len({row["tax_year"] for row in evidence}) != len(evidence) or any(row["amount_minor"] < 0 for row in evidence):
        pending.append("historical_annual_evidence_invalid")
    controls = {row["tax_year"]: row["amount_minor"] for row in evidence}
    opening = 0
    if asset["placed_in_service_on"]:
        for past_year in range(int(asset["placed_in_service_on"][:4]), year):
            if past_year not in controls and opening < effective_basis:
                pending.append("historical_opening_year_missing:" + str(past_year))
            opening += controls.get(past_year, 0)
    proof = [[row["tax_year"], row["amount_minor"], row["source_hash"]]
             for row in evidence if row["tax_year"] < year]
    groups: dict[str, list[dict[str, Any]]] = {}
    for raw in LedgerDB(connection).list_tax_rows(year=year):
        groups.setdefault(raw["transaction_id"], []).append(raw)
    for identifier, treatments in groups.items():
        charges = [row for row in treatments if row["aeat_expense_concept"] == "G03"]
        if not charges:
            continue
        row = charges[0]
        if row["document_type"] != "gastos_book":
            continue
        if row["lifecycle_status"] not in {"posted", "included_in_snapshot"}:
            continue
        inferred_match = (asset["counterparty_id"] is not None and row["counterparty_id"] == asset["counterparty_id"] and row["issued_on"] == asset["issued_on"])
        if row["asset_id"] != asset_id and not (row["asset_count"] > 1 and
                (inferred_match or identifier == asset["acquisition_transaction_id"])):
            continue
        attributed = connection.execute("""SELECT decision_json FROM transaction_tax_attributions
            WHERE transaction_id=? ORDER BY revision DESC LIMIT 1""", (identifier,)).fetchone()
        irpf = json.loads(attributed["decision_json"]).get("irpf") if attributed else None
        dates = [row["transaction_date"], irpf["date"]] if irpf else [row["transaction_date"]]
        if not any(f"{year}-01-01" <= day <= cutoff for day in dates):
            continue
        if row["amortization_entry_id"]:
            pending.append("historical_linked_recognition_requires_review:" + identifier)
            continue
        if irpf:
            pending.append("historical_depreciation_attribution_requires_review:" + identifier)
            continue
        document = connection.execute("SELECT lifecycle_status FROM documents WHERE document_id=?", (row["document_id"],)).fetchone()
        if not row["document_source_hash"] or document[0] not in {"approved", "posted", "included_in_snapshot"}:
            pending.append("historical_depreciation_evidence_invalid:" + identifier)
            continue
        fields = ("tax_code", "aeat_expense_concept", "taxable_base_minor", "vat_minor",
                  "deductible_irpf_minor", "deductible_vat_minor", "withholding_minor", "rate_basis_points")
        if any(len({item[field] for item in treatments if item[field] not in {None, "", "unknown"}}) > 1 for field in fields):
            pending.append("historical_depreciation_conflict:" + identifier)
            continue
        amount = row["deductible_irpf_minor"]
        if row["asset_count"] != 1 or amount is None or amount < 0:
            pending.append("historical_depreciation_invalid:" + identifier)
            continue
        value = (amount, row["transaction_date"], row["document_source_hash"])
        recognized[identifier] = value
        current += amount
        proof.append([identifier, *value])
    annual = [row["amount_minor"] for row in evidence if row["tax_year"] == year]
    if len(annual) > 1:
        pending.append("historical_annual_evidence_ambiguous")
    if annual and annual[0] and not recognized:
        pending.append("historical_depreciation_recognition_missing")
    if not annual and prior < effective_basis and (not recognized or cutoff == f"{year}-12-31"):
        pending.append("historical_current_year_evidence_missing")
    if cutoff == f"{year}-12-31" and annual and current != annual[0]:
        pending.append("historical_annual_recognition_mismatch")
    return {"prior_minor": prior, "current_minor": current, "pending": pending,
            "source_hash": hashlib.sha256(json.dumps(proof).encode()).hexdigest(),
            "source_kind": "historical_posted_g03", "annual_control_minor": annual[0] if len(annual) == 1 else None}


def accumulated_at(connection, asset_id: str, through: str) -> int:
    year = int(through[:4])
    native = book_year_summary(connection, asset_id, year, through_date=through)
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
