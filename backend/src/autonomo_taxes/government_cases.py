"""Evidence-backed government cases. These operations never post accounting entries."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from . import government_correspondence as correspondence

from .ledger_db import (
    AEAT_CASE_STATUSES,
    AEAT_DOCUMENT_KINDS,
    LedgerDB,
    StaleRowVersionError,
)

WORK_STATES = {"action_required", "waiting_authority", "closed"}
ACTION_KINDS = {"receive", "respond", "pay", "decide_appeal", "check_response"}
EVENT_KINDS = {
    "submission",
    "notification",
    "response",
    "decision",
    "payment",
    "correction",
    "note",
    "document_link",
}


def migrate(connection: sqlite3.Connection) -> None:
    # Keep legacy identifiers and FKs while lifting only the authority restriction.
    sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='aeat_cases'"
    ).fetchone()[0]
    connection.execute(
        sql.replace(
            "CREATE TABLE aeat_cases", "CREATE TABLE aeat_cases_new", 1
        ).replace("authority_code = 'AEAT'", "authority_code IN ('AEAT', 'TGSS')")
    )
    connection.execute("INSERT INTO aeat_cases_new SELECT * FROM aeat_cases")
    connection.execute("DROP TABLE aeat_cases")
    connection.execute("ALTER TABLE aeat_cases_new RENAME TO aeat_cases")
    connection.execute(
        "ALTER TABLE aeat_cases ADD COLUMN work_state TEXT NOT NULL DEFAULT 'action_required' CHECK(work_state IN ('action_required','waiting_authority','closed'))"
    )
    connection.execute(
        "ALTER TABLE aeat_cases ADD COLUMN subject TEXT NOT NULL DEFAULT ''"
    )
    connection.execute(
        "CREATE INDEX aeat_cases_date_idx ON aeat_cases(opened_at DESC, aeat_case_id)"
    )
    connection.execute(
        "CREATE INDEX aeat_cases_filter_idx ON aeat_cases(form_code, procedure_kind, current_status)"
    )
    statements = [
        """CREATE TABLE government_case_events (
            event_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES aeat_cases(aeat_case_id),
            kind TEXT NOT NULL, occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
            evidence_reference TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}', supersedes_event_id TEXT)""",
        """CREATE TABLE government_document_context (
            aeat_document_id TEXT PRIMARY KEY REFERENCES aeat_documents(aeat_document_id),
            placed_at TEXT, received_at TEXT, access_expires_at TEXT)""",
        """CREATE TABLE government_actions (
            action_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES aeat_cases(aeat_case_id),
            kind TEXT NOT NULL CHECK(kind IN ('receive','respond','pay','decide_appeal','check_response')),
            description TEXT NOT NULL, due_on TEXT, deadline_source TEXT NOT NULL DEFAULT '',
            confirmed INTEGER NOT NULL CHECK(confirmed IN (0,1)),
            status TEXT NOT NULL CHECK(status IN ('open','completed','cancelled')),
            row_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            CHECK(confirmed=0 OR (due_on IS NOT NULL AND length(trim(deadline_source))>0)))""",
        """CREATE INDEX government_actions_due_idx ON government_actions(status, due_on)""",
        """CREATE TABLE government_reminder_runs (
            run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
            error TEXT)""",
        """CREATE TABLE government_reminder_messages (
            message_id TEXT PRIMARY KEY, dedup_key TEXT NOT NULL UNIQUE, recipient_hash TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, status TEXT NOT NULL,
            subject TEXT NOT NULL, body TEXT NOT NULL, action_versions_json TEXT NOT NULL,
            error TEXT, sent_at TEXT)""",
    ]
    for statement in statements:
        connection.execute(statement)
    for table in ("government_case_events", "government_document_context"):
        for action in ("UPDATE", "DELETE"):
            connection.execute(
                f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Government evidence is immutable'); END"
            )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any, name: str, *, required: bool = False, limit: int = 4000) -> str:
    if (
        not isinstance(value, str)
        or len(value.strip()) > limit
        or (required and not value.strip())
    ):
        raise ValueError(f"Invalid {name}")
    return value.strip()


def _timestamp(value: Any, name: str, *, optional: bool = False) -> str | None:
    if optional and (value is None or value == ""):
        return None
    value = _text(value, name, required=True, limit=50)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{name} requires a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _fields(payload: Mapping[str, Any], allowed: set[str]) -> None:
    if not isinstance(payload, Mapping) or set(payload) - allowed:
        raise ValueError("Unknown or invalid case fields")


def _case(db: LedgerDB, case_id: str) -> dict[str, Any]:
    row = db.connection.execute(
        "SELECT * FROM aeat_cases WHERE aeat_case_id=?", (case_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Unknown government case")
    return dict(row)


def _version(db: LedgerDB, case_id: str, expected: Any) -> dict[str, Any]:
    row = _case(db, case_id)
    if type(expected) is not int or row["row_version"] != expected:
        raise StaleRowVersionError("Government case changed; reload it before saving")
    return row


def _event(
    db: LedgerDB,
    case_id: str,
    kind: str,
    occurred: str,
    evidence: str,
    notes: str,
    actor: str,
    payload: Mapping[str, Any] | None = None,
    supersedes: str | None = None,
) -> str:
    event_id = str(uuid4())
    db.connection.execute(
        "INSERT INTO government_case_events VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            event_id,
            case_id,
            kind,
            occurred,
            _now(),
            evidence,
            notes,
            _text(actor, "actor", required=True, limit=200),
            json.dumps(payload or {}, ensure_ascii=False),
            supersedes,
        ),
    )
    return event_id


def _bump(db: LedgerDB, case_id: str, expected: int) -> None:
    count = db.connection.execute(
        "UPDATE aeat_cases SET row_version=row_version+1,updated_at=? WHERE aeat_case_id=? AND row_version=?",
        (_now(), case_id, expected),
    ).rowcount
    if count != 1:
        raise StaleRowVersionError("Government case changed; reload it before saving")


def create_case(
    db: LedgerDB, payload: Mapping[str, Any], *, actor: str
) -> dict[str, Any]:
    _fields(
        payload,
        {
            "authority_code",
            "title",
            "subject",
            "primary_reference",
            "opened_at",
            "evidence_reference",
            "authority_name",
            "parent_case_id",
        },
    )
    authority = payload.get("authority_code")
    title = _text(payload.get("title"), "title", required=True, limit=200)
    reference = _text(
        payload.get("primary_reference", ""),
        "primary_reference",
        required=False,
        limit=200,
    )
    subject = _text(payload.get("subject", ""), "subject")
    opened = _timestamp(payload.get("opened_at"), "opened_at")
    evidence = _text(
        payload.get("evidence_reference"), "evidence_reference", required=True
    )
    case_id, timestamp = str(uuid4()), _now()
    with db.transaction():
        authority = correspondence.authority(
            db, authority, payload.get("authority_name")
        )
        parent = correspondence.validate_parent(db, None, payload.get("parent_case_id"))
        if (
            reference
            and db.connection.execute(
                "SELECT 1 FROM aeat_cases WHERE authority_code=? AND primary_reference=?",
                (authority, reference),
            ).fetchone()
        ):
            raise ValueError("A case with this authority and reference already exists")
        db.connection.execute(
            """INSERT INTO aeat_cases (aeat_case_id,authority_code,title,procedure_kind,
            opened_at,primary_reference,current_status,row_version,created_at,updated_at,work_state,subject)
            VALUES (?,?,?,'other',?,?,'submitted',1,?,?,'action_required',?)""",
            (
                case_id,
                authority,
                title,
                opened,
                reference,
                timestamp,
                timestamp,
                subject,
            ),
        )
        db.connection.execute(
            "UPDATE aeat_cases SET parent_case_id=? WHERE aeat_case_id=?",
            (parent, case_id),
        )
        _event(
            db,
            case_id,
            "case_opened",
            opened,
            evidence,
            "",
            actor,
            {"parent_case_id": parent},
        )
    return case_detail(db, case_id)


def list_cases(
    db: LedgerDB,
    *,
    authority: str | None = None,
    state: str | None = None,
    query: str = "",
) -> dict[str, Any]:
    if state and state not in WORK_STATES:
        raise ValueError("Invalid work state")
    rows = [
        dict(row)
        for row in db.connection.execute(
            "SELECT *,aeat_case_id AS case_id FROM aeat_cases ORDER BY updated_at DESC,aeat_case_id"
        )
    ]
    for row in rows:
        row["authority_name"] = db.connection.execute(
            "SELECT name FROM government_authorities WHERE authority_code=?",
            (row["authority_code"],),
        ).fetchone()[0]
        history = correspondence.messages(db, row["case_id"])
        row["last_message"] = (
            {
                k: history[-1].get(k)
                for k in ("subject", "occurred_at", "event_id", "date_precision")
            }
            if history
            else None
        )
        row["search_text"] = " ".join(
            [
                str(row.get(k) or "")
                for k in ("title", "subject", "primary_reference", "authority_name")
            ]
            + [
                " ".join(
                    [
                        m["subject"],
                        m["body_text"],
                        m["summary"],
                        m["sender"],
                        *m["recipients"],
                    ]
                )
                for m in history
            ]
        )
    rows = [
        r
        for r in rows
        if (not authority or r["authority_code"] == authority)
        and (not state or r["work_state"] == state)
        and (not query or query.casefold() in r["search_text"].casefold())
    ]
    for row in rows:
        row["decision_outcome"] = (
            row["current_status"]
            if row["current_status"] in {"approved", "rejected"}
            else None
        )
    actions = [
        dict(r)
        for r in db.connection.execute(
            "SELECT a.*,c.title,c.authority_code FROM government_actions a JOIN aeat_cases c ON c.aeat_case_id=a.case_id WHERE a.status='open' AND c.work_state<>'closed' ORDER BY a.due_on IS NULL,a.due_on,a.created_at"
        )
    ]
    for row in rows:
        row["next_action"] = next(
            (a for a in actions if a["case_id"] == row["case_id"]), None
        )
    return {
        "rows": rows,
        "total": len(rows),
        "actions": actions,
        "authorities": correspondence.authorities(db),
    }


def case_detail(db: LedgerDB, case_id: str) -> dict[str, Any]:
    case = _case(db, case_id)
    events = [
        dict(r)
        for r in db.connection.execute(
            "SELECT * FROM government_case_events WHERE case_id=? ORDER BY occurred_at,recorded_at,event_id",
            (case_id,),
        )
    ]
    events += [
        dict(r)
        for r in db.connection.execute(
            "SELECT aeat_case_event_id AS event_id,'legacy_status' AS kind,occurred_at,created_at AS recorded_at,evidence_reference,notes,actor,status FROM aeat_case_events WHERE aeat_case_id=?",
            (case_id,),
        )
    ]
    events.sort(
        key=lambda r: (
            datetime.fromisoformat(r["occurred_at"].replace("Z", "+00:00")).replace(
                tzinfo=timezone.utc
            )
            if datetime.fromisoformat(r["occurred_at"].replace("Z", "+00:00")).tzinfo
            is None
            else datetime.fromisoformat(r["occurred_at"].replace("Z", "+00:00")),
            r["recorded_at"],
        )
    )
    documents = [
        dict(r)
        for r in db.connection.execute(
            """SELECT ad.*,gc.placed_at,gc.received_at,gc.access_expires_at
        FROM aeat_documents ad LEFT JOIN government_document_context gc USING(aeat_document_id)
        WHERE ad.aeat_case_id=? ORDER BY ad.occurred_at,ad.created_at""",
            (case_id,),
        )
    ]
    for document in documents:
        document["content_url"] = f"/api/document/{document['document_id']}/content"
    return {
        **case,
        "case_id": case_id,
        "decision_outcome": case["current_status"]
        if case["current_status"] in {"approved", "rejected"}
        else None,
        "authority_name": db.connection.execute(
            "SELECT name FROM government_authorities WHERE authority_code=?",
            (case["authority_code"],),
        ).fetchone()[0],
        "correspondence": correspondence.messages(db, case_id),
        "related_cases": [
            dict(r)
            for r in db.connection.execute(
                "SELECT aeat_case_id AS case_id,title,authority_code,primary_reference,work_state FROM aeat_cases WHERE parent_case_id=? OR aeat_case_id=? ORDER BY opened_at",
                (case_id, case["parent_case_id"]),
            )
        ],
        "events": events,
        "documents": documents,
        "actions": [
            dict(r)
            for r in db.connection.execute(
                "SELECT * FROM government_actions WHERE case_id=? ORDER BY created_at,action_id",
                (case_id,),
            )
        ],
    }


def update_case(
    db: LedgerDB, case_id: str, payload: Mapping[str, Any], *, actor: str
) -> dict[str, Any]:
    _fields(
        payload,
        {
            "expected_row_version",
            "work_state",
            "evidence_reference",
            "notes",
            "parent_case_id",
            "title",
            "subject",
            "primary_reference",
        },
    )
    state = payload.get("work_state")
    if not isinstance(state, str) or state not in WORK_STATES:
        raise ValueError("Invalid work state")
    evidence = _text(
        payload.get("evidence_reference"), "evidence_reference", required=True
    )
    with db.transaction():
        row = _version(db, case_id, payload.get("expected_row_version"))
        if (
            state == "closed"
            and db.connection.execute(
                "SELECT 1 FROM government_actions WHERE case_id=? AND status='open'",
                (case_id,),
            ).fetchone()
        ):
            raise ValueError("Complete or cancel open actions before closing the case")
        changes = {
            key: _text(
                payload[key],
                key,
                required=key == "title",
                limit=200 if key in {"title", "primary_reference"} else 4000,
            )
            for key in ("title", "subject", "primary_reference")
            if key in payload
        }
        if (
            changes.get("primary_reference")
            and db.connection.execute(
                "SELECT 1 FROM aeat_cases WHERE authority_code=? AND primary_reference=? AND aeat_case_id<>?",
                (row["authority_code"], changes["primary_reference"], case_id),
            ).fetchone()
        ):
            raise ValueError("A case with this authority and reference already exists")
        for key, value in changes.items():
            db.connection.execute(
                f"UPDATE aeat_cases SET {key}=? WHERE aeat_case_id=?", (value, case_id)
            )
        if "parent_case_id" in payload:
            parent = correspondence.validate_parent(
                db, case_id, payload["parent_case_id"]
            )
            db.connection.execute(
                "UPDATE aeat_cases SET parent_case_id=? WHERE aeat_case_id=?",
                (parent, case_id),
            )
        _bump(db, case_id, row["row_version"])
        db.connection.execute(
            "UPDATE aeat_cases SET work_state=? WHERE aeat_case_id=?", (state, case_id)
        )
        _event(
            db,
            case_id,
            "workflow",
            _now(),
            evidence,
            _text(payload.get("notes", ""), "notes"),
            actor,
            {
                "from": row["work_state"],
                "to": state,
                "parent_case_id": payload.get("parent_case_id", row["parent_case_id"]),
                "metadata_before": {k: row[k] for k in changes},
                "metadata_after": changes,
            },
        )
    return case_detail(db, case_id)


def add_event(
    db: LedgerDB, case_id: str, payload: Mapping[str, Any], *, actor: str
) -> dict[str, Any]:
    _fields(
        payload,
        {
            "expected_row_version",
            "kind",
            "occurred_at",
            "evidence_reference",
            "notes",
            "outcome",
            "supersedes_event_id",
        },
    )
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in EVENT_KINDS:
        raise ValueError("Invalid event kind")
    occurred = _timestamp(payload.get("occurred_at"), "occurred_at")
    evidence = _text(
        payload.get("evidence_reference"), "evidence_reference", required=True
    )
    if kind == "document_link":
        url = urlsplit(evidence)
        if (
            url.scheme != "https"
            or url.hostname not in {"drive.google.com", "docs.google.com"}
            or url.username
            or url.password
        ):
            raise ValueError("External documents require an HTTPS Google Drive link")
    outcome = payload.get("outcome")
    if outcome is not None and (
        kind not in {"decision", "correction"}
        or not isinstance(outcome, str)
        or outcome not in AEAT_CASE_STATUSES
    ):
        raise ValueError("Invalid decision outcome")
    supersedes = payload.get("supersedes_event_id")
    if supersedes is not None:
        supersedes = _text(supersedes, "supersedes_event_id", required=True, limit=100)
    with db.transaction():
        row = _version(db, case_id, payload.get("expected_row_version"))
        if (kind == "correction" and not supersedes) or (
            supersedes
            and (
                kind != "correction"
                or not db.connection.execute(
                    "SELECT 1 FROM government_case_events WHERE event_id=? AND case_id=? UNION ALL SELECT 1 FROM aeat_case_events WHERE aeat_case_event_id=? AND aeat_case_id=?",
                    (supersedes, case_id, supersedes, case_id),
                ).fetchone()
            )
        ):
            raise ValueError("Correction must reference an event of the same case")
        _bump(db, case_id, row["row_version"])
        if outcome:
            db.connection.execute(
                "UPDATE aeat_cases SET current_status=? WHERE aeat_case_id=?",
                (outcome, case_id),
            )
        _event(
            db,
            case_id,
            kind,
            occurred,
            evidence,
            _text(payload.get("notes", ""), "notes"),
            actor,
            {"outcome": outcome},
            supersedes,
        )
    return case_detail(db, case_id)


def save_action(
    db: LedgerDB,
    case_id: str,
    payload: Mapping[str, Any],
    *,
    actor: str,
    action_id: str | None = None,
) -> dict[str, Any]:
    _fields(
        payload,
        {
            "expected_row_version",
            "action_version",
            "kind",
            "description",
            "due_on",
            "deadline_source",
            "confirmed",
            "status",
            "correspondence_event_id",
        },
    )
    kind, status = payload.get("kind"), payload.get("status", "open")
    if (
        not isinstance(kind, str)
        or kind not in ACTION_KINDS
        or not isinstance(status, str)
        or status not in {"open", "completed", "cancelled"}
    ):
        raise ValueError("Invalid action kind or status")
    description = _text(
        payload.get("description"), "description", required=True, limit=1000
    )
    due = payload.get("due_on") or None
    if due is not None:
        if not isinstance(due, str) or len(due) != 10:
            raise ValueError("due_on must use YYYY-MM-DD")
        date.fromisoformat(due)
    confirmed = payload.get("confirmed", False)
    if type(confirmed) is not bool:
        raise ValueError("confirmed must be a boolean")
    source = _text(payload.get("deadline_source", ""), "deadline_source")
    if confirmed and (not due or not source):
        raise ValueError("Confirmed deadlines require a date and evidence source")
    with db.transaction():
        case = _version(db, case_id, payload.get("expected_row_version"))
        if case["work_state"] == "closed":
            raise ValueError("Reopen the case before changing actions")
        source_event = payload.get("correspondence_event_id") or None
        if source_event:
            correspondence._message(db, case_id, source_event)
        before = None
        timestamp = _now()
        if action_id:
            before = db.connection.execute(
                "SELECT * FROM government_actions WHERE action_id=? AND case_id=?",
                (action_id, case_id),
            ).fetchone()
            if (
                before is None
                or type(payload.get("action_version")) is not int
                or before["row_version"] != payload["action_version"]
            ):
                raise StaleRowVersionError("Action changed; reload before saving")
            db.connection.execute(
                "UPDATE government_actions SET kind=?,description=?,due_on=?,deadline_source=?,confirmed=?,status=?,row_version=row_version+1,updated_at=? WHERE action_id=?",
                (
                    kind,
                    description,
                    due,
                    source,
                    int(confirmed),
                    status,
                    timestamp,
                    action_id,
                ),
            )
        else:
            action_id = str(uuid4())
            db.connection.execute(
                "INSERT INTO government_actions (action_id,case_id,kind,description,due_on,deadline_source,confirmed,status,row_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                (
                    action_id,
                    case_id,
                    kind,
                    description,
                    due,
                    source,
                    int(confirmed),
                    status,
                    timestamp,
                    timestamp,
                ),
            )
        if "correspondence_event_id" not in payload and before:
            source_event = before["correspondence_event_id"]
        db.connection.execute(
            "UPDATE government_actions SET correspondence_event_id=? WHERE action_id=?",
            (source_event, action_id),
        )
        _bump(db, case_id, case["row_version"])
        _event(
            db,
            case_id,
            "action_updated" if before else "action_created",
            timestamp,
            source or "Operator action",
            description,
            actor,
            {
                "action_id": action_id,
                "before": dict(before) if before else None,
                "after": dict(
                    db.connection.execute(
                        "SELECT * FROM government_actions WHERE action_id=?",
                        (action_id,),
                    ).fetchone()
                ),
            },
        )
    return case_detail(db, case_id)


def attach_pdf(
    db: LedgerDB,
    case_id: str,
    path: Path,
    archive_root: Path,
    payload: Mapping[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    from .aeat_documents import archive_aeat_pdf, inspect_aeat_pdf
    from .storage_service import register_local_source_replica

    _fields(
        payload,
        {
            "expected_row_version",
            "document_kind",
            "occurred_at",
            "notes",
            "placed_at",
            "received_at",
            "access_expires_at",
        },
    )
    kind = payload.get("document_kind")
    if not isinstance(kind, str) or kind not in AEAT_DOCUMENT_KINDS:
        raise ValueError("Invalid document kind")
    occurred = _timestamp(payload.get("occurred_at"), "occurred_at")
    dates = [
        _timestamp(payload.get(k), k, optional=True)
        for k in ("placed_at", "received_at", "access_expires_at")
    ]
    placed, received, expires = [
        datetime.fromisoformat(t) if t else None for t in dates
    ]
    if placed and ((received and received < placed) or (expires and expires < placed)):
        raise ValueError("Notification dates precede placement")
    inspect_aeat_pdf(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    notes = _text(payload.get("notes", ""), "notes")
    with db.transaction():
        case = _case(db, case_id)
        existing = db.connection.execute(
            "SELECT * FROM aeat_documents WHERE source_hash=?", (digest,)
        ).fetchone()
        if existing:
            context = db.connection.execute(
                "SELECT placed_at,received_at,access_expires_at FROM government_document_context WHERE aeat_document_id=?",
                (existing["aeat_document_id"],),
            ).fetchone()
            if (
                existing["aeat_case_id"] != case_id
                or existing["document_kind"] != kind
                or existing["occurred_at"] != occurred
                or existing["notes"] != notes
                or list(context or (None, None, None)) != dates
            ):
                raise ValueError(
                    "PDF already registered with different case or metadata"
                )
            return {**case_detail(db, case_id), "idempotent": True}
        _version(db, case_id, payload.get("expected_row_version"))
        if case["work_state"] == "closed":
            raise ValueError("Reopen the case before adding documents")
        archived = archive_aeat_pdf(
            path, archive_root, occurred_at=occurred, document_kind=kind, digest=digest
        )
        document = db.connection.execute(
            "SELECT * FROM documents WHERE source_hash=?", (digest,)
        ).fetchone()
        if document is not None:
            raise ValueError("PDF is already used outside this case")
        document = db.upsert_document(
            document_type="government_official",
            document_number=None,
            issued_on=occurred[:10],
            currency="EUR",
            lifecycle_status="approved",
            source_hash=digest,
        )
        document = db.set_document_storage(
            document["document_id"],
            source_path=str(archived),
            mime_type="application/pdf",
            expected_row_version=document["row_version"],
        )
        register_local_source_replica(
            db,
            document_id=document["document_id"],
            source_path=archived,
            media_type="application/pdf",
            storage_root=archive_root,
        )
        doc_id = str(uuid4())
        db.connection.execute(
            "INSERT INTO aeat_documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                doc_id,
                case_id,
                document["document_id"],
                kind,
                occurred,
                None,
                None,
                None,
                notes,
                digest,
                _now(),
            ),
        )
        db.connection.execute(
            "INSERT INTO government_document_context VALUES (?,?,?,?)", (doc_id, *dates)
        )
        # Legacy readers retain a sourced status event for each PDF, scoped to AEAT.
        db.connection.execute(
            "INSERT INTO aeat_case_events VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                case_id,
                case["current_status"],
                occurred,
                doc_id,
                doc_id,
                notes,
                actor,
                _now(),
            ),
        )
        _bump(db, case_id, case["row_version"])
        _event(
            db,
            case_id,
            "document",
            occurred,
            doc_id,
            notes,
            actor,
            {
                "sha256": digest,
                "placed_at": dates[0],
                "received_at": dates[1],
                "access_expires_at": dates[2],
            },
        )
    return {**case_detail(db, case_id), "idempotent": False}
