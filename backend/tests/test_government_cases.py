from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import sqlite3

import pytest

from autonomo_taxes.ledger_db import LedgerDB, StaleRowVersionError
from autonomo_taxes import government_cases as cases
from autonomo_taxes.case_reminders import run, settings, resolve_message
from autonomo_taxes.aeat_documents import unified_aeat_documents
from autonomo_taxes.cli import main


@pytest.fixture
def db(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as value:
        yield value


def create(db, authority="TGSS", reference="SYN-CASE-001"):
    return cases.create_case(
        db,
        {
            "authority_code": authority,
            "title": "Synthetic government case",
            "subject": "Synthetic appeal",
            "primary_reference": reference,
            "opened_at": "2032-02-01T10:00:00+01:00",
            "evidence_reference": "Synthetic filing receipt",
        },
        actor="test",
    )


def action(db, case, due="2032-04-08", confirmed=True):
    return cases.save_action(
        db,
        case["case_id"],
        {
            "kind": "respond",
            "description": "Review synthetic request",
            "due_on": due,
            "deadline_source": "Synthetic authority PDF",
            "confirmed": confirmed,
            "expected_row_version": case["row_version"],
        },
        actor="test",
    )


def event(db, case, **extra):
    return cases.add_event(
        db,
        case["case_id"],
        {
            "kind": "decision",
            "occurred_at": "2032-04-01T10:00:00+02:00",
            "evidence_reference": "Synthetic decision",
            "notes": "Synthetic rejection",
            "outcome": "rejected",
            "expected_row_version": case["row_version"],
            **extra,
        },
        actor="test",
    )


def test_migration_preserves_populated_legacy_and_immutable_events(tmp_path):
    path = tmp_path / "ledger.sqlite"
    with (
        patch("autonomo_taxes.ledger_db.LATEST_SCHEMA_VERSION", 31),
        LedgerDB.initialize(path) as db,
    ):
        doc = db.upsert_document(
            document_type="aeat_official",
            document_number=None,
            issued_on="2032-02-01",
            currency="EUR",
            lifecycle_status="approved",
            source_hash="a" * 64,
        )
        row = db.create_aeat_case_with_document(
            document_id=doc["document_id"],
            source_hash="a" * 64,
            title="Synthetic ROI",
            procedure_kind="roi_registration",
            procedure_code="G322",
            form_code="036",
            document_kind="resolution",
            status="approved",
            occurred_at="2032-02-01T10:00:00+01:00",
            requested_effective_on=None,
            primary_reference="SYN-ROI",
            submission_reference=None,
            justificante_number=None,
            verification_code=None,
            notes="Synthetic",
            actor="test",
        )
        before = [
            tuple(r) for r in db.connection.execute("SELECT * FROM aeat_case_events")
        ]
    with LedgerDB.open(path, apply_migrations=True) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 32
        assert [
            tuple(r) for r in db.connection.execute("SELECT * FROM aeat_case_events")
        ] == before
        current = cases.case_detail(db, row["aeat_case_id"])
        assert (
            current["authority_code"] == "AEAT"
            and current["current_status"] == "approved"
        )
        assert current["documents"][0]["document_id"] == doc["document_id"]
        assert not db.connection.execute("PRAGMA foreign_key_check").fetchall()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.connection.execute("UPDATE aeat_case_events SET notes='changed'")
        db.connection.rollback()
        create(db)
        assert all(
            r["aeat_case_id"] == row["aeat_case_id"]
            for r in unified_aeat_documents(db.connection)
        )


def test_rejection_keeps_work_open_and_close_requires_no_actions(db):
    case = action(db, create(db))
    rejected = event(db, case)
    assert (
        rejected["current_status"] == "rejected"
        and rejected["work_state"] == "action_required"
    )
    with pytest.raises(ValueError, match="open actions"):
        cases.update_case(
            db,
            case["case_id"],
            {
                "expected_row_version": rejected["row_version"],
                "work_state": "closed",
                "evidence_reference": "Operator decision",
            },
            actor="test",
        )
    row = rejected["actions"][0]
    completed = cases.save_action(
        db,
        case["case_id"],
        {
            "kind": row["kind"],
            "description": row["description"],
            "due_on": row["due_on"],
            "deadline_source": row["deadline_source"],
            "confirmed": True,
            "status": "completed",
            "expected_row_version": rejected["row_version"],
            "action_version": row["row_version"],
        },
        actor="test",
        action_id=row["action_id"],
    )
    closed = cases.update_case(
        db,
        case["case_id"],
        {
            "expected_row_version": completed["row_version"],
            "work_state": "closed",
            "evidence_reference": "Operator decision",
        },
        actor="test",
    )
    assert closed["work_state"] == "closed"
    assert not cases.list_cases(db)["actions"]


def test_stale_versions_and_append_only_deadline_changes(db):
    first = create(db)
    second = action(db, first)
    with pytest.raises(StaleRowVersionError):
        action(db, first)
    row = second["actions"][0]
    changed = cases.save_action(
        db,
        first["case_id"],
        {
            "kind": "respond",
            "description": row["description"],
            "due_on": "2032-04-09",
            "deadline_source": "Corrected source",
            "confirmed": False,
            "expected_row_version": second["row_version"],
            "action_version": row["row_version"],
        },
        actor="test",
        action_id=row["action_id"],
    )
    assert changed["actions"][0]["confirmed"] == 0
    assert any(e["kind"] == "action_updated" for e in changed["events"])
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.connection.execute("DELETE FROM government_case_events")
    db.connection.rollback()
    with pytest.raises(ValueError):
        cases.save_action(
            db,
            first["case_id"],
            {
                "kind": "pay",
                "description": "x",
                "due_on": None,
                "deadline_source": "",
                "confirmed": True,
                "expected_row_version": changed["row_version"],
            },
            actor="test",
        )


def test_pdf_dates_hash_idempotency_and_legacy_scope(db, tmp_path, monkeypatch):
    case = create(db)
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-synthetic fixture")
    monkeypatch.setattr("autonomo_taxes.aeat_documents.inspect_aeat_pdf", lambda p: {})
    payload = {
        "expected_row_version": case["row_version"],
        "document_kind": "certificate",
        "occurred_at": "2032-04-01T11:00:00+02:00",
        "notes": "Synthetic",
        "placed_at": "2032-04-01T09:00:00+02:00",
        "received_at": "2032-04-01T11:00:00+02:00",
        "access_expires_at": "2032-04-11T23:59:59+02:00",
    }
    saved = cases.attach_pdf(
        db, case["case_id"], pdf, tmp_path / "archive", payload, actor="test"
    )
    again = cases.attach_pdf(
        db, case["case_id"], pdf, tmp_path / "archive", payload, actor="test"
    )
    assert again["idempotent"] and len(again["documents"]) == 1
    assert datetime.fromisoformat(
        saved["documents"][0]["received_at"]
    ) == datetime.fromisoformat(payload["received_at"])
    assert not unified_aeat_documents(db.connection)
    equivalent = {
        **payload,
        "occurred_at": "2032-04-01T09:00:00Z",
        "placed_at": "2032-04-01T07:00:00Z",
        "received_at": "2032-04-01T09:00:00Z",
        "access_expires_at": "2032-04-11T21:59:59Z",
    }
    assert cases.attach_pdf(
        db, case["case_id"], pdf, tmp_path / "archive", equivalent, actor="test"
    )["idempotent"]
    with pytest.raises(ValueError, match="different"):
        cases.attach_pdf(
            db,
            case["case_id"],
            pdf,
            tmp_path / "archive",
            {**payload, "received_at": ""},
            actor="test",
        )
    with pytest.raises(Exception):
        db.record_aeat_case_status(
            case["case_id"],
            status="approved",
            occurred_at=payload["occurred_at"],
            evidence_reference="wrong API",
            notes=None,
            actor="test",
            expected_row_version=saved["row_version"],
        )


def test_drive_link_and_correction(db):
    case = create(db)
    with pytest.raises(ValueError, match="HTTPS Google Drive"):
        event(
            db,
            case,
            kind="document_link",
            outcome=None,
            evidence_reference="javascript:alert(1)",
        )
    case = event(db, case)
    corrected = event(
        db,
        case,
        kind="correction",
        outcome="submitted",
        supersedes_event_id=case["events"][-1]["event_id"],
        notes="Corrected by source",
    )
    assert len(corrected["events"]) == 3


def smtp_env():
    return {
        "AUTONOMO_SMTP_HOST": "smtp.example.invalid",
        "AUTONOMO_SMTP_FROM": "billing@example.com",
        "AUTONOMO_SMTP_TO": "synthetic@example.invalid",
        "AUTONOMO_CASE_BASE_URL": "https://app.example.invalid",
    }


NOW = datetime(2032, 4, 1, 8, tzinfo=timezone.utc)  # 10:00 Madrid


def test_reminder_due_thresholds_retry_dedup_and_cancel(db):
    case = action(db, create(db))
    deliveries = []
    sender = lambda config, message: deliveries.append(message)
    assert run(db, now=NOW, env=smtp_env(), sender=sender)["status"] == "sent"
    assert (
        run(db, now=NOW + timedelta(hours=1), env=smtp_env(), sender=sender)["status"]
        == "ok"
    )
    assert len(deliveries) == 1 and not list(deliveries[0].iter_attachments())
    assert "/government-cases?case=" in deliveries[0].get_content()
    row = case["actions"][0]
    cases.save_action(
        db,
        case["case_id"],
        {
            "kind": row["kind"],
            "description": row["description"],
            "due_on": row["due_on"],
            "deadline_source": row["deadline_source"],
            "confirmed": True,
            "status": "completed",
            "expected_row_version": case["row_version"],
            "action_version": row["row_version"],
        },
        actor="test",
        action_id=row["action_id"],
    )
    assert (
        run(db, now=NOW + timedelta(days=7), env=smtp_env(), sender=sender)["due_count"]
        == 0
    )


def test_smtp_failure_and_uncertain_delivery_are_distinct(db):
    action(db, create(db), due="2032-04-01")

    def refused(*args):
        raise ConnectionError("Rejected before DATA")

    assert run(db, now=NOW, env=smtp_env(), sender=refused)["status"] == "failed"

    def uncertain(*args):
        raise TimeoutError("Lost DATA response")

    first = run(db, now=NOW + timedelta(hours=1), env=smtp_env(), sender=uncertain)
    assert first["status"] == "uncertain"
    assert (
        run(db, now=NOW + timedelta(days=1), env=smtp_env(), sender=uncertain)[
            "due_count"
        ]
        == 0
    )
    resolve_message(
        db,
        {
            "message_id": first["message_id"],
            "outcome": "failed",
            "evidence_reference": "Operator verified rejection",
        },
        actor="test",
    )
    calls = []
    assert (
        run(
            db,
            now=NOW + timedelta(days=1),
            env=smtp_env(),
            sender=lambda *args: calls.append(1),
        )["status"]
        == "sent"
    )
    assert calls == [1]


def test_madrid_schedule_unconfirmed_and_catchup(db):
    first = action(db, create(db), due="2032-04-08")
    action(db, create(db, reference="SYN-002"), due=None, confirmed=False)
    calls = []
    sender = lambda *args: calls.append(1)
    assert (
        run(db, now=NOW.replace(hour=6), env=smtp_env(), sender=sender)["due_count"]
        == 0
    )
    assert run(db, now=NOW, env=smtp_env(), sender=sender)["due_count"] == 1
    assert (
        run(db, now=NOW + timedelta(days=9), env=smtp_env(), sender=sender)["due_count"]
        == 1
    )
    assert len(calls) == 2
    assert (
        run(db, now=NOW + timedelta(days=9, hours=1), env=smtp_env(), sender=sender)[
            "due_count"
        ]
        == 0
    )
    assert run(db, now=NOW, env=smtp_env(), dry_run=True)["configured"]


def test_overlapping_and_interrupted_worker(db):
    action(db, create(db), due="2032-04-01")
    db.connection.execute(
        "INSERT INTO government_reminder_runs VALUES ('test',?,NULL,'running',NULL)",
        (NOW.isoformat(),),
    )
    db.connection.commit()
    assert (
        run(db, now=NOW, env=smtp_env(), sender=lambda *args: None)["status"] == "busy"
    )
    assert (
        run(
            db, now=NOW + timedelta(hours=1), env=smtp_env(), sender=lambda *args: None
        )["status"]
        == "sent"
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("AUTONOMO_SMTP_FROM", "billing@example.com\nBcc:x"),
        ("AUTONOMO_CASE_BASE_URL", "http://app.example.invalid"),
        ("AUTONOMO_SMTP_TLS", "none"),
    ],
)
def test_mail_configuration_rejects_unsafe_values(key, value):
    with pytest.raises(ValueError):
        settings({**smtp_env(), key: value})


def test_government_cli_shared_services(db, tmp_path, capsys):
    import json

    data = tmp_path / "case.json"
    data.write_text(
        json.dumps(
            {
                "authority_code": "TGSS",
                "title": "Synthetic CLI",
                "primary_reference": "SYN-CLI",
                "opened_at": "2032-01-01T10:00:00+01:00",
                "evidence_reference": "Synthetic source",
            }
        )
    )
    assert (
        main(
            [
                "government-cases",
                "create",
                "--db",
                str(db.path),
                "--input",
                str(data),
                "--actor",
                "test",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["government-cases", "list", "--db", str(db.path)]) == 0
    assert "SYN-CLI" in capsys.readouterr().out


def test_tls_transport_uses_verification_and_no_original_attachments(monkeypatch):
    from email.message import EmailMessage
    from autonomo_taxes.case_reminders import _smtp_send

    operations = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            operations.append("connect")

        def ehlo(self):
            operations.append("ehlo")

        def starttls(self, *, context):
            import ssl

            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
            operations.append("tls")

        def send_message(self, message):
            operations.append("send")

        def close(self):
            operations.append("close")

    monkeypatch.setattr("smtplib.SMTP", SMTP)
    message = EmailMessage()
    message.set_content("Synthetic deadline")
    _smtp_send(settings(smtp_env()), message)
    assert operations == ["connect", "ehlo", "tls", "ehlo", "send", "close"]


@pytest.mark.web
def test_http_auth_csrf_and_case_conflict(tmp_path):
    import json, threading
    from http.client import HTTPConnection
    from autonomo_test_support.local_web import _config
    from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingServer

    config = _config(tmp_path)
    with LedgerDB.initialize(config.database):
        pass
    app = LocalAccountingApp(config, session_token="test-token")
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    headers = {
        "Cookie": "autonomo_session=test-token",
        "Content-Type": "application/json",
        "Origin": base,
    }

    def request(method, path, payload=None, override=None):
        connection.request(
            method,
            path,
            body=json.dumps(payload) if payload is not None else None,
            headers=headers if override is None else override,
        )
        response = connection.getresponse()
        body = response.read()
        return response.status, json.loads(body)

    try:
        assert request("GET", "/api/government-cases", override={})[0] == 400
        data = {
            "authority_code": "TGSS",
            "title": "Synthetic HTTP case",
            "primary_reference": "SYN-HTTP",
            "opened_at": "2032-01-01T10:00:00+01:00",
            "evidence_reference": "Synthetic receipt",
        }
        assert (
            request(
                "POST",
                "/api/government-cases",
                data,
                {**headers, "Origin": "https://foreign.invalid"},
            )[0]
            == 400
        )
        status, case = request("POST", "/api/government-cases", data)
        assert status == 200
        status, new = request(
            "POST",
            f"/api/government-cases/{case['case_id']}/actions",
            {
                "kind": "respond",
                "description": "Synthetic response",
                "due_on": None,
                "confirmed": False,
                "deadline_source": "",
                "expected_row_version": 1,
            },
        )
        assert status == 200
        assert (
            request(
                "POST",
                f"/api/government-cases/{case['case_id']}/state",
                {
                    "work_state": "waiting_authority",
                    "evidence_reference": "Synthetic source",
                    "expected_row_version": 1,
                },
            )[0]
            == 409
        )
        assert request("GET", "/api/aeat-documents")[1]["rows"] == []
        assert (
            request("GET", f"/api/government-cases/{case['case_id']}")[1]["actions"][0][
                "confirmed"
            ]
            == 0
        )
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_opening_a_case_does_not_claim_a_decision_or_a_submission(db):
    case = create(db)
    assert case["decision_outcome"] is None
    assert case["events"][0]["kind"] == "case_opened"
    assert cases.list_cases(db)["rows"][0]["decision_outcome"] is None


def test_overdue_daily_digest_waits_until_nine_after_prior_delivery(db):
    action(db, create(db), due="2032-04-01")
    calls = []
    sender = lambda *args: calls.append(1)
    assert run(db, now=NOW, env=smtp_env(), sender=sender)["status"] == "sent"
    assert (
        run(db, now=NOW + timedelta(days=1, hours=-6), env=smtp_env(), sender=sender)[
            "due_count"
        ]
        == 0
    )
    assert (
        run(db, now=NOW + timedelta(days=1), env=smtp_env(), sender=sender)["status"]
        == "sent"
    )
    assert len(calls) == 2


@pytest.mark.parametrize(
    "instant,due",
    [
        (datetime(2032, 1, 1, 8, tzinfo=timezone.utc), "2032-01-08"),
        (datetime(2032, 7, 1, 7, tzinfo=timezone.utc), "2032-07-08"),
    ],
)
def test_daily_clock_is_nine_in_madrid_in_winter_and_summer(db, instant, due):
    action(db, create(db), due=due)
    calls = []
    assert (
        run(
            db,
            now=instant - timedelta(hours=1),
            env=smtp_env(),
            sender=lambda *args: calls.append(1),
        )["due_count"]
        == 0
    )
    assert (
        run(db, now=instant, env=smtp_env(), sender=lambda *args: calls.append(1))[
            "status"
        ]
        == "sent"
    )
    assert calls == [1]
