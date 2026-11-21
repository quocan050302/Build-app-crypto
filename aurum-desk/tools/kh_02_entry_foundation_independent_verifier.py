#!/usr/bin/env python3
"""
aurum-desk/tools/kh_02_entry_foundation_independent_verifier.py

AURUM DESK — KH-02 Separate-OS-Process Independent Disk Verifier
SPEC VERSION: KH-02.R1

Reads EVERY closed artifact (`00`..`17`, `19`, `README`, `HANDOVER_VI`, `logs/runtime_memory_samples_0p5s.jsonl.gz`,
all `junit/*.xml`, and all `receipts/G-KH02-*.json`) directly from disk in a separate OS process
(`verifier_pid != orchestrator_pid`), independently recomputes every safety/causality/parity/JUnit/RAM/disk
check without trusting in-memory orchestrator state, and seals `18_independent_verifier_content_sha256_manifest.json`.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domain_calculator import CostAssumptions
from services.kh_01b_r1_ram_safety_r3_service import (
    HARD_ABORT_RAM_FLOOR_BYTES_R3,
    PRIOR_R2_ACCEPTED_RUN_ID,
    PRIOR_R2_LATEST_PTR_REL,
    PRIOR_R2_LATEST_PTR_SHA256,
    PRIOR_R2_TESTED_SHA,
    SEALED_R2_A1_56710_IDS_SHA256,
    SEALED_R2_E00_BUNDLE_SHA256,
    SEALED_R2_SI0_242_TUPLES_SHA256,
    SEALED_R2_SI8_6375_IDS_SHA256,
    audit_r3_memory_samples_jsonl_gz,
    detect_active_64mib_ram_floor_in_source,
)
from services.kh_02_entry_foundation_research_service import (
    CANONICAL_DATASET_SHA256_KH02,
    MANDATORY_BRANCH_KH02,
    PRIOR_R3_ACCEPTED_RUN_ID,
    PRIOR_R3_LATEST_PTR_REL,
    PRIOR_R3_LATEST_PTR_SHA256,
    PRIOR_R3_PUBLICATION_HEAD_SHA,
    PRIOR_R3_TESTED_SHA,
    REQUIRED_ARTIFACTS_KH02,
    SCHEMA_VERSION_KH02,
    SPEC_VERSION_KH02,
    evaluate_kh02_independent_gates_from_disk,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
    verify_source_freeze_v17931211,
)
from services.v17_9_3_1_2_19_truth_and_core_repair_service import EvidenceReceiptV17931219
from tools.fresh_junit_io import write_json_durable_atomic

MANIFEST_18_NAME = "18_independent_verifier_content_sha256_manifest.json"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_kh02_run_dir_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_git_source_freeze: bool = True,
    check_live_repo_pointers: bool = True,
) -> Dict[str, Any]:
    run_dir = run_dir.resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {"independent_verifier_status": "FAIL", "verifier_pid": verifier_pid, "violations": [f"RUN_DIR_MISSING:{run_dir}"]}

    for rel_req in REQUIRED_ARTIFACTS_KH02:
        if rel_req == MANIFEST_18_NAME:
            continue
        fp = run_dir / rel_req
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_REQUIRED_KH02_ARTIFACT:{rel_req}")
    if violations:
        return {"independent_verifier_status": "FAIL", "verifier_pid": verifier_pid, "violations": violations}

    try:
        f00 = json.loads((run_dir / "00_preflight_git_branch_head_resource_runtime.json").read_text("utf-8"))
        f01 = json.loads((run_dir / "01_function_callgraph_keep_as_is_diff_immutability.json").read_text("utf-8"))
        f02 = json.loads((run_dir / "02_dataset_registry_lineage_m1_m15_h1_quality.json").read_text("utf-8"))
        f03 = json.loads((run_dir / "03_frozen_hypotheses_budget_splits_power_falsification.json").read_text("utf-8"))
        f04 = json.loads((run_dir / "04_asof_mtf_feature_provenance_and_leakage_controls.json").read_text("utf-8"))
        f05 = json.loads((run_dir / "05_opportunity_universe_denominators_and_cohort_ids.json").read_text("utf-8"))
        f07 = json.loads((run_dir / "07_matched_cohort_confounder_balance_ablations.json").read_text("utf-8"))
        f08 = json.loads((run_dir / "08_chronological_one_position_three_fill_replay.json").read_text("utf-8"))
        f09 = json.loads((run_dir / "09_fee_spread_slippage_1x_2x_3x_counterfactuals.json").read_text("utf-8"))
        f10 = json.loads((run_dir / "10_purged_walkforward_and_cluster_uncertainty.json").read_text("utf-8"))
        f11 = json.loads((run_dir / "11_oos_escrow_no_overlap_no_peek_status.json").read_text("utf-8"))
        f12 = json.loads((run_dir / "12_full_pytest_collection_junit_bijection_exit.json").read_text("utf-8"))
        f13 = json.loads((run_dir / "13_async_w1_w2_w4_w1repeat_hashes_speedups.json").read_text("utf-8"))
        f14 = json.loads((run_dir / "14_resource_telemetry_ram_disk_pid_cleanup.json").read_text("utf-8"))
        f15 = json.loads((run_dir / "15_red_green_fault_injection_independent_controls.json").read_text("utf-8"))
        f16 = json.loads((run_dir / "16_production_baseline_adapter_fingerprint_parity.json").read_text("utf-8"))
        f17 = json.loads((run_dir / "17_engineering_economic_oos_activation_gate_matrix.json").read_text("utf-8"))
        f19 = json.loads((run_dir / "19_git_tested_sha_evidence_publication_receipt.json").read_text("utf-8"))
        readme_txt = (run_dir / "README_KH02_ENTRY_FOUNDATION.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH02_ENTRY_FOUNDATION_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {"independent_verifier_status": "FAIL", "verifier_pid": verifier_pid, "violations": [f"JSON_PARSE_ERROR:{exc}"]}

    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    orch_pid = int(f00.get("orchestrator_pid") or -1)
    if verifier_pid == orch_pid:
        violations.append(f"VERIFIER_SAME_OS_PID:{verifier_pid}")

    # 1. Branch, tested SHA, stale run guard, source freeze, prior pointers & live cost defaults
    if f00.get("branch") != MANDATORY_BRANCH_KH02:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if f00.get("tested_executable_sha") != tested_sha or f17.get("tested_executable_sha") != tested_sha or f19.get("tested_executable_sha") != tested_sha:
        violations.append("TESTED_EXECUTABLE_SHA_MISMATCH")
    if tested_sha in (PRIOR_R3_TESTED_SHA, PRIOR_R3_PUBLICATION_HEAD_SHA, PRIOR_R2_TESTED_SHA):
        violations.append(f"STALE_TESTED_SHA_REUSED:{tested_sha}")
    if run_id in (PRIOR_R3_ACCEPTED_RUN_ID, PRIOR_R2_ACCEPTED_RUN_ID):
        violations.append(f"STALE_RUN_ID_REUSED:{run_id}")
    if len(tested_sha) >= 12 and not run_id.endswith(f"_{tested_sha[:12]}"):
        violations.append(f"RUN_ID_MISSING_SHA12_SUFFIX:{run_id}")

    if int(f01.get("locked_files_modified_count", 1)) != 0 or int(f01.get("modified_existing_files_count", 1)) != 0:
        violations.append("LOCKED_OR_EXISTING_FILES_MODIFIED_IN_01")
    if len(f01.get("forbidden_order_import_hits") or []) > 0:
        violations.append("FORBIDDEN_ORDER_IMPORT_IN_01")

    if check_live_repo_pointers:
        ds_disk = APP_ROOT / "backtest/historical_dataset_XAUUSDT.json.gz"
        if ds_disk.is_file() and _file_sha256(ds_disk) != CANONICAL_DATASET_SHA256_KH02:
            violations.append("CANONICAL_DATASET_MUTATED_ON_DISK")
        for ptr_rel, exp_sha, lbl in (
            (PRIOR_R3_LATEST_PTR_REL, PRIOR_R3_LATEST_PTR_SHA256, "R3"),
            (PRIOR_R2_LATEST_PTR_REL, PRIOR_R2_LATEST_PTR_SHA256, "R2"),
        ):
            p = REPO_ROOT / ptr_rel
            if p.is_file() and _file_sha256(p) != exp_sha:
                violations.append(f"PRIOR_{lbl}_LATEST_RUN_PTR_MUTATED")
        c_live = CostAssumptions()
        if c_live.maker_fee_rate != 0.0002 or c_live.taker_fee_rate != 0.0004 or c_live.slippage_usd != 0.10:
            violations.append("LIVE_COST_ASSUMPTIONS_MUTATED")
        if detect_active_64mib_ram_floor_in_source(REPO_ROOT)["status"] != "PASS":
            violations.append("ACTIVE_64MIB_OVERRIDE_FOUND")

    if check_git_source_freeze:
        sf = verify_source_freeze_v17931211(REPO_ROOT, tested_sha)
        if sf.get("status") != "PASS":
            violations.extend(f"SOURCE_FREEZE:{v}" for v in (sf.get("violations") or []))

    # 2. Hypothesis freeze digest recomputation (03)
    f03_body = {k: v for k, v in f03.items() if k != "manifest_sha256"}
    if canonical_hash(f03_body) != str(f03.get("manifest_sha256") or ""):
        violations.append("FROZEN_HYPOTHESIS_03_SHA256_TAMPERED")
    if not f03.get("frozen_before_outcome_evaluation") or int(f03.get("primary_hypotheses_count", 0)) > 3:
        violations.append("HYPOTHESIS_03_FREEZE_OR_BUDGET_TAMPERED")

    # 3. Causality (04), Candidate Ledger (05/06), Replay (08), Uncertainty (10), OOS (11)
    if f04.get("status") != "PASS" or (f04.get("future_mutation_invariance_test") or {}).get("status") != "PASS":
        violations.append("ASOF_CAUSALITY_04_FAIL")
    cand_gz = run_dir / "06_b0_b1_b2_entry_candidates_and_reason_codes.jsonl.gz"
    if _file_sha256(cand_gz) != str(f05.get("candidate_audit_jsonl_gz_sha256") or ""):
        violations.append("CANDIDATE_LEDGER_06_SHA256_MISMATCH")
    if f08.get("status") != "PASS" or int(f08.get("total_overlap_violations_across_all_arms", 1)) != 0:
        violations.append("CHRONOLOGICAL_REPLAY_08_FAIL")
    if int(f08.get("total_risk_cap_violations_across_all_arms", 1)) != 0 or int(f08.get("total_min_net_rr_violations_across_all_arms", 1)) != 0:
        violations.append("RISK_OR_RR_VIOLATION_IN_08")

    # Check zero-trade honesty & no PROVEN_EDGE without fresh OOS
    for ac, a_doc in (f10.get("arms_uncertainty") or {}).items():
        if int(a_doc.get("closed_trades_nb") or 0) == 0 and a_doc.get("expectancy_1x_nb_r") is not None:
            violations.append(f"ZERO_TRADE_ARM_NON_NULL_EXPECTANCY:{ac}")
        if a_doc.get("arm_economic_verdict") == "PROVEN_EDGE" and f11.get("FRESH_OOS_VERDICT") != "PASS":
            violations.append(f"ILLEGAL_PROVEN_EDGE_WITHOUT_FRESH_OOS:{ac}")

    if f11.get("ACTIVATION_READY") != "NO" or int(f11.get("OOS_EVALUATIONS", 1)) != 0:
        violations.append("OOS_11_ACTIVATION_OR_EVAL_VIOLATION")
    rm11 = f11.get("runtime_safety_modes") or {}
    if any(rm11.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
        violations.append("OOS_11_RUNTIME_MODES_NOT_OFF")

    # 4. Raw 0.5s RAM samples audit & disk/temp telemetry (14)
    samples_rel = str(f14.get("memory_samples_rel_path") or "logs/runtime_memory_samples_0p5s.jsonl.gz")
    samples_p = run_dir / samples_rel
    if not samples_p.is_file() or _file_sha256(samples_p) != str(f14.get("memory_samples_sha256") or ""):
        violations.append("MEMORY_SAMPLES_GZ_MISSING_OR_SHA_MISMATCH")
        recomp_mem: Dict[str, Any] = {"status": "FAIL"}
    else:
        recomp_mem = audit_r3_memory_samples_jsonl_gz(samples_p, hard_floor_bytes=HARD_ABORT_RAM_FLOOR_BYTES_R3)
        if recomp_mem.get("status") != "PASS" or int(recomp_mem.get("below_floor_samples_count", 1)) != 0:
            violations.append(f"RAW_RAM_SAMPLES_BELOW_512MIB:{recomp_mem.get('min_available_ram_mib')}")

    # 5. Raw JUnit XML 1:1 bijection (12) & w1/w2/w4/w1_repeat hashes (13)
    if f12.get("execution_status") == "EXECUTED":
        jb = verify_fresh_junit_bijection_v17931211(
            run_dir,
            f12.get("collection_manifest") or {},
            f12.get("shard_manifest") or {},
            expected_run_id=run_id,
            expected_tested_sha=tested_sha,
            mandatory_node_prefixes=("backend/tests/test_kh_02",),
            require_shard_schedule=True,
        )
        if jb.get("status") != "PASS":
            violations.extend(f"JUNIT_BIJECTION:{v}" for v in (jb.get("violations") or []))
    else:
        violations.append(f"FULL_REGRESSION_NOT_EXECUTED:{f12.get('execution_status')}")

    for sec_k, sha_k in (
        ("pytest_controlled_subset_w1_w2_w4_repeat", "canonical_sorted_outcome_sha256"),
        ("kh02_science_slice_w1_w2_w4_repeat", "canonical_sorted_sha256"),
    ):
        r_list = (f13.get(sec_k) or {}).get("runs") or []
        modes = [str(r.get("mode_label")) for r in r_list]
        shas = {str(r.get(sha_k) or "") for r in r_list}
        if modes != ["w=1", "w=2", "w=4", "w=1_repeat"] or len(shas) != 1 or "" in shas:
            violations.append(f"WORKER_DETERMINISM_13_MISMATCH:{sec_k}")

    # 6. 33/33 faults (15) & Sealed baseline parity (16)
    if f15.get("status") != "PASS" or int(f15.get("passed_fault_controls", 0)) != 33 or int(f15.get("failed_fault_controls", 1)) != 0:
        violations.append("FAULT_SUITE_15_NOT_33_OF_33")
    nb16 = f16.get("si0_e00_primary_nb_summary") or {}
    fp16 = f16.get("per_decision_and_episode_fingerprints") or {}
    if (
        int(f16.get("a1_evaluated_count") or 0) != 56710
        or int(f16.get("a1_mismatches_count", 1)) != 0
        or int(f16.get("si8_separate_population_count") or 0) != 6375
        or int(f16.get("three_adapter_episodes_denominator") or 0) != 246
        or int(f16.get("three_adapter_mismatches_count", 1)) != 0
        or int(nb16.get("total_scheduled_episodes") or 0) != 242
        or int(nb16.get("closed_trades_count") or 0) != 166
        or int(nb16.get("wins_count") or 0) != 9
        or int(nb16.get("losses_count") or 0) != 157
        or float(nb16.get("total_realized_net_usd") or 0.0) != -345.8971
        or fp16.get("a1_56710_ids_sha256") != SEALED_R2_A1_56710_IDS_SHA256
        or fp16.get("si8_6375_ids_sha256") != SEALED_R2_SI8_6375_IDS_SHA256
        or fp16.get("si0_242_episode_matched_tuples_sha256") != SEALED_R2_SI0_242_TUPLES_SHA256
        or fp16.get("e00_bundle_sha256") != SEALED_R2_E00_BUNDLE_SHA256
    ):
        violations.append("BASELINE_PARITY_16_FINGERPRINT_OR_COUNT_MISMATCH")

    # 7. Gate receipts & independent re-evaluation of 17
    rcpt_dir = run_dir / "receipts"
    receipts_read: Dict[str, Any] = {}
    if rcpt_dir.is_dir():
        for rp in sorted(rcpt_dir.glob("G-KH02-*.json")):
            r_doc = json.loads(rp.read_text("utf-8")).get("receipt") or {}
            r_obj = EvidenceReceiptV17931219(**r_doc)
            receipts_read[rp.stem] = asdict(r_obj)
            if r_obj.status == "PASS" and not r_obj.is_valid_pass(
                expected_run_id=run_id, expected_tested_sha=tested_sha, run_dir=run_dir, reject_uniform_placeholder_hash=True
            ):
                violations.append(f"INVALID_GATE_RECEIPT:{rp.stem}:{r_obj.violations}")

    ov17 = f17.get("orthogonal_verdicts") or {}
    re_eval = evaluate_kh02_independent_gates_from_disk(
        run_dir, run_id, tested_sha, publication_status=str(ov17.get("PUBLICATION_VERDICT") or "PENDING_REMOTE_PUSH")
    )
    re_ov = re_eval["orthogonal_verdicts"]
    for vk in (
        "ENGINEERING_VERDICT", "REGRESSION_VERDICT", "RESOURCE_SAFETY_VERDICT",
        "BASELINE_PARITY_VERDICT", "DATA_VERDICT", "RESEARCH_ECONOMIC_VERDICT",
        "FRESH_OOS_VERDICT", "ACTIVATION_READY",
    ):
        if ov17.get(vk) != re_ov.get(vk):
            violations.append(f"FORGED_VERDICT_IN_17:{vk}:claimed={ov17.get(vk)}!=recomputed={re_ov.get(vk)}")
    if re_ov["ACTIVATION_READY"] != "NO":
        violations.append("ACTIVATION_READY_NOT_NO_IN_17")
    if run_id not in readme_txt or tested_sha not in readme_txt or run_id not in handover_txt or tested_sha not in handover_txt:
        violations.append("README_OR_HANDOVER_MISSING_RUN_ID_OR_SHA")

    # 8. Seal 18_independent_verifier_content_sha256_manifest.json (excluding only itself)
    delivered: Dict[str, Dict[str, Any]] = {}
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.name == MANIFEST_18_NAME:
            continue
        rel = str(p.relative_to(run_dir)).replace("\\", "/")
        delivered[rel] = {"byte_size": int(p.stat().st_size), "sha256": _file_sha256(p)}

    m18 = {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "run_id": run_id,
        "branch": f00.get("branch"),
        "head_at_start_sha": f00.get("head_at_start_sha"),
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "independent_verifier": {
            "verifier_script": "aurum-desk/tools/kh_02_entry_foundation_independent_verifier.py",
            "verifier_pid": verifier_pid,
            "orchestrator_pid": orch_pid,
            "separate_os_process_verified": verifier_pid != orch_pid,
            "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "PASS" if not violations else "FAIL",
            "recomputed_verdicts": re_ov,
            "recomputed_memory_samples_audit": recomp_mem,
            "violations_count": len(violations),
            "violations": violations,
        },
        "gate_receipts_readback": receipts_read,
        "circular_hash_exclusion_policy": f"{MANIFEST_18_NAME} hashes every other file in {run_dir.name} and excludes only itself.",
        "delivered_files_count": len(delivered),
        "delivered_files_aggregate_sha256": canonical_hash(delivered),
        "delivered_files": delivered,
    }
    m18_sha = write_json_durable_atomic(run_dir / MANIFEST_18_NAME, m18)

    return {
        "spec_version": SPEC_VERSION_KH02,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "verifier_pid": verifier_pid,
        "orchestrator_pid": orch_pid,
        "separate_os_process_verified": verifier_pid != orch_pid,
        "independent_verifier_status": "PASS" if not violations else "FAIL",
        "recomputed_verdicts": re_ov,
        "delivered_files_count": len(delivered),
        "manifest_18_sha256": m18_sha,
        "violations": violations,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="KH-02 Separate-OS-Process Independent Disk Verifier")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--skip-git-freeze", action="store_true")
    ap.add_argument("--skip-repo-pointers", action="store_true")
    args = ap.parse_args()

    res = verify_kh02_run_dir_from_disk(
        Path(args.run_dir),
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        check_git_source_freeze=not args.skip_git_freeze,
        check_live_repo_pointers=not args.skip_repo_pointers,
    )
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0 if res.get("independent_verifier_status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
