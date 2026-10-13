"""The public fixture is a persistent native ledger, never a captured API response."""
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import socket
import sqlite3

from pypdf import PdfReader
import pytest

from autonomo_taxes.dashboard_service import build_dashboard_from_db
from autonomo_taxes.ledger_db import LedgerDB, LATEST_SCHEMA_VERSION
from autonomo_taxes.public_demo_data import (
    AS_OF, CANONICAL_DOCUMENT, DATABASE_NAME, DOCUMENT_NAME, MANIFEST_NAME,
    PERIODS, main, prepare_demo,
)

REFERENCE = Path(__file__).resolve().parents[2] / "reference"


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def logical_dump(root):
    with sqlite3.connect(f"file:{root / DATABASE_NAME}?mode=ro", uri=True) as connection:
        return "\n".join(connection.iterdump())


@pytest.fixture
def demo_data(tmp_path):
    root = tmp_path / "data"
    prepare_demo(root, reference_root=REFERENCE)
    return root


def test_single_explanatory_pdf_and_only_persistent_artifacts(demo_data):
    assert {p.name for p in demo_data.iterdir()} == {DATABASE_NAME, DOCUMENT_NAME, MANIFEST_NAME}
    reader = PdfReader(demo_data / DOCUMENT_NAME)
    assert len(reader.pages) == 1
    assert reader.metadata.title == "Demo document"
    page = reader.pages[0]
    text = page.extract_text()
    assert "NoAutonomo.ai demo document" in text
    assert "Every document link in the demo opens this same PDF." in text
    assert "fictional accounting data and is read-only" in text
    assert "no real invoices, receipts or personal information" in text
    assert not page.get("/Annots")
    assert tuple(page.mediabox) == (0, 0, 595, 842)
    manifest = json.loads((demo_data / MANIFEST_NAME).read_text())
    assert manifest == {
        "format": 1, "mode": "public_demo", "synthetic": True,
        "schema_version": LATEST_SCHEMA_VERSION, "as_of": AS_OF,
        "default_period": "2026-Q3", "periods": list(PERIODS),
        "sha256": {name: file_hash(demo_data / name) for name in (DATABASE_NAME, DOCUMENT_NAME)},
    }


def test_seed_is_logically_reproducible_without_host_paths(demo_data, tmp_path):
    other = tmp_path / "second"
    prepare_demo(other, reference_root=REFERENCE)
    first_dump = logical_dump(demo_data)
    assert first_dump == logical_dump(other)
    assert (demo_data / DOCUMENT_NAME).read_bytes() == (other / DOCUMENT_NAME).read_bytes()
    assert str(demo_data) not in first_dump
    assert ".seed-" not in first_dump
    assert "snapshot.json" not in first_dump
    assert "translations" not in first_dump


def test_distinct_documents_share_one_catalogued_source(demo_data):
    with LedgerDB.open(demo_data / DATABASE_NAME, read_only=True) as db:
        connection = db.connection
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 55
        assert connection.execute("SELECT COUNT(DISTINCT source_hash) FROM documents").fetchone()[0] == 55
        assert connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 49
        assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM document_attachments").fetchone()[0] == 55
        rows = connection.execute("""SELECT d.document_id,d.source_path,d.mime_type,f.content_sha256,
            da.display_name,da.attachment_role,fr.provider_locator,sb.access_mode,sb.config_json
            FROM documents d JOIN document_attachments da USING(document_id)
            JOIN files f USING(file_id) JOIN file_replicas fr USING(file_id)
            JOIN storage_backends sb USING(storage_backend_id)""").fetchall()
        assert len(rows) == 55
        for row in rows:
            assert row["source_path"] == CANONICAL_DOCUMENT
            assert row["mime_type"] == "application/pdf"
            assert row["content_sha256"] == file_hash(demo_data / DOCUMENT_NAME)
            assert row["display_name"] == row["provider_locator"] == DOCUMENT_NAME
            assert row["attachment_role"] == "source"
            assert row["access_mode"] == "read_only"
            assert json.loads(row["config_json"])["root"] == "/data"
        snapshots = connection.execute("SELECT * FROM filing_snapshots").fetchall()
        assert len(snapshots) == 4
        documents = {row["document_id"] for row in rows}
        assert all(row["filing_snapshot_id"] in documents and row["source_reference"] == CANONICAL_DOCUMENT
                   for row in snapshots)


def test_native_calculations_and_scenarios_are_retained(demo_data):
    with LedgerDB.open(demo_data / DATABASE_NAME, read_only=True) as db:
        for period in PERIODS:
            dashboard = build_dashboard_from_db(db, period_key=period, as_of=date.fromisoformat(AS_OF))
            rows = db.connection.execute("""SELECT t.*,tt.deductible_irpf_minor FROM transactions t
                JOIN periods p USING(period_id) JOIN tax_treatments tt USING(transaction_id)
                WHERE p.period_key=? AND t.lifecycle_status IN ('posted','included_in_snapshot')""", (period,)).fetchall()
            actual = dashboard["posted_actual"]
            income = sum(row["amount_eur_minor"] for row in rows if row["entry_type"] == "income")
            expenses = sum(row["deductible_irpf_minor"] or 0 for row in rows if row["entry_type"] == "expense")
            assert Decimal(str(actual["income_eur"])) * 100 == income
            assert Decimal(str(actual["expense_irpf_deductible_eur"])) * 100 == expenses
            previews = dashboard["tax_arithmetic_preview"]["posted_actual"]
            assert previews["modelo130"]["values"] and previews["modelo303"]["values"]
            assert {row["obligation_code"] for row in db.list_obligations(period_key=period)} == {"130", "303"}
        schedule = db.connection.execute("SELECT * FROM amortization_entries").fetchall()
        assert sum(row["amount_minor"] for row in schedule) == 180000
        assert len([row for row in schedule if row["recognition_transaction_id"]]) == 3
        assert db.connection.execute("SELECT COUNT(*) FROM taxpayer_profile").fetchone()[0] == 1
        assert db.connection.execute("SELECT COUNT(*) FROM counterparties").fetchone()[0] == 6
        assert db.connection.execute("SELECT COUNT(*) FROM aeat_cases").fetchone()[0] == 1
        assert db.connection.execute("SELECT COUNT(*) FROM validation_issues WHERE issue_status='open'").fetchone()[0] == 2


def test_seed_ignores_private_configuration_and_remains_offline(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir()
    canary = private / "config.yaml"
    canary.write_text("database: PRIVATE_DATA_MUST_NOT_BE_READ\n")
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(private))
    monkeypatch.chdir(private)
    def forbidden_network(*args, **kwargs):
        raise AssertionError("Demo initialization must stay offline")
    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    original_read = Path.read_text
    def guarded_read(path, *args, **kwargs):
        assert path != canary, "Private configuration was read"
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded_read)
    root = tmp_path / "data"
    prepare_demo(root, reference_root=REFERENCE)
    assert "PRIVATE_DATA_MUST_NOT_BE_READ" not in logical_dump(root)
    assert list(private.iterdir()) == [canary]


def test_refuses_overwrite_and_invalid_output_locations(demo_data, tmp_path):
    before = {p.name: p.read_bytes() for p in demo_data.iterdir()}
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        prepare_demo(demo_data, reference_root=REFERENCE)
    assert before == {p.name: p.read_bytes() for p in demo_data.iterdir()}
    with pytest.raises(ValueError, match="absolute"):
        prepare_demo(Path("relative-data"), reference_root=REFERENCE)
    repo = tmp_path / "repository"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: unrelated")
    with pytest.raises(ValueError, match="outside Git"):
        prepare_demo(repo / "data", reference_root=REFERENCE)
    link = tmp_path / "linked-data"
    link.symlink_to(demo_data, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink"):
        prepare_demo(link, reference_root=REFERENCE)
    missing = tmp_path / "missing-reference-output"
    with pytest.raises(FileNotFoundError):
        prepare_demo(missing, reference_root=tmp_path / "no-reference")
    assert not missing.exists()


def test_cli_requires_explicit_output_and_accepts_an_empty_mount(tmp_path, capsys):
    with pytest.raises(SystemExit) as missing_output:
        main([])
    assert missing_output.value.code == 2
    assert "--output" in capsys.readouterr().err
    root = tmp_path / "mounted-data"
    root.mkdir()
    assert main(["--output", str(root), "--reference-root", str(REFERENCE)]) == 0
    assert {path.name for path in root.iterdir()} == {DATABASE_NAME, DOCUMENT_NAME, MANIFEST_NAME}
