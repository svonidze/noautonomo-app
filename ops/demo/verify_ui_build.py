"""Bind the ordinary packaged UI to the clean source archive used for the image."""

import hashlib
import json
from pathlib import Path
import re
import sys


def verify(root: Path, source_sha: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ValueError("SOURCE_SHA must be the complete reviewed commit SHA")
    manifest = json.loads((root / "build-manifest.json").read_text())
    if (manifest.get("contract") != 1 or manifest.get("source_sha") != source_sha
            or manifest.get("source_dirty") is not False or manifest.get("test_only") is not False):
        raise ValueError("Build the ordinary frontend from the clean reviewed commit")
    if manifest.get("node") != "24.20.0" or manifest.get("npm") != "11.19.0":
        raise ValueError("Expected Node 24.20.0 and npm 11.19.0")
    files = manifest.get("files")
    if not isinstance(files, dict) or "index.html" not in files:
        raise ValueError("Missing frontend resource manifest")
    for name, checksum in files.items():
        if not re.fullmatch(r"index\.html|ui-assets/[\w.-]+\.(js|css)", name):
            raise ValueError("Invalid frontend resource name")
        target = root / name
        if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
            raise ValueError("Frontend resources must be ordinary files")
        if hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
            raise ValueError(f"Frontend checksum mismatch: {name}")
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    if actual != set(files) | {"build-manifest.json"}:
        raise ValueError("Frontend directory contains unlisted resources")


if __name__ == "__main__":
    verify(Path(sys.argv[1]), sys.argv[2])
