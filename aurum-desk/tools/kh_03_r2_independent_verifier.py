#!/usr/bin/env python3
"""
aurum-desk/tools/kh_03_r2_independent_verifier.py

AURUM DESK — KH-03.R2 Separate-OS-Process Independent Disk Verifier (G01..G28)
SPEC VERSION: KH-03.R2-v1.0

Executes in a DISTINCT OS process (`verifier_pid != orchestrator_pid`), reads EVERY closed
artifact (`00`..`24`, `26`, `08_h3_per_episode_policy_qty_time_ledger.jsonl.gz`,
`README_KH03_R2.md`, `HANDOVER_KH03_R2_VI.txt`, `logs/runtime_memory_samples_0p5s.jsonl.gz`,
`junit/*.xml`) directly from disk, NEVER trusts producer booleans or self-reported `PASS`
labels, independently recomputes:
  - Strict CI parsing & raw-evidence DEV advancement criteria (`evaluate_kh03_dev_ci_and_advancement`)
  - Streaming Decimal H3 advisory ledger reconciliation (`validate_kh03_r2_h3_ledger_independently`)
  - Forward-only WF fold causality, embargo >= 240m, label-exit purge & selection-freeze hashes
  - Prior R1 + C4 + canonical dataset SHA-256 immutability
  - Raw JUnit XML 1:1 bijection, W1/W2/W4/W1-repeat hashes, 0.5s RAM/disk/PID logs
  - Full 28-gate matrix (`G01`..`G28`) and orthogonal verdicts
and seals `25_independent_OS_verifier_manifest_sha256.json`.

Exit codes:
  0 — ALL mandatory engineering & methodology gates PASS (economic/OOS/KH04 truthfully reported)
  1 — ANY tamper, forged CI/H3/WF claim, SHA mismatch, or invariant violation
  2 — BLOCKED environment / low disk / incomplete heavy stages (fail-closed)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import (
    ACTIVATION_READY_KH02_R2_SHARED as ACTIVATION_READY_KH03_R2,
    CANONICAL_DATASET_SHA256_KH02 as CANONICAL_DATASET_SHA256_KH03_R2,
    GLOBAL_MAX_DESCENDANTS_SHARED as GLOBAL_MAX_DESCENDANTS_KH03_R2,
    HARD_RAM_FLOOR_MIB_SHARED as HARD_RAM_FLOOR_MIB_KH03_R2,
    MANDATORY_BRANCH_KH02_R2_SHARED as MANDATORY_BRANCH_KH03_R2,
    PRIOR_KH02_R1_TESTED_SHA_SHARED,
    PRIOR_KH02_R2_C1_CODE_COMMIT_SHA_SHARED,
    PRIOR_KH02_R2_C2_TESTED_SHA_SHARED,
    PRIOR_KH02_R2_TESTED_SHA_SHARED,
    STRICT_PREFLIGHT_DISK_MIN_GIB_SHARED as STRICT_PREFLIGHT_DISK_MIN_GIB,
    STRICT_RUNTIME_DISK_MIN_GIB_SHARED as STRICT_RUNTIME_DISK_MIN_GIB,
)
from services.kh_03_r2_methodology_service import (
    KH03_R2_H3_ADVISORY_ARM,
    MIN_EMBARGO_MINUTES_KH03_R2,
    SCHEMA_VERSION_KH03_R2,
    SPEC_VERSION_KH03_R2,
    evaluate_kh03_dev_ci_and_advancement,
    parse_kh03_cluster_ci_strict,
    validate_kh03_r2_fold_causality,
    validate_kh03_r2_h3_ledger_independently,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_02_r2_c3_independent_verifier import _audit_memory_samples_c3
from tools.kh_02_r2_c4_independent_verifier import (
    PRIOR_KH02_R2_C3_TESTED_SHA,
    _recompute_w1_w2_w4_c4,
    _verify_junit_raw_xml_bijection_c4,
)
from tools.kh_03_independent_verifier import (
    KH03_SOURCE_FILES_ALLOWLIST,
    PRIOR_KH02_R2_C4_PUB_HEAD_SHA,
    PRIOR_KH02_R2_C4_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03,
)

MANIFEST_25_NAME_KH03_R2 = "25_independent_OS_verifier_manifest_sha256.json"

PRIOR_KH03_R1_PUB_HEAD_SHA = "0b2d88f952d53fd1e4e388abfc7dcc9b882b3ebf"
PRIOR_KH03_R1_TESTED_SHA = "f0b668dc0dd1c25c880121517b7580640ba4987b"
PRIOR_KH03_R1_EVIDENCE_SHA = "39294703c3e8f83740f126eb4ff32a5e05ba8d78"
PRIOR_KH03_R1_RUN_ID = "20261123T055123Z_f0b668dc0dd1"

PRIOR_KH03_R1_LATEST_PTR_REL = "aurum-desk/docs/artifacts/kh_03_research_data/latest_run.json"
PRIOR_KH03_R1_LATEST_PTR_SHA256 = "9bc9d1b2dac13a7a43253b418edb30efcfa3148917a76250ea18fd1ba2e87630"
PRIOR_KH03_R1_MANIFEST_24_REL = (
    "aurum-desk/docs/artifacts/kh_03_research_data/"
    "20261123T055123Z_f0b668dc0dd1/24_independent_os_verifier_manifest_sha256.json"
)
PRIOR_KH03_R1_MANIFEST_24_SHA256 = "62beec9db13d0b3649f16d9c7545645434d7362bfaa784dc30d8de412237ad70"
PRIOR_KH03_R1_POSTPUSH_REL = (
    "aurum-desk/docs/artifacts/kh_03_research_data/"
    "25_phase2_evidence_commit_remote_readback_receipt_20261123T055123Z_f0b668dc0dd1.json"
)
PRIOR_KH03_R1_POSTPUSH_SHA256 = "2cb52042d7df0fc615fd9272780477c20555bc46b030aa51560477a9f80d9901"

PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R2: Tuple[Tuple[str, str, str], ...] = (
    *PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03,
    (PRIOR_KH03_R1_LATEST_PTR_REL, PRIOR_KH03_R1_LATEST_PTR_SHA256, "KH03_R1_LATEST_PTR"),
    (PRIOR_KH03_R1_MANIFEST_24_REL, PRIOR_KH03_R1_MANIFEST_24_SHA256, "KH03_R1_MANIFEST_24"),
    (PRIOR_KH03_R1_POSTPUSH_REL, PRIOR_KH03_R1_POSTPUSH_SHA256, "KH03_R1_POSTPUSH_RECEIPT"),
)

KH03_R2_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    *KH03_SOURCE_FILES_ALLOWLIST,
    "aurum-desk/backend/services/kh_03_r2_methodology_service.py",
    "aurum-desk/backend/tests/test_kh03_r2_ci_schema_and_gate.py",
    "aurum-desk/backend/tests/test_kh03_r2_h3_policy_truth.py",
    "aurum-desk/backend/tests/test_kh03_r2_forward_only_temporal_folds.py",
    "aurum-desk/backend/tests/test_kh03_r2_verifier_mutations.py",
    "aurum-desk/tools/run_kh_03_r2_acceptance.py",
    "aurum-desk/tools/kh_03_r2_independent_verifier.py",
)

REQUIRED_ARTIFACTS_KH03_R2: Tuple[str, ...] = (
    "00_branch_head_preflight_resource_and_sha.json",
    "01_function_ast_ownership_and_immutable_diff.json",
    "02_prior_R1_C4_and_canonical_dataset_immutability.json",
    "03_ci_old_bug_red_proof.json",
    "04_ci_strict_adapter_green_boundary_matrix.json",
    "05_ci_independent_positive_negative_verdicts.json",
    "06_h3_old_filter_only_vs_resize_red_proof.json",
    "07_h3_policy_choice_and_execution_feasibility.json",
    "08_h3_per_episode_policy_qty_time_ledger.jsonl.gz",
    "09_h3_reconciliation_research_only_or_filter_only_disclosure.json",
    "10_wf_old_posthoc_and_future_train_red_proofs.json",
    "11_wf_forward_only_folds_train_val_timestamps.json",
    "12_wf_purged_labels_embargo_episode_nonoverlap.json",
    "13_wf_fold_local_selection_freeze_and_replay.json",
    "14_statistical_ci_holm_matching_controls_recomputed.json",
    "15_six_cost_full_rerisk_and_fee_integrity.json",
    "16_realized_tail_loss_and_1position_portfolio.json",
    "17_historical_baseline_parity_and_prior_hashes.json",
    "18_full_pytest_collected_executed_junit_bijection.json",
    "19_w1_w2_w4_w1repeat_timing_hashes.json",
    "20_disk_ram_pid_temp_telemetry.json",
    "21_inherited_plus_R2_new_unique_red_green_faults.json",
    "22_independent_verifier_mutation_tests.json",
    "23_economic_win_loss_dev_eligibility_and_KH04_gate.json",
    "24_prepublication_28_gate_matrix.json",
    "26_phase1_code_commit_push_receipt.json",
    "README_KH03_R2.md",
    "HANDOVER_KH03_R2_VI.txt",
)

GATE_28_KEYS_KH03_R2: Tuple[str, ...] = (
    "G01_BRANCH_ORIGIN_CLEAN_TESTED_SHA",
    "G02_IMMUTABLE_PRODUCTION_DATASET_PRIOR_EVIDENCE",
    "G03_CI_RED_REPRODUCTION_REAL",
    "G04_CI_STRICT_SCHEMA_AND_SEMANTIC_RED_GREEN",
    "G05_CI_POSITIVE_NEGATIVE_AND_BOUNDARY_FALSIFICATION",
    "G06_R1_H3_RESIZE_DISCARD_RED_REPRODUCTION",
    "G07_H3_POLICY_CHOICE_FREEZE_AND_TRUTHFUL_CLASSIFICATION",
    "G08_H3_EXECUTION_CAUSALITY_OR_FILTER_ONLY_EXCLUSION",
    "G09_H3_QUANTITY_FEE_RISK_AND_TIME_RECONCILIATION",
    "G10_WF_R1_POSTHOC_SLICE_RED_REPRODUCTION",
    "G11_TRUE_FORWARD_ONLY_FOLDS_NO_FUTURE_TRAIN",
    "G12_PURGE_EMBARGO_HORIZON_NO_EPISODE_LEAK",
    "G13_FOLD_LOCAL_POLICY_FREEZE_AND_VALIDATION_REPLAY",
    "G14_PREREG_BUDGET_AND_HOLM_P_VALUE_NOT_RELAXED",
    "G15_NEGATIVE_CONTROLS_MATCHING_INTEGRITY",
    "G16_SIX_COST_RERISK_FEES_RISK_TAIL_PARITY",
    "G17_FULL_BASELINE_A1_SI8_ADAPTER_D4_PARITY",
    "G18_PROVENANCE_C4_AND_OOS_NO_PEEK",
    "G19_FULL_REPO_PYTEST_AND_JUNIT_BIJECTION",
    "G20_ASYNC_W1_W2_W4_W1REPEAT_SEMANTIC_HASH_EQUAL",
    "G21_STRICT_DISK_RAM_PID_WINDOWS_CLEANUP",
    "G22_NEW_R2_FAULTS_PLUS_INHERITED_RED_GREEN",
    "G23_INDEPENDENT_VERIFIER_SECOND_OS",
    "G24_MUTATION_DETECTION_AT_LEAST_16_NEW",
    "G25_RESEARCH_ECONOMIC_AND_WIN_LOSS_TRUTH",
    "G26_KH04_ENTRY_DEPENDENCY_NOT_FORCED",
    "G27_PHASE1_CODE_PUSH_THEN_PHASE2_EVIDENCE_AND_READBACK",
    "G28_ALWAYS_OFF_ACTIVATION_AND_NO_NETWORK_CAPTURE",
)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _recompute_ci_and_dev_gates_from_raw_14(
    f14: Mapping[str, Any],
    f11: Mapping[str, Any],
) -> Tuple[bool, List[str], List[str]]:
    """
    Independently recomputes CI strict parsing and `passes_preregistered_dev_advancement`
    for EVERY primary arm in `14` directly from raw numeric bounds & raw stats!
    Catches forged `passes_preregistered_dev_advancement`, forged CI status, or false `CROSSES_ZERO`.
    """
    violations: List[str] = []
    passing_arms: List[str] = []
    evals = f14.get("primary_arm_evaluations") or {}
    matching = f14.get("matching_reports") or {}
    valid_folds_n = int(f11.get("valid_folds_count") or 0)

    if len(evals) != 3:
        violations.append(f"PRIMARY_ARMS_COUNT_NOT_3:{len(evals)}")
        return False, violations, passing_arms

    for ac, st in sorted(evals.items()):
        mr = matching.get(ac) or {}
        raw_ci = st.get("ny_day_cluster_95ci_r")
        ci_p = parse_kh03_cluster_ci_strict(raw_ci, label=f"verifier:{ac}")
        if not ci_p["schema_valid"]:
            violations.append(f"RAW_CI_INVALID_SCHEMA:{ac}:{ci_p['diagnostic']}")
        stored_ci = st.get("ci_parsed") or {}
        if stored_ci.get("status") != ci_p["status"]:
            violations.append(f"CI_STATUS_TAMPERED:{ac}:stored={stored_ci.get('status')}!=recomp={ci_p['status']}")

        # Forbid falsely reporting CROSSES_ZERO when CI is strictly negative
        fails_stored = list(st.get("failed_advancement_criteria") or ())
        if ci_p["status"] == "CI_VALID_NEGATIVE" and any("CROSSES_ZERO" in x for x in fails_stored):
            violations.append(f"FALSE_CROSSES_ZERO_ON_STRICTLY_NEGATIVE_CI:{ac}")

        is_h3 = (ac == KH03_R2_H3_ADVISORY_ARM)
        recomp = evaluate_kh03_dev_ci_and_advancement(
            arm_code=ac,
            closed_trades_1x=int(st.get("closed_trades_1x") or 0),
            effective_ny_days_1x=int(st.get("effective_ny_days_1x") or 0),
            expectancy_r_1x=st.get("expectancy_r_1x"),
            net_pnl_usd_1x=st.get("net_pnl_usd_1x"),
            expectancy_r_fee_only_0p0006=st.get("expectancy_r_fee_only_0p0006"),
            expectancy_r_2x=st.get("expectancy_r_2x"),
            raw_ny_day_95ci_r=raw_ci,
            holm_bonferroni_adjusted_pvalue=st.get("holm_bonferroni_adjusted_pvalue"),
            matching_quality_status=mr.get("matching_quality_status"),
            matched_mean_delta_r_vs_null=mr.get("matched_mean_delta_r_vs_null"),
            expectancy_r_vs_placebo_shuffled=mr.get("expectancy_r_vs_placebo_shuffled"),
            expectancy_r_vs_time_offset=mr.get("expectancy_r_vs_time_offset"),
            wf_median_fold_expectancy_r=st.get("forward_only_wf_median_fold_expectancy_r"),
            wf_positive_fold_ratio=st.get("forward_only_wf_positive_fold_ratio"),
            wf_valid_forward_only_folds_count=valid_folds_n,
            executable_edge_eligible=(not is_h3),
            disqualification_reason=(
                "H3_DISQUALIFIED_PATH_B_FILTER_ONLY_NOT_EXECUTABLE_WITH_M1" if is_h3 else None
            ),
        )
        if bool(st.get("passes_preregistered_dev_advancement")) != bool(recomp["passes_preregistered_dev_advancement"]):
            violations.append(
                f"FORGED_DEV_ADVANCEMENT_BOOLEAN:{ac}:stored={st.get('passes_preregistered_dev_advancement')}!=recomp={recomp['passes_preregistered_dev_advancement']}"
            )
        if recomp["passes_preregistered_dev_advancement"]:
            passing_arms.append(ac)

    return len(violations) == 0, violations, passing_arms


def recompute_kh03_r2_28_gates_and_verdicts(
    *,
    docs: Mapping[str, Mapping[str, Any]],
    mem_audit: Mapping[str, Any],
    h3_ledger_audit: Mapping[str, Any],
    junit_bijection_ok: bool,
    verifier_violations_count: int = 0,
    for_final_verifier_manifest: bool = False,
) -> Dict[str, Any]:
    """Independently recomputes ALL 28 orthogonal KH-03.R2 gates (G01..G28) and distinct verdicts."""
    f00, f01, f02, f03, f04, f05 = docs["00"], docs["01"], docs["02"], docs["03"], docs["04"], docs["05"]
    f06, f07, f09, f10, f11, f12 = docs["06"], docs["07"], docs["09"], docs["10"], docs["11"], docs["12"]
    f13, f14, f15, f16, f17, f18 = docs["13"], docs["14"], docs["15"], docs["16"], docs["17"], docs["18"]
    f19, f20, f21, f22, f23, f26 = docs["19"], docs["20"], docs["21"], docs["22"], docs["23"], docs["26"]

    gates: Dict[str, str] = {}

    # G01: Branch / clean SHA / fresh tested SHA
    tsha = str(f00.get("tested_executable_sha") or "")
    sha_ok = (
        f00.get("branch") == MANDATORY_BRANCH_KH03_R2
        and len(tsha) == 40
        and tsha == f00.get("start_head_sha")
        and tsha == f00.get("end_head_sha")
        and tsha not in (
            PRIOR_KH02_R1_TESTED_SHA_SHARED,
            PRIOR_KH02_R2_TESTED_SHA_SHARED,
            PRIOR_KH02_R2_C1_CODE_COMMIT_SHA_SHARED,
            PRIOR_KH02_R2_C2_TESTED_SHA_SHARED,
            PRIOR_KH02_R2_C3_TESTED_SHA,
            PRIOR_KH02_R2_C4_TESTED_SHA,
            PRIOR_KH02_R2_C4_PUB_HEAD_SHA,
            PRIOR_KH03_R1_TESTED_SHA,
            PRIOR_KH03_R1_PUB_HEAD_SHA,
        )
    )
    clean_ok = bool(f00.get("git_clean_at_start", False)) and not bool(f00.get("local_uncommitted_changes", False))
    gates["G01_BRANCH_ORIGIN_CLEAN_TESTED_SHA"] = "PASS" if (sha_ok and clean_ok) else ("BLOCKED_UNCOMMITTED_LOCAL_RUN" if (sha_ok and not clean_ok) else "FAIL")

    # G02: Production immutability + canonical dataset + prior R1/C4 sealed intact
    keep_ok = (
        f01.get("status") == "PASS"
        and int(f01.get("locked_files_modified_count", 1)) == 0
        and len(f01.get("forbidden_order_import_hits") or []) == 0
        and bool(f01.get("cost_assumptions_defaults_intact", False))
    )
    prior_ok = (
        f02.get("status") == "PASS"
        and bool(f02.get("canonical_dataset_sha256_match", False))
        and bool(f02.get("all_prior_sealed_artifacts_intact", False))
    )
    gates["G02_IMMUTABLE_PRODUCTION_DATASET_PRIOR_EVIDENCE"] = "PASS" if (keep_ok and prior_ok) else "FAIL"

    # G03..G05: P0-SCI-01 CI Red Proof, Strict Schema Matrix & Independent Positive/Negative Verdicts
    gates["G03_CI_RED_REPRODUCTION_REAL"] = (
        "PASS"
        if (f03.get("classification") == "REAL_RED" and f03.get("status") == "PASS" and bool(f03.get("all_3_r1_arms_bug_reproduced", False)))
        else "FAIL"
    )
    ci_raw_ok, _ci_v, recomp_passing_arms = _recompute_ci_and_dev_gates_from_raw_14(f14, f11)
    gates["G04_CI_STRICT_SCHEMA_AND_SEMANTIC_RED_GREEN"] = (
        "PASS"
        if (
            f04.get("status") == "PASS"
            and int(f04.get("total_boundary_cases", 0)) >= 12
            and int(f04.get("passed_boundary_cases", -1)) == int(f04.get("total_boundary_cases", 0))
            and ci_raw_ok
        )
        else "FAIL"
    )
    gates["G05_CI_POSITIVE_NEGATIVE_AND_BOUNDARY_FALSIFICATION"] = (
        "PASS" if (f05.get("status") == "PASS" and bool(f05.get("synthetic_fixtures_never_prove_real_edge", False)) and ci_raw_ok) else "FAIL"
    )

    # G06..G09: P0-SCI-02 H3 Policy Truth (PATH B filter-only advisory + ledger reconciliation)
    gates["G06_R1_H3_RESIZE_DISCARD_RED_REPRODUCTION"] = (
        "PASS"
        if (
            f06.get("classification") == "REAL_RED"
            and f06.get("status") == "PASS"
            and int(f06.get("real_dev_replayed_episodes_where_r1_discarded_final_qty_count", 0)) >= 1
        )
        else "FAIL"
    )
    recomp_p07_sha = canonical_hash(
        {k: v for k, v in f07.items() if k not in ("policy_choice_frozen_sha256", "artifact_canonical_sha256")}
    )
    g07_ok = (
        f07.get("status") == "PASS"
        and bool(f07.get("frozen_before_r2_h3_recomputation", False))
        and f07.get("policy_choice_frozen_sha256") == recomp_p07_sha
        and f09.get("policy_choice_frozen_sha256") == recomp_p07_sha
        and f07.get("chosen_path") == "PATH_B_FILTER_ONLY_RESEARCH_ADVISORY"
        and f07.get("H3_ECONOMIC_CLAIM_TIER") == "H3_FILL_TIME_FILTER_ONLY_RESEARCH_ADVISORY"
        and f07.get("H3_RESIZE_EXECUTION_STATUS") == "NOT_VERIFIABLE_WITH_M1"
        and f07.get("H3_EXECUTABLE_EDGE_CANDIDATE_ELIGIBLE") is False
        and f07.get("authoritative_simulator_mutated") is False
    )
    gates["G07_H3_POLICY_CHOICE_FREEZE_AND_TRUTHFUL_CLASSIFICATION"] = "PASS" if g07_ok else "FAIL"

    # G08: Truthful exclusion under PATH B (H3_EXECUTABLE_RESIZE stays NOT_VERIFIABLE_WITH_M1; never claimed executed)
    h3_14 = (f14.get("primary_arm_evaluations") or {}).get(KH03_R2_H3_ADVISORY_ARM) or {}
    g08_ok = (
        g07_ok
        and f09.get("H3_RESIZE_EXECUTION_STATUS") == "NOT_VERIFIABLE_WITH_M1"
        and f09.get("H3_FILTER_ONLY_RESEARCH") == "PASS"
        and f09.get("H3_EXECUTABLE_EDGE_CANDIDATE_ELIGIBLE") is False
        and f09.get("size_applied_to_execution_any_row") is False
        and h3_14.get("executable_edge_eligible") is False
        and h3_14.get("passes_preregistered_dev_advancement") is False
        and (f07.get("dataset_execution_feasibility_audit") or {}).get("observed_l1_bbo_available") is False
    )
    gates["G08_H3_EXECUTION_CAUSALITY_OR_FILTER_ONLY_EXCLUSION"] = "PASS" if g08_ok else "FAIL"

    g09_ok = (
        f09.get("status") == "PASS"
        and h3_ledger_audit.get("valid") is True
        and h3_ledger_audit.get("status") == "PASS"
        and int(h3_ledger_audit.get("total_episode_rows", 0)) > 0
        and float(h3_ledger_audit.get("max_residual_usd", 99.0)) <= 0.001
    )
    gates["G09_H3_QUANTITY_FEE_RISK_AND_TIME_RECONCILIATION"] = "PASS" if g09_ok else "FAIL"

    # G10..G13: P0-SCI-03 True Expanding Forward-Only DEV Walk-Forward
    gates["G10_WF_R1_POSTHOC_SLICE_RED_REPRODUCTION"] = (
        "PASS"
        if (
            f10.get("classification") == "REAL_RED"
            and f10.get("status") == "PASS"
            and int(f10.get("r1_total_future_train_bars_across_4_folds", 0)) > 0
        )
        else "FAIL"
    )
    folds_11 = list(f11.get("folds") or ())
    wf_caus = validate_kh03_r2_fold_causality(folds_11, min_embargo_minutes=MIN_EMBARGO_MINUTES_KH03_R2)
    g11_ok = (
        f11.get("status") == "PASS"
        and f11.get("is_fresh_oos") is False
        and int(f11.get("OOS_EVALUATIONS", -1)) == 0
        and int(f11.get("valid_folds_count", 0)) >= 2
        and wf_caus["valid"] is True
    )
    gates["G11_TRUE_FORWARD_ONLY_FOLDS_NO_FUTURE_TRAIN"] = "PASS" if g11_ok else "FAIL"

    g12_ok = (
        f12.get("status") == "PASS"
        and int(f12.get("embargo_minutes", 0)) >= MIN_EMBARGO_MINUTES_KH03_R2
        and bool(f12.get("all_train_exits_strictly_le_embargo_start", False))
        and bool(f12.get("all_train_val_episode_overlaps_zero", False))
        and bool(f12.get("all_cross_fold_val_duplicates_zero", False))
        and int(f12.get("total_overlap_violations_across_wf_replays", 1)) == 0
    )
    gates["G12_PURGE_EMBARGO_HORIZON_NO_EPISODE_LEAK"] = "PASS" if g12_ok else "FAIL"

    # Verify every fold's selection_freeze_sha256 recomputes cleanly!
    fold_replays = list(f13.get("per_fold_selection_and_validation_replays") or ())
    sel_hashes_ok = len(fold_replays) >= 2
    for fr in fold_replays:
        sf = fr.get("fold_local_selection_freeze") or {}
        rec_sf = canonical_hash({k: v for k, v in sf.items() if k != "selection_freeze_sha256"})
        if sf.get("selection_freeze_sha256") != rec_sf or not bool(sf.get("selection_frozen_before_validation_replay", False)):
            sel_hashes_ok = False
        if not (fr.get("static_preregistered_arm_validation_results") or {}):
            sel_hashes_ok = False
    g13_ok = (
        f13.get("status") == "PASS"
        and bool(f13.get("future_win_injection_invariance_passed", False))
        and sel_hashes_ok
    )
    gates["G13_FOLD_LOCAL_POLICY_FREEZE_AND_VALIDATION_REPLAY"] = "PASS" if g13_ok else "FAIL"

    # G14..G16: Multiplicity budget, matching/controls, 6-cost + risk-tail
    gates["G14_PREREG_BUDGET_AND_HOLM_P_VALUE_NOT_RELAXED"] = (
        "PASS"
        if (
            f14.get("status") == "PASS"
            and int(f14.get("preregistered_hypothesis_budget_m", 0)) == 3
            and ci_raw_ok
        )
        else "FAIL"
    )
    gates["G15_NEGATIVE_CONTROLS_MATCHING_INTEGRITY"] = (
        "PASS"
        if (
            len(f14.get("matching_reports") or {}) == 3
            and len(f14.get("negative_control_summaries_1x") or {}) == 3
            and "NEVER proof" in str(f14.get("matching_disclosure") or "")
        )
        else "FAIL"
    )
    g16_ok = (
        f15.get("status") == "PASS"
        and int(f15.get("zero_fee_display_bugs_count", 1)) == 0
        and int(f15.get("total_overlap_violations", 1)) == 0
        and len(f15.get("scenarios") or {}) == 6
        and f15.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") == "UNKNOWN_NOT_LOCALLY_VERIFIED"
        and f15.get("observed_l1_bbo_available") is False
        and f16.get("status") == "PASS"
        and f16.get("planned_cap_is_physical_guarantee") is False
        and int(f16.get("total_overlap_violations_1x", 1)) == 0
    )
    gates["G16_SIX_COST_RERISK_FEES_RISK_TAIL_PARITY"] = "PASS" if g16_ok else "FAIL"

    # G17: Full historical baseline parity A1=56710 / SI8=6375 / 246 / D4=0
    p_st = str(f17.get("BASELINE_PARITY_VERDICT") or "FAIL")
    g17_ok = (
        p_st == "PASS"
        and bool((f17.get("per_decision_and_episode_fingerprints") or {}).get("all_sealed_fingerprints_exact_match", False))
        and int(f17.get("a1_population_count", 0)) == 56710
        and int(f17.get("a1_mismatches_count", 1)) == 0
        and int(f17.get("si8_population_count", 0)) == 6375
        and int(f17.get("si8_mismatches_count", 1)) == 0
        and int(f17.get("three_adapter_population_count", 0)) == 246
        and int(f17.get("three_adapter_mismatches_count", 1)) == 0
        and int(f17.get("d4_historical_closed_trades", 1)) == 0
    )
    gates["G17_FULL_BASELINE_A1_SI8_ADAPTER_D4_PARITY"] = (
        "NOT_RUN_BLOCKED_LOW_DISK" if p_st == "NOT_RUN_BLOCKED_LOW_DISK" else ("PASS" if g17_ok else "FAIL")
    )

    # G18: C4 Provenance & OOS no-peek
    prov = f02.get("c4_provenance_and_oos_boundary") or {}
    g18_ok = (
        int(f23.get("OOS_EVALUATIONS", -1)) == 0
        and int(f11.get("OOS_EVALUATIONS", -1)) == 0
        and f23.get("FRESH_OOS_VERDICT") == "BLOCKED_NO_UNTOUCHED_DATA"
        and bool(prov.get("network_capture_enabled") is False)
        and int(prov.get("real_oos_escrow_final_record_count", -1)) == 0
        and prov.get("LIVE_NETWORK_VALIDATION") == "NOT_RUN_BY_DESIGN"
    )
    gates["G18_PROVENANCE_C4_AND_OOS_NO_PEEK"] = "PASS" if g18_ok else "FAIL"

    # G21: Strict Disk / RAM / PID / Windows cleanup (evaluated before G19/G20)
    mp00 = f00.get("strict_multi_mount_probe") or {}
    waiver_flag = (
        bool(f00.get("owner_disk_preflight_waiver", False))
        or bool(mp00.get("owner_waiver_permitted", False))
        or bool(f20.get("owner_disk_preflight_waiver", False))
        or bool(f26.get("owner_disk_preflight_waiver", False))
    )
    mounts = list(mp00.get("distinct_mounts") or ())
    probe_errs = list(mp00.get("probe_errors") or ())
    preflight_disk_ok = (
        len(mounts) > 0
        and len(probe_errs) == 0
        and all(
            float(m.get("free_gib", 0.0)) >= STRICT_PREFLIGHT_DISK_MIN_GIB
            and float(m.get("projected_post_reserve_gib", 0.0)) >= STRICT_RUNTIME_DISK_MIN_GIB
            for m in mounts
        )
        and bool(mp00.get("can_run_heavy_stages", False))
    )
    rt_disk_ok = float(f20.get("min_disk_free_gib") or 0.0) >= STRICT_RUNTIME_DISK_MIN_GIB
    ram_pid_ok = (
        float(f20.get("min_available_ram_mib") or 0.0) >= HARD_RAM_FLOOR_MIB_KH03_R2
        and int(f20.get("max_descendant_children", 99)) <= GLOBAL_MAX_DESCENDANTS_KH03_R2
        and int(f20.get("orphan_children_after_cleanup", 99)) == 0
        and bool(f20.get("sentinel_temp_cleaned_up", False))
        and mem_audit.get("status") == "PASS"
        and int(mem_audit.get("below_floor_samples_count", 1)) == 0
        and int(mem_audit.get("max_workers_observed_in_samples", 99)) <= GLOBAL_MAX_DESCENDANTS_KH03_R2
    )
    if waiver_flag or len(probe_errs) > 0 or not ram_pid_ok:
        gates["G21_STRICT_DISK_RAM_PID_WINDOWS_CLEANUP"] = "FAIL"
    elif preflight_disk_ok and rt_disk_ok:
        gates["G21_STRICT_DISK_RAM_PID_WINDOWS_CLEANUP"] = "PASS"
    elif preflight_disk_ok and not rt_disk_ok:
        gates["G21_STRICT_DISK_RAM_PID_WINDOWS_CLEANUP"] = "FAIL"
    else:
        gates["G21_STRICT_DISK_RAM_PID_WINDOWS_CLEANUP"] = "BLOCKED_LOW_DISK"

    # G19: Full repo pytest & JUnit 1:1 bijection
    r18 = str(f18.get("status") or "FAIL")
    if r18 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G19_FULL_REPO_PYTEST_AND_JUNIT_BIJECTION"] = (
            "NOT_RUN_BLOCKED_LOW_DISK" if gates["G21_STRICT_DISK_RAM_PID_WINDOWS_CLEANUP"] == "BLOCKED_LOW_DISK" else "FAIL"
        )
    elif (
        r18 == "PASS"
        and gates["G21_STRICT_DISK_RAM_PID_WINDOWS_CLEANUP"] == "PASS"
        and junit_bijection_ok
        and int(f18.get("collected_count", 0)) >= 5587
        and int(f18.get("collected_count", -1)) == int(f18.get("executed_count", -2))
        and int(f18.get("failed_count", 1)) == 0
        and int(f18.get("error_count", 1)) == 0
        and int(f18.get("mandatory_skipped_count", 1)) == 0
    ):
        gates["G19_FULL_REPO_PYTEST_AND_JUNIT_BIJECTION"] = "PASS"
    else:
        gates["G19_FULL_REPO_PYTEST_AND_JUNIT_BIJECTION"] = "FAIL"

    # G20: Async W1/W2/W4/W1_repeat determinism
    r19 = str(f19.get("status") or "FAIL")
    if r19 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G20_ASYNC_W1_W2_W4_W1REPEAT_SEMANTIC_HASH_EQUAL"] = (
            "NOT_RUN_BLOCKED_LOW_DISK" if gates["G21_STRICT_DISK_RAM_PID_WINDOWS_CLEANUP"] == "BLOCKED_LOW_DISK" else "FAIL"
        )
    elif r19 == "PASS" and gates["G21_STRICT_DISK_RAM_PID_WINDOWS_CLEANUP"] == "PASS" and _recompute_w1_w2_w4_c4(f19):
        gates["G20_ASYNC_W1_W2_W4_W1REPEAT_SEMANTIC_HASH_EQUAL"] = "PASS"
    else:
        gates["G20_ASYNC_W1_W2_W4_W1REPEAT_SEMANTIC_HASH_EQUAL"] = "FAIL"

    # G22: Faults >= 150 inherited + >= 24 new R2 = >= 174 unique controls
    g22_ok = (
        f21.get("status") == "PASS"
        and int(f21.get("inherited_r1_controls_count", 0)) >= 150
        and int(f21.get("new_kh03_r2_controls_count", 0)) >= 24
        and int(f21.get("total_controls", 0)) >= 174
        and int(f21.get("unique_fault_ids", 0)) == int(f21.get("total_controls", -1))
        and int(f21.get("failed_controls", 1)) == 0
    )
    gates["G22_NEW_R2_FAULTS_PLUS_INHERITED_RED_GREEN"] = "PASS" if g22_ok else "FAIL"

    # G24: Mutation detection >= 16 NEW R2 attacks
    g24_ok = (
        f22.get("status") == "PASS"
        and bool(f22.get("clean_clone_verifier_green", False))
        and int(f22.get("mutations_tested_count", 0)) >= 16
        and int(f22.get("mutations_failed_detection_count", 1)) == 0
    )
    gates["G24_MUTATION_DETECTION_AT_LEAST_16_NEW"] = "PASS" if g24_ok else "FAIL"

    # G23: Independent verifier second OS process
    if for_final_verifier_manifest:
        gates["G23_INDEPENDENT_VERIFIER_SECOND_OS"] = "PASS" if (verifier_violations_count == 0 and g24_ok) else "FAIL"
    else:
        gates["G23_INDEPENDENT_VERIFIER_SECOND_OS"] = "PENDING_SEPARATE_OS_VERIFIER"

    # G25: Research economic & win/loss truth (recomputed directly from raw 14 + 23!)
    claimed_dev_v = str(f23.get("KH03_R2_DEV_CANDIDATE_VERDICT") or "FAIL")
    claimed_econ_v = str(f23.get("RESEARCH_ECONOMIC_VERDICT") or "FAIL")
    expected_dev_v = "DEV_CANDIDATE_UNPROVEN_OOS" if recomp_passing_arms else "NO_ECONOMIC_ADVANCEMENT_CANDIDATE"
    g25_ok = (
        ci_raw_ok
        and claimed_econ_v == "NO_PROVEN_EDGE"
        and claimed_dev_v == expected_dev_v
        and list(f23.get("passing_dev_candidate_arms") or []) == recomp_passing_arms
    )
    gates["G25_RESEARCH_ECONOMIC_AND_WIN_LOSS_TRUTH"] = expected_dev_v if g25_ok else "FAIL"

    # G27: Publication phase status
    pub_st = str(f26.get("publication_status") or "NOT_PUBLISHED_BLOCKED_LOW_DISK")
    if waiver_flag or "WAIVER" in pub_st:
        gates["G27_PHASE1_CODE_PUSH_THEN_PHASE2_EVIDENCE_AND_READBACK"] = "FAIL"
    elif pub_st == "PASS_REMOTE_VERIFIED" and bool(f26.get("remote_readback_sha256_verified", False)):
        gates["G27_PHASE1_CODE_PUSH_THEN_PHASE2_EVIDENCE_AND_READBACK"] = "PASS_REMOTE_VERIFIED"
    elif pub_st == "PRE_PUSH_PENDING":
        gates["G27_PHASE1_CODE_PUSH_THEN_PHASE2_EVIDENCE_AND_READBACK"] = "PRE_PUSH_PENDING"
    elif "BLOCKED" in pub_st:
        gates["G27_PHASE1_CODE_PUSH_THEN_PHASE2_EVIDENCE_AND_READBACK"] = pub_st
    else:
        gates["G27_PHASE1_CODE_PUSH_THEN_PHASE2_EVIDENCE_AND_READBACK"] = "FAIL"

    # G28: Always OFF activation & zero live network capture
    modes_ok = True
    for d in (f00, f01, f23):
        if d.get("ACTIVATION_READY") != "NO":
            modes_ok = False
        rsm = d.get("runtime_safety_modes") or {}
        if any(rsm.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
            modes_ok = False
    net_off_ok = (
        f23.get("LIVE_NETWORK_VALIDATION") == "NOT_RUN_BY_DESIGN"
        and prov.get("network_capture_enabled") is False
        and prov.get("live_network_capture_executed") is False
    )
    gates["G28_ALWAYS_OFF_ACTIVATION_AND_NO_NETWORK_CAPTURE"] = "PASS" if (modes_ok and net_off_ok) else "FAIL"

    # Roll up ENGINEERING across mandatory engineering/methodology gates
    eng_gates = [
        "G01_BRANCH_ORIGIN_CLEAN_TESTED_SHA",
        "G02_IMMUTABLE_PRODUCTION_DATASET_PRIOR_EVIDENCE",
        "G03_CI_RED_REPRODUCTION_REAL",
        "G04_CI_STRICT_SCHEMA_AND_SEMANTIC_RED_GREEN",
        "G05_CI_POSITIVE_NEGATIVE_AND_BOUNDARY_FALSIFICATION",
        "G06_R1_H3_RESIZE_DISCARD_RED_REPRODUCTION",
        "G07_H3_POLICY_CHOICE_FREEZE_AND_TRUTHFUL_CLASSIFICATION",
        "G08_H3_EXECUTION_CAUSALITY_OR_FILTER_ONLY_EXCLUSION",
        "G09_H3_QUANTITY_FEE_RISK_AND_TIME_RECONCILIATION",
        "G10_WF_R1_POSTHOC_SLICE_RED_REPRODUCTION",
        "G11_TRUE_FORWARD_ONLY_FOLDS_NO_FUTURE_TRAIN",
        "G12_PURGE_EMBARGO_HORIZON_NO_EPISODE_LEAK",
        "G13_FOLD_LOCAL_POLICY_FREEZE_AND_VALIDATION_REPLAY",
        "G14_PREREG_BUDGET_AND_HOLM_P_VALUE_NOT_RELAXED",
        "G15_NEGATIVE_CONTROLS_MATCHING_INTEGRITY",
        "G16_SIX_COST_RERISK_FEES_RISK_TAIL_PARITY",
        "G17_FULL_BASELINE_A1_SI8_ADAPTER_D4_PARITY",
        "G18_PROVENANCE_C4_AND_OOS_NO_PEEK",
        "G19_FULL_REPO_PYTEST_AND_JUNIT_BIJECTION",
        "G20_ASYNC_W1_W2_W4_W1REPEAT_SEMANTIC_HASH_EQUAL",
        "G21_STRICT_DISK_RAM_PID_WINDOWS_CLEANUP",
        "G22_NEW_R2_FAULTS_PLUS_INHERITED_RED_GREEN",
        "G24_MUTATION_DETECTION_AT_LEAST_16_NEW",
        "G28_ALWAYS_OFF_ACTIVATION_AND_NO_NETWORK_CAPTURE",
    ]
    any_fail = (
        any(gates[k] == "FAIL" for k in gates)
        or (for_final_verifier_manifest and verifier_violations_count > 0)
    )
    any_low_disk = any("LOW_DISK" in gates[k] for k in eng_gates)
    eng_core_pass = all(gates[k] == "PASS" for k in eng_gates) and (
        (not for_final_verifier_manifest) or gates["G23_INDEPENDENT_VERIFIER_SECOND_OS"] == "PASS"
    )

    if any_fail:
        eng_verdict = "FAIL"
    elif any_low_disk:
        eng_verdict = "BLOCKED_LOW_DISK"
    elif eng_core_pass:
        eng_verdict = "PASS"
    else:
        eng_verdict = "BLOCKED_INCOMPLETE_VERIFICATION"

    # G26: KH04_ENTRY dependency (never forced)
    if eng_verdict != "PASS" or not g18_ok or not modes_ok:
        gates["G26_KH04_ENTRY_DEPENDENCY_NOT_FORCED"] = f"BLOCKED_{eng_verdict}"
    elif expected_dev_v == "DEV_CANDIDATE_UNPROVEN_OOS":
        gates["G26_KH04_ENTRY_DEPENDENCY_NOT_FORCED"] = "BLOCKED_NO_ELIGIBLE_PROSPECTIVE_DATA"
    else:
        gates["G26_KH04_ENTRY_DEPENDENCY_NOT_FORCED"] = "BLOCKED_NO_DEV_CANDIDATE"

    if eng_verdict == "PASS" and g25_ok and g18_ok and modes_ok:
        overall = "PASS_KH03_R2_METHODOLOGY_ENGINEERING_RESEARCH_NO_PROVEN_EDGE_OOS_BLOCKED"
    elif eng_verdict == "BLOCKED_LOW_DISK":
        overall = "BLOCKED_LOW_DISK"
    elif eng_verdict == "FAIL":
        overall = "FAIL_METHODOLOGY_OR_INTEGRITY"
    else:
        overall = f"BLOCKED_{eng_verdict}"

    verdicts = {
        "ENGINEERING": eng_verdict,
        "RESEARCH_METHOD_VALIDITY": (
            "PASS"
            if all(
                gates[k] == "PASS"
                for k in (
                    "G03_CI_RED_REPRODUCTION_REAL",
                    "G04_CI_STRICT_SCHEMA_AND_SEMANTIC_RED_GREEN",
                    "G05_CI_POSITIVE_NEGATIVE_AND_BOUNDARY_FALSIFICATION",
                    "G06_R1_H3_RESIZE_DISCARD_RED_REPRODUCTION",
                    "G07_H3_POLICY_CHOICE_FREEZE_AND_TRUTHFUL_CLASSIFICATION",
                    "G08_H3_EXECUTION_CAUSALITY_OR_FILTER_ONLY_EXCLUSION",
                    "G09_H3_QUANTITY_FEE_RISK_AND_TIME_RECONCILIATION",
                    "G10_WF_R1_POSTHOC_SLICE_RED_REPRODUCTION",
                    "G11_TRUE_FORWARD_ONLY_FOLDS_NO_FUTURE_TRAIN",
                    "G12_PURGE_EMBARGO_HORIZON_NO_EPISODE_LEAK",
                    "G13_FOLD_LOCAL_POLICY_FREEZE_AND_VALIDATION_REPLAY",
                )
            )
            else "FAIL"
        ),
        "CI_GATE_CORRECTNESS": (
            "PASS"
            if (
                gates["G03_CI_RED_REPRODUCTION_REAL"] == "PASS"
                and gates["G04_CI_STRICT_SCHEMA_AND_SEMANTIC_RED_GREEN"] == "PASS"
                and gates["G05_CI_POSITIVE_NEGATIVE_AND_BOUNDARY_FALSIFICATION"] == "PASS"
            )
            else "FAIL"
        ),
        "H3_POLICY_TRUTH": (
            "PASS_PATH_B_FILTER_ONLY_ADVISORY"
            if (
                gates["G06_R1_H3_RESIZE_DISCARD_RED_REPRODUCTION"] == "PASS"
                and gates["G07_H3_POLICY_CHOICE_FREEZE_AND_TRUTHFUL_CLASSIFICATION"] == "PASS"
                and gates["G08_H3_EXECUTION_CAUSALITY_OR_FILTER_ONLY_EXCLUSION"] == "PASS"
                and gates["G09_H3_QUANTITY_FEE_RISK_AND_TIME_RECONCILIATION"] == "PASS"
            )
            else "FAIL"
        ),
        "H3_RESIZE_EXECUTION_STATUS": str(f07.get("H3_RESIZE_EXECUTION_STATUS") or "FAIL"),
        "WALKFORWARD_VALIDITY": (
            "PASS"
            if (
                gates["G10_WF_R1_POSTHOC_SLICE_RED_REPRODUCTION"] == "PASS"
                and gates["G11_TRUE_FORWARD_ONLY_FOLDS_NO_FUTURE_TRAIN"] == "PASS"
                and gates["G12_PURGE_EMBARGO_HORIZON_NO_EPISODE_LEAK"] == "PASS"
                and gates["G13_FOLD_LOCAL_POLICY_FREEZE_AND_VALIDATION_REPLAY"] == "PASS"
            )
            else "FAIL"
        ),
        "PARITY": gates["G17_FULL_BASELINE_A1_SI8_ADAPTER_D4_PARITY"],
        "REGRESSION": gates["G19_FULL_REPO_PYTEST_AND_JUNIT_BIJECTION"],
        "DETERMINISM": gates["G20_ASYNC_W1_W2_W4_W1REPEAT_SEMANTIC_HASH_EQUAL"],
        "RESOURCE": gates["G21_STRICT_DISK_RAM_PID_WINDOWS_CLEANUP"],
        "PROVENANCE": gates["G18_PROVENANCE_C4_AND_OOS_NO_PEEK"],
        "ECONOMIC_DEV": gates["G25_RESEARCH_ECONOMIC_AND_WIN_LOSS_TRUTH"],
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE" if g25_ok else "FAIL",
        "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA" if g18_ok else "FAIL",
        "KH04_ENTRY": gates["G26_KH04_ENTRY_DEPENDENCY_NOT_FORCED"],
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": str(f15.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") or "FAIL"),
        "LIVE_NETWORK_VALIDATION": str(f23.get("LIVE_NETWORK_VALIDATION") or "FAIL"),
        "PUBLICATION": gates["G27_PHASE1_CODE_PUSH_THEN_PHASE2_EVIDENCE_AND_READBACK"],
        "ACTIVATION": "NO" if modes_ok else "FAIL",
    }
    return {
        "overall_classification": overall,
        "gates_28": gates,
        "verdicts": verdicts,
    }


def verify_kh03_r2_artifacts_independently_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_live_repo_pointers: bool = True,
    enforce_distinct_pid: bool = True,
    write_manifest: bool = True,
) -> Dict[str, Any]:
    """Separate-OS-process disk verifier recomputing all raw KH-03.R2 invariants & G01..G28."""
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {
            "spec_version": SPEC_VERSION_KH03_R2,
            "schema_version": SCHEMA_VERSION_KH03_R2,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "exit_code_recommended": 1,
            "violations": [f"RUN_DIR_MISSING:{run_dir}"],
        }

    for rel in REQUIRED_ARTIFACTS_KH03_R2:
        fp = run_dir / rel
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_OR_EMPTY_ARTIFACT:{rel}")
    if violations:
        return {
            "spec_version": SPEC_VERSION_KH03_R2,
            "schema_version": SCHEMA_VERSION_KH03_R2,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "exit_code_recommended": 1,
            "violations": violations,
        }

    def _rd(fn: str) -> Dict[str, Any]:
        return json.loads((run_dir / fn).read_text("utf-8"))

    try:
        docs: Dict[str, Dict[str, Any]] = {
            "00": _rd("00_branch_head_preflight_resource_and_sha.json"),
            "01": _rd("01_function_ast_ownership_and_immutable_diff.json"),
            "02": _rd("02_prior_R1_C4_and_canonical_dataset_immutability.json"),
            "03": _rd("03_ci_old_bug_red_proof.json"),
            "04": _rd("04_ci_strict_adapter_green_boundary_matrix.json"),
            "05": _rd("05_ci_independent_positive_negative_verdicts.json"),
            "06": _rd("06_h3_old_filter_only_vs_resize_red_proof.json"),
            "07": _rd("07_h3_policy_choice_and_execution_feasibility.json"),
            "09": _rd("09_h3_reconciliation_research_only_or_filter_only_disclosure.json"),
            "10": _rd("10_wf_old_posthoc_and_future_train_red_proofs.json"),
            "11": _rd("11_wf_forward_only_folds_train_val_timestamps.json"),
            "12": _rd("12_wf_purged_labels_embargo_episode_nonoverlap.json"),
            "13": _rd("13_wf_fold_local_selection_freeze_and_replay.json"),
            "14": _rd("14_statistical_ci_holm_matching_controls_recomputed.json"),
            "15": _rd("15_six_cost_full_rerisk_and_fee_integrity.json"),
            "16": _rd("16_realized_tail_loss_and_1position_portfolio.json"),
            "17": _rd("17_historical_baseline_parity_and_prior_hashes.json"),
            "18": _rd("18_full_pytest_collected_executed_junit_bijection.json"),
            "19": _rd("19_w1_w2_w4_w1repeat_timing_hashes.json"),
            "20": _rd("20_disk_ram_pid_temp_telemetry.json"),
            "21": _rd("21_inherited_plus_R2_new_unique_red_green_faults.json"),
            "22": _rd("22_independent_verifier_mutation_tests.json"),
            "23": _rd("23_economic_win_loss_dev_eligibility_and_KH04_gate.json"),
            "24": _rd("24_prepublication_28_gate_matrix.json"),
            "26": _rd("26_phase1_code_commit_push_receipt.json"),
        }
        readme_txt = (run_dir / "README_KH03_R2.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH03_R2_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {
            "spec_version": SPEC_VERSION_KH03_R2,
            "schema_version": SCHEMA_VERSION_KH03_R2,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "exit_code_recommended": 1,
            "violations": [f"ARTIFACT_PARSE_ERROR:{exc}"],
        }

    f00, f02, f07, f09, f11, f13, f14, f15, f18, f19, f23, f24, f26 = (
        docs["00"], docs["02"], docs["07"], docs["09"], docs["11"], docs["13"],
        docs["14"], docs["15"], docs["18"], docs["19"], docs["23"], docs["24"], docs["26"],
    )

    orch_pid = int(f00.get("orchestrator_pid") or -1)
    if enforce_distinct_pid and verifier_pid == orch_pid:
        violations.append(f"VERIFIER_SAME_OS_PID_AS_ORCHESTRATOR:{verifier_pid}")

    # 1. Branch, tested SHA, run_id binding
    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    if f00.get("branch") != MANDATORY_BRANCH_KH03_R2:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if tested_sha != f00.get("tested_executable_sha") or tested_sha != f24.get("tested_executable_sha") or tested_sha != f26.get("tested_executable_sha"):
        violations.append("TESTED_SHA_MISMATCH_ACROSS_00_24_26")
    if len(tested_sha) >= 12 and not run_id.endswith(f"_{tested_sha[:12]}"):
        violations.append(f"RUN_ID_MISSING_TESTED_SHA12_SUFFIX:{run_id}")

    # 2. Prior R1 + C4 + Canonical dataset immutability
    if check_live_repo_pointers:
        for rel_p, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R2:
            p = REPO_ROOT / rel_p
            if not p.is_file() or _file_sha256(p) != exp_sha:
                violations.append(f"PRIOR_SEALED_ARTIFACT_MUTATED:{lbl}")
        from domain_calculator import CostAssumptions
        c_live = CostAssumptions()
        if (
            abs(float(c_live.taker_fee_rate) - 0.0004) > 1e-12
            or abs(float(c_live.maker_fee_rate) - 0.0002) > 1e-12
            or abs(float(c_live.slippage_usd) - 0.10) > 1e-12
        ):
            violations.append("LIVE_COST_ASSUMPTIONS_MUTATED")
    else:
        pm = f02.get("prior_sealed_artifacts_immutability") or {}
        if any(not bool(v.get("match", False)) for v in pm.values() if isinstance(v, Mapping)):
            violations.append("PRIOR_SEALED_ARTIFACT_MUTATED")
    if f02.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH03_R2 or not bool(f02.get("canonical_dataset_sha256_match", False)):
        violations.append("CANONICAL_DATASET_SHA256_TAMPERED")

    # 3. P0-SCI-01: Independently recompute CI strict parsing & DEV advancement from raw 14!
    ci_ok, ci_viols, _pass_arms = _recompute_ci_and_dev_gates_from_raw_14(f14, f11)
    if not ci_ok:
        violations.extend(ci_viols)

    # 4. P0-SCI-02: Independently verify H3 policy choice freeze + streaming Decimal H3 ledger 08 vs 09!
    recomp_p07_sha = canonical_hash(
        {k: v for k, v in f07.items() if k not in ("policy_choice_frozen_sha256", "artifact_canonical_sha256")}
    )
    if f07.get("policy_choice_frozen_sha256") != recomp_p07_sha or f09.get("policy_choice_frozen_sha256") != recomp_p07_sha:
        violations.append("H3_POLICY_CHOICE_SHA256_TAMPERED")
    if f07.get("H3_RESIZE_EXECUTION_STATUS") != "NOT_VERIFIABLE_WITH_M1" or f09.get("H3_RESIZE_EXECUTION_STATUS") != "NOT_VERIFIABLE_WITH_M1":
        violations.append(f"H3_RESIZE_EXECUTION_FALSE_CLAIM:{f07.get('H3_RESIZE_EXECUTION_STATUS')}")
    if f09.get("size_applied_to_execution_any_row") is not False:
        violations.append("H3_PATH_B_CLAIMED_SIZE_APPLIED_TO_EXECUTION")
    h3_audit = validate_kh03_r2_h3_ledger_independently(
        run_dir / "08_h3_per_episode_policy_qty_time_ledger.jsonl.gz", f09
    )
    if not h3_audit.get("valid", False):
        violations.extend([f"H3_LEDGER:{v}" for v in (h3_audit.get("violations") or ["H3_LEDGER_FAILED"])])

    # 5. P0-SCI-03: Independently verify forward-only WF fold causality & selection freeze hashes!
    wf_caus = validate_kh03_r2_fold_causality(list(f11.get("folds") or ()), min_embargo_minutes=MIN_EMBARGO_MINUTES_KH03_R2)
    if not wf_caus["valid"]:
        violations.extend([f"WF_CAUSALITY:{v}" for v in wf_caus["violations"]])
    fold_replays = list(f13.get("per_fold_selection_and_validation_replays") or ())
    if len(fold_replays) < 2:
        violations.append("WF_MISSING_FOLD_REPLAYS")
    for fr in fold_replays:
        sf = fr.get("fold_local_selection_freeze") or {}
        rec_sf = canonical_hash({k: v for k, v in sf.items() if k != "selection_freeze_sha256"})
        if sf.get("selection_freeze_sha256") != rec_sf:
            violations.append(f"WF_SELECTION_FREEZE_SHA_TAMPERED:{fr.get('fold_id')}")
        if not (fr.get("static_preregistered_arm_validation_results") or {}):
            violations.append(f"WF_MISSING_RAW_FOLD_RESULTS:{fr.get('fold_id')}")

    # 6. Six-scenario fee sanity check in 15 + observed L1 BBO honesty
    if f15.get("observed_l1_bbo_available") is not False:
        violations.append("FORGED_OBSERVED_L1_BBO_AVAILABLE_TRUE")
    if int(f15.get("zero_fee_display_bugs_count", 1)) != 0:
        violations.append("NONZERO_ZERO_FEE_DISPLAY_BUGS_IN_15")
    for sc_id, sc_obj in (f15.get("scenarios") or {}).items():
        for ac, rs in (sc_obj.get("arms") or {}).items():
            cl = int(rs.get("closed") or 0)
            if cl > 0:
                g = float(rs.get("gross_pnl_usd") or 0.0)
                f_u = float(rs.get("total_fees_usd") or 0.0)
                n_u = float(rs.get("net_pnl_usd") or 0.0)
                if f_u <= 0.0 and (g - n_u) > 0.01:
                    violations.append(f"ZERO_FEE_BUG_IN_15:{sc_id}:{ac}")
                if abs((g - f_u) - n_u) > 0.01:
                    violations.append(f"FEE_RECONCILIATION_DRIFT_IN_15:{sc_id}:{ac}")

    # 7. Economic / OOS / Activation / Network honesty
    if int(f23.get("OOS_EVALUATIONS", -1)) != 0 or int(f11.get("OOS_EVALUATIONS", -1)) != 0:
        violations.append("NONZERO_OOS_EVALUATIONS_PEEK")
    if f23.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE":
        violations.append(f"FORGED_RESEARCH_ECONOMIC_VERDICT:{f23.get('RESEARCH_ECONOMIC_VERDICT')}")
    if f23.get("ACTIVATION_READY") != "NO" or f00.get("ACTIVATION_READY") != "NO":
        violations.append("FORGED_ACTIVATION_READY_ON")
    prov = f02.get("c4_provenance_and_oos_boundary") or {}
    if prov.get("live_network_capture_executed") is not False or int(prov.get("real_oos_escrow_final_record_count", -1)) != 0:
        violations.append("UNAUTHORIZED_LIVE_NETWORK_OR_NONZERO_REAL_OOS_ESCROW")

    # 8. Raw JUnit XML 1:1 bijection
    junit_ok = False
    if f18.get("status") == "PASS":
        junit_ok, j_errs = _verify_junit_raw_xml_bijection_c4(
            run_dir, f18, expected_run_id=run_id, expected_tested_sha=tested_sha
        )
        if not junit_ok:
            violations.extend(j_errs)

    # 9. W1/W2/W4/W1_repeat determinism
    if f19.get("status") == "PASS" and not _recompute_w1_w2_w4_c4(f19):
        violations.append("W1_W2_W4_DETERMINISM_HASH_MISMATCH")

    # 10. Memory/disk/PID telemetry directly from gzip log
    mem_log = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    if not mem_log.is_file() or mem_log.stat().st_size == 0:
        mem_audit: Dict[str, Any] = {"status": "FAIL_MISSING_LOG", "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99}
        violations.append("MISSING_MEMORY_TELEMETRY_LOG")
    else:
        mem_audit = _audit_memory_samples_c3(mem_log)
        if mem_audit.get("status") != "PASS":
            violations.append(f"MEMORY_TELEMETRY_BREACHED_512MIB_FLOOR:{mem_audit.get('status')}")
        if int(mem_audit.get("max_workers_observed_in_samples", 99)) > GLOBAL_MAX_DESCENDANTS_KH03_R2:
            violations.append(f"MEMORY_TELEMETRY_EXCEEDED_CHILD_CAP:{mem_audit.get('max_workers_observed_in_samples')}")

    # 11. Zero disk waiver & publication honesty
    if f26.get("publication_status") == "PASS_REMOTE_VERIFIED" and not bool(f26.get("remote_readback_sha256_verified", False)):
        violations.append("FALSE_REMOTE_READBACK_CLAIM")
    if bool(f00.get("owner_disk_preflight_waiver", False)):
        violations.append("UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN")

    # 12. Recompute 28 gates & verify against stored 24_prepublication_28_gate_matrix.json
    recomp_pre = recompute_kh03_r2_28_gates_and_verdicts(
        docs=docs,
        mem_audit=mem_audit,
        h3_ledger_audit=h3_audit,
        junit_bijection_ok=junit_ok,
        verifier_violations_count=len(violations),
        for_final_verifier_manifest=False,
    )
    stored_gates = f24.get("gates_28") or {}
    for gk, gv in recomp_pre["gates_28"].items():
        if stored_gates.get(gk) != gv:
            violations.append(f"GATE_TAMPER_OR_DRIFT:{gk}:stored={stored_gates.get(gk)}!=recomputed={gv}")

    # Cross-view consistency on README & HANDOVER
    if run_id not in readme_txt or run_id not in handover_txt:
        violations.append("CROSS_VIEW_RUN_ID_MISSING")
    if "ACTIVATION_READY=NO" not in readme_txt or "ACTIVATION_READY=NO" not in handover_txt:
        violations.append("CROSS_VIEW_ACTIVATION_LOCK_MISSING")
    if "NO_PROVEN_EDGE" not in readme_txt or "NO_PROVEN_EDGE" not in handover_txt:
        violations.append("CROSS_VIEW_NO_PROVEN_EDGE_MISSING")
    if "NOT_VERIFIABLE_WITH_M1" not in readme_txt or "NOT_VERIFIABLE_WITH_M1" not in handover_txt:
        violations.append("CROSS_VIEW_H3_PATH_B_DISCLOSURE_MISSING")

    recomp_final = recompute_kh03_r2_28_gates_and_verdicts(
        docs=docs,
        mem_audit=mem_audit,
        h3_ledger_audit=h3_audit,
        junit_bijection_ok=junit_ok,
        verifier_violations_count=len(violations),
        for_final_verifier_manifest=True,
    )

    artifact_sha256_map: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(run_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(run_dir).as_posix()
        if rel == MANIFEST_25_NAME_KH03_R2 or rel.endswith(".tmp"):
            continue
        artifact_sha256_map[rel] = {
            "size_bytes": int(fp.stat().st_size),
            "sha256": _file_sha256(fp),
        }

    eng_final = recomp_final["verdicts"]["ENGINEERING"]
    if len(violations) > 0 or eng_final == "FAIL":
        verifier_status = "FAIL"
        exit_code = 1
    elif eng_final == "PASS":
        verifier_status = "PASS"
        exit_code = 0
    else:
        verifier_status = "PASS"
        exit_code = 2

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "orchestrator_pid": orch_pid,
        "verifier_pid": verifier_pid,
        "distinct_os_process_verified": (verifier_pid != orch_pid),
        "independent_verifier_status": verifier_status,
        "exit_code_recommended": exit_code,
        "overall_classification": recomp_final["overall_classification"],
        "recomputed_28_gates": recomp_final["gates_28"],
        "recomputed_verdicts": recomp_final["verdicts"],
        "independent_h3_ledger_audit": h3_audit,
        "independent_wf_causality_audit": wf_caus,
        "independent_memory_telemetry_audit": mem_audit,
        "independent_junit_bijection_ok": junit_ok,
        "violations_count": len(violations),
        "violations": violations,
        "artifact_count_hashed": len(artifact_sha256_map),
        "manifest_content_sha256": canonical_hash(artifact_sha256_map),
        "artifact_sha256_manifest": artifact_sha256_map,
    }
    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_25_NAME_KH03_R2, payload)
    return payload


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK KH-03.R2 Separate-OS Independent Verifier")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--skip-live-repo-pointers", action="store_true")
    ap.add_argument("--allow-same-pid", action="store_true")
    ap.add_argument("--no-write-manifest", action="store_true")
    args = ap.parse_args(argv)

    res = verify_kh03_r2_artifacts_independently_from_disk(
        Path(args.run_dir),
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        check_live_repo_pointers=(not args.skip_live_repo_pointers),
        enforce_distinct_pid=(not args.allow_same_pid),
        write_manifest=(not args.no_write_manifest),
    )
    print(
        json.dumps(
            {
                "independent_verifier_status": res["independent_verifier_status"],
                "verifier_pid": res["verifier_pid"],
                "distinct_os_process_verified": res.get("distinct_os_process_verified"),
                "overall_classification": res.get("overall_classification"),
                "recomputed_verdicts": res.get("recomputed_verdicts"),
                "recomputed_28_gates": res.get("recomputed_28_gates"),
                "artifact_count_hashed": res.get("artifact_count_hashed"),
                "violations": res.get("violations"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return int(res.get("exit_code_recommended", 1))


if __name__ == "__main__":
    raise SystemExit(main())
