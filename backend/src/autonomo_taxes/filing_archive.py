"""Retain an export with its database, originals, exporter and dependency wheels."""
from __future__ import annotations

from email.parser import BytesParser
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable
import zipfile

from .ledger_db import LedgerDB
from .legacy_paths import LegacyPathResolver
from .storage_service import resolve_verified_replica


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _runtime_requirements() -> dict[str, str]:
    return {_name(dist.metadata["Name"]): dist.version for dist in importlib.metadata.distributions()
            if dist.metadata.get("Name") and _name(dist.metadata["Name"]) not in {
                "spain-autonomo-taxes", "pip", "setuptools", "wheel"}}


def _retain_dependencies(source: Path, target: Path) -> dict[str, str]:
    try:
        from packaging.tags import sys_tags
        from packaging.utils import parse_wheel_filename
    except ImportError:
        # pip is also the offline replay installer in supported deployments.
        from pip._vendor.packaging.tags import sys_tags
        from pip._vendor.packaging.utils import parse_wheel_filename
    required = _runtime_requirements()
    compatible = set(sys_tags())
    available = {}
    for path in sorted(source.glob("*.whl")):
        if path.is_symlink():
            continue
        wheel_name, wheel_version, _, tags = parse_wheel_filename(path.name)
        if not tags & compatible:
            continue
        with zipfile.ZipFile(path) as wheel:
            metadata = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata) != 1:
                raise ValueError("Dependency wheel has no unique package metadata")
            info = BytesParser().parsebytes(wheel.read(metadata[0]))
            if _name(info["Name"]) != _name(str(wheel_name)) or info["Version"] != str(wheel_version):
                raise ValueError("Wheel filename and package metadata disagree")
            available[(_name(info["Name"]), info["Version"])] = path
    missing = [f"{name}=={version}" for name, version in required.items() if (name, version) not in available]
    if missing:
        raise ValueError("Retained dependency wheels missing: " + ", ".join(sorted(missing)))
    target.mkdir()
    for name, version in sorted(required.items()):
        path = available[(name, version)]
        shutil.copyfile(path, target / path.name)
    return required


def _stage_program(root: Path) -> None:
    package_root = Path(__file__).parent
    program = root / "program/autonomo_taxes"
    for path in sorted(package_root.rglob("*")):
        if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts and not any(
            part.startswith(".") for part in path.relative_to(package_root).parts
        ):
            checksum = _sha256(path)
            target = program / path.relative_to(package_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            if _sha256(target) != checksum:
                raise ValueError("Program source changed while being retained")


def _generate_from_staged_program(root: Path, period_key: str, evidence: dict | None) -> dict:
    script = """import sys,json
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root/'program'))
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.filing_package import write_filing_package,verify_filing_package
with LedgerDB.open(root/'ledger.sqlite',read_only=True) as db:
    manifest=write_filing_package(db,root/'books',period_key=sys.argv[2],supplemental_evidence=json.loads(sys.stdin.read()))
    print(json.dumps(verify_filing_package(db,manifest)))
"""
    result = subprocess.run([sys.executable,"-I","-B","-c",script,str(root.resolve()),period_key],
                            input=json.dumps(evidence),capture_output=True,text=True,timeout=120)
    if result.returncode:
        raise ValueError("The retained program could not generate and verify the frozen export: " + result.stderr[-2000:])
    verification = json.loads(result.stdout)
    if not verification["ok"]:
        raise ValueError("Frozen export did not reproduce using the retained program")
    return verification


def write_filing_archive(database: LedgerDB, output_dir: str | Path, *, period_key: str,
                         dependency_dir: Path, source_files: Iterable[Path] = (),
                         legacy_path_map: Path | None = None,
                         supplemental_evidence: dict[str, Any] | None = None) -> Path:
    root = Path(output_dir)
    if database.connection.in_transaction:
        raise ValueError("Archive requires a committed database connection")
    if root.resolve().is_relative_to(Path(__file__).parent.resolve()):
        raise ValueError("Archive output must be outside the application package")
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        snapshot = database.backup_to(root / "ledger.sqlite")
        _stage_program(root)
        required = _retain_dependencies(Path(dependency_dir), root / "wheels")
        verification = _generate_from_staged_program(root, period_key, supplemental_evidence)
        package = root / "books/filing-package-manifest.json"
        resolver = LegacyPathResolver.from_json_file(legacy_path_map)
        extra_files = [Path(path) for path in source_files]
        extra_by_hash = {_sha256(path):path for path in extra_files}
        sources = []
        sources_dir = root / "sources"
        sources_dir.mkdir()

        def retain(path: Path, document_id: str | None = None, expected_hash: str | None = None,
                   *, identity_status: str | None = None, provenance_hash: str | None = None) -> Path:
            if not path.is_file():
                raise ValueError("An original document required by the archive is unavailable")
            checksum = _sha256(path)
            if expected_hash and checksum != expected_hash.lower():
                raise ValueError("An original document hash differs from the reviewed evidence")
            target = sources_dir / (checksum + path.suffix.lower())
            if not target.exists():
                shutil.copyfile(path, target)
            if _sha256(target) != checksum:
                raise ValueError("Source changed while it was being archived")
            sources.append({"document_id": document_id, "original_path": str(path),
                            "retained_path": target.relative_to(root).as_posix(), "sha256": checksum,
                            "content_identity":identity_status or ("verified_reviewed_hash" if expected_hash else "captured_at_archive"),
                            "provenance_hash":provenance_hash})
            return target

        with LedgerDB.open(snapshot, read_only=True) as frozen:
            if frozen.connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or frozen.connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Archive database snapshot failed integrity checks")
            projection = json.loads((root / "books/tax-registers.json").read_text())
            period_end = json.loads(package.read_text())["period"]["ends_on"]
            document_ids = {row["document_id"] for row in projection["source_rows"] if row["document_id"]}
            # Asset acquisition documents can predate the selected register year.
            document_ids.update(row[0] for row in frozen.connection.execute(
                "SELECT document_id FROM assets WHERE (placed_in_service_on<=? OR placed_in_service_on IS NULL) AND document_id IS NOT NULL",
                (period_end,)))
            document_ids.update(row[0] for row in frozen.connection.execute("""SELECT d.document_id FROM documents d
                LEFT JOIN periods p USING(period_id) WHERE (d.period_id IS NULL OR (p.starts_on>=? AND p.ends_on<=?)) AND
                (d.document_type IN ('aeat_official','tax_report','tax_filing','tax_receipt')
                 OR EXISTS(SELECT 1 FROM aeat_documents ad WHERE ad.document_id=d.document_id))""",
                (period_key[:4]+"-01-01",period_end)))
            filing_references = []
            snapshots = frozen.connection.execute("""SELECT fs.* FROM filing_snapshots fs JOIN periods p USING(period_id)
                WHERE p.starts_on>=? AND p.ends_on<=?""", (period_key[:4]+"-01-01",period_end)).fetchall()
            for filing in snapshots:
                filing_payload = json.loads(filing["payload_json"])
                has_content_hash = (filing["source_hash"] != filing["snapshot_hash"] or
                                    filing_payload.get("evidence_kind") == "filed_return_pdf")
                for field in ("source_reference", "manifest_path"):
                    reference = filing[field]
                    if not reference:
                        continue
                    identifier = reference.removeprefix("document:")
                    document = frozen.connection.execute("SELECT document_id FROM documents WHERE document_id=? OR source_path=?",
                                                          (identifier,reference)).fetchone()
                    if document:
                        document_ids.add(document[0])
                        filing_references.append({"snapshot_id":filing["filing_snapshot_id"],"reference":reference,"document_id":document[0]})
                        continue
                    path = resolver.resolve(reference) or Path(reference)
                    if field == "source_reference" and has_content_hash and not path.is_file():
                        path = extra_by_hash.get((filing["source_hash"] or "").lower(),path)
                    if field == "source_reference" and (re.match(r"https?://",reference) or not path.is_absolute() and not path.suffix):
                        filing_references.append({"snapshot_id":filing["filing_snapshot_id"],"external_reference":reference})
                        continue
                    expected = filing["source_hash"] if (field == "source_reference" and has_content_hash
                                and re.fullmatch(r"[a-fA-F0-9]{64}",filing["source_hash"])) else None
                    captured = retain(path,expected_hash=expected)
                    filing_references.append({"snapshot_id":filing["filing_snapshot_id"],"reference":reference,"sha256":_sha256(captured)})
                    if field == "manifest_path":
                        payload = json.loads(captured.read_text())
                        for item in payload.get("artifacts", []):
                            name = item["filename"]
                            if Path(name).name != name or name in {".",".."} or "\\" in name:
                                raise ValueError("Snapshot manifest artifact must be a local filename")
                            if not re.fullmatch(r"[a-fA-F0-9]{64}",item.get("sha256", "")):
                                raise ValueError("Snapshot manifest artifacts require content hashes")
                            retain(path.parent/name,expected_hash=item["sha256"])
            (root/"filing-references.json").write_text(json.dumps(filing_references,sort_keys=True,indent=2)+"\n")
            for document_id in sorted(document_ids):
                document = frozen.connection.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
                provenance = frozen.connection.execute("SELECT * FROM document_sources WHERE document_id=?",(document_id,)).fetchall()
                replica = resolve_verified_replica(frozen.connection, document_id=document_id, cache_root=root / ".source-cache")
                if replica is not None:
                    trusted_hash = frozen.connection.execute("""SELECT f.content_sha256 FROM file_replicas fr
                        JOIN files f USING(file_id) WHERE fr.file_replica_id=?""",(replica.replica_id,)).fetchone()[0]
                    retain(replica.path, document_id,trusted_hash,identity_status="verified_storage_replica",provenance_hash=document["source_hash"])
                else:
                    path = resolver.resolve(document["source_path"]) or Path(document["source_path"] or "")
                    catalog_hashes = {row[0] for row in frozen.connection.execute("""SELECT f.content_sha256 FROM files f
                        JOIN document_attachments da USING(file_id) WHERE da.document_id=? AND da.attachment_role='source'""",(document_id,))}
                    key = (document["external_key"] or "").removeprefix("document:")
                    canonical_hash = hashlib.sha256(json.dumps({"kind":"canonical_document","key":key},sort_keys=True,ensure_ascii=True).encode()).hexdigest()
                    source_book_path = any(source["source_file"] and
                        (resolver.resolve(source["source_file"]) or Path(source["source_file"])).resolve()==path.resolve() for source in provenance)
                    if catalog_hashes:
                        checksum = _sha256(path)
                        if checksum not in catalog_hashes:
                            raise ValueError("Original differs from catalogued content identity")
                        retain(path,document_id,checksum,provenance_hash=document["source_hash"])
                    elif provenance and source_book_path and (document["external_key"] or "").startswith("document:") and canonical_hash == document["source_hash"]:
                        retain(path,document_id,identity_status="historical_provenance_only",provenance_hash=canonical_hash)
                    else:
                        retain(path, document_id, document["source_hash"])
                for source in frozen.connection.execute(
                    "SELECT DISTINCT source_file FROM document_sources WHERE document_id=? AND source_file IS NOT NULL AND source_file<>''",
                    (document_id,),
                ):
                    path = resolver.resolve(source["source_file"]) or Path(source["source_file"])
                    if not any(item["original_path"] == str(path) for item in sources):
                        retain(path)
        for path in extra_files:
            if not any(item["original_path"] == str(path) for item in sources):
                retain(path)
        shutil.rmtree(root / ".source-cache", ignore_errors=True)
        (root / "requirements.txt").write_text("".join(f"{name}=={version}\n" for name, version in sorted(required.items())))
        (root / "sources.json").write_text(json.dumps(sources, sort_keys=True, indent=2)+"\n")
        (root / "verification.json").write_text(json.dumps(verification, sort_keys=True, indent=2)+"\n")
        (root / "REPRODUCE.txt").write_text(
            "Use an isolated environment; never restore over a running ledger.\n"
            "Runtime: " + sys.version + "\nPlatform: " + platform.platform() + "\n"
            "Verify the exact archive inventory and trusted manifest hash before replay.\n"
            "python -I -m venv ../replay-environment\n"
            "../replay-environment/bin/python -I -m pip install --no-index --find-links wheels -r requirements.txt\n"
            "../replay-environment/bin/python -I -B -c \"import sys; sys.path.insert(0,'program'); "
            "from autonomo_taxes.cli import main; raise SystemExit(main())\" filing-package verify "
            "--db ledger.sqlite --manifest books/filing-package-manifest.json\n"
            "Book-content acceptance is independent of archive integrity.\n")
        files = [{"path":path.relative_to(root).as_posix(),"sha256":_sha256(path),"size_bytes":path.stat().st_size}
                 for path in sorted(root.rglob("*")) if path.is_file()]
        manifest = {"archive_version":1,"period_key":period_key,"python":sys.version,
                    "platform":platform.platform(),"files":files,"mandatory_books_ready":False,
                    "runtime_replay_verified":False,"frozen_export_verified":True}
        manifest["authority_evidence_scope"] = "selected year plus all unperioded authority records"
        manifest["program_sha256"] = hashlib.sha256(json.dumps(
            [row for row in files if row["path"].startswith("program/")],sort_keys=True).encode()).hexdigest()
        manifest["historical_content_identity_unverified"] = [item["document_id"] for item in sources
            if item["content_identity"] == "historical_provenance_only"]
        target = root / "archive-manifest.json"
        target.write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n")
        return target
    except BaseException:
        shutil.rmtree(root)
        raise


def verify_filing_archive(manifest_path: Path, *, expected_manifest_sha256: str) -> dict[str, Any]:
    if manifest_path.is_symlink() or _sha256(manifest_path) != expected_manifest_sha256:
        return {"integrity_ok":False,"reason":"manifest_hash_mismatch"}
    manifest = json.loads(manifest_path.read_text())
    invalid = []
    seen = set()
    for row in manifest["files"]:
        name = row["path"]
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts or name in seen or "\\" in name or relative.as_posix() != name:
            invalid.append(name)
            continue
        seen.add(name)
        path = manifest_path.parent / relative
        inside_paths = [manifest_path.parent.joinpath(*relative.parts[:count]) for count in range(len(relative.parts)+1)]
        if any(parent.is_symlink() for parent in inside_paths):
            invalid.append(name)
        elif not path.is_file() or path.stat().st_size != row["size_bytes"] or _sha256(path) != row["sha256"]:
            invalid.append(name)
    actual = {path.relative_to(manifest_path.parent).as_posix() for path in manifest_path.parent.rglob("*")
              if path.is_file() or path.is_symlink()}
    unexpected = sorted(actual - seen - {manifest_path.name})
    return {"integrity_ok":bool(seen) and not invalid and not unexpected,"invalid_files":invalid,
            "unexpected_files":unexpected,
            "mandatory_books_ready":False,"runtime_replay_verified":False}
