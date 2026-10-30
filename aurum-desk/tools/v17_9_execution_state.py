"""
Aurum Desk V17.9.0 — Resumable Checkpoints & Interrupted-Run Recovery.

Enforces Section 13 requirements:
  - Atomic checkpoint writes to `docs/artifacts/v17_9_0/<RUN_ID>/execution_state.json`
  - Strict validation of process exit code == 0, non-empty valid XML/JSON/log artifacts, and `source_sha == HEAD`
  - Automatic invalidation of checkpoints when `source_sha` changes or artifacts are missing/corrupt
  - Safe resumption of interrupted runs with `resume_count` tracking
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def validate_artifact_file(path: Path, expected_kind: str = "AUTO") -> Tuple[bool, str]:
    """
    Validates that `path` exists, is non-empty, and parses cleanly as XML or JSON when applicable.
    """
    if not path.exists() or not path.is_file():
        return False, "ARTIFACT_MISSING"
    try:
        size = path.stat().st_size
    except OSError:
        return False, "ARTIFACT_STAT_ERROR"
    if size <= 0:
        return False, "ARTIFACT_EMPTY"

    kind = expected_kind.upper()
    if kind == "AUTO":
        if path.suffix.lower() == ".xml":
            kind = "XML"
        elif path.suffix.lower() == ".json":
            kind = "JSON"
        else:
            kind = "TEXT"

    try:
        if kind == "XML":
            tree = ET.parse(path)
            root = tree.getroot()
            if root is None:
                return False, "XML_ROOT_NONE"
        elif kind == "JSON":
            data = json.loads(path.read_text(encoding="utf-8"))
            if data is None:
                return False, "JSON_NULL"
        else:
            _ = path.read_bytes()[:256]
    except Exception as exc:
        return False, f"CORRUPT_{kind}: {exc}"

    return True, "VALID"


class ExecutionStateManager:
    """
    Section 13: Manages atomic persistence, verification, invalidation, and resumption
    of `docs/artifacts/v17_9_0/<RUN_ID>/execution_state.json`.
    """

    def __init__(
        self,
        *,
        run_id: str,
        source_sha: str,
        all_test_groups: Sequence[str],
        artifact_dir: Optional[Path] = None,
    ) -> None:
        self.run_id = run_id
        self.source_sha = source_sha
        self.all_test_groups = list(all_test_groups)
        self.artifact_dir = (
            Path(artifact_dir).resolve()
            if artifact_dir
            else (REPO_ROOT / "docs" / "artifacts" / "v17_9_0" / run_id).resolve()
        )
        self.state_path = self.artifact_dir / "execution_state.json"
        self.state: Dict[str, Any] = self._build_initial_state()

    def _build_initial_state(self) -> Dict[str, Any]:
        now_iso = _utc_iso_now()
        return {
            "schema_version": "V17.9.0",
            "run_id": self.run_id,
            "source_sha": self.source_sha,
            "started_at": now_iso,
            "updated_at": now_iso,
            "current_stage": "INITIALIZED",
            "completed_stages": [],
            "completed_test_groups": [],
            "pending_test_groups": list(self.all_test_groups),
            "failed_test_groups": [],
            "verified_artifacts": {},
            "group_records": {},
            "disk_budget_status": "PASS",
            "interrupted": False,
            "resume_count": 0,
            "invalidation_history": [],
        }

    def save_atomic(self) -> Path:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.state["updated_at"] = _utc_iso_now()
        tmp_path = self.state_path.with_suffix(".json.tmp")
        payload = json.dumps(self.state, indent=2, sort_keys=False)
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, self.state_path)
        return self.state_path

    def load_or_resume(self, current_head_sha: str) -> Dict[str, Any]:
        """
        Loads an existing `execution_state.json` if present, validates `source_sha == current_head_sha`,
        verifies all recorded artifacts for completed groups, and invalidates stale/corrupt entries.
        """
        if not self.state_path.exists():
            self.source_sha = current_head_sha
            self.state = self._build_initial_state()
            self.save_atomic()
            return {
                "resumed": False,
                "invalidated": False,
                "reason": "NEW_EXECUTION_STATE",
                "completed_test_groups": [],
                "pending_test_groups": list(self.all_test_groups),
            }

        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.source_sha = current_head_sha
            self.state = self._build_initial_state()
            self.state["invalidation_history"].append({
                "timestamp": _utc_iso_now(),
                "reason": f"CORRUPT_STATE_JSON: {exc}",
            })
            self.save_atomic()
            return {
                "resumed": False,
                "invalidated": True,
                "reason": "CORRUPT_STATE_JSON",
                "completed_test_groups": [],
                "pending_test_groups": list(self.all_test_groups),
            }

        recorded_sha = str(loaded.get("source_sha") or "")
        if recorded_sha != current_head_sha:
            prev_history = list(loaded.get("invalidation_history", []))
            prev_history.append({
                "timestamp": _utc_iso_now(),
                "reason": "CHECKPOINT_INVALIDATED_SOURCE_SHA_MISMATCH",
                "previous_source_sha": recorded_sha,
                "new_source_sha": current_head_sha,
            })
            self.source_sha = current_head_sha
            self.state = self._build_initial_state()
            self.state["invalidation_history"] = prev_history
            self.save_atomic()
            return {
                "resumed": False,
                "invalidated": True,
                "reason": "CHECKPOINT_INVALIDATED_SOURCE_SHA_MISMATCH",
                "completed_test_groups": [],
                "pending_test_groups": list(self.all_test_groups),
            }

        # Same source_sha: verify every completed group's artifacts
        self.state = loaded
        self.state["resume_count"] = int(self.state.get("resume_count", 0)) + 1
        self.state["interrupted"] = False

        valid_completed: List[str] = []
        invalidated_groups: List[str] = []
        group_records = dict(self.state.get("group_records", {}))
        verified_artifacts = dict(self.state.get("verified_artifacts", {}))

        for gid in list(self.state.get("completed_test_groups", [])):
            grec = group_records.get(gid) or {}
            if int(grec.get("exit_code", -1)) != 0:
                invalidated_groups.append(gid)
                continue
            artifact_paths = list(grec.get("artifacts", []))
            if not artifact_paths:
                invalidated_groups.append(gid)
                continue
            all_valid = True
            for rel_or_abs in artifact_paths:
                p = Path(rel_or_abs)
                if not p.is_absolute():
                    p = self.artifact_dir / p
                ok, _ = validate_artifact_file(p)
                if not ok:
                    all_valid = False
                    break
                expected_sha = (verified_artifacts.get(str(rel_or_abs)) or {}).get("sha256")
                if expected_sha and _sha256_file(p) != expected_sha:
                    all_valid = False
                    break
            if all_valid:
                valid_completed.append(gid)
            else:
                invalidated_groups.append(gid)
                group_records.pop(gid, None)

        pending = [g for g in self.all_test_groups if g not in valid_completed]
        self.state["completed_test_groups"] = valid_completed
        self.state["pending_test_groups"] = pending
        self.state["failed_test_groups"] = [
            g for g in self.state.get("failed_test_groups", []) if g not in valid_completed
        ]
        self.state["group_records"] = group_records
        if invalidated_groups:
            self.state.setdefault("invalidation_history", []).append({
                "timestamp": _utc_iso_now(),
                "reason": "CORRUPT_OR_MISSING_GROUP_ARTIFACTS",
                "invalidated_groups": invalidated_groups,
            })

        self.save_atomic()
        return {
            "resumed": True,
            "invalidated": len(invalidated_groups) > 0,
            "invalidated_groups": invalidated_groups,
            "reason": "RESUMED_FROM_VERIFIED_CHECKPOINT",
            "resume_count": self.state["resume_count"],
            "completed_test_groups": list(valid_completed),
            "pending_test_groups": list(pending),
        }

    def set_stage(self, stage: str, disk_budget_status: str = "PASS") -> None:
        self.state["current_stage"] = stage
        self.state["disk_budget_status"] = disk_budget_status
        self.save_atomic()

    def mark_stage_completed(self, stage: str) -> None:
        completed = list(self.state.get("completed_stages", []))
        if stage not in completed:
            completed.append(stage)
        self.state["completed_stages"] = completed
        self.state["current_stage"] = f"{stage}_COMPLETED"
        self.save_atomic()

    def mark_interrupted(self, reason: str = "INTERRUPTED") -> None:
        self.state["interrupted"] = True
        self.state["current_stage"] = reason
        self.save_atomic()

    def record_test_group_completion(
        self,
        *,
        group_id: str,
        exit_code: int,
        artifact_paths: Sequence[Path],
        current_head_sha: str,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """
        Section 13.2: Marks a test group COMPLETED only after:
          1. exit_code == 0
          2. expected JUnit/log/JSON artifacts exist and are valid XML/JSON/text
          3. source_sha == current_head_sha
        """
        if current_head_sha != self.source_sha:
            return False, "SOURCE_SHA_MISMATCH"

        if exit_code != 0:
            failed = list(self.state.get("failed_test_groups", []))
            if group_id not in failed:
                failed.append(group_id)
            self.state["failed_test_groups"] = failed
            self.save_atomic()
            return False, f"NON_ZERO_EXIT_{exit_code}"

        rel_list: List[str] = []
        verified_map = dict(self.state.get("verified_artifacts", {}))
        for p in artifact_paths:
            if p.is_absolute():
                abs_p = p.resolve()
            elif (self.artifact_dir / p).exists():
                abs_p = (self.artifact_dir / p).resolve()
            else:
                abs_p = p.resolve()
            ok, reason = validate_artifact_file(abs_p)
            if not ok:
                failed = list(self.state.get("failed_test_groups", []))
                if group_id not in failed:
                    failed.append(group_id)
                self.state["failed_test_groups"] = failed
                self.save_atomic()
                return False, reason
            try:
                rel_str = str(abs_p.relative_to(self.artifact_dir).as_posix())
            except ValueError:
                rel_str = str(abs_p)
            rel_list.append(rel_str)
            verified_map[rel_str] = {
                "sha256": _sha256_file(abs_p),
                "size_bytes": abs_p.stat().st_size,
                "verified_at": _utc_iso_now(),
            }

        completed = list(self.state.get("completed_test_groups", []))
        if group_id not in completed:
            completed.append(group_id)
        pending = [g for g in self.all_test_groups if g not in completed]
        failed = [g for g in self.state.get("failed_test_groups", []) if g != group_id]

        group_records = dict(self.state.get("group_records", {}))
        group_records[group_id] = {
            "group_id": group_id,
            "exit_code": exit_code,
            "completed_at": _utc_iso_now(),
            "artifacts": rel_list,
            "metrics": metrics or {},
        }

        self.state["completed_test_groups"] = completed
        self.state["pending_test_groups"] = pending
        self.state["failed_test_groups"] = failed
        self.state["verified_artifacts"] = verified_map
        self.state["group_records"] = group_records
        self.save_atomic()
        return True, "COMPLETED"

