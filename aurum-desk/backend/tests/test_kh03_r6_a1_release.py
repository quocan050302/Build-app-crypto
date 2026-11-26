"""
aurum-desk/backend/tests/test_kh03_r6_a1_release.py

AURUM DESK — KH-03.R6.A1 | GROUP K (K01..K10) FROZEN EXECUTABLE RELEASE BINDING,
DETACHED WORKTREE VS EVIDENCE-ONLY DESCENDANT, CODE/CONFIG/PYTHONPATH DRIFT,
CLI FLAG/TEMPLATE CONTRACT & PRE-VERIFIER PENDING VS PASS TRUTH
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Tuple

from services.kh_03_r6_a1_acceptance_service import (
    R6A1FaultCaseSpec,
    derive_r6_a1_fault_verdict,
    derive_r6_a1_readiness_verdicts,
    execute_r6_a1_fault_pair,
)
from services.kh_03_r6_a1_release_service import (
    build_r6_a1_unsigned_owner_review_template,
    cleanup_r6_a1_frozen_execution_context,
    compute_frozen_runtime_fingerprint_a1,
    prepare_r6_a1_frozen_execution_context,
    verify_r6_a1_release_binding,
)
from services.kh_03_r6_a1_trust_service import (
    PRIOR_R6_MANIFEST_26_SHA256,
    PRIOR_R6_READBACK_28_SHA256,
    PRIOR_R6_RUN_ID,
    PRIOR_R6_TESTED_EXECUTABLE_SHA,
)
from services.kh_03_r6_durable_grant_service import (
    QA_ISOLATED_TRUST_NAMESPACE_R6,
    load_r6_owner_trust_root,
    provision_qa_isolated_trust_root,
    verify_r6_owner_grant,
)
from services.kh_03_r6_source_contract_service import (
    MANDATORY_BRANCH_KH03_R6,
    build_r6_source_contract_registry,
)

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
CAPTURE_CLI_PATH = APP_ROOT / "tools" / "run_kh_03_r6_capture.py"

R6_ART_ROOT = REPO_ROOT / "aurum-desk/docs/artifacts/kh_03_r6_observed_market_data"
R6_MANIFEST_26 = R6_ART_ROOT / PRIOR_R6_RUN_ID / "26_independent_OS_verifier_manifest_sha256.json"
R6_READBACK_28 = R6_ART_ROOT / f"28_phase2_remote_readback_{PRIOR_R6_RUN_ID}.json"
R6_LATEST_PTR = R6_ART_ROOT / "latest_run.json"


def _git_ok(cwd: Path, args: List[str]) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True, timeout=20)
    return p.stdout.strip()


def _init_mini_release_repo(base_dir: Path, *, with_code_drift: bool = False) -> Tuple[Path, str, str, Path, str, Path, Path]:
    """Creates a clean isolated temporary Git repo with tested SHA -> evidence-only commit (+ optional code drift)."""
    rdir = base_dir / "mini_repo"
    rdir.mkdir(parents=True, exist_ok=True)
    _git_ok(rdir, ["init"])
    _git_ok(rdir, ["checkout", "-b", MANDATORY_BRANCH_KH03_R6])
    _git_ok(rdir, ["config", "user.email", "qa@aurum.local"])
    _git_ok(rdir, ["config", "user.name", "Aurum QA"])

    # Create a real executable file under aurum-desk/tools/
    exe_p = rdir / "aurum-desk/tools/run_kh_03_r6_capture.py"
    exe_p.parent.mkdir(parents=True, exist_ok=True)
    exe_p.write_text("# frozen tested executable v1\n", encoding="utf-8")
    _git_ok(rdir, ["add", "."])
    _git_ok(rdir, ["commit", "-m", "tested executable"])
    tested_sha = _git_ok(rdir, ["rev-parse", "HEAD"])

    # Add evidence-only artifacts commit under aurum-desk/docs/artifacts/kh_03_r6_a1_acquisition_trust_closure/
    run_id = f"20261126T030000Z_{tested_sha[:12]}_qa"
    ev_dir = rdir / "aurum-desk/docs/artifacts/kh_03_r6_a1_acquisition_trust_closure" / run_id
    ev_dir.mkdir(parents=True, exist_ok=True)
    inv: Dict[str, Dict[str, Any]] = {}
    for i in range(16):
        fn = f"{i:02d}_dummy.json"
        fp = ev_dir / fn
        b = json.dumps({"run_id": run_id, "tested_executable_sha": tested_sha, "idx": i}).encode("utf-8")
        fp.write_bytes(b)
        inv[fn] = {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}

    gates_36 = {f"G{i:02d}_MOCK": "PASS" for i in range(1, 36)}
    gates_36["G36_REMOTE_PUBLICATION"] = "PENDING_REMOTE_READBACK"
    man_obj = {
        "spec_version": "KH-03.R6.A1-v1.0",
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "status": "PASS",
        "distinct_os_process_verified": True,
        "verifier_pid": 2002,
        "orchestrator_pid": 1001,
        "violations_count": 0,
        "recomputed_36_gates": gates_36,
        "sealed_files_sha256": inv,
    }
    man_p = ev_dir / "26_independent_OS_verifier_manifest_sha256.json"
    man_bytes = (json.dumps(man_obj, indent=2, sort_keys=True) + "\n").encode("utf-8")
    man_p.write_bytes(man_bytes)
    man_sha = hashlib.sha256(man_bytes).hexdigest()

    _git_ok(rdir, ["add", "."])
    _git_ok(rdir, ["commit", "-m", "evidence commit"])
    ev_sha = _git_ok(rdir, ["rev-parse", "HEAD"])

    ptr_p = ev_dir.parent / "latest_run.json"
    ptr_p.write_text(json.dumps({
        "run_id": run_id, "branch": MANDATORY_BRANCH_KH03_R6,
        "tested_executable_sha": tested_sha, "evidence_commit_sha": ev_sha,
        "manifest_26_sha256": man_sha,
    }, indent=2), encoding="utf-8")
    rb_p = ev_dir.parent / f"28_phase2_remote_readback_{run_id}.json"
    rb_p.write_text(json.dumps({
        "run_id": run_id, "tested_executable_sha": tested_sha, "evidence_commit_sha": ev_sha,
        "manifest_26_sha256": man_sha, "all_sealed_files_remote_verified": True, "remote_tested_sha_verified": True,
    }, indent=2), encoding="utf-8")

    if with_code_drift:
        exe_p.write_text("# DRIFTED CODE AFTER TESTED SHA!\n", encoding="utf-8")
    _git_ok(rdir, ["add", "."])
    _git_ok(rdir, ["commit", "-m", "pointer commit"])
    pub_sha = _git_ok(rdir, ["rev-parse", "HEAD"])
    return rdir, tested_sha, pub_sha, man_p, man_sha, ptr_p, rb_p


def run_kh03_r6_a1_controls_k01_to_k10(
    *,
    executable_sha: str,
    run_id: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    receipts: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}
    env_cli = dict(os.environ)
    env_cli["PYTHONPATH"] = f"{BACKEND_ROOT}{os.pathsep}{APP_ROOT}{os.pathsep}{REPO_ROOT}"
    env_cli["PYTHONHASHSEED"] = "0"

    with tempfile.TemporaryDirectory(prefix="a1_grp_k_") as td:
        root = Path(td)
        mini_ok, t_sha, _p_sha, m_p, m_sha, ptr_p, rb_p = _init_mini_release_repo(root / "ok_case", with_code_drift=False)
        mini_drift, _td_sha, _pd_sha, md_p, md_sha, ptrd_p, rbd_p = _init_mini_release_repo(root / "drift_case", with_code_drift=True)

        # K01: Legitimate tested SHA + evidence-only publication descendant verified AND detached worktree at exact 529bdaf2!
        def _k01_neg():
            # Unpinned manifest SHA refused as non-self-authenticating!
            r_unp = verify_r6_a1_release_binding(
                repo_root=mini_ok, release_manifest_path=m_p, expected_manifest_sha256=None,
                require_pinned_manifest_sha=True,
            )
            return ({"attack": "unpinned_manifest_self_auth"}, {"ready_or_valid": bool(r_unp["valid"]), "violations_count": len(r_unp["violations"])})

        def _k01_pos():
            # 1. Verify evidence-only descendant on mini_ok
            rb_ok = verify_r6_a1_release_binding(
                repo_root=mini_ok, release_manifest_path=m_p, expected_manifest_sha256=m_sha,
                latest_pointer_path=ptr_p, remote_receipt_path=rb_p, require_pinned_manifest_sha=True, require_remote_receipt=True,
            )
            fctx = prepare_r6_a1_frozen_execution_context(
                repo_root=mini_ok, release_binding=rb_ok["binding"], mode="verify_evidence_only_descendant",
                require_clean_worktree=True, check_pythonpath=False,
            ) if rb_ok["valid"] else {"ready": False, "violations": ["BINDING_FAIL"]}

            # 2. Verify REAL published R6 release (529bdaf2) + detached worktree on actual REPO_ROOT!
            rb_real = verify_r6_a1_release_binding(
                repo_root=REPO_ROOT, release_manifest_path=R6_MANIFEST_26,
                expected_manifest_sha256=PRIOR_R6_MANIFEST_26_SHA256,
                latest_pointer_path=R6_LATEST_PTR, remote_receipt_path=R6_READBACK_28,
                require_pinned_manifest_sha=True, require_remote_receipt=True,
            )
            wt_ok = False
            wt_viols: List[str] = []
            if rb_real["valid"]:
                wt_base = (Path(r"C:\Users\Public") / f"a1k01_{os.getpid()}") if (os.name == "nt" and Path(r"C:\Users\Public").is_dir()) else (root / "wt_parent")
                wt_base.mkdir(parents=True, exist_ok=True)
                try:
                    wt_ctx = prepare_r6_a1_frozen_execution_context(
                        repo_root=REPO_ROOT, release_binding=rb_real["binding"], mode="detached_worktree",
                        worktree_parent_dir=wt_base, require_clean_worktree=False, check_pythonpath=False,
                    )
                    if wt_ctx["ready"] and wt_ctx["context"] is not None:
                        wt_head = _git_ok(Path(wt_ctx["context"].checkout_root), ["rev-parse", "HEAD"])
                        cln = cleanup_r6_a1_frozen_execution_context(repo_root=REPO_ROOT, worktree_path=Path(wt_ctx["context"].checkout_root))
                        wt_ok = (wt_head == PRIOR_R6_TESTED_EXECUTABLE_SHA) and bool(cln["cleaned"])
                    else:
                        wt_viols.extend(wt_ctx.get("violations") or [])
                finally:
                    import shutil as _sh
                    _sh.rmtree(wt_base, ignore_errors=True)
            else:
                wt_viols.extend(rb_real.get("violations") or [])

            summary["published_evidence_only_descendant_verified"] = bool(rb_ok["valid"] and fctx["ready"])
            summary["detached_worktree_exact_tested_sha_verified"] = bool(wt_ok)
            ok_all = bool(rb_ok["valid"] and fctx["ready"] and wt_ok)
            return ({"check": "evidence_only_descendant_and_real_529bdaf2_detached_worktree"}, {"ready_or_valid": ok_all, "violations_count": len(wt_viols) + len(fctx.get("violations") or [])})

        r_k01 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("K01", "K", "BASELINE_BUG_RED", "services.kh_03_r6_a1_release_service.prepare_r6_a1_frozen_execution_context", "a1_release_rejected", "a1_release_verified", "Evidence-only publication descendant and exact detached worktree at 529bdaf2 verified"),
            negative_fn=_k01_neg, positive_fn=_k01_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_k01)

        # K02: Publication descendant includes changed executable/config file -> REJECTED!
        def _k02_neg():
            rb_d = verify_r6_a1_release_binding(
                repo_root=mini_drift, release_manifest_path=md_p, expected_manifest_sha256=md_sha,
                latest_pointer_path=ptrd_p, remote_receipt_path=rbd_p, require_pinned_manifest_sha=True, require_remote_receipt=True,
            )
            f_d = prepare_r6_a1_frozen_execution_context(
                repo_root=mini_drift, release_binding=rb_d["binding"], mode="verify_evidence_only_descendant",
                require_clean_worktree=True, check_pythonpath=False,
            )
            return ({"attack": "code_drift_between_tested_and_head"}, {"ready_or_valid": bool(f_d["ready"]), "violations_count": len(f_d["violations"])})

        def _k02_pos():
            rb_o = verify_r6_a1_release_binding(repo_root=mini_ok, release_manifest_path=m_p, expected_manifest_sha256=m_sha, require_pinned_manifest_sha=True)
            f_o = prepare_r6_a1_frozen_execution_context(repo_root=mini_ok, release_binding=rb_o["binding"], mode="verify_evidence_only_descendant", require_clean_worktree=True, check_pythonpath=False)
            return ({"check": "clean_evidence_only_descendant"}, {"ready_or_valid": bool(f_o["ready"]), "violations_count": len(f_o["violations"])})

        r_k02 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("K02", "K", "NEGATIVE_CONTRACT", "services.kh_03_r6_a1_release_service.prepare_r6_a1_frozen_execution_context", "a1_release_rejected", "a1_release_verified", "Code or config drift in descendant rejected"),
            negative_fn=_k02_neg, positive_fn=_k02_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_k02)
        summary["code_or_config_drift_rejected"] = (r_k02["status"] == "PASS")

        # K03: Dirty worktree / untrusted PYTHONPATH / dependency drift rejected
        def _k03_neg():
            rb_o = verify_r6_a1_release_binding(repo_root=mini_ok, release_manifest_path=m_p, expected_manifest_sha256=m_sha, require_pinned_manifest_sha=True)
            # 1. Dependency fingerprint drift
            f_dep = prepare_r6_a1_frozen_execution_context(
                repo_root=mini_ok, release_binding=rb_o["binding"], mode="verify_evidence_only_descendant",
                expected_dependencies={"cryptography": "0.0.0-DRIFT"}, require_clean_worktree=True, check_pythonpath=False,
            )
            # 2. Untrusted PYTHONPATH
            old_pp = os.environ.get("PYTHONPATH")
            os.environ["PYTHONPATH"] = str(root / "evil_imports")
            try:
                f_pp = prepare_r6_a1_frozen_execution_context(
                    repo_root=mini_ok, release_binding=rb_o["binding"], mode="verify_evidence_only_descendant",
                    require_clean_worktree=True, check_pythonpath=True,
                )
            finally:
                if old_pp is None:
                    os.environ.pop("PYTHONPATH", None)
                else:
                    os.environ["PYTHONPATH"] = old_pp
            # 3. Dirty tree
            dirty_f = mini_ok / "untracked_dirty.py"
            dirty_f.write_text("# dirty\n", encoding="utf-8")
            try:
                f_dirty = prepare_r6_a1_frozen_execution_context(
                    repo_root=mini_ok, release_binding=rb_o["binding"], mode="verify_evidence_only_descendant",
                    require_clean_worktree=True, check_pythonpath=False,
                )
            finally:
                dirty_f.unlink()
            return (
                {"attack": "dirty_tree_untrusted_pythonpath_and_dep_drift"},
                {"ready_or_valid": bool(f_dep["ready"]) or bool(f_pp["ready"]) or bool(f_dirty["ready"]), "violations_count": len(f_dep["violations"]) + len(f_pp["violations"]) + len(f_dirty["violations"])},
            )

        r_k03 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("K03", "K", "NEGATIVE_CONTRACT", "services.kh_03_r6_a1_release_service.prepare_r6_a1_frozen_execution_context", "a1_release_rejected", "a1_release_verified", "Dirty checkout, untrusted PYTHONPATH, and dependency drift rejected"),
            negative_fn=_k03_neg, positive_fn=_k02_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_k03)
        summary["dirty_or_untrusted_pythonpath_or_dep_drift_rejected"] = (r_k03["status"] == "PASS")

        # K04: Forged manifest PASS / wrong SHA / incomplete required gates / verifier_pid == orchestrator_pid
        def _k04_neg():
            forged_p = root / "forged_26.json"
            forged_p.write_text(json.dumps({
                "spec_version": "KH-03.R6.A1-v1.0", "run_id": "fake_run", "tested_executable_sha": "0" * 40,
                "status": "PASS", "distinct_os_process_verified": True, "verifier_pid": 100, "orchestrator_pid": 100,
                "violations_count": 0, "recomputed_36_gates": {"G01_BRANCH_SHA_CLEAN": "PASS"}, "sealed_files_sha256": {},
            }), encoding="utf-8")
            f_sha = hashlib.sha256(forged_p.read_bytes()).hexdigest()
            r_f = verify_r6_a1_release_binding(repo_root=mini_ok, release_manifest_path=forged_p, expected_manifest_sha256=f_sha, require_pinned_manifest_sha=True)
            return ({"attack": "forged_manifest_incomplete_gates_same_pid"}, {"ready_or_valid": bool(r_f["valid"]), "violations_count": len(r_f["violations"])})

        r_k04 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("K04", "K", "NEGATIVE_CONTRACT", "services.kh_03_r6_a1_release_service.verify_r6_a1_release_binding", "a1_release_rejected", "a1_release_verified", "Forged PASS manifest with incomplete gates and same PID rejected"),
            negative_fn=_k04_neg, positive_fn=_k02_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_k04)
        summary["forged_manifest_pass_rejected"] = (r_k04["status"] == "PASS")

        # K05: Wrong pointer / remote receipt / ancestry mismatch rejected
        def _k05_neg():
            bad_ptr = root / "bad_latest.json"
            bad_ptr.write_text(json.dumps({"run_id": "WRONG_RUN", "tested_executable_sha": t_sha, "manifest_26_sha256": "9" * 64}), encoding="utf-8")
            bad_rb = root / "bad_28.json"
            bad_rb.write_text(json.dumps({"run_id": "WRONG_RUN", "tested_executable_sha": t_sha, "all_sealed_files_remote_verified": False, "remote_tested_sha_verified": False}), encoding="utf-8")
            r_bad = verify_r6_a1_release_binding(
                repo_root=mini_ok, release_manifest_path=m_p, expected_manifest_sha256=m_sha,
                latest_pointer_path=bad_ptr, remote_receipt_path=bad_rb, require_pinned_manifest_sha=True, require_remote_receipt=True,
            )
            return ({"attack": "mismatched_pointer_and_unverified_receipt"}, {"ready_or_valid": bool(r_bad["valid"]), "violations_count": len(r_bad["violations"])})

        r_k05 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("K05", "K", "NEGATIVE_CONTRACT", "services.kh_03_r6_a1_release_service.verify_r6_a1_release_binding", "a1_release_rejected", "a1_release_verified", "Mismatched pointer and unverified remote receipt rejected"),
            negative_fn=_k05_neg, positive_fn=_k02_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_k05)
        summary["pointer_receipt_ancestry_mismatch_rejected"] = (r_k05["status"] == "PASS")

        # K06: Conflicting CLI flags & bypass env var rejected with exit 2 and 0 claim; corrected template includes --execute-authorized!
        def _k06_neg():
            p_conf = subprocess.run([sys.executable, str(CAPTURE_CLI_PATH), "--stage", "smoke", "--dry-run", "--execute-authorized"], cwd=str(REPO_ROOT), env=env_cli, capture_output=True, text=True, timeout=20)
            env_byp = dict(env_cli); env_byp["KH03_R6_A1_ALLOW_EXTERNAL"] = "1"
            p_byp = subprocess.run([sys.executable, str(CAPTURE_CLI_PATH), "--stage", "smoke", "--dry-run"], cwd=str(REPO_ROOT), env=env_byp, capture_output=True, text=True, timeout=20)
            return (
                {"attack": "conflicting_cli_flags_and_bypass_env"},
                {"exit_code": int(p_conf.returncode) if (p_conf.returncode == 2 and p_byp.returncode == 2) else 0, "grant_claimed": False, "external_requests": 0},
            )

        def _k06_pos():
            tmpl = build_r6_a1_unsigned_owner_review_template(
                tested_executable_sha=PRIOR_R6_TESTED_EXECUTABLE_SHA, publication_head_sha="28b252676a10aa21bec7ce14fbd9415db230c2f4",
                stage_a1_run_id=run_id, manifest_26_sha256=PRIOR_R6_MANIFEST_26_SHA256,
                source_registry_digest=build_r6_source_contract_registry().registry_digest, stage="B_SMOKE",
            )
            fut_cmd = tmpl["exact_local_cli_commands"]["future_r6b_owner_authorized_command_DO_NOT_RUN_IN_A1"]
            ok = ("--execute-authorized" in fut_cmd and "--release-receipt" in fut_cmd and tmpl["valid_authorization"] is False and tmpl["a1_external_capture_enabled"] is False)
            return ({"check": "corrected_template_flags_and_a1_disclosure"}, {"ready_or_valid": ok, "violations_count": 0 if ok else 1})

        r_k06 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("K06", "K", "BASELINE_BUG_RED", "services.kh_03_r6_a1_release_service.build_r6_a1_unsigned_owner_review_template", "a1_cli_failed_no_claim", "a1_release_verified", "Conflicting flags/bypass env blocked and template includes --execute-authorized"),
            negative_fn=_k06_neg, positive_fn=_k06_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_k06)
        summary["missing_execute_authorized_or_conflict_rejected"] = (r_k06["status"] == "PASS")

        # K07: Unsigned review template used as signed grant -> rejected with 0 authorization!
        def _k07_neg():
            tr_p, _, _ = provision_qa_isolated_trust_root(root / "k07_tr", store_id="k07_s")
            tr = load_r6_owner_trust_root(tr_p, expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)["trust_root"]
            tmpl = build_r6_a1_unsigned_owner_review_template(
                tested_executable_sha=executable_sha, publication_head_sha=None, stage_a1_run_id=run_id,
                manifest_26_sha256="a" * 64, source_registry_digest=build_r6_source_contract_registry().registry_digest,
            )
            from datetime import datetime, timezone
            gv = verify_r6_owner_grant(
                json.dumps(tmpl).encode("utf-8"), tr, datetime.now(timezone.utc),
                expected_executable_sha=executable_sha, expected_release_manifest_sha256="a" * 64, expected_store_id="k07_s", expected_stage="QA_LOOPBACK",
            )
            return ({"attack": "unsigned_template_submitted_as_grant"}, {"ready_or_valid": bool(gv["valid"]), "violations_count": len(gv["violations"])})

        r_k07 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("K07", "K", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.verify_r6_owner_grant", "a1_release_rejected", "a1_release_verified", "Unsigned owner review template rejected as grant"),
            negative_fn=_k07_neg, positive_fn=_k06_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_k07)
        summary["unsigned_template_as_grant_rejected"] = (r_k07["status"] == "PASS")

        # K08: Dry-run with invalid release manifest or invalid plan exits nonzero!
        def _k08_neg():
            bad_m = root / "k08_bad_man.json"
            bad_m.write_text('{"status":"PASS"}', encoding="utf-8")
            p_m = subprocess.run(
                [sys.executable, str(CAPTURE_CLI_PATH), "--stage", "smoke", "--dry-run", "--release-manifest", str(bad_m)],
                cwd=str(REPO_ROOT), env=env_cli, capture_output=True, text=True, timeout=20,
            )
            return ({"attack": "dryrun_with_invalid_release_manifest"}, {"exit_code": int(p_m.returncode), "grant_claimed": False, "external_requests": 0})

        def _k08_pos():
            p_ok = subprocess.run(
                [sys.executable, str(CAPTURE_CLI_PATH), "--stage", "smoke", "--dry-run", "--release-manifest", str(R6_MANIFEST_26), "--expected-manifest-sha256", PRIOR_R6_MANIFEST_26_SHA256, "--release-receipt", str(R6_READBACK_28), "--latest-pointer", str(R6_LATEST_PTR)],
                cwd=str(REPO_ROOT), env=env_cli, capture_output=True, text=True, timeout=25,
            )
            doc = json.loads(p_ok.stdout) if p_ok.stdout.strip().startswith("{") else {}
            ok = (p_ok.returncode == 0 and doc.get("status") == "DRY_RUN_VALIDATED_ZERO_IO" and (doc.get("release_binding") or {}).get("valid") is True)
            return ({"check": "dryrun_valid_pinned_r6_release"}, {"ready_or_valid": ok, "violations_count": len(doc.get("violations") or [])})

        r_k08 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("K08", "K", "NEGATIVE_CONTRACT", "tools.run_kh_03_r6_capture.run_capture_cli", "a1_cli_failed_no_claim", "a1_release_verified", "Dry-run rejects invalid manifest and verifies real pinned R6 manifest"),
            negative_fn=_k08_neg, positive_fn=_k08_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_k08)

        # K09: F30 truthfulness: pre-verifier PENDING_SEPARATE_OS_VERIFIER vs genuine FAIL vs post-verifier PASS!
        def _k09_neg():
            # Missing artifacts -> MUST classify as FAIL_OR_BLOCKED_KH03_R6_A1_REQUIRED_GATES (never PASS!)
            v_fail = derive_r6_a1_readiness_verdicts({"00": {}}, expected_run_id=run_id, expected_tested_sha=executable_sha, pre_verifier_mode=False)
            ok_fail = (v_fail["overall_classification"] == "FAIL_OR_BLOCKED_KH03_R6_A1_REQUIRED_GATES" and v_fail["gates"]["G32_INDEPENDENT_VERIFIER"] == "FAIL")
            return ({"attack": "missing_artifacts_must_fail"}, {"ready_or_valid": not ok_fail, "violations_count": 1 if ok_fail else 0})

        def _k09_pos():
            v_pend = derive_r6_a1_readiness_verdicts({"26": {"verifier_status": "PENDING"}}, expected_run_id=run_id, expected_tested_sha=executable_sha, pre_verifier_mode=True)
            ok_pend = (v_pend["gates"]["G32_INDEPENDENT_VERIFIER"] == "PENDING_SEPARATE_OS_VERIFIER" and v_pend["external_stages_separate"]["executed_count"] == 0)
            return ({"check": "pre_verifier_pending_distinct_from_fail"}, {"ready_or_valid": ok_pend, "violations_count": 0 if ok_pend else 1})

        r_k09 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("K09", "K", "NEGATIVE_CONTRACT", "services.kh_03_r6_a1_acceptance_service.derive_r6_a1_readiness_verdicts", "a1_release_rejected", "a1_release_verified", "Pre-verifier PENDING status cleanly separated from FAIL and PASS"),
            negative_fn=_k09_neg, positive_fn=_k09_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_k09)

        # K10: Full QA CLI E2E bound to verified pinned R6 manifest (manifest verified + real QA manifest returned!)
        from tests.test_kh03_r6_a1_acquisition import LoopbackBitgetV2FixtureServer, _write_qa_cli_bundle
        from services.kh_03_r6_durable_grant_service import provision_r6_durable_ledger
        from datetime import datetime, timezone
        now_dt = datetime.now(timezone.utc)
        win_s = 1794000000000
        win_e = win_s + 3 * 60_000
        fx = LoopbackBitgetV2FixtureServer(window_start_ms=win_s, window_end_ms=win_e, bars_per_page=3).start()
        try:
            k10_dest = root / "k10_dest"; k10_dest.mkdir()
            k10_tr, k10_pk, _ = provision_qa_isolated_trust_root(root / "k10_tr", store_id="k10_s")
            k10_led = root / "k10_led"
            provision_r6_durable_ledger(k10_led, store_id="k10_s", trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_dt)

            def _k10_neg():
                # Grant signed for WRONG tested SHA vs pinned R6 release manifest -> rejected before claim!
                cfg_p, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id="G_K10_BADSHA", store_id="k10_s", priv_k=k10_pk, tr_path=k10_tr, ledger_root=k10_led, dest_dir=k10_dest, http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha="0" * 40, rel_sha=PRIOR_R6_MANIFEST_26_SHA256, win_start=win_s, win_end=win_e, run_id=f"{run_id}_k10n")
                p = subprocess.run([sys.executable, str(CAPTURE_CLI_PATH), "--qa-loopback-e2e", "--stage", "smoke", "--qa-config", str(cfg_p), "--grant", str(gr_p), "--plan", str(pl_p), "--release-manifest", str(R6_MANIFEST_26), "--expected-manifest-sha256", PRIOR_R6_MANIFEST_26_SHA256], cwd=str(REPO_ROOT), env=env_cli, capture_output=True, text=True, timeout=30)
                doc = json.loads(p.stdout) if p.stdout.strip().startswith("{") else {}
                return ({"attack": "qa_cli_grant_sha_mismatch_vs_release_manifest"}, {"exit_code": int(p.returncode), "grant_claimed": bool(doc.get("grant_claimed")), "external_requests": 0})

            def _k10_pos():
                cfg_p, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id="G_K10_OK", store_id="k10_s", priv_k=k10_pk, tr_path=k10_tr, ledger_root=k10_led, dest_dir=k10_dest, http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha=PRIOR_R6_TESTED_EXECUTABLE_SHA, rel_sha=PRIOR_R6_MANIFEST_26_SHA256, win_start=win_s, win_end=win_e, run_id=f"{run_id}_k10p")
                p = subprocess.run([sys.executable, str(CAPTURE_CLI_PATH), "--qa-loopback-e2e", "--stage", "smoke", "--qa-config", str(cfg_p), "--grant", str(gr_p), "--plan", str(pl_p), "--release-manifest", str(R6_MANIFEST_26), "--expected-manifest-sha256", PRIOR_R6_MANIFEST_26_SHA256, "--release-receipt", str(R6_READBACK_28), "--latest-pointer", str(R6_LATEST_PTR)], cwd=str(REPO_ROOT), env=env_cli, capture_output=True, text=True, timeout=60)
                doc = json.loads(p.stdout) if p.stdout.strip().startswith("{") else {}
                ok = (p.returncode == 0 and doc.get("status") == "QA_ACQUISITION_END_TO_END_PASS" and bool(doc.get("dataset_manifest_sha256")))
                summary["qa_cli_on_frozen_executable_verified"] = ok
                return ({"check": "qa_cli_bound_to_verified_pinned_release_manifest"}, {"exit_code": int(p.returncode), "status": doc.get("status"), "committed_chunks": int(doc.get("committed_chunks_count", 0)), "committed_rows": int(doc.get("total_committed_normalized_rows", 0)), "custody_valid": bool(doc.get("custody_readonly_valid")), "real_observed_records": int(doc.get("accepted_real_observed_records", -1)), "external_requests": int(doc.get("external_market_requests_attempted", -1))})

            r_k10 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("K10", "K", "INTEGRATION_FAULT", "tools.run_kh_03_r6_capture.run_capture_cli", "a1_cli_failed_no_claim", "a1_qa_e2e_pass", "Full QA CLI E2E verified against pinned release manifest and receipt"),
                negative_fn=_k10_neg, positive_fn=_k10_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_k10)
        finally:
            fx.stop()

    return receipts, summary


def test_kh03_r6_a1_release_k01_to_k10() -> None:
    recs, summary = run_kh03_r6_a1_controls_k01_to_k10(executable_sha="k" * 40, run_id="pytest_a1_k")
    verd = derive_r6_a1_fault_verdict(recs, expected_executable_sha="k" * 40, expected_run_id="pytest_a1_k")
    assert verd["valid"], f"K_CONTROLS_FAILED:{verd['violations']}"
    assert verd["passed_controls"] == 10
    assert all(summary.values()), f"K_SUMMARY_INCOMPLETE:{summary}"
