"""
AURUM DESK V17.9.0.1.3 R3.3 — Live Runtime Execution Event Logger & Run Context Coordinator
===========================================================================================
Provides authentic boundary-level runtime invocation logging (`INVOCATION_STARTED`,
`ACTUAL_INPUT_CAPTURED`, `INVOCATION_COMPLETED`, `INVOCATION_FAILED`), content-addressed
input/configuration snapshot persistence (`R33_RUNTIME_INPUT_SNAPSHOTS.jsonl`),
`observe_runtime_invocation(...)` boundary wrapper, and single authoritative `R33RunContext`
lifecycle management (`create_r33_run_context`, `verify_frozen_checkout`, `finalize_r33_run_context`).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_REFACTOR_BASELINE_COMMIT_SHA = "c356d6551e13cd6af61cea5c3ab1f4aae1548af2"

R33_MANDATORY_ARTIFACTS: Tuple[str, ...] = (
    "R33_RUN_MANIFEST.json",
    "R33_DEFECT_ROOT_CAUSE_MATRIX.md",
    "R33_SOURCE_AND_COMMAND_EXECUTIONS.jsonl",
    "R33_RUNTIME_INVOCATION_EVENTS.jsonl",
    "R33_RUNTIME_INPUT_SNAPSHOTS.jsonl",
    "R33_RUNTIME_INPUT_CAPTURES.jsonl",
    "R33_RUNTIME_CAPTURE_INDEPENDENT_VERIFICATION.json",
    "R33_FIVE_WAY_PARITY.json",
    "R33_HISTORICAL_PRODUCTION_LINEAGE.jsonl",
    "R33_PARITY_CLASSIFICATION.json",
    "R33_NEGATIVE_TEST_COLLECTION.json",
    "R33_NEGATIVE_TEST_RESULTS.json",
    "R33_WORKER_EXECUTIONS.jsonl",
    "R33_RUN_TASK_ACCOUNTING.json",
    "R33_JUNIT_RECONCILIATION.json",
    "R33_ASYNC_RESOURCE_TELEMETRY.json",
    "R33_DISK_GUARD.json",
    "R33_EXACT_SOURCE_BACKTEST_MANIFEST.json",
    "R33_BACKTEST_TRADE_LEDGER.jsonl",
    "R33_BACKTEST_A_B_C_D_METRICS.json",
    "R33_MATCHED_COHORT.json",
    "R33_OOS_REPORT.json",
    "R33_ARTIFACT_SHA256_MANIFEST.json",
    "R33_INDEPENDENT_VERIFIER_REPORT.json",
    "R33_FINAL_ACCEPTANCE_AUDIT.md",
)


def compute_payload_sha256(payload: Any) -> str:
    """Computes deterministic SHA-256 of any JSON-serializable payload."""
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def compute_executable_source_tree_hash(repo_root: Path = REPO_ROOT) -> str:
    """Computes deterministic composite SHA-256 over executable Python/TS/JS files in backend, frontend/src, and tools."""
    hasher = hashlib.sha256()
    roots = [repo_root / "backend", repo_root / "tools", repo_root / "frontend" / "src"]
    files: List[Path] = []
    for r in roots:
        if r.exists():
            for ext in ("*.py", "*.ts", "*.tsx"):
                files.extend(p for p in r.rglob(ext) if "__pycache__" not in p.parts and "node_modules" not in p.parts)
    for f in sorted(set(files), key=lambda p: str(p.relative_to(repo_root)).replace("\\", "/")):
        rel = str(f.relative_to(repo_root)).replace("\\", "/")
        raw = f.read_bytes().replace(b"\r\n", b"\n")
        f_sha = hashlib.sha256(raw).hexdigest()
        hasher.update(f"{rel}:{f_sha}\n".encode("utf-8"))
    return hasher.hexdigest()


@dataclass(frozen=True)
class R33RunContext:
    """Immutable run context created BEFORE any mandatory regression, parity replay, or backtest execution."""
    run_id: str
    release: str
    schema_version: str
    frozen_executable_sha: str
    executable_tree_sha256: str
    historical_baseline_sha: str
    dataset_sha256: str
    configuration_sha256: str
    run_dir: Path
    reports_dir: Path
    logs_dir: Path
    started_at_utc: str
    started_at_ms: int
    started_monotonic_ns: int
    producer_pid: int
    max_global_workers: int
    expected_mandatory_artifacts: Tuple[str, ...] = R33_MANDATORY_ARTIFACTS

    def to_manifest_dict(self, *, status: str = "STARTED", extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "release": self.release,
            "run_id": self.run_id,
            "status": status,
            "final_code_sha": self.frozen_executable_sha,
            "tested_source_sha": self.frozen_executable_sha,
            "backtest_source_sha": self.frozen_executable_sha,
            "frozen_executable_sha": self.frozen_executable_sha,
            "executable_tree_sha256": self.executable_tree_sha256,
            "historical_baseline_sha": self.historical_baseline_sha,
            "dataset_sha256": self.dataset_sha256,
            "configuration_sha256": self.configuration_sha256,
            "run_dir": str(self.run_dir),
            "started_at_utc": self.started_at_utc,
            "started_at_ms": self.started_at_ms,
            "started_monotonic_ns": self.started_monotonic_ns,
            "producer_pid": self.producer_pid,
            "max_global_workers": self.max_global_workers,
            "expected_mandatory_artifacts": list(self.expected_mandatory_artifacts),
            "seed_run_reused": False,
        }
        if extra:
            payload.update(dict(extra))
        return payload


def create_r33_run_context(
    *,
    frozen_sha: str,
    dataset_sha: str,
    config_sha: str,
    output_root: Optional[Path] = None,
    run_id: Optional[str] = None,
    max_global_workers: Optional[int] = None,
) -> R33RunContext:
    """
    Phase 1.1 & 1.2: Creates an immutable R33RunContext BEFORE any mandatory test,
    historical scenario replay, or backtest executes, and writes the initial
    `R33_RUN_MANIFEST.json` on disk.
    """
    clean_sha = str(frozen_sha or "").strip()
    if not clean_sha or len(clean_sha) < 7:
        raise ValueError("R33_MISSING_FROZEN_SHA: frozen_sha must be a valid non-empty Git commit SHA")
    clean_ds_sha = str(dataset_sha or "").strip()
    if not clean_ds_sha:
        raise ValueError("R33_MISSING_DATASET_SHA: dataset_sha must be non-empty")
    clean_cfg_sha = str(config_sha or "").strip()
    if not clean_cfg_sha:
        raise ValueError("R33_MISSING_CONFIG_SHA: config_sha must be non-empty")

    now_ms = int(time.time() * 1000)
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mono_ns = time.monotonic_ns()
    eff_run_id = str(run_id or "").strip() or f"r33_run_{now_ms}_{os.getpid()}"
    base_root = Path(output_root) if output_root is not None else (REPO_ROOT / "docs" / "artifacts" / "v17_9_0_1_3" / "r33")
    run_dir = (base_root / eff_run_id).resolve()
    reports_dir = run_dir / "reports"
    logs_dir = run_dir / "logs"
    run_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    cpu_count = os.cpu_count() or 2
    eff_workers = int(max_global_workers) if max_global_workers is not None else min(4, max(2, cpu_count // 2))
    tree_sha = compute_executable_source_tree_hash(REPO_ROOT)

    ctx = R33RunContext(
        run_id=eff_run_id,
        release="V17.9.0.1.3_R3.3",
        schema_version="R33_RUN_MANIFEST_V1",
        frozen_executable_sha=clean_sha,
        executable_tree_sha256=tree_sha,
        historical_baseline_sha=PRE_REFACTOR_BASELINE_COMMIT_SHA,
        dataset_sha256=clean_ds_sha,
        configuration_sha256=clean_cfg_sha,
        run_dir=run_dir,
        reports_dir=reports_dir,
        logs_dir=logs_dir,
        started_at_utc=now_utc,
        started_at_ms=now_ms,
        started_monotonic_ns=mono_ns,
        producer_pid=os.getpid(),
        max_global_workers=eff_workers,
    )
    manifest_path = run_dir / "R33_RUN_MANIFEST.json"
    manifest_bytes = (json.dumps(ctx.to_manifest_dict(status="STARTED"), indent=2, sort_keys=True) + "\n").encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    return ctx


def verify_frozen_checkout(
    run_context: R33RunContext,
    *,
    repo_root: Path = REPO_ROOT,
    require_exact_tree_hash: bool = True,
) -> Dict[str, Any]:
    """
    Phase 1.1: Verifies that the current Git HEAD and executable worktree match `run_context`.
    Fails closed if HEAD or executable tree hash drifted.
    """
    violations: List[str] = []
    actual_head = ""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if res.returncode == 0:
            actual_head = res.stdout.strip()
    except Exception as exc:
        violations.append(f"GIT_HEAD_CHECK_FAILED:{exc}")

    if not actual_head:
        violations.append("MISSING_ACTUAL_GIT_HEAD")
    elif actual_head != run_context.frozen_executable_sha:
        violations.append(
            f"FROZEN_HEAD_DRIFT:{actual_head}!={run_context.frozen_executable_sha}"
        )

    current_tree_sha = compute_executable_source_tree_hash(repo_root)
    if require_exact_tree_hash and current_tree_sha != run_context.executable_tree_sha256:
        violations.append(
            f"EXECUTABLE_TREE_HASH_DRIFT:{current_tree_sha}!={run_context.executable_tree_sha256}"
        )

    return {
        "is_valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "run_id": run_context.run_id,
        "expected_frozen_sha": run_context.frozen_executable_sha,
        "actual_head_sha": actual_head,
        "expected_tree_sha256": run_context.executable_tree_sha256,
        "actual_tree_sha256": current_tree_sha,
        "violations": violations,
    }


def finalize_r33_run_context(
    run_context: R33RunContext,
    *,
    completed_tasks: Mapping[str, Any],
    evidence: Mapping[str, Any],
    overall_status: str = "PASS",
    repo_root: Path = REPO_ROOT,
) -> Dict[str, Any]:
    """
    Phase 1.1: Finalizes `R33_RUN_MANIFEST.json` after all tasks finish and verifies that the
    executable checkout did not drift during execution.
    """
    checkout_check = verify_frozen_checkout(run_context, repo_root=repo_root)
    completed_ms = int(time.time() * 1000)
    completed_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    duration_sec = round(max(0.0, (time.monotonic_ns() - run_context.started_monotonic_ns) / 1_000_000_000.0), 3)
    eff_status = overall_status if checkout_check["is_valid"] else "FAIL"

    final_manifest = run_context.to_manifest_dict(
        status=eff_status,
        extra={
            "completed_at_utc": completed_utc,
            "completed_at_ms": completed_ms,
            "total_run_duration_sec": duration_sec,
            "frozen_checkout_verification": checkout_check,
            "completed_tasks": dict(completed_tasks),
            "evidence_summary": dict(evidence),
        },
    )
    manifest_path = run_context.run_dir / "R33_RUN_MANIFEST.json"
    manifest_path.write_bytes(
        (json.dumps(final_manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    return final_manifest


@dataclass(frozen=True)
class RuntimeInvocationEvent:
    event_id: str
    event_type: str  # "INVOCATION_STARTED" | "ACTUAL_INPUT_CAPTURED" | "INVOCATION_COMPLETED" | "INVOCATION_FAILED"
    run_id: str
    worker_id: str
    producer_pid: int
    execution_id: str
    parent_cycle_invocation_id: Optional[str]
    invocation_scope: str  # "CYCLE_EVALUATION" | "MODEL_EVALUATION"
    scenario_id: str
    runtime_name: str
    callable_identity: str
    model_id: str
    decision_timestamp_ms: int
    timestamp_utc_ms: int
    timestamp_monotonic_ns: int
    source_sha: str
    snapshot_id: Optional[str] = None
    arguments_hash: Optional[str] = None
    raw_input_hash: Optional[str] = None
    canonical_input_hash: Optional[str] = None
    raw_config_hash: Optional[str] = None
    canonical_config_hash: Optional[str] = None
    output_decision_id: Optional[str] = None
    output_status: Optional[str] = None
    output_fingerprint: Optional[str] = None
    pre_call_monotonic_ns: Optional[int] = None
    post_call_monotonic_ns: Optional[int] = None
    duration_ms: Optional[float] = None
    exit_status: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RuntimeExecutionEventLogger:
    """
    Captures live runtime execution events and content-addressed input/config snapshots
    across the 5 parity runtimes (`HISTORICAL_PRODUCTION`, `SHARED_STRATEGY_EVALUATOR`,
    `PRODUCTION`, `MARKET_SHADOW`, `CHRONOLOGICAL_BACKTEST`).
    """

    def __init__(
        self,
        run_id: str,
        *,
        source_sha: str = "",
        worker_id: str = "main",
        run_dir: Optional[Path] = None,
    ) -> None:
        clean_run_id = str(run_id or "").strip()
        if not clean_run_id:
            raise ValueError("R33_INVALID_RUN_ID: RuntimeExecutionEventLogger requires a non-empty run_id")
        self.run_id = clean_run_id
        self.source_sha = str(source_sha or "").strip()
        self.worker_id = str(worker_id or "main").strip()
        self.run_dir = Path(run_dir) if run_dir is not None else None
        self.producer_pid = os.getpid()
        self._lock = threading.Lock()
        self._events: List[RuntimeInvocationEvent] = []
        self._snapshots: Dict[str, Dict[str, Any]] = {}

    def record_invocation_started(
        self,
        *,
        execution_id: str,
        scenario_id: str,
        runtime_name: str,
        callable_identity: str,
        model_id: str,
        decision_timestamp_ms: int,
        source_sha: str,
        arguments_hash: str,
        parent_cycle_invocation_id: Optional[str] = None,
        invocation_scope: str = "MODEL_EVALUATION",
    ) -> str:
        eff_sha = str(source_sha or self.source_sha).strip()
        if not eff_sha:
            raise ValueError(f"R33_MISSING_SOURCE_SHA: source_sha required for {execution_id}")
        evt_id = f"evt_start_{execution_id}_{uuid.uuid4().hex[:8]}"
        evt = RuntimeInvocationEvent(
            event_id=evt_id,
            event_type="INVOCATION_STARTED",
            run_id=self.run_id,
            worker_id=self.worker_id,
            producer_pid=self.producer_pid,
            execution_id=execution_id,
            parent_cycle_invocation_id=parent_cycle_invocation_id,
            invocation_scope=invocation_scope,
            scenario_id=scenario_id,
            runtime_name=runtime_name,
            callable_identity=callable_identity,
            model_id=model_id,
            decision_timestamp_ms=int(decision_timestamp_ms),
            timestamp_utc_ms=int(time.time() * 1000),
            timestamp_monotonic_ns=time.monotonic_ns(),
            source_sha=eff_sha,
            arguments_hash=arguments_hash,
        )
        with self._lock:
            self._events.append(evt)
        return evt_id

    def record_actual_input_captured(
        self,
        *,
        execution_id: str,
        scenario_id: str,
        runtime_name: str,
        callable_identity: str,
        model_id: str,
        decision_timestamp_ms: int,
        source_sha: str,
        raw_input_payload: Any,
        canonical_input_payload: Any,
        raw_config_payload: Any,
        canonical_config_payload: Any,
        parent_cycle_invocation_id: Optional[str] = None,
        invocation_scope: str = "MODEL_EVALUATION",
    ) -> Tuple[str, Dict[str, str]]:
        eff_sha = str(source_sha or self.source_sha).strip()
        if not eff_sha:
            raise ValueError(f"R33_MISSING_SOURCE_SHA: source_sha required for {execution_id}")
        evt_id = f"evt_input_{execution_id}_{uuid.uuid4().hex[:8]}"
        snap_id = f"snap_{execution_id}"
        mono_ns = time.monotonic_ns()
        utc_ms = int(time.time() * 1000)

        raw_in_h = compute_payload_sha256(raw_input_payload)
        can_in_h = compute_payload_sha256(canonical_input_payload)
        raw_cfg_h = compute_payload_sha256(raw_config_payload)
        can_cfg_h = compute_payload_sha256(canonical_config_payload)

        snapshot_record = {
            "schema_version": "R33_RUNTIME_INPUT_SNAPSHOT_V1",
            "snapshot_id": snap_id,
            "run_id": self.run_id,
            "worker_id": self.worker_id,
            "producer_pid": self.producer_pid,
            "source_sha": eff_sha,
            "execution_id": execution_id,
            "parent_cycle_invocation_id": parent_cycle_invocation_id,
            "invocation_scope": invocation_scope,
            "scenario_id": scenario_id,
            "runtime_name": runtime_name,
            "callable_identity": callable_identity,
            "model_id": model_id,
            "decision_timestamp_ms": int(decision_timestamp_ms),
            "captured_at_utc_ms": utc_ms,
            "captured_at_monotonic_ns": mono_ns,
            "raw_input_payload": raw_input_payload,
            "canonical_input_payload": canonical_input_payload,
            "raw_config_payload": raw_config_payload,
            "canonical_config_payload": canonical_config_payload,
            "declared_raw_input_hash": raw_in_h,
            "declared_canonical_input_hash": can_in_h,
            "declared_raw_config_hash": raw_cfg_h,
            "declared_canonical_config_hash": can_cfg_h,
        }

        evt = RuntimeInvocationEvent(
            event_id=evt_id,
            event_type="ACTUAL_INPUT_CAPTURED",
            run_id=self.run_id,
            worker_id=self.worker_id,
            producer_pid=self.producer_pid,
            execution_id=execution_id,
            parent_cycle_invocation_id=parent_cycle_invocation_id,
            invocation_scope=invocation_scope,
            scenario_id=scenario_id,
            runtime_name=runtime_name,
            callable_identity=callable_identity,
            model_id=model_id,
            decision_timestamp_ms=int(decision_timestamp_ms),
            timestamp_utc_ms=utc_ms,
            timestamp_monotonic_ns=mono_ns,
            source_sha=eff_sha,
            snapshot_id=snap_id,
            raw_input_hash=raw_in_h,
            canonical_input_hash=can_in_h,
            raw_config_hash=raw_cfg_h,
            canonical_config_hash=can_cfg_h,
        )
        with self._lock:
            self._snapshots[execution_id] = snapshot_record
            self._events.append(evt)
        return evt_id, {
            "snapshot_id": snap_id,
            "raw_input_hash": raw_in_h,
            "canonical_input_hash": can_in_h,
            "raw_config_hash": raw_cfg_h,
            "canonical_config_hash": can_cfg_h,
        }

    def record_invocation_completed(
        self,
        *,
        execution_id: str,
        scenario_id: str,
        runtime_name: str,
        callable_identity: str,
        model_id: str,
        decision_timestamp_ms: int,
        source_sha: str,
        output_decision_id: str,
        output_status: str,
        output_fingerprint: str,
        duration_ms: float,
        parent_cycle_invocation_id: Optional[str] = None,
        invocation_scope: str = "MODEL_EVALUATION",
        pre_call_monotonic_ns: Optional[int] = None,
        post_call_monotonic_ns: Optional[int] = None,
    ) -> str:
        eff_sha = str(source_sha or self.source_sha).strip()
        if not eff_sha:
            raise ValueError(f"R33_MISSING_SOURCE_SHA: source_sha required for {execution_id}")
        evt_id = f"evt_done_{execution_id}_{uuid.uuid4().hex[:8]}"
        evt = RuntimeInvocationEvent(
            event_id=evt_id,
            event_type="INVOCATION_COMPLETED",
            run_id=self.run_id,
            worker_id=self.worker_id,
            producer_pid=self.producer_pid,
            execution_id=execution_id,
            parent_cycle_invocation_id=parent_cycle_invocation_id,
            invocation_scope=invocation_scope,
            scenario_id=scenario_id,
            runtime_name=runtime_name,
            callable_identity=callable_identity,
            model_id=model_id,
            decision_timestamp_ms=int(decision_timestamp_ms),
            timestamp_utc_ms=int(time.time() * 1000),
            timestamp_monotonic_ns=time.monotonic_ns(),
            source_sha=eff_sha,
            output_decision_id=output_decision_id,
            output_status=output_status,
            output_fingerprint=output_fingerprint,
            pre_call_monotonic_ns=pre_call_monotonic_ns,
            post_call_monotonic_ns=post_call_monotonic_ns,
            duration_ms=round(float(duration_ms), 4),
            exit_status="SUCCESS",
        )
        with self._lock:
            self._events.append(evt)
        return evt_id

    def record_invocation_failed(
        self,
        *,
        execution_id: str,
        scenario_id: str,
        runtime_name: str,
        callable_identity: str,
        model_id: str,
        decision_timestamp_ms: int,
        source_sha: str,
        error_message: str,
        duration_ms: float = 0.0,
        parent_cycle_invocation_id: Optional[str] = None,
        invocation_scope: str = "MODEL_EVALUATION",
        pre_call_monotonic_ns: Optional[int] = None,
        post_call_monotonic_ns: Optional[int] = None,
    ) -> str:
        eff_sha = str(source_sha or self.source_sha).strip()
        evt_id = f"evt_fail_{execution_id}_{uuid.uuid4().hex[:8]}"
        evt = RuntimeInvocationEvent(
            event_id=evt_id,
            event_type="INVOCATION_FAILED",
            run_id=self.run_id,
            worker_id=self.worker_id,
            producer_pid=self.producer_pid,
            execution_id=execution_id,
            parent_cycle_invocation_id=parent_cycle_invocation_id,
            invocation_scope=invocation_scope,
            scenario_id=scenario_id,
            runtime_name=runtime_name,
            callable_identity=callable_identity,
            model_id=model_id,
            decision_timestamp_ms=int(decision_timestamp_ms),
            timestamp_utc_ms=int(time.time() * 1000),
            timestamp_monotonic_ns=time.monotonic_ns(),
            source_sha=eff_sha,
            pre_call_monotonic_ns=pre_call_monotonic_ns,
            post_call_monotonic_ns=post_call_monotonic_ns,
            duration_ms=round(float(duration_ms), 4),
            exit_status="FAILED",
            error_message=str(error_message),
        )
        with self._lock:
            self._events.append(evt)
        return evt_id

    def observe_runtime_invocation(
        self,
        *,
        execution_id: Optional[str] = None,
        scenario_id: str,
        runtime_name: str,
        callable_identity: str,
        model_id: str,
        decision_timestamp_ms: int,
        source_sha: Optional[str] = None,
        raw_input_payload: Any,
        canonical_input_payload: Any,
        raw_config_payload: Any,
        canonical_config_payload: Any,
        callable_fn: Callable[..., Any],
        args: Sequence[Any] = (),
        kwargs: Optional[Mapping[str, Any]] = None,
        parent_cycle_invocation_id: Optional[str] = None,
        invocation_scope: str = "MODEL_EVALUATION",
        output_extractor: Optional[Callable[[Any], Dict[str, Any]]] = None,
    ) -> Tuple[Any, str]:
        """
        Phase 2.1: Wraps an actual runtime callable boundary:
          1. Emits `INVOCATION_STARTED` BEFORE the callable executes.
          2. Emits `ACTUAL_INPUT_CAPTURED` and persists the content-addressed snapshot BEFORE the callable executes.
          3. Invokes `callable_fn(*args, **kwargs)`.
          4. On exception: emits `INVOCATION_FAILED` and re-raises.
          5. On normal return: emits `INVOCATION_COMPLETED` with real output fingerprint and monotonic bounds.
        """
        eff_sha = str(source_sha or self.source_sha).strip()
        if not eff_sha:
            raise ValueError("R33_MISSING_SOURCE_SHA: observe_runtime_invocation requires source_sha")
        exec_id = str(execution_id or "").strip() or f"{runtime_name.lower()}_{scenario_id}_{uuid.uuid4().hex[:10]}"
        arg_hash = compute_payload_sha256(canonical_input_payload)

        self.record_invocation_started(
            execution_id=exec_id,
            scenario_id=scenario_id,
            runtime_name=runtime_name,
            callable_identity=callable_identity,
            model_id=model_id,
            decision_timestamp_ms=decision_timestamp_ms,
            source_sha=eff_sha,
            arguments_hash=arg_hash,
            parent_cycle_invocation_id=parent_cycle_invocation_id,
            invocation_scope=invocation_scope,
        )
        self.record_actual_input_captured(
            execution_id=exec_id,
            scenario_id=scenario_id,
            runtime_name=runtime_name,
            callable_identity=callable_identity,
            model_id=model_id,
            decision_timestamp_ms=decision_timestamp_ms,
            source_sha=eff_sha,
            raw_input_payload=raw_input_payload,
            canonical_input_payload=canonical_input_payload,
            raw_config_payload=raw_config_payload,
            canonical_config_payload=canonical_config_payload,
            parent_cycle_invocation_id=parent_cycle_invocation_id,
            invocation_scope=invocation_scope,
        )

        pre_mono_ns = time.monotonic_ns()
        try:
            result = callable_fn(*args, **(dict(kwargs) if kwargs else {}))
        except BaseException as exc:
            post_mono_ns = time.monotonic_ns()
            dur_ms = max(0.001, (post_mono_ns - pre_mono_ns) / 1_000_000.0)
            self.record_invocation_failed(
                execution_id=exec_id,
                scenario_id=scenario_id,
                runtime_name=runtime_name,
                callable_identity=callable_identity,
                model_id=model_id,
                decision_timestamp_ms=decision_timestamp_ms,
                source_sha=eff_sha,
                error_message=f"{type(exc).__name__}:{exc}",
                duration_ms=dur_ms,
                parent_cycle_invocation_id=parent_cycle_invocation_id,
                invocation_scope=invocation_scope,
                pre_call_monotonic_ns=pre_mono_ns,
                post_call_monotonic_ns=post_mono_ns,
            )
            raise

        post_mono_ns = time.monotonic_ns()
        dur_ms = max(0.001, (post_mono_ns - pre_mono_ns) / 1_000_000.0)
        extracted: Dict[str, Any] = {}
        if output_extractor is not None:
            extracted = dict(output_extractor(result) or {})
        elif isinstance(result, Mapping):
            extracted = {
                "output_decision_id": str(result.get("decision_id") or result.get("armed_order_id") or f"dec_{exec_id}"),
                "output_status": str(result.get("decision_status") or result.get("status") or "COMPLETED"),
                "output_fingerprint": str(result.get("decision_fingerprint") or compute_payload_sha256(result)),
            }
        else:
            extracted = {
                "output_decision_id": str(getattr(result, "decision_id", None) or f"dec_{exec_id}"),
                "output_status": str(getattr(result, "decision_status", None) or "COMPLETED"),
                "output_fingerprint": str(getattr(result, "decision_fingerprint", None) or compute_payload_sha256(str(result))),
            }

        self.record_invocation_completed(
            execution_id=exec_id,
            scenario_id=scenario_id,
            runtime_name=runtime_name,
            callable_identity=callable_identity,
            model_id=model_id,
            decision_timestamp_ms=decision_timestamp_ms,
            source_sha=eff_sha,
            output_decision_id=str(extracted.get("output_decision_id") or f"dec_{exec_id}"),
            output_status=str(extracted.get("output_status") or "COMPLETED"),
            output_fingerprint=str(extracted.get("output_fingerprint") or ""),
            duration_ms=dur_ms,
            parent_cycle_invocation_id=parent_cycle_invocation_id,
            invocation_scope=invocation_scope,
            pre_call_monotonic_ns=pre_mono_ns,
            post_call_monotonic_ns=post_mono_ns,
        )
        return result, exec_id

    def ingest_external_events_and_snapshots(
        self,
        *,
        events: Sequence[Mapping[str, Any]],
        snapshots: Sequence[Mapping[str, Any]],
    ) -> None:
        """Ingests authentic pre-recorded events and snapshots from an isolated child subprocess (e.g. historical baseline)."""
        with self._lock:
            for s in snapshots:
                ex_id = str(s.get("execution_id") or "")
                if ex_id:
                    self._snapshots[ex_id] = dict(s)
            for e in events:
                self._events.append(
                    RuntimeInvocationEvent(
                        event_id=str(e["event_id"]),
                        event_type=str(e["event_type"]),
                        run_id=str(e.get("run_id") or self.run_id),
                        worker_id=str(e.get("worker_id") or "isolated_subprocess"),
                        producer_pid=int(e.get("producer_pid") or 0),
                        execution_id=str(e["execution_id"]),
                        parent_cycle_invocation_id=e.get("parent_cycle_invocation_id"),
                        invocation_scope=str(e.get("invocation_scope") or "MODEL_EVALUATION"),
                        scenario_id=str(e["scenario_id"]),
                        runtime_name=str(e["runtime_name"]),
                        callable_identity=str(e["callable_identity"]),
                        model_id=str(e["model_id"]),
                        decision_timestamp_ms=int(e["decision_timestamp_ms"]),
                        timestamp_utc_ms=int(e["timestamp_utc_ms"]),
                        timestamp_monotonic_ns=int(e["timestamp_monotonic_ns"]),
                        source_sha=str(e["source_sha"]),
                        snapshot_id=e.get("snapshot_id"),
                        arguments_hash=e.get("arguments_hash"),
                        raw_input_hash=e.get("raw_input_hash"),
                        canonical_input_hash=e.get("canonical_input_hash"),
                        raw_config_hash=e.get("raw_config_hash"),
                        canonical_config_hash=e.get("canonical_config_hash"),
                        output_decision_id=e.get("output_decision_id"),
                        output_status=e.get("output_status"),
                        output_fingerprint=e.get("output_fingerprint"),
                        pre_call_monotonic_ns=e.get("pre_call_monotonic_ns"),
                        post_call_monotonic_ns=e.get("post_call_monotonic_ns"),
                        duration_ms=e.get("duration_ms"),
                        exit_status=e.get("exit_status"),
                        error_message=e.get("error_message"),
                    )
                )

    @property
    def events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self._events]

    @property
    def snapshots(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._snapshots.values()]

    def build_runtime_input_captures(self, *, include_cycle_invocations: bool = False) -> List[Dict[str, Any]]:
        """
        P0-02 Fix: Builds runtime input capture records ONLY from complete, ordered
        (`INVOCATION_STARTED` -> `ACTUAL_INPUT_CAPTURED` -> `INVOCATION_COMPLETED` with `exit_status == "SUCCESS"`)
        invocation sequences that have a persisted content-addressed snapshot.
        Never falls back to `start_evt.arguments_hash` when `input_evt` is missing, and never
        self-certifies `input_provenance_verified=True` without complete sequence & snapshot proof.
        """
        with self._lock:
            events_snapshot = [e.to_dict() for e in self._events]
            snapshots_map = dict(self._snapshots)

        by_exec: Dict[str, List[Dict[str, Any]]] = {}
        for evt_d in events_snapshot:
            by_exec.setdefault(evt_d["execution_id"], []).append(evt_d)

        captures: List[Dict[str, Any]] = []
        for exec_id, evts in by_exec.items():
            starts = [e for e in evts if e["event_type"] == "INVOCATION_STARTED"]
            inputs = [e for e in evts if e["event_type"] == "ACTUAL_INPUT_CAPTURED"]
            completes = [e for e in evts if e["event_type"] == "INVOCATION_COMPLETED"]
            fails = [e for e in evts if e["event_type"] == "INVOCATION_FAILED"]

            if len(starts) != 1 or len(inputs) != 1 or len(completes) != 1 or len(fails) > 0:
                continue

            start_evt = starts[0]
            input_evt = inputs[0]
            comp_evt = completes[0]

            if not include_cycle_invocations and start_evt.get("invocation_scope") == "CYCLE_EVALUATION":
                continue

            if comp_evt.get("exit_status") != "SUCCESS":
                continue

            mono_start = int(start_evt.get("timestamp_monotonic_ns") or 0)
            mono_input = int(input_evt.get("timestamp_monotonic_ns") or 0)
            mono_comp = int(comp_evt.get("timestamp_monotonic_ns") or 0)
            pre_call_ns = comp_evt.get("pre_call_monotonic_ns")
            post_call_ns = comp_evt.get("post_call_monotonic_ns")

            order_valid = (mono_start <= mono_input <= mono_comp)
            if pre_call_ns is not None:
                order_valid = order_valid and (mono_start <= int(pre_call_ns)) and (mono_input <= int(pre_call_ns))
            if post_call_ns is not None:
                order_valid = order_valid and (int(post_call_ns) <= mono_comp)
            if not order_valid:
                continue

            raw_in_h = str(input_evt.get("raw_input_hash") or "").strip()
            can_in_h = str(input_evt.get("canonical_input_hash") or "").strip()
            raw_cfg_h = str(input_evt.get("raw_config_hash") or "").strip()
            can_cfg_h = str(input_evt.get("canonical_config_hash") or "").strip()
            snap_id = str(input_evt.get("snapshot_id") or "").strip()
            snap_rec = snapshots_map.get(exec_id)

            if not (raw_in_h and can_in_h and raw_cfg_h and can_cfg_h and snap_id and snap_rec):
                continue

            captures.append(
                {
                    "schema_version": "R33_RUNTIME_CAPTURE_V1",
                    "run_id": start_evt["run_id"],
                    "worker_id": start_evt["worker_id"],
                    "producer_pid": start_evt["producer_pid"],
                    "scenario_id": start_evt["scenario_id"],
                    "runtime_name": start_evt["runtime_name"],
                    "execution_id": exec_id,
                    "parent_cycle_invocation_id": start_evt.get("parent_cycle_invocation_id"),
                    "invocation_scope": start_evt.get("invocation_scope", "MODEL_EVALUATION"),
                    "capture_point": start_evt["callable_identity"],
                    "model_id": start_evt["model_id"],
                    "decision_timestamp_ms": start_evt["decision_timestamp_ms"],
                    "captured_at_utc_ms": input_evt["timestamp_utc_ms"],
                    "started_monotonic_ns": mono_start,
                    "input_captured_monotonic_ns": mono_input,
                    "pre_call_monotonic_ns": pre_call_ns,
                    "post_call_monotonic_ns": post_call_ns,
                    "completed_monotonic_ns": mono_comp,
                    "duration_ms": comp_evt["duration_ms"],
                    "source_sha": start_evt["source_sha"],
                    "snapshot_id": snap_id,
                    "raw_input_hash": raw_in_h,
                    "canonical_input_hash": can_in_h,
                    "raw_config_hash": raw_cfg_h,
                    "canonical_config_hash": can_cfg_h,
                    "canonical_schema_version": "V17.9.0.1.3_CANONICAL_HASH_V1",
                    "invocation_started_event_id": start_evt["event_id"],
                    "input_captured_event_id": input_evt["event_id"],
                    "invocation_completed_event_id": comp_evt["event_id"],
                    "output_decision_id": comp_evt["output_decision_id"],
                    "output_status": comp_evt["output_status"],
                    "output_fingerprint": comp_evt["output_fingerprint"],
                    "invocation_completed": True,
                    "verification_status": "PENDING_INDEPENDENT_VERIFIER",
                }
            )
        return captures

    def events_to_jsonl(self) -> str:
        with self._lock:
            lines = [json.dumps(e.to_dict(), sort_keys=True) for e in self._events]
        return ("\n".join(lines) + "\n") if lines else ""

    def snapshots_to_jsonl(self) -> str:
        with self._lock:
            lines = [json.dumps(s, sort_keys=True, default=str) for s in self._snapshots.values()]
        return ("\n".join(lines) + "\n") if lines else ""
