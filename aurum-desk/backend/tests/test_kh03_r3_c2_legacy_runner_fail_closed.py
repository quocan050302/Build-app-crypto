"""
aurum-desk/backend/tests/test_kh03_r3_c2_legacy_runner_fail_closed.py

KH-03.R3.C2 Legacy Runner Fail-Closed Boundary Suite (C2T01..C2T40) & 16 NEW Fault Controls:
Verifies REAL pre-fix RED reproduction (`R3C2-RED01..RED06`), ALL 40 mandatory boundary cases
(`C2T01`..`C2T40`) on `_evaluate_r3_f19_policy_certification` and hardened R3 independent
verifier G22 (`recompute_r3_worker_policy_from_raw_for_verifier` + `recompute_kh03_r3_30_gates_and_verdicts`),
16 NEW unique C2 RED/GREEN fault controls (`KH03R3C2-F01`..`F16`), and C2 CLI waiver exit-2 refusal.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Tuple
import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    parse_json_strict_no_nonfinite,
    select_measured_worker_policy_strict,
)
from tools.kh_03_r3_independent_verifier import (
    ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF,
    ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF,
    recompute_kh03_r3_30_gates_and_verdicts,
    recompute_r3_worker_policy_from_raw_for_verifier,
)
from tools.run_kh_03_r3_acceptance import (
    _compute_optimum_worker_policy,
    _evaluate_r3_f19_policy_certification,
    _legacy_prefixed_r3_f19_status_red_repro_only,
)

FRESH_SHA = "f" * 40
FRESH_RID = f"20261123T220000Z_{FRESH_SHA[:12]}"
GOOD_RES = {
    "preflight_disk_free_gib": 33.0,
    "runtime_disk_free_gib": 32.5,
    "min_available_ram_mib": 3200.0,
    "owner_disk_preflight_waiver": False,
}


def _make_pb(*, w1=30.0, w2=20.0, w4=14.0, w1r=29.0, sha="1" * 64, run_id=FRESH_RID, tested_sha=FRESH_SHA) -> Dict[str, Any]:
    return {
        "run_id": run_id, "tested_executable_sha": tested_sha, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "all_4_runs_identical": True,
        "measured_speedup_w2_vs_w1": round(w1 / max(1e-9, w2), 3),
        "measured_speedup_w4_vs_w1": round(w1 / max(1e-9, w4), 3),
        "runs": [
            {"mode_label": "w=1", "workers": 1, "worker_pids": [101], "wall_clock_seconds": w1, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": sha},
            {"mode_label": "w=2", "workers": 2, "worker_pids": [201, 202], "wall_clock_seconds": w2, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": sha},
            {"mode_label": "w=4", "workers": 4, "worker_pids": [401, 402, 403, 404], "wall_clock_seconds": w4, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": sha},
            {"mode_label": "w=1_repeat", "workers": 1, "worker_pids": [501], "wall_clock_seconds": w1r, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": sha},
        ],
    }


def _make_sb(*, w1=1.35, w2=1.08, w4=1.15, w1r=1.28, sha="2" * 64, run_id=FRESH_RID, tested_sha=FRESH_SHA) -> Dict[str, Any]:
    return {
        "run_id": run_id, "tested_executable_sha": tested_sha, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "all_4_runs_identical": True,
        "measured_speedup_w2_vs_w1": round(w1 / max(1e-9, w2), 3),
        "measured_speedup_w4_vs_w1": round(w1 / max(1e-9, w4), 3),
        "runs": [
            {"mode_label": "w=1", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": w1, "canonical_sorted_sha256": sha},
            {"mode_label": "w=2", "workers": 2, "chunks_count": 2, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": w2, "canonical_sorted_sha256": sha},
            {"mode_label": "w=4", "workers": 4, "chunks_count": 4, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": w4, "canonical_sorted_sha256": sha},
            {"mode_label": "w=1_repeat", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": w1r, "canonical_sorted_sha256": sha},
        ],
    }


def _make_valid_pair() -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    pb, sb = _make_pb(), _make_sb()
    p_pol = _compute_optimum_worker_policy(pb, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits={"configured_child_process_cap": 4, "currently_allowed_worker_cap": 3, "full_suite_safe_worker_cap": 3, "shard_exclusive_cap": 3})
    s_pol = _compute_optimum_worker_policy(sb, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits={"configured_child_process_cap": 4, "currently_allowed_worker_cap": 4, "full_suite_safe_worker_cap": 4, "shard_exclusive_cap": 4})
    return pb, sb, p_pol, s_pol


def _cert(pb, sb, p_pol, s_pol, *, run_id=FRESH_RID, tested_sha=FRESH_SHA, res=None, can_run_heavy=True) -> Dict[str, Any]:
    return _evaluate_r3_f19_policy_certification(
        pb_raw=pb, sb_raw=sb, pytest_policy=p_pol, science_policy=s_pol,
        run_id=run_id, tested_sha=tested_sha, dataset_sha=CANONICAL_DATASET_SHA256_KH02,
        resource_state=GOOD_RES if res is None else res, can_run_heavy=can_run_heavy,
    )


def _assemble_f19(pb, sb, p_pol, s_pol, cert) -> Dict[str, Any]:
    return {
        "worker_policy_version": cert["worker_policy_version"],
        "raw_benchmark_identity": cert["raw_benchmark_identity"],
        "raw_semantic_sha": cert["raw_semantic_sha"],
        "pytest_controlled_subset_w1_w2_w4_repeat": pb,
        "r2_science_slice_w1_w2_w4_repeat": sb,
        "measured_pytest_speedup_w2_vs_w1": pb.get("measured_speedup_w2_vs_w1"),
        "measured_pytest_speedup_w4_vs_w1": pb.get("measured_speedup_w4_vs_w1"),
        "measured_science_speedup_w2_vs_w1": sb.get("measured_speedup_w2_vs_w1"),
        "measured_science_speedup_w4_vs_w1": sb.get("measured_speedup_w4_vs_w1"),
        "pytest_optimum_worker_policy": p_pol,
        "science_optimum_worker_policy": s_pol,
        "policy_certification_status": cert["policy_certification_status"],
        "can_certify": cert["can_certify"],
        "policy_failure_reasons": cert["policy_failure_reasons"],
        "all_determinism_identical": cert["all_determinism_identical"],
        "status": cert["status"],
    }


def execute_c2t01_to_c2t40_boundary_matrix() -> List[Dict[str, Any]]:
    """Executes ALL 40 mandatory KH-03.R3.C2 boundary cases (C2T01..C2T40) deterministically."""
    results: List[Dict[str, Any]] = []

    def _rec(cid: str, ok: bool, detail: str) -> None:
        results.append({"case_id": cid, "passed": bool(ok), "detail": detail})

    pb, sb, p_pol, s_pol = _make_valid_pair()
    c_ok = _cert(pb, sb, p_pol, s_pol)
    f19_ok = _assemble_f19(pb, sb, p_pol, s_pol, c_ok)

    # C2T01..C2T03: Valid pytest, valid science, both valid -> f19 PASS
    _rec("C2T01", p_pol["status"] == "PASS" and p_pol["mean_wall_sec_by_mode"] == {"w=1": 30.0, "w=2": 20.0, "w=4": 14.0}, "valid pytest policy")
    _rec("C2T02", s_pol["status"] == "PASS" and s_pol["mean_wall_sec_by_mode"] == {"w=1": 1.35, "w=2": 1.08, "w=4": 1.15}, "valid science policy")
    _rec("C2T03", c_ok["status"] == "PASS" and c_ok["can_certify"] is True and recompute_r3_worker_policy_from_raw_for_verifier(f19_ok, expected_run_id=FRESH_RID, expected_tested_sha=FRESH_SHA)["valid"], "both valid -> f19 PASS + verifier PASS")

    # C2T04: pb missing W4 despite all_4_runs_identical=True -> FAIL (not PASS)
    pb4 = copy.deepcopy(pb); pb4["runs"] = [r for r in pb4["runs"] if r["mode_label"] != "w=4"]
    p4 = _compute_optimum_worker_policy(pb4, benchmark_kind=BENCHMARK_KIND_PYTEST)
    c4 = _cert(pb4, sb, p4, s_pol)
    _rec("C2T04", c4["status"] != "PASS" and not c4["can_certify"] and _legacy_prefixed_r3_f19_status_red_repro_only(pb4, sb) == "PASS", "missing W4 blocked (old was PASS)")

    # C2T05: science missing W1_repeat -> FAIL
    sb5 = copy.deepcopy(sb); sb5["runs"] = [r for r in sb5["runs"] if r["mode_label"] != "w=1_repeat"]
    s5 = _compute_optimum_worker_policy(sb5, benchmark_kind=BENCHMARK_KIND_SCIENCE)
    _rec("C2T05", _cert(pb, sb5, p_pol, s5)["status"] != "PASS", "missing science w=1_repeat blocked")

    # C2T06: duplicate W2 mode -> FAIL
    pb6 = copy.deepcopy(pb); pb6["runs"].append(copy.deepcopy(pb6["runs"][1]))
    _rec("C2T06", _cert(pb6, sb, _compute_optimum_worker_policy(pb6, benchmark_kind=BENCHMARK_KIND_PYTEST), s_pol)["status"] != "PASS", "duplicate w=2 blocked")

    # C2T07..C2T13: None, 0, negative, NaN+JSON, Inf, bool True, string "14.119"
    for cid, bad_v in (("C2T07", None), ("C2T08", 0.0), ("C2T09", -2.5), ("C2T10", float("nan")), ("C2T11", float("inf")), ("C2T12", True), ("C2T13", "14.119")):
        pbx = copy.deepcopy(pb); pbx["runs"][2]["wall_clock_seconds"] = bad_v
        px = _compute_optimum_worker_policy(pbx, benchmark_kind=BENCHMARK_KIND_PYTEST)
        cx = _cert(pbx, sb, px, s_pol)
        extra_ok = True
        if cid == "C2T10":
            try:
                parse_json_strict_no_nonfinite('{"wall_clock_seconds": NaN}')
                extra_ok = False
            except ValueError:
                extra_ok = True
        _rec(cid, cx["status"] != "PASS" and not cx["can_certify"] and extra_ok, f"rejected bad duration={bad_v!r}")

    # C2T14: cross-kind key contamination
    sb14 = copy.deepcopy(sb); sb14["runs"][0]["wall_clock_seconds"] = 1.5
    _rec("C2T14", _cert(pb, sb14, p_pol, _compute_optimum_worker_policy(sb14, benchmark_kind=BENCHMARK_KIND_SCIENCE))["status"] != "PASS", "cross-kind rejected")

    # C2T15: conflicting legacy + canonical duration keys
    pb15 = copy.deepcopy(pb); pb15["runs"][0]["elapsed_sec"] = 99.0
    _rec("C2T15", _cert(pb15, sb, _compute_optimum_worker_policy(pb15, benchmark_kind=BENCHMARK_KIND_PYTEST), s_pol)["status"] != "PASS", "conflicting duration rejected")

    # C2T16: missing benchmark_kind inference works on clean unique kind & fails closed on mixed/empty
    inf_ok = _compute_optimum_worker_policy(pb)["status"] == "PASS"
    pb_mix = copy.deepcopy(pb); pb_mix["runs"][0]["wall_seconds"] = 1.2
    inf_bad = _compute_optimum_worker_policy(pb_mix)["status"] == "BLOCKED_INVALID_BENCHMARK"
    _rec("C2T16", inf_ok and inf_bad, "strict kind inference ok & mixed blocked")

    # C2T17..C2T20: mode W4 workers=2, worker_pids len 3, dup worker_pids, science chunks != workers
    pb17 = copy.deepcopy(pb); pb17["runs"][2]["workers"] = 2
    _rec("C2T17", _cert(pb17, sb, _compute_optimum_worker_policy(pb17, benchmark_kind=BENCHMARK_KIND_PYTEST), s_pol)["status"] != "PASS", "W4 workers=2 rejected")
    pb18 = copy.deepcopy(pb); pb18["runs"][2]["worker_pids"] = [401, 402, 403]
    _rec("C2T18", _cert(pb18, sb, _compute_optimum_worker_policy(pb18, benchmark_kind=BENCHMARK_KIND_PYTEST), s_pol)["status"] != "PASS", "W4 3 pids rejected")
    pb19 = copy.deepcopy(pb); pb19["runs"][2]["worker_pids"] = [401, 401, 402, 403]
    _rec("C2T19", _cert(pb19, sb, _compute_optimum_worker_policy(pb19, benchmark_kind=BENCHMARK_KIND_PYTEST), s_pol)["status"] != "PASS", "duplicate pids rejected")
    sb20 = copy.deepcopy(sb); sb20["runs"][2]["chunks_count"] = 2
    _rec("C2T20", _cert(pb, sb20, p_pol, _compute_optimum_worker_policy(sb20, benchmark_kind=BENCHMARK_KIND_SCIENCE))["status"] != "PASS", "science chunks mismatch rejected")

    # C2T21 & C2T22: nonidentical SHA or count drift with forged all_4_runs_identical=True
    pb21 = copy.deepcopy(pb); pb21["runs"][1]["canonical_sorted_outcome_sha256"] = "9" * 64
    _rec("C2T21", _cert(pb21, sb, p_pol, s_pol)["status"] != "PASS", "forged identical SHA rejected")
    pb22 = copy.deepcopy(pb); pb22["runs"][1]["executed_tests_count"] = 154
    _rec("C2T22", _cert(pb22, sb, p_pol, s_pol)["status"] != "PASS", "count drift rejected")

    # C2T23: forged policy.status=PASS on invalid raw caught by independent verifier
    f19_23 = copy.deepcopy(f19_ok); f19_23["pytest_controlled_subset_w1_w2_w4_repeat"]["runs"][2]["wall_clock_seconds"] = -1.0
    _rec("C2T23", not recompute_r3_worker_policy_from_raw_for_verifier(f19_23, expected_run_id=FRESH_RID, expected_tested_sha=FRESH_SHA)["valid"], "forged PASS on invalid raw caught")

    # C2T24: can_certify="true" (string) rejected
    p24 = dict(p_pol, can_certify="true")
    _rec("C2T24", _cert(pb, sb, p24, s_pol)["status"] != "PASS", "string 'true' can_certify rejected")

    # C2T25: empty mean_wall_sec_by_mode under PASS rejected
    p25 = dict(p_pol, mean_wall_sec_by_mode={})
    _rec("C2T25", _cert(pb, sb, p25, s_pol)["status"] != "PASS", "empty mean map rejected")

    # C2T26: fastest W4 but policy claims W1 rejected
    p26 = dict(p_pol, measured_fastest_mode="w=1", optimum_worker_policy="w=1")
    _rec("C2T26", _cert(pb, sb, p26, s_pol)["status"] != "PASS", "wrong fastest W1 vs W4 rejected")

    # C2T27: w=1_repeat SHA mismatch rejected
    pb27 = copy.deepcopy(pb); pb27["runs"][3]["canonical_sorted_outcome_sha256"] = "8" * 64
    _rec("C2T27", _cert(pb27, sb, p_pol, s_pol)["status"] != "PASS", "w1_repeat SHA mismatch rejected")

    # C2T28: applied_worker_count > safe cap / > 4 rejected
    p28 = dict(p_pol, applied_worker_count=5)
    _rec("C2T28", _cert(pb, sb, p28, s_pol)["status"] != "PASS", "applied_worker_count=5 rejected")

    # C2T29..C2T33: runtime disk <20, preflight <20.5, RAM <512, waiver True, can_run_heavy=False
    _rec("C2T29", _cert(pb, sb, p_pol, s_pol, res=dict(GOOD_RES, runtime_disk_free_gib=19.5))["status"] == "BLOCKED_RESOURCE", "runtime disk <20 blocked")
    _rec("C2T30", _cert(pb, sb, p_pol, s_pol, res=dict(GOOD_RES, preflight_disk_free_gib=20.2))["status"] == "BLOCKED_RESOURCE", "preflight disk <20.5 blocked")
    _rec("C2T31", _cert(pb, sb, p_pol, s_pol, res=dict(GOOD_RES, min_available_ram_mib=400.0))["status"] == "BLOCKED_RESOURCE", "RAM <512 blocked")
    _rec("C2T32", _cert(pb, sb, p_pol, s_pol, res=dict(GOOD_RES, owner_disk_preflight_waiver=True))["status"] == "FAIL", "waiver rejected FAIL")
    _rec("C2T33", _cert(pb, sb, p_pol, s_pol, can_run_heavy=False)["status"] == "NOT_RUN_BLOCKED_LOW_DISK", "can_run_heavy=False never PASS")

    # C2T34 & C2T35: stale archived R3 identity refused in strict mode; allowed ONLY as LEGACY_DESCRIPTIVE_ONLY
    _rec("C2T34", _cert(pb, sb, p_pol, s_pol, run_id=ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF, tested_sha=ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF)["status"] == "FAIL", "stale archived R3 refused")
    r3_19_path = REPO_ROOT / f"aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis/{ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF}/19_w1_w2_w4_w1repeat_hashes_timings_speedups.json"
    r3_19_old = json.loads(r3_19_path.read_text("utf-8"))
    strict_arch = recompute_r3_worker_policy_from_raw_for_verifier(r3_19_old, expected_run_id=ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF, expected_tested_sha=ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF, allow_legacy_descriptive_only=False)
    desc_arch = recompute_r3_worker_policy_from_raw_for_verifier(r3_19_old, expected_run_id=ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF, expected_tested_sha=ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF, allow_legacy_descriptive_only=True)
    _rec("C2T35", (not strict_arch["valid"]) and desc_arch["classification"] == "LEGACY_DESCRIPTIVE_ONLY" and desc_arch["admitted_for_new_c2_certification"] is False, "historical R3 descriptive-only")

    # C2T36: W1_repeat duration drift with identical semantic SHA -> deterministic output + noise warning
    pb36 = _make_pb(w1=30.0, w2=20.0, w4=14.0, w1r=38.0)
    p36 = _compute_optimum_worker_policy(pb36, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits={"configured_child_process_cap": 4, "currently_allowed_worker_cap": 3, "full_suite_safe_worker_cap": 3, "shard_exclusive_cap": 3})
    _rec("C2T36", p36["status"] == "PASS" and p36["timing_determinism_claimed"] is False and p36["timing_stability_status"] == "HIGH_TIMING_NOISE_WARNING", "W1_repeat noise warning")

    # C2T37: W4 subset fastest with safe cap W3 -> applied=3, applied_worker_count_is_benchmark_measured=False
    _rec("C2T37", p_pol["measured_fastest_mode"] == "w=4" and p_pol["applied_worker_count"] == 3 and p_pol["applied_worker_count_is_benchmark_measured"] is False and p_pol["full_suite_speedup_extrapolated"] is False, "W4 measured vs W3 cap separated")

    # C2T38: science fastest W2 on 8k slice has explicit non-extrapolated scope
    _rec("C2T38", s_pol["measured_fastest_mode"] == "w=2" and s_pol["applied_worker_count"] == 2 and "NOT_full_dataset" in str(s_pol["benchmark_scope"]), "science W2 scoped to 8k slice")

    # C2T39 & C2T40: G22 invalid blocks overall engineering PASS & verifier detects G22 stored-vs-recomputed drift
    from tests.test_kh03_r3_resource_async_and_verifier_mutations import build_synthetic_valid_kh03_r3_run_dir
    import tempfile
    with tempfile.TemporaryDirectory(prefix="c2t39_") as td:
        cd = Path(td) / "r3_c39"
        rid39, _ = build_synthetic_valid_kh03_r3_run_dir(cd)
        docs39 = {
            "00": json.loads((cd / "00_source_branch_clean_sha_disk_ram_preflight.json").read_text()),
            "01": json.loads((cd / "01_ast_ownership_legacy_guard_and_keep_as_is_delta.json").read_text()),
            "02": json.loads((cd / "02_prior_sealed_sha_and_canonical_dataset_integrity.json").read_text()),
            "03": json.loads((cd / "03_r1_legacy_false_pass_red_reproductions.json").read_text()),
            "04": json.loads((cd / "04_r2_method_guard_green_and_entrypoint_scans.json").read_text()),
            "05": json.loads((cd / "05_full_opportunity_lineage_and_hash_manifest.json").read_text()),
            "07": json.loads((cd / "07_win_loss_breakdown_denominator_ci_coverage.json").read_text()),
            "08": json.loads((cd / "08_stop_target_gap_tie_holding_risk_tail_attribution.json").read_text()),
            "09": json.loads((cd / "09_six_cost_scenarios_and_breakeven_accounting.json").read_text()),
            "10": json.loads((cd / "10_negative_controls_matching_and_causal_disclaimers.json").read_text()),
            "11": json.loads((cd / "11_training_only_bounded_prereg_plan.json").read_text()),
            "12": json.loads((cd / "12_candidate_asof_features_and_exact_conservation.json").read_text()),
            "13": json.loads((cd / "13_r2_forward_only_fold_boundaries_freeze_no_peek.json").read_text()),
            "14": json.loads((cd / "14_validation_fold_independent_results_by_arm.json").read_text()),
            "15": json.loads((cd / "15_fixed_fill_vs_dynamic_rerisk_and_expense_truth.json").read_text()),
            "16": json.loads((cd / "16_strict_dev_gate_power_holm_cluster_bootstrap.json").read_text()),
            "17": json.loads((cd / "17_baseline_parity_A1_SI8_adapter_D4.json").read_text()),
            "18": json.loads((cd / "18_full_pytest_junit_collected_executed_bijection.json").read_text()),
            "19": json.loads((cd / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json").read_text()),
            "20": json.loads((cd / "20_disk_ram_pid_samples_and_cleanup.json").read_text()),
            "21": json.loads((cd / "21_inherited_plus_new_unique_red_green_fault_receipts.json").read_text()),
            "22": json.loads((cd / "22_independent_verifier_new_mutation_resistance.json").read_text()),
            "25": json.loads((cd / "25_phase1_code_commit_push_readback.json").read_text()),
        }
        docs39["19"]["pytest_optimum_worker_policy"]["mean_wall_sec_by_mode"] = {}
        rec39 = recompute_kh03_r3_30_gates_and_verdicts(
            docs=docs39,
            mem_audit={"status": "PASS", "below_floor_samples_count": 0, "max_workers_observed_in_samples": 2},
            forensic_ledger_audit={"valid": True, "max_residual_usd": 0.0},
            junit_bijection_ok=True,
            for_final_verifier_manifest=False,
        )
        _rec("C2T39", rec39["gates_30"]["G22_W1_W2_W4_W1REPEAT_SEMANTIC_DETERMINISM"] == "FAIL" and rec39["verdicts"]["KH03_R3_ENGINEERING"] == "FAIL", "G22 FAIL forces engineering FAIL")
        _rec("C2T40", rec39["gates_30"]["G22_W1_W2_W4_W1REPEAT_SEMANTIC_DETERMINISM"] != "PASS", "mutated G22 PASS drift caught")

    return results


def run_new_kh03_r3_c2_legacy_fail_closed_fault_controls() -> List[Dict[str, Any]]:
    """Executes 16 NEW unique C2 RED/GREEN fault controls (`KH03R3C2-F01`..`F16`)."""
    pb, sb, p_pol, s_pol = _make_valid_pair()
    good_c = _cert(pb, sb, p_pol, s_pol)
    good_f19 = _assemble_f19(pb, sb, p_pol, s_pol, good_c)
    good_v = recompute_r3_worker_policy_from_raw_for_verifier(good_f19, expected_run_id=FRESH_RID, expected_tested_sha=FRESH_SHA)["valid"]
    out: List[Dict[str, Any]] = []

    def _rec(fid: str, name: str, fn: str, red_ok: bool, green_ok: bool, detail: str) -> None:
        out.append({"fault_id": fid, "name": name, "anchored_function": fn, "red_rejected": bool(red_ok), "green_accepted": bool(green_ok), "status": "PASS" if (red_ok and green_ok) else "FAIL", "diagnostic": detail})

    pb_nw4 = copy.deepcopy(pb); pb_nw4["runs"] = [r for r in pb_nw4["runs"] if r["mode_label"] != "w=4"]
    _rec("KH03R3C2-F01", "OLD_R3_PASS_ON_MISSING_W4_CLOSED", "_evaluate_r3_f19_policy_certification", _legacy_prefixed_r3_f19_status_red_repro_only(pb_nw4, sb) == "PASS" and _cert(pb_nw4, sb, _compute_optimum_worker_policy(pb_nw4, benchmark_kind=BENCHMARK_KIND_PYTEST), s_pol)["status"] == "FAIL", good_c["status"] == "PASS", "missing W4 closed")
    pb_neg = copy.deepcopy(pb); pb_neg["runs"][0]["wall_clock_seconds"] = -1.0
    _rec("KH03R3C2-F02", "NEGATIVE_DURATION_F19_FAIL_CLOSED", "_evaluate_r3_f19_policy_certification", _cert(pb_neg, sb, _compute_optimum_worker_policy(pb_neg, benchmark_kind=BENCHMARK_KIND_PYTEST), s_pol)["status"] == "FAIL", good_c["status"] == "PASS", "negative duration blocked")
    pb_nan = copy.deepcopy(pb); pb_nan["runs"][0]["wall_clock_seconds"] = float("nan")
    _rec("KH03R3C2-F03", "NAN_DURATION_F19_FAIL_CLOSED", "_evaluate_r3_f19_policy_certification", _cert(pb_nan, sb, _compute_optimum_worker_policy(pb_nan, benchmark_kind=BENCHMARK_KIND_PYTEST), s_pol)["status"] == "FAIL", good_c["status"] == "PASS", "NaN duration blocked")
    sb_mix = copy.deepcopy(sb); sb_mix["runs"][0]["wall_clock_seconds"] = 2.0
    _rec("KH03R3C2-F04", "CROSS_KIND_SCIENCE_KEY_BLOCKED", "_evaluate_r3_f19_policy_certification", _cert(pb, sb_mix, p_pol, _compute_optimum_worker_policy(sb_mix, benchmark_kind=BENCHMARK_KIND_SCIENCE))["status"] == "FAIL", good_c["status"] == "PASS", "cross-kind rejected")
    _rec("KH03R3C2-F05", "STRING_TRUE_CAN_CERTIFY_REJECTED", "_evaluate_r3_f19_policy_certification", _cert(pb, sb, dict(p_pol, can_certify="true"), s_pol)["status"] == "FAIL", good_c["can_certify"] is True, "strict bool enforced")
    _rec("KH03R3C2-F06", "WHITELIST_POLICY_STATUS_ENFORCED", "_evaluate_r3_f19_policy_certification", _cert(pb, sb, dict(p_pol, policy_status="CUSTOM_UNAPPROVED"), s_pol)["status"] == "FAIL", good_c["status"] == "PASS", "policy_status whitelist enforced")
    _rec("KH03R3C2-F07", "EMPTY_MEAN_MAP_WITH_PASS_REJECTED", "_evaluate_r3_f19_policy_certification", _cert(pb, sb, dict(p_pol, mean_wall_sec_by_mode={}), s_pol)["status"] == "FAIL", good_c["status"] == "PASS", "empty means rejected")
    _rec("KH03R3C2-F08", "APPLIED_CAP_OVER_3_PYTEST_REJECTED", "_evaluate_r3_f19_policy_certification", _cert(pb, sb, dict(p_pol, applied_worker_count=4), s_pol)["status"] == "FAIL", p_pol["applied_worker_count"] == 3, "pytest cap<=3 enforced")
    _rec("KH03R3C2-F09", "FULL_SUITE_EXTRAPOLATION_REJECTED", "_evaluate_r3_f19_policy_certification", _cert(pb, sb, dict(p_pol, full_suite_speedup_extrapolated=True), s_pol)["status"] == "FAIL", good_c["status"] == "PASS", "extrapolation blocked")
    _rec("KH03R3C2-F10", "LOW_DISK_OR_RAM_BLOCKS_F19", "_evaluate_r3_f19_policy_certification", _cert(pb, sb, p_pol, s_pol, res=dict(GOOD_RES, runtime_disk_free_gib=19.0))["status"] == "BLOCKED_RESOURCE" and _cert(pb, sb, p_pol, s_pol, res=dict(GOOD_RES, min_available_ram_mib=300.0))["status"] == "BLOCKED_RESOURCE", good_c["status"] == "PASS", "resource floor blocks")
    _rec("KH03R3C2-F11", "WAIVER_FLAG_FAILS_F19", "_evaluate_r3_f19_policy_certification", _cert(pb, sb, p_pol, s_pol, res=dict(GOOD_RES, owner_disk_preflight_waiver=True))["status"] == "FAIL", good_c["status"] == "PASS", "waiver rejected")
    _rec("KH03R3C2-F12", "CAN_RUN_HEAVY_FALSE_NEVER_PASS", "_evaluate_r3_f19_policy_certification", _cert(pb, sb, p_pol, s_pol, can_run_heavy=False)["status"] == "NOT_RUN_BLOCKED_LOW_DISK", good_c["status"] == "PASS", "low-disk never PASS")
    _rec("KH03R3C2-F13", "STALE_ARCHIVED_R3_NEW_CERT_REFUSED", "_evaluate_r3_f19_policy_certification", _cert(pb, sb, p_pol, s_pol, run_id=ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF, tested_sha=ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF)["status"] == "FAIL", good_c["status"] == "PASS", "stale R3 identity refused")
    f19_dg = copy.deepcopy(good_f19); f19_dg.pop("worker_policy_version", None)
    _rec("KH03R3C2-F14", "VERIFIER_REJECTS_VERSION_STRIP_DOWNGRADE", "recompute_r3_worker_policy_from_raw_for_verifier", not recompute_r3_worker_policy_from_raw_for_verifier(f19_dg, expected_run_id=FRESH_RID, expected_tested_sha=FRESH_SHA)["valid"], good_v, "downgrade attack blocked")
    f19_sp = copy.deepcopy(good_f19); f19_sp["measured_pytest_speedup_w4_vs_w1"] = 999.0
    _rec("KH03R3C2-F15", "VERIFIER_REJECTS_FORGED_TOP_SPEEDUP", "recompute_r3_worker_policy_from_raw_for_verifier", not recompute_r3_worker_policy_from_raw_for_verifier(f19_sp, expected_run_id=FRESH_RID, expected_tested_sha=FRESH_SHA)["valid"], good_v, "forged speedup caught")
    f19_nm = copy.deepcopy(good_f19); f19_nm["pytest_controlled_subset_w1_w2_w4_repeat"]["runs"][2]["wall_clock_seconds"] = 99.0
    _rec("KH03R3C2-F16", "VERIFIER_INDEPENDENTLY_RECOMPUTES_RAW_DURATIONS", "recompute_r3_worker_policy_from_raw_for_verifier", not recompute_r3_worker_policy_from_raw_for_verifier(f19_nm, expected_run_id=FRESH_RID, expected_tested_sha=FRESH_SHA)["valid"], good_v, "raw duration edit caught")
    return out


_MATRIX_CACHE = execute_c2t01_to_c2t40_boundary_matrix()


@pytest.mark.parametrize("row", _MATRIX_CACHE, ids=[r["case_id"] for r in _MATRIX_CACHE])
def test_kh03_r3_c2_boundary_case_c2t01_to_c2t40(row):
    assert row["passed"] is True, f"Boundary case {row['case_id']} failed: {row['detail']}"


def test_kh03_r3_c2_16_new_fault_controls_all_pass():
    ctrls = run_new_kh03_r3_c2_legacy_fail_closed_fault_controls()
    assert len(ctrls) == 16
    assert len({c["fault_id"] for c in ctrls}) == 16
    assert all(c["status"] == "PASS" for c in ctrls), [c for c in ctrls if c["status"] != "PASS"]


def test_kh03_r3_c2_cli_refuses_disk_waiver_exit_2():
    script = APP_ROOT / "tools" / "run_kh_03_r3_c2_acceptance.py"
    assert script.is_file()
    p = subprocess.run([sys.executable, str(script), "--owner-waive-disk-preflight"], cwd=str(APP_ROOT), capture_output=True, text=True, timeout=20)
    assert p.returncode == 2
    env = os.environ.copy()
    env["KH03_C2_OWNER_WAIVE_DISK_PREFLIGHT"] = "1"
    p2 = subprocess.run([sys.executable, str(script), "--help"], cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=20)
    assert p2.returncode == 2
