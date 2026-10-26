"""Exercise file history without any real cloud, credentials or runtime files."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import xml.etree.ElementTree as ET

import pytest
from autonomo_test_support.paths import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "ops"))
import backup_files as files
import backup_storage_policy as policy


def _archiver():
    spec = importlib.util.spec_from_file_location("files_test_archiver", REPO_ROOT / "ops/backup/backup_private_root.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VersionedRemote:
    def __init__(self):
        self.versions = {}
        self.fail = None
        self.silent = False
        self.commands = []
        self.uploads = 0

    def put(self, path, data):
        path = path.replace(":/", ":", 1)
        versions = self.versions.setdefault(path, [])
        if not versions or versions[-1][1] != data:
            versions.append((datetime.now(timezone.utc).isoformat(timespec="microseconds"), data))
            self.uploads += 1

    def get(self, path, cutoff=None):
        path = path.replace(":/", ":", 1)
        versions = [data for stamp, data in self.versions[path] if cutoff is None or stamp <= cutoff]
        if not versions:
            raise files.BackupFilesError("missing historical object")
        return versions[-1]

    def __call__(self, config, *args):
        args = list(map(str, args))
        self.commands.append(args)
        operation = args[0]
        if self.fail == operation:
            if operation == "cryptcheck":
                raise files.UnsupportedChecksumError("provider checksum unavailable")
            raise files.BackupFilesError("synthetic transport failure")
        cutoff = args[args.index("--s3-version-at") + 1] if "--s3-version-at" in args else None
        if operation == "cat":
            return self.get(args[1], cutoff).decode()
        if operation == "copyto":
            source, target = args[1:3]
            if ":" in source:
                Path(target).write_bytes(self.get(source, cutoff))
            else:
                if "--immutable" in args and target in self.versions and self.get(target) != Path(source).read_bytes():
                    raise files.BackupFilesError("immutable index changed")
                self.put(target, Path(source).read_bytes())
            return ""
        if operation in {"copy", "cryptcheck", "check"}:
            source, target = Path(args[1]), args[2]
            paths = Path(args[args.index("--files-from-raw") + 1]).read_text().splitlines()
            matched = []
            for path in paths:
                remote_path = target.rstrip("/") + ("" if target.endswith(":") else "/") + path
                data = (source / path).read_bytes()
                if operation == "copy":
                    if "--immutable" in args and remote_path in self.versions and self.get(remote_path) != data:
                        raise files.BackupFilesError("immutable evidence changed")
                    self.put(remote_path, data)
                else:
                    if self.get(remote_path, cutoff) != data:
                        raise files.BackupFilesError("checksum mismatch")
                    matched.append(path)
            return "" if self.silent else "".join(path + "\n" for path in matched)
        raise AssertionError(args)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "private"
    root.mkdir()
    database = root / "autonomo.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE sample (value INTEGER)")
        db.execute("INSERT INTO sample VALUES (1)")
    for name in ("inbox/new.txt", "archives/submission.json", "evidence/source.txt", "audit/rehearsal.sqlite", "counterparty-identities/identity.json"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic first version")
    config = tmp_path / "rclone.conf"
    config.write_text("synthetic")
    config.chmod(0o600)
    remote = VersionedRemote()
    monkeypatch.setattr(files, "rclone", remote)
    monkeypatch.setattr(files, "preflight", lambda *args: None)
    options = dict(root=root, config=config, files_remote="files-crypt:", evidence_remote="evidence-crypt:", archive_remote="archive-crypt:", excluded_dirs=[])
    return root, database, remote, options


def make_backup(options):
    archive, manifest = _archiver().create_backup(private_root=options["root"], database=options["root"] / "autonomo.sqlite", output_dir=options["root"] / "backups/daily", keep=12, scope="database")
    index = files.backup(archive=archive, manifest=manifest, **options)
    return archive, manifest, index


def test_versioned_copy_recovers_old_and_new_files_without_reuploading_unchanged(setup, tmp_path):
    root, database, remote, options = setup
    first = make_backup(options)
    index = json.loads(first[2].read_text())
    assert index["archive_sha256"] == hashlib.sha256(first[0].read_bytes()).hexdigest()
    assert {row["path"] for row in index["files"]} == {"inbox/new.txt", "archives/submission.json", "evidence/source.txt", "audit/rehearsal.sqlite", "counterparty-identities/identity.json"}
    before = remote.uploads
    second = make_backup(options)
    assert remote.uploads == before + 3  # New SQLite pair and index; no unchanged file bytes.
    (root / "archives/submission.json").write_text("synthetic second version")
    (root / "inbox/new.txt").unlink()
    third = make_backup(options)
    assert len(remote.versions["files-crypt:tree/archives/submission.json"]) == 2
    for pair, version, inbox in ((first, "first", True), (second, "first", True), (third, "second", False)):
        target = tmp_path / version / pair[0].stem
        files.restore(archive=pair[0], manifest=pair[1], config=options["config"], files_remote=options["files_remote"], evidence_remote=options["evidence_remote"], target=target, private_root=root)
        assert (target / "archives/submission.json").read_text() == f"synthetic {version} version"
        assert (target / "inbox/new.txt").exists() is inbox
        assert (target / "evidence/source.txt").read_text() == "synthetic first version"
        with sqlite3.connect(target / "autonomo.sqlite") as db:
            assert db.execute("SELECT value FROM sample").fetchall() == [(1,)]


def test_inventory_excludes_services_output_and_symlinks(setup, tmp_path):
    root, _, _, options = setup
    for name in ("cache/state", "browser/token", "backups/old", "special-output/old", "config.yaml", "account-backup-settings.json", "autonomo.sqlite-wal"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("synthetic")
    (root / "linked-directory").symlink_to(outside, target_is_directory=True)
    (root / "linked-file").symlink_to(outside / "secret")
    rows = files.inventory(root, [root / "special-output"])
    assert len(rows) == 5
    assert not any(row["path"].startswith(("special-output/", "linked-", "cache/", "backups/", "browser/")) for row in rows)


@pytest.mark.parametrize("failure", ["copy", "copyto", "cryptcheck", "silent", "mutation"])
def test_failures_never_publish_a_local_success_index(setup, monkeypatch, failure):
    root, _, remote, options = setup
    archive, manifest = _archiver().create_backup(private_root=root, database=root / "autonomo.sqlite", output_dir=root / "backups/daily", keep=12, scope="database")
    if failure == "silent":
        remote.silent = True
    elif failure == "cryptcheck":
        original = remote.__call__
        def fail_checks(config, *args):
            if args[0] in {"cryptcheck", "check"}:
                raise files.BackupFilesError("synthetic checksum failure")
            return original(config, *args)
        monkeypatch.setattr(files, "rclone", fail_checks)
    elif failure == "mutation":
        original = remote.__call__
        def mutate(config, *args):
            result = original(config, *args)
            if args[0] == "cryptcheck" and args[2].startswith("files-crypt:tree"):
                (root / "inbox/new.txt").write_text("changed during backup")
            return result
        monkeypatch.setattr(files, "rclone", mutate)
    else:
        remote.fail = failure
    with pytest.raises(files.BackupFilesError):
        files.backup(archive=archive, manifest=manifest, **options)
    assert archive.is_file() and manifest.is_file()
    assert not list(archive.parent.glob("*.files.json"))


def test_multipart_verification_falls_back_to_download(setup):
    _, _, remote, options = setup
    remote.fail = "cryptcheck"
    make_backup(options)
    assert any(command[0] == "check" and "--download" in command for command in remote.commands)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "cache/unsafe", "autonomo.sqlite", "evidence/../escape", "C:/unsafe", "C:outside", "bad\nname"])
def test_restore_rejects_unsafe_index_before_writing(setup, tmp_path, path):
    root, _, remote, options = setup
    archive, manifest, index = make_backup(options)
    payload = json.loads(index.read_text())
    payload["files"][0]["path"] = path
    remote.put("files-crypt:snapshots/" + index.name, json.dumps(payload).encode())
    target = tmp_path / "restored"
    with pytest.raises(ValueError):
        files.restore(archive=archive, manifest=manifest, config=options["config"], files_remote=options["files_remote"], evidence_remote=options["evidence_remote"], target=target, private_root=root)
    assert not target.exists()


@pytest.mark.parametrize("target_name", ["inside", "ancestor", "nonempty"])
def test_restore_refuses_live_root_or_nonempty_target(setup, tmp_path, target_name):
    root, _, _, options = setup
    archive, manifest, _ = make_backup(options)
    target = root / "restore" if target_name == "inside" else tmp_path
    if target_name == "nonempty":
        target = tmp_path / "occupied"
        target.mkdir()
        (target / "existing").write_text("preserve")
    with pytest.raises(ValueError):
        files.restore(archive=archive, manifest=manifest, config=options["config"], files_remote=options["files_remote"], evidence_remote=options["evidence_remote"], target=target, private_root=root)


def test_policy_requires_versioning_and_rejects_overlapping_expiration():
    versioning = ET.fromstring("<VersioningConfiguration><Status>Enabled</Status></VersioningConfiguration>")
    prefix = "archive/private-root-files/"
    policy.validate_policy(versioning, ET.fromstring("<LifecycleConfiguration/>"), prefix)
    for parent in ("", "archive/", prefix, prefix + "nested/"):
        for action in ("Expiration", "NoncurrentVersionExpiration"):
            lifecycle = ET.fromstring(f"<LifecycleConfiguration><Rule><Status>Enabled</Status><Filter><And><Prefix>{parent}</Prefix><Tag><Key>synthetic</Key></Tag></And></Filter><{action}><Days>1</Days></{action}></Rule></LifecycleConfiguration>")
            with pytest.raises(policy.PolicyError, match="must not expire"):
                policy.validate_policy(versioning, lifecycle, prefix)
    safe = ET.fromstring("<LifecycleConfiguration><Rule><Status>Enabled</Status><Prefix>backups/</Prefix><Expiration/></Rule></LifecycleConfiguration>")
    policy.validate_policy(versioning, safe, prefix)
    with pytest.raises(policy.PolicyError, match="Enabled"):
        policy.validate_policy(ET.fromstring("<VersioningConfiguration><Status>Suspended</Status></VersioningConfiguration>"), safe, prefix)


def test_policy_requests_are_read_only_and_signed(monkeypatch):
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'<VersioningConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Status>Enabled</Status></VersioningConfiguration>'
    def open_request(request, timeout):
        requests.append(request)
        assert timeout == 30
        return Response()
    monkeypatch.setattr(policy, "urlopen", open_request)
    result = policy._get_bucket_setting({"endpoint": "https://s3.example.invalid", "access_key_id": "synthetic", "secret_access_key": "synthetic", "region": "synthetic"}, "synthetic-bucket", "versioning")
    assert result.findtext("Status") == "Enabled"
    assert requests[0].get_method() == "GET"
    assert requests[0].full_url == "https://s3.example.invalid/synthetic-bucket?versioning="
    assert requests[0].get_header("Authorization").startswith("AWS4-HMAC-SHA256 ")


def test_named_remote_policy_validation_rejects_ambiguous_destinations(monkeypatch):
    def setting(options, bucket, kind):
        return ET.fromstring("<VersioningConfiguration><Status>Enabled</Status></VersioningConfiguration>" if kind == "versioning" else "<LifecycleConfiguration/>")
    monkeypatch.setattr(policy, "_get_bucket_setting", setting)
    configs = {"files-crypt": {"type": "crypt", "remote": "raw:synthetic-bucket/archive/private-root-files"}, "raw": {"type": "s3"}}
    policy.check_remote(configs, "files-crypt:")
    for remote in ("raw:", "files-crypt:subpath", "files-crypt,remote=other:", "missing:"):
        with pytest.raises(ValueError):
            policy.check_remote(configs, remote)


def test_catalogue_recovery_reads_local_upload_and_official_document_through_application(setup, tmp_path):
    from autonomo_taxes.ledger_db import LedgerDB
    from autonomo_taxes.services.application import AccountingService, RuntimeConfig
    from autonomo_taxes.storage_service import register_local_source_replica
    root, database, _, options = setup
    database.unlink()
    documents = []
    with LedgerDB.initialize(database) as db:
        for number, kind in enumerate(("expense_invoice", "aeat_official")):
            source = root / "evidence" / f"synthetic-{number}.txt"
            source.write_bytes(f"synthetic source {number}".encode())
            document = db.upsert_document(document_type=kind, issued_on="2026-01-01", source_hash=hashlib.sha256(source.read_bytes()).hexdigest())
            db.connection.execute("UPDATE documents SET source_path=? WHERE document_id=?", (str(source), document["document_id"]))
            db.connection.commit()
            register_local_source_replica(db, document_id=document["document_id"], source_path=source, storage_root=root / "evidence", media_type="text/plain")
            documents.append((document["document_id"], source.read_bytes()))
    archive, manifest, _ = make_backup(options)
    target = tmp_path / "drill"
    files.restore(archive=archive, manifest=manifest, config=options["config"], files_remote=options["files_remote"], evidence_remote=options["evidence_remote"], target=target, private_root=root)
    shutil.rmtree(root)  # A passing drill cannot accidentally read the original tree.
    with sqlite3.connect(target / "autonomo.sqlite") as db:
        for backend_id, configuration in db.execute("SELECT storage_backend_id,config_json FROM storage_backends WHERE driver_key='filesystem'").fetchall():
            value = json.loads(configuration)
            value["root"] = str(target / Path(value["root"]).relative_to(root))
            db.execute("UPDATE storage_backends SET config_json=? WHERE storage_backend_id=?", (json.dumps(value), backend_id))
    service = AccountingService(RuntimeConfig(project_root=tmp_path, database=target / "autonomo.sqlite", inbox_root=target / "inbox", archive_root=target / "evidence", cache_root=target / "cache", private_root=target))
    for document_id, content in documents:
        path, media_type = service.document_file(document_id)
        assert path.read_bytes() == content
        assert media_type == "text/plain"
    assert (target / "archives/submission.json").is_file()


@pytest.mark.parametrize("corrupt", [False, True])
def test_remote_only_catalogue_replica_is_downloaded_and_hash_checked(setup, tmp_path, corrupt):
    from autonomo_taxes.ledger_db import LedgerDB
    root, database, remote, options = setup
    database.unlink()
    content = b"synthetic remote-only document"
    digest = hashlib.sha256(content).hexdigest()
    with LedgerDB.initialize(database) as db:
        document = db.upsert_document(document_type="expense_invoice", issued_on="2026-01-01", source_hash="a" * 64)
        file = db.upsert_file(content_sha256=digest, byte_size=len(content), media_type="text/plain")
        db.attach_file_to_document(document_id=document["document_id"], file_id=file["file_id"], attachment_role="source")
        backend = db.upsert_storage_backend(backend_key="synthetic_mirror", display_name="Synthetic mirror", driver_key="s3", provider_key="yandex_s3", access_mode="read_only", config={"rclone_remote": "evidence-crypt:", "crypt_config_fingerprint": "synthetic"}, credential_ref="file:/synthetic/rclone.conf")
        db.register_file_replica(file_id=file["file_id"], storage_backend_id=backend["storage_backend_id"], provider_locator="sha256/" + digest, is_primary=True, last_verified_at="2026-01-01T00:00:00Z")
    remote.put("evidence-crypt:sha256/" + digest, b"corrupt" if corrupt else content)
    if corrupt:
        with pytest.raises(files.BackupFilesError, match="hash mismatch"):
            make_backup(options)
    else:
        archive, manifest, index = make_backup(options)
        assert any(row["kind"] == "replica" for row in json.loads(index.read_text())["files"])
        files.restore(archive=archive, manifest=manifest, config=options["config"], files_remote=options["files_remote"], evidence_remote=options["evidence_remote"], target=tmp_path / "restored", private_root=root)
        assert (tmp_path / "restored/evidence/sha256" / digest).read_bytes() == content


def test_bytes_at_an_unregistered_path_do_not_falsely_cover_catalogue(setup):
    from autonomo_taxes.ledger_db import LedgerDB
    root, database, _, options = setup
    database.unlink()
    content = (root / "evidence/source.txt").read_bytes()
    with LedgerDB.initialize(database) as db:
        document = db.upsert_document(document_type="expense_invoice", issued_on="2026-01-01", source_hash="b" * 64)
        file = db.upsert_file(content_sha256=hashlib.sha256(content).hexdigest(), byte_size=len(content), media_type="text/plain")
        db.attach_file_to_document(document_id=document["document_id"], file_id=file["file_id"], attachment_role="source")
    with pytest.raises(files.BackupFilesError, match="lacks a recoverable"):
        make_backup(options)


def test_local_rclone_crypt_round_trip_uses_real_checks(tmp_path, monkeypatch):
    import subprocess
    if not shutil.which("rclone"):
        pytest.skip("rclone is not installed")
    root = tmp_path / "private"
    (root / "inbox").mkdir(parents=True)
    (root / "inbox/file.txt").write_text("synthetic")
    (root / "evidence").mkdir()
    (root / "evidence/source.txt").write_text("synthetic evidence")
    with sqlite3.connect(root / "autonomo.sqlite") as db:
        db.execute("CREATE TABLE sample (value INTEGER)")
    password = subprocess.run(["rclone", "obscure", "synthetic-test-password"], capture_output=True, text=True, check=True).stdout.strip()
    config = tmp_path / "rclone.conf"
    config.write_text(f"[archive-crypt]\ntype = crypt\nremote = {tmp_path / 'remote-archive'}\npassword = {password}\n[files-crypt]\ntype = crypt\nremote = {tmp_path / 'remote-files'}\npassword = {password}\n[evidence-crypt]\ntype = crypt\nremote = {tmp_path / 'remote-evidence'}\npassword = {password}\n")
    config.chmod(0o600)
    monkeypatch.setattr(files, "preflight", lambda *args: None)  # Local crypt has no S3 lifecycle.
    options = dict(root=root, config=config, files_remote="files-crypt:", evidence_remote="evidence-crypt:", archive_remote="archive-crypt:", excluded_dirs=[])
    archive, manifest, index = make_backup(options)
    assert files.rclone(config, "cat", "files-crypt:/snapshots/" + index.name) == files.rclone(config, "cat", "files-crypt:snapshots/" + index.name)
    target = tmp_path / "drill"
    files.restore(archive=archive, manifest=manifest, config=config, files_remote=options["files_remote"], evidence_remote=options["evidence_remote"], target=target, private_root=root)
    assert (target / "inbox/file.txt").read_text() == "synthetic"
    assert (target / "evidence/source.txt").read_text() == "synthetic evidence"
    assert index.is_file()


@pytest.mark.parametrize("failure", [None, "changed_source", "changed_artifact", "missing_artifact", "unsafe_artifact"])
def test_filing_snapshot_content_hashes_and_manifest_artifacts_are_required(setup, failure):
    root, database, _, options = setup
    source = root / "archives/filed.txt"
    source.write_text("synthetic filed source")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    artifact = root / "archives/artifact.txt"
    artifact.write_text("synthetic artifact")
    manifest = root / "archives/manifest.json"
    manifest.write_text(json.dumps({"artifacts": [{"filename": "../escape" if failure == "unsafe_artifact" else artifact.name, "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}]}))
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE filing_snapshots (source_reference TEXT, manifest_path TEXT, source_hash TEXT, snapshot_hash TEXT, payload_json TEXT)")
        db.execute("INSERT INTO filing_snapshots VALUES (?, ?, ?, ?, ?)", (str(source), str(manifest), digest, "a" * 64, json.dumps({"evidence_kind": "filed_return_pdf"})))
    if failure == "changed_source": source.write_text("changed source")
    if failure == "changed_artifact": artifact.write_text("changed artifact")
    if failure == "missing_artifact": artifact.unlink()
    if failure:
        with pytest.raises(files.BackupFilesError):
            make_backup(options)
    else:
        make_backup(options)


@pytest.mark.parametrize("case", ["unsupported", "transport_failure", "bad_json", "download_failure"])
def test_zero_exit_cryptcheck_without_hashes_requires_real_download(tmp_path, monkeypatch, case):
    from types import SimpleNamespace
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        if "cryptcheck" in args:
            assert "--use-json-log" in args
            return SimpleNamespace(returncode=1 if case == "transport_failure" else 0, stdout="file.txt\n", stderr="invalid-json" if case == "bad_json" else json.dumps({"msg": "1 hashes could not be checked"}) + "\n")
        assert "check" in args and "--download" in args
        return SimpleNamespace(returncode=1 if case == "download_failure" else 0, stdout="file.txt\n", stderr="")
    monkeypatch.setattr(files.subprocess, "run", command)
    if case == "unsupported":
        files._verify_copy(tmp_path / "rclone.conf", tmp_path, "files-crypt:", ["file.txt"], tmp_path)
        assert len(calls) == 2
    else:
        with pytest.raises(ValueError):
            files._verify_copy(tmp_path / "rclone.conf", tmp_path, "files-crypt:", ["file.txt"], tmp_path)
        assert len(calls) == (2 if case == "download_failure" else 1)


@pytest.mark.parametrize("location", ["synthetic-bucket/archive//private", "synthetic-bucket/archive/./private", "synthetic-bucket//archive/private", "synthetic-bucket/archive/../private", "/synthetic-bucket/archive/private"])
def test_policy_rejects_paths_that_rclone_would_normalize(location, monkeypatch):
    monkeypatch.setattr(policy, "_get_bucket_setting", lambda *args: pytest.fail("must reject before policy HTTP requests"))
    configs = {"files-crypt": {"type": "crypt", "remote": "raw:" + location}, "raw": {"type": "s3"}}
    with pytest.raises(policy.PolicyError):
        policy.check_remote(configs, "files-crypt:")


def test_restore_refuses_case_insensitive_alias_overwrites(setup, tmp_path):
    root, _, remote, options = setup
    probe = tmp_path / "CaseProbe"
    probe.write_text("probe")
    if not (tmp_path / "caseprobe").exists():
        pytest.skip("filesystem is case-sensitive")
    archive, manifest, index = make_backup(options)
    payload = json.loads(index.read_text())
    for name, content in (("A.txt", b"first"), ("a.txt", b"second")):
        remote.versions["files-crypt:tree/" + name] = [("2020-01-01T00:00:00.000000+00:00", content)]
        payload["files"].append({"path": name, "kind": "tree", "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)})
    remote.put("files-crypt:snapshots/" + index.name, json.dumps(payload).encode())
    target = tmp_path / "drill"
    with pytest.raises(FileExistsError):
        files.restore(archive=archive, manifest=manifest, config=options["config"], files_remote=options["files_remote"], evidence_remote=options["evidence_remote"], target=target, private_root=root)
    assert (target / "A.txt").read_bytes() == b"first"


@pytest.mark.parametrize("output", ["root", "ancestor", "relative"])
def test_output_directory_cannot_hide_the_private_tree(setup, output):
    root, _, _, _ = setup
    directory = root if output == "root" else root.parent if output == "ancestor" else Path("relative")
    with pytest.raises(files.BackupFilesError):
        files.inventory(root, [directory])


def test_filesystem_locator_through_an_excluded_symlink_is_not_covered(setup):
    from autonomo_taxes.ledger_db import LedgerDB
    root, database, _, options = setup
    database.unlink()
    folder = root / "evidence/target"
    folder.mkdir()
    source = folder / "source.txt"
    source.write_bytes(b"synthetic source")
    (root / "evidence/link").symlink_to(folder, target_is_directory=True)
    with LedgerDB.initialize(database) as db:
        document = db.upsert_document(document_type="expense_invoice", issued_on="2026-01-01", source_hash="d" * 64)
        file = db.upsert_file(content_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), byte_size=source.stat().st_size, media_type="text/plain")
        db.attach_file_to_document(document_id=document["document_id"], file_id=file["file_id"], attachment_role="source")
        backend = db.upsert_storage_backend(backend_key="synthetic_local", display_name="Synthetic local", driver_key="filesystem", provider_key="local", access_mode="read_only", config={"root": str(root / "evidence")})
        db.register_file_replica(file_id=file["file_id"], storage_backend_id=backend["storage_backend_id"], provider_locator="link/source.txt", is_primary=True, last_verified_at="2026-01-01T00:00:00Z")
    with pytest.raises(files.BackupFilesError, match="lacks a recoverable"):
        make_backup(options)
