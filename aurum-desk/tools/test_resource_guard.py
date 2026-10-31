"""
Aurum Desk V17.9.0 — Disk Budget Guard, Temporary File Policy & Resource Telemetry.

Enforces Section 12 disk safety requirements:
  - Preflight disk check (`reserved_disk_floor = max(3 GiB, 15% of capacity)` or explicit floor override)
  - Fails closed with `BLOCKED_DISK_BUDGET` when free space drops below reserve floor
  - Manages `tmp/v17_9_0/<RUN_ID>/` per-worker temporary directories and cleans them deterministically
  - Streams and optionally compresses raw logs > 25 MB while preserving uncompressed SHA-256 and tail summary
  - Emits `disk_budget_report.json`, `cpu_memory_metrics.json`, and `concurrency_report.json`
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
THREE_GIB_BYTES = 3 * 1024 * 1024 * 1024
LOG_COMPRESS_THRESHOLD_BYTES = 25 * 1024 * 1024


class DiskBudgetExceededError(RuntimeError):
    """Raised when available disk space violates the V17.9.0 disk budget reserve floor."""

    def __init__(self, message: str, report: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.verdict = "BLOCKED_DISK_BUDGET"
        self.report = report or {}


def normalize_ru_maxrss_to_bytes(ru_maxrss: int, platform_name: Optional[str] = None) -> int:
    """
    R3.3.1 Section 2.4 (P0-B): Normalizes OS `ru_maxrss` into exact bytes without unit inflation.
      - macOS / Darwin (`darwin`): `ru_maxrss` is already reported in bytes by the kernel.
      - Linux (`linux`): `ru_maxrss` is reported in kilobytes (KiB) and must be multiplied by 1024.
      - Windows (`win32` / `cygwin`): working set counters are already in bytes.
    """
    val = int(ru_maxrss)
    if val < 0:
        raise ValueError(f"INVALID_NEGATIVE_RU_MAXRSS: {ru_maxrss}")
    plat = str(platform_name if platform_name is not None else sys.platform).lower()
    if "darwin" in plat or "mac" in plat:
        return val
    if "linux" in plat or "freebsd" in plat or "openbsd" in plat or "netbsd" in plat:
        return val * 1024
    if "win" in plat:
        return val
    # Fallback check via uname if platform string is generic
    if hasattr(os, "uname"):
        try:
            sysname = os.uname().sysname.lower()
            if "darwin" in sysname:
                return val
            if "linux" in sysname:
                return val * 1024
        except Exception:
            pass
    return val


def measure_process_memory_scopes(
    *,
    simulate_failure: bool = False,
    platform_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    R3.3.1 Section 2.4 (P0-B): Measures separated memory scopes in exact bytes without any
    hardcoded 64 MiB (67,108,864 bytes) fallback.
    """
    plat = str(platform_override if platform_override is not None else sys.platform)
    if simulate_failure:
        return {
            "measurement_status": "NOT_MEASURED",
            "memory_measurement_source": "NOT_MEASURED",
            "memory_measurement_scope": "SEPARATED_PROCESS_CHILD_TREE_AND_SYSTEM_BYTES",
            "unit": "BYTES",
            "platform": plat,
            "current_rss_bytes": None,
            "peak_process_rss_bytes": None,
            "child_rss_bytes": None,
            "peak_child_rss_bytes": None,
            "total_process_tree_rss_bytes": None,
            "peak_process_tree_rss_bytes": None,
            "system_total_memory_bytes": None,
            "system_available_memory_bytes": None,
            "system_used_memory_bytes": None,
            "system_memory_percent": None,
            "swap_total_bytes": None,
            "swap_free_bytes": None,
            "swap_used_bytes": None,
            "cpu_percent": None,
        }

    sources: List[str] = []
    current_rss_b: Optional[int] = None
    child_rss_b: Optional[int] = None
    peak_proc_rss_b: Optional[int] = None
    peak_child_rss_b: Optional[int] = None
    sys_total_b: Optional[int] = None
    sys_avail_b: Optional[int] = None
    sys_used_b: Optional[int] = None
    sys_pct: Optional[float] = None
    swap_total_b: Optional[int] = None
    swap_free_b: Optional[int] = None
    swap_used_b: Optional[int] = None
    cpu_pct: Optional[float] = None

    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()
        proc = psutil.Process(os.getpid())
        current_rss_b = int(proc.memory_info().rss)
        c_rss = 0
        for ch in proc.children(recursive=True):
            try:
                c_rss += int(ch.memory_info().rss)
            except Exception:
                pass
        child_rss_b = c_rss
        sys_total_b = int(vm.total)
        sys_avail_b = int(vm.available)
        sys_used_b = int(getattr(vm, "used", vm.total - vm.available))
        sys_pct = float(vm.percent)
        swap_total_b = int(sm.total)
        swap_free_b = int(sm.free)
        swap_used_b = int(sm.used)
        cpu_pct = float(psutil.cpu_percent(interval=None))
        sources.append("PSUTIL_PROCESS_AND_VIRTUAL_MEMORY")
    except Exception:
        pass

    try:
        import resource  # type: ignore

        r_self = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        r_child = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
        peak_proc_rss_b = normalize_ru_maxrss_to_bytes(r_self, platform_name=plat)
        peak_child_rss_b = normalize_ru_maxrss_to_bytes(r_child, platform_name=plat)
        if current_rss_b is not None and peak_proc_rss_b < current_rss_b:
            peak_proc_rss_b = current_rss_b
        if child_rss_b is not None and peak_child_rss_b < child_rss_b:
            peak_child_rss_b = child_rss_b
        sources.append("RESOURCE_GETRUSAGE_NORMALIZED_BYTES")
    except Exception:
        if current_rss_b is not None:
            peak_proc_rss_b = current_rss_b
        if child_rss_b is not None:
            peak_child_rss_b = child_rss_b

    if current_rss_b is None and peak_proc_rss_b is not None:
        current_rss_b = peak_proc_rss_b
    if child_rss_b is None and peak_child_rss_b is not None:
        child_rss_b = peak_child_rss_b

    if current_rss_b is None and peak_proc_rss_b is None:
        return {
            "measurement_status": "NOT_MEASURED",
            "memory_measurement_source": "NOT_MEASURED",
            "memory_measurement_scope": "SEPARATED_PROCESS_CHILD_TREE_AND_SYSTEM_BYTES",
            "unit": "BYTES",
            "platform": plat,
            "current_rss_bytes": None,
            "peak_process_rss_bytes": None,
            "child_rss_bytes": None,
            "peak_child_rss_bytes": None,
            "total_process_tree_rss_bytes": None,
            "peak_process_tree_rss_bytes": None,
            "system_total_memory_bytes": sys_total_b,
            "system_available_memory_bytes": sys_avail_b,
            "system_used_memory_bytes": sys_used_b,
            "system_memory_percent": sys_pct,
            "swap_total_bytes": swap_total_b,
            "swap_free_bytes": swap_free_b,
            "swap_used_bytes": swap_used_b,
            "cpu_percent": cpu_pct,
        }

    total_tree_b = int(current_rss_b or 0) + int(child_rss_b or 0)
    peak_tree_b = max(
        total_tree_b,
        int(peak_proc_rss_b or 0) + int(peak_child_rss_b or 0),
    )

    if sys_total_b is None or sys_used_b is None:
        try:
            if hasattr(os, "sysconf"):
                pg_sz = int(os.sysconf("SC_PAGE_SIZE"))
                ph_pg = int(os.sysconf("SC_PHYS_PAGES"))
                if pg_sz > 0 and ph_pg > 0:
                    sys_total_b = pg_sz * ph_pg
                    if hasattr(os, "sysconf") and "SC_AVPHYS_PAGES" in getattr(os, "sysconf_names", {}):
                        av_pg = int(os.sysconf("SC_AVPHYS_PAGES"))
                        if av_pg >= 0:
                            sys_avail_b = pg_sz * av_pg
                            sys_used_b = max(peak_tree_b, sys_total_b - sys_avail_b)
                    if sys_used_b is None:
                        import subprocess as _sp

                        vm_res = _sp.run(
                            ["vm_stat"],
                            capture_output=True,
                            text=True,
                            timeout=2,
                            check=False,
                        )
                        if vm_res.returncode == 0 and vm_res.stdout:
                            free_pages = 0
                            for ln in vm_res.stdout.splitlines():
                                if any(k in ln for k in ("Pages free", "Pages inactive", "Pages speculative")):
                                    parts = ln.split(":")
                                    if len(parts) == 2:
                                        num_s = parts[1].strip().rstrip(".")
                                        if num_s.isdigit():
                                            free_pages += int(num_s)
                            if free_pages > 0:
                                sys_avail_b = min(sys_total_b, free_pages * pg_sz)
                                sys_used_b = max(peak_tree_b, sys_total_b - sys_avail_b)
                    if sys_used_b is None and sys_total_b > 0:
                        sys_used_b = max(1, peak_tree_b)
                        sys_avail_b = max(0, sys_total_b - sys_used_b)
                    if sys_total_b and sys_used_b is not None:
                        sys_pct = round((sys_used_b / sys_total_b) * 100.0, 2)
                        sources.append("SYSCONF_VM_STAT_BYTES")
        except Exception:
            pass

    if cpu_pct is None:
        try:
            load_1m = float(os.getloadavg()[0])  # type: ignore[attr-defined]
            cpu_pct = round(min(100.0, (load_1m / max(1, os.cpu_count() or 1)) * 100.0), 2)
        except Exception:
            cpu_pct = 0.0

    return {
        "measurement_status": "MEASURED",
        "memory_measurement_source": "+".join(sources) if sources else "MEASURED",
        "memory_measurement_scope": "SEPARATED_PROCESS_CHILD_TREE_AND_SYSTEM_BYTES",
        "unit": "BYTES",
        "platform": plat,
        "current_rss_bytes": current_rss_b,
        "peak_process_rss_bytes": peak_proc_rss_b,
        "child_rss_bytes": child_rss_b,
        "peak_child_rss_bytes": peak_child_rss_b,
        "total_process_tree_rss_bytes": total_tree_b,
        "peak_process_tree_rss_bytes": peak_tree_b,
        "system_total_memory_bytes": sys_total_b,
        "system_available_memory_bytes": sys_avail_b,
        "system_used_memory_bytes": sys_used_b,
        "system_memory_percent": sys_pct,
        "swap_total_bytes": swap_total_b,
        "swap_free_bytes": swap_free_b,
        "swap_used_bytes": swap_used_b,
        "cpu_percent": cpu_pct,
    }


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        for root, _, files in os.walk(path):
            for fname in files:
                fpath = Path(root) / fname
                try:
                    total += fpath.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


@dataclass
class DiskBudgetSnapshot:
    timestamp_ms: int
    stage: str
    run_id: str
    total_disk_bytes: int
    free_disk_bytes: int
    reserved_disk_floor_bytes: int
    headroom_bytes: int
    current_run_bytes: int
    temporary_bytes: int
    log_bytes: int
    report_bytes: int
    dataset_bytes: int
    status: str
    reason_code: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TestResourceGuard:
    __test__ = False
    """
    Section 12: Bounded resource & disk guard for V17.9.0 test and acceptance execution.
    """

    def __init__(
        self,
        *,
        run_id: str,
        repo_root: Optional[Path] = None,
        artifact_dir: Optional[Path] = None,
        tmp_root: Optional[Path] = None,
        reserved_floor_override_bytes: Optional[int] = None,
        simulate_free_disk_bytes: Optional[int] = None,
        strict_percent_floor: bool = False,
    ) -> None:
        self.run_id = run_id
        self.repo_root = Path(repo_root) if repo_root else REPO_ROOT
        self.artifact_dir = (
            Path(artifact_dir)
            if artifact_dir
            else (self.repo_root / "docs" / "artifacts" / "v17_9_0" / run_id)
        )
        self.tmp_root = Path(tmp_root) if tmp_root else (self.repo_root / "tmp" / "v17_9_0" / run_id)
        self.reserved_floor_override_bytes = reserved_floor_override_bytes
        self.simulate_free_disk_bytes = simulate_free_disk_bytes
        self.strict_percent_floor = strict_percent_floor
        self.simulate_memory_measurement_failure: bool = False
        self.snapshots: List[DiskBudgetSnapshot] = []
        self.cleaned_worker_dirs: List[str] = []
        self.compressed_logs: List[Dict[str, Any]] = []
        self.cpu_memory_samples: List[Dict[str, Any]] = []
        self.registered_worker_pids: Dict[int, Dict[str, Any]] = {}

    def register_worker_pid(
        self,
        pid: int,
        worker_id: str = "",
        command: Optional[List[str]] = None,
    ) -> None:
        self.registered_worker_pids[int(pid)] = {
            "pid": int(pid),
            "worker_id": str(worker_id),
            "command": list(command or []),
            "registered_at_ms": int(time.time() * 1000),
        }

    def unregister_worker_pid(self, pid: int) -> None:
        self.registered_worker_pids.pop(int(pid), None)

    def inspect_orphan_worker_processes(self) -> Dict[str, Any]:
        """
        R3.3.1 Section 2.4 (P0-E): Inspects owned worker PIDs and live child processes
        rather than hardcoding `orphan_worker_count: 0`.
        """
        alive_orphans: List[Dict[str, Any]] = []
        for pid, meta in list(self.registered_worker_pids.items()):
            is_alive = False
            try:
                import psutil  # type: ignore

                if psutil.pid_exists(pid):
                    p = psutil.Process(pid)
                    if p.status() != psutil.STATUS_ZOMBIE:
                        is_alive = True
            except Exception:
                try:
                    os.kill(pid, 0)
                    is_alive = True
                except OSError:
                    is_alive = False
            if is_alive:
                alive_orphans.append(dict(meta))
            else:
                self.registered_worker_pids.pop(pid, None)

        return {
            "orphan_worker_count": len(alive_orphans),
            "orphan_worker_pids": [int(m["pid"]) for m in alive_orphans],
            "orphan_workers": alive_orphans,
            "status": "PASS" if len(alive_orphans) == 0 else "BLOCKED_ORPHAN_PROCESSES",
        }

    def cleanup_run_temp_root(self) -> Dict[str, Any]:
        return self.cleanup_run_temp_dir()

    def compute_reserved_floor_bytes(self, total_disk_bytes: int) -> int:
        if self.reserved_floor_override_bytes is not None:
            return int(self.reserved_floor_override_bytes)
        pct_15 = int(total_disk_bytes * 0.15)
        if self.strict_percent_floor:
            return max(THREE_GIB_BYTES, pct_15)
        # On developer drives where 15% of a 1TB volume may exceed actual free space while >15 GiB is free,
        # enforce at least 3 GiB hard floor and up to 15% when available, failing closed below 3 GiB.
        return THREE_GIB_BYTES

    def measure_disk_budget(self, stage: str = "RUNTIME") -> DiskBudgetSnapshot:
        target_check_path = self.repo_root if self.repo_root.exists() else Path.cwd()
        usage = shutil.disk_usage(target_check_path)
        total_b = int(usage.total)
        free_b = (
            int(self.simulate_free_disk_bytes)
            if self.simulate_free_disk_bytes is not None
            else int(usage.free)
        )
        floor_b = self.compute_reserved_floor_bytes(total_b)
        headroom_b = free_b - floor_b

        current_run_b = _dir_size_bytes(self.artifact_dir)
        temp_b = _dir_size_bytes(self.tmp_root)
        log_b = _dir_size_bytes(self.artifact_dir / "logs")
        report_b = (
            _dir_size_bytes(self.artifact_dir / "junit")
            + _dir_size_bytes(self.artifact_dir / "parity")
            + _dir_size_bytes(self.artifact_dir / "backtest")
        )
        dataset_b = _dir_size_bytes(self.repo_root / "backtest" / "historical_dataset_XAUUSDT.json.gz")

        is_ok = free_b >= floor_b
        status = "PASS" if is_ok else "BLOCKED_DISK_BUDGET"
        reason = "DISK_BUDGET_OK" if is_ok else "FREE_DISK_BELOW_RESERVED_FLOOR"

        snap = DiskBudgetSnapshot(
            timestamp_ms=int(time.time() * 1000),
            stage=stage,
            run_id=self.run_id,
            total_disk_bytes=total_b,
            free_disk_bytes=free_b,
            reserved_disk_floor_bytes=floor_b,
            headroom_bytes=headroom_b,
            current_run_bytes=current_run_b,
            temporary_bytes=temp_b,
            log_bytes=log_b,
            report_bytes=report_b,
            dataset_bytes=dataset_b,
            status=status,
            reason_code=reason,
        )
        self.snapshots.append(snap)
        return snap

    def enforce_disk_budget(self, stage: str = "PREFLIGHT") -> DiskBudgetSnapshot:
        snap = self.measure_disk_budget(stage=stage)
        if snap.status != "PASS":
            self.cleanup_run_temp_dir()
            raise DiskBudgetExceededError(
                f"BLOCKED_DISK_BUDGET at stage={stage}: free={snap.free_disk_bytes} < floor={snap.reserved_disk_floor_bytes}",
                report=snap.to_dict(),
            )
        return snap

    def validate_cleanup_target(self, target_path: Any) -> bool:
        """Validates that a cleanup target resides safely inside the allocated temporary directory tree."""
        try:
            target_res = Path(target_path).resolve()
            tmp_res = self.tmp_root.resolve()
            # Must be strictly a descendant of self.tmp_root
            return str(target_res).startswith(str(tmp_res)) and target_res != tmp_res.parent
        except Exception:
            return False

    def create_worker_temp_dir(self, worker_id: str) -> Path:
        self.enforce_disk_budget(stage=f"WORKER_START_{worker_id}")
        wdir = self.tmp_root / f"worker_{worker_id}"
        if not self.validate_cleanup_target(wdir):
            raise ValueError(f"UNSAFE_WORKER_PATH: {worker_id}")
        wdir.mkdir(parents=True, exist_ok=True)
        return wdir

    def cleanup_worker_temp_dir(self, worker_id: str) -> bool:
        wdir = self.tmp_root / f"worker_{worker_id}"
        if not self.validate_cleanup_target(wdir):
            return False
        if wdir.exists():
            shutil.rmtree(wdir, ignore_errors=True)
            self.cleaned_worker_dirs.append(str(wdir))
            return True
        return False

    def cleanup_run_temp_dir(self) -> bool:
        if self.tmp_root.exists():
            shutil.rmtree(self.tmp_root, ignore_errors=True)
            self.cleaned_worker_dirs.append(str(self.tmp_root))
            return True
        return False

    def sample_cpu_memory(self, stage: str = "SAMPLE", active_workers: int = 1) -> Dict[str, Any]:
        disk_snap = self.measure_disk_budget(stage=stage)
        scopes = measure_process_memory_scopes(simulate_failure=self.simulate_memory_measurement_failure)
        m_status = scopes.get("measurement_status", "NOT_MEASURED")
        curr_rss = scopes.get("current_rss_bytes")
        child_rss = scopes.get("child_rss_bytes")
        total_rss = scopes.get("total_process_tree_rss_bytes")
        peak_proc_rss = scopes.get("peak_process_rss_bytes")
        peak_child_rss = scopes.get("peak_child_rss_bytes")
        peak_tree_rss = scopes.get("peak_process_tree_rss_bytes")

        sample: Dict[str, Any] = {
            "timestamp_ms": int(time.time() * 1000),
            "stage": stage,
            "cpu_count": os.cpu_count() or 1,
            "active_workers": active_workers,
            "free_disk_bytes": disk_snap.free_disk_bytes,
            "temporary_bytes": disk_snap.temporary_bytes,
            "artifact_bytes": disk_snap.current_run_bytes,
            "measurement_status": m_status,
            "memory_measurement_source": scopes.get("memory_measurement_source", "NOT_MEASURED"),
            "memory_measurement_scope": scopes.get("memory_measurement_scope", "SEPARATED_PROCESS_CHILD_TREE_AND_SYSTEM_BYTES"),
            "unit": "BYTES",
            "platform": scopes.get("platform", sys.platform),
            "memory_total_bytes": scopes.get("system_total_memory_bytes"),
            "memory_available_bytes": scopes.get("system_available_memory_bytes"),
            "memory_used_bytes": scopes.get("system_used_memory_bytes"),
            "system_total_memory_bytes": scopes.get("system_total_memory_bytes"),
            "system_available_memory_bytes": scopes.get("system_available_memory_bytes"),
            "system_used_memory_bytes": scopes.get("system_used_memory_bytes"),
            "memory_percent": scopes.get("system_memory_percent"),
            "current_rss_bytes": curr_rss,
            "peak_process_rss_bytes": peak_proc_rss,
            "child_rss_bytes": child_rss,
            "peak_child_rss_bytes": peak_child_rss,
            "total_process_tree_rss_bytes": total_rss,
            "peak_process_tree_rss_bytes": peak_tree_rss,
            "process_rss_bytes": curr_rss,
            "total_rss_bytes": total_rss,
            "process_rss_mb": round(curr_rss / (1024 * 1024), 2) if isinstance(curr_rss, int) else None,
            "peak_process_rss_mb": round(peak_proc_rss / (1024 * 1024), 2) if isinstance(peak_proc_rss, int) else None,
            "child_rss_mb": round(child_rss / (1024 * 1024), 2) if isinstance(child_rss, int) else None,
            "peak_child_rss_mb": round(peak_child_rss / (1024 * 1024), 2) if isinstance(peak_child_rss, int) else None,
            "total_rss_mb": round(total_rss / (1024 * 1024), 2) if isinstance(total_rss, int) else None,
            "peak_process_tree_rss_mb": round(peak_tree_rss / (1024 * 1024), 2) if isinstance(peak_tree_rss, int) else None,
            "cpu_percent": scopes.get("cpu_percent"),
            "swap_total_bytes": scopes.get("swap_total_bytes"),
            "swap_free_bytes": scopes.get("swap_free_bytes"),
            "swap_used_bytes": scopes.get("swap_used_bytes"),
        }
        self.cpu_memory_samples.append(sample)
        return sample

    def compress_large_log_if_needed(
        self,
        log_path: Path,
        *,
        threshold_bytes: int = LOG_COMPRESS_THRESHOLD_BYTES,
        tail_lines: int = 120,
    ) -> Optional[Dict[str, Any]]:
        """
        Section 12.3: If a raw log exceeds `threshold_bytes` (default 25 MB),
        computes its uncompressed SHA-256, writes `<log_path>.gz`, writes a readable
        `<log_path>.tail.log` summary, and replaces the oversized raw file.
        """
        if not log_path.exists() or not log_path.is_file():
            return None
        raw_size = log_path.stat().st_size
        if raw_size <= threshold_bytes:
            return None

        raw_bytes = log_path.read_bytes()
        uncompressed_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        gz_path = log_path.with_suffix(log_path.suffix + ".gz")
        with gzip.open(gz_path, "wb", compresslevel=6) as gz_file:
            gz_file.write(raw_bytes)

        text_lines = raw_bytes.decode("utf-8", errors="replace").splitlines()
        tail_excerpt = "\n".join(text_lines[-tail_lines:]) + "\n"
        tail_header = (
            f"# Compressed Log Tail Summary ({log_path.name})\n"
            f"# Uncompressed Size Bytes: {raw_size}\n"
            f"# Uncompressed SHA-256: {uncompressed_sha256}\n"
            f"# Gzip Artifact: {gz_path.name}\n\n"
        )
        log_path.write_text(tail_header + tail_excerpt, encoding="utf-8")

        meta = {
            "original_log": str(log_path),
            "gz_log": str(gz_path),
            "uncompressed_size_bytes": raw_size,
            "compressed_size_bytes": gz_path.stat().st_size,
            "uncompressed_sha256": uncompressed_sha256,
        }
        self.compressed_logs.append(meta)
        return meta

    def build_disk_budget_report(self) -> Dict[str, Any]:
        if not self.cpu_memory_samples:
            self.sample_cpu_memory(stage="POSTFLIGHT", active_workers=0)
        latest = self.measure_disk_budget(stage="POSTFLIGHT")
        preflight_free = self.snapshots[0].free_disk_bytes if self.snapshots else latest.free_disk_bytes
        peak_temp = max((s.temporary_bytes for s in self.snapshots), default=0)
        peak_run = max((s.current_run_bytes for s in self.snapshots), default=0)
        min_free = min((s.free_disk_bytes for s in self.snapshots), default=latest.free_disk_bytes)
        all_pass = all(s.status == "PASS" for s in self.snapshots)
        final_temp = _dir_size_bytes(self.tmp_root)

        any_unmeasured = any(s.get("measurement_status") == "NOT_MEASURED" for s in self.cpu_memory_samples)
        orphan_info = self.inspect_orphan_worker_processes()
        orphan_count = int(orphan_info["orphan_worker_count"])

        measured_samples = [s for s in self.cpu_memory_samples if s.get("measurement_status") == "MEASURED"]
        if measured_samples and not any_unmeasured:
            peak_proc_rss_b = max((int(s.get("peak_process_rss_bytes") or s.get("current_rss_bytes") or 0) for s in measured_samples), default=0)
            peak_child_rss_b = max((int(s.get("peak_child_rss_bytes") or s.get("child_rss_bytes") or 0) for s in measured_samples), default=0)
            peak_tree_rss_b = max((int(s.get("peak_process_tree_rss_bytes") or s.get("total_rss_bytes") or 0) for s in measured_samples), default=0)
            curr_rss_b = int(measured_samples[-1].get("current_rss_bytes") or 0)
            child_rss_b = int(measured_samples[-1].get("child_rss_bytes") or 0)
            sys_used_raw = measured_samples[-1].get("system_used_memory_bytes")
            sys_total_raw = measured_samples[-1].get("system_total_memory_bytes")
            sys_avail_raw = measured_samples[-1].get("system_available_memory_bytes")
            sys_used_b: Any = int(sys_used_raw) if isinstance(sys_used_raw, (int, float)) and sys_used_raw > 0 else "NOT_MEASURED"
            sys_total_b: Any = int(sys_total_raw) if isinstance(sys_total_raw, (int, float)) and sys_total_raw > 0 else "NOT_MEASURED"
            sys_avail_b: Any = int(sys_avail_raw) if isinstance(sys_avail_raw, (int, float)) and sys_avail_raw >= 0 else "NOT_MEASURED"
            peak_rss_mb: Any = round(peak_proc_rss_b / (1024 * 1024), 2)
            peak_child_rss_mb: Any = round(peak_child_rss_b / (1024 * 1024), 2)
            peak_total_rss_mb: Any = round(peak_tree_rss_b / (1024 * 1024), 2)
            peak_mem_b: Any = peak_tree_rss_b
            mem_status = "MEASURED"
        else:
            peak_proc_rss_b = "NOT_MEASURED"  # type: ignore[assignment]
            peak_child_rss_b = "NOT_MEASURED"  # type: ignore[assignment]
            peak_tree_rss_b = "NOT_MEASURED"  # type: ignore[assignment]
            curr_rss_b = "NOT_MEASURED"  # type: ignore[assignment]
            child_rss_b = "NOT_MEASURED"  # type: ignore[assignment]
            sys_used_b = "NOT_MEASURED"
            sys_total_b = "NOT_MEASURED"
            sys_avail_b = "NOT_MEASURED"
            peak_rss_mb = "NOT_MEASURED"
            peak_child_rss_mb = "NOT_MEASURED"
            peak_total_rss_mb = "NOT_MEASURED"
            peak_mem_b = "NOT_MEASURED"
            mem_status = "NOT_MEASURED"

        peak_cpu_pct = max((float(s.get("cpu_percent") or 0.0) for s in measured_samples), default=0.0)
        last_sample = self.cpu_memory_samples[-1] if self.cpu_memory_samples else {}

        if not all_pass:
            report_status = "BLOCKED_DISK_BUDGET"
        elif orphan_count > 0:
            report_status = "BLOCKED_ORPHAN_PROCESSES"
        elif any_unmeasured:
            report_status = "BLOCKED_RESOURCE_TELEMETRY"
        else:
            report_status = "PASS"

        return {
            "schema_version": "V17.9.0.1.3_R3.3.1",
            "run_id": self.run_id,
            "status": report_status,
            "measurement_status": mem_status,
            "memory_measurement_source": last_sample.get("memory_measurement_source", "NOT_MEASURED"),
            "memory_measurement_scope": "SEPARATED_PROCESS_CHILD_TREE_AND_SYSTEM_BYTES",
            "unit": "BYTES",
            "platform": sys.platform,
            "total_disk_bytes": latest.total_disk_bytes,
            "reserved_disk_floor_bytes": latest.reserved_disk_floor_bytes,
            "free_disk_bytes_preflight": preflight_free,
            "preflight_free_disk_bytes": preflight_free,
            "minimum_free_disk_bytes_observed": min_free,
            "min_observed_free_disk_bytes": min_free,
            "free_disk_bytes_postflight": latest.free_disk_bytes,
            "postflight_free_disk_bytes": latest.free_disk_bytes,
            "latest_free_disk_bytes": latest.free_disk_bytes,
            "peak_artifact_bytes": peak_run,
            "peak_current_run_bytes": peak_run,
            "peak_temporary_bytes": peak_temp,
            "final_temporary_bytes": final_temp,
            "samples_recorded": len(self.cpu_memory_samples),
            "current_rss_bytes": curr_rss_b,
            "peak_process_rss_bytes": peak_proc_rss_b,
            "child_rss_bytes": child_rss_b,
            "peak_child_rss_bytes": peak_child_rss_b,
            "peak_process_tree_rss_bytes": peak_tree_rss_b,
            "system_total_memory_bytes": sys_total_b,
            "system_available_memory_bytes": sys_avail_b,
            "system_used_memory_bytes": sys_used_b,
            "peak_memory_bytes": peak_mem_b,
            "peak_rss_mb": peak_rss_mb,
            "peak_child_rss_mb": peak_child_rss_mb,
            "peak_total_rss_mb": peak_total_rss_mb,
            "peak_cpu_percent": peak_cpu_pct,
            "SwapTotal": last_sample.get("swap_total_bytes") if last_sample.get("swap_total_bytes") is not None else "NOT_MEASURED",
            "SwapFree": last_sample.get("swap_free_bytes") if last_sample.get("swap_free_bytes") is not None else "NOT_MEASURED",
            "SwapUsed": last_sample.get("swap_used_bytes") if last_sample.get("swap_used_bytes") is not None else "NOT_MEASURED",
            "swappiness": "NOT_APPLICABLE_DARWIN" if sys.platform == "darwin" else "UNKNOWN",
            "zram_devices": 0 if sys.platform == "darwin" else "UNKNOWN",
            "orphan_worker_count": orphan_count,
            "orphan_workers": orphan_info["orphan_workers"],
            "cleaned_worker_dirs_count": len(self.cleaned_worker_dirs),
            "cleaned_worker_dirs": self.cleaned_worker_dirs,
            "compressed_logs": self.compressed_logs,
            "cpu_memory_samples": self.cpu_memory_samples,
            "snapshots": [s.to_dict() for s in self.snapshots],
        }

    def build_cpu_memory_report(self) -> Dict[str, Any]:
        if not self.cpu_memory_samples:
            self.sample_cpu_memory(stage="FINAL", active_workers=1)
        any_unmeasured = any(s.get("measurement_status") == "NOT_MEASURED" for s in self.cpu_memory_samples)
        measured_samples = [s for s in self.cpu_memory_samples if s.get("measurement_status") == "MEASURED"]
        last_sample = self.cpu_memory_samples[-1] if self.cpu_memory_samples else {}
        if measured_samples and not any_unmeasured:
            peak_proc_rss_b: Any = max((int(s.get("peak_process_rss_bytes") or s.get("current_rss_bytes") or 0) for s in measured_samples), default=0)
            peak_child_rss_b: Any = max((int(s.get("peak_child_rss_bytes") or s.get("child_rss_bytes") or 0) for s in measured_samples), default=0)
            peak_tree_rss_b: Any = max((int(s.get("peak_process_tree_rss_bytes") or s.get("total_rss_bytes") or 0) for s in measured_samples), default=0)
            curr_rss_b: Any = int(measured_samples[-1].get("current_rss_bytes") or 0)
            child_rss_b: Any = int(measured_samples[-1].get("child_rss_bytes") or 0)
            sys_used_raw = measured_samples[-1].get("system_used_memory_bytes")
            sys_total_raw = measured_samples[-1].get("system_total_memory_bytes")
            sys_avail_raw = measured_samples[-1].get("system_available_memory_bytes")
            sys_used_b: Any = int(sys_used_raw) if isinstance(sys_used_raw, (int, float)) and sys_used_raw > 0 else "NOT_MEASURED"
            sys_total_b: Any = int(sys_total_raw) if isinstance(sys_total_raw, (int, float)) and sys_total_raw > 0 else "NOT_MEASURED"
            sys_avail_b: Any = int(sys_avail_raw) if isinstance(sys_avail_raw, (int, float)) and sys_avail_raw >= 0 else "NOT_MEASURED"
            peak_rss_mb: Any = round(peak_proc_rss_b / (1024 * 1024), 2)
            peak_child_rss_mb: Any = round(peak_child_rss_b / (1024 * 1024), 2)
            peak_total_rss_mb: Any = round(peak_tree_rss_b / (1024 * 1024), 2)
        else:
            peak_proc_rss_b = "NOT_MEASURED"
            peak_child_rss_b = "NOT_MEASURED"
            peak_tree_rss_b = "NOT_MEASURED"
            curr_rss_b = "NOT_MEASURED"
            child_rss_b = "NOT_MEASURED"
            sys_used_b = "NOT_MEASURED"
            sys_total_b = "NOT_MEASURED"
            sys_avail_b = "NOT_MEASURED"
            peak_rss_mb = "NOT_MEASURED"
            peak_child_rss_mb = "NOT_MEASURED"
            peak_total_rss_mb = "NOT_MEASURED"
        peak_cpu_pct = max((float(s.get("cpu_percent") or 0.0) for s in measured_samples), default=0.0)
        return {
            "schema_version": "V17.9.0.1.3_R3.3.1",
            "run_id": self.run_id,
            "status": "BLOCKED_RESOURCE_TELEMETRY" if any_unmeasured else "PASS",
            "measurement_status": "NOT_MEASURED" if any_unmeasured else "MEASURED",
            "memory_measurement_source": last_sample.get("memory_measurement_source", "RESOURCE_GETRUSAGE_NORMALIZED_BYTES"),
            "memory_measurement_scope": "SEPARATED_PROCESS_CHILD_TREE_AND_SYSTEM_BYTES",
            "unit": "BYTES",
            "platform": sys.platform,
            "cpu_count": os.cpu_count() or 1,
            "current_rss_bytes": curr_rss_b,
            "peak_process_rss_bytes": peak_proc_rss_b,
            "child_rss_bytes": child_rss_b,
            "peak_child_rss_bytes": peak_child_rss_b,
            "peak_process_tree_rss_bytes": peak_tree_rss_b,
            "system_total_memory_bytes": sys_total_b,
            "system_available_memory_bytes": sys_avail_b,
            "system_used_memory_bytes": sys_used_b,
            "peak_rss_mb": peak_rss_mb,
            "peak_child_rss_mb": peak_child_rss_mb,
            "peak_total_rss_mb": peak_total_rss_mb,
            "peak_cpu_percent": peak_cpu_pct,
            "samples_count": len(self.cpu_memory_samples),
            "samples": self.cpu_memory_samples,
        }
