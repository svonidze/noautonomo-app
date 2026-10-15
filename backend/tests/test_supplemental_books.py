from datetime import date
import json

import pytest

from autonomo_taxes.filing_package import write_filing_package, verify_filing_package
from autonomo_taxes.ledger_db import initialize


def confirmation(**changes):
    return {"period_key": "2024-Q1", "confirmed_on": date.today().isoformat(),
            "confirmed_by": "Synthetic taxpayer", "evidence_ref": "review:test-1",
            "has_movements": False, **changes}


@pytest.mark.parametrize("evidence,status,has_book", [
    (None, "unconfirmed", False),
    (confirmation(), "confirmed_no_movements", True),
    (confirmation(has_movements=True), "unsupported_movements", False),
])
def test_absence_requires_explicit_period_evidence(tmp_path, evidence, status, has_book):
    with initialize(tmp_path / "db.sqlite") as db:
        db.ensure_period("2024-Q1")
        manifest = write_filing_package(db, tmp_path / "package", period_key="2024-Q1",
                                        supplemental_evidence=evidence)
        payload = json.loads(manifest.read_text())
        assert payload["supplemental_book_status"]["status"] == status
        assert (manifest.parent / "provisiones-suplidos.csv").exists() is has_book
        if has_book:
            assert len((manifest.parent / "provisiones-suplidos.csv").read_text().splitlines()) == 1
            assert payload["supplemental_book_status"]["period_total_eur"] == "0.00"
        assert payload["mandatory_books_ready"] is False
        assert verify_filing_package(db, manifest)["ok"] is True


@pytest.mark.parametrize("evidence", [
    {}, confirmation(period_key="2024-Q2"), confirmation(confirmed_on="2024-02-01"),
    confirmation(confirmed_on="2999-01-01"), confirmation(confirmed_by=None),
    confirmation(has_movements="false"),
])
def test_invalid_or_insufficient_confirmation_is_rejected(tmp_path, evidence):
    with initialize(tmp_path / "db.sqlite") as db:
        db.ensure_period("2024-Q1")
        with pytest.raises(ValueError):
            write_filing_package(db, tmp_path / "package", period_key="2024-Q1",
                                 supplemental_evidence=evidence)
        assert not (tmp_path / "package").exists()


def test_cli_build_and_database_independent_integrity(tmp_path, capsys):
    import hashlib
    from autonomo_taxes.cli import main

    database = tmp_path / "db.sqlite"
    with initialize(database) as db:
        db.ensure_period("2024-Q1")
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(confirmation()))
    assert main(["filing-package", "build", "--db", str(database), "--period", "2024-Q1",
                 "--out-dir", str(tmp_path / "package"), "--supplemental-evidence", str(evidence)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["submission_ready"] is False
    manifest = tmp_path / "package" / "filing-package-manifest.json"
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    database.unlink()
    args = ["filing-package", "integrity", "--manifest", str(manifest), "--expected-manifest-sha256", digest]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["integrity_ok"] is True
    (manifest.parent / "provisiones-suplidos.csv").write_text("changed")
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["integrity_ok"] is False
