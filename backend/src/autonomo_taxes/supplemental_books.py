"""Evidence for a period with no client provisions or disbursements.

Movement accounting is deliberately unsupported until its posting rules exist.
Missing source sheets never establish an absence of movements.
"""
from __future__ import annotations

import csv
from datetime import date
import json
from pathlib import Path
from typing import Any


SUPLIDOS_FIELDS = (
    "numero_anotacion", "naturaleza_movimiento", "fecha", "importe_eur",
    "nombre_pagador_o_perceptor", "nif_pagador_o_perceptor", "numero_factura_o_documento",
)


def write_supplemental_books(
    root: Path, *, period_key: str, starts_on: str, ends_on: str,
    evidence: dict[str, Any] | None,
) -> tuple[dict[str, Path], dict[str, Any]]:
    files: dict[str, Path] = {}
    status = {"period_key": period_key, "starts_on": starts_on, "ends_on": ends_on,
              "book": "provisiones_fondos_suplidos", "status": "unconfirmed",
              "blockers": ["provisiones_suplidos_confirmation_missing"],
              "movements_supported": False}
    if evidence is not None:
        required = {"period_key", "confirmed_on", "confirmed_by", "evidence_ref", "has_movements"}
        if not isinstance(evidence, dict) or not required <= evidence.keys() or any(
            not isinstance(evidence[key], str) or not evidence[key].strip()
            for key in required - {"has_movements"}
        ):
            raise ValueError("Supplemental book evidence requires period, confirmation date, author, reference and movement status")
        if evidence["period_key"] != period_key:
            raise ValueError("Supplemental book evidence belongs to another period")
        confirmed_on = date.fromisoformat(evidence["confirmed_on"])
        if not date.fromisoformat(ends_on) <= confirmed_on <= date.today():
            raise ValueError("Confirmation must follow the period end and cannot be future dated")
        if type(evidence["has_movements"]) is not bool:
            raise ValueError("has_movements must be a boolean")
        files["supplemental_book_evidence"] = root / "supplemental-book-evidence.json"
        files["supplemental_book_evidence"].write_text(
            json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if evidence["has_movements"]:
            status.update(status="unsupported_movements", blockers=["provisiones_suplidos_movements_unsupported"])
        else:
            files["provisiones_suplidos"] = root / "provisiones-suplidos.csv"
            with files["provisiones_suplidos"].open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(SUPLIDOS_FIELDS)
            status.update(status="confirmed_no_movements", blockers=[], row_count=0,
                          period_total_eur="0.00", evidence_ref=evidence["evidence_ref"])
    files["supplemental_book_status"] = root / "supplemental-book-status.json"
    files["supplemental_book_status"].write_text(
        json.dumps(status, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return files, status
