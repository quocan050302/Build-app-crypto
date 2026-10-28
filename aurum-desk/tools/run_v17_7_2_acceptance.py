#!/usr/bin/env python3
"""
AURUM DESK V17.7.2 — ACCEPTANCE RUNNER ENTRYPOINT
Wraps tools/run_v17_7_acceptance.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from run_v17_7_acceptance import main

if __name__ == "__main__":
    sys.exit(main())
