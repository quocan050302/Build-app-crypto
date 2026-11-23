#!/usr/bin/env python3
"""
aurum-desk/tools/kh_03_r3_independent_verifier.py

AURUM DESK — KH-03.R3 Separate-OS-Process Independent Disk Verifier (G01..G30)
SPEC VERSION: KH-03.R3-v1.0

Executes in a DISTINCT OS process (`verifier_pid != orchestrator_pid`), NEVER imports
the acceptance orchestrator, reads EVERY raw artifact (`00`..`23`, `25`,
`06_per_trade_entry_exit_fee_forensic_ledger.jsonl.gz`, `README_KH03_R3.md`,
`HANDOVER_KH03_R3_VI.txt`, `logs/runtime_memory_samples_0p5s.jsonl.gz`, `junit/*.xml`)
directly from disk, independently recomputes:
  - Prior KH-03.R2 + KH-03.R1 + C4 + Canonical Dataset SHA-256 immutability
  - P0-A Legacy R1 method firewall AST audit & protocol gate (`01`, `03`, `04`)
  - P0-B Streaming Decimal forensic ledger 06 vs 07 & lineage conservation 05
  - P1-C Pre-registration plan SHA 11 -> 12/13/14/16, forward-only WF causality 13/14,
    selection-freeze SHA recomputation, 6-cost fee reconciliation 09/15
  - Raw-evidence strict CI parsing & DEV advancement recomputation directly from 16
  - Baseline parity 17, raw JUnit XML 1:1 bijection 18, W1/W2/W4/W1-repeat 19,
    0.5s RAM/disk/PID telemetry 20, >=194 faults 21, >=16 new mutations 22
  - ALL 30 orthogonal gates (`G01`..`G30`) and independent axis verdicts
and seals `24_independent_OS_verifier_manifest_sha256.json`.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import (
    ACTIVATION_READY_KH02_R2_SHARED as ACTIVATION_READY_KH03_R3,
    CANONICAL_DATASET_SHA256_KH02 as CANONICAL_DATASET_SHA256_KH03_R3,
    GLOBAL_MAX_DESCENDANTS_SHARED as GLOBAL_MAX_DESCENDANTS_KH03_R3,
    HARD_RAM_FLOOR_MIB_SHARED as HARD_RAM_FLOOR_MIB_KH03_R3,
    MANDATORY_BRANCH_KH02_R2_SHARED as MANDATORY_BRANCH_KH03_R3,
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
    evaluate_kh03_dev_ci_and_advancement,
    parse_kh03_cluster_ci_strict,
    validate_kh03_r2_fold_causality,
)
from services.kh_03_r3_diagnostic_service import validate_r3_forensic_ledger_independently
from services.kh_03_r3_legacy_research_guard import (
    SCHEMA_VERSION_KH03_R3,
    SPEC_VERSION_KH03_R3,
    validate_r3_research_output_provenance,
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
    PRIOR_KH02_R2_C4_PUB_HEAD_SHA,
    PRIOR_KH02_R2_C4_TESTED_SHA,
)
from tools.kh_03_r2_independent_verifier import (
    KH03_R2_SOURCE_FILES_ALLOWLIST,
    PRIOR_KH03_R1_PUB_HEAD_SHA,
    PRIOR_KH03_R1_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R2,
)

MANIFEST_24_NAME_KH03_R3 = "24_independent_OS_verifier_manifest_sha256.json"

PRIOR_KH03_R2_PUB_HEAD_SHA = "60a718b8284fc5059f63e34139e679120e0f8e03"
PRIOR_KH03_R2_TESTED_SHA = "a7f6fd52bfb39a3c771a1d3dc3853f1dbdc75a4f"
PRIOR_KH03_R2_EVIDENCE_SHA = "e1f13ab97bf4c76d9f93fbf06b2fdf19216b2bc6"
PRIOR_KH03_R2_RUN_ID = "20261123T073028Z_a7f6fd52bfb3"

PRIOR_KH03_R2_LATEST_PTR_REL = "aurum-desk/docs/artifacts/kh_03_r2_methodology_closure/latest_run.json"
PRIOR_KH03_R2_LATEST_PTR_SHA256 = "380caaae077bd9c712587c2ed2b1506347f192b42e5f1bf1ba8af4ab0c69d1e7"
PRIOR_KH03_R2_MANIFEST_25_REL = (
    "aurum-desk/docs/artifacts/kh_03_r2_methodology_closure/"
    "20261123T073028Z_a7f6fd52bfb3/25_independent_OS_verifier_manifest_sha256.json"
)
PRIOR_KH03_R2_MANIFEST_25_SHA256 = "3ea7188d0efe7920fa6777ec1d65cd17af7300d1b2c55073de8de4678af3909c"
PRIOR_KH03_R2_POSTPUSH_REL = (
    "aurum-desk/docs/artifacts/kh_03_r2_methodology_closure/"
    "27_phase2_remote_evidence_readback_receipt_20261123T073028Z_a7f6fd52bfb3.json"
)
PRIOR_KH03_R2_POSTPUSH_SHA256 = "b3c0c7f82c5ebd130451d77c3fdce639602500df18804812e5517e5aa7a2891e"

PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3: Tuple[Tuple[str, str, str], ...] = (
    *PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R2,
    (PRIOR_KH03_R2_LATEST_PTR_REL, PRIOR_KH03_R2_LATEST_PTR_SHA256, "KH03_R2_LATEST_PTR"),
    (PRIOR_KH03_R2_MANIFEST_25_REL, PRIOR_KH03_R2_MANIFEST_25_SHA256, "KH03_R2_MANIFEST_25"),
    (PRIOR_KH03_R2_POSTPUSH_REL, PRIOR_KH03_R2_POSTPUSH_SHA256, "KH03_R2_POSTPUSH_RECEIPT"),
)

KH03_R3_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    *KH03_R2_SOURCE_FILES_ALLOWLIST,
    "aurum-desk/backend/services/kh_03_r3_legacy_research_guard.py",
    "aurum-desk/backend/services/kh_03_r3_diagnostic_service.py",
    "aurum-desk/backend/services/kh_03_r3_experiment_service.py",
    "aurum-desk/backend/tests/test_kh03_r3_legacy_guard.py",
    "aurum-desk/backend/tests/test_kh03_r3_entry_exit_diagnosis.py",
    "aurum-desk/backend/tests/test_kh03_r3_research_falsification.py",
    "aurum-desk/backend/tests/test_kh03_r3_forward_only_economics.py",
    "aurum-desk/backend/tests/test_kh03_r3_resource_async_and_verifier_mutations.py",
    "aurum-desk/tools/run_kh_03_r3_acceptance.py",
    "aurum-desk/tools/kh_03_r3_independent_verifier.py",
)

REQUIRED_ARTIFACTS_KH03_R3: Tuple[str, ...] = (
    "00_source_branch_clean_sha_disk_ram_preflight.json",
    "01_ast_ownership_legacy_guard_and_keep_as_is_delta.json",
    "02_prior_sealed_sha_and_canonical_dataset_integrity.json",
    "03_r1_legacy_false_pass_red_reproductions.json",
    "04_r2_method_guard_green_and_entrypoint_scans.json",
    "05_full_opportunity_lineage_and_hash_manifest.json",
    "06_per_trade_entry_exit_fee_forensic_ledger.jsonl.gz",
    "07_win_loss_breakdown_denominator_ci_coverage.json",
    "08_stop_target_gap_tie_holding_risk_tail_attribution.json",
    "09_six_cost_scenarios_and_breakeven_accounting.json",
    "10_negative_controls_matching_and_causal_disclaimers.json",
    "11_training_only_bounded_prereg_plan.json",
    "12_candidate_asof_features_and_exact_conservation.json",
    "13_r2_forward_only_fold_boundaries_freeze_no_peek.json",
    "14_validation_fold_independent_results_by_arm.json",
    "15_fixed_fill_vs_dynamic_rerisk_and_expense_truth.json",
    "16_strict_dev_gate_power_holm_cluster_bootstrap.json",
    "17_baseline_parity_A1_SI8_adapter_D4.json",
    "18_full_pytest_junit_collected_executed_bijection.json",
    "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json",
    "20_disk_ram_pid_samples_and_cleanup.json",
    "21_inherited_plus_new_unique_red_green_fault_receipts.json",
    "22_independent_verifier_new_mutation_resistance.json",
    "23_orthogonal_gate_matrix_economic_kh04_oos_activation.json",
    "25_phase1_code_commit_push_readback.json",
    "README_KH03_R3.md",
    "HANDOVER_KH03_R3_VI.txt",
)

GATE_30_KEYS_KH03_R3: Tuple[str, ...] = (
    "G01_BRANCH_CLEAN_EXECUTABLE_SHA",
    "G02_DATASET_AND_PRIOR_ARTIFACT_IMMUTABILITY",
    "G03_PRODUCTION_KEEP_AS_IS_AST_PARITY",
    "G04_LEGACY_R1_BAD_METHOD_RED_REPRO",
    "G05_R2_OR_NEWER_METHOD_GUARD",
    "G06_FULL_OPPORTUNITY_LINEAGE",
    "G07_WIN_LOSS_DENOMINATOR_TRUTH",
    "G08_ENTRY_EXIT_ASOF_CAUSALITY",
    "G09_FEE_AND_RISK_ACCOUNTING",
    "G10_FAILURE_ATTRIBUTION_DISCLOSURE",
    "G11_NEGATIVE_CONTROLS_AND_MATCHING",
    "G12_BOUNDED_PREREG_OR_DIAGNOSIS_ONLY",
    "G13_CANDIDATE_FILTER_CONSERVATION",
    "G14_FORWARD_ONLY_TRAIN_VAL_EMBARGO",
    "G15_FOLD_LOCAL_SELECTION_FROZEN",
    "G16_FRESH_FOLD_REPLAY_AND_LEAKAGE_TESTS",
    "G17_FILL_REALISM_H3_NOT_PROMOTED",
    "G18_SIX_SCENARIO_NET_COST_STRESS",
    "G19_STATISTICS_CI_HOLM_POWER",
    "G20_BASELINE_A1_SI8_ADAPTER_D4_PARITY",
    "G21_FULL_PYTEST_JUNIT_1_TO_1",
    "G22_W1_W2_W4_W1REPEAT_SEMANTIC_DETERMINISM",
    "G23_DISK_RAM_CHILD_PROCESS_SAFETY",
    "G24_UNIQUE_RED_GREEN_FAULTS",
    "G25_INDEPENDENT_VERIFIER_MUTATION_DEFENSE",
    "G26_OS_VERIFIER_MANIFEST_OWN_RECOMPUTATION",
    "G27_RESEARCH_ECONOMIC_VERDICT_HONESTY",
    "G28_FRESH_OOS_ZERO_AND_EXCHANGE_OFF",
    "G29_ACTIVATION_ALWAYS_OFF_AND_KH04_GATED",
    "G30_CODE_AND_EVIDENCE_REMOTE_READBACK",
)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _recompute_r3_dev_gates_from_raw_16(
    f16: Mapping[str, Any],
    f13: Mapping[str, Any],
) -> Tuple[bool, List[str], List[str]]:
    violations: List[str] = []
    passing_arms: List[str] = []
    evals = f16.get("primary_arm_evaluations") or {}
    matching = f16.get("matching_reports") or {}
    valid_folds = int(f13.get("valid_folds_count") or 0)

    if len(evals) == 0 or len(evals) > 3:
        violations.append(f"R3_PRIMARY_ARMS_OUT_OF_BUDGET:{len(evals)}")
        return False, violations, passing_arms

    for ac, st in sorted(evals.items()):
        if ac == KH03_R2_H3_ADVISORY_ARM:
            violations.append("FORBIDDEN_H3_IN_R3_EXECUTABLE_PRIMARY_ARMS")
            continue
        mr = matching.get(ac) or {}
        raw_ci = st.get("ny_day_cluster_95ci_r")
        ci_p = parse_kh03_cluster_ci_strict(raw_ci, label=f"r3_verif:{ac}")
        if not ci_p["schema_valid"]:
            violations.append(f"RAW_CI_INVALID_SCHEMA:{ac}:{ci_p['diagnostic']}")
        if (st.get("ci_parsed") or {}).get("status") != ci_p["status"]:
            violations.append(f"CI_STATUS_TAMPERED:{ac}")
        if ci_p["status"] == "CI_VALID_NEGATIVE" and any("CROSSES_ZERO" in str(x) for x in (st.get("failed_advancement_criteria") or ())):
            violations.append(f"FALSE_CROSSES_ZERO_ON_NEG_CI:{ac}")

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
            wf_valid_forward_only_folds_count=valid_folds,
            executable_edge_eligible=True,
        )
        if bool(st.get("passes_preregistered_dev_advancement")) != bool(recomp["passes_preregistered_dev_advancement"]):
            violations.append(f"FORGED_DEV_ADVANCEMENT_BOOLEAN:{ac}")
        if recomp["passes_preregistered_dev_advancement"]:
            passing_arms.append(ac)

    return len(violations) == 0, violations, passing_arms


ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF = "20261123T085134Z_2301d9c64898"
ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF = "2301d9c648984f6b06ccb4e22f47174c3990d7af"


def _strict_json_loads_r3(raw_text: str) -> Any:
    def _reject_const(tok: str) -> Any:
        raise ValueError(f"NONSTANDARD_JSON_CONSTANT_FORBIDDEN:{tok}")

    return json.loads(raw_text, parse_constant=_reject_const)


def _r3v_is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _r3v_is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _r3v_is_hex64(v: Any) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdefABCDEF" for c in v)


def recompute_r3_worker_policy_from_raw_for_verifier(
    f19: Mapping[str, Any],
    *,
    expected_run_id: str,
    expected_tested_sha: str,
    expected_dataset_sha: str = CANONICAL_DATASET_SHA256_KH03_R3,
    allow_legacy_descriptive_only: bool = False,
) -> Dict[str, Any]:
    """
    COMPLETELY INDEPENDENT verifier-owned raw benchmark & worker-policy recomputer for R3 f19.
    NEVER imports run_kh_03_r3_acceptance or kh_03_r3_c1_worker_policy_service (no common-mode bug).
    Default mode is STRICT fail-closed; archived original R3 run may ONLY be audited read-only
    under explicit `allow_legacy_descriptive_only=True` and is NEVER admitted as C2 certification.
    """
    is_archived_r3 = (
        expected_run_id == ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF
        or expected_tested_sha == ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF
    )
    if allow_legacy_descriptive_only:
        if expected_run_id == ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF and expected_tested_sha == ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF:
            sem_ok = _recompute_w1_w2_w4_c4(f19)
            return {
                "valid": bool(sem_ok),
                "classification": "LEGACY_DESCRIPTIVE_ONLY",
                "admitted_for_new_c2_certification": False,
                "violations": [] if sem_ok else ["LEGACY_R3_SEMANTIC_DETERMINISM_FAILED"],
                "recomputed_pytest": None,
                "recomputed_science": None,
            }
        return {
            "valid": False,
            "classification": "LEGACY_DESCRIPTIVE_REJECTED_NON_ARCHIVED_RUN",
            "admitted_for_new_c2_certification": False,
            "violations": ["LEGACY_DESCRIPTIVE_MODE_ONLY_PERMITTED_FOR_ARCHIVED_R3_RUN"],
            "recomputed_pytest": None,
            "recomputed_science": None,
        }

    viols: List[str] = []
    if is_archived_r3:
        viols.append("STALE_ARCHIVED_R3_REFUSED_FOR_NEW_CERTIFICATION")

    if not isinstance(f19, Mapping):
        return {
            "valid": False,
            "classification": "STRICT_C2_CERTIFICATION_FAILED",
            "admitted_for_new_c2_certification": False,
            "violations": ["F19_NOT_MAPPING"],
            "recomputed_pytest": None,
            "recomputed_science": None,
        }

    # Protocol & top-level certification guard (prevents silent downgrade by stripping fields!)
    wpv = str(f19.get("worker_policy_version") or "")
    if not wpv.startswith("KH-03.R3.C2"):
        viols.append(f"F19_MISSING_OR_FORGED_C2_WORKER_POLICY_VERSION:{wpv!r}")
    if f19.get("policy_certification_status") != "PASS":
        viols.append(f"F19_POLICY_CERTIFICATION_STATUS_NOT_PASS:{f19.get('policy_certification_status')!r}")
    if f19.get("can_certify") is not True:
        viols.append(f"F19_CAN_CERTIFY_NOT_STRICT_TRUE:{f19.get('can_certify')!r}")

    ident = f19.get("raw_benchmark_identity")
    if not isinstance(ident, Mapping):
        viols.append("F19_MISSING_RAW_BENCHMARK_IDENTITY")
    else:
        if ident.get("run_id") != expected_run_id:
            viols.append(f"F19_IDENTITY_RUN_ID_MISMATCH:got={ident.get('run_id')}!=exp={expected_run_id}")
        if ident.get("tested_executable_sha") != expected_tested_sha:
            viols.append(f"F19_IDENTITY_TESTED_SHA_MISMATCH:got={ident.get('tested_executable_sha')}!=exp={expected_tested_sha}")
        if ident.get("canonical_dataset_sha256") != expected_dataset_sha:
            viols.append("F19_IDENTITY_DATASET_SHA_MISMATCH")

    req_workers = {"w=1": 1, "w=2": 2, "w=4": 4, "w=1_repeat": 1}

    def _audit_one(
        raw_sub: Any,
        rep_pol: Any,
        *,
        tag: str,
        canon_dur_key: str,
        cross_keys: Tuple[str, ...],
        cnt_key: str,
        sha_key: str,
        max_cap: int,
        top_sp2: Any,
        top_sp4: Any,
    ) -> Dict[str, Any]:
        sub_v: List[str] = []
        if not isinstance(raw_sub, Mapping):
            sub_v.append(f"R3V_{tag}_RAW_NOT_MAPPING")
            return {"valid": False, "violations": sub_v}
        if raw_sub.get("all_4_runs_identical") is not True:
            sub_v.append(f"R3V_{tag}_ALL_4_RUNS_IDENTICAL_NOT_TRUE")
        if "run_id" in raw_sub and raw_sub.get("run_id") != expected_run_id:
            sub_v.append(f"R3V_{tag}_RAW_RUN_ID_MISMATCH")
        if "tested_executable_sha" in raw_sub and raw_sub.get("tested_executable_sha") != expected_tested_sha:
            sub_v.append(f"R3V_{tag}_RAW_TESTED_SHA_MISMATCH")

        raw_runs = raw_sub.get("runs")
        if not isinstance(raw_runs, list) or len(raw_runs) == 0:
            sub_v.append(f"R3V_{tag}_EMPTY_RUNS")
            return {"valid": False, "violations": sub_v}

        durs: Dict[str, float] = {}
        cnts: Dict[str, int] = {}
        shas: Dict[str, str] = {}

        for idx, row in enumerate(raw_runs):
            if not isinstance(row, Mapping):
                sub_v.append(f"R3V_{tag}_ROW_NOT_OBJ:{idx}")
                continue
            for ck in cross_keys:
                if ck in row:
                    sub_v.append(f"R3V_{tag}_CROSS_KIND_KEY:{ck}")
            ml = row.get("mode_label")
            if ml not in req_workers:
                sub_v.append(f"R3V_{tag}_BAD_MODE:{ml!r}")
                continue
            if ml in durs:
                sub_v.append(f"R3V_{tag}_DUPLICATE_MODE:{ml}")
                continue
            wk = row.get("workers")
            if not _r3v_is_int(wk) or int(wk) != req_workers[ml]:
                sub_v.append(f"R3V_{tag}_WORKERS_MISMATCH:{ml}:{wk!r}")

            if tag == "PYTEST" and "worker_pids" in row:
                pids = row.get("worker_pids")
                if (
                    not isinstance(pids, list)
                    or len(pids) != req_workers[ml]
                    or len(set(pids)) != req_workers[ml]
                    or any((not _r3v_is_int(p) or int(p) <= 0) for p in pids)
                ):
                    sub_v.append(f"R3V_{tag}_BAD_WORKER_PIDS:{ml}")
            if tag == "SCIENCE" and "chunks_count" in row:
                cc = row.get("chunks_count")
                if not _r3v_is_int(cc) or int(cc) != req_workers[ml]:
                    sub_v.append(f"R3V_{tag}_BAD_CHUNKS_COUNT:{ml}:{cc!r}")

            if canon_dur_key not in row:
                sub_v.append(f"R3V_{tag}_MISSING_DURATION_KEY:{ml}:{canon_dur_key}")
            else:
                all_dks = [k for k in (canon_dur_key, "elapsed_sec", "wall_sec", "duration_sec") if k in row]
                vals: List[float] = []
                bad_d = False
                for dk in all_dks:
                    dv = row.get(dk)
                    if not _r3v_is_num(dv):
                        sub_v.append(f"R3V_{tag}_NON_NUMERIC_DURATION:{ml}:{dk}:{dv!r}")
                        bad_d = True
                        continue
                    fv = float(dv)
                    if not math.isfinite(fv) or fv <= 0.0 or fv > 86_400.0:
                        sub_v.append(f"R3V_{tag}_INVALID_DURATION_RANGE:{ml}:{dk}:{dv!r}")
                        bad_d = True
                        continue
                    vals.append(fv)
                if not bad_d and len(vals) > 1 and any(abs(x - vals[0]) > 1e-9 for x in vals[1:]):
                    sub_v.append(f"R3V_{tag}_CONFLICTING_DURATION_KEYS:{ml}")
                if not bad_d and _r3v_is_num(row.get(canon_dur_key)):
                    durs[ml] = float(row[canon_dur_key])

            cv = row.get(cnt_key)
            if not _r3v_is_int(cv) or int(cv) <= 0:
                sub_v.append(f"R3V_{tag}_INVALID_COUNT:{ml}:{cv!r}")
            else:
                cnts[ml] = int(cv)
            sv = row.get(sha_key)
            if not _r3v_is_hex64(sv):
                sub_v.append(f"R3V_{tag}_INVALID_SHA:{ml}:{sv!r}")
            else:
                shas[ml] = str(sv).lower()

        for req_m in ("w=1", "w=2", "w=4", "w=1_repeat"):
            if req_m not in durs:
                sub_v.append(f"R3V_{tag}_MISSING_MODE:{req_m}")
        if len(cnts) == 4 and len(set(cnts.values())) != 1:
            sub_v.append(f"R3V_{tag}_COUNT_DRIFT:{cnts}")
        if "w=1" in shas and "w=1_repeat" in shas and shas["w=1"] != shas["w=1_repeat"]:
            sub_v.append(f"R3V_{tag}_W1_REPEAT_SHA_MISMATCH")
        if len(shas) == 4 and len(set(shas.values())) != 1:
            sub_v.append(f"R3V_{tag}_SEMANTIC_SHA_MISMATCH")

        if any(m not in durs for m in ("w=1", "w=2", "w=4", "w=1_repeat")):
            return {"valid": False, "violations": sub_v}

        means = {m: round(durs[m], 4) for m in ("w=1", "w=2", "w=4")}
        fastest = min(("w=1", "w=2", "w=4"), key=lambda m: (round(means[m], 6), req_workers[m]))
        sp2 = round(durs["w=1"] / max(1e-9, durs["w=2"]), 3)
        sp4 = round(durs["w=1"] / max(1e-9, durs["w=4"]), 3)
        w1_rep = round(durs["w=1_repeat"], 4)
        w1_var = round(abs(durs["w=1_repeat"] - durs["w=1"]) / max(1e-9, min(durs["w=1"], durs["w=1_repeat"])), 4)

        if not isinstance(rep_pol, Mapping):
            sub_v.append(f"R3V_{tag}_POLICY_NOT_MAPPING")
            return {"valid": False, "violations": sub_v}

        if rep_pol.get("status") != "PASS":
            sub_v.append(f"R3V_{tag}_POLICY_STATUS_NOT_PASS:{rep_pol.get('status')!r}")
        if rep_pol.get("can_certify") is not True:
            sub_v.append(f"R3V_{tag}_POLICY_CAN_CERTIFY_NOT_STRICT_TRUE:{rep_pol.get('can_certify')!r}")
        if rep_pol.get("policy_status") != "PASS_MEASURED_AND_SAFE_CAP_APPLIED":
            sub_v.append(f"R3V_{tag}_POLICY_ENUM_INVALID:{rep_pol.get('policy_status')!r}")

        rep_means = rep_pol.get("mean_wall_sec_by_mode")
        if not isinstance(rep_means, Mapping) or len(rep_means) == 0:
            sub_v.append(f"R3V_{tag}_EMPTY_MEAN_WALL_MAP")
        else:
            for m in ("w=1", "w=2", "w=4"):
                gv = rep_means.get(m)
                if not _r3v_is_num(gv) or abs(float(gv) - means[m]) > 1e-3:
                    sub_v.append(f"R3V_{tag}_MEAN_WALL_MISMATCH:{m}:rep={gv}!=recomp={means[m]}")

        if rep_pol.get("measured_fastest_mode") != fastest or rep_pol.get("optimum_worker_policy") != fastest:
            sub_v.append(f"R3V_{tag}_FASTEST_MODE_MISMATCH:rep={rep_pol.get('measured_fastest_mode')}/{rep_pol.get('optimum_worker_policy')}!=recomp={fastest}")

        rep_sp2 = rep_pol.get("measured_speedup_w2_vs_w1")
        rep_sp4 = rep_pol.get("measured_speedup_w4_vs_w1")
        if not _r3v_is_num(rep_sp2) or abs(float(rep_sp2) - sp2) > 0.01:
            sub_v.append(f"R3V_{tag}_SPEEDUP_W2_MISMATCH:rep={rep_sp2}!=recomp={sp2}")
        if not _r3v_is_num(rep_sp4) or abs(float(rep_sp4) - sp4) > 0.01:
            sub_v.append(f"R3V_{tag}_SPEEDUP_W4_MISMATCH:rep={rep_sp4}!=recomp={sp4}")
        if top_sp2 is not None and (not _r3v_is_num(top_sp2) or abs(float(top_sp2) - sp2) > 0.01):
            sub_v.append(f"R3V_{tag}_TOP_SPEEDUP_W2_FORGED:top={top_sp2}!=recomp={sp2}")
        if top_sp4 is not None and (not _r3v_is_num(top_sp4) or abs(float(top_sp4) - sp4) > 0.01):
            sub_v.append(f"R3V_{tag}_TOP_SPEEDUP_W4_FORGED:top={top_sp4}!=recomp={sp4}")

        rep_w1r = rep_pol.get("w1_repeat_wall_sec")
        rep_var = rep_pol.get("w1_repeat_variance_pct")
        if not _r3v_is_num(rep_w1r) or abs(float(rep_w1r) - w1_rep) > 1e-3:
            sub_v.append(f"R3V_{tag}_W1_REPEAT_WALL_MISMATCH")
        if not _r3v_is_num(rep_var) or abs(float(rep_var) - w1_var) > 1e-3:
            sub_v.append(f"R3V_{tag}_W1_REPEAT_VARIANCE_MISMATCH")

        sc = rep_pol.get("safe_worker_cap")
        aw = rep_pol.get("applied_worker_count")
        if not _r3v_is_int(sc) or int(sc) < 1 or int(sc) > max_cap or int(sc) > GLOBAL_MAX_DESCENDANTS_KH03_R3:
            sub_v.append(f"R3V_{tag}_INVALID_SAFE_WORKER_CAP:{sc!r}:max={max_cap}")
        if not _r3v_is_int(aw) or int(aw) < 1 or int(aw) > max_cap or int(aw) > GLOBAL_MAX_DESCENDANTS_KH03_R3 or (_r3v_is_int(sc) and int(aw) > int(sc)):
            sub_v.append(f"R3V_{tag}_APPLIED_WORKER_CAP_EXCEEDED:{aw!r}:safe_cap={sc!r}:max={max_cap}")
        if rep_pol.get("full_suite_speedup_extrapolated") is not False:
            sub_v.append(f"R3V_{tag}_FULL_SUITE_EXTRAPOLATED_FORBIDDEN")
        if rep_pol.get("timing_determinism_claimed") is not False:
            sub_v.append(f"R3V_{tag}_TIMING_DETERMINISM_CLAIMED_FORBIDDEN")

        return {
            "valid": len(sub_v) == 0,
            "means": means,
            "fastest": fastest,
            "speedup_w2_vs_w1": sp2,
            "speedup_w4_vs_w1": sp4,
            "w1_repeat_wall_sec": w1_rep,
            "w1_repeat_variance_pct": w1_var,
            "semantic_sha256": next(iter(shas.values())) if len(set(shas.values())) == 1 else None,
            "violations": sub_v,
        }

    p_aud = _audit_one(
        f19.get("pytest_controlled_subset_w1_w2_w4_repeat"),
        f19.get("pytest_optimum_worker_policy"),
        tag="PYTEST",
        canon_dur_key="wall_clock_seconds",
        cross_keys=("wall_seconds", "canonical_sorted_sha256", "rows_count"),
        cnt_key="executed_tests_count",
        sha_key="canonical_sorted_outcome_sha256",
        max_cap=3,
        top_sp2=f19.get("measured_pytest_speedup_w2_vs_w1"),
        top_sp4=f19.get("measured_pytest_speedup_w4_vs_w1"),
    )
    s_aud = _audit_one(
        f19.get("r2_science_slice_w1_w2_w4_repeat"),
        f19.get("science_optimum_worker_policy"),
        tag="SCIENCE",
        canon_dur_key="wall_seconds",
        cross_keys=("wall_clock_seconds", "canonical_sorted_outcome_sha256", "executed_tests_count"),
        cnt_key="rows_count",
        sha_key="canonical_sorted_sha256",
        max_cap=GLOBAL_MAX_DESCENDANTS_KH03_R3,
        top_sp2=f19.get("measured_science_speedup_w2_vs_w1"),
        top_sp4=f19.get("measured_science_speedup_w4_vs_w1"),
    )
    viols.extend(p_aud.get("violations") or [])
    viols.extend(s_aud.get("violations") or [])

    ok = len(viols) == 0
    return {
        "valid": ok,
        "classification": "STRICT_C2_CERTIFICATION_PASS" if ok else "STRICT_C2_CERTIFICATION_FAILED",
        "admitted_for_new_c2_certification": ok,
        "violations": viols,
        "recomputed_pytest": p_aud,
        "recomputed_science": s_aud,
    }


def recompute_kh03_r3_30_gates_and_verdicts(
    *,
    docs: Mapping[str, Mapping[str, Any]],
    mem_audit: Mapping[str, Any],
    forensic_ledger_audit: Mapping[str, Any],
    junit_bijection_ok: bool,
    verifier_violations_count: int = 0,
    for_final_verifier_manifest: bool = False,
    r3_f19_policy_audit: Optional[Mapping[str, Any]] = None,
    allow_legacy_descriptive_only: bool = False,
) -> Dict[str, Any]:
    """Independently recomputes ALL 30 orthogonal KH-03.R3 gates (G01..G30) and axis verdicts."""
    f00, f01, f02, f03, f04, f05 = docs["00"], docs["01"], docs["02"], docs["03"], docs["04"], docs["05"]
    f07, f08, f09, f10, f11, f12 = docs["07"], docs["08"], docs["09"], docs["10"], docs["11"], docs["12"]
    f13, f14, f15, f16, f17, f18 = docs["13"], docs["14"], docs["15"], docs["16"], docs["17"], docs["18"]
    f19, f20, f21, f22, f25 = docs["19"], docs["20"], docs["21"], docs["22"], docs["25"]

    gates: Dict[str, str] = {}

    # G01: Branch / clean SHA / fresh tested SHA
    tsha = str(f00.get("tested_executable_sha") or "")
    sha_ok = (
        f00.get("branch") == MANDATORY_BRANCH_KH03_R3
        and len(tsha) == 40
        and tsha == f00.get("start_head_sha")
        and tsha == f00.get("end_head_sha")
        and tsha not in (
            PRIOR_KH02_R1_TESTED_SHA_SHARED, PRIOR_KH02_R2_TESTED_SHA_SHARED,
            PRIOR_KH02_R2_C1_CODE_COMMIT_SHA_SHARED, PRIOR_KH02_R2_C2_TESTED_SHA_SHARED,
            PRIOR_KH02_R2_C3_TESTED_SHA, PRIOR_KH02_R2_C4_TESTED_SHA, PRIOR_KH02_R2_C4_PUB_HEAD_SHA,
            PRIOR_KH03_R1_TESTED_SHA, PRIOR_KH03_R1_PUB_HEAD_SHA,
            PRIOR_KH03_R2_TESTED_SHA, PRIOR_KH03_R2_PUB_HEAD_SHA,
        )
    )
    clean_ok = bool(f00.get("git_clean_at_start", False)) and not bool(f00.get("local_uncommitted_changes", False))
    gates["G01_BRANCH_CLEAN_EXECUTABLE_SHA"] = "PASS" if (sha_ok and clean_ok) else ("BLOCKED_UNCOMMITTED_LOCAL_RUN" if (sha_ok and not clean_ok) else "FAIL")

    # G02: Dataset & prior sealed immutability
    gates["G02_DATASET_AND_PRIOR_ARTIFACT_IMMUTABILITY"] = (
        "PASS"
        if (f02.get("status") == "PASS" and bool(f02.get("canonical_dataset_sha256_match")) and bool(f02.get("all_prior_sealed_artifacts_intact")))
        else "FAIL"
    )

    # G03: Production KEEP AS IS + AST parity
    gates["G03_PRODUCTION_KEEP_AS_IS_AST_PARITY"] = (
        "PASS"
        if (
            f01.get("status") == "PASS"
            and int(f01.get("locked_files_modified_count", 1)) == 0
            and len(f01.get("forbidden_order_import_hits") or []) == 0
            and bool(f01.get("cost_assumptions_defaults_intact"))
        )
        else "FAIL"
    )

    # G04 & G05: Legacy R1 RED repro & R2+ method guard
    gates["G04_LEGACY_R1_BAD_METHOD_RED_REPRO"] = (
        "PASS" if (f03.get("classification") == "REAL_RED" and f03.get("status") == "PASS") else "FAIL"
    )
    cg = f04.get("callgraph_audit") or {}
    gates["G05_R2_OR_NEWER_METHOD_GUARD"] = (
        "PASS"
        if (
            f04.get("status") == "PASS"
            and cg.get("LEGACY_METHOD_GUARD") == "PASS"
            and int(cg.get("unauthorized_r1_callers_count", 1)) == 0
            and bool(f04.get("all_synthetic_bypass_vectors_detected"))
        )
        else "FAIL"
    )

    # G06..G11: Forensic entry/exit diagnosis
    cc05 = f05.get("conservation_checks") or {}
    gates["G06_FULL_OPPORTUNITY_LINEAGE"] = (
        "PASS"
        if (
            f05.get("status") == "PASS"
            and all(bool(cc05.get(k)) for k in ("eligible_eq_scheduled_plus_busy_skipped", "scheduled_eq_terminal_outcome_partition", "zero_duplicate_opportunity_ids", "zero_1pos_overlap_violations"))
        )
        else "FAIL"
    )
    ov07 = f07.get("overall_pooled_null_1x") or {}
    gates["G07_WIN_LOSS_DENOMINATOR_TRUTH"] = (
        "PASS"
        if (
            f07.get("status") == "PASS"
            and int(ov07.get("closed_count", 0)) > 0
            and int(ov07.get("wins", -1)) + int(ov07.get("losses", -1)) == int(ov07.get("closed_count", -2))
            and forensic_ledger_audit.get("valid") is True
        )
        else "FAIL"
    )
    gates["G08_ENTRY_EXIT_ASOF_CAUSALITY"] = (
        "PASS"
        if (
            f10.get("status") == "PASS"
            and bool((f10.get("future_bar_mutation_invariance") or {}).get("passed"))
            and int((f10.get("future_bar_mutation_invariance") or {}).get("mismatches_count", 1)) == 0
            and bool((f10.get("synthetic_label_and_conservative_tie_controls") or {}).get("passed"))
        )
        else "FAIL"
    )
    gates["G09_FEE_AND_RISK_ACCOUNTING"] = (
        "PASS"
        if (
            forensic_ledger_audit.get("valid") is True
            and float(forensic_ledger_audit.get("max_residual_usd", 99.0)) <= 0.001
            and f09.get("status") == "PASS"
            and int(f09.get("zero_fee_display_bugs_count", 1)) == 0
            and f08.get("planned_cap_is_physical_guarantee") is False
        )
        else "FAIL"
    )
    gates["G10_FAILURE_ATTRIBUTION_DISCLOSURE"] = (
        "PASS"
        if (
            f08.get("status") == "PASS"
            and "ASSOCIATION_ONLY" in str(f08.get("attribution_epistemic_label") or "")
            and (f08.get("three_axis_diagnosis") or {}).get("ENGINE_AXIS", {}).get("accounting_or_simulator_bug_detected") is False
        )
        else "FAIL"
    )
    gates["G11_NEGATIVE_CONTROLS_AND_MATCHING"] = (
        "PASS"
        if (
            f10.get("status") == "PASS"
            and int((f10.get("direction_inverted_symmetric_sl_tp_control") or {}).get("inverted_closed", 0)) > 0
            and len(f16.get("matching_reports") or {}) >= 1
            and len(f16.get("negative_control_summaries_1x") or {}) == 3
        )
        else "FAIL"
    )

    # G12..G16: Pre-registration, conservation, R2 forward-only WF
    recomp_p11 = canonical_hash({k: v for k, v in f11.items() if k != "plan_frozen_sha256"})
    gates["G12_BOUNDED_PREREG_OR_DIAGNOSIS_ONLY"] = (
        "PASS"
        if (
            f11.get("status") == "PASS"
            and bool(f11.get("written_and_hashed_before_validation_replay"))
            and f11.get("plan_frozen_sha256") == recomp_p11
            and int((f11.get("finite_search_budget") or {}).get("declared_primary_arms_count", 99)) <= 3
            and f13.get("preregistered_plan_sha256") == recomp_p11
            and f16.get("preregistered_plan_sha256") == recomp_p11
        )
        else "FAIL"
    )
    gates["G13_CANDIDATE_FILTER_CONSERVATION"] = (
        "PASS"
        if (f12.get("status") == "PASS" and bool(f12.get("all_arms_conserved_exact")))
        else "FAIL"
    )
    wf_caus = validate_kh03_r2_fold_causality(list(f13.get("folds") or ()), min_embargo_minutes=MIN_EMBARGO_MINUTES_KH03_R2)
    gates["G14_FORWARD_ONLY_TRAIN_VAL_EMBARGO"] = (
        "PASS"
        if (
            f13.get("status") == "PASS"
            and f13.get("is_fresh_oos") is False
            and int(f13.get("OOS_EVALUATIONS", -1)) == 0
            and wf_caus["valid"] is True
        )
        else "FAIL"
    )
    replays_14 = list(f14.get("per_fold_selection_and_validation_replays") or ())
    sel_ok = len(replays_14) >= 2
    for fr in replays_14:
        sf = fr.get("fold_local_selection_freeze") or {}
        if sf.get("selection_freeze_sha256") != canonical_hash({k: v for k, v in sf.items() if k != "selection_freeze_sha256"}):
            sel_ok = False
        if not bool(sf.get("selection_frozen_before_validation_replay")):
            sel_ok = False
    gates["G15_FOLD_LOCAL_SELECTION_FROZEN"] = "PASS" if sel_ok else "FAIL"
    gates["G16_FRESH_FOLD_REPLAY_AND_LEAKAGE_TESTS"] = (
        "PASS"
        if (
            f14.get("status") == "PASS"
            and bool(f13.get("future_win_injection_invariance_passed"))
            and int(f14.get("total_overlap_violations", 1)) == 0
        )
        else "FAIL"
    )

    # G17..G19: Fill realism / H3 excluded, 6-cost stress, strict CI/Holm/power
    gates["G17_FILL_REALISM_H3_NOT_PROMOTED"] = (
        "PASS"
        if (
            f11.get("h3_r2_excluded_from_executable_arms") is True
            and f15.get("observed_l1_bbo_available") is False
            and f09.get("observed_l1_bbo_available") is False
            and f15.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") == "UNKNOWN_NOT_LOCALLY_VERIFIED"
            and KH03_R2_H3_ADVISORY_ARM not in (f16.get("primary_arm_evaluations") or {})
        )
        else "FAIL"
    )
    gates["G18_SIX_SCENARIO_NET_COST_STRESS"] = (
        "PASS"
        if (
            f15.get("status") == "PASS"
            and len(f15.get("dynamic_full_rerisk_six_scenarios") or {}) == 6
            and len(f15.get("fixed_fills_descriptive_repricing") or {}) >= 1
            and int(f15.get("zero_fee_display_bugs_count", 1)) == 0
            and int(f15.get("total_overlap_violations", 1)) == 0
        )
        else "FAIL"
    )
    ci_raw_ok, _ci_v, recomp_passing_arms = _recompute_r3_dev_gates_from_raw_16(f16, f13)
    gates["G19_STATISTICS_CI_HOLM_POWER"] = (
        "PASS"
        if (
            f16.get("status") == "PASS"
            and ci_raw_ok
            and int(f16.get("preregistered_hypothesis_budget_m", 0)) == 3
            and bool((f16.get("synthetic_positive_gate_capability_proof") or {}).get("passes_preregistered_dev_advancement"))
        )
        else "FAIL"
    )

    # G20: Baseline parity A1=56710 / SI8=6375 / 246 / D4=0
    p_st = str(f17.get("BASELINE_PARITY_VERDICT") or "FAIL")
    g20_ok = (
        p_st == "PASS"
        and bool((f17.get("per_decision_and_episode_fingerprints") or {}).get("all_sealed_fingerprints_exact_match"))
        and int(f17.get("a1_population_count", 0)) == 56710
        and int(f17.get("a1_mismatches_count", 1)) == 0
        and int(f17.get("si8_population_count", 0)) == 6375
        and int(f17.get("si8_mismatches_count", 1)) == 0
        and int(f17.get("three_adapter_population_count", 0)) == 246
        and int(f17.get("three_adapter_mismatches_count", 1)) == 0
        and int(f17.get("d4_historical_closed_trades", 1)) == 0
    )
    gates["G20_BASELINE_A1_SI8_ADAPTER_D4_PARITY"] = (
        "NOT_RUN_BLOCKED_LOW_DISK" if p_st == "NOT_RUN_BLOCKED_LOW_DISK" else ("PASS" if g20_ok else "FAIL")
    )

    # G23: Disk / RAM / Child process safety
    mp00 = f00.get("strict_multi_mount_probe") or {}
    waiver = (
        bool(f00.get("owner_disk_preflight_waiver", False))
        or bool(mp00.get("owner_waiver_permitted", False))
        or bool(f20.get("owner_disk_preflight_waiver", False))
        or bool(f25.get("owner_disk_preflight_waiver", False))
    )
    mounts = list(mp00.get("distinct_mounts") or ())
    probe_errs = list(mp00.get("probe_errors") or ())
    pre_disk_ok = (
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
        float(f20.get("min_available_ram_mib") or 0.0) >= HARD_RAM_FLOOR_MIB_KH03_R3
        and int(f20.get("max_descendant_children", 99)) <= GLOBAL_MAX_DESCENDANTS_KH03_R3
        and int(f20.get("orphan_children_after_cleanup", 99)) == 0
        and bool(f20.get("sentinel_temp_cleaned_up", False))
        and mem_audit.get("status") == "PASS"
        and int(mem_audit.get("below_floor_samples_count", 1)) == 0
        and int(mem_audit.get("max_workers_observed_in_samples", 99)) <= GLOBAL_MAX_DESCENDANTS_KH03_R3
    )
    if waiver or len(probe_errs) > 0 or not ram_pid_ok:
        gates["G23_DISK_RAM_CHILD_PROCESS_SAFETY"] = "FAIL"
    elif pre_disk_ok and rt_disk_ok:
        gates["G23_DISK_RAM_CHILD_PROCESS_SAFETY"] = "PASS"
    elif pre_disk_ok and not rt_disk_ok:
        gates["G23_DISK_RAM_CHILD_PROCESS_SAFETY"] = "FAIL"
    else:
        gates["G23_DISK_RAM_CHILD_PROCESS_SAFETY"] = "BLOCKED_LOW_DISK"

    # G21: Full pytest JUnit 1:1 bijection
    r18 = str(f18.get("status") or "FAIL")
    if r18 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G21_FULL_PYTEST_JUNIT_1_TO_1"] = "NOT_RUN_BLOCKED_LOW_DISK" if gates["G23_DISK_RAM_CHILD_PROCESS_SAFETY"] == "BLOCKED_LOW_DISK" else "FAIL"
    elif (
        r18 == "PASS"
        and gates["G23_DISK_RAM_CHILD_PROCESS_SAFETY"] == "PASS"
        and junit_bijection_ok
        and int(f18.get("collected_count", 0)) >= 5646
        and int(f18.get("collected_count", -1)) == int(f18.get("executed_count", -2))
        and int(f18.get("failed_count", 1)) == 0
        and int(f18.get("error_count", 1)) == 0
        and int(f18.get("mandatory_skipped_count", 1)) == 0
    ):
        gates["G21_FULL_PYTEST_JUNIT_1_TO_1"] = "PASS"
    else:
        gates["G21_FULL_PYTEST_JUNIT_1_TO_1"] = "FAIL"

    # G22: W1/W2/W4/W1_repeat determinism AND strict independent worker-policy certification (KH-03.R3.C2)
    r19 = str(f19.get("status") or "FAIL")
    if r19 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G22_W1_W2_W4_W1REPEAT_SEMANTIC_DETERMINISM"] = (
            "NOT_RUN_BLOCKED_LOW_DISK" if gates["G23_DISK_RAM_CHILD_PROCESS_SAFETY"] == "BLOCKED_LOW_DISK" else "FAIL"
        )
    else:
        pol_aud = (
            r3_f19_policy_audit
            if r3_f19_policy_audit is not None
            else recompute_r3_worker_policy_from_raw_for_verifier(
                f19,
                expected_run_id=str(f00.get("run_id") or ""),
                expected_tested_sha=str(f00.get("tested_executable_sha") or ""),
                allow_legacy_descriptive_only=allow_legacy_descriptive_only,
            )
        )
        g22_strict_ok = (
            r19 == "PASS"
            and gates["G23_DISK_RAM_CHILD_PROCESS_SAFETY"] == "PASS"
            and _recompute_w1_w2_w4_c4(f19)
            and bool(pol_aud.get("valid", False))
            and (
                allow_legacy_descriptive_only
                or (
                    f19.get("policy_certification_status") == "PASS"
                    and f19.get("can_certify") is True
                    and bool(pol_aud.get("admitted_for_new_c2_certification", False))
                )
            )
        )
        gates["G22_W1_W2_W4_W1REPEAT_SEMANTIC_DETERMINISM"] = "PASS" if g22_strict_ok else "FAIL"

    # G24 & G25: Faults >= 194 (174 inherited + >= 20 new R3) & Mutations >= 16 new R3
    gates["G24_UNIQUE_RED_GREEN_FAULTS"] = (
        "PASS"
        if (
            f21.get("status") == "PASS"
            and int(f21.get("inherited_r2_controls_count", 0)) >= 174
            and int(f21.get("new_kh03_r3_controls_count", 0)) >= 20
            and int(f21.get("total_controls", 0)) >= 194
            and int(f21.get("unique_fault_ids", 0)) == int(f21.get("total_controls", -1))
            and int(f21.get("failed_controls", 1)) == 0
        )
        else "FAIL"
    )
    g25_ok = (
        f22.get("status") == "PASS"
        and bool(f22.get("clean_clone_verifier_green"))
        and int(f22.get("mutations_tested_count", 0)) >= 16
        and int(f22.get("mutations_failed_detection_count", 1)) == 0
    )
    gates["G25_INDEPENDENT_VERIFIER_MUTATION_DEFENSE"] = "PASS" if g25_ok else "FAIL"
    gates["G26_OS_VERIFIER_MANIFEST_OWN_RECOMPUTATION"] = (
        ("PASS" if (verifier_violations_count == 0 and g25_ok) else "FAIL")
        if for_final_verifier_manifest
        else "PENDING_SEPARATE_OS_VERIFIER"
    )

    # G27: Economic truth (orthogonal verdict — NOT collapsed into generic PASS)
    expected_dev_v = "DEV_CANDIDATE_UNPROVEN_OOS" if recomp_passing_arms else "NO_ECONOMIC_ADVANCEMENT_CANDIDATE"
    g27_ok = (
        ci_raw_ok
        and f16.get("RESEARCH_ECONOMIC_VERDICT") == "NO_PROVEN_EDGE"
        and f16.get("DEV_RESEARCH_CANDIDATE") == expected_dev_v
        and list(f16.get("passing_dev_candidate_arms") or []) == recomp_passing_arms
    )
    gates["G27_RESEARCH_ECONOMIC_VERDICT_HONESTY"] = expected_dev_v if g27_ok else "FAIL"

    # G28: Fresh OOS == 0 & live network OFF
    prov = f02.get("c4_provenance_and_oos_boundary") or {}
    oos_ok = (
        int(f16.get("OOS_EVALUATIONS", -1)) == 0
        and int(f13.get("OOS_EVALUATIONS", -1)) == 0
        and f16.get("FRESH_OOS") == "BLOCKED_NO_UNTOUCHED_DATA"
        and prov.get("network_capture_enabled") is False
        and prov.get("live_network_capture_executed") is False
        and int(prov.get("real_oos_escrow_final_record_count", -1)) == 0
    )
    gates["G28_FRESH_OOS_ZERO_AND_EXCHANGE_OFF"] = "BLOCKED_NO_UNTOUCHED_DATA" if oos_ok else "FAIL"

    # G29: Activation ALWAYS OFF & KH04 gated
    modes_ok = all(
        d.get("ACTIVATION_READY") == "NO"
        and all((d.get("runtime_safety_modes") or {}).get(k) == "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO"))
        for d in (f00, f01, f11, f16)
    )
    kh04_v = "BLOCKED_NO_ELIGIBLE_PROSPECTIVE_DATA" if (modes_ok and oos_ok and recomp_passing_arms) else ("BLOCKED_NO_DEV_CANDIDATE" if (modes_ok and oos_ok) else "FAIL")
    gates["G29_ACTIVATION_ALWAYS_OFF_AND_KH04_GATED"] = kh04_v if (modes_ok and oos_ok) else "FAIL"

    # G30: Publication status
    pub_st = str(f25.get("publication_status") or "NOT_PUBLISHED_BLOCKED_LOW_DISK")
    if waiver or "WAIVER" in pub_st:
        gates["G30_CODE_AND_EVIDENCE_REMOTE_READBACK"] = "FAIL"
    elif pub_st == "PASS_REMOTE_VERIFIED" and bool(f25.get("remote_readback_sha256_verified")):
        gates["G30_CODE_AND_EVIDENCE_REMOTE_READBACK"] = "PASS_REMOTE_VERIFIED"
    elif pub_st == "PRE_PUSH_PENDING":
        gates["G30_CODE_AND_EVIDENCE_REMOTE_READBACK"] = "PRE_PUSH_PENDING"
    elif "BLOCKED" in pub_st:
        gates["G30_CODE_AND_EVIDENCE_REMOTE_READBACK"] = pub_st
    else:
        gates["G30_CODE_AND_EVIDENCE_REMOTE_READBACK"] = "FAIL"

    eng_gate_ids = [f"G{i:02d}_" for i in range(1, 26)]
    eng_keys = [k for k in GATE_30_KEYS_KH03_R3 if any(k.startswith(p) for p in eng_gate_ids)]
    any_fail = any(gates[k] == "FAIL" for k in gates) or (for_final_verifier_manifest and verifier_violations_count > 0)
    any_low_disk = any("LOW_DISK" in gates[k] for k in eng_keys)
    eng_pass = all(gates[k] == "PASS" for k in eng_keys) and (
        (not for_final_verifier_manifest) or gates["G26_OS_VERIFIER_MANIFEST_OWN_RECOMPUTATION"] == "PASS"
    )

    if any_fail:
        eng_v = "FAIL"
    elif any_low_disk:
        eng_v = "BLOCKED_RESOURCE"
    elif eng_pass:
        eng_v = "PASS"
    else:
        eng_v = "BLOCKED_INTEGRITY"

    if eng_v == "PASS" and g27_ok and oos_ok and modes_ok:
        overall = "PASS_KH03_R3_ENGINEERING_DIAGNOSIS_NO_PROVEN_EDGE_OOS_BLOCKED"
    elif eng_v == "BLOCKED_RESOURCE":
        overall = "BLOCKED_LOW_DISK"
    else:
        overall = f"FAIL_OR_BLOCKED_{eng_v}"

    verdicts = {
        "KH03_R3_ENGINEERING": eng_v,
        "RESEARCH_METHOD_VALIDITY": (
            "PASS"
            if all(gates[k] == "PASS" for k in ("G04_LEGACY_R1_BAD_METHOD_RED_REPRO", "G05_R2_OR_NEWER_METHOD_GUARD", "G12_BOUNDED_PREREG_OR_DIAGNOSIS_ONLY", "G14_FORWARD_ONLY_TRAIN_VAL_EMBARGO", "G15_FOLD_LOCAL_SELECTION_FROZEN", "G16_FRESH_FOLD_REPLAY_AND_LEAKAGE_TESTS", "G17_FILL_REALISM_H3_NOT_PROMOTED", "G19_STATISTICS_CI_HOLM_POWER"))
            else "FAIL"
        ),
        "ENTRY_EXIT_DIAGNOSTIC_VALIDITY": (
            "PASS"
            if all(gates[k] == "PASS" for k in ("G06_FULL_OPPORTUNITY_LINEAGE", "G07_WIN_LOSS_DENOMINATOR_TRUTH", "G08_ENTRY_EXIT_ASOF_CAUSALITY", "G09_FEE_AND_RISK_ACCOUNTING", "G10_FAILURE_ATTRIBUTION_DISCLOSURE", "G11_NEGATIVE_CONTROLS_AND_MATCHING"))
            else "FAIL"
        ),
        "DEV_RESEARCH_CANDIDATE": gates["G27_RESEARCH_ECONOMIC_VERDICT_HONESTY"],
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE" if g27_ok else "FAIL",
        "FRESH_OOS": gates["G28_FRESH_OOS_ZERO_AND_EXCHANGE_OFF"],
        "KH04_ENTRY": gates["G29_ACTIVATION_ALWAYS_OFF_AND_KH04_GATED"],
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": str(f15.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") or "FAIL"),
        "LIVE_NETWORK_VALIDATION": str(prov.get("LIVE_NETWORK_VALIDATION") or "FAIL"),
        "PUBLICATION": gates["G30_CODE_AND_EVIDENCE_REMOTE_READBACK"],
        "ACTIVATION_READY": "NO" if modes_ok else "FAIL",
        "OOS_EVALUATIONS": 0 if oos_ok else -1,
    }
    return {"overall_classification": overall, "gates_30": gates, "verdicts": verdicts}


def verify_kh03_r3_artifacts_independently_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_live_repo_pointers: bool = True,
    enforce_distinct_pid: bool = True,
    write_manifest: bool = True,
    allow_legacy_descriptive_only: bool = False,
) -> Dict[str, Any]:
    """Separate-OS-process disk verifier recomputing all KH-03.R3 invariants & G01..G30."""
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {"spec_version": SPEC_VERSION_KH03_R3, "independent_verifier_status": "FAIL", "verifier_pid": verifier_pid, "exit_code_recommended": 1, "violations": [f"RUN_DIR_MISSING:{run_dir}"]}

    for rel in REQUIRED_ARTIFACTS_KH03_R3:
        fp = run_dir / rel
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_OR_EMPTY_ARTIFACT:{rel}")
    if violations:
        return {"spec_version": SPEC_VERSION_KH03_R3, "independent_verifier_status": "FAIL", "verifier_pid": verifier_pid, "exit_code_recommended": 1, "violations": violations}

    def _rd(fn: str) -> Dict[str, Any]:
        return _strict_json_loads_r3((run_dir / fn).read_text("utf-8"))

    try:
        docs: Dict[str, Dict[str, Any]] = {
            "00": _rd("00_source_branch_clean_sha_disk_ram_preflight.json"),
            "01": _rd("01_ast_ownership_legacy_guard_and_keep_as_is_delta.json"),
            "02": _rd("02_prior_sealed_sha_and_canonical_dataset_integrity.json"),
            "03": _rd("03_r1_legacy_false_pass_red_reproductions.json"),
            "04": _rd("04_r2_method_guard_green_and_entrypoint_scans.json"),
            "05": _rd("05_full_opportunity_lineage_and_hash_manifest.json"),
            "07": _rd("07_win_loss_breakdown_denominator_ci_coverage.json"),
            "08": _rd("08_stop_target_gap_tie_holding_risk_tail_attribution.json"),
            "09": _rd("09_six_cost_scenarios_and_breakeven_accounting.json"),
            "10": _rd("10_negative_controls_matching_and_causal_disclaimers.json"),
            "11": _rd("11_training_only_bounded_prereg_plan.json"),
            "12": _rd("12_candidate_asof_features_and_exact_conservation.json"),
            "13": _rd("13_r2_forward_only_fold_boundaries_freeze_no_peek.json"),
            "14": _rd("14_validation_fold_independent_results_by_arm.json"),
            "15": _rd("15_fixed_fill_vs_dynamic_rerisk_and_expense_truth.json"),
            "16": _rd("16_strict_dev_gate_power_holm_cluster_bootstrap.json"),
            "17": _rd("17_baseline_parity_A1_SI8_adapter_D4.json"),
            "18": _rd("18_full_pytest_junit_collected_executed_bijection.json"),
            "19": _rd("19_w1_w2_w4_w1repeat_hashes_timings_speedups.json"),
            "20": _rd("20_disk_ram_pid_samples_and_cleanup.json"),
            "21": _rd("21_inherited_plus_new_unique_red_green_fault_receipts.json"),
            "22": _rd("22_independent_verifier_new_mutation_resistance.json"),
            "23": _rd("23_orthogonal_gate_matrix_economic_kh04_oos_activation.json"),
            "25": _rd("25_phase1_code_commit_push_readback.json"),
        }
        readme_txt = (run_dir / "README_KH03_R3.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH03_R3_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {"spec_version": SPEC_VERSION_KH03_R3, "independent_verifier_status": "FAIL", "verifier_pid": verifier_pid, "exit_code_recommended": 1, "violations": [f"ARTIFACT_PARSE_ERROR:{exc}"]}

    f00, f02, f04, f07, f09, f11, f13, f14, f15, f16, f18, f19, f23, f25 = (
        docs["00"], docs["02"], docs["04"], docs["07"], docs["09"], docs["11"],
        docs["13"], docs["14"], docs["15"], docs["16"], docs["18"], docs["19"], docs["23"], docs["25"],
    )

    orch_pid = int(f00.get("orchestrator_pid") or -1)
    if enforce_distinct_pid and verifier_pid == orch_pid:
        violations.append(f"VERIFIER_SAME_OS_PID_AS_ORCHESTRATOR:{verifier_pid}")

    # 1. Branch, SHA, run_id binding
    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    if f00.get("branch") != MANDATORY_BRANCH_KH03_R3:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if tested_sha != f00.get("tested_executable_sha") or tested_sha != f23.get("tested_executable_sha") or tested_sha != f25.get("tested_executable_sha"):
        violations.append("TESTED_SHA_MISMATCH_ACROSS_00_23_25")
    if len(tested_sha) >= 12 and not run_id.endswith(f"_{tested_sha[:12]}"):
        violations.append(f"RUN_ID_MISSING_TESTED_SHA12_SUFFIX:{run_id}")

    # 2. Prior R2 + R1 + C4 + Canonical dataset immutability
    if check_live_repo_pointers:
        for rel_p, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3:
            p = REPO_ROOT / rel_p
            if not p.is_file() or _file_sha256(p) != exp_sha:
                violations.append(f"PRIOR_SEALED_ARTIFACT_MUTATED:{lbl}")
    else:
        pm = f02.get("prior_sealed_artifacts_immutability") or {}
        if any(not bool(v.get("match", False)) for v in pm.values() if isinstance(v, Mapping)):
            violations.append("PRIOR_SEALED_ARTIFACT_MUTATED")
    if f02.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH03_R3 or not bool(f02.get("canonical_dataset_sha256_match", False)):
        violations.append("CANONICAL_DATASET_SHA256_TAMPERED")

    # 3. Legacy R1 guard & output provenance
    if int((f04.get("callgraph_audit") or {}).get("unauthorized_r1_callers_count", 1)) != 0:
        violations.append("UNAUTHORIZED_R1_CALLER_DETECTED")
    prov16 = validate_r3_research_output_provenance(f16)
    if not prov16["valid"]:
        violations.extend([f"PROVENANCE_16:{v}" for v in prov16["violations"]])

    # 4. Streaming Decimal forensic ledger 06 vs 07
    forensic_audit = validate_r3_forensic_ledger_independently(
        run_dir / "06_per_trade_entry_exit_fee_forensic_ledger.jsonl.gz", f07
    )
    if not forensic_audit.get("valid", False):
        violations.extend([f"FORENSIC_06:{v}" for v in (forensic_audit.get("violations") or ["FAILED"])])

    # 5. Pre-registration SHA + forward-only WF causality + selection freeze
    recomp_p11 = canonical_hash({k: v for k, v in f11.items() if k != "plan_frozen_sha256"})
    if f11.get("plan_frozen_sha256") != recomp_p11 or f13.get("preregistered_plan_sha256") != recomp_p11 or f16.get("preregistered_plan_sha256") != recomp_p11:
        violations.append("PREREG_PLAN_SHA256_TAMPERED_OR_UNLINKED")

    wf_caus = validate_kh03_r2_fold_causality(list(f13.get("folds") or ()), min_embargo_minutes=MIN_EMBARGO_MINUTES_KH03_R2)
    if not wf_caus["valid"]:
        violations.extend([f"WF_CAUSALITY:{v}" for v in wf_caus["violations"]])
    for fr in (f14.get("per_fold_selection_and_validation_replays") or ()):
        sf = fr.get("fold_local_selection_freeze") or {}
        if sf.get("selection_freeze_sha256") != canonical_hash({k: v for k, v in sf.items() if k != "selection_freeze_sha256"}):
            violations.append(f"WF_SELECTION_FREEZE_SHA_TAMPERED:{fr.get('fold_id')}")

    # 6. Raw CI + DEV gate recomputation from 16
    ci_ok, ci_viols, _ = _recompute_r3_dev_gates_from_raw_16(f16, f13)
    if not ci_ok:
        violations.extend(ci_viols)

    # 7. Six-scenario fee reconciliation in 09 & 15 + observed L1 honesty
    if f15.get("observed_l1_bbo_available") is not False or f09.get("observed_l1_bbo_available") is not False:
        violations.append("FORGED_OBSERVED_L1_BBO_AVAILABLE_TRUE")
    for sc_id, sc_obj in (f15.get("dynamic_full_rerisk_six_scenarios") or {}).items():
        for ac, rs in (sc_obj.get("arms") or {}).items():
            if int(rs.get("closed") or 0) > 0:
                g = float(rs.get("gross_pnl_usd") or 0.0)
                f_u = float(rs.get("total_fees_usd") or 0.0)
                n_u = float(rs.get("net_pnl_usd") or 0.0)
                if f_u <= 0.0 and (g - n_u) > 0.01:
                    violations.append(f"ZERO_FEE_BUG_IN_15:{sc_id}:{ac}")
                if abs((g - f_u) - n_u) > 0.01:
                    violations.append(f"FEE_RECONCILIATION_DRIFT_IN_15:{sc_id}:{ac}")

    # 8. OOS / Activation / Economic / Network honesty
    if int(f16.get("OOS_EVALUATIONS", -1)) != 0 or int(f13.get("OOS_EVALUATIONS", -1)) != 0:
        violations.append("NONZERO_OOS_EVALUATIONS_PEEK")
    if f16.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE":
        violations.append(f"FORGED_RESEARCH_ECONOMIC_VERDICT:{f16.get('RESEARCH_ECONOMIC_VERDICT')}")
    if f16.get("ACTIVATION_READY") != "NO" or f00.get("ACTIVATION_READY") != "NO":
        violations.append("FORGED_ACTIVATION_READY_ON")
    if docs["08"].get("planned_cap_is_physical_guarantee") is not False:
        violations.append("FORGED_RISK_CAP_PHYSICAL_GUARANTEE")

    # 9. Raw JUnit XML 1:1 bijection
    junit_ok = False
    if f18.get("status") == "PASS":
        junit_ok, j_errs = _verify_junit_raw_xml_bijection_c4(
            run_dir, f18, expected_run_id=run_id, expected_tested_sha=tested_sha
        )
        if not junit_ok:
            violations.extend(j_errs)

    # 10. W1/W2/W4/W1_repeat determinism AND strict independent worker-policy certification (KH-03.R3.C2)
    r3_f19_policy_audit = recompute_r3_worker_policy_from_raw_for_verifier(
        f19,
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        allow_legacy_descriptive_only=allow_legacy_descriptive_only,
    )
    if f19.get("status") != "NOT_RUN_BLOCKED_LOW_DISK":
        if f19.get("status") == "PASS" and not _recompute_w1_w2_w4_c4(f19):
            violations.append("W1_W2_W4_DETERMINISM_HASH_MISMATCH")
        if not r3_f19_policy_audit.get("valid", False):
            violations.extend(r3_f19_policy_audit.get("violations") or ["R3_F19_POLICY_VERIFICATION_FAILED"])

    # 11. Memory/disk/PID telemetry directly from gzip log
    mem_log = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    if not mem_log.is_file() or mem_log.stat().st_size == 0:
        mem_audit: Dict[str, Any] = {"status": "FAIL_MISSING_LOG", "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99}
        violations.append("MISSING_MEMORY_TELEMETRY_LOG")
    else:
        mem_audit = _audit_memory_samples_c3(mem_log)
        if mem_audit.get("status") != "PASS":
            violations.append(f"MEMORY_TELEMETRY_BREACHED_512MIB_FLOOR:{mem_audit.get('status')}")
        if int(mem_audit.get("max_workers_observed_in_samples", 99)) > GLOBAL_MAX_DESCENDANTS_KH03_R3:
            violations.append(f"MEMORY_TELEMETRY_EXCEEDED_CHILD_CAP:{mem_audit.get('max_workers_observed_in_samples')}")

    # 12. Zero disk waiver & remote readback honesty
    if f25.get("publication_status") == "PASS_REMOTE_VERIFIED" and not bool(f25.get("remote_readback_sha256_verified")):
        violations.append("FALSE_REMOTE_READBACK_CLAIM")
    if bool(f00.get("owner_disk_preflight_waiver", False)):
        violations.append("UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN")

    # 13. Recompute 30 gates & verify against 23_orthogonal_gate_matrix_economic_kh04_oos_activation.json
    recomp_pre = recompute_kh03_r3_30_gates_and_verdicts(
        docs=docs, mem_audit=mem_audit, forensic_ledger_audit=forensic_audit,
        junit_bijection_ok=junit_ok, verifier_violations_count=len(violations), for_final_verifier_manifest=False,
        r3_f19_policy_audit=r3_f19_policy_audit, allow_legacy_descriptive_only=allow_legacy_descriptive_only,
    )
    stored_gates = f23.get("gates_30") or {}
    for gk, gv in recomp_pre["gates_30"].items():
        if stored_gates.get(gk) != gv:
            violations.append(f"GATE_TAMPER_OR_DRIFT:{gk}:stored={stored_gates.get(gk)}!=recomputed={gv}")

    # Cross-view consistency on README & HANDOVER
    for tok, err_code in (
        (run_id, "CROSS_VIEW_RUN_ID_MISSING"),
        ("ACTIVATION_READY=NO", "CROSS_VIEW_ACTIVATION_LOCK_MISSING"),
        ("NO_PROVEN_EDGE", "CROSS_VIEW_NO_PROVEN_EDGE_MISSING"),
        ("OOS_EVALUATIONS=0", "CROSS_VIEW_OOS_ZERO_MISSING"),
    ):
        if tok not in readme_txt or tok not in handover_txt:
            violations.append(err_code)

    recomp_final = recompute_kh03_r3_30_gates_and_verdicts(
        docs=docs, mem_audit=mem_audit, forensic_ledger_audit=forensic_audit,
        junit_bijection_ok=junit_ok, verifier_violations_count=len(violations), for_final_verifier_manifest=True,
        r3_f19_policy_audit=r3_f19_policy_audit, allow_legacy_descriptive_only=allow_legacy_descriptive_only,
    )

    artifact_sha256_map: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(run_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(run_dir).as_posix()
        if rel == MANIFEST_24_NAME_KH03_R3 or rel.endswith(".tmp"):
            continue
        artifact_sha256_map[rel] = {"size_bytes": int(fp.stat().st_size), "sha256": _file_sha256(fp)}

    eng_final = recomp_final["verdicts"]["KH03_R3_ENGINEERING"]
    if len(violations) > 0 or eng_final == "FAIL":
        verifier_status, exit_code = "FAIL", 1
    elif eng_final == "PASS":
        verifier_status, exit_code = "PASS", 0
    else:
        verifier_status, exit_code = "PASS", 2

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R3,
        "schema_version": SCHEMA_VERSION_KH03_R3,
        "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "orchestrator_pid": orch_pid,
        "verifier_pid": verifier_pid,
        "distinct_os_process_verified": (verifier_pid != orch_pid),
        "independent_verifier_status": verifier_status,
        "exit_code_recommended": exit_code,
        "overall_classification": recomp_final["overall_classification"],
        "recomputed_30_gates": recomp_final["gates_30"],
        "recomputed_verdicts": recomp_final["verdicts"],
        "independent_forensic_ledger_audit": forensic_audit,
        "independent_wf_causality_audit": wf_caus,
        "independent_memory_telemetry_audit": mem_audit,
        "independent_r3_f19_policy_audit": r3_f19_policy_audit,
        "r3_certification_mode": r3_f19_policy_audit.get("classification"),
        "admitted_for_new_c2_certification": bool(r3_f19_policy_audit.get("admitted_for_new_c2_certification", False)),
        "independent_junit_bijection_ok": junit_ok,
        "violations_count": len(violations),
        "violations": violations,
        "artifact_count_hashed": len(artifact_sha256_map),
        "manifest_content_sha256": canonical_hash(artifact_sha256_map),
        "artifact_sha256_manifest": artifact_sha256_map,
    }
    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_24_NAME_KH03_R3, payload)
    return payload


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK KH-03.R3 Separate-OS Independent Verifier")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--skip-live-repo-pointers", action="store_true")
    ap.add_argument("--allow-same-pid", action="store_true")
    ap.add_argument("--no-write-manifest", action="store_true")
    ap.add_argument("--legacy-descriptive-audit-only", action="store_true")
    args = ap.parse_args(argv)

    res = verify_kh03_r3_artifacts_independently_from_disk(
        Path(args.run_dir),
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        check_live_repo_pointers=(not args.skip_live_repo_pointers),
        enforce_distinct_pid=(not args.allow_same_pid),
        write_manifest=(not args.no_write_manifest),
        allow_legacy_descriptive_only=bool(args.legacy_descriptive_audit_only),
    )
    print(
        json.dumps(
            {
                "independent_verifier_status": res["independent_verifier_status"],
                "verifier_pid": res["verifier_pid"],
                "distinct_os_process_verified": res.get("distinct_os_process_verified"),
                "overall_classification": res.get("overall_classification"),
                "recomputed_verdicts": res.get("recomputed_verdicts"),
                "recomputed_30_gates": res.get("recomputed_30_gates"),
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
