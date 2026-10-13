"""Run the real public server against one disposable prepared SQLite dataset."""
from __future__ import annotations

from pathlib import Path
import signal
import tempfile

from autonomo_taxes.local_web import main
from autonomo_taxes.public_demo_data import prepare_demo


def stop(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


def run() -> int:
    for name in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(name, stop)
    with tempfile.TemporaryDirectory(prefix="autonomo-public-demo-browser-") as directory:
        root = Path(directory)
        prepare_demo(root, reference_root=Path(__file__).resolve().parents[2] / "reference")
        return main([
            "--public-demo-root", str(root),
            "--host", "127.0.0.1", "--port", "8766",
        ])


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except KeyboardInterrupt:
        raise SystemExit(0)
