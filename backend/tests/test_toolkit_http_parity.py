"""Both adapters expose the same decision, refusal and committed accounting result."""

from pathlib import Path
import json
from uuid import uuid4

import pytest

from autonomo_taxes.local_web import LocalWebConfig
from autonomo_taxes.ui_assets import default_ui_root
from autonomo_taxes.toolkit_cli import public_value
from test_expense_workflow import setup_draft
from test_local_web_guided import _Server
from test_toolkit_cli import invoke

pytestmark = pytest.mark.web


def test_expense_http_cli_preview_errors_and_replay_share_one_action(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture, draft = setup_draft(tmp_path)
    root = Path(__file__).resolve().parents[2]
    config = LocalWebConfig(
        project_root=root,
        database=fixture["database"],
        inbox_root=tmp_path / "private/inbox",
        archive_root=tmp_path / "private/evidence",
        cache_root=tmp_path / "private/cache",
        static_root=default_ui_root(),
        read_only_document_roots=(tmp_path,),
    )
    server = _Server(config, monkeypatch, ecb=None)
    origin = f"http://127.0.0.1:{server.port}"
    base = "/api/expense-workflows/" + fixture["transaction_id"]

    def post(suffix, payload):
        status, _, body = server.request(
            "POST",
            base + suffix,
            body=json.dumps(payload).encode(),
            origin=origin,
            content_type="application/json",
        )
        return status, json.loads(body)

    try:
        for version in (draft["draft_version"] - 1, draft["draft_version"]):
            payload = {"expected_version": version}
            code, cli = invoke(
                capsys,
                fixture,
                ["expense", "preview", fixture["transaction_id"]],
                payload=payload,
            )
            status, http = post("/preview", payload)
            assert (code == 0) == (status == 200)
            if code:
                assert cli["code"] == http["code"]
            else:
                assert cli == public_value(http)
        request = {
            "expected_version": draft["draft_version"],
            "preview_token": http["preview_token"],
            "request_id": str(uuid4()),
        }
        status, http = post("/confirm", request)
        assert status == 200 and http["posted"]
        code, cli = invoke(
            capsys,
            fixture,
            ["expense", "confirm", fixture["transaction_id"]],
            payload=request,
        )
        assert (
            code == 0
            and cli["transaction_id"] == http["transaction_id"]
            and cli["posted"]
        )
        code, detail = invoke(
            capsys, fixture, ["transactions", "show", fixture["transaction_id"]]
        )
        assert code == 0
        assert detail == public_value(
            server.server.app.transaction_detail(fixture["transaction_id"])
        )
    finally:
        server.close()


@pytest.mark.parametrize("adapter", ["http", "cli"])
def test_intake_refuses_bad_amount_and_conflicting_retry_without_ledger_changes(
    tmp_path, capsys, monkeypatch, adapter
):
    from autonomo_taxes.ledger_db import LedgerDB
    from autonomo_test_support.local_web_guided import _config

    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    config = _config(tmp_path / "private")
    with LedgerDB.initialize(config.database):
        pass
    source = tmp_path / "synthetic.txt"
    content = b"Synthetic customer invoice\n2026-07-01\nEUR 12.00"
    source.write_bytes(content)
    facts = {
        "kind": "income_invoice",
        "period": "2026-Q3",
        "issued_on": "2026-07-01",
        "currency": "EUR",
        "counterparty_name": "Synthetic Customer",
    }
    server = _Server(config, monkeypatch, ecb=None) if adapter == "http" else None

    def intake(gross):
        fields = {**facts, "gross": gross}
        if server is None:
            return invoke(
                capsys, {"database": config.database},
                ["intake", "local", str(source),
                 "--inbox-root", str(config.inbox_root),
                 "--archive-root", str(config.archive_root)],
                payload=fields,
            )
        boundary = "synthetic-intake-boundary"
        chunks = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            for name, value in fields.items()
        ]
        chunks.extend([
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.txt"\r\nContent-Type: text/plain\r\n\r\n'.encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ])
        status, _, body = server.request(
            "POST", "/api/intake", body=b"".join(chunks),
            origin=f"http://127.0.0.1:{server.port}",
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return status, json.loads(body)

    def ledger_state():
        with LedgerDB.open(config.database, read_only=True) as db:
            return "\n".join(db.connection.iterdump())

    rejected = 400 if server else 1
    accepted = 201 if server else 0
    try:
        before = ledger_state()
        status, result = intake("banana")
        assert status == rejected, result
        assert "decimal numbers" in result["error"]
        if server is None:
            assert result["code"] == "invalid_input"
        assert ledger_state() == before
        assert source.read_bytes() == content

        status, result = intake("12.00")
        assert status == accepted, result
        transaction_id = result["transaction_id"]
        document_id = result["document_id"]
        before = ledger_state()
        status, result = intake("13.00")
        assert status == rejected, result
        assert "conflicts" in result["error"]
        if server is None:
            assert result["code"] == "invalid_input"
        assert ledger_state() == before
        with LedgerDB.open(config.database, read_only=True) as db:
            assert db.connection.execute(
                "SELECT amount_minor FROM transactions WHERE transaction_id = ?", (transaction_id,)
            ).fetchone()[0] == 1200
            assert db.connection.execute(
                "SELECT total_minor FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()[0] == 1200
        assert source.read_bytes() == content
    finally:
        if server is not None:
            server.close()
