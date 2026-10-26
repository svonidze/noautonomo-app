"""Copy and recover the files paired with an opt-in database-only archive."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import xml.etree.ElementTree as ET

from backup_state import atomic_json, read_json, sha256_file, _secure_regular
from backup_storage_policy import PolicyError, check_remote


class BackupFilesError(ValueError):
    pass


class UnsupportedChecksumError(BackupFilesError):
    pass


ARCHIVE_FILES = {"autonomo.sqlite", "config.yaml", "account-backup-settings.json"}
EXCLUDED = {"backups", "cache", "browser"}
SIDECARS = {"autonomo.sqlite-wal", "autonomo.sqlite-shm", "autonomo.sqlite-journal"}
DIGEST = re.compile(r"[0-9a-f]{64}")


def rclone(config: Path, *arguments: str) -> str:
    # Remote overrides must not change the destination whose policy was checked.
    env = {key: value for key, value in os.environ.items() if not key.startswith("RCLONE_")}
    command = ["rclone", "--config", str(config), *map(str, arguments)]
    if arguments[0] == "cryptcheck":
        command.extend(("--use-json-log", "--log-level", "INFO"))
    result = subprocess.run(command, text=True, capture_output=True, env=env, timeout=3600)
    if result.returncode:
        raise BackupFilesError(f"rclone {arguments[0]} failed; no file backup was acknowledged")
    if arguments[0] == "cryptcheck":
        for line in result.stderr.splitlines():
            message = json.loads(line).get("msg", "")
            if re.search(r"\b[1-9][0-9]* hashes could not be checked\b", message):
                raise UnsupportedChecksumError("provider checksum unavailable; download verification is required")
    return result.stdout


def preflight(config: Path, files_remote: str, evidence_remote: str, archive_remote: str | None = None) -> None:
    _secure_regular(config)
    configs = json.loads(rclone(config, "config", "dump"))
    if archive_remote is not None and (not re.fullmatch(r"[\w-]+:", archive_remote) or configs.get(archive_remote[:-1], {}).get("type") != "crypt"):
        raise BackupFilesError("archive backup requires a named crypt root")
    if files_remote == evidence_remote:
        raise BackupFilesError("files and evidence require separate crypt roots")
    destinations = [check_remote(configs, remote) for remote in (files_remote, evidence_remote)]
    first, second = destinations
    if first[:2] == second[:2] and (first[2].startswith(second[2]) or second[2].startswith(first[2])):
        raise BackupFilesError("files and evidence S3 prefixes must not overlap")


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value or any(c in value for c in "\r\n\\\x00"):
        raise BackupFilesError("unsafe file backup path")
    path = PurePosixPath(value)
    if path.is_absolute() or PureWindowsPath(value).drive or (os.name == "nt" and ":" in value) or any(part in {"", ".", ".."} for part in value.split("/")):
        raise BackupFilesError("unsafe file backup path")
    return value


def validate_outputs(root: Path, excluded_dirs: list[Path]) -> None:
    if not root.is_absolute() or any(not path.is_absolute() for path in excluded_dirs):
        raise BackupFilesError("private root and backup directories must be absolute")
    root = root.resolve(strict=True)
    if any(root == path.resolve() or path.resolve() in root.parents for path in excluded_dirs):
        raise BackupFilesError("backup directories must not contain the private root")


def inventory(root: Path, excluded_dirs: list[Path]) -> list[dict]:
    validate_outputs(root, excluded_dirs)
    root = root.resolve(strict=True)
    excluded = {path.resolve() for path in excluded_dirs}
    rows = []
    for directory, directories, filenames in os.walk(root, followlinks=False):
        parent = Path(directory)
        directories[:] = sorted(name for name in directories if not (parent / name).is_symlink() and (parent / name).resolve() not in excluded and not (parent == root and name in EXCLUDED))
        for name in sorted(filenames):
            path = parent / name
            relative = _relative(path.relative_to(root).as_posix())
            if parent == root and name in ARCHIVE_FILES | SIDECARS:
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            rows.append({"path": relative, "size": info.st_size, "sha256": sha256_file(path), "kind": "evidence" if relative.startswith("evidence/") else "tree"})
    return rows


def _verify_local(root: Path, rows: list[dict]) -> None:
    for row in rows:
        path = root / row["path"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != row["size"] or sha256_file(path) != row["sha256"]:
            raise BackupFilesError("source files changed during backup")


def _verify_copy(config: Path, source: Path, destination: str, paths: list[str], scratch: Path, *, version_at: str | None = None) -> None:
    if not paths:
        return
    listing = scratch / "selected-files.txt"
    listing.write_text("".join(path + "\n" for path in paths), encoding="utf-8")
    arguments = ["--one-way", "--files-from-raw", str(listing), "--match", "-"]
    if version_at:
        arguments += ["--s3-version-at", version_at]
    try:
        matched = rclone(config, "cryptcheck", str(source), destination, *arguments)
    except UnsupportedChecksumError:
        # Multipart crypt objects may lack a usable provider checksum.
        matched = rclone(config, "check", str(source), destination, "--download", *arguments)
    if set(matched.splitlines()) != set(paths):
        raise BackupFilesError("remote file matches do not cover the selected files")


def verify_pair(config: Path, archive: Path, manifest: Path, remote: str) -> None:
    if manifest.parent != archive.parent:
        raise BackupFilesError("archive pair must share its directory")
    with tempfile.TemporaryDirectory(dir=archive.parent, prefix=".pair-check-") as raw:
        _verify_copy(config, archive.parent, remote, [archive.name, manifest.name], Path(raw))


def _covered_catalogue(database: Path, root: Path, rows: list[dict], config: Path, evidence_remote: str, scratch: Path) -> list[dict]:
    """Verify attachment bytes, not merely the persisted replica availability flag."""
    by_path = {row["path"]: row for row in rows}
    remote_rows = []
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if {"files", "document_attachments", "file_replicas", "storage_backends"} <= tables:
            files = db.execute("SELECT DISTINCT f.file_id,f.content_sha256,f.byte_size FROM files f JOIN document_attachments da USING(file_id)").fetchall()
            for file in files:
                digest, size = file["content_sha256"], file["byte_size"]
                replicas = db.execute("SELECT fr.provider_locator,fr.replica_status,sb.driver_key,sb.config_json FROM file_replicas fr JOIN storage_backends sb USING(storage_backend_id) WHERE fr.file_id=? AND sb.enabled=1 AND fr.replica_status='available'", (file["file_id"],)).fetchall()
                local = False
                for replica in replicas:
                    if replica["driver_key"] != "filesystem":
                        continue
                    settings = json.loads(replica["config_json"])
                    locator_path = Path(replica["provider_locator"])
                    if locator_path.is_absolute() or ".." in locator_path.parts:
                        continue
                    # Preserve the locator spelling: excluded symlinks are not restored.
                    candidate = Path(settings["root"]).expanduser() / locator_path
                    try:
                        relative = candidate.relative_to(root).as_posix()
                    except ValueError:
                        continue
                    row = by_path.get(relative)
                    local |= row is not None and (row["sha256"], row["size"]) == (digest, size)
                if local:
                    continue
                locator = f"sha256/{digest}"
                if not DIGEST.fullmatch(digest) or not any(row["driver_key"] == "s3" and row["provider_locator"] == locator and json.loads(row["config_json"]).get("rclone_remote", "").rstrip(":") == evidence_remote.rstrip(":") for row in replicas):
                    raise BackupFilesError("snapshot attachment lacks a recoverable local or configured S3 copy")
                downloaded = scratch / "catalogue-file"
                rclone(config, "copyto", evidence_remote + locator, str(downloaded))
                if downloaded.stat().st_size != size or sha256_file(downloaded) != digest:
                    raise BackupFilesError("snapshot attachment hash mismatch")
                downloaded.unlink()
                remote_rows.append({"kind": "replica", "path": "evidence/" + locator, "sha256": digest, "size": size})
        # A filing's content hash is distinct from its provenance/snapshot hash.
        for table, columns in (("documents", ("source_path",)), ("filing_snapshots", ("manifest_path", "source_reference"))):
            if table not in tables:
                continue
            present = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            for column in set(columns) & present:
                where = f"{column} IS NOT NULL"
                if table == "documents" and "document_attachments" in tables:
                    where += " AND NOT EXISTS (SELECT 1 FROM document_attachments da WHERE da.document_id=documents.document_id AND da.attachment_role='source')"
                for record in db.execute(f"SELECT * FROM {table} WHERE {where}"):
                    value = record[column]
                    if not value:
                        continue
                    if value.startswith("document:"):
                        if "documents" not in tables or not db.execute("SELECT 1 FROM documents WHERE document_id=?", (value.removeprefix("document:"),)).fetchone():
                            raise BackupFilesError("snapshot refers to a missing document")
                        continue
                    if value.startswith(("https://", "http://")) or (column == "source_reference" and not Path(value).is_absolute() and not Path(value).suffix):
                        continue
                    if PureWindowsPath(value).drive and not Path(value).is_absolute():
                        raise BackupFilesError("snapshot has an unmapped legacy local path")
                    path = Path(value) if Path(value).is_absolute() else root / value
                    try:
                        relative = path.relative_to(root).as_posix()
                    except ValueError:
                        raise BackupFilesError("snapshot references a local file outside the backed-up root") from None
                    _relative(relative)
                    if relative not in by_path:
                        raise BackupFilesError("snapshot references a missing or excluded local file")
                    if table == "filing_snapshots" and column == "source_reference":
                        payload = json.loads(record["payload_json"])
                        content_hash = record["source_hash"]
                        has_content_hash = content_hash != record["snapshot_hash"] or payload.get("evidence_kind") == "filed_return_pdf"
                        if has_content_hash and re.fullmatch(r"[a-fA-F0-9]{64}", content_hash or "") and by_path[relative]["sha256"] != content_hash.lower():
                            raise BackupFilesError("filed source does not match the snapshot content hash")
                    if column == "manifest_path":
                        payload = json.loads(path.read_text(encoding="utf-8"))
                        for artifact in payload.get("artifacts", []):
                            name = _relative(artifact["filename"])
                            expected = str(artifact.get("sha256", "")).lower()
                            if "/" in name or not DIGEST.fullmatch(expected):
                                raise BackupFilesError("filing artifacts require a local filename and content hash")
                            row = by_path.get((path.parent / name).relative_to(root).as_posix())
                            if row is None or row["sha256"] != expected:
                                raise BackupFilesError("filing artifact is missing or does not match its content hash")
    return remote_rows


def _snapshot(archive: Path, manifest: Path, scratch: Path) -> Path:
    document = read_json(manifest)
    if document.get("format") != 1 or document.get("archive") != archive.name or document.get("archive_sha256") != sha256_file(archive):
        raise BackupFilesError("invalid archive/manifest binding")
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.getmember("autonomo.sqlite")
        stream = tar.extractfile(member) if member.isfile() else None
        if stream is None:
            raise BackupFilesError("archive lacks a SQLite snapshot")
        database = scratch / "snapshot.sqlite"
        with database.open("wb") as target:
            shutil.copyfileobj(stream, target)
    expected = next(row for row in document["files"] if row["path"] == "autonomo.sqlite")
    if sha256_file(database) != expected["sha256"] or database.stat().st_size != expected["size"]:
        raise BackupFilesError("snapshot does not match manifest")
    return database


def backup(*, root: Path, archive: Path, manifest: Path, config: Path, files_remote: str, evidence_remote: str, archive_remote: str, excluded_dirs: list[Path]) -> Path:
    preflight(config, files_remote, evidence_remote, archive_remote)
    root = root.resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=archive.parent, prefix=".files-") as raw:
        scratch = Path(raw)
        for path in (archive, manifest):
            rclone(config, "copyto", str(path), archive_remote + path.name, "--immutable")
        _verify_copy(config, archive.parent, archive_remote, [archive.name, manifest.name], scratch)
        rows = inventory(root, [*excluded_dirs, archive.parent])
        database = _snapshot(archive, manifest, scratch)
        replicas = _covered_catalogue(database, root, rows, config, evidence_remote, scratch)
        for kind, source, destination in (("tree", root, files_remote + "tree"), ("evidence", root / "evidence", evidence_remote)):
            paths = [row["path"].removeprefix("evidence/") if kind == "evidence" else row["path"] for row in rows if row["kind"] == kind]
            if not paths:
                continue
            listing = scratch / "selected-files.txt"
            listing.write_text("".join(path + "\n" for path in paths), encoding="utf-8")
            arguments = ["copy", str(source), destination, "--files-from-raw", str(listing)]
            if kind == "evidence":
                arguments.append("--immutable")
            rclone(config, *arguments)
        cutoff = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        for kind, source, destination in (("tree", root, files_remote + "tree"), ("evidence", root / "evidence", evidence_remote)):
            paths = [row["path"].removeprefix("evidence/") if kind == "evidence" else row["path"] for row in rows if row["kind"] == kind]
            _verify_copy(config, source, destination, paths, scratch, version_at=cutoff)
        _verify_local(root, rows)
        for row in replicas:
            downloaded = scratch / "catalogue-file"
            rclone(config, "copyto", evidence_remote + row["path"].removeprefix("evidence/"), str(downloaded), "--s3-version-at", cutoff)
            if downloaded.stat().st_size != row["size"] or sha256_file(downloaded) != row["sha256"]:
                raise BackupFilesError("historical replica hash mismatch")
            downloaded.unlink()
        payload = {"format": 1, "archive": archive.name, "archive_sha256": sha256_file(archive), "manifest_sha256": sha256_file(manifest), "version_at": cutoff, "files": rows + replicas}
        name = archive.name.removesuffix(".tar.gz") + ".files.json"
        index = scratch / name
        atomic_json(index, payload)
        rclone(config, "copyto", str(index), files_remote + "snapshots/" + name, "--immutable")
        _verify_copy(config, scratch, files_remote + "snapshots", [name], scratch)
        local = archive.parent / name
        atomic_json(local, payload)
        return local


def restore(*, archive: Path, manifest: Path, config: Path, files_remote: str, evidence_remote: str, target: Path, private_root: Path) -> None:
    target, private_root = target.resolve(), private_root.resolve()
    if target == private_root or private_root in target.parents or target in private_root.parents:
        raise BackupFilesError("restore target must be separate from the private root")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise BackupFilesError("restore target must be empty")
    name = archive.name.removesuffix(".tar.gz") + ".files.json"
    index = json.loads(rclone(config, "cat", files_remote + "snapshots/" + name))
    if index.get("format") != 1 or index.get("archive") != archive.name or index.get("archive_sha256") != sha256_file(archive) or index.get("manifest_sha256") != sha256_file(manifest):
        raise BackupFilesError("file index does not match the archive pair")
    datetime.fromisoformat(index["version_at"])
    seen = set()
    for row in index["files"]:
        path = _relative(row["path"])
        if path in seen or path in ARCHIVE_FILES | SIDECARS or PurePosixPath(path).parts[0] in EXCLUDED or row["kind"] not in {"tree", "evidence", "replica"} or not DIGEST.fullmatch(row["sha256"]) or type(row["size"]) is not int or row["size"] < 0:
            raise BackupFilesError("invalid file index member")
        if (row["kind"] != "tree") != path.startswith("evidence/"):
            raise BackupFilesError("invalid evidence path in file index")
        seen.add(path)
    script = Path(__file__).parent / "restore_private_root.py"
    if not script.is_file():
        script = Path(__file__).parent / "backup" / "restore_private_root.py"
    spec = importlib.util.spec_from_file_location("file_backup_restore", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.restore_backup(archive=archive, manifest=manifest, target_root=target, private_root=private_root)
    for row in index["files"]:
        destination = target / row["path"]
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Refuse filesystem aliases too (for example A.txt/a.txt on macOS).
        with destination.open("xb"):
            pass
        destination.chmod(0o600)
        source = files_remote + "tree/" + row["path"] if row["kind"] == "tree" else evidence_remote + row["path"].removeprefix("evidence/")
        rclone(config, "copyto", source, str(destination), "--s3-version-at", index["version_at"])
        destination.chmod(0o600)
        if destination.stat().st_size != row["size"] or sha256_file(destination) != row["sha256"]:
            raise BackupFilesError("restored file does not match the index")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "backup", "restore", "verify-pair"))
    parser.add_argument("--rclone-config", type=Path, required=True)
    parser.add_argument("--files-remote", required=True)
    parser.add_argument("--evidence-remote", required=True)
    parser.add_argument("--archive-remote")
    parser.add_argument("--private-root", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--exclude-dir", type=Path, action="append", default=[])
    parser.add_argument("--target-root", type=Path)
    args = parser.parse_args()
    _secure_regular(args.rclone_config)
    if args.command in {"preflight", "backup"} and args.private_root:
        validate_outputs(args.private_root, args.exclude_dir)
    if args.command == "verify-pair":
        if not args.archive or not args.manifest or not args.archive_remote:
            parser.error("archive, manifest and archive-remote are required")
        verify_pair(args.rclone_config, args.archive.resolve(strict=True), args.manifest.resolve(strict=True), args.archive_remote)
    elif args.command == "preflight":
        preflight(args.rclone_config, args.files_remote, args.evidence_remote, args.archive_remote)
    else:
        if not args.private_root or not args.archive or not args.manifest or (args.command == "restore" and not args.target_root):
            parser.error("archive, manifest, private-root and (for restore) target-root are required")
        common = dict(archive=args.archive.resolve(strict=True), manifest=args.manifest.resolve(strict=True), config=args.rclone_config, files_remote=args.files_remote, evidence_remote=args.evidence_remote)
        if args.command == "backup":
            if not args.archive_remote:
                parser.error("archive-remote is required for backup")
            backup(root=args.private_root, archive_remote=args.archive_remote, excluded_dirs=args.exclude_dir, **common)
        else:
            restore(target=args.target_root, private_root=args.private_root, **common)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BackupFilesError, PolicyError) as exc:
        raise SystemExit(str(exc)) from None
    except (ValueError, OSError, KeyError, TypeError, StopIteration, sqlite3.Error, tarfile.TarError, ET.ParseError, subprocess.SubprocessError):
        raise SystemExit("file backup failed; inspect configuration and recovery coverage before retrying") from None
