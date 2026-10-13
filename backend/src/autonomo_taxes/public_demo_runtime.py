"""Validate an explicitly prepared demo directory and render its shared UI shell."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any

AS_OF = "2026-09-30"
PERIODS = ("2026-Q1", "2026-Q2", "2026-Q3")
RUNTIME = {"mode": "public_demo", "basePath": "/demo", "readOnly": True,
           "locales": ["en", "es", "ru"], "defaultLocale": "en"}
FILES = {"demo.sqlite", "demo-document.pdf", "demo-manifest.json"}


def load_demo_manifest(root: Path) -> tuple[Path, dict[str, Any]]:
    root = Path(root)
    if not root.is_absolute() or root.is_symlink() or any(char in str(root) for char in "%?#"):
        raise ValueError("Public demo root must be an absolute, non-symlink filesystem directory")
    root = root.resolve(strict=True)
    if not root.is_dir() or {path.name for path in root.iterdir()} != FILES:
        raise ValueError("Public demo root must contain only demo.sqlite, demo-document.pdf and demo-manifest.json")
    for name in FILES:
        path = root / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
            raise ValueError(f"Public demo artifact must be a regular file: {name}")
    if (root / "demo-manifest.json").stat().st_size > 64 * 1024:
        raise ValueError("Public demo manifest is too large")
    manifest = json.loads((root / "demo-manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or type(manifest.get("format")) is not int
            or manifest["format"] != 1 or manifest.get("mode") != "public_demo"
            or manifest.get("synthetic") is not True or manifest.get("as_of") != AS_OF
            or manifest.get("periods") != list(PERIODS) or manifest.get("default_period") != "2026-Q3"
            or type(manifest.get("schema_version")) is not int):
        raise ValueError("Invalid public demo manifest")
    hashes = manifest.get("sha256")
    if not isinstance(hashes, dict) or set(hashes) != {"demo.sqlite", "demo-document.pdf"}:
        raise ValueError("Invalid public demo artifact hashes")
    for name, expected in hashes.items():
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid public demo artifact hash")
        with (root / name).open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"Public demo artifact hash mismatch: {name}")
    with (root / "demo.sqlite").open("rb") as source:
        header = source.read(20)
        if not header.startswith(b"SQLite format 3\0") or header[18:20] != b"\x01\x01":
            raise ValueError("Public demo database must be checkpointed in DELETE journal mode")
    with (root / "demo-document.pdf").open("rb") as source:
        if not source.read(5) == b"%PDF-":
            raise ValueError("Public demo document must be a PDF")
    return root, manifest


class _RuntimeScript(HTMLParser):
    def __init__(self, html: str):
        super().__init__()
        self.html = html
        self.line_offsets = [0]
        for line in html.splitlines(keepends=True):
            self.line_offsets.append(self.line_offsets[-1] + len(line))
        self.start: int | None = None
        self.spans: list[tuple[int, int]] = []

    def source_offset(self) -> int:
        line, column = self.getpos()
        return self.line_offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("id") == "autonomo-runtime":
            if attributes.get("type", "").lower() != "application/json":
                raise ValueError("Public demo runtime script must contain JSON")
            self.start = self.source_offset() + len(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag == "script" and self.start is not None:
            self.spans.append((self.start, self.source_offset()))
            self.start = None


def render_demo_shell(content: bytes, packaged_assets: set[str]) -> bytes:
    html = content.decode("utf-8")
    parser = _RuntimeScript(html)
    parser.feed(html)
    if len(parser.spans) != 1 or parser.start is not None:
        raise ValueError("Shared frontend must contain exactly one public runtime placeholder")
    start, end = parser.spans[0]
    if json.loads(html[start:end]) != {}:
        raise ValueError("Shared frontend runtime placeholder must be empty")
    html = html[:start] + json.dumps(RUNTIME, separators=(",", ":")) + html[end:]
    def prefix(match):
        name = match[3].removeprefix("/")
        if name not in packaged_assets:
            return match[0]
        return match[1] + match[2] + "/demo/" + name + match[2]
    html = re.sub(r'''(\b(?:src|href)\s*=\s*)(["'])(/ui-assets/[^"']+)\2''', prefix, html)
    return html.encode("utf-8")
