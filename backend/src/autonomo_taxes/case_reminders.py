"""Hourly server job, durable SMTP outcomes, and one current daily summary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr
import hashlib
import json
import os
import smtplib
import ssl
from typing import Callable, Mapping
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from .government_cases import list_cases
from .ledger_db import LedgerDB


def settings(env: Mapping[str, str] | None = None) -> dict:
    env = os.environ if env is None else env
    values = {
        key: env.get("AUTONOMO_" + key, "").strip()
        for key in (
            "SMTP_HOST",
            "SMTP_PORT",
            "SMTP_USER",
            "SMTP_PASSWORD",
            "SMTP_FROM",
            "SMTP_TO",
            "CASE_BASE_URL",
            "SMTP_TLS",
        )
    }
    values["SMTP_PASSWORD"] = env.get("AUTONOMO_SMTP_PASSWORD", "")
    for key in ("SMTP_FROM", "SMTP_TO"):
        value = values[key]
        if value and (
            "\n" in value
            or "\r" in value
            or parseaddr(value)[1] != value
            or "@" not in value
        ):
            raise ValueError("Invalid SMTP address")
    url = urlsplit(values["CASE_BASE_URL"])
    if values["CASE_BASE_URL"] and (
        url.scheme != "https"
        or not url.netloc
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("CASE_BASE_URL must be a public HTTPS application URL")
    language = env.get("AUTONOMO_CASE_MAIL_LOCALE", "ru")
    if language not in {"ru", "en", "es"}:
        raise ValueError("CASE_MAIL_LOCALE must be ru, en or es")
    mode = values["SMTP_TLS"] or "starttls"
    if mode not in {"starttls", "implicit"}:
        raise ValueError("SMTP_TLS must be starttls or implicit")
    port = int(values["SMTP_PORT"] or (465 if mode == "implicit" else 587))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid SMTP port")
    if values["SMTP_USER"] and not values["SMTP_PASSWORD"]:
        raise ValueError("SMTP_PASSWORD is required with SMTP_USER")
    return {
        **values,
        "locale": language,
        "port": port,
        "mode": mode,
        "ready": all(
            values[k] for k in ("SMTP_HOST", "SMTP_FROM", "SMTP_TO", "CASE_BASE_URL")
        ),
    }


def health(db: LedgerDB) -> dict:
    runs = [
        dict(r)
        for r in db.connection.execute(
            "SELECT * FROM government_reminder_runs ORDER BY started_at DESC,rowid DESC LIMIT 1"
        )
    ]
    last = db.connection.execute(
        "SELECT MAX(sent_at) FROM government_reminder_messages WHERE status='sent'"
    ).fetchone()[0]
    try:
        configured = settings()["ready"]
        config_error = None
    except (ValueError, OverflowError):
        configured, config_error = False, "SMTP configuration is invalid"
    stale = not runs or datetime.now(timezone.utc) - datetime.fromisoformat(
        runs[0]["started_at"]
    ) > timedelta(hours=2)
    return {
        "configured": configured,
        "config_error": config_error,
        "last_run": runs[0] if runs else None,
        "last_sent_at": last,
        "worker_stale": stale,
        "messages": [
            dict(r)
            for r in db.connection.execute(
                "SELECT message_id,created_at,status,error,sent_at FROM government_reminder_messages ORDER BY created_at DESC,rowid DESC LIMIT 20"
            )
        ],
    }


def _smtp_send(config: dict, message: EmailMessage) -> None:
    """Failure before DATA is retryable; after DATA begins delivery can be unknown."""
    smtp = None
    try:
        if config["mode"] == "implicit":
            smtp = smtplib.SMTP_SSL(
                config["SMTP_HOST"],
                config["port"],
                timeout=30,
                context=ssl.create_default_context(),
            )
        else:
            smtp = smtplib.SMTP(config["SMTP_HOST"], config["port"], timeout=30)
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if config["SMTP_USER"]:
            smtp.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
    except Exception:
        if smtp is not None:
            smtp.close()
        raise ConnectionError("SMTP connection/authentication failed") from None
    try:
        smtp.send_message(message)
    finally:
        # QUIT failure after a positive DATA response must not re-send the message.
        smtp.close()


def run(
    db: LedgerDB,
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
    sender: Callable[[dict, EmailMessage], None] | None = None,
    dry_run: bool = False,
) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Reminder clock requires a timezone")
    now = now.astimezone(timezone.utc)
    stamp, local = (
        now.isoformat(timespec="seconds"),
        now.astimezone(ZoneInfo("Europe/Madrid")),
    )
    config = settings(env)
    recipient_hash = hashlib.sha256(config["SMTP_TO"].encode()).hexdigest()
    actions = list_cases(db)["actions"]
    sent_versions: set[tuple[str, int]] = set()
    for row in db.connection.execute(
        "SELECT action_versions_json FROM government_reminder_messages WHERE status IN ('sent','sending','uncertain') AND recipient_hash=? AND (status IN ('sending','uncertain') OR substr(dedup_key,1,10)=?)",
        (recipient_hash, local.date().isoformat()),
    ):
        sent_versions.update(tuple(item) for item in json.loads(row[0]))
    notified_versions = set()
    for row in db.connection.execute(
        "SELECT action_versions_json FROM government_reminder_messages WHERE status='sent' AND recipient_hash=?",
        (recipient_hash,),
    ):
        notified_versions.update(tuple(item) for item in json.loads(row[0]))
    due = []
    for action in actions:
        if not action["confirmed"] or not action["due_on"]:
            continue
        delta = (datetime.fromisoformat(action["due_on"]).date() - local.date()).days
        scheduled = local.hour >= 9 and (delta in {7, 3, 1, 0} or delta < 0)
        urgent = (
            delta <= 1
            and (action["action_id"], action["row_version"]) not in notified_versions
        )
        if scheduled or urgent:
            if (action["action_id"], action["row_version"]) not in sent_versions:
                due.append(action)
    if dry_run:
        return {
            "configured": config["ready"],
            "due": due,
            "will_send": bool(due) and config["ready"],
        }
    run_id = str(uuid4())
    with db.transaction():
        active = db.connection.execute(
            "SELECT * FROM government_reminder_runs WHERE status='running'"
        ).fetchone()
        if active and now - datetime.fromisoformat(active["started_at"]) < timedelta(
            minutes=15
        ):
            return {"status": "busy"}
        if active:
            db.connection.execute(
                "UPDATE government_reminder_runs SET status='interrupted',finished_at=? WHERE status='running'",
                (stamp,),
            )
            db.connection.execute(
                "UPDATE government_reminder_messages SET status='uncertain',updated_at=?,error='Worker interrupted during SMTP delivery; inspect before retrying' WHERE status='sending'",
                (stamp,),
            )
        db.connection.execute(
            "INSERT INTO government_reminder_runs VALUES (?,?,NULL,?,NULL)",
            (run_id, stamp, "running"),
        )
    status, error, message_id = "ok", None, None
    try:
        if not config["ready"]:
            status, error = "unconfigured", "SMTP is not configured; no email was sent"
        elif due:
            versions = sorted([[a["action_id"], a["row_version"]] for a in due])
            key = (
                local.date().isoformat()
                + ":"
                + hashlib.sha256(
                    json.dumps([recipient_hash, versions]).encode()
                ).hexdigest()
            )
            subject = (
                {
                    "ru": "NoAutonomo: сроки дел",
                    "en": "NoAutonomo: case deadlines",
                    "es": "NoAutonomo: plazos de expedientes",
                }[config["locale"]]
                + " — "
                + local.date().isoformat()
            )
            deadline_label = {"ru": "Срок", "en": "Deadline", "es": "Fecha límite"}[
                config["locale"]
            ]
            body = "\n\n".join(
                f"{a['authority_code']} · {a['title']}\n{a['description']}\n{deadline_label}: {a['due_on']}\n{config['CASE_BASE_URL'].rstrip('/')}/government-cases?case={a['case_id']}"
                for a in due
            )
            with db.transaction():
                existing = db.connection.execute(
                    "SELECT * FROM government_reminder_messages WHERE dedup_key=?",
                    (key,),
                ).fetchone()
                if existing and existing["status"] in {
                    "sent",
                    "sending",
                    "uncertain",
                    "cancelled",
                }:
                    status = "already_processed"
                else:
                    message_id = existing["message_id"] if existing else str(uuid4())
                    if existing:
                        db.connection.execute(
                            "UPDATE government_reminder_messages SET status='pending',updated_at=?,error=NULL WHERE message_id=?",
                            (stamp, message_id),
                        )
                    else:
                        db.connection.execute(
                            "INSERT INTO government_reminder_messages VALUES (?,?,?,?,?,?,?,?,?,?,NULL)",
                            (
                                message_id,
                                key,
                                recipient_hash,
                                stamp,
                                stamp,
                                "pending",
                                subject,
                                body,
                                json.dumps(versions),
                                None,
                            ),
                        )
                    # Recheck versions immediately before claiming delivery. Editing an action
                    # invalidates its queued email instead of sending a stale deadline.
                    valid = all(
                        db.connection.execute(
                            "SELECT 1 FROM government_actions a JOIN aeat_cases c ON c.aeat_case_id=a.case_id WHERE a.action_id=? AND a.row_version=? AND a.status='open' AND a.confirmed=1 AND c.work_state<>'closed'",
                            (a, v),
                        ).fetchone()
                        for a, v in versions
                    )
                    if valid:
                        db.connection.execute(
                            "UPDATE government_reminder_messages SET status='sending',updated_at=? WHERE message_id=?",
                            (stamp, message_id),
                        )
                    else:
                        db.connection.execute(
                            "UPDATE government_reminder_messages SET status='cancelled',updated_at=? WHERE message_id=?",
                            (stamp, message_id),
                        )
                        status = "changed"
            if status == "ok" and message_id:
                message = EmailMessage()
                message["From"], message["To"], message["Subject"] = (
                    config["SMTP_FROM"],
                    config["SMTP_TO"],
                    subject,
                )
                message["Message-ID"] = (
                    f"<{message_id}@{urlsplit(config['CASE_BASE_URL']).hostname}>"
                )
                message.set_content(body)
                try:
                    (sender or _smtp_send)(config, message)
                except (
                    ConnectionError,
                    smtplib.SMTPRecipientsRefused,
                    smtplib.SMTPSenderRefused,
                    smtplib.SMTPDataError,
                ):
                    status, error = (
                        "failed",
                        "SMTP rejected delivery; will retry on the next hourly run",
                    )
                except Exception:
                    # SMTP cannot guarantee exactly-once delivery after a lost DATA response.
                    status, error = (
                        "uncertain",
                        "SMTP delivery outcome is unknown; inspect before retrying",
                    )
                else:
                    status = "sent"
                with db.transaction():
                    db.connection.execute(
                        "UPDATE government_reminder_messages SET status=?,updated_at=?,error=?,sent_at=? WHERE message_id=?",
                        (
                            status,
                            stamp,
                            error,
                            stamp if status == "sent" else None,
                            message_id,
                        ),
                    )
    except Exception:
        status, error = "failed", "Reminder job failed; inspect the server log"
        raise
    finally:
        with db.transaction():
            db.connection.execute(
                "UPDATE government_reminder_runs SET status=?,finished_at=?,error=? WHERE run_id=?",
                (status, stamp, error, run_id),
            )
    return {
        "status": status,
        "message_id": message_id,
        "due_count": len(due),
        "error": error,
    }


def resolve_message(db: LedgerDB, payload: Mapping, *, actor: str) -> dict:
    from .government_cases import _event, _text

    if not isinstance(payload, Mapping):
        raise ValueError("Delivery resolution must be an object")
    if set(payload) != {"message_id", "outcome", "evidence_reference"} or payload[
        "outcome"
    ] not in ("sent", "failed"):
        raise ValueError(
            "Resolve requires message_id, sent/failed outcome and evidence_reference"
        )
    evidence = _text(payload["evidence_reference"], "evidence_reference", required=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.transaction():
        row = db.connection.execute(
            "SELECT * FROM government_reminder_messages WHERE message_id=?",
            (payload["message_id"],),
        ).fetchone()
        if row is None or row["status"] != "uncertain":
            raise ValueError("Only uncertain deliveries can be resolved")
        db.connection.execute(
            "UPDATE government_reminder_messages SET status=?,updated_at=?,error=NULL,sent_at=? WHERE message_id=?",
            (
                payload["outcome"],
                stamp,
                stamp if payload["outcome"] == "sent" else None,
                payload["message_id"],
            ),
        )
        case_ids = {
            db.connection.execute(
                "SELECT case_id FROM government_actions WHERE action_id=?", (action_id,)
            ).fetchone()[0]
            for action_id, _ in json.loads(row["action_versions_json"])
        }
        for case_id in case_ids:
            _event(
                db,
                case_id,
                "delivery_resolution",
                stamp,
                evidence,
                "SMTP outcome confirmed by operator",
                actor,
                dict(payload),
            )
    return {"message_id": payload["message_id"], "status": payload["outcome"]}
