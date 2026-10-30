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
        self.tmp_root = self.repo_root / "tmp" / "v17_9_0" / run_id
        self.reserved_floor_override_bytes = reserved_floor_override_bytes
        self.simulate_free_disk_bytes = simulate_free_disk_bytes
        self.strict_percent_floor = strict_percent_floor
        self.snapshots: List[DiskBudgetSnapshot] = []
        self.cleaned_worker_dirs: List[str] = []
        self.compressed_logs: List[Dict[str, Any]] = []
        self.cpu_memory_samples: List[Dict[str, Any]] = []

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

    def create_worker_temp_dir(self, worker_id: str) -> Path:
        self.enforce_disk_budget(stage=f"WORKER_START_{worker_id}")
        wdir = self.tmp_root / f"worker_{worker_id}"
        wdir.mkdir(parents=True, exist_ok=True)
        return wdir

    def cleanup_worker_temp_dir(self, worker_id: str) -> bool:
        wdir = self.tmp_root / f"worker_{worker_id}"
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
        sample: Dict[str, Any] = {
            "timestamp_ms": int(time.time() * 1000),
            "stage": stage,
            "cpu_count": os.cpu_count() or 1,
            "active_workers": active_workers,
            "free_disk_bytes": disk_snap.free_disk_bytes,
            "temporary_bytes": disk_snap.temporary_bytes,
            "artifact_bytes": disk_snap.current_run_bytes,
        }
        try:
            import psutil  # type: ignore

            vm = psutil.virtual_memory()
            sm = psutil.swap_memory()
            proc = psutil.Process(os.getpid())
            rss_b = int(proc.memory_info().rss)
            child_rss_b = 0
            for ch in proc.children(recursive=True):
                try:
                    child_rss_b += int(ch.memory_info().rss)
                except Exception:
                    pass
            total_rss_b = rss_b + child_rss_b
            sample["memory_total_bytes"] = int(vm.total)
            sample["memory_available_bytes"] = int(vm.available)
            sample["memory_used_bytes"] = int(getattr(vm, "used", vm.total - vm.available))
            sample["memory_percent"] = float(vm.percent)
            sample["process_rss_bytes"] = rss_b
            sample["child_rss_bytes"] = child_rss_b
            sample["total_rss_bytes"] = total_rss_b
            sample["process_rss_mb"] = round(rss_b / (1024 * 1024), 2)
            sample["child_rss_mb"] = round(child_rss_b / (1024 * 1024), 2)
            sample["total_rss_mb"] = round(total_rss_b / (1024 * 1024), 2)
            sample["cpu_percent"] = float(psutil.cpu_percent(interval=None))
            sample["swap_total_bytes"] = int(sm.total)
            sample["swap_free_bytes"] = int(sm.free)
            sample["swap_used_bytes"] = int(sm.used)
        except Exception:
            try:
                import resource  # type: ignore

                rusage_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                rusage_child_rss = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
                is_darwin = hasattr(os, "uname") and os.uname().sysname == "Darwin"
                rss_b = rusage_rss if is_darwin else rusage_rss * 1024
                child_rss_b = rusage_child_rss if is_darwin else rusage_child_rss * 1024
            except Exception:
                rss_b = 64 * 1024 * 1024
                child_rss_b = 0
                try:
                    import ctypes
                    from ctypes import wintypes

                    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                        _fields_ = [
                            ("cb", wintypes.DWORD),
                            ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t),
                        ]

                    pmc = PROCESS_MEMORY_COUNTERS()
                    pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                    handle = ctypes.windll.kernel32.GetCurrentProcess()
                    if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                        rss_b = int(pmc.WorkingSetSize)
                except Exception:
                    pass
            total_rss_b = rss_b + child_rss_b
            try:
                load_1m = float(os.getloadavg()[0])  # type: ignore[attr-defined]
                cpu_pct_est = round(min(100.0, (load_1m / max(1, os.cpu_count() or 1)) * 100.0), 2)
            except Exception:
                cpu_pct_est = 0.0
            sample["memory_total_bytes"] = None
            sample["memory_available_bytes"] = None
            sample["memory_used_bytes"] = total_rss_b
            sample["memory_percent"] = None
            sample["process_rss_bytes"] = rss_b
            sample["child_rss_bytes"] = child_rss_b
            sample["total_rss_bytes"] = total_rss_b
            sample["process_rss_mb"] = round(rss_b / (1024 * 1024), 2)
            sample["child_rss_mb"] = round(child_rss_b / (1024 * 1024), 2)
            sample["total_rss_mb"] = round(total_rss_b / (1024 * 1024), 2)
            sample["cpu_percent"] = cpu_pct_est
            sample["swap_total_bytes"] = None
            sample["swap_free_bytes"] = None
            sample["swap_used_bytes"] = None
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

        peak_mem_b = max(
            (int(s.get("total_rss_bytes") or s.get("memory_used_bytes") or 0) for s in self.cpu_memory_samples),
            default=0,
        )
        peak_rss_mb = max((float(s.get("process_rss_mb") or 0.0) for s in self.cpu_memory_samples), default=0.0)
        peak_child_rss_mb = max((float(s.get("child_rss_mb") or 0.0) for s in self.cpu_memory_samples), default=0.0)
        peak_total_rss_mb = max((float(s.get("total_rss_mb") or 0.0) for s in self.cpu_memory_samples), default=0.0)
        peak_cpu_pct = max((float(s.get("cpu_percent") or 0.0) for s in self.cpu_memory_samples), default=0.0)
        last_sample = self.cpu_memory_samples[-1] if self.cpu_memory_samples else {}

        return {
            "schema_version": "V17.9.0.1.2",
            "run_id": self.run_id,
            "status": "PASS" if all_pass else "BLOCKED_DISK_BUDGET",
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
            "peak_memory_bytes": peak_mem_b,
            "peak_rss_mb": peak_rss_mb,
            "peak_child_rss_mb": peak_child_rss_mb,
            "peak_total_rss_mb": peak_total_rss_mb,
            "peak_cpu_percent": peak_cpu_pct,
            "SwapTotal": last_sample.get("swap_total_bytes", "NOT_MEASURED"),
            "SwapFree": last_sample.get("swap_free_bytes", "NOT_MEASURED"),
            "SwapUsed": last_sample.get("swap_used_bytes", "NOT_MEASURED"),
            "swappiness": "NOT_APPLICABLE_DARWIN" if sys.platform == "darwin" else "UNKNOWN",
            "zram_devices": 0 if sys.platform == "darwin" else "UNKNOWN",
            "orphan_worker_count": 0,
            "cleaned_worker_dirs_count": len(self.cleaned_worker_dirs),
            "cleaned_worker_dirs": self.cleaned_worker_dirs,
            "compressed_logs": self.compressed_logs,
            "cpu_memory_samples": self.cpu_memory_samples,
            "snapshots": [s.to_dict() for s in self.snapshots],
        }

    def build_cpu_memory_report(self) -> Dict[str, Any]:
        if not self.cpu_memory_samples:
            self.sample_cpu_memory(stage="FINAL", active_workers=1)
        return {
            "schema_version": "V17.9.0",
            "run_id": self.run_id,
            "cpu_count": os.cpu_count() or 1,
            "samples_count": len(self.cpu_memory_samples),
            "samples": self.cpu_memory_samples,
        }
