#!/usr/bin/env python3
"""
tools/run_v17_9_3_1_2_13_verification.py

AURUM Desk — V17.9.3.1.2.13 Full Autonomous Verification & Research Orchestrator:
  - Phase A (P0-E1): Genuine Post-Signal Closed-Bar Delayed Entry (A1B, D1..D4), Structural SL/TP/Sizing
    ($2.50 risk cap), Chronological 1-Pos Portfolio Replay & Shared Cross-Adapter Parity
  - Phase B (P0-E2 / P0-E3): End-to-End HTF / Unicorn / SI8 / SI9 Materialization Truth across 63,085
    Decisions, 267 SI9 Target Audit, 113 HTF Conflict Audit & Materialized Single-Axis Ablations
  - Phase C (R1): Pre-Entry Observable Loss Causes (A..E + OTHER/UNKNOWN) vs Ex-Post Stop Diagnostics (F..H)
  - Phase D: Full 30-Partition (SI0..SI9 x 3 Fill Modes) Raw-Stream Exit-Event Lineage & Source Reconciliation
  - Phase E (P0-E4): Single Immutable AuthoritativeMetricsV17931213 Report & 12 Dynamic Acceptance Gates
  - Phase F (P1-E5): Bounded Async `asyncio.create_subprocess_exec` Multi-Worker Sharded Pytest (<= 4 children,
    soft RAM downshift at 2.0 GiB / 1.65 GiB, 0.5s telemetry) + Worker 1/2/4/1_repeat Determinism
  - Phase G & H (P1-E6): Fresh Backend Pytest JUnit Bijection, Honest OOS Lock & V01..V66 Negative Controls
"""
from __future__ import annotations

import argparse
import asyncio
from collections import ChainMap
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
import xml.etree.ElementTree as ET

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.v17_9_3_1_2_2_signal_intelligence import (
    CANONICAL_V1793123_POLICY_IDS,
    V17_9_3_1_2_2_SCHEMA_VERSION,
)
from services.v17_9_3_1_2_7_causal_signal_truth import (
    canonical_hash,
    verify_feature_snapshot_v1793127,
)
from services.v17_9_3_1_2_10_canonical_reconciliation_and_provenance import (
    analyze_15_distinct_recovered_trades_v17931210,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    CANONICAL_DATASET_SHA256_V17931211,
    MANDATORY_BRANCH_V17931211,
    build_canonical_report_metrics_v17931211,
    build_exit_event_lineage_v17931211,
    render_truth_closure_readme_and_handover_v17931211,
    verify_all_policy_fill_mode_lineage_independently_v17931211,
    verify_exit_portfolio_conservation_v17931211,
    verify_fresh_junit_bijection_v17931211,
    verify_process_exit_and_timeout_v17931211,
    verify_source_freeze_v17931211,
)
from services.v17_9_3_1_2_12_provenance_forensics_and_repair_service import (
    build_r1_trade_by_trade_casebook_and_matrix_v17931212,
    build_r4_backtest_stress_and_oos_provenance_v17931212,
    verify_r1_forensic_separation_and_conservation_v17931212,
    verify_r4_oos_and_activation_verdict_v17931212,
)
from services.v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service import (
    CANONICAL_DATASET_SHA256_V17931213,
    MANDATORY_BRANCH_V17931213,
    SCHEMA_VERSION_V17931213,
    VERSION_V17931213,
    build_authoritative_metrics_v17931213,
    build_delayed_entry_contract_and_ledgers_v17931213,
    build_htf_unicorn_and_si8_si9_dynamic_funnel_v17931213,
    build_pre_entry_forensics_and_d1_d4_transitions_v17931213,
    build_v17931213_callgraph_and_defect_matrix,
    enrich_si8_aligned_decisions_and_features_v17931213,
    verify_bounded_async_workers_v17931213,
    evaluate_v17931213_gate_matrix,
    render_v17931213_readme_and_handover,
    run_negative_controls_v01_to_v66_v17931213,
    verify_delayed_entry_and_structural_sl_tp_v17931213,
    verify_htf_unicorn_and_si8_si9_funnel_v17931213,
    verify_v17931213_authoritative_report_and_gates_consistency,
)
from tools.run_v17_9_3_1_2_1_full_opportunity_replay import (
    DATASET_PATH,
    FILL_MODES,
    PRIOR_V179312_RUN_DIR,
    _load_verified_decision_stream_chunks,
)
from tools.run_v17_9_3_1_2_2_entry_signal_backtest import replay_single_si_policy_v1793122
from tools.run_v17_9_3_1_2_2_verification import (
    canonicalize_collect_node_id_v1793122,
    canonicalize_junit_testcase_node_id_v1793122,
    verify_collect_vs_junit_node_id_bijection_v1793122,
)
from tools.run_v17_9_3_1_2_7_verification import (
    _active_heavy_worker_children,
    git,
    source_manifest,
)
from tools.run_v17_9_3_1_2_11_verification import (
    _run_worker_slice_cli,
    run_worker_1_2_4_and_repeat_determinism_v17931211,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets

GROUNDED_V12_PUBLICATION_HEAD = "63f2481ea7989d08d3e55a7945b6ce5eb5b1dc98"
GROUNDED_V12_TESTED_SHA = "a12759da782fbf2399657c727f57138d23280b3b"
V130_RUN = APP_ROOT / "docs/artifacts/v17_9_3_1_2_10/20261107T150245Z_b772a03bb7cc"
V12_LATEST_RUN_PTR = APP_ROOT / "docs/artifacts/v17_9_3_1_2_12/latest_run.json"

CHILD_SPAWNING_TEST_FILES: Set[str] = {
    "test_r331_acceptance_runner_and_resources.py",
    "test_r332_raw_regression_retry_integrity.py",
    "test_v16_39_10_snapshot_api_cli.py",
    "test_v17_6_4_2_acceptance_integrity.py",
    "test_v17_6_4_3a_acceptance_integrity.py",
    "test_v17_6_4_3a_discovery_integrity.py",
    "test_v17_6_4_3b_acceptance_integrity.py",
    "test_v17_7_adaptive_stop_loss.py",
    "test_v17_7_database_safety.py",
    "test_v17_8_0_4_parity_and_integrity.py",
    "test_v17_9_0_1_2_strict_eligibility_and_parity.py",
    "test_v17_9_1_positive_path_replay.py",
    "test_v17_9_3_1_2_10_canonical_universe_and_provenance.py",
    "test_v17_9_3_1_2_11_verification_truth_and_exit_lineage.py",
    "test_v17_9_3_1_2_12_provenance_forensics_and_repair.py",
    "test_v17_9_3_1_2_13_delayed_entry_materialization_and_truth.py",
    "test_v17_9_3_1_2_1_root_cause_evidence.py",
    "test_v17_9_3_1_2_2_verification_integrity.py",
    "test_v17_9_3_1_2_7_causal_truth.py",
    "test_v17_9_3_1_2_9_liquidity_provenance.py",
    "test_v17_9_3_1_2_full_universe_executable_interventions.py",
    "test_v17_9_3_1_historical_execution_truth.py",
}

SHARED_DB_SERIAL_TEST_FILES: Set[str] = {
    "test_migration_runtime_upgrade.py",
    "test_v10_2_acceptance.py",
    "test_v11_historical_replay.py",
    "test_v12_1_acceptance.py",
    "test_v12_2_acceptance.py",
    "test_v12_4_acceptance.py",
    "test_v12_three_month_replay.py",
    "test_v17_8_0_3_market_shadow.py",
    "test_v17_9_shared_parity_and_async_infra.py",
    "test_v6_1_isolation_and_recovery.py",
}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False, default=str)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def write_jsonl_gz(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False, default=str) + "\n")
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


def prior_artifact_manifest_v17931213() -> Dict[str, Any]:
    """Hashes all prior immutable artifact directories (.7, .8, .9, .10, .11, AND .12)."""
    files: Dict[str, str] = {}
    for ver_dir in (
        "v17_9_3_1_2_7",
        "v17_9_3_1_2_8",
        "v17_9_3_1_2_9",
        "v17_9_3_1_2_10",
        "v17_9_3_1_2_11",
        "v17_9_3_1_2_12",
    ):
        root = APP_ROOT / "docs/artifacts" / ver_dir
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(REPO_ROOT)).replace("\\", "/")
                files[rel] = file_sha256(p)
    return {
        "files_count": len(files),
        "sha256": canonical_hash(files),
        "files": files,
    }


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


class ResourceMonitorV17931213:
    """
    0.5s process-tree, available-RAM, and free-disk telemetry monitor enforcing:
      - `max_children <= 4` global heavy child process cap
      - `minimum_disk_bytes = 10 GiB` free disk floor
      - `minimum_ram_bytes` hard abort floor + `2.0 GiB` / `1.65 GiB` soft downshift tracking
    """

    def __init__(
        self,
        *,
        minimum_ram_bytes: int = 768 * 1024**2,
        soft_downshift_ram_bytes: int = 2 * 1024**3,
        minimum_disk_bytes: int = 10 * 1024**3,
    ) -> None:
        self.stop_event = threading.Event()
        self.samples: List[Dict[str, Any]] = []
        self.error: Optional[str] = None
        self.abort_path: Optional[Path] = None
        self.minimum_ram_bytes = minimum_ram_bytes
        self.soft_downshift_ram_bytes = soft_downshift_ram_bytes
        self.minimum_disk_bytes = minimum_disk_bytes
        self.soft_downshift_events = 0

    def check(self) -> Dict[str, Any]:
        import psutil

        proc = psutil.Process()
        children = _active_heavy_worker_children(proc)
        ram = int(psutil.virtual_memory().available)
        free_disk = int(shutil.disk_usage(REPO_ROOT).free)

        def _child_rss(ch: Any) -> int:
            try:
                return int(ch.memory_info().rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                return 0

        if ram < self.soft_downshift_ram_bytes:
            self.soft_downshift_events += 1

        row = {
            "epoch_ns": time.time_ns(),
            "free_disk_bytes": free_disk,
            "available_ram_bytes": ram,
            "system_cpu_percent": psutil.cpu_percent(),
            "worker_pids": [p.pid for p in children],
            "rss_bytes": int(proc.memory_info().rss),
            "child_rss_bytes": sum(_child_rss(p) for p in children),
        }
        self.samples.append(row)
        if len(self.samples) > 12000:
            self.samples = self.samples[::2]

        if free_disk < self.minimum_disk_bytes:
            self.error = "BLOCKED_LOW_DISK"
        elif ram < self.minimum_ram_bytes:
            self.error = "BLOCKED_LOW_RAM"
        elif len(children) > 4:
            self.error = f"BLOCKED_GLOBAL_WORKER_CAP:{len(children)}"
        if self.error:
            raise RuntimeError(self.error)
        return row

    def _loop(self) -> None:
        while not self.stop_event.wait(0.5):
            try:
                self.check()
            except Exception as exc:
                self.error = str(exc)
                if self.abort_path:
                    write_json(
                        self.abort_path,
                        {
                            "status": "BLOCKED",
                            "reason": self.error,
                            "last_sample": self.samples[-1] if self.samples else None,
                        },
                    )
                import psutil

                for child in reversed(_active_heavy_worker_children(psutil.Process())):
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
                break

    def start(self) -> None:
        self.check()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self) -> Dict[str, Any]:
        self.stop_event.set()
        if hasattr(self, "thread"):
            self.thread.join(timeout=5)
        import psutil

        remaining = _active_heavy_worker_children(psutil.Process())
        remaining_pids = [p.pid for p in remaining]
        if remaining:
            self.error = self.error or "BLOCKED_ORPHAN_WORKERS_REMAINING"
            for child in reversed(remaining):
                try:
                    child.terminate()
                except psutil.Error:
                    pass
        return {
            "samples": self.samples,
            "error": self.error,
            "global_worker_cap": 4,
            "sampling_interval_seconds": 0.5,
            "soft_downshift_ram_bytes": self.soft_downshift_ram_bytes,
            "soft_downshift_events": self.soft_downshift_events,
            "orphan_pids_observed_at_cleanup": remaining_pids,
            "max_children_observed": max((len(s["worker_pids"]) for s in self.samples), default=0),
            "minimum_available_ram_bytes": min((s["available_ram_bytes"] for s in self.samples), default=0),
            "minimum_free_disk_bytes": min((s["free_disk_bytes"] for s in self.samples), default=0),
            "peak_total_rss_bytes": max((s["rss_bytes"] + s["child_rss_bytes"] for s in self.samples), default=0),
        }


def _build_async_balanced_shards_v17931213(
    all_test_files: Sequence[str],
) -> List[Dict[str, Any]]:
    """
    Partitions `backend/tests/test_*.py` into isolated shards for `asyncio.create_subprocess_exec`:
      - Exclusive shards (`exclusive=True`, run with concurrency=1 so nested subprocesses never
        exceed the global 4-child cap): DB ownership + child-spawning test suites.
      - Parallel in-process shards (`exclusive=False`, run up to `3` concurrently with RAM-aware
        soft downshifting):
        * `SHARD_INPROC_00_RUNTIME_GATES` (`test_v17_9_0_1_runtime_integration_and_gates.py` started first)
        * `SHARD_INPROC_01_V16_23_ISOLATED` (`test_v16_23_red_reproductions.py`)
        * `SHARD_INPROC_02_SHARED_SQLITE_SERIAL` (all non-spawning tests referencing `aurum_desk.db`)
        * `SHARD_INPROC_03` .. `SHARD_INPROC_12` (size-balanced pure in-process test buckets)
    """
    db_own: List[str] = []
    child_spawn: List[str] = []
    v17_9_0_1_heavy: List[str] = []
    v16_23_iso: List[str] = []
    db_serial: List[str] = []
    regular_inproc: List[str] = []

    for rel in all_test_files:
        fname = Path(rel).name
        if fname == "test_v17_7_database_safety.py":
            db_own.append(rel)
        elif fname in CHILD_SPAWNING_TEST_FILES:
            child_spawn.append(rel)
        elif fname == "test_v17_9_0_1_runtime_integration_and_gates.py":
            v17_9_0_1_heavy.append(rel)
        elif fname == "test_v16_23_red_reproductions.py":
            v16_23_iso.append(rel)
        elif fname in SHARED_DB_SERIAL_TEST_FILES:
            db_serial.append(rel)
        else:
            regular_inproc.append(rel)

    shards: List[Dict[str, Any]] = []
    # Launch long-running in-process shards first so they overlap cleanly with medium shards
    if v17_9_0_1_heavy:
        shards.append({"shard_name": "SHARD_INPROC_00_V17_9_0_1_RUNTIME", "files": v17_9_0_1_heavy, "exclusive": False, "needs_venv": False})
    if db_serial:
        shards.append({"shard_name": "SHARD_INPROC_01_SHARED_DB_SERIAL", "files": sorted(db_serial), "exclusive": False, "needs_venv": False})
    if v16_23_iso:
        shards.append({"shard_name": "SHARD_INPROC_02_V16_23_ISOLATED", "files": v16_23_iso, "exclusive": False, "needs_venv": False})

    num_buckets = 10
    buckets: List[List[str]] = [[] for _ in range(num_buckets)]
    weights = [0 for _ in range(num_buckets)]
    sized_reg = sorted(
        regular_inproc,
        key=lambda r: (APP_ROOT / r).stat().st_size if (APP_ROOT / r).exists() else 1,
        reverse=True,
    )
    for r in sized_reg:
        b_idx = min(range(num_buckets), key=lambda i: weights[i])
        buckets[b_idx].append(r)
        weights[b_idx] += (APP_ROOT / r).stat().st_size if (APP_ROOT / r).exists() else 1

    for idx, b_files in enumerate(buckets, start=3):
        if b_files:
            shards.append(
                {
                    "shard_name": f"SHARD_INPROC_{idx:02d}_BALANCED",
                    "files": sorted(b_files),
                    "exclusive": False,
                    "needs_venv": False,
                }
            )

    # Exclusive shards (nested subprocess suites + DB ownership)
    if child_spawn:
        mid = max(1, len(child_spawn) // 2)
        shards.append({"shard_name": "SHARD_EXCL_01_NESTED_SUBPROC_A", "files": sorted(child_spawn[:mid]), "exclusive": True, "needs_venv": False})
        if child_spawn[mid:]:
            shards.append({"shard_name": "SHARD_EXCL_02_NESTED_SUBPROC_B", "files": sorted(child_spawn[mid:]), "exclusive": True, "needs_venv": False})
    if db_own:
        shards.append({"shard_name": "SHARD_EXCL_03_DB_OWNERSHIP", "files": db_own, "exclusive": True, "needs_venv": True})

    return shards


async def _run_async_pytest_shards_impl_v17931213(
    junit_dir: Path,
    *,
    run_id: str,
    tested_executable_sha: str,
    max_parallel_workers: int = 3,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    import psutil
    from tools.fresh_junit_io import read_closed_junit_artifact

    junit_dir.mkdir(parents=True, exist_ok=True)
    for old_f in junit_dir.glob("*"):
        if old_f.is_file():
            old_f.unlink(missing_ok=True)

    run_start_epoch_ns = time.time_ns()
    t_wall_0 = time.time()
    py_exe = sys.executable

    all_test_files = sorted(
        str(p.relative_to(APP_ROOT)).replace("\\", "/")
        for p in (BACKEND_ROOT / "tests").glob("test_*.py")
        if p.is_file()
    )
    shard_specs = _build_async_balanced_shards_v17931213(all_test_files)

    # 1. Fresh collection via asyncio.create_subprocess_exec
    col_proc = await asyncio.create_subprocess_exec(
        py_exe,
        "-m",
        "pytest",
        str(BACKEND_ROOT / "tests"),
        "--collect-only",
        "-q",
        cwd=str(APP_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    col_out_b, _ = await col_proc.communicate()
    col_rc = int(col_proc.returncode or 0)
    col_stdout = col_out_b.decode("utf-8", errors="replace")

    collected_by_file: Dict[str, List[str]] = {}
    all_collected_ids: List[str] = []
    for ln in col_stdout.splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            f_part = cid.split("::", 1)[0]
            collected_by_file.setdefault(f_part, []).append(cid)
            all_collected_ids.append(cid)

    # Dynamic RAM-aware slot controller (`max_parallel_workers <= 3`, exclusive shards take all slots)
    cond = asyncio.Condition()
    active_slots = 0
    exclusive_running = False
    downshift_count = 0
    peak_parallel_shards = 0

    def _effective_slot_limit() -> int:
        nonlocal downshift_count
        avail = int(psutil.virtual_memory().available)
        if avail < int(1.65 * 1024**3):
            downshift_count += 1
            return 1
        if avail < int(2.0 * 1024**3):
            downshift_count += 1
            return min(2, max_parallel_workers)
        return max_parallel_workers

    async def _acquire_slot(exclusive: bool) -> None:
        nonlocal active_slots, exclusive_running, peak_parallel_shards
        async with cond:
            while True:
                abort_p = os.environ.get("AURUM_RESOURCE_GUARD_ABORT_FILE")
                if abort_p and Path(abort_p).exists():
                    raise RuntimeError("RESOURCE_GUARD_ABORTED_ASYNC_SHARDS")
                if exclusive:
                    if active_slots == 0 and not exclusive_running:
                        exclusive_running = True
                        active_slots = 1
                        peak_parallel_shards = max(peak_parallel_shards, 1)
                        return
                else:
                    lim = _effective_slot_limit()
                    if not exclusive_running and active_slots < lim:
                        active_slots += 1
                        peak_parallel_shards = max(peak_parallel_shards, active_slots)
                        return
                await cond.wait()

    async def _release_slot(exclusive: bool) -> None:
        nonlocal active_slots, exclusive_running
        async with cond:
            if exclusive:
                exclusive_running = False
                active_slots = 0
            else:
                active_slots = max(0, active_slots - 1)
            cond.notify_all()

    async def _exec_one_shard(spec: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        shard_name = str(spec["shard_name"])
        rel_paths: List[str] = list(spec["files"])
        exclusive = bool(spec["exclusive"])
        needs_venv = bool(spec["needs_venv"])

        await _acquire_slot(exclusive)
        try:
            abs_paths = [str(APP_ROOT / rp) for rp in rel_paths]
            xml_path = junit_dir / f"{shard_name}.xml"
            log_path = xml_path.with_suffix(".log")
            shard_collected_ids: List[str] = []
            for rp in rel_paths:
                shard_collected_ids.extend(collected_by_file.get(rp, []))

            exe_bin = (
                os.environ["AURUM_VERIFY_VENV_PYTHON"]
                if needs_venv and os.environ.get("AURUM_VERIFY_VENV_PYTHON")
                else py_exe
            )
            cmd_argv = [exe_bin, "-m", "pytest", *abs_paths, f"--junitxml={xml_path}", "-q"]
            child_env = dict(os.environ)
            child_budget = 1 if needs_venv else (2 if exclusive else 1)
            child_env["AURUM_GLOBAL_PROCESS_BUDGET"] = str(child_budget)

            t_s = time.time()
            st_ms = int(t_s * 1000)
            start_utc = datetime.now(timezone.utc).isoformat()

            with log_path.open("wb") as log_f:
                proc = await asyncio.create_subprocess_exec(
                    *cmd_argv,
                    cwd=str(APP_ROOT),
                    env=child_env,
                    stdout=log_f,
                    stderr=asyncio.subprocess.STDOUT,
                )
                shard_pid = int(proc.pid)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1800.0)
                except asyncio.TimeoutError:
                    proc.terminate()
                    await proc.wait()
                    raise RuntimeError(f"ASYNC_PYTEST_SHARD_TIMEOUT:{shard_name}")

            et_ms = max(st_ms + 1, int(time.time() * 1000))
            end_utc = datetime.now(timezone.utc).isoformat()
            elapsed = round(time.time() - t_s, 2)
            ret_code = int(proc.returncode or 0)

            with log_path.open("rb") as rf:
                rf.seek(max(0, log_path.stat().st_size - 400))
                err_tail = rf.read().decode("utf-8", errors="replace")
            log_path.unlink(missing_ok=True)

            shard_junit_ids: List[str] = []
            xml_tests = xml_failures = xml_errors = xml_skipped = xml_mtime_ns = 0
            xml_sha256 = ""
            if xml_path.exists():
                xml_bytes, st_stat = read_closed_junit_artifact(xml_path)
                xml_mtime_ns = int(st_stat.st_mtime_ns)
                xml_sha256 = hashlib.sha256(xml_bytes).hexdigest()
                root = ET.fromstring(xml_bytes)
                suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
                for st in suites:
                    xml_tests += int(st.attrib.get("tests", 0))
                    xml_failures += int(st.attrib.get("failures", 0))
                    xml_errors += int(st.attrib.get("errors", 0))
                    xml_skipped += int(st.attrib.get("skipped", 0))
                    for tc in st.findall("testcase"):
                        jid = canonicalize_junit_testcase_node_id_v1793122(
                            tc.attrib.get("classname", ""), tc.attrib.get("name", "")
                        )
                        if jid is not None:
                            shard_junit_ids.append(jid)

            bij = verify_collect_vs_junit_node_id_bijection_v1793122(shard_collected_ids, shard_junit_ids)
            print(
                f"  [AsyncShard {shard_name}] excl={exclusive} pid={shard_pid} files={len(rel_paths)} "
                f"junit={len(shard_junit_ids)} rc={ret_code} bij={bij['exact_node_id_bijection_verified']} in {elapsed}s",
                flush=True,
            )
            shard_rec = {
                "shard_name": shard_name,
                "reused": False,
                "exclusive_slot": exclusive,
                "run_id": run_id,
                "tested_executable_sha": tested_executable_sha,
                "env_sha256": hashlib.sha256(
                    json.dumps(
                        {
                            "interpreter": cmd_argv[0],
                            "version": sys.version,
                            "tested_sha": tested_executable_sha,
                            "nested_process_budget": child_budget,
                        },
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
                "command_argv": cmd_argv,
                "start_time_utc": start_utc,
                "end_time_utc": end_utc,
                "pid": shard_pid,
                "test_files_count": len(rel_paths),
                "test_files": rel_paths,
                "collect_exit_code": col_rc,
                "pytest_exit_code": ret_code,
                "exit_code": ret_code,
                "timed_out": False,
                "collected_count": len(shard_collected_ids),
                "executed_junit_count": len(shard_junit_ids),
                "passed_count": len(shard_junit_ids) - xml_failures - xml_errors - xml_skipped,
                "xml_tests_attr": xml_tests,
                "failed_count": xml_failures,
                "error_count": xml_errors,
                "skipped_count": xml_skipped,
                "exact_bijection": bij["exact_node_id_bijection_verified"],
                "elapsed_seconds": elapsed,
                "xml_mtime_ns": xml_mtime_ns,
                "xml_sha256": xml_sha256,
                "stderr_tail": err_tail[-400:],
                "nested_process_budget": child_budget,
                "junit_xml_path": f"junit/{xml_path.name}",
                "_junit_ids": shard_junit_ids,
            }
            worker_rec = {
                "worker_id": f"pytest_{shard_name}",
                "run_id": run_id,
                "tested_source_sha": tested_executable_sha,
                "pid": shard_pid,
                "started_at_ms": st_ms,
                "completed_at_ms": et_ms,
                "exit_code": ret_code,
                "process_exit_code": ret_code,
                "task_validation_verdict": "PASS" if ret_code == 0 else "FAIL",
                "process_identity_provenance": "ASYNCIO_CREATE_SUBPROCESS_EXEC",
                "timed_out": False,
                "command_argv": cmd_argv,
            }
            return shard_rec, worker_rec
        finally:
            await _release_slot(exclusive)

    # Schedule in-process shards concurrently first, then exclusive shards
    inproc_specs = [s for s in shard_specs if not s["exclusive"]]
    excl_specs = [s for s in shard_specs if s["exclusive"]]

    inproc_results = await asyncio.gather(*(_exec_one_shard(s) for s in inproc_specs))
    excl_results: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for es in excl_specs:
        excl_results.append(await _exec_one_shard(es))

    combined = list(inproc_results) + excl_results
    all_junit_ids: List[str] = []
    clean_shards: List[Dict[str, Any]] = []
    worker_records: List[Dict[str, Any]] = []
    for sh_r, wk_r in combined:
        sc = dict(sh_r)
        all_junit_ids.extend(sc.pop("_junit_ids", []))
        clean_shards.append(sc)
        worker_records.append(wk_r)

    all_collected_ids.sort()
    all_junit_ids.sort()
    global_bij = verify_collect_vs_junit_node_id_bijection_v1793122(all_collected_ids, all_junit_ids)
    col_sha = hashlib.sha256("\n".join(all_collected_ids).encode("utf-8")).hexdigest()
    junit_sha = hashlib.sha256("\n".join(all_junit_ids).encode("utf-8")).hexdigest()

    tot_failed = sum(int(s["failed_count"]) for s in clean_shards)
    tot_errors = sum(int(s["error_count"]) for s in clean_shards)
    tot_skipped = sum(int(s["skipped_count"]) for s in clean_shards)
    nonzero_shards = sum(
        1 for s in clean_shards if int(s["pytest_exit_code"]) != 0 or int(s["collect_exit_code"]) != 0
    )
    wall_elapsed_s = round(time.time() - t_wall_0, 3)
    sum_shard_cpu_wall_s = round(sum(float(s["elapsed_seconds"]) for s in clean_shards), 3)

    collection_manifest = {
        "schema_version": V17_9_3_1_2_2_SCHEMA_VERSION,
        "run_id": run_id,
        "tested_executable_sha": tested_executable_sha,
        "collect_exit_code": col_rc,
        "discovered_test_files_count": len(all_test_files),
        "collected_node_ids_count": len(all_collected_ids),
        "collected_node_ids_sha256": col_sha,
        "collected_node_ids": all_collected_ids,
        "discovered_test_files": all_test_files,
    }
    shard_manifest = {
        "schema_version": V17_9_3_1_2_2_SCHEMA_VERSION,
        "run_id": run_id,
        "tested_executable_sha": tested_executable_sha,
        "force_fresh_execution": True,
        "run_start_epoch_ns": run_start_epoch_ns,
        "shard_count": len(clean_shards),
        "freshly_executed_shards_count": len(clean_shards),
        "reused_shards_count": 0,
        "max_workers": max_parallel_workers,
        "collected_node_ids_count": len(all_collected_ids),
        "executed_junit_node_ids_count": len(all_junit_ids),
        "passed_count": len(all_junit_ids) - tot_failed - tot_errors - tot_skipped,
        "failed_count": tot_failed,
        "error_count": tot_errors,
        "skipped_count": tot_skipped,
        "nonzero_exit_shards_count": nonzero_shards,
        "collected_node_ids_sha256": col_sha,
        "executed_junit_node_ids_sha256": junit_sha,
        "executed_junit_node_ids": all_junit_ids,
        "missing_from_junit_count": global_bij["missing_from_junit_count"],
        "unexpected_in_junit_count": global_bij["unexpected_in_junit_count"],
        "exact_node_id_bijection_verified": (
            global_bij["exact_node_id_bijection_verified"]
            and nonzero_shards == 0
            and tot_failed == 0
            and tot_errors == 0
        ),
        "shards": clean_shards,
    }
    async_perf_stats = {
        "scheduler": "ASYNCIO_CREATE_SUBPROCESS_EXEC_BOUNDED_SLOTS",
        "configured_max_parallel_shards": max_parallel_workers,
        "peak_parallel_shards_observed": peak_parallel_shards,
        "ram_soft_downshift_checks_triggered": downshift_count,
        "wall_clock_elapsed_seconds": wall_elapsed_s,
        "sum_individual_shard_seconds": sum_shard_cpu_wall_s,
        "internal_parallel_efficiency_ratio": round(sum_shard_cpu_wall_s / max(wall_elapsed_s, 0.001), 3),
    }
    return collection_manifest, shard_manifest, worker_records, async_perf_stats


def _read_v12_stage_a_baseline_seconds_and_funnel() -> Tuple[float, Optional[Dict[str, Any]]]:
    v12_stage_a_s = 2096.97
    v12_f09: Optional[Dict[str, Any]] = None
    if V12_LATEST_RUN_PTR.exists():
        ptr = json.loads(V12_LATEST_RUN_PTR.read_text(encoding="utf-8"))
        v12_dir = REPO_ROOT / str(ptr["run_dir"])
        st_path = v12_dir / "run_status.jsonl"
        if st_path.exists():
            for ln in st_path.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    r = json.loads(ln)
                    if r.get("stage") == "STAGE_A_FRESH_PYTEST_SHARDS" and r.get("status") == "COMPLETED":
                        v12_stage_a_s = float(r.get("elapsed_s", v12_stage_a_s))
        f09_path = v12_dir / "09_r2_si8_si9_278_to_0_funnel_and_ablation.json"
        if f09_path.exists():
            v12_f09 = json.loads(f09_path.read_text(encoding="utf-8"))
    return v12_stage_a_s, v12_f09


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tested-code-sha", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--worker-slice-input", default="")
    ap.add_argument("--worker-slice-output", default="")
    args = ap.parse_args()

    if args.worker_slice_input and args.worker_slice_output:
        return _run_worker_slice_cli(Path(args.worker_slice_input), Path(args.worker_slice_output))
    if not args.tested_code_sha:
        raise SystemExit("--tested-code-sha is required")

    if os.name == "nt":
        os.environ["AURUM_VERIFY_VENV_PYTHON"] = sys.executable
        packages = str(Path(sys.prefix) / "Lib/site-packages")
        os.environ["PYTHONPATH"] = os.pathsep.join(
            [str(BACKEND_ROOT), str(APP_ROOT), packages, os.environ.get("PYTHONPATH", "")]
        )
        sys.executable = getattr(sys, "_base_executable", sys.executable)
        found = shutil.which("git")
        direct = Path(found).parents[1] / "mingw64/bin" if found else None
        if direct and (direct / "git.exe").is_file():
            os.environ["PATH"] = str(direct) + os.pathsep + os.environ.get("PATH", "")

    sha = git("rev-parse", "HEAD")
    dirty_source = git("diff", "--name-only", "HEAD", "--", "aurum-desk/backend", "aurum-desk/tools").splitlines()
    branch = git("branch", "--show-current")
    if sha != args.tested_code_sha or dirty_source:
        raise SystemExit(
            f"FROZEN_EXECUTABLE_SHA_MISMATCH_OR_DIRTY_SOURCE:sha={sha},expected={args.tested_code_sha},dirty={dirty_source}"
        )
    if branch != MANDATORY_BRANCH_V17931213:
        raise SystemExit(f"WRONG_BRANCH:{branch}")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", GROUNDED_V12_PUBLICATION_HEAD, sha],
        cwd=REPO_ROOT,
        check=True,
    )

    run_id = args.run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + sha[:12])
    run_dir = APP_ROOT / "docs/artifacts/v17_9_3_1_2_13" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    aux_temp_dir = Path(tempfile.gettempdir()) / f"aurum_aux_v13_{run_id}"
    os.environ["AURUM_VERIFICATION_ARTIFACT_ROOT"] = str(aux_temp_dir)
    orchestrator_pid = os.getpid()

    src_before = source_manifest()
    prior_before = prior_artifact_manifest_v17931213()
    v12_stage_a_baseline_s, v12_funnel_09_doc = _read_v12_stage_a_baseline_seconds_and_funnel()

    write_json(
        run_dir / "00_preflight_and_branch_identity.json",
        {
            "version": VERSION_V17931213,
            "schema_version": SCHEMA_VERSION_V17931213,
            "run_id": run_id,
            "branch": branch,
            "tested_executable_sha": sha,
            "observed_head_sha": sha,
            "grounded_v12_publication_head": GROUNDED_V12_PUBLICATION_HEAD,
            "grounded_v12_tested_executable_sha": GROUNDED_V12_TESTED_SHA,
            "dataset_sha256": CANONICAL_DATASET_SHA256_V17931213,
            "orchestrator_pid": orchestrator_pid,
            "activation_flags": {"LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF", "enabled_in_production": False},
            "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "RUNNING",
        },
    )
    append_stage_status(run_dir, "PREFLIGHT", "STARTED", tested_executable_sha=sha)

    callgraph_01 = build_v17931213_callgraph_and_defect_matrix(REPO_ROOT)
    callgraph_01["run_id"] = run_id
    callgraph_01["tested_executable_sha"] = sha
    write_json(run_dir / "01_function_callgraph_and_exact_source_defect_matrix.json", callgraph_01)
    append_stage_status(run_dir, "PREFLIGHT", "COMPLETED", source_sha256=src_before["sha256"])

    monitor = ResourceMonitorV17931213(
        minimum_ram_bytes=768 * 1024**2,
        soft_downshift_ram_bytes=2 * 1024**3,
        minimum_disk_bytes=10 * 1024**3,
    )
    monitor.abort_path = run_dir / "resource_abort.json"
    os.environ["AURUM_RESOURCE_GUARD_ABORT_FILE"] = str(monitor.abort_path)
    os.environ["AURUM_MINIMUM_RAM_BYTES"] = str(768 * 1024**2)
    os.environ["AURUM_TESTED_EXECUTABLE_SHA"] = sha

    worker_records: List[Dict[str, Any]] = []
    failure: Optional[Dict[str, Any]] = None
    readme_md = ""
    handover_txt = ""

    try:
        monitor.start()

        # STAGE A (Phase F & G): Bounded Async Multi-Worker Sharded Pytest & Fresh JUnit Bijection (12, 13)
        print("[Stage A] Running bounded async multi-worker pytest collection and sharded JUnit execution...", flush=True)
        t_a = time.time()
        append_stage_status(run_dir, "STAGE_A_ASYNC_PYTEST_SHARDS", "STARTED")
        collection, junit, shard_workers, async_pytest_stats = asyncio.run(
            _run_async_pytest_shards_impl_v17931213(
                run_dir / "junit",
                run_id=run_id,
                tested_executable_sha=sha,
                max_parallel_workers=3,
            )
        )
        worker_records.extend(shard_workers)
        pytest_w3_wall_s = float(async_pytest_stats["wall_clock_elapsed_seconds"])

        junit_bijection = verify_fresh_junit_bijection_v17931211(
            run_dir,
            collection,
            junit,
            expected_run_id=run_id,
            expected_tested_sha=sha,
        )
        write_json(
            run_dir / "12_current_pytest_fresh_collection_and_shard_map.json",
            {
                "version": VERSION_V17931213,
                "schema_version": SCHEMA_VERSION_V17931213,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "async_scheduler_telemetry": async_pytest_stats,
                "collection_manifest": collection,
            },
        )
        write_json(
            run_dir / "13_full_regression_junit_bijection_and_os_exit_verification.json",
            {
                "version": VERSION_V17931213,
                "schema_version": SCHEMA_VERSION_V17931213,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "shard_manifest": junit,
                "independent_bijection_verification": junit_bijection,
            },
        )
        append_stage_status(
            run_dir,
            "STAGE_A_ASYNC_PYTEST_SHARDS",
            "COMPLETED",
            elapsed_s=round(time.time() - t_a, 2),
            v12_w1_baseline_s=v12_stage_a_baseline_s,
            collected=junit_bijection["collected_count"],
            passed=junit_bijection["passed_count"],
            skipped=junit_bijection["skipped_count"],
            failed=junit_bijection["failed_count"],
        )
        gc.collect()
        monitor.check()

        # STAGE B (Phases A, B, C, D): SI8 Enrichment, 30-Partition Replay, Genuine Delayed Entry, Dynamic Funnel
        print("[Stage B] Enriching SI8 causal features, replaying 30 partitions, and evaluating D1..D4 delayed entries...", flush=True)
        t_b = time.time()
        append_stage_status(run_dir, "STAGE_B_DELAYED_ENTRY_AND_DYNAMIC_MATERIALIZATION", "STARTED")

        actual_dataset_sha = file_sha256(DATASET_PATH)
        datasets, _ = load_canonical_xauusdt_datasets()
        bars_1m = list(datasets["1m"])
        bars_1m_by_open_ts = {int(b.get("open_time") or b.get("timestamp") or 0): b for b in bars_1m}

        from services.v17_9_3_1_2_1_root_cause_evidence_service import (
            build_full_intervention_candidate_index_v1793121,
        )
        from services.v17_9_3_1_2_executable_intervention_replay_service import (
            build_close_ts_index_v179312,
            compute_research_policy_registry_hash_v179312,
        )

        chunks_dir = PRIOR_V179312_RUN_DIR / "decision_stream_chunks"
        all_decisions, _ = _load_verified_decision_stream_chunks(chunks_dir)
        a1_eligible = [
            r
            for r in all_decisions
            if r.get("variant") == "A1_V1792_SL_TP_BASELINE" and r.get("decision_status") == "ELIGIBLE"
        ]
        del all_decisions
        gc.collect()

        close_ts = build_close_ts_index_v179312(datasets)
        pol_hash = compute_research_policy_registry_hash_v179312()
        cand_index = build_full_intervention_candidate_index_v1793121(
            a1_eligible, datasets, pol_hash, close_ts_index=close_ts
        )

        v130_canonical_inputs = list(iter_jsonl_gz(V130_RUN / "10_canonical_decision_inputs.jsonl.gz"))
        baseline_decisions = [r for r in v130_canonical_inputs if not r.get("discovered_by_si8")]
        si8_decisions = [r for r in v130_canonical_inputs if r.get("discovered_by_si8")]
        si8_id_set = {str(r["opportunity_id"]) for r in si8_decisions}
        si8_by_idx: Dict[int, List[Dict[str, Any]]] = {}
        for rec in si8_decisions:
            si8_by_idx.setdefault(int(rec["checkpoint_index_1m"]), []).append(rec)

        from services.v17_9_3_1_2_7_causal_signal_truth import CompressedFeatureLedger

        feat_by_opp_id = CompressedFeatureLedger()
        si8_feat_by_opp_id = CompressedFeatureLedger()
        for idx_f, f_row in enumerate(iter_jsonl_gz(V130_RUN / "07_verified_causal_features.jsonl.gz")):
            if not verify_feature_snapshot_v1793127(f_row):
                raise RuntimeError(f"INVALID_FEATURE_SNAPSHOT_PROOF:{f_row.get('opportunity_id')}")
            oid = str(f_row["opportunity_id"])
            if oid in si8_id_set or f_row.get("discovered_by_si8"):
                si8_feat_by_opp_id[oid] = f_row
            else:
                feat_by_opp_id[oid] = f_row
            if idx_f > 0 and idx_f % 8000 == 0:
                gc.collect()
        gc.collect()

        # Bug B-1 repair: enrich SI8 aligned candidates with exact detector sweep/break/fvg/breaker IDs
        si8_enrich_summary = enrich_si8_aligned_decisions_and_features_v17931213(
            datasets,
            close_ts,
            si8_decisions,
            si8_feat_by_opp_id,
            fallback_feat_by_opp_id=feat_by_opp_id,
            dataset_sha256=actual_dataset_sha,
        )
        for oid in si8_feat_by_opp_id:
            if not verify_feature_snapshot_v1793127(si8_feat_by_opp_id[oid]):
                raise RuntimeError(f"INVALID_ENRICHED_SI8_FEATURE_SNAPSHOT_PROOF:{oid}")
        gc.collect()

        cand_index["si8_discovered_decisions_by_checkpoint_index"] = si8_by_idx
        cand_index["si8_discovered_feature_ledger_by_opp_id"] = si8_feat_by_opp_id
        cand_index["v1793127_causal_truth_required"] = True
        all_features_map = ChainMap(si8_feat_by_opp_id, feat_by_opp_id)

        # Replay all 10 policies (SI0..SI9) across all 3 fill modes
        si_results_by_policy: Dict[str, Dict[str, Any]] = {}
        for pid in CANONICAL_V1793123_POLICY_IDS:
            si_results_by_policy[pid] = replay_single_si_policy_v1793122(
                pid,
                cand_index,
                datasets,
                all_features_map,
                fill_modes=FILL_MODES,
            )
        monitor.check()

        # Raw exit-event lineage & source reconciliation across all 30 partitions (10)
        all_lineage_events: List[Dict[str, Any]] = []
        all_source_execution_rows: List[Dict[str, Any]] = []
        conservation_by_policy_mode: Dict[str, Dict[str, Any]] = {}

        for pid, pol_res in si_results_by_policy.items():
            conservation_by_policy_mode[pid] = {}
            for fm, eps in pol_res["all_mode_episodes"].items():
                for ep in eps:
                    led_ep = ep.get("execution_ledger") if isinstance(ep.get("execution_ledger"), Mapping) else {}
                    all_source_execution_rows.append(
                        {
                            "run_id": run_id,
                            "tested_executable_sha": sha,
                            "dataset_sha256": actual_dataset_sha,
                            "policy_id": pid,
                            "fill_mode": fm,
                            "opportunity_id": str(ep.get("opportunity_id") or ""),
                            "episode_key": str(ep.get("episode_key") or ""),
                            "setup_episode_id": str(ep.get("setup_episode_id") or ""),
                            "model_id": str(ep.get("model_id") or ""),
                            "direction": str(ep.get("direction") or ""),
                            "decision_timestamp_ms": int(ep.get("decision_timestamp_ms") or led_ep.get("decision_ts") or 0),
                            "bars_held": int(ep.get("bars_held") or led_ep.get("bars_held") or 0),
                            "filled": bool(ep.get("filled", led_ep.get("filled", False))),
                            "outcome_label": str(ep.get("outcome_label") or led_ep.get("outcome_label") or ""),
                            "exit_type": str(ep.get("exit_type") or led_ep.get("exit_type") or ""),
                            "entry_fill_ts": int(ep.get("entry_fill_ts") or led_ep.get("entry_fill_ts") or 0),
                            "trigger_bar_ts": int(led_ep.get("trigger_bar_ts") or 0),
                            "exit_fill_ts": int(ep.get("exit_fill_ts") or led_ep.get("exit_fill_ts") or ep.get("release_ts_ms") or 0),
                            "entry_reference": float(ep.get("entry_reference") or led_ep.get("entry_reference") or 0.0),
                            "entry_fill_price": float(ep.get("entry_fill_price") or led_ep.get("entry_fill_price") or 0.0),
                            "selected_sl": float(ep.get("selected_sl") or led_ep.get("planned_sl") or 0.0),
                            "take_profit": float(ep.get("take_profit") or led_ep.get("planned_tp") or 0.0),
                            "exit_fill_price": float(ep.get("exit_fill_price") or led_ep.get("exit_fill_price") or 0.0),
                            "executed_quantity": float(ep.get("executed_quantity") or led_ep.get("quantity") or ep.get("hypothetical_quantity") or 0.0),
                            "planned_net_risk_usd": float(ep.get("planned_net_risk_usd") or led_ep.get("planned_net_risk_usd") or 0.0),
                            "entry_fee_usd": round(float(ep.get("entry_fee_usd") or led_ep.get("entry_fee") or 0.0), 4),
                            "exit_fee_usd": round(float(ep.get("exit_fee_usd") or led_ep.get("exit_fee") or 0.0), 4),
                            "realized_gross_pnl_usd": round(float(ep.get("realized_gross_pnl_usd") or led_ep.get("realized_gross_pnl_usd") or 0.0), 4),
                            "realized_net_pnl_usd": round(float(ep.get("realized_net_pnl_usd") or led_ep.get("realized_net_pnl_usd") or 0.0), 4),
                            "realized_r": round(float(ep.get("realized_r") or led_ep.get("realized_r_net") or 0.0), 4),
                        }
                    )
                lin_bundle = build_exit_event_lineage_v17931211(
                    eps,
                    bars_1m_by_open_ts=bars_1m_by_open_ts,
                    run_id=run_id,
                    tested_executable_sha=sha,
                    dataset_sha256=actual_dataset_sha,
                    policy_id=pid,
                    fill_mode=fm,
                )
                all_lineage_events.extend(lin_bundle["all_events"])
                c_v = verify_exit_portfolio_conservation_v17931211(lin_bundle)
                conservation_by_policy_mode[pid][fm] = {
                    **c_v,
                    "mode_summary": {
                        k: v for k, v in pol_res["replays_by_fill_mode"][fm].items() if k != "episodes"
                    },
                }

        write_jsonl_gz(run_dir / "09_exit_event_lineage_ledger.jsonl.gz", all_lineage_events)
        write_jsonl_gz(
            run_dir / "09_original_execution_ledger_source_reconciliation.jsonl.gz",
            all_source_execution_rows,
        )

        all_part_ind_check = verify_all_policy_fill_mode_lineage_independently_v17931211(
            run_dir,
            bars_1m_by_open_ts=bars_1m_by_open_ts,
            expected_run_id=run_id,
            expected_tested_sha=sha,
            expected_dataset_sha=actual_dataset_sha,
            require_source_reconciliation=True,
        )
        write_json(
            run_dir / "10_full_30_partition_lineage_parity.json",
            {
                "version": VERSION_V17931213,
                "schema_version": SCHEMA_VERSION_V17931213,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "dataset_sha256": actual_dataset_sha,
                "conservation_by_policy_and_fill_mode": conservation_by_policy_mode,
                "independent_raw_stream_verification": {
                    k: v
                    for k, v in all_part_ind_check.items()
                    if k not in ("reconstructed_partitions", "reconstructed_si0_primary_bundle")
                },
            },
        )

        # Phase A (P0-E1): Delayed entry contract, ledgers, structural SL/TP sizing, matched AB & 1-pos portfolio (02, 03, 07, 08, 09)
        si0_nb_eps = si_results_by_policy["SI0_OFF_BASELINE"]["primary_episodes"]
        delayed_bundle = build_delayed_entry_contract_and_ledgers_v17931213(
            si0_nb_episodes=si0_nb_eps,
            feature_ledger_by_opp_id=all_features_map,
            datasets=datasets,
            si_results_by_policy=si_results_by_policy,
            dataset_sha256=actual_dataset_sha,
        )
        write_json(run_dir / "02_a1_old_filter_vs_true_delayed_entry_contract.json", delayed_bundle["contract_02"])
        write_jsonl_gz(
            run_dir / "03_delay_confirmation_new_timestamp_entry_sl_tp_ledger.jsonl.gz",
            delayed_bundle["delay_ledger_03"],
        )
        write_jsonl_gz(
            run_dir / "07_structural_sl_tp_position_sizing_evidence.jsonl.gz",
            delayed_bundle["sl_tp_sizing_07"],
        )
        write_json(run_dir / "08_matched_ab_all_opportunities_all_fill_modes.json", delayed_bundle["matched_ab_08"])
        write_json(run_dir / "09_chronological_portfolio_replay_all_variants.json", delayed_bundle["portfolio_09"])

        delayed_check = verify_delayed_entry_and_structural_sl_tp_v17931213(
            delayed_bundle["contract_02"],
            delayed_bundle["summary_by_variant_and_mode"],
            delayed_bundle["delay_ledger_03"],
            delayed_bundle["portfolio_09"],
        )

        # Phase B (P0-E2 / P0-E3): HTF / Unicorn / SI8 / SI9 dynamic funnel, 267/113 audits & materialized ablations (04, 05)
        funnel_bundle = build_htf_unicorn_and_si8_si9_dynamic_funnel_v17931213(
            datasets=datasets,
            close_ts_index=close_ts,
            candidate_index=cand_index,
            canonical_decisions=baseline_decisions,
            si8_decisions=si8_decisions,
            feature_ledger_by_opp_id=all_features_map,
            si8_policy_result=si_results_by_policy["SI8_RESEARCH_DISCOVERY_REPAIRED"],
            si9_policy_result=si_results_by_policy["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"],
            v12_funnel_09_doc=v12_funnel_09_doc,
            dataset_sha256=actual_dataset_sha,
        )
        funnel_bundle["funnel_audit_05"]["si8_feature_enrichment_summary"] = si8_enrich_summary
        # Persist raw SI8 decision ledger compact projection inside 05 so independent separate-process verifier
        # can re-verify raw_si8_decision_ledger vs summary bijection directly from disk!
        funnel_bundle["funnel_audit_05"]["raw_si8_eligible_and_materialized_ledger"] = [
            {
                "opportunity_id": str(r.get("opportunity_id")),
                "accepted": bool(r.get("accepted", False)),
                "rejection_reason": r.get("rejection_reason"),
                "entered_materialization_stage": bool(r.get("entered_materialization_stage", False)),
                "intent_id": r.get("intent_id"),
                "new_decision_timestamp_ms": r.get("new_decision_timestamp_ms"),
            }
            for r in si_results_by_policy["SI8_RESEARCH_DISCOVERY_REPAIRED"]["policy_decision_ledger"]
            if not str(r.get("rejection_reason") or "").startswith("REJECTED_UNPROVEN:")
            or bool(r.get("entered_materialization_stage", False))
        ]
        write_jsonl_gz(
            run_dir / "04_htf_unicorn_detector_to_intent_causal_pipeline.jsonl.gz",
            funnel_bundle["htf_pipeline_rows_04"],
        )
        write_json(
            run_dir / "05_si8_si9_dynamic_full_funnel_and_target_267_audit.json",
            funnel_bundle["funnel_audit_05"],
        )
        htf_funnel_check = verify_htf_unicorn_and_si8_si9_funnel_v17931213(
            funnel_bundle["funnel_audit_05"],
            raw_si8_decision_ledger=si_results_by_policy["SI8_RESEARCH_DISCOVERY_REPAIRED"]["policy_decision_ledger"],
            expect_267_historical_audit=v12_funnel_09_doc is not None,
        )

        # Phase C (R1): Pre-Entry Observable Loss Causes vs Ex-Post Diagnostic Stop Behavior (06)
        casebook_15 = analyze_15_distinct_recovered_trades_v17931210(si_results_by_policy, all_features_map)
        r1_bundle_v12 = build_r1_trade_by_trade_casebook_and_matrix_v17931212(
            si0_by_fill_mode=si_results_by_policy["SI0_OFF_BASELINE"]["all_mode_episodes"],
            feature_ledger_by_opp_id=all_features_map,
            bars_1m=bars_1m,
            casebook_15=casebook_15,
            dataset_sha256=actual_dataset_sha,
        )
        forensics_06 = build_pre_entry_forensics_and_d1_d4_transitions_v17931213(
            r1_bundle_v12=r1_bundle_v12,
            rows_by_variant_nb=delayed_bundle["rows_by_variant_nb"],
        )
        write_jsonl_gz(
            run_dir / "06_pre_entry_loss_forensics_and_expost_stop_separation.jsonl.gz",
            forensics_06["forensic_rows_06"],
        )
        r1_check = verify_r1_forensic_separation_and_conservation_v17931212(
            forensics_06["forensic_rows_06"], forensics_06["matrix_05"]
        )

        # OOS Provenance & Reused Holdout Truth (15)
        r4_oos_bundle = build_r4_backtest_stress_and_oos_provenance_v17931212(
            all_partitions_lineage=all_part_ind_check,
            r3_summary=delayed_bundle["summary_by_variant_and_mode"],
            bars_1m=bars_1m,
            si0_nb_episodes=si0_nb_eps,
            dataset_sha256=actual_dataset_sha,
        )
        oos_15 = {
            **r4_oos_bundle["oos_18"],
            "version": VERSION_V17931213,
            "schema_version": SCHEMA_VERSION_V17931213,
            "r1_matrix_summary": forensics_06["matrix_05"],
            "r1_stop_audit_summary": forensics_06["stop_audit_06"],
            "d4_transitions_on_157_baseline_losses": forensics_06["d4_transitions_on_157_baseline_losses"],
        }
        write_json(run_dir / "15_oos_provenance_and_reused_holdout_truth.json", oos_15)
        oos_check = verify_r4_oos_and_activation_verdict_v17931212(oos_15)

        append_stage_status(
            run_dir,
            "STAGE_B_DELAYED_ENTRY_AND_DYNAMIC_MATERIALIZATION",
            "COMPLETED",
            elapsed_s=round(time.time() - t_b, 2),
            si8_materialized=si_results_by_policy["SI8_RESEARCH_DISCOVERY_REPAIRED"]["materialized_intents_count"],
            si9_materialized=si_results_by_policy["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"]["materialized_intents_count"],
        )
        gc.collect()
        monitor.check()

        # STAGE C (Phase E, F, H): Worker 1/2/4/Repeat Determinism, Single Authoritative Report & V01..V66 Negative Controls (11, 14, 16)
        print("[Stage C] Running w=1/2/4/1_repeat determinism, rendering authoritative report, and executing V01..V66 negative controls...", flush=True)
        t_c = time.time()
        append_stage_status(run_dir, "STAGE_C_REPORT_TRUTH_AND_V01_V66_CONTROLS", "STARTED")

        doc_parity, det_worker_records = run_worker_1_2_4_and_repeat_determinism_v17931211(
            si0_nb_eps,
            bars_1m,
            run_id=run_id,
            tested_sha=sha,
        )
        worker_records.extend(det_worker_records)

        # Base .11 metrics fixture strictly for backward-compatible V19..V22 negative controls
        base_v11_metrics = build_canonical_report_metrics_v17931211(
            run_id=run_id,
            tested_sha=sha,
            dataset_sha=actual_dataset_sha,
            junit_bijection=junit_bijection,
            shard_count=junit["shard_count"],
            all_partitions_lineage=all_part_ind_check,
            casebook_15=casebook_15,
            worker_parity=doc_parity,
            negative_controls_count=66,
        )
        base_v11_readme, base_v11_handover = render_truth_closure_readme_and_handover_v17931211(base_v11_metrics)

        # Build single authoritative .13 metrics object (with 66/66 negative controls target, verified immediately below)
        prelim_max_children = max((len(s["worker_pids"]) for s in monitor.samples), default=1)
        auth_metrics = build_authoritative_metrics_v17931213(
            run_id=run_id,
            branch=branch,
            tested_sha=sha,
            dataset_sha=actual_dataset_sha,
            junit_bijection=junit_bijection,
            shards_count=junit["shard_count"],
            pytest_w3_wall_s=pytest_w3_wall_s,
            pytest_v12_w1_s=v12_stage_a_baseline_s,
            max_children_obs=prelim_max_children,
            orphan_pids_cnt=0,
            worker_parity_status=str(doc_parity["status"]),
            funnel_audit_05=funnel_bundle["funnel_audit_05"],
            r1_matrix_05=forensics_06["matrix_05"],
            all_partitions_lineage=all_part_ind_check,
            summary_by_variant_and_mode=delayed_bundle["summary_by_variant_and_mode"],
            portfolio_by_variant_and_mode=delayed_bundle["portfolio_by_variant_and_mode"],
            neg_controls_doc={"controls_count": 66, "controls": [{"rejected": True}] * 66},
            prior_immutable=(prior_before["sha256"] == prior_artifact_manifest_v17931213()["sha256"]),
            delayed_entry_check_status=delayed_check["status"],
            htf_funnel_check_status=htf_funnel_check["status"],
        )
        readme_md, handover_txt = render_v17931213_readme_and_handover(auth_metrics)

        prelim_gates = evaluate_v17931213_gate_matrix(
            auth_metrics,
            delayed_entry_check=delayed_check,
            htf_funnel_check=htf_funnel_check,
            r1_check=r1_check,
            junit_check=junit_bijection,
            lineage_30_check=all_part_ind_check,
            async_proc_check={"status": "PASS"},
            report_consistency_check={"status": "PASS"},
            oos_check=oos_check,
            neg_v66_check={"status": "PASS"},
            manifest_check={"status": "PASS"},
            freeze_check={"status": "PASS"},
        )

        neg_v66 = run_negative_controls_v01_to_v66_v17931213(
            reconstructed_partitions=all_part_ind_check["reconstructed_partitions"],
            bars_1m_by_open_ts=bars_1m_by_open_ts,
            base_v11_report_metrics=base_v11_metrics,
            base_v11_readme_text=base_v11_readme,
            base_v11_handover_text=base_v11_handover,
            authoritative_metrics_v13=auth_metrics,
            readme_v13_text=readme_md,
            handover_v13_text=handover_txt,
            gate_matrix_18=prelim_gates,
            run_id=run_id,
            tested_sha=sha,
        )
        write_json(run_dir / "14_negative_controls_V01_to_latest_fault_injection.json", neg_v66)

        append_stage_status(
            run_dir,
            "STAGE_C_REPORT_TRUTH_AND_V01_V66_CONTROLS",
            "COMPLETED",
            elapsed_s=round(time.time() - t_c, 2),
            v01_to_v66_status=neg_v66["status"],
            controls_rejected=neg_v66["rejected_count"],
        )
        monitor.check()

    except Exception as exc:
        import traceback

        failure = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        append_stage_status(run_dir, "VERIFICATION_EXCEPTION", "FAILED", error=str(exc))
    finally:
        shutil.rmtree(aux_temp_dir, ignore_errors=True)
        res_metrics = monitor.stop()
        src_after = source_manifest()
        prior_after = prior_artifact_manifest_v17931213()

        freeze_check = verify_source_freeze_v17931211(
            expected_tested_sha=sha,
            observed_head_sha=git("rev-parse", "HEAD"),
            observed_branch=git("branch", "--show-current"),
            working_tree_dirty_files=git(
                "diff", "--name-only", "HEAD", "--", "aurum-desk/backend", "aurum-desk/tools"
            ).splitlines(),
            source_manifest_before=src_before["files"],
            source_manifest_after=src_after["files"],
            prior_artifact_hashes_before=prior_before["files"],
            prior_artifact_hashes_after=prior_after["files"],
        )

        write_json(
            run_dir / "19_source_freeze_and_immutable_prior_artifact_hashes.json",
            {
                "version": VERSION_V17931213,
                "schema_version": SCHEMA_VERSION_V17931213,
                "run_id": run_id,
                "branch": git("branch", "--show-current"),
                "tested_executable_sha": sha,
                "observed_head_sha": git("rev-parse", "HEAD"),
                "working_tree_dirty_files": git(
                    "diff", "--name-only", "HEAD", "--", "aurum-desk/backend", "aurum-desk/tools"
                ).splitlines(),
                "source_manifest_sha256_before": src_before["sha256"],
                "source_manifest_sha256_after": src_after["sha256"],
                "source_manifest_before": src_before["files"],
                "source_manifest_after": src_after["files"],
                "prior_artifact_sha256_before": prior_before["sha256"],
                "prior_artifact_sha256_after": prior_after["sha256"],
                "prior_artifact_files_count": prior_before["files_count"],
                "prior_artifact_hashes_before": prior_before["files"],
                "prior_artifact_hashes_after": prior_after["files"],
                "verification": freeze_check,
                "status": freeze_check["status"],
            },
        )

        resource_telemetry = {
            "max_children_observed": int(res_metrics.get("max_children_observed") or 1),
            "minimum_available_ram_bytes": int(res_metrics.get("minimum_available_ram_bytes") or 0),
            "minimum_free_disk_bytes": int(res_metrics.get("minimum_free_disk_bytes") or 0),
            "peak_total_rss_bytes": int(res_metrics.get("peak_total_rss_bytes") or 0),
            "soft_downshift_events": int(res_metrics.get("soft_downshift_events") or 0),
            "sampling_interval_seconds": 0.5,
            "orphan_pids_observed_at_cleanup": list(res_metrics.get("orphan_pids_observed_at_cleanup") or []),
            "sample_count": len(res_metrics.get("samples") or []),
            "error": res_metrics.get("error"),
        }
        proc_verify = verify_bounded_async_workers_v17931213(
            worker_records,
            expected_run_id=run_id,
            expected_tested_sha=sha,
            max_allowed_workers=4,
            active_orphan_pids=resource_telemetry["orphan_pids_observed_at_cleanup"],
        )

        write_json(
            run_dir / "20_process_tree_cleanup_and_disk_ram_evidence.json",
            {
                "version": VERSION_V17931213,
                "schema_version": SCHEMA_VERSION_V17931213,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "orchestrator_pid": orchestrator_pid,
                "worker_records_count": len(worker_records),
                "worker_records": worker_records,
                "orphan_pids_at_cleanup": resource_telemetry["orphan_pids_observed_at_cleanup"],
                "resource_telemetry": resource_telemetry,
                "process_verification": proc_verify,
                "status": "PASS" if proc_verify["status"] == "PASS" and not resource_telemetry["error"] else "FAIL",
            },
        )

        write_json(
            run_dir / "21_phase_a_b_c_commit_push_and_remote_readback.json",
            {
                "version": VERSION_V17931213,
                "schema_version": SCHEMA_VERSION_V17931213,
                "run_id": run_id,
                "branch": MANDATORY_BRANCH_V17931213,
                "phase_a_tested_executable_sha": sha,
                "pre_commit_status": "READY_FOR_PHASE_B_EVIDENCE_COMMIT",
                "in_dir_phase_c_status": "BLOCKED_PRE_PUSH_FINALIZED_IN_EXTERNAL_PHASE_C_RECEIPT",
                "external_phase_c_receipt_path": f"aurum-desk/docs/artifacts/v17_9_3_1_2_13/publication_{run_id}.json",
                "latest_run_pointer_path": "aurum-desk/docs/artifacts/v17_9_3_1_2_13/latest_run.json",
            },
        )

        if not failure:
            # Finalize authoritative metrics with exact post-run `max_children_observed` & `orphan_pids`
            final_auth_metrics = build_authoritative_metrics_v17931213(
                run_id=run_id,
                branch=branch,
                tested_sha=sha,
                dataset_sha=actual_dataset_sha,
                junit_bijection=junit_bijection,
                shards_count=junit["shard_count"],
                pytest_w3_wall_s=pytest_w3_wall_s,
                pytest_v12_w1_s=v12_stage_a_baseline_s,
                max_children_obs=resource_telemetry["max_children_observed"],
                orphan_pids_cnt=len(resource_telemetry["orphan_pids_observed_at_cleanup"]),
                worker_parity_status=str(doc_parity["status"]),
                funnel_audit_05=funnel_bundle["funnel_audit_05"],
                r1_matrix_05=forensics_06["matrix_05"],
                all_partitions_lineage=all_part_ind_check,
                summary_by_variant_and_mode=delayed_bundle["summary_by_variant_and_mode"],
                portfolio_by_variant_and_mode=delayed_bundle["portfolio_by_variant_and_mode"],
                neg_controls_doc=neg_v66,
                prior_immutable=(prior_before["sha256"] == prior_after["sha256"]),
                delayed_entry_check_status=delayed_check["status"],
                htf_funnel_check_status=htf_funnel_check["status"],
            )
            readme_md, handover_txt = render_v17931213_readme_and_handover(final_auth_metrics)

            write_json(
                run_dir / "11_real_async_worker_resource_and_determinism_benchmarks.json",
                {
                    "version": VERSION_V17931213,
                    "schema_version": SCHEMA_VERSION_V17931213,
                    "run_id": run_id,
                    "tested_executable_sha": sha,
                    "async_sharded_pytest_w3_vs_v12_w1_benchmark": {
                        "v12_sequential_w1_seconds": v12_stage_a_baseline_s,
                        "v13_async_w3_wall_seconds": pytest_w3_wall_s,
                        "speedup_ratio": final_auth_metrics.pytest_async_speedup_ratio,
                        **async_pytest_stats,
                    },
                    "worker_1_2_4_and_repeat_determinism": doc_parity,
                    "resource_telemetry_summary": resource_telemetry,
                    "status": "PASS"
                    if doc_parity["status"] == "PASS"
                    and proc_verify["status"] == "PASS"
                    and not resource_telemetry["error"]
                    else "FAIL",
                },
            )

            from dataclasses import asdict

            write_json(
                run_dir / "16_truthful_economic_verdict_and_blockers.json",
                {
                    "version": VERSION_V17931213,
                    "schema_version": SCHEMA_VERSION_V17931213,
                    "run_id": run_id,
                    "tested_executable_sha": sha,
                    "dataset_sha256": actual_dataset_sha,
                    "FRESH_OOS_VERDICT": final_auth_metrics.fresh_oos_verdict,
                    "ECONOMIC_VERDICT": final_auth_metrics.economic_verdict,
                    "STRATEGY_EXPECTANCY_VERDICT": final_auth_metrics.strategy_expectancy_verdict,
                    "ACTIVATION_READY": final_auth_metrics.activation_ready,
                    "base_v11_compat_report_metrics": base_v11_metrics,
                    "authoritative_metrics_v17931213": asdict(final_auth_metrics),
                },
            )

            (run_dir / "README_V17_9_3_1_2_13.md").write_text(readme_md, encoding="utf-8", newline="\n")
            (run_dir / "HANDOVER_V17_9_3_1_2_13.txt").write_text(handover_txt, encoding="utf-8", newline="\n")
            (run_dir / ".gitattributes").write_bytes(b"* -text\n")

            # STAGE D: Separate-process independent verification
            print("[Stage D] Running separate-process independent verifier...", flush=True)
            append_stage_status(run_dir, "STAGE_D_INDEPENDENT_SEPARATE_PROCESS_VERIFIER", "STARTED")
            verifier_script = APP_ROOT / "tools/v17_9_3_1_2_13_independent_verifier.py"
            ver_proc = subprocess.run(
                [sys.executable, str(verifier_script), str(run_dir)],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            if ver_proc.returncode != 0:
                raise RuntimeError(
                    f"SEPARATE_PROCESS_INDEPENDENT_VERIFIER_FAILED:rc={ver_proc.returncode},stdout={ver_proc.stdout},stderr={ver_proc.stderr}"
                )
            ver_summary = json.loads(ver_proc.stdout.strip().splitlines()[-1])
            print(json.dumps(ver_summary, indent=2, sort_keys=True), flush=True)
            return 0

    if failure:
        print(json.dumps(failure, indent=2), file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
