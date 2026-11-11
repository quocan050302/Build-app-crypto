#!/usr/bin/env python3
"""
tools/v17_9_3_1_2_13_independent_verifier.py

AURUM Desk — V17.9.3.1.2.13 Separate-Process Independent Verifier.

Runs in an independent child OS process (`verifier_pid != orchestrator_pid`),
reconstructs and cross-checks all V17.9.3.1.2.13 artifacts directly from disk,
re-runs all 66 fault-injected negative controls (`V01..V66`) in disposable
temporary directories, evaluates the 12 dynamic acceptance gates, and seals
`artifact_sha256_manifest.json`.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, Iterable, List

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(APP_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(APP_ROOT / "backend"))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    render_truth_closure_readme_and_handover_v17931211,
    verify_all_policy_fill_mode_lineage_independently_v17931211,
    verify_artifact_manifest_v17931211,
    verify_execution_not_synthetic_v17931211,
    verify_fresh_junit_bijection_v17931211,
    verify_process_exit_and_timeout_v17931211,
    verify_source_freeze_v17931211,
)
from services.v17_9_3_1_2_12_provenance_forensics_and_repair_service import (
    verify_r1_forensic_separation_and_conservation_v17931212,
    verify_r4_oos_and_activation_verdict_v17931212,
)
from services.v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service import (
    AuthoritativeMetricsV17931213,
    CANONICAL_DATASET_SHA256_V17931213,
    REQUIRED_ARTIFACTS_V17931213,
    SCHEMA_VERSION_V17931213,
    VERSION_V17931213,
    evaluate_v17931213_gate_matrix,
    run_negative_controls_v01_to_v66_v17931213,
    verify_bounded_async_workers_v17931213,
    verify_delayed_entry_and_structural_sl_tp_v17931213,
    verify_htf_unicorn_and_si8_si9_funnel_v17931213,
    verify_v17931213_authoritative_report_and_gates_consistency,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets

DATASET_PATH = APP_ROOT / "backtest/historical_dataset_XAUUSDT.json.gz"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False, default=str)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def iter_jsonl_gz(path: Path) -> Iterable[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def append_stage_status(run_dir: Path, stage: str, status: str, **extra: Any) -> None:
    status_path = run_dir / "run_status.jsonl"
    row = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "epoch_ns": time.time_ns(),
        "pid": os.getpid(),
        "version": VERSION_V17931213,
        "stage": stage,
        "status": status,
        **extra,
    }
    with status_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def build_and_write_artifact_manifest(run_dir: Path) -> Dict[str, Any]:
    files_map: Dict[str, Dict[str, Any]] = {}
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.name == "artifact_sha256_manifest.json" or p.name.endswith(".partial"):
            continue
        rel = str(p.relative_to(run_dir)).replace("\\", "/")
        files_map[rel] = {
            "sha256": file_sha256(p),
            "bytes": int(p.stat().st_size),
        }
    manifest_doc = {
        "version": VERSION_V17931213,
        "schema_version": SCHEMA_VERSION_V17931213,
        "generated_at_epoch_ms": int(time.time() * 1000),
        "files_count": len(files_map),
        "files": files_map,
    }
    write_json(run_dir / "artifact_sha256_manifest.json", manifest_doc)
    return manifest_doc


def run_independent_verification_v17931213(run_dir: Path) -> Dict[str, Any]:
    t0 = time.time()
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()

    preflight_00 = read_json(run_dir / "00_preflight_and_branch_identity.json")
    freeze_19 = read_json(run_dir / "19_source_freeze_and_immutable_prior_artifact_hashes.json")
    run_id = str(preflight_00["run_id"])
    tested_sha = str(preflight_00["tested_executable_sha"])
    dataset_sha = str(preflight_00["dataset_sha256"])
    orchestrator_pid = int(preflight_00.get("orchestrator_pid") or 0)

    # 1. Separate OS process check
    separate_process_ok = verifier_pid != orchestrator_pid and orchestrator_pid > 0

    # 2. Canonical dataset SHA-256 and bar closure check from disk
    actual_dataset_sha = file_sha256(DATASET_PATH)
    datasets, _ = load_canonical_xauusdt_datasets()
    bars_1m = list(datasets.get("1m", []))
    bars_1m_by_open_ts = {int(b.get("open_time") or b.get("timestamp") or 0): b for b in bars_1m}
    bad_bars = sum(
        1
        for _tf, b_list in datasets.items()
        if isinstance(b_list, list)
        for b in b_list
        if isinstance(b, dict)
        and (
            b.get("is_closed") is False
            or int(b.get("close_time") or 0) <= int(b.get("open_time") or b.get("timestamp") or 0)
        )
    )
    dataset_ok = (
        actual_dataset_sha == CANONICAL_DATASET_SHA256_V17931213
        and actual_dataset_sha == dataset_sha
        and len(bars_1m) == 22376
        and bad_bars == 0
    )

    # 3. Source freeze & .7..12 prior artifact immutability check
    freeze_check = verify_source_freeze_v17931211(
        expected_tested_sha=tested_sha,
        observed_head_sha=str(freeze_19.get("observed_head_sha") or ""),
        observed_branch=str(freeze_19.get("branch") or ""),
        working_tree_dirty_files=list(freeze_19.get("working_tree_dirty_files") or []),
        source_manifest_before=freeze_19.get("source_manifest_before") or {},
        source_manifest_after=freeze_19.get("source_manifest_after") or {},
        prior_artifact_hashes_before=freeze_19.get("prior_artifact_hashes_before") or {},
        prior_artifact_hashes_after=freeze_19.get("prior_artifact_hashes_after") or {},
    )
    prior_immutability_ok = (
        freeze_19.get("prior_artifact_sha256_before") == freeze_19.get("prior_artifact_sha256_after")
        and bool(freeze_19.get("prior_artifact_sha256_before"))
    )

    # 4. Fresh pytest collection and JUnit XML node bijection check (12, 13)
    col_12 = read_json(run_dir / "12_current_pytest_fresh_collection_and_shard_map.json")
    jun_13 = read_json(run_dir / "13_full_regression_junit_bijection_and_os_exit_verification.json")
    col_manifest = col_12["collection_manifest"]
    shard_manifest = jun_13["shard_manifest"]
    junit_bijection_check = verify_fresh_junit_bijection_v17931211(
        run_dir,
        col_manifest,
        shard_manifest,
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
    )

    # 5. Async multi-worker process tree & determinism check (11, 20)
    bench_11 = read_json(run_dir / "11_real_async_worker_resource_and_determinism_benchmarks.json")
    proc_20 = read_json(run_dir / "20_process_tree_cleanup_and_disk_ram_evidence.json")
    parity_doc = bench_11["worker_1_2_4_and_repeat_determinism"]
    proc_check = verify_bounded_async_workers_v17931213(
        proc_20.get("worker_records") or [],
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        max_allowed_workers=4,
        active_orphan_pids=proc_20.get("orphan_pids_at_cleanup") or [],
    )
    non_synth_check = verify_execution_not_synthetic_v17931211(
        collection_manifest=col_manifest,
        shard_manifest=shard_manifest,
        parity_manifest=parity_doc,
        resource_telemetry=proc_20.get("resource_telemetry"),
    )
    async_proc_ok = (
        proc_check["status"] == "PASS"
        and non_synth_check["status"] == "PASS"
        and parity_doc.get("status") == "PASS"
        and not (proc_20.get("resource_telemetry") or {}).get("error")
    )
    async_proc_check = {
        "status": "PASS" if async_proc_ok else "FAIL",
        "proc_check": proc_check,
        "non_synth_check": non_synth_check,
        "parity_status": parity_doc.get("status"),
    }

    # 6. Raw-stream reconstruction across all 30 partitions & source execution reconciliation (10)
    all_partitions_check = verify_all_policy_fill_mode_lineage_independently_v17931211(
        run_dir,
        bars_1m_by_open_ts=bars_1m_by_open_ts,
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        expected_dataset_sha=dataset_sha,
        require_source_reconciliation=True,
    )

    # 7. Phase A (P0-E1): Delayed entry & structural SL/TP/sizing check (02, 03, 08, 09)
    contract_02 = read_json(run_dir / "02_a1_old_filter_vs_true_delayed_entry_contract.json")
    delay_ledger_03 = list(iter_jsonl_gz(run_dir / "03_delay_confirmation_new_timestamp_entry_sl_tp_ledger.jsonl.gz"))
    matched_ab_08 = read_json(run_dir / "08_matched_ab_all_opportunities_all_fill_modes.json")
    portfolio_09 = read_json(run_dir / "09_chronological_portfolio_replay_all_variants.json")
    delayed_entry_check = verify_delayed_entry_and_structural_sl_tp_v17931213(
        contract_02,
        matched_ab_08.get("matched_variants_summary_by_fill_mode") or {},
        delay_ledger_03,
        portfolio_09,
    )

    # 8. Phase B (P0-E2 / P0-E3): Dynamic HTF/Unicorn/SI8/SI9 funnel & 267/113 audit check (04, 05)
    pipeline_rows_04_cnt = sum(
        1 for _ in iter_jsonl_gz(run_dir / "04_htf_unicorn_detector_to_intent_causal_pipeline.jsonl.gz")
    )
    funnel_05 = read_json(run_dir / "05_si8_si9_dynamic_full_funnel_and_target_267_audit.json")
    htf_funnel_check = verify_htf_unicorn_and_si8_si9_funnel_v17931213(
        funnel_05,
        raw_si8_decision_ledger=funnel_05.get("raw_si8_eligible_and_materialized_ledger"),
        expect_267_historical_audit=True,
    )
    if pipeline_rows_04_cnt != 63085:
        htf_funnel_check = {
            "status": "FAIL",
            "violations": list(htf_funnel_check.get("violations") or [])
            + [f"PIPELINE_04_ROW_COUNT_MISMATCH:{pipeline_rows_04_cnt}!=63085"],
        }

    # 9. Phase C (R1) & OOS lock check (06, 15, 16)
    forensic_rows_06 = list(iter_jsonl_gz(run_dir / "06_pre_entry_loss_forensics_and_expost_stop_separation.jsonl.gz"))
    oos_15 = read_json(run_dir / "15_oos_provenance_and_reused_holdout_truth.json")
    verdict_16 = read_json(run_dir / "16_truthful_economic_verdict_and_blockers.json")
    r1_check = verify_r1_forensic_separation_and_conservation_v17931212(
        forensic_rows_06, oos_15.get("r1_matrix_summary") or {}
    )
    oos_check = verify_r4_oos_and_activation_verdict_v17931212(oos_15, verdict_16)

    # 10. Single Authoritative Report & Gate Consistency + V01..V66 Negative Controls (14, 18)
    auth_metrics = AuthoritativeMetricsV17931213(**verdict_16["authoritative_metrics_v17931213"])
    readme_text = (run_dir / "README_V17_9_3_1_2_13.md").read_text(encoding="utf-8")
    handover_text = (run_dir / "HANDOVER_V17_9_3_1_2_13.txt").read_text(encoding="utf-8")

    prelim_gates = evaluate_v17931213_gate_matrix(
        auth_metrics,
        delayed_entry_check=delayed_entry_check,
        htf_funnel_check=htf_funnel_check,
        r1_check=r1_check,
        junit_check=junit_bijection_check,
        lineage_30_check=all_partitions_check,
        async_proc_check=async_proc_check,
        report_consistency_check={"status": "PASS"},
        oos_check=oos_check,
        neg_v66_check={"status": "PASS"},
        manifest_check={"status": "PASS"},
        freeze_check=freeze_check,
    )

    underlying_statuses = {
        "G_01_DELAYED_ENTRY_TRUTH": delayed_entry_check["status"],
        "G_02_HTF_UNICORN_DYNAMIC_MATERIALIZATION": htf_funnel_check["status"],
        "G_03_SI9_267_AND_HTF_113_FORENSIC_CLOSURE": htf_funnel_check["status"],
        "G_04_PRE_ENTRY_VS_EXPOST_FORENSIC_SEPARATION": r1_check["status"],
        "G_05_RAW_STREAM_30_PARTITIONS_AND_SOURCE_RECONCILIATION": all_partitions_check["status"],
        "G_07_BOUNDED_ASYNC_MULTI_WORKER_PERFORMANCE_AND_DETERMINISM": async_proc_check["status"],
        "G_08_FRESH_PYTEST_COLLECTION_AND_JUNIT_BIJECTION": junit_bijection_check["status"],
        "G_10_HONEST_OOS_AND_ACTIVATION_LOCK": oos_check["status"],
        "G_11_SOURCE_FREEZE_AND_PRIOR_IMMUTABILITY": freeze_check["status"] if prior_immutability_ok else "FAIL",
    }
    report_consistency_check = verify_v17931213_authoritative_report_and_gates_consistency(
        readme_text,
        handover_text,
        auth_metrics,
        gate_matrix_18=prelim_gates,
        underlying_check_statuses=underlying_statuses,
    )

    base_v11_metrics = verdict_16.get("base_v11_compat_report_metrics")
    base_v11_readme, base_v11_handover = (
        render_truth_closure_readme_and_handover_v17931211(base_v11_metrics)
        if isinstance(base_v11_metrics, dict)
        else (None, None)
    )
    neg_v66_check = run_negative_controls_v01_to_v66_v17931213(
        reconstructed_partitions=all_partitions_check["reconstructed_partitions"],
        bars_1m_by_open_ts=bars_1m_by_open_ts,
        base_v11_report_metrics=base_v11_metrics,
        base_v11_readme_text=base_v11_readme,
        base_v11_handover_text=base_v11_handover,
        authoritative_metrics_v13=auth_metrics,
        readme_v13_text=readme_text,
        handover_v13_text=handover_text,
        gate_matrix_18=prelim_gates,
        run_id=run_id,
        tested_sha=tested_sha,
    )
    write_json(run_dir / "14_negative_controls_V01_to_latest_fault_injection.json", neg_v66_check)

    verifier_violations: List[str] = []
    if not separate_process_ok:
        verifier_violations.append(f"VERIFIER_NOT_SEPARATE_PROCESS:pid={verifier_pid},orch={orchestrator_pid}")
    if not dataset_ok:
        verifier_violations.append("DATASET_SHA_OR_BAR_CLOSURE_MISMATCH")
    if not prior_immutability_ok:
        verifier_violations.append("PRIOR_ARTIFACTS_MUTATED")

    for sub_name, sub_res in (
        ("freeze_check", freeze_check),
        ("junit_bijection_check", junit_bijection_check),
        ("async_proc_check", async_proc_check),
        ("all_partitions_check", all_partitions_check),
        ("delayed_entry_check", delayed_entry_check),
        ("htf_funnel_check", htf_funnel_check),
        ("r1_check", r1_check),
        ("oos_check", oos_check),
        ("report_consistency_check", report_consistency_check),
        ("neg_v66_check", neg_v66_check),
    ):
        if sub_res.get("status") != "PASS":
            verifier_violations.append(f"{sub_name}_FAILED:{sub_res.get('violations')}")

    report_17 = {
        "version": VERSION_V17931213,
        "schema_version": SCHEMA_VERSION_V17931213,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "dataset_sha256": dataset_sha,
        "verifier_pid": verifier_pid,
        "orchestrator_pid": orchestrator_pid,
        "separate_process_verified": separate_process_ok,
        "elapsed_seconds": round(time.time() - t0, 3),
        "status": "PASS" if not verifier_violations else "FAIL",
        "violations": verifier_violations,
        "checks": {
            "dataset_and_bar_closure_ok": dataset_ok,
            "source_freeze": freeze_check["status"],
            "prior_v7_to_v12_artifacts_byte_immutable": prior_immutability_ok,
            "p0_e1_delayed_entry_and_structural_sl_tp": delayed_entry_check["status"],
            "p0_e2_e3_htf_unicorn_and_dynamic_si8_si9_funnel": htf_funnel_check["status"],
            "phase_c_r1_loss_forensics_separation": r1_check["status"],
            "phase_d_all_30_partitions_raw_stream_reconstructed": all_partitions_check["status"],
            "phase_d_source_execution_ledger_reconciled": all_partitions_check["source_execution_reconciliation"]["status"],
            "p0_e4_authoritative_report_and_gates_consistency": report_consistency_check["status"],
            "p1_e5_async_worker_resource_and_determinism": async_proc_check["status"],
            "phase_g_fresh_pytest_junit_bijection": junit_bijection_check["status"],
            "phase_h_negative_controls_v01_to_v66": neg_v66_check["status"],
            "p1_e6_oos_and_activation_lock": oos_check["status"],
        },
    }
    write_json(run_dir / "17_independent_verifier_report.json", report_17)

    required_files = [f for f in REQUIRED_ARTIFACTS_V17931213 if f != "artifact_sha256_manifest.json"]
    write_json(run_dir / "18_acceptance_gate_matrix.json", {"status": "PRELIM", "run_id": run_id})
    build_and_write_artifact_manifest(run_dir)
    manifest_check = verify_artifact_manifest_v17931211(run_dir, required_files)

    gate_matrix_18 = evaluate_v17931213_gate_matrix(
        auth_metrics,
        delayed_entry_check=delayed_entry_check,
        htf_funnel_check=htf_funnel_check,
        r1_check=r1_check,
        junit_check=junit_bijection_check,
        lineage_30_check=all_partitions_check,
        async_proc_check=async_proc_check,
        report_consistency_check=report_consistency_check,
        oos_check=oos_check,
        neg_v66_check=neg_v66_check,
        manifest_check=manifest_check,
        freeze_check=freeze_check,
        phase_c_status="BLOCKED",
    )
    write_json(run_dir / "18_acceptance_gate_matrix.json", gate_matrix_18)

    preflight_00["status"] = "COMPLETED" if report_17["status"] == "PASS" else "FAILED"
    preflight_00["independent_verifier_status"] = report_17["status"]
    preflight_00["independent_verifier_pid"] = verifier_pid
    preflight_00["orthogonal_verdicts"] = gate_matrix_18["orthogonal_verdicts"]
    write_json(run_dir / "00_preflight_and_branch_identity.json", preflight_00)

    append_stage_status(
        run_dir,
        "STAGE_D_INDEPENDENT_SEPARATE_PROCESS_VERIFIER",
        "COMPLETED",
        verifier_pid=verifier_pid,
        status_verdict=report_17["status"],
    )

    build_and_write_artifact_manifest(run_dir)
    final_manifest_check = verify_artifact_manifest_v17931211(run_dir, required_files)
    if final_manifest_check["status"] != "PASS" or report_17["status"] != "PASS":
        raise RuntimeError(
            f"V17_9_3_1_2_13_INDEPENDENT_VERIFIER_FAILED:report={report_17['violations']},manifest={final_manifest_check['violations']}"
        )

    return {
        "status": "PASS",
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "verifier_pid": verifier_pid,
        "orchestrator_pid": orchestrator_pid,
        "verified_files_count": final_manifest_check["verified_files_count"],
        "manifest_sha256": file_sha256(run_dir / "artifact_sha256_manifest.json"),
        "orthogonal_verdicts": gate_matrix_18["orthogonal_verdicts"],
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: v17_9_3_1_2_13_independent_verifier.py <run_dir>")
    result = run_independent_verification_v17931213(Path(sys.argv[1]))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result.get("status") == "PASS" else 1)
