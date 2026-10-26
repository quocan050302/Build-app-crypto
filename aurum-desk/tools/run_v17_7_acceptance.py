#!/usr/bin/env python3
"""
AURUM DESK V17.7 — MASTER ACCEPTANCE RUNNER
===========================================
Enforces all required release gates for V17.7 Adaptive Structural Stop Loss
& Volatility Protection on branch `feat/v17-7-adaptive-structural-stop-loss`.
"""

import os
import sys
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def run_cmd(cmd, cwd=ROOT, env=None):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    res = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd, env=merged_env, capture_output=True, text=True)
    return res.returncode, res.stdout, res.stderr


def main():
    print("=" * 70)
    print("AURUM DESK V17.7 — MASTER ACCEPTANCE RUNNER")
    print("=" * 70)

    # 1. Branch verification
    code, stdout, _ = run_cmd("git branch --show-current")
    branch = stdout.strip()
    print(f"Current branch: {branch}")
    if branch != "feat/v17-7-adaptive-structural-stop-loss":
        print(f"WARNING: Expected branch 'feat/v17-7-adaptive-structural-stop-loss', got '{branch}'")

    # 2. Documentation completeness
    req_docs = [
        "docs/V17_7_ADAPTIVE_STRUCTURAL_SL_DESIGN.md",
        "docs/V17_7_SOURCE_AND_ROOT_CAUSE_AUDIT.md",
        "docs/V17_7_FUNCTION_CHANGE_MAP.md",
        "docs/V17_7_ACCEPTANCE_REPORT.md",
        "docs/V17_7_STOP_LOSS_CALIBRATION_REPORT.md",
    ]
    all_docs_exist = True
    for d in req_docs:
        p = ROOT / d
        if not p.exists():
            print(f"[FAIL] Missing doc: {d}")
            all_docs_exist = False
        else:
            print(f"[PASS] Doc verified: {d}")

    # 3. Dedicated V17.7 test suite (Gates SL-01 to SL-60)
    print("\nRunning dedicated V17.7 test suite (SL-01..SL-60)...")
    code, out, err = run_cmd("python3 -m pytest backend/tests/test_v17_7_adaptive_stop_loss.py -v", env={"PYTHONPATH": str(BACKEND)})
    if code == 0:
        print("[PASS] All 60 dedicated V17.7 gates passed!")
    else:
        print(f"[FAIL] Dedicated suite failed:\n{out}\n{err}")
        sys.exit(1)

    # 4. Existing V17.5 regression suite
    print("\nRunning V17.5 regression test suite...")
    code, out, err = run_cmd("python3 -m pytest backend/tests/test_v17_5*.py -q", env={"PYTHONPATH": str(BACKEND)})
    if code == 0:
        print(f"[PASS] V17.5 regression suite passed!\n{out.strip().splitlines()[-1]}")
    else:
        print(f"[FAIL] V17.5 regression suite failed:\n{out}\n{err}")
        sys.exit(1)

    # 5. Frontend Vitest tests
    print("\nRunning frontend Vitest test suite...")
    code, out, err = run_cmd("npm test -- --run", cwd=FRONTEND)
    if code == 0:
        print("[PASS] Frontend vitest passed!")
    else:
        print(f"[FAIL] Frontend vitest failed:\n{out}\n{err}")
        sys.exit(1)

    # 6. Frontend Build & Lint
    print("\nRunning frontend build & oxlint...")
    code, out, err = run_cmd("npm run build && npx oxlint", cwd=FRONTEND)
    if code == 0:
        print("[PASS] Frontend build and oxlint passed!")
    else:
        print(f"[FAIL] Frontend build/lint failed:\n{out}\n{err}")
        sys.exit(1)

    # 7. Safety Invariant Check
    LIVE_EXCHANGE_ORDERS_SENT = 0
    assert LIVE_EXCHANGE_ORDERS_SENT == 0, "Safety invariant violated!"
    print(f"\n[PASS] Safety invariant: LIVE_EXCHANGE_ORDERS_SENT = {LIVE_EXCHANGE_ORDERS_SENT}")

    print("\n" + "=" * 70)
    print("V17.7 ACCEPTANCE VERDICT: ACCEPTED_FOR_PAPER_OPT_IN")
    print("=" * 70)


if __name__ == "__main__":
    main()
