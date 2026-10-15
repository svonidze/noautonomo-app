"""Evidence-backed annual business adjustments, separate from quarterly journals."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

from .ledger_db import LedgerDB
from .tax_engine import CalculationBlocked, TaxRow, calculate_modelo100_business_support
from .tax_row_loader import load_tax_rows
from .tax_rules import difficult_expense_rule_for_year


class AnnualAdjustmentError(ValueError):
    pass


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise AnnualAdjustmentError(f"{label} must be nonempty text (at most 4000 characters)")
    return value.strip()


def _year(value: Any) -> int:
    if type(value) is not int or not 1900 <= value <= 9999:
        raise AnnualAdjustmentError("year must be an integer from 1900 to 9999")
    return value


def _source(db: LedgerDB, transaction_id: str) -> tuple[dict, str]:
    raw = db.connection.execute("""SELECT t.*, p.period_key, p.period_type FROM transactions t
        JOIN periods p USING(period_id) WHERE transaction_id=?""", (transaction_id,)).fetchone()
    if raw is None:
        raise AnnualAdjustmentError("Source transaction not found")
    tx = dict(raw)
    doc = db.connection.execute("SELECT * FROM documents WHERE document_id=?", (tx["document_id"],)).fetchone()
    if not doc or not doc["source_hash"]:
        raise AnnualAdjustmentError("An archived source document is required")
    if tx["lifecycle_status"] in {"void", "duplicate", "rejected"}:
        raise AnnualAdjustmentError("Source transaction has been withdrawn")
    if tx["period_type"] != "annual" or tx["amount_eur_minor"] is None:
        raise AnnualAdjustmentError("Use an annual source record with an explicit EUR valuation")
    fx = None
    if (tx["original_currency"] or tx["currency"]) != "EUR":
        rate = db.connection.execute("SELECT * FROM fx_rates WHERE fx_rate_id=?", (tx["fx_rate_id"],)).fetchone()
        if rate is None or rate["quote_currency"] != "EUR" or rate["base_currency"] != tx["original_currency"]:
            raise AnnualAdjustmentError("Foreign-currency source requires its linked EUR exchange rate")
        from .fx_policy import FXRate, convert_to_eur
        try:
            converted = int(convert_to_eur(Decimal(tx["amount_original_minor"]) / 100,
                FXRate(currency=rate["base_currency"], rate_date=date.fromisoformat(rate["rate_date"]),
                    eur_per_unit=Decimal(rate["rate"]), source=rate["rate_source"],
                    rule_version=rate["rule_version_id"] or "linked-ledger-rate")) * 100)
        except ValueError as exc:
            raise AnnualAdjustmentError(str(exc)) from exc
        if converted != tx["amount_eur_minor"]:
            raise AnnualAdjustmentError("Source EUR valuation differs from its linked rate")
        fx = {key: rate[key] for key in ("rate_date", "base_currency", "quote_currency", "rate", "rate_source", "source_hash")}
    if db.connection.execute("""SELECT 1 FROM tax_treatments WHERE transaction_id=?
        AND (include_modelo130=1 OR include_modelo303=1)""", (transaction_id,)).fetchone():
        raise AnnualAdjustmentError("Source is already classified for quarterly taxation; avoid duplicate recognition")
    facts = {k: tx[k] for k in ("transaction_id", "period_key", "transaction_date", "document_id",
        "counterparty_id", "amount_original_minor", "original_currency", "amount_eur_minor", "fx_rate_id")}
    facts["document_source_hash"] = doc["source_hash"]
    facts["fx"] = fx
    return tx, _hash(facts)


def list_adjustments(db: LedgerDB, year: int) -> list[dict]:
    _year(year)
    return [dict(row) for row in db.connection.execute("""SELECT a.*, t.description,
        t.amount_original_minor, t.original_currency, t.document_id, c.display_name AS counterparty_name,
        CASE WHEN v.annual_adjustment_id IS NULL THEN 0 ELSE 1 END AS voided
        FROM annual_adjustments a LEFT JOIN annual_adjustment_voids v USING(annual_adjustment_id)
        LEFT JOIN transactions t ON t.transaction_id=a.source_transaction_id
        LEFT JOIN counterparties c ON c.counterparty_id=t.counterparty_id
        WHERE a.tax_year=? ORDER BY a.recognized_on, a.created_at, a.annual_adjustment_id""", (year,))]


def _verified_source(db: LedgerDB, tx: dict) -> None:
    if db.connection.execute("""SELECT 1 FROM validation_issues WHERE issue_status='open' AND blocking=1
        AND ((subject_table='transactions' AND subject_id=?) OR (subject_table='documents' AND subject_id=?)
          OR (subject_table='counterparties' AND subject_id=?))""",
        (tx["transaction_id"], tx["document_id"], tx["counterparty_id"])).fetchone():
        raise AnnualAdjustmentError("Resolve the source's specific blocking issues before verifying an annual adjustment")


def record_adjustment(db: LedgerDB, request: dict, *, actor: str) -> dict:
    fields = {"request_id", "year", "source_transaction_id", "expected_row_version", "recognized_on",
        "kind", "amount_eur_minor", "evidence_status", "evidence_ref", "rationale"}
    if not isinstance(request, dict) or set(request) != fields:
        raise AnnualAdjustmentError("Annual adjustment request has unexpected or missing fields")
    year = _year(request["year"])
    try:
        request_id = str(UUID(request["request_id"]))
        source_id = str(UUID(request["source_transaction_id"]))
        recognized = date.fromisoformat(request["recognized_on"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise AnnualAdjustmentError("Valid request/source UUIDs and a YYYY-MM-DD recognition date are required") from exc
    if recognized.year != year or recognized.isoformat() != request["recognized_on"]:
        raise AnnualAdjustmentError("Recognition date must belong to the selected tax year")
    if request["kind"] not in ("income", "expense") or request["evidence_status"] not in ("verified", "provisional"):
        raise AnnualAdjustmentError("Invalid adjustment kind or evidence status")
    amount = request["amount_eur_minor"]
    if type(amount) is not int or not 0 < amount <= 10**14:
        raise AnnualAdjustmentError("amount_eur_minor must be positive integer EUR cents")
    if type(request["expected_row_version"]) is not int or request["expected_row_version"] < 1:
        raise AnnualAdjustmentError("expected_row_version must be a positive integer")
    evidence = _text(request["evidence_ref"], "Evidence reference")
    rationale = _text(request["rationale"], "Rationale")
    actor = _text(actor, "Actor")
    code = "renta:" + request_id
    payload_hash = _hash(request)
    with db.transaction():
        existing = db.connection.execute("SELECT * FROM annual_adjustments WHERE adjustment_code=?", (code,)).fetchone()
        if existing:
            if existing["source_hash"] != payload_hash:
                raise AnnualAdjustmentError("Request ID was already used with different facts")
            if db.connection.execute("SELECT 1 FROM annual_adjustment_voids WHERE annual_adjustment_id=?",
                    (existing["annual_adjustment_id"],)).fetchone():
                raise AnnualAdjustmentError("Request identifies a withdrawn adjustment; review a replacement")
            return dict(existing)
        period = db.ensure_period(str(year))
        if period["status"] != "open":
            raise AnnualAdjustmentError("The annual period is closed")
        tx, snapshot = _source(db, source_id)
        if request["evidence_status"] == "verified":
            _verified_source(db, tx)
        document = dict(db.connection.execute("SELECT * FROM documents WHERE document_id=?", (tx["document_id"],)).fetchone())
        from .storage_service import resolve_verified_filesystem_replica
        replica = resolve_verified_filesystem_replica(db.connection, document_id=document["document_id"])
        original = replica.path if replica else Path(document["source_path"] or "")
        if not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest() != document["source_hash"]:
            raise AnnualAdjustmentError("The archived source must be accessible and match its recorded hash")
        if tx["row_version"] != request["expected_row_version"]:
            raise AnnualAdjustmentError("Source changed; read and review the current version")
        if tx["entry_type"] != request["kind"]:
            raise AnnualAdjustmentError("Adjustment kind does not match the source")
        if db.connection.execute("""SELECT 1 FROM annual_adjustments a
            LEFT JOIN annual_adjustment_voids v USING(annual_adjustment_id)
            WHERE a.source_transaction_id=? AND a.tax_year=? AND v.annual_adjustment_id IS NULL""",
            (source_id, year)).fetchone():
            raise AnnualAdjustmentError("Source already has an active annual adjustment")
        adjustment_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.connection.execute("""INSERT INTO annual_adjustments
            (annual_adjustment_id,tax_year,period_id,adjustment_code,amount_minor,currency,rationale,
             source_hash,row_version,created_at,updated_at,evidence_ref,source_transaction_id,
             recognized_on,adjustment_kind,evidence_status,source_snapshot_hash,actor)
            VALUES (?,?,?,?,?,'EUR',?,?,1,?,?,?,?,?,?,?,?,?)""",
            (adjustment_id, year, period["period_id"], code, amount, rationale, payload_hash, now, now,
             evidence, source_id, recognized.isoformat(), request["kind"], request["evidence_status"], snapshot, actor))
        return dict(db.connection.execute("SELECT * FROM annual_adjustments WHERE annual_adjustment_id=?", (adjustment_id,)).fetchone())


def void_adjustment(db: LedgerDB, adjustment_id: str, *, reason: str, actor: str) -> None:
    reason, actor = _text(reason, "Reason"), _text(actor, "Actor")
    with db.transaction():
        row = db.connection.execute("""SELECT a.*,p.status AS period_status FROM annual_adjustments a
            JOIN periods p USING(period_id) WHERE annual_adjustment_id=?""", (adjustment_id,)).fetchone()
        if row is None or row["period_status"] != "open":
            raise AnnualAdjustmentError("Adjustment is missing or its annual period is closed")
        existing = db.connection.execute("SELECT * FROM annual_adjustment_voids WHERE annual_adjustment_id=?", (adjustment_id,)).fetchone()
        if existing:
            if existing["reason"] != reason:
                raise AnnualAdjustmentError("Adjustment was already withdrawn with a different reason")
            return
        db.connection.execute("INSERT INTO annual_adjustment_voids VALUES (?,?,?,?)",
            (adjustment_id, reason, actor, datetime.now(timezone.utc).isoformat()))


def adjustment_rows(db: LedgerDB, year: int, *, through: date | None = None,
                    include_provisional: bool = False) -> list[TaxRow]:
    result = []
    for row in list_adjustments(db, year):
        if row["voided"]:
            continue
        if row["adjustment_kind"] is None:
            raise CalculationBlocked("Legacy annual adjustment requires explicit review")
        recognized = date.fromisoformat(row["recognized_on"])
        if through is not None and recognized > through:
            continue
        if row["evidence_status"] == "provisional" and not include_provisional:
            raise CalculationBlocked("Annual adjustment still requires its earnings/withholding evidence")
        try:
            tx, snapshot = _source(db, row["source_transaction_id"])
            if row["evidence_status"] == "verified":
                _verified_source(db, tx)
        except AnnualAdjustmentError as exc:
            raise CalculationBlocked(str(exc)) from exc
        if snapshot != row["source_snapshot_hash"]:
            raise CalculationBlocked("Annual adjustment source facts changed; review a replacement")
        amount = Decimal(row["amount_minor"]) / 100
        result.append(TaxRow(transaction_id="annual:" + row["annual_adjustment_id"], tax_date=recognized,
            kind=row["adjustment_kind"], amount_eur=amount,
            taxable_base_eur=amount if row["adjustment_kind"] == "income" else Decimal(0),
            deductible_irpf_eur=amount if row["adjustment_kind"] == "expense" else Decimal(0),
            include_modelo130=True, source_transaction_id=tx["transaction_id"],
            counterparty_id=tx["counterparty_id"] or "", document_id=tx["document_id"] or ""))
    return result


def annual_preview(db: LedgerDB, year: int, *, through: date | None = None) -> dict:
    _year(year)
    through = through or max(date(year, 1, 1), min(date.today(), date(year, 12, 31)))
    if through.year != year:
        raise AnnualAdjustmentError("Preview cutoff must belong to the selected year")
    adjustments = list_adjustments(db, year)
    provisional = any(not a["voided"] and a["evidence_status"] == "provisional"
        and a["recognized_on"] <= through.isoformat() for a in adjustments)
    rule = difficult_expense_rule_for_year(year)
    result = calculate_modelo100_business_support(
        load_tax_rows(db, year, allow_authoritative_history=True, through=through), year=year,
        difficult_expenses_rate=rule.rate, difficult_expenses_cap=rule.annual_cap_eur,
        annual_adjustment_rows=adjustment_rows(db, year, through=through, include_provisional=True))
    return {"year": year, "as_of": through.isoformat(), "status": "provisional" if provisional else "preliminary",
        "filing_ready": False, "values": json.loads(json.dumps(result.values, default=str)), "lineage": result.lineage,
        "annual_adjustments": adjustments}
