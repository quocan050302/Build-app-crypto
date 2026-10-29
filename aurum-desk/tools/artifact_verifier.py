"""
tools/artifact_verifier.py
AURUM DESK V17.8.0.4 — Acceptance Artifact & Git-Tracked Source Integrity Verifier
(V17.7.4 Section 11/12.1 & V17.8.0.4 Sections 3, 4, 14, 20)
Builds Git-tracked source manifests, writes atomic run-scoped acceptance artifacts,
and strictly verifies both local and remote bundle integrity with fail-closed semantics.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT_DIR = Path(__file__).resolve().parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from tools.acceptance_verdict import (
    determine_release_verdict,
    MANDATORY_GATES_V17_7_4,
    MANDATORY_GATES_V17_8,
    MANDATORY_GATES_V17_8_0_2,
    MANDATORY_GATES_V17_8_0_3,
    MANDATORY_GATES_V17_8_0_4,
    MANDATORY_GATES_V17_9,
    MANDATORY_GATES_V17_9_0,
)

EXECUTABLE_SOURCE_SCOPES: Tuple[str, ...] = (
    "backend",
    "frontend",
    "tools",
    ".gitignore",
    ".env.example",
    "_dev_tools.py",
    "run.ps1",
    "run.sh",
)

EXCLUDE_PATTERNS = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    "node_modules",
    "dist",
    "dist-ssr",
    "venv",
    ".aurum_test_owned",
    "artifacts",
    "reports",
    "logs",
    "test-results",
    "playwright-report",
    "tmp",
}

FORBIDDEN_FILENAMES = {
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
}

FORBIDDEN_PATH_PREFIXES = (
    "backend/lab/data/",
    "backend/lab/artifacts/",
    "docs/artifacts/",
    "backtest/",
    "research_artifacts/",
    "frontend/node_modules/",
    "frontend/dist/",
    "frontend/test-results/",
    "tmp/",
)

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


def _is_forbidden_in_source_manifest(rel_path: str) -> Optional[str]:
    """
    Returns a non-empty reason string if `rel_path` is forbidden inside `source_manifest.json`
    (Section 4.2: .DS_Store, Thumbs.db, caches, node_modules, dist, test-results, local databases,
    runtime logs, temporary files, generated acceptance artifacts, untracked lab data).
    """
    norm = rel_path.replace("\\", "/").lstrip("/")
    parts = norm.split("/")
    fname = parts[-1] if parts else norm

    if fname in FORBIDDEN_FILENAMES or fname.startswith(".DS_Store"):
        return f"FORBIDDEN_OS_METADATA_FILE:{fname}"
    for prefix in FORBIDDEN_PATH_PREFIXES:
        if norm.startswith(prefix):
            return f"FORBIDDEN_GENERATED_OR_DATA_PATH:{prefix}"
    for part in parts:
        if part in EXCLUDE_PATTERNS:
            return f"FORBIDDEN_EXCLUDED_DIRECTORY:{part}"
    suffix = Path(fname).suffix.lower()
    if suffix in EXCLUDE_SUFFIXES or ".bak" in fname or fname.startswith("aurum_desk.db"):
        return f"FORBIDDEN_FILE_EXTENSION_OR_DB:{fname}"
    return None


def _read_git_commit_blobs(
    root: Path,
    commit_sha: str,
    scopes: Tuple[str, ...] = EXECUTABLE_SOURCE_SCOPES,
) -> Dict[str, bytes]:
    """
    Reads the authoritative tracked file inventory and exact committed blob bytes from Git
    for `<commit_sha>` across `scopes`.
    """
    cmd = ["git", "ls-tree", "-r", "-z", commit_sha, "--", *scopes]
    out = subprocess.check_output(cmd, cwd=str(root))
    raw_entries = [e.decode("utf-8") for e in out.split(b"\x00") if e]

    entries: List[Tuple[str, str]] = []
    for item in raw_entries:
        if "\t" not in item:
            continue
        meta, rel_path = item.split("\t", 1)
        rel_norm = rel_path.replace("\\", "/")
        if _is_forbidden_in_source_manifest(rel_norm) is not None:
            continue
        meta_parts = meta.split()
        if len(meta_parts) >= 3 and meta_parts[1] == "blob":
            entries.append((meta_parts[2], rel_norm))

    if not entries:
        return {}

    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=str(root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    req_bytes = "".join(f"{blob_sha}\n" for blob_sha, _ in entries).encode("utf-8")
    stdout_data, _ = proc.communicate(req_bytes)
    if proc.returncode != 0:
        raise RuntimeError(f"git cat-file --batch failed with code {proc.returncode}")

    blobs: Dict[str, bytes] = {}
    offset = 0
    for blob_sha, rel_norm in entries:
        nl = stdout_data.index(b"\n", offset)
        header = stdout_data[offset:nl].decode("utf-8")
        header_parts = header.split()
        if len(header_parts) < 3 or header_parts[1] != "blob":
            raise RuntimeError(f"Unexpected git cat-file header for {rel_norm}: {header}")
        size = int(header_parts[2])
        blob_bytes = stdout_data[nl + 1 : nl + 1 + size]
        offset = nl + 1 + size + 1
        blobs[rel_norm] = blob_bytes

    return blobs


def _normalize_line_endings_if_text(data: bytes) -> bytes:
    """Normalizes CRLF to LF for text files (files without NUL bytes) for cross-OS reproducibility."""
    if b"\x00" in data:
        return data
    return data.replace(b"\r\n", b"\n")


def _compute_file_manifest_digest(rel_path: str, content_bytes: bytes) -> str:
    """Computes deterministic SHA-256 over normalized relative path and canonical LF-normalized text/binary bytes."""
    canonical = _normalize_line_endings_if_text(content_bytes)
    return hashlib.sha256(rel_path.encode("utf-8") + b"\x00" + canonical).hexdigest()


def _check_worktree_matches_blobs(root: Path, committed_blobs: Dict[str, bytes]) -> List[str]:
    """
    Compares local worktree files against `committed_blobs`.
    Returns a list of error strings for any missing or modified tracked file.
    """
    diffs: List[str] = []
    for rel_norm in sorted(committed_blobs.keys()):
        disk_path = root / rel_norm
        if not disk_path.is_file():
            diffs.append(f"MISSING_IN_WORKTREE:{rel_norm}")
            continue
        try:
            disk_bytes = disk_path.read_bytes()
        except Exception as exc:
            diffs.append(f"UNREADABLE_IN_WORKTREE:{rel_norm}:{exc}")
            continue
        blob_bytes = committed_blobs[rel_norm]
        if disk_bytes != blob_bytes:
            if _normalize_line_endings_if_text(disk_bytes) != _normalize_line_endings_if_text(blob_bytes):
                diffs.append(f"MODIFIED_IN_WORKTREE:{rel_norm}")
    return diffs


def _has_git_repo(root: Path) -> bool:
    return (root / ".git").exists() or (root.parent / ".git").exists()


def build_source_manifest(
    root_dir: Path,
    commit_sha: Optional[str] = None,
    require_clean_worktree: bool = False,
) -> Dict[str, Any]:
    """
    Computes deterministic SHA-256 source manifest across tracked executable source files
    in `backend/`, `frontend/`, `tools/`, and root scripts/config (Section 4 / DEF-17803-06).
    Uses Git's committed tree inventory (`git ls-tree`) when `.git` is present so untracked
    local files, `.DS_Store`, and ignored caches are never included.
    """
    root = Path(root_dir).resolve()
    manifest: Dict[str, str] = {}

    if _has_git_repo(root):
        eff_sha = commit_sha
        if not eff_sha:
            eff_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=str(root),
                text=True,
            ).strip()

        committed_blobs = _read_git_commit_blobs(root, eff_sha)
        if not committed_blobs:
            raise RuntimeError(f"CRITICAL: Git tree for {eff_sha} returned zero tracked source files!")

        worktree_diffs = _check_worktree_matches_blobs(root, committed_blobs)
        if require_clean_worktree and worktree_diffs:
            raise RuntimeError(
                f"CRITICAL: Local worktree differs from committed source {eff_sha}: {worktree_diffs[:10]}"
            )

        for rel_norm in sorted(committed_blobs.keys()):
            manifest[rel_norm] = _compute_file_manifest_digest(rel_norm, committed_blobs[rel_norm])

        sorted_hashes = "".join(f"{k}:{manifest[k]};" for k in sorted(manifest.keys()))
        composite_sha = hashlib.sha256(sorted_hashes.encode("utf-8")).hexdigest()

        return {
            "schema_version": 2,
            "source_mode": "GIT_COMMIT_TREE",
            "tested_source_sha": eff_sha,
            "commit_sha": eff_sha,
            "files_count": len(manifest),
            "untracked_files_included": 0,
            "worktree_matches_commit": len(worktree_diffs) == 0,
            "worktree_clean": len(worktree_diffs) == 0,
            "worktree_diverged_files": worktree_diffs,
            "worktree_mismatches": worktree_diffs,
            "composite_sha": composite_sha,
            "files": manifest,
        }

    # Non-git fallback for isolated unit-test directories
    scan_dirs = ["backend", "tools"]
    frontend_dir = root / "frontend"
    if frontend_dir.is_dir():
        scan_dirs.append("frontend")

    for sub in scan_dirs:
        sub_path = root / sub
        if not sub_path.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(sub_path):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_PATTERNS and not d.startswith(".")]
            for fname in sorted(filenames):
                if fname.startswith(".") and fname not in (".gitignore", ".env.example"):
                    continue
                p = Path(dirpath) / fname
                rel_str = str(p.relative_to(root)).replace("\\", "/")
                if _is_forbidden_in_source_manifest(rel_str) is not None:
                    continue
                try:
                    content = p.read_bytes()
                    manifest[rel_str] = _compute_file_manifest_digest(rel_str, content)
                except Exception as e:
                    raise RuntimeError(f"CRITICAL: Failed to read source file {p}: {e}")

    for conf_file in (".gitignore", ".env.example", "_dev_tools.py", "run.ps1", "run.sh"):
        conf_p = root / conf_file
        if conf_p.is_file():
            rel_str = str(conf_p.relative_to(root)).replace("\\", "/")
            if _is_forbidden_in_source_manifest(rel_str) is None:
                manifest[rel_str] = _compute_file_manifest_digest(rel_str, conf_p.read_bytes())

    if not manifest:
        raise RuntimeError("CRITICAL: Source manifest is empty!")

    sorted_hashes = "".join(f"{k}:{manifest[k]};" for k in sorted(manifest.keys()))
    composite_sha = hashlib.sha256(sorted_hashes.encode("utf-8")).hexdigest()

    return {
        "schema_version": 2,
        "source_mode": "FILESYSTEM_FALLBACK",
        "files_count": len(manifest),
        "untracked_files_included": 0,
        "worktree_matches_commit": True,
        "composite_sha": composite_sha,
        "files": manifest,
    }


def verify_source_manifest_against_git(
    source_manifest: Dict[str, Any],
    root_dir: Optional[Path] = None,
    expected_commit_sha: Optional[str] = None,
    check_worktree_clean: bool = True,
    require_clean_worktree: Optional[bool] = None,
) -> Tuple[bool, List[str]]:
    """
    Independently verifies `source_manifest` against the committed Git source tree and local worktree.
    Enforces all 7 mandatory negative controls from Section 4.3:
    1. A tracked source file changes.
    2. A required tracked file is omitted.
    3. A file hash is incorrect.
    4. A fake/untracked file appears in the manifest.
    5. An ignored `.DS_Store` or forbidden file is included.
    6. A manifest claims a different commit.
    7. The local worktree differs from the declared tested code.
    """
    errors: List[str] = []
    root = Path(root_dir).resolve() if root_dir else _ROOT_DIR
    eff_clean_check = check_worktree_clean if require_clean_worktree is None else bool(require_clean_worktree)

    if not isinstance(source_manifest, dict):
        return False, ["Source manifest is not a dictionary"]

    sm_files = source_manifest.get("files")
    if not isinstance(sm_files, dict) or not sm_files:
        return False, ["Source manifest 'files' is empty or invalid"]

    # Control 5: Check for forbidden files (.DS_Store, Thumbs.db, lab/data, etc.)
    for rel_path in sorted(sm_files.keys()):
        forb = _is_forbidden_in_source_manifest(rel_path)
        if forb is not None:
            errors.append(f"FORBIDDEN_FILES_IN_MANIFEST: {rel_path} ({forb})")

    if int(source_manifest.get("untracked_files_included", 0)) != 0:
        errors.append(
            f"UNTRACKED_FILES_IN_MANIFEST: untracked_files_included={source_manifest.get('untracked_files_included')}"
        )

    if source_manifest.get("worktree_matches_commit") is False or source_manifest.get("worktree_clean") is False:
        errors.append(
            f"DIRTY_WORKTREE_DETECTED: WORKTREE_DIFFERS_FROM_DECLARED_COMMIT: {source_manifest.get('worktree_diverged_files') or source_manifest.get('worktree_mismatches') or []}"
        )

    # Check composite_sha mathematical consistency
    sorted_hashes = "".join(f"{k}:{sm_files[k]};" for k in sorted(sm_files.keys()))
    recalc_comp = hashlib.sha256(sorted_hashes.encode("utf-8")).hexdigest()
    rec_comp = source_manifest.get("composite_sha")
    if rec_comp != recalc_comp:
        errors.append(
            f"SOURCE_MANIFEST_COMPOSITE_SHA_MISMATCH: recorded={rec_comp}, recomputed={recalc_comp}"
        )

    if source_manifest.get("files_count", len(sm_files)) != len(sm_files):
        errors.append(
            f"SOURCE_MANIFEST_FILES_COUNT_MISMATCH: files_count={source_manifest.get('files_count')} != len={len(sm_files)}"
        )

    # Control 6: Commit SHA verification
    manifest_sha = source_manifest.get("tested_source_sha") or source_manifest.get("commit_sha")
    if expected_commit_sha and manifest_sha and manifest_sha != expected_commit_sha:
        errors.append(
            f"SOURCE_MANIFEST_COMMIT_SHA_MISMATCH: manifest tested_source_sha={manifest_sha} != expected={expected_commit_sha}"
        )

    target_sha = expected_commit_sha or manifest_sha
    if _has_git_repo(root):
        try:
            if not target_sha:
                target_sha = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(root),
                    text=True,
                ).strip()
            committed_blobs = _read_git_commit_blobs(root, target_sha)
        except Exception as exc:
            errors.append(f"GIT_COMMIT_LOOKUP_FAILED for {target_sha}: {exc}")
            return False, errors

        expected_files: Dict[str, str] = {
            rel: _compute_file_manifest_digest(rel, blob)
            for rel, blob in committed_blobs.items()
        }

        # Control 2: Required tracked file omitted
        omitted = sorted(set(expected_files.keys()) - set(sm_files.keys()))
        if omitted:
            errors.append(
                f"MISSING_TRACKED_FILES_IN_MANIFEST / OMITTED_TRACKED_SOURCE_FILES ({len(omitted)}): {omitted[:5]}"
            )

        # Control 4: Fake or untracked file included in manifest
        extra = sorted(set(sm_files.keys()) - set(expected_files.keys()))
        if extra:
            errors.append(
                f"UNTRACKED_FILES_IN_MANIFEST / FAKE_OR_UNTRACKED_FILE_IN_MANIFEST ({len(extra)}): {extra[:5]}"
            )

        # Control 3: File hash mismatch against committed Git blob
        hash_mismatches: List[str] = []
        for rel, exp_digest in expected_files.items():
            if rel in sm_files and sm_files[rel] != exp_digest:
                hash_mismatches.append(rel)
        if hash_mismatches:
            errors.append(
                f"HASH_MISMATCHES_AGAINST_COMMIT / SOURCE_FILE_HASH_MISMATCH ({len(hash_mismatches)}): {hash_mismatches[:5]}"
            )

        # Control 1 & 7: Local worktree differs from committed source
        if eff_clean_check:
            worktree_diffs = _check_worktree_matches_blobs(root, committed_blobs)
            if worktree_diffs:
                errors.append(
                    f"DIRTY_WORKTREE_DETECTED / WORKTREE_DIFFERS_FROM_DECLARED_COMMIT ({len(worktree_diffs)}): {worktree_diffs[:5]}"
                )

    return len(errors) == 0, errors


def write_acceptance_artifacts(
    run_id: str,
    results_payload: Dict[str, Any],
    source_manifest: Dict[str, Any],
    gate_manifest: List[str],
    artifacts_base_dir: Path,
    version_dir: str = "v17_7_4",
) -> Path:
    """
    Writes run-scoped acceptance artifacts under docs/artifacts/<version_dir>/<run_id>/ (Section 11.5).
    Preserves historical files without overwrite.
    """
    base_dir = Path(artifacts_base_dir).resolve()
    run_dir = base_dir / version_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write gate_manifest.json and gate_results.json
    (run_dir / "gate_manifest.json").write_text(
        json.dumps({"schema_version": 1, "run_id": run_id, "mandatory_gates": gate_manifest}, indent=2),
        encoding="utf-8",
    )
    (run_dir / "gate_results.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "runner_version": results_payload.get("runner_version"),
                "tested_source_sha": results_payload.get("tested_source_sha") or results_payload.get("source_sha"),
                "mandatory_gates": gate_manifest,
                "gates": results_payload.get("gates", {}),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # 2. Write source_manifest.json
    (run_dir / "source_manifest.json").write_text(
        json.dumps(source_manifest, indent=2),
        encoding="utf-8",
    )

    # 3. Write acceptance_results.json and acceptance_summary.json
    (run_dir / "acceptance_results.json").write_text(
        json.dumps(results_payload, indent=2),
        encoding="utf-8",
    )
    (run_dir / "acceptance_summary.json").write_text(
        json.dumps(results_payload, indent=2),
        encoding="utf-8",
    )

    # 4. Compute integrity manifest over all written leaf files (including reports and logs)
    integrity_digests = {}
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file():
            continue
        rel_name = str(p.relative_to(run_dir)).replace("\\", "/")
        if rel_name in ("integrity_manifest.json", "artifact_hashes.json"):
            continue
        integrity_digests[rel_name] = {
            "size_bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }

    artifact_hashes_payload = {
        "schema_version": 1,
        "run_id": run_id,
        "leaf_files": dict(integrity_digests),
    }
    artifact_hashes_path = run_dir / "artifact_hashes.json"
    artifact_hashes_path.write_text(
        json.dumps(artifact_hashes_payload, indent=2),
        encoding="utf-8",
    )
    integrity_digests["artifact_hashes.json"] = {
        "size_bytes": artifact_hashes_path.stat().st_size,
        "sha256": hashlib.sha256(artifact_hashes_path.read_bytes()).hexdigest(),
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
    expected_gate_manifest: Optional[List[str]] = None,
    strict_git_source: bool = False,
    root_dir: Optional[Path] = None,
) -> Tuple[bool, List[str]]:
    """
    Strict verifier for acceptance artifact bundle (Section 11.6, Section 12.1, & V17.8.0.4 Sections 3, 4, 14).
    Validates schema, version, run ID, hashes, reports, raw logs, XML test evidence, Git source manifest,
    and recomputed verdict.
    Fails closed with `ACCEPTANCE_EVIDENCE_INCOMPLETE` if any mandatory log or leaf file is missing.
    """
    errors: List[str] = []
    rdir = Path(run_dir).resolve()
    repo_root = Path(root_dir).resolve() if root_dir else _ROOT_DIR

    if not rdir.is_dir():
        return False, [f"Run directory does not exist: {rdir}"]

    # Required files
    req_files = ["integrity_manifest.json", "acceptance_results.json", "source_manifest.json", "gate_manifest.json"]
    for req in req_files:
        if not (rdir / req).is_file():
            errors.append(f"Missing required artifact (ACCEPTANCE_EVIDENCE_INCOMPLETE): {req}")

    if errors:
        return False, errors

    # 1. Verify source manifest non-emptiness and composite SHA
    sm_data: Dict[str, Any] = {}
    try:
        sm_data = json.loads((rdir / "source_manifest.json").read_text(encoding="utf-8"))
        sm_files = sm_data.get("files", {})
        files_count = sm_data.get("files_count", len(sm_files))
        if not sm_files or files_count <= 0:
            errors.append("Source manifest is empty or has zero files!")
        for rel_p in sm_files:
            forb = _is_forbidden_in_source_manifest(rel_p)
            if forb is not None:
                errors.append(f"FORBIDDEN_FILE_IN_SOURCE_MANIFEST: {rel_p} ({forb})")
        sorted_hashes = "".join(f"{k}:{sm_files[k]};" for k in sorted(sm_files.keys()))
        recalc_comp = hashlib.sha256(sorted_hashes.encode("utf-8")).hexdigest()
        simple_hashes = "".join(sm_files[k] for k in sorted(sm_files.keys()))
        simple_comp = hashlib.sha256(simple_hashes.encode("utf-8")).hexdigest()
        rec_comp = sm_data.get("composite_sha")
        if rec_comp not in (recalc_comp, simple_comp):
            errors.append(f"Source manifest composite SHA mismatch: recorded {rec_comp}")

        if strict_git_source or sm_data.get("source_mode") == "GIT_COMMIT_TREE":
            git_ok, git_errs = verify_source_manifest_against_git(
                sm_data,
                root_dir=repo_root,
                expected_commit_sha=expected_source_sha,
                check_worktree_clean=strict_git_source,
            )
            if not git_ok:
                errors.extend(git_errs)
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
    leaf_files: Dict[str, Any] = {}
    try:
        integ_data = json.loads((rdir / "integrity_manifest.json").read_text(encoding="utf-8"))
        leaf_files = integ_data.get("leaf_files", {})
        if not leaf_files:
            errors.append("Integrity manifest has zero leaf files!")
        for fname, meta in leaf_files.items():
            fpath = rdir / fname
            if not fpath.is_file():
                errors.append(f"Integrity leaf file missing (ACCEPTANCE_EVIDENCE_INCOMPLETE): {fname}")
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

    # 5. Verify acceptance_results.json and per-gate log/JUnit evidence
    try:
        results = json.loads((rdir / "acceptance_results.json").read_text(encoding="utf-8"))
        if results.get("runner_version") != expected_version:
            errors.append(f"Version mismatch: expected {expected_version}, got {results.get('runner_version')}")

        if expected_run_id and results.get("run_id") != expected_run_id:
            errors.append(f"Run ID mismatch: expected {expected_run_id}, got {results.get('run_id')}")

        rec_sha = results.get("tested_source_sha") or results.get("source_sha")
        if expected_source_sha and rec_sha != expected_source_sha:
            errors.append(f"Source SHA mismatch: expected {expected_source_sha}, got {rec_sha}")

        gates = results.get("gates", {})
        eval_manifest = expected_gate_manifest if expected_gate_manifest is not None else MANDATORY_GATES_V17_7_4

        # Verify gate log_path and junit_path / report_path exist when declared (DEF-17803-05)
        for gid, gdata in gates.items():
            if not isinstance(gdata, dict):
                continue
            for path_key in ("log_path", "junit_path", "report_path"):
                rel_p = gdata.get(path_key)
                if not rel_p:
                    continue
                norm_p = str(rel_p).replace("\\", "/")
                candidate_in_run = rdir / norm_p
                candidate_in_repo = repo_root / norm_p
                if not candidate_in_run.is_file() and not candidate_in_repo.is_file():
                    errors.append(
                        f"ACCEPTANCE_EVIDENCE_INCOMPLETE: Gate {gid} {path_key} missing on disk: {norm_p}"
                    )

        recomputed = determine_release_verdict(gates, eval_manifest)
        recorded_verdict = results.get("verdict")
        if recorded_verdict != recomputed:
            errors.append(f"Verdict inconsistency: recorded '{recorded_verdict}', recomputed '{recomputed}'")
        if recomputed != "ACCEPTED_FOR_PAPER_OPT_IN":
            errors.append(f"Bundle recomputed verdict is not ACCEPTED_FOR_PAPER_OPT_IN: {recomputed}")
    except Exception as e:
        errors.append(f"Failed to parse/validate acceptance results: {e}")

    return len(errors) == 0, errors


def verify_remote_acceptance_bundle(
    run_dir: Path,
    remote_ref: str = "HEAD",
    root_dir: Optional[Path] = None,
    expected_version: str = "17.8.0.4",
) -> Tuple[bool, List[str]]:
    """
    Verifies that every mandatory artifact in `run_dir` (including all `logs/*.log` and `reports/*.xml`
    in `integrity_manifest.json`) is NOT ignored by `.gitignore` and is tracked and retrievable
    from the Git commit `<remote_ref>` with matching SHA-256 hashes (Section 3 DEF-17803-05 & Section 20 Step 10).
    """
    repo_root = Path(root_dir).resolve() if root_dir else _ROOT_DIR
    rdir = Path(run_dir).resolve()

    ok_local, errors = verify_acceptance_bundle(
        rdir,
        expected_version=expected_version,
        strict_git_source=True,
        root_dir=repo_root,
    )

    try:
        rel_run_dir = str(rdir.relative_to(repo_root)).replace("\\", "/")
    except ValueError:
        return False, errors + [f"Run directory {rdir} is not inside repository root {repo_root}"]

    # 1. Check .gitignore does not ignore logs inside run_dir
    logs_dir = rdir / "logs"
    if logs_dir.is_dir():
        log_files = [str(p.relative_to(repo_root)).replace("\\", "/") for p in sorted(logs_dir.glob("*.log"))]
        if log_files:
            proc = subprocess.run(
                ["git", "check-ignore", "--", *log_files],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                ignored = proc.stdout.strip().splitlines()
                errors.append(
                    f"GITIGNORE_BLOCKS_ACCEPTANCE_LOGS ({len(ignored)}): {ignored[:5]}"
                )

    # 2. Verify every leaf file in integrity_manifest.json exists in Git tree `<remote_ref>`
    integ_path = rdir / "integrity_manifest.json"
    if not integ_path.is_file():
        return False, errors + ["Missing integrity_manifest.json"]

    integ_data = json.loads(integ_path.read_text(encoding="utf-8"))
    leaf_files = integ_data.get("leaf_files", {})

    try:
        out = subprocess.check_output(
            ["git", "ls-tree", "-r", "-z", remote_ref, "--", rel_run_dir],
            cwd=str(repo_root),
        )
        tree_entries = [e.decode("utf-8").split("\t", 1)[1].replace("\\", "/") for e in out.split(b"\x00") if b"\t" in e]
        tracked_in_ref = set(tree_entries)
    except Exception as exc:
        return False, errors + [f"Failed to query git ls-tree for {remote_ref}: {exc}"]

    missing_in_remote: List[str] = []
    for rel_leaf in sorted(leaf_files.keys()):
        full_rel = f"{rel_run_dir}/{rel_leaf}"
        if full_rel not in tracked_in_ref:
            missing_in_remote.append(rel_leaf)

    if f"{rel_run_dir}/integrity_manifest.json" not in tracked_in_ref:
        missing_in_remote.append("integrity_manifest.json")

    if missing_in_remote:
        errors.append(
            f"ACCEPTANCE_EVIDENCE_INCOMPLETE_IN_GIT_TREE ({len(missing_in_remote)} missing in {remote_ref}): {missing_in_remote[:10]}"
        )

    return len(errors) == 0, errors


def verify_remote_branch_artifacts(
    root_dir: Path,
    remote_ref: str,
    run_id: str,
    version_dir: str = "v17_9_0",
    expected_version: str = "17.9.0",
) -> Dict[str, Any]:
    """
    Verifies the remote acceptance artifact bundle for `run_id` at `remote_ref` (V17.9.0 Section 19 Step 7).
    Raises RuntimeError if verification fails.
    """
    repo_root = Path(root_dir).resolve()
    run_dir = repo_root / "docs" / "artifacts" / version_dir / run_id
    ok, errors = verify_remote_acceptance_bundle(
        run_dir=run_dir,
        remote_ref=remote_ref,
        root_dir=repo_root,
        expected_version=expected_version,
    )
    report = {
        "status": "PASS" if ok else "FAIL",
        "run_id": run_id,
        "remote_ref": remote_ref,
        "run_dir": str(run_dir),
        "errors": errors,
    }
    if not ok:
        raise RuntimeError(f"REMOTE_ACCEPTANCE_BUNDLE_INCOMPLETE ({run_id} @ {remote_ref}): {errors}")
    return report


def main(argv: Optional[List[str]] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    remote_ref: Optional[str] = None
    filtered_args: List[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--remote-ref" and i + 1 < len(args):
            remote_ref = args[i + 1]
            i += 2
        else:
            filtered_args.append(args[i])
            i += 1

    if filtered_args:
        target = Path(filtered_args[0])
        if not target.is_absolute():
            if (Path.cwd() / target).exists():
                target = (Path.cwd() / target).resolve()
            else:
                target = (_ROOT_DIR / target).resolve()
    else:
        latest_file = _ROOT_DIR / "docs" / "artifacts" / "v17_9_0" / "latest_run.json"
        if not latest_file.is_file():
            latest_file = _ROOT_DIR / "docs" / "artifacts" / "v17_8_0_4" / "latest_run.json"
        if not latest_file.is_file():
            latest_file = _ROOT_DIR / "docs" / "artifacts" / "v17_8_0_3" / "latest_run.json"
        if not latest_file.is_file():
            latest_file = _ROOT_DIR / "docs" / "artifacts" / "v17_8_0_2" / "latest_run.json"
        if not latest_file.is_file():
            print(json.dumps({"status": "FAIL", "errors": [f"Missing latest_run.json: {latest_file}"]}, indent=2))
            return 1
        latest_data = json.loads(latest_file.read_text(encoding="utf-8"))
        target = (_ROOT_DIR / latest_data["run_directory"]).resolve()

    if not target.is_dir():
        print(json.dumps({"status": "FAIL", "errors": [f"Run directory does not exist: {target}"]}, indent=2))
        return 1

    res_path = target / "acceptance_results.json"
    gm_path = target / "gate_manifest.json"
    integ_path = target / "integrity_manifest.json"

    is_v17_9 = "v17_9_0" in str(target).replace("\\", "/")
    expected_ver = "17.9.0" if is_v17_9 else "17.8.0.4"
    expected_run = target.name
    expected_sha = None
    expected_manifest = list(MANDATORY_GATES_V17_9_0) if is_v17_9 else list(MANDATORY_GATES_V17_8_0_4)

    if res_path.is_file():
        try:
            res_json = json.loads(res_path.read_text(encoding="utf-8"))
            expected_ver = res_json.get("runner_version", expected_ver)
            expected_run = res_json.get("run_id", expected_run)
            expected_sha = res_json.get("tested_source_sha") or res_json.get("source_sha")
        except Exception:
            pass

    if gm_path.is_file():
        try:
            gm_json = json.loads(gm_path.read_text(encoding="utf-8"))
            if gm_json.get("mandatory_gates"):
                expected_manifest = gm_json["mandatory_gates"]
        except Exception:
            pass

    if remote_ref:
        ok, errors = verify_remote_acceptance_bundle(
            target,
            remote_ref=remote_ref,
            root_dir=_ROOT_DIR,
            expected_version=expected_ver,
        )
    else:
        ok, errors = verify_acceptance_bundle(
            target,
            expected_version=expected_ver,
            expected_run_id=expected_run,
            expected_source_sha=expected_sha,
            expected_gate_manifest=expected_manifest,
        )

    leaf_count = 0
    if integ_path.is_file():
        try:
            integ_json = json.loads(integ_path.read_text(encoding="utf-8"))
            leaf_count = len(integ_json.get("leaf_files", {}))
        except Exception:
            pass

    release_verdict = None
    if res_path.is_file():
        try:
            res_json = json.loads(res_path.read_text(encoding="utf-8"))
            release_verdict = (
                res_json.get("engineering_verdict")
                or res_json.get("release_verdict")
                or res_json.get("verdict")
            )
        except Exception:
            pass

    report = {
        "status": "PASS" if ok else "FAIL",
        "bundle_directory": str(target),
        "runner_version": expected_ver,
        "run_id": expected_run,
        "tested_source_sha": expected_sha,
        "remote_ref_verified": remote_ref,
        "gates_verified": len(expected_manifest),
        "leaf_files_verified": leaf_count,
        "release_verdict": release_verdict,
        "errors": errors,
    }
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
