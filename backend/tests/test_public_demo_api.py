"""The public runtime serves the real SQL projections without accepting writes."""
from contextlib import contextmanager
from datetime import date
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import socket
import sqlite3
import threading

import pytest

from autonomo_taxes import local_web
from autonomo_taxes.dashboard_service import build_dashboard_from_db
from autonomo_taxes.ledger_db import LedgerDB, SchemaVersionError
from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingServer, load_public_demo_config
from autonomo_taxes.public_demo_data import prepare_demo
from autonomo_taxes.public_demo_runtime import render_demo_shell
from autonomo_test_support.paths import REPO_ROOT


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / "public-data"
    prepare_demo(root, reference_root=REPO_ROOT / "reference")
    return root


@contextmanager
def server_for(root):
    app = LocalAccountingApp(load_public_demo_config(root))
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, app
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(server, method, path, *, body=None, host=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{server.server_port}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def tree(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def test_shared_shell_injection_and_host_boundary(prepared):
    with server_for(prepared) as (server, app):
        packaged = app.ui_assets.path("index.html").read_bytes()
        for path in ("/", "/demo", "/demo/", "/demo/dashboard"):
            status, headers, body = request(server, "GET", path, host="noautonomo.ai")
            assert status == 200
            assert "Set-Cookie" not in headers
            assert '"mode":"public_demo"' in body.decode()
            assert '"basePath":"/demo"' in body.decode()
            assert '"readOnly":true' in body.decode()
            assert '/demo/ui-assets/' in body.decode()
            assert "connect-src 'self'" in headers["Content-Security-Policy"]
            assert "frame-src 'self'" in headers["Content-Security-Policy"]
            assert "form-action 'none'" in headers["Content-Security-Policy"]
        assert app.ui_assets.path("index.html").read_bytes() == packaged
        assert request(server, "GET", "/demo/api/bootstrap", host="attacker.example.invalid")[0] == 403
        status, headers, body = request(server, "GET", "/demo/api/bootstrap")
        assert status == 200 and "Set-Cookie" not in headers
        assert json.loads(body)["default_period"] == "2026-Q3"
        assert json.loads(body)["intake_enabled"] is False
        assert request(server, "GET", "/api/bootstrap")[0] == 200
        for endpoint in ("/api/google-picker/config", "/api/intake", "/api/review/validate", "/api/unknown"):
            assert request(server, "GET", "/demo" + endpoint)[0] == 404
        asset = next(name for name in app.ui_assets.files if name.endswith(".js"))
        status, _, body = request(server, "GET", "/demo/" + asset)
        assert status == 200 and body == app.ui_assets.path(asset).read_bytes()


def test_writes_are_denied_before_body_dispatch_including_unknown_verbs(prepared, monkeypatch):
    before = tree(prepared)
    monkeypatch.setattr(local_web.LocalAccountingApp, "post_ready", lambda *a, **kw: pytest.fail("write dispatched"))
    with server_for(prepared) as (server, _):
        for verb in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT", "PURGE"):
            for path in ("/demo/api/review/post-ready", "/api/settings/profile", "/api/unknown"):
                status, headers, body = request(server, verb, path, body='{"period":"2026-Q3"}')
                assert status == 405
                assert headers["Allow"] == "GET, HEAD"
                assert json.loads(body)["code"] == "public_demo_read_only"
        # A declared but absent 10MB body cannot make the server wait for it.
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=2) as client:
            client.sendall(b"POST /api/intake HTTP/1.1\r\nHost: localhost\r\nContent-Length: 10000000\r\nExpect: 100-continue\r\n\r\n")
            assert b" 405 " in client.recv(4096)
    assert tree(prepared) == before


def test_real_get_models_read_only_no_cache_or_provider_fallback(prepared, monkeypatch):
    before = tree(prepared)
    monkeypatch.setattr(local_web, "fetch_eur_rate", lambda *a, **kw: pytest.fail("ECB fallback"))
    monkeypatch.setattr(local_web, "resolve_verified_replica", lambda *a, **kw: pytest.fail("remote storage fallback"))
    monkeypatch.setattr(local_web.LocalAccountingApp, "_run_cli", lambda *a, **kw: pytest.fail("CLI subprocess"))
    with server_for(prepared) as (server, app):
        for period in ("2026-Q1", "2026-Q2", "2026-Q3"):
            for endpoint in ("dashboard", "transactions", "expenses", "documents", "issues", "assets", "taxes", "analytics", "review/posting-preview"):
                status, _, body = request(server, "GET", f"/demo/api/{endpoint}?period={period}")
                assert status == 200, body
            with LedgerDB.open(prepared / "demo.sqlite", read_only=True) as db:
                expected = build_dashboard_from_db(db, period_key=period, as_of=date(2026, 9, 30))
            assert app._load_cached_dashboard(period) == json.loads(json.dumps(expected, default=str))
        assert request(server, "GET", "/demo/api/settings")[0] == 200
        assert request(server, "GET", "/demo/api/counterparties")[0] == 200
        assert request(server, "GET", "/demo/api/aeat-documents")[0] == 200
        assert request(server, "GET", "/demo/api/reta-check?year=2026")[0] == 200
        status, _, body = request(server, "GET", "/demo/api/analytics?period=2026-Q3&as_of=2028-12-31")
        assert status == 400
        assert app.today == date(2026, 9, 30)
        assert app._fx_suggestion_for(None, {"state": {"transaction": {
            "currency": "USD", "original_currency": "USD", "fx_rate_id": None,
            "transaction_date": "2026-09-15",
        }}}) is None
        with app._connect() as connection:
            assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
            with pytest.raises(sqlite3.OperationalError):
                connection.execute("UPDATE counterparties SET display_name='blocked'")
        assert not app.config.cache_root.exists()
    assert tree(prepared) == before


def test_every_actual_document_and_filing_mapping_serves_the_same_pdf(prepared, monkeypatch):
    monkeypatch.setattr(local_web, "resolve_verified_replica", lambda *a, **kw: pytest.fail("remote source"))
    expected = (prepared / "demo-document.pdf").read_bytes()
    with server_for(prepared) as (server, app):
        with app._connect() as connection:
            documents = [row[0] for row in connection.execute("SELECT document_id FROM documents")]
            filings = [row[0] for row in connection.execute("SELECT filing_snapshot_id FROM filing_snapshots")]
        for document in documents:
            assert app.document_file(document) == (prepared / "demo-document.pdf", "application/pdf")
        for identifier in filings:
            assert app.filing_file(identifier) == (prepared / "demo-document.pdf", "application/pdf")
        for path in (f"/api/document/{documents[0]}/preview", f"/api/document/{documents[0]}/content", f"/api/aeat-filings/{filings[0]}/content"):
            status, headers, body = request(server, "GET", "/demo" + path)
            assert status == 200 and body == expected
            assert headers["Content-Type"] == "application/pdf"
            assert headers["X-Frame-Options"] == "SAMEORIGIN"
            assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]
            status, head_headers, body = request(server, "HEAD", "/demo" + path)
            assert status == 200 and body == b""
            assert head_headers["Content-Length"] == str(len(expected))
        for path in ("/api/document/00000000-0000-0000-0000-000000000000/content", "/api/aeat-filings/00000000-0000-0000-0000-000000000000/content", "/api/document/not-an-id/preview"):
            assert request(server, "GET", "/demo" + path)[0] == 404
        status, headers, body = request(server, "HEAD", "/demo/api/bootstrap")
        assert status == 200 and body == b"" and int(headers["Content-Length"]) > 0


def test_missing_or_changed_artifacts_fail_and_public_mode_never_loads_private_config(prepared, tmp_path, monkeypatch):
    monkeypatch.setattr(local_web, "load_config", lambda *a, **kw: pytest.fail("private config discovery"))
    with pytest.raises((ValueError, FileNotFoundError)):
        load_public_demo_config(tmp_path / "missing")
    with pytest.raises(SystemExit):
        local_web.main(["--public-demo-root", str(prepared), "--config", str(tmp_path / "private.yaml")])
    with pytest.raises(SystemExit, match="loopback"):
        local_web.main(["--host", "0.0.0.0"])
    seen = []
    class StopServer:
        def __init__(self, address, app):
            seen.append((address, app.config)); self.server_address = address
        def serve_forever(self, **kwargs): raise KeyboardInterrupt
        def server_close(self): pass
    monkeypatch.setattr(local_web, "LocalAccountingServer", StopServer)
    assert local_web.main(["--public-demo-root", str(prepared), "--host", "0.0.0.0"]) == 0
    assert seen[0][1].public_demo is True
    (prepared / "demo.sqlite").chmod(0o600)
    with sqlite3.connect(prepared / "demo.sqlite") as connection:
        connection.execute("UPDATE counterparties SET display_name='Atlas Changed (demo)' WHERE display_name='Atlas Demo Studio'")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_public_demo_config(prepared)


def test_controlled_db_change_is_visible_without_rebuilding_the_frontend(prepared):
    normal_index = REPO_ROOT / "backend/src/autonomo_taxes/web_ui/dist/index.html"
    before_ui = normal_index.read_bytes()
    (prepared / "demo.sqlite").chmod(0o600)
    with sqlite3.connect(prepared / "demo.sqlite") as connection:
        connection.execute("UPDATE counterparties SET display_name='Atlas Changed (demo)' WHERE display_name='Atlas Demo Studio'")
    manifest_path = prepared / "demo-manifest.json"
    manifest_path.chmod(0o600)
    manifest = json.loads(manifest_path.read_text())
    manifest["sha256"]["demo.sqlite"] = hashlib.sha256((prepared / "demo.sqlite").read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with server_for(prepared) as (server, _):
        status, _, body = request(server, "GET", "/demo/api/counterparties")
        assert status == 200
        assert any(row["display_name"] == "Atlas Changed (demo)" for row in json.loads(body))
    assert normal_index.read_bytes() == before_ui


def test_html_bootstrap_handles_attribute_order_quotes_and_whitespace():
    html = b'''<html><script type='application/json'\n id='autonomo-runtime'>\n { } \n</script><script src="/ui-assets/a.js"></script><a href="/api/bootstrap">API</a></html>'''
    rendered = render_demo_shell(html, {"ui-assets/a.js"}).decode()
    assert '"mode":"public_demo"' in rendered
    assert 'src="/demo/ui-assets/a.js"' in rendered
    assert 'href="/api/bootstrap"' in rendered
    with pytest.raises(ValueError):
        render_demo_shell(b'<html></html>', set())
    with pytest.raises(ValueError):
        render_demo_shell(html.replace(b'{ }', b'{"mode":"other"}'), {"ui-assets/a.js"})


@pytest.mark.parametrize("fault", ["missing", "corrupt_manifest", "bad_schema", "symlink", "wal"])
def test_prepared_runtime_rejects_invalid_artifacts_without_repair(prepared, tmp_path, fault):
    manifest_path = prepared / "demo-manifest.json"
    manifest_path.chmod(0o600)
    manifest = json.loads(manifest_path.read_text())
    database = prepared / "demo.sqlite"
    database.chmod(0o600)
    if fault == "missing":
        database.unlink()
    elif fault == "corrupt_manifest":
        manifest_path.write_text("{broken")
    elif fault == "symlink":
        moved = tmp_path / "outside.sqlite"
        database.rename(moved)
        database.symlink_to(moved)
    else:
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA user_version=999" if fault == "bad_schema" else "PRAGMA journal_mode=WAL")
        connection.close()
        manifest["sha256"]["demo.sqlite"] = hashlib.sha256(database.read_bytes()).hexdigest()
        if fault == "bad_schema": manifest["schema_version"] = 999
        manifest_path.write_text(json.dumps(manifest))
    before = tree(prepared)
    expected_error = SchemaVersionError if fault == "bad_schema" else (ValueError, FileNotFoundError)
    with pytest.raises(expected_error):
        load_public_demo_config(prepared)
    assert tree(prepared) == before


def test_document_requires_an_actual_attachment_even_when_the_source_path_exists(prepared):
    database = prepared / "demo.sqlite"
    database.chmod(0o600)
    with LedgerDB.open(database) as db:
        orphan = db.upsert_document(document_type="expense_invoice", document_number="DEMO-UNATTACHED", issued_on="2026-09-01", period_key="2026-Q3", lifecycle_status="approved")
        db.set_document_storage(orphan["document_id"], source_path="/data/demo-document.pdf", mime_type="application/pdf", expected_row_version=orphan["row_version"])
    manifest_path = prepared / "demo-manifest.json"
    manifest_path.chmod(0o600)
    manifest = json.loads(manifest_path.read_text())
    manifest["sha256"]["demo.sqlite"] = hashlib.sha256(database.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with server_for(prepared) as (server, _):
        assert request(server, "GET", f"/demo/api/document/{orphan['document_id']}/content")[0] == 404
