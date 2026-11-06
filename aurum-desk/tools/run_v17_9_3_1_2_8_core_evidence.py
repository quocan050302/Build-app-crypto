"""Collect fresh full regression and replay evidence for V128.

The reused runner emits its original 25 checks. Its exit code is core
evidence only and cannot authorize publication under the V128 contract.
"""
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP / "backend"), str(APP)]

from tools.run_v17_9_3_1_2_7_verification import main


if __name__ == "__main__":
    raise SystemExit(main(artifact_namespace="v17_9_3_1_2_8", minimum_ram_bytes=256 * 1024**2))
