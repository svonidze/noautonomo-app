"""Append-only correspondence and original files within government cases."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import mimetypes
import os
import re
from pathlib import Path
import shutil
import tempfile
import sqlite3
from typing import Any, Mapping

MAX_REQUEST_BYTES = 1024 * 1024
MAX_FILE_BYTES = 30 * 1024 * 1024


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE government_authorities (authority_code TEXT PRIMARY KEY, name TEXT NOT NULL)"
    )
    connection.executemany(
        "INSERT INTO government_authorities VALUES (?,?)",
        [("AEAT", "AEAT"), ("TGSS", "TGSS")],
    )
    sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='aeat_cases'"
    ).fetchone()[0]
    sql = re.sub(
        r'CREATE TABLE "?aeat_cases"?', "CREATE TABLE aeat_cases_new", sql, count=1
    )
    sql = sql.replace(
        "CHECK (authority_code IN ('AEAT', 'TGSS'))",
        "REFERENCES government_authorities(authority_code)",
    )
    connection.execute(sql)
    connection.execute("INSERT INTO aeat_cases_new SELECT * FROM aeat_cases")
    connection.execute("DROP TABLE aeat_cases")
    connection.execute("ALTER TABLE aeat_cases_new RENAME TO aeat_cases")
    connection.execute(
        "ALTER TABLE aeat_cases ADD COLUMN parent_case_id TEXT REFERENCES aeat_cases(aeat_case_id)"
    )
    connection.execute(
        "CREATE INDEX aeat_cases_date_idx ON aeat_cases(opened_at DESC, aeat_case_id)"
    )
    connection.execute(
        "CREATE INDEX aeat_cases_filter_idx ON aeat_cases(form_code, procedure_kind, current_status)"
    )
    connection.execute(
        "CREATE INDEX government_case_parent_idx ON aeat_cases(parent_case_id)"
    )
    connection.execute(
        "ALTER TABLE government_actions ADD COLUMN correspondence_event_id TEXT REFERENCES government_case_events(event_id)"
    )
    connection.execute("""CREATE TABLE government_correspondence_attachments (
        event_id TEXT NOT NULL REFERENCES government_case_events(event_id),
        document_id TEXT NOT NULL REFERENCES documents(document_id),
        filename TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(event_id,filename))""")
    connection.execute("""CREATE UNIQUE INDEX government_message_source_idx ON government_case_events(
        json_extract(payload_json,'$.source_provider'), json_extract(payload_json,'$.source_account'),
        json_extract(payload_json,'$.source_message_id'))
        WHERE kind='correspondence' AND supersedes_event_id IS NULL
        AND json_extract(payload_json,'$.source_message_id')<>''""")
    for action in ("UPDATE", "DELETE"):
        connection.execute(
            f"CREATE TRIGGER government_attachments_no_{action.lower()} BEFORE {action} ON government_correspondence_attachments BEGIN SELECT RAISE(ABORT, 'Correspondence attachments are immutable'); END"
        )
    for action in ("UPDATE", "DELETE"):
        connection.execute(
            f"CREATE TRIGGER government_attachment_documents_no_{action.lower()} BEFORE {'UPDATE OF source_hash,source_path,mime_type' if action == 'UPDATE' else action} ON documents WHEN EXISTS (SELECT 1 FROM government_correspondence_attachments WHERE document_id=OLD.document_id) BEGIN SELECT RAISE(ABORT, 'Correspondence originals are immutable'); END"
        )


def authorities(db) -> list[dict]:
    return [
        dict(r)
        for r in db.connection.execute(
            "SELECT * FROM government_authorities ORDER BY name COLLATE NOCASE"
        )
    ]


def authority(db, code: Any, name: Any = None) -> str:
    from .government_cases import _text

    code = _text(code, "authority_code", required=True, limit=200)
    found = db.connection.execute(
        "SELECT authority_code FROM government_authorities WHERE authority_code=? COLLATE NOCASE OR name=? COLLATE NOCASE",
        (code, code),
    ).fetchone()
    if found:
        return found[0]
    display = _text(name or code, "authority_name", required=True, limit=200)
    db.connection.execute(
        "INSERT INTO government_authorities VALUES (?,?)", (code, display)
    )
    return code


def validate_parent(db, case_id: str | None, parent: Any) -> str | None:
    from .government_cases import _case, _text

    if parent is None or parent == "":
        return None
    parent = _text(parent, "parent_case_id", required=True, limit=100)
    visited = {case_id}
    current = parent
    while current:
        if current in visited:
            raise ValueError("Related cases must not form a cycle")
        visited.add(current)
        current = _case(db, current)["parent_case_id"]
    return parent


def _message(db, case_id: str, event_id: str) -> dict:
    row = db.connection.execute(
        "SELECT * FROM government_case_events WHERE event_id=? AND case_id=? AND kind='correspondence'",
        (event_id, case_id),
    ).fetchone()
    if row is None:
        raise ValueError("Correspondence must belong to the same case")
    return dict(row)


def add(db, case_id: str, payload: Mapping[str, Any], *, actor: str) -> dict:
    from . import government_cases as cases

    cases._fields(
        payload,
        {
            "expected_row_version",
            "direction",
            "date_precision",
            "channel",
            "occurred_at",
            "sender",
            "recipients",
            "subject",
            "body_text",
            "summary",
            "source_url",
            "source_provider",
            "source_account",
            "source_message_id",
            "reply_to_event_id",
            "supersedes_event_id",
            "attachment_names",
        },
    )
    if (
        len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        > MAX_REQUEST_BYTES
    ):
        raise ValueError("Correspondence request exceeds 1 MiB")
    direction, channel = payload.get("direction"), payload.get("channel")
    if (
        not isinstance(direction, str)
        or direction not in {"incoming", "outgoing"}
        or not isinstance(channel, str)
        or channel not in {"email", "portal", "paper"}
    ):
        raise ValueError("Invalid correspondence direction or channel")
    occurred = cases._timestamp(payload.get("occurred_at"), "occurred_at")
    data = {
        k: cases._text(
            payload.get(k, ""), k, limit=MAX_REQUEST_BYTES if k == "body_text" else 4000
        )
        for k in (
            "sender",
            "subject",
            "body_text",
            "summary",
            "source_url",
            "source_provider",
            "source_account",
            "source_message_id",
        )
    }
    data["body_text"] = payload.get("body_text", "")
    if not data["subject"] or not (
        data["body_text"] or data["source_url"] or payload.get("attachment_names")
    ):
        raise ValueError(
            "Correspondence requires a subject and text, originals or an evidence source"
        )
    if data["source_url"]:
        from urllib.parse import urlsplit

        u = urlsplit(data["source_url"])
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            raise ValueError(
                "Correspondence source requires an HTTPS URL without credentials"
            )
    if data["source_message_id"] and not (
        data["source_provider"] and data["source_account"]
    ):
        raise ValueError("Source identifiers require a provider and account")
    for field in ("recipients", "attachment_names"):
        value = payload.get(field, [])
        if not isinstance(value, list) or len(value) > 200:
            raise ValueError(f"Invalid {field}")
        data[field] = [cases._text(v, field, required=True, limit=500) for v in value]
        if len(set(data[field])) != len(data[field]):
            raise ValueError(f"Duplicate {field}")
    precision = payload.get("date_precision", "instant")
    if not isinstance(precision, str) or precision not in {"day", "instant"}:
        raise ValueError("Invalid correspondence date precision")
    if precision == "day":
        occurred = (
            datetime.fromisoformat(payload["occurred_at"].replace("Z", "+00:00"))
            .date()
            .isoformat()
            + "T12:00:00+00:00"
        )
    data.update(
        direction=payload["direction"],
        channel=payload["channel"],
        date_precision=precision,
    )
    for field in ("reply_to_event_id", "supersedes_event_id"):
        data[field] = cases._text(payload.get(field, "") or "", field, limit=100)
    with db.transaction():
        case = cases._case(db, case_id)
        for key in ("reply_to_event_id", "supersedes_event_id"):
            if data[key]:
                _message(db, case_id, data[key])
        if data["source_message_id"] and not data["supersedes_event_id"]:
            previous = db.connection.execute(
                "SELECT * FROM government_case_events WHERE kind='correspondence' AND supersedes_event_id IS NULL AND json_extract(payload_json,'$.source_provider')=? AND json_extract(payload_json,'$.source_account')=? AND json_extract(payload_json,'$.source_message_id')=?",
                (
                    data["source_provider"],
                    data["source_account"],
                    data["source_message_id"],
                ),
            ).fetchone()
            if previous:
                if (
                    previous["case_id"] != case_id
                    or previous["occurred_at"] != occurred
                    or json.loads(previous["payload_json"]) != data
                ):
                    raise ValueError(
                        "Source message already registered with different contents or case; record a correction"
                    )
                return {
                    **cases.case_detail(db, case_id),
                    "correspondence_event_id": previous["event_id"],
                    "idempotent": True,
                }
        if data["supersedes_event_id"]:
            previous = db.connection.execute(
                "SELECT * FROM government_case_events WHERE supersedes_event_id=?",
                (data["supersedes_event_id"],),
            ).fetchone()
            if previous:
                if (
                    previous["kind"] == "correspondence"
                    and previous["occurred_at"] == occurred
                    and json.loads(previous["payload_json"]) == data
                ):
                    return {
                        **cases.case_detail(db, case_id),
                        "correspondence_event_id": previous["event_id"],
                        "idempotent": True,
                    }
                raise ValueError(
                    "This message was already corrected; correct its latest revision"
                )
        cases._version(db, case_id, payload.get("expected_row_version"))
        event_id = cases._event(
            db,
            case_id,
            "correspondence",
            occurred,
            data["source_url"] or "Operator correspondence record",
            data["summary"],
            actor,
            data,
            data["supersedes_event_id"] or None,
        )
        if data["supersedes_event_id"]:
            db.connection.execute(
                "INSERT INTO government_correspondence_attachments SELECT ?,document_id,filename,? FROM government_correspondence_attachments WHERE event_id=?",
                (event_id, cases._now(), data["supersedes_event_id"]),
            )
        cases._bump(db, case_id, case["row_version"])
    return {
        **cases.case_detail(db, case_id),
        "correspondence_event_id": event_id,
        "idempotent": False,
    }


def messages(db, case_id: str) -> list[dict]:
    rows = [
        dict(r)
        for r in db.connection.execute(
            "SELECT * FROM government_case_events WHERE case_id=? AND kind='correspondence' ORDER BY occurred_at,recorded_at,event_id",
            (case_id,),
        )
    ]
    superseded = {r["supersedes_event_id"] for r in rows}
    for row in rows:
        row.update(json.loads(row.pop("payload_json")))
        row["superseded"] = row["event_id"] in superseded
        row["attachments"] = [
            dict(r)
            for r in db.connection.execute(
                "SELECT a.*,d.source_hash,d.mime_type FROM government_correspondence_attachments a JOIN documents d USING(document_id) WHERE event_id=? ORDER BY filename",
                (row["event_id"],),
            )
        ]
        for attachment in row["attachments"]:
            attachment["content_url"] = (
                f"/api/document/{attachment['document_id']}/content"
            )
        row["missing_attachments"] = sorted(
            set(row["attachment_names"]) - {a["filename"] for a in row["attachments"]}
        )
    return rows


def attach(
    db,
    case_id: str,
    path: Path,
    archive_root: Path,
    payload: Mapping[str, Any],
    *,
    actor: str,
) -> dict:
    from . import government_cases as cases
    from .storage_service import (
        register_local_source_replica,
        resolve_verified_filesystem_replica,
    )

    cases._fields(
        payload, {"expected_row_version", "correspondence_event_id", "filename"}
    )
    event_id = cases._text(
        payload.get("correspondence_event_id"),
        "correspondence_event_id",
        required=True,
        limit=100,
    )
    filename = cases._text(
        payload.get("filename", path.name), "filename", required=True, limit=500
    )
    if any(c in filename for c in ("/", "\\", "\r", "\n", "\x00")) or filename in {
        ".",
        "..",
    }:
        raise ValueError("Invalid attachment filename")
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_FILE_BYTES:
        raise ValueError("Attachment must be a nonempty file no larger than 30 MB")
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    media = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with db.transaction():
        event = _message(db, case_id, event_id)
        previous = db.connection.execute(
            "SELECT d.source_hash FROM government_correspondence_attachments a JOIN documents d USING(document_id) WHERE event_id=? AND filename=?",
            (event_id, filename),
        ).fetchone()
        if previous:
            if previous[0] != digest:
                raise ValueError(
                    "Attachment name is already registered with different bytes"
                )
            return {**cases.case_detail(db, case_id), "idempotent": True}
        case = cases._version(db, case_id, payload.get("expected_row_version"))
        document = db.connection.execute(
            "SELECT * FROM documents WHERE source_hash=?", (digest,)
        ).fetchone()
        replica = (
            resolve_verified_filesystem_replica(
                db.connection, document_id=document["document_id"]
            )
            if document
            else None
        )
        if replica is None:
            target = archive_root / "correspondence" / digest[:2] / digest
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = None
            try:
                with (
                    tempfile.NamedTemporaryFile(
                        dir=target.parent, delete=False
                    ) as dest,
                    path.open("rb") as source,
                ):
                    temporary = Path(dest.name)
                    shutil.copyfileobj(source, dest)
                    dest.flush()
                    os.fsync(dest.fileno())
                with temporary.open("rb") as stored:
                    if hashlib.file_digest(stored, "sha256").hexdigest() != digest:
                        raise ValueError("Source changed while archiving attachment")
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    pass
                with target.open("rb") as stored:
                    if hashlib.file_digest(stored, "sha256").hexdigest() != digest:
                        raise ValueError("Archived attachment hash mismatch")
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            if document is None:
                document = db.upsert_document(
                    document_type="government_correspondence",
                    document_number=None,
                    issued_on=event["occurred_at"][:10],
                    currency="EUR",
                    lifecycle_status="approved",
                    source_hash=digest,
                )
                document = db.set_document_storage(
                    document["document_id"],
                    source_path=str(target),
                    mime_type=media,
                    expected_row_version=document["row_version"],
                )
            register_local_source_replica(
                db,
                document_id=document["document_id"],
                source_path=target,
                media_type=document["mime_type"] or media,
                storage_root=archive_root,
            )
        db.connection.execute(
            "INSERT INTO government_correspondence_attachments VALUES (?,?,?,?)",
            (event_id, document["document_id"], filename, cases._now()),
        )
        cases._event(
            db,
            case_id,
            "correspondence_attachment",
            cases._now(),
            digest,
            filename,
            actor,
            {
                "correspondence_event_id": event_id,
                "document_id": document["document_id"],
            },
        )
        cases._bump(db, case_id, case["row_version"])
    return {**cases.case_detail(db, case_id), "idempotent": False}
