"""Run installed ops against disposable data; never use a real runtime environment."""
from __future__ import annotations

from autonomo_test_support.paths import REPO_ROOT

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile

import pytest

from autonomo_taxes.account_settings import save_backups
from autonomo_taxes.backup_settings import SETTINGS_FILENAME
from autonomo_taxes.ledger_db import LedgerDB

ROOT = REPO_ROOT


@pytest.fixture
def installed(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    database = private / "autonomo.sqlite"
    with LedgerDB.initialize(database):
        pass
    database.chmod(0o600)
    (private / "config.yaml").write_text("synthetic: true\n")
    (private / "config.yaml").chmod(0o600)
    ops = tmp_path / "ops"
    ops.mkdir()
    for filename in ("lib.sh", "backup.sh", "preflight.sh"):
        shutil.copy2(ROOT / "ops" / filename, ops / filename)
    shutil.copy2(ROOT / "ops" / "backup_state.py", ops / "backup_state.py")
    shutil.copy2(ROOT / "ops" / "backup" / "backup_private_root.py", ops / "backup_private_root.py")
    shutil.copy2(ROOT / "backend/src/autonomo_taxes/backup_settings.py", ops / "backup_settings.py")
    (ops / "sops").mkdir()
    for filename in ("lib.sh", "preflight.sh"):
        shutil.copy2(ROOT / "ops" / "sops" / filename, ops / "sops" / filename)
    releases = tmp_path / "releases"
    old_release = releases / "releases" / ("a" * 40)
    old_release.mkdir(parents=True)
    (releases / "current").symlink_to(old_release)
    # Intentionally no venv/package in this old release: the installed reader survives rollback.
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "python3").symlink_to(sys.executable)
    if not shutil.which("flock"):
        (binaries / "flock").write_text("#!/bin/sh\nexit 0\n")
        (binaries / "flock").chmod(0o755)
    runtime = tmp_path / "runtime.env"
    runtime.write_text(f"AUTONOMO_PRIVATE_ROOT={private}\nAUTONOMO_RELEASE_ROOT={releases}\nAUTONOMO_BACKUP_KEEP=12\nAUTONOMO_MONTHLY_BACKUP_KEEP=12\n")
    runtime.chmod(0o600)
    bootstrap = tmp_path / "bootstrap.env"
    bootstrap.write_text(f"AUTONOMO_PRIVATE_ROOT={private}\nAUTONOMO_RELEASE_ROOT={releases}\nAUTONOMO_SECRET_SYNC_REQUIRED=0\n")
    bootstrap.chmod(0o600)
    env = {key: value for key, value in os.environ.items() if not key.startswith("AUTONOMO_")}
    env.update({"AUTONOMO_RUNTIME_ENV_PATH": str(runtime), "AUTONOMO_BOOTSTRAP_ENV_PATH": str(bootstrap), "PATH": f"{binaries}:{os.environ['PATH']}"})
    return private, database, ops, env


def _run(script, env):
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=30, check=False)


def _seed_archives(private, database, backup_class):
    output = private / "backups" / ("private-root" if backup_class == "daily" else "monthly")
    spec = importlib.util.spec_from_file_location("synthetic_backup", ROOT / "ops" / "backup" / "backup_private_root.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for _ in range(8):
        module.create_backup(private_root=private, database=database, output_dir=output, keep=12)
    return output


@pytest.mark.parametrize("backup_class,keep", [("daily", 7), ("monthly", 3)])
def test_installed_ops_applies_saved_policy_and_prunes_only_when_run(installed, backup_class, keep):
    private, database, ops, env = installed
    output = _seed_archives(private, database, backup_class)
    save_backups(database, private, {"expected_revision": "missing", "daily_keep": 7, "monthly_keep": 3, "confirm_local_pruning": True})
    assert len(list(output.glob("*.tar.gz"))) == 8
    env["AUTONOMO_BACKUP_CLASS"] = backup_class
    result = _run(ops / "backup.sh", env)
    assert result.returncode == 0, result.stderr
    archives = sorted(output.glob("*.tar.gz"))
    assert len(archives) == keep
    assert len(list(output.glob("*.manifest.json"))) == keep
    with tarfile.open(archives[-1]) as archive:
        assert SETTINGS_FILENAME in archive.getnames()
        snapshot = archive.extractfile("autonomo.sqlite").read()
    restored = private.parent / "snapshot.sqlite"
    restored.write_bytes(snapshot)
    with sqlite3.connect(restored) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    marker = json.loads((private / "backups" / f"last-backup-{backup_class}.json").read_text())
    assert marker["format"] == 2
    assert marker["local_status"] == "verified"
    assert marker["keep"] == keep
    assert marker["settings_format"] == 1
    assert marker["offsite"] == "no"
    assert marker["offsite_status"] == "disabled"


def test_invalid_saved_policy_leaves_archives_and_success_marker_untouched(installed):
    private, database, ops, env = installed
    output = _seed_archives(private, database, "daily")
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    marker = private / "backups" / "last-backup-daily.json"
    marker.write_text("existing fixture marker")
    path = private / SETTINGS_FILENAME
    path.write_text('{"private":"must not appear in errors"')
    path.chmod(0o600)
    result = _run(ops / "backup.sh", env)
    assert result.returncode != 0
    assert "operator repair" in result.stderr
    assert "must not appear" not in result.stderr
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    assert marker.read_text() == "existing fixture marker"


def test_no_policy_does_not_need_installed_reader(installed):
    private, _, ops, env = installed
    (ops / "backup_settings.py").unlink()
    result = _run(ops / "backup.sh", env)
    assert result.returncode == 0, result.stderr
    marker = json.loads((private / "backups" / "last-backup-daily.json").read_text())
    assert marker["keep"] == 12


def _fake_crypt_remote(env, tmp_path):
    config = tmp_path / "rclone.conf"
    config.write_text("[crypt]\ntype = crypt\n")
    config.chmod(0o600)
    commands = tmp_path / "rclone-commands"
    rclone = Path(env["PATH"].split(":", 1)[0]) / "rclone"
    rclone.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = --config ]; then shift 2; fi\n"
        "case \"$1\" in\n"
        "  config) printf 'type = crypt\\n' ;;\n"
        "  copyto) printf '%s\\n' \"$*\" >> \"$FAKE_RCLONE_STATE\" ;;\n"
        "  cryptcheck) printf '%s\\n' \"$*\" >> \"$FAKE_RCLONE_STATE\"\n"
        "    case \"$*\" in *\"${FAKE_CRYPTCHECK_FAIL:-no-such-pair}\"*) exit 1 ;; esac\n"
        "    case \"$*\" in *\"${FAKE_CRYPTCHECK_SILENT:-no-such-pair}\"*) exit 0 ;; esac\n"
        "    previous=\n"
        "    for argument in \"$@\"; do\n"
        "      [ \"$previous\" = --include ] && printf '%s\\n' \"${argument#/}\"\n"
        "      previous=\"$argument\"\n"
        "    done ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n"
    )
    rclone.chmod(0o755)
    runtime = Path(env["AUTONOMO_RUNTIME_ENV_PATH"])
    runtime.write_text(
        runtime.read_text()
        + f"AUTONOMO_RCLONE_CONFIG={config}\nAUTONOMO_RCLONE_REMOTE=crypt:daily\n"
    )
    env["FAKE_RCLONE_STATE"] = str(commands)
    return commands


def test_offsite_marker_records_checksum_verified_upload_without_download(installed, tmp_path):
    private, _, ops, env = installed
    commands = _fake_crypt_remote(env, tmp_path)

    result = _run(ops / "backup.sh", env)

    assert result.returncode == 0, result.stderr
    marker = json.loads((private / "backups" / "last-backup-daily.json").read_text())
    assert marker["offsite"] == "yes"
    assert marker["offsite_status"] == "acknowledged"
    log = commands.read_text()
    assert log.count("copyto") == 2
    assert f"--include /{marker['archive']}" in log
    assert f"--include /{marker['manifest']}" in log
    assert "size" not in log


def test_verified_upload_keeps_only_the_newest_local_pair(installed, tmp_path):
    private, database, ops, env = installed
    output = _seed_archives(private, database, "daily")
    unmatched = sorted(output.glob("*.tar.gz"))[2].name.removesuffix(".tar.gz")
    _fake_crypt_remote(env, tmp_path)
    env["FAKE_CRYPTCHECK_FAIL"] = unmatched

    result = _run(ops / "backup.sh", env)

    assert result.returncode == 0, result.stderr
    marker = json.loads((private / "backups" / "last-backup-daily.json").read_text())
    kept = {path.name.removesuffix(".tar.gz") for path in output.glob("*.tar.gz")}
    assert kept == {marker["archive"].removesuffix(".tar.gz"), unmatched}
    assert {path.name.removesuffix(".manifest.json") for path in output.glob("*.manifest.json")} == kept
    assert f"kept {unmatched}.tar.gz" in result.stderr


def test_offsite_pruning_keeps_the_newest_pair_for_a_trailing_slash_backup_dir(installed, tmp_path):
    private, database, ops, env = installed
    output = _seed_archives(private, database, "daily")
    _fake_crypt_remote(env, tmp_path)
    runtime = Path(env["AUTONOMO_RUNTIME_ENV_PATH"])
    runtime.write_text(runtime.read_text() + f"AUTONOMO_BACKUP_DIR={output}/\n")

    result = _run(ops / "backup.sh", env)

    assert result.returncode == 0, result.stderr
    marker = json.loads((private / "backups" / "last-backup-daily.json").read_text())
    assert [path.name for path in output.glob("*.tar.gz")] == [marker["archive"]]
    assert [path.name for path in output.glob("*.manifest.json")] == [marker["manifest"]]


def test_cryptcheck_without_reported_matches_deletes_nothing(installed, tmp_path):
    private, database, ops, env = installed
    output = _seed_archives(private, database, "daily")
    silent = sorted(output.glob("*.tar.gz"))[1].name.removesuffix(".tar.gz")
    _fake_crypt_remote(env, tmp_path)
    env["FAKE_CRYPTCHECK_SILENT"] = silent

    result = _run(ops / "backup.sh", env)

    assert result.returncode == 0, result.stderr
    assert (output / f"{silent}.tar.gz").is_file()
    assert (output / f"{silent}.manifest.json").is_file()
    assert len(list(output.glob("*.tar.gz"))) == 2


def test_unverified_upload_prunes_nothing_beyond_the_count_limit(installed, tmp_path):
    private, database, ops, env = installed
    output = _seed_archives(private, database, "daily")
    _fake_crypt_remote(env, tmp_path)
    env["FAKE_CRYPTCHECK_FAIL"] = "private-root-"

    result = _run(ops / "backup.sh", env)

    assert result.returncode != 0
    assert len(list(output.glob("*.tar.gz"))) == 9
    marker = json.loads((private / "backups" / "last-backup-daily.json").read_text())
    assert marker["local_status"] == "verified"
    assert marker["offsite_status"] == "failed"


def test_upload_failure_preserves_local_readiness_and_records_failure(installed, tmp_path):
    private, _, ops, env = installed
    config = tmp_path / "rclone.conf"
    config.write_text("[crypt]\ntype = crypt\n")
    config.chmod(0o600)
    rclone_state = tmp_path / "rclone-state"
    rclone_state.mkdir()
    rclone = Path(env["PATH"].split(":", 1)[0]) / "rclone"
    rclone.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = --config ]; then shift 2; fi\n"
        "case \"$1\" in\n"
        "  config) printf 'type = crypt\\n' ;;\n"
        "  copyto) exit 3 ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n"
    )
    rclone.chmod(0o755)
    runtime = Path(env["AUTONOMO_RUNTIME_ENV_PATH"])
    runtime.write_text(
        runtime.read_text()
        + f"AUTONOMO_RCLONE_CONFIG={config}\nAUTONOMO_RCLONE_REMOTE=crypt:daily\n"
    )
    result = _run(ops / "backup.sh", env)

    assert result.returncode != 0
    marker = json.loads((private / "backups" / "last-backup-daily.json").read_text())
    assert marker["local_status"] == "verified"
    assert marker["offsite"] == "no"
    assert marker["offsite_status"] == "failed"


@pytest.mark.parametrize("sops", [False, True])
def test_both_preflights_fail_closed_for_invalid_policy(installed, sops):
    private, _, ops, env = installed
    path = private / SETTINGS_FILENAME
    path.write_text('{"format":1,"daily_keep":0,"monthly_keep":3}')
    path.chmod(0o600)
    script = ops / "sops" / "preflight.sh" if sops else ops / "preflight.sh"
    result = _run(script, env)
    assert result.returncode != 0
    assert "operator repair" in result.stderr
    assert not (private / "backups").exists()


@pytest.mark.parametrize("sops", [False, True])
def test_both_installers_deliver_the_standalone_reader(sops):
    path = ROOT / "ops" / "sops" / "install-systemd-user-units.sh" if sops else ROOT / "ops" / "install-systemd-user-units.sh"
    installer = path.read_text()
    source = "$source_ops_dir" if sops else "$script_dir"
    assert f'install -m 644 "{source}/../backend/src/autonomo_taxes/backup_settings.py" "$ops_root/backup_settings.py"' in installer


@pytest.fixture
def database_installed(installed, tmp_path):
    """New backup writers with the unchanged readers from the deployed baseline."""
    import hashlib
    import subprocess
    private, database, ops, env = installed
    for name in ("backup_files.py", "backup_storage_policy.py"):
        shutil.copy2(ROOT / "ops" / name, ops / name)
    # The reachable main baseline has byte-identical readers to deployed 81b3b54.
    expected_readers = {
        'lib.sh': '5f567545827d26caf9acb70867191339e89a0603ca7f7917b3c38a224fe504b4',
        'backup_state.py': '750e371155cd26aa1cbb41552b18d89393d450d9b5af6d38bc4eda1bfa13ff46',
        'backup_readiness.py': '20854cb9755e73648f9b1f0326fe8c87f621b6066cfac1dca386ce3f01de0692',
        'verify_backup.py': '432480b44367064db536458f53c71f81353fa0c372eb0701c992e6b790731d75',
    }
    for name, expected_digest in expected_readers.items():
        old = subprocess.run(["git", "show", f"410c8955408006d9b1dc0b2f0d4677c4c67cee54:ops/{name}"], cwd=ROOT, capture_output=True, check=True)
        assert hashlib.sha256(old.stdout).hexdigest() == expected_digest
        (ops / name).write_bytes(old.stdout)
    shutil.copy2(ROOT / "ops/backup/restore_private_root.py", ops / "restore_private_root.py")
    # Network policy behavior is covered separately; this fixture tests the job wiring.
    (ops / "backup_storage_policy.py").write_text(
        "class PolicyError(ValueError): pass\n"
        "def check_remote(configs, remote):\n"
        "    if remote not in {'files-crypt:', 'evidence-crypt:'}: raise PolicyError('missing named crypt root')\n"
        "    return ('synthetic', 'synthetic-bucket', remote[:-1] + '/')\n"
    )
    config = tmp_path / "rclone.conf"
    config.write_text("synthetic")
    config.chmod(0o600)
    state = tmp_path / "versioned-remote.json"
    env["FAKE_VERSIONED_STATE"] = str(state)
    script = '''import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, os.environ['FAKE_TEST_SUPPORT'])
spec = importlib.util.spec_from_file_location('synthetic_versioned_remote', os.environ['FAKE_TEST_REMOTE_MODULE'])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
remote = module.VersionedRemote()
state = Path(os.environ['FAKE_VERSIONED_STATE'])
if state.exists():
    data = json.loads(state.read_text())
    remote.versions = {key: [(stamp, base64.b64decode(value)) for stamp, value in rows] for key, rows in data['versions'].items()}
    remote.commands = data['commands']
arguments = sys.argv[1:]
if arguments[:1] == ['--config']: arguments = arguments[2:]
if arguments[0] == 'config':
    if arguments[1] == 'dump': print(json.dumps({'archive-crypt': {'type': 'crypt'}}))
    else: print('type = crypt')
    sys.exit(0)
if '--files-from-raw' in arguments and os.environ.get('FAKE_OLD_NO_HASH'):
    paths = Path(arguments[arguments.index('--files-from-raw') + 1]).read_text().splitlines()
    if os.environ['FAKE_OLD_NO_HASH'] in paths:
        if arguments[0] == 'cryptcheck':
            print(''.join(path + '\\n' for path in paths), end='')
            print(json.dumps({'msg': '1 hashes could not be checked'}), file=sys.stderr)
            sys.exit(0)
        if arguments[0] == 'check' and os.environ.get('FAKE_OLD_DOWNLOAD_FAILURE'):
            sys.exit(2)
if os.environ.get('FAKE_FILES_FAILURE') == arguments[0]:
    sys.exit(2)
try:
    print(remote(Path('unused'), *arguments), end='')
finally:
    state.write_text(json.dumps({'versions': {key: [(stamp, base64.b64encode(value).decode()) for stamp, value in rows] for key, rows in remote.versions.items()}, 'commands': remote.commands}))
'''
    executable = Path(env["PATH"].split(":", 1)[0]) / "rclone"
    executable.write_text(f"#!{sys.executable}\n" + script)
    executable.chmod(0o755)
    env["FAKE_TEST_SUPPORT"] = str(ROOT / "tests/support")
    env["FAKE_TEST_REMOTE_MODULE"] = str(ROOT / "tests/ops/test_backup_files.py")
    runtime = Path(env["AUTONOMO_RUNTIME_ENV_PATH"])
    runtime.write_text(runtime.read_text() + f"AUTONOMO_BACKUP_SCOPE=database\nAUTONOMO_RCLONE_CONFIG={config}\nAUTONOMO_RCLONE_REMOTE=archive-crypt:\nAUTONOMO_RCLONE_MONTHLY_REMOTE=archive-crypt:\nAUTONOMO_RCLONE_FILES_REMOTE=files-crypt:\nAUTONOMO_RCLONE_EVIDENCE_REMOTE=evidence-crypt:\n")
    for name in ("inbox/new.txt", "evidence/source.txt", "archives/filed.txt"):
        path = private / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic")
    return private, database, ops, env


@pytest.mark.parametrize("backup_class", ["daily", "monthly"])
def test_database_job_with_old_readers_acknowledges_only_after_file_index(database_installed, backup_class):
    private, database, ops, env = database_installed
    env["AUTONOMO_BACKUP_CLASS"] = backup_class
    result = _run(ops / "backup.sh", env)
    assert result.returncode == 0, result.stderr
    marker = json.loads((private / "backups" / f"last-backup-{backup_class}.json").read_text())
    assert marker["local_status"] == "verified" and marker["offsite_status"] == "acknowledged"
    output = private / "backups" / ("private-root" if backup_class == "daily" else "monthly")
    with tarfile.open(output / marker["archive"]) as tar:
        assert set(tar.getnames()) == {"autonomo.sqlite", "config.yaml"}
    commands = json.loads(Path(env["FAKE_VERSIONED_STATE"]).read_text())["commands"]
    assert any(command[0] == "copyto" and "snapshots/" in command[2] for command in commands)
    if backup_class == "daily":
        release_root = Path(env["AUTONOMO_RUNTIME_ENV_PATH"]).parent / "releases"
        readiness = subprocess.run([sys.executable, str(ops / "backup_readiness.py"), "--runtime-env", env["AUTONOMO_RUNTIME_ENV_PATH"], "--private-root", str(private), "--release-root", str(release_root)], capture_output=True, text=True)
        assert readiness.returncode == 0, readiness.stderr
    verification = subprocess.run([sys.executable, str(ops / "verify_backup.py"), "--class", backup_class, "--private-root", str(private), "--backup-dir", str(output), "--remote", "archive-crypt:", "--rclone-config", str(Path(env["AUTONOMO_RUNTIME_ENV_PATH"]).parent / "rclone.conf"), "--restore-script", str(ops / "restore_private_root.py")], env=env, capture_output=True, text=True)
    assert verification.returncode == 0, verification.stderr


def test_database_job_file_copy_failure_keeps_local_pair_and_old_archives(database_installed):
    private, database, ops, env = database_installed
    output = _seed_archives(private, database, "daily")
    env["FAKE_FILES_FAILURE"] = "copy"
    result = _run(ops / "backup.sh", env)
    assert result.returncode != 0
    marker = json.loads((private / "backups/last-backup-daily.json").read_text())
    assert marker["local_status"] == "verified" and marker["offsite_status"] == "failed"
    assert len(list(output.glob("*.tar.gz"))) == 9


@pytest.mark.parametrize("failure", ["missing_remote", "reconcile"])
def test_database_job_stops_before_creation_or_rotation(database_installed, failure):
    private, database, ops, env = database_installed
    output = _seed_archives(private, database, "daily")
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    runtime = Path(env["AUTONOMO_RUNTIME_ENV_PATH"])
    if failure == "missing_remote":
        runtime.write_text(runtime.read_text().replace("AUTONOMO_RCLONE_FILES_REMOTE=files-crypt:\n", ""))
    else:
        binary = runtime.parent / "releases/releases" / ("a" * 40) / ".venv/bin/autonomo-tax"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\nexit 2\n")
        binary.chmod(0o755)
        runtime.write_text(runtime.read_text() + "AUTONOMO_RECONCILE_BACKENDS=synthetic\n")
    result = _run(ops / "backup.sh", env)
    assert result.returncode != 0
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    assert not (private / "backups/last-backup-daily.json").exists()


@pytest.mark.parametrize("sops", [False, True])
def test_both_installers_deliver_file_history_helpers(sops):
    path = ROOT / "ops/sops/install-systemd-user-units.sh" if sops else ROOT / "ops/install-systemd-user-units.sh"
    source = "$source_ops_dir" if sops else "$script_dir"
    assert f'install -m 644 "{source}/backup_files.py" "{source}/backup_storage_policy.py" "$ops_root/"' in path.read_text()


@pytest.mark.parametrize("download_fails", [False, True])
def test_database_pruning_requires_hashes_or_download_for_old_large_pairs(database_installed, download_fails):
    import base64
    private, database, ops, env = database_installed
    output = _seed_archives(private, database, "daily")
    old_archive = sorted(output.glob("*.tar.gz"))[0]
    versions = {}
    for path in output.iterdir():
        versions["archive-crypt:" + path.name] = [["2020-01-01T00:00:00.000000+00:00", base64.b64encode(path.read_bytes()).decode()]]
    Path(env["FAKE_VERSIONED_STATE"]).write_text(json.dumps({"versions": versions, "commands": []}))
    env["FAKE_OLD_NO_HASH"] = old_archive.name
    if download_fails:
        env["FAKE_OLD_DOWNLOAD_FAILURE"] = "1"
    result = _run(ops / "backup.sh", env)
    assert result.returncode == 0, result.stderr
    marker = json.loads((private / "backups/last-backup-daily.json").read_text())
    assert marker["offsite_status"] == "acknowledged"
    assert old_archive.exists() is download_fails
    assert len(list(output.glob("*.tar.gz"))) == (2 if download_fails else 1)
