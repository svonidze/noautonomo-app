"""Shared application operations for CLI and HTTP adapters."""

from __future__ import annotations
from typing import Any, Mapping
from pathlib import Path
from http import HTTPStatus
from contextlib import closing
from ..dashboard_service import build_dashboard_from_db
from ..public_demo_runtime import AS_OF as DEMO_AS_OF
from datetime import date, timedelta
import re
import json
import mimetypes
from ..analytics_series import AnalyticsQuery, build_analytics
from ..aeat_documents import unified_aeat_documents
from ..ledger_db import SchemaVersionError
from .common import RETA_ELECTION_FIELDS, RETA_UNAVAILABLE_NULL_FIELDS
from ..reta_ledger import RetaInputUnavailable, ledger_bracket_check
from ..expense_view import expense_summary
from ..ledger_db import open as open_ledger_db
from ..status_context import simple_context
from ..tax_result_view import (
    build_tax_summary,
    compact_tax_preview as _compact_tax_preview,
    form_results as _dashboard_tax_forms,
    period_status as _tax_period_status,
    year_form_results,
)
from .common import _missing_period, ServiceApiError, ServiceError, UUID_RE, _empty_scope, _exact_object_fields, _is_relative_to, _minor_to_text, _posting_preview_summary, _review_summary


class FinancialsService:
    def dashboard(self, period_key: str) -> dict[str, Any]:
        period = self._validate_period(period_key)
        today = self.today
        cached = self._load_cached_dashboard(period)
        with closing(self._connect()) as connection:
            period_row = self._period_row(connection, period, missing_ok=True)
            # A quarter without a row (a fresh ledger) has no records: '' matches no period_id.
            period_id = period_row["period_id"] if period_row else ""
            context_builder = self._status_context(connection, today=today)
            totals = self._transaction_totals(connection, period_id)
            expense_rows = self._transactions(
                connection, period_id=period_id, entry_type="expense", limit=-1, today=today,
                context_builder=context_builder,
            )
            recent = self._transactions(
                connection,
                period_id=period_id,
                limit=8,
                today=today,
                context_builder=context_builder,
            )
            open_issues = self._issues(
                connection,
                period_id=period_id,
                limit=6, context_builder=context_builder,
            )
            open_issue_count = connection.execute(
                "SELECT COUNT(*) FROM validation_issues WHERE period_id = ? AND issue_status = 'open'",
                (period_id,),
            ).fetchone()[0]
            # Review-queue rows of this quarter without a EUR amount (the analytics quality count
            # covers two years and every status, so it cannot drive a task for this quarter), and
            # the approved ones among them, whose posting the missing rate blocks. Foreign means
            # what posting.py checks: the original currency first, then the booked one.
            missing_fx_count, missing_fx_blocked_count = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(lifecycle_status = 'approved'), 0) FROM transactions
                WHERE period_id = ?
                  AND lifecycle_status IN ('received', 'extracted', 'needs_review', 'approved')
                  AND amount_eur_minor IS NULL
                  AND UPPER(COALESCE(NULLIF(original_currency, ''), currency, '')) <> 'EUR'
                """,
                (period_id,),
            ).fetchone()
            # Any quarter: until the first posting the overview leads with its first-run steps.
            ledger_has_postings = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM transactions"
                " WHERE lifecycle_status IN ('posted', 'included_in_snapshot'))"
            ).fetchone()[0]
            obligations = self._obligations(
                connection,
                period_id=period_id,
            )
            tax_forms = _dashboard_tax_forms(
                connection,
                period_key=period,
                obligations=obligations,
                cached=cached,
            )
            posting_preview = context_builder.preview_payload(period) if period_row else {}
            document_counts = {
                row["lifecycle_status"]: int(row["count"])
                for row in connection.execute(
                    """
                    SELECT lifecycle_status, COUNT(*) AS count
                    FROM documents
                    WHERE period_id = ?
                    GROUP BY lifecycle_status
                    """,
                    (period_id,),
                ).fetchall()
            }
            review_rows = self._transactions(
                connection,
                period_id=period_id,
                lifecycle_status="review",
                limit=500, context_builder=context_builder,
            )
            review_summary = _review_summary(review_rows)
        return {
            "period": dict(period_row) if period_row else _missing_period(period),
            "totals": totals,
            "expense_summary": expense_summary(expense_rows),
            "expense_view_as_of": today.isoformat(),
            "recent_transactions": recent,
            "open_issues": open_issues,
            "open_issue_count": open_issue_count,
            "missing_fx_count": missing_fx_count,
            "missing_fx_blocked_count": missing_fx_blocked_count,
            "ledger_has_postings": bool(ledger_has_postings),
            "obligations": obligations,
            "document_counts": document_counts,
            "tax_preview": _compact_tax_preview(cached),
            "tax_forms": tax_forms,
            "forecast_as_of": cached.get("as_of") if cached else None,
            "has_tax_attributions": bool(cached.get("tax_attribution_state_hash")),
            "filing_ready": bool(cached and cached.get("filing_ready")),
            "posting_preview_summary": _posting_preview_summary(posting_preview),
            "submission_ready": bool(cached and cached.get("submission_ready")),
            "readyCount": review_summary["ready"],
            "posting_summary": review_summary,
        }

    def taxes(self, period_key: str, *, as_of: date | None = None) -> dict[str, Any]:
        period = self._validate_period(period_key)
        cached = self._load_cached_dashboard(period)
        with closing(self._connect()) as connection:
            period_row = self._period_row(connection, period, missing_ok=True)
            obligations = self._obligations(
                connection,
                period_id=period_row["period_id"],
            ) if period_row else []
            tax_forms = _dashboard_tax_forms(
                connection,
                period_key=period,
                obligations=obligations,
                cached=cached,
            )
            period_details = _tax_period_status(connection, period) or _missing_period(period)
            period_state, tax_summary = build_tax_summary(
                connection,
                period=period_details,
                obligations=obligations,
                tax_forms=tax_forms,
                cached=cached,
                as_of=self.today if self.config.public_demo else (as_of or self.today),
            )
        return {
            "period": period,
            "period_state": period_state,
            "obligations": obligations,
            "tax_preview": _compact_tax_preview(cached),
            "tax_forms": tax_forms,
            "tax_summary": tax_summary,
            "forecast_as_of": cached.get("as_of") if cached else None,
            "warnings": list(cached.get("warnings", [])) if cached else [],
        }

    def analytics(self, period_key: str, *, as_of: str | None = None) -> dict[str, Any]:
        period = self._validate_period(period_key)
        if self.config.public_demo and as_of not in {None, DEMO_AS_OF}:
            raise ServiceError("Public demo calculation date is fixed")
        if as_of is None:
            as_of_date = self.today
        else:
            try:
                as_of_date = date.fromisoformat(as_of)
            except ValueError as exc:
                raise ServiceError(f"Invalid as_of date: {as_of}") from exc
        year = int(period[:4])
        quarters = [f"{year}-Q{index}" for index in range(1, int(period[-1]) + 1)]
        analytics_query = AnalyticsQuery(period_key=period, as_of=as_of_date)
        with closing(self._connect()) as connection:

            def load_obligations(quarter_key: str) -> list[dict[str, Any]]:
                row = connection.execute(
                    "SELECT period_id FROM periods"
                    " WHERE period_key = ? AND period_type = 'quarter'",
                    (quarter_key,),
                ).fetchone()
                if row is None:
                    return []
                return self._obligations(connection, period_id=row["period_id"])

            year_forms = year_form_results(
                connection,
                quarters=quarters,
                load_cached=self._load_cached_dashboard,
                load_obligations=load_obligations,
            )
            return build_analytics(connection, analytics_query, year_forms=year_forms)

    def _transaction_totals(
        self, connection: sqlite3.Connection, period_id: str
    ) -> dict[str, dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT
                CASE
                    WHEN t.lifecycle_status IN ('posted', 'included_in_snapshot')
                        THEN 'actual'
                    WHEN t.lifecycle_status = 'approved'
                        THEN 'forecast'
                    ELSE 'review'
                END AS scope,
                t.entry_type,
                COUNT(*) AS transaction_count,
                COALESCE(SUM(
                    CASE
                        WHEN t.amount_eur_minor IS NOT NULL THEN t.amount_eur_minor
                        WHEN t.currency = 'EUR' THEN t.amount_minor
                        ELSE 0
                    END
                ), 0) AS gross_minor,
                COALESCE(SUM(
                    CASE
                        WHEN t.amount_eur_minor IS NULL AND COALESCE(t.currency, '') <> 'EUR'
                            THEN 1
                        ELSE 0
                    END
                ), 0) AS missing_amount_count,
                COALESCE(SUM(tt.deductible_irpf_minor), 0) AS deductible_irpf_minor,
                COALESCE(SUM(tt.deductible_vat_minor), 0) AS deductible_vat_minor
            FROM transactions t
            LEFT JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
            WHERE t.period_id = ?
              AND t.lifecycle_status NOT IN ('duplicate', 'rejected', 'void')
            GROUP BY scope, t.entry_type
            """,
            (period_id,),
        ).fetchall()
        result: dict[str, dict[str, Any]] = {
            "actual": _empty_scope(),
            "forecast": _empty_scope(),
            "review": _empty_scope(),
        }
        for row in rows:
            scope = result[row["scope"]]
            scope["transaction_count"] += int(row["transaction_count"])
            if row["entry_type"] == "income":
                scope["income_transaction_count"] += int(row["transaction_count"])
                scope["income_eur"] = _minor_to_text(row["gross_minor"])
                # Display aggregates: `income_eur` counts rows without a EUR amount as zero.
                scope["income_known_amount_minor"] = int(row["gross_minor"])
                scope["income_missing_amount_count"] = int(row["missing_amount_count"])
            elif row["entry_type"] == "expense":
                scope["expense_transaction_count"] += int(row["transaction_count"])
                scope["expense_gross_eur"] = _minor_to_text(row["gross_minor"])
                scope["deductible_irpf_eur"] = _minor_to_text(
                    row["deductible_irpf_minor"]
                )
                scope["deductible_vat_eur"] = _minor_to_text(
                    row["deductible_vat_minor"]
                )
        return result

    def _obligations(
        self,
        connection: sqlite3.Connection,
        *,
        period_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT
                o.obligation_id,
                o.obligation_code,
                o.determination,
                o.filing_status,
                o.blocking,
                o.explanation,
                o.filed_at,
                COALESCE(tc.internal_due_on, o.due_on) AS internal_due_on,
                tc.direct_debit_cutoff_on,
                COALESCE(tc.statutory_due_on, o.due_on) AS statutory_due_on,
                tc.deadline_status,
                tc.source_url
            FROM obligations o
            LEFT JOIN tax_calendar_entries tc
                ON tc.period_id = o.period_id
                AND tc.form_code = o.obligation_code
            WHERE o.period_id = ?
            ORDER BY
                CASE o.determination
                    WHEN 'due' THEN 0
                    WHEN 'unknown' THEN 1
                    ELSE 2
                END,
                o.obligation_code
            """,
            (period_id,),
        ).fetchall()
        return [dict(row) | {"ui_context": simple_context("obligation", dict(row))} for row in rows]

    def _load_cached_dashboard(self, period_key: str) -> dict[str, Any]:
        if self.config.public_demo:
            with open_ledger_db(self.config.database, read_only=True) as db:
                value = build_dashboard_from_db(db, period_key=period_key, as_of=self.today)
            return json.loads(json.dumps(value, default=str))
        from ..tax_attribution import attribution_state_hash
        with open_ledger_db(self.config.database, read_only=True) as db:
            attribution_hash = attribution_state_hash(db)
        candidates = (self.config.cache_root / period_key / "dashboard.json",)
        for path in candidates:
            if path.is_file():
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if value.get("period") == period_key and value.get("tax_attribution_state_hash") == attribution_hash:
                    return value
        return {}


    def aeat_documents(
        self,
        *,
        year: str | None = None,
        form_code: str | None = None,
        document_kind: str | None = None,
        status: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            rows = unified_aeat_documents(connection)
        search = (query or "").strip().casefold()
        filtered: list[dict[str, Any]] = []
        for row in rows:
            occurred = str(row.get("occurred_at") or "")
            if year and occurred[:4] != year:
                continue
            if form_code and str(row.get("form_code") or "") != form_code:
                continue
            if document_kind and row.get("document_kind") != document_kind:
                continue
            if status and row.get("status") != status:
                continue
            if search:
                haystack = " ".join(
                    str(row.get(key) or "")
                    for key in (
                        "title",
                        "form_code",
                        "procedure_kind",
                        "procedure_code",
                        "submission_reference",
                        "justificante_number",
                        "verification_code",
                        "period_key",
                    )
                ).casefold()
                if search not in haystack:
                    continue
            source_available = bool(row.get("source_available"))
            if row.get("record_source") == "filing_snapshot":
                try:
                    self.filing_file(str(row["record_id"]))
                except (FileNotFoundError, ServiceError):
                    source_available = False
            filtered.append({**row, "source_available": source_available})
        return {"rows": filtered, "total": len(filtered)}


    def update_aeat_case_status(
        self, aeat_case_id: str, payload: Mapping[str, Any], *, actor: str,
    ) -> dict[str, Any]:
        required = {
            "status", "occurred_at", "evidence_reference", "notes", "expected_row_version",
        }
        _exact_object_fields(payload, required, "AEAT status request")
        with open_ledger_db(self.config.database) as db:
            return db.record_aeat_case_status(
                aeat_case_id,
                status=str(payload["status"]),
                occurred_at=str(payload["occurred_at"]),
                evidence_reference=str(payload["evidence_reference"]),
                notes=str(payload["notes"]) if payload["notes"] is not None else None,
                actor=actor,
                expected_row_version=int(payload["expected_row_version"]),
            )


    def filing_file(self, filing_snapshot_id: str) -> tuple[Path, str]:
        if not UUID_RE.fullmatch(filing_snapshot_id):
            raise ServiceError("Invalid filing snapshot id")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT source_reference FROM filing_snapshots WHERE filing_snapshot_id = ?",
                (filing_snapshot_id,),
            ).fetchone()
        if row is None or not row["source_reference"]:
            raise FileNotFoundError("Filing source is unavailable")
        if self.config.public_demo:
            return self.document_file(filing_snapshot_id)
        path = self.resolve_document_path(str(row["source_reference"]))
        if not any(_is_relative_to(path, root.resolve()) for root in self.document_roots):
            raise ServiceError("Filing source is outside configured evidence roots")
        if not path.is_file():
            raise FileNotFoundError(path)
        return path, mimetypes.guess_type(path.name)[0] or "application/pdf"


    def reta_check(
        self, year: str, *, through: str | None = None, as_of: date | None = None,
    ) -> dict[str, Any]:
        """Read-only RETA bracket check plus all live base rows.

        Decision support only: nothing here feeds tax cash due. A check that
        cannot run is an ``unknown`` result with a reason, not an error.
        """
        if not re.fullmatch(r"[0-9]{4}", year):
            raise ServiceError("Year must use YYYY")
        today = self.today if self.config.public_demo else (as_of or self.today)
        through_date = None
        if through is not None:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", through):
                raise ServiceError("through must use YYYY-MM-DD")
            try:
                through_date = date.fromisoformat(through)
            except ValueError as exc:
                raise ServiceError("through must use YYYY-MM-DD") from exc
            # Checked before the ledger so the answer never depends on its contents.
            if (
                through_date.year != int(year)
                or (through_date + timedelta(days=1)).day != 1
                or through_date > today
            ):
                raise ServiceError("through must be a month end of the year, not after today")
        try:
            with open_ledger_db(self.config.database, read_only=True) as db:
                elections = [
                    {key: row[key] for key in RETA_ELECTION_FIELDS}
                    for row in db.list_reta_base_elections()
                ]
                try:
                    result = ledger_bracket_check(db, year=int(year), as_of=today, through=through_date)
                except RetaInputUnavailable as exc:
                    reasons = [exc.reason]
                    if db.reta_rate_table(int(year)) is None:
                        reasons.append("table_unavailable")
                    result = {
                        "year": int(year), "as_of": today.isoformat(), "through": None,
                        "status": "unknown", "reasons": reasons, "months": [],
                        **dict.fromkeys(RETA_UNAVAILABLE_NULL_FIELDS),
                    }
        except SchemaVersionError:
            raise ServiceApiError(
                HTTPStatus.CONFLICT, "schema_upgrade_required",
                "An operator must upgrade the database before viewing the RETA check.",
            ) from None
        return {**result, "elections": elections}
