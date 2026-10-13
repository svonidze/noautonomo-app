"""Run inside the built image with /tmp writable; no source checkout or pytest."""

import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from autonomo_taxes import local_web
from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingServer, load_public_demo_config
from autonomo_taxes.public_demo_data import prepare_demo


def file_hashes(root):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir()}


def main():
    assert os.geteuid() != 0, "The image must run as a non-root user"
    assert "site-packages" in Path(local_web.__file__).parts, "Exercise the installed wheel"
    assert Path("/app/reference/reta/2026.json").is_file(), "Missing packaged public RETA reference"
    with tempfile.TemporaryDirectory(prefix="noa-image-test-") as directory:
        root = Path(directory)
        prepare_demo(root)
        before = file_hashes(root)
        assert set(before) == {"demo.sqlite", "demo-document.pdf", "demo-manifest.json"}
        try:
            prepare_demo(root)
        except FileExistsError:
            pass
        else:
            raise AssertionError("Seed must refuse an existing prepared directory")
        server = LocalAccountingServer(("127.0.0.1", 0), LocalAccountingApp(load_public_demo_config(root)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(path, method="GET"):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            try:
                connection.request(method, path)
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()

        try:
            # Caddy strips /demo on forwarding; the upstream receives /api/... .
            status, headers, html = request("/")
            assert status == 200
            assert b'"basePath":"/demo"' in html and b'"readOnly":true' in html
            assert "connect-src 'self'" in headers["Content-Security-Policy"]
            assets = re.findall(r'(?:src|href)="(/demo/ui-assets/[^\"]+)"', html.decode())
            assert assets, "Missing ordinary packaged frontend resources"
            for asset in assets:
                assert request(asset.removeprefix("/demo"))[0] == 200
            status, _, body = request("/api/bootstrap")
            assert status == 200
            period = json.loads(body)["default_period"]
            assert period == "2026-Q3"
            assert request("/api/bootstrap", "HEAD")[0::2] == (200, b"")
            status, _, settings = request("/api/settings")
            assert status == 200 and isinstance(json.loads(settings), dict)
            for endpoint in ("dashboard", "transactions", "documents", "analytics", "taxes"):
                status, _, body = request(f"/api/{endpoint}?period={period}")
                assert status == 200, (endpoint, status)
                assert json.loads(body) is not None
            documents = json.loads(request(f"/api/documents?period={period}")[2])
            preview = f"/api/document/{documents[0]['document_id']}/preview"
            status, headers, pdf = request(preview)
            assert status == 200 and pdf.startswith(b"%PDF-")
            assert headers["Content-Type"] == "application/pdf"
            assert headers["X-Frame-Options"] == "SAMEORIGIN"
            assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]
            assert request(preview, "HEAD")[0::2] == (200, b"")
            for path in ("/api/settings/profile", "/api/google-picker/config", "/api/unknown", "/missing"):
                assert request(path)[0] == 404, path
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT", "CUSTOM"):
                for path in ("/", "/api/bootstrap", preview):
                    status, headers, _ = request(path, method)
                    assert status == 405 and headers["Allow"] == "GET, HEAD", (method, path, status)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        assert file_hashes(root) == before, "HTTP reads or rejected writes changed prepared data"
    print("PASS: installed wheel, embedded RETA reference, seed, packaged UI, native HTTP API/PDF, denied writes, unchanged data")


if __name__ == "__main__":
    main()
