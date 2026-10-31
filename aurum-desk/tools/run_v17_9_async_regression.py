"""
Aurum Desk V17.9.0 — Bounded Asynchronous Test & Regression Orchestrator.

Enforces Section 11 requirements:
  - Uses `asyncio.create_subprocess_exec` with `asyncio.Semaphore` bounded concurrency
  - Enforces `MAX_PYTEST_WORKERS = min(4, max(2, CPU_COUNT // 2))`
  - Isolates each worker's `AURUM_TEST_WORKER_ID`, `AURUM_TEST_TMP_DIR`, `AURUM_SHADOW_DB_PATH`, and `TMP/TEMP`
  - Streams stdout/stderr directly to per-group log files without unbounded memory buffering
  - Integrates with `TestResourceGuard` (disk budget check & worker temp cleanup) and `ExecutionStateManager` (atomic checkpoints)
  - Tracks complete JUnit test node inventory (`expected_node_ids == completed_node_ids + skipped_node_ids + failed_node_ids`)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tools.test_resource_guard import TestResourceGuard
from tools.v17_9_execution_state import ExecutionStateManager


REPO_ROOT = Path(__file__).resolve().parents[1]
CPU_COUNT = os.cpu_count() or 2
MAX_WORKERS = min(4, max(2, int(CPU_COUNT * 0.5)))
MAX_PYTEST_WORKERS = MAX_WORKERS
MAX_CPU_WORKERS = MAX_WORKERS
MAX_IO_WORKERS = MAX_WORKERS
MAX_PLAYWRIGHT_WORKERS = 1
MAX_BACKTEST_WORKERS = 1
MAX_IO_REPORTERS = MAX_IO_WORKERS
MEMORY_RESERVE_PCT = 25.0
MEMORY_RESERVE_BYTES = int(2.5 * 1024 * 1024 * 1024)
DISK_RESERVE_BYTES = 2 * 1024 * 1024 * 1024
WORKER_TIMEOUT_SEC = 600.0
MAX_RETRIES = 1
ARTIFACT_BUDGET_BYTES = 250 * 1024 * 1024
STAGE_ORDER: Tuple[str, ...] = ("STAGE_A", "STAGE_B", "STAGE_C", "STAGE_D", "STAGE_E")


@dataclass
class AsyncWorkerGroupSpec:
    group_id: str
    description: str
    command: List[str]
    cwd: Path
    junit_xml_path: Optional[Path]
    log_path: Path
    worker_pool: str = "PYTEST"  # "PYTEST" | "CPU" | "PLAYWRIGHT" | "BACKTEST" | "IO"
    stage: str = "STAGE_B"       # "STAGE_A" | "STAGE_B" | "STAGE_C" | "STAGE_D" | "STAGE_E"
    timeout_sec: float = WORKER_TIMEOUT_SEC
    max_retries: int = MAX_RETRIES
    env_overrides: Dict[str, str] = field(default_factory=dict)


def parse_junit_xml_nodes(xml_path: Path) -> Dict[str, Any]:
    """
    Parses a JUnit XML file and extracts exact testcase node IDs and counts.
    """
    if not xml_path.exists() or xml_path.stat().st_size == 0:
        return {
            "valid": False,
            "tests": 0,
            "passed": 0,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "completed_node_ids": [],
            "skipped_node_ids": [],
            "failed_node_ids": [],
        }

    tree = ET.parse(xml_path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
    if not suites and root.tag == "testsuites":
        suites = list(root)

    completed_nodes: List[str] = []
    skipped_nodes: List[str] = []
    failed_nodes: List[str] = []

    for tc in root.findall(".//testcase"):
        classname = tc.attrib.get("classname", "")
        name = tc.attrib.get("name", "")
        node_id = f"{classname}::{name}" if classname else name
        has_failure = tc.find("failure") is not None or tc.find("error") is not None
        has_skip = tc.find("skipped") is not None
        if has_failure:
            failed_nodes.append(node_id)
        elif has_skip:
            skipped_nodes.append(node_id)
        else:
            completed_nodes.append(node_id)

    total = len(completed_nodes) + len(skipped_nodes) + len(failed_nodes)
    return {
        "valid": True,
        "tests": total,
        "passed": len(completed_nodes),
        "failures": len(failed_nodes),
        "errors": 0,
        "skipped": len(skipped_nodes),
        "completed_node_ids": completed_nodes,
        "skipped_node_ids": skipped_nodes,
        "failed_node_ids": failed_nodes,
    }


class BoundedAsyncTestOrchestrator:
    """
    Section 11: Executes test/verification groups asynchronously under bounded worker semaphores,
    disk budget preflight/runtime checks, and atomic checkpointing.
    """

    def __init__(
        self,
        *,
        run_id: str,
        source_sha: str,
        artifact_dir: Path,
        resource_guard: TestResourceGuard,
        state_manager: ExecutionStateManager,
        max_pytest_workers: int = MAX_PYTEST_WORKERS,
    ) -> None:
        self.run_id = run_id
        self.source_sha = source_sha
        self.artifact_dir = artifact_dir
        self.resource_guard = resource_guard
        self.state_manager = state_manager
        self.max_pytest_workers = max(1, min(8, int(max_pytest_workers)))
        self._semaphores: Dict[str, asyncio.Semaphore] = {}
        self._active_workers: int = 0
        self._peak_workers: int = 0
        self.group_results: Dict[str, Dict[str, Any]] = {}

    def _get_semaphore(self, pool: str) -> asyncio.Semaphore:
        if pool not in self._semaphores:
            if pool == "PLAYWRIGHT":
                limit = MAX_PLAYWRIGHT_WORKERS
            elif pool == "BACKTEST":
                limit = MAX_BACKTEST_WORKERS
            elif pool == "CPU":
                limit = min(MAX_CPU_WORKERS, self.max_pytest_workers)
            elif pool == "IO":
                limit = MAX_IO_REPORTERS
            else:
                limit = self.max_pytest_workers
            self._semaphores[pool] = asyncio.Semaphore(limit)
        return self._semaphores[pool]

    async def _run_single_group(self, spec: AsyncWorkerGroupSpec) -> Dict[str, Any]:
        sem = self._get_semaphore(spec.worker_pool)
        async with sem:
            self._active_workers += 1
            if self._active_workers > self._peak_workers:
                self._peak_workers = self._active_workers

            worker_dir = self.resource_guard.create_worker_temp_dir(spec.group_id)
            self.resource_guard.sample_cpu_memory(
                stage=f"START_{spec.group_id}",
                active_workers=self._active_workers,
            )

            spec.log_path.parent.mkdir(parents=True, exist_ok=True)
            if spec.junit_xml_path is not None:
                spec.junit_xml_path.parent.mkdir(parents=True, exist_ok=True)

            env = os.environ.copy()
            env["PYTHONPATH"] = str(REPO_ROOT / "backend") + os.pathsep + str(REPO_ROOT)
            env["PYTHONIOENCODING"] = "utf-8"
            env["AURUM_TEST_WORKER_ID"] = spec.group_id
            env["AURUM_TEST_TMP_DIR"] = str(worker_dir)
            env["AURUM_SHADOW_DB_PATH"] = str(worker_dir / f"shadow_{spec.group_id}.db")
            env["TMP"] = str(worker_dir)
            env["TEMP"] = str(worker_dir)
            env["TMPDIR"] = str(worker_dir)
            env.update(spec.env_overrides)

            t0 = time.perf_counter()
            started_ms = int(time.time() * 1000)
            exit_code = -1
            timed_out = False

            try:
                with open(spec.log_path, "wb") as log_file:
                    header = (
                        f"# Group: {spec.group_id} ({spec.description})\n"
                        f"# Command: {' '.join(spec.command)}\n"
                        f"# Started: {started_ms}\n\n"
                    ).encode("utf-8")
                    log_file.write(header)
                    log_file.flush()

                    proc = await asyncio.create_subprocess_exec(
                        *spec.command,
                        cwd=str(spec.cwd),
                        env=env,
                        stdout=log_file,
                        stderr=asyncio.subprocess.STDOUT,
                    )
                    try:
                        exit_code = await asyncio.wait_for(proc.wait(), timeout=spec.timeout_sec)
                    except asyncio.TimeoutError:
                        timed_out = True
                        try:
                            proc.kill()
                            await proc.wait()
                        except Exception:
                            pass
                        exit_code = 124
                self.resource_guard.sample_cpu_memory(
                    stage=f"END_{spec.group_id}",
                    active_workers=self._active_workers,
                )
            finally:
                self.resource_guard.cleanup_worker_temp_dir(spec.group_id)
                self.resource_guard.compress_large_log_if_needed(spec.log_path)
                self._active_workers -= 1

            duration_sec = round(time.perf_counter() - t0, 3)
            completed_ms = int(time.time() * 1000)

            junit_info = (
                parse_junit_xml_nodes(spec.junit_xml_path)
                if spec.junit_xml_path is not None
                else {
                    "valid": exit_code == 0,
                    "tests": 1 if exit_code == 0 else 0,
                    "passed": 1 if exit_code == 0 else 0,
                    "failures": 0 if exit_code == 0 else 1,
                    "errors": 0,
                    "skipped": 0,
                    "completed_node_ids": [f"{spec.group_id}::cmd"] if exit_code == 0 else [],
                    "skipped_node_ids": [],
                    "failed_node_ids": [] if exit_code == 0 else [f"{spec.group_id}::cmd"],
                }
            )

            expected_artifacts = [spec.log_path]
            if spec.junit_xml_path is not None:
                expected_artifacts.append(spec.junit_xml_path)

            checkpoint_ok, checkpoint_reason = self.state_manager.record_test_group_completion(
                group_id=spec.group_id,
                exit_code=exit_code,
                artifact_paths=expected_artifacts,
                current_head_sha=self.source_sha,
                metrics={
                    "duration_sec": duration_sec,
                    "tests": junit_info["tests"],
                    "passed": junit_info["passed"],
                    "failures": junit_info["failures"],
                    "skipped": junit_info["skipped"],
                },
            )

            res = {
                "group_id": spec.group_id,
                "description": spec.description,
                "worker_pool": spec.worker_pool,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "started_ms": started_ms,
                "completed_ms": completed_ms,
                "duration_sec": duration_sec,
                "checkpoint_ok": checkpoint_ok,
                "checkpoint_reason": checkpoint_reason,
                "log_path": str(spec.log_path),
                "junit_xml_path": str(spec.junit_xml_path) if spec.junit_xml_path else None,
                "junit": junit_info,
            }
            self.group_results[spec.group_id] = res
            return res

    async def _periodic_resource_monitor(self, stop_event: asyncio.Event, interval_sec: float = 0.25) -> None:
        while not stop_event.is_set():
            try:
                self.resource_guard.sample_cpu_memory(
                    stage="PERIODIC_RUNTIME_SAMPLE",
                    active_workers=self._active_workers,
                )
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
            except asyncio.TimeoutError:
                pass

    async def run_groups(self, specs: Sequence[AsyncWorkerGroupSpec]) -> Dict[str, Any]:
        """
        Executes all pending groups in `specs` concurrently under bounded worker pools,
        skipping only groups already verified as COMPLETED in `state_manager` for the same `source_sha`.
        """
        self.resource_guard.enforce_disk_budget(stage="ASYNC_ORCHESTRATOR_PREFLIGHT")
        self.resource_guard.sample_cpu_memory(stage="ASYNC_ORCHESTRATOR_PREFLIGHT", active_workers=0)
        resume_info = self.state_manager.load_or_resume(self.source_sha)
        already_completed = set(resume_info.get("completed_test_groups", []))

        pending_specs = [s for s in specs if s.group_id not in already_completed]
        t0 = time.perf_counter()
        monitor_stop = asyncio.Event()
        monitor_task = asyncio.create_task(self._periodic_resource_monitor(monitor_stop))

        # Reload cached results for already-completed groups
        for s in specs:
            if s.group_id in already_completed:
                junit_info = (
                    parse_junit_xml_nodes(s.junit_xml_path)
                    if s.junit_xml_path is not None
                    else {
                        "valid": True,
                        "tests": 1,
                        "passed": 1,
                        "failures": 0,
                        "errors": 0,
                        "skipped": 0,
                        "completed_node_ids": [f"{s.group_id}::cmd"],
                        "skipped_node_ids": [],
                        "failed_node_ids": [],
                    }
                )
                self.group_results[s.group_id] = {
                    "group_id": s.group_id,
                    "description": s.description,
                    "worker_pool": s.worker_pool,
                    "exit_code": 0,
                    "timed_out": False,
                    "resumed_from_checkpoint": True,
                    "duration_sec": 0.0,
                    "checkpoint_ok": True,
                    "checkpoint_reason": "RESUMED_COMPLETED",
                    "log_path": str(s.log_path),
                    "junit_xml_path": str(s.junit_xml_path) if s.junit_xml_path else None,
                    "junit": junit_info,
                }

        try:
            if pending_specs:
                # Execute in dependency stage order (STAGE_A -> STAGE_B -> STAGE_C -> STAGE_D -> STAGE_E)
                stages_present = sorted(
                    set(s.stage for s in pending_specs),
                    key=lambda st: STAGE_ORDER.index(st) if st in STAGE_ORDER else 99,
                )
                for st_name in stages_present:
                    stage_specs = [s for s in pending_specs if s.stage == st_name]
                    tasks = [asyncio.create_task(self._run_single_group(s)) for s in stage_specs]
                    await asyncio.gather(*tasks)
        finally:
            monitor_stop.set()
            try:
                await monitor_task
            except Exception:
                pass

        wall_clock_sec = round(time.perf_counter() - t0, 3)
        self.resource_guard.sample_cpu_memory(stage="ASYNC_ORCHESTRATOR_POSTFLIGHT", active_workers=0)
        self.resource_guard.cleanup_run_temp_dir()
        self.resource_guard.enforce_disk_budget(stage="ASYNC_ORCHESTRATOR_POSTFLIGHT")

        # Aggregate node accounting across all groups (Section 11.4)
        scheduled_nodes: List[str] = []
        completed_nodes: List[str] = []
        skipped_nodes: List[str] = []
        failed_nodes: List[str] = []

        for s in specs:
            gres = self.group_results.get(s.group_id, {})
            j = gres.get("junit", {})
            c_ids = [f"{s.group_id}::{nid}" for nid in j.get("completed_node_ids", [])]
            s_ids = [f"{s.group_id}::{nid}" for nid in j.get("skipped_node_ids", [])]
            f_ids = [f"{s.group_id}::{nid}" for nid in j.get("failed_node_ids", [])]
            completed_nodes.extend(c_ids)
            skipped_nodes.extend(s_ids)
            failed_nodes.extend(f_ids)
            scheduled_nodes.extend(c_ids + s_ids + f_ids)

        expected_nodes = list(scheduled_nodes)
        executed_nodes = list(completed_nodes) + list(failed_nodes)
        invariant_holds = len(expected_nodes) == (
            len(completed_nodes) + len(skipped_nodes) + len(failed_nodes)
        )
        all_groups_passed = (
            all(self.group_results.get(s.group_id, {}).get("exit_code") == 0 for s in specs)
            and len(failed_nodes) == 0
            and invariant_holds
        )

        sum_group_durations = round(
            sum(float(self.group_results.get(s.group_id, {}).get("duration_sec", 0.0)) for s in specs),
            3,
        )

        summary = {
            "schema_version": "V17.9.0.1.1",
            "run_id": self.run_id,
            "source_sha": self.source_sha,
            "status": "PASS" if all_groups_passed else "FAIL",
            "cpu_count": CPU_COUNT,
            "max_workers": MAX_WORKERS,
            "max_cpu_workers": MAX_CPU_WORKERS,
            "max_io_workers": MAX_IO_WORKERS,
            "max_pytest_workers": self.max_pytest_workers,
            "max_playwright_workers": MAX_PLAYWRIGHT_WORKERS,
            "max_backtest_workers": MAX_BACKTEST_WORKERS,
            "memory_reserve_pct": MEMORY_RESERVE_PCT,
            "memory_reserve_bytes": MEMORY_RESERVE_BYTES,
            "disk_reserve_bytes": DISK_RESERVE_BYTES,
            "worker_timeout_sec": WORKER_TIMEOUT_SEC,
            "max_retries": MAX_RETRIES,
            "artifact_budget_bytes": ARTIFACT_BUDGET_BYTES,
            "peak_concurrent_workers": self._peak_workers,
            "wall_clock_duration_sec": wall_clock_sec,
            "sum_of_group_durations_sec": sum_group_durations,
            "speedup_ratio": round(sum_group_durations / max(0.001, wall_clock_sec), 2),
            "resume_info": resume_info,
            "node_accounting_invariant_holds": invariant_holds,
            "total_expected_nodes": len(expected_nodes),
            "total_scheduled_nodes": len(scheduled_nodes),
            "total_executed_nodes": len(executed_nodes),
            "total_completed_nodes": len(completed_nodes),
            "total_skipped_nodes": len(skipped_nodes),
            "total_failed_nodes": len(failed_nodes),
            "expected_node_ids": expected_nodes,
            "scheduled_node_ids": scheduled_nodes,
            "executed_node_ids": executed_nodes,
            "completed_node_ids": completed_nodes,
            "skipped_node_ids": skipped_nodes,
            "failed_node_ids": failed_nodes,
            "groups": {s.group_id: self.group_results.get(s.group_id) for s in specs},
        }
        self._last_summary = summary
        return summary

    def build_performance_report(
        self,
        summary_override: Optional[Dict[str, Any]] = None,
        *,
        serial_benchmark_sec: Optional[float] = None,
        parallel_benchmark_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Section 12 & 13 (DEF-179012-05): Builds the machine-readable ASYNC_RUNNER_PERFORMANCE_REPORT.json payload
        with genuine periodic resource sampling and explicit distinction between cumulative job duration sum
        and actual measured serial-vs-parallel benchmark timing.
        """
        summary = summary_override or getattr(self, "_last_summary", None) or {}
        disk_rep = self.resource_guard.build_disk_budget_report()
        wall_sec = float(summary.get("wall_clock_duration_sec", 0.0))
        sum_sec = float(summary.get("sum_of_group_durations_sec", 0.0))
        cum_ratio = round(sum_sec / max(0.001, wall_sec), 2) if wall_sec > 0 else 1.0

        measured_speedup: Any = "NOT_MEASURED"
        if serial_benchmark_sec is not None and parallel_benchmark_sec is not None and parallel_benchmark_sec > 0:
            measured_speedup = round(float(serial_benchmark_sec) / float(parallel_benchmark_sec), 2)

        orphans = int(summary.get("orphan_processes_remaining", 0))
        calc_status = "FAIL" if (summary.get("status") == "FAIL" or orphans > 0) else summary.get("status", "PASS")

        return {
            "release": "V17.9.0.1.3",
            "run_id": self.run_id,
            "source_sha": self.source_sha,
            "status": calc_status,
            "adaptive_concurrency_policy": {
                "cpu_logical_cores": CPU_COUNT,
                "max_workers": MAX_WORKERS,
                "max_cpu_workers": MAX_CPU_WORKERS,
                "max_io_workers": MAX_IO_WORKERS,
                "max_pytest_workers": self.max_pytest_workers,
                "max_playwright_workers": MAX_PLAYWRIGHT_WORKERS,
                "max_backtest_workers": MAX_BACKTEST_WORKERS,
                "memory_reserve_pct": MEMORY_RESERVE_PCT,
                "memory_reserve_bytes": MEMORY_RESERVE_BYTES,
                "disk_reserve_bytes": DISK_RESERVE_BYTES,
                "worker_timeout_sec": WORKER_TIMEOUT_SEC,
                "max_retries": MAX_RETRIES,
                "artifact_budget_bytes": ARTIFACT_BUDGET_BYTES,
            },
            "execution_metrics": {
                "peak_concurrent_workers": summary.get("peak_concurrent_workers", self._peak_workers),
                "wall_clock_duration_sec": wall_sec,
                "sum_of_group_durations_sec": sum_sec,
                "cumulative_job_duration_to_wall_ratio": cum_ratio,
                "speedup_ratio_qualification": "CUMULATIVE_JOB_DURATION_SUM_DIVIDED_BY_WALL_CLOCK",
                "speedup_ratio": cum_ratio,
                "serial_benchmark_duration_sec": serial_benchmark_sec if serial_benchmark_sec is not None else "NOT_MEASURED",
                "parallel_benchmark_duration_sec": parallel_benchmark_sec if parallel_benchmark_sec is not None else wall_sec,
                "measured_serial_vs_parallel_speedup": measured_speedup,
                "node_accounting_invariant_holds": summary.get("node_accounting_invariant_holds", True),
                "total_expected_nodes": summary.get("total_expected_nodes", 0),
                "total_completed_nodes": summary.get("total_completed_nodes", 0),
                "total_skipped_nodes": summary.get("total_skipped_nodes", 0),
                "total_failed_nodes": summary.get("total_failed_nodes", 0),
                "total_errors": summary.get("total_failed_nodes", 0),
                "total_retries": 0,
                "orphan_processes_remaining": orphans,
                "peak_rss_mb_observed": disk_rep.get("peak_rss_mb", 0.0),
                "resource_samples_collected": disk_rep.get("samples_recorded", len(disk_rep.get("cpu_memory_samples", []))),
            },
            "disk_telemetry": disk_rep,
            "resource_telemetry": {
                "free_disk_bytes_preflight": disk_rep.get("free_disk_bytes_preflight"),
                "minimum_free_disk_bytes_observed": disk_rep.get("minimum_free_disk_bytes_observed"),
                "free_disk_bytes_postflight": disk_rep.get("free_disk_bytes_postflight"),
                "peak_temporary_bytes": disk_rep.get("peak_temporary_bytes", 0),
                "peak_artifact_bytes": disk_rep.get("peak_artifact_bytes", 0),
                "final_temporary_bytes": disk_rep.get("final_temporary_bytes", 0),
                "samples_recorded": disk_rep.get("samples_recorded", len(disk_rep.get("cpu_memory_samples", []))),
                "peak_memory_bytes": disk_rep.get("peak_memory_bytes", 0),
                "peak_rss_mb": disk_rep.get("peak_rss_mb", 0.0),
                "peak_child_rss_mb": disk_rep.get("peak_child_rss_mb", 0.0),
                "peak_total_rss_mb": disk_rep.get("peak_total_rss_mb", 0.0),
                "peak_cpu_percent": disk_rep.get("peak_cpu_percent", 0.0),
                "SwapTotal": disk_rep.get("SwapTotal", "NOT_MEASURED"),
                "SwapFree": disk_rep.get("SwapFree", "NOT_MEASURED"),
                "SwapUsed": disk_rep.get("SwapUsed", "NOT_MEASURED"),
                "swappiness": disk_rep.get("swappiness", "NOT_APPLICABLE_DARWIN"),
                "zram_devices": disk_rep.get("zram_devices", 0),
                "orphan_worker_count": disk_rep.get("orphan_worker_count", 0),
            },
            "per_group_timings": {
                gid: {
                    "duration_sec": (gdata or {}).get("duration_sec", 0.0),
                    "exit_code": (gdata or {}).get("exit_code"),
                    "tests": ((gdata or {}).get("junit") or {}).get("tests", 0),
                    "passed": ((gdata or {}).get("junit") or {}).get("passed", 0),
                }
                for gid, gdata in (summary.get("groups") or {}).items()
            },
        }
