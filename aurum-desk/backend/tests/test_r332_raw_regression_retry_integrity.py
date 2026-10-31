"""
backend/tests/test_r332_raw_regression_retry_integrity.py
=========================================================
AURUM DESK V17.9.0.1.3 R3.3.2 — Raw Regression Truth, Non-Masking Retries,
Real Worker Provenance & Independent Verifier Fault-Injection Suite (R332-01 .. R332-36)

Each test constructs a complete, valid R3.3.2 evidence bundle fixture on disk,
verifies that the unmodified positive control passes `verify_r332_evidence_bundle()`,
injects one deliberate fault or mutation, executes the real production verifier,
and asserts fail-closed rejection with the specific expected violation code.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from tools.acceptance_verdict import (
    MANDATORY_GATES_V17_9_0,
    verify_persisted_acceptance_artifacts,
    verify_r332_evidence_bundle,
)
from tools.runtime_execution_logger import (
    PRE_REFACTOR_BASELINE_COMMIT_SHA,
    R332_MANDATORY_ARTIFACTS,
    compute_payload_sha256,
    create_r332_run_context,
    finalize_r332_run_manifest,
)


def _refresh_r332_manifest_hashes(run_dir: Path, code_sha: str = "a" * 40, run_id: str = "r332_test_run_01") -> None:
    excluded = {
        "R332_ARTIFACT_SHA256_MANIFEST.json",
        "R332_INDEPENDENT_VERIFICATION.json",
        "R332_FINAL_ACCEPTANCE_AUDIT.md",
    }
    artifacts_list = []
    for fname in R332_MANDATORY_ARTIFACTS:
        if fname in excluded:
            continue
        p = run_dir / fname
        if p.exists():
            raw = p.read_bytes()
            artifacts_list.append(
                {
                    "filename": fname,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                }
            )
    sha_man = {
        "schema_version": "R332_ARTIFACT_SHA256_MANIFEST_V1",
        "run_id": run_id,
        "tested_source_sha": code_sha,
        "artifacts": artifacts_list,
    }
    (run_dir / "R332_ARTIFACT_SHA256_MANIFEST.json").write_text(
        json.dumps(sha_man, indent=2),
        encoding="utf-8",
    )


def _build_valid_r332_bundle_fixture(
    base_dir: Path,
    *,
    run_id: str = "r332_test_run_01",
    code_sha: str = "a" * 40,
    dataset_sha: str = "6bcac947a842db06b4edb11e3db8164421dfcd27a3c0b9d6e571bf07543c3fb5",
) -> Path:
    run_dir = base_dir / run_id
    ctx = create_r332_run_context(
        run_id=run_id,
        frozen_executable_sha=code_sha,
        historical_baseline_sha=PRE_REFACTOR_BASELINE_COMMIT_SHA,
        dataset_sha256=dataset_sha,
        run_dir=run_dir,
        verify_git=False,
        allow_join_existing=True,
    )
    init_doc = json.loads((run_dir / "R332_INITIAL_RUN_MANIFEST.json").read_text(encoding="utf-8"))
    init_doc["expected_backend_test_files"] = ["backend/tests/test_sample_a.py", "backend/tests/test_sample_b.py"]
    (run_dir / "R332_INITIAL_RUN_MANIFEST.json").write_text(json.dumps(init_doc, indent=2), encoding="utf-8")
    created_ms = int(init_doc["created_at_ms"])

    raw_primary_dir = run_dir / "reports" / "raw" / "primary"
    raw_retries_dir = run_dir / "reports" / "raw" / "retries"
    reports_dir = run_dir / "reports"
    junit_dir = run_dir / "junit"
    logs_dir = run_dir / "logs"
    raw_primary_dir.mkdir(parents=True, exist_ok=True)
    raw_retries_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    junit_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Raw Primary Shard XML & Log
    shard_0_xml = raw_primary_dir / "shard_0.xml"
    shard_0_xml.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="pytest_shard_0" tests="2" errors="0" failures="0" skipped="0" time="0.100">'
        '<testcase classname="backend.tests.test_sample_a" name="test_one" time="0.050" />'
        '<testcase classname="backend.tests.test_sample_b" name="test_two" time="0.050" />'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    shard_0_xml_sha = hashlib.sha256(shard_0_xml.read_bytes()).hexdigest()

    shard_0_log = logs_dir / "pytest_shard_0.log"
    shard_0_log.write_text(
        "EXECUTION_MODE: ACTUAL_SUBPROCESS\n"
        "ATTEMPT_TYPE: PRIMARY\n"
        "COMMAND: python -m pytest -q backend/tests/test_sample_a.py backend/tests/test_sample_b.py\n"
        f"CWD: {REPO_ROOT}\n"
        "PID: 12345\n"
        f"STARTED_AT_UTC: {init_doc['created_at_utc']}\n"
        f"COMPLETED_AT_UTC: {init_doc['created_at_utc']}\n"
        "DURATION_MS: 100\n"
        "EXIT_CODE: 0\n"
        "--- STDOUT ---\n2 passed in 0.10s\n--- STDERR ---\n",
        encoding="utf-8",
    )
    shard_0_log_sha = hashlib.sha256(shard_0_log.read_bytes()).hexdigest()

    # 2. Derived Gate XML + Sidecar + Honest Projection Log
    gate_xml = reports_dir / "g01_unit.xml"
    gate_xml.write_text(shard_0_xml.read_text(encoding="utf-8"), encoding="utf-8")
    gate_xml_sha = hashlib.sha256(gate_xml.read_bytes()).hexdigest()
    gate_meta = reports_dir / "g01_unit.xml.meta.json"
    gate_meta.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": code_sha,
                "xml_sha256": gate_xml_sha,
                "execution_mode": "DERIVED_PROJECTION",
                "provenance": "DERIVED_VIEW_FROM_PRIMARY_EXECUTION",
                "exit_code": "not_applicable",
                "source_primary_exit_code": 0,
                "source_attempt_ids": ["primary_pytest_shard_0"],
                "source_xml_hashes": {"primary_pytest_shard_0": shard_0_xml_sha},
                "counts": {"collected": 2, "passed": 2, "failed": 0, "skipped": 0, "errors": 0},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    gate_log = logs_dir / "g01_unit.log"
    gate_log.write_text(
        "EXECUTION_MODE: DERIVED_PROJECTION\n"
        "PROVENANCE: DERIVED_VIEW_FROM_PRIMARY_EXECUTION\n"
        "SOURCE_ATTEMPT_IDS: primary_pytest_shard_0\n"
        "SOURCE_PRIMARY_EXIT_CODE: 0\n"
        "DERIVED_EXIT_CODE: not_applicable\n"
        "--- PROJECTED TESTCASES ---\n2 passed in 0.10s\n",
        encoding="utf-8",
    )

    batch_xml = junit_dir / "pytest_batch_0.xml"
    batch_xml.write_text(shard_0_xml.read_text(encoding="utf-8"), encoding="utf-8")
    batch_xml_sha = hashlib.sha256(batch_xml.read_bytes()).hexdigest()
    (junit_dir / "pytest_batch_0.xml.meta.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": code_sha,
                "xml_sha256": batch_xml_sha,
                "execution_mode": "DERIVED_VIEW_FROM_PRIMARY_EXECUTION",
                "provenance": "DERIVED_VIEW_FROM_PRIMARY_EXECUTION",
                "source_attempt_id": "primary_pytest_shard_0",
                "source_xml_sha256": shard_0_xml_sha,
                "primary_exit_code": 0,
                "exit_code": 0,
                "clean_acceptance_status": "PASS",
                "counts": {"collected": 2, "passed": 2, "failed": 0, "skipped": 0, "errors": 0},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # 3. Master Primary & Retry Attempts
    primary_row = {
        "attempt_id": "primary_pytest_shard_0",
        "primary_attempt_id": "primary_pytest_shard_0",
        "attempt_type": "PRIMARY",
        "source_attempt_id": None,
        "execution_mode": "ACTUAL_SUBPROCESS",
        "shard_idx": 0,
        "run_id": run_id,
        "source_sha": code_sha,
        "tested_source_sha": code_sha,
        "task_id": "pytest_shard_0",
        "worker_id": "pytest_shard_0",
        "pid": 12345,
        "ppid": 12300,
        "active_workers_at_start": 1,
        "command": ["python", "-m", "pytest", "-q", "backend/tests/test_sample_a.py", "backend/tests/test_sample_b.py"],
        "files": ["backend/tests/test_sample_a.py", "backend/tests/test_sample_b.py"],
        "cwd": str(REPO_ROOT),
        "started_at_utc": init_doc["created_at_utc"],
        "started_at_ms": created_ms + 10,
        "completed_at_utc": init_doc["created_at_utc"],
        "completed_at_ms": created_ms + 110,
        "duration_ms": 100,
        "timed_out": False,
        "exit_code": 0,
        "primary_exit_code": 0,
        "status": "PASS",
        "primary_status": "PASS",
        "primary_counts": {"collected": 2, "passed": 2, "failed": 0, "skipped": 0, "errors": 0},
        "raw_xml_path": "reports/raw/primary/shard_0.xml",
        "raw_xml_sha256": shard_0_xml_sha,
        "log_path": "logs/pytest_shard_0.log",
        "log_sha256": shard_0_log_sha,
        "effective_test_outcome": "CLEAN_PASS",
        "clean_acceptance_status": "PASS",
    }
    (run_dir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl").write_text(json.dumps(primary_row) + "\n", encoding="utf-8")
    (run_dir / "R332_MASTER_RETRY_ATTEMPTS.jsonl").write_text("", encoding="utf-8")
    (run_dir / "R332_SOURCE_COMMAND_EXECUTIONS.jsonl").write_text(json.dumps(primary_row) + "\n", encoding="utf-8")
    (run_dir / "R332_PHASE_H_WORKER_EXECUTIONS.jsonl").write_text(json.dumps(primary_row) + "\n", encoding="utf-8")

    (run_dir / "R332_MASTER_FAILURE_AND_FLAKE_REPORT.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0-r3.3.2",
                "run_id": run_id,
                "tested_source_sha": code_sha,
                "primary_shards_total": 1,
                "primary_shards_succeeded": 1,
                "primary_shards_failed": 0,
                "primary_failed_stems": [],
                "primary_failed_testcases_count": 0,
                "primary_failed_testcases": [],
                "isolated_retries_count": 0,
                "isolated_retries": [],
                "flaky_or_isolation_dependent_count": 0,
                "masked_primary_failures_count": 0,
                "clean_primary_acceptance_status": "PASS",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    acc_doc = {
        "schema_version": 2,
        "run_id": run_id,
        "tested_source_sha": code_sha,
        "status": "PASS",
        "max_workers": 4,
        "max_configured_workers": 4,
        "max_active_workers_observed": 1,
        "batches_count": 1,
        "wall_clock_ms": 100,
        "scheduled_tasks": 1,
        "succeeded_tasks": 1,
        "failed_tasks": 0,
        "blocked_tasks": 0,
        "cancelled_tasks": 0,
        "retry_attempts_count": 0,
        "retry_succeeded_count": 0,
        "retry_failed_count": 0,
        "total_subprocess_attempts": 1,
        "expected_nodes_count": 2,
        "completed_nodes_count": 2,
        "skipped_nodes_count": 0,
        "failed_nodes_count": 0,
        "unaccounted_nodes_count": 0,
        "orphan_processes_remaining": 0,
    }
    (run_dir / "R332_MASTER_ATTEMPT_ACCOUNTING.json").write_text(json.dumps(acc_doc, indent=2), encoding="utf-8")

    (run_dir / "R332_RAW_JUNIT_SHA256_MANIFEST.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0-r3.3.2",
                "run_id": run_id,
                "tested_source_sha": code_sha,
                "primary_shard_xml_count": 1,
                "retry_xml_count": 0,
                "entries": [
                    {
                        "shard_idx": 0,
                        "primary_attempt_id": "primary_pytest_shard_0",
                        "attempt_type": "PRIMARY",
                        "raw_xml_path": "reports/raw/primary/shard_0.xml",
                        "raw_xml_sha256": shard_0_xml_sha,
                        "exists": True,
                        "size_bytes": shard_0_xml.stat().st_size,
                        "primary_exit_code": 0,
                        "primary_counts": {"collected": 2, "passed": 2, "failed": 0, "skipped": 0, "errors": 0},
                        "log_path": "logs/pytest_shard_0.log",
                        "log_sha256": shard_0_log_sha,
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (run_dir / "R332_PRIMARY_VS_RETRY_JUNIT_RECONCILIATION.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0-r3.3.2",
                "run_id": run_id,
                "tested_source_sha": code_sha,
                "primary_execution_scope": {
                    "shards_count": 1,
                    "backend_files_scheduled": 2,
                    "backend_files_executed": 2,
                    "total_tests_collected": 2,
                    "passed": 2,
                    "failed": 0,
                    "errors": 0,
                    "skipped": 0,
                    "unique_testcase_nodes": 2,
                },
                "retry_execution_scope": {
                    "retry_attempts_count": 0,
                    "total_tests_collected": 0,
                    "passed": 0,
                    "failed": 0,
                    "errors": 0,
                    "skipped": 0,
                },
                "replacement_performed": False,
                "primary_exit_codes_rewritten": False,
                "raw_shard_xml_retained": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (run_dir / "R332_GATE_PROJECTION_PROVENANCE.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0-r3.3.2",
                "run_id": run_id,
                "tested_source_sha": code_sha,
                "total_gate_xml_files": 1,
                "derived_projections_count": 1,
                "actual_subprocess_gates_count": 0,
                "projections": {
                    "g01_unit.xml": {
                        "gate_xml": "g01_unit.xml",
                        "log_name": "g01_unit",
                        "execution_mode": "DERIVED_PROJECTION",
                        "provenance": "DERIVED_VIEW_FROM_PRIMARY_EXECUTION",
                        "exit_code": "not_applicable",
                        "source_primary_exit_code": 0,
                        "effective_gate_status": "PASS",
                        "source_attempt_ids": ["primary_pytest_shard_0"],
                        "source_xml_hashes": {"primary_pytest_shard_0": shard_0_xml_sha},
                        "xml_sha256": gate_xml_sha,
                        "counts": {"collected": 2, "passed": 2, "failed": 0, "skipped": 0, "errors": 0},
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    gates_dict = {
        gid: {"status": "PASS", "tested_source_sha": code_sha, "exit_code": 0}
        for gid in MANDATORY_GATES_V17_9_0
    }
    (run_dir / "R332_GATE_RESULTS.json").write_text(
        json.dumps({"run_id": run_id, "tested_source_sha": code_sha, "gates": gates_dict}, indent=2),
        encoding="utf-8",
    )

    (run_dir / "R332_JUNIT_SCOPE_INVENTORY.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": code_sha,
                "status": "PASS",
                "gate_xml_files_count": 1,
                "gate_xml_raw_total_tests": 2,
                "gate_xml_unique_test_nodes": 2,
                "gate_xml_overlapping_executions": 0,
                "gate_xml_scope": {
                    "xml_files_count": 1,
                    "raw_total_tests": 2,
                    "raw_total_passed": 2,
                    "raw_total_failures": 0,
                    "raw_total_errors": 0,
                    "raw_total_skipped": 0,
                    "unique_test_node_count": 2,
                    "overlapping_test_executions": 0,
                },
                "suite_xml_scope": {
                    "xml_files_count": 1,
                    "raw_total_tests": 2,
                    "raw_total_passed": 2,
                    "raw_total_failures": 0,
                    "raw_total_errors": 0,
                    "raw_total_skipped": 0,
                    "unique_test_node_count": 2,
                    "overlapping_test_executions": 0,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "R332_JUNIT_PER_XML_RECALCULATION.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": code_sha,
                "status": "PASS",
                "xml_files_count": 1,
                "per_xml_records": {
                    "g01_unit.xml": {
                        "xml_file": "g01_unit.xml",
                        "xml_sha256": gate_xml_sha,
                        "tests": 2,
                        "passed": 2,
                        "failures": 0,
                        "errors": 0,
                        "skipped": 0,
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    all_neg_ids = sorted(
        {f"N{i:02d}" for i in range(1, 149)}
        | {f"F{i:02d}" for i in range(1, 21)}
        | {f"R331-{i:02d}" for i in range(1, 31)}
        | {f"R332-{i:02d}" for i in range(1, 37)}
    )
    (run_dir / "R332_NEGATIVE_CONTROL_COLLECTION.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": code_sha,
                "total_collected": len(all_neg_ids),
                "collected_control_ids": all_neg_ids,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "R332_NEGATIVE_CONTROL_RESULTS.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": code_sha,
                "total_negative_controls": len(all_neg_ids),
                "passed_negative_controls": len(all_neg_ids),
                "failed_negative_controls": 0,
                "results": [{"control_id": cid, "status": "PASS"} for cid in all_neg_ids],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # 4. Five-way runtime invocation events, snapshots, captures & parity
    t0_ms = created_ms + 10
    events: List[Dict[str, Any]] = []
    snapshots: List[Dict[str, Any]] = []
    captures: List[Dict[str, Any]] = []

    cycle_runtimes = [
        ("HISTORICAL_PRODUCTION", "services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_cycle", PRE_REFACTOR_BASELINE_COMMIT_SHA),
        ("PRODUCTION", "backend.services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_cycle", code_sha),
        ("MARKET_SHADOW", "backend.services.market_shadow_service.MarketShadowService.evaluate_shadow_cycle", code_sha),
    ]
    model_ids = [
        "SMC_M1_REVERSAL",
        "SMC_M1_CONTINUATION",
        "SMC_M1_FVG_CONTINUATION",
        "SMC_M1_SWEEP_RECLAIM",
        "CG01",
    ]
    model_runtimes = [
        ("HISTORICAL_PRODUCTION", "services.scalp_m1_model_service.ScalpM1ModelService.evaluate_m1_reversal_candidate", PRE_REFACTOR_BASELINE_COMMIT_SHA),
        ("SHARED_STRATEGY_EVALUATOR", "backend.services.shared_strategy_evaluator.SharedStrategyEvaluator.evaluate_decision", code_sha),
        ("PRODUCTION", "backend.services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_shared_decision_snapshot", code_sha),
        ("MARKET_SHADOW", "backend.services.market_shadow_service.MarketShadowService.evaluate_shared_decision_snapshot", code_sha),
        ("CHRONOLOGICAL_BACKTEST", "backend.services.v17_8_backtest_runner.execute_chronological_backtest_window_for_parity", code_sha),
    ]

    mono_ns = 1_000_000_000
    for idx in range(30):
        ts_ms = 1761696000000 + idx * 60000
        can_in = {"symbol": "XAUUSDT", "decision_timestamp_ms": ts_ms, "m1_closes": [2745.1 + idx]}
        can_cfg = {"evaluator_version": "17.9.0.1.3", "min_net_rr": 2.0}
        can_in_h = hashlib.sha256(json.dumps(can_in, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        can_cfg_h = hashlib.sha256(json.dumps(can_cfg, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

        cycle_ids: Dict[str, str] = {}
        for rt_name, c_ident, s_sha in cycle_runtimes:
            c_exec_id = f"cycle_{rt_name}_{ts_ms}"
            cycle_ids[rt_name] = c_exec_id
            raw_in = {"runtime": rt_name, "scope": "CYCLE", "can": can_in}
            raw_cfg = {"runtime": rt_name, "scope": "CYCLE", "cfg": can_cfg}
            raw_in_h = hashlib.sha256(json.dumps(raw_in, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            raw_cfg_h = hashlib.sha256(json.dumps(raw_cfg, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            snap_id = f"snap_{c_exec_id}"

            mono_ns += 100
            c_st_ns = mono_ns
            events.append({
                "event_id": f"evt_start_{c_exec_id}",
                "event_type": "INVOCATION_STARTED",
                "run_id": run_id,
                "worker_id": "test_worker",
                "producer_pid": 1234,
                "execution_id": c_exec_id,
                "parent_cycle_invocation_id": None,
                "invocation_scope": "CYCLE_EVALUATION",
                "scenario_id": f"CYCLE_{rt_name}_{ts_ms}",
                "runtime_name": rt_name,
                "callable_identity": c_ident,
                "model_id": "ALL_MODELS_CYCLE",
                "decision_timestamp_ms": ts_ms,
                "timestamp_utc_ms": t0_ms,
                "timestamp_monotonic_ns": c_st_ns,
                "pre_call_monotonic_ns": c_st_ns,
                "source_sha": s_sha,
                "arguments_hash": can_in_h,
            })
            mono_ns += 100
            c_in_ns = mono_ns
            snapshots.append({
                "schema_version": "R33_RUNTIME_INPUT_SNAPSHOT_V1",
                "snapshot_id": snap_id,
                "run_id": run_id,
                "worker_id": "test_worker",
                "producer_pid": 1234,
                "source_sha": s_sha,
                "execution_id": c_exec_id,
                "parent_cycle_invocation_id": None,
                "invocation_scope": "CYCLE_EVALUATION",
                "scenario_id": f"CYCLE_{rt_name}_{ts_ms}",
                "runtime_name": rt_name,
                "callable_identity": c_ident,
                "model_id": "ALL_MODELS_CYCLE",
                "decision_timestamp_ms": ts_ms,
                "captured_at_utc_ms": t0_ms,
                "captured_at_monotonic_ns": c_in_ns,
                "raw_input_payload": raw_in,
                "canonical_input_payload": can_in,
                "raw_config_payload": raw_cfg,
                "canonical_config_payload": can_cfg,
                "declared_raw_input_hash": raw_in_h,
                "declared_canonical_input_hash": can_in_h,
                "declared_raw_config_hash": raw_cfg_h,
                "declared_canonical_config_hash": can_cfg_h,
            })
            events.append({
                "event_id": f"evt_in_{c_exec_id}",
                "event_type": "ACTUAL_INPUT_CAPTURED",
                "run_id": run_id,
                "worker_id": "test_worker",
                "producer_pid": 1234,
                "execution_id": c_exec_id,
                "parent_cycle_invocation_id": None,
                "invocation_scope": "CYCLE_EVALUATION",
                "scenario_id": f"CYCLE_{rt_name}_{ts_ms}",
                "runtime_name": rt_name,
                "callable_identity": c_ident,
                "model_id": "ALL_MODELS_CYCLE",
                "decision_timestamp_ms": ts_ms,
                "timestamp_utc_ms": t0_ms,
                "timestamp_monotonic_ns": c_in_ns,
                "source_sha": s_sha,
                "snapshot_id": snap_id,
                "raw_input_hash": raw_in_h,
                "canonical_input_hash": can_in_h,
                "raw_config_hash": raw_cfg_h,
                "canonical_config_hash": can_cfg_h,
            })
            mono_ns += 100
            c_done_ns = mono_ns
            events.append({
                "event_id": f"evt_done_{c_exec_id}",
                "event_type": "INVOCATION_COMPLETED",
                "run_id": run_id,
                "worker_id": "test_worker",
                "producer_pid": 1234,
                "execution_id": c_exec_id,
                "parent_cycle_invocation_id": None,
                "invocation_scope": "CYCLE_EVALUATION",
                "scenario_id": f"CYCLE_{rt_name}_{ts_ms}",
                "runtime_name": rt_name,
                "callable_identity": c_ident,
                "model_id": "ALL_MODELS_CYCLE",
                "decision_timestamp_ms": ts_ms,
                "timestamp_utc_ms": t0_ms,
                "timestamp_monotonic_ns": c_done_ns,
                "pre_call_monotonic_ns": c_in_ns,
                "post_call_monotonic_ns": c_done_ns,
                "source_sha": s_sha,
                "output_decision_id": f"dec_{c_exec_id}",
                "output_status": "CYCLE_COMPLETED",
                "output_fingerprint": "f" * 64,
                "duration_ms": 0.2,
                "exit_status": "SUCCESS",
            })

        for mid in model_ids:
            scen_id = f"HIST_REAL_{idx + 1:03d}_{mid}_{ts_ms}"
            for rt_name, m_ident, s_sha in model_runtimes:
                if rt_name == "HISTORICAL_PRODUCTION" and mid == "CG01":
                    m_ident = "services.cg01_engine.evaluate_cg01_candidate"
                m_exec_id = f"exec_{scen_id}_{rt_name}"
                p_cid = cycle_ids.get(rt_name)
                raw_in = {"runtime": rt_name, "model_id": mid, "can": can_in}
                raw_cfg = {"runtime": rt_name, "model_id": mid, "cfg": can_cfg}
                raw_in_h = hashlib.sha256(json.dumps(raw_in, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                raw_cfg_h = hashlib.sha256(json.dumps(raw_cfg, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                snap_id = f"snap_{m_exec_id}"

                mono_ns += 100
                t_start_ns = mono_ns
                events.append({
                    "event_id": f"evt_start_{m_exec_id}",
                    "event_type": "INVOCATION_STARTED",
                    "run_id": run_id,
                    "worker_id": "test_worker",
                    "producer_pid": 1234,
                    "execution_id": m_exec_id,
                    "parent_cycle_invocation_id": p_cid,
                    "invocation_scope": "MODEL_EVALUATION",
                    "scenario_id": scen_id,
                    "runtime_name": rt_name,
                    "callable_identity": m_ident,
                    "model_id": mid,
                    "decision_timestamp_ms": ts_ms,
                    "timestamp_utc_ms": t0_ms,
                    "timestamp_monotonic_ns": t_start_ns,
                    "pre_call_monotonic_ns": t_start_ns,
                    "source_sha": s_sha,
                    "arguments_hash": can_in_h,
                })
                mono_ns += 100
                t_in_ns = mono_ns
                snapshots.append({
                    "schema_version": "R33_RUNTIME_INPUT_SNAPSHOT_V1",
                    "snapshot_id": snap_id,
                    "run_id": run_id,
                    "worker_id": "test_worker",
                    "producer_pid": 1234,
                    "source_sha": s_sha,
                    "execution_id": m_exec_id,
                    "parent_cycle_invocation_id": p_cid,
                    "invocation_scope": "MODEL_EVALUATION",
                    "scenario_id": scen_id,
                    "runtime_name": rt_name,
                    "callable_identity": m_ident,
                    "model_id": mid,
                    "decision_timestamp_ms": ts_ms,
                    "captured_at_utc_ms": t0_ms,
                    "captured_at_monotonic_ns": t_in_ns,
                    "raw_input_payload": raw_in,
                    "canonical_input_payload": can_in,
                    "raw_config_payload": raw_cfg,
                    "canonical_config_payload": can_cfg,
                    "declared_raw_input_hash": raw_in_h,
                    "declared_canonical_input_hash": can_in_h,
                    "declared_raw_config_hash": raw_cfg_h,
                    "declared_canonical_config_hash": can_cfg_h,
                })
                events.append({
                    "event_id": f"evt_in_{m_exec_id}",
                    "event_type": "ACTUAL_INPUT_CAPTURED",
                    "run_id": run_id,
                    "worker_id": "test_worker",
                    "producer_pid": 1234,
                    "execution_id": m_exec_id,
                    "parent_cycle_invocation_id": p_cid,
                    "invocation_scope": "MODEL_EVALUATION",
                    "scenario_id": scen_id,
                    "runtime_name": rt_name,
                    "callable_identity": m_ident,
                    "model_id": mid,
                    "decision_timestamp_ms": ts_ms,
                    "timestamp_utc_ms": t0_ms,
                    "timestamp_monotonic_ns": t_in_ns,
                    "source_sha": s_sha,
                    "snapshot_id": snap_id,
                    "raw_input_hash": raw_in_h,
                    "canonical_input_hash": can_in_h,
                    "raw_config_hash": raw_cfg_h,
                    "canonical_config_hash": can_cfg_h,
                })
                mono_ns += 100
                t_done_ns = mono_ns
                events.append({
                    "event_id": f"evt_done_{m_exec_id}",
                    "event_type": "INVOCATION_COMPLETED",
                    "run_id": run_id,
                    "worker_id": "test_worker",
                    "producer_pid": 1234,
                    "execution_id": m_exec_id,
                    "parent_cycle_invocation_id": p_cid,
                    "invocation_scope": "MODEL_EVALUATION",
                    "scenario_id": scen_id,
                    "runtime_name": rt_name,
                    "callable_identity": m_ident,
                    "model_id": mid,
                    "decision_timestamp_ms": ts_ms,
                    "timestamp_utc_ms": t0_ms,
                    "timestamp_monotonic_ns": t_done_ns,
                    "pre_call_monotonic_ns": t_in_ns,
                    "post_call_monotonic_ns": t_done_ns,
                    "source_sha": s_sha,
                    "output_decision_id": f"dec_{m_exec_id}",
                    "output_status": "ELIGIBLE",
                    "output_fingerprint": "e" * 64,
                    "duration_ms": 0.1,
                    "exit_status": "SUCCESS",
                })
                captures.append({
                    "schema_version": "R33_RUNTIME_CAPTURE_V1",
                    "run_id": run_id,
                    "worker_id": "test_worker",
                    "producer_pid": 1234,
                    "scenario_id": scen_id,
                    "runtime_name": rt_name,
                    "capture_point": m_ident,
                    "callable_identity": m_ident,
                    "execution_id": m_exec_id,
                    "parent_cycle_invocation_id": p_cid,
                    "invocation_scope": "MODEL_EVALUATION",
                    "snapshot_id": snap_id,
                    "decision_timestamp_ms": ts_ms,
                    "invocation_started_at_ms": t0_ms,
                    "invocation_completed_at_ms": t0_ms + 1,
                    "invocation_started_monotonic_ns": t_start_ns,
                    "input_captured_monotonic_ns": t_in_ns,
                    "invocation_completed_monotonic_ns": t_done_ns,
                    "source_sha": s_sha,
                    "model_id": mid,
                    "raw_input_hash": raw_in_h,
                    "canonical_input_hash": can_in_h,
                    "raw_config_hash": raw_cfg_h,
                    "canonical_config_hash": can_cfg_h,
                    "decision_id": f"dec_{m_exec_id}",
                    "decision_status": "ELIGIBLE",
                    "decision_fingerprint": "e" * 64,
                    "invocation_completed": True,
                })

    (run_dir / "R332_RUNTIME_INVOCATION_EVENTS.jsonl").write_text(
        "".join(json.dumps(e, sort_keys=True) + "\n" for e in events),
        encoding="utf-8",
    )
    (run_dir / "R332_RUNTIME_INPUT_SNAPSHOTS.jsonl").write_text(
        "".join(json.dumps(s, sort_keys=True) + "\n" for s in snapshots),
        encoding="utf-8",
    )
    (run_dir / "R332_RUNTIME_INPUT_CAPTURES.jsonl").write_text(
        "".join(json.dumps(c, sort_keys=True) + "\n" for c in captures),
        encoding="utf-8",
    )
    (run_dir / "R332_HISTORICAL_PRODUCTION_LINEAGE.jsonl").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": code_sha,
                "scenario_id": "HIST_REAL_001_SMC_M1_REVERSAL_1761696000000",
                "is_consumed": True,
                "lifecycle_stage": "CANDIDATE_CONSUMED",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    (run_dir / "R332_FIVE_WAY_PARITY.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": code_sha,
                "final_code_sha": code_sha,
                "frozen_executable_sha": code_sha,
                "five_way_verified_count": 210,
                "total_comparisons": 210,
                "exact_matches": 21,
                "expected_documented_changes": 189,
                "unexpected_divergences": 0,
                "divergences_count": 0,
                "not_comparable": 0,
                "not_executed": 0,
                "status": "PASS",
                "breakdown_by_category": {
                    "EXACT_MATCH": 21,
                    "EXPECTED_DOCUMENTED_CHANGE": 189,
                    "UNEXPECTED_DIVERGENCE": 0,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # 5. Resource and Disk Telemetry
    (run_dir / "R332_RESOURCE_AND_DISK_TELEMETRY.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": code_sha,
                "phase_h_task_accounting": acc_doc,
                "memory_unit_and_scope_report": {
                    "run_id": run_id,
                    "tested_source_sha": code_sha,
                    "final_code_sha": code_sha,
                    "source_sha": code_sha,
                    "unit": "BYTES",
                    "measurement_status": "MEASURED",
                    "status": "PASS",
                    "current_rss_bytes": 120_000_000,
                    "peak_process_rss_bytes": 180_000_000,
                    "child_rss_bytes": 40_000_000,
                    "peak_child_rss_bytes": 60_000_000,
                    "peak_process_tree_rss_bytes": 240_000_000,
                    "system_total_memory_bytes": 16 * 1024 * 1024 * 1024,
                    "system_used_memory_bytes": 8 * 1024 * 1024 * 1024,
                    "orphan_worker_count": 0,
                },
                "disk_budget_and_cleanup_report": {
                    "run_id": run_id,
                    "tested_source_sha": code_sha,
                    "final_code_sha": code_sha,
                    "source_sha": code_sha,
                    "measurement_status": "MEASURED",
                    "status": "PASS",
                    "reserved_disk_floor_bytes": 2_000_000_000,
                    "free_disk_bytes_preflight": 10_000_000_000,
                    "minimum_free_disk_bytes_observed": 9_500_000_000,
                    "free_disk_bytes_postflight": 9_800_000_000,
                    "orphan_worker_count": 0,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # 6. Backtest Manifest, Variants ABCD, Trade Ledger & Matched Cohort/OOS
    (run_dir / "R332_EXACT_SOURCE_BACKTEST_MANIFEST.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "PASS",
                "backtest_source_sha": code_sha,
                "tested_source_sha": code_sha,
                "final_code_sha": code_sha,
                "dataset_sha256": dataset_sha,
                "started_at_ms": created_ms + 20,
                "completed_at_ms": created_ms + 500,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "R332_BACKTEST_A_B_C_D.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "backtest_source_sha": code_sha,
                "tested_source_sha": code_sha,
                "variants": {
                    "A": {"simulated_trades": 489, "net_expectancy_R": -0.2569},
                    "B": {"simulated_trades": 489, "net_expectancy_R": -0.2647},
                    "C": {"simulated_trades": 456, "net_expectancy_R": -0.2738},
                    "D": {"simulated_trades": 456, "net_expectancy_R": -0.2660},
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "R332_BACKTEST_TRADE_LEDGER.jsonl").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "source_sha": code_sha,
                "variant": "C",
                "trade_id": "T001",
                "symbol": "XAUUSDT",
                "net_r": -1.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "R332_MATCHED_COHORT_AND_OOS.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "backtest_source_sha": code_sha,
                "tested_source_sha": code_sha,
                "out_of_sample_verdict": "OUT_OF_SAMPLE_INCONCLUSIVE",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (run_dir / "R332_ROOT_CAUSE_AND_FUNCTION_CLOSURE.md").write_text(
        "# R3.3.2 Root Cause and Function Closure\n",
        encoding="utf-8",
    )
    (run_dir / "R332_FINAL_ACCEPTANCE_AUDIT.md").write_text(
        "# R3.3.2 Final Acceptance Audit\n",
        encoding="utf-8",
    )

    finalize_r332_run_manifest(
        ctx,
        final_status="PASS",
        backtest_source_sha=code_sha,
        verdict_summary={"release_readiness_verdict": "VERIFIED_FOUNDATION_CLOSURE"},
        task_accounting_summary=acc_doc,
    )
    _refresh_r332_manifest_hashes(run_dir, code_sha=code_sha, run_id=run_id)
    verif = verify_r332_evidence_bundle(run_dir, code_sha, expected_run_id=run_id)
    (run_dir / "R332_INDEPENDENT_VERIFICATION.json").write_text(json.dumps(verif, indent=2), encoding="utf-8")
    return run_dir


# ==============================================================================
# R332-01 .. R332-36 FAULT-INJECTION & POSITIVE CONTROL SUITE
# ==============================================================================


def test_r332_01_primary_subprocess_nonzero_plus_retry_pass_not_clean_pass() -> None:
    """R332-01: primary subprocess exit_code=1 + retry=0 => NOT CLEAN PASS."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        p_path = bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl"
        row = json.loads(p_path.read_text(encoding="utf-8").strip())
        row["exit_code"] = 1
        row["primary_exit_code"] = 1
        row["primary_status"] = "FAIL"
        row["effective_test_outcome"] = "FLAKY_OR_ISOLATION_DEPENDENT"
        p_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("PRIMARY_SUBPROCESS_NONZERO_EXIT_OR_FAIL" in v for v in res["violations"])


def test_r332_02_original_junit_has_failing_testcase_derived_xml_all_pass_rejected() -> None:
    """R332-02: original JUnit has one failing testcase + derived XML all PASS => reject if failure omitted."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        s_xml = bdir / "reports" / "raw" / "primary" / "shard_0.xml"
        s_xml.write_text(
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuites><testsuite name="pytest_shard_0" tests="2" errors="0" failures="1" skipped="0" time="0.100">'
            '<testcase classname="backend.tests.test_sample_a" name="test_one" time="0.050"><failure message="boom">err</failure></testcase>'
            '<testcase classname="backend.tests.test_sample_b" name="test_two" time="0.050" />'
            "</testsuite></testsuites>",
            encoding="utf-8",
        )
        new_sha = hashlib.sha256(s_xml.read_bytes()).hexdigest()
        p_path = bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl"
        row = json.loads(p_path.read_text(encoding="utf-8").strip())
        row["raw_xml_sha256"] = new_sha
        p_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        rj_path = bdir / "R332_RAW_JUNIT_SHA256_MANIFEST.json"
        rj = json.loads(rj_path.read_text(encoding="utf-8"))
        rj["entries"][0]["raw_xml_sha256"] = new_sha
        rj_path.write_text(json.dumps(rj, indent=2), encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("RAW_PRIMARY_SHARD_XML_HAS_FAILURES" in v for v in res["violations"])


def test_r332_03_mutate_primary_exit_code_1_to_0_detected_against_raw_log() -> None:
    """R332-03: mutate primary_exit_code 1 to 0 => detect raw attempt/hash mismatch."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        log_p = bdir / "logs" / "pytest_shard_0.log"
        log_p.write_text(log_p.read_text(encoding="utf-8").replace("EXIT_CODE: 0", "EXIT_CODE: 1"), encoding="utf-8")
        new_log_sha = hashlib.sha256(log_p.read_bytes()).hexdigest()
        p_path = bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl"
        row = json.loads(p_path.read_text(encoding="utf-8").strip())
        row["log_sha256"] = new_log_sha
        row["primary_exit_code"] = 0
        row["exit_code"] = 0
        p_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("PRIMARY_EXIT_CODE_MUTATED_FROM_RAW_LOG" in v or "RAW_LOG_RECORDS_NONZERO_EXIT" in v for v in res["violations"])


def test_r332_04_missing_original_raw_shard_xml_fails_closed() -> None:
    """R332-04: missing original raw shard XML => fail closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        (bdir / "reports" / "raw" / "primary" / "shard_0.xml").unlink()
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("MISSING_ORIGINAL_RAW_SHARD_XML" in v for v in res["violations"])


def test_r332_05_missing_original_raw_shard_log_fails_closed() -> None:
    """R332-05: missing original raw shard log => fail closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        (bdir / "logs" / "pytest_shard_0.log").unlink()
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("MISSING_ORIGINAL_RAW_SHARD_LOG" in v for v in res["violations"])


def test_r332_06_altered_raw_xml_without_manifest_sha_update_rejected() -> None:
    """R332-06: altered raw XML without manifest SHA update => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        s_xml = bdir / "reports" / "raw" / "primary" / "shard_0.xml"
        s_xml.write_text(s_xml.read_text(encoding="utf-8") + "\n<!-- mutated -->\n", encoding="utf-8")
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("ALTERED_RAW_XML_SHA256_MISMATCH" in v for v in res["violations"])


def test_r332_07_altered_retry_xml_with_matching_metadata_only_rejected() -> None:
    """R332-07: altered retry XML with matching metadata only => reject lineage."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        r_xml = bdir / "reports" / "raw" / "retries" / "primary_pytest_shard_0__retry_test_sample_a.xml"
        r_xml.write_text(
            '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="retry" tests="1" failures="1">'
            '<testcase classname="backend.tests.test_sample_a" name="test_one"><failure>fail</failure></testcase>'
            "</testsuite></testsuites>",
            encoding="utf-8",
        )
        r_sha = hashlib.sha256(r_xml.read_bytes()).hexdigest()
        r_row = {
            "retry_attempt_id": "retry_test_sample_a",
            "source_attempt_id": "primary_pytest_shard_0",
            "run_id": "r332_test_run_01",
            "source_sha": "a" * 40,
            "tested_source_sha": "a" * 40,
            "pid": 22345,
            "command": ["python", "-m", "pytest"],
            "started_at_ms": 2000,
            "completed_at_ms": 2100,
            "exit_code": 0,
            "counts": {"collected": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0},
            "retry_xml_path": "reports/raw/retries/primary_pytest_shard_0__retry_test_sample_a.xml",
            "retry_xml_sha256": r_sha,
            "effective_test_outcome": "FLAKY_OR_ISOLATION_DEPENDENT",
        }
        (bdir / "R332_MASTER_RETRY_ATTEMPTS.jsonl").write_text(json.dumps(r_row) + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("ALTERED_RETRY_XML_LINEAGE_MISMATCH" in v for v in res["violations"])


def test_r332_08_duplicate_attempt_id_rejected() -> None:
    """R332-08: duplicate attempt ID => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        p_path = bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl"
        line = p_path.read_text(encoding="utf-8").strip()
        p_path.write_text(line + "\n" + line + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("DUPLICATE_ATTEMPT_ID" in v for v in res["violations"])


def test_r332_09_duplicate_testcase_id_collapsed_rejected() -> None:
    """R332-09: duplicate testcase ID accidentally collapsed => reject unreported count."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        s_xml = bdir / "reports" / "raw" / "primary" / "shard_0.xml"
        s_xml.write_text(
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuites><testsuite name="pytest_shard_0" tests="2" errors="0" failures="0" skipped="0" time="0.100">'
            '<testcase classname="backend.tests.test_sample_a" name="test_one" time="0.050" />'
            '<testcase classname="backend.tests.test_sample_a" name="test_one" time="0.050" />'
            "</testsuite></testsuites>",
            encoding="utf-8",
        )
        new_sha = hashlib.sha256(s_xml.read_bytes()).hexdigest()
        p_path = bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl"
        row = json.loads(p_path.read_text(encoding="utf-8").strip())
        row["raw_xml_sha256"] = new_sha
        p_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        rj_path = bdir / "R332_RAW_JUNIT_SHA256_MANIFEST.json"
        rj = json.loads(rj_path.read_text(encoding="utf-8"))
        rj["entries"][0]["raw_xml_sha256"] = new_sha
        rj_path.write_text(json.dumps(rj, indent=2), encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("DUPLICATE_TESTCASE_ID_IN_SHARD" in v or "PRIMARY_UNIQUE_TESTCASE_COUNT_MISMATCH" in v for v in res["violations"])


def test_r332_10_retry_reuses_primary_attempt_id_rejected() -> None:
    """R332-10: retry reuses primary attempt ID => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        r_row = {
            "retry_attempt_id": "primary_pytest_shard_0",
            "source_attempt_id": "primary_pytest_shard_0",
            "run_id": "r332_test_run_01",
            "source_sha": "a" * 40,
            "tested_source_sha": "a" * 40,
            "pid": 22345,
            "command": ["python", "-m", "pytest"],
            "started_at_ms": 2000,
            "completed_at_ms": 2100,
            "exit_code": 0,
            "counts": {"collected": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0},
            "retry_xml_path": "reports/raw/primary/shard_0.xml",
            "retry_xml_sha256": "deadbeef",
        }
        (bdir / "R332_MASTER_RETRY_ATTEMPTS.jsonl").write_text(json.dumps(r_row) + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("RETRY_REUSES_PRIMARY_ATTEMPT_ID" in v for v in res["violations"])


def test_r332_11_retry_claimed_actual_subprocess_without_pid_argv_timestamps_rejected() -> None:
    """R332-11: retry claimed ACTUAL_SUBPROCESS without PID/argv/timestamps => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        r_row = {
            "retry_attempt_id": "retry_missing_prov_01",
            "source_attempt_id": "primary_pytest_shard_0",
            "execution_mode": "ACTUAL_SUBPROCESS",
            "run_id": "r332_test_run_01",
            "source_sha": "a" * 40,
            "tested_source_sha": "a" * 40,
            "pid": None,
            "command": [],
            "started_at_ms": None,
            "completed_at_ms": None,
            "exit_code": 0,
        }
        (bdir / "R332_MASTER_RETRY_ATTEMPTS.jsonl").write_text(json.dumps(r_row) + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("RETRY_MISSING_SUBPROCESS_PROVENANCE" in v for v in res["violations"])


def test_r332_12_derived_projection_claiming_independently_executed_pytest_rejected() -> None:
    """R332-12: derived projection claiming independently executed pytest => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        g_log = bdir / "logs" / "g01_unit.log"
        g_log.write_text("COMMAND: python -m pytest\nEXIT_CODE: 0\n", encoding="utf-8")
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("DERIVED_PROJECTION_CLAIMS_SUBPROCESS_EXECUTION" in v for v in res["violations"])


def test_r332_13_derived_junit_sidecar_exit_0_while_linked_raw_attempt_exit_1_rejected() -> None:
    """R332-13: derived JUnit sidecar exit 0 while linked raw attempt exit 1 => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        p_path = bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl"
        row = json.loads(p_path.read_text(encoding="utf-8").strip())
        row["exit_code"] = 1
        row["primary_exit_code"] = 1
        row["primary_status"] = "FAIL"
        p_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("DERIVED_JUNIT_MASKS_FAILED_PRIMARY_ATTEMPT" in v for v in res["violations"])


def test_r332_14_master_worker_ledger_22_scheduled_20_succeeded_2_failed_phase_h_17_17_rejected() -> None:
    """R332-14: master worker ledger 22 scheduled/20 succeeded/2 failed, Phase-H 17/17 => release must FAIL."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        acc_p = bdir / "R332_MASTER_ATTEMPT_ACCOUNTING.json"
        acc = json.loads(acc_p.read_text(encoding="utf-8"))
        acc["scheduled_tasks"] = 22
        acc["succeeded_tasks"] = 20
        acc["failed_tasks"] = 2
        acc_p.write_text(json.dumps(acc, indent=2), encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("MASTER_WORKER_LEDGER_HAS_FAILED_TASKS" in v for v in res["violations"])


def test_r332_15_missing_master_worker_ledger_rejected() -> None:
    """R332-15: missing master worker ledger => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        (bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl").unlink()
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("MISSING_MANDATORY_R332_ARTIFACT:R332_MASTER_PRIMARY_ATTEMPTS.jsonl" in v for v in res["violations"])


def test_r332_16_empty_master_worker_ledger_rejected() -> None:
    """R332-16: empty master worker ledger => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        (bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl").write_text("", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any(
            "EMPTY_MANDATORY_R332_ARTIFACT:R332_MASTER_PRIMARY_ATTEMPTS.jsonl" in v
            or "EMPTY_OR_MISSING_MASTER_PRIMARY_ATTEMPTS" in v
            for v in res["violations"]
        )


def test_r332_17_missing_primary_shard_started_at_ms_rejected() -> None:
    """R332-17: missing primary shard started_at_ms => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        p_path = bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl"
        row = json.loads(p_path.read_text(encoding="utf-8").strip())
        row["started_at_ms"] = None
        p_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("MISSING_PRIMARY_SHARD_STARTED_AT_MS" in v for v in res["violations"])


def test_r332_18_missing_worker_source_sha_rejected() -> None:
    """R332-18: missing worker source SHA => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        p_path = bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl"
        row = json.loads(p_path.read_text(encoding="utf-8").strip())
        row["source_sha"] = ""
        row["tested_source_sha"] = ""
        p_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any(
            "MISSING_OR_MISMATCHED_WORKER_SOURCE_SHA" in v or "MISSING_NESTED_SOURCE_SHA" in v
            for v in res["violations"]
        )


def test_r332_19_cross_run_retry_xml_sidecar_rejected() -> None:
    """R332-19: cross-run retry XML sidecar => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        r_row = {
            "retry_attempt_id": "retry_cross_run_01",
            "source_attempt_id": "primary_pytest_shard_0",
            "run_id": "r331_old_stale_run_99",
            "source_sha": "a" * 40,
            "tested_source_sha": "a" * 40,
            "pid": 22345,
            "command": ["python", "-m", "pytest"],
            "started_at_ms": 2000,
            "completed_at_ms": 2100,
            "exit_code": 0,
        }
        (bdir / "R332_MASTER_RETRY_ATTEMPTS.jsonl").write_text(json.dumps(r_row) + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any(
            "CROSS_RUN_OR_COMMIT_RETRY_XML" in v or "STALE_OR_MISMATCHED_NESTED_RUN_ID" in v
            for v in res["violations"]
        )


def test_r332_20_cross_commit_xml_with_same_testcase_name_rejected() -> None:
    """R332-20: cross-commit XML with same testcase name => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        sc_p = bdir / "reports" / "g01_unit.xml.meta.json"
        sc = json.loads(sc_p.read_text(encoding="utf-8"))
        sc["tested_source_sha"] = "b" * 40
        sc_p.write_text(json.dumps(sc, indent=2), encoding="utf-8")
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("JUNIT_SIDECAR_SOURCE_SHA_MISMATCH" in v for v in res["violations"])


def test_r332_21_missing_backend_tests_in_shard_inventory_rejected() -> None:
    """R332-21: missing backend tests in shard inventory => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        p_path = bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl"
        row = json.loads(p_path.read_text(encoding="utf-8").strip())
        row["files"] = ["backend/tests/test_sample_a.py"]  # missing test_sample_b.py
        p_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("MISSING_BACKEND_TESTS_IN_SHARD_INVENTORY" in v for v in res["violations"])


def test_r332_22_interrupted_worker_or_timeout_reported_pass_rejected() -> None:
    """R332-22: interrupted worker/timeout reported PASS => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        p_path = bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl"
        row = json.loads(p_path.read_text(encoding="utf-8").strip())
        row["timed_out"] = True
        p_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("INTERRUPTED_OR_TIMED_OUT_WORKER_REPORTED" in v for v in res["violations"])


def test_r332_23_orphaned_worker_after_run_rejected() -> None:
    """R332-23: orphaned worker after run => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        rd_p = bdir / "R332_RESOURCE_AND_DISK_TELEMETRY.json"
        rd = json.loads(rd_p.read_text(encoding="utf-8"))
        rd["disk_budget_and_cleanup_report"]["orphan_worker_count"] = 1
        rd["disk_budget_and_cleanup_report"]["orphan_worker_pids"] = [99991]
        rd_p.write_text(json.dumps(rd, indent=2), encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("ORPHAN_WORKERS_REMAINING" in v for v in res["violations"])


def test_r332_24_actual_peak_concurrency_exceeding_limit_rejected() -> None:
    """R332-24: actual peak concurrency > limit => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        rd_p = bdir / "R332_RESOURCE_AND_DISK_TELEMETRY.json"
        rd = json.loads(rd_p.read_text(encoding="utf-8"))
        rd["phase_h_task_accounting"]["max_configured_workers"] = 4
        rd["phase_h_task_accounting"]["max_active_workers_observed"] = 6
        rd_p.write_text(json.dumps(rd, indent=2), encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("TASK_ACCOUNTING_CONCURRENCY_VIOLATION" in v for v in res["violations"])


def test_r332_25_disk_guard_threshold_breached_but_report_pass_rejected() -> None:
    """R332-25: disk guard threshold breached but report PASS => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        rd_p = bdir / "R332_RESOURCE_AND_DISK_TELEMETRY.json"
        rd = json.loads(rd_p.read_text(encoding="utf-8"))
        rd["disk_budget_and_cleanup_report"]["minimum_free_disk_bytes_observed"] = 512 * 1024 * 1024  # 512 MiB < 2 GiB floor
        rd_p.write_text(json.dumps(rd, indent=2), encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("DISK_BUDGET_BELOW_FLOOR" in v for v in res["violations"])


def test_r332_26_memory_sampling_failed_but_numeric_default_pass_rejected() -> None:
    """R332-26: memory sampling failed but numeric default PASS => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        rd_p = bdir / "R332_RESOURCE_AND_DISK_TELEMETRY.json"
        rd = json.loads(rd_p.read_text(encoding="utf-8"))
        rd["memory_unit_and_scope_report"]["peak_process_rss_bytes"] = 0
        rd_p.write_text(json.dumps(rd, indent=2), encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("PEAK_PROCESS_RSS_BYTES_NON_POSITIVE" in v for v in res["violations"])


def test_r332_27_one_negative_control_absent_from_registry_or_execution_rejected() -> None:
    """R332-27: one negative control absent from registry/execution => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        nc_p = bdir / "R332_NEGATIVE_CONTROL_COLLECTION.json"
        nc = json.loads(nc_p.read_text(encoding="utf-8"))
        nc["collected_control_ids"] = [c for c in nc["collected_control_ids"] if c != "R332-14"]
        nc_p.write_text(json.dumps(nc, indent=2), encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("MISSING_MANDATORY_NEGATIVE_CONTROL_ID:R332-14" in v for v in res["violations"])


def test_r332_28_bad_backtest_source_sha_rejected() -> None:
    """R332-28: bad backtest source SHA => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        bt_p = bdir / "R332_EXACT_SOURCE_BACKTEST_MANIFEST.json"
        bt = json.loads(bt_p.read_text(encoding="utf-8"))
        bt["backtest_source_sha"] = "f" * 40
        bt_p.write_text(json.dumps(bt, indent=2), encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("BACKTEST_SOURCE_SHA_MISMATCH" in v or "STALE_OR_MISMATCHED_NESTED_SOURCE_SHA" in v for v in res["violations"])


def test_r332_29_bad_dataset_sha_rejected() -> None:
    """R332-29: bad dataset SHA => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        bt_p = bdir / "R332_EXACT_SOURCE_BACKTEST_MANIFEST.json"
        bt = json.loads(bt_p.read_text(encoding="utf-8"))
        bt["dataset_sha256"] = "0" * 64
        bt_p.write_text(json.dumps(bt, indent=2), encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("BACKTEST_DATASET_SHA_MISMATCH" in v for v in res["violations"])


def test_r332_30_historical_baseline_sha_preserved_as_separate_identity() -> None:
    """R332-30: historical baseline SHA falsely set equal to new source SHA is rejected; separate baseline identity preserved."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        # Positive check: separate baseline SHA (c356d655...) is accepted
        pos_res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert pos_res["is_valid"] is True
        assert pos_res["historical_baseline_sha"] == PRE_REFACTOR_BASELINE_COMMIT_SHA
        assert pos_res["historical_baseline_sha"] != pos_res["tested_source_sha"]

        # Negative check: setting historical_baseline_sha == new source SHA ("a"*40) is rejected
        im_p = bdir / "R332_INITIAL_RUN_MANIFEST.json"
        im = json.loads(im_p.read_text(encoding="utf-8"))
        im["historical_baseline_sha"] = "a" * 40
        im_p.write_text(json.dumps(im, indent=2), encoding="utf-8")
        fm_p = bdir / "R332_RUN_MANIFEST_FINALIZATION.json"
        fm = json.loads(fm_p.read_text(encoding="utf-8"))
        fm["initial_manifest_sha256"] = hashlib.sha256(im_p.read_bytes()).hexdigest()
        fm_p.write_text(json.dumps(fm, indent=2), encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        neg_res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert neg_res["is_valid"] is False
        assert any("HISTORICAL_BASELINE_SHA_MISMATCH" in v for v in neg_res["violations"])


def test_r332_31_rerun_file_passes_but_original_test_fails_classified_flaky_and_blocks() -> None:
    """R332-31: rerun file passes but original test fails => FLAKY_OR_ISOLATION_DEPENDENT and blocks clean acceptance."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        fl_p = bdir / "R332_MASTER_FAILURE_AND_FLAKE_REPORT.json"
        fl = json.loads(fl_p.read_text(encoding="utf-8"))
        fl["flaky_or_isolation_dependent_count"] = 1
        fl["isolated_retries_count"] = 1
        fl["clean_primary_acceptance_status"] = "FAIL"
        fl_p.write_text(json.dumps(fl, indent=2), encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("MASTER_FAILURE_OR_FLAKE_REPORT_NOT_CLEAN_PASS" in v for v in res["violations"])


def test_r332_32_two_monitored_tests_in_former_shard_neighbor_layout_pass_without_masking() -> None:
    """R332-32: Both formerly failing tests pass on first primary execution alongside their former shard neighbors."""
    # Check 1: test_v16_18_red_reproductions.py::test_red_023 + test_v17_1_monitor_scope_and_focus.py::test_repro_m1_default_list_isolates_active_run
    p1 = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "backend/tests/test_v16_18_red_reproductions.py",
            "-k",
            "test_red_023 or test_red_024",
            "backend/tests/test_v17_1_monitor_scope_and_focus.py",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p1.returncode == 0, f"Primary shard neighbor run 1 failed:\n{p1.stdout}\n{p1.stderr}"

    # Check 2: test_v16_19_red_reproductions.py::test_red_026 + test_v16_21_red_reproductions.py::test_red_030_live_wiring_cache_to_scheduler_trace
    p2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "backend/tests/test_v16_19_red_reproductions.py",
            "-k",
            "test_red_026 or test_red_030_live_wiring_cache_to_scheduler_trace",
            "backend/tests/test_v16_21_red_reproductions.py",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p2.returncode == 0, f"Primary shard neighbor run 2 failed:\n{p2.stdout}\n{p2.stderr}"


def test_r332_33_attempt_artifact_deleted_by_cleanup_rejected() -> None:
    """R332-33: attempt artifact deleted by cleanup => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        # Simulate post-run cleanup deleting raw/primary directory
        for f in (bdir / "reports" / "raw" / "primary").glob("*.xml"):
            f.unlink()
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("MISSING_ORIGINAL_RAW_SHARD_XML" in v for v in res["violations"])


def test_r332_34_apparent_zero_failures_but_raw_worker_ledger_nonzero_rejected() -> None:
    """R332-34: apparent 0 failures in gate results, but raw XML/worker ledger nonzero => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        p_path = bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl"
        row = json.loads(p_path.read_text(encoding="utf-8").strip())
        row["exit_code"] = 2
        row["primary_exit_code"] = 2
        p_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("PRIMARY_SUBPROCESS_NONZERO_EXIT_OR_FAIL" in v for v in res["violations"])


def test_r332_35_run_manifest_created_after_first_mandatory_worker_rejected() -> None:
    """R332-35: run manifest created after first mandatory worker => reject."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        init_doc = json.loads((bdir / "R332_INITIAL_RUN_MANIFEST.json").read_text(encoding="utf-8"))
        p_path = bdir / "R332_MASTER_PRIMARY_ATTEMPTS.jsonl"
        row = json.loads(p_path.read_text(encoding="utf-8").strip())
        row["started_at_ms"] = int(init_doc["created_at_ms"]) - 5000
        p_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        _refresh_r332_manifest_hashes(bdir)
        res = verify_r332_evidence_bundle(bdir, "a" * 40, expected_run_id="r332_test_run_01")
        assert res["is_valid"] is False
        assert any("PRE_EXECUTION_MANIFEST_CREATED_AFTER_FIRST_TASK" in v for v in res["violations"])


def test_r332_36_success_path_with_primary_zero_failures_and_verified_evidence_accepted() -> None:
    """R332-36: success path with primary zero failures and verified evidence => accept (positive control)."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r332_bundle_fixture(Path(td))
        res = verify_persisted_acceptance_artifacts(
            bdir,
            "a" * 40,
            required_schema="R332",
            expected_run_id="r332_test_run_01",
        )
        assert res["is_valid"] is True, f"Positive control failed: {res['violations']}"
        assert res["status"] == "PASS"
        assert res["violations"] == []
        assert res["ready_for_v17_9_1"] == "YES"
        assert res["trading_authorization"] == "MARKET_SHADOW_RESEARCH_ONLY"
