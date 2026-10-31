"""
AURUM DESK V17.9.0.1.3 R3.3.1 — Acceptance Runner, Pre-Execution Manifest Lifecycle,
OS-Normalized Memory Telemetry, JUnit Scope Reconciliation & Fail-Closed Verifier Suite.

Covers all 6 P0 root-cause closures (`P0-A` through `P0-F`) and all 30 mandatory
fault-injection controls (`R331-01` through `R331-30`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.acceptance_verdict import (
    assert_nested_run_provenance,
    independently_reconcile_junit_scopes,
    reconcile_r33_runtime_captures,
    verify_mandatory_artifact_hash_coverage,
    verify_persisted_acceptance_artifacts,
    verify_pre_execution_run_manifest,
    verify_r331_evidence_bundle,
    verify_resource_telemetry_semantics,
    verify_unmodified_run_identity_tree,
    verify_worker_execution_accounting,
)
from tools.generate_v17_9_0_1_artifacts import (
    _measure_process_peak_memory_bytes,
    copy_verified_current_run_artifacts,
)
from tools.run_v17_9_async_regression import (
    AsyncWorkerGroupSpec,
    BoundedAsyncTestOrchestrator,
)
from tools.runtime_execution_logger import (
    R331_MANDATORY_ARTIFACTS,
    create_r331_run_context,
    finalize_r331_run_manifest,
    verify_r331_pre_execution_manifest,
)
from tools.test_resource_guard import (
    TestResourceGuard,
    measure_process_memory_scopes,
    normalize_ru_maxrss_to_bytes,
)
from tools.v17_9_execution_state import ExecutionStateManager


def _build_valid_r331_bundle_fixture(tmp_dir: Path) -> Path:
    """
    Constructs a complete, self-consistent 27-deliverable R3.3.1 evidence bundle in
    `tmp_dir / "r331_bundle"` that passes `verify_r331_evidence_bundle` with 0 violations.
    """
    bundle_dir = tmp_dir / "r331_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = bundle_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    run_id = "r331_test_run_01"
    frozen_sha = "a" * 40
    hist_sha = "c356d6551e13cd6af61cea5c3ab1f4aae1548af2"

    # 1. Pre-execution manifest created BEFORE any task or backtest timestamp
    ctx = create_r331_run_context(
        run_id=run_id,
        frozen_executable_sha=frozen_sha,
        historical_baseline_sha=hist_sha,
        run_dir=bundle_dir,
        branch="fix/v17-9-0-1-3-historical-parity-hardening",
    )
    t0_ms = ctx.created_at_ms + 10
    t1_ms = t0_ms + 50

    # 2. Sample JUnit XML + .xml.meta.json sidecar with real exit_code=0
    xml_content = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuites><testsuite name="pytest" errors="0" failures="0" skipped="0" tests="2">\n'
        '  <testcase classname="test_suite" name="test_alpha" time="0.01"/>\n'
        '  <testcase classname="test_suite" name="test_beta" time="0.01"/>\n'
        "</testsuite></testsuites>\n"
    )
    xml_path = reports_dir / "g01_unit.xml"
    xml_path.write_text(xml_content, encoding="utf-8")
    xml_sha = hashlib.sha256(xml_path.read_bytes()).hexdigest()
    sidecar_path = reports_dir / "g01_unit.xml.meta.json"
    sidecar_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "source_sha": frozen_sha,
                "xml_sha256": xml_sha,
                "exit_code": 0,
                "counts": {"tests": 2, "passed": 2, "failures": 0, "errors": 0, "skipped": 0},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # 3. Build 30 timestamps * (3 cycle runtimes + 5 models * 5 runtimes) authentic events/snapshots/captures
    events: List[Dict[str, Any]] = []
    snapshots: List[Dict[str, Any]] = []
    captures: List[Dict[str, Any]] = []

    cycle_runtimes = [
        ("HISTORICAL_PRODUCTION", "services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_cycle", hist_sha),
        ("PRODUCTION", "backend.services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_cycle", frozen_sha),
        ("MARKET_SHADOW", "backend.services.market_shadow_service.MarketShadowService.evaluate_shadow_cycle", frozen_sha),
    ]
    model_ids = [
        "SMC_M1_REVERSAL",
        "SMC_M1_CONTINUATION",
        "SMC_M1_FVG_CONTINUATION",
        "SMC_M1_SWEEP_RECLAIM",
        "CG01",
    ]
    model_runtimes = [
        ("HISTORICAL_PRODUCTION", "services.scalp_m1_model_service.ScalpM1ModelService.evaluate_m1_reversal_candidate", hist_sha),
        ("SHARED_STRATEGY_EVALUATOR", "backend.services.shared_strategy_evaluator.SharedStrategyEvaluator.evaluate_decision", frozen_sha),
        ("PRODUCTION", "backend.services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_shared_decision_snapshot", frozen_sha),
        ("MARKET_SHADOW", "backend.services.market_shadow_service.MarketShadowService.evaluate_shared_decision_snapshot", frozen_sha),
        ("CHRONOLOGICAL_BACKTEST", "backend.services.v17_8_backtest_runner.execute_chronological_backtest_window_for_parity", frozen_sha),
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

    rt_verif = reconcile_r33_runtime_captures(
        events=events,
        snapshots=snapshots,
        captures=captures,
        expected_run_id=run_id,
        expected_frozen_sha=frozen_sha,
        expected_historical_sha=hist_sha,
    )

    neg_ids = (
        [f"N{i:02d}" for i in range(1, 149)]
        + [f"F{i:02d}" for i in range(1, 21)]
        + [f"R331-{i:02d}" for i in range(1, 31)]
    )
    neg_results = [{"test_id": nid, "control_id": nid, "status": "PASS"} for nid in neg_ids]

    finalize_r331_run_manifest(
        ctx,
        status="PASS",
        backtest_source_sha=frozen_sha,
        summary_metrics={"passed_gates_count": 107},
    )

    (bundle_dir / "R331_ROOT_CAUSE_AND_FUNCTION_CLOSURE.md").write_text("# R3.3.1 Root Cause Matrix\n", encoding="utf-8")
    (bundle_dir / "R331_COMMAND_AND_SOURCE_EXECUTIONS.jsonl").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "source_sha": frozen_sha,
                "stage": "GATE_G01",
                "command": "pytest",
                "started_at_ms": t0_ms,
                "completed_at_ms": t1_ms,
                "exit_code": 0,
                "status": "PASS",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "R331_WORKER_EXECUTIONS.jsonl").write_text(
        json.dumps(
            {
                "worker_id": "w1",
                "task_id": "G01",
                "pid": 1234,
                "ppid": 1000,
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "source_sha": frozen_sha,
                "started_at_ms": t0_ms,
                "completed_at_ms": t1_ms,
                "duration_sec": 0.05,
                "exit_code": 0,
                "junit_xml_path": "reports/g01_unit.xml",
                "status": "PASS",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "R331_TASK_ACCOUNTING.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "scheduled_tasks": 1,
                "succeeded_tasks": 1,
                "failed_tasks": 0,
                "blocked_tasks": 0,
                "cancelled_tasks": 0,
                "max_configured_workers": 4,
                "max_active_workers_observed": 2,
                "orphan_processes_remaining": 0,
                "status": "PASS",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "R331_JUNIT_SCOPE_INVENTORY.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "gate_xml_files_count": 1,
                "gate_xml_raw_total_tests": 2,
                "gate_xml_unique_test_nodes": 2,
                "gate_xml_overlapping_executions": 0,
                "status": "PASS",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "R331_JUNIT_PER_XML_RECALCULATION.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "xml_files_count": 1,
                "per_xml_records": {
                    "g01_unit.xml": {
                        "xml_file": "g01_unit.xml",
                        "xml_sha256": xml_sha,
                        "exit_code": 0,
                        "tests": 2,
                        "passed": 2,
                        "failures": 0,
                        "errors": 0,
                        "skipped": 0,
                    }
                },
                "status": "PASS",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "R331_JUNIT_OVERLAP_AND_RECONCILIATION.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "xml_files_count": 1,
                "raw_total_tests": 2,
                "raw_total_passed": 2,
                "raw_total_failures": 0,
                "raw_total_errors": 0,
                "raw_total_skipped": 0,
                "unique_test_node_count": 2,
                "overlapping_test_executions": 0,
                "status": "PASS",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "R331_NEGATIVE_COLLECTION.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "total_collected_controls": len(neg_ids),
                "collected_control_ids": neg_ids,
                "status": "PASS",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "R331_NEGATIVE_RESULTS.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "total_negative_controls": len(neg_ids),
                "passed_negative_controls": len(neg_ids),
                "failed_negative_controls": 0,
                "status": "PASS",
                "results": neg_results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "R331_RUNTIME_INVOCATION_EVENTS.jsonl").write_text(
        "".join(json.dumps(e, sort_keys=True) + "\n" for e in events),
        encoding="utf-8",
    )
    (bundle_dir / "R331_RUNTIME_INPUT_SNAPSHOTS.jsonl").write_text(
        "".join(json.dumps(s, sort_keys=True) + "\n" for s in snapshots),
        encoding="utf-8",
    )
    (bundle_dir / "R331_RUNTIME_INPUT_CAPTURES.jsonl").write_text(
        "".join(json.dumps(c, sort_keys=True) + "\n" for c in captures),
        encoding="utf-8",
    )
    (bundle_dir / "R331_RUNTIME_PROVENANCE_VERIFICATION.json").write_text(
        json.dumps(
            {
                **rt_verif,
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "R331_FIVE_WAY_PARITY.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "frozen_executable_sha": frozen_sha,
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
    (bundle_dir / "R331_HISTORICAL_PRODUCTION_LINEAGE.jsonl").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "scenario_id": "HIST_REAL_001_SMC_M1_REVERSAL_1761696000000",
                "is_consumed": True,
                "lifecycle_stage": "CANDIDATE_CONSUMED",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "R331_ASYNC_WORKER_TELEMETRY.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "source_sha": frozen_sha,
                "max_configured_workers": 4,
                "max_active_workers_observed": 2,
                "orphan_processes_remaining": 0,
                "status": "PASS",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "R331_MEMORY_UNIT_AND_SCOPE_REPORT.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "source_sha": frozen_sha,
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
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "R331_DISK_BUDGET_AND_CLEANUP.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "source_sha": frozen_sha,
                "measurement_status": "MEASURED",
                "status": "PASS",
                "reserved_disk_floor_bytes": 2_000_000_000,
                "free_disk_bytes_preflight": 10_000_000_000,
                "minimum_free_disk_bytes_observed": 9_500_000_000,
                "free_disk_bytes_postflight": 9_800_000_000,
                "orphan_worker_count": 0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "R331_EXACT_SOURCE_BACKTEST_MANIFEST.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "frozen_executable_sha": frozen_sha,
                "backtest_source_sha": frozen_sha,
                "started_at_ms": t0_ms,
                "completed_at_ms": t1_ms,
                "status": "PASS",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "R331_BACKTEST_VARIANTS_ABCD.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "backtest_source_sha": frozen_sha,
                "authoritative_production_parity_simulation": {
                    "simulated_trades": 18,
                    "net_pnl": -1.82,
                    "source_sha": frozen_sha,
                },
                "status": "PASS",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "R331_BACKTEST_TRADE_LINEAGE.jsonl").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "source_sha": frozen_sha,
                "backtest_source_sha": frozen_sha,
                "trade_id": "tr_01",
                "decision_id": "dec_01",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "R331_MATCHED_COHORT_AND_OOS.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "acceptance_run_id": run_id,
                "tested_source_sha": frozen_sha,
                "source_sha": frozen_sha,
                "backtest_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "out_of_sample_verdict": "OUT_OF_SAMPLE_INCONCLUSIVE",
                "status": "PASS",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "R331_INDEPENDENT_VERIFICATION.json").write_text(
        json.dumps({"run_id": run_id, "tested_source_sha": frozen_sha, "status": "PASS"}, indent=2),
        encoding="utf-8",
    )
    (bundle_dir / "R331_FINAL_ACCEPTANCE_AUDIT.md").write_text("# R3.3.1 Final Audit\n", encoding="utf-8")

    _refresh_r331_manifest_hashes(bundle_dir, run_id=run_id, frozen_sha=frozen_sha)
    verif = verify_r331_evidence_bundle(bundle_dir, frozen_sha, expected_run_id=run_id)
    assert verif["is_valid"] is True, f"Valid R3.3.1 fixture failed: {verif['violations']}"
    return bundle_dir


def _refresh_r331_manifest_hashes(
    bundle_dir: Path,
    *,
    run_id: str = "r331_test_run_01",
    frozen_sha: str = "a" * 40,
) -> None:
    art_hashes: Dict[str, Any] = {}
    for fname in R331_MANDATORY_ARTIFACTS:
        if fname in (
            "R331_ARTIFACT_SHA256_MANIFEST.json",
            "R331_INDEPENDENT_VERIFICATION.json",
            "R331_FINAL_ACCEPTANCE_AUDIT.md",
        ):
            continue
        fp = bundle_dir / fname
        if fp.exists():
            art_hashes[fname] = {
                "filename": fname,
                "sha256": hashlib.sha256(fp.read_bytes()).hexdigest(),
                "size_bytes": fp.stat().st_size,
            }
    (bundle_dir / "R331_ARTIFACT_SHA256_MANIFEST.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "artifacts": art_hashes,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================================
# P0-A: Pre-Execution Run Manifest Lifecycle (`R331-01` .. `R331-05`)
# ============================================================================

def test_r331_01_missing_or_empty_initial_run_manifest_rejected() -> None:
    """R331-01: Missing or empty R331_INITIAL_RUN_MANIFEST.json fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        (bdir / "R331_INITIAL_RUN_MANIFEST.json").write_text("", encoding="utf-8")
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("EMPTY_MANDATORY_R331_ARTIFACT:R331_INITIAL_RUN_MANIFEST.json" in v or "EMPTY_OR_INVALID_INITIAL_RUN_MANIFEST" in v for v in res["violations"])


def test_r331_02_initial_run_manifest_non_running_status_rejected() -> None:
    """R331-02: R331_INITIAL_RUN_MANIFEST.json with status != 'RUNNING' fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        man_p = bdir / "R331_INITIAL_RUN_MANIFEST.json"
        doc = json.loads(man_p.read_text(encoding="utf-8"))
        doc["status"] = "PASS"
        raw_b = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")
        man_p.write_bytes(raw_b)
        fin_p = bdir / "R331_RUN_MANIFEST_FINALIZATION.json"
        fin_doc = json.loads(fin_p.read_text(encoding="utf-8"))
        fin_doc["initial_manifest_sha256"] = hashlib.sha256(raw_b).hexdigest()
        fin_p.write_text(json.dumps(fin_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("INITIAL_MANIFEST_STATUS_NOT_RUNNING" in v for v in res["violations"])


def test_r331_03_initial_manifest_created_after_first_task_rejected() -> None:
    """R331-03: R331_INITIAL_RUN_MANIFEST.json created_at_ms > first_task.started_at_ms fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        init_doc = json.loads((bdir / "R331_INITIAL_RUN_MANIFEST.json").read_text(encoding="utf-8"))
        fin_doc = json.loads((bdir / "R331_RUN_MANIFEST_FINALIZATION.json").read_text(encoding="utf-8"))
        chk = verify_pre_execution_run_manifest(
            init_doc,
            fin_doc,
            expected_run_id="r331_test_run_01",
            expected_source_sha="a" * 40,
            first_task_started_at_ms=int(init_doc["created_at_ms"]) - 500,
        )
        assert chk["is_valid"] is False
        assert any("PRE_EXECUTION_MANIFEST_CREATED_AFTER_FIRST_TASK" in v for v in chk["violations"])


def test_r331_04_initial_manifest_created_after_backtest_rejected() -> None:
    """R331-04: R331_INITIAL_RUN_MANIFEST.json created_at_ms > backtest.started_at_ms fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        bt_p = bdir / "R331_EXACT_SOURCE_BACKTEST_MANIFEST.json"
        bt_doc = json.loads(bt_p.read_text(encoding="utf-8"))
        init_doc = json.loads((bdir / "R331_INITIAL_RUN_MANIFEST.json").read_text(encoding="utf-8"))
        bt_doc["started_at_ms"] = int(init_doc["created_at_ms"]) - 1000
        bt_p.write_text(json.dumps(bt_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("PRE_EXECUTION_MANIFEST_CREATED_AFTER_BACKTEST_START" in v for v in res["violations"])


def test_r331_05_initial_manifest_mutated_after_creation_rejected() -> None:
    """R331-05: Mutating R331_INITIAL_RUN_MANIFEST.json after creation breaks initial_manifest_sha256 and fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        man_p = bdir / "R331_INITIAL_RUN_MANIFEST.json"
        doc = json.loads(man_p.read_text(encoding="utf-8"))
        doc["hostname"] = "mutated_host_post_hoc"
        man_p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("FINALIZATION_INITIAL_MANIFEST_SHA256_MISMATCH" in v for v in res["violations"])


# ============================================================================
# P0-B: OS Memory Unit Normalization & Resource Scopes (`R331-06` .. `R331-10`)
# ============================================================================

def test_r331_06_macos_ru_maxrss_unit_normalization_and_1024x_inflation_rejected() -> None:
    """R331-06: Darwin ru_maxrss is already bytes (not multiplied by 1024), and 1024x inflated peak RSS fails closed."""
    assert normalize_ru_maxrss_to_bytes(600 * 1024 * 1024, "darwin") == 600 * 1024 * 1024
    assert normalize_ru_maxrss_to_bytes(600 * 1024, "linux") == 600 * 1024 * 1024
    measured = _measure_process_peak_memory_bytes()
    assert measured > 0
    assert measured != 67108864
    assert measured < 32 * 1024 * 1024 * 1024

    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        mem_p = bdir / "R331_MEMORY_UNIT_AND_SCOPE_REPORT.json"
        mem_doc = json.loads(mem_p.read_text(encoding="utf-8"))
        mem_doc["peak_process_rss_bytes"] = 619_920 * 1024 * 1024  # 619,920 MiB inflation
        mem_doc["peak_process_tree_rss_bytes"] = 619_920 * 1024 * 1024
        mem_p.write_text(json.dumps(mem_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("MACOS_RU_MAXRSS_UNIT_INFLATION_DETECTED" in v for v in res["violations"])


def test_r331_07_fabricated_64mib_memory_fallback_rejected() -> None:
    """R331-07: Fabricated 64 MiB (67108864) memory fallback fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        mem_p = bdir / "R331_MEMORY_UNIT_AND_SCOPE_REPORT.json"
        mem_doc = json.loads(mem_p.read_text(encoding="utf-8"))
        mem_doc["current_rss_bytes"] = 67108864
        mem_p.write_text(json.dumps(mem_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("FABRICATED_64MIB_MEMORY_FALLBACK_DETECTED" in v for v in res["violations"])


def test_r331_08_not_measured_memory_telemetry_rejected() -> None:
    """R331-08: measurement_status == 'NOT_MEASURED' or status == 'BLOCKED_RESOURCE_TELEMETRY' fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        mem_p = bdir / "R331_MEMORY_UNIT_AND_SCOPE_REPORT.json"
        mem_doc = json.loads(mem_p.read_text(encoding="utf-8"))
        mem_doc["measurement_status"] = "NOT_MEASURED"
        mem_doc["status"] = "BLOCKED_RESOURCE_TELEMETRY"
        mem_p.write_text(json.dumps(mem_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("MEMORY_REPORT_MEASUREMENT_STATUS_NOT_MEASURED" in v for v in res["violations"])


def test_r331_09_missing_separated_memory_scopes_or_inverted_peak_rejected() -> None:
    """R331-09: Missing separated memory scopes or peak_process_rss_bytes < current_rss_bytes fails closed."""
    scopes = measure_process_memory_scopes()
    assert scopes["measurement_status"] == "MEASURED"
    assert scopes["unit"] == "BYTES"
    assert scopes["peak_process_rss_bytes"] >= scopes["current_rss_bytes"] > 0

    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        mem_p = bdir / "R331_MEMORY_UNIT_AND_SCOPE_REPORT.json"
        mem_doc = json.loads(mem_p.read_text(encoding="utf-8"))
        mem_doc["peak_process_rss_bytes"] = mem_doc["current_rss_bytes"] - 1024
        mem_p.write_text(json.dumps(mem_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("PEAK_PROCESS_RSS_BELOW_CURRENT_RSS" in v for v in res["violations"])


def test_r331_10_disk_budget_below_reserved_floor_rejected() -> None:
    """R331-10: Disk free space below reserved_disk_floor_bytes fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        disk_p = bdir / "R331_DISK_BUDGET_AND_CLEANUP.json"
        disk_doc = json.loads(disk_p.read_text(encoding="utf-8"))
        disk_doc["free_disk_bytes_postflight"] = 500_000_000  # below 2 GB floor
        disk_p.write_text(json.dumps(disk_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("DISK_BUDGET_BELOW_FLOOR" in v for v in res["violations"])


# ============================================================================
# P0-C: JUnit Scope & Cross-Directory Reconciliation (`R331-11` .. `R331-15`)
# ============================================================================

def test_r331_11_junit_scope_inventory_file_count_mismatch_rejected() -> None:
    """R331-11: JUnit scope inventory claiming 92 files when 93 exist on disk fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        inv_p = bdir / "R331_JUNIT_SCOPE_INVENTORY.json"
        inv_doc = json.loads(inv_p.read_text(encoding="utf-8"))
        inv_doc["gate_xml_files_count"] = 92
        inv_p.write_text(json.dumps(inv_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("JUNIT_SCOPE_INVENTORY_FILE_COUNT_MISMATCH" in v for v in res["violations"])


def test_r331_12_junit_scope_inventory_raw_test_count_mismatch_rejected() -> None:
    """R331-12: JUnit scope inventory claiming 5957 raw tests when XMLs sum to 5816 fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        inv_p = bdir / "R331_JUNIT_SCOPE_INVENTORY.json"
        inv_doc = json.loads(inv_p.read_text(encoding="utf-8"))
        inv_doc["gate_xml_raw_total_tests"] = 5957
        inv_p.write_text(json.dumps(inv_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("JUNIT_SCOPE_INVENTORY_RAW_TESTS_MISMATCH" in v for v in res["violations"])


def test_r331_13_junit_per_xml_recalculation_mismatch_rejected() -> None:
    """R331-13: Per-XML recalculation record mismatching actual XML test count or SHA-256 fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        px_p = bdir / "R331_JUNIT_PER_XML_RECALCULATION.json"
        px_doc = json.loads(px_p.read_text(encoding="utf-8"))
        px_doc["per_xml_records"]["g01_unit.xml"]["tests"] = 154
        px_p.write_text(json.dumps(px_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("JUNIT_PER_XML_RECORD_MISMATCH" in v for v in res["violations"])


def test_r331_14_junit_overlap_reconciliation_formula_mismatch_rejected() -> None:
    """R331-14: Overlap reconciliation document mismatching actual overlap count fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        ov_p = bdir / "R331_JUNIT_OVERLAP_AND_RECONCILIATION.json"
        ov_doc = json.loads(ov_p.read_text(encoding="utf-8"))
        ov_doc["overlapping_test_executions"] = 99
        ov_p.write_text(json.dumps(ov_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("JUNIT_OVERLAP_RECONCILIATION_MISMATCH" in v for v in res["violations"])


def test_r331_15_missing_or_corrupt_junit_sidecar_rejected() -> None:
    """R331-15: Missing .xml.meta.json sidecar for any JUnit XML fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        (bdir / "reports" / "g01_unit.xml.meta.json").unlink()
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("JUNIT_SIDECAR_MISSING" in v for v in res["violations"])


# ============================================================================
# P0-D: Zero Stale-Run Fallback & Unmodified Nested Provenance (`R331-16` .. `R331-20`)
# ============================================================================

def test_r331_16_copy_verified_current_run_artifacts_rejects_missing_dir_no_latest_fallback() -> None:
    """R331-16: copy_verified_current_run_artifacts raises RuntimeError when current-run dir is missing."""
    with tempfile.TemporaryDirectory() as td:
        tpath = Path(td)
        with pytest.raises(RuntimeError, match="BLOCKED_STALE_EVIDENCE_INJECTION"):
            copy_verified_current_run_artifacts(
                tpath / "nonexistent_run_dir",
                tpath / "dest_reports",
                tpath / "dest_logs",
                expected_run_id="r331_current_run",
                expected_code_sha="a" * 40,
            )


def test_r331_17_copy_verified_current_run_artifacts_rejects_stale_run_id_or_sha() -> None:
    """R331-17: copy_verified_current_run_artifacts rejects acceptance_results.json with stale run_id or sha."""
    with tempfile.TemporaryDirectory() as td:
        tpath = Path(td)
        stale_dir = tpath / "stale_run"
        stale_dir.mkdir()
        (stale_dir / "acceptance_results.json").write_text(
            json.dumps({"run_id": "stale_run_00", "tested_source_sha": "b" * 40}),
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError, match="BLOCKED_STALE_EVIDENCE_INJECTION"):
            copy_verified_current_run_artifacts(
                stale_dir,
                tpath / "dest_reports",
                tpath / "dest_logs",
                expected_run_id="r331_current_run",
                expected_code_sha="a" * 40,
            )


def test_r331_18_unmodified_run_identity_tree_rejects_stale_nested_subobject() -> None:
    """R331-18: assert_nested_run_provenance rejects stale nested concurrency_report.run_id."""
    payload = {
        "run_id": "r331_run_01",
        "tested_source_sha": "a" * 40,
        "concurrency_report": {
            "run_id": "run_v17_9_0_stale_20261030",
            "tested_source_sha": "a" * 40,
        },
    }
    res = assert_nested_run_provenance(
        payload,
        expected_run_id="r331_run_01",
        expected_source_sha="a" * 40,
    )
    assert res["is_valid"] is False
    assert any("STALE_OR_MISMATCHED_NESTED_RUN_ID" in v for v in res["violations"])


def test_r331_19_unmodified_run_identity_tree_rejects_provenance_rewrite_marker() -> None:
    """R331-19: verify_unmodified_run_identity_tree rejects _provenance_rewritten or _stale_fallback_used."""
    payload = {
        "run_id": "r331_run_01",
        "tested_source_sha": "a" * 40,
        "cpu_memory_metrics": {
            "run_id": "r331_run_01",
            "tested_source_sha": "a" * 40,
            "_provenance_rewritten": True,
        },
    }
    res = verify_unmodified_run_identity_tree(
        payload,
        expected_run_id="r331_run_01",
        expected_source_sha="a" * 40,
    )
    assert res["is_valid"] is False
    assert any("ILLEGAL_PROVENANCE_REWRITE_DETECTED" in v for v in res["violations"])


def test_r331_20_stale_run_id_in_jsonl_artifact_rejected() -> None:
    """R331-20: Stale run_id or source_sha in R331_BACKTEST_TRADE_LINEAGE.jsonl fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        tl_p = bdir / "R331_BACKTEST_TRADE_LINEAGE.jsonl"
        rows = [json.loads(ln) for ln in tl_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        rows[0]["run_id"] = "stale_backtest_run_id"
        tl_p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("STALE_OR_MISMATCHED_NESTED_RUN_ID" in v for v in res["violations"])


# ============================================================================
# P0-E: Bounded Worker Orchestration, Exit Codes & Orphans (`R331-21` .. `R331-25`)
# ============================================================================

def test_r331_21_junit_sidecar_nonzero_exit_code_with_zero_xml_failures_rejected() -> None:
    """R331-21: JUnit sidecar with exit_code != 0 fails closed even when XML has 0 failures."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        sc_p = bdir / "reports" / "g01_unit.xml.meta.json"
        sc_doc = json.loads(sc_p.read_text(encoding="utf-8"))
        sc_doc["exit_code"] = 2
        sc_p.write_text(json.dumps(sc_doc, indent=2), encoding="utf-8")
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("JUNIT_SIDECAR_NONZERO_OR_MISSING_EXIT_CODE" in v for v in res["violations"])


def test_r331_22_bounded_orchestrator_global_semaphore_and_concurrency_bound_violation_rejected() -> None:
    """R331-22: BoundedAsyncTestOrchestrator enforces global semaphore and verifier rejects observed > configured."""
    with tempfile.TemporaryDirectory() as td:
        tpath = Path(td)
        guard = TestResourceGuard(run_id="r331_sem_test", repo_root=REPO_ROOT, tmp_root=tpath / "tmp")
        state_mgr = ExecutionStateManager(
            run_id="r331_sem_test",
            source_sha="a" * 40,
            all_test_groups=["G_A", "G_B", "G_C"],
            artifact_dir=tpath,
        )
        orch = BoundedAsyncTestOrchestrator(
            run_id="r331_sem_test",
            source_sha="a" * 40,
            artifact_dir=tpath,
            resource_guard=guard,
            state_manager=state_mgr,
            max_pytest_workers=2,
            max_global_workers=2,
        )
        specs = []
        for gid in ("G_A", "G_B", "G_C"):
            specs.append(
                AsyncWorkerGroupSpec(
                    group_id=gid,
                    description=gid,
                    command=[sys.executable, "-c", "import time; time.sleep(0.05)"],
                    cwd=REPO_ROOT,
                    junit_xml_path=tpath / f"{gid.lower()}.xml",
                    log_path=tpath / f"{gid.lower()}.log",
                    worker_pool="PYTEST",
                    stage="STAGE_B",
                )
            )
        summary = asyncio.run(orch.run_groups(specs))
        assert summary["peak_concurrent_workers"] <= 2
        assert summary["task_accounting"]["scheduled_tasks"] == 3
        assert summary["task_accounting"]["succeeded_tasks"] == 3
        guard.cleanup_run_temp_root()

        # Negative check: observed > configured rejected
        bdir = _build_valid_r331_bundle_fixture(Path(td) / "sub")
        ta_p = bdir / "R331_TASK_ACCOUNTING.json"
        ta_doc = json.loads(ta_p.read_text(encoding="utf-8"))
        ta_doc["max_configured_workers"] = 4
        ta_doc["max_active_workers_observed"] = 7
        ta_p.write_text(json.dumps(ta_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("TASK_ACCOUNTING_CONCURRENCY_VIOLATION" in v for v in res["violations"])


def test_r331_23_task_accounting_sum_mismatch_rejected() -> None:
    """R331-23: scheduled_tasks != succeeded + failed + blocked + cancelled fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        ta_p = bdir / "R331_TASK_ACCOUNTING.json"
        ta_doc = json.loads(ta_p.read_text(encoding="utf-8"))
        ta_doc["scheduled_tasks"] = 5
        ta_doc["succeeded_tasks"] = 4
        ta_p.write_text(json.dumps(ta_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("TASK_ACCOUNTING_SUM_MISMATCH" in v for v in res["violations"])


def test_r331_24_orphan_worker_process_detection_blocks_resource_guard() -> None:
    """R331-24: Live registered child worker PID is detected as an orphan and blocks resource reports."""
    with tempfile.TemporaryDirectory() as td:
        tpath = Path(td)
        guard = TestResourceGuard(run_id="r331_orphan_test", repo_root=REPO_ROOT, tmp_root=tpath / "tmp")
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        try:
            guard.register_worker_pid(proc.pid)
            orphan_info = guard.inspect_orphan_worker_processes()
            assert orphan_info["orphan_worker_count"] == 1
            assert proc.pid in orphan_info["orphan_worker_pids"]
            disk_rep = guard.build_disk_budget_report()
            assert disk_rep["status"] == "BLOCKED_ORPHAN_PROCESSES"
        finally:
            proc.kill()
            proc.wait()
            guard.unregister_worker_pid(proc.pid)
            guard.cleanup_run_temp_root()


def test_r331_25_worker_started_before_pre_execution_manifest_rejected() -> None:
    """R331-25: Worker execution row with started_at_ms < manifest.created_at_ms fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        init_doc = json.loads((bdir / "R331_INITIAL_RUN_MANIFEST.json").read_text(encoding="utf-8"))
        w_p = bdir / "R331_WORKER_EXECUTIONS.jsonl"
        w_rows = [json.loads(ln) for ln in w_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        w_rows[0]["started_at_ms"] = int(init_doc["created_at_ms"]) - 200
        w_p.write_text("".join(json.dumps(w, sort_keys=True) + "\n" for w in w_rows), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any(
            "WORKER_STARTED_BEFORE_PRE_EXECUTION_MANIFEST" in v or "INITIAL_MANIFEST_CREATED_AFTER_FIRST_TASK" in v
            for v in res["violations"]
        )


# ============================================================================
# P0-F: Fail-Closed Verifier & Complete SHA-256 Coverage (`R331-26` .. `R331-30`)
# ============================================================================

def test_r331_26_worker_missing_exit_code_never_defaults_to_zero() -> None:
    """R331-26: Worker execution row missing exit_code fails closed (never defaults to 0)."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        w_p = bdir / "R331_WORKER_EXECUTIONS.jsonl"
        w_rows = [json.loads(ln) for ln in w_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        w_rows[0].pop("exit_code", None)
        w_p.write_text("".join(json.dumps(w, sort_keys=True) + "\n" for w in w_rows), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("WORKER_MISSING_OR_INVALID_EXIT_CODE" in v for v in res["violations"])


def test_r331_27_worker_missing_status_never_defaults_to_pass() -> None:
    """R331-27: Worker execution row missing status fails closed (never defaults to 'PASS')."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        w_p = bdir / "R331_WORKER_EXECUTIONS.jsonl"
        w_rows = [json.loads(ln) for ln in w_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        w_rows[0]["status"] = ""
        w_p.write_text("".join(json.dumps(w, sort_keys=True) + "\n" for w in w_rows), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("WORKER_MISSING_STATUS" in v for v in res["violations"])


def test_r331_28_task_accounting_missing_status_rejected() -> None:
    """R331-28: R331_TASK_ACCOUNTING.json missing status or status != 'PASS' fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        ta_p = bdir / "R331_TASK_ACCOUNTING.json"
        ta_doc = json.loads(ta_p.read_text(encoding="utf-8"))
        ta_doc.pop("status", None)
        ta_p.write_text(json.dumps(ta_doc, indent=2), encoding="utf-8")
        _refresh_r331_manifest_hashes(bdir)
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("TASK_ACCOUNTING_STATUS_NOT_PASS" in v for v in res["violations"])


def test_r331_29_incomplete_sha256_manifest_missing_mandatory_entry_rejected() -> None:
    """R331-29: R331_ARTIFACT_SHA256_MANIFEST.json omitting a mandatory R331 deliverable fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        man_p = bdir / "R331_ARTIFACT_SHA256_MANIFEST.json"
        man_doc = json.loads(man_p.read_text(encoding="utf-8"))
        man_doc["artifacts"].pop("R331_FIVE_WAY_PARITY.json", None)
        man_p.write_text(json.dumps(man_doc, indent=2), encoding="utf-8")
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("INCOMPLETE_HASH_MANIFEST_MISSING_ENTRY:R331_FIVE_WAY_PARITY.json" in v for v in res["violations"])


def test_r331_30_post_manifest_byte_mutation_in_r331_deliverable_rejected() -> None:
    """R331-30: Mutating any manifested R331 deliverable without updating SHA-256 manifest fails closed."""
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r331_bundle_fixture(Path(td))
        rc_p = bdir / "R331_ROOT_CAUSE_AND_FUNCTION_CLOSURE.md"
        rc_p.write_text("# Tampered content after hash manifest\n", encoding="utf-8")
        res = verify_r331_evidence_bundle(bdir, "a" * 40, expected_run_id="r331_test_run_01")
        assert res["is_valid"] is False
        assert any("ARTIFACT_BYTE_CHECKSUM_MISMATCH:R331_ROOT_CAUSE_AND_FUNCTION_CLOSURE.md" in v for v in res["violations"])

