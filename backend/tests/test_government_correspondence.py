import hashlib
import json
import sqlite3
from unittest.mock import patch

import pytest

from autonomo_taxes import government_cases as cases
from autonomo_taxes import government_correspondence as mail
from autonomo_taxes.ledger_db import LedgerDB, StaleRowVersionError
from autonomo_taxes.cli import main


@pytest.fixture
def db(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        yield db


def create(db, **extra):
    return cases.create_case(
        db,
        dict(
            authority_code="TGSS",
            title="Synthetic case",
            opened_at="2032-01-01T10:00:00Z",
            evidence_reference="Synthetic source",
            **extra,
        ),
        actor="test",
    )


def record(db, case, **extra):
    payload = dict(
        expected_row_version=case["row_version"],
        direction="incoming",
        channel="email",
        occurred_at="2032-02-01T10:00:00+01:00",
        subject="Synthetic request",
        body_text="Synthetic full text",
        source_provider="fixture",
        source_account="TEST-ACCOUNT",
        source_message_id="TEST-MSG-001",
    )
    payload.update(extra)
    return mail.add(db, case["case_id"], payload, actor="test")


def test_populated_migration_preserves_cases_actions_and_events(tmp_path):
    path = tmp_path / "ledger.sqlite"
    with (
        patch("autonomo_taxes.ledger_db.LATEST_SCHEMA_VERSION", 32),
        LedgerDB.initialize(path) as old,
    ):
        case_id = old.connection.execute("SELECT count(*) FROM aeat_cases").fetchone()[
            0
        ]
        # The schema-32 writer is emulated, since current writers require schema 33.
        old.connection.execute(
            "INSERT INTO aeat_cases (aeat_case_id,authority_code,title,procedure_kind,opened_at,current_status,created_at,updated_at,work_state,subject) VALUES ('TEST-CASE','TGSS','Synthetic','other','2032-01-01T00:00:00Z','rejected','2032-01-01','2032-01-01','action_required','Synthetic')"
        )
        old.connection.execute(
            "INSERT INTO government_actions VALUES ('TEST-ACTION','TEST-CASE','respond','Synthetic',NULL,'',0,'open',1,'2032-01-01','2032-01-01')"
        )
        old.connection.commit()
    with LedgerDB.open(path, apply_migrations=True) as migrated:
        detail = cases.case_detail(migrated, "TEST-CASE")
        assert detail["decision_outcome"] == "rejected"
        assert detail["actions"][0]["action_id"] == "TEST-ACTION"
        assert not migrated.connection.execute("PRAGMA foreign_key_check").fetchall()
        assert migrated.connection.execute("PRAGMA user_version").fetchone()[0] == 33


def test_new_authority_optional_reference_and_parent_cycles(db):
    parent = create(db)
    child = create(db, parent_case_id=parent["case_id"])
    custom = cases.create_case(
        db,
        dict(
            authority_code="TEST-AUTHORITY",
            authority_name="Synthetic municipality",
            title="Synthetic municipal case",
            opened_at="2032-01-01T00:00:00Z",
            evidence_reference="Synthetic",
        ),
        actor="test",
    )
    assert custom["authority_name"] == "Synthetic municipality"
    assert custom["primary_reference"] == ""
    assert (
        cases.case_detail(db, parent["case_id"])["related_cases"][0]["case_id"]
        == child["case_id"]
    )
    for value in (parent["case_id"], child["case_id"]):
        with pytest.raises(ValueError, match="cycle"):
            cases.update_case(
                db,
                parent["case_id"],
                dict(
                    expected_row_version=parent["row_version"],
                    work_state="action_required",
                    evidence_reference="Synthetic",
                    parent_case_id=value,
                ),
                actor="test",
            )


def test_message_retry_conflict_search_and_immutable_history(db):
    case = create(db)
    current = record(db, case)
    event_id = current["correspondence_event_id"]
    assert record(db, case)["idempotent"] is True
    assert len(current["correspondence"]) == 1
    assert current["decision_outcome"] is None
    assert current["work_state"] == case["work_state"]
    assert cases.list_cases(db, query="full text")["total"] == 1
    with pytest.raises(ValueError, match="different contents"):
        record(db, current, summary="Changed")
    with pytest.raises(ValueError, match="different contents"):
        record(db, create(db))
    corrected = record(
        db, current, supersedes_event_id=event_id, summary="Corrected interpretation"
    )
    original = next(m for m in corrected["correspondence"] if m["event_id"] == event_id)
    assert original["superseded"] is True
    assert original["summary"] == ""
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.connection.execute(
            "DELETE FROM government_case_events WHERE event_id=?", (event_id,)
        )
    db.connection.rollback()


def test_message_replies_versions_sources_and_request_boundaries(db):
    case = create(db)
    result = record(
        db, case, sender="Synthetic sender", recipients=["synthetic@example.invalid"]
    )
    other = create(db)
    with pytest.raises(ValueError, match="same case"):
        record(
            db,
            other,
            reply_to_event_id=result["correspondence_event_id"],
            source_message_id="TEST-OTHER",
        )
    with pytest.raises(StaleRowVersionError):
        record(db, case, source_message_id="TEST-NEW")
    for source in (
        "javascript:alert(1)",
        "http://example.invalid",
        "https://user:pass@example.invalid",
    ):
        with pytest.raises(ValueError, match="HTTPS"):
            record(db, result, source_url=source, source_message_id="TEST-NEW")
    with pytest.raises(ValueError, match="1 MiB"):
        record(db, result, body_text="x" * mail.MAX_REQUEST_BYTES)
    # Unicode and a body exceeding the old 4K event-note limit are retained.
    large = record(
        db, result, source_message_id="TEST-LARGE", body_text="Texto\n" * 20000
    )
    assert (
        next(
            m
            for m in large["correspondence"]
            if m["event_id"] == large["correspondence_event_id"]
        )["body_text"]
        == "Texto\n" * 20000
    )


def test_attachments_resume_deduplicate_and_preserve_originals(db, tmp_path):
    case = create(db)
    result = record(db, case, attachment_names=["source.eml", "scan.pdf"])
    event_id = result["correspondence_event_id"]
    assert result["correspondence"][0]["missing_attachments"] == [
        "scan.pdf",
        "source.eml",
    ]
    original = b"From: synthetic@example.invalid\r\nSubject: Synthetic\r\n\r\nSynthetic text\r\n"
    file = tmp_path / "source.eml"
    file.write_bytes(original)
    metadata = dict(
        expected_row_version=result["row_version"],
        correspondence_event_id=event_id,
        filename="source.eml",
    )
    attached = mail.attach(
        db, case["case_id"], file, tmp_path / "archive", metadata, actor="test"
    )
    assert mail.attach(
        db, case["case_id"], file, tmp_path / "archive", metadata, actor="test"
    )["idempotent"]
    assert attached["correspondence"][0]["missing_attachments"] == ["scan.pdf"]
    assert (
        attached["correspondence"][0]["attachments"][0]["source_hash"]
        == hashlib.sha256(original).hexdigest()
    )
    file.write_bytes(b"Changed")
    with pytest.raises(ValueError, match="different bytes"):
        mail.attach(
            db, case["case_id"], file, tmp_path / "archive", metadata, actor="test"
        )
    file.write_bytes(original)
    other = record(
        db, create(db), source_message_id="TEST-OTHER", attachment_names=["source.eml"]
    )
    mail.attach(
        db,
        other["case_id"],
        file,
        tmp_path / "archive",
        dict(
            expected_row_version=other["row_version"],
            correspondence_event_id=other["correspondence_event_id"],
            filename="source.eml",
        ),
        actor="test",
    )
    assert db.connection.execute("SELECT count(*) FROM files").fetchone()[0] == 1
    assert (
        len(list((tmp_path / "archive").rglob(hashlib.sha256(original).hexdigest())))
        == 1
    )
    assert not db.connection.execute("SELECT * FROM transactions").fetchall()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.connection.execute("DELETE FROM government_correspondence_attachments")
    db.connection.rollback()


def test_action_can_reference_message_without_changing_outcome(db):
    case = record(db, create(db))
    updated = cases.save_action(
        db,
        case["case_id"],
        dict(
            expected_row_version=case["row_version"],
            kind="respond",
            description="Review synthetic response",
            correspondence_event_id=case["correspondence_event_id"],
        ),
        actor="test",
    )
    assert (
        updated["actions"][0]["correspondence_event_id"]
        == case["correspondence_event_id"]
    )
    assert updated["decision_outcome"] is None


def test_cli_uses_one_mib_and_same_service(db, tmp_path, capsys):
    case = create(db)
    request = dict(
        expected_row_version=case["row_version"],
        direction="outgoing",
        channel="paper",
        occurred_at="2032-01-01T00:00:00Z",
        subject="Synthetic paper submission",
        body_text="z" * 70000,
    )
    file = tmp_path / "request.json"
    file.write_text(json.dumps(request))
    assert (
        main(
            [
                "government-cases",
                "correspondence",
                "--db",
                str(db.path),
                "--case-id",
                case["case_id"],
                "--actor",
                "test",
                "--input",
                str(file),
            ]
        )
        == 0
    )
    assert (
        len(json.loads(capsys.readouterr().out)["correspondence"][0]["body_text"])
        == 70000
    )


def test_historical_schema29_annual_branch_upgrades_without_losing_rows(tmp_path):
    from autonomo_taxes.ledger_db import _migration_31

    path = tmp_path / "historical.sqlite"
    with (
        patch("autonomo_taxes.ledger_db.LATEST_SCHEMA_VERSION", 28),
        LedgerDB.initialize(path) as old,
    ):
        _migration_31(old.connection)
        old.connection.execute("PRAGMA user_version=29")
        old.connection.commit()
        tables_before = {
            r[0]
            for r in old.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "receipt_registrations" not in tables_before
    with LedgerDB.open(path, apply_migrations=True) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 33
        assert db.connection.execute(
            "SELECT name FROM sqlite_master WHERE name='receipt_registrations'"
        ).fetchone()
        assert not db.connection.execute("PRAGMA foreign_key_check").fetchall()


def test_partial_historical_schema_is_rejected_without_advancing_version(tmp_path):
    from autonomo_taxes.ledger_db import _migration_31, LedgerDbError

    path = tmp_path / "partial.sqlite"
    with (
        patch("autonomo_taxes.ledger_db.LATEST_SCHEMA_VERSION", 28),
        LedgerDB.initialize(path) as old,
    ):
        _migration_31(old.connection)
        old.connection.execute("DROP TRIGGER annual_adjustments_no_update")
        old.connection.execute("PRAGMA user_version=29")
        old.connection.commit()
    with pytest.raises(LedgerDbError, match="immutability"):
        LedgerDB.open(path, apply_migrations=True)
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 29
        assert not check.execute(
            "SELECT name FROM sqlite_master WHERE name='receipt_registrations'"
        ).fetchone()


@pytest.mark.web
def test_http_message_limits_upload_download_and_csrf(tmp_path):
    from http.client import HTTPConnection
    import threading
    from autonomo_test_support.local_web import _config
    from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingServer

    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        case = create(db)
    server = LocalAccountingServer(
        ("127.0.0.1", 0), LocalAccountingApp(config, session_token="test-token")
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = HTTPConnection("127.0.0.1", server.server_address[1])
    headers = {
        "Cookie": "autonomo_session=test-token",
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Content-Type": "application/json",
    }

    def request(method, path, data=None, override=None):
        connection.request(
            method,
            path,
            body=json.dumps(data).encode() if isinstance(data, dict) else data,
            headers=headers if override is None else override,
        )
        response = connection.getresponse()
        body = response.read()
        status = response.status
        meta = dict(response.getheaders())
        return (
            status,
            json.loads(body)
            if "application/json" in meta.get("Content-Type", "")
            else body,
            meta,
        )

    path = f"/api/government-cases/{case['case_id']}"
    payload = dict(
        expected_row_version=case["row_version"],
        direction="incoming",
        channel="email",
        occurred_at="2032-01-01T00:00:00Z",
        subject="Synthetic HTTP correspondence",
        body_text="Text\n" * 20000,
        attachment_names=["source.eml"],
    )
    try:
        assert (
            request(
                "POST",
                path + "/correspondence",
                payload,
                {**headers, "Origin": "https://foreign.invalid"},
            )[0]
            == 400
        )
        assert request("POST", path + "/correspondence", payload, {})[0] == 400
        status, current, _ = request("POST", path + "/correspondence", payload)
        assert status == 200
        event_id = current["correspondence_event_id"]
        assert current["correspondence"][0]["body_text"] == payload["body_text"]
        assert (
            request(
                "POST",
                path + "/correspondence",
                b"{}",
                {**headers, "Content-Length": str(mail.MAX_REQUEST_BYTES + 1)},
            )[0]
            == 400
        )
        original = b"From: synthetic@example.invalid\r\nSubject: Synthetic\r\n\r\nOriginal body"
        metadata = json.dumps(
            dict(
                expected_row_version=current["row_version"],
                correspondence_event_id=event_id,
            )
        )
        boundary = "SYNTHETIC-BOUNDARY"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n{metadata}\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="source.eml"\r\nContent-Type: message/rfc822\r\n\r\n'.encode()
            + original
            + f"\r\n--{boundary}--\r\n".encode()
        )
        status, current, _ = request(
            "POST",
            path + "/documents",
            body,
            {**headers, "Content-Type": "multipart/form-data; boundary=" + boundary},
        )
        assert status == 201
        attachment = current["correspondence"][0]["attachments"][0]
        status, content, meta = request("GET", attachment["content_url"])
        assert status == 200 and content == original
        assert meta["Content-Type"] == "application/octet-stream"
        assert meta["Content-Disposition"] == 'attachment; filename="source.eml"'
        assert request("GET", attachment["content_url"], override={})[0] == 400
        assert (
            request("GET", attachment["content_url"].replace("/content", "/preview"))[0]
            == 415
        )
        assert request("GET", "/api/government-cases?q=Synthetic")[1]["total"] == 1
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_attachment_boundary_and_correction_inherits_original(db, tmp_path):
    case = record(db, create(db), attachment_names=["source.eml"])
    file = tmp_path / "source.eml"
    file.write_bytes(b"Original")
    case = mail.attach(
        db,
        case["case_id"],
        file,
        tmp_path / "archive",
        dict(
            expected_row_version=case["row_version"],
            correspondence_event_id=case["correspondence_event_id"],
            filename="source.eml",
        ),
        actor="test",
    )
    original = case["correspondence"][0]
    revised = record(
        db,
        case,
        supersedes_event_id=original["event_id"],
        attachment_names=["source.eml"],
        summary="Corrected",
    )
    correction = next(
        m
        for m in revised["correspondence"]
        if m["event_id"] == revised["correspondence_event_id"]
    )
    assert correction["missing_attachments"] == []
    assert correction["attachments"] == [
        {
            **original["attachments"][0],
            "event_id": correction["event_id"],
            "created_at": correction["attachments"][0]["created_at"],
        }
    ]
    file = tmp_path / "oversized"
    file.touch()
    with file.open("wb") as sparse:
        sparse.truncate(mail.MAX_FILE_BYTES + 1)
    with pytest.raises(ValueError, match="30 MB"):
        mail.attach(
            db,
            case["case_id"],
            file,
            tmp_path / "archive",
            dict(
                expected_row_version=case["row_version"],
                correspondence_event_id=original["event_id"],
            ),
            actor="test",
        )


def test_existing_verified_original_is_linked_without_copying(db, tmp_path):
    from autonomo_taxes.storage_service import register_local_source_replica

    archive = tmp_path / "archive"
    archive.mkdir()
    original = archive / "original.pdf"
    original.write_bytes(b"%PDF-1.4\nSynthetic original")
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    doc = db.upsert_document(
        document_type="aeat_official",
        document_number=None,
        issued_on="2032-01-01",
        currency="EUR",
        lifecycle_status="approved",
        source_hash=digest,
    )
    doc = db.set_document_storage(
        doc["document_id"],
        source_path=str(original),
        mime_type="application/pdf",
        expected_row_version=doc["row_version"],
    )
    register_local_source_replica(
        db,
        document_id=doc["document_id"],
        source_path=original,
        media_type="application/pdf",
        storage_root=archive,
    )
    case = record(db, create(db), attachment_names=["copy.pdf"])
    attached = mail.attach(
        db,
        case["case_id"],
        original,
        archive,
        dict(
            expected_row_version=case["row_version"],
            correspondence_event_id=case["correspondence_event_id"],
            filename="copy.pdf",
        ),
        actor="test",
    )
    assert (
        attached["correspondence"][0]["attachments"][0]["document_id"]
        == doc["document_id"]
    )
    assert not (archive / "correspondence").exists()
    assert db.connection.execute(
        "SELECT source_path FROM documents WHERE document_id=?", (doc["document_id"],)
    ).fetchone()[0] == str(original)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.connection.execute(
            "UPDATE documents SET mime_type='text/html' WHERE document_id=?",
            (doc["document_id"],),
        )
    db.connection.rollback()


def test_missing_old_replica_does_not_block_supplied_original(db, tmp_path):
    from autonomo_taxes.storage_service import register_local_source_replica

    archive = tmp_path / "archive"
    archive.mkdir()
    original = archive / "original.eml"
    original.write_bytes(b"Original")
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    doc = db.upsert_document(
        document_type="government_correspondence",
        document_number=None,
        issued_on="2032-01-01",
        currency="EUR",
        lifecycle_status="approved",
        source_hash=digest,
    )
    register_local_source_replica(
        db,
        document_id=doc["document_id"],
        source_path=original,
        media_type="message/rfc822",
        storage_root=archive,
    )
    original.unlink()
    supplied = tmp_path / "supplied.eml"
    supplied.write_bytes(b"Original")
    case = record(db, create(db), attachment_names=["supplied.eml"])
    updated = mail.attach(
        db,
        case["case_id"],
        supplied,
        archive,
        dict(
            expected_row_version=case["row_version"],
            correspondence_event_id=case["correspondence_event_id"],
            filename="supplied.eml",
        ),
        actor="test",
    )
    assert updated["correspondence"][0]["missing_attachments"] == []


def test_date_only_keeps_source_calendar_date_and_correction_retry(db):
    case = create(db)
    saved = record(
        db, case, date_precision="day", occurred_at="2032-01-01T00:00:00+02:00"
    )
    message = saved["correspondence"][0]
    assert message["occurred_at"].startswith("2032-01-01")
    assert message["date_precision"] == "day"
    revised = record(
        db,
        saved,
        date_precision="day",
        occurred_at="2032-01-01T00:00:00+02:00",
        supersedes_event_id=message["event_id"],
        summary="Correction",
    )
    assert record(
        db,
        saved,
        date_precision="day",
        occurred_at="2032-01-01T00:00:00+02:00",
        supersedes_event_id=message["event_id"],
        summary="Correction",
    )["idempotent"]


def test_case_can_acquire_official_reference_with_audited_metadata(db):
    case = create(db)
    changed = cases.update_case(
        db,
        case["case_id"],
        dict(
            expected_row_version=case["row_version"],
            work_state=case["work_state"],
            evidence_reference="Synthetic official receipt",
            title="Synthetic updated case",
            primary_reference="TEST-OFFICIAL-REF",
        ),
        actor="test",
    )
    assert changed["primary_reference"] == "TEST-OFFICIAL-REF"
    audit = next(e for e in changed["events"] if e["kind"] == "workflow")
    assert (
        json.loads(audit["payload_json"])["metadata_before"]["primary_reference"] == ""
    )
    other = create(db)
    with pytest.raises(ValueError, match="already exists"):
        cases.update_case(
            db,
            other["case_id"],
            dict(
                expected_row_version=other["row_version"],
                work_state=other["work_state"],
                evidence_reference="Synthetic",
                primary_reference="TEST-OFFICIAL-REF",
            ),
            actor="test",
        )


@pytest.mark.parametrize("field", ["direction", "channel", "date_precision"])
@pytest.mark.parametrize("value", [[], {}, None, True, 1])
def test_correspondence_rejects_nontext_choices(db, field, value):
    with pytest.raises(ValueError):
        record(db, create(db), **{field: value})


def test_attached_business_document_can_still_progress_without_changing_original(
    db, tmp_path
):
    case = record(db, create(db))
    file = tmp_path / "evidence.txt"
    file.write_bytes(b"Synthetic immutable original")
    current = mail.attach(
        db,
        case["case_id"],
        file,
        tmp_path / "archive",
        dict(
            expected_row_version=case["row_version"],
            correspondence_event_id=case["correspondence_event_id"],
            filename="evidence.txt",
        ),
        actor="test",
    )
    document_id = current["correspondence"][0]["attachments"][0]["document_id"]
    db.connection.execute(
        "UPDATE documents SET lifecycle_status='needs_review' WHERE document_id=?",
        (document_id,),
    )
    db.connection.commit()
    assert (
        db.connection.execute(
            "SELECT lifecycle_status FROM documents WHERE document_id=?", (document_id,)
        ).fetchone()[0]
        == "needs_review"
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.connection.execute(
            "UPDATE documents SET source_hash=? WHERE document_id=?",
            ("a" * 64, document_id),
        )
    db.connection.rollback()
