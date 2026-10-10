import copy
import os
import uuid
import time
import json
import hashlib
import threading
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import schemas
from lab.replay_engine import ReplayEngine, ReplayCancelledException

class ReplayQueueFullException(RuntimeError):
    """Raised when the replay background worker queue capacity is reached (mapped to HTTP 429)."""
    pass


class ReplayJobManager:
    """
    Background Replay Job Manager for Aurum Desk V12_2 & V12.3.
    Provides non-blocking job queuing, execution, progress tracking,
    cancellation checkpoint token, bounded queue, and secure artifact download with path traversal prevention.
    """
    _instance = None
    _lock = threading.Lock()
    MAX_ACTIVE_JOBS = 5
    MAX_JOB_RETENTION = 50

    def __init__(self, max_workers: int = 1):
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="replay-worker")
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.job_locks: Dict[str, threading.Lock] = {}

    @classmethod
    def get_instance(cls) -> "ReplayJobManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def submit_job(self, request: schemas.ReplayRunRequest) -> schemas.JobCreateResponse:
        now_ts = int(time.time() * 1000)

        with self._lock:
            # Check bounded queue limit
            active_jobs = [
                j for j in self.jobs.values()
                if isinstance(j, dict) and j.get("status") in ("QUEUED", "RUNNING", "VALIDATING", "CANCEL_REQUESTED")
            ]
            if len(active_jobs) >= self.MAX_ACTIVE_JOBS:
                raise ReplayQueueFullException(
                    f"Hàng đợi Replay đã đầy (tối đa {self.MAX_ACTIVE_JOBS} tác vụ đồng thời). Vui lòng đợi tác vụ hiện tại hoàn tất."
                )

            # Prune old terminal jobs if exceeding retention limit
            if len(self.jobs) >= self.MAX_JOB_RETENTION:
                terminal_jobs = sorted(
                    [j for j in self.jobs.values() if j["status"] in ("SUCCEEDED", "FAILED", "CANCELLED")],
                    key=lambda x: x["created_at"]
                )
                for j in terminal_jobs[:10]:
                    self.jobs.pop(j["job_id"], None)
                    self.job_locks.pop(j["job_id"], None)

            job_id = f"job-{uuid.uuid4().hex[:8]}"

            # Compute deterministic effective config and config hash
            effective_config = {
                "symbol": request.symbol,
                "strategy_variant": request.strategy_variant,
                "initial_equity": request.initial_equity,
                "risk_pct": request.risk_pct,
                "quality_risk_pct": getattr(request, "quality_risk_pct", None) or request.risk_pct,
                "quota_risk_pct": getattr(request, "quota_risk_pct", None) or 0.10,
                "leverage": request.leverage,
                "margin_mode": request.margin_mode,
                "fee_rate": request.fee_rate,
                "maker_fee_rate": getattr(request, "maker_fee_rate", 0.0002),
                "spread_multiplier": request.spread_multiplier,
                "slippage_multiplier": request.slippage_multiplier,
                "latency_ms": request.latency_ms,
                "start_ts": request.start_ts,
                "end_ts": request.end_ts,
                "warmup_days": request.warmup_days,
                "mode": request.mode
            }
            config_str = json.dumps(effective_config, sort_keys=True)
            config_hash = hashlib.sha256(config_str.encode("utf-8")).hexdigest()[:16]

            job_info = {
                "job_id": job_id,
                "status": "QUEUED",
                "progress_pct": 0.0,
                "current_phase": "QUEUED",
                "completed_events": 0,
                "total_events": 0,
                "effective_config": effective_config,
                "config_hash": config_hash,
                "quality_summary": None,
                "error_message": None,
                "created_at": now_ts,
                "updated_at": now_ts,
                "cancel_event": threading.Event(),
                "result": None,
                "artifacts_dir": None,
                "request": copy.deepcopy(request)
            }

            self.jobs[job_id] = job_info
            self.job_locks[job_id] = threading.Lock()

        # Submit to bounded thread pool
        self.executor.submit(self._execute_job, job_id)

        return schemas.JobCreateResponse(
            job_id=job_id,
            status="QUEUED",
            message="Tác vụ kiểm định Replay đã được đưa vào hàng đợi xử lý",
            created_at=now_ts,
            config_hash=config_hash
        )

    def _execute_job(self, job_id: str):
        job = self.jobs.get(job_id)
        if not job:
            return

        cancel_ev: threading.Event = job["cancel_event"]
        if cancel_ev.is_set():
            job["status"] = "CANCELLED"
            job["current_phase"] = "CANCELLED"
            job["updated_at"] = int(time.time() * 1000)
            return

        try:
            job["status"] = "RUNNING"
            job["current_phase"] = "VALIDATING"
            job["progress_pct"] = 10.0
            job["updated_at"] = int(time.time() * 1000)

            # Check cancel before running
            if cancel_ev.is_set():
                job["status"] = "CANCELLED"
                job["current_phase"] = "CANCELLED"
                job["updated_at"] = int(time.time() * 1000)
                return

            req: schemas.ReplayRunRequest = job["request"]
            job["current_phase"] = "RUNNING"
            job["progress_pct"] = 30.0
            job["updated_at"] = int(time.time() * 1000)

            # Run actual replay with cancel_check token
            res: schemas.ReplayRunResponse = ReplayEngine.run_replay(req, cancel_check=cancel_ev.is_set)

            if cancel_ev.is_set():
                job["status"] = "CANCELLED"
                job["current_phase"] = "CANCELLED"
                job["updated_at"] = int(time.time() * 1000)
                return

            job["current_phase"] = "EXPORTING"
            job["progress_pct"] = 90.0
            job["updated_at"] = int(time.time() * 1000)

            # Succeeded
            job["result"] = res
            job["artifacts_dir"] = res.artifacts_dir
            job["status"] = "SUCCEEDED"
            job["current_phase"] = "COMPLETED"
            job["progress_pct"] = 100.0
            job["completed_events"] = res.total_trades
            job["total_events"] = res.total_trades
            job["quality_summary"] = {
                "dataset_hash": res.dataset_hash,
                "dataset_type": res.dataset_type,
                "execution_fidelity": res.execution_fidelity,
                "signals_count": res.signals_count,
                "rejected_count": res.rejected_count,
                "total_trades": res.total_trades
            }
            job["updated_at"] = int(time.time() * 1000)

        except ReplayCancelledException:
            job["status"] = "CANCELLED"
            job["current_phase"] = "CANCELLED"
            job["error_message"] = "Tác vụ đã dừng theo yêu cầu hủy."
            job["updated_at"] = int(time.time() * 1000)
        except Exception as e:
            job["status"] = "FAILED"
            job["current_phase"] = "ERROR"
            job["error_message"] = f"Lỗi trong quá trình chạy Replay: {str(e)}"
            job["updated_at"] = int(time.time() * 1000)

    def get_job_status(self, job_id: str) -> Optional[schemas.JobStatusResponse]:
        job = self.jobs.get(job_id)
        if not job:
            return None

        return schemas.JobStatusResponse(
            job_id=job["job_id"],
            status=job["status"],
            progress_pct=job["progress_pct"],
            current_phase=job["current_phase"],
            completed_events=job["completed_events"],
            total_events=job["total_events"],
            effective_config=job["effective_config"],
            quality_summary=job["quality_summary"],
            error_message=job["error_message"],
            created_at=job["created_at"],
            updated_at=job["updated_at"]
        )

    def cancel_job(self, job_id: str) -> Optional[schemas.JobCancelResponse]:
        job = self.jobs.get(job_id)
        if not job:
            return None

        cancel_ev: threading.Event = job["cancel_event"]
        cancel_ev.set()

        if job["status"] == "QUEUED":
            job["status"] = "CANCELLED"
            job["current_phase"] = "CANCELLED"
            job["updated_at"] = int(time.time() * 1000)
            msg = "Tác vụ trong hàng đợi đã được hủy bỏ thành công"
        elif job["status"] in ("RUNNING", "VALIDATING"):
            job["status"] = "CANCEL_REQUESTED"
            job["current_phase"] = "CANCEL_REQUESTED"
            job["updated_at"] = int(time.time() * 1000)
            msg = "Đang yêu cầu dừng tác vụ..."
        elif job["status"] == "SUCCEEDED":
            msg = "Tác vụ đã hoàn thành trước khi yêu cầu hủy được ghi nhận"
        elif job["status"] == "CANCELLED":
            msg = "Tác vụ đã ở trạng thái hủy bỏ"
        else:
            msg = f"Tác vụ đang ở trạng thái {job['status']}"

        return schemas.JobCancelResponse(
            job_id=job_id,
            status=job["status"],
            message=msg
        )

    def get_job_result(self, job_id: str) -> Optional[schemas.ReplayRunResponse]:
        job = self.jobs.get(job_id)
        if not job:
            return None
        return job.get("result")

    def resolve_artifact_path(self, job_id: str, artifact_id: str) -> str:
        """
        D19/E12/P11: Resolves artifact path with strict path traversal & symlink escape protection.
        Prevents '../', URL encoding traversal, symlink escapes, or reading outside artifacts dir.
        """
        # 1. Sanitize artifact_id: must not contain path traversal characters
        cleaned_id = os.path.basename(artifact_id.strip())
        if cleaned_id != artifact_id.strip() or ".." in artifact_id or "/" in artifact_id or "\\" in artifact_id:
            raise PermissionError(f"Phát hiện hành vi truy cập tệp không hợp lệ: {artifact_id}")

        allowed_extensions = (".json", ".jsonl", ".csv", ".xlsx", ".log", ".html", ".txt")
        if not any(cleaned_id.endswith(ext) for ext in allowed_extensions):
            raise ValueError(f"Định dạng tệp không được hỗ trợ tải về: {cleaned_id}")

        job = self.jobs.get(job_id)
        if not job:
            raise FileNotFoundError(f"Không tìm thấy tác vụ (job_id): {job_id}")

        artifacts_dir = job.get("artifacts_dir")
        if not artifacts_dir or not os.path.exists(artifacts_dir):
            raise FileNotFoundError(f"Thư mục artifact của tác vụ {job_id} chưa sẵn sàng hoặc không tồn tại")

        resolved_artifacts_dir = Path(artifacts_dir).resolve()
        candidate = resolved_artifacts_dir / cleaned_id
        if not candidate.exists():
            raise FileNotFoundError(f"Không tìm thấy tệp artifact: {cleaned_id}")

        # Resolve real target strictly following any symlinks
        try:
            real_target = candidate.resolve(strict=True)
        except Exception:
            raise FileNotFoundError(f"Không thể định vị tệp artifact: {cleaned_id}")

        # Strict containment check against outside traversal and symlink escapes
        try:
            real_target.relative_to(resolved_artifacts_dir)
        except ValueError:
            raise PermissionError(f"Truy cập ngoài phạm vi thư mục cho phép (symlink escape bị chặn): {artifact_id}")

        if not real_target.is_file():
            raise PermissionError(f"Chỉ cho phép tải về tệp tin thông thường (regular file): {cleaned_id}")

        return str(real_target)
