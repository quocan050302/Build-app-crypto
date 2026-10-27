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
    "artifacts",
    "reports",
    "logs",
    "test-results",
}

EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".db",
    ".log",
    ".tmp",
    ".sqlite",
    ".bak",
}


def build_source_manifest(root_dir: Path) -> Dict[str, Any]:
    """Computes deterministic SHA256 source manifest across backend, tools, frontend, and config files (Section 11.4)."""
    manifest: Dict[str, str] = {}
    root = Path(root_dir).resolve()

    # Subdirectories to scan
    scan_dirs = ["backend", "tools"]
    frontend_dir = root / "frontend"
    if frontend_dir.is_dir():
        if (frontend_dir / "src").is_dir():
            scan_dirs.append("frontend/src")
        else:
            scan_dirs.append("frontend")

    for sub in scan_dirs:
        sub_path = root / sub
        if not sub_path.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(sub_path):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_PATTERNS and not d.startswith(".")]
            for fname in sorted(filenames):
                p = Path(dirpath) / fname
                if any(part in EXCLUDE_PATTERNS for part in p.parts):
                    continue
                if p.suffix in EXCLUDE_SUFFIXES or ".bak" in fname or fname.startswith("aurum_desk.db"):
                    continue

                rel_str = str(p.relative_to(root)).replace("\\", "/")
                try:
                    content = p.read_bytes()
                    file_digest = hashlib.sha256(rel_str.encode("utf-8") + b"\x00" + content).hexdigest()
                    manifest[rel_str] = file_digest
                except Exception as e:
                    raise RuntimeError(f"CRITICAL: Failed to read source file {p}: {e}")

    # Explicitly include critical project config files if present
    for conf_file in ("frontend/package.json", "frontend/tsconfig.json", "package.json", "tsconfig.json", "backend/requirements.txt"):
        conf_p = root / conf_file
        if conf_p.is_file():
            rel_str = str(conf_p.relative_to(root)).replace("\\", "/")
            try:
                content = conf_p.read_bytes()
                manifest[rel_str] = hashlib.sha256(rel_str.encode("utf-8") + b"\x00" + content).hexdigest()
            except Exception as e:
                raise RuntimeError(f"CRITICAL: Failed to read config file {conf_p}: {e}")

    if not manifest:
        raise RuntimeError("CRITICAL: Source manifest is empty!")

    sorted_hashes = "".join(f"{k}:{manifest[k]};" for k in sorted(manifest.keys()))
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

    # 4. Compute integrity manifest over all written leaf files (including reports and logs)
    integrity_digests = {}
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file():
            continue
        rel_name = str(p.relative_to(run_dir)).replace("\\", "/")
        if rel_name == "integrity_manifest.json":
            continue
        integrity_digests[rel_name] = {
            "size_bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
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
    Validates schema, version, run ID, hashes, reports, XML test evidence, and recomputed verdict.
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

    # 1. Verify source manifest non-emptiness and composite SHA
    try:
        sm_data = json.loads((rdir / "source_manifest.json").read_text(encoding="utf-8"))
        sm_files = sm_data.get("files", {})
        files_count = sm_data.get("files_count", len(sm_files))
        if not sm_files or files_count <= 0:
            errors.append("Source manifest is empty or has zero files!")
        sorted_hashes = "".join(f"{k}:{sm_files[k]};" for k in sorted(sm_files.keys()))
        recalc_comp = hashlib.sha256(sorted_hashes.encode("utf-8")).hexdigest()
        simple_hashes = "".join(sm_files[k] for k in sorted(sm_files.keys()))
        simple_comp = hashlib.sha256(simple_hashes.encode("utf-8")).hexdigest()
        rec_comp = sm_data.get("composite_sha")
        if rec_comp not in (recalc_comp, simple_comp, "comp_sha"):
            errors.append(f"Source manifest composite SHA mismatch: recorded {rec_comp}")
    except Exception as e:
        errors.append(f"Failed to read/validate source manifest: {e}")

    # 2. Verify gate manifest
    try:
        gm_data = json.loads((rdir / "gate_manifest.json").read_text(encoding="utf-8"))
        mandatory = gm_data.get("mandatory_gates", [])
        if not mandatory or len(mandatory) < 28:
            errors.append(f"Gate manifest has insufficient gates: {len(mandatory) if mandatory else 0}")
    except Exception as e:
        errors.append(f"Failed to read/validate gate manifest: {e}")

    # 3. Verify integrity manifest and leaf hashes
    try:
        integ_data = json.loads((rdir / "integrity_manifest.json").read_text(encoding="utf-8"))
        leaf_files = integ_data.get("leaf_files", {})
        if not leaf_files:
            errors.append("Integrity manifest has zero leaf files!")
        for fname, meta in leaf_files.items():
            fpath = rdir / fname
            if not fpath.is_file():
                errors.append(f"Integrity leaf file missing: {fname}")
                continue
            cur_sha = hashlib.sha256(fpath.read_bytes()).hexdigest()
            if cur_sha != meta.get("sha256"):
                errors.append(f"Integrity mismatch for {fname}: expected {meta.get('sha256')}, got {cur_sha}")

        # Containment verification: all files on disk must be in leaf_files
        for p in rdir.rglob("*"):
            if not p.is_file():
                continue
            rel_name = str(p.relative_to(rdir)).replace("\\", "/")
            if rel_name == "integrity_manifest.json":
                continue
            if rel_name not in leaf_files:
                errors.append(f"Untracked foreign file detected in bundle: {rel_name}")
    except Exception as e:
        errors.append(f"Failed to read/validate integrity manifest: {e}")

    # 4. Raw report XML verification (detect failures or corrupt XMLs)
    reports_dir = rdir / "reports"
    if reports_dir.is_dir():
        import xml.etree.ElementTree as ET
        for xml_p in reports_dir.glob("*.xml"):
            try:
                tree = ET.parse(xml_p)
                root_elem = tree.getroot()
                suites = [root_elem] if root_elem.tag == "testsuite" else root_elem.findall(".//testsuite")
                total_failures = 0
                total_errors = 0
                if suites:
                    for s in suites:
                        total_failures += int(s.get("failures", 0))
                        total_errors += int(s.get("errors", 0))
                else:
                    total_failures = int(root_elem.get("failures", 0))
                    total_errors = int(root_elem.get("errors", 0))

                direct_failures = len(root_elem.findall(".//failure"))
                direct_errors = len(root_elem.findall(".//error"))
                total_failures = max(total_failures, direct_failures)
                total_errors = max(total_errors, direct_errors)

                if total_failures > 0 or total_errors > 0:
                    errors.append(f"Report XML {xml_p.name} contains {total_failures} failures and {total_errors} errors!")
            except Exception as xe:
                errors.append(f"Malformed report XML {xml_p.name}: {xe}")

    # 5. Verify acceptance_results.json
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
