"""
aurum-desk/backend/tests/test_kh03_r4_independent_verifier_mutations.py

KH-03.R4 Separate-OS Independent Verifier Mutation Suite (18 NEW Non-Redundant Attacks R4M01..R4M18):
Verifies clean synthetic R4 run passes in a separate OS process (`exit_code=0`, `G01..G28=PASS`,
`G29=NO_PROVEN_EDGE_NO_GO_DIAGNOSIS_CONSISTENT`, `violations=[]`), and EVERY ONE of the 18
targeted tampering attacks (covering all 16 mandatory attacks in spec §9 item 5 + 2 extra) causes
non-zero exit with its exact violation token!
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable, Dict, List, Tuple

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_fee_integrity_service import EXPECTED_SIX_SCENARIOS_KH03
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    select_measured_worker_policy_strict,
)
from services.kh_03_r4_economic_diagnostic_service import (
    audit_r4_covariate_overlap_and_effective_n,
    build_r4_counterfactual_cost_only_diagnostics,
    build_r4_data_gap_register,
    build_r4_mechanism_evidence_table,
    build_r4_trade_population_conservation,
    decide_r4_research_next_action,
    derive_r4_breakeven_feasibility_bounds,
    recompute_r4_decimal_economic_bridge,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tests.test_kh03_r4_population_and_censoring import _make_population_inputs
from tools.kh_03_r3_c1_independent_verifier import (
    PRIOR_KH03_R3_RUN_ID,
    PRIOR_KH03_R3_TESTED_SHA,
    SEALED_BASELINE_PARITY_FINGERPRINTS,
)
from tools.kh_03_r4_independent_verifier import (
    MANDATORY_BRANCH_KH03_R4,
    SCHEMA_VERSION_KH03_R4,
    SPEC_VERSION_KH03_R4,
    _audit_claims_10_data_11_decision_12,
    _audit_forensic_ledger_gz_decimal,
    _audit_six_cost_08_and_arms_09,
    _audit_telemetry_gz_vs_16_r4,
    _recompute_raw_bench_r4,
    recompute_r4_gates_from_raw,
)


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _mut_json(p: Path, fn: Callable[[Dict[str, Any]], None]) -> None:
    d = json.loads(p.read_text("utf-8"))
    fn(d)
    p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")


def _rewrite_ledger_and_resign(run_dir: Path, mut_rows_fn: Callable[[List[Dict[str, Any]]], None]) -> None:
    """Rewrites raw ledger gz AND updates file/row hashes in 04 so verifier MUST catch semantic tamper directly."""
    gz_p = run_dir / "logs" / "forensic_closed_open_gap_ledger_1x.jsonl.gz"
    rows: List[Dict[str, Any]] = []
    with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
        for ln in fh:
            if ln.strip():
                rows.append(json.loads(ln))
    mut_rows_fn(rows)
    uids: List[str] = []
    rhashes: List[str] = []
    with gzip.open(gz_p, "wt", encoding="utf-8") as fh:
        for r in rows:
            s = json.dumps(r, sort_keys=True, separators=(",", ":"))
            fh.write(s + "\n")
            uids.append(str(r["episode_uid"]))
            rhashes.append(hashlib.sha256(s.encode("utf-8")).hexdigest())

    def _upd04(d: Dict[str, Any]) -> None:
        lk = d["linked_forensic_ledger"]
        lk["ledger_file_sha256"] = _sha256_file(gz_p)
        lk["total_episode_rows"] = len(uids)
        lk["episode_uids_sha256"] = canonical_hash(uids)
        lk["canonical_rows_sha256"] = canonical_hash(rhashes)

    _mut_json(run_dir / "04_closed_trade_decimal_gross_fee_net_bridge.json", _upd04)


def build_synthetic_valid_kh03_r4_run_dir(run_dir: Path) -> Tuple[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    tsha = "d" * 40
    rid = f"20261123T234500Z_{tsha[:12]}"

    inp = _make_population_inputs()
    rows = inp["rows"]
    led_rel = "logs/forensic_closed_open_gap_ledger_1x.jsonl.gz"
    led_gz = run_dir / led_rel
    uids: List[str] = []
    rhashes: List[str] = []
    with gzip.open(led_gz, "wt", encoding="utf-8") as gz:
        for r in rows:
            s = json.dumps(r, sort_keys=True, separators=(",", ":"))
            gz.write(s + "\n")
            uids.append(str(r["episode_uid"]))
            rhashes.append(hashlib.sha256(s.encode("utf-8")).hexdigest())
    led_meta = {
        "ledger_filename": led_gz.name,
        "ledger_rel_path": led_rel,
        "ledger_file_sha256": _sha256_file(led_gz),
        "total_episode_rows": len(uids),
        "episode_uids_sha256": canonical_hash(uids),
        "canonical_rows_sha256": canonical_hash(rhashes),
        "max_gross_fee_net_residual_usd": 0.0,
        "overlap_violations": 0,
    }

    # Build 6 dynamic scenarios matching valid closed counts & Decimal math
    base_1x_arms = inp["dyn6"]["1X_BASE_0p0004"]["arms"]
    dyn6: Dict[str, Any] = {"1X_BASE_0p0004": {"arms": base_1x_arms}}
    for idx_sc, sc in enumerate(EXPECTED_SIX_SCENARIOS_KH03):
        if sc == "1X_BASE_0p0004":
            continue
        cl_k = max(14, 150 - idx_sc * 20)
        dyn6[sc] = {
            "arms": {
                "NC_NULL_TRIGGER": {
                    "eligible_candidates_input": cl_k + 50, "scheduled": cl_k, "skipped_due_to_active_position": 50,
                    "unfilled": 0, "closed": cl_k, "wins": 1, "losses": cl_k - 1, "open_at_horizon": 0, "data_gap_unresolved": 0,
                    "effective_setup_episodes": cl_k, "effective_ny_days": 10, "overlap_violations": 0,
                    "gross_pnl_usd": -50.0, "total_fees_usd": 80.0, "net_pnl_usd": -130.0, "expectancy_r": -0.9,
                    "ny_day_cluster_95ci_r": [-1.0, -0.7], "executed_ids_sha256": f"{idx_sc}" * 64,
                }
            }
        }

    d03 = build_r4_trade_population_conservation(
        lineage_05=inp["lineage_05"], forensic_rows=rows, r2_cands_1x=inp["r2_cands_1x"],
        dyn6_scenarios=dyn6, dataset_sha=CANONICAL_DATASET_SHA256_KH02,
    )
    d04 = recompute_r4_decimal_economic_bridge(rows, dataset_sha=CANONICAL_DATASET_SHA256_KH02, ledger_meta=led_meta)
    d07 = build_r4_counterfactual_cost_only_diagnostics(rows, fixed_fills_from_r3={"status": "PASS"}, dyn6_scenarios=dyn6, dataset_sha=CANONICAL_DATASET_SHA256_KH02)
    f08_attr = {"non_exclusive_failure_buckets": {"losses_with_20bar_mfe_ge_1p0x_sl_before_stopout": 86, "realized_losses_gt_2p50_cap_count": 139}}
    d05, d10 = build_r4_mechanism_evidence_table(rows, f08_attr=f08_attr, f09_cost={}, decimal_bridge=d04, fee_only_diag=d07)
    d06, d09 = audit_r4_covariate_overlap_and_effective_n(
        f15_cost={"dynamic_full_rerisk_six_scenarios": dyn6}, f16_gate={"passing_dev_candidate_arms": [], "primary_arm_evaluations": {}},
        forensic_rows=rows,
        f10_nc={"status": "PASS", "future_bar_mutation_invariance": {"mismatches_count": 0, "passed": True}, "synthetic_label_and_conservative_tie_controls": {"passed": True}},
        f13_wf={"status": "PASS", "is_fresh_oos": False},
        r2_fold_causality={"valid": True, "status": "PASS", "violations": []},
    )
    d08 = derive_r4_breakeven_feasibility_bounds(rows, f09_cost={"scenarios": {}}, dyn6_scenarios=dyn6, dataset_sha=CANONICAL_DATASET_SHA256_KH02)
    d11 = build_r4_data_gap_register(repo_root=REPO_ROOT, dataset_sha=CANONICAL_DATASET_SHA256_KH02, timeframe_stats={"1m": {"first_utc": "2026-10-08T00:00:00Z", "last_utc": "2026-10-28T00:00:00Z", "missing_bar_ratio": 0.2364}})
    d12 = decide_r4_research_next_action(decimal_bridge=d04, fee_only_diag=d07, data_register=d11, arm_comparability=d09)

    pb_raw = {
        "spec_version": SPEC_VERSION_KH03_R4, "schema_version": SCHEMA_VERSION_KH03_R4,
        "run_id": rid, "tested_executable_sha": tsha, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "benchmark_kind": BENCHMARK_KIND_PYTEST, "status": "PASS", "all_4_runs_identical": True,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "worker_pids": [101], "wall_clock_seconds": 30.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=2", "workers": 2, "worker_pids": [201, 202], "wall_clock_seconds": 20.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=4", "workers": 4, "worker_pids": [401, 402, 403, 404], "wall_clock_seconds": 15.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=1_repeat", "workers": 1, "worker_pids": [501], "wall_clock_seconds": 29.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
        ],
    }
    sb_raw = {
        "spec_version": SPEC_VERSION_KH03_R4, "schema_version": SCHEMA_VERSION_KH03_R4,
        "run_id": rid, "tested_executable_sha": tsha, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "benchmark_kind": BENCHMARK_KIND_SCIENCE, "status": "PASS", "all_4_runs_identical": True,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.40, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=2", "workers": 2, "chunks_count": 2, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.12, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=4", "workers": 4, "chunks_count": 4, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.25, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=1_repeat", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.38, "canonical_sorted_sha256": "2" * 64},
        ],
    }
    p_pol = select_measured_worker_policy_strict(pb_raw, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits={"configured_child_process_cap": 4, "currently_allowed_worker_cap": 3, "full_suite_safe_worker_cap": 3, "shard_exclusive_cap": 3}, expected_run_id=rid, expected_tested_sha=tsha, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02)
    s_pol = select_measured_worker_policy_strict(sb_raw, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits={"configured_child_process_cap": 4, "currently_allowed_worker_cap": 4, "full_suite_safe_worker_cap": 4, "shard_exclusive_cap": 4}, expected_run_id=rid, expected_tested_sha=tsha, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02)

    n_tc = 5840
    c_ids = [f"backend/tests/test_kh03_r4_synth.py::test_r4_{i:04d}" for i in range(n_tc)]
    xp = run_dir / "junit" / "SHARD_01.xml"
    tcs = "".join(f'<testcase classname="backend.tests.test_kh03_r4_synth" name="test_r4_{i:04d}" time="0.001"/>' for i in range(n_tc))
    xp.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuite tests="{n_tc}" failures="0" errors="0" skipped="0">{tcs}</testsuite>', encoding="utf-8")

    mem_gz = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(mem_gz, "wt", encoding="utf-8") as mgz:
        for i in range(4):
            mgz.write(json.dumps({"ts_ms": 1795440000000 + i * 500, "available_ram_bytes": 3_500_000_000, "available_mib": 3337.86, "child_count": 3, "worker_pids": [1, 2, 3], "disk_free_gib": 29.8}) + "\n")

    docs: Dict[str, Dict[str, Any]] = {
        "00": {"branch": MANDATORY_BRANCH_KH03_R4, "start_head_sha": tsha, "end_head_sha": tsha, "git_clean_at_start": True, "local_uncommitted_changes": False, "orchestrator_pid": -9999, "owner_disk_preflight_waiver": False, "strict_multi_mount_probe": {"can_run_heavy_stages": True, "owner_waiver_permitted": False, "probe_errors": [], "distinct_mounts": [{"free_gib": 29.8, "projected_post_reserve_gib": 29.3}]}, "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"}, "ACTIVATION_READY": "NO", "status": "PASS"},
        "01": {"status": "PASS", "locked_files_modified_count": 0, "unauthorized_changed_files_count": 0, "forbidden_order_import_hits": [], "cost_assumptions_defaults_intact": True, "legacy_guard_status": "PASS", "unauthorized_r1_callers_count": 0},
        "02": {"status": "PASS", "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "canonical_dataset_sha256_match": True, "all_prior_sealed_artifacts_intact": True},
        "03": d03, "04": d04, "05": d05, "06": d06, "07": d07, "08": d08, "09": d09, "10": d10, "11": d11, "12": d12,
        "13": {"status": "PASS", "collected_count": n_tc, "executed_count": n_tc, "passed_count": n_tc, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": True, "collection_manifest": {"run_id": rid, "tested_executable_sha": tsha, "collected_node_ids_count": n_tc, "collected_node_ids": c_ids}, "shard_manifest": {"run_id": rid, "tested_executable_sha": tsha, "nonzero_exit_shards_count": 0, "shards": [{"shard_id": "SHARD_01", "exit_code": 0, "timed_out": False, "reused": False, "junit_xml_path": "junit/SHARD_01.xml", "xml_sha256": _sha256_file(xp), "scheduled_node_ids": c_ids}]}},
        "14": {"BASELINE_PARITY_VERDICT": "PASS", "a1_population_count": 56710, "a1_mismatches_count": 0, "si8_population_count": 6375, "si8_mismatches_count": 0, "three_adapter_population_count": 246, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {**SEALED_BASELINE_PARITY_FINGERPRINTS, "all_sealed_fingerprints_exact_match": True}},
        "15": {"status": "PASS", "pytest_controlled_subset_raw": pb_raw, "science_slice_8000_raw": sb_raw, "pytest_worker_policy": p_pol, "science_worker_policy": s_pol, "full_suite_speedup_extrapolated": False},
        "16": {"status": "PASS", "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz", "min_disk_free_gib": 29.8, "min_available_ram_mib": 3337.86, "max_descendant_children": 3, "orphan_children_after_cleanup": 0, "sentinel_temp_cleaned_up": True, "owner_disk_preflight_waiver": False},
        "17": {"status": "PASS", "inherited_c2_c1_r3_controls_count": 224, "new_r4_controls_count": 16, "total_controls": 240, "unique_fault_ids": 240, "failed_controls": 0},
        "18": {"status": "PASS", "clean_clone_verifier_green": True, "inherited_c2_c1_r3_mutations_tested_count": 51, "inherited_c2_c1_r3_mutations_failed_detection_count": 0, "new_r4_mutations_tested_count": 18, "new_r4_mutations_failed_detection_count": 0},
        "20": {"publication_status": "PRE_PUSH_PENDING", "remote_readback_sha256_verified": False, "publication_head_sha": None, "evidence_commit_sha": None, "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN", "actual_github_actions_run_verified": False, "owner_disk_preflight_waiver": False},
    }
    for k, d in docs.items():
        d["spec_version"] = SPEC_VERSION_KH03_R4
        d["schema_version"] = SCHEMA_VERSION_KH03_R4
        d["run_id"] = rid
        d["tested_executable_sha"] = tsha

    led_au = _audit_forensic_ledger_gz_decimal(run_dir, docs["03"], docs["04"], docs["05"], docs["06"], docs["07"])
    ec_au = _audit_six_cost_08_and_arms_09(docs["08"], docs["09"])
    cl_au = _audit_claims_10_data_11_decision_12(docs["10"], docs["11"], docs["12"])
    mem_au = _audit_telemetry_gz_vs_16_r4(mem_gz, docs["16"])
    p_rec = _recompute_raw_bench_r4(pb_raw, p_pol, kind=BENCHMARK_KIND_PYTEST, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=3)
    s_rec = _recompute_raw_bench_r4(sb_raw, s_pol, kind=BENCHMARK_KIND_SCIENCE, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=4)
    r19 = recompute_r4_gates_from_raw(
        docs=docs, ledger_audit=led_au, econ_audit=ec_au, claims_audit=cl_au,
        pytest_recomp=p_rec, science_recomp=s_rec, mem_audit=mem_au, junit_ok=True, for_final_verifier_manifest=False,
    )
    docs["19"] = {"spec_version": SPEC_VERSION_KH03_R4, "schema_version": SCHEMA_VERSION_KH03_R4, "run_id": rid, "tested_executable_sha": tsha, **r19}

    fn_map = {
        "00": "00_branch_head_clean_sha_preflight_resource_lock.json",
        "01": "01_ownership_keep_as_is_locked_sha_manifest.json",
        "02": "02_prior_r1_r2_r3_c1_c2_dataset_hash_integrity.json",
        "03": "03_raw_population_lineage_and_denominator_conservation.json",
        "04": "04_closed_trade_decimal_gross_fee_net_bridge.json",
        "05": "05_entry_exit_stop_target_risk_and_tail_diagnostic.json",
        "06": "06_censoring_open_gap_unfilled_and_effective_n.json",
        "07": "07_fee_only_fixed_fills_non_executable_counterfactual.json",
        "08": "08_full_dynamic_rerisk_6_cost_scenario_replay.json",
        "09": "09_E1_E2_E3_null_negative_controls_comparability.json",
        "10": "10_assoc_not_causal_and_no_alpha_claim_proof.json",
        "11": "11_data_readiness_register_and_missing_evidence.json",
        "12": "12_go_nogo_decision_rules_and_owner_next_actions.json",
        "13": "13_full_pytest_junit_fresh_collection_bijection.json",
        "14": "14_A1_SI8_adapter_D4_production_baseline_parity.json",
        "15": "15_W1_W2_W4_W1repeat_determinism_real_timings.json",
        "16": "16_disk_ram_PID_0p5s_log_and_cleanup.json",
        "17": "17_inherited_plus_new_fault_red_green_receipts.json",
        "18": "18_separate_OS_verifier_mutation_attack_results.json",
        "19": "19_orthogonal_30_gates_and_verdict_matrix.json",
        "20": "20_phase1_code_commit_push_receipt.json",
    }
    for k, fn in fn_map.items():
        (run_dir / fn).write_text(json.dumps(docs[k], indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "README_KH03_R4.md").write_text("# Synthetic Valid KH-03.R4 Run\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH03_R4_VI.txt").write_text("Synthetic Valid KH-03.R4 Handover\n", encoding="utf-8")
    return rid, tsha


def run_kh03_r4_verifier_subprocess(run_dir: Path, rid: str, tsha: str) -> Tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    proc = subprocess.run(
        [sys.executable, str(APP_ROOT / "tools" / "kh_03_r4_independent_verifier.py"), "--run-dir", str(run_dir), "--expected-run-id", rid, "--expected-tested-sha", tsha, "--no-write-manifest"],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    return int(proc.returncode), proc.stdout + "\n" + proc.stderr


# 18 NEW Separate-OS Verifier Mutations (covering all 16 mandatory §9.5 attacks + 2 extra)
KH03_R4_OWN_VERIFIER_MUTATIONS_SPECS: List[Tuple[str, Callable[[Path], None], str]] = [
    ("R4M01_FORGED_PNL_IN_04", lambda rd: _mut_json(rd / "04_closed_trade_decimal_gross_fee_net_bridge.json", lambda d: d["closed_trades_decimal_bridge_usd"].__setitem__("net_pnl_closed_usd", 123.45)), "R4V_LEDGER_NET_PNL_MISMATCH"),
    ("R4M02_ALTERED_N_CLOSED_LOSS_IN_03_04", lambda rd: _mut_json(rd / "03_raw_population_lineage_and_denominator_conservation.json", lambda d: d["primary_baseline_1x_null_conservation"].__setitem__("n_losses", 150)), "R4V_LEDGER_CLOSED_OR_LOSS_COUNT_MISMATCH"),
    ("R4M03_CHANGED_FEE_RESIDUAL_IN_LEDGER", lambda rd: _rewrite_ledger_and_resign(rd, lambda rs: rs[0].__setitem__("realized_net_pnl_usd", round(float(rs[0]["realized_net_pnl_usd"]) + 0.09, 4))), "R4V_LEDGER_GROSS_FEE_NET_RESIDUAL_BREACH"),
    ("R4M04_OPEN_CENSOR_RECODED_IN_06", lambda rd: _mut_json(rd / "06_censoring_open_gap_unfilled_and_effective_n.json", lambda d: d["baseline_1x_censoring_and_gaps"].__setitem__("n_open_censored", 0)), "R4V_CENSOR_OR_GAP_COUNT_MISMATCH"),
    ("R4M05_GROSS_ZEROED_OR_FEE_ONLY_ALPHA_IN_07", lambda rd: _mut_json(rd / "07_fee_only_fixed_fills_non_executable_counterfactual.json", lambda d: d.update({"baseline_gross_pnl_usd": 0.0, "is_executable_strategy": True})), "R4V_FEE_ONLY_MISLABELED_OR_GROSS_TAMPERED"),
    ("R4M06_INVENTED_OBSERVED_L1_IN_11", lambda rd: _mut_json(rd / "11_data_readiness_register_and_missing_evidence.json", lambda d: d.__setitem__("observed_l1_bbo_available", True)), "R4V_FABRICATED_L1_OR_ACCOUNT_FEE_OR_FUNDING"),
    ("R4M07_FORGED_FRESH_OOS_IN_12", lambda rd: _mut_json(rd / "12_go_nogo_decision_rules_and_owner_next_actions.json", lambda d: d.update({"OOS_EVALUATIONS": 1, "FRESH_OOS": "PASS"})), "R4V_FORGED_OOS_OR_ALPHA_OR_ACTIVATION"),
    ("R4M08_WRONG_DATASET_SHA_IN_02", lambda rd: _mut_json(rd / "02_prior_r1_r2_r3_c1_c2_dataset_hash_integrity.json", lambda d: d.__setitem__("canonical_dataset_sha256", "0" * 64)), "R4V_DATASET_SHA_MISMATCH"),
    ("R4M09_DUPLICATED_TRADE_ID_IN_LEDGER", lambda rd: _rewrite_ledger_and_resign(rd, lambda rs: rs[1].__setitem__("opportunity_id", rs[0]["opportunity_id"])), "R4V_LEDGER_DUPLICATE_OPPORTUNITY_ID"),
    ("R4M10_HIDDEN_DATA_GAP_IN_06", lambda rd: _mut_json(rd / "06_censoring_open_gap_unfilled_and_effective_n.json", lambda d: d["baseline_1x_censoring_and_gaps"].__setitem__("n_gap_unresolved", 0)), "R4V_CENSOR_OR_GAP_COUNT_MISMATCH"),
    ("R4M11_FORGED_POSITIVE_CI_IN_09", lambda rd: _mut_json(rd / "09_E1_E2_E3_null_negative_controls_comparability.json", lambda d: d["arm_profiles_1x"]["NC_NULL_TRIGGER"].__setitem__("ny_day_cluster_95ci_r_raw", [0.10, 0.40])), "R4V_FORGED_POSITIVE_CI_OR_ANCHOR_DRIFT"),
    ("R4M12_INFLATED_SPEEDUP_IN_15", lambda rd: _mut_json(rd / "15_W1_W2_W4_W1repeat_determinism_real_timings.json", lambda d: d["pytest_worker_policy"].__setitem__("measured_speedup_w4_vs_w1", 9.99)), "R4V_BENCH_SPEEDUP_OR_EXTRAPOLATION_TAMPERED"),
    ("R4M13_MAX_CHILD_BREACH_IN_TELEMETRY_GZ", lambda rd: gzip.open(rd / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", "wt", encoding="utf-8").write(json.dumps({"ts_ms": 1795440000000, "available_mib": 3337.86, "child_count": 6, "disk_free_gib": 29.8}) + "\n"), "R4V_TELEMETRY_CHILD_EXCEEDED_4"),
    ("R4M14_FALSE_GITHUB_READBACK_IN_20", lambda rd: _mut_json(rd / "20_phase1_code_commit_push_receipt.json", lambda d: d.update({"remote_readback_sha256_verified": True, "ci_status": "CI_PASS"})), "R4V_FALSE_PUBLICATION_OR_CI_CLAIM"),
    ("R4M15_ANCIENT_R3_RUN_REUSED_IN_00", lambda rd: _mut_json(rd / "00_branch_head_clean_sha_preflight_resource_lock.json", lambda d: d.update({"run_id": PRIOR_KH03_R3_RUN_ID, "tested_executable_sha": PRIOR_KH03_R3_TESTED_SHA})), "R4V_STALE_PRIOR_RUN_OR_SHA_REUSED"),
    ("R4M16_OMISSION_OF_RUNTIME_TELEMETRY_LOG", lambda rd: (rd / "logs" / "runtime_memory_samples_0p5s.jsonl.gz").unlink(), "R4V_MISSING_TELEMETRY_LOG"),
    ("R4M17_CAUSAL_ALPHA_INFLATION_IN_10", lambda rd: _mut_json(rd / "10_assoc_not_causal_and_no_alpha_claim_proof.json", lambda d: d["three_axis_diagnosis"]["STRATEGY_AND_COST_AXIS"].__setitem__("verdict", "CAUSAL_FIX_PROVED")), "R4V_CAUSAL_OR_ALPHA_CLAIM_INFLATION"),
    ("R4M18_PLANNED_CAP_AS_GUARANTEE_IN_05", lambda rd: _mut_json(rd / "05_entry_exit_stop_target_risk_and_tail_diagnostic.json", lambda d: d.__setitem__("planned_cap_is_physical_guarantee", True)), "R4V_PLANNED_CAP_MISREPRESENTED_AS_GUARANTEE"),
]


def test_r4_clean_synthetic_clone_passes_separate_os_verifier(tmp_path: Path) -> None:
    rdir = tmp_path / "clean_r4"
    rid, tsha = build_synthetic_valid_kh03_r4_run_dir(rdir)
    rc, out = run_kh03_r4_verifier_subprocess(rdir, rid, tsha)
    assert rc == 0, f"Clean R4 clone failed verifier (rc={rc}):\n{out}"
    assert '"status": "PASS"' in out


@pytest.mark.parametrize("mut_id,mut_fn,expected_token", KH03_R4_OWN_VERIFIER_MUTATIONS_SPECS, ids=[m[0] for m in KH03_R4_OWN_VERIFIER_MUTATIONS_SPECS])
def test_r4_independent_verifier_catches_all_18_mutations(
    tmp_path: Path, mut_id: str, mut_fn: Callable[[Path], None], expected_token: str
) -> None:
    rdir = tmp_path / f"mut_{mut_id}"
    rid, tsha = build_synthetic_valid_kh03_r4_run_dir(rdir)
    mut_fn(rdir)
    rc, out = run_kh03_r4_verifier_subprocess(rdir, rid, tsha)
    assert rc != 0, f"Mutation {mut_id} was NOT caught (rc=0)!\n{out}"
    assert expected_token in out, f"Mutation {mut_id} missing expected token {expected_token}:\n{out}"
