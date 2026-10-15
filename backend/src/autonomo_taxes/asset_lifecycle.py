"""Continue reviewed opening balances and prevent depreciation after disposal."""
from __future__ import annotations

from datetime import date, timedelta
import json
from typing import Any

from .depreciation import CALCULATION_VERSION, quarter_end, schedule
from .ledger_db import LedgerDB


def continue_asset(db: LedgerDB, *, asset_id: str, as_of: str, prior_year_minor: int,
                   current_year_minor: int, evidence_ref: str, reason: str) -> dict[str, Any]:
    from .expense_workflow import digest

    day = date.fromisoformat(as_of)
    if as_of != day.isoformat() or quarter_end(day) != day or day > date.today():
        raise ValueError("Opening balance must be at a completed quarter end")
    if any(type(value) is not int or value < 0 for value in (prior_year_minor, current_year_minor)):
        raise ValueError("Opening balances must be nonnegative EUR minor units")
    if not evidence_ref.strip() or not reason.strip():
        raise ValueError("Opening balance evidence and review reason are required")
    opening = dict(as_of=as_of, prior_year_minor=prior_year_minor, current_year_minor=current_year_minor,
                   evidence_ref=evidence_ref, reason=reason)
    with db.transaction():
        asset = db.connection.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        if asset is None or asset["currency"] != "EUR" or asset["depreciation_method"] != "linear":
            raise ValueError("Continuation requires an existing linear EUR asset")
        if not asset["placed_in_service_on"] or asset["placed_in_service_on"] > as_of:
            raise ValueError("Opening balance must follow the in-service date")
        if not asset["aeat_asset_type"] or not asset["amortizable_base_minor"] or not asset["annual_rate_basis_points"] or asset["business_use_ratio"] is None:
            raise ValueError("Review the asset category, basis and annual rate before continuation")
        if db.connection.execute("SELECT 1 FROM asset_disposals WHERE asset_id=?", (asset_id,)).fetchone():
            raise ValueError("A disposed asset cannot start a continuation plan")
        existing = db.connection.execute("SELECT * FROM asset_depreciation_plans WHERE asset_id=?", (asset_id,)).fetchone()
        if existing:
            if json.loads(existing["parameters_json"]).get("opening") == opening:
                return dict(existing)
            raise ValueError("An existing depreciation plan cannot be replaced by an opening balance")
        overlap = db.connection.execute("""SELECT 1 FROM amortization_entries ae JOIN periods p USING(period_id)
            WHERE ae.asset_id=? AND ae.entry_kind<>'annual_evidence' AND p.ends_on>? LIMIT 1""", (asset_id, as_of)).fetchone()
        if overlap:
            raise ValueError("Existing depreciation after the opening date requires reconciliation; no rows were replaced")
        values = dict(basis_minor=asset["amortizable_base_minor"], business_use_ratio=asset["business_use_ratio"],
            annual_rate_basis_points=asset["annual_rate_basis_points"], placed_in_service_on=asset["placed_in_service_on"],
            method="linear", aeat_asset_type=asset["aeat_asset_type"], opening=opening,
            opening_accumulated_minor=prior_year_minor+current_year_minor,
            recognition_starts_on=(day+timedelta(days=1)).isoformat())
        rows = schedule(**{key:values[key] for key in ("basis_minor","business_use_ratio","annual_rate_basis_points",
            "placed_in_service_on","method","opening_accumulated_minor","recognition_starts_on")})
        db.connection.execute("INSERT INTO asset_depreciation_plans VALUES(?,?,?,?,?)",
                              (asset_id,"linear",CALCULATION_VERSION,json.dumps(values),digest(values)))
        for row in rows:
            entry = db.add_amortization_entry(asset_id=asset_id,period_key=row["period_key"],amount_minor=row["amount_minor"],
                source_hash=digest({"parameters":values,"row":row,"version":CALCULATION_VERSION}),include_in_books=False)
            db.connection.execute("UPDATE amortization_entries SET recognition_on=? WHERE amortization_entry_id=?",
                                  (row["recognition_on"],entry["amortization_entry_id"]))
        return dict(db.connection.execute("SELECT * FROM asset_depreciation_plans WHERE asset_id=?", (asset_id,)).fetchone())


def record_disposal(db: LedgerDB, *, asset_id: str, disposed_on: str, last_depreciable_on: str,
                    reason: str, evidence_ref: str) -> dict[str, Any]:
    day = date.fromisoformat(disposed_on)
    last = date.fromisoformat(last_depreciable_on)
    if disposed_on != day.isoformat() or last_depreciable_on != last.isoformat() or last > day or day > date.today():
        raise ValueError("Disposal and last-use dates must be valid, ordered and not future dated")
    if not reason.strip() or not evidence_ref.strip():
        raise ValueError("Disposal reason and evidence are required")
    values = dict(asset_id=asset_id,disposed_on=disposed_on,last_depreciable_on=last_depreciable_on,
                  reason=reason,evidence_ref=evidence_ref)
    with db.transaction():
        asset = db.connection.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        if asset is None or not asset["placed_in_service_on"] or last_depreciable_on < asset["placed_in_service_on"]:
            raise ValueError("Disposal requires an asset already placed in service")
        existing = db.connection.execute("SELECT * FROM asset_disposals WHERE asset_id=?", (asset_id,)).fetchone()
        if existing:
            if dict(existing) == values:
                return values
            raise ValueError("Recorded disposal is immutable")
        overlap = db.connection.execute("""SELECT 1 FROM amortization_entries ae
            JOIN periods p USING(period_id) WHERE ae.asset_id=? AND ae.include_in_books=1
            AND ae.entry_kind<>'annual_evidence' AND COALESCE(ae.recognition_on,p.ends_on)>? LIMIT 1""",
            (asset_id,last_depreciable_on)).fetchone()
        if overlap:
            raise ValueError("Depreciation already recognized after disposal requires an explicit historical correction")
        db.connection.execute("""INSERT INTO asset_disposals VALUES
            (:asset_id,:disposed_on,:last_depreciable_on,:reason,:evidence_ref)""", values)
    return values
