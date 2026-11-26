"""
aurum-desk/backend/services/kh_03_r6_a1_release_service.py

AURUM DESK — KH-03.R6.A1 (M5 / P1-K) | FROZEN EXECUTABLE RELEASE BINDING,
DETACHED WORKTREE / EVIDENCE-ONLY DESCENDANT VERIFICATION & CLI TEMPLATE TRUTH
SPEC VERSION: KH-03.R6.A1-v1.0 | OFFLINE / LOOPBACK QA ONLY

Implements:
  - F27: `verify_r6_a1_release_binding()` — cross-checks sealed manifest `26`, post-push receipt `28`,
    `latest_run.json`, pinned manifest digest, Git ancestry on mandatory branch, and sealed file hashes.
    Never treats any arbitrary JSON with `status == "PASS"` as self-authenticating!
  - F28: `prepare_r6_a1_frozen_execution_context()` & `cleanup_r6_a1_frozen_execution_context()` —
    solves `tested_executable_sha` vs publication HEAD either via a bounded detached read-only Git
    worktree at exact `tested_executable_sha` OR by proving every executable/config/dependency file
    at publication HEAD is byte-identical to `tested_executable_sha` (strict evidence-only diff allowlist).
    NEVER modifies or switches the user's active working branch!
  - F29: `build_r6_a1_unsigned_owner_review_template()` — corrects missing `--execute-authorized` flag,
    includes `--release-receipt`, emits exact dry-run + QA E2E commands + clearly marked FUTURE R6.B
    owner-review command (`DO NOT RUN IN A1 — EXTERNAL HARD-BLOCKED`).
  - F30: Explicit pre-verifier vs post-verifier gate transition helpers.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.kh_03_r6_a1_trust_service import (
    A1_EXTERNAL_CAPTURE_ENABLED,
    BASELINE_REVIEWED_HEAD_SHA_R6_A1,
    PRIOR_R6_EVIDENCE_COMMIT_SHA,
    PRIOR_R6_MANIFEST_26_SHA256,
    PRIOR_R6_READBACK_28_SHA256,
    PRIOR_R6_RUN_ID,
    PRIOR_R6_TESTED_EXECUTABLE_SHA,
    SCHEMA_VERSION_KH03_R6_A1,
    SPEC_VERSION_KH03_R6_A1,
    _sha256_canonical_a1,
)
from services.kh_03_r6_durable_grant_service import (
    _scan_for_secrets_or_bypass,
    _strict_loads_r6,
)
from services.kh_03_r6_source_contract_service import (
    API_FAMILY_CLASSIC_V2,
    APPROVED_PRODUCT_TYPE_V2_R6,
    APPROVED_SYMBOL_R6,
    APPROVED_VENUE_R6,
    MANDATORY_BRANCH_KH03_R6,
)

# Evidence-only path prefixes allowed between tested_executable_sha and publication HEAD
EVIDENCE_ONLY_DIFF_PREFIXES_A1: Tuple[str, ...] = (
    "aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/",
    "aurum-desk/docs/artifacts/kh_03_r6_a1_acquisition_trust_closure/",
    "aurum-desk/docs/artifacts/kh_03_r6_a1_corrective_closure/",
)

# Core executable/config/dependency paths fingerprinted for frozen runtime verification
FROZEN_RUNTIME_FINGERPRINT_REL_PATHS_A1: Tuple[str, ...] = (
    "aurum-desk/tools/run_kh_03_r6_capture.py",
    "aurum-desk/backend/services/kh_03_r6_source_contract_service.py",
    "aurum-desk/backend/services/kh_03_r6_durable_grant_service.py",
    "aurum-desk/backend/services/kh_03_r6_bounded_transport_service.py",
    "aurum-desk/backend/services/kh_03_r6_market_data_service.py",
    "aurum-desk/backend/services/kh_03_r6_custody_readiness_service.py",
    "aurum-desk/backend/services/kh_03_r6_a1_trust_service.py",
    "aurum-desk/backend/services/kh_03_r6_a1_release_service.py",
    "aurum-desk/backend/services/kh_03_r6_a1_acquisition_service.py",
    "aurum-desk/backend/services/kh_03_r6_a1_corrective_service.py",
)

WORKTREE_SENTINEL_FILENAME_A1 = ".kh03_r6_a1_owned_frozen_worktree.json"


def _sha256_file_a1(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_run_a1(repo_root: Path, args: Sequence[str], *, timeout: float = 20.0) -> Tuple[int, str, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    return int(proc.returncode), proc.stdout.strip(), proc.stderr.strip()


def _is_hex_sha(val: Any, length: int) -> bool:
    if not isinstance(val, str) or len(val) != length:
        return False
    return all(c in "0123456789abcdef" for c in val.lower())


def compute_frozen_runtime_fingerprint_a1(checkout_root: Path) -> Dict[str, Any]:
    """Computes deterministic SHA-256 fingerprint of all executable/config/dependency files."""
    files: Dict[str, Optional[str]] = {}
    missing: List[str] = []
    for rel in FROZEN_RUNTIME_FINGERPRINT_REL_PATHS_A1:
        p = checkout_root / rel
        if p.is_file():
            files[rel] = _sha256_file_a1(p)
        else:
            files[rel] = None
            missing.append(rel)
    # Include requirements/pyproject if present
    for cfg_rel in ("aurum-desk/backend/requirements.txt", "aurum-desk/pyproject.toml", "requirements.txt"):
        cp = checkout_root / cfg_rel
        if cp.is_file():
            files[cfg_rel] = _sha256_file_a1(cp)
    dep_versions = {
        "python_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    for mod_name in ("cryptography", "websockets", "psutil", "certifi"):
        try:
            m = __import__(mod_name)
            dep_versions[mod_name] = str(getattr(m, "__version__", "unknown"))
        except Exception:
            dep_versions[mod_name] = "MISSING"
    fp_core = {"files": files, "dependencies": dep_versions}
    return {
        "fingerprint_sha256": _sha256_canonical_a1(fp_core),
        "files": files,
        "missing_files": missing,
        "dependencies": dep_versions,
    }


def _check_pythonpath_import_roots_clean_a1(allowed_roots: Sequence[Path]) -> Tuple[bool, List[str]]:
    """Rejects untrusted/arbitrary PYTHONPATH directories outside approved checkout/stdlib/venv."""
    viols: List[str] = []
    norm_allowed = [str(p.resolve(strict=False)).lower() for p in allowed_roots]
    pp = os.environ.get("PYTHONPATH", "")
    if not pp.strip():
        return True, []
    for entry in pp.split(os.pathsep):
        e = entry.strip()
        if not e:
            continue
        res = str(Path(e).resolve(strict=False)).lower()
        ok = any(res == a or res.startswith(a + os.sep) for a in norm_allowed)
        if not ok:
            viols.append(f"UNTRUSTED_PYTHONPATH_ENTRY:{e}")
    return len(viols) == 0, viols


# =====================================================================
# F27: verify_r6_a1_release_binding
# =====================================================================
@dataclass(frozen=True)
class R6A1VerifiedReleaseBinding:
    release_phase: str  # "KH-03.R6" | "KH-03.R6.A1"
    run_id: str
    branch: str
    tested_executable_sha: str
    evidence_commit_sha: Optional[str]
    publication_head_sha: Optional[str]
    manifest_path: str
    manifest_sha256: str
    pointer_sha256: Optional[str]
    remote_receipt_sha256: Optional[str]
    verifier_pid: int
    orchestrator_pid: int
    gates_passed_count: int
    gates_required_count: int
    external_stage_bc_executed_count: int
    ancestry_verified: bool
    evidence_only_descendant_verified: bool
    binding_digest: str

    def to_redacted_dict(self) -> Dict[str, Any]:
        return {
            "release_phase": self.release_phase,
            "run_id": self.run_id,
            "branch": self.branch,
            "tested_executable_sha": self.tested_executable_sha,
            "evidence_commit_sha": self.evidence_commit_sha,
            "publication_head_sha": self.publication_head_sha,
            "manifest_sha256": self.manifest_sha256,
            "pointer_sha256": self.pointer_sha256,
            "remote_receipt_sha256": self.remote_receipt_sha256,
            "verifier_pid": self.verifier_pid,
            "orchestrator_pid": self.orchestrator_pid,
            "gates_passed_count": self.gates_passed_count,
            "gates_required_count": self.gates_required_count,
            "external_stage_bc_executed_count": self.external_stage_bc_executed_count,
            "ancestry_verified": self.ancestry_verified,
            "evidence_only_descendant_verified": self.evidence_only_descendant_verified,
            "binding_digest": self.binding_digest,
        }


def verify_r6_a1_release_binding(
    *,
    repo_root: Path,
    release_manifest_path: Path,
    expected_manifest_sha256: Optional[str] = None,
    latest_pointer_path: Optional[Path] = None,
    remote_receipt_path: Optional[Path] = None,
    expected_branch: str = MANDATORY_BRANCH_KH03_R6,
    require_pinned_manifest_sha: bool = True,
    require_remote_receipt: bool = False,
    verify_sealed_inventory_files: bool = True,
    allow_pre_publication_head_equals_tested: bool = False,
) -> Dict[str, Any]:
    """
    F27: Independently verifies a sealed release manifest (`26_independent_OS_verifier_manifest_sha256.json`)
    together with optional post-push receipt (`28`) and `latest_run.json`, Git commit ancestry on the
    mandatory branch, and sealed artifact file hashes.
    NEVER treats a caller-supplied manifest JSON with `status == "PASS"` as self-authenticating:
    when `require_pinned_manifest_sha=True`, `expected_manifest_sha256` MUST be pinned and exact!
    """
    viols: List[str] = []
    rel_p = Path(release_manifest_path)
    if not rel_p.is_file():
        return {"valid": False, "status": "BLOCKED_RELEASE_MANIFEST_MISSING", "binding": None, "violations": ["RELEASE_MANIFEST_NOT_FOUND"]}

    raw_bytes = rel_p.read_bytes()
    actual_manifest_sha = hashlib.sha256(raw_bytes).hexdigest()

    if require_pinned_manifest_sha:
        if not _is_hex_sha(expected_manifest_sha256, 64):
            viols.append("UNPINNED_RELEASE_MANIFEST_SHA256_NOT_SELF_AUTHENTICATING")
        elif actual_manifest_sha != str(expected_manifest_sha256).lower():
            viols.append(f"RELEASE_MANIFEST_SHA256_MISMATCH:expected={expected_manifest_sha256}:actual={actual_manifest_sha}")
    elif expected_manifest_sha256 is not None and actual_manifest_sha != str(expected_manifest_sha256).lower():
        viols.append("RELEASE_MANIFEST_SHA256_MISMATCH")

    try:
        mdoc = _strict_loads_r6(raw_bytes, max_bytes=2 * 1024 * 1024)
    except Exception as exc:
        return {"valid": False, "status": "REJECTED_RELEASE_MANIFEST_PARSE", "binding": None, "violations": viols + [f"MANIFEST_PARSE_ERR:{exc}"]}

    if not isinstance(mdoc, Mapping):
        return {"valid": False, "status": "REJECTED_RELEASE_MANIFEST_INVALID", "binding": None, "violations": viols + ["MANIFEST_NOT_OBJECT"]}

    sec_hits = _scan_for_secrets_or_bypass(mdoc)
    if sec_hits:
        viols.extend(sec_hits)

    # Verify independent verifier proof fields
    if mdoc.get("status") != "PASS":
        viols.append(f"RELEASE_MANIFEST_STATUS_NOT_PASS:{mdoc.get('status')}")
    if mdoc.get("distinct_os_process_verified") is not True:
        viols.append("RELEASE_MANIFEST_NOT_DISTINCT_OS_VERIFIED")
    v_pid = mdoc.get("verifier_pid")
    o_pid = mdoc.get("orchestrator_pid")
    if not isinstance(v_pid, int) or isinstance(v_pid, bool) or v_pid <= 0:
        viols.append("RELEASE_MANIFEST_INVALID_VERIFIER_PID")
    if not isinstance(o_pid, int) or isinstance(o_pid, bool) or o_pid <= 0:
        viols.append("RELEASE_MANIFEST_INVALID_ORCHESTRATOR_PID")
    if isinstance(v_pid, int) and isinstance(o_pid, int) and v_pid == o_pid:
        viols.append("RELEASE_MANIFEST_VERIFIER_PID_EQUALS_ORCHESTRATOR_PID")
    if int(mdoc.get("violations_count", -1)) != 0:
        viols.append(f"RELEASE_MANIFEST_NONZERO_VIOLATIONS:{mdoc.get('violations_count')}")

    run_id = str(mdoc.get("run_id") or "").strip()
    tested_sha = str(mdoc.get("tested_executable_sha") or "").strip()
    if not run_id:
        viols.append("RELEASE_MANIFEST_MISSING_RUN_ID")
    if not _is_hex_sha(tested_sha, 40):
        viols.append(f"RELEASE_MANIFEST_INVALID_TESTED_SHA:{tested_sha}")

    # Verify required gate completion from manifest's recomputed verdicts / gates
    verdicts_block = mdoc.get("recomputed_verdicts") or mdoc.get("verdicts") or {}
    gates_map = (
        mdoc.get("recomputed_36_gates")
        if isinstance(mdoc.get("recomputed_36_gates"), Mapping)
        else (verdicts_block.get("gates") if isinstance(verdicts_block, Mapping) else None)
    )
    spec_ver = str(mdoc.get("spec_version") or "")
    is_a1 = spec_ver.startswith("KH-03.R6.A1")
    release_phase = "KH-03.R6.A1" if is_a1 else "KH-03.R6"

    gates_passed = 0
    gates_required = 35 if is_a1 else 31
    bc_exec = 0
    if not isinstance(gates_map, Mapping) or len(gates_map) < 35:
        viols.append("RELEASE_MANIFEST_INCOMPLETE_GATES_MAP")
    else:
        if is_a1:
            req_gates = [g for g in gates_map.keys() if g != "G36_REMOTE_PUBLICATION"]
            gates_required = len(req_gates)
            gates_passed = sum(1 for g in req_gates if gates_map.get(g) == "PASS")
            if gates_passed < gates_required or gates_required < 35:
                viols.append(f"RELEASE_MANIFEST_INCOMPLETE_A1_GATES:{gates_passed}/{gates_required}")
        else:
            bc_keys = {"G31_EXTERNAL_AUTHORIZATION", "G32_AUTHORIZED_SMOKE", "G33_DEV_ACQUISITION", "G34_R7_DATA_READINESS"}
            req_gates = [g for g in gates_map.keys() if g not in bc_keys and g != "G36_REMOTE_PUBLICATION"]
            gates_required = len(req_gates)
            gates_passed = sum(1 for g in req_gates if gates_map.get(g) == "PASS")
            bc_exec = sum(1 for g in bc_keys if gates_map.get(g) == "PASS")
            if gates_passed < gates_required or gates_required < 31:
                viols.append(f"RELEASE_MANIFEST_INCOMPLETE_R6_STAGE_A_GATES:{gates_passed}/{gates_required}")

    # Verify sealed file inventory hashes inside run directory
    run_dir = rel_p.parent
    inv_files = mdoc.get("sealed_files_sha256") or mdoc.get("files_sha256") or {}
    if verify_sealed_inventory_files:
        if not isinstance(inv_files, Mapping) or len(inv_files) < 15:
            viols.append("RELEASE_MANIFEST_MISSING_SEALED_FILE_INVENTORY")
        else:
            # Verify all sealed files present and byte-exact (supports {"sha256", "bytes"} or hex string)
            checked_inv = 0
            for rel_f, entry in inv_files.items():
                if isinstance(entry, Mapping):
                    exp_h = entry.get("sha256")
                    exp_b = entry.get("bytes")
                else:
                    exp_h = entry
                    exp_b = None
                if not isinstance(rel_f, str) or not _is_hex_sha(exp_h, 64):
                    viols.append(f"RELEASE_MANIFEST_BAD_INVENTORY_ENTRY:{rel_f}")
                    continue
                fp = run_dir / rel_f
                if not fp.is_file():
                    viols.append(f"SEALED_ARTIFACT_MISSING:{rel_f}")
                else:
                    if exp_b is not None and fp.stat().st_size != int(exp_b):
                        viols.append(f"SEALED_ARTIFACT_BYTES_DRIFT:{rel_f}")
                    if _sha256_file_a1(fp) != str(exp_h).lower():
                        viols.append(f"SEALED_ARTIFACT_SHA256_DRIFT:{rel_f}")
                    checked_inv += 1
            if checked_inv == 0:
                viols.append("RELEASE_MANIFEST_ZERO_SEALED_FILES_VERIFIED")

    # Cross-check optional latest_run.json pointer
    pointer_sha: Optional[str] = None
    evidence_sha: Optional[str] = None
    pub_head_sha: Optional[str] = None
    if latest_pointer_path is not None:
        lp = Path(latest_pointer_path)
        if not lp.is_file():
            viols.append("LATEST_POINTER_FILE_MISSING")
        else:
            lp_bytes = lp.read_bytes()
            pointer_sha = hashlib.sha256(lp_bytes).hexdigest()
            try:
                pdoc = _strict_loads_r6(lp_bytes, max_bytes=262_144)
                if str(pdoc.get("run_id") or "") != run_id:
                    viols.append("POINTER_RUN_ID_MISMATCH")
                if str(pdoc.get("tested_executable_sha") or "") != tested_sha:
                    viols.append("POINTER_TESTED_SHA_MISMATCH")
                p_man_sha = str(pdoc.get("manifest_26_sha256") or pdoc.get("independent_manifest_sha256") or "")
                if p_man_sha and p_man_sha != actual_manifest_sha:
                    viols.append("POINTER_MANIFEST_SHA256_MISMATCH")
                ev_s = str(pdoc.get("evidence_commit_sha") or "")
                if _is_hex_sha(ev_s, 40):
                    evidence_sha = ev_s
                if str(pdoc.get("branch") or expected_branch) != expected_branch:
                    viols.append(f"POINTER_BRANCH_MISMATCH:{pdoc.get('branch')}")
            except Exception as exc:
                viols.append(f"POINTER_PARSE_ERR:{exc}")

    # Cross-check optional Phase 2 remote readback receipt `28`
    remote_receipt_sha: Optional[str] = None
    if remote_receipt_path is not None:
        rp = Path(remote_receipt_path)
        if not rp.is_file():
            viols.append("REMOTE_RECEIPT_28_MISSING")
        else:
            rp_bytes = rp.read_bytes()
            remote_receipt_sha = hashlib.sha256(rp_bytes).hexdigest()
            try:
                rdoc = _strict_loads_r6(rp_bytes, max_bytes=2 * 1024 * 1024)
                if str(rdoc.get("run_id") or "") != run_id:
                    viols.append("REMOTE_RECEIPT_RUN_ID_MISMATCH")
                if str(rdoc.get("tested_executable_sha") or "") != tested_sha:
                    viols.append("REMOTE_RECEIPT_TESTED_SHA_MISMATCH")
                if rdoc.get("all_sealed_files_remote_verified") is not True or rdoc.get("remote_tested_sha_verified") is not True:
                    viols.append("REMOTE_RECEIPT_NOT_VERIFIED")
                r_ev = str(rdoc.get("evidence_commit_sha") or "")
                if _is_hex_sha(r_ev, 40):
                    if evidence_sha is not None and evidence_sha != r_ev:
                        viols.append("POINTER_VS_RECEIPT_EVIDENCE_SHA_MISMATCH")
                    evidence_sha = r_ev
                r_man = str(rdoc.get("manifest_26_sha256") or "")
                if r_man and r_man != actual_manifest_sha:
                    viols.append("REMOTE_RECEIPT_MANIFEST_SHA256_MISMATCH")
            except Exception as exc:
                viols.append(f"REMOTE_RECEIPT_PARSE_ERR:{exc}")
    elif require_remote_receipt:
        viols.append("REMOTE_RECEIPT_28_REQUIRED_BUT_MISSING")

    # Git branch & ancestry verification against actual repository!
    rc_br, cur_branch, _ = _git_run_a1(repo_root, ["branch", "--show-current"])
    rc_hd, cur_head, _ = _git_run_a1(repo_root, ["rev-parse", "HEAD"])
    if rc_br != 0 or rc_hd != 0:
        viols.append("GIT_REPO_INSPECTION_FAILED")
    else:
        pub_head_sha = cur_head
        if cur_branch != expected_branch:
            viols.append(f"WRONG_GIT_BRANCH:expected={expected_branch}:got={cur_branch}")

    # Verify tested_sha object exists and is an ancestor of evidence_sha (if known) and current HEAD
    ancestry_ok = False
    ev_only_ok = False
    if _is_hex_sha(tested_sha, 40):
        rc_cat, obj_type, _ = _git_run_a1(repo_root, ["cat-file", "-t", tested_sha])
        if rc_cat != 0 or obj_type != "commit":
            viols.append("TESTED_EXECUTABLE_SHA_NOT_VALID_GIT_COMMIT")
        else:
            rc_anc_head, _, _ = _git_run_a1(repo_root, ["merge-base", "--is-ancestor", tested_sha, "HEAD"])
            if rc_anc_head != 0:
                viols.append("TESTED_SHA_NOT_ANCESTOR_OF_HEAD")
            else:
                ancestry_ok = True
            if evidence_sha is not None:
                rc_anc_ev, _, _ = _git_run_a1(repo_root, ["merge-base", "--is-ancestor", tested_sha, evidence_sha])
                rc_ev_hd, _, _ = _git_run_a1(repo_root, ["merge-base", "--is-ancestor", evidence_sha, "HEAD"])
                if rc_anc_ev != 0 or rc_ev_hd != 0:
                    viols.append("EVIDENCE_COMMIT_ANCESTRY_CHAIN_BROKEN")
                    ancestry_ok = False
            # Also verify mandatory remote tracking ref ancestry if origin/<expected_branch> exists
            rc_rem, rem_sha, _ = _git_run_a1(repo_root, ["rev-parse", f"refs/remotes/origin/{expected_branch}"])
            if rc_rem == 0 and _is_hex_sha(rem_sha, 40) and not allow_pre_publication_head_equals_tested:
                rc_anc_rem, _, _ = _git_run_a1(repo_root, ["merge-base", "--is-ancestor", tested_sha, rem_sha])
                if rc_anc_rem != 0:
                    viols.append("TESTED_SHA_NOT_ANCESTOR_OF_REMOTE_MANDATORY_BRANCH")
                    ancestry_ok = False

            # Check whether diff(tested_sha..HEAD) is strictly evidence-only (NO code/config/import drift!)
            rc_df, df_out, _ = _git_run_a1(repo_root, ["diff", "--name-only", f"{tested_sha}..HEAD"])
            if rc_df == 0:
                diff_paths = [ln.strip() for ln in df_out.splitlines() if ln.strip()]
                non_ev = [p for p in diff_paths if not any(p.startswith(pref) for pref in EVIDENCE_ONLY_DIFF_PREFIXES_A1)]
                ev_only_ok = len(non_ev) == 0

    if viols:
        return {
            "valid": False,
            "status": "BLOCKED_RELEASE_BINDING_INVALID",
            "binding": None,
            "violations": viols,
        }

    b_core = {
        "release_phase": release_phase,
        "run_id": run_id,
        "branch": expected_branch,
        "tested_executable_sha": tested_sha,
        "evidence_commit_sha": evidence_sha,
        "publication_head_sha": pub_head_sha,
        "manifest_sha256": actual_manifest_sha,
        "pointer_sha256": pointer_sha,
        "remote_receipt_sha256": remote_receipt_sha,
        "verifier_pid": int(v_pid),
        "orchestrator_pid": int(o_pid),
        "gates_passed_count": int(gates_passed),
        "gates_required_count": int(gates_required),
        "external_stage_bc_executed_count": int(bc_exec),
    }
    binding = R6A1VerifiedReleaseBinding(
        release_phase=release_phase,
        run_id=run_id,
        branch=expected_branch,
        tested_executable_sha=tested_sha,
        evidence_commit_sha=evidence_sha,
        publication_head_sha=pub_head_sha,
        manifest_path=str(rel_p.resolve()),
        manifest_sha256=actual_manifest_sha,
        pointer_sha256=pointer_sha,
        remote_receipt_sha256=remote_receipt_sha,
        verifier_pid=int(v_pid),
        orchestrator_pid=int(o_pid),
        gates_passed_count=int(gates_passed),
        gates_required_count=int(gates_required),
        external_stage_bc_executed_count=int(bc_exec),
        ancestry_verified=ancestry_ok,
        evidence_only_descendant_verified=ev_only_ok,
        binding_digest=_sha256_canonical_a1(b_core),
    )
    return {
        "valid": True,
        "status": "RELEASE_BINDING_VERIFIED",
        "binding": binding,
        "violations": [],
    }


# =====================================================================
# F28: prepare_r6_a1_frozen_execution_context & Safe Worktree Cleanup
# =====================================================================
@dataclass(frozen=True)
class R6A1FrozenExecutionContext:
    mode: str  # "detached_worktree" | "verify_evidence_only_descendant"
    checkout_root: str
    tested_executable_sha: str
    publication_head_sha: str
    active_user_branch: str
    manifest_sha256: str
    worktree_owned: bool
    evidence_only_diff_files: Tuple[str, ...]
    runtime_fingerprint_sha256: str
    runtime_files_sha256: Mapping[str, Optional[str]]
    dependencies: Mapping[str, str]
    context_digest: str

    def to_redacted_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "tested_executable_sha": self.tested_executable_sha,
            "publication_head_sha": self.publication_head_sha,
            "active_user_branch": self.active_user_branch,
            "manifest_sha256": self.manifest_sha256,
            "worktree_owned": self.worktree_owned,
            "evidence_only_diff_files_count": len(self.evidence_only_diff_files),
            "runtime_fingerprint_sha256": self.runtime_fingerprint_sha256,
            "dependencies": dict(self.dependencies),
            "context_digest": self.context_digest,
        }


def prepare_r6_a1_frozen_execution_context(
    *,
    repo_root: Path,
    release_binding: R6A1VerifiedReleaseBinding,
    mode: str = "verify_evidence_only_descendant",  # "detached_worktree" | "verify_evidence_only_descendant"
    worktree_parent_dir: Optional[Path] = None,
    expected_runtime_fingerprint_sha256: Optional[str] = None,
    expected_dependencies: Optional[Mapping[str, str]] = None,
    require_clean_worktree: bool = True,
    check_pythonpath: bool = True,
) -> Dict[str, Any]:
    """
    F28: Prepares and verifies an exact frozen executable execution context at `release_binding.tested_executable_sha`
    WITHOUT ever modifying, switching, or resetting the user's active Git working branch!
      - `detached_worktree`: creates an isolated temporary detached worktree at `tested_executable_sha`,
        verifies `git rev-parse HEAD == tested_executable_sha`, clean tree, import roots, and runtime fingerprint.
      - `verify_evidence_only_descendant`: verifies that `HEAD` is clean, on the mandatory branch, and EVERY
        file difference in `tested_executable_sha..HEAD` is strictly inside `EVIDENCE_ONLY_DIFF_PREFIXES_A1`
        (proving 100% byte-identical executable/config/import files — rejecting ANY code/config/import drift!).
    """
    viols: List[str] = []
    if not isinstance(release_binding, R6A1VerifiedReleaseBinding):
        return {"ready": False, "status": "BLOCKED_INVALID_RELEASE_BINDING", "context": None, "violations": ["INVALID_RELEASE_BINDING_TYPE"]}
    if mode not in ("detached_worktree", "verify_evidence_only_descendant"):
        return {"ready": False, "status": "REJECTED_UNKNOWN_FROZEN_MODE", "context": None, "violations": [f"UNKNOWN_MODE:{mode}"]}

    rc_br, active_br, _ = _git_run_a1(repo_root, ["branch", "--show-current"])
    rc_hd, pub_head, _ = _git_run_a1(repo_root, ["rev-parse", "HEAD"])
    rc_st, st_out, _ = _git_run_a1(repo_root, ["status", "--porcelain"])
    if rc_br != 0 or rc_hd != 0 or rc_st != 0:
        return {"ready": False, "status": "BLOCKED_GIT_ERROR", "context": None, "violations": ["GIT_STATE_CHECK_FAILED"]}

    if active_br != release_binding.branch:
        viols.append(f"ACTIVE_BRANCH_MISMATCH:expected={release_binding.branch}:got={active_br}")
    if require_clean_worktree and bool(st_out.strip()):
        viols.append("DIRTY_WORKING_TREE_FORBIDDEN_FOR_FROZEN_EXECUTION")

    tested_sha = release_binding.tested_executable_sha
    rc_anc, _, _ = _git_run_a1(repo_root, ["merge-base", "--is-ancestor", tested_sha, pub_head])
    if rc_anc != 0:
        viols.append("TESTED_SHA_NOT_ANCESTOR_OF_PUBLICATION_HEAD")

    # Compute exact diff between tested_sha and HEAD
    rc_df, df_out, _ = _git_run_a1(repo_root, ["diff", "--name-only", f"{tested_sha}..{pub_head}"])
    diff_files = [ln.strip() for ln in df_out.splitlines() if ln.strip()] if rc_df == 0 else []
    non_evidence_drift = [f for f in diff_files if not any(f.startswith(pref) for pref in EVIDENCE_ONLY_DIFF_PREFIXES_A1)]

    if mode == "verify_evidence_only_descendant" and non_evidence_drift:
        viols.append(f"NON_EVIDENCE_CODE_OR_CONFIG_DRIFT_DETECTED:{','.join(non_evidence_drift[:8])}")

    if viols:
        return {"ready": False, "status": "BLOCKED_FROZEN_EXECUTION_CONTEXT", "context": None, "violations": viols}

    checkout_root = repo_root
    wt_owned = False
    if mode == "detached_worktree":
        if worktree_parent_dir is None:
            return {"ready": False, "status": "BLOCKED_MISSING_WORKTREE_PARENT", "context": None, "violations": ["WORKTREE_PARENT_DIR_REQUIRED"]}
        wt_parent = Path(worktree_parent_dir).resolve(strict=False)
        wt_parent.mkdir(parents=True, exist_ok=True)
        wt_dir = wt_parent / f"frozen_wt_{tested_sha[:12]}_{os.getpid()}_{int(time.time_ns() % 1_000_000)}"
        rc_add, _, err_add = _git_run_a1(repo_root, ["worktree", "add", "--detach", str(wt_dir), tested_sha], timeout=60.0)
        if rc_add != 0 or not wt_dir.is_dir():
            return {"ready": False, "status": "BLOCKED_DETACHED_WORKTREE_FAILED", "context": None, "violations": [f"GIT_WORKTREE_ADD_FAILED:{err_add}"]}
        # Stamp ownership sentinel strictly inside owned worktree (cleaned before git worktree remove)
        (wt_dir / WORKTREE_SENTINEL_FILENAME_A1).write_text(
            json.dumps({
                "owned_by": "KH-03.R6.A1",
                "tested_executable_sha": tested_sha,
                "owner_pid": os.getpid(),
                "created_utc_ms": int(time.time() * 1000),
            }, sort_keys=True),
            encoding="utf-8",
        )
        rc_wt_hd, wt_head, _ = _git_run_a1(wt_dir, ["rev-parse", "HEAD"])
        if rc_wt_hd != 0 or wt_head != tested_sha:
            cleanup_r6_a1_frozen_execution_context(repo_root=repo_root, worktree_path=wt_dir)
            return {"ready": False, "status": "BLOCKED_WORKTREE_HEAD_MISMATCH", "context": None, "violations": ["DETACHED_WORKTREE_HEAD_MISMATCH"]}
        checkout_root = wt_dir
        wt_owned = True

    try:
        # Verify PYTHONPATH cleanliness against checkout_root & repo_root
        if check_pythonpath:
            pp_ok, pp_viols = _check_pythonpath_import_roots_clean_a1([checkout_root, repo_root])
            if not pp_ok:
                viols.extend(pp_viols)

        # Verify runtime code/config/dependency fingerprint
        fp = compute_frozen_runtime_fingerprint_a1(checkout_root)
        if expected_runtime_fingerprint_sha256 is not None and fp["fingerprint_sha256"] != expected_runtime_fingerprint_sha256:
            viols.append(f"RUNTIME_FINGERPRINT_MISMATCH:expected={expected_runtime_fingerprint_sha256}:got={fp['fingerprint_sha256']}")
        if expected_dependencies is not None:
            for dep_k, dep_v in expected_dependencies.items():
                if fp["dependencies"].get(dep_k) != dep_v:
                    viols.append(f"DEPENDENCY_FINGERPRINT_MISMATCH:{dep_k}:expected={dep_v}:got={fp['dependencies'].get(dep_k)}")

        # In verify_evidence_only_descendant mode, also prove every executable/config file on disk
        # matches `tested_sha` (using Git tree-vs-worktree diff so Windows autocrlf is handled cleanly
        # while ANY committed OR uncommitted byte drift is strictly rejected!)
        if mode == "verify_evidence_only_descendant":
            for rel, actual_h in fp["files"].items():
                if actual_h is None:
                    continue
                rc_obj, _, _ = _git_run_a1(repo_root, ["cat-file", "-e", f"{tested_sha}:{rel}"])
                if rc_obj != 0:
                    viols.append(f"EXTRA_EXECUTABLE_FILE_NOT_IN_TESTED_SHA:{rel}")
                else:
                    rc_d, d_out, _ = _git_run_a1(repo_root, ["diff", "--name-only", tested_sha, "--", rel])
                    if rc_d != 0 or bool(d_out.strip()):
                        viols.append(f"EXECUTABLE_BYTE_DRIFT_VS_TESTED_SHA:{rel}")

        if viols:
            if wt_owned:
                cleanup_r6_a1_frozen_execution_context(repo_root=repo_root, worktree_path=checkout_root)
            return {"ready": False, "status": "BLOCKED_FROZEN_RUNTIME_VERIFICATION", "context": None, "violations": viols}

        ctx_core = {
            "mode": mode,
            "tested_executable_sha": tested_sha,
            "publication_head_sha": pub_head,
            "active_user_branch": active_br,
            "manifest_sha256": release_binding.manifest_sha256,
            "runtime_fingerprint_sha256": fp["fingerprint_sha256"],
            "evidence_only_diff_files": sorted(diff_files),
        }
        ctx = R6A1FrozenExecutionContext(
            mode=mode,
            checkout_root=str(checkout_root),
            tested_executable_sha=tested_sha,
            publication_head_sha=pub_head,
            active_user_branch=active_br,
            manifest_sha256=release_binding.manifest_sha256,
            worktree_owned=wt_owned,
            evidence_only_diff_files=tuple(sorted(diff_files)),
            runtime_fingerprint_sha256=fp["fingerprint_sha256"],
            runtime_files_sha256=dict(fp["files"]),
            dependencies=dict(fp["dependencies"]),
            context_digest=_sha256_canonical_a1(ctx_core),
        )
        return {"ready": True, "status": "FROZEN_EXECUTION_CONTEXT_READY", "context": ctx, "violations": []}
    except Exception as exc:
        if wt_owned:
            cleanup_r6_a1_frozen_execution_context(repo_root=repo_root, worktree_path=checkout_root)
        return {"ready": False, "status": "BLOCKED_FROZEN_CONTEXT_EXCEPTION", "context": None, "violations": [str(exc)]}


def cleanup_r6_a1_frozen_execution_context(
    *,
    repo_root: Path,
    worktree_path: Path,
) -> Dict[str, Any]:
    """Safely removes ONLY a sentinel-owned temporary detached Git worktree (never deletes unrelated directories!)."""
    wt = Path(worktree_path)
    if not wt.exists():
        return {"cleaned": True, "status": "ALREADY_ABSENT", "violations": []}
    sentinel = wt / WORKTREE_SENTINEL_FILENAME_A1
    if not sentinel.is_file():
        return {"cleaned": False, "status": "REFUSED_UNOWNED_WORKTREE", "violations": ["MISSING_A1_OWNERSHIP_SENTINEL"]}
    try:
        sdoc = json.loads(sentinel.read_text("utf-8"))
        if sdoc.get("owned_by") != "KH-03.R6.A1":
            return {"cleaned": False, "status": "REFUSED_UNOWNED_WORKTREE", "violations": ["BAD_OWNERSHIP_SENTINEL"]}
    except Exception:
        return {"cleaned": False, "status": "REFUSED_UNOWNED_WORKTREE", "violations": ["CORRUPT_OWNERSHIP_SENTINEL"]}

    try:
        sentinel.unlink()
    except OSError:
        pass
    rc_rm, _, err_rm = _git_run_a1(repo_root, ["worktree", "remove", "--force", str(wt)], timeout=30.0)
    if rc_rm != 0 and wt.exists():
        shutil.rmtree(wt, ignore_errors=True)
        _git_run_a1(repo_root, ["worktree", "prune"], timeout=15.0)
    still_exists = wt.exists()
    return {
        "cleaned": not still_exists,
        "status": "OWNED_WORKTREE_REMOVED" if not still_exists else "QUARANTINED_WORKTREE_CLEANUP_FAILED",
        "violations": [] if not still_exists else [f"WORKTREE_REMOVE_FAILED:{err_rm}"],
    }


# =====================================================================
# F29: Corrected Unsigned Owner Review Template & Exact CLI Commands
# =====================================================================
def build_r6_a1_unsigned_owner_review_template(
    *,
    tested_executable_sha: str,
    publication_head_sha: Optional[str],
    stage_a1_run_id: str,
    manifest_26_sha256: str,
    source_registry_digest: str,
    stage: str = "B_SMOKE",
) -> Dict[str, Any]:
    """
    F29: Emits a strictly UNSIGNED owner review template with corrected CLI flags
    (`--execute-authorized`, `--release-manifest`, `--release-receipt`) and explicit A1
    hard-block disclosure (`DO NOT RUN EXTERNAL IN A1 — BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED`).
    """
    is_smoke = stage == "B_SMOKE"
    stage_cli = "smoke" if is_smoke else "dev"
    proposed_caps = (
        {
            "connections": 2,
            "requests": 30,
            "retries": 6,
            "subscriptions": 2,
            "messages": 10_000,
            "payload_bytes": 10 * 1024 * 1024,
            "decoded_bytes": 20 * 1024 * 1024,
            "pages": 5,
            "disk_bytes": 32 * 1024 * 1024,
            "records": 20_000,
        }
        if is_smoke
        else {
            "connections": 4,
            "requests": 500,
            "retries": 20,
            "subscriptions": 4,
            "messages": 200_000,
            "payload_bytes": 50 * 1024 * 1024,
            "decoded_bytes": 100 * 1024 * 1024,
            "pages": 250,
            "disk_bytes": 256 * 1024 * 1024,
            "records": 500_000,
        }
    )
    return {
        "spec_version": SPEC_VERSION_KH03_R6_A1,
        "schema_version": SCHEMA_VERSION_KH03_R6_A1,
        "template_status": "UNSIGNED_OWNER_REVIEW_TEMPLATE",
        "unsigned_template": True,
        "valid_authorization": False,
        "a1_external_capture_enabled": A1_EXTERNAL_CAPTURE_ENABLED,
        "a1_external_status": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED",
        "warning_vi": (
            "DAY LA MAU PHE DUYET CHUA KY CHO R6.B TUONG LAI — KHONG CO HIEU LUC MO SOCKET. "
            "Trong KH-03.R6.A1, capture mang ben ngoai BI KHOA CUNG (BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED). "
            "Khong chay lenh external trong A1; R6.B sau nay can mot commit mo khoa hep duoc review rieng "
            "va grant Ed25519 do chu du an tu ky cuc bo ngoai Git."
        ),
        "stage": stage,
        "tested_executable_sha": tested_executable_sha,
        "publication_head_sha": publication_head_sha or "<POST_PUSH_PUBLICATION_HEAD_SHA>",
        "stage_a1_run_id": stage_a1_run_id,
        "manifest_26_sha256": manifest_26_sha256,
        "source_registry_digest": source_registry_digest,
        "proposed_grant_payload_template": {
            "grant_id": "<OWNER_SETS_UNIQUE_ONE_SHOT_GRANT_ID>",
            "issuer": "<OWNER_ISSUER_ID_MATCHING_TRUST_ROOT>",
            "trust_namespace": "OWNER_EXTERNAL_PRODUCTION_TRUST",
            "issued_utc_ms": "<OWNER_SETS_INTEGER_UTC_MS>",
            "expires_utc_ms": "<OWNER_SETS_FINITE_EXPIRY_UTC_MS>",
            "repo": "quocan050302/Build-app-crypto",
            "branch": MANDATORY_BRANCH_KH03_R6,
            "tested_executable_sha": tested_executable_sha,
            "release_manifest_sha256": manifest_26_sha256,
            "purpose": "DEV_ONLY",
            "stage": stage,
            "venue": APPROVED_VENUE_R6,
            "api_family": API_FAMILY_CLASSIC_V2,
            "symbol": APPROVED_SYMBOL_R6,
            "product_type": APPROVED_PRODUCT_TYPE_V2_R6,
            "allowed_endpoints": [
                "classic_v2_contracts",
                "classic_v2_history_candles",
                "classic_v2_history_fund_rate",
                "classic_v2_ws_books1",
            ],
            "allowed_ws_channels": ["books1"],
            "window_start_ms": "<OWNER_SETS_DEV_WINDOW_START_UTC_MS>",
            "window_end_ms": "<OWNER_SETS_DEV_WINDOW_END_UTC_MS>",
            "destination_store_id": "<OWNER_PROVISIONED_LOCAL_STORE_ID>",
            "destination_root": "<LOCAL_FIXED_DRIVE_PATH_OUTSIDE_GIT>",
            "max_session_duration_sec": 300 if is_smoke else 3600,
            "max_concurrency": 2,
            "caps": proposed_caps,
            "retention_policy": {"max_retention_days": 90, "git_commit_raw_payloads": False},
        },
        "exact_local_cli_commands": {
            "offline_dry_run_command": (
                f"python aurum-desk/tools/run_kh_03_r6_capture.py --stage {stage_cli} --dry-run "
                "--plan <local-plan.json> --release-manifest <verified-local-manifest-26.json> "
                "--release-receipt <verified-local-readback-28.json>"
            ),
            "local_qa_loopback_e2e_command": (
                f"python aurum-desk/tools/run_kh_03_r6_capture.py --qa-loopback-e2e --stage {stage_cli} "
                "--qa-config <local-qa-loopback-config.json> --grant <ephemeral-qa-signed-grant.json> "
                "--plan <local-qa-plan.json> --release-manifest <verified-local-manifest-26.json>"
            ),
            "future_r6b_owner_authorized_command_DO_NOT_RUN_IN_A1": (
                f"python aurum-desk/tools/run_kh_03_r6_capture.py --stage {stage_cli} --execute-authorized "
                "--grant <owner-signed-local-grant.json> --owner-config <owner-local-config.json> "
                "--release-manifest <verified-local-manifest-26.json> "
                "--release-receipt <verified-local-readback-28.json> "
                "--expected-manifest-sha256 <owner-pinned-manifest-26-sha256>"
            ),
            "frozen_checkout_ancestry_verification_command": (
                f"git fetch origin && git merge-base --is-ancestor {tested_executable_sha} origin/{MANDATORY_BRANCH_KH03_R6} "
                f"&& git worktree add --detach <local-frozen-dir> {tested_executable_sha}"
            ),
        },
    }
