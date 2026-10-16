"""Transport-neutral government-case operations."""

from pathlib import Path
import json
import tempfile

from .. import government_cases as cases
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
        if len(content) > 30 * 1024 * 1024 or not content.startswith(b"%PDF-"):
            raise ValueError("Evidence must be a PDF no larger than 30 MB")
        metadata = json.loads(fields["metadata"])
        self.config.cache_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="case-upload-", dir=self.config.cache_root
        ) as folder:
            path = Path(folder) / "evidence.pdf"
            path.write_bytes(content)
            path.chmod(0o600)
            with open_ledger_db(self.config.database) as db:
                return cases.attach_pdf(
                    db, case_id, path, self.config.archive_root, metadata, actor=actor
                )
