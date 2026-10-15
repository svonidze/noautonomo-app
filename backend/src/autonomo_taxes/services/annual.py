"""Annual preparation reads and explicit adjustment recording."""
from __future__ import annotations

from datetime import date

from ..annual_adjustments import AnnualAdjustmentError, annual_preview, list_adjustments, record_adjustment, void_adjustment
from ..ledger_db import open as open_ledger_db
from ..tax_engine import CalculationBlocked
from .common import ServiceError


class AnnualService:
    def annual_adjustments(self, year: str) -> dict:
        try:
            parsed = int(year)
            if str(parsed) != year:
                raise ValueError("year must use YYYY")
            with open_ledger_db(self.config.database, read_only=True) as db:
                try:
                    return annual_preview(db, parsed, through=max(date(parsed, 1, 1), min(self.today, date(parsed, 12, 31))))
                except CalculationBlocked as exc:
                    return {"year": parsed, "status": "blocked", "filing_ready": False,
                        "reason": str(exc), "values": None, "annual_adjustments": list_adjustments(db, parsed)}
        except (ValueError, AnnualAdjustmentError) as exc:
            raise ServiceError(str(exc)) from exc

    def record_annual_adjustment(self, payload: dict, *, actor: str | None = None) -> dict:
        try:
            with open_ledger_db(self.config.database) as db:
                return record_adjustment(db, payload, actor=actor or "web")
        except AnnualAdjustmentError as exc:
            raise ServiceError(str(exc)) from exc

    def void_annual_adjustment(self, adjustment_id: str, payload: dict, *, actor: str | None = None) -> dict:
        if set(payload) != {"reason"}:
            raise ServiceError("A withdrawal reason is required")
        try:
            with open_ledger_db(self.config.database) as db:
                void_adjustment(db, adjustment_id, reason=payload["reason"], actor=actor or "web")
            return {"annual_adjustment_id": adjustment_id, "voided": True}
        except AnnualAdjustmentError as exc:
            raise ServiceError(str(exc)) from exc
