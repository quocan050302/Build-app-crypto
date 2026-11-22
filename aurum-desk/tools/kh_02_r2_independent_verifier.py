#!/usr/bin/env python3
"""
aurum-desk/tools/kh_02_r2_independent_verifier.py

AURUM DESK — KH-02.R2 Separate-OS-Process Independent Disk Verifier
SPEC VERSION: KH-02.R2-v1.0

Executes in a separate OS process (`verifier_pid != orchestrator_pid`), reads EVERY
closed artifact (`00`..`21`, `23`, `README_KH02_R2.md`, `HANDOVER_KH02_R2_VI.txt`,
`logs/runtime_memory_samples_0p5s.jsonl.gz`, `receipts/G-KH02R2-*.json`, and `junit/*.xml`
when executed) directly from disk, independently recomputes hashes, pairing conservation,
cost arithmetic, JUnit bijection, RAM/disk strict floors, OOS non-peek, and orthogonal gates,
and seals `22_independent_os_disk_verifier_sha256_manifest.json`.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import gzip
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
    PRIOR_R2_LATEST_PTR_REL,
    PRIOR_R2_LATEST_PTR_SHA256,
    audit_r3_memory_samples_jsonl_gz,
    detect_active_64mib_ram_floor_in_source,
)
from services.kh_02_entry_foundation_research_service import (
    PRIOR_R3_LATEST_PTR_REL,
    PRIOR_R3_LATEST_PTR_SHA256,
)
from services.kh_02_r2_research_integrity_service import (
    ACTIVATION_READY_KH02_R2,
    CANONICAL_DATASET_REL_PATH,
    CANONICAL_DATASET_SHA256_KH02_R2,
    GLOBAL_MAX_DESCENDANTS_KH02_R2,
    HARD_RAM_FLOOR_MIB_KH02_R2,
    MANDATORY_BRANCH_KH02_R2,
    PRIOR_KH02_R1_LATEST_PTR_REL,
    PRIOR_KH02_R1_LATEST_PTR_SHA256,
    PRIOR_KH02_R1_RUN_ID,
    PRIOR_KH02_R1_TESTED_SHA,
    R2_NEW_FILES_ALLOWLIST,
    REQUIRED_ARTIFACTS_KH02_R2,
    SCHEMA_VERSION_KH02_R2,
    SPEC_VERSION_KH02_R2,
    STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
    evaluate_r2_orthogonal_science_and_engineering_gates_from_disk,
    validate_r2_cost_scope_honesty,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from tools.fresh_junit_io import write_json_durable_atomic

MANIFEST_22_NAME = "22_independent_os_disk_verifier_sha256_manifest.json"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_jsonl_gz_rows(path: Path) -> int:
    cnt = 0
    with gzip.open(path, "rt", encoding="utf-8") as gz:
        for line in gz:
            if line.strip():
                json.loads(line)
                cnt += 1
    return cnt


def verify_r2_artifacts_independently_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_live_repo_pointers: bool = True,
    enforce_distinct_pid: bool = True,
    write_manifest: bool = True,
) -> Dict[str, Any]:
    """
    §03(14) -> Artifact 22:
    Independently audits EVERY closed artifact on disk without trusting orchestrator
    in-memory state. MUST fail on any tampered gate, forged verdict, broken arithmetic,
    missing ledger/JUnit, lowered RAM/disk floor, OOS peek, or stale R1 SHA.
    """
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {
            "spec_version": SPEC_VERSION_KH02_R2,
            "schema_version": SCHEMA_VERSION_KH02_R2,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"RUN_DIR_MISSING:{run_dir}"],
        }

    # 1. Check presence & non-emptiness of every required artifact (excluding 22 itself)
    for rel_req in REQUIRED_ARTIFACTS_KH02_R2:
        if rel_req == MANIFEST_22_NAME:
            continue
        fp = run_dir / rel_req
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_OR_EMPTY_ARTIFACT:{rel_req}")
    if violations:
        return {
            "spec_version": SPEC_VERSION_KH02_R2,
            "schema_version": SCHEMA_VERSION_KH02_R2,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": violations,
        }

    # 2. Load JSON & text artifacts directly from disk
    try:
        f00 = json.loads((run_dir / "00_preflight_branch_repo_sha_data_and_resources.json").read_text("utf-8"))
        f01 = json.loads((run_dir / "01_function_ownership_keep_as_is_diff.json").read_text("utf-8"))
        f02 = json.loads((run_dir / "02_r1_source_integrity_and_snapshot_registry.json").read_text("utf-8"))
        f03 = json.loads((run_dir / "03_frozen_r2_estimands_thresholds_hypotheses_and_falsification.json").read_text("utf-8"))
        f05 = json.loads((run_dir / "05_r1_target_eligibility_counterexamples_and_root_cause.json").read_text("utf-8"))
        f08 = json.loads((run_dir / "08_strata_common_support_balance_and_matching_quality.json").read_text("utf-8"))
        f09 = json.loads((run_dir / "09_chronological_1position_three_fill_paired_replays.json").read_text("utf-8"))
        f10 = json.loads((run_dir / "10_fill_time_net_risk_and_realized_tail_audit.json").read_text("utf-8"))
        f11 = json.loads((run_dir / "11_fixed_fill_same_ids_fee_spread_slippage_1x2x3x.json").read_text("utf-8"))
        f12 = json.loads((run_dir / "12_full_universe_rerisked_1x2x3x_and_fee_004_006.json").read_text("utf-8"))
        f13 = json.loads((run_dir / "13_m1_m15_h1_gaps_source_latency_quote_fee_provenance.json").read_text("utf-8"))
        f14 = json.loads((run_dir / "14_prospective_oos_escrow_collection_readiness_no_peek.json").read_text("utf-8"))
        f15 = json.loads((run_dir / "15_purged_walkforward_cluster_uncertainty_and_multiplicity.json").read_text("utf-8"))
        f16 = json.loads((run_dir / "16_full_pytest_collection_execution_junit_bijection.json").read_text("utf-8"))
        f17 = json.loads((run_dir / "17_async_w1_w2_w4_repeat_times_outputs_and_scheduler_policy.json").read_text("utf-8"))
        f18 = json.loads((run_dir / "18_ram_disk_pid_temp_telemetry.json").read_text("utf-8"))
        f19 = json.loads((run_dir / "19_real_red_green_fault_injections.json").read_text("utf-8"))
        f20 = json.loads((run_dir / "20_production_baseline_parity_and_prior_evidence_immutability.json").read_text("utf-8"))
        f21 = json.loads((run_dir / "21_orthogonal_engineering_science_economic_oos_gate_matrix.json").read_text("utf-8"))
        f23 = json.loads((run_dir / "23_tested_sha_evidence_publication_receipt_pre_push.json").read_text("utf-8"))
        readme_txt = (run_dir / "README_KH02_R2.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH02_R2_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {
            "spec_version": SPEC_VERSION_KH02_R2,
            "schema_version": SCHEMA_VERSION_KH02_R2,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"ARTIFACT_PARSE_ERROR:{exc}"],
        }

    # 3. Distinct OS PID, branch, run_id, and tested_executable_sha checks
    orch_pid = int(f00.get("orchestrator_pid") or -1)
    if enforce_distinct_pid and verifier_pid == orch_pid:
        violations.append(f"VERIFIER_SAME_OS_PID_AS_ORCHESTRATOR:{verifier_pid}")

    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    if f00.get("branch") != MANDATORY_BRANCH_KH02_R2:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if tested_sha != f00.get("tested_executable_sha") or tested_sha != f21.get("tested_executable_sha") or tested_sha != f23.get("tested_executable_sha"):
        violations.append("TESTED_EXECUTABLE_SHA_MISMATCH_ACROSS_00_21_23")
    if tested_sha == PRIOR_KH02_R1_TESTED_SHA:
        violations.append(f"STALE_KH02_R1_TESTED_SHA_REUSED:{tested_sha}")
    if run_id == PRIOR_KH02_R1_RUN_ID:
        violations.append(f"STALE_KH02_R1_RUN_ID_REUSED:{run_id}")
    if len(tested_sha) >= 12 and not run_id.endswith(f"_{tested_sha[:12]}"):
        violations.append(f"RUN_ID_MISSING_TESTED_SHA12_SUFFIX:{run_id}")

    # 4. Verify compressed JSONL ledgers (04, 06, 07) are valid & non-empty
    compressed_row_counts: Dict[str, int] = {}
    for gz_name in (
        "04_asof_unswept_level_ledger_and_mutation_proof.jsonl.gz",
        "06_r2_target_repair_full_opportunity_conservation.jsonl.gz",
        "07_paired_entry_geometry_factorial_control_mapping.jsonl.gz",
    ):
        try:
            rc = _count_jsonl_gz_rows(run_dir / gz_name)
            compressed_row_counts[gz_name] = rc
            if rc <= 0:
                violations.append(f"EMPTY_COMPRESSED_LEDGER:{gz_name}")
        except Exception as exc:
            violations.append(f"CORRUPTED_COMPRESSED_LEDGER:{gz_name}:{exc}")

    # Verify pairing ledger 07 explicitly includes pairing keys and never drops unmatched
    try:
        pair_rows_checked = 0
        with gzip.open(run_dir / "07_paired_entry_geometry_factorial_control_mapping.jsonl.gz", "rt", encoding="utf-8") as gz:
            for line in gz:
                if not line.strip():
                    continue
                row = json.loads(line)
                if "estimand" not in row or "opportunity_id" not in row or "pair_status" not in row:
                    violations.append("PAIRING_LEDGER_MISSING_REQUIRED_COLUMNS")
                    break
                pair_rows_checked += 1
        if pair_rows_checked == 0:
            violations.append("PAIRING_LEDGER_ZERO_ROWS")
    except Exception as exc:
        violations.append(f"PAIRING_LEDGER_AUDIT_ERROR:{exc}")

    # 5. Live repo immutability & safety checks
    if int(f01.get("locked_files_modified_count", 1)) != 0 or int(f01.get("modified_existing_files_count", 1)) != 0:
        violations.append("LOCKED_OR_EXISTING_FILES_MODIFIED")
    if len(f01.get("forbidden_order_import_hits") or []) > 0:
        violations.append("FORBIDDEN_ORDER_SERVICE_IMPORT_PRESENT")
    if f01.get("ACTIVATION_READY") != ACTIVATION_READY_KH02_R2 or f14.get("ACTIVATION_READY") != ACTIVATION_READY_KH02_R2 or (f21.get("orthogonal_verdicts") or {}).get("ACTIVATION_READY") != ACTIVATION_READY_KH02_R2:
        violations.append("UNSAFE_ACTIVATION_READY_VALUE")
    for k_mode in ("PRODUCTION", "LIVE", "PAPER", "AUTO"):
        if (f01.get("runtime_safety_modes") or {}).get(k_mode) != "OFF" or (f21.get("runtime_safety_modes") or {}).get(k_mode) != "OFF":
            violations.append(f"UNSAFE_RUNTIME_MODE_NOT_OFF:{k_mode}")
    if int(f14.get("OOS_EVALUATIONS", -1)) != 0 or int(f21.get("OOS_EVALUATIONS", -1)) != 0:
        violations.append("NONZERO_OOS_EVALUATIONS")

    if check_live_repo_pointers:
        ds_disk = REPO_ROOT / CANONICAL_DATASET_REL_PATH
        if not ds_disk.is_file() or _file_sha256(ds_disk) != CANONICAL_DATASET_SHA256_KH02_R2:
            violations.append("CANONICAL_DATASET_SHA_MUTATED_ON_DISK")
        for ptr_rel, exp_sha, lbl in (
            (PRIOR_KH02_R1_LATEST_PTR_REL, PRIOR_KH02_R1_LATEST_PTR_SHA256, "KH02_R1"),
            (PRIOR_R3_LATEST_PTR_REL, PRIOR_R3_LATEST_PTR_SHA256, "R3"),
            (PRIOR_R2_LATEST_PTR_REL, PRIOR_R2_LATEST_PTR_SHA256, "R2"),
        ):
            p = REPO_ROOT / ptr_rel
            if p.is_file() and _file_sha256(p) != exp_sha:
                violations.append(f"PRIOR_{lbl}_LATEST_RUN_PTR_MUTATED")
        c_live = CostAssumptions()
        if (
            abs(float(c_live.taker_fee_rate) - 0.0004) > 1e-12
            or abs(float(c_live.maker_fee_rate) - 0.0002) > 1e-12
            or abs(float(c_live.slippage_usd) - 0.10) > 1e-12
        ):
            violations.append("LIVE_COST_ASSUMPTIONS_MUTATED")
        r2_extra_src = {
            rel: (REPO_ROOT / rel).read_text(encoding="utf-8")
            for rel in R2_NEW_FILES_ALLOWLIST
            if (REPO_ROOT / rel).is_file() and rel.endswith(".py") and "/tests/" not in rel
        }
        hits_64 = detect_active_64mib_ram_floor_in_source(REPO_ROOT, extra_text_by_name=r2_extra_src)
        if hits_64.get("status") != "PASS" or int(hits_64.get("violations_count", 0)) > 0:
            violations.append(f"ACTIVE_64MIB_RAM_FLOOR_FOUND:{hits_64.get('violations_count')}")

    # 6. Independently verify fixed-fill (11) Decimal arithmetic & full-universe (12) scope honesty
    ok_ff, errs_ff = validate_r2_cost_scope_honesty(f11)
    if not ok_ff:
        violations.extend(errs_ff)
    ok_fu, errs_fu = validate_r2_cost_scope_honesty(f12)
    if not ok_fu:
        violations.extend(errs_fu)

    for ac, a11 in (f11.get("arms") or {}).items():
        g1 = Decimal(str(a11.get("gross_pnl_1x_usd", 0.0)))
        f1 = Decimal(str((a11.get("1x_base_0p0004") or {}).get("fees_usd", 0.0)))
        n1 = Decimal(str((a11.get("1x_base_0p0004") or {}).get("net_pnl_usd", 0.0)))
        if abs((g1 - f1) - n1) > Decimal("0.01"):
            violations.append(f"FIXED_FILL_ARITHMETIC_MISMATCH:{ac}:gross={g1},fee={f1},net={n1}")
        f06 = Decimal(str((a11.get("fee_only_0p0006") or {}).get("fees_usd", 0.0)))
        n06 = Decimal(str((a11.get("fee_only_0p0006") or {}).get("net_pnl_usd", 0.0)))
        if abs((g1 - f06) - n06) > Decimal("0.01"):
            violations.append(f"FIXED_FILL_0P0006_ARITHMETIC_MISMATCH:{ac}")

    # 7. Independently verify RAM/disk telemetry log from disk (`logs/runtime_memory_samples_0p5s.jsonl.gz`)
    mem_log = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    mem_audit: Dict[str, Any] = {}
    if not mem_log.is_file() or mem_log.stat().st_size == 0:
        violations.append("MISSING_MEMORY_TELEMETRY_LOG")
    else:
        mem_audit = audit_r3_memory_samples_jsonl_gz(mem_log)
        if mem_audit.get("status") != "PASS" or int(mem_audit.get("below_floor_samples_count", 1)) > 0:
            violations.append(f"MEMORY_TELEMETRY_BREACHED_512MIB_FLOOR:{mem_audit.get('status')}")
        if int(mem_audit.get("max_workers_observed_in_samples", 99)) > GLOBAL_MAX_DESCENDANTS_KH02_R2:
            violations.append(f"MEMORY_TELEMETRY_EXCEEDED_CHILD_CAP:{mem_audit.get('max_workers_observed_in_samples')}")

    min_disk = float(f18.get("min_disk_free_gib") or 0.0)
    reported_res_v = str((f21.get("orthogonal_verdicts") or {}).get("RESOURCE_SAFETY_VERDICT") or "")
    reported_eng_v = str((f21.get("orthogonal_verdicts") or {}).get("ENGINEERING_VERDICT") or "")
    if bool(f00.get("owner_disk_preflight_waiver", False)) or bool(f18.get("owner_disk_preflight_waiver", False)):
        violations.append("UNAPPROVED_AUTONOMOUS_OWNER_DISK_WAIVER_FORBIDDEN")
    if reported_res_v == "WAIVED_BY_OWNER_LOW_DISK" or reported_eng_v == "PASS_WITH_OWNER_DISK_WAIVER":
        violations.append(f"DISALLOWED_WAIVER_VERDICT:{reported_res_v}/{reported_eng_v}")
    if min_disk < STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2 and reported_res_v == "PASS":
        violations.append(f"FORGED_RESOURCE_PASS_BELOW_20P5_GIB:disk={min_disk}")

    # 8. Independently verify JUnit bijection IF full regression (16) claims PASS
    if f16.get("status") == "PASS":
        junit_dir = run_dir / "junit"
        xmls = sorted(junit_dir.glob("*.xml")) if junit_dir.is_dir() else []
        if not xmls:
            violations.append("FULL_REGRESSION_CLAIMS_PASS_WITH_ZERO_JUNIT_XML")
        else:
            cm16 = f16.get("collection_manifest") or {}
            sm16 = f16.get("shard_manifest") or {}
            if not cm16 or not sm16:
                violations.append("FULL_REGRESSION_MISSING_COLLECTION_OR_SHARD_MANIFEST")
            else:
                bj = verify_fresh_junit_bijection_v17931211(
                    run_dir,
                    cm16,
                    sm16,
                    expected_run_id=run_id,
                    expected_tested_sha=tested_sha,
                    mandatory_node_prefixes=("backend/tests/test_kh_02_r2",),
                    require_shard_schedule=True,
                )
                if bj.get("status") != "PASS":
                    violations.append(f"INDEPENDENT_JUNIT_BIJECTION_FAILED:{bj.get('violations')}")

    # 8b. Independently verify W1/W2/W4/W1_repeat determinism (17) and full parity (20) when executed
    if f17.get("status") == "PASS":
        if not bool(f17.get("pytest_subset_w1_w2_w4_repeat_identical", False)):
            violations.append("W1_W2_W4_PYTEST_SUBSET_NOT_IDENTICAL")
        if not bool(f17.get("r2_science_slice_w1_w2_w4_repeat_identical", False)):
            violations.append("W1_W2_W4_R2_SCIENCE_SLICE_NOT_IDENTICAL")
    if f20.get("BASELINE_PARITY_VERDICT") == "PASS":
        if int(f20.get("a1_mismatches_count", 1)) != 0 or int(f20.get("si8_mismatches_count", 1)) != 0 or int(f20.get("three_adapter_mismatches_count", 1)) != 0:
            violations.append("PARITY_MISMATCH_IN_20")
        if int(f20.get("d4_historical_closed_trades", 1)) != 0:
            violations.append("STRICT_D4_NONZERO_UNEXPECTED")

    # 9. Independently verify economic honesty (cannot forge PROVEN_EDGE on reused data!)
    rep_econ = str((f21.get("orthogonal_verdicts") or {}).get("RESEARCH_ECONOMIC_VERDICT") or "")
    if rep_econ == "PROVEN_EDGE":
        violations.append("FORGED_PROVEN_EDGE_ON_REUSED_DATA")

    # 10. Independently recompute ALL 20 gates & 12 verdicts from disk and compare against `21`!
    recomputed_21 = evaluate_r2_orthogonal_science_and_engineering_gates_from_disk(run_dir)
    stored_gates = {g["gate_id"]: g["status"] for g in (f21.get("gates") or ())}
    recomp_gates = {g["gate_id"]: g["status"] for g in (recomputed_21.get("gates") or ())}
    for gid, r_st in recomp_gates.items():
        if gid == "Gate 19":
            continue  # Gate 19 reads 22 which is sealed at the end of this verifier
        if stored_gates.get(gid) != r_st:
            violations.append(f"GATE_TAMPER_OR_DRIFT:{gid}:stored={stored_gates.get(gid)}!=recomputed={r_st}")

    stored_verdicts = dict(f21.get("orthogonal_verdicts") or {})
    recomp_verdicts = dict(recomputed_21.get("orthogonal_verdicts") or {})
    for vk, rv in recomp_verdicts.items():
        if stored_verdicts.get(vk) != rv:
            violations.append(f"VERDICT_TAMPER_OR_DRIFT:{vk}:stored={stored_verdicts.get(vk)}!=recomputed={rv}")

    # 11. Cross-view consistency check on README_KH02_R2.md and HANDOVER_KH02_R2_VI.txt
    if run_id not in readme_txt or run_id not in handover_txt:
        violations.append("CROSS_VIEW_RUN_ID_MISSING_IN_README_OR_HANDOVER")
    if "ACTIVATION_READY=NO" not in readme_txt or "ACTIVATION_READY=NO" not in handover_txt:
        violations.append("CROSS_VIEW_ACTIVATION_LOCK_MISSING")
    eng_v = recomp_verdicts.get("ENGINEERING_VERDICT", "")
    if eng_v and (eng_v not in readme_txt or eng_v not in handover_txt):
        violations.append(f"CROSS_VIEW_ENGINEERING_VERDICT_MISSING:{eng_v}")

    # 12. Independently hash EVERY closed file in run_dir recursively (non-circular: exclude 22)
    artifact_sha256_map: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(run_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(run_dir).as_posix()
        if rel == MANIFEST_22_NAME or rel.endswith(".tmp"):
            continue
        artifact_sha256_map[rel] = {
            "size_bytes": int(fp.stat().st_size),
            "sha256": _file_sha256(fp),
        }

    # Check receipts/G-KH02R2-*.json exist
    receipt_files = [k for k in artifact_sha256_map if k.startswith("receipts/G-KH02R2-") and k.endswith(".json")]
    if len(receipt_files) == 0:
        violations.append("NO_GATE_RECEIPTS_FOUND")

    status = "PASS" if len(violations) == 0 else "FAIL"
    manifest_payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "orchestrator_pid": orch_pid,
        "verifier_pid": verifier_pid,
        "distinct_os_process_verified": (verifier_pid != orch_pid),
        "independent_verifier_status": status,
        "recomputed_orthogonal_verdicts": recomp_verdicts,
        "recomputed_gates_summary": {
            "passed": recomputed_21["gates_passed"],
            "blocked": recomputed_21["gates_blocked"],
            "failed": recomputed_21["gates_failed"],
            "pending": recomputed_21["gates_pending"],
        },
        "compressed_ledger_row_counts": compressed_row_counts,
        "memory_telemetry_disk_audit": mem_audit,
        "receipt_files_count": len(receipt_files),
        "violations": violations,
        "artifact_count_hashed": len(artifact_sha256_map),
        "artifact_sha256_map": artifact_sha256_map,
    }
    manifest_payload["manifest_content_sha256"] = canonical_hash(
        {k: v for k, v in manifest_payload.items() if k != "manifest_content_sha256"}
    )

    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_22_NAME, manifest_payload)

    return manifest_payload


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KH-02.R2 Independent Separate-OS Disk Verifier")
    ap.add_argument("--run-dir", required=True, help="Path to KH-02.R2 run directory")
    ap.add_argument("--expected-run-id", default="", help="Expected run_id")
    ap.add_argument("--expected-tested-sha", default="", help="Expected tested executable SHA")
    ap.add_argument("--skip-live-repo-pointers", action="store_true", help="Skip live repo pointer checks (for isolated synthetic tamper tests)")
    ap.add_argument("--no-write-manifest", action="store_true", help="Do not write manifest 22")
    args = ap.parse_args(argv)

    res = verify_r2_artifacts_independently_from_disk(
        Path(args.run_dir),
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        check_live_repo_pointers=not args.skip_live_repo_pointers,
        enforce_distinct_pid=True,
        write_manifest=not args.no_write_manifest,
    )
    print(
        json.dumps(
            {
                "independent_verifier_status": res["independent_verifier_status"],
                "verifier_pid": res["verifier_pid"],
                "distinct_os_process_verified": res.get("distinct_os_process_verified"),
                "recomputed_orthogonal_verdicts": res.get("recomputed_orthogonal_verdicts"),
                "violations": res.get("violations"),
                "artifact_count_hashed": res.get("artifact_count_hashed"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if res["independent_verifier_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
