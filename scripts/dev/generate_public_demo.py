#!/usr/bin/env python3
"""Compatibility entry point for the persistent, offline demo initializer."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/src"))
from autonomo_taxes.public_demo_data import main

if __name__ == "__main__":
    raise SystemExit(main())
