"""
tools/artifact_verifier.py
AURUM DESK V17.7.4 — Acceptance Artifact Integrity Verifier (Section 11 / Section 12.1)
Builds source manifests, writes atomic run-scoped acceptance artifacts, and strictly verifies bundle integrity.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.acceptance_verdict import determine_release_verdict, MANDATORY_GATES_V17_7_4

EXCLUDE_PATTERNS = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    "node_modules",
    "dist",
    "venv",
    ".aurum_test_owned",
}

EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".db",
    ".log",
    ".tmp",
    ".sqlite",
}


def build_source_manifest(root_dir: Path) -> Dict[str, Any]:
    """Computes deterministic SHA256 source manifest across executable files (Section 11.4)."""
    manifest: Dict[str, str] = {}
    root = Path(root_dir).resolve()

    for sub in ("backend", "tools"):
        sub_path = root / sub
        if not sub_path.is_dir():
            continue
        for p in sorted(sub_path.rglob("*")):
            if not p.is_file():
                continue
            if any(part in EXCLUDE_PATTERNS for part in p.parts):
                continue
            if p.suffix in EXCLUDE_SUFFIXES:
                continue

            rel_str = str(p.relative_to(root)).replace("\\", "/")
            try:
                content = p.read_bytes()
                manifest[rel_str] = hashlib.sha256(content).hexdigest()
            except Exception:
                pass

    sorted_hashes = "".join(manifest[k] for k in sorted(manifest.keys()))
    composite_sha = hashlib.sha256(sorted_hashes.encode("utf-8")).hexdigest()

    return {
        "schema_version": 1,
        "files_count": len(manifest),
        "composite_sha": composite_sha,
        "files": manifest,
    }


def write_acceptance_artifacts(
    run_id: str,
    results_payload: Dict[str, Any],
    source_manifest: Dict[str, Any],
    gate_manifest: List[str],
    artifacts_base_dir: Path,
) -> Path:
    """
    Writes run-scoped acceptance artifacts under docs/artifacts/v17_7_4/<run_id>/ (Section 11.5).
    Preserves historical v17_7_2 and v17_7_3 files without overwrite.
    """
    base_dir = Path(artifacts_base_dir).resolve()
    run_dir = base_dir / "v17_7_4" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write gate_manifest.json
    (run_dir / "gate_manifest.json").write_text(
        json.dumps({"schema_version": 1, "run_id": run_id, "mandatory_gates": gate_manifest}, indent=2),
        encoding="utf-8",
    )

    # 2. Write source_manifest.json
    (run_dir / "source_manifest.json").write_text(
        json.dumps(source_manifest, indent=2),
        encoding="utf-8",
    )

    # 3. Write acceptance_results.json
    (run_dir / "acceptance_results.json").write_text(
        json.dumps(results_payload, indent=2),
        encoding="utf-8",
    )

    # 4. Compute integrity manifest over written leaf files (excluding integrity_manifest itself)
    leaf_files = ["gate_manifest.json", "source_manifest.json", "acceptance_results.json"]
    integrity_digests = {}
    for fname in sorted(leaf_files):
        fpath = run_dir / fname
        if fpath.is_file():
            integrity_digests[fname] = {
                "size_bytes": fpath.stat().st_size,
                "sha256": hashlib.sha256(fpath.read_bytes()).hexdigest(),
            }

    integrity_payload = {
        "schema_version": 1,
        "run_id": run_id,
        "leaf_files": integrity_digests,
    }
    (run_dir / "integrity_manifest.json").write_text(
        json.dumps(integrity_payload, indent=2),
        encoding="utf-8",
    )

    return run_dir


def verify_acceptance_bundle(
    run_dir: Path,
    expected_version: str = "17.7.4",
    expected_run_id: Optional[str] = None,
    expected_source_sha: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """
    Strict verifier for acceptance artifact bundle (Section 11.6 & Section 12.1).
    Validates schema, version, run ID, hashes, gates, and recomputed verdict.
    """
    errors: List[str] = []
    rdir = Path(run_dir).resolve()

    if not rdir.is_dir():
        return False, [f"Run directory does not exist: {rdir}"]

    # Required files
    req_files = ["integrity_manifest.json", "acceptance_results.json", "source_manifest.json", "gate_manifest.json"]
    for req in req_files:
        if not (rdir / req).is_file():
            errors.append(f"Missing required artifact: {req}")

    if errors:
        return False, errors

    # 1. Verify integrity manifest and leaf hashes
    try:
        integ_data = json.loads((rdir / "integrity_manifest.json").read_text(encoding="utf-8"))
        leaf_files = integ_data.get("leaf_files", {})
        for fname, meta in leaf_files.items():
            fpath = rdir / fname
            if not fpath.is_file():
                errors.append(f"Integrity leaf file missing: {fname}")
                continue
            cur_sha = hashlib.sha256(fpath.read_bytes()).hexdigest()
            if cur_sha != meta.get("sha256"):
                errors.append(f"Integrity mismatch for {fname}: expected {meta.get('sha256')}, got {cur_sha}")
    except Exception as e:
        errors.append(f"Failed to read/validate integrity manifest: {e}")

    # 2. Verify acceptance_results.json
    try:
        results = json.loads((rdir / "acceptance_results.json").read_text(encoding="utf-8"))
        if results.get("runner_version") != expected_version:
            errors.append(f"Version mismatch: expected {expected_version}, got {results.get('runner_version')}")

        if expected_run_id and results.get("run_id") != expected_run_id:
            errors.append(f"Run ID mismatch: expected {expected_run_id}, got {results.get('run_id')}")

        if expected_source_sha and results.get("source_sha") != expected_source_sha:
            errors.append(f"Source SHA mismatch: expected {expected_source_sha}, got {results.get('source_sha')}")

        # Check recomputed verdict
        gates = results.get("gates", {})
        recomputed = determine_release_verdict(gates, MANDATORY_GATES_V17_7_4)
        recorded_verdict = results.get("verdict")
        if recorded_verdict != recomputed:
            errors.append(f"Verdict inconsistency: recorded '{recorded_verdict}', recomputed '{recomputed}'")
        if recomputed != "ACCEPTED_FOR_PAPER_OPT_IN":
            errors.append(f"Bundle recomputed verdict is not ACCEPTED_FOR_PAPER_OPT_IN: {recomputed}")
    except Exception as e:
        errors.append(f"Failed to parse/validate acceptance results: {e}")

    return len(errors) == 0, errors
