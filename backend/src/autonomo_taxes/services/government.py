"""Transport-neutral government-case operations."""

from pathlib import Path
import json
import tempfile

from .. import government_cases as cases
from .. import government_correspondence as correspondence
from ..case_reminders import health, resolve_message
from ..ledger_db import open as open_ledger_db


class GovernmentService:
    def government_cases(self, **filters):
        with open_ledger_db(self.config.database, read_only=True) as db:
            return {**cases.list_cases(db, **filters), "reminders": health(db)}

    def government_case(self, case_id):
        with open_ledger_db(self.config.database, read_only=True) as db:
            return cases.case_detail(db, case_id)

    def government_write(
        self, operation, payload, *, actor, case_id=None, action_id=None
    ):
        with open_ledger_db(self.config.database) as db:
            if operation == "create":
                return cases.create_case(db, payload, actor=actor)
            if operation == "state":
                return cases.update_case(db, case_id, payload, actor=actor)
            if operation == "correspondence":
                return correspondence.add(db, case_id, payload, actor=actor)
            if operation == "event":
                return cases.add_event(db, case_id, payload, actor=actor)
            if operation == "action":
                return cases.save_action(
                    db, case_id, payload, actor=actor, action_id=action_id
                )
            if operation == "resolve-reminder":
                return resolve_message(db, payload, actor=actor)
            raise ValueError("Unsupported government operation")

    def ingest_government_upload(self, case_id, *, fields, filename, content, actor):
        if self.config.archive_root is None:
            raise ValueError("An evidence archive must be configured")
        if set(fields) != {"metadata"} or len(fields["metadata"]) > 16000:
            raise ValueError("PDF upload requires metadata JSON")
        if len(content) > correspondence.MAX_FILE_BYTES:
            raise ValueError("Evidence must be no larger than 30 MB")
        metadata = json.loads(fields["metadata"])
        if not isinstance(metadata, dict):
            raise ValueError("Metadata must be an object")
        is_message = bool(metadata.get("correspondence_event_id"))
        if not is_message and not content.startswith(b"%PDF-"):
            raise ValueError("Standalone official evidence must be a PDF")
        if is_message:
            metadata["filename"] = filename
        self.config.cache_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="case-upload-", dir=self.config.cache_root
        ) as folder:
            path = Path(folder) / "evidence.pdf"
            path.write_bytes(content)
            path.chmod(0o600)
            with open_ledger_db(self.config.database) as db:
                operation = correspondence.attach if is_message else cases.attach_pdf
                return operation(
                    db, case_id, path, self.config.archive_root, metadata, actor=actor
                )
