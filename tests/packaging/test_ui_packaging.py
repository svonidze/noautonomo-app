from autonomo_test_support.paths import REPO_ROOT
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


def project(root: Path, *, corrupt: bool = False, missing: bool = False, core: bool = False) -> None:
    source = REPO_ROOT / ("backend" if core else "packages/ui")
    (root / "setup.py").write_text((source / "setup.py").read_text())
    (root / "pyproject.toml").write_text((source / "pyproject.toml").read_text())
    (root / "README.md").write_text("Synthetic packaging fixture")
    package = root / ("src/autonomo_taxes" if core else "src/autonomo_taxes_ui")
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    if missing:
        return
    dist = package / "dist"
    (dist / "ui-assets").mkdir(parents=True)
    files = {"index.html": b'<script src="/ui-assets/current.js"></script>', "ui-assets/current.js": b"export {};"}
    for name, body in files.items():
        (dist / name).write_bytes(body)
    checks = {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}
    if corrupt:
        checks["ui-assets/current.js"] = "0" * 64
    (dist / "build-manifest.json").write_text(json.dumps({"contract": 1, "files": checks}))


@pytest.mark.parametrize("missing", [True, False])
def test_distributable_refuses_missing_or_corrupt_frontend(tmp_path, missing):
    project(tmp_path, missing=missing, corrupt=not missing)
    result = subprocess.run([sys.executable, "-m", "build", "--wheel"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Run npm --prefix frontend ci --include=dev and npm --prefix frontend run build from the repository root" in result.stderr + result.stdout
    assert not list((tmp_path / "dist").glob("*.whl"))


def test_wheel_does_not_retain_an_obsolete_cached_bundle(tmp_path):
    project(tmp_path)
    cached = tmp_path / "build/lib/autonomo_taxes_ui/dist/ui-assets"
    cached.mkdir(parents=True)
    (cached / "obsolete.js").write_text("old code")
    result = subprocess.run([sys.executable, "-m", "build", "--wheel"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    wheel, = (tmp_path / "dist").glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        resources = [name for name in archive.namelist() if name.startswith("autonomo_taxes_ui/dist/")]
        assert set(resources) == {"autonomo_taxes_ui/dist/" + name for name in ("index.html", "build-manifest.json", "ui-assets/current.js")}


def test_wheel_ships_the_expense_rule_catalog(tmp_path):
    project(tmp_path, core=True, missing=True)
    catalog = (REPO_ROOT / "backend/src/autonomo_taxes/expense_rules.json").read_bytes()
    (tmp_path / "src/autonomo_taxes/expense_rules.json").write_bytes(catalog)
    result = subprocess.run([sys.executable, "-m", "build", "--wheel"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    wheel, = (tmp_path / "dist").glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("autonomo_taxes/expense_rules.json") == catalog
        assert not any("web_ui/" in name or name.startswith("autonomo_taxes_ui/") for name in archive.namelist())


def test_optional_launcher_rejects_a_mismatched_core_before_web_import(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("optional_ui_launcher", REPO_ROOT / "packages/ui/src/autonomo_taxes_ui/cli.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    monkeypatch.setattr(launcher, "version", lambda name: "0.1.0" if name.endswith("-ui") else "0.2.0")
    with pytest.raises(SystemExit, match="versions must match"):
        launcher.main(["--help"])
