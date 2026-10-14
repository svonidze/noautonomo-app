from __future__ import annotations
from autonomo_test_support.paths import REPO_ROOT

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest


ROOT = REPO_ROOT
OPS = ROOT / "ops"


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


backup_tool = _module("backup_private_root_for_recovery_tests", ROOT / "ops" / "backup" / "backup_private_root.py")
state_tool = _module("backup_state", OPS / "backup_state.py")
verifier = _module("verify_backup_for_recovery_tests", OPS / "verify_backup.py")


@pytest.fixture
def backup_pair(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    database = private / "autonomo.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA user_version = 23")
        db.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO sample VALUES (1)")
    database.chmod(0o600)
    output = private / "backups" / "private-root"
    archive, manifest = backup_tool.create_backup(private_root=private, database=database, output_dir=output, keep=5)
    return private, database, archive, manifest


def _marker(private, database, archive, manifest, backup_class="daily"):
    marker = private / "backups" / f"last-backup-{backup_class}.json"
    payload = state_tool.build_local_marker(
        backup_class=backup_class, archive=archive, manifest=manifest,
        app_sha="a" * 40, database=database, keep=5, remote_configured=True,
    )
    state_tool.atomic_json(marker, payload)
    state_tool.update_offsite(marker, "acknowledged")
    return marker


def test_deploy_readiness_uses_only_verified_local_pair(backup_pair, tmp_path):
    private, database, archive, manifest = backup_pair
    _marker(private, database, archive, manifest)
    release_root = tmp_path / "releases"
    release = release_root / "releases" / ("a" * 40)
    release.mkdir(parents=True)
    (release_root / "current").symlink_to(release)
    runtime = tmp_path / "runtime.env"
    runtime.write_text(f"AUTONOMO_BACKUP_DIR={archive.parent}\n")
    runtime.chmod(0o600)

    completed = subprocess.run([
        sys.executable, str(OPS / "backup_readiness.py"), "--runtime-env", str(runtime),
        "--private-root", str(private), "--release-root", str(release_root),
    ], text=True, capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    assert "local backup readiness passed" in completed.stdout

    state_tool.update_offsite(private / "backups" / "last-backup-daily.json", "failed")
    remote_failed = subprocess.run(completed.args, text=True, capture_output=True, check=False)
    assert remote_failed.returncode == 0
    assert "offsite upload is not acknowledged" in remote_failed.stderr


def test_deploy_readiness_rejects_corrupt_local_pair(backup_pair, tmp_path):
    private, database, archive, manifest = backup_pair
    _marker(private, database, archive, manifest)
    archive.write_bytes(archive.read_bytes() + b"corrupt")
    release_root = tmp_path / "releases"
    release = release_root / "releases" / ("a" * 40)
    release.mkdir(parents=True)
    (release_root / "current").symlink_to(release)
    runtime = tmp_path / "runtime.env"
    runtime.write_text(f"AUTONOMO_BACKUP_DIR={archive.parent}\n")

    completed = subprocess.run([
        sys.executable, str(OPS / "backup_readiness.py"), "--runtime-env", str(runtime),
        "--private-root", str(private), "--release-root", str(release_root),
    ], text=True, capture_output=True, check=False)

    assert completed.returncode != 0
    assert "local backup readiness failed" in completed.stderr


def test_deploy_readiness_rejects_legacy_marker(backup_pair, tmp_path):
    private, database, archive, manifest = backup_pair
    marker = _marker(private, database, archive, manifest)
    payload = json.loads(marker.read_text())
    payload["format"] = 1
    state_tool.atomic_json(marker, payload)
    release_root = tmp_path / "releases"
    release = release_root / "releases" / ("a" * 40)
    release.mkdir(parents=True)
    (release_root / "current").symlink_to(release)
    runtime = tmp_path / "runtime.env"
    runtime.write_text(f"AUTONOMO_BACKUP_DIR={archive.parent}\n")

    completed = subprocess.run([
        sys.executable, str(OPS / "backup_readiness.py"), "--runtime-env", str(runtime),
        "--private-root", str(private), "--release-root", str(release_root),
    ], text=True, capture_output=True, check=False)

    assert completed.returncode != 0
    assert "not deployment-ready" in completed.stderr


@pytest.fixture(params=("daily", "monthly"))
def verification_pair(backup_pair, tmp_path, monkeypatch, request):
    private, database, archive, manifest = backup_pair
    backup_class = request.param
    if backup_class == "monthly":
        output = private / "backups" / "monthly"
        output.mkdir()
        shutil.copy2(archive, output / archive.name)
        shutil.copy2(manifest, output / manifest.name)
        archive, manifest = output / archive.name, output / manifest.name
    marker = _marker(private, database, archive, manifest, backup_class)
    remote = tmp_path / "remote"
    remote.mkdir()
    shutil.copy2(archive, remote / archive.name)
    shutil.copy2(manifest, remote / manifest.name)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    rclone = binaries / "rclone"
    rclone.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = --config ]; then shift 2; fi\n'
        '[ "$1" = copyto ] || exit 2\n'
        '[ "$(basename "$2")" != "$FAKE_READ_FAILURE" ] || exit 3\n'
        'cp "$FAKE_REMOTE/$(basename "$2")" "$3"\n'
    )
    rclone.chmod(0o755)
    config = tmp_path / "rclone.conf"
    config.write_text("[crypt]\ntype = crypt\n")
    config.chmod(0o600)
    monkeypatch.setenv("PATH", f"{binaries}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_REMOTE", str(remote))
    monkeypatch.setenv("FAKE_READ_FAILURE", "")
    arguments = [
        sys.executable, str(OPS / "verify_backup.py"), "--private-root", str(private),
        "--backup-dir", str(archive.parent), "--remote", f"crypt:{backup_class}",
        "--rclone-config", str(config), "--restore-script", str(OPS / "backup" / "restore_private_root.py"),
    ]
    # Existing monthly callers can keep omitting --class.
    if backup_class == "daily":
        arguments.extend(["--class", "daily"])
    return {
        "class": backup_class, "private": private, "database": database,
        "archive": archive, "manifest": manifest, "marker": marker,
        "remote": remote, "config": config, "binaries": binaries, "arguments": arguments,
    }


def test_verifier_downloads_and_restores_exact_pair(verification_pair):
    pair = verification_pair
    marker_bytes = pair["marker"].read_bytes()
    # The live database changes after the snapshot and must survive the drill.
    with sqlite3.connect(pair["database"]) as database:
        database.execute("INSERT INTO sample VALUES (2)")
    live_bytes = pair["database"].read_bytes()

    completed = subprocess.run(pair["arguments"], text=True, capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    attempt = json.loads((pair["private"] / "backups" / f'last-backup-verification-attempt-{pair["class"]}.json').read_text())
    success = json.loads((pair["private"] / "backups" / f'last-backup-verified-{pair["class"]}.json').read_text())
    assert attempt["status"] == success["status"] == "success"
    assert success["class"] == pair["class"]
    assert success["sqlite_schema"] == 23
    assert success["archive_sha256"] == json.loads(marker_bytes)["archive_sha256"]
    assert pair["marker"].read_bytes() == marker_bytes
    assert pair["database"].read_bytes() == live_bytes
    other_class = "monthly" if pair["class"] == "daily" else "daily"
    assert not (pair["private"] / "backups" / f"last-backup-verified-{other_class}.json").exists()


@pytest.mark.parametrize("failure,member", [("corrupt", "archive"), ("corrupt", "manifest"), ("read", "archive"), ("read", "manifest")])
def test_verifier_failure_preserves_success_and_local_marker(verification_pair, monkeypatch, failure, member):
    pair = verification_pair
    previous = pair["private"] / "backups" / f'last-backup-verified-{pair["class"]}.json'
    state_tool.atomic_json(previous, {"class": pair["class"], "status": "success", "recorded_at": "2026-01-01T00:00:00Z"})
    previous_bytes = previous.read_bytes()
    marker_bytes = pair["marker"].read_bytes()
    live_bytes = pair["database"].read_bytes()
    if failure == "corrupt":
        downloaded = pair["remote"] / pair[member].name
        downloaded.write_bytes(downloaded.read_bytes() + b"corrupt")
    else:
        monkeypatch.setenv("FAKE_READ_FAILURE", pair[member].name)

    completed = subprocess.run(pair["arguments"], text=True, capture_output=True, check=False)

    assert completed.returncode != 0
    attempt = json.loads((pair["private"] / "backups" / f'last-backup-verification-attempt-{pair["class"]}.json').read_text())
    assert attempt["class"] == pair["class"]
    assert attempt["status"] == "failed"
    assert attempt["failure_code"] == ("download_hash_mismatch" if failure == "corrupt" else "remote_download_failed")
    assert previous.read_bytes() == previous_bytes
    assert pair["marker"].read_bytes() == marker_bytes
    assert pair["database"].read_bytes() == live_bytes


def test_verifier_rejects_invalid_restored_member(verification_pair):
    pair = verification_pair
    document = json.loads(pair["manifest"].read_text())
    document["files"][0]["sha256"] = "0" * 64
    state_tool.atomic_json(pair["manifest"], document)
    payload = json.loads(pair["marker"].read_text())
    payload["manifest_sha256"] = state_tool.sha256_file(pair["manifest"])
    state_tool.atomic_json(pair["marker"], payload)
    shutil.copy2(pair["manifest"], pair["remote"] / pair["manifest"].name)
    live_bytes = pair["database"].read_bytes()

    completed = subprocess.run(pair["arguments"], text=True, capture_output=True, check=False)

    assert completed.returncode != 0
    attempt = json.loads((pair["private"] / "backups" / f'last-backup-verification-attempt-{pair["class"]}.json').read_text())
    assert attempt["failure_code"] == "archive_validation_failed"
    assert pair["database"].read_bytes() == live_bytes
    assert not (pair["private"] / "backups" / f'last-backup-verified-{pair["class"]}.json').exists()


@pytest.mark.parametrize("age_hours", [2, 49, 74, -1])
def test_verifier_marker_freshness_at_month_boundary(verification_pair, monkeypatch, age_hours):
    pair = verification_pair
    now = datetime(2026, 10, 1, 1, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(verifier, "datetime", FrozenDatetime)
    payload = json.loads(pair["marker"].read_text())
    payload["backup_created_at"] = (now - timedelta(hours=age_hours)).strftime("%Y%m%dT%H%M%S.%fZ")
    state_tool.atomic_json(pair["marker"], payload)
    arguments = dict(
        private_root=pair["private"], backup_dir=pair["archive"].parent,
        remote=f'crypt:{pair["class"]}', config=pair["config"],
        restore_script=OPS / "backup" / "restore_private_root.py",
        max_age_hours=48 if pair["class"] == "daily" else 72, backup_class=pair["class"],
    )
    if age_hours == 2 and pair["class"] == "daily":
        verifier.verify(**arguments)
        assert (pair["private"] / "backups" / "last-backup-verified-daily.json").exists()
    else:
        with pytest.raises(SystemExit, match=f'{pair["class"]}_marker_not_current'):
            verifier.verify(**arguments)
        assert not (pair["private"] / "backups" / f'last-backup-verified-{pair["class"]}.json').exists()


def test_installed_wrapper_verifies_matching_class(verification_pair, tmp_path):
    pair = verification_pair
    installed = tmp_path / "ops"
    installed.mkdir()
    for filename in ("lib.sh", "verify-backup.sh", "verify_backup.py", "backup_state.py"):
        shutil.copy2(OPS / filename, installed / filename)
    shutil.copy2(OPS / "backup" / "restore_private_root.py", installed / "restore_private_root.py")
    (pair["binaries"] / "python3").symlink_to(sys.executable)
    if not shutil.which("flock"):
        flock = pair["binaries"] / "flock"
        flock.write_text("#!/bin/sh\nexit 0\n")
        flock.chmod(0o755)
    runtime = tmp_path / "runtime.env"
    remote_key = "AUTONOMO_RCLONE_REMOTE" if pair["class"] == "daily" else "AUTONOMO_RCLONE_MONTHLY_REMOTE"
    runtime.write_text(
        f'AUTONOMO_PRIVATE_ROOT={pair["private"]}\nAUTONOMO_RELEASE_ROOT={tmp_path / "releases"}\n'
        f'AUTONOMO_RCLONE_CONFIG={pair["config"]}\n{remote_key}=crypt:{pair["class"]}\n'
    )
    runtime.chmod(0o600)
    env = {key: value for key, value in os.environ.items() if not key.startswith("AUTONOMO_")}
    env["AUTONOMO_RUNTIME_ENV_PATH"] = str(runtime)

    completed = subprocess.run(["bash", str(installed / "verify-backup.sh"), pair["class"]], env=env, text=True, capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    success = json.loads((pair["private"] / "backups" / f'last-backup-verified-{pair["class"]}.json').read_text())
    assert success["class"] == pair["class"]
    assert success["status"] == "success"
