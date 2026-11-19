"""
backend/tests/test_kh_01b_r1_verifier_and_resources.py

Separate-OS-process adversarial tamper tests against `tools/kh_01b_r1_independent_verifier.py`
(G14) and baseline-aware disk / resource cleanup guard tests (G13).
"""
from __future__ import annotations

import copy
from dataclasses import asdict
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from services.kh_01a_r1_forensics_closure_service import build_dynamic_validated_gate_receipt_r1
from services.kh_01b_entry_unicorn_research_service import FILL_MODES_KH01B
from services.kh_01b_r1_entry_edge_discovery_service import (
    CANONICAL_DATASET_SHA256_KH01B_R1,
    MANDATORY_BRANCH_KH01B_R1,
    MANDATORY_KH01B_R1_ENGINEERING_GATES,
    SCHEMA_VERSION_KH01B_R1,
    SPEC_VERSION_KH01B_R1,
    evaluate_kh01b_r1_engineering_and_economic_gates_from_disk,
    pre_register_kh01b_r1_theses,
    render_kh01b_r1_readme_and_handover_vi,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import (
    write_json_durable_atomic,
    write_jsonl_gz_durable_atomic,
    write_text_durable_atomic,
)
from tools.run_kh_01b_r1_verification import (
    compute_kh01b_r1_disk_reserve_policy,
    verify_kh01b_r1_resource_cleanup,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFIER_R1 = REPO_ROOT / "aurum-desk/tools/kh_01b_r1_independent_verifier.py"


def _pass_val() -> dict:
    return {"status": "PASS", "violations": []}


def _blocked_acq_val() -> dict:
    return {"status": "BLOCKED_UNAVAILABLE", "violations": ["NETWORK_UNAVAILABLE"]}


def test_kh01b_r1_disk_reserve_policy_standard_and_below_20gib_baseline() -> None:
    # 1. Host starting >= 20.5 GiB enforces strict 20.0 GiB reserve
    pol_high = compute_kh01b_r1_disk_reserve_policy(int(35.0 * 1024**3))
    assert pol_high["can_proceed"] is True
    assert pol_high["acceptance_eligible"] is True
    assert pol_high["disk_safety_verdict"] == "PASS_STRICT_20GIB"
    assert pol_high["baseline_already_below_20gib"] is False
    assert pol_high["effective_enforced_reserve_gib"] == 20.0
    assert pol_high["lowered_reserve_without_approval"] is False

    # Dropping below 20.0 GiB on a 35 GiB baseline host MUST FAIL
    res_breach = verify_kh01b_r1_resource_cleanup(
        {"max_children_observed": 2, "minimum_free_disk_bytes": int(19.2 * 1024**3), "orphan_pids_at_cleanup_count": 0, "disk_reserve_breached": False},
        {"cleaned": True, "sentinel_verified": True},
        pol_high,
        orchestrator_pid=os.getpid(),
        post_cleanup_free_bytes=int(34.9 * 1024**3),
    )
    assert res_breach["status"] == "FAIL"
    assert res_breach["effective_disk_reserve_breached"] is True

    # 2. Strict fail-closed: 20.49 GiB, 18.5 GiB, 13.4667 GiB, and 8.0 GiB ALL block heavy execution!
    for low_gib in (20.49, 18.5, 13.4667, 8.0):
        pol_blocked = compute_kh01b_r1_disk_reserve_policy(int(low_gib * 1024**3))
        assert pol_blocked["can_proceed"] is False
        assert pol_blocked["acceptance_eligible"] is False
        assert pol_blocked["disk_safety_verdict"] == "BLOCKED_LOW_DISK"
        assert pol_blocked["enforced_reserve_gib"] == 20.0
        assert pol_blocked["lowered_reserve_without_approval"] is False
        assert "BLOCKED_LOW_DISK" in str(pol_blocked["abort_reason"])

        res_blocked = verify_kh01b_r1_resource_cleanup(
            {"max_children_observed": 2, "minimum_free_disk_bytes": int(low_gib * 1024**3), "orphan_pids_at_cleanup_count": 0, "disk_reserve_breached": False},
            {"cleaned": True, "sentinel_verified": True},
            pol_blocked,
            orchestrator_pid=os.getpid(),
            post_cleanup_free_bytes=int(low_gib * 1024**3),
        )
        assert res_blocked["status"] == "FAIL"

    # 3. Separate volumes: repo=35 GiB but temp=15 GiB MUST block heavy execution
    pol_sep = compute_kh01b_r1_disk_reserve_policy(
        int(35.0 * 1024**3),
        volume_free_bytes_by_label={"repo": int(35.0 * 1024**3), "output": int(35.0 * 1024**3), "temp": int(15.0 * 1024**3)},
    )
    assert pol_sep["can_proceed"] is False
    assert pol_sep["constraining_volume"] == "temp"
    assert pol_sep["disk_safety_verdict"] == "BLOCKED_LOW_DISK"

    # 4. Explicit UNVERIFIED_DIAGNOSTIC_ONLY mode is never acceptance-eligible and never passes resource gate
    pol_diag = compute_kh01b_r1_disk_reserve_policy(int(16.0 * 1024**3), mode="UNVERIFIED_DIAGNOSTIC_ONLY")
    assert pol_diag["acceptance_eligible"] is False
    assert pol_diag["disk_safety_verdict"] == "UNVERIFIED_DIAGNOSTIC_ONLY_NEVER_PASS"
    res_diag = verify_kh01b_r1_resource_cleanup(
        {"max_children_observed": 1, "minimum_free_disk_bytes": int(15.5 * 1024**3), "orphan_pids_at_cleanup_count": 0, "disk_reserve_breached": False},
        {"cleaned": True, "sentinel_verified": True},
        pol_diag,
        orchestrator_pid=os.getpid(),
        post_cleanup_free_bytes=int(15.8 * 1024**3),
    )
    assert res_diag["status"] == "FAIL"


def _build_valid_minimal_r1_bundle(base_dir: Path) -> Path:
    run_id = "TEST_KH01B_R1_RUN"
    sha = "0123456789abcdef0123456789abcdef01234567"
    r = base_dir / "ok_r1"
    for sub in ("ledgers", "receipts", "junit", "logs"):
        (r / sub).mkdir(parents=True)

    nodes = [
        "backend/tests/test_kh_01b_r1_causal_and_forensics.py::test_a",
        "backend/tests/test_kh_01b_entry_unicorn_research.py::test_b",
        "backend/tests/test_kh_01a_data_and_verification_foundation.py::test_c",
        "backend/tests/test_v17_9_3_1_2_7_causal_truth.py::test_d",
    ]
    tcs_xml = "".join(
        f'<testcase classname="{n.split("::")[0].replace("/", ".").removesuffix(".py")}" name="{n.split("::")[1]}" time="0.01"/>'
        for n in nodes
    )
    xml_p = r / "junit/SHARD_01.xml"
    xml_p.write_text(
        f'<?xml version="1.0" encoding="utf-8"?><testsuite name="pytest" tests="{len(nodes)}" errors="0" failures="0" skipped="0">{tcs_xml}</testsuite>',
        encoding="utf-8",
    )
    xml_sha = hashlib.sha256(xml_p.read_bytes()).hexdigest()
    xml_mtime = int(xml_p.stat().st_mtime_ns)

    write_json_durable_atomic(r / "00_preflight_branch_source_dataset_invariants.json", {
        "spec_version": SPEC_VERSION_KH01B_R1, "schema_version": SCHEMA_VERSION_KH01B_R1,
        "run_id": run_id, "branch": MANDATORY_BRANCH_KH01B_R1, "tested_executable_sha": sha,
        "orchestrator_pid": -999, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01B_R1,
        "source_truth": {"verification_passed": True, "a1_candidate_count": 56710, "si8_discovery_count": 6375, "total_candidate_universe_count": 63085},
        "source_tree_unchanged_during_run": True, "prior_artifacts_unchanged_during_run": True,
    })
    cg_doc = {"existing_production_or_prior_kh_files_modified_count": 0}
    write_json_durable_atomic(r / "01_function_callgraph_keep_as_is_and_no_production_change.json", cg_doc)
    write_json_durable_atomic(r / "01_function_to_responsibility_map.json", cg_doc)

    m_obj = pre_register_kh01b_r1_theses(CANONICAL_DATASET_SHA256_KH01B_R1, "cfg_sha", sha, e03_completeness_ratio=0.99)
    write_json_durable_atomic(r / "02_frozen_hypothesis_budget_and_expected_falsification.json", {
        **asdict(m_obj), "run_id": run_id, "tested_executable_sha": sha,
    })

    cb_row = {
        "opportunity_id": "opp_cb_1", "decision_timestamp_ms": 1_792_000_000_000,
        "evidence_completeness": "PARTIAL",
        "PREDICTIVE_PRE_ENTRY": {"max_known_at_ms": 1_792_000_000_000},
        "DESCRIPTIVE_EX_POST": {"filled": True, "outcome_label": "LOSS"},
    }
    cb_row["row_sha256"] = canonical_hash(cb_row)
    write_jsonl_gz_durable_atomic(r / "ledgers/kh01b_r1_si0_242_casebook.jsonl.gz", [cb_row])
    write_json_durable_atomic(r / "03_si0_242_episode_preentry_vs_expost_loss_attribution.json", {
        "scheduled_total": 1, "all_rows_partial_reconstructed_honesty": {"upgraded_to_fake_persisted_context_count": 0},
    })
    write_json_durable_atomic(r / "04_model_direction_session_regime_cost_decomposition.json", {
        "parent_population_conservation": {"si0_closed_total": 166, "stratum_closed_sum_model": 166, "stratum_closed_sum_direction": 166, "stratum_closed_sum_regime": 166},
        "overall_gross_vs_fee_decomposition": {"gross_pnl_usd": -142.7836, "gross_expectancy_r": -0.3498, "total_fees_usd": 203.1135, "fee_drag_r": 0.4976, "net_pnl_usd": -345.8971, "net_expectancy_r": -0.8474},
    })
    write_json_durable_atomic(r / "05_source_evidence_missingness_causal_regime_quality.json", {
        "a1_frames_evaluated_count": 56710, "future_known_at_or_bar_close_violations_count": 0, "invalid_frames_count": 0,
    })
    write_json_durable_atomic(r / "06_extended_dev_dataset_provenance_gap_and_quote_capability.json", {
        "DATA_ACQUISITION_VERDICT": "BLOCKED_UNAVAILABLE",
        "canonical_reused_dataset": {"untouched_bytes_verified": True},
    })
    write_json_durable_atomic(r / "07_future_oos_sealed_escrow_registry_no_evaluation.json", {
        "status": "PASS", "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA", "strategy_evaluation_count_total": 0,
    })

    arms_08, arms_09, arms_10 = {}, {}, {}
    ep_rows, flt_rows = [], []
    for sc in ("E00", "E01", "E02", "E03", "E04", "E05", "E06"):
        zero_arm = (sc == "E06")
        fr_d = {
            "short_code": sc, "variant_id": f"KH01B_R1-{sc}", "ontology": "RESEARCH",
            "population_total": 56710, "eligible_candidates_count": 0 if zero_arm else 100,
            "rejected_candidates_count": 56710 if zero_arm else 56610,
            "max_known_at_minus_decision_ms": 0,
        }
        fr_d["row_sha256"] = canonical_hash(fr_d)
        flt_rows.append(fr_d)
        arms_08[sc] = {"eligible_candidates_count": fr_d["eligible_candidates_count"], "rejected_candidates_count": fr_d["rejected_candidates_count"]}

        fm_sums = {}
        for fm in FILL_MODES_KH01B:
            if not zero_arm:
                ep_d = {
                    "short_code": sc, "variant_id": f"KH01B_R1-{sc}", "ontology": "RESEARCH", "fill_mode": fm,
                    "opportunity_id": f"opp_{sc}_1", "decision_timestamp_ms": 1_792_000_000_000,
                    "known_at_ms": 1_792_000_000_000, "release_ts_ms": 1_792_000_600_000,
                    "filled": True, "outcome_label": "LOSS", "planned_net_risk_usd": 2.25,
                    "realized_gross_pnl_usd": -2.0, "total_fees_usd": 0.25, "realized_net_pnl_usd": -2.25,
                    "realized_net_r": -1.0, "is_synthetic_fixture": False,
                }
                ep_d["row_sha256"] = canonical_hash(ep_d)
                ep_rows.append(ep_d)
                fm_sums[fm] = {"closed_trades_count": 1, "total_realized_net_usd": -2.25}
            else:
                fm_sums[fm] = {"closed_trades_count": 0, "total_realized_net_usd": 0.0}

        arms_09[sc] = {"fill_mode_summaries": fm_sums}
        arms_10[sc] = {
            "variant_id": f"KH01B_R1-{sc}", "short_code": sc,
            "scheduled_nb": 0 if zero_arm else 1, "closed_trades_nb": 0 if zero_arm else 1,
            "gross_pnl_1x_nb_usd": 0.0 if zero_arm else -2.0, "total_fees_1x_nb_usd": 0.0 if zero_arm else 0.25,
            "net_pnl_1x_nb_usd": 0.0 if zero_arm else -2.25,
            "expectancy_1x_nb_r": None if zero_arm else -1.0,
            "stress_2x_all_cost_net_pnl_usd": 0.0 if zero_arm else -2.75,
            "stress_3x_all_cost_net_pnl_usd": 0.0 if zero_arm else -3.25,
            "cluster_bootstrap_95ci_expectancy_r": None,
            "research_verdict": "NO_VALID_SETUPS" if zero_arm else "NO_CANDIDATE",
            "qualifies_for_kh01c": False,
        }

    write_jsonl_gz_durable_atomic(r / "ledgers/kh01b_r1_all_variants_episode_ledgers.jsonl.gz", ep_rows)
    write_jsonl_gz_durable_atomic(r / "ledgers/kh01b_r1_candidate_filter_decisions_summary.jsonl.gz", flt_rows)
    write_json_durable_atomic(r / "08_E00_E06_variant_conservation_and_rejection_reason_ledger.json", {"arms_conservation": arms_08})
    write_json_durable_atomic(r / "09_matched_baseline_and_chronological_1pos_3fill_economics.json", {
        "one_position_no_overlap_verified_all_arms": True, "risk_cap_violations_count": 0, "arms_summary": arms_09,
    })
    write_json_durable_atomic(r / "10_cost_stress_cluster_bootstrap_reused_purged_folds.json", {
        "status": "PASS", "research_hypothesis_verdict": "NO_DEFENSIBLE_EDGE",
        "kh01c_advancement_verdict": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE",
        "qualified_kh01c_candidates": [], "arms_economics": arms_10,
    })
    write_json_durable_atomic(r / "11_D4_strict_unchanged_and_unicorn_adjacent_isolation.json", {
        "ontology_isolation_verified": True, "d4_historical_closed_trades": 0, "d4_historical_expectancy_r": None,
        "adjacent_b10_a1_eligible_count": 6874, "adjacent_counted_toward_d4_count": 0,
        "detector_unit_fixture_validation": {"status": "PASS", "passed_cases": 25, "total_cases": 25},
    })
    write_json_durable_atomic(r / "12_full_regression_junit_shard_exits_bijection.json", {
        "collection_manifest": {"run_id": run_id, "tested_executable_sha": sha, "collect_exit_code": 0, "collected_node_ids": nodes},
        "shard_manifest": {
            "run_id": run_id, "tested_executable_sha": sha, "run_start_epoch_ns": xml_mtime - 1000, "nonzero_exit_shards_count": 0,
            "shards": [{
                "shard_id": "SHARD_01", "shard_name": "SHARD_01", "run_id": run_id, "tested_executable_sha": sha,
                "pid": 12345, "reused": False, "reused_from_prior_run": False, "exit_code": 0, "timed_out": False,
                "scheduled_node_ids": nodes, "junit_xml_path": "junit/SHARD_01.xml",
                "xml_sha256": xml_sha, "xml_mtime_ns": xml_mtime, "junit_sha256": xml_sha, "junit_mtime_ns": xml_mtime,
            }],
        },
        "independent_bijection_verification": {"status": "PASS", "counters": {"collected": 4, "executed": 4, "passed": 4, "skipped": 0, "failed": 0, "errors": 0}},
    })
    write_json_durable_atomic(r / "13_async_workers_w1_w2_w4_repeat_speed_determinism.json", {
        "pytest_subset_w1_w2_w4_repeat_identical": True, "r1_variant_slice_w1_w2_w4_repeat_identical": True,
        "measured_speedup_w4_vs_w1": 2.4, "speedup_truth_label": "MEANINGFUL_PARALLEL_SPEEDUP",
    })
    write_json_durable_atomic(r / "14_disk_ram_cpu_pid_atomic_write_cleanup.json", {
        "max_children_observed": 2, "orphan_pids_at_cleanup_count": 0, "preflight_baseline_free_gib": 35.0,
        "baseline_already_below_20gib": False, "effective_enforced_reserve_gib": 20.0,
        "hard_abort_floor_gib": 20.0, "min_free_disk_gb_observed": 34.5,
        "effective_disk_reserve_breached": False, "temp_dirs_cleaned_with_sentinel_check": True,
    })
    write_json_durable_atomic(r / "15_real_fault_injection_red_green_mutations.json", {
        "status": "PASS", "passed_mutations": 24, "total_mutations": 24, "failed_mutations": 0,
    })
    write_json_durable_atomic(r / "16_full_baseline_decision_trade_action_and_adapter_parity.json", {
        "status": "PASS", "e00_vs_si0_exact_match": True, "a1_population_denominator": 56710, "a1_evaluated_count": 56710,
        "a1_mismatches_count": 0, "three_adapter_episodes_denominator": 246, "three_adapter_mismatches_count": 0,
        "tampered_fingerprint_negative_control_detected": True, "fake_activation_config_rejected": True,
        "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
    })

    target_map = {
        "G01": "00_preflight_branch_source_dataset_invariants.json",
        "G02": "00_preflight_branch_source_dataset_invariants.json",
        "G03": "02_frozen_hypothesis_budget_and_expected_falsification.json",
        "G04": "05_source_evidence_missingness_causal_regime_quality.json",
        "G05": "15_real_fault_injection_red_green_mutations.json",
        "G06": "04_model_direction_session_regime_cost_decomposition.json",
        "G07": "11_D4_strict_unchanged_and_unicorn_adjacent_isolation.json",
        "G08": "09_matched_baseline_and_chronological_1pos_3fill_economics.json",
        "G09": "16_full_baseline_decision_trade_action_and_adapter_parity.json",
        "G10": "16_full_baseline_decision_trade_action_and_adapter_parity.json",
        "G11": "12_full_regression_junit_shard_exits_bijection.json",
        "G12": "13_async_workers_w1_w2_w4_repeat_speed_determinism.json",
        "G13": "14_disk_ram_cpu_pid_atomic_write_cleanup.json",
        "G14": "00_preflight_branch_source_dataset_invariants.json",
        "G16": "07_future_oos_sealed_escrow_registry_no_evaluation.json",
        "G17": "10_cost_stress_cluster_bootstrap_reused_purged_folds.json",
    }
    for gid in MANDATORY_KH01B_R1_ENGINEERING_GATES:
        build_dynamic_validated_gate_receipt_r1(
            gate_id=gid, run_dir=r, run_id=run_id, tested_sha=sha,
            target_rel_path=target_map[gid], validator_fn=_pass_val, write_json_fn=write_json_durable_atomic,
        )
    build_dynamic_validated_gate_receipt_r1(
        gate_id="G15", run_dir=r, run_id=run_id, tested_sha=sha,
        target_rel_path="06_extended_dev_dataset_provenance_gap_and_quote_capability.json",
        validator_fn=_blocked_acq_val, write_json_fn=write_json_durable_atomic,
    )

    v18 = evaluate_kh01b_r1_engineering_and_economic_gates_from_disk(r, run_id, sha)
    write_json_durable_atomic(r / "18_final_orthogonal_engineering_economics_data_oos_verdicts.json", v18)
    rm, ho = render_kh01b_r1_readme_and_handover_vi(
        run_id=run_id, branch=MANDATORY_BRANCH_KH01B_R1, starting_head_sha=sha, tested_sha=sha,
        dataset_sha=CANONICAL_DATASET_SHA256_KH01B_R1,
        manifest_02=json.loads((r / "02_frozen_hypothesis_budget_and_expected_falsification.json").read_text("utf-8")),
        decomp_04=json.loads((r / "04_model_direction_session_regime_cost_decomposition.json").read_text("utf-8")),
        data_06=json.loads((r / "06_extended_dev_dataset_provenance_gap_and_quote_capability.json").read_text("utf-8")),
        escrow_07=json.loads((r / "07_future_oos_sealed_escrow_registry_no_evaluation.json").read_text("utf-8")),
        conservation_08=json.loads((r / "08_E00_E06_variant_conservation_and_rejection_reason_ledger.json").read_text("utf-8")),
        economics_10=json.loads((r / "10_cost_stress_cluster_bootstrap_reused_purged_folds.json").read_text("utf-8")),
        d4_11=json.loads((r / "11_D4_strict_unchanged_and_unicorn_adjacent_isolation.json").read_text("utf-8")),
        junit_12=json.loads((r / "12_full_regression_junit_shard_exits_bijection.json").read_text("utf-8")),
        workers_13=json.loads((r / "13_async_workers_w1_w2_w4_repeat_speed_determinism.json").read_text("utf-8")),
        resource_14=json.loads((r / "14_disk_ram_cpu_pid_atomic_write_cleanup.json").read_text("utf-8")),
        faults_15=json.loads((r / "15_real_fault_injection_red_green_mutations.json").read_text("utf-8")),
        parity_16=json.loads((r / "16_full_baseline_decision_trade_action_and_adapter_parity.json").read_text("utf-8")),
        verdicts_18=v18,
    )
    write_text_durable_atomic(r / "README_KH01B_R1.md", rm)
    write_text_durable_atomic(r / "HANDOVER_KH01B_R1_VI.txt", ho)
    return r


def test_kh01b_r1_subproc_verifier_green_and_8_adversarial_tampers(tmp_path: Path) -> None:
    ok_dir = _build_valid_minimal_r1_bundle(tmp_path)

    def _run_v(d: Path) -> tuple[int, dict]:
        p = subprocess.run(
            [sys.executable, str(VERIFIER_R1), "--run-dir", str(d), "--skip-git-freeze-check"],
            capture_output=True, text=True,
        )
        return p.returncode, json.loads(p.stdout)

    # GREEN control
    rc0, out0 = _run_v(ok_dir)
    assert rc0 == 0 and out0["independent_verifier_status"] == "PASS", f"GREEN failed: {out0}"

    rows = [json.loads(ln) for ln in gzip.open(ok_dir / "ledgers/kh01b_r1_all_variants_episode_ledgers.jsonl.gz", "rt", encoding="utf-8")]

    # 1. Truncated gzip ledger -> FAIL
    d1 = tmp_path / "t1"
    shutil.copytree(ok_dir, d1)
    (d1 / "ledgers/kh01b_r1_all_variants_episode_ledgers.jsonl.gz").write_bytes(b"\x1f\x8b\x08\x00truncated")
    rc1, out1 = _run_v(d1)
    assert rc1 != 0 and any("GZIP_CORRUPTION" in v for v in out1["violations"])

    # 2. Swapped row hash in episode ledger -> FAIL
    d2 = tmp_path / "t2"
    shutil.copytree(ok_dir, d2)
    r2 = copy.deepcopy(rows)
    r2[0]["row_sha256"] = "0" * 64
    write_jsonl_gz_durable_atomic(d2 / "ledgers/kh01b_r1_all_variants_episode_ledgers.jsonl.gz", r2)
    rc2, out2 = _run_v(d2)
    assert rc2 != 0 and any("EPISODE_LEDGER_HASH_TAMPERED" in v for v in out2["violations"])

    # 3. Edited negative PnL to positive in 10_cost_stress -> FAIL
    d3 = tmp_path / "t3"
    shutil.copytree(ok_dir, d3)
    f10 = json.loads((d3 / "10_cost_stress_cluster_bootstrap_reused_purged_folds.json").read_text("utf-8"))
    f10["arms_economics"]["E01"]["net_pnl_1x_nb_usd"] = +55.0
    write_json_durable_atomic(d3 / "10_cost_stress_cluster_bootstrap_reused_purged_folds.json", f10)
    rc3, out3 = _run_v(d3)
    assert rc3 != 0 and any("NET_PNL_TAMPERED_IN_10" in v for v in out3["violations"])

    # 4. Zero-trade arm given expectancy = 0.0 instead of None -> FAIL
    d4 = tmp_path / "t4"
    shutil.copytree(ok_dir, d4)
    f10_4 = json.loads((d4 / "10_cost_stress_cluster_bootstrap_reused_purged_folds.json").read_text("utf-8"))
    f10_4["arms_economics"]["E06"]["expectancy_1x_nb_r"] = 0.0
    write_json_durable_atomic(d4 / "10_cost_stress_cluster_bootstrap_reused_purged_folds.json", f10_4)
    rc4, out4 = _run_v(d4)
    assert rc4 != 0 and any("ZERO_TRADES_NON_NULL_EXPECTANCY" in v for v in out4["violations"])

    # 5. Future known_at_ms in episode ledger -> FAIL
    d5 = tmp_path / "t5"
    shutil.copytree(ok_dir, d5)
    r5 = copy.deepcopy(rows)
    r5[0]["known_at_ms"] = int(r5[0]["decision_timestamp_ms"]) + 60_000
    r5[0].pop("row_sha256", None)
    r5[0]["row_sha256"] = canonical_hash(r5[0])
    write_jsonl_gz_durable_atomic(d5 / "ledgers/kh01b_r1_all_variants_episode_ledgers.jsonl.gz", r5)
    rc5, out5 = _run_v(d5)
    assert rc5 != 0 and any("EPISODE_FUTURE_KNOWN_AT" in v for v in out5["violations"])

    # 6. Synthetic fixture trade injected into historical episode ledger -> FAIL
    d6 = tmp_path / "t6"
    shutil.copytree(ok_dir, d6)
    r6 = copy.deepcopy(rows)
    r6[0]["is_synthetic_fixture"] = True
    r6[0].pop("row_sha256", None)
    r6[0]["row_sha256"] = canonical_hash(r6[0])
    write_jsonl_gz_durable_atomic(d6 / "ledgers/kh01b_r1_all_variants_episode_ledgers.jsonl.gz", r6)
    rc6, out6 = _run_v(d6)
    assert rc6 != 0 and any("SYNTHETIC_TRADE_INJECTED" in v for v in out6["violations"])

    # 7. Counterfeit Fresh OOS PASS -> FAIL
    d7 = tmp_path / "t7"
    shutil.copytree(ok_dir, d7)
    write_json_durable_atomic(d7 / "07_future_oos_sealed_escrow_registry_no_evaluation.json", {
        "status": "PASS", "FRESH_OOS_VERDICT": "PASS_FINANCIAL_EDGE", "strategy_evaluation_count_total": 1,
    })
    rc7, out7 = _run_v(d7)
    assert rc7 != 0 and any("ILLEGAL_FRESH_OOS_VERDICT" in v for v in out7["violations"])

    # 8. Deleted mandatory receipt G08 -> FAIL
    d8 = tmp_path / "t8"
    shutil.copytree(ok_dir, d8)
    (d8 / "receipts/G08.json").unlink()
    rc8, out8 = _run_v(d8)
    assert rc8 != 0 and any("MISSING_MANDATORY_GATE_RECEIPT" in v for v in out8["violations"])
