"""
aurum-desk/backend/tests/test_kh_03_verifier_mutations.py

KH-03.R1 Separate-OS Independent Verifier Mutation-Resistance Suite (14 Attacks):
Constructs a clean synthetic KH-03 run folder in `tmp_path` (NEVER touches sealed repo runs),
verifies exit code 0 on the clean clone, then mutates 14 distinct fields/files in isolated
clones and proves `kh_03_independent_verifier.py` exits non-zero with exact violation tokens!
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable, Dict, List, Tuple

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_fee_integrity_service import (
    EXPECTED_SIX_SCENARIOS_KH03,
    SCHEMA_VERSION_KH03,
    SPEC_VERSION_KH03,
    validate_kh03_fee_ledger_independently,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.kh_02_r2_c3_independent_verifier import _audit_memory_samples_c3
from tools.kh_03_independent_verifier import recompute_kh03_24_gates_and_verdicts


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_synthetic_valid_kh03_run_dir(run_dir: Path) -> Tuple[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)

    tsha = "f" * 40
    rid = f"20261123T120000Z_{tsha[:12]}"

    # 04: Streaming fee ledger across all 6 scenarios x 5 arms (1 closed LOSS episode each = 30 rows)
    arms = ("B2_H1", "B2_H123", "B2_H2", "B2_H3", "NC_NULL_TRIGGER")
    fee_gz = run_dir / "04_fee_per_episode_ledger.jsonl.gz"
    uids: List[str] = []
    rhashes: List[str] = []
    scenarios_05: Dict[str, Any] = {}
    with gzip.open(fee_gz, "wt", encoding="utf-8") as gz:
        for sc in EXPECTED_SIX_SCENARIOS_KH03:
            sc_arms: Dict[str, Any] = {}
            for ac in arms:
                uid = f"{sc}|{ac}|1791500000000|opp_{sc}_{ac}|0"
                row = {
                    "episode_uid": uid,
                    "scenario_id": sc,
                    "arm_code": ac,
                    "opportunity_id": f"opp_{sc}_{ac}",
                    "direction": "LONG",
                    "decision_timestamp_ms": 1791500000000,
                    "filled": True,
                    "outcome_label": "LOSS",
                    "exit_type": "LOSS",
                    "executed_quantity": 0.30,
                    "planned_net_risk_usd": 2.45,
                    "realized_gross_pnl_usd": -1.4430,
                    "entry_fee_usd": 0.5058,
                    "exit_fee_usd": 0.5052,
                    "total_fees_usd": 1.0110,
                    "accrued_open_entry_fee_usd": 0.0,
                    "realized_net_pnl_usd": -2.4540,
                    "realized_net_r": -1.0016,
                    "gross_fee_net_residual_usd": 0.0,
                }
                ln = json.dumps(row, sort_keys=True, separators=(",", ":"))
                gz.write(ln + "\n")
                uids.append(uid)
                rhashes.append(hashlib.sha256(ln.encode("utf-8")).hexdigest())
                sc_arms[ac] = {
                    "scheduled": 1,
                    "closed": 1,
                    "wins": 0,
                    "losses": 1,
                    "open_at_horizon": 0,
                    "data_gap_unresolved": 0,
                    "unfilled": 0,
                    "gross_pnl_usd": -1.4430,
                    "entry_fees_closed_usd": 0.5058,
                    "exit_fees_closed_usd": 0.5052,
                    "total_fees_usd": 1.0110,
                    "accrued_open_entry_fees_usd": 0.0,
                    "net_pnl_usd": -2.4540,
                    "expectancy_r": -1.0016,
                }
            scenarios_05[sc] = {"raw_r2_geometry_risk_eligible_count": 100, "arms": sc_arms}

    f05 = {
        "spec_version": SPEC_VERSION_KH03,
        "starts_from_full_raw_opportunity_stream": True,
        "conservation_verified_all_scenarios": True,
        "total_overlap_violations": 0,
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "C4_UPSTREAM_FEE_DISPLAY_ROOT_CAUSE_FIXED": True,
        "zero_fee_display_bug_hits_after_fix": 0,
        "linked_per_episode_fee_ledger": {
            "ledger_filename": "04_fee_per_episode_ledger.jsonl.gz",
            "ledger_file_sha256": _sha256_file(fee_gz),
            "total_episode_rows": len(uids),
            "episode_uids_sha256": canonical_hash(uids),
            "canonical_rows_sha256": canonical_hash(rhashes),
        },
        "scenarios": scenarios_05,
        "status": "PASS",
    }

    # Memory telemetry log
    mem_gz = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(mem_gz, "wt", encoding="utf-8") as mgz:
        for i in range(4):
            mgz.write(json.dumps({"ts_ms": 1791500000000 + i * 500, "available_ram_bytes": 4_000_000_000, "available_mib": 3814.7, "child_count": 2, "worker_pids": [101, 102], "disk_free_gib": 35.0}) + "\n")

    # JUnit XML (5580 synthetic testcases matching 1:1)
    n_tc = 5580
    c_ids = [f"backend/tests/test_kh_03_synth.py::test_case_{i:04d}" for i in range(n_tc)]
    xml_path = run_dir / "junit" / "SHARD_01.xml"
    tcs = "".join(f'<testcase classname="backend.tests.test_kh_03_synth" name="test_case_{i:04d}" time="0.001"/>' for i in range(n_tc))
    xml_path.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuite tests="{n_tc}" failures="0" errors="0" skipped="0">{tcs}</testsuite>', encoding="utf-8")

    f10: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "written_before_any_kh03_replay": True,
        "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
        "ACTIVATION_READY": "NO",
        "OOS_EVALUATIONS": 0,
        "status": "PASS",
    }
    f10["plan_frozen_sha256"] = canonical_hash(f10)

    docs: Dict[str, Dict[str, Any]] = {
        "00": {
            "spec_version": SPEC_VERSION_KH03,
            "run_id": rid,
            "branch": "feature/v17-9-2-structural-sl-tp-from-qa",
            "start_head_sha": tsha,
            "end_head_sha": tsha,
            "tested_executable_sha": tsha,
            "git_clean_at_start": True,
            "local_uncommitted_changes": False,
            "orchestrator_pid": -9999,
            "owner_disk_preflight_waiver": False,
            "strict_multi_mount_probe": {
                "can_run_heavy_stages": True,
                "owner_waiver_permitted": False,
                "probe_errors": [],
                "distinct_mounts": [{"free_gib": 35.0, "projected_post_reserve_gib": 34.0}],
            },
            "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
            "ACTIVATION_READY": "NO",
            "status": "PASS",
        },
        "01": {
            "status": "PASS",
            "locked_files_modified_count": 0,
            "forbidden_order_import_hits": [],
            "cost_assumptions_defaults_intact": True,
            "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
            "ACTIVATION_READY": "NO",
        },
        "02": {
            "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
            "canonical_dataset_sha256_match": True,
            "all_prior_sealed_artifacts_intact": True,
            "prior_sealed_artifacts_immutability": {"C4": {"match": True}},
            "status": "PASS",
        },
        "03": {"classification": "REAL_RED", "zero_fee_display_bug_cells_count": 29},
        "05": f05,
        "06": {
            "status": "PASS",
            "dev_contract_validation_status": "PASS",
            "ny_dst_validation": {"passed": True},
            "observed_l1_bbo_available": False,
            "historical_quote_mode": "MODELED_BID_ASK",
        },
        "07": {"status": "PASS", "embargo_minutes": 240, "OOS_EVALUATIONS": 0, "noncontamination_controls": {"all_passed": True}},
        "08": {
            "status": "PASS",
            "network_capture_enabled": False,
            "persistent_capture_active": False,
            "live_network_session_authorized": False,
            "live_network_capture_executed": False,
            "LIVE_NETWORK_VALIDATION": "NOT_RUN_BY_DESIGN",
            "real_prospective_snapshots_collected": 0,
            "c4_boundary_direct_mint_blocked": True,
            "c4_boundary_evil_subclass_blocked": True,
            "c4_boundary_monkeypatch_blocked": True,
        },
        "09": {
            "status": "PASS",
            "real_oos_escrow_integrity_valid": True,
            "real_oos_escrow_final_record_count": 0,
            "qa_fixture_refused_by_real_oos_escrow": True,
            "unanchored_worm_verified": False,
        },
        "10": f10,
        "11": {
            "status": "PASS",
            "future_mutation_invariance": {"passed": True, "mismatches_count": 0},
            "t01_to_t10_unswept_target_all_passed": True,
        },
        "12": {
            "status": "PASS",
            "preregistered_plan_sha256": f10["plan_frozen_sha256"],
            "all_arms_conserved_exact": True,
            "total_overlap_violations": 0,
        },
        "13": {
            "status": "PASS",
            "b0_vs_b2_cross_universe_status": "COMPARISON_NOT_IDENTIFIABLE",
            "arm_matching_reports": {"KH03_H1_MTF_UNSWEPT_ALIGN": {}, "KH03_H2_LOW_FRICTION_VOL_REGIME": {}, "KH03_H3_FILL_TIME_CAUSAL_RR_GATE": {}},
            "negative_control_summaries_1x": {"NC_NULL_TRIGGER": {}, "NC_PLACEBO_SHUFFLED_TRIGGER": {}, "NC_TIME_OFFSET_SHIFT": {}},
        },
        "14": {"status": "PASS", "zero_fee_display_bugs_count": 0, "total_overlap_violations": 0},
        "15": {
            "status": "PASS",
            "preregistered_plan_sha256": f10["plan_frozen_sha256"],
            "primary_arm_evaluations": {
                "KH03_H1_MTF_UNSWEPT_ALIGN": {"passes_preregistered_dev_advancement": False},
                "KH03_H2_LOW_FRICTION_VOL_REGIME": {"passes_preregistered_dev_advancement": False},
                "KH03_H3_FILL_TIME_CAUSAL_RR_GATE": {"passes_preregistered_dev_advancement": False},
            },
            "KH03_DEV_CANDIDATE_VERDICT": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE",
            "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
            "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
            "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE",
            "OOS_EVALUATIONS": 0,
            "ACTIVATION_READY": "NO",
            "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
        },
        "16": {
            "status": "PASS",
            "planned_cap_is_physical_guarantee": False,
            "realized_tail_loss_disclosure": "planned_net_risk_usd <= 2.50 USD is NOT a physical guarantee.",
        },
        "17": {
            "status": "PASS",
            "collected_count": n_tc,
            "executed_count": n_tc,
            "passed_count": n_tc,
            "skipped_count": 0,
            "mandatory_skipped_count": 0,
            "failed_count": 0,
            "error_count": 0,
            "junit_bijection_exact": True,
            "collection_manifest": {"run_id": rid, "tested_executable_sha": tsha, "collected_node_ids_count": n_tc, "collected_node_ids": c_ids},
            "shard_manifest": {
                "run_id": rid,
                "tested_executable_sha": tsha,
                "nonzero_exit_shards_count": 0,
                "shards": [
                    {
                        "shard_id": "SHARD_01",
                        "exit_code": 0,
                        "timed_out": False,
                        "reused": False,
                        "junit_xml_path": "junit/SHARD_01.xml",
                        "xml_sha256": _sha256_file(xml_path),
                        "scheduled_node_ids": c_ids,
                    }
                ],
            },
        },
        "18": {
            "status": "PASS",
            "pytest_controlled_subset_w1_w2_w4_repeat": {
                "all_4_runs_identical": True,
                "runs": [
                    {"mode_label": "w=1", "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64, "worker_pids": [1]},
                    {"mode_label": "w=2", "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64, "worker_pids": [2, 3]},
                    {"mode_label": "w=4", "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64, "worker_pids": [4, 5, 6, 7]},
                    {"mode_label": "w=1_repeat", "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64, "worker_pids": [8]},
                ],
            },
            "r2_science_slice_w1_w2_w4_repeat": {
                "all_4_runs_identical": True,
                "runs": [
                    {"mode_label": "w=1", "rows_count": 12062, "canonical_sorted_sha256": "2" * 64},
                    {"mode_label": "w=2", "rows_count": 12062, "canonical_sorted_sha256": "2" * 64},
                    {"mode_label": "w=4", "rows_count": 12062, "canonical_sorted_sha256": "2" * 64},
                    {"mode_label": "w=1_repeat", "rows_count": 12062, "canonical_sorted_sha256": "2" * 64},
                ],
            },
            "runtime_resource_telemetry": {
                "min_disk_free_gib": 35.0,
                "min_available_ram_mib": 3800.0,
                "max_descendant_children": 4,
                "orphan_children_after_cleanup": 0,
                "sentinel_temp_cleaned_up": True,
                "owner_disk_preflight_waiver": False,
            },
        },
        "19": {"status": "PASS", "inherited_c4_controls_count": 126, "new_kh03_controls_count": 24, "total_controls": 150, "unique_fault_ids": 150, "failed_controls": 0},
        "20": {
            "BASELINE_PARITY_VERDICT": "PASS",
            "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": True},
            "a1_population_count": 56710,
            "a1_mismatches_count": 0,
            "si8_population_count": 6375,
            "si8_mismatches_count": 0,
            "three_adapter_population_count": 246,
            "three_adapter_mismatches_count": 0,
            "d4_historical_closed_trades": 0,
        },
        "21": {"status": "PASS", "mutations_tested_count": 14, "mutations_failed_detection_count": 0},
        "23": {"run_id": rid, "tested_executable_sha": tsha, "publication_status": "PRE_PUSH_PENDING", "remote_readback_sha256_verified": False, "owner_disk_preflight_waiver": False},
    }

    mem_aud = _audit_memory_samples_c3(mem_gz)
    fee_aud = validate_kh03_fee_ledger_independently(fee_gz, f05)
    rec22 = recompute_kh03_24_gates_and_verdicts(docs=docs, mem_audit=mem_aud, fee_ledger_audit=fee_aud, junit_bijection_ok=True, for_final_verifier_manifest=False)
    docs["22"] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "run_id": rid,
        "tested_executable_sha": tsha,
        "overall_classification": rec22["overall_classification"],
        "gates_24": rec22["gates_24"],
        "verdicts": rec22["verdicts"],
    }

    fn_map = {
        "00": "00_source_branch_sha_and_resource_preflight.json",
        "01": "01_file_function_ownership_and_keep_as_is_ast_diff.json",
        "02": "02_prior_dataset_and_sealed_sha256_immutability.json",
        "03": "03_fee_root_bug_red_proof_and_diff.json",
        "05": "05_fee_full_universe_six_scenario_reconciliation.json",
        "06": "06_data_source_contract_and_quality_inventory.json",
        "07": "07_partition_freeze_pre_registration_and_noncontamination.json",
        "08": "08_public_collector_readiness_authorization_and_provenance.json",
        "09": "09_escrow_integrity_and_hash_linked_readiness.json",
        "10": "10_kh03_frozen_hypotheses_with_falsification_budget.json",
        "11": "11_causal_asof_mtf_and_future_mutation_tests.json",
        "12": "12_full_opportunity_conservation_and_candidate_replay.json",
        "13": "13_paired_null_matching_and_balance.json",
        "14": "14_net_economics_cost_1x_2x_3x_stress.json",
        "15": "15_purged_walkforward_bootstrap_multiplicity_power.json",
        "16": "16_risk_tail_gap_fill_time_and_fee_disclosure.json",
        "17": "17_full_regression_collected_executed_junit_bijection.json",
        "18": "18_w1_w2_w4_w1repeat_speed_hash_and_resource.json",
        "19": "19_all_faults_unique_red_green_receipts.json",
        "20": "20_full_A1_SI8_246_adapter_D4_parity.json",
        "21": "21_independent_verifier_mutation_resistance.json",
        "22": "22_orthogonal_gate_matrix_prepush.json",
        "23": "23_phase1_code_commit_push_receipt.json",
    }
    for k, fn in fn_map.items():
        (run_dir / fn).write_text(json.dumps(docs[k], indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "README_KH03_R1.md").write_text(f"# KH03 {rid}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH03_R1_VI.txt").write_text(f"KH03 {rid}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\n", encoding="utf-8")
    return rid, tsha


def run_kh03_verifier_subprocess(run_dir: Path, run_id: str, tested_sha: str) -> Tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    p = subprocess.run(
        [
            sys.executable,
            str(APP_ROOT / "tools" / "kh_03_independent_verifier.py"),
            "--run-dir", str(run_dir),
            "--expected-run-id", run_id,
            "--expected-tested-sha", tested_sha,
            "--skip-live-repo-pointers",
            "--no-write-manifest",
        ],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    return int(p.returncode), (p.stdout or "") + "\n" + (p.stderr or "")


def _mut_json(p: Path, fn: Callable[[Dict[str, Any]], None]) -> None:
    d = json.loads(p.read_text("utf-8"))
    fn(d)
    p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")


def _m01_zero_fee_cell(d: Path) -> None:
    _mut_json(d / "05_fee_full_universe_six_scenario_reconciliation.json", lambda o: o["scenarios"]["1X_BASE_0p0004"]["arms"]["B2_H1"].__setitem__("total_fees_usd", 0.0))


def _m02_forged_proven_edge(d: Path) -> None:
    _mut_json(d / "15_purged_walkforward_bootstrap_multiplicity_power.json", lambda o: o.__setitem__("RESEARCH_ECONOMIC_VERDICT", "PROVEN_EDGE"))


def _m03_nonzero_oos(d: Path) -> None:
    _mut_json(d / "15_purged_walkforward_bootstrap_multiplicity_power.json", lambda o: o.__setitem__("OOS_EVALUATIONS", 1))


def _m04_drop_fee_row(d: Path) -> None:
    p = d / "04_fee_per_episode_ledger.jsonl.gz"
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip()]
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        for ln in lines[:-1]:
            fh.write(ln)


def _m05_dup_fee_uid(d: Path) -> None:
    p = d / "04_fee_per_episode_ledger.jsonl.gz"
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip()]
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(ln)
        fh.write(lines[0])


def _m06_tamper_dataset_sha(d: Path) -> None:
    _mut_json(d / "02_prior_dataset_and_sealed_sha256_immutability.json", lambda o: o.__setitem__("canonical_dataset_sha256", "0" * 64))


def _m07_tamper_prereg_sha(d: Path) -> None:
    _mut_json(d / "10_kh03_frozen_hypotheses_with_falsification_budget.json", lambda o: o.__setitem__("plan_frozen_sha256", "a" * 64))


def _m08_forged_activation_yes(d: Path) -> None:
    _mut_json(d / "15_purged_walkforward_bootstrap_multiplicity_power.json", lambda o: o.__setitem__("ACTIVATION_READY", "YES"))


def _m09_tamper_junit_counts(d: Path) -> None:
    _mut_json(d / "17_full_regression_collected_executed_junit_bijection.json", lambda o: o.__setitem__("passed_count", 1))


def _m10_w4_hash_drift(d: Path) -> None:
    def _f(o: Dict[str, Any]) -> None:
        o["pytest_controlled_subset_w1_w2_w4_repeat"]["runs"][2]["canonical_sorted_outcome_sha256"] = "9" * 64
    _mut_json(d / "18_w1_w2_w4_w1repeat_speed_hash_and_resource.json", _f)


def _m11_false_remote_readback(d: Path) -> None:
    _mut_json(d / "23_phase1_code_commit_push_receipt.json", lambda o: o.__setitem__("publication_status", "PASS_REMOTE_VERIFIED"))


def _m12_unauth_live_network(d: Path) -> None:
    _mut_json(d / "08_public_collector_readiness_authorization_and_provenance.json", lambda o: o.__setitem__("live_network_capture_executed", True))


def _m13_ram_breach_log(d: Path) -> None:
    p = d / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(p, "wt", encoding="utf-8") as gz:
        gz.write(json.dumps({"ts_ms": 1791500000000, "available_ram_bytes": 100_000_000, "available_mib": 95.3, "child_count": 2, "worker_pids": [1, 2], "disk_free_gib": 35.0}) + "\n")


def _m14_disk_waiver_flag(d: Path) -> None:
    _mut_json(d / "00_source_branch_sha_and_resource_preflight.json", lambda o: o.__setitem__("owner_disk_preflight_waiver", True))


KH03_VERIFIER_MUTATIONS_SPECS: Tuple[Tuple[str, Callable[[Path], None], str], ...] = (
    ("M01_ZERO_FEE_CELL_WHILE_GROSS_MINUS_NET_GT_0", _m01_zero_fee_cell, "INDEPENDENT_FEE_LEDGER"),
    ("M02_FORGED_PROVEN_EDGE_VERDICT", _m02_forged_proven_edge, "FORGED_RESEARCH_ECONOMIC_VERDICT"),
    ("M03_NONZERO_OOS_EVALUATIONS", _m03_nonzero_oos, "NONZERO_OOS_EVALUATIONS_PEEK"),
    ("M04_DROPPED_FEE_LEDGER_ROW", _m04_drop_fee_row, "INDEPENDENT_FEE_LEDGER"),
    ("M05_DUPLICATE_FEE_LEDGER_UID", _m05_dup_fee_uid, "INDEPENDENT_FEE_LEDGER"),
    ("M06_TAMPERED_DATASET_SHA", _m06_tamper_dataset_sha, "CANONICAL_DATASET_SHA256_TAMPERED"),
    ("M07_TAMPERED_PREREG_PLAN_SHA", _m07_tamper_prereg_sha, "PREREG_PLAN_SHA256_TAMPERED"),
    ("M08_FORGED_ACTIVATION_READY_YES", _m08_forged_activation_yes, "FORGED_ACTIVATION_READY_ON"),
    ("M09_TAMPERED_JUNIT_COUNTS", _m09_tamper_junit_counts, "JUNIT_COUNTS_TAMPERED_VS_RAW_XML"),
    ("M10_W1_W4_HASH_DRIFT", _m10_w4_hash_drift, "W1_W2_W4_DETERMINISM_HASH_MISMATCH"),
    ("M11_FALSE_REMOTE_READBACK_CLAIM", _m11_false_remote_readback, "FALSE_REMOTE_READBACK_CLAIM"),
    ("M12_UNAUTHORIZED_LIVE_NETWORK_FLAG", _m12_unauth_live_network, "UNAUTHORIZED_LIVE_NETWORK"),
    ("M13_MEMORY_LOG_512MIB_BREACH", _m13_ram_breach_log, "MEMORY_TELEMETRY_BREACHED_512MIB_FLOOR"),
    ("M14_DISK_WAIVER_FLAG_SET", _m14_disk_waiver_flag, "UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN"),
)


def test_kh03_independent_os_verifier_clean_green_and_all_14_mutations_rejected(tmp_path: Path) -> None:
    base = tmp_path / "clean_kh03"
    rid, tsha = build_synthetic_valid_kh03_run_dir(base)
    rc0, out0 = run_kh03_verifier_subprocess(base, rid, tsha)
    assert rc0 == 0, f"Clean clone verifier failed: {out0}"

    for mid, mut_fn, tok in KH03_VERIFIER_MUTATIONS_SPECS:
        clone = tmp_path / f"mut_{mid}"
        shutil.copytree(base, clone)
        mut_fn(clone)
        rc, out = run_kh03_verifier_subprocess(clone, rid, tsha)
        assert rc != 0, f"Mutation {mid} was NOT rejected! Output:\n{out}"
        assert tok in out, f"Mutation {mid} missing expected token {tok!r}! Output:\n{out}"
