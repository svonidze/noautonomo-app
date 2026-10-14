from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from .books import BookExportResult, write_accounting_books
from .ledger_db import LedgerDB
from .supplemental_books import write_supplemental_books


MANIFEST_FILENAME = "filing-package-manifest.json"
FINAL_SNAPSHOT_STATUSES = {"filed", "submitted", "final"}


def write_filing_package(
    database: LedgerDB, output_dir: str | Path, *, period_key: str,
    supplemental_evidence: dict[str, Any] | None = None,
) -> Path:
    root = Path(output_dir)
    # Each destination is a new version, including when another export is in progress.
    root.mkdir(parents=True, exist_ok=False)
    try:
        export = write_accounting_books(database, root, period_key=period_key)
        period = database.connection.execute(
            "SELECT starts_on, ends_on FROM periods WHERE period_key=?", (period_key,),
        ).fetchone()
        files, _ = write_supplemental_books(root, period_key=period_key,
            starts_on=period["starts_on"], ends_on=period["ends_on"], evidence=supplemental_evidence)
        export.files.update(files)
        export.row_counts.update({key: 0 for key in files})
        manifest = _build_manifest(database, export)
        manifest_path = root / MANIFEST_FILENAME
        manifest_path.write_text(_serialize_json(manifest), encoding="utf-8")
    except BaseException:
        shutil.rmtree(root)
        raise
    return manifest_path


def verify_filing_package_integrity(
    manifest_path: str | Path, *, expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Check retained bytes independently of the current database or export rules."""
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    missing_artifacts: list[str] = []
    mismatched_artifacts: list[str] = []
    invalid_artifacts: list[str] = []
    seen: set[str] = set()
    for artifact in manifest.get("artifacts", []):
        name = artifact["filename"]
        if not name or Path(name).name != name or name in {".", "..", MANIFEST_FILENAME} or name in seen:
            invalid_artifacts.append(name)
            continue
        seen.add(name)
        artifact_path = manifest_file.parent / name
        if artifact_path.is_symlink():
            invalid_artifacts.append(name)
            continue
        if not artifact_path.is_file():
            missing_artifacts.append(name)
            continue
        actual_hash = _sha256_file(artifact_path)
        if actual_hash != artifact["sha256"] or artifact_path.stat().st_size != artifact["size_bytes"]:
            mismatched_artifacts.append(name)
    manifest_sha256 = _sha256_file(manifest_file)
    trusted_manifest_matches = (None if expected_manifest_sha256 is None
                                else manifest_sha256 == expected_manifest_sha256)
    return {
        "integrity_ok": bool(seen) and not (missing_artifacts or mismatched_artifacts or invalid_artifacts)
                        and trusted_manifest_matches is not False and not manifest_file.is_symlink(),
        "manifest_sha256": manifest_sha256,
        "trusted_manifest_matches": trusted_manifest_matches,
        "missing_artifacts": missing_artifacts,
        "mismatched_artifacts": mismatched_artifacts,
        "invalid_artifacts": invalid_artifacts,
    }


def verify_filing_package(database: LedgerDB, manifest_path: str | Path) -> dict[str, Any]:
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    integrity = verify_filing_package_integrity(manifest_file)
    evidence_file = manifest_file.parent / "supplemental-book-evidence.json"
    evidence = (json.loads(evidence_file.read_text(encoding="utf-8"))
                if integrity["integrity_ok"] and any(
                    item.get("filename") == evidence_file.name for item in manifest.get("artifacts", [])
                ) else None)

    with tempfile.TemporaryDirectory() as tmp:
        regenerated_path = write_filing_package(database, Path(tmp) / "package",
            period_key=manifest["period"]["period_key"], supplemental_evidence=evidence)
        regenerated = json.loads(regenerated_path.read_text(encoding="utf-8"))

    manifest_matches = regenerated == manifest
    ok = manifest_matches and integrity["integrity_ok"]
    return {
        "ok": ok,
        "submission_ready": bool(regenerated.get("submission_ready")),
        "readiness_blockers": regenerated.get("readiness_blockers", []),
        "manifest_matches": manifest_matches,
        "currentness_ok": manifest_matches,
        "completeness_verified": False,
        **integrity,
        "expected_manifest": regenerated,
    }


def _build_manifest(database: LedgerDB, export: BookExportResult) -> dict[str, Any]:
    connection = database.connection
    period = connection.execute(
        "SELECT period_key, starts_on, ends_on, status, source_hash, row_version FROM periods WHERE period_key = ?",
        (export.period_key,),
    ).fetchone()
    if period is None:
        raise ValueError(f"Unknown period: {export.period_key}")

    artifacts = []
    for name in sorted(export.files):
        path = export.files[name]
        artifacts.append(
            {
                "book": name,
                "filename": path.name,
                "row_count": export.row_counts[name],
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )

    rule_versions = [
        {
            "rule_version_id": row["rule_version_id"],
            "rule_name": row["rule_name"],
            "version": row["version"],
            "activated_at": row["activated_at"] or "",
            "source_hash": row["source_hash"],
        }
        for row in connection.execute(
            """
            SELECT DISTINCT rv.rule_version_id, rv.rule_name, rv.version, rv.activated_at, rv.source_hash
            FROM rule_versions rv
            WHERE rv.rule_version_id IN (
                SELECT tt.rule_version_id
                FROM tax_treatments tt
                JOIN transactions t ON t.transaction_id = tt.transaction_id
                JOIN periods p ON p.period_id = t.period_id
                WHERE p.period_key = ? AND tt.rule_version_id IS NOT NULL
                UNION
                SELECT fx.rule_version_id
                FROM fx_rates fx
                JOIN transactions t ON t.fx_rate_id = fx.fx_rate_id
                JOIN periods p ON p.period_id = t.period_id
                WHERE p.period_key = ? AND fx.rule_version_id IS NOT NULL
            )
            ORDER BY rv.rule_name, rv.version, rv.rule_version_id
            """,
            (export.period_key, export.period_key),
        ).fetchall()
    ]

    snapshot_refs = [
        {
            "filing_snapshot_id": row["filing_snapshot_id"],
            "snapshot_hash": row["snapshot_hash"],
            "filed_on": row["filed_on"],
            "status": row["status"],
            "manifest_path": row["manifest_path"] or "",
            "source_hash": row["source_hash"],
        }
        for row in connection.execute(
            """
            SELECT
                fs.filing_snapshot_id,
                fs.snapshot_hash,
                fs.filed_on,
                fs.status,
                fs.manifest_path,
                fs.source_hash
            FROM filing_snapshots fs
            JOIN periods p ON p.period_id = fs.period_id
            WHERE p.period_key = ?
            ORDER BY filed_on, filing_snapshot_id
            """,
            (export.period_key,),
        ).fetchall()
    ]
    readiness_reasons: list[str] = []
    if period["status"] not in {"closed", "amended"}:
        readiness_reasons.append("period_not_closed")
    if not any(row["status"] in FINAL_SNAPSHOT_STATUSES for row in snapshot_refs):
        readiness_reasons.append("final_filing_snapshot_missing")
    else:
        # Historical recognition can leave the latest-only loader's year entirely.
        year_start = period['starts_on'][:4] + '-01-01'
        end = period['ends_on']
        attribution_history = connection.execute("""
            SELECT 1 FROM transaction_tax_attributions a
            JOIN transactions t ON t.transaction_id=a.transaction_id
            WHERE t.transaction_date BETWEEN ? AND ?
               OR json_extract(a.decision_json, '$.irpf.date') BETWEEN ? AND ?
               OR json_extract(a.decision_json, '$.iva.date') BETWEEN ? AND ?
               OR EXISTS (
                   SELECT 1 FROM tax_attribution_fx_events fx
                   WHERE fx.attribution_id=a.attribution_id
                     AND fx.recognized_on BETWEEN ? AND ?)
            LIMIT 1
        """, (year_start, end, year_start, end, period['starts_on'], end, year_start, end)).fetchone()
        if attribution_history:
            readiness_reasons.append('tax_attribution_snapshot_review_required')
    supplemental = json.loads(export.files["supplemental_book_status"].read_text(encoding="utf-8"))
    readiness_reasons.extend(supplemental["blockers"])
    content_check = json.loads(export.files["register_content_check"].read_text(encoding="utf-8"))
    if not content_check["checked_fields_ready"]:
        readiness_reasons.append("register_required_fields_missing")
    # Hashes and filed snapshots cannot certify the legal content of every book.
    readiness_reasons.append("mandatory_book_content_validation_pending")
    attribution_basis = {}
    if "tax_registers" in export.files:
        registers = json.loads(export.files["tax_registers"].read_text(encoding="utf-8"))
        if any(item["blocked"] for item in registers["registers"].values()):
            readiness_reasons.append("tax_attribution_pending")
        attribution_basis = {"book_date_basis": {
            "income": "source_transaction_date", "expense": "source_transaction_date",
            "irpf": "reviewed_irpf_ytd", "iva": "reviewed_iva_period",
            "tax_registers_xlsx": "per_sheet_reviewed_tax_date",
        }, "unified_aeat_import_supported": False,
        "tax_register_xlsx": {
            "format": "native_separate_tax_registers_review",
            "generation_supported": True,
            "aeat_import_supported": False,
            "registers_complete": not any(item['blocked'] for item in registers['registers'].values()),
            "scope": {basis: {key: register[key] for key in ('starts_on', 'through')}
                      for basis, register in registers['registers'].items()},
        }}

    return {
        "manifest_version": 3,
        "package_type": "ledgerdb_export_only",
        "package_stage": "submission_ready" if not readiness_reasons else "draft_review",
        "submission_ready": not readiness_reasons,
        "readiness_blockers": readiness_reasons,
        "mandatory_books_ready": False,
        "supplemental_book_status": supplemental,
        "period": {
            "period_key": period["period_key"],
            "starts_on": period["starts_on"],
            "ends_on": period["ends_on"],
            "status": period["status"],
            "row_version": period["row_version"],
            "source_hash": period["source_hash"],
        },
        "artifacts": artifacts,
        "rule_versions": rule_versions,
        "snapshot_refs": snapshot_refs,
        **attribution_basis,
    }


def _serialize_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
