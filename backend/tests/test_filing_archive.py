import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from autonomo_taxes.filing_archive import write_filing_archive, verify_filing_archive
from autonomo_taxes.ledger_db import initialize, open_database
from test_tax_attribution import source


def archive_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr("autonomo_taxes.filing_archive._runtime_requirements", lambda: {"example-dependency":"1.0"})
    wheels = tmp_path / "wheel-cache"; wheels.mkdir()
    with zipfile.ZipFile(wheels / "example_dependency-1.0-py3-none-any.whl", "w") as z:
        z.writestr("example_dependency-1.0.dist-info/METADATA", "Name: example-dependency\nVersion: 1.0\n")
    db_path = tmp_path / "ledger.sqlite"
    with initialize(db_path) as db:
        source(db, tmp_path)
    return db_path, wheels


def test_archive_retains_frozen_sources_code_and_dependencies(tmp_path, monkeypatch):
    db_path, wheels = archive_fixture(tmp_path, monkeypatch)
    with open_database(db_path, read_only=True) as db:
        manifest = write_filing_archive(db, tmp_path / "archive", period_key="2025-Q1", dependency_dir=wheels)
    data = json.loads(manifest.read_text())
    names = {row["path"] for row in data["files"]}
    assert "ledger.sqlite" in names and "program/autonomo_taxes/books.py" in names
    assert "wheels/example_dependency-1.0-py3-none-any.whl" in names
    assert "books/tax-registers-review.xlsx" in names
    assert data["runtime_replay_verified"] is False
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert verify_filing_archive(manifest,expected_manifest_sha256=digest)["integrity_ok"] is True
    db_path.unlink()
    (tmp_path / "synthetic-source.txt").unlink()
    assert verify_filing_archive(manifest,expected_manifest_sha256=digest)["integrity_ok"] is True
    originals = json.loads((manifest.parent / "sources.json").read_text())
    assert len(originals) == 1
    (manifest.parent / originals[0]["retained_path"]).write_text("tampered")
    assert verify_filing_archive(manifest,expected_manifest_sha256=digest)["integrity_ok"] is False


@pytest.mark.parametrize("failure", ["dependency", "source"])
def test_missing_dependencies_or_changed_sources_cannot_leave_successful_archive(tmp_path, monkeypatch, failure):
    db_path, wheels = archive_fixture(tmp_path, monkeypatch)
    if failure == "dependency":
        next(wheels.iterdir()).unlink()
    else:
        (tmp_path / "synthetic-source.txt").write_text("changed")
    with open_database(db_path, read_only=True) as db:
        with pytest.raises(ValueError):
            write_filing_archive(db,tmp_path / "archive",period_key="2025-Q1",dependency_dir=wheels)
    assert not (tmp_path / "archive").exists()


def test_archive_rejects_reuse_traversal_and_symlinks(tmp_path, monkeypatch):
    db_path, wheels = archive_fixture(tmp_path, monkeypatch)
    with open_database(db_path, read_only=True) as db:
        manifest = write_filing_archive(db,tmp_path / "archive",period_key="2025-Q1",dependency_dir=wheels)
        original = manifest.read_bytes()
        with pytest.raises(FileExistsError):
            write_filing_archive(db,manifest.parent,period_key="2025-Q1",dependency_dir=wheels)
        assert manifest.read_bytes() == original
    payload = json.loads(original)
    payload["files"][0]["path"] = "../ledger.sqlite"
    manifest.write_text(json.dumps(payload))
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert verify_filing_archive(manifest,expected_manifest_sha256=digest)["integrity_ok"] is False
    manifest.write_bytes(original)
    first = manifest.parent / json.loads(original)["files"][0]["path"]
    first.unlink(); first.symlink_to(db_path)
    assert verify_filing_archive(manifest,expected_manifest_sha256=hashlib.sha256(original).hexdigest())["integrity_ok"] is False


def test_archive_retains_mapped_import_source_and_cli_verifies_without_database(tmp_path, monkeypatch, capsys):
    from autonomo_taxes.cli import main
    db_path, wheels = archive_fixture(tmp_path, monkeypatch)
    source_book = tmp_path / "original-book.csv"
    source_book.write_text("id,amount\nsynthetic,100\n")
    legacy_path = "Z:\\originals\\book.csv"
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({legacy_path:str(source_book)}))
    with open_database(db_path) as db:
        document = db.connection.execute("SELECT document_id FROM documents").fetchone()[0]
        db.add_document_source(document_id=document, source_book_line_id="synthetic:line-1",
                               source_hash="synthetic:row-hash", source_file=legacy_path)
    assert main(["filing-package","archive","--db",str(db_path),"--period","2025-Q1",
                 "--out-dir",str(tmp_path / "archive"),"--dependency-dir",str(wheels),
                 "--legacy-path-map",str(mapping)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["runtime_replay_verified"] is False
    source_book.unlink(); db_path.unlink()
    assert main(["filing-package","archive-verify","--manifest",result["manifest"],
                 "--expected-manifest-sha256",result["manifest_sha256"]]) == 0
    assert json.loads(capsys.readouterr().out)["integrity_ok"] is True


def test_archive_rejects_unlisted_executable_and_wrong_platform_wheel(tmp_path, monkeypatch):
    db_path, wheels = archive_fixture(tmp_path, monkeypatch)
    with open_database(db_path, read_only=True) as db:
        manifest = write_filing_archive(db,tmp_path / "archive",period_key="2025-Q1",dependency_dir=wheels)
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    (manifest.parent / "program/sitecustomize.py").write_text("raise RuntimeError('unlisted code')\n")
    check = verify_filing_archive(manifest,expected_manifest_sha256=digest)
    assert check["integrity_ok"] is False
    assert check["unexpected_files"] == ["program/sitecustomize.py"]
    next(wheels.iterdir()).rename(wheels / "example_dependency-1.0-cp27-cp27m-win32.whl")
    with open_database(db_path, read_only=True) as db:
        with pytest.raises(ValueError,match="dependencies|dependency"):
            write_filing_archive(db,tmp_path / "incompatible",period_key="2025-Q1",dependency_dir=wheels)


def test_archive_retains_filing_receipt_and_original_package_support(tmp_path, monkeypatch):
    db_path, wheels = archive_fixture(tmp_path, monkeypatch)
    receipt = tmp_path / "official-receipt.pdf"; receipt.write_bytes(b"Synthetic receipt evidence")
    calculation = tmp_path / "calculation.json"; calculation.write_text('{"synthetic":true}')
    support = tmp_path / "snapshot-manifest.json"
    support.write_text(json.dumps({"artifacts":[{"filename":calculation.name,"sha256":hashlib.sha256(calculation.read_bytes()).hexdigest()}]}))
    with open_database(db_path) as db:
        document = db.upsert_document(external_key="official-receipt", document_type="aeat_official",
            period_key="2025-Q1", issued_on="2025-04-12", source_hash=hashlib.sha256(receipt.read_bytes()).hexdigest())
        db.set_document_storage(document["document_id"],source_path=str(receipt),mime_type="application/pdf")
        filing = db.create_filing_snapshot("2025-Q1",source_reference="document:"+document["document_id"])
        db.connection.execute("UPDATE filing_snapshots SET manifest_path=? WHERE filing_snapshot_id=?",(str(support),filing["filing_snapshot_id"]))
        db.connection.commit()
    with open_database(db_path,read_only=True) as db:
        manifest = write_filing_archive(db,tmp_path / "archive",period_key="2025-Q1",dependency_dir=wheels)
    originals = json.loads((manifest.parent / "sources.json").read_text())
    assert {str(receipt),str(calculation),str(support)} <= {item["original_path"] for item in originals}
    for p in (receipt,calculation,support):
        p.unlink()
    assert verify_filing_archive(manifest,expected_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())["integrity_ok"]


def test_historical_import_keeps_provenance_separate_from_content_identity(tmp_path, monkeypatch):
    from autonomo_taxes.history_migration import _upsert_source_document
    db_path, wheels = archive_fixture(tmp_path, monkeypatch)
    book = tmp_path / "historical-book.csv"; book.write_text("date,number,amount\n2025-01-02,SYNTHETIC-8,100\n")
    with open_database(db_path) as db:
        party = db.upsert_counterparty(display_name="Example Historical Supplier",tax_id="TEST-ID")
        batch = db.add_import_batch(source_name="synthetic history",source_hash="synthetic:import-batch")
        document = _upsert_source_document(db,row={"date":"2025-01-02","document_number":"SYNTHETIC-8",
            "source_file":str(book),"source_book_line_id":"synthetic:line-8"},
            counterparty_id=party["counterparty_id"],import_batch_id=batch["import_batch_id"],
            document_type="expense_invoice",period_key="2025-Q1",total_minor=10000)
        assert document["source_hash"] != hashlib.sha256(book.read_bytes()).hexdigest()
        tx = db.add_transaction(period_key="2025-Q1",transaction_date="2025-01-02",booking_date="2025-01-02",
            entry_type="expense",description="Synthetic historical service",amount_minor=10000,
            lifecycle_status="posted",document_id=document["document_id"],counterparty_id=party["counterparty_id"])
        db.add_detailed_tax_treatment(transaction_id=tx["transaction_id"],treatment_type="invoice_review",
            tax_code="not_subject",deductible_irpf_minor=10000,include_modelo130=True)
    with open_database(db_path,read_only=True) as db:
        manifest = write_filing_archive(db,tmp_path / "archive",period_key="2025-Q1",dependency_dir=wheels)
    payload = json.loads(manifest.read_text())
    assert payload["historical_content_identity_unverified"] == [document["document_id"]]
    originals = json.loads((manifest.parent / "sources.json").read_text())
    retained = next(item for item in originals if item["document_id"] == document["document_id"])
    assert retained["provenance_hash"] == document["source_hash"]
    assert retained["sha256"] == hashlib.sha256(book.read_bytes()).hexdigest()
    assert retained["content_identity"] == "historical_provenance_only"


def test_archive_uses_the_retained_exporter_not_already_loaded_modules(tmp_path, monkeypatch):
    import autonomo_taxes.filing_archive as archive_module
    db_path, wheels = archive_fixture(tmp_path, monkeypatch)
    stage = archive_module._stage_program
    def changed_retained_program(root):
        stage(root)
        code = root / "program/autonomo_taxes/books.py"
        code.write_text(code.read_text().replace('"income": "income.csv"', '"income": "income-retained.csv"'))
    monkeypatch.setattr(archive_module,"_stage_program",changed_retained_program)
    with open_database(db_path,read_only=True) as db:
        manifest = write_filing_archive(db,tmp_path / "archive",period_key="2025-Q1",dependency_dir=wheels)
    assert (manifest.parent / "books/income-retained.csv").is_file()
    assert not (manifest.parent / "books/income.csv").exists()
    assert json.loads(manifest.read_text())["frozen_export_verified"] is True


def test_unperioded_authority_record_is_retained_through_actual_recording_api(tmp_path, monkeypatch):
    from autonomo_taxes.aeat_documents import record_aeat_document
    db_path, wheels = archive_fixture(tmp_path,monkeypatch)
    receipt = tmp_path / "authority.pdf"; receipt.write_bytes(b"%PDF-synthetic authority receipt")
    metadata = {"form_code":"303","filed_on":"2025-04-12T10:00:00","submission_reference":"SYNTHETIC-303"}
    monkeypatch.setattr("autonomo_taxes.aeat_documents.inspect_aeat_pdf",lambda _:metadata)
    with open_database(db_path) as db:
        record_aeat_document(db,receipt,tmp_path / "authority-archive",actor="synthetic-user",
            title="Synthetic periodic filing",procedure_kind="periodic_filing",form_code="303",
            document_kind="submission_receipt",status="submitted",occurred_at="2025-04-12T10:00:00",
            submission_reference="SYNTHETIC-303",procedure_code=None,requested_effective_on=None,
            primary_reference="SYNTHETIC-303",justificante_number="3037000000001",verification_code="SYNTHETICCSV0002",notes=None)
        document = db.connection.execute("SELECT * FROM documents WHERE document_type='aeat_official'").fetchone()
        assert document["period_id"] is None
        identifier = document["document_id"]
    with open_database(db_path,read_only=True) as db:
        manifest = write_filing_archive(db,tmp_path / "archive",period_key="2025-Q1",dependency_dir=wheels)
    assert identifier in {row["document_id"] for row in json.loads((manifest.parent / "sources.json").read_text())}


def test_snapshot_manifest_is_parsed_from_captured_bytes(tmp_path, monkeypatch):
    import shutil
    db_path, wheels = archive_fixture(tmp_path,monkeypatch)
    first = tmp_path / "first.json"; first.write_text("first generation")
    second = tmp_path / "second.json"; second.write_text("second generation")
    original = tmp_path / "snapshot.json"
    def payload(path):
        return json.dumps({"artifacts":[{"filename":path.name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}]})
    original.write_text(payload(first)); expected = hashlib.sha256(original.read_bytes()).hexdigest()
    with open_database(db_path) as db:
        snapshot = db.create_filing_snapshot("2025-Q1")
        db.connection.execute("UPDATE filing_snapshots SET manifest_path=? WHERE filing_snapshot_id=?",(str(original),snapshot["filing_snapshot_id"]))
        db.connection.commit()
    copy = shutil.copyfile
    def concurrent_replacement(source, target, **kwargs):
        result = copy(source,target,**kwargs)
        if Path(source) == original:
            original.write_text(payload(second))
        return result
    monkeypatch.setattr("autonomo_taxes.filing_archive.shutil.copyfile",concurrent_replacement)
    with open_database(db_path,read_only=True) as db:
        manifest = write_filing_archive(db,tmp_path / "archive",period_key="2025-Q1",dependency_dir=wheels)
    references = json.loads((manifest.parent / "filing-references.json").read_text())
    assert references[0]["sha256"] == expected
    paths = {row["original_path"] for row in json.loads((manifest.parent / "sources.json").read_text())}
    assert str(first) in paths and str(second) not in paths


def test_relative_filing_reference_resolves_only_by_verified_supplied_content(tmp_path, monkeypatch):
    db_path, wheels = archive_fixture(tmp_path,monkeypatch)
    original = tmp_path / "retained-receipt.pdf"; original.write_bytes(b"Synthetic historical filed receipt")
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    with open_database(db_path) as db:
        db.create_filing_snapshot("2025-Q1",source_reference="Old export/TAX_REPORT/receipt.pdf",source_hash=digest)
    with open_database(db_path,read_only=True) as db:
        manifest = write_filing_archive(db,tmp_path / "archive",period_key="2025-Q1",dependency_dir=wheels,source_files=[original])
    refs = json.loads((manifest.parent / "filing-references.json").read_text())
    assert refs[0]["sha256"] == digest
    original.write_bytes(b"Same location but different receipt")
    with open_database(db_path,read_only=True) as db:
        with pytest.raises(ValueError,match="unavailable"):
            write_filing_archive(db,tmp_path / "wrong",period_key="2025-Q1",dependency_dir=wheels,source_files=[original])


def test_supplied_reference_cannot_change_after_content_lookup(tmp_path, monkeypatch):
    import autonomo_taxes.filing_archive as archive_module
    db_path, wheels = archive_fixture(tmp_path,monkeypatch)
    original = tmp_path / "historical.pdf"; original.write_bytes(b"Synthetic original receipt")
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    with open_database(db_path) as db:
        db.create_filing_snapshot("2025-Q1",source_reference="Old export/receipt.pdf",source_hash=digest,
            snapshot_hash=digest,payload={"evidence_kind":"filed_return_pdf"})
    sha = archive_module._sha256
    def change_after_lookup(path):
        result = sha(path)
        if path == original:
            original.write_bytes(b"Changed after lookup")
        return result
    monkeypatch.setattr(archive_module,"_sha256",change_after_lookup)
    with open_database(db_path,read_only=True) as db:
        with pytest.raises(ValueError,match="hash differs"):
            write_filing_archive(db,tmp_path / "archive",period_key="2025-Q1",dependency_dir=wheels,source_files=[original])
