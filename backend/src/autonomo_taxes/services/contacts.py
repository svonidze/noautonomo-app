"""Shared application operations for CLI and HTTP adapters."""

from __future__ import annotations
from typing import Any, Mapping
from http import HTTPStatus
from contextlib import closing
import sqlite3
from ..ledger_db import StaleRowVersionError, open as open_ledger_db
from ..status_context import simple_context
from .common import ServiceApiError, ServiceError, _counterparty_api_state, _exact_object_fields, _validated_counterparty_id


class ContactsService:
    def counterparties(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT
                    c.counterparty_id,
                    c.display_name,
                    c.row_version,
                    c.name_is_manual,
                    c.country_code,
                    c.tax_id,
                    c.vat_id,
                    c.roi_status,
                    c.professional_supplier,
                    c.retention_expected,
                    COUNT(DISTINCT t.transaction_id) AS transaction_count,
                    MAX(t.transaction_date) AS last_transaction_on
                FROM counterparties c
                LEFT JOIN transactions t ON t.counterparty_id = c.counterparty_id
                GROUP BY c.counterparty_id
                ORDER BY c.display_name COLLATE NOCASE
                """
            ).fetchall()
            builder = self._status_context(connection)
            actions_by_counterparty: dict[str, list[dict[str, Any]]] = {}
            for transaction in connection.execute(
                "SELECT t.transaction_id,t.counterparty_id,p.period_key FROM transactions t JOIN periods p USING(period_id) WHERE t.lifecycle_status='needs_review' ORDER BY t.transaction_date DESC"):
                actions = actions_by_counterparty.setdefault(transaction['counterparty_id'], [])
                if len(actions) < 10:
                    actions.extend(builder.review_actions(transaction['transaction_id'], transaction['period_key']))
            result = []
            for raw in rows:
                row = dict(raw)
                context = simple_context("counterparty", row)
                context["reasons"] = builder.issues([("counterparties", row["counterparty_id"])])
                context["actions"] = actions_by_counterparty.get(row["counterparty_id"], [])
                result.append(row | {"ui_context": context})
            return result

    def counterparty_detail(self, counterparty_id: str) -> dict[str, Any]:
        counterparty_id = _validated_counterparty_id(counterparty_id)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            _counterparty_api_state(connection, counterparty_id)
            row = dict(connection.execute(
                """SELECT counterparty_id, display_name, row_version, name_is_manual,
                          country_code, tax_id, vat_id, roi_status, legal_form, email, phone,
                          professional_supplier, retention_expected
                   FROM counterparties WHERE counterparty_id = ?""", (counterparty_id,),
            ).fetchone())
            builder = self._status_context(connection)
            context = simple_context("counterparty", row)
            context["reasons"] = builder.issues([("counterparties", counterparty_id)])
            periods = [item["period_key"] for item in connection.execute(
                """SELECT DISTINCT p.period_key FROM transactions t
                   JOIN periods p ON p.period_id=t.period_id
                   WHERE t.counterparty_id = ? AND p.period_type = 'quarter'
                   ORDER BY p.period_key DESC""", (counterparty_id,),
            )]
            return {"counterparty": row | {"ui_context": context}, "periods": periods}

    def counterparty_transactions(
        self, counterparty_id: str, *, period_key: str | None = None,
        offset: int = 0, limit: int = 50,
    ) -> dict[str, Any]:
        counterparty_id = _validated_counterparty_id(counterparty_id)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ServiceError("Counterparty page requires offset >= 0 and limit between 1 and 100")
        period = self._validate_period(period_key) if period_key is not None else None
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            _counterparty_api_state(connection, counterparty_id)
            period_row = self._period_row(connection, period, missing_ok=True) if period else None
            if period and period_row is None:
                # A valid quarter without a row yet has no operations.
                count, rows = 0, []
            else:
                period_id = period_row["period_id"] if period_row else None
                count = connection.execute(
                    """SELECT COUNT(*) FROM transactions
                       WHERE counterparty_id = ? AND (? IS NULL OR period_id = ?)""",
                    (counterparty_id, period_id, period_id),
                ).fetchone()[0]
                rows = self._transactions(
                    connection, period_id=period_id, counterparty_id=counterparty_id,
                    offset=offset, limit=limit,
                )
            return {
                "counterparty_id": counterparty_id, "period": period, "rows": rows,
                "offset": offset, "limit": limit, "matching_count": count,
                "has_more": offset + len(rows) < count, "next_offset": offset + len(rows),
            }

    def counterparty_name_history(self, counterparty_id: str) -> dict[str, Any]:
        counterparty_id = _validated_counterparty_id(counterparty_id)
        with open_ledger_db(self.config.database, read_only=True) as db:
            _counterparty_api_state(db.connection, counterparty_id)
            return {
                "counterparty_id": counterparty_id,
                "changes": db.counterparty_name_history(counterparty_id),
            }

    def rename_counterparty(
        self, counterparty_id: str, payload: Mapping[str, Any], *, actor: str | None = None,
        change_source: str = "web",
    ) -> dict[str, Any]:
        counterparty_id = _validated_counterparty_id(counterparty_id)
        _exact_object_fields(payload, {"display_name", "expected_row_version"}, "rename request")
        version = payload["expected_row_version"]
        if type(version) is not int or version < 1:
            raise ServiceApiError(
                HTTPStatus.BAD_REQUEST, "invalid_request", "expected_row_version must be a positive integer",
            )
        try:
            with open_ledger_db(self.config.database) as db, db.transaction():
                _counterparty_api_state(db.connection, counterparty_id)
                try:
                    updated = db.rename_counterparty(
                        counterparty_id, display_name=payload["display_name"],
                        expected_row_version=version, change_source=change_source, actor=actor,
                    )
                except StaleRowVersionError as exc:
                    raise ServiceApiError(
                        HTTPStatus.CONFLICT, "stale_counterparty", "Counterparty has changed; review the current name",
                        current=_counterparty_api_state(db.connection, counterparty_id),
                    ) from exc
                except ValueError as exc:
                    raise ServiceApiError(HTTPStatus.BAD_REQUEST, "invalid_name", str(exc)) from exc
                return {
                    **_counterparty_api_state(db.connection, counterparty_id),
                    "changed": updated["row_version"] != version,
                }
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise ServiceApiError(
                    HTTPStatus.SERVICE_UNAVAILABLE, "counterparty_busy", "Database is busy; retry saving",
                ) from exc
            raise
