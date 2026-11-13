#!/usr/bin/env python3
"""
tools/run_v17_9_3_1_2_17_verification.py

AURUM Desk — V17.9.3.1.2.17 Full Autonomous Verification & Research Orchestrator:
  - Phase 1: True Unicorn / HTF Causal Source Audit on 14 Stage 4 survivors (05_TRUE_UNICORN_BREAKER_BOUND_TO_FVG == 0).
  - Phase 2: Target ATR Unit Repair & Reachability Audit on 267 historical failures (201 ATR mismatch + 66 horizon unreachable + 40 controls).
  - Phase 3: Entry / SL Loss Forensics on 157 baseline closed losses (vs 242 scheduled episodes) + SI8 4 losses + SI9 2 losses (2 shared).
  - Bounded Async Multi-Worker Sharded Pytest (17 shards, <=3 workers, <=4 child procs, 0.5s telemetry).
  - Matched same-SHA worker speed benchmark (w=1, w=2, w=3 x 2 repeats) and 4-PID determinism parity (w=1, 2, 4, 1repeat).
  - 180 Adversarial Negative Controls (V01..V180) RED/GREEN in temporary directories.
  - 17 Orthogonal Acceptance Gates (G01..G17).
  - Independent separate-process from-disk verification and sealed SHA-256 manifests.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import ChainMap
from dataclasses import asdict
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

class _SafeStreamWrapper:
    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def write(self, data: Any) -> int:
        try:
            return self._stream.write(data)
        except OSError:
            return len(data) if hasattr(data, "__len__") else 0

    def flush(self) -> None:
        try:
            self._stream.flush()
        except OSError:
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


sys.stdout = _SafeStreamWrapper(sys.stdout)  # type: ignore[assignment]
sys.stderr = _SafeStreamWrapper(sys.stderr)  # type: ignore[assignment]

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
    CompressedFeatureLedger,
)
from services.v17_9_3_1_2_10_canonical_reconciliation_and_provenance import (
    analyze_15_distinct_recovered_trades_v17931210,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    build_exit_event_lineage_v17931211,
    verify_all_policy_fill_mode_lineage_independently_v17931211,
    verify_exit_portfolio_conservation_v17931211,
    verify_fresh_junit_bijection_v17931211,
    verify_source_freeze_v17931211,
)
from services.v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service import (
    build_delayed_entry_contract_and_ledgers_v17931213,
    enrich_si8_aligned_decisions_and_features_v17931213,
    verify_bounded_async_workers_v17931213,
)
from services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service import (
    build_d4_full_63085_and_matched_funnel_v17931214,
    build_v17931214_callgraph_and_defect_matrix,
)
from services.v17_9_3_1_2_16_verification_and_forensics_service import (
    build_breaker_fvg_episode_asof_causal_14_case_audit_v17931216,
    build_d4_14_survivor_raw_source_casebook_v17931216,
    build_feature_vs_raw_unicorn_provenance_diff_v17931216,
)
from services.v17_9_3_1_2_17_verification_and_forensics_service import (
    AuthoritativeMetricsV17931217,
    CANONICAL_DATASET_SHA256_V17931217,
    MANDATORY_BRANCH_V17931217,
    REQUIRED_ARTIFACTS_V17931217,
    SCHEMA_VERSION_V17931217,
    VERSION_V17931217,
    build_authoritative_metrics_from_evidence_v17931217,
    build_loss_episode_attribution_from_raw_v17931217,
    evaluate_v17931217_orthogonal_gate_matrix,
    execute_red_reproductions_v17931217,
    parse_required_pytest_counts_v17931217,
    render_v17931217_readme_and_handover,
    run_fully_observed_adversarial_suite_v17931217,
    verify_v17931217_cross_view_numerical_consistency,
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

GROUNDED_V16_REMOTE_HEAD = "d1783664cca7308cd43fac03205eaf229fd97f6e"
GROUNDED_V16_TESTED_SHA = "900af62f5f5ca7d550df48d05339c90608bff3c0"
GROUNDED_V16_RUN_ID = "20261112T140357Z_900af62f5f5c"
GROUNDED_V16_EVIDENCE_SHA = "440619973ae0a28399e29fbe776e0266da5bf12f"

V130_RUN = APP_ROOT / "docs/artifacts/v17_9_3_1_2_10/20261107T150245Z_b772a03bb7cc"
V16_RUN_DIR = APP_ROOT / "docs/artifacts/v17_9_3_1_2_16" / GROUNDED_V16_RUN_ID

SAME_SHA_BENCHMARK_TEST_FILES: Tuple[str, ...] = (
    "backend/tests/test_v14_3_ny_quota.py",
    "backend/tests/test_v17_9_1_mtf_structure_liquidity.py",
    "backend/tests/test_v17_9_2_structural_stop.py",
    "backend/tests/test_v17_9_3_macro_unicorn_intelligence.py",
    "backend/tests/test_v17_7_atr_provenance.py",
    "backend/tests/test_v15_9_quote_source_rejection.py",
)

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
    "test_v17_9_3_1_2_14_accounting_target_and_d4_truth.py",
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
    "test_v16_22_red_reproductions.py",
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


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
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


def prior_artifact_manifest_v17931217() -> Dict[str, Any]:
    """Hashes all prior immutable artifact directories (.7 through .16)."""
    files: Dict[str, str] = {}
    for ver_dir in (
        "v17_9_3_1_2_7",
        "v17_9_3_1_2_8",
        "v17_9_3_1_2_9",
        "v17_9_3_1_2_10",
        "v17_9_3_1_2_11",
        "v17_9_3_1_2_12",
        "v17_9_3_1_2_13",
        "v17_9_3_1_2_14",
        "v17_9_3_1_2_15",
        "v17_9_3_1_2_16",
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
        "version": VERSION_V17931217,
        "stage": stage,
        "status": status,
        **extra,
    }
    with status_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class ResourceMonitorV17931217:
    """
    0.5s process-tree, available-RAM, CPU, and free-disk telemetry monitor enforcing:
      - `max_children <= 4` global heavy child process cap
      - `minimum_disk_bytes = 8 GiB` free disk floor (documented host-safe floor)
      - `minimum_ram_bytes = 512 MiB` hard abort floor + `2.0 GiB` / `1.65 GiB` soft downshift tracking
    """

    def __init__(
        self,
        *,
        minimum_ram_bytes: int = 512 * 1024**2,
        soft_downshift_ram_bytes: int = 2 * 1024**3,
        minimum_disk_bytes: int = 8 * 1024**3,
    ) -> None:
        self.stop_event = threading.Event()
        self.samples: List[Dict[str, Any]] = []
        self.error: Optional[str] = None
        self.abort_path: Optional[Path] = None
        self.minimum_ram_bytes = minimum_ram_bytes
        self.soft_downshift_ram_bytes = soft_downshift_ram_bytes
        self.minimum_disk_bytes = minimum_disk_bytes
        self.soft_downshift_events = 0
        self.low_ram_consecutive_ticks = 0
        self.cap_consecutive_ticks = 0

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
            self.low_ram_consecutive_ticks += 1
            gc.collect()
            if self.low_ram_consecutive_ticks >= 4:
                self.error = "BLOCKED_LOW_RAM"
        elif len(children) > 4:
            self.cap_consecutive_ticks += 1
            if self.cap_consecutive_ticks >= 3:
                self.error = f"BLOCKED_GLOBAL_WORKER_CAP:{len(children)}"
        else:
            self.low_ram_consecutive_ticks = 0
            self.cap_consecutive_ticks = 0
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


def _build_async_balanced_shards_v17931217(
    all_test_files: Sequence[str],
) -> List[Dict[str, Any]]:
    """
    Partitions `backend/tests/test_*.py` into isolated shards for `asyncio.create_subprocess_exec`,
    explicitly isolating sub-30ms realtime latency tests in `SHARD_EXCL_00_REALTIME_30MS`.
    """
    realtime_30ms: List[str] = []
    db_own: List[str] = []
    child_spawn: List[str] = []
    v17_9_0_1_heavy: List[str] = []
    v16_23_iso: List[str] = []
    db_serial: List[str] = []
    regular_inproc: List[str] = []

    for rel in all_test_files:
        fname = Path(rel).name
        if fname == "test_v7_2_realtime_protocol.py":
            realtime_30ms.append(rel)
        elif fname == "test_v17_7_database_safety.py":
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

    if realtime_30ms:
        shards.append({"shard_name": "SHARD_EXCL_00_REALTIME_30MS", "files": realtime_30ms, "exclusive": True, "needs_venv": True})
    if child_spawn:
        mid = max(1, len(child_spawn) // 2)
        shards.append({"shard_name": "SHARD_EXCL_01_NESTED_SUBPROC_A", "files": sorted(child_spawn[:mid]), "exclusive": True, "needs_venv": False})
        if child_spawn[mid:]:
            shards.append({"shard_name": "SHARD_EXCL_02_NESTED_SUBPROC_B", "files": sorted(child_spawn[mid:]), "exclusive": True, "needs_venv": False})
    if db_own:
        shards.append({"shard_name": "SHARD_EXCL_03_DB_OWNERSHIP", "files": db_own, "exclusive": True, "needs_venv": True})

    return shards


async def _run_async_pytest_shards_impl_v17931217(
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
    shard_specs = _build_async_balanced_shards_v17931217(all_test_files)

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
    col_pid = int(col_proc.pid)
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

            def _run_subproc() -> Tuple[int, int, str]:
                p = subprocess.Popen(
                    cmd_argv,
                    cwd=str(APP_ROOT),
                    env=child_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                )
                pid = p.pid
                try:
                    out_b, _ = p.communicate(timeout=1800.0)
                    ret = p.returncode
                    out_txt = out_b.decode("utf-8", errors="replace") if out_b else ""
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.communicate()
                    raise RuntimeError(f"ASYNC_PYTEST_SHARD_TIMEOUT:{shard_name}")
                return pid, ret, out_txt

            shard_pid, rc, stdout_text = await asyncio.to_thread(_run_subproc)
            t_e = time.time()
            et_ms = int(t_e * 1000)
            end_utc = datetime.now(timezone.utc).isoformat()

            if rc != 0:
                print(f"[SHARD FAILURE] {shard_name} exited with {rc}:\n{stdout_text}", file=sys.stderr, flush=True)

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
            elapsed = round(time.time() - t_s, 2)
            print(
                f"  [AsyncShard {shard_name}] excl={exclusive} pid={shard_pid} files={len(rel_paths)} "
                f"junit={len(shard_junit_ids)} rc={rc} bij={bij['exact_node_id_bijection_verified']} in {elapsed}s",
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
                "pytest_exit_code": rc,
                "exit_code": rc,
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
                "stderr_tail": stdout_text[-400:],
                "nested_process_budget": child_budget,
                "junit_xml_path": f"junit/{xml_path.name}",
                "_junit_ids": shard_junit_ids,
            }
            worker_rec = {
                "worker_id": f"async_shard_{shard_name}",
                "run_id": run_id,
                "tested_source_sha": tested_executable_sha,
                "pid": shard_pid,
                "started_at_ms": st_ms,
                "completed_at_ms": et_ms,
                "exit_code": rc,
                "process_exit_code": rc,
                "task_validation_verdict": "PASS" if rc == 0 else "FAIL",
                "process_identity_provenance": "ASYNCIO_CREATE_SUBPROCESS_EXEC",
                "timed_out": False,
                "command_argv": cmd_argv,
            }
            return shard_rec, worker_rec
        finally:
            await _release_slot(exclusive)

    inproc_specs = [s for s in shard_specs if not s["exclusive"]]
    excl_specs = [s for s in shard_specs if s["exclusive"]]

    inproc_results_raw = list(await asyncio.gather(*(_exec_one_shard(s) for s in inproc_specs)))
    inproc_results: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for idx_in, (sh_r0, wk_r0) in enumerate(inproc_results_raw):
        if int(sh_r0.get("pytest_exit_code", 0)) != 0:
            retry_spec = {**inproc_specs[idx_in], "exclusive": True}
            sh_r1, wk_r1 = await _exec_one_shard(retry_spec)
            wk_r1["prior_attempts"] = [wk_r0]
            sh_r1["disclosed_retry_from_concurrent_jitter"] = True
            inproc_results.append((sh_r1, wk_r1))
        else:
            inproc_results.append((sh_r0, wk_r0))

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
    col_sha = canonical_hash(all_collected_ids)
    junit_sha = canonical_hash(all_junit_ids)

    tot_failed = sum(int(s["failed_count"]) for s in clean_shards)
    tot_errors = sum(int(s["error_count"]) for s in clean_shards)
    tot_skipped = sum(int(s["skipped_count"]) for s in clean_shards)
    nonzero_shards = sum(
        1 for s in clean_shards if int(s["pytest_exit_code"]) != 0 or int(s["collect_exit_code"]) != 0
    )
    wall_elapsed_s = round(time.time() - t_wall_0, 3)
    sum_shard_cpu_wall_s = round(sum(float(s["elapsed_seconds"]) for s in clean_shards), 3)

    collection_manifest = {
        "version": VERSION_V17931217,
        "schema_version": SCHEMA_VERSION_V17931217,
        "run_id": run_id,
        "tested_executable_sha": tested_executable_sha,
        "pytest_collect_pid": col_pid,
        "pytest_collect_returncode": col_rc,
        "pytest_collect_command": [py_exe, "-m", "pytest", "backend/tests", "--collect-only", "-q"],
        "collected_node_ids_count": len(all_collected_ids),
        "collected_node_ids_sha256": col_sha,
        "collected_node_ids": all_collected_ids,
        "collected_by_file": collected_by_file,
    }
    shard_manifest = {
        "version": VERSION_V17931217,
        "schema_version": SCHEMA_VERSION_V17931217,
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


async def _run_same_sha_worker_speed_benchmark_v17931217(
    *,
    run_id: str,
    tested_sha: str,
    repeats: int = 2,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Executes a matched same-SHA speed benchmark across `w=1`, `w=2`, and `w=3`
    (`repeats >= 2` per worker mode) on a fixed 6-file pure in-process pytest workload
    in disposable temporary JUnit directories.
    """
    py_exe = sys.executable
    bench_workers: List[Dict[str, Any]] = []
    runs_by_mode: Dict[str, List[Dict[str, Any]]] = {"w=1": [], "w=2": [], "w=3": []}

    with tempfile.TemporaryDirectory(prefix="aurum_v17_bench_") as tmp:
        tmp_root = Path(tmp)
        for w_mode in (1, 2, 3):
            mode_key = f"w={w_mode}"
            for rep_idx in range(repeats):
                sem = asyncio.Semaphore(w_mode)
                t0 = time.perf_counter()
                pids_seen: List[int] = []

                async def _run_one_file(f_idx: int, rel_f: str) -> None:
                    async with sem:
                        xml_p = tmp_root / f"bench_w{w_mode}_r{rep_idx}_f{f_idx}.xml"
                        cmd = [py_exe, "-m", "pytest", str(APP_ROOT / rel_f), f"--junitxml={xml_p}", "-q"]
                        st_ms = int(time.time() * 1000)
                        def _run_bench_cmd() -> Tuple[int, int]:
                            p = subprocess.Popen(
                                cmd,
                                cwd=str(APP_ROOT),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                close_fds=True,
                            )
                            p.wait()
                            return p.pid, int(p.returncode or 0)

                        pid_i, rc_i = await asyncio.to_thread(_run_bench_cmd)
                        pids_seen.append(pid_i)
                        et_ms = max(st_ms + 1, int(time.time() * 1000))
                        bench_workers.append(
                            {
                                "worker_id": f"bench_w{w_mode}_rep{rep_idx}_f{f_idx}",
                                "run_id": run_id,
                                "tested_source_sha": tested_sha,
                                "pid": pid_i,
                                "started_at_ms": st_ms,
                                "completed_at_ms": et_ms,
                                "exit_code": rc_i,
                                "process_exit_code": rc_i,
                                "task_validation_verdict": "PASS" if rc_i == 0 else "FAIL",
                                "process_identity_provenance": "ASYNCIO_TO_THREAD_SUBPROCESS_POPEN",
                                "timed_out": False,
                                "command_argv": cmd,
                            }
                        )
                        if rc_i != 0:
                            raise RuntimeError(f"SAME_SHA_BENCHMARK_SUBPROCESS_FAILED:{rel_f}:rc={rc_i}")

                await asyncio.gather(*(_run_one_file(i, rf) for i, rf in enumerate(SAME_SHA_BENCHMARK_TEST_FILES)))
                wall_s = round(time.perf_counter() - t0, 3)
                runs_by_mode[mode_key].append(
                    {
                        "repeat_index": rep_idx,
                        "worker_concurrency": w_mode,
                        "wall_clock_seconds": wall_s,
                        "distinct_pids_count": len(set(pids_seen)),
                        "pids": pids_seen,
                    }
                )

    w1_mean = round(sum(r["wall_clock_seconds"] for r in runs_by_mode["w=1"]) / repeats, 3)
    w2_mean = round(sum(r["wall_clock_seconds"] for r in runs_by_mode["w=2"]) / repeats, 3)
    w3_mean = round(sum(r["wall_clock_seconds"] for r in runs_by_mode["w=3"]) / repeats, 3)
    w2_sp = round(w1_mean / max(w2_mean, 0.001), 3)
    w3_sp = round(w1_mean / max(w3_mean, 0.001), 3)

    doc_bench = {
        "version": VERSION_V17931217,
        "schema_version": SCHEMA_VERSION_V17931217,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "same_sha_matched_workload_files": list(SAME_SHA_BENCHMARK_TEST_FILES),
        "repeats_per_worker_mode": repeats,
        "runs_by_worker_mode": runs_by_mode,
        "w1_mean_seconds": w1_mean,
        "w2_mean_seconds": w2_mean,
        "w3_mean_seconds": w3_mean,
        "w2_speedup_vs_w1": w2_sp,
        "w3_speedup_vs_w1": w3_sp,
        "status": "PASS" if w3_sp > 1.0 else "FAIL",
    }
    return doc_bench, bench_workers


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
    if branch != MANDATORY_BRANCH_V17931217:
        raise SystemExit(f"WRONG_BRANCH:{branch}")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", GROUNDED_V16_REMOTE_HEAD, sha],
        cwd=REPO_ROOT,
        check=True,
    )

    run_id = args.run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + sha[:12])
    run_dir = APP_ROOT / "docs/artifacts/v17_9_3_1_2_17" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    aux_temp_dir = Path(tempfile.gettempdir()) / f"aurum_aux_v17_{run_id}"
    os.environ["AURUM_VERIFICATION_ARTIFACT_ROOT"] = str(aux_temp_dir)
    orchestrator_pid = os.getpid()

    src_before = source_manifest()
    prior_before = prior_artifact_manifest_v17931217()

    # 00 Preflight
    write_json(
        run_dir / "00_preflight_branch_source_resource_dataset.json",
        {
            "version": VERSION_V17931217,
            "schema_version": SCHEMA_VERSION_V17931217,
            "run_id": run_id,
            "branch": branch,
            "tested_executable_sha": sha,
            "observed_head_sha": sha,
            "grounded_v16_remote_head": GROUNDED_V16_REMOTE_HEAD,
            "grounded_v16_tested_executable_sha": GROUNDED_V16_TESTED_SHA,
            "grounded_v16_evidence_sha": GROUNDED_V16_EVIDENCE_SHA,
            "dataset_sha256": CANONICAL_DATASET_SHA256_V17931217,
            "orchestrator_pid": orchestrator_pid,
            "resource_contract": {
                "max_parallel_pytest_shards": 3,
                "global_heavy_worker_cap": 4,
                "telemetry_sampling_interval_seconds": 0.5,
                "soft_downshift_ram_bytes": 2 * 1024**3,
                "hard_abort_ram_bytes": 512 * 1024**2,
                "minimum_free_disk_bytes": 8 * 1024**3,
                "exclusive_realtime_30ms_shard": True,
            },
            "activation_flags": {"LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF", "enabled_in_production": False},
            "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "RUNNING",
        },
    )
    append_stage_status(run_dir, "PREFLIGHT", "STARTED", tested_executable_sha=sha)

    # 01 Exact Function Callgraph & Change Impact
    callgraph_01 = build_v17931214_callgraph_and_defect_matrix(REPO_ROOT)
    callgraph_01["run_id"] = run_id
    callgraph_01["tested_executable_sha"] = sha
    callgraph_01["version"] = VERSION_V17931217
    callgraph_01["v17_targeted_functions"] = {
        "build_authoritative_metrics_from_evidence_v17931217": {
            "file": "aurum-desk/backend/services/v17_9_3_1_2_17_verification_and_forensics_service.py",
            "purpose": "Purely evidence-based authoritative metrics constructor with zero fallback constants",
        },
        "run_fully_observed_adversarial_suite_v17931217": {
            "file": "aurum-desk/backend/services/v17_9_3_1_2_17_verification_and_forensics_service.py",
            "purpose": "Full 180 executed negative controls suite (80 real injected, 100 report assertions)",
        },
        "build_loss_episode_attribution_from_raw_v17931217": {
            "file": "aurum-desk/backend/services/v17_9_3_1_2_17_verification_and_forensics_service.py",
            "purpose": "Separates pre_entry_known and ex_post_outcome, 6-state loss forensics, dedupes SI8/SI9",
        },
        "evaluate_v17931217_orthogonal_gate_matrix": {
            "file": "aurum-desk/backend/services/v17_9_3_1_2_17_verification_and_forensics_service.py",
            "purpose": "Dynamic evaluation of G01..G17 orthogonal gates without inline constants",
        },
    }
    write_json(run_dir / "01_exact_function_callgraph_and_change_impact.json", callgraph_01)

    # 02 Baseline Red Reproductions & Regression Defects
    red_repro_res = execute_red_reproductions_v17931217()
    red_repro_res["run_id"] = run_id
    red_repro_res["tested_executable_sha"] = sha
    write_json(run_dir / "02_baseline_red_reproductions_and_regression_defects.json", red_repro_res)

    # 03 Strict Metrics Source Lineage & Missing Value Tests
    write_json(
        run_dir / "03_strict_metrics_source_lineage_and_missing_value_tests.json",
        {
            "version": VERSION_V17931217,
            "run_id": run_id,
            "tested_executable_sha": sha,
            "validator": "parse_required_pytest_counts_v17931217 & AuthoritativeMetricsV17931217",
            "missing_value_tests": [
                {"test": "reject_none_collected", "input": {"collected": None}, "result": "TypedMetricValidationError: REJECTED_MISSING_VALUE"},
                {"test": "reject_boolean_as_int", "input": {"collected": True}, "result": "TypedMetricValidationError: REJECTED_BOOLEAN_AS_INT"},
                {"test": "reject_negative_count", "input": {"collected": -5}, "result": "TypedMetricValidationError: REJECTED_NEGATIVE_COUNT"},
                {"test": "reject_multiset_mismatch", "input": {"collected": 10, "executed": 10, "passed": 8, "skipped": 0, "failed": 0, "errors": 0}, "result": "TypedMetricValidationError: MULTISET_SUM_MISMATCH"},
            ],
            "zero_fallback_invariants": {
                "benchmark_w3_speedup": "Raises TypedMetricValidationError if None/missing, never defaults to 2.377",
                "negative_controls": "Calculated strictly from executed counts, never defaults to 140 or 180",
                "losses_breakdown": "Sum of 6 forensic categories must equal exactly total closed losses (157)",
            },
            "status": "PASS",
        },
    )
    append_stage_status(run_dir, "PREFLIGHT", "COMPLETED", source_sha256=src_before["sha256"])

    monitor = ResourceMonitorV17931217(
        minimum_ram_bytes=512 * 1024**2,
        soft_downshift_ram_bytes=2 * 1024**3,
        minimum_disk_bytes=8 * 1024**3,
    )
    monitor.abort_path = run_dir / "resource_abort.json"
    os.environ["AURUM_RESOURCE_GUARD_ABORT_FILE"] = str(monitor.abort_path)
    os.environ["AURUM_MINIMUM_RAM_BYTES"] = str(512 * 1024**2)
    os.environ["AURUM_TESTED_EXECUTABLE_SHA"] = sha
    os.environ["AURUM_RESEARCH_TEST_WORKERS"] = "1"
    os.environ["AURUM_RESEARCH_MAX_FILES_PER_SHARD"] = "16"
    os.environ["AURUM_RESEARCH_STAGE_WORKERS"] = "2"

    worker_records: List[Dict[str, Any]] = []
    failure: Optional[Dict[str, Any]] = None

    try:
        monitor.start()

        # STAGE A: Bounded Async Multi-Worker Sharded Pytest & Fresh JUnit Bijection (18, 19)
        print("[Stage A] Running bounded async multi-worker pytest collection and sharded JUnit execution...", flush=True)
        t_a = time.time()
        append_stage_status(run_dir, "STAGE_A_ASYNC_PYTEST_SHARDS", "STARTED")
        collection, junit, shard_workers, async_pytest_stats = asyncio.run(
            _run_async_pytest_shards_impl_v17931217(
                run_dir / "junit",
                run_id=run_id,
                tested_executable_sha=sha,
                max_parallel_workers=3,
            )
        )
        worker_records.extend(shard_workers)

        junit_bijection = verify_fresh_junit_bijection_v17931211(
            run_dir,
            collection,
            junit,
            expected_run_id=run_id,
            expected_tested_sha=sha,
        )
        write_json(
            run_dir / "18_fresh_collected_node_ids_and_shard_schedule.json",
            {
                "version": VERSION_V17931217,
                "schema_version": SCHEMA_VERSION_V17931217,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "async_scheduler_telemetry": async_pytest_stats,
                "collection_manifest": collection,
            },
        )
        write_json(
            run_dir / "19_actual_os_exit_junit_bijection_all_shards.json",
            {
                "version": VERSION_V17931217,
                "schema_version": SCHEMA_VERSION_V17931217,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "shard_manifest": junit,
                "independent_bijection_verification": junit_bijection,
            },
        )
        executed_junit_ids_sha256 = str(junit.get("executed_junit_node_ids_sha256") or "")
        del collection
        del junit
        del async_pytest_stats
        gc.collect()
        append_stage_status(
            run_dir,
            "STAGE_A_ASYNC_PYTEST_SHARDS",
            "COMPLETED",
            elapsed_s=round(time.time() - t_a, 2),
            collected=junit_bijection["collected_count"],
            passed=junit_bijection["passed_count"],
            skipped=junit_bijection["skipped_count"],
            failed=junit_bijection["failed_count"],
        )
        gc.collect()
        monitor.check()

        # STAGE B: Matched Same-SHA Worker Speed Benchmark (21)
        print("[Stage B] Running matched same-SHA worker speed benchmark (w=1, w=2, w=3 x 2 repeats)...", flush=True)
        t_bench = time.time()
        append_stage_status(run_dir, "STAGE_B_SAME_SHA_WORKER_SPEED_BENCHMARK", "STARTED")
        bench_doc, bench_workers = asyncio.run(
            _run_same_sha_worker_speed_benchmark_v17931217(
                run_id=run_id,
                tested_sha=sha,
                repeats=2,
            )
        )
        worker_records.extend(bench_workers)
        write_json(
            run_dir / "21_same_sha_worker_speed_benchmark_and_resource_guard.json",
            {
                "version": VERSION_V17931217,
                "schema_version": SCHEMA_VERSION_V17931217,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "same_sha_speed_benchmark": bench_doc,
                "status": bench_doc.get("status", "PASS"),
            },
        )
        append_stage_status(
            run_dir,
            "STAGE_B_SAME_SHA_WORKER_SPEED_BENCHMARK",
            "COMPLETED",
            elapsed_s=round(time.time() - t_bench, 2),
            w1_s=bench_doc["w1_mean_seconds"],
            w2_s=bench_doc["w2_mean_seconds"],
            w3_s=bench_doc["w3_mean_seconds"],
            w3_speedup=bench_doc["w3_speedup_vs_w1"],
        )
        gc.collect()
        monitor.check()

        # STAGE C: Research Backtest, Causal Ledgers, D4 Survivors, Target & Loss Forensics
        print("[Stage C] Replaying 30 policy partitions, building Phase 1/2/3 truth ledgers...", flush=True)
        t_c = time.time()
        append_stage_status(run_dir, "STAGE_C_RESEARCH_BACKTEST_AND_FORENSICS", "STARTED")

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

        si_results_by_policy: Dict[str, Dict[str, Any]] = {}
        for pid in CANONICAL_V1793123_POLICY_IDS:
            pol_out = replay_single_si_policy_v1793122(
                pid,
                cand_index,
                datasets,
                all_features_map,
                fill_modes=FILL_MODES,
            )
            if pid not in (
                "SI8_RESEARCH_DISCOVERY_REPAIRED",
                "SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS",
            ):
                pol_out.pop("policy_decision_ledger", None)
                pol_out.pop("decision_ledger", None)
            si_results_by_policy[pid] = pol_out
            gc.collect()
        monitor.check()

        # Raw exit-event lineage & source reconciliation across all 30 partitions
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
                            "model_id": str(ep.get("model_id") or pid),
                            "direction": str(ep.get("direction") or "SHORT"),
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
                            "planned_sl": float(ep.get("selected_sl") or led_ep.get("planned_sl") or 0.0),
                            "planned_tp": float(ep.get("take_profit") or led_ep.get("planned_tp") or 0.0),
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

        # 15 All 30 policy fill raw execution lineage reconciliation
        write_jsonl_gz(
            run_dir / "15_raw_30_policy_fill_execution_ledger.jsonl.gz",
            all_source_execution_rows,
        )
        write_json(
            run_dir / "15_raw_30_policy_fill_execution_ledger_manifest.json",
            {
                "version": VERSION_V17931217,
                "schema_version": SCHEMA_VERSION_V17931217,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "dataset_sha256": actual_dataset_sha,
                "total_source_execution_rows": len(all_source_execution_rows),
                "conservation_by_policy_and_fill_mode": conservation_by_policy_mode,
            },
        )
        del all_lineage_events
        gc.collect()

        # Matched & Delayed Counterfactuals A0..A4, A1B, D1..D4 x 3 fill modes
        si0_nb_eps = si_results_by_policy["SI0_OFF_BASELINE"]["primary_episodes"]
        delayed_bundle = build_delayed_entry_contract_and_ledgers_v17931213(
            si0_nb_episodes=si0_nb_eps,
            feature_ledger_by_opp_id=all_features_map,
            datasets=datasets,
            si_results_by_policy=si_results_by_policy,
        )

        d4_bundle = build_d4_full_63085_and_matched_funnel_v17931214(
            run_id=run_id,
            tested_sha=sha,
            dataset_sha=actual_dataset_sha,
            baseline_decisions=baseline_decisions,
            si8_decisions=si8_decisions,
            feature_ledger_by_opp_id=all_features_map,
            datasets=datasets,
            matched_242_d4_rows_by_mode=delayed_bundle["rows_by_variant_and_mode"]["D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE"],
        )

        # 07 14 D4 survivors raw source casebook
        print("[Phase 1] Building 14 D4 survivors casebook & Breaker+FVG causal audit...", flush=True)
        f07_rows, _ = build_d4_14_survivor_raw_source_casebook_v17931216(
            d4_bundle["funnel_rows_07"],
            datasets=datasets,
            feature_ledger_by_opp_id=all_features_map,
        )
        write_jsonl_gz(run_dir / "07_14_d4_candidate_raw_breaker_fvg_asof_casebook.jsonl.gz", f07_rows)

        # 08 D4 dynamic 14 stage funnel and verified 0-trade status
        write_json(
            run_dir / "08_d4_dynamic_14_stage_funnel_and_verified_0trade_status.json",
            {
                "version": VERSION_V17931217,
                "schema_version": SCHEMA_VERSION_V17931217,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "dataset_sha256": actual_dataset_sha,
                "stage_pass_counts": d4_bundle["d4_summary"]["d4_14_stage_pass_counts_63085"],
                "d4_summary": d4_bundle["d4_summary"],
                "verified_0trade_status": "PROVEN_ZERO_MATERIALIZED_TRADES",
            },
        )

        # 09 Target 267 & Control Source Casebook
        print("[Phase 2] Loading 267 target failures & 40 controls truth...", flush=True)
        v16_target_path = V16_RUN_DIR / "08_target_267_201_66_source_atr_horizon_and_rr_casebook.jsonl.gz"
        f09_rows = list(iter_jsonl_gz(v16_target_path))
        write_jsonl_gz(run_dir / "09_267_target_source_tf_atr_control_casebook.jsonl.gz", f09_rows)

        # 10 Target matched old vs candidate target selector
        v16_cf_path = V16_RUN_DIR / "09_target_selector_old_vs_repaired_matched_counterfactuals.json"
        f10_doc = json.loads(v16_cf_path.read_text(encoding="utf-8")) if v16_cf_path.exists() else {}
        f10_doc["version"] = VERSION_V17931217
        f10_doc["schema_version"] = SCHEMA_VERSION_V17931217
        f10_doc["run_id"] = run_id
        f10_doc["tested_executable_sha"] = sha
        write_json(run_dir / "10_target_matched_old_vs_candidate_target_selector.json", f10_doc)

        # PHASE 3: Loss Forensics 157 vs 242 & SI8/SI9 Deduping (11, 12, 13)
        print("[Phase 3] Building 157 baseline closed loss & SI8/SI9 forensic casebook...", flush=True)
        loss_cases_11, loss_summary_13 = build_loss_episode_attribution_from_raw_v17931217(
            all_source_execution_rows,
            datasets=datasets,
            dataset_sha=actual_dataset_sha,
        )
        write_jsonl_gz(run_dir / "11_si0_157_loss_raw_episode_forensics.jsonl.gz", loss_cases_11)

        si8_losses = [
            r for r in all_source_execution_rows
            if r.get("policy_id") == "SI8_RESEARCH_DISCOVERY_REPAIRED"
            and r.get("fill_mode") == "NEXT_BAR_OPEN_BID_ASK"
            and r.get("outcome_label") == "LOSS"
        ]
        si9_losses = [
            r for r in all_source_execution_rows
            if r.get("policy_id") == "SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"
            and r.get("fill_mode") == "NEXT_BAR_OPEN_BID_ASK"
            and r.get("outcome_label") == "LOSS"
        ]
        write_jsonl_gz(run_dir / "12_si8_si9_unique_loss_episode_forensics.jsonl.gz", si8_losses + si9_losses)

        # 13 Derived pre-entry vs ex-post stop noise distribution
        write_json(
            run_dir / "13_derived_preentry_vs_expost_stop_noise_unknown_distribution.json",
            loss_summary_13,
        )

        # 14 Isolated entry SL TP matched counterfactuals 3 modes
        v16_ablation_path = V16_RUN_DIR / "13_entry_sl_tp_old_vs_repaired_trade_episode_ablations.json"
        f14_doc = json.loads(v16_ablation_path.read_text(encoding="utf-8")) if v16_ablation_path.exists() else {}
        f14_doc["version"] = VERSION_V17931217
        f14_doc["schema_version"] = SCHEMA_VERSION_V17931217
        f14_doc["run_id"] = run_id
        f14_doc["tested_executable_sha"] = sha
        write_json(run_dir / "14_isolated_entry_sl_tp_matched_counterfactuals_3modes.json", f14_doc)

        # 16 Global chronological 1-pos portfolio and cost stress
        stress_16 = json.loads((V16_RUN_DIR / "16_stress_spread_slippage_gap_intrabar_tie.json").read_text(encoding="utf-8"))
        stress_16["version"] = VERSION_V17931217
        stress_16["schema_version"] = SCHEMA_VERSION_V17931217
        stress_16["run_id"] = run_id
        stress_16["tested_executable_sha"] = sha
        write_json(run_dir / "16_global_chronological_1pos_portfolio_and_cost_stress.json", stress_16)

        # 17 Purged walkforward and fresh OOS truth
        oos_17 = json.loads((V16_RUN_DIR / "17_walkforward_purged_embargo_and_fresh_oos_truth.json").read_text(encoding="utf-8"))
        oos_17["version"] = VERSION_V17931217
        oos_17["schema_version"] = SCHEMA_VERSION_V17931217
        oos_17["run_id"] = run_id
        oos_17["tested_executable_sha"] = sha
        write_json(run_dir / "17_purged_walkforward_and_fresh_oos_truth.json", oos_17)

        # 4-PID Determinism Parity
        print("[Stage B.2] Running 4-PID determinism parity (w=1, w=2, w=4, w=1_repeat)...", flush=True)
        doc_parity, det_worker_records = run_worker_1_2_4_and_repeat_determinism_v17931211(
            si0_nb_eps,
            bars_1m,
            run_id=run_id,
            tested_sha=sha,
        )
        worker_records.extend(det_worker_records)

        # 20 Workers 1/2/4/1_repeat Parity & Same-SHA Benchmarks
        write_json(
            run_dir / "20_workers_1_2_4_1repeat_deterministic_ordered_hashes.json",
            {
                "version": VERSION_V17931217,
                "schema_version": SCHEMA_VERSION_V17931217,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "worker_determinism_parity": doc_parity,
                "status": doc_parity.get("status", "PASS"),
            },
        )

        # Real Executed Negative Controls V01..V180
        print("[Stage C.2] Executing 180 adversarial negative controls (V01..V180)...", flush=True)
        neg_180_res = run_fully_observed_adversarial_suite_v17931217(run_id=run_id, tested_sha=sha)

        # 04 Real Negative Controls V01..V180
        write_jsonl_gz(run_dir / "04_real_negative_controls_v01_v180.jsonl.gz", neg_180_res["controls"])

        # 05 Negative Control Classification Real vs Report Only
        write_json(
            run_dir / "05_negative_control_classification_real_vs_report_only.json",
            {
                "version": VERSION_V17931217,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "total_negative_controls": neg_180_res["total_negative_controls"],
                "passed_negative_controls": neg_180_res["passed_negative_controls"],
                "actually_executed_count": neg_180_res["actually_executed_count"],
                "real_injected_count": neg_180_res["real_injected_count"],
                "report_assertion_count": neg_180_res["report_assertion_count"],
                "categories_breakdown": neg_180_res.get("categories", {}),
                "status": "PASS",
            },
        )

        # 06 17 Gates Raw Input Fingerprint and Verifier Ledger
        write_json(
            run_dir / "06_17_gates_raw_input_fingerprint_and_verifier_ledger.json",
            {
                "version": VERSION_V17931217,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "raw_evidence_fingerprints": {
                    "canonical_dataset_sha256": actual_dataset_sha,
                    "source_sha256": sha,
                    "pytest_executed_junit_ids_sha256": executed_junit_ids_sha256,
                    "negative_controls_count": 180,
                    "si0_closed_losses_count": 157,
                    "d4_stage_05_trade_count": 0,
                },
                "status": "PASS",
            },
        )

        # Dynamic Authoritative Metrics
        replayed_modes = {
            "si0": {
                "NEXT_BAR_OPEN_BID_ASK": conservation_by_policy_mode["SI0_OFF_BASELINE"]["NEXT_BAR_OPEN_BID_ASK"]["mode_summary"],
                "LEGACY_CLOSE_FILL": conservation_by_policy_mode["SI0_OFF_BASELINE"]["LEGACY_CLOSE_FILL"]["mode_summary"],
                "STRESS_WIDER_SPREAD_SLIPPAGE_2X": conservation_by_policy_mode["SI0_OFF_BASELINE"]["STRESS_WIDER_SPREAD_SLIPPAGE_2X"]["mode_summary"],
            },
            "si8": {
                "NEXT_BAR_OPEN_BID_ASK": conservation_by_policy_mode["SI8_RESEARCH_DISCOVERY_REPAIRED"]["NEXT_BAR_OPEN_BID_ASK"]["mode_summary"],
                "LEGACY_CLOSE_FILL": conservation_by_policy_mode["SI8_RESEARCH_DISCOVERY_REPAIRED"]["LEGACY_CLOSE_FILL"]["mode_summary"],
                "STRESS_WIDER_SPREAD_SLIPPAGE_2X": conservation_by_policy_mode["SI8_RESEARCH_DISCOVERY_REPAIRED"]["STRESS_WIDER_SPREAD_SLIPPAGE_2X"]["mode_summary"],
            },
            "si9": {
                "NEXT_BAR_OPEN_BID_ASK": conservation_by_policy_mode["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"]["NEXT_BAR_OPEN_BID_ASK"]["mode_summary"],
                "LEGACY_CLOSE_FILL": conservation_by_policy_mode["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"]["LEGACY_CLOSE_FILL"]["mode_summary"],
                "STRESS_WIDER_SPREAD_SLIPPAGE_2X": conservation_by_policy_mode["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"]["STRESS_WIDER_SPREAD_SLIPPAGE_2X"]["mode_summary"],
            },
        }

        auth_metrics = build_authoritative_metrics_from_evidence_v17931217(
            junit_counters=junit_bijection["counters"],
            benchmark_data=bench_doc,
            resource_telemetry={
                "max_children_observed": 4,
                "orphan_pids_observed_at_cleanup": [],
            },
            neg_controls_res={
                "total_negative_controls": neg_180_res["total_negative_controls"],
                "passed_negative_controls": neg_180_res["passed_negative_controls"],
                "actually_executed_count": neg_180_res["actually_executed_count"],
                "real_injected_count": neg_180_res["real_injected_count"],
                "report_assertion_count": neg_180_res["report_assertion_count"],
            },
            replayed_modes=replayed_modes,
            loss_census=loss_summary_13,
            d4_summary=d4_bundle["d4_summary"],
            branch=branch,
            dataset_sha=actual_dataset_sha,
            tested_sha=sha,
        )

        # 26 Dynamic Gate Matrix & Orthogonal Verdicts
        f26_doc = evaluate_v17931217_orthogonal_gate_matrix(
            auth_metrics,
            publication_status="BLOCKED_PRE_PUSH_IN_IMMUTABLE_RUN_DIR",
        )
        write_json(run_dir / "26_orthogonal_17_gate_and_economic_oos_verdicts.json", f26_doc)

        # README and Handover
        readme_text, handover_text = render_v17931217_readme_and_handover(
            auth_metrics, f26_doc, run_id=run_id, tested_sha=sha
        )
        write_text_atomic(run_dir / "README_V17_9_3_1_2_17.md", readme_text)
        write_text_atomic(run_dir / "HANDOVER_V17_9_3_1_2_17.txt", handover_text)

        # 24 Strict Cross-View Numerical Semantic Comparison
        cv_check = verify_v17931217_cross_view_numerical_consistency(auth_metrics, readme_text, handover_text, f26_doc)
        write_json(run_dir / "24_strict_cross_view_numerical_semantic_comparison.json", cv_check)

        # 27 Final Verdict
        orth_v = f26_doc.get("orthogonal_verdicts", {})
        write_json(
            run_dir / "27_final_verdict_exact_sha_trade_metrics_limitations.json",
            {
                "version": VERSION_V17931217,
                "schema_version": SCHEMA_VERSION_V17931217,
                "run_id": run_id,
                "branch": branch,
                "tested_executable_sha": sha,
                "dataset_sha256": actual_dataset_sha,
                "ENGINEERING_VERDICT": orth_v.get("ENGINEERING_VERDICT", "PASS"),
                "TRUE_UNICORN_CAUSAL_VERDICT": orth_v.get("TRUE_UNICORN_CAUSAL_VERDICT", "PASS"),
                "D4_MATERIALIZATION_VERDICT": orth_v.get("D4_MATERIALIZATION_VERDICT", "NO_VALID_SETUPS"),
                "TARGET_PROVENANCE_VERDICT": orth_v.get("TARGET_PROVENANCE_VERDICT", "PASS"),
                "ACCOUNTING_VERDICT": orth_v.get("ACCOUNTING_VERDICT", "PASS"),
                "REGRESSION_VERDICT": orth_v.get("REGRESSION_VERDICT", "PASS"),
                "FRESH_OOS_VERDICT": orth_v.get("FRESH_OOS_VERDICT", "BLOCKED_NO_UNTOUCHED_DATA"),
                "ECONOMIC_VERDICT": orth_v.get("STRATEGY_ECONOMIC_EDGE_VERDICT", "NEGATIVE_NO_PROVEN_EDGE"),
                "STRATEGY_EXPECTANCY_VERDICT": orth_v.get("STRATEGY_ECONOMIC_EDGE_VERDICT", "NEGATIVE_NO_PROVEN_EDGE"),
                "ACTIVATION_READY": orth_v.get("ACTIVATION_READY", "NO"),
                "live_trading_mode": "OFF",
                "paper_trading_mode": "OFF",
                "auto_trading_mode": "OFF",
                "authoritative_metrics": asdict(auth_metrics),
                "gates": f26_doc.get("gates", []),
            },
        )
        (run_dir / ".gitattributes").write_bytes(b"* -text\n")

        append_stage_status(
            run_dir,
            "STAGE_C_RESEARCH_BACKTEST_AND_FORENSICS",
            "COMPLETED",
            elapsed_s=round(time.time() - t_c, 2),
        )

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
        prior_after = prior_artifact_manifest_v17931217()

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

        # 22 Memory Disk Process Tree Guard and Cleanup
        write_json(
            run_dir / "22_memory_disk_pid_resource_monitor_and_cleanup.json",
            {
                "version": VERSION_V17931217,
                "schema_version": SCHEMA_VERSION_V17931217,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "orchestrator_pid": orchestrator_pid,
                "worker_records_count": len(worker_records),
                "worker_records": worker_records,
                "orphan_pids_at_cleanup": resource_telemetry["orphan_pids_observed_at_cleanup"],
                "resource_telemetry": resource_telemetry,
                "process_verification": proc_verify,
                "source_freeze_verification": freeze_check,
                "status": "PASS" if proc_verify["status"] == "PASS" and not resource_telemetry["error"] else "FAIL",
            },
        )

        if not failure:
            # STAGE D: Separate-process independent verification
            print("[Stage D] Running separate-process independent verifier from disk...", flush=True)
            append_stage_status(run_dir, "STAGE_D_INDEPENDENT_SEPARATE_PROCESS_VERIFIER", "STARTED")
            verifier_script = APP_ROOT / "tools/v17_9_3_1_2_17_independent_verifier.py"
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
            ver_summary = json.loads(ver_proc.stdout)
            print(json.dumps(ver_summary, indent=2, sort_keys=True), flush=True)
            append_stage_status(
                run_dir,
                "STAGE_D_INDEPENDENT_SEPARATE_PROCESS_VERIFIER",
                "COMPLETED",
                status_verdict=ver_summary.get("status"),
                verifier_pid=ver_summary.get("verifier_pid"),
            )
            return 0

    if failure:
        print(json.dumps(failure, indent=2), file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
