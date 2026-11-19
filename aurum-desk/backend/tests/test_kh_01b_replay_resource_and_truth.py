"""
backend/tests/test_kh_01b_replay_resource_and_truth.py

Replay accounting, 2x cost flip, 20 GiB resource cleanup guard, and separate-OS-process
adversarial tamper tests against `tools/kh_01b_independent_verifier.py` (§14).
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from domain_calculator import CostAssumptions
from services.kh_01a_r1_forensics_closure_service import build_dynamic_validated_gate_receipt_r1
from services.kh_01b_entry_unicorn_research_service import (
    CANONICAL_DATASET_SHA256_KH01B,
    FILL_MODES_KH01B,
    MANDATORY_BRANCH_KH01B,
    MANDATORY_KH01B_TECHNICAL_GATES,
    SCHEMA_VERSION_KH01B,
    SPEC_VERSION_KH01B,
    VariantReplayBundle,
    _summarize_episode_list_kh01b,
    compute_kh01b_research_economics_and_uncertainty,
    evaluate_kh01b_orthogonal_gates_from_disk,
    pre_register_kh01b_hypotheses,
    render_kh01b_readme_handover_vi,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import (
    write_json_durable_atomic,
    write_jsonl_gz_durable_atomic,
    write_text_durable_atomic,
)
from tools.run_kh_01b_verification import verify_resource_cleanup

REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFIER_KH01B = REPO_ROOT / "aurum-desk/tools/kh_01b_independent_verifier.py"


def _pass_validator() -> dict:
    return {"status": "PASS", "violations": []}


def _blocked_oos_validator() -> dict:
    return {"status": "BLOCKED_NO_UNTOUCHED_DATA", "violations": ["NO_UNTOUCHED_POST_FREEZE_EXCHANGE_DATASET_AVAILABLE"]}


def _pending_pub_validator() -> dict:
    return {"status": "PENDING_REMOTE_PUSH", "violations": ["AWAITING_POST_PUSH_REMOTE_BLOB_READBACK"]}


def test_kh01b_2x_cost_flip_and_resource_20gb_guard() -> None:
    ts0 = 1_792_000_000_000
    eps_1x = [
        {
            "opportunity_id": f"op_{i}",
            "filled": True,
            "outcome_label": "WIN",
            "realized_gross_pnl_usd": 0.50,
            "total_fees_usd": 0.30,
            "realized_net_pnl_usd": 0.20,
            "realized_net_r": 0.08,
            "planned_net_risk_usd": 2.50,
            "decision_timestamp_ms": ts0 + i * 86_400_000,
            "exit_ts_ms": ts0 + i * 86_400_000 + 600_000,
            "setup_episode_id": f"ep_{i}",
        }
        for i in range(36)
    ]
    eps_sp2 = [{**e, "realized_gross_pnl_usd": 0.25, "realized_net_pnl_usd": -0.05} for e in eps_1x]
    bnd = VariantReplayBundle(
        variant_id="KH01B-B01-H4-VERIFIED-ALIGNED-ONLY",
        short_code="B01",
        ontology="OTHER_CAUSAL_FILTER_VARIANTS",
        axis_type="SINGLE_AXIS_ENTRY_FILTER",
        population_name="TEST",
        population_total=100,
        eligible_candidates_count=36,
        rejected_candidates_count=64,
        missing_evidence_rejected_count=20,
        proven_false_rejected_count=44,
        invalid_evidence_rejected_count=0,
        plausible_eligibility_lower_bound=36,
        plausible_eligibility_upper_bound=56,
        rejection_reasons_counts={"HTF_H4_OPPOSITE": 44, "HTF_H4_UNKNOWN_MISSING": 20},
        post_arbitration_timestamps_selected=36,
        arbitration_suppressed_ties_count=0,
        arbitration_winner_substitutions_vs_b00=0,
        materialized_accepted_count=36,
        materialized_modified_tp_count=0,
        fill_mode_summaries={
            "NEXT_BAR_OPEN_BID_ASK": _summarize_episode_list_kh01b(eps_1x, 0),
            "LEGACY_CLOSE_FILL": _summarize_episode_list_kh01b(eps_1x, 0),
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": _summarize_episode_list_kh01b(eps_sp2, 0),
        },
        primary_nb_episodes=eps_1x,
        all_mode_episodes={"NEXT_BAR_OPEN_BID_ASK": eps_1x, "LEGACY_CLOSE_FILL": eps_1x, "STRESS_WIDER_SPREAD_SLIPPAGE_2X": eps_sp2},
        matched_b00_242_counterfactual={"paired_net_pnl_delta_vs_b00_usd": 7.20},
        si8_separate_diagnostic=None,
        bundle_sha256="b01_test",
    )
    ec = compute_kh01b_research_economics_and_uncertainty(
        bnd, CostAssumptions(), [(ts0 - 86_400_000, ts0 + 10 * 86_400_000, ts0 + 10 * 86_400_000 + 180 * 60_000, ts0 + 40 * 86_400_000)]
    )
    assert ec.net_pnl_1x_nb_usd == 7.20
    assert ec.stress_2x_all_cost_net_pnl_usd < 0.0
    assert ec.selection_outcome == "RESEARCH_NEGATIVE_EDGE"
    assert ec.activation_ready == "NO"

    # Resource 20 GiB guard fails when min_free_disk_gb < 20.0
    bad_res = verify_resource_cleanup(
        {"max_children_observed": 2, "min_free_disk_gb_observed": 18.5, "orphan_pids_at_cleanup_count": 0, "disk_reserve_breached": False},
        {"cleaned": True, "sentinel_verified": True},
        orchestrator_pid=os.getpid(),
    )
    assert bad_res["status"] == "FAIL"
    assert bad_res["disk_reserve_20gb_breached"] is True


def _build_valid_minimal_kh01b_bundle(base_dir: Path) -> Path:
    run_id = "TEST_KH01B_RUN"
    sha = "0123456789abcdef0123456789abcdef01234567"
    r = base_dir / "ok"
    (r / "ledgers").mkdir(parents=True)
    (r / "receipts").mkdir(parents=True)
    (r / "junit").mkdir(parents=True)
    (r / "logs").mkdir(parents=True)

    xml_p = r / "junit/SHARD_01.xml"
    xml_p.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuite name="pytest" tests="1" errors="0" failures="0" skipped="0">'
        '<testcase classname="backend.tests.test_kh_01b_entry_unicorn_research" name="test_sample" time="0.01"/></testsuite>',
        encoding="utf-8",
    )
    xml_sha = canonical_hash(xml_p.read_text(encoding="utf-8"))
    import hashlib
    xml_sha = hashlib.sha256(xml_p.read_bytes()).hexdigest()
    xml_mtime = int(xml_p.stat().st_mtime_ns)

    write_json_durable_atomic(r / "00_preflight_source_config_dataset_freeze.json", {
        "spec_version": SPEC_VERSION_KH01B, "schema_version": SCHEMA_VERSION_KH01B, "run_id": run_id,
        "branch": MANDATORY_BRANCH_KH01B, "tested_executable_sha": sha, "orchestrator_pid": -999,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01B,
        "source_tree_unchanged_during_run": True, "prior_artifacts_unchanged_during_run": True,
    })
    write_json_durable_atomic(r / "01_function_callgraph_preentry_evidence_and_keep_as_is.json", {
        "macro_call_site_ast_audit": {"invalid_production_call_sites_count": 0},
    })
    m_obj = pre_register_kh01b_hypotheses(CANONICAL_DATASET_SHA256_KH01B, "cfg", sha)
    from dataclasses import asdict
    write_json_durable_atomic(r / "02_pre_registered_hypothesis_manifest_and_search_budget.json", {
        **asdict(m_obj), "run_id": run_id, "tested_executable_sha": sha,
    })
    write_json_durable_atomic(r / "03_causal_feature_sources_unknown_invalid_coverage.json", {
        "a1_frames_evaluated_count": 10, "future_bar_or_known_at_violations_count": 0, "invalid_evidence_frames_count": 0,
    })
    write_json_durable_atomic(r / "04_fixed_population_ids_and_match_conservation.json", {
        "baseline_verification_passed": True, "duplicate_opportunity_ids_count": 0,
        "a1_candidate_count": 10, "si8_discovery_count": 2, "total_candidate_universe_count": 12,
    })
    for fn, mid in (
        ("05_htf_single_axis_B01_B02_B03.json", "HTF"),
        ("06_target_source_tf_horizon_B04_B11_and_267_controls.json", "TGT"),
        ("07_macro_B05_calendar_DST_unverified_contract.json", "MAC"),
        ("08_sweep_mss_displacement_B06_B07_B08_evidence.json", "SWP"),
    ):
        write_json_durable_atomic(r / fn, {"module_id": mid, "status": "PASS"})

    write_json_durable_atomic(r / "09_strict_D4_and_adjacent_B09_B10_detector_funnel.json", {
        "ontology_isolation_verified": True, "b10_counted_toward_d4": False,
        "d4_stage_counts_monotonically_non_increasing": True,
        "b09_strict_d4_closed_trades": 0, "b10_adjacent_closed_trades": 1, "d4_63085_accepted_count": 0,
        "detector_fixture_validation": {"status": "PASS", "passed_cases": 25, "total_cases": 25, "positive_strict_unicorn_fixtures_passed": 1, "synthetic_counted_as_historical_trades": 0},
    })

    ep_rows = []
    flt_rows = []
    vs_10 = {}
    ec_11 = {}
    for idx in range(13):
        sc = f"B{idx:02d}"
        ont = "STRICT_D4_TRUE_UNICORN" if sc == "B09" else ("ADJACENT_FVG_SWEEP_MSS" if sc == "B10" else ("TP_ONLY_RESEARCH" if sc == "B11" else ("BASELINE_CONTROL" if sc == "B00" else "OTHER_CAUSAL_FILTER_VARIANTS")))
        fr_d = {
            "short_code": sc, "variant_id": f"KH01B-{sc}", "ontology": ont, "counts_toward_d4": sc == "B09",
            "population_total": 10, "eligible_candidates_count": 2, "rejected_candidates_count": 8,
            "max_known_at_minus_decision_ms": 0,
        }
        fr_d["row_sha256"] = canonical_hash(fr_d)
        flt_rows.append(fr_d)

        fm_sums = {}
        for fm in FILL_MODES_KH01B:
            ep_d = {
                "short_code": sc, "variant_id": f"KH01B-{sc}", "ontology": ont, "fill_mode": fm,
                "opportunity_id": f"opp_{sc}_1", "decision_timestamp_ms": 1_792_000_000_000,
                "known_at_ms": 1_792_000_000_000, "release_ts_ms": 1_792_000_600_000,
                "filled": True, "outcome_label": "LOSS", "planned_net_risk_usd": 2.25,
                "realized_gross_pnl_usd": -2.00, "total_fees_usd": 0.25, "realized_net_pnl_usd": -2.25, "realized_net_r": -1.0,
            }
            ep_d["row_sha256"] = canonical_hash(ep_d)
            ep_rows.append(ep_d)
            fm_sums[fm] = {"closed_trades_count": 1, "total_realized_net_usd": -2.25}

        vs_10[sc] = {
            "variant_id": f"KH01B-{sc}", "short_code": sc, "ontology": ont,
            "population_total": 10, "eligible_candidates_count": 2, "rejected_candidates_count": 8,
            "fill_mode_summaries": fm_sums,
        }
        ec_11[sc] = {
            "variant_id": f"KH01B-{sc}", "short_code": sc, "ontology": ont,
            "scheduled_nb": 1, "closed_trades_nb": 1, "net_pnl_1x_nb_usd": -2.25,
            "expectancy_1x_nb_r": -1.0, "stress_2x_all_cost_net_pnl_usd": -2.75,
            "selection_outcome": "RESEARCH_NEGATIVE_EDGE", "sample_power_warning": "UNDERPOWERED",
        }

    write_jsonl_gz_durable_atomic(r / "ledgers/kh01b_all_variants_episode_ledgers.jsonl.gz", ep_rows)
    write_jsonl_gz_durable_atomic(r / "ledgers/kh01b_candidate_filter_decisions_summary.jsonl.gz", flt_rows)
    write_json_durable_atomic(r / "10_all_variant_comparable_replays_3fill_1pos_ledgers.json", {
        "one_position_no_overlap_verified_all_variants": True, "variants_summary": vs_10,
    })
    write_json_durable_atomic(r / "11_net_economics_cost_stress_cluster_uncertainty_reused_folds.json", {
        "status": "PASS", "kh01c_advancement_decision": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE",
        "qualified_kh01c_candidates": [], "variants_economics": ec_11,
    })
    write_json_durable_atomic(r / "12_full_baseline_and_three_adapter_parity.json", {
        "status": "PASS", "b00_vs_si0_exact_match": True, "a1_population_denominator": 10,
        "a1_evaluated_count": 10, "a1_mismatches_count": 0,
        "three_adapter_episodes_denominator": 246, "three_adapter_mismatches_count": 0,
    })
    write_json_durable_atomic(r / "13_real_fault_injection_red_green_casebook.json", {
        "status": "PASS", "passed_cases": 25, "total_cases": 25,
    })
    node_id = "backend/tests/test_kh_01b_entry_unicorn_research.py::test_sample"
    write_json_durable_atomic(r / "14_full_pytest_junit_shards_process_exit_and_bijection.json", {
        "collection_manifest": {"run_id": run_id, "tested_executable_sha": sha, "collect_exit_code": 0, "collected_node_ids": [node_id]},
        "shard_manifest": {
            "run_id": run_id, "tested_executable_sha": sha, "run_start_epoch_ns": xml_mtime - 1000,
            "nonzero_exit_shards_count": 0,
            "shards": [{
                "shard_id": "SHARD_01", "shard_name": "SHARD_01", "run_id": run_id, "tested_executable_sha": sha,
                "pid": 12345, "reused": False, "reused_from_prior_run": False,
                "exit_code": 0, "timed_out": False, "scheduled_node_ids": [node_id],
                "junit_xml_path": "junit/SHARD_01.xml", "xml_sha256": xml_sha, "xml_mtime_ns": xml_mtime,
                "junit_sha256": xml_sha, "junit_mtime_ns": xml_mtime,
            }],
        },
        "independent_bijection_verification": {"status": "PASS", "counters": {"collected": 1, "executed": 1, "passed": 1, "skipped": 0, "failed": 0, "errors": 0}},
    })
    write_json_durable_atomic(r / "15_async_workers_1_2_4_repeat_determinism_and_real_speed.json", {
        "pytest_subset_w1_w2_w4_repeat_identical": True, "ablation_slice_w1_w2_w4_repeat_identical": True, "measured_speedup_w4_vs_w1": 2.5,
    })
    write_json_durable_atomic(r / "16_resource_disk_ram_cpu_pid_cleanup.json", {
        "max_children_observed": 2, "orphan_pids_at_cleanup_count": 0, "min_free_disk_gb_observed": 35.0,
        "min_available_ram_gb_observed": 4.0, "disk_reserve_20gb_breached": False, "temp_dirs_cleaned_with_sentinel_check": True,
    })
    write_json_durable_atomic(r / "17_fresh_oos_registry_and_economic_verdict_separation.json", {
        "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
    })

    target_map = {
        "G-KH01B-01": "00_preflight_source_config_dataset_freeze.json",
        "G-KH01B-02": "02_pre_registered_hypothesis_manifest_and_search_budget.json",
        "G-KH01B-03": "04_fixed_population_ids_and_match_conservation.json",
        "G-KH01B-04": "03_causal_feature_sources_unknown_invalid_coverage.json",
        "G-KH01B-05": "05_htf_single_axis_B01_B02_B03.json",
        "G-KH01B-06": "09_strict_D4_and_adjacent_B09_B10_detector_funnel.json",
        "G-KH01B-07": "09_strict_D4_and_adjacent_B09_B10_detector_funnel.json",
        "G-KH01B-08": "10_all_variant_comparable_replays_3fill_1pos_ledgers.json",
        "G-KH01B-09": "11_net_economics_cost_stress_cluster_uncertainty_reused_folds.json",
        "G-KH01B-10": "12_full_baseline_and_three_adapter_parity.json",
        "G-KH01B-11": "14_full_pytest_junit_shards_process_exit_and_bijection.json",
        "G-KH01B-12": "15_async_workers_1_2_4_repeat_determinism_and_real_speed.json",
        "G-KH01B-13": "16_resource_disk_ram_cpu_pid_cleanup.json",
        "G-KH01B-14": "00_preflight_source_config_dataset_freeze.json",
    }
    for gid in MANDATORY_KH01B_TECHNICAL_GATES:
        build_dynamic_validated_gate_receipt_r1(
            gate_id=gid, run_dir=r, run_id=run_id, tested_sha=sha,
            target_rel_path=target_map[gid], validator_fn=_pass_validator, write_json_fn=write_json_durable_atomic,
        )
    build_dynamic_validated_gate_receipt_r1(
        gate_id="G-KH01B-15", run_dir=r, run_id=run_id, tested_sha=sha,
        target_rel_path="17_fresh_oos_registry_and_economic_verdict_separation.json",
        validator_fn=_blocked_oos_validator, write_json_fn=write_json_durable_atomic,
    )
    build_dynamic_validated_gate_receipt_r1(
        gate_id="G-KH01B-16", run_dir=r, run_id=run_id, tested_sha=sha,
        target_rel_path="00_preflight_source_config_dataset_freeze.json",
        validator_fn=_pending_pub_validator, write_json_fn=write_json_durable_atomic,
    )

    v19 = evaluate_kh01b_orthogonal_gates_from_disk(r, run_id, sha)
    write_json_durable_atomic(r / "19_final_orthogonal_gates_blockers_and_kh01c_candidates.json", v19)
    rm, ho = render_kh01b_readme_handover_vi(
        run_id=run_id, branch=MANDATORY_BRANCH_KH01B, tested_sha=sha, dataset_sha=CANONICAL_DATASET_SHA256_KH01B,
        manifest_02=json.loads((r / "02_pre_registered_hypothesis_manifest_and_search_budget.json").read_text("utf-8")),
        replays_10=json.loads((r / "10_all_variant_comparable_replays_3fill_1pos_ledgers.json").read_text("utf-8")),
        economics_11=json.loads((r / "11_net_economics_cost_stress_cluster_uncertainty_reused_folds.json").read_text("utf-8")),
        d4_09=json.loads((r / "09_strict_D4_and_adjacent_B09_B10_detector_funnel.json").read_text("utf-8")),
        parity_12=json.loads((r / "12_full_baseline_and_three_adapter_parity.json").read_text("utf-8")),
        junit_14=json.loads((r / "14_full_pytest_junit_shards_process_exit_and_bijection.json").read_text("utf-8")),
        workers_15=json.loads((r / "15_async_workers_1_2_4_repeat_determinism_and_real_speed.json").read_text("utf-8")),
        resource_16=json.loads((r / "16_resource_disk_ram_cpu_pid_cleanup.json").read_text("utf-8")),
        verdicts_19=v19,
    )
    write_text_durable_atomic(r / "README_KH01B.md", rm)
    write_text_durable_atomic(r / "HANDOVER_KH01B_VI.txt", ho)
    return r


def test_kh01b_subproc_verifier_all_7_adversarial_tampers(tmp_path: Path) -> None:
    """§14: Executes separate-OS-process verifier against GREEN control + all 7 adversarial tampers."""
    ok_dir = _build_valid_minimal_kh01b_bundle(tmp_path)

    def _run_v(target_d: Path) -> tuple[int, dict]:
        p = subprocess.run(
            [sys.executable, str(VERIFIER_KH01B), "--run-dir", str(target_d), "--skip-git-freeze-check"],
            capture_output=True, text=True,
        )
        return p.returncode, json.loads(p.stdout)

    # GREEN control
    rc0, out0 = _run_v(ok_dir)
    assert rc0 == 0 and out0["independent_verifier_status"] == "PASS"

    # 1. Tamper one row hash in episode ledger
    d1 = tmp_path / "t1"
    shutil.copytree(ok_dir, d1)
    import gzip
    rows = [json.loads(ln) for ln in gzip.open(d1 / "ledgers/kh01b_all_variants_episode_ledgers.jsonl.gz", "rt", encoding="utf-8")]
    rows[0]["row_sha256"] = "0" * 64
    write_jsonl_gz_durable_atomic(d1 / "ledgers/kh01b_all_variants_episode_ledgers.jsonl.gz", rows)
    rc1, out1 = _run_v(d1)
    assert rc1 != 0 and any("EPISODE_LEDGER_ROW_HASH_TAMPERED" in v for v in out1["violations"])

    # 2. Relabel adjacent B10 as True Unicorn
    d2 = tmp_path / "t2"
    shutil.copytree(ok_dir, d2)
    f10 = json.loads((d2 / "10_all_variant_comparable_replays_3fill_1pos_ledgers.json").read_text("utf-8"))
    f10["variants_summary"]["B10"]["ontology"] = "STRICT_D4_TRUE_UNICORN"
    write_json_durable_atomic(d2 / "10_all_variant_comparable_replays_3fill_1pos_ledgers.json", f10)
    rc2, out2 = _run_v(d2)
    assert rc2 != 0 and any("ONTOLOGY_TAMPER" in v or "INVALID_GATE_RECEIPT" in v for v in out2["violations"])

    # 3. Alter a negative PnL sign to positive in 11_net_economics
    d3 = tmp_path / "t3"
    shutil.copytree(ok_dir, d3)
    f11 = json.loads((d3 / "11_net_economics_cost_stress_cluster_uncertainty_reused_folds.json").read_text("utf-8"))
    f11["variants_economics"]["B01"]["net_pnl_1x_nb_usd"] = +25.00
    write_json_durable_atomic(d3 / "11_net_economics_cost_stress_cluster_uncertainty_reused_folds.json", f11)
    rc3, out3 = _run_v(d3)
    assert rc3 != 0 and any("PNL_TAMPERED_VS_LEDGER" in v for v in out3["violations"])

    # 4. Change feature known_at to future in episode ledger
    d4 = tmp_path / "t4"
    shutil.copytree(ok_dir, d4)
    rows4 = copy.deepcopy(rows)
    rows4[0]["known_at_ms"] = int(rows4[0]["decision_timestamp_ms"]) + 60_000
    rows4[0].pop("row_sha256", None)
    rows4[0]["row_sha256"] = canonical_hash(rows4[0])
    write_jsonl_gz_durable_atomic(d4 / "ledgers/kh01b_all_variants_episode_ledgers.jsonl.gz", rows4)
    rc4, out4 = _run_v(d4)
    assert rc4 != 0 and any("FUTURE_KNOWN_AT" in v for v in out4["violations"])

    # 5. Mark reused OOS as PASS
    d5 = tmp_path / "t5"
    shutil.copytree(ok_dir, d5)
    write_json_durable_atomic(d5 / "17_fresh_oos_registry_and_economic_verdict_separation.json", {"FRESH_OOS_VERDICT": "PASS"})
    rc5, out5 = _run_v(d5)
    assert rc5 != 0 and any("COUNTERFEIT_OR_INVALID_FRESH_OOS_VERDICT" in v for v in out5["violations"])

    # 6. Fabricate JUnit PASS / corrupt XML on disk
    d6 = tmp_path / "t6"
    shutil.copytree(ok_dir, d6)
    (d6 / "junit/SHARD_01.xml").write_text("<testsuite tests='0'/>", encoding="utf-8")
    rc6, out6 = _run_v(d6)
    assert rc6 != 0 and any("JUNIT_BIJECTION" in v for v in out6["violations"])

    # 7. Delete a mandatory gate receipt
    d7 = tmp_path / "t7"
    shutil.copytree(ok_dir, d7)
    (d7 / "receipts/G-KH01B-08.json").unlink()
    rc7, out7 = _run_v(d7)
    assert rc7 != 0 and any("MISSING_MANDATORY_GATE_RECEIPT" in v for v in out7["violations"])
