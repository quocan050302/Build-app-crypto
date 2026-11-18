"""
backend/tests/test_kh_01a_r1_subprocess_verifier.py

Separate-OS-process negative controls for `tools/kh_01a_r1_independent_verifier.py`
(scheduled in exclusive subprocess shard via CHILD_SPAWNING_TEST_FILES).
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from services.kh_01a_foundation_service import (
    CANONICAL_DATASET_SHA256_KH01A,
    MANDATORY_BRANCH_KH01A,
)
from services.kh_01a_r1_forensics_closure_service import REQUIRED_ARTIFACTS_KH01A_R1
from tools.fresh_junit_io import write_json_durable_atomic

REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFIER_SCRIPT = REPO_ROOT / "aurum-desk/tools/kh_01a_r1_independent_verifier.py"


def test_r1_subproc_verifier_fail_closed(tmp_path: Path) -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"
    run_id = "TEST_SUBPROC_VERIFIER"

    # Case 1: Incomplete directory exits nonzero
    inc_dir = tmp_path / "inc"
    inc_dir.mkdir()
    p1 = subprocess.run(
        [sys.executable, str(VERIFIER_SCRIPT), "--run-dir", str(inc_dir), "--skip-git-freeze-check"],
        capture_output=True,
        text=True,
    )
    assert p1.returncode != 0
    out1 = json.loads(p1.stdout)
    assert out1["independent_verifier_status"] == "FAIL"
    assert any("MISSING_REQUIRED_ARTIFACT" in v for v in out1["violations"])

    # Case 2: Corrupted compressed 05_r1_preentry_170_rows.jsonl.gz exits nonzero even if 16 claims PASS
    corrupt_dir = tmp_path / "bad"
    corrupt_dir.mkdir()
    for req in REQUIRED_ARTIFACTS_KH01A_R1:
        if req == "15_independent_verifier_receipts_sha256_manifest.json":
            continue
        fp = corrupt_dir / req
        if req.endswith(".jsonl.gz"):
            fp.write_bytes(b"\x1f\x8b\x08\x00NOT_VALID_GZIP_PAYLOAD")
        elif req.endswith(".json"):
            write_json_durable_atomic(
                fp,
                {
                    "run_id": run_id,
                    "tested_executable_sha": sha,
                    "branch": MANDATORY_BRANCH_KH01A,
                    "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01A,
                    "orthogonal_verdicts": {"ENGINEERING_VERDICT": "PASS", "FORENSICS_SEMANTIC_VERDICT": "PASS"},
                },
            )
        else:
            fp.write_text("placeholder\n", encoding="utf-8")

    p2 = subprocess.run(
        [sys.executable, str(VERIFIER_SCRIPT), "--run-dir", str(corrupt_dir), "--skip-git-freeze-check"],
        capture_output=True,
        text=True,
    )
    assert p2.returncode != 0
    out2 = json.loads(p2.stdout)
    assert out2["independent_verifier_status"] == "FAIL"
    assert any("ARTIFACT_PARSE_OR_DECOMPRESS_ERROR" in v for v in out2["violations"])
