#!/usr/bin/env python3
"""
aurum-desk/tools/kh_01b_r1_data_safety_independent_verifier.py

AURUM DESK — KH-01B.R1.DSC Separate-OS-Process Independent Disk Verifier (§3.E, §7, §8 G-DS-14 & §9 Phase 5).
Runs in a fresh OS process (`verifier_pid != orchestrator_pid`), reopens closed JSON artifacts,
compressed ledgers, JUnit XMLs (when executed), and dynamic cryptographic receipts from disk,
independently recomputes 6-TF gap conservation, window-slot conservation, Decimal fee sensitivity,
strict 20 GiB disk enforcement, OOS escrow/runtime OFF locks, and cross-view README/Handover
consistency, and seals `13_manifest_sha256_independent_os_readback.json`.
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
from typing import Any, Dict, List, Mapping

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from domain_calculator import CostAssumptions
from services.kh_01b_r1_data_safety_closure_service import (
    CANONICAL_DATASET_SHA256_KH01B_R1,
    MANDATORY_BRANCH_KH01B_R1,
    PRIOR_DSC_V1_BLOCKED_RUN_ID,
    PRIOR_DSC_V1_TESTED_SHA,
    REQUIRED_ARTIFACTS_KH01B_R1_DSC,
    REQUIRED_ARTIFACTS_KH01B_R1_DSC_R2,
    SCHEMA_VERSION_KH01B_R1_DSC,
    SCHEMA_VERSION_KH01B_R1_DSC_R2,
    SPEC_VERSION_KH01B_R1_DSC,
    SPEC_VERSION_KH01B_R1_DSC_R2,
    compute_kh01b_r1_fee_sensitivity,
    evaluate_kh01b_r1_dsc_gates_from_disk,
    evaluate_kh01b_r1_dsc_r2_gates_from_disk,
    validate_latest_run_pointer_not_blocked,
    verify_kh01b_r1_dsc_cross_view_consistency,
    verify_kh01b_r1_dsc_r2_cross_view_consistency,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
    verify_source_freeze_v17931211,
)
from services.v17_9_3_1_2_19_truth_and_core_repair_service import EvidenceReceiptV17931219
from tools.fresh_junit_io import write_json_durable_atomic
from tools.run_kh_01a_verification import V130_RUN, iter_jsonl_gz

MANIFEST_13_NAME = "13_manifest_sha256_independent_os_readback.json"
MANIFEST_15_R2_NAME = "15_independent_verifier_manifest_and_content_sha256.json"
MANIFEST_14_R3_NAME = "14_sha256_manifest_independent_os_readback.json"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_kh_01b_r1_dsc_run_dir_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_git_source_freeze: bool = True,
    recompute_v130_fees: bool = True,
) -> Dict[str, Any]:
    run_dir = run_dir.resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"RUN_DIR_NOT_FOUND:{run_dir}"],
        }

    # 1. Verify all required closure files exist and are non-empty (excluding 13 sealed here)
    for rel_req in REQUIRED_ARTIFACTS_KH01B_R1_DSC:
        if rel_req == MANIFEST_13_NAME:
            continue
        fp = run_dir / rel_req
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_REQUIRED_DSC_ARTIFACT:{rel_req}")

    if violations:
        return {
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": violations,
        }

    try:
        f00 = json.loads((run_dir / "00_preflight_branch_head_dataset_disk_volumes.json").read_text("utf-8"))
        f01 = json.loads((run_dir / "01_immutable_baseline_and_keep_as_is_callgraph.json").read_text("utf-8"))
        f02 = json.loads((run_dir / "02_gap_schema_mapping_red_green_and_six_tf_completeness.json").read_text("utf-8"))
        f03 = json.loads((run_dir / "03_pagination_cursor_order_boundary_duplicates_negative_controls.json").read_text("utf-8"))
        f04 = json.loads((run_dir / "04_extended_dev_requested_vs_observed_window_coverage.json").read_text("utf-8"))
        f05 = json.loads((run_dir / "05_bitget_origin_fee_quote_calendar_provenance.json").read_text("utf-8"))
        f06 = json.loads((run_dir / "06_baseline_0p04_vs_public_0p06_fee_sensitivity_caveats.json").read_text("utf-8"))
        f07 = json.loads((run_dir / "07_strict_20gib_disk_policy_injections_and_resource_telemetry.json").read_text("utf-8"))
        f08 = json.loads((run_dir / "08_async_w1_w2_w4_repeat_determinism_and_speed.json").read_text("utf-8"))
        f09 = json.loads((run_dir / "09_full_regression_collected_executed_junit_exit_bijection.json").read_text("utf-8"))
        f10 = json.loads((run_dir / "10_backtest_original_parity_three_fill_and_adapter_fingerprints.json").read_text("utf-8"))
        f11 = json.loads((run_dir / "11_data_oos_partition_never_evaluated_verdict.json").read_text("utf-8"))
        f12 = json.loads((run_dir / "12_independent_verifier_fault_injections.json").read_text("utf-8"))
        f14 = json.loads((run_dir / "14_final_orthogonal_verdicts_and_blockers.json").read_text("utf-8"))
        readme_txt = (run_dir / "README_KH01B_R1_DATA_SAFETY_CLOSURE.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH01B_R1_DATA_SAFETY_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"ARTIFACT_JSON_PARSE_ERROR:{type(exc).__name__}:{exc}"],
        }

    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    orchestrator_pid = int(f00.get("orchestrator_pid") or -1)
    if verifier_pid == orchestrator_pid:
        violations.append(f"VERIFIER_NOT_SEPARATE_OS_PROCESS:{verifier_pid}")

    # 2. Branch, canonical dataset & locked CostAssumptions checks
    if f00.get("branch") != MANDATORY_BRANCH_KH01B_R1:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if f00.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH01B_R1:
        violations.append(f"DATASET_SHA_MISMATCH_IN_00:{f00.get('canonical_dataset_sha256')}")
    ds_disk = APP_ROOT / "backtest/historical_dataset_XAUUSDT.json.gz"
    if ds_disk.is_file() and _file_sha256(ds_disk) != CANONICAL_DATASET_SHA256_KH01B_R1:
        violations.append("CANONICAL_DATASET_MODIFIED_ON_DISK")
    if int(f01.get("locked_files_modified_count", 1)) != 0:
        violations.append("LOCKED_PRODUCTION_OR_PRIOR_RESEARCH_FILES_MODIFIED")

    live_costs = CostAssumptions()
    if live_costs.maker_fee_rate != 0.0002 or live_costs.taker_fee_rate != 0.0004 or live_costs.slippage_usd != 0.10:
        violations.append("LIVE_DOMAIN_CALCULATOR_COST_DEFAULTS_MUTATED")

    if check_git_source_freeze:
        sf = verify_source_freeze_v17931211(REPO_ROOT, tested_sha)
        if sf.get("status") != "PASS":
            violations.extend(f"SOURCE_FREEZE:{v}" for v in (sf.get("violations") or []))

    # 3. Independent 6-TF gap schema & sum conservation check on 02
    for sec_name in ("canonical_dataset_six_tf_gap_summary", "extended_dev_snapshot_six_tf_gap_summary"):
        tfs_sec = (f02.get(sec_name) or {}).get("timeframes") or {}
        for tf_k in ("1m", "5m", "15m", "1H", "4H", "1D"):
            ge = tfs_sec.get(tf_k)
            if not isinstance(ge, Mapping):
                violations.append(f"GAP_02_MISSING_TF:{sec_name}:{tf_k}")
                continue
            if ge.get("actual_bars") is None or ge.get("total_missing_bars") is None or ge.get("unknown_unverified_missing_bars") is None:
                violations.append(f"GAP_02_NULL_NUMERIC:{sec_name}:{tf_k}")
                continue
            mb_cat = ge.get("missing_bars_by_category")
            ev_cat = ge.get("gap_events_by_category")
            if not isinstance(mb_cat, Mapping) or not isinstance(ev_cat, Mapping):
                violations.append(f"GAP_02_MALFORMED_CATEGORY_DICTS:{sec_name}:{tf_k}")
                continue
            if sum(int(v) for v in mb_cat.values()) != int(ge["total_missing_bars"]):
                violations.append(f"GAP_02_CATEGORY_SUM_MISMATCH:{sec_name}:{tf_k}")
            if "gap_class_counts" in ge:
                violations.append(f"GAP_02_INVENTED_LEGACY_GAP_CLASS_COUNTS:{sec_name}:{tf_k}")

    # 4. Independent window-slot conservation & partial-coverage honesty check on 04
    cov_map = f04.get("coverage_by_timeframe") or {}
    has_missing_any = False
    for tf_k in ("1m", "5m", "15m", "1H", "4H", "1D"):
        c = cov_map.get(tf_k)
        if not isinstance(c, Mapping):
            violations.append(f"COVERAGE_04_MISSING_TF:{tf_k}")
            continue
        exp_s = int(c.get("expected_closed_slots", -1))
        obs_s = int(c.get("observed_unique_closed_slots", -2))
        miss_s = int(c.get("missing_slots", -3))
        lead_s = int(c.get("leading_missing_slots", -4))
        int_s = int(c.get("internal_missing_slots", -5))
        trail_s = int(c.get("trailing_missing_slots", -6))
        if obs_s + miss_s != exp_s:
            violations.append(f"COVERAGE_04_SLOT_CONSERVATION_TAMPERED:{tf_k}:{obs_s}+{miss_s}!={exp_s}")
        if lead_s + int_s + trail_s != miss_s:
            violations.append(f"COVERAGE_04_MISSING_SPLIT_TAMPERED:{tf_k}:{lead_s}+{int_s}+{trail_s}!={miss_s}")
        if miss_s > 0:
            has_missing_any = True
            if c.get("coverage_verdict") == "VERIFIED_COMPLETE":
                violations.append(f"COVERAGE_04_PARTIAL_MISLABELED_COMPLETE:{tf_k}")
    if has_missing_any and f04.get("reclassified_DATA_ACQUISITION_VERDICT") == "VERIFIED_COMPLETE":
        violations.append("COVERAGE_04_OVERALL_MISLABELED_VERIFIED_COMPLETE")

    # 5. Independent Decimal fee sensitivity recomputation & provenance check on 05 & 06
    ct_05 = f05.get("public_contract_fee_snapshot_audit") or {}
    if ct_05.get("actual_account_effective_fee_tier") != "UNKNOWN_NOT_LOCALLY_VERIFIED":
        violations.append("FEE_05_UNVERIFIED_ACCOUNT_TIER_CLAIMED")
    if f06.get("actual_account_effective_fee_tier") != "UNKNOWN_NOT_LOCALLY_VERIFIED":
        violations.append("FEE_06_UNVERIFIED_ACCOUNT_TIER_CLAIMED")

    fo_06 = f06.get("FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS") or {}
    for rk in ("0.0004", "0.0006"):
        sc = fo_06.get(rk)
        if not isinstance(sc, Mapping):
            violations.append(f"FEE_06_MISSING_SCENARIO:{rk}")
            continue
        g_u = float(sc.get("gross_pnl_1x_usd") or 0.0)
        f_u = float(sc.get("total_fees_1x_usd") or 0.0)
        n_u = float(sc.get("net_pnl_1x_usd") or 999999.0)
        if abs(round(g_u - f_u, 4) - n_u) > 1e-3:
            violations.append(f"FEE_06_ALGEBRAIC_PNL_TAMPERED:{rk}:{g_u}-{f_u}!={n_u}")
        if n_u >= 0.0:
            violations.append(f"FEE_06_FABRICATED_PROFITABILITY:{rk}:{n_u}")

    if recompute_v130_fees:
        nb_p = V130_RUN / "SI0_OFF_BASELINE_NEXT_BAR_OPEN_BID_ASK_trades.jsonl.gz"
        sp2_p = V130_RUN / "SI0_OFF_BASELINE_STRESS_WIDER_SPREAD_SLIPPAGE_2X_trades.jsonl.gz"
        if nb_p.is_file() and sp2_p.is_file():
            recomp_06 = compute_kh01b_r1_fee_sensitivity(
                list(iter_jsonl_gz(nb_p)),
                list(iter_jsonl_gz(sp2_p)),
            )
            for rk in ("0.0004", "0.0006"):
                exp_net = (recomp_06.get("FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS") or {}).get(rk, {}).get("net_pnl_1x_usd")
                obs_net = (fo_06.get(rk) or {}).get("net_pnl_1x_usd")
                if exp_net is None or obs_net is None or abs(float(exp_net) - float(obs_net)) > 1e-4:
                    violations.append(f"FEE_06_MISMATCH_VS_RAW_V130_LEDGER:{rk}:obs={obs_net}!=recomp={exp_net}")

    # 6. Disk safety, JUnit anti-fabrication, OOS escrow & runtime OFF locks
    d_pol = f00.get("disk_reserve_policy") or {}
    disk_verdict = str(d_pol.get("disk_safety_verdict") or "FAIL")
    ov_14 = f14.get("orthogonal_verdicts") or {}

    if bool(d_pol.get("lowered_reserve_without_approval", True)) or float(d_pol.get("enforced_reserve_gib") or 0.0) < 20.0:
        violations.append("DISK_RESERVE_LOWERED_BELOW_STRICT_20GIB")

    if disk_verdict == "BLOCKED_LOW_DISK":
        if ov_14.get("ENGINEERING_CLOSURE_VERDICT") == "PASS":
            violations.append("FORGED_ENGINEERING_PASS_UNDER_BLOCKED_LOW_DISK")
        if (run_dir / "junit").is_dir() and list((run_dir / "junit").glob("*.xml")):
            violations.append("FAKE_JUNIT_XML_PRESENT_UNDER_BLOCKED_LOW_DISK")
        for fn_k, f_doc in (("08", f08), ("09", f09), ("10", f10)):
            if f_doc.get("execution_status") != "NOT_RUN_BLOCKED_LOW_DISK":
                violations.append(f"HEAVY_STAGE_NOT_HONESTLY_MARKED_NOT_RUN:{fn_k}:{f_doc.get('execution_status')}")
    elif disk_verdict == "PASS_STRICT_20GIB":
        if float(d_pol.get("preflight_baseline_free_gib") or 0.0) < 20.5:
            violations.append("FALSE_PASS_STRICT_20GIB_BELOW_20P5_PREFLIGHT")
        jb = verify_fresh_junit_bijection_v17931211(
            run_dir,
            f09.get("collection_manifest") or {},
            f09.get("shard_manifest") or {},
            expected_run_id=run_id,
            expected_tested_sha=tested_sha,
            mandatory_node_prefixes=(
                "backend/tests/test_kh_01b_r1",
                "backend/tests/test_kh_01b",
                "backend/tests/test_kh_01a",
                "backend/tests/test_v17_9_3_1_2",
            ),
            require_shard_schedule=True,
        )
        if jb.get("status") != "PASS":
            violations.extend(f"JUNIT_BIJECTION:{v}" for v in (jb.get("violations") or []))
    else:
        violations.append(f"INVALID_DISK_VERDICT:{disk_verdict}")

    if ov_14.get("ACTIVATION_READY") != "NO":
        violations.append("ACTIVATION_READY_NOT_NO")
    if ov_14.get("KH01C_ADVANCEMENT_VERDICT") != "NO_ECONOMIC_ADVANCEMENT_CANDIDATE":
        violations.append("ILLEGAL_KH01C_CANDIDATE_PROMOTION_IN_DSC")
    modes_14 = ov_14.get("RUNTIME_SAFETY_MODES") or {}
    if any(modes_14.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
        violations.append(f"UNSAFE_RUNTIME_MODES_IN_14:{modes_14}")

    esc_11 = f11.get("future_oos_escrow") or {}
    if esc_11.get("FRESH_OOS_VERDICT") not in ("BLOCKED_NO_UNTOUCHED_DATA", "SEALED_UNTOUCHED_NOT_EVALUATED"):
        violations.append(f"ILLEGAL_FRESH_OOS_VERDICT:{esc_11.get('FRESH_OOS_VERDICT')}")
    if int(esc_11.get("strategy_evaluation_count_total", 1)) != 0:
        violations.append("NONZERO_OOS_STRATEGY_EVALUATION_COUNT")

    # 7. Verify dynamic gate receipts on disk & re-evaluate G-DS-01..G-DS-15 from scratch
    rcpt_dir = run_dir / "receipts"
    receipts_read: Dict[str, EvidenceReceiptV17931219] = {}
    if rcpt_dir.is_dir():
        for rp in sorted(rcpt_dir.glob("G-DS-*.json")):
            r_doc = json.loads(rp.read_text("utf-8")).get("receipt") or {}
            r_obj = EvidenceReceiptV17931219(**r_doc)
            receipts_read[rp.stem] = r_obj
            if r_obj.status == "PASS" and not r_obj.is_valid_pass(
                expected_run_id=run_id,
                expected_tested_sha=tested_sha,
                run_dir=run_dir,
                reject_uniform_placeholder_hash=True,
            ):
                violations.append(f"INVALID_GATE_RECEIPT:{rp.stem}:{r_obj.violations}")

    re_eval_14 = evaluate_kh01b_r1_dsc_gates_from_disk(
        run_dir,
        run_id,
        tested_sha,
        publication_status=str(ov_14.get("PUBLICATION_VERDICT") or "PENDING_REMOTE_PUSH"),
    )
    re_ov = re_eval_14["orthogonal_verdicts"]
    if re_ov["ENGINEERING_CLOSURE_VERDICT"] == "FAIL":
        failed_gids = [g["gate_id"] for g in re_eval_14["gates"] if g["status"] == "FAIL"]
        violations.append(f"INDEPENDENT_GATE_REEVALUATION_FAILED:{failed_gids}")
    for vk in (
        "ENGINEERING_CLOSURE_VERDICT",
        "DISK_SAFETY_VERDICT",
        "DATA_ACQUISITION_VERDICT",
        "FULL_REGRESSION_VERDICT",
        "BACKTEST_VERDICT",
        "WORKER_DETERMINISM_VERDICT",
        "KH01C_ADVANCEMENT_VERDICT",
        "FRESH_OOS_VERDICT",
        "ACTIVATION_READY",
    ):
        if ov_14.get(vk) != re_ov.get(vk):
            violations.append(f"FORGED_VERDICT_IN_14:{vk}:claimed={ov_14.get(vk)}!=recomputed={re_ov.get(vk)}")

    cv = verify_kh01b_r1_dsc_cross_view_consistency(
        readme_text=readme_txt,
        handover_text=handover_txt,
        f00=f00,
        f06=f06,
        f12=f12,
        f14=f14,
    )
    if cv["status"] != "PASS":
        violations.extend(f"CROSS_VIEW:{v}" for v in cv["violations"])

    # 8. Seal 13_manifest_sha256_independent_os_readback.json
    delivered_files: Dict[str, Dict[str, Any]] = {}
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.name == MANIFEST_13_NAME:
            continue
        rel = str(p.relative_to(run_dir)).replace("\\", "/")
        delivered_files[rel] = {
            "byte_size": int(p.stat().st_size),
            "sha256": _file_sha256(p),
        }

    manifest_13_doc = {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC,
        "run_id": run_id,
        "branch": f00.get("branch"),
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01B_R1,
        "independent_verifier": {
            "verifier_script": "aurum-desk/tools/kh_01b_r1_data_safety_independent_verifier.py",
            "verifier_pid": verifier_pid,
            "orchestrator_pid": orchestrator_pid,
            "separate_os_process_verified": verifier_pid != orchestrator_pid,
            "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "PASS" if not violations else "FAIL",
            "recomputed_engineering_closure_verdict": re_ov["ENGINEERING_CLOSURE_VERDICT"],
            "recomputed_disk_safety_verdict": re_ov["DISK_SAFETY_VERDICT"],
            "violations_count": len(violations),
            "violations": violations,
        },
        "gate_receipts_readback": {k: asdict(v) for k, v in sorted(receipts_read.items())},
        "circular_hash_exclusion_policy": (
            f"{MANIFEST_13_NAME} hashes every other file in {run_dir.name} and excludes only itself."
        ),
        "delivered_files_count": len(delivered_files),
        "delivered_files_aggregate_sha256": canonical_hash(delivered_files),
        "delivered_files": delivered_files,
    }
    manifest_sha = write_json_durable_atomic(run_dir / MANIFEST_13_NAME, manifest_13_doc)

    return {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "verifier_pid": verifier_pid,
        "orchestrator_pid": orchestrator_pid,
        "separate_os_process_verified": verifier_pid != orchestrator_pid,
        "independent_verifier_status": "PASS" if not violations else "FAIL",
        "recomputed_engineering_closure_verdict": re_ov["ENGINEERING_CLOSURE_VERDICT"],
        "recomputed_disk_safety_verdict": re_ov["DISK_SAFETY_VERDICT"],
        "delivered_files_count": len(delivered_files),
        "manifest_13_sha256": manifest_sha,
        "violations": violations,
    }


def verify_kh_01b_r1_dsc_r2_run_dir_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_git_source_freeze: bool = True,
    recompute_v130_fees: bool = True,
) -> Dict[str, Any]:
    """
    KH-01B.R1.DSC.R2 Separate-OS-Process Independent Disk Verifier (§3, §4 Phase 6, §5 G-DS-13/14 & R2-F).
    Re-reads closed R2 artifacts (`00`..`14`), compressed ledgers, JUnit XMLs (when executed), and
    dynamic cryptographic receipts from disk; independently recomputes 6-TF gap conservation,
    48h window-slot conservation & origin HTTP receipts, Decimal fee sensitivity (`0.0004` vs `0.0006`
    fixed-fill vs `SI0-242` rerisked scope guard), multi-mount strict `>= 20.5 GiB` disk policy,
    SHA execution graph (`07`), `w=1/2/4/1_repeat` canonical hashes (`08`), JUnit bijection (`09`),
    full `56,710 + 6,375` & `SI0 242/166` 3-fill / 3-adapter parity (`10`), OOS/runtime OFF locks (`12`),
    prior artifact immutability (`13`), and seals `15_independent_verifier_manifest_and_content_sha256.json`.
    """
    run_dir = run_dir.resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"RUN_DIR_NOT_FOUND:{run_dir}"],
        }

    for rel_req in REQUIRED_ARTIFACTS_KH01B_R1_DSC_R2:
        if rel_req == MANIFEST_15_R2_NAME:
            continue
        fp = run_dir / rel_req
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_REQUIRED_DSC_R2_ARTIFACT:{rel_req}")

    if violations:
        return {
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": violations,
        }

    try:
        f00 = json.loads((run_dir / "00_preflight_branch_head_code_sha_disk_mounts_dataset.json").read_text("utf-8"))
        f01 = json.loads((run_dir / "01_function_callgraph_and_immutability_manifest.json").read_text("utf-8"))
        f02 = json.loads((run_dir / "02_gap_schema_six_tf_and_actual_missingness.json").read_text("utf-8"))
        f03 = json.loads((run_dir / "03_pagination_decimal_dedupe_cursor_and_red_green.json").read_text("utf-8"))
        f04 = json.loads((run_dir / "04_requested_window_coverage_origin_and_online_capture_status.json").read_text("utf-8"))
        f05 = json.loads((run_dir / "05_fee_0p04_0p06_fixed_fill_and_rerisked_truth.json").read_text("utf-8"))
        f06 = json.loads((run_dir / "06_strict_disk_recovery_actions_and_resource_monitor.json").read_text("utf-8"))
        f07 = json.loads((run_dir / "07_sha_lock_current_HEAD_vs_tested_executable_execution_graph.json").read_text("utf-8"))
        f08 = json.loads((run_dir / "08_async_w1_w2_w4_w1repeat_hashes_timing_and_worker_manifest.json").read_text("utf-8"))
        f09 = json.loads((run_dir / "09_full_regression_junit_collected_executed_bijection.json").read_text("utf-8"))
        f10 = json.loads((run_dir / "10_full_baseline_backtest_three_fill_1pos_adapter_and_fingerprint_parity.json").read_text("utf-8"))
        f11 = json.loads((run_dir / "11_negative_controls_fault_injection_independent_verifier.json").read_text("utf-8"))
        f12 = json.loads((run_dir / "12_OOS_untouched_and_activation_off_receipt.json").read_text("utf-8"))
        f13 = json.loads((run_dir / "13_prior_artifact_and_code_immutability_diffs.json").read_text("utf-8"))
        f14 = json.loads((run_dir / "14_final_orthogonal_verdicts_gate_matrix_blockers.json").read_text("utf-8"))
        readme_txt = (run_dir / "README_KH01B_R1_DSC_R2.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH01B_R1_DSC_R2_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"ARTIFACT_JSON_PARSE_ERROR:{type(exc).__name__}:{exc}"],
        }

    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    orchestrator_pid = int(f00.get("orchestrator_pid") or -1)
    if verifier_pid == orchestrator_pid:
        violations.append(f"VERIFIER_NOT_SEPARATE_OS_PROCESS:{verifier_pid}")

    # 1. Branch, SHA execution graph (07), canonical dataset & CostAssumptions locks
    if f00.get("branch") != MANDATORY_BRANCH_KH01B_R1:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if f00.get("tested_executable_sha") != tested_sha or f07.get("tested_executable_sha") != tested_sha or f14.get("tested_executable_sha") != tested_sha:
        violations.append("TESTED_EXECUTABLE_SHA_MISMATCH_ACROSS_00_07_14")
    if tested_sha == PRIOR_DSC_V1_TESTED_SHA or run_id == PRIOR_DSC_V1_BLOCKED_RUN_ID:
        violations.append("STALE_PRIOR_DSC_V1_SHA_OR_RUN_ID_REUSED")
    if len(tested_sha) >= 12 and not run_id.endswith(f"_{tested_sha[:12]}"):
        violations.append(f"RUN_ID_MISSING_TESTED_SHA12_SUFFIX:{run_id}:{tested_sha[:12]}")
    if f00.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH01B_R1:
        violations.append(f"DATASET_SHA_MISMATCH_IN_00:{f00.get('canonical_dataset_sha256')}")
    ds_disk = APP_ROOT / "backtest/historical_dataset_XAUUSDT.json.gz"
    if ds_disk.is_file() and _file_sha256(ds_disk) != CANONICAL_DATASET_SHA256_KH01B_R1:
        violations.append("CANONICAL_DATASET_MODIFIED_ON_DISK")
    if int(f01.get("locked_files_modified_count", 1)) != 0:
        violations.append("LOCKED_PRODUCTION_OR_PRIOR_RESEARCH_FILES_MODIFIED")

    live_costs = CostAssumptions()
    if live_costs.maker_fee_rate != 0.0002 or live_costs.taker_fee_rate != 0.0004 or live_costs.slippage_usd != 0.10:
        violations.append("LIVE_DOMAIN_CALCULATOR_COST_DEFAULTS_MUTATED")

    if check_git_source_freeze:
        sf = verify_source_freeze_v17931211(REPO_ROOT, tested_sha)
        if sf.get("status") != "PASS":
            violations.extend(f"SOURCE_FREEZE:{v}" for v in (sf.get("violations") or []))

    # 2. 6-TF gap schema & sum conservation check on 02
    for sec_name in ("canonical_dataset_six_tf_gap_summary", "extended_dev_snapshot_six_tf_gap_summary"):
        tfs_sec = (f02.get(sec_name) or {}).get("timeframes") or {}
        for tf_k in ("1m", "5m", "15m", "1H", "4H", "1D"):
            ge = tfs_sec.get(tf_k)
            if not isinstance(ge, Mapping):
                violations.append(f"GAP_02_MISSING_TF:{sec_name}:{tf_k}")
                continue
            if ge.get("actual_bars") is None or ge.get("total_missing_bars") is None or ge.get("unknown_unverified_missing_bars") is None:
                violations.append(f"GAP_02_NULL_NUMERIC:{sec_name}:{tf_k}")
                continue
            mb_cat = ge.get("missing_bars_by_category")
            ev_cat = ge.get("gap_events_by_category")
            if not isinstance(mb_cat, Mapping) or not isinstance(ev_cat, Mapping):
                violations.append(f"GAP_02_MALFORMED_CATEGORY_DICTS:{sec_name}:{tf_k}")
                continue
            if sum(int(v) for v in mb_cat.values()) != int(ge["total_missing_bars"]):
                violations.append(f"GAP_02_CATEGORY_SUM_MISMATCH:{sec_name}:{tf_k}")

    # 3. Window-slot conservation, old 1600/2880 M1 honesty & online HTTP receipt SHA check on 04
    cov_map = f04.get("coverage_by_timeframe") or {}
    has_missing_old = False
    for tf_k in ("1m", "5m", "15m", "1H", "4H", "1D"):
        c = cov_map.get(tf_k)
        if not isinstance(c, Mapping):
            violations.append(f"COVERAGE_04_MISSING_TF:{tf_k}")
            continue
        exp_s = int(c.get("expected_closed_slots", -1))
        obs_s = int(c.get("observed_unique_closed_slots", -2))
        miss_s = int(c.get("missing_slots", -3))
        lead_s = int(c.get("leading_missing_slots", -4))
        int_s = int(c.get("internal_missing_slots", -5))
        trail_s = int(c.get("trailing_missing_slots", -6))
        if obs_s + miss_s != exp_s:
            violations.append(f"COVERAGE_04_SLOT_CONSERVATION_TAMPERED:{tf_k}:{obs_s}+{miss_s}!={exp_s}")
        if lead_s + int_s + trail_s != miss_s:
            violations.append(f"COVERAGE_04_MISSING_SPLIT_TAMPERED:{tf_k}:{lead_s}+{int_s}+{trail_s}!={miss_s}")
        if miss_s > 0:
            has_missing_old = True
            if c.get("coverage_verdict") == "VERIFIED_COMPLETE":
                violations.append(f"COVERAGE_04_PARTIAL_MISLABELED_COMPLETE:{tf_k}")
    if has_missing_old and f04.get("reclassified_DATA_ACQUISITION_VERDICT") == "VERIFIED_COMPLETE":
        violations.append("COVERAGE_04_OLD_SNAPSHOT_MISLABELED_VERIFIED_COMPLETE")
    if int(f04.get("unbacked_bars_without_origin_receipt_count", 1)) != 0:
        violations.append("COVERAGE_04_UNBACKED_BAR_RECEIPTS")

    if bool(f04.get("online_capture_executed", False)):
        on_sum = f04.get("online_capture_summary") or {}
        on_v = str(on_sum.get("DATA_ACQUISITION_VERDICT") or "")
        if on_v in ("VERIFIED_COMPLETE", "VERIFIED_PARTIAL"):
            snap_rel = str(on_sum.get("dev_extended_snapshot_rel_path") or "")
            rcpt_rel = str(on_sum.get("origin_http_receipts_rel_path") or "")
            snap_p = run_dir / snap_rel
            rcpt_p = run_dir / rcpt_rel
            if not snap_p.is_file() or _file_sha256(snap_p) != on_sum.get("dev_extended_snapshot_sha256"):
                violations.append("ONLINE_SNAPSHOT_SHA256_TAMPERED_OR_MISSING")
            if not rcpt_p.is_file() or _file_sha256(rcpt_p) != on_sum.get("origin_http_receipts_sha256"):
                violations.append("ONLINE_HTTP_RECEIPTS_SHA256_TAMPERED_OR_MISSING")
            else:
                rcpt_doc = json.loads(rcpt_p.read_text("utf-8"))
                known_http_shas = {str(x.get("raw_http_sha256") or "") for x in (rcpt_doc.get("receipts") or [])}
                if snap_p.is_file():
                    for row in iter_jsonl_gz(snap_p):
                        if str(row.get("origin_page_http_sha256") or "") not in known_http_shas:
                            violations.append("ONLINE_SNAPSHOT_BAR_UNBACKED_ORIGIN_SHA")
                            break

    # 4. Provenance & Decimal fee sensitivity recomputation on 04 & 05
    prov_05 = f04.get("provenance_audit") or {}
    ct_05 = prov_05.get("public_contract_fee_snapshot_audit") or {}
    if ct_05.get("actual_account_effective_fee_tier") != "UNKNOWN_NOT_LOCALLY_VERIFIED":
        violations.append("FEE_04_UNVERIFIED_ACCOUNT_TIER_CLAIMED")
    if f05.get("actual_account_effective_fee_tier") != "UNKNOWN_NOT_LOCALLY_VERIFIED":
        violations.append("FEE_05_UNVERIFIED_ACCOUNT_TIER_CLAIMED")

    fo_05 = f05.get("FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS") or {}
    rr_05 = f05.get("FULL_RE_RISKED_REPLAY") or {}
    for rk in ("0.0004", "0.0006"):
        sc = fo_05.get(rk)
        if not isinstance(sc, Mapping):
            violations.append(f"FEE_05_MISSING_SCENARIO:{rk}")
            continue
        g_u = float(sc.get("gross_pnl_1x_usd") or 0.0)
        f_u = float(sc.get("total_fees_1x_usd") or 0.0)
        n_u = float(sc.get("net_pnl_1x_usd") or 999999.0)
        if abs(round(g_u - f_u, 4) - n_u) > 1e-3:
            violations.append(f"FEE_05_ALGEBRAIC_PNL_TAMPERED:{rk}:{g_u}-{f_u}!={n_u}")
        if n_u >= 0.0:
            violations.append(f"FEE_05_FABRICATED_PROFITABILITY:{rk}:{n_u}")
        rr_sc = rr_05.get(rk) or {}
        if rr_sc.get("scope_is_full_56710_opportunity_rerun") is not False:
            violations.append(f"FEE_05_242_RERISK_CLAIMED_AS_FULL_56710_RERUN:{rk}")

    if recompute_v130_fees:
        nb_p = V130_RUN / "SI0_OFF_BASELINE_NEXT_BAR_OPEN_BID_ASK_trades.jsonl.gz"
        sp2_p = V130_RUN / "SI0_OFF_BASELINE_STRESS_WIDER_SPREAD_SLIPPAGE_2X_trades.jsonl.gz"
        if nb_p.is_file() and sp2_p.is_file():
            recomp = compute_kh01b_r1_fee_sensitivity(list(iter_jsonl_gz(nb_p)), list(iter_jsonl_gz(sp2_p)))
            for rk in ("0.0004", "0.0006"):
                exp_net = (recomp.get("FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS") or {}).get(rk, {}).get("net_pnl_1x_usd")
                obs_net = (fo_05.get(rk) or {}).get("net_pnl_1x_usd")
                if exp_net is None or obs_net is None or abs(float(exp_net) - float(obs_net)) > 1e-4:
                    violations.append(f"FEE_05_MISMATCH_VS_RAW_V130_LEDGER:{rk}:obs={obs_net}!=recomp={exp_net}")

    # 5. Strict multi-mount 20 GiB disk check, JUnit bijection, worker determinism & full backtest parity
    d_pol = f00.get("disk_reserve_policy") or {}
    d_pol_06 = f06.get("disk_reserve_policy") or {}
    disk_verdict = str(d_pol.get("disk_safety_verdict") or "FAIL")
    ov_14 = f14.get("orthogonal_verdicts") or {}

    if d_pol_06.get("disk_safety_verdict") != disk_verdict:
        violations.append(f"CONFLICTING_DISK_VERDICT_00_VS_06:{disk_verdict}!={d_pol_06.get('disk_safety_verdict')}")
    if bool(d_pol.get("lowered_reserve_without_approval", True)) or float(d_pol.get("enforced_reserve_gib") or 0.0) < 20.0:
        violations.append("DISK_RESERVE_LOWERED_BELOW_STRICT_20GIB")

    parent_latest_ptr = run_dir.parent / "latest_run.json"
    if parent_latest_ptr.is_file():
        ptr_doc = json.loads(parent_latest_ptr.read_text("utf-8"))
        if ptr_doc.get("run_id") == run_id and validate_latest_run_pointer_not_blocked(ptr_doc)["status"] != "PASS":
            violations.append("BLOCKED_RUN_ILLEGALLY_PROMOTED_TO_LATEST_RUN_JSON")

    if disk_verdict == "BLOCKED_LOW_DISK":
        if ov_14.get("ENGINEERING_CLOSURE_VERDICT") == "PASS":
            violations.append("FORGED_ENGINEERING_PASS_UNDER_BLOCKED_LOW_DISK")
        if (run_dir / "junit").is_dir() and list((run_dir / "junit").glob("*.xml")):
            violations.append("FAKE_JUNIT_XML_PRESENT_UNDER_BLOCKED_LOW_DISK")
        for fn_k, f_doc in (("08", f08), ("09", f09), ("10", f10)):
            if f_doc.get("execution_status") != "NOT_RUN_BLOCKED_LOW_DISK" or f_doc.get("completed") is not False:
                violations.append(f"HEAVY_STAGE_NOT_HONESTLY_MARKED_NOT_RUN:{fn_k}:{f_doc.get('execution_status')}")
    elif disk_verdict == "PASS_STRICT_20GIB":
        if float(d_pol.get("preflight_baseline_free_gib") or 0.0) < 20.5:
            violations.append("FALSE_PASS_STRICT_20GIB_BELOW_20P5_PREFLIGHT")
        for v_lbl in ("repo", "output", "temp"):
            if float(((d_pol.get("volumes") or {}).get(v_lbl) or {}).get("free_gib") or 0.0) < 20.5:
                violations.append(f"VOLUME_BELOW_20P5_GIB_IN_00:{v_lbl}")

        # Verify 08 worker determinism runs & canonical hashes
        for sec_8, sha_key in (
            ("pytest_controlled_subset_w1_w2_w4_repeat", "canonical_sorted_outcome_sha256"),
            ("r1_variant_slice_w1_w2_w4_repeat", "canonical_sorted_sha256"),
        ):
            r_list = (f08.get(sec_8) or {}).get("runs") or []
            modes_seen = [str(x.get("mode_label")) for x in r_list]
            shas_seen = {str(x.get(sha_key) or "") for x in r_list}
            if modes_seen != ["w=1", "w=2", "w=4", "w=1_repeat"] or len(shas_seen) != 1 or "" in shas_seen:
                violations.append(f"WORKER_08_SHARD_OR_HASH_MISMATCH:{sec_8}:modes={modes_seen},shas={len(shas_seen)}")

        # Verify 09 closed JUnit bijection
        jb = verify_fresh_junit_bijection_v17931211(
            run_dir,
            f09.get("collection_manifest") or {},
            f09.get("shard_manifest") or {},
            expected_run_id=run_id,
            expected_tested_sha=tested_sha,
            mandatory_node_prefixes=(
                "backend/tests/test_kh_01b_r1",
                "backend/tests/test_kh_01b",
                "backend/tests/test_kh_01a",
                "backend/tests/test_v17_9_3_1_2",
            ),
            require_shard_schedule=True,
        )
        if jb.get("status") != "PASS":
            violations.extend(f"JUNIT_BIJECTION:{v}" for v in (jb.get("violations") or []))

        # Verify 10 full backtest parity
        nb_10 = f10.get("si0_e00_primary_nb_summary") or {}
        if (
            int(f10.get("a1_evaluated_count") or 0) != 56710
            or int(f10.get("a1_mismatches_count", 1)) != 0
            or int(f10.get("si8_separate_population_count") or 0) != 6375
            or int(f10.get("three_adapter_episodes_denominator") or 0) != 246
            or int(f10.get("three_adapter_mismatches_count", 1)) != 0
            or int(nb_10.get("total_scheduled_episodes") or 0) != 242
            or int(nb_10.get("closed_trades_count") or 0) != 166
            or int(nb_10.get("wins_count") or 0) != 9
            or int(nb_10.get("losses_count") or 0) != 157
            or float(nb_10.get("total_realized_net_usd") or 0.0) != -345.8971
        ):
            violations.append("BACKTEST_10_PARITY_OR_COHORT_TAMPERED")
    else:
        violations.append(f"INVALID_DISK_VERDICT:{disk_verdict}")

    # 6. OOS escrow, runtime OFF locks & prior artifact immutability
    if ov_14.get("ACTIVATION_READY") != "NO":
        violations.append("ACTIVATION_READY_NOT_NO")
    if ov_14.get("KH01C_ADVANCEMENT_VERDICT") != "NO_ECONOMIC_ADVANCEMENT_CANDIDATE":
        violations.append("ILLEGAL_KH01C_CANDIDATE_PROMOTION_IN_DSC_R2")
    modes_14 = ov_14.get("RUNTIME_SAFETY_MODES") or {}
    if any(modes_14.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
        violations.append(f"UNSAFE_RUNTIME_MODES_IN_14:{modes_14}")

    esc_12 = f12.get("future_oos_escrow") or {}
    if esc_12.get("FRESH_OOS_VERDICT") not in ("BLOCKED_NO_UNTOUCHED_DATA", "SEALED_UNTOUCHED_NOT_EVALUATED"):
        violations.append(f"ILLEGAL_FRESH_OOS_VERDICT:{esc_12.get('FRESH_OOS_VERDICT')}")
    if int(esc_12.get("strategy_evaluation_count_total", 1)) != 0:
        violations.append("NONZERO_OOS_STRATEGY_EVALUATION_COUNT")

    if f13.get("status") != "PASS" or int(f13.get("modified_prior_files_count", 1)) != 0:
        violations.append("PRIOR_ARTIFACTS_MUTATED_IN_13")

    # 7. Dynamic gate receipts & independent re-evaluation of G-DS-01..15 + R2-A..G
    rcpt_dir = run_dir / "receipts"
    receipts_read: Dict[str, EvidenceReceiptV17931219] = {}
    if rcpt_dir.is_dir():
        for rp in sorted(rcpt_dir.glob("*.json")):
            if not (rp.stem.startswith("G-DS-") or rp.stem.startswith("R2-")):
                continue
            r_doc = json.loads(rp.read_text("utf-8")).get("receipt") or {}
            r_obj = EvidenceReceiptV17931219(**r_doc)
            receipts_read[rp.stem] = r_obj
            if r_obj.status == "PASS" and not r_obj.is_valid_pass(
                expected_run_id=run_id,
                expected_tested_sha=tested_sha,
                run_dir=run_dir,
                reject_uniform_placeholder_hash=True,
            ):
                violations.append(f"INVALID_GATE_RECEIPT:{rp.stem}:{r_obj.violations}")

    re_eval_14 = evaluate_kh01b_r1_dsc_r2_gates_from_disk(
        run_dir,
        run_id,
        tested_sha,
        publication_status=str(ov_14.get("PUBLICATION_VERDICT") or "PENDING_REMOTE_PUSH"),
    )
    re_ov = re_eval_14["orthogonal_verdicts"]
    if re_ov["ENGINEERING_CLOSURE_VERDICT"] == "FAIL":
        failed_gids = [g["gate_id"] for g in re_eval_14["gates"] if g["status"] == "FAIL"]
        violations.append(f"INDEPENDENT_GATE_REEVALUATION_FAILED:{failed_gids}")
    for vk in (
        "ENGINEERING_CLOSURE_VERDICT",
        "DISK_SAFETY_VERDICT",
        "FULL_REGRESSION_VERDICT",
        "BACKTEST_VERDICT",
        "WORKER_DETERMINISM_VERDICT",
        "DATA_ACQUISITION_VERDICT",
        "KH01C_ADVANCEMENT_VERDICT",
        "FRESH_OOS_VERDICT",
        "ACTIVATION_READY",
    ):
        if ov_14.get(vk) != re_ov.get(vk):
            violations.append(f"FORGED_VERDICT_IN_14:{vk}:claimed={ov_14.get(vk)}!=recomputed={re_ov.get(vk)}")

    cv = verify_kh01b_r1_dsc_r2_cross_view_consistency(
        readme_text=readme_txt,
        handover_text=handover_txt,
        f00=f00,
        f05=f05,
        f11=f11,
        f14=f14,
    )
    if cv["status"] != "PASS":
        violations.extend(f"CROSS_VIEW:{v}" for v in cv["violations"])

    # 8. Seal 15_independent_verifier_manifest_and_content_sha256.json (circular-exclusion policy)
    delivered_files: Dict[str, Dict[str, Any]] = {}
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.name == MANIFEST_15_R2_NAME:
            continue
        rel = str(p.relative_to(run_dir)).replace("\\", "/")
        delivered_files[rel] = {
            "byte_size": int(p.stat().st_size),
            "sha256": _file_sha256(p),
        }

    manifest_15_doc = {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC_R2,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC_R2,
        "run_id": run_id,
        "branch": f00.get("branch"),
        "head_at_start_sha": f00.get("head_at_start_sha"),
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01B_R1,
        "independent_verifier": {
            "verifier_script": "aurum-desk/tools/kh_01b_r1_data_safety_independent_verifier.py",
            "verifier_pid": verifier_pid,
            "orchestrator_pid": orchestrator_pid,
            "separate_os_process_verified": verifier_pid != orchestrator_pid,
            "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "PASS" if not violations else "FAIL",
            "recomputed_engineering_closure_verdict": re_ov["ENGINEERING_CLOSURE_VERDICT"],
            "recomputed_disk_safety_verdict": re_ov["DISK_SAFETY_VERDICT"],
            "violations_count": len(violations),
            "violations": violations,
        },
        "gate_receipts_readback": {k: asdict(v) for k, v in sorted(receipts_read.items())},
        "circular_hash_exclusion_policy": (
            f"{MANIFEST_15_R2_NAME} hashes every other file in {run_dir.name} and excludes only itself."
        ),
        "delivered_files_count": len(delivered_files),
        "delivered_files_aggregate_sha256": canonical_hash(delivered_files),
        "delivered_files": delivered_files,
    }
    manifest_sha = write_json_durable_atomic(run_dir / MANIFEST_15_R2_NAME, manifest_15_doc)

    return {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC_R2,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "verifier_pid": verifier_pid,
        "orchestrator_pid": orchestrator_pid,
        "separate_os_process_verified": verifier_pid != orchestrator_pid,
        "independent_verifier_status": "PASS" if not violations else "FAIL",
        "recomputed_engineering_closure_verdict": re_ov["ENGINEERING_CLOSURE_VERDICT"],
        "recomputed_disk_safety_verdict": re_ov["DISK_SAFETY_VERDICT"],
        "delivered_files_count": len(delivered_files),
        "manifest_15_sha256": manifest_sha,
        "violations": violations,
    }


def verify_kh_01b_r1_ram_safety_r3_run_dir_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_git_source_freeze: bool = True,
) -> Dict[str, Any]:
    """
    KH-01B.R1.DSC.R3 Separate-OS-Process Independent Disk Verifier (§3.E, §5 Phase 5, §7 S11).
    Re-reads closed R3 artifacts (`00`..`13`), `08_runtime_memory_samples_and_peak_rss.jsonl.gz`,
    JUnit XMLs (when executed), and dynamic cryptographic receipts (`S01..S14`) from disk;
    independently recomputes min sampled available RAM, process-tree peak RSS, descendant caps,
    OOM/exit status of every shard, JUnit bijection, `w=1/2/4/1_repeat` hashes, full `56,710 + 6,375`
    & `SI0 242/166` 3-fill / 3-adapter fingerprints vs sealed R2, R2 artifact/pointer immutability,
    and seals `14_sha256_manifest_independent_os_readback.json`.
    """
    from services.kh_01b_r1_ram_safety_r3_service import (
        HARD_ABORT_RAM_FLOOR_BYTES_R3,
        PRIOR_R2_ACCEPTED_RUN_ID,
        PRIOR_R2_LATEST_PTR_REL,
        PRIOR_R2_LATEST_PTR_SHA256,
        PRIOR_R2_PUBLICATION_HEAD_SHA,
        PRIOR_R2_TESTED_SHA,
        REQUIRED_ARTIFACTS_KH01B_R1_RAM_SAFETY_R3,
        SCHEMA_VERSION_KH01B_R1_DSC_R3,
        SEALED_R2_A1_56710_IDS_SHA256,
        SEALED_R2_E00_BUNDLE_SHA256,
        SEALED_R2_SI0_242_TUPLES_SHA256,
        SEALED_R2_SI8_6375_IDS_SHA256,
        SPEC_VERSION_KH01B_R1_DSC_R3,
        audit_r3_memory_samples_jsonl_gz,
        detect_active_64mib_ram_floor_in_source,
        evaluate_kh01b_r1_ram_safety_r3_gates_from_disk,
        validate_r3_latest_run_pointer,
        verify_kh01b_r1_ram_safety_r3_cross_view_consistency,
        verify_r3_worker_determinism_artifact,
    )

    run_dir = run_dir.resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"RUN_DIR_NOT_FOUND:{run_dir}"],
        }

    for rel_req in REQUIRED_ARTIFACTS_KH01B_R1_RAM_SAFETY_R3:
        if rel_req == MANIFEST_14_R3_NAME:
            continue
        fp = run_dir / rel_req
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_REQUIRED_R3_ARTIFACT:{rel_req}")

    if violations:
        return {
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": violations,
        }

    try:
        f00 = json.loads((run_dir / "00_preflight_branch_sha_mount_ram_disk_immutable.json").read_text("utf-8"))
        f01 = json.loads((run_dir / "01_function_callgraph_keep_as_is_and_source_diff.json").read_text("utf-8"))
        f02 = json.loads((run_dir / "02_ram_policy_version_thresholds_preflight_and_projections.json").read_text("utf-8"))
        f03 = json.loads((run_dir / "03_admission_hysteresis_exclusive_nested_child_reservations.json").read_text("utf-8"))
        f04 = json.loads((run_dir / "04_ram_pressure_red_green_512mib_boundary_faults.json").read_text("utf-8"))
        f05 = json.loads((run_dir / "05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json").read_text("utf-8"))
        f06 = json.loads((run_dir / "06_async_w1_w2_w4_repeat_timings_hashes_and_capacity.json").read_text("utf-8"))
        f07 = json.loads((run_dir / "07_full_a1_si8_si0_three_fill_adapter_backtest_parity.json").read_text("utf-8"))
        f09 = json.loads((run_dir / "09_disk_all_mounts_and_safe_temp_cleanup.json").read_text("utf-8"))
        f10 = json.loads((run_dir / "10_integrity_previous_artifacts_and_source_sha_diffs.json").read_text("utf-8"))
        f11 = json.loads((run_dir / "11_independent_verifier_red_controls_and_receipts.json").read_text("utf-8"))
        f12 = json.loads((run_dir / "12_oos_zero_evaluations_and_activation_off.json").read_text("utf-8"))
        f13 = json.loads((run_dir / "13_final_orthogonal_ram_engineering_economic_verdicts.json").read_text("utf-8"))
        readme_txt = (run_dir / "README_KH01B_R1_RAM_SAFETY_R3.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH01B_R1_RAM_SAFETY_R3_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"ARTIFACT_JSON_PARSE_ERROR:{type(exc).__name__}:{exc}"],
        }

    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    orchestrator_pid = int(f00.get("orchestrator_pid") or -1)
    if verifier_pid == orchestrator_pid:
        violations.append(f"VERIFIER_NOT_SEPARATE_OS_PROCESS:{verifier_pid}")

    # 1. Branch, new tested_sha, canonical dataset, CostAssumptions & R2 pointer immutability
    if f00.get("branch") != MANDATORY_BRANCH_KH01B_R1:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if f00.get("tested_executable_sha") != tested_sha or f13.get("tested_executable_sha") != tested_sha:
        violations.append("TESTED_EXECUTABLE_SHA_MISMATCH_ACROSS_00_13")
    if tested_sha in (PRIOR_DSC_V1_TESTED_SHA, PRIOR_R2_TESTED_SHA, PRIOR_R2_PUBLICATION_HEAD_SHA):
        violations.append(f"STALE_TESTED_SHA_REUSED:{tested_sha}")
    if run_id in (PRIOR_DSC_V1_BLOCKED_RUN_ID, PRIOR_R2_ACCEPTED_RUN_ID):
        violations.append(f"STALE_RUN_ID_REUSED:{run_id}")
    if len(tested_sha) >= 12 and not run_id.endswith(f"_{tested_sha[:12]}"):
        violations.append(f"RUN_ID_MISSING_TESTED_SHA12_SUFFIX:{run_id}:{tested_sha[:12]}")
    if f00.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH01B_R1:
        violations.append("CANONICAL_DATASET_SHA_MISMATCH_IN_00")
    ds_disk = APP_ROOT / "backtest/historical_dataset_XAUUSDT.json.gz"
    if ds_disk.is_file() and _file_sha256(ds_disk) != CANONICAL_DATASET_SHA256_KH01B_R1:
        violations.append("CANONICAL_DATASET_MODIFIED_ON_DISK")
    if int(f01.get("locked_files_modified_count", 1)) != 0:
        violations.append("LOCKED_PRODUCTION_OR_STRATEGY_FILES_MODIFIED")

    r2_ptr_disk = REPO_ROOT / PRIOR_R2_LATEST_PTR_REL
    if r2_ptr_disk.is_file() and _file_sha256(r2_ptr_disk) != PRIOR_R2_LATEST_PTR_SHA256:
        violations.append("PRIOR_R2_LATEST_RUN_POINTER_MODIFIED_ON_DISK")

    live_costs = CostAssumptions()
    if live_costs.maker_fee_rate != 0.0002 or live_costs.taker_fee_rate != 0.0004 or live_costs.slippage_usd != 0.10:
        violations.append("LIVE_DOMAIN_CALCULATOR_COST_DEFAULTS_MUTATED")

    st_64 = detect_active_64mib_ram_floor_in_source(REPO_ROOT)
    if st_64["status"] != "PASS":
        violations.append(f"ACTIVE_64MIB_FLOOR_IN_REPO_SOURCE:{st_64['violations']}")

    if check_git_source_freeze:
        sf = verify_source_freeze_v17931211(REPO_ROOT, tested_sha)
        if sf.get("status") != "PASS":
            violations.extend(f"SOURCE_FREEZE:{v}" for v in (sf.get("violations") or []))

    # 2. Independent raw 08_runtime_memory_samples_and_peak_rss.jsonl.gz & abort check
    cfg_floor = int(f02.get("hard_abort_floor_bytes") or 0)
    env_floor = int(f02.get("configured_env_aurum_minimum_ram_bytes") or 0)
    if cfg_floor < HARD_ABORT_RAM_FLOOR_BYTES_R3 or env_floor < HARD_ABORT_RAM_FLOOR_BYTES_R3:
        violations.append(f"RAM_HARD_FLOOR_BELOW_512MIB:cfg={cfg_floor},env={env_floor}")

    samples_aud = audit_r3_memory_samples_jsonl_gz(
        run_dir / "08_runtime_memory_samples_and_peak_rss.jsonl.gz",
        hard_floor_bytes=max(HARD_ABORT_RAM_FLOOR_BYTES_R3, cfg_floor),
    )
    abort_exists = (run_dir / "resource_abort.json").is_file()
    ov_13 = f13.get("orthogonal_verdicts") or {}
    d_pol = f00.get("disk_reserve_policy") or {}
    disk_verdict = str(d_pol.get("disk_safety_verdict") or "FAIL")
    tel_09 = f09.get("runtime_telemetry") or {}

    if samples_aud["samples_count"] == 0:
        violations.append("EMPTY_OR_MISSING_08_MEMORY_SAMPLES")
    if samples_aud.get("over_worker_cap_samples_count", 0) > 0 or int(tel_09.get("max_children_observed") or 0) > 4:
        violations.append("GLOBAL_DESCENDANT_CAP_EXCEEDED_4")
    if int(tel_09.get("orphan_pids_at_cleanup_count", 1)) != 0:
        violations.append("LIVE_ORPHAN_PIDS_AT_CLEANUP")

    parent_latest_ptr = run_dir.parent / "latest_run.json"
    if parent_latest_ptr.is_file():
        ptr_doc = json.loads(parent_latest_ptr.read_text("utf-8"))
        if ptr_doc.get("run_id") == run_id and validate_r3_latest_run_pointer(ptr_doc)["status"] != "PASS":
            violations.append("BLOCKED_OR_INVALID_R3_RUN_PROMOTED_TO_LATEST_RUN_JSON")

    # Anti-fake-PASS checks on RAM & Disk
    ram_breached_any = (
        not bool(f02.get("can_proceed", False))
        or abort_exists
        or bool(tel_09.get("hard_ram_breached", False))
        or samples_aud["status"] == "FAIL_BELOW_HARD_FLOOR"
        or str(tel_09.get("latched_error") or "").startswith("BLOCKED_LOW_RAM")
        or str(f03.get("scheduler_terminal_status") or "").startswith("BLOCKED_LOW_RAM")
    )
    if ram_breached_any:
        if ov_13.get("ENGINEERING_CLOSURE_VERDICT") == "PASS" or str(ov_13.get("RAM_SAFETY_VERDICT") or "").startswith("PASS"):
            violations.append("FORGED_PASS_UNDER_LOW_RAM_OR_ABORT_BREACH")
    if disk_verdict == "BLOCKED_LOW_DISK":
        if ov_13.get("ENGINEERING_CLOSURE_VERDICT") == "PASS":
            violations.append("FORGED_PASS_UNDER_BLOCKED_LOW_DISK")
        if (run_dir / "junit").is_dir() and list((run_dir / "junit").glob("*.xml")):
            violations.append("FAKE_JUNIT_XML_PRESENT_UNDER_BLOCKED_LOW_DISK")

    # 3. When Engineering claims PASS, independently verify JUnit, OOM=0, Determinism & Full Backtest Parity
    if not ram_breached_any and disk_verdict == "PASS_STRICT_20GIB":
        shards_05 = ((f05.get("shard_manifest") or {}).get("shards") or [])
        for sh_item in shards_05:
            if int(sh_item.get("exit_code", -1)) != 0 or bool(sh_item.get("oom_or_killed_detected", False)) or sh_item.get("shard_status") != "PASS":
                violations.append(f"SHARD_OOM_OR_NONZERO_EXIT:{sh_item.get('shard_name')}:rc={sh_item.get('exit_code')}")
            if int(sh_item.get("peak_rss_bytes") or 0) <= 0 or int(sh_item.get("rss_sample_count") or 0) <= 0:
                violations.append(f"SHARD_ZERO_RSS_SAMPLE:{sh_item.get('shard_name')}")

        jb = verify_fresh_junit_bijection_v17931211(
            run_dir,
            f05.get("collection_manifest") or {},
            f05.get("shard_manifest") or {},
            expected_run_id=run_id,
            expected_tested_sha=tested_sha,
            mandatory_node_prefixes=(
                "backend/tests/test_kh_01b_r1",
                "backend/tests/test_kh_01b",
                "backend/tests/test_kh_01a",
                "backend/tests/test_v17_9_3_1_2",
            ),
            require_shard_schedule=True,
        )
        if jb.get("status") != "PASS":
            violations.extend(f"JUNIT_BIJECTION:{v}" for v in (jb.get("violations") or []))

        det_chk = verify_r3_worker_determinism_artifact(f06)
        if det_chk["status"] == "FAIL":
            violations.extend(f"WORKER_06:{v}" for v in det_chk["violations"])

        nb_07 = f07.get("si0_e00_primary_nb_summary") or {}
        fp_07 = f07.get("per_decision_and_episode_fingerprints") or {}
        if (
            int(f07.get("a1_evaluated_count") or 0) != 56710
            or int(f07.get("a1_mismatches_count", 1)) != 0
            or int(f07.get("si8_separate_population_count") or 0) != 6375
            or int(f07.get("three_adapter_episodes_denominator") or 0) != 246
            or int(f07.get("three_adapter_mismatches_count", 1)) != 0
            or int(nb_07.get("total_scheduled_episodes") or 0) != 242
            or int(nb_07.get("closed_trades_count") or 0) != 166
            or int(nb_07.get("wins_count") or 0) != 9
            or int(nb_07.get("losses_count") or 0) != 157
            or float(nb_07.get("total_realized_net_usd") or 0.0) != -345.8971
            or fp_07.get("a1_56710_ids_sha256") != SEALED_R2_A1_56710_IDS_SHA256
            or fp_07.get("si8_6375_ids_sha256") != SEALED_R2_SI8_6375_IDS_SHA256
            or fp_07.get("si0_242_episode_matched_tuples_sha256") != SEALED_R2_SI0_242_TUPLES_SHA256
            or fp_07.get("e00_bundle_sha256") != SEALED_R2_E00_BUNDLE_SHA256
        ):
            violations.append("BACKTEST_07_PARITY_OR_SEALED_R2_FINGERPRINT_TAMPERED")

    # 4. OOS, runtime OFF, prior artifacts & S01..S14 receipts + recomputed verdicts
    if ov_13.get("ACTIVATION_READY") != "NO":
        violations.append("ACTIVATION_READY_NOT_NO")
    if ov_13.get("KH01C_ADVANCEMENT_VERDICT") != "NO_ECONOMIC_ADVANCEMENT_CANDIDATE":
        violations.append("ILLEGAL_KH01C_PROMOTION_IN_R3")
    modes_13 = ov_13.get("RUNTIME_SAFETY_MODES") or {}
    if any(modes_13.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
        violations.append(f"UNSAFE_RUNTIME_MODES_IN_13:{modes_13}")

    if f10.get("status") != "PASS" or int(f10.get("modified_prior_files_count", 1)) != 0:
        violations.append("PRIOR_ARTIFACTS_MUTATED_IN_10")

    rcpt_dir = run_dir / "receipts"
    receipts_read: Dict[str, EvidenceReceiptV17931219] = {}
    if rcpt_dir.is_dir():
        for rp in sorted(rcpt_dir.glob("S*.json")):
            r_doc = json.loads(rp.read_text("utf-8")).get("receipt") or {}
            r_obj = EvidenceReceiptV17931219(**r_doc)
            receipts_read[rp.stem] = r_obj
            if r_obj.status == "PASS" and not r_obj.is_valid_pass(
                expected_run_id=run_id,
                expected_tested_sha=tested_sha,
                run_dir=run_dir,
                reject_uniform_placeholder_hash=True,
            ):
                violations.append(f"INVALID_GATE_RECEIPT:{rp.stem}:{r_obj.violations}")

    re_eval_13 = evaluate_kh01b_r1_ram_safety_r3_gates_from_disk(
        run_dir,
        run_id,
        tested_sha,
        publication_status=str(ov_13.get("PUBLICATION_VERDICT") or "PENDING_REMOTE_PUSH"),
    )
    re_ov = re_eval_13["orthogonal_verdicts"]
    if re_ov["ENGINEERING_CLOSURE_VERDICT"] in ("FAIL_INTEGRITY", "FAIL_RUNTIME_SAFETY"):
        failed_s = [g["gate_id"] for g in re_eval_13["gates"] if g["status"] == "FAIL"]
        violations.append(f"INDEPENDENT_R3_GATE_REEVALUATION_FAILED:{failed_s}")

    for vk in (
        "ENGINEERING_CLOSURE_VERDICT",
        "RAM_SAFETY_VERDICT",
        "DISK_SAFETY_VERDICT",
        "FULL_REGRESSION_VERDICT",
        "WORKER_DETERMINISM_VERDICT",
        "BACKTEST_VERDICT",
        "RESEARCH_ECONOMIC_VERDICT",
        "KH01C_ADVANCEMENT_VERDICT",
        "FRESH_OOS_VERDICT",
        "ACTIVATION_READY",
    ):
        if ov_13.get(vk) != re_ov.get(vk):
            violations.append(f"FORGED_VERDICT_IN_13:{vk}:claimed={ov_13.get(vk)}!=recomputed={re_ov.get(vk)}")

    cv = verify_kh01b_r1_ram_safety_r3_cross_view_consistency(
        readme_text=readme_txt,
        handover_text=handover_txt,
        f00=f00,
        f11=f11,
        f13=f13,
    )
    if cv["status"] != "PASS":
        violations.extend(f"CROSS_VIEW:{v}" for v in cv["violations"])

    # 5. Seal 14_sha256_manifest_independent_os_readback.json (circular-exclusion policy)
    delivered_files: Dict[str, Dict[str, Any]] = {}
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.name == MANIFEST_14_R3_NAME:
            continue
        rel = str(p.relative_to(run_dir)).replace("\\", "/")
        delivered_files[rel] = {
            "byte_size": int(p.stat().st_size),
            "sha256": _file_sha256(p),
        }

    manifest_14_doc = {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC_R3,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC_R3,
        "run_id": run_id,
        "branch": f00.get("branch"),
        "head_at_start_sha": f00.get("head_at_start_sha"),
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01B_R1,
        "independent_verifier": {
            "verifier_script": "aurum-desk/tools/kh_01b_r1_data_safety_independent_verifier.py",
            "verifier_pid": verifier_pid,
            "orchestrator_pid": orchestrator_pid,
            "separate_os_process_verified": verifier_pid != orchestrator_pid,
            "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "PASS" if not violations else "FAIL",
            "recomputed_engineering_closure_verdict": re_ov["ENGINEERING_CLOSURE_VERDICT"],
            "recomputed_ram_safety_verdict": re_ov["RAM_SAFETY_VERDICT"],
            "recomputed_disk_safety_verdict": re_ov["DISK_SAFETY_VERDICT"],
            "recomputed_samples_audit": samples_aud,
            "violations_count": len(violations),
            "violations": violations,
        },
        "gate_receipts_readback": {k: asdict(v) for k, v in sorted(receipts_read.items())},
        "circular_hash_exclusion_policy": (
            f"{MANIFEST_14_R3_NAME} hashes every other file in {run_dir.name} and excludes only itself."
        ),
        "delivered_files_count": len(delivered_files),
        "delivered_files_aggregate_sha256": canonical_hash(delivered_files),
        "delivered_files": delivered_files,
    }
    manifest_sha = write_json_durable_atomic(run_dir / MANIFEST_14_R3_NAME, manifest_14_doc)

    return {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC_R3,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "verifier_pid": verifier_pid,
        "orchestrator_pid": orchestrator_pid,
        "separate_os_process_verified": verifier_pid != orchestrator_pid,
        "independent_verifier_status": "PASS" if not violations else "FAIL",
        "recomputed_engineering_closure_verdict": re_ov["ENGINEERING_CLOSURE_VERDICT"],
        "recomputed_ram_safety_verdict": re_ov["RAM_SAFETY_VERDICT"],
        "recomputed_disk_safety_verdict": re_ov["DISK_SAFETY_VERDICT"],
        "delivered_files_count": len(delivered_files),
        "manifest_14_sha256": manifest_sha,
        "violations": violations,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="KH-01B.R1.DSC / R2 / R3 Separate-OS-Process Independent Disk Verifier")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--r2", action="store_true", help="Force KH-01B.R1.DSC.R2 schema verification")
    ap.add_argument("--r3", action="store_true", help="Force KH-01B.R1.DSC.R3 RAM-safety schema verification")
    ap.add_argument("--skip-git-freeze-check", action="store_true")
    ap.add_argument("--skip-v130-recompute", action="store_true")
    args = ap.parse_args()

    r_dir = Path(args.run_dir)
    is_r3 = bool(args.r3) or (r_dir / "00_preflight_branch_sha_mount_ram_disk_immutable.json").is_file()
    if is_r3:
        res = verify_kh_01b_r1_ram_safety_r3_run_dir_from_disk(
            r_dir,
            expected_run_id=args.expected_run_id,
            expected_tested_sha=args.expected_tested_sha,
            check_git_source_freeze=not args.skip_git_freeze_check,
        )
    else:
        is_r2 = bool(args.r2) or (r_dir / "00_preflight_branch_head_code_sha_disk_mounts_dataset.json").is_file()
        fn = verify_kh_01b_r1_dsc_r2_run_dir_from_disk if is_r2 else verify_kh_01b_r1_dsc_run_dir_from_disk
        res = fn(
            r_dir,
            expected_run_id=args.expected_run_id,
            expected_tested_sha=args.expected_tested_sha,
            check_git_source_freeze=not args.skip_git_freeze_check,
            recompute_v130_fees=not args.skip_v130_recompute,
        )
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0 if res["independent_verifier_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
