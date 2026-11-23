"""
Dedicated KH-02.R2 unit tests: data lineage & latency sensitivity, prospective OOS escrow
readiness (OOS_EVALUATIONS == 0), strict disk floor policy, 42 unique real RED/GREEN faults,
and separate-OS-process independent disk verifier GREEN + 10 adversarial tamper rejections.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

from services.kh_02_entry_foundation_research_service import build_kh02_asof_mtf_features
from services.kh_02_r2_prospective_data_service import (
    SCHEMA_VERSION_KH02_R2,
    prepare_r2_prospective_oos_ingestion_and_escrow,
    validate_prospective_snapshot_entry_r2,
)
from services.kh_02_r2_research_integrity_service import (
    ACTIVATION_READY_KH02_R2,
    CANONICAL_DATASET_SHA256_KH02_R2,
    MANDATORY_BRANCH_KH02_R2,
    PRIOR_KH02_R1_TESTED_SHA,
    RUNTIME_SAFETY_MODES_KH02_R2,
    SPEC_VERSION_KH02_R2,
    audit_r2_data_lineage_and_gap_sensitivity,
    evaluate_r2_disk_preflight_policy,
    evaluate_r2_orthogonal_science_and_engineering_gates_from_disk,
    run_kh02_r2_42_red_green_fault_suite,
)

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
VERIFIER_SCRIPT = APP_ROOT / "tools" / "kh_02_r2_independent_verifier.py"


def test_prospective_oos_readiness_non_peek_and_strict_escrow_contract() -> None:
    freeze_ms = 1_795_000_000_000
    res_empty = prepare_r2_prospective_oos_ingestion_and_escrow(
        repo_root=REPO_ROOT,
        freeze_timestamp_ms=freeze_ms,
        tested_executable_sha="c" * 40,
        frozen_config_sha256="d" * 64,
        disk_free_gib=9.65,
    )
    assert res_empty["escrow_contract_integrity_status"] == "PASS"
    assert res_empty["OOS_EVALUATIONS"] == 0
    assert res_empty["FUTURE_OOS_READINESS_VERDICT"] == "BLOCKED_NO_VERIFIED_NEW_DATA"
    assert res_empty["FRESH_OOS_VERDICT"] == "BLOCKED_NO_UNTOUCHED_DATA"
    assert res_empty["ACTIVATION_READY"] == "NO"
    assert res_empty["prior_escrow_mutated_count"] == 0

    # Valid new post-embargo prospective snapshot seals as NOT_EVALUATED with 0 evaluations
    good_snap = {
        "snapshot_id": "prosp_xau_001",
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "exchange": "bitget",
        "exchange_contract_id": "XAUUSDT_UMCBL",
        "symbol": "XAUUSDT",
        "price_precision": 2,
        "qty_precision": 2,
        "timeframe": "1m",
        "start_utc_ms": freeze_ms + 250 * 60_000,
        "end_utc_ms": freeze_ms + 2000 * 60_000,
        "bar_count": 1750,
        "utc_monotonic_strict": True,
        "observed_bid_ask_present": True,
        "quote_mode": "OBSERVED_L1_BBO",
        "raw_response_sha256": "1" * 64,
        "pagination_lineage": ["p1", "p2"],
        "source_signature_or_checksum": "sig1",
        "capture_time_ms": freeze_ms + 2005 * 60_000,
        "known_at_ms": freeze_ms + 2005 * 60_000,
        "dataset_sha256": "2" * 64,
        "strategy_evaluation_count": 0,
    }
    v_ok = validate_prospective_snapshot_entry_r2(
        good_snap, freeze_timestamp_ms=freeze_ms, known_dev_shas=(CANONICAL_DATASET_SHA256_KH02_R2,)
    )
    assert v_ok["valid_for_escrow_admission"] is True

    res_sealed = prepare_r2_prospective_oos_ingestion_and_escrow(
        repo_root=REPO_ROOT,
        freeze_timestamp_ms=freeze_ms,
        tested_executable_sha="c" * 40,
        frozen_config_sha256="d" * 64,
        proposed_prospective_snapshots=[good_snap],
        disk_free_gib=30.0,
    )
    assert res_sealed["FUTURE_OOS_READINESS_VERDICT"] == "SEALED_PROSPECTIVE_UNTOUCHED_NOT_EVALUATED"
    assert res_sealed["FRESH_OOS_VERDICT"] == "SEALED_UNTOUCHED_NOT_EVALUATED"
    assert res_sealed["OOS_EVALUATIONS"] == 0

    # Unauthorized OOS evaluation MUST FAIL
    res_peek = prepare_r2_prospective_oos_ingestion_and_escrow(
        repo_root=REPO_ROOT,
        freeze_timestamp_ms=freeze_ms,
        tested_executable_sha="c" * 40,
        frozen_config_sha256="d" * 64,
        claimed_oos_evaluations=1,
    )
    assert res_peek["FRESH_OOS_VERDICT"] == "FAIL"


def test_disk_preflight_strict_floor_policy() -> None:
    low = evaluate_r2_disk_preflight_policy({"repo": 9.646, "temp": 9.646})
    assert low["status"] == "BLOCKED_LOW_DISK"
    assert low["can_run_heavy_stages"] is False
    assert low["shortfall_to_strict_20p5_gib"] == pytest.approx(10.854, abs=1e-3)

    ok_strict = evaluate_r2_disk_preflight_policy({"repo": 22.0, "temp": 22.0})
    assert ok_strict["status"] == "PASS_STRICT_BELOW_ADVISORY_25GIB"
    assert ok_strict["can_run_heavy_stages"] is True

    ok_full = evaluate_r2_disk_preflight_policy({"repo": 30.0, "temp": 30.0})
    assert ok_full["status"] == "PASS_STRICT_AND_ADVISORY"

    with pytest.raises(ValueError, match="ILLEGAL_LOWERED_PREFLIGHT_DISK_FLOOR"):
        evaluate_r2_disk_preflight_policy({"repo": 15.0}, strict_preflight_min_gib=10.0)


def test_data_lineage_and_latency_sensitivity_on_synth_data(tmp_path: Path) -> None:
    base_ts = 1_792_080_000_000
    def _mk(tf_ms: int, cnt: int) -> list:
        out = []
        st = base_ts - cnt * tf_ms
        for i in range(cnt):
            o_t = st + i * tf_ms
            px = 4000.0 + 0.1 * i
            out.append({"timestamp": o_t, "open_time": o_t, "close_time": o_t + tf_ms, "open": px, "high": px + 1.0, "low": px - 1.0, "close": px + 0.2, "volume": 10.0, "is_closed": True})
        return out

    ds = {"1m": _mk(60_000, 90), "15m": _mk(900_000, 20), "1H": _mk(3_600_000, 20)}
    mtf = build_kh02_asof_mtf_features(ds, warmup_m1=30)
    fake_ds_file = tmp_path / "ds.json.gz"
    fake_ds_file.write_bytes(b"test")
    lin = audit_r2_data_lineage_and_gap_sensitivity(
        datasets=ds, mtf_bundle=mtf, dataset_path=fake_ds_file, latency_grid_ms=(0, 5000)
    )
    assert lin["quote_and_fee_provenance"]["quote_mode"] == "MODELED_BID_ASK"
    assert lin["quote_and_fee_provenance"]["observed_l1_bbo_available"] is False
    assert lin["quote_and_fee_provenance"]["actual_account_effective_fee_tier"] == "UNKNOWN_NOT_LOCALLY_VERIFIED"
    assert len(lin["source_latency_sensitivity"]) == 2


def test_kh02_r2_42_unique_real_red_green_fault_suite() -> None:
    res = run_kh02_r2_42_red_green_fault_suite(REPO_ROOT)
    assert res["total_controls"] >= 42
    assert res["unique_fault_ids"] == res["total_controls"]
    assert res["unique_fault_names"] == res["total_controls"]
    assert res["failed_controls"] == 0, [c for c in res["controls"] if c["status"] != "PASS"]
    assert res["all_controls_passed"] is True
    assert res["status"] == "PASS"


def _build_minimal_valid_synthetic_r2_run_dir(run_dir: Path, *, disk_free_gib: float = 9.65) -> tuple[str, str]:
    """Creates a minimal, self-consistent KH-02.R2 run dir for separate-OS verifier tamper testing."""
    sha = "e" * 40
    run_id = f"20261121T120000Z_{sha[:12]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "receipts").mkdir(exist_ok=True)

    def _wj(name: str, obj: dict) -> None:
        (run_dir / name).write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")

    def _wgz(name: str, rows: list) -> None:
        with gzip.open(run_dir / name, "wt", encoding="utf-8") as gz:
            for r in rows:
                gz.write(json.dumps(r, sort_keys=True) + "\n")

    dp = evaluate_r2_disk_preflight_policy({"repo": disk_free_gib})
    _wj("00_preflight_branch_repo_sha_data_and_resources.json", {
        "run_id": run_id,
        "branch": MANDATORY_BRANCH_KH02_R2,
        "tested_executable_sha": sha,
        "start_head_sha": sha,
        "orchestrator_pid": os.getpid(),  # Different from subprocess verifier PID!
        "git_clean_at_start": False,
        "local_blocked_dirty_uncommitted_r2": True,
        "disk_preflight_policy": dp,
    })
    _wj("01_function_ownership_keep_as_is_diff.json", {
        "status": "PASS",
        "locked_files_modified_count": 0,
        "modified_existing_files_count": 0,
        "forbidden_order_import_hits": [],
        "cost_assumptions_defaults_intact": True,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
    })
    _wj("02_r1_source_integrity_and_snapshot_registry.json", {
        "status": "PASS",
        "reused_dataset_provenance": {"match": True, "sha256": CANONICAL_DATASET_SHA256_KH02_R2},
    })
    _wj("03_frozen_r2_estimands_thresholds_hypotheses_and_falsification.json", {"frozen_before_outcome_reads": True})
    _wgz("04_asof_unswept_level_ledger_and_mutation_proof.jsonl.gz", [{"level_id": "15m_1", "price": 4005.0}])
    _wj("05_r1_target_eligibility_counterexamples_and_root_cause.json", {
        "status": "PASS",
        "finding_classification": "R1_SUSPECT_CONFIRMED_ON_RESEARCH_PATH_ONLY",
        "production_affected": False,
        "synthetic_t01_to_t10_suite": {"all_passed": True, "passed_count": 10, "scenario_count": 10},
        "full_dataset_audit_summary": {"total_confirmed_already_swept_raw_targets": 12},
    })
    _wgz("06_r2_target_repair_full_opportunity_conservation.jsonl.gz", [{"opportunity_id": "op1", "r2_status": "ELIGIBLE"}])
    _wgz("07_paired_entry_geometry_factorial_control_mapping.jsonl.gz", [
        {"estimand": "E_ONLY_PAIRED_FIXED_GEOMETRY", "opportunity_id": "op1", "pair_status": "MATCHED_EXACT_TRADE"}
    ])
    _wj("08_strata_common_support_balance_and_matching_quality.json", {
        "PAIRED_COMPARISON_VERDICT": "INSUFFICIENT_COMMON_SUPPORT",
        "arm_balance_reports": {"B2_H1": {"matching_quality_status": "MATCHING_INADEQUATE"}},
    })
    _wj("09_chronological_1position_three_fill_paired_replays.json", {
        "status": "PASS",
        "total_overlap_violations": 0,
        "causality_violations_count": 0,
    })
    _wj("10_fill_time_net_risk_and_realized_tail_audit.json", {
        "status": "PASS",
        "decision_time_cap_violations": 0,
        "post_fill_policy_cap_violations": 0,
    })
    _wj("11_fixed_fill_same_ids_fee_spread_slippage_1x2x3x.json", {
        "status": "PASS",
        "population_mode": "FIXED_FILL_SAME_IDS",
        "zero_dropped_ids_verified": True,
        "max_decimal_reconciliation_residual_usd": 0.0,
        "reconciliation_tolerance_usd": 0.001,
        "arms": {
            "B2_H1": {
                "gross_pnl_1x_usd": -10.0,
                "1x_base_0p0004": {"fees_usd": 1.0, "net_pnl_usd": -11.0},
                "fee_only_0p0006": {"fees_usd": 1.5, "net_pnl_usd": -11.5},
            }
        },
    })
    _wj("12_full_universe_rerisked_1x2x3x_and_fee_004_006.json", {
        "status": "PASS",
        "population_mode": "FULL_RERISKED_DYNAMIC_POPULATION",
        "starts_from_full_raw_opportunity_stream": True,
    })
    _wj("13_m1_m15_h1_gaps_source_latency_quote_fee_provenance.json", {
        "DATA_PROVENANCE_VERDICT": "PASS",
        "headline_effective_m1_calendar_span_days": 20.35,
        "quote_and_fee_provenance": {
            "quote_mode": "MODELED_BID_ASK",
            "observed_l1_bbo_available": False,
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        },
        "source_latency_sensitivity": [{"source_latency_ms": 0}, {"source_latency_ms": 5000}],
    })
    _wj("14_prospective_oos_escrow_collection_readiness_no_peek.json", {
        "escrow_contract_integrity_status": "PASS",
        "OOS_EVALUATIONS": 0,
        "prior_escrow_mutated_count": 0,
        "FUTURE_OOS_READINESS_VERDICT": "BLOCKED_NO_VERIFIED_NEW_DATA",
        "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
    })
    _wj("15_purged_walkforward_cluster_uncertainty_and_multiplicity.json", {
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
        "status": "PASS",
    })
    _wj("16_full_pytest_collection_execution_junit_bijection.json", {
        "status": "NOT_RUN_BLOCKED_LOW_DISK",
        "collected_count": 0,
        "executed_count": 0,
    })
    _wj("17_async_w1_w2_w4_repeat_times_outputs_and_scheduler_policy.json", {
        "status": "NOT_RUN_BLOCKED_LOW_DISK",
    })
    _wj("18_ram_disk_pid_temp_telemetry.json", {
        "min_available_ram_mib": 3000.0,
        "min_disk_free_gib": disk_free_gib,
        "max_descendant_children": 1,
        "orphan_children_after_cleanup": 0,
        "disk_preflight_policy": dp,
    })
    _wgz("logs/runtime_memory_samples_0p5s.jsonl.gz", [
        {"ts_ms": 1, "available_ram_bytes": 3000 * 1024 * 1024, "rss_bytes": 80 * 1024 * 1024, "child_rss_bytes": 0, "worker_pids": [101]},
        {"ts_ms": 2, "available_ram_bytes": 2950 * 1024 * 1024, "rss_bytes": 85 * 1024 * 1024, "child_rss_bytes": 0, "worker_pids": [101]},
    ])
    _wj("19_real_red_green_fault_injections.json", {
        "status": "PASS",
        "total_controls": 42,
        "unique_fault_ids": 42,
        "passed_controls": 42,
        "failed_controls": 0,
    })
    _wj("20_production_baseline_parity_and_prior_evidence_immutability.json", {
        "BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK",
    })
    _wj("23_tested_sha_evidence_publication_receipt_pre_push.json", {
        "run_id": run_id,
        "tested_executable_sha": sha,
        "publication_status": "NOT_PUBLISHED_BLOCKED_LOW_DISK",
    })

    f21 = evaluate_r2_orthogonal_science_and_engineering_gates_from_disk(run_dir)
    _wj("21_orthogonal_engineering_science_economic_oos_gate_matrix.json", f21)
    (run_dir / "receipts" / "G-KH02R2-01.json").write_text(json.dumps({"gate_id": "Gate 01"}), encoding="utf-8")

    eng_v = f21["orthogonal_verdicts"]["ENGINEERING_VERDICT"]
    (run_dir / "README_KH02_R2.md").write_text(f"# KH-02.R2\nrun_id={run_id}\nACTIVATION_READY=NO\n{eng_v}\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH02_R2_VI.txt").write_text(f"run_id={run_id}\nACTIVATION_READY=NO\n{eng_v}\n", encoding="utf-8")
    return run_id, sha


def _run_verifier_subprocess(run_dir: Path, run_id: str, sha: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    return subprocess.run(
        [
            sys.executable,
            str(VERIFIER_SCRIPT),
            "--run-dir",
            str(run_dir),
            "--expected-run-id",
            run_id,
            "--expected-tested-sha",
            sha,
            "--skip-live-repo-pointers",
            "--no-write-manifest",
        ],
        cwd=str(APP_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_separate_os_independent_verifier_green_and_10_adversarial_tampers(tmp_path: Path) -> None:
    clean_dir = tmp_path / "clean_run"
    run_id, sha = _build_minimal_valid_synthetic_r2_run_dir(clean_dir)

    # 0. Clean separate-OS run MUST PASS with distinct PID
    ok_proc = _run_verifier_subprocess(clean_dir, run_id, sha)
    assert ok_proc.returncode == 0, ok_proc.stdout + ok_proc.stderr
    out_ok = json.loads(ok_proc.stdout)
    assert out_ok["independent_verifier_status"] == "PASS"
    assert out_ok["distinct_os_process_verified"] is True
    assert out_ok["verifier_pid"] != os.getpid()

    # 10 Adversarial Tamper Controls — EVERY SINGLE ONE must cause separate OS verifier to FAIL!
    # Executed with bounded max_workers=3 separate-OS verifier processes (respects <= 4 descendant cap).
    def _clone(name: str) -> Path:
        dst = tmp_path / name
        shutil.copytree(clean_dir, dst)
        return dst

    tamper_jobs: list[tuple[str, Path, str, str]] = []

    # Tamper 1: Flip Gate 17 from BLOCKED_LOW_DISK to PASS in 21
    d1 = _clone("t1_flip_gate")
    p21 = d1 / "21_orthogonal_engineering_science_economic_oos_gate_matrix.json"
    j21 = json.loads(p21.read_text("utf-8"))
    for g in j21["gates"]:
        if g["gate_id"] == "Gate 17":
            g["status"] = "PASS"
    p21.write_text(json.dumps(j21), encoding="utf-8")
    tamper_jobs.append(("t1_flip_gate", d1, run_id, sha))

    # Tamper 2: Forge RESOURCE_SAFETY_VERDICT = PASS under 9.65 GiB disk
    d2 = _clone("t2_forge_disk_pass")
    p21 = d2 / "21_orthogonal_engineering_science_economic_oos_gate_matrix.json"
    j21 = json.loads(p21.read_text("utf-8"))
    j21["orthogonal_verdicts"]["RESOURCE_SAFETY_VERDICT"] = "PASS"
    p21.write_text(json.dumps(j21), encoding="utf-8")
    tamper_jobs.append(("t2_forge_disk_pass", d2, run_id, sha))

    # Tamper 3: Forge RESEARCH_ECONOMIC_VERDICT = PROVEN_EDGE on reused data
    d3 = _clone("t3_forge_econ")
    p15 = d3 / "15_purged_walkforward_cluster_uncertainty_and_multiplicity.json"
    j15 = json.loads(p15.read_text("utf-8"))
    j15["RESEARCH_ECONOMIC_VERDICT"] = "PROVEN_EDGE"
    p15.write_text(json.dumps(j15), encoding="utf-8")
    tamper_jobs.append(("t3_forge_econ", d3, run_id, sha))

    # Tamper 4: Mutate fixed-fill cost table arithmetic (gross - fee != net)
    d4 = _clone("t4_cost_math")
    p11 = d4 / "11_fixed_fill_same_ids_fee_spread_slippage_1x2x3x.json"
    j11 = json.loads(p11.read_text("utf-8"))
    j11["arms"]["B2_H1"]["1x_base_0p0004"]["net_pnl_usd"] = 999.0
    p11.write_text(json.dumps(j11), encoding="utf-8")
    tamper_jobs.append(("t4_cost_math", d4, run_id, sha))

    # Tamper 5: Subset rerisk substitution in 12
    d5 = _clone("t5_subset_rerisk")
    p12 = d5 / "12_full_universe_rerisked_1x2x3x_and_fee_004_006.json"
    j12 = json.loads(p12.read_text("utf-8"))
    j12["starts_from_full_raw_opportunity_stream"] = False
    p12.write_text(json.dumps(j12), encoding="utf-8")
    tamper_jobs.append(("t5_subset_rerisk", d5, run_id, sha))

    # Tamper 6: Enable ACTIVATION_READY = YES
    d6 = _clone("t6_activation")
    p01 = d6 / "01_function_ownership_keep_as_is_diff.json"
    j01 = json.loads(p01.read_text("utf-8"))
    j01["ACTIVATION_READY"] = "YES"
    p01.write_text(json.dumps(j01), encoding="utf-8")
    tamper_jobs.append(("t6_activation", d6, run_id, sha))

    # Tamper 7: Nonzero OOS_EVALUATIONS = 1 (OOS peek)
    d7 = _clone("t7_oos_peek")
    p14 = d7 / "14_prospective_oos_escrow_collection_readiness_no_peek.json"
    j14 = json.loads(p14.read_text("utf-8"))
    j14["OOS_EVALUATIONS"] = 1
    p14.write_text(json.dumps(j14), encoding="utf-8")
    tamper_jobs.append(("t7_oos_peek", d7, run_id, sha))

    # Tamper 8: Swap tested_executable_sha to stale KH-02.R1 SHA
    d8 = _clone("t8_stale_sha")
    for fname in (
        "00_preflight_branch_repo_sha_data_and_resources.json",
        "21_orthogonal_engineering_science_economic_oos_gate_matrix.json",
        "23_tested_sha_evidence_publication_receipt_pre_push.json",
    ):
        fp = d8 / fname
        jj = json.loads(fp.read_text("utf-8"))
        jj["tested_executable_sha"] = PRIOR_KH02_R1_TESTED_SHA
        fp.write_text(json.dumps(jj), encoding="utf-8")
    tamper_jobs.append(("t8_stale_sha", d8, run_id, PRIOR_KH02_R1_TESTED_SHA))

    # Tamper 9: Delete pairing ledger 07
    d9 = _clone("t9_delete_pairing")
    (d9 / "07_paired_entry_geometry_factorial_control_mapping.jsonl.gz").unlink()
    tamper_jobs.append(("t9_delete_pairing", d9, run_id, sha))

    # Tamper 10: Claim full pytest PASS with zero JUnit XML
    d10 = _clone("t10_fake_pytest_pass")
    p16 = d10 / "16_full_pytest_collection_execution_junit_bijection.json"
    p16.write_text(json.dumps({"status": "PASS", "collected_count": 100, "executed_count": 100, "failed_count": 0, "error_count": 0, "junit_bijection_exact": True}), encoding="utf-8")
    tamper_jobs.append(("t10_fake_pytest_pass", d10, run_id, sha))

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(lambda job: (job[0], _run_verifier_subprocess(job[1], job[2], job[3])), tamper_jobs)
        )
    for label, proc in results:
        assert proc.returncode != 0, f"Tamper {label} unexpectedly passed: {proc.stdout}"
