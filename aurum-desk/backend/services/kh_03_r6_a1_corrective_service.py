"""
aurum-desk/backend/services/kh_03_r6_a1_corrective_service.py

AURUM DESK — KH-03.R6.A1 POST-REVIEW CORRECTIVE CLOSURE (P1-L / P1-M / P1-N)
SPEC VERSION: KH-03.R6.A1-CORRECTIVE-v1.0 | OFFLINE / LOOPBACK QA ONLY | EXTERNAL HARD-BLOCKED

Implements core corrective primitives directly integrated into S1..S4 & S6:
  - P1-L (Custody Single-Writer, Determinism & Atomicity):
      * `R6A1CrossProcessCustodyLock` — OS-level cross-process exclusive store lock + live owner
        metadata (`pid`, `create_time`, `authority_id`, `session_id`); refuses lock stealing while
        owner process is alive; requires explicit intent recovery after crash.
      * `R6A1CustodyWriterAuthority` — non-serializable process-registered single-writer capability
        token bound to `(store_dir, session_id, partition, epoch, owner_pid)`.
      * `R6A1CustodyChunkEnvelope` + `build_r6_a1_custody_envelope()` — immutable typed envelope
        produced by parallel transport/validation workers; workers NEVER write to custody directly.
      * `R6A1BoundedOrderedCustodyQueue` — bounded producer-consumer queue capping BOTH item count
        AND queued bytes, with dedicated head-slot reservation so the next required `(task_ordinal,
        chunk_ordinal)` can NEVER starve behind later-ordinal items!
      * `run_r6_a1_single_custody_writer()` — sole custody consumer committing strictly in canonical
        `(task_ordinal, chunk_ordinal, chunk_id, tie_key)` order with explicit END/abort protocol.
  - P1-M (Hard Disk Budget, Atomic Reservations & No Over-Allocation):
      * `compute_effective_disk_budget_contract()` — strict integer `min(...)` intersection across
        grant, immutable plan, registry/policy, and verified target volume headroom (NEVER `max()`!).
      * `R6A1DiskBudgetLedger` — durable SQLite (`BEGIN IMMEDIATE`, `synchronous=FULL`) + OS-locked
        atomic reservation authority enforcing `committed + active_reserved + uncertain <= cap`
        across BOTH session scope and global volume scope, accounting for raw wire, decoded,
        normalized UTF-8, temp staging overlap, filesystem 4 KiB block slack, journal/anchor,
        SQLite WAL/SHM, manifest, logs, and pre-reserved emergency audit floor!
  - P1-N (Exception-Safe Claim / Finalization / Quarantine):
      * `derive_r6_a1_terminal_disposition()` — separates durable ledger terminal state from
        acquisition outcome (durable `SPENT`/`FINALIZED` after `ABORTED` is NEVER acquisition PASS!).
  - M0 Real Baseline RED on Untouched `133980ac9e28e663df8284617bc4306791a0f86a` (`RED-L1`, `RED-L2`,
    `RED-M1`, `RED-N1a`, `RED-N1b` + clean positive control) via isolated `git show` temp overlay.
  - M6 Corrective Paired Fault Truth Engine (`L01..L12`, `M01..M14`, `N01..N15` = 41 pairs) &
    12 Mandatory Corrective Gates (`C01..C12`) kept strictly separate from original 36 A1 gates &
    0/4 external R6.B/C/D stages!

Documented Global Lock Order (STRICTLY ACYCLIC — NO NETWORK I/O UNDER LOCKS):
  1. `session_lifecycle_lock` (cancel_epoch / intake close / finalization ownership)
  2. `custody_writer_lock` (`R6CustodyJournal._tx_lock` + `R6A1CrossProcessCustodyLock`)
  3. `disk_budget_ledger_lock` (`R6A1DiskBudgetLedger` OS lock + SQLite `BEGIN IMMEDIATE`)
  4. `grant_ledger_lock` (`R6DurableGrantLedger._anchor_lock` + SQLite `BEGIN IMMEDIATE`)
  Rule: Never acquire a lower-numbered lock while holding a higher-numbered lock; never perform
  network/child subprocess I/O while holding Level 2, 3, or 4 locks!
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple
import uuid

import psutil

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from services.kh_03_r6_a1_trust_service import (
    A1_EXTERNAL_CAPTURE_ENABLED,
    _is_finite_num_a1,
    _is_strict_int_a1,
    _sha256_bytes_a1,
    _sha256_canonical_a1,
)
from services.kh_03_r6_durable_grant_service import (
    _atomic_write_json_fsync,
    _check_local_path_safety,
    _has_any_reparse_ancestor,
    _is_inside_directory,
    _strict_loads_r6,
)
from services.kh_03_r6_source_contract_service import MANDATORY_BRANCH_KH03_R6

SPEC_VERSION_KH03_R6_A1_CORRECTIVE = "KH-03.R6.A1-CORRECTIVE-v1.0"
SCHEMA_VERSION_KH03_R6_A1_CORRECTIVE = "kh_03_r6_a1_corrective_v1"

PRIOR_A1_REVIEWED_TESTED_SHA = "133980ac9e28e663df8284617bc4306791a0f86a"
PRIOR_A1_EVIDENCE_COMMIT_SHA = "faa3eac1852785a9aae131e83b01155aaec7f7b9"
PRIOR_A1_PUBLICATION_HEAD_SHA = "ff69871d466d0df9925e62f6dd336d633a369714"
PRIOR_A1_RUN_ID = "20261126T041244Z_133980ac9e28_1628"
PRIOR_A1_MANIFEST_26_SHA256 = "475f841fb1f1df91248fc0c945d12556937b0ef73c9d8604fa56fce839444dea"
PRIOR_A1_READBACK_28_SHA256 = "a9d8d332dced671b307a462b322a7add25834bef5db7bff3619d2c1f9d1b390d"
PRIOR_A1_LATEST_POINTER_SHA256 = "1cd514fdd68ae1c6a8b6c540ab5afff89a9fe77ec29e2ce07a596a1b9540df1e"

FS_BLOCK_ALIGNMENT_BYTES = 4096
DEFAULT_EMERGENCY_AUDIT_RESERVE_BYTES = 8192
DEFAULT_MANIFEST_AND_ANCHOR_RESERVE_BYTES = 16_384
DEFAULT_SQLITE_WAL_OVERHEAD_RESERVE_BYTES = 16_384
DEFAULT_PER_CHUNK_JOURNAL_INTENT_OVERHEAD_BYTES = 8192
MIN_SAFE_DISK_FLOOR_BYTES = 5 * (1024 ** 3)  # 5 GiB minimum floor
STRICT_PREFLIGHT_DISK_FLOOR_GIB = 20.5
STRICT_RUNTIME_DISK_FLOOR_GIB = 20.0
MIN_PREFLIGHT_RAM_FLOOR_MIB = 1024.0
HARD_RUNTIME_RAM_FLOOR_MIB = 512.0
MAX_INT64_SAFE_CORR = (1 << 62) - 1

CORRECTIVE_12_GATE_KEYS: Tuple[str, ...] = (
    "C01_BASELINE_RED_REPRODUCTION",
    "C02_SINGLE_WRITER_ATOMICITY_RESTART",
    "C03_DETERMINISTIC_W1_W2_W4_CUSTODY",
    "C04_BOUNDED_QUEUE_BACKPRESSURE_NO_LATE_WRITE",
    "C05_HARD_CAP_MIN_INTERSECTION_NO_ENLARGEMENT",
    "C06_ATOMIC_DISK_RESERVATIONS_PEAK_OVERHEAD",
    "C07_REAL_TELEMETRY_PRESSURE_CLEANUP_ENOSPC",
    "C08_CLAIM_EXCEPTION_DURABLE_TERMINAL_RECONCILE",
    "C09_FINALIZER_ISOLATION_CANCEL_OWNED_REAP",
    "C10_COMPOUND_CRASH_QUARANTINE_REPLAY_NO_SEAL",
    "C11_INDEPENDENT_VERIFIER_MUTATIONS_INHERITED",
    "C12_PROTECTED_SOURCE_PARITY_FROZEN_REMOTE",
)

CLASSIFICATION_PASS_OFFLINE_CORRECTIVE = (
    "ENGINEERING_PASS_OFFLINE_R6_A1_CORRECTIVE_L_M_N_CLOSED_"
    "CAPTURE_BLOCKED_NO_PROVEN_EDGE"
)

_CORR_REG_LOCK = threading.Lock()
_ACTIVE_WRITER_AUTHORITIES: Dict[str, "R6A1CustodyWriterAuthority"] = {}
_REGISTERED_ENVELOPE_TOKENS: Dict[str, "R6A1CustodyChunkEnvelope"] = {}


def _align_fs_blocks(n_bytes: int) -> int:
    if n_bytes <= 0:
        return 0
    return ((int(n_bytes) + FS_BLOCK_ALIGNMENT_BYTES - 1) // FS_BLOCK_ALIGNMENT_BYTES) * FS_BLOCK_ALIGNMENT_BYTES


def _volume_identity_for_path_corr(p: Path) -> str:
    res = p.resolve(strict=False)
    if os.name == "nt":
        drv = os.path.splitdrive(str(res))[0].upper()
        return drv or str(res.anchor).upper()
    cur = res
    while not cur.exists() and cur.parent != cur:
        cur = cur.parent
    try:
        return f"dev:{os.stat(cur).st_dev}"
    except OSError:
        return str(res.anchor)


def _disk_usage_for_path_corr(p: Path) -> Tuple[int, int, int]:
    cur = p.resolve(strict=False)
    while not cur.exists() and cur.parent != cur:
        cur = cur.parent
    du = shutil.disk_usage(cur)
    return int(du.total), int(du.used), int(du.free)


def _is_pid_alive_with_create_time(pid: int, expected_create_time: Optional[float]) -> bool:
    if not _is_strict_int_a1(pid) or int(pid) <= 0:
        return False
    try:
        proc = psutil.Process(int(pid))
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return False
        if expected_create_time is not None and _is_finite_num_a1(expected_create_time):
            actual_ct = float(proc.create_time())
            if abs(actual_ct - float(expected_create_time)) > 2.0:
                return False
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, OSError):
        return False


def _current_proc_create_time() -> float:
    try:
        return round(float(psutil.Process(os.getpid()).create_time()), 3)
    except Exception:
        return 0.0


# =====================================================================
# P1-L: Cross-Process OS Store Lock + Registered Writer Authority
# =====================================================================
class R6A1CrossProcessCustodyLock:
    """
    Exclusive OS file lock (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX) paired with
    durable live-owner metadata (`writer.lock.json`) for single-writer custody store ownership.
    NEVER steals or deletes a lock while the recorded owner PID/create_time is still alive!
    """

    def __init__(
        self,
        store_dir: Path,
        *,
        session_id: str,
        authority_id: str,
        partition: str,
        timeout_sec: float = 0.25,
    ) -> None:
        self.store_dir = Path(store_dir)
        self.lock_path = self.store_dir / ".custody_writer.oslock"
        self.meta_path = self.store_dir / "writer.lock.json"
        self.session_id = str(session_id)
        self.authority_id = str(authority_id)
        self.partition = str(partition)
        self.timeout_sec = max(0.0, float(timeout_sec))
        self._fd: Optional[int] = None
        self.acquired = False
        self.stale_owner_detected: Optional[Dict[str, Any]] = None

    def acquire(self) -> Dict[str, Any]:
        if self.acquired and self._fd is not None:
            return {"acquired": True, "status": "ALREADY_HELD", "stale_owner": None, "violations": []}
        self.store_dir.mkdir(parents=True, exist_ok=True)
        # First check existing writer.lock.json metadata BEFORE touching OS lock
        stale_info: Optional[Dict[str, Any]] = None
        if self.meta_path.is_file():
            try:
                old = _strict_loads_r6(self.meta_path.read_bytes(), max_bytes=16_384)
                if isinstance(old, Mapping):
                    old_pid = old.get("owner_pid")
                    old_ct = old.get("owner_create_time")
                    if _is_strict_int_a1(old_pid) and _is_pid_alive_with_create_time(int(old_pid), old_ct):
                        # Live process owns this store (even if same process holds another authority!)
                        return {
                            "acquired": False,
                            "status": "REJECTED_ACTIVE_WRITER_OWNERSHIP_CONFLICT",
                            "stale_owner": None,
                            "live_owner": dict(old),
                            "violations": [f"LIVE_CUSTODY_WRITER_OWNER_ACTIVE:pid={old_pid}:auth={old.get('authority_id')}"],
                        }
                    stale_info = dict(old)
            except Exception as exc:
                return {
                    "acquired": False,
                    "status": "REJECTED_CORRUPT_WRITER_LOCK_METADATA",
                    "stale_owner": None,
                    "violations": [f"CORRUPT_WRITER_LOCK_META:{exc}"],
                }

        deadline = time.monotonic() + self.timeout_sec
        fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"1")
        except OSError:
            pass

        locked = False
        while True:
            try:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)

        if not locked:
            try:
                os.close(fd)
            except OSError:
                pass
            return {
                "acquired": False,
                "status": "REJECTED_OS_WRITER_LOCK_HELD",
                "stale_owner": None,
                "violations": ["CUSTODY_OS_LOCK_HELD_BY_CONCURRENT_WRITER"],
            }

        self._fd = fd
        self.acquired = True
        self.stale_owner_detected = stale_info
        meta = {
            "schema_version": SCHEMA_VERSION_KH03_R6_A1_CORRECTIVE,
            "store_dir": str(self.store_dir.resolve(strict=False)),
            "volume_id": _volume_identity_for_path_corr(self.store_dir),
            "partition": self.partition,
            "session_id": self.session_id,
            "authority_id": self.authority_id,
            "owner_pid": int(os.getpid()),
            "owner_create_time": _current_proc_create_time(),
            "owner_thread_ident": int(threading.get_ident()),
            "acquired_utc_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
            "recovered_from_stale_owner": stale_info is not None,
        }
        _atomic_write_json_fsync(self.meta_path, meta)
        return {
            "acquired": True,
            "status": "WRITER_LOCK_ACQUIRED",
            "stale_owner": stale_info,
            "violations": [],
        }

    def release(self) -> None:
        if self.acquired:
            try:
                if self.meta_path.is_file():
                    cur = _strict_loads_r6(self.meta_path.read_bytes(), max_bytes=16_384)
                    if isinstance(cur, Mapping) and str(cur.get("authority_id") or "") == self.authority_id:
                        self.meta_path.unlink()
            except Exception:
                pass
        if self._fd is not None:
            try:
                if os.name == "nt":
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
        self.acquired = False


@dataclass(frozen=True)
class R6A1CustodyWriterAuthority:
    """
    Process-registered single-writer custody capability token.
    Caller-constructed instances without registration in `_ACTIVE_WRITER_AUTHORITIES` are REJECTED!
    """
    authority_id: str
    store_dir_resolved: str
    volume_id: str
    session_id: str
    run_id: str
    partition: str
    owner_pid: int
    owner_create_time: float
    cancel_epoch_snapshot: int
    deadline_monotonic: float
    token: str

    def to_redacted_dict(self) -> Dict[str, Any]:
        return {
            "authority_id": self.authority_id,
            "volume_id": self.volume_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "partition": self.partition,
            "owner_pid": self.owner_pid,
            "cancel_epoch_snapshot": self.cancel_epoch_snapshot,
        }


def register_custody_writer_authority_a1(
    *,
    store_dir: Path,
    session_id: str,
    run_id: str,
    partition: str,
    cancel_epoch_snapshot: int,
    deadline_monotonic: float,
) -> R6A1CustodyWriterAuthority:
    res_dir = str(Path(store_dir).resolve(strict=False))
    aid = f"cauth_{session_id}_{uuid.uuid4().hex[:10]}"
    tok = secrets.token_hex(24)
    auth = R6A1CustodyWriterAuthority(
        authority_id=aid,
        store_dir_resolved=res_dir,
        volume_id=_volume_identity_for_path_corr(Path(store_dir)),
        session_id=str(session_id),
        run_id=str(run_id),
        partition=str(partition),
        owner_pid=int(os.getpid()),
        owner_create_time=_current_proc_create_time(),
        cancel_epoch_snapshot=int(cancel_epoch_snapshot),
        deadline_monotonic=float(deadline_monotonic),
        token=tok,
    )
    with _CORR_REG_LOCK:
        _ACTIVE_WRITER_AUTHORITIES[tok] = auth
    return auth


def revoke_custody_writer_authority_a1(authority: Optional[R6A1CustodyWriterAuthority]) -> None:
    if authority is None or not isinstance(authority, R6A1CustodyWriterAuthority):
        return
    with _CORR_REG_LOCK:
        _ACTIVE_WRITER_AUTHORITIES.pop(authority.token, None)


def verify_custody_writer_authority_a1(
    authority: Any,
    *,
    expected_store_dir: Path,
    expected_partition: str,
    expected_session_id: Optional[str] = None,
    current_cancel_epoch: Optional[int] = None,
    check_deadline: bool = True,
) -> Tuple[bool, List[str]]:
    if not isinstance(authority, R6A1CustodyWriterAuthority):
        return False, ["INVALID_OR_MISSING_CUSTODY_WRITER_AUTHORITY"]
    with _CORR_REG_LOCK:
        reg = _ACTIVE_WRITER_AUTHORITIES.get(authority.token)
    if reg is None or reg is not authority:
        return False, ["FORGED_OR_REVOKED_CUSTODY_WRITER_AUTHORITY"]
    viols: List[str] = []
    if authority.store_dir_resolved != str(Path(expected_store_dir).resolve(strict=False)):
        viols.append("WRITER_AUTHORITY_STORE_DIR_MISMATCH")
    if authority.volume_id != _volume_identity_for_path_corr(Path(expected_store_dir)):
        viols.append("WRITER_AUTHORITY_VOLUME_SUBSTITUTION_DETECTED")
    if authority.partition != str(expected_partition):
        viols.append("WRITER_AUTHORITY_PARTITION_MISMATCH")
    if expected_session_id is not None and authority.session_id != str(expected_session_id):
        viols.append("WRITER_AUTHORITY_SESSION_MISMATCH")
    if authority.owner_pid != int(os.getpid()):
        viols.append("WRITER_AUTHORITY_PID_MISMATCH")
    if current_cancel_epoch is not None and int(current_cancel_epoch) != int(authority.cancel_epoch_snapshot):
        viols.append("WRITER_AUTHORITY_STALE_CANCEL_EPOCH")
    if check_deadline and time.monotonic() >= float(authority.deadline_monotonic):
        viols.append("WRITER_AUTHORITY_DEADLINE_EXPIRED")
    return len(viols) == 0, viols


# =====================================================================
# P1-L: Immutable Validated Chunk Envelope + Bounded Anti-Starvation Queue
# =====================================================================
@dataclass(frozen=True)
class R6A1CustodyChunkEnvelope:
    envelope_id: str
    session_id: str
    run_id: str
    partition: str
    task_ordinal: int
    task_id: str
    chunk_ordinal: int
    chunk_id: str
    tie_key: str
    attempt_id: str
    plan_digest: str
    raw_payload: bytes
    raw_sha256: str
    raw_bytes_len: int
    normalized_bytes: bytes
    norm_sha256: str
    norm_bytes_len: int
    norm_rows_count: int
    provenance_receipt: Any  # R6RawProvenanceReceipt
    disk_reservation_id: Optional[str]
    cancel_epoch: int
    envelope_digest: str
    envelope_token: str

    @property
    def order_key(self) -> Tuple[int, int, str, str]:
        return (int(self.task_ordinal), int(self.chunk_ordinal), str(self.chunk_id), str(self.tie_key))

    @property
    def total_queued_bytes(self) -> int:
        return int(self.raw_bytes_len) + int(self.norm_bytes_len)


def build_r6_a1_custody_envelope(
    *,
    session_id: str,
    run_id: str,
    partition: str,
    task_ordinal: int,
    task_id: str,
    chunk_ordinal: int,
    chunk_id: str,
    attempt_id: str,
    plan_digest: str,
    raw_payload: bytes,
    normalized_rows: Sequence[Mapping[str, Any]],
    provenance_receipt: Any,
    disk_reservation_id: Optional[str] = None,
    cancel_epoch: int = 0,
    tie_key: str = "",
) -> Dict[str, Any]:
    """
    Constructs an immutable, pre-serialized, process-registered `R6A1CustodyChunkEnvelope`
    with exact UTF-8 byte lengths & SHA-256 digests (NEVER `len(str(obj))` estimates!).
    """
    viols: List[str] = []
    if not _is_strict_int_a1(task_ordinal) or int(task_ordinal) < 0:
        viols.append("INVALID_TASK_ORDINAL")
    if not _is_strict_int_a1(chunk_ordinal) or int(chunk_ordinal) < 0:
        viols.append("INVALID_CHUNK_ORDINAL")
    if not isinstance(raw_payload, (bytes, bytearray)):
        viols.append("RAW_PAYLOAD_NOT_BYTES")
        raw_b = b""
    else:
        raw_b = bytes(raw_payload)
    if not str(chunk_id or "").strip() or not str(task_id or "").strip() or not str(session_id or "").strip():
        viols.append("MISSING_ENVELOPE_IDENTIFIERS")

    # Serialize normalized rows deterministically to exact UTF-8 bytes
    try:
        rows_list = list(normalized_rows)
        norm_b = json.dumps(rows_list, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except Exception as exc:
        viols.append(f"NORMALIZED_ROWS_SERIALIZE_ERR:{exc}")
        rows_list = []
        norm_b = b""

    if provenance_receipt is None or not getattr(provenance_receipt, "accepted_for_custody", False):
        viols.append("ENVELOPE_UNACCEPTED_PROVENANCE_RECEIPT")
    else:
        if getattr(provenance_receipt, "partition", None) != str(partition):
            viols.append("ENVELOPE_PROVENANCE_PARTITION_MISMATCH")
        if getattr(provenance_receipt, "session_id", None) != str(session_id):
            viols.append("ENVELOPE_PROVENANCE_SESSION_MISMATCH")
        if getattr(provenance_receipt, "raw_payload_sha256", None) != _sha256_bytes_a1(raw_b):
            viols.append("ENVELOPE_RAW_SHA256_MISMATCH_PROVENANCE")
        if getattr(provenance_receipt, "raw_payload_bytes", None) != len(raw_b):
            viols.append("ENVELOPE_RAW_BYTES_LEN_MISMATCH_PROVENANCE")

    if viols:
        return {"valid": False, "status": "REJECTED_CUSTODY_ENVELOPE", "envelope": None, "violations": viols}

    raw_sha = _sha256_bytes_a1(raw_b)
    norm_sha = _sha256_bytes_a1(norm_b)
    env_id = f"cenv_{session_id}_{int(task_ordinal):03d}_{int(chunk_ordinal):03d}_{uuid.uuid4().hex[:8]}"
    core = {
        "envelope_id": env_id,
        "session_id": str(session_id),
        "run_id": str(run_id),
        "partition": str(partition),
        "task_ordinal": int(task_ordinal),
        "task_id": str(task_id),
        "chunk_ordinal": int(chunk_ordinal),
        "chunk_id": str(chunk_id),
        "tie_key": str(tie_key or f"{int(task_ordinal):04d}:{int(chunk_ordinal):04d}:{chunk_id}"),
        "attempt_id": str(attempt_id),
        "plan_digest": str(plan_digest),
        "raw_sha256": raw_sha,
        "raw_bytes_len": len(raw_b),
        "norm_sha256": norm_sha,
        "norm_bytes_len": len(norm_b),
        "norm_rows_count": len(rows_list),
        "receipt_id": str(getattr(provenance_receipt, "receipt_id", "")),
        "disk_reservation_id": str(disk_reservation_id or ""),
        "cancel_epoch": int(cancel_epoch),
    }
    env_dig = _sha256_canonical_a1(core)
    tok = secrets.token_hex(24)
    env = R6A1CustodyChunkEnvelope(
        envelope_id=env_id,
        session_id=core["session_id"],
        run_id=core["run_id"],
        partition=core["partition"],
        task_ordinal=core["task_ordinal"],
        task_id=core["task_id"],
        chunk_ordinal=core["chunk_ordinal"],
        chunk_id=core["chunk_id"],
        tie_key=core["tie_key"],
        attempt_id=core["attempt_id"],
        plan_digest=core["plan_digest"],
        raw_payload=raw_b,
        raw_sha256=raw_sha,
        raw_bytes_len=len(raw_b),
        normalized_bytes=norm_b,
        norm_sha256=norm_sha,
        norm_bytes_len=len(norm_b),
        norm_rows_count=len(rows_list),
        provenance_receipt=provenance_receipt,
        disk_reservation_id=disk_reservation_id,
        cancel_epoch=int(cancel_epoch),
        envelope_digest=env_dig,
        envelope_token=tok,
    )
    with _CORR_REG_LOCK:
        _REGISTERED_ENVELOPE_TOKENS[tok] = env
    return {"valid": True, "status": "CUSTODY_ENVELOPE_BUILT", "envelope": env, "violations": []}


def verify_custody_envelope_a1(envelope: Any) -> Tuple[bool, List[str]]:
    if not isinstance(envelope, R6A1CustodyChunkEnvelope):
        return False, ["INVALID_CUSTODY_ENVELOPE_TYPE"]
    with _CORR_REG_LOCK:
        reg = _REGISTERED_ENVELOPE_TOKENS.get(envelope.envelope_token)
    if reg is None or reg is not envelope:
        return False, ["FORGED_OR_UNREGISTERED_CUSTODY_ENVELOPE"]
    if _sha256_bytes_a1(envelope.raw_payload) != envelope.raw_sha256 or len(envelope.raw_payload) != envelope.raw_bytes_len:
        return False, ["ENVELOPE_RAW_INTEGRITY_DRIFT"]
    if _sha256_bytes_a1(envelope.normalized_bytes) != envelope.norm_sha256 or len(envelope.normalized_bytes) != envelope.norm_bytes_len:
        return False, ["ENVELOPE_NORM_INTEGRITY_DRIFT"]
    return True, []


class R6A1BoundedOrderedCustodyQueue:
    """
    Bounded, backpressure-enforced, anti-starvation deterministic ordering queue between parallel
    transport/validation producers and the SINGLE custody writer thread!

    Key properties (P1-L / F01):
      - Enforces BOTH `max_items` AND `max_bytes` strictly.
      - Anti-starvation head-slot reservation: tracks `ordered_task_ordinals`; reserves 1 item slot
        exclusively for the current head `task_ordinal` so later-ordinal tasks can NEVER fill the
        queue and deadlock/starve the next required task ordinal!
      - Yields items to the single writer strictly in canonical `(task_ordinal, chunk_ordinal,
        chunk_id, tie_key)` order, emitting all chunks of task ordinal `k` before advancing to `k+1`
        once producer `k` calls `mark_task_complete(task_ordinal)`.
      - Explicit `close_intake()` (normal END) and `abort(reason)` (immediate cancel wakeup of ALL
        blocked producers and consumer -> zero deadlock on producer/writer failure!).
    """

    def __init__(
        self,
        *,
        ordered_task_ordinals: Sequence[int],
        max_items: int = 8,
        max_bytes: int = 2 * 1024 * 1024,
        head_reserved_bytes: Optional[int] = None,
    ) -> None:
        if not _is_strict_int_a1(max_items) or int(max_items) < 2:
            raise ValueError("ORDERED_QUEUE_MAX_ITEMS_MUST_BE_AT_LEAST_2")
        if not _is_strict_int_a1(max_bytes) or int(max_bytes) < 1024:
            raise ValueError("ORDERED_QUEUE_MAX_BYTES_TOO_SMALL")
        self.max_items = int(max_items)
        self.max_bytes = int(max_bytes)
        self.head_reserved_bytes = int(head_reserved_bytes) if head_reserved_bytes is not None else max(1024, self.max_bytes // 2)
        self._task_ordinals: Tuple[int, ...] = tuple(sorted(set(int(x) for x in ordered_task_ordinals)))
        self._head_idx = 0
        self._completed_tasks: Set[int] = set()
        self._by_task: Dict[int, List[Tuple[Tuple[int, int, str, str], R6A1CustodyChunkEnvelope]]] = {
            ord_i: [] for ord_i in self._task_ordinals
        }
        self._current_items = 0
        self._current_bytes = 0
        self._peak_items = 0
        self._peak_bytes = 0
        self._backpressure_waits = 0
        self._enqueued_total = 0
        self._dequeued_total = 0
        self._intake_closed = False
        self._aborted = False
        self._abort_reason: Optional[str] = None
        self._cond = threading.Condition(threading.Lock())

    def _current_head_task_ordinal(self) -> Optional[int]:
        if self._head_idx < len(self._task_ordinals):
            return self._task_ordinals[self._head_idx]
        return None

    def put_envelope(
        self,
        envelope: R6A1CustodyChunkEnvelope,
        *,
        deadline_monotonic: float,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        ok_e, e_viols = verify_custody_envelope_a1(envelope)
        if not ok_e:
            return {"enqueued": False, "status": "REJECTED_INVALID_ENVELOPE", "violations": e_viols}

        sz = envelope.total_queued_bytes
        if sz > self.max_bytes:
            return {
                "enqueued": False,
                "status": "REJECTED_ENVELOPE_EXCEEDS_QUEUE_BYTE_CAP",
                "violations": [f"ENVELOPE_BYTES_EXCEED_QUEUE_CAP:{sz}>{self.max_bytes}"],
            }

        with self._cond:
            if envelope.task_ordinal not in self._by_task:
                return {
                    "enqueued": False,
                    "status": "REJECTED_UNREGISTERED_TASK_ORDINAL",
                    "violations": [f"UNKNOWN_TASK_ORDINAL:{envelope.task_ordinal}"],
                }
            if envelope.task_ordinal in self._completed_tasks:
                return {
                    "enqueued": False,
                    "status": "REJECTED_LATE_ENVELOPE_AFTER_TASK_COMPLETE",
                    "violations": [f"TASK_ALREADY_COMPLETED:{envelope.task_ordinal}"],
                }

            waited = False
            while True:
                if self._aborted or (cancel_event is not None and cancel_event.is_set()):
                    return {"enqueued": False, "status": "ABORTED_QUEUE_CANCELLED", "violations": [self._abort_reason or "QUEUE_ABORTED"]}
                if self._intake_closed:
                    return {"enqueued": False, "status": "REJECTED_QUEUE_INTAKE_CLOSED", "violations": ["QUEUE_INTAKE_CLOSED"]}

                head_ord = self._current_head_task_ordinal()
                is_head = (head_ord is not None and envelope.task_ordinal == head_ord)
                # Non-head items leave 1 reserved head item slot AND head byte headroom so head NEVER starves!
                eff_max_items = self.max_items if is_head else (self.max_items - 1)
                eff_max_bytes = self.max_bytes if is_head else max(0, self.max_bytes - self.head_reserved_bytes)
                # Ensure a single non-head item can still fit when queue is empty and item <= max_bytes
                if not is_head and self._current_items == 0 and eff_max_bytes < sz <= self.max_bytes and eff_max_items >= 1:
                    eff_max_bytes = self.max_bytes

                if (self._current_items + 1 <= eff_max_items) and (self._current_bytes + sz <= eff_max_bytes):
                    break

                rem = float(deadline_monotonic) - time.monotonic()
                if rem <= 0.0:
                    return {"enqueued": False, "status": "ABORTED_QUEUE_BACKPRESSURE_DEADLINE", "violations": ["QUEUE_PUT_DEADLINE_EXPIRED"]}
                if not waited:
                    self._backpressure_waits += 1
                    waited = True
                self._cond.wait(timeout=min(0.05, rem))

            heapq.heappush(self._by_task[envelope.task_ordinal], (envelope.order_key, envelope))
            self._current_items += 1
            self._current_bytes += sz
            self._peak_items = max(self._peak_items, self._current_items)
            self._peak_bytes = max(self._peak_bytes, self._current_bytes)
            self._enqueued_total += 1
            self._cond.notify_all()
            return {"enqueued": True, "status": "ENQUEUED", "queued_items": self._current_items, "queued_bytes": self._current_bytes, "violations": []}

    def mark_task_complete(self, task_ordinal: int) -> None:
        with self._cond:
            self._completed_tasks.add(int(task_ordinal))
            self._cond.notify_all()

    def close_intake(self) -> None:
        with self._cond:
            self._intake_closed = True
            self._cond.notify_all()

    def abort(self, reason: str = "SESSION_ABORTED") -> List[R6A1CustodyChunkEnvelope]:
        with self._cond:
            self._aborted = True
            self._intake_closed = True
            self._abort_reason = str(reason)
            dropped: List[R6A1CustodyChunkEnvelope] = []
            for ord_i in self._task_ordinals:
                heap = self._by_task[ord_i]
                while heap:
                    _, env = heapq.heappop(heap)
                    dropped.append(env)
            self._current_items = 0
            self._current_bytes = 0
            self._cond.notify_all()
            return dropped

    def pop_next_ordered(
        self,
        *,
        deadline_monotonic: float,
        cancel_event: Optional[threading.Event] = None,
    ) -> Tuple[str, Optional[R6A1CustodyChunkEnvelope]]:
        """
        Consumer API for the SINGLE custody writer thread.
        Returns:
          ("ITEM", envelope) | ("END", None) | ("ABORTED", None)
        """
        with self._cond:
            while True:
                if self._aborted or (cancel_event is not None and cancel_event.is_set()):
                    return "ABORTED", None

                # Advance head past completed empty task ordinals strictly in order
                while self._head_idx < len(self._task_ordinals):
                    head_ord = self._task_ordinals[self._head_idx]
                    heap = self._by_task[head_ord]
                    if heap:
                        _, env = heapq.heappop(heap)
                        self._current_items -= 1
                        self._current_bytes -= env.total_queued_bytes
                        self._dequeued_total += 1
                        self._cond.notify_all()
                        return "ITEM", env
                    if head_ord in self._completed_tasks or self._intake_closed:
                        self._head_idx += 1
                        self._cond.notify_all()
                        continue
                    break

                if self._head_idx >= len(self._task_ordinals):
                    if self._intake_closed or len(self._completed_tasks) >= len(self._task_ordinals):
                        return "END", None

                rem = float(deadline_monotonic) - time.monotonic()
                if rem <= 0.0:
                    self._aborted = True
                    self._abort_reason = "WRITER_DEADLINE_EXPIRED"
                    self._cond.notify_all()
                    return "ABORTED", None
                self._cond.wait(timeout=min(0.05, rem))

    def snapshot_stats(self) -> Dict[str, Any]:
        with self._cond:
            return {
                "max_items": self.max_items,
                "max_bytes": self.max_bytes,
                "current_items": self._current_items,
                "current_bytes": self._current_bytes,
                "peak_items": self._peak_items,
                "peak_bytes": self._peak_bytes,
                "backpressure_waits": self._backpressure_waits,
                "enqueued_total": self._enqueued_total,
                "dequeued_total": self._dequeued_total,
                "intake_closed": self._intake_closed,
                "aborted": self._aborted,
                "abort_reason": self._abort_reason,
                "completed_tasks_count": len(self._completed_tasks),
                "planned_tasks_count": len(self._task_ordinals),
            }


def run_r6_a1_single_custody_writer(
    *,
    queue: R6A1BoundedOrderedCustodyQueue,
    custody: Any,  # R6CustodyJournal
    writer_authority: R6A1CustodyWriterAuthority,
    disk_budget_ledger: Optional["R6A1DiskBudgetLedger"],
    deadline_monotonic: float,
    cancel_event: threading.Event,
    get_cancel_epoch: Callable[[], int],
    on_writer_error: Optional[Callable[[str], None]] = None,
    per_commit_hook: Optional[Callable[[R6A1CustodyChunkEnvelope], None]] = None,
) -> Dict[str, Any]:
    """
    F01: Dedicated SINGLE custody writer loop.
    Drains `queue` strictly in canonical `(task_ordinal, chunk_ordinal, chunk_id, tie_key)` order,
    commits each chunk through `custody.commit_r6_custody_transaction(...)`, and guarantees zero
    late writes after cancel/deadline/error!
    """
    committed: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    quarantined: List[Dict[str, Any]] = []
    violations: List[str] = []
    writer_thread_ident = int(threading.get_ident())
    writer_pid = int(os.getpid())
    status = "WRITER_COMPLETED"

    try:
        while True:
            tag, env = queue.pop_next_ordered(deadline_monotonic=deadline_monotonic, cancel_event=cancel_event)
            if tag == "END":
                break
            if tag == "ABORTED" or env is None:
                status = "WRITER_ABORTED"
                break

            # Strict pre-commit cancel epoch & deadline guard (no late commit!)
            if cancel_event.is_set() or int(get_cancel_epoch()) != int(writer_authority.cancel_epoch_snapshot) or time.monotonic() >= float(deadline_monotonic):
                violations.append("LATE_WRITE_BLOCKED_AFTER_CANCEL_OR_DEADLINE")
                status = "WRITER_ABORTED_CANCEL_OR_DEADLINE"
                queue.abort("LATE_WRITE_BLOCKED")
                break

            if per_commit_hook is not None:
                per_commit_hook(env)

            res = custody.commit_r6_custody_transaction(
                envelope=env,
                writer_authority=writer_authority,
                disk_budget_ledger=disk_budget_ledger,
                current_cancel_epoch=int(get_cancel_epoch()),
            )
            term = str(res.get("terminal_status") or ("COMMITTED" if res.get("persisted") else "REJECTED"))
            if term == "COMMITTED" and res.get("persisted") is True:
                committed.append({
                    "seq": int(res["seq"]),
                    "task_ordinal": int(env.task_ordinal),
                    "task_id": env.task_id,
                    "chunk_ordinal": int(env.chunk_ordinal),
                    "chunk_id": env.chunk_id,
                    "attempt_id": env.attempt_id,
                    "receipt_id": env.provenance_receipt.receipt_id,
                    "provenance_class": env.provenance_receipt.provenance_class,
                    "raw_sha256": res["raw_sha256"],
                    "raw_bytes_len": env.raw_bytes_len,
                    "norm_sha256": res["norm_sha256"],
                    "norm_bytes_len": env.norm_bytes_len,
                    "norm_rows_count": env.norm_rows_count,
                    "entry_hash": res["entry_hash"],
                    "content_identity_hash": res.get("content_identity_hash"),
                    "settlement_generation": int(env.provenance_receipt.settlement_generation),
                    "disk_reservation_id": env.disk_reservation_id,
                })
            elif term == "QUARANTINED":
                quarantined.append({"chunk_id": env.chunk_id, "status": res.get("status"), "violations": list(res.get("violations") or ())})
                violations.extend(res.get("violations") or [str(res.get("status"))])
                status = "WRITER_QUARANTINED"
                cancel_event.set()
                queue.abort(f"WRITER_QUARANTINED:{res.get('status')}")
                if on_writer_error is not None:
                    on_writer_error(f"WRITER_QUARANTINED:{res.get('status')}")
                break
            else:
                rejected.append({"chunk_id": env.chunk_id, "status": res.get("status"), "violations": list(res.get("violations") or ())})
                violations.extend(res.get("violations") or [str(res.get("status"))])
                status = "WRITER_REJECTED_CHUNK"
                cancel_event.set()
                queue.abort(f"WRITER_REJECTED:{res.get('status')}")
                if on_writer_error is not None:
                    on_writer_error(f"WRITER_REJECTED:{res.get('status')}")
                break
    except BaseException as exc:
        status = "WRITER_EXCEPTION"
        violations.append(f"SINGLE_WRITER_EXCEPTION:{type(exc).__name__}:{exc}")
        cancel_event.set()
        queue.abort(f"WRITER_EXCEPTION:{type(exc).__name__}")
        if on_writer_error is not None:
            on_writer_error(f"SINGLE_WRITER_EXCEPTION:{type(exc).__name__}:{exc}")

    return {
        "ok": (status == "WRITER_COMPLETED" and not violations and not rejected and not quarantined),
        "status": status,
        "writer_pid": writer_pid,
        "writer_thread_ident": writer_thread_ident,
        "authority_id": writer_authority.authority_id,
        "committed_chunks": committed,
        "rejected_chunks": rejected,
        "quarantined_chunks": quarantined,
        "violations": violations,
    }


# =====================================================================
# P1-M: Hard Cap Intersection Contract (NEVER `max()` Enlargement!)
# =====================================================================
def compute_effective_disk_budget_contract(
    *,
    grant_caps: Mapping[str, Any],
    plan_caps: Mapping[str, Any],
    target_path: Path,
    repo_root: Optional[Path] = None,
    oos_roots: Sequence[Path] = (),
    policy_max_chunk_bytes: Optional[int] = None,
    policy_max_total_disk_bytes: Optional[int] = None,
    policy_max_ram_queue_bytes: Optional[int] = None,
    override_max_chunk_bytes: Optional[int] = None,
    override_max_total_bytes: Optional[int] = None,
    emergency_audit_reserve_bytes: int = DEFAULT_EMERGENCY_AUDIT_RESERVE_BYTES,
    manifest_and_anchor_reserve_bytes: int = DEFAULT_MANIFEST_AND_ANCHOR_RESERVE_BYTES,
    sqlite_wal_overhead_reserve_bytes: int = DEFAULT_SQLITE_WAL_OVERHEAD_RESERVE_BYTES,
    volume_free_bytes_override: Optional[int] = None,
    volume_total_bytes_override: Optional[int] = None,
    strict_preflight_floor_gib: float = STRICT_PREFLIGHT_DISK_FLOOR_GIB,
) -> Dict[str, Any]:
    """
    F05: Computes strict effective disk/chunk/network/RAM caps as the MINIMUM across ALL applicable
    signed grant caps, immutable plan caps, policy caps, and verified target volume capacity!
    NEVER uses `max()` to enlarge a narrower cap! Rejects bool, float, NaN/Inf, negative, missing,
    overflow, or symlink/junction/volume substitution BEFORE claim or I/O!
    """
    viols: List[str] = []

    # 1. Validate path & volume safety (detects symlink/junction/reparse/OOS/Git repo BEFORE I/O)
    path_viols = _check_local_path_safety(target_path, repo_root=repo_root, must_exist=False)
    viols.extend(path_viols)
    if _has_any_reparse_ancestor(target_path):
        if "SYMLINK_OR_JUNCTION_FORBIDDEN" not in " ".join(viols):
            viols.append("SYMLINK_OR_JUNCTION_FORBIDDEN")
    for oos_r in oos_roots:
        if _is_inside_directory(target_path, oos_r):
            viols.append("TARGET_INSIDE_OOS_ROOT_FORBIDDEN")

    def _req_strict_pos_int(src_map: Any, key: str, label: str) -> Optional[int]:
        if not isinstance(src_map, Mapping) or key not in src_map:
            viols.append(f"MISSING_REQUIRED_CAP:{label}.{key}")
            return None
        v = src_map[key]
        if not _is_strict_int_a1(v) or int(v) <= 0 or int(v) > MAX_INT64_SAFE_CORR:
            viols.append(f"INVALID_CAP_INT:{label}.{key}:{v!r}")
            return None
        return int(v)

    def _opt_strict_pos_int(v: Any, label: str) -> Optional[int]:
        if v is None:
            return None
        if not _is_strict_int_a1(v) or int(v) <= 0 or int(v) > MAX_INT64_SAFE_CORR:
            viols.append(f"INVALID_OPTIONAL_CAP_INT:{label}:{v!r}")
            return None
        return int(v)

    g_pay = _req_strict_pos_int(grant_caps, "payload_bytes", "grant")
    g_dec = _req_strict_pos_int(grant_caps, "decoded_bytes", "grant")
    g_dsk = _req_strict_pos_int(grant_caps, "disk_bytes", "grant")

    p_pay = _req_strict_pos_int(plan_caps, "payload_bytes", "plan")
    p_dec = _req_strict_pos_int(plan_caps, "decoded_bytes", "plan")
    p_dsk = _req_strict_pos_int(plan_caps, "disk_bytes", "plan")

    pol_chunk = _opt_strict_pos_int(policy_max_chunk_bytes, "policy_max_chunk_bytes")
    pol_disk = _opt_strict_pos_int(policy_max_total_disk_bytes, "policy_max_total_disk_bytes")
    pol_ram = _opt_strict_pos_int(policy_max_ram_queue_bytes, "policy_max_ram_queue_bytes")
    ov_chunk = _opt_strict_pos_int(override_max_chunk_bytes, "override_max_chunk_bytes")
    ov_total = _opt_strict_pos_int(override_max_total_bytes, "override_max_total_bytes")
    emerg_r = _opt_strict_pos_int(emergency_audit_reserve_bytes, "emergency_audit_reserve_bytes") or 0
    man_r = _opt_strict_pos_int(manifest_and_anchor_reserve_bytes, "manifest_and_anchor_reserve_bytes") or 0
    wal_r = _opt_strict_pos_int(sqlite_wal_overhead_reserve_bytes, "sqlite_wal_overhead_reserve_bytes") or 0

    if g_pay is not None and p_pay is not None and p_pay > g_pay:
        viols.append(f"PLAN_PAYLOAD_EXCEEDS_GRANT:{p_pay}>{g_pay}")
    if g_dec is not None and p_dec is not None and p_dec > g_dec:
        viols.append(f"PLAN_DECODED_EXCEEDS_GRANT:{p_dec}>{g_dec}")
    if g_dsk is not None and p_dsk is not None and p_dsk > g_dsk:
        viols.append(f"PLAN_DISK_EXCEEDS_GRANT:{p_dsk}>{g_dsk}")

    # Reject any caller override attempting to enlarge beyond signed grant/plan cap!
    if ov_chunk is not None and p_pay is not None and ov_chunk > p_pay:
        viols.append(f"OVERRIDE_CHUNK_CAP_EXCEEDS_PLAN_NO_ENLARGEMENT:{ov_chunk}>{p_pay}")
    if ov_total is not None and p_dsk is not None and ov_total > p_dsk:
        viols.append(f"OVERRIDE_TOTAL_DISK_CAP_EXCEEDS_PLAN_NO_ENLARGEMENT:{ov_total}>{p_dsk}")

    # 2. Measure exact target volume capacity & required safe floor
    vol_id = _volume_identity_for_path_corr(target_path)
    try:
        real_tot, _, real_free = _disk_usage_for_path_corr(target_path)
    except OSError as exc:
        return {
            "valid": False,
            "status": "BLOCKED_TARGET_VOLUME_UNREADABLE",
            "contract": None,
            "violations": viols + [f"VOLUME_STAT_ERROR:{exc}"],
        }
    v_total = int(volume_total_bytes_override) if _is_strict_int_a1(volume_total_bytes_override) else real_tot
    v_free = int(volume_free_bytes_override) if _is_strict_int_a1(volume_free_bytes_override) else real_free
    if v_total <= 0 or v_free < 0:
        viols.append("INVALID_TARGET_VOLUME_CAPACITY")

    # Floor = max(existing stricter policy floor, 5 GiB, 10% of target volume)
    floor_policy = int(float(strict_preflight_floor_gib) * (1024 ** 3))
    floor_pct = int(v_total * 0.10)
    volume_safe_floor_bytes = max(floor_policy, MIN_SAFE_DISK_FLOOR_BYTES, floor_pct)
    volume_headroom_bytes = v_free - volume_safe_floor_bytes
    if volume_headroom_bytes <= 0:
        viols.append(f"TARGET_VOLUME_INSUFFICIENT_FREE_HEADROOM:free={v_free}:floor={volume_safe_floor_bytes}")

    if viols:
        return {"valid": False, "status": "BLOCKED_BUDGET_SCOPE", "contract": None, "violations": viols}

    assert g_pay is not None and p_pay is not None and g_dec is not None and p_dec is not None and g_dsk is not None and p_dsk is not None

    # Effective caps = strict MIN across all layers (ZERO `max()` enlargement!)
    eff_net_pay = min(g_pay, p_pay)
    eff_dec = min(g_dec, p_dec)

    chunk_candidates = [g_pay, p_pay, g_dsk, p_dsk]
    if pol_chunk is not None:
        chunk_candidates.append(pol_chunk)
    if ov_chunk is not None:
        chunk_candidates.append(ov_chunk)
    eff_max_chunk = min(chunk_candidates)

    disk_candidates = [g_dsk, p_dsk, volume_headroom_bytes]
    if pol_disk is not None:
        disk_candidates.append(pol_disk)
    if ov_total is not None:
        disk_candidates.append(ov_total)
    eff_peak_disk = min(disk_candidates)

    fixed_overhead = _align_fs_blocks(emerg_r) + _align_fs_blocks(man_r) + _align_fs_blocks(wal_r)
    if eff_peak_disk <= fixed_overhead:
        return {
            "valid": False,
            "status": "BLOCKED_BUDGET_SCOPE",
            "contract": None,
            "violations": [
                f"SIGNED_DISK_CAP_INSUFFICIENT_FOR_FIXED_OVERHEAD:eff_disk={eff_peak_disk}<=fixed_overhead={fixed_overhead}"
            ],
        }

    eff_logical_dataset_cap = eff_peak_disk - fixed_overhead
    # Chunk cap also bounded by logical dataset cap!
    eff_max_chunk = min(eff_max_chunk, eff_logical_dataset_cap)

    ram_candidates = [eff_dec, max(4096, eff_peak_disk)]
    if pol_ram is not None:
        ram_candidates.append(pol_ram)
    eff_ram_queue = min(ram_candidates)

    contract = {
        "schema_version": SCHEMA_VERSION_KH03_R6_A1_CORRECTIVE,
        "volume_id": vol_id,
        "target_path_resolved": str(target_path.resolve(strict=False)),
        "volume_total_bytes": v_total,
        "volume_free_bytes": v_free,
        "volume_safe_floor_bytes": volume_safe_floor_bytes,
        "volume_headroom_bytes": volume_headroom_bytes,
        "network_payload_cap_bytes": eff_net_pay,
        "decoded_cap_bytes": eff_dec,
        "max_chunk_bytes": eff_max_chunk,
        "peak_physical_disk_cap_bytes": eff_peak_disk,
        "fixed_overhead_reserve_bytes": fixed_overhead,
        "emergency_audit_reserve_bytes": _align_fs_blocks(emerg_r),
        "manifest_and_anchor_reserve_bytes": _align_fs_blocks(man_r),
        "sqlite_wal_overhead_reserve_bytes": _align_fs_blocks(wal_r),
        "persisted_logical_dataset_cap_bytes": eff_logical_dataset_cap,
        "ram_queue_cap_bytes": eff_ram_queue,
        "enlargement_via_max_used": False,
    }
    contract["contract_digest"] = _sha256_canonical_a1(contract)
    return {"valid": True, "status": "BUDGET_CONTRACT_VERIFIED", "contract": contract, "violations": []}


# =====================================================================
# P1-M: Atomic Durable Disk Budget Ledger (`R6A1DiskBudgetLedger`)
# =====================================================================
_DISK_BUDGET_DDL_A1 = """
CREATE TABLE IF NOT EXISTS volume_scope (
    volume_id TEXT PRIMARY KEY,
    global_cap_bytes INTEGER NOT NULL,
    safe_floor_bytes INTEGER NOT NULL,
    committed_bytes INTEGER NOT NULL DEFAULT 0,
    active_reserved_bytes INTEGER NOT NULL DEFAULT 0,
    uncertain_bytes INTEGER NOT NULL DEFAULT 0,
    peak_reserved_bytes INTEGER NOT NULL DEFAULT 0,
    quarantined INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS session_scope (
    session_id TEXT PRIMARY KEY,
    volume_id TEXT NOT NULL,
    session_cap_bytes INTEGER NOT NULL,
    committed_bytes INTEGER NOT NULL DEFAULT 0,
    active_reserved_bytes INTEGER NOT NULL DEFAULT 0,
    uncertain_bytes INTEGER NOT NULL DEFAULT 0,
    peak_reserved_bytes INTEGER NOT NULL DEFAULT 0,
    emergency_reserved_bytes INTEGER NOT NULL DEFAULT 0,
    quarantined INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS disk_reservations (
    reservation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    volume_id TEXT NOT NULL,
    write_class TEXT NOT NULL,
    state TEXT NOT NULL,
    reserved_bytes INTEGER NOT NULL,
    committed_bytes INTEGER NOT NULL DEFAULT 0,
    uncertain_bytes INTEGER NOT NULL DEFAULT 0,
    released_bytes INTEGER NOT NULL DEFAULT 0,
    created_utc_ms INTEGER NOT NULL,
    reconciled_utc_ms INTEGER,
    reason TEXT NOT NULL DEFAULT ''
);
"""


def compute_chunk_conservative_peak_disk_bytes(
    *,
    raw_bytes_len: int,
    norm_bytes_len: int,
    same_volume_atomic_rename: bool = True,
) -> Dict[str, int]:
    """
    Computes exact conservative pre-materialization peak physical disk footprint for a chunk:
      - raw staged file (4 KiB aligned)
      - normalized UTF-8 staged file (4 KiB aligned)
      - durable intent JSON file + journal append line + anchor rewrite (4 KiB aligned)
      - optional duplicate copy overlap if cross-device (0 extra if same-volume rename proven)
    """
    if not _is_strict_int_a1(raw_bytes_len) or int(raw_bytes_len) < 0:
        raise ValueError("INVALID_RAW_BYTES_LEN")
    if not _is_strict_int_a1(norm_bytes_len) or int(norm_bytes_len) < 0:
        raise ValueError("INVALID_NORM_BYTES_LEN")
    raw_alloc = _align_fs_blocks(int(raw_bytes_len))
    norm_alloc = _align_fs_blocks(int(norm_bytes_len))
    intent_alloc = _align_fs_blocks(DEFAULT_PER_CHUNK_JOURNAL_INTENT_OVERHEAD_BYTES)
    journal_and_anchor_alloc = _align_fs_blocks(4096)
    rename_overlap = 0 if same_volume_atomic_rename else (raw_alloc + norm_alloc)
    peak_bytes = raw_alloc + norm_alloc + intent_alloc + journal_and_anchor_alloc + rename_overlap
    logical_bytes = int(raw_bytes_len) + int(norm_bytes_len)
    return {
        "logical_bytes": logical_bytes,
        "raw_alloc_bytes": raw_alloc,
        "norm_alloc_bytes": norm_alloc,
        "intent_and_metadata_alloc_bytes": intent_alloc + journal_and_anchor_alloc,
        "rename_overlap_bytes": rename_overlap,
        "conservative_peak_bytes": peak_bytes,
    }


class R6A1DiskBudgetLedger:
    """
    F06: Transactional, cross-thread & cross-process atomic disk reservation ledger on the verified
    target volume (SQLite `BEGIN IMMEDIATE` + OS file lock).
    Enforces:
      `committed_bytes + active_reserved_bytes + uncertain_bytes + requested <= cap`
    across BOTH session scope AND global volume scope, plus real-time volume free floor!
    """

    def __init__(
        self,
        *,
        store_root: Path,
        volume_id: str,
        global_cap_bytes: int,
        safe_floor_bytes: int,
        volume_free_bytes_fn: Optional[Callable[[], int]] = None,
        busy_timeout_ms: int = 10_000,
    ) -> None:
        if not _is_strict_int_a1(global_cap_bytes) or int(global_cap_bytes) <= 0:
            raise ValueError("GLOBAL_CAP_BYTES_MUST_BE_POSITIVE_INT")
        if not _is_strict_int_a1(safe_floor_bytes) or int(safe_floor_bytes) < 0:
            raise ValueError("SAFE_FLOOR_BYTES_MUST_BE_NONNEG_INT")
        self.store_root = Path(store_root)
        self.store_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.store_root / "disk_budget.sqlite3"
        self.lock_path = self.store_root / ".disk_budget.lock"
        self.volume_id = str(volume_id)
        self.global_cap_bytes = int(global_cap_bytes)
        self.safe_floor_bytes = int(safe_floor_bytes)
        self._volume_free_bytes_fn = volume_free_bytes_fn
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._tx_lock = threading.Lock()
        self._init_db()

    def _os_lock_enter(self) -> int:
        deadline = time.monotonic() + (self.busy_timeout_ms / 1000.0)
        fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
        except OSError:
            pass
        while True:
            try:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise TimeoutError("DISK_BUDGET_OS_LOCK_TIMEOUT")
                time.sleep(0.01)

    def _os_lock_exit(self, fd: int) -> None:
        try:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=self.busy_timeout_ms / 1000.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms};")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=FULL;")
        return conn

    def _init_db(self) -> None:
        with self._tx_lock:
            fd = self._os_lock_enter()
            conn = self._connect()
            try:
                conn.executescript(_DISK_BUDGET_DDL_A1)
                conn.execute("BEGIN IMMEDIATE;")
                row = conn.execute("SELECT volume_id, global_cap_bytes, safe_floor_bytes FROM volume_scope WHERE volume_id = ?", (self.volume_id,)).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO volume_scope (volume_id, global_cap_bytes, safe_floor_bytes) VALUES (?, ?, ?)",
                        (self.volume_id, self.global_cap_bytes, self.safe_floor_bytes),
                    )
                else:
                    # NEVER enlarge global cap via max(); only tighten via min()!
                    tighter_cap = min(int(row["global_cap_bytes"]), self.global_cap_bytes)
                    stricter_floor = max(int(row["safe_floor_bytes"]), self.safe_floor_bytes)
                    conn.execute(
                        "UPDATE volume_scope SET global_cap_bytes = ?, safe_floor_bytes = ? WHERE volume_id = ?",
                        (tighter_cap, stricter_floor, self.volume_id),
                    )
                conn.execute("COMMIT;")
            finally:
                conn.close()
                self._os_lock_exit(fd)

    def _get_live_free_bytes(self) -> Optional[int]:
        try:
            if self._volume_free_bytes_fn is not None:
                v = self._volume_free_bytes_fn()
                return int(v) if _is_strict_int_a1(v) and int(v) >= 0 else None
            _, _, free_b = _disk_usage_for_path_corr(self.store_root)
            return int(free_b)
        except Exception:
            return None

    def register_session_budget(
        self,
        *,
        session_id: str,
        session_cap_bytes: int,
        emergency_audit_reserve_bytes: int = DEFAULT_EMERGENCY_AUDIT_RESERVE_BYTES,
        manifest_and_wal_reserve_bytes: int = DEFAULT_MANIFEST_AND_ANCHOR_RESERVE_BYTES + DEFAULT_SQLITE_WAL_OVERHEAD_RESERVE_BYTES,
    ) -> Dict[str, Any]:
        if not str(session_id or "").strip():
            return {"registered": False, "status": "REJECTED_MISSING_SESSION_ID", "violations": ["MISSING_SESSION_ID"]}
        if not _is_strict_int_a1(session_cap_bytes) or int(session_cap_bytes) <= 0 or int(session_cap_bytes) > MAX_INT64_SAFE_CORR:
            return {"registered": False, "status": "REJECTED_INVALID_SESSION_CAP", "violations": ["INVALID_SESSION_CAP_BYTES"]}

        with self._tx_lock:
            fd = self._os_lock_enter()
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                vrow = conn.execute("SELECT * FROM volume_scope WHERE volume_id = ?", (self.volume_id,)).fetchone()
                if int(vrow["quarantined"]) != 0:
                    conn.execute("ROLLBACK;")
                    return {"registered": False, "status": "BLOCKED_DISK_LEDGER_QUARANTINED", "violations": [f"VOLUME_QUARANTINED:{vrow['quarantine_reason']}"]}
                eff_sess_cap = min(int(session_cap_bytes), int(vrow["global_cap_bytes"]))
                fixed_r = _align_fs_blocks(int(emergency_audit_reserve_bytes)) + _align_fs_blocks(int(manifest_and_wal_reserve_bytes))
                if eff_sess_cap <= fixed_r:
                    conn.execute("ROLLBACK;")
                    return {"registered": False, "status": "BLOCKED_BUDGET_SCOPE", "violations": [f"SESSION_CAP_TOO_SMALL_FOR_FIXED_OVERHEAD:{eff_sess_cap}<={fixed_r}"]}
                existing = conn.execute("SELECT session_id FROM session_scope WHERE session_id = ?", (session_id,)).fetchone()
                if existing is not None:
                    conn.execute("ROLLBACK;")
                    return {"registered": False, "status": "REJECTED_DUPLICATE_SESSION_BUDGET", "violations": ["DUPLICATE_SESSION_BUDGET_REGISTRATION"]}
                conn.execute(
                    "INSERT INTO session_scope (session_id, volume_id, session_cap_bytes, emergency_reserved_bytes) VALUES (?, ?, ?, ?)",
                    (session_id, self.volume_id, eff_sess_cap, _align_fs_blocks(int(emergency_audit_reserve_bytes))),
                )
                conn.execute("COMMIT;")
            finally:
                conn.close()
                self._os_lock_exit(fd)

        # Immediately reserve fixed overhead (emergency audit + manifest/anchor/WAL) atomically!
        if fixed_r > 0:
            r_fix = self.reserve_r6_disk_bytes(
                session_id=session_id,
                reservation_id=f"fixed_overhead_{session_id}",
                write_class="fixed_manifest_wal_emergency_reserve",
                requested_bytes=fixed_r,
            )
            if not r_fix["reserved"]:
                return {"registered": False, "status": "BLOCKED_FIXED_OVERHEAD_RESERVATION", "violations": r_fix["violations"]}
        return {"registered": True, "status": "SESSION_BUDGET_REGISTERED", "effective_session_cap_bytes": eff_sess_cap, "fixed_reserved_bytes": fixed_r, "violations": []}

    def reserve_r6_disk_bytes(
        self,
        *,
        session_id: str,
        reservation_id: str,
        write_class: str,
        requested_bytes: int,
    ) -> Dict[str, Any]:
        if not str(session_id or "").strip() or not str(reservation_id or "").strip() or not str(write_class or "").strip():
            return {"reserved": False, "status": "REJECTED_MISSING_RESERVATION_FIELDS", "violations": ["MISSING_FIELDS"]}
        if not _is_strict_int_a1(requested_bytes) or int(requested_bytes) <= 0 or int(requested_bytes) > MAX_INT64_SAFE_CORR:
            return {"reserved": False, "status": "REJECTED_INVALID_REQUESTED_BYTES", "violations": [f"INVALID_BYTES:{requested_bytes!r}"]}
        req_b = int(requested_bytes)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        with self._tx_lock:
            fd = self._os_lock_enter()
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                vrow = conn.execute("SELECT * FROM volume_scope WHERE volume_id = ?", (self.volume_id,)).fetchone()
                srow = conn.execute("SELECT * FROM session_scope WHERE session_id = ?", (session_id,)).fetchone()
                if vrow is None or srow is None:
                    conn.execute("ROLLBACK;")
                    return {"reserved": False, "status": "REJECTED_UNREGISTERED_SESSION_OR_VOLUME", "violations": ["SESSION_OR_VOLUME_NOT_REGISTERED"]}
                if int(vrow["quarantined"]) != 0 or int(srow["quarantined"]) != 0:
                    conn.execute("ROLLBACK;")
                    return {"reserved": False, "status": "BLOCKED_DISK_LEDGER_QUARANTINED", "violations": ["DISK_BUDGET_QUARANTINED"]}
                if str(srow["volume_id"]) != self.volume_id:
                    conn.execute("ROLLBACK;")
                    return {"reserved": False, "status": "REJECTED_VOLUME_MISMATCH", "violations": ["SESSION_VOLUME_MISMATCH"]}
                dup = conn.execute("SELECT state FROM disk_reservations WHERE reservation_id = ?", (reservation_id,)).fetchone()
                if dup is not None:
                    conn.execute("ROLLBACK;")
                    return {"reserved": False, "status": "REJECTED_DUPLICATE_RESERVATION_ID", "violations": [f"DUPLICATE_RESERVATION_ID:{reservation_id}"]}

                # Check live volume free space minus active reserved + requested >= safe floor
                live_free = self._get_live_free_bytes()
                if live_free is None:
                    conn.execute("ROLLBACK;")
                    return {"reserved": False, "status": "BLOCKED_VOLUME_FREE_UNREADABLE", "violations": ["VOLUME_FREE_UNREADABLE"]}
                v_active = int(vrow["active_reserved_bytes"])
                v_floor = int(vrow["safe_floor_bytes"])
                if live_free - (v_active + req_b) < v_floor:
                    conn.execute("ROLLBACK;")
                    return {
                        "reserved": False,
                        "status": "REJECTED_VOLUME_FREE_FLOOR_BREACH",
                        "violations": [f"VOLUME_FLOOR_BREACH:free={live_free}:active={v_active}:req={req_b}:floor={v_floor}"],
                    }

                # Check session scope invariant: committed + active + uncertain + req <= session_cap
                s_proj = int(srow["committed_bytes"]) + int(srow["active_reserved_bytes"]) + int(srow["uncertain_bytes"]) + req_b
                if s_proj > int(srow["session_cap_bytes"]):
                    conn.execute("ROLLBACK;")
                    return {
                        "reserved": False,
                        "status": "REJECTED_SESSION_DISK_BUDGET_EXCEEDED",
                        "violations": [f"SESSION_DISK_CAP_EXCEEDED:projected={s_proj}>cap={srow['session_cap_bytes']}"],
                    }

                # Check global volume scope invariant: committed + active + uncertain + req <= global_cap
                v_proj = int(vrow["committed_bytes"]) + v_active + int(vrow["uncertain_bytes"]) + req_b
                if v_proj > int(vrow["global_cap_bytes"]):
                    conn.execute("ROLLBACK;")
                    return {
                        "reserved": False,
                        "status": "REJECTED_GLOBAL_VOLUME_DISK_BUDGET_EXCEEDED",
                        "violations": [f"GLOBAL_DISK_CAP_EXCEEDED:projected={v_proj}>cap={vrow['global_cap_bytes']}"],
                    }

                new_s_act = int(srow["active_reserved_bytes"]) + req_b
                new_s_peak = max(int(srow["peak_reserved_bytes"]), int(srow["committed_bytes"]) + new_s_act + int(srow["uncertain_bytes"]))
                new_v_act = v_active + req_b
                new_v_peak = max(int(vrow["peak_reserved_bytes"]), int(vrow["committed_bytes"]) + new_v_act + int(vrow["uncertain_bytes"]))

                conn.execute(
                    "INSERT INTO disk_reservations (reservation_id, session_id, volume_id, write_class, state, reserved_bytes, created_utc_ms) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)",
                    (reservation_id, session_id, self.volume_id, str(write_class), req_b, now_ms),
                )
                conn.execute(
                    "UPDATE session_scope SET active_reserved_bytes = ?, peak_reserved_bytes = ? WHERE session_id = ?",
                    (new_s_act, new_s_peak, session_id),
                )
                conn.execute(
                    "UPDATE volume_scope SET active_reserved_bytes = ?, peak_reserved_bytes = ? WHERE volume_id = ?",
                    (new_v_act, new_v_peak, self.volume_id),
                )
                conn.execute("COMMIT;")
                return {
                    "reserved": True,
                    "status": "DISK_BYTES_RESERVED",
                    "reservation_id": reservation_id,
                    "reserved_bytes": req_b,
                    "session_projected_bytes": s_proj,
                    "global_projected_bytes": v_proj,
                    "violations": [],
                }
            finally:
                conn.close()
                self._os_lock_exit(fd)

    def reconcile_r6_disk_actual(
        self,
        *,
        session_id: str,
        reservation_id: str,
        actual_committed_bytes: int,
        uncertain_bytes: int = 0,
        reason: str = "COMMITTED",
    ) -> Dict[str, Any]:
        """
        Reconciles an ACTIVE reservation against actual measured physical bytes.
        If `actual_committed_bytes + uncertain_bytes > reserved_bytes` -> QUARANTINE without clamping!
        Releases ONLY proven unused bytes (`reserved - actual - uncertain`).
        """
        if not _is_strict_int_a1(actual_committed_bytes) or int(actual_committed_bytes) < 0:
            return {"reconciled": False, "status": "REJECTED_INVALID_ACTUAL_BYTES", "violations": ["INVALID_ACTUAL_BYTES"]}
        if not _is_strict_int_a1(uncertain_bytes) or int(uncertain_bytes) < 0:
            return {"reconciled": False, "status": "REJECTED_INVALID_UNCERTAIN_BYTES", "violations": ["INVALID_UNCERTAIN_BYTES"]}
        act_b = int(actual_committed_bytes)
        unc_b = int(uncertain_bytes)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        with self._tx_lock:
            fd = self._os_lock_enter()
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                rrow = conn.execute("SELECT * FROM disk_reservations WHERE reservation_id = ?", (reservation_id,)).fetchone()
                if rrow is None or str(rrow["session_id"]) != str(session_id):
                    conn.execute("ROLLBACK;")
                    return {"reconciled": False, "status": "REJECTED_UNKNOWN_OR_FOREIGN_RESERVATION", "violations": ["UNKNOWN_OR_FOREIGN_RESERVATION"]}
                if str(rrow["state"]) != "ACTIVE":
                    conn.execute("ROLLBACK;")
                    return {"reconciled": False, "status": "REJECTED_DOUBLE_RECONCILE_OR_RELEASE", "violations": [f"RESERVATION_NOT_ACTIVE:{rrow['state']}"]}

                res_b = int(rrow["reserved_bytes"])
                srow = conn.execute("SELECT * FROM session_scope WHERE session_id = ?", (session_id,)).fetchone()
                vrow = conn.execute("SELECT * FROM volume_scope WHERE volume_id = ?", (self.volume_id,)).fetchone()
                overrun = (act_b + unc_b) > res_b
                released = max(0, res_b - act_b - unc_b)

                new_s_act = max(0, int(srow["active_reserved_bytes"]) - res_b)
                new_s_com = int(srow["committed_bytes"]) + act_b
                new_s_unc = int(srow["uncertain_bytes"]) + unc_b
                new_v_act = max(0, int(vrow["active_reserved_bytes"]) - res_b)
                new_v_com = int(vrow["committed_bytes"]) + act_b
                new_v_unc = int(vrow["uncertain_bytes"]) + unc_b

                cap_breach = (new_s_com + new_s_act + new_s_unc > int(srow["session_cap_bytes"])) or (
                    new_v_com + new_v_act + new_v_unc > int(vrow["global_cap_bytes"])
                )
                if overrun or cap_breach:
                    q_reason = f"DISK_BUDGET_OVERRUN:res={res_b}:act={act_b}:unc={unc_b}"
                    conn.execute(
                        "UPDATE disk_reservations SET state='QUARANTINED_OVERRUN', committed_bytes=?, uncertain_bytes=?, released_bytes=0, reconciled_utc_ms=?, reason=? WHERE reservation_id=?",
                        (act_b, unc_b, now_ms, q_reason, reservation_id),
                    )
                    conn.execute(
                        "UPDATE session_scope SET committed_bytes=?, active_reserved_bytes=?, uncertain_bytes=?, quarantined=1, quarantine_reason=? WHERE session_id=?",
                        (new_s_com, new_s_act, new_s_unc, q_reason, session_id),
                    )
                    conn.execute(
                        "UPDATE volume_scope SET committed_bytes=?, active_reserved_bytes=?, uncertain_bytes=?, quarantined=1, quarantine_reason=? WHERE volume_id=?",
                        (new_v_com, new_v_act, new_v_unc, q_reason, self.volume_id),
                    )
                    conn.execute("COMMIT;")
                    return {
                        "reconciled": False,
                        "quarantined": True,
                        "status": "QUARANTINED_DISK_OVERRUN",
                        "committed_bytes": act_b,
                        "uncertain_bytes": unc_b,
                        "released_bytes": 0,
                        "violations": [q_reason],
                    }

                new_state = "UNCERTAIN_RETAINED" if unc_b > 0 and act_b == 0 else "RECONCILED"
                conn.execute(
                    "UPDATE disk_reservations SET state=?, committed_bytes=?, uncertain_bytes=?, released_bytes=?, reconciled_utc_ms=?, reason=? WHERE reservation_id=?",
                    (new_state, act_b, unc_b, released, now_ms, str(reason), reservation_id),
                )
                conn.execute(
                    "UPDATE session_scope SET committed_bytes=?, active_reserved_bytes=?, uncertain_bytes=? WHERE session_id=?",
                    (new_s_com, new_s_act, new_s_unc, session_id),
                )
                conn.execute(
                    "UPDATE volume_scope SET committed_bytes=?, active_reserved_bytes=?, uncertain_bytes=? WHERE volume_id=?",
                    (new_v_com, new_v_act, new_v_unc, self.volume_id),
                )
                conn.execute("COMMIT;")
                return {
                    "reconciled": True,
                    "quarantined": False,
                    "status": new_state,
                    "committed_bytes": act_b,
                    "uncertain_bytes": unc_b,
                    "released_bytes": released,
                    "violations": [],
                }
            finally:
                conn.close()
                self._os_lock_exit(fd)

    def release_r6_disk_reservation(
        self,
        *,
        session_id: str,
        reservation_id: str,
        proven_unused_or_deleted: bool,
        reason: str = "PROVEN_UNUSED",
    ) -> Dict[str, Any]:
        """
        Releases an ACTIVE reservation ONLY when `proven_unused_or_deleted=True`!
        Otherwise retains 100% of reserved bytes as `uncertain_bytes` (NEVER releases on assumption!).
        """
        with self._tx_lock:
            fd = self._os_lock_enter()
            conn = self._connect()
            try:
                rrow = conn.execute("SELECT reserved_bytes, state, session_id FROM disk_reservations WHERE reservation_id = ?", (reservation_id,)).fetchone()
                if rrow is None or str(rrow["session_id"]) != str(session_id):
                    return {"released": False, "status": "REJECTED_UNKNOWN_OR_FOREIGN_RESERVATION", "violations": ["UNKNOWN_OR_FOREIGN_RESERVATION"]}
                if str(rrow["state"]) != "ACTIVE":
                    return {"released": False, "status": "REJECTED_DOUBLE_RELEASE", "violations": [f"RESERVATION_ALREADY_{rrow['state']}"]}
                res_b = int(rrow["reserved_bytes"])
            finally:
                conn.close()
                self._os_lock_exit(fd)

        if proven_unused_or_deleted:
            rec = self.reconcile_r6_disk_actual(
                session_id=session_id,
                reservation_id=reservation_id,
                actual_committed_bytes=0,
                uncertain_bytes=0,
                reason=f"RELEASED:{reason}",
            )
            return {
                "released": bool(rec.get("reconciled")),
                "status": "RELEASED_PROVEN_UNUSED" if rec.get("reconciled") else rec.get("status"),
                "released_bytes": rec.get("released_bytes", 0),
                "uncertain_bytes": 0,
                "violations": list(rec.get("violations") or ()),
            }
        # Conservative retention when unused/deletion is NOT proven!
        rec = self.reconcile_r6_disk_actual(
            session_id=session_id,
            reservation_id=reservation_id,
            actual_committed_bytes=0,
            uncertain_bytes=res_b,
            reason=f"UNCERTAIN_RETAINED:{reason}",
        )
        return {
            "released": False,
            "status": "UNCERTAIN_BYTES_RETAINED",
            "released_bytes": 0,
            "uncertain_bytes": res_b,
            "violations": list(rec.get("violations") or ()),
        }

    def recover_r6_disk_reservations(self, *, active_session_ids: Sequence[str] = ()) -> Dict[str, Any]:
        """
        Startup crash recovery: converts ALL remaining ACTIVE reservations for non-active sessions
        into `UNCERTAIN_RETAINED` bytes (NEVER resets quota after crash/restart!).
        """
        active_set = {str(s) for s in active_session_ids}
        recovered_ids: List[str] = []
        retained_total = 0
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        with self._tx_lock:
            fd = self._os_lock_enter()
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                rows = conn.execute("SELECT * FROM disk_reservations WHERE state = 'ACTIVE'").fetchall()
                for r in rows:
                    sid = str(r["session_id"])
                    if sid in active_set:
                        continue
                    rid = str(r["reservation_id"])
                    rb = int(r["reserved_bytes"])
                    conn.execute(
                        "UPDATE disk_reservations SET state='UNCERTAIN_RETAINED', uncertain_bytes=?, reconciled_utc_ms=?, reason='CRASH_RECOVERY_RETAINED' WHERE reservation_id=?",
                        (rb, now_ms, rid),
                    )
                    conn.execute(
                        "UPDATE session_scope SET active_reserved_bytes=MAX(0, active_reserved_bytes-?), uncertain_bytes=uncertain_bytes+? WHERE session_id=?",
                        (rb, rb, sid),
                    )
                    conn.execute(
                        "UPDATE volume_scope SET active_reserved_bytes=MAX(0, active_reserved_bytes-?), uncertain_bytes=uncertain_bytes+? WHERE volume_id=?",
                        (rb, rb, self.volume_id),
                    )
                    recovered_ids.append(rid)
                    retained_total += rb
                conn.execute("COMMIT;")
            finally:
                conn.close()
                self._os_lock_exit(fd)
        return {
            "recovered": True,
            "recovered_reservation_ids": recovered_ids,
            "uncertain_bytes_retained_total": retained_total,
        }

    def snapshot_r6_disk_budget(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        with self._tx_lock:
            conn = self._connect()
            try:
                vrow = conn.execute("SELECT * FROM volume_scope WHERE volume_id = ?", (self.volume_id,)).fetchone()
                srow = conn.execute("SELECT * FROM session_scope WHERE session_id = ?", (session_id,)).fetchone() if session_id else None
                res_rows = (
                    conn.execute("SELECT * FROM disk_reservations WHERE session_id = ? ORDER BY created_utc_ms, reservation_id", (session_id,)).fetchall()
                    if session_id
                    else conn.execute("SELECT * FROM disk_reservations ORDER BY created_utc_ms, reservation_id").fetchall()
                )
                return {
                    "volume": dict(vrow) if vrow else None,
                    "session": dict(srow) if srow else None,
                    "reservations": [dict(r) for r in res_rows],
                    "active_reservations_count": sum(1 for r in res_rows if str(r["state"]) == "ACTIVE"),
                    "db_plus_wal_bytes": self._measure_db_wal_bytes(),
                }
            finally:
                conn.close()

    def _measure_db_wal_bytes(self) -> int:
        tot = 0
        for p in (self.db_path, Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")):
            try:
                if p.is_file():
                    tot += int(p.stat().st_size)
            except OSError:
                pass
        return tot

    def checkpoint_wal_bounded(self) -> int:
        with self._tx_lock:
            fd = self._os_lock_enter()
            conn = self._connect()
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except sqlite3.DatabaseError:
                pass
            finally:
                conn.close()
                self._os_lock_exit(fd)
        return self._measure_db_wal_bytes()


# =====================================================================
# P1-N: Terminal Disposition Derivation (Separates Ledger vs Acquisition!)
# =====================================================================
def derive_r6_a1_terminal_disposition(
    *,
    has_context: bool,
    qa_loopback_mode: bool,
    expected_tasks: int,
    completed_tasks: int,
    failed_tasks: int,
    committed_chunks_count: int,
    total_raw_bytes: int,
    total_norm_rows: int,
    custody_readonly_valid: bool,
    all_children_reaped_verified: bool,
    unreaped_worker_detected: bool,
    ledger_final_status: str,
    ledger_terminal_verified_from_db: bool,
    unproven_terminal: bool,
    quarantined: bool,
    cancelled: bool,
    deadline_expired: bool,
    original_error: Optional[str],
    cleanup_errors: Sequence[str],
    violations: Sequence[str],
    unresolved_disk_reservations: int = 0,
    claim_status: str = "",
) -> Dict[str, Any]:
    """
    F11: Derives truthful terminal acquisition disposition strictly from durable receipts and
    observed coverage — NEVER equating durable ledger `SPENT`/`FINALIZED` with acquisition PASS!
    """
    if not has_context:
        return {
            "acquisition_status": claim_status or "BLOCKED_ACQUISITION_PRECHECK",
            "ledger_terminal_state": ledger_final_status or "NOT_CLAIMED",
            "acquisition_succeeded": False,
            "exit_code": 2,
        }
    if not A1_EXTERNAL_CAPTURE_ENABLED and not qa_loopback_mode:
        return {
            "acquisition_status": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED",
            "ledger_terminal_state": "NOT_CLAIMED",
            "acquisition_succeeded": False,
            "exit_code": 2,
        }
    if unproven_terminal or not ledger_terminal_verified_from_db:
        return {
            "acquisition_status": "FAILED_UNPROVEN_TERMINAL_STATE",
            "ledger_terminal_state": "UNPROVEN_TERMINAL",
            "acquisition_succeeded": False,
            "exit_code": 2,
        }
    if unreaped_worker_detected or quarantined or ledger_final_status == "QUARANTINED":
        return {
            "acquisition_status": "QUARANTINED_ACQUISITION_SESSION",
            "ledger_terminal_state": ledger_final_status,
            "acquisition_succeeded": False,
            "exit_code": 2,
        }
    if cancelled:
        return {
            "acquisition_status": "ABORTED_CANCELLED" if committed_chunks_count == 0 else "PARTIAL_ABORTED_CANCELLED",
            "ledger_terminal_state": ledger_final_status,
            "acquisition_succeeded": False,
            "exit_code": 2,
        }
    if deadline_expired:
        return {
            "acquisition_status": "ABORTED_DEADLINE_EXPIRED" if committed_chunks_count == 0 else "PARTIAL_DEADLINE_EXPIRED",
            "ledger_terminal_state": ledger_final_status,
            "acquisition_succeeded": False,
            "exit_code": 2,
        }
    if original_error or cleanup_errors or unresolved_disk_reservations > 0:
        return {
            "acquisition_status": "PARTIAL_OR_FAILED_WITH_ERRORS" if committed_chunks_count > 0 else "FAILED_SESSION_EXCEPTION",
            "ledger_terminal_state": ledger_final_status,
            "acquisition_succeeded": False,
            "exit_code": 2,
        }

    all_ok = (
        expected_tasks > 0
        and completed_tasks == expected_tasks
        and failed_tasks == 0
        and committed_chunks_count > 0
        and total_raw_bytes > 0
        and total_norm_rows > 0
        and custody_readonly_valid
        and all_children_reaped_verified
        and ledger_final_status == "SPENT"
        and ledger_terminal_verified_from_db
        and len(violations) == 0
    )
    if all_ok:
        return {
            "acquisition_status": "QA_ACQUISITION_END_TO_END_PASS",
            "ledger_terminal_state": ledger_final_status,
            "acquisition_succeeded": True,
            "exit_code": 0,
        }
    return {
        "acquisition_status": "PARTIAL_ACQUISITION_FAILED" if committed_chunks_count > 0 else "FAILED_ACQUISITION_NO_COMMITTED_DATA",
        "ledger_terminal_state": ledger_final_status,
        "acquisition_succeeded": False,
        "exit_code": 2,
    }


# =====================================================================
# M0: Real Isolated Baseline RED Reproduction on Untouched `133980ac`
# =====================================================================
_BASELINE_133980A_FILES: Tuple[str, ...] = (
    "aurum-desk/backend/services/kh_03_r6_source_contract_service.py",
    "aurum-desk/backend/services/kh_03_r6_durable_grant_service.py",
    "aurum-desk/backend/services/kh_03_r6_bounded_transport_service.py",
    "aurum-desk/backend/services/kh_03_r6_market_data_service.py",
    "aurum-desk/backend/services/kh_03_r6_custody_readiness_service.py",
    "aurum-desk/backend/services/kh_03_r6_a1_trust_service.py",
    "aurum-desk/backend/services/kh_03_r6_a1_release_service.py",
    "aurum-desk/backend/services/kh_03_r6_a1_acquisition_service.py",
)


def run_m0_real_baseline_red_133980ac(
    *,
    repo_root: Path,
    run_id: str,
    tested_sha: str,
) -> Dict[str, Any]:
    """
    M0: Extracts untouched `133980ac9e28e663df8284617bc4306791a0f86a` source via `git show`
    into a minimal bounded isolated temp overlay (NEVER resets branch or copies datasets/OOS!)
    and reproduces ALL 3 review findings (`P1-L`, `P1-M`, `P1-N` across 5 real witnesses + 1 positive
    control) in an isolated separate OS subprocess!
    """
    extracted_hashes: Dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="a1_corr_m0_red_") as td:
        overlay = Path(td)
        for rel in _BASELINE_133980A_FILES:
            p_show = subprocess.run(
                ["git", "show", f"{PRIOR_A1_REVIEWED_TESTED_SHA}:{rel}"],
                cwd=str(repo_root),
                capture_output=True,
                check=False,
                timeout=20,
            )
            if p_show.returncode != 0 or not p_show.stdout:
                return {
                    "status": "FAIL_GIT_SHOW_EXTRACTION",
                    "baseline_sha": PRIOR_A1_REVIEWED_TESTED_SHA,
                    "reproduced_count": 0,
                    "all_reproduced": False,
                    "receipts": [],
                    "violations": [f"GIT_SHOW_FAILED:{rel}"],
                }
            dst = overlay / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(p_show.stdout)
            extracted_hashes[rel] = hashlib.sha256(p_show.stdout).hexdigest()

        (overlay / "aurum-desk" / "backend" / "services" / "__init__.py").write_text(
            "from pkgutil import extend_path\n__path__ = extend_path(__path__, __name__)\n",
            encoding="utf-8",
        )

        probe_script = overlay / "run_m0_red_133980a.py"
        probe_script.write_text(
            '''import hashlib, inspect, json, os, sys, tempfile, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

overlay = Path(sys.argv[1])
repo_root = Path(sys.argv[2])

sys.path.insert(0, str(repo_root / "aurum-desk/backend"))
sys.path.insert(0, str(repo_root / "aurum-desk"))
sys.path.insert(0, str(overlay / "aurum-desk/backend"))
sys.path.insert(0, str(overlay / "aurum-desk"))

import services.kh_03_r6_durable_grant_service as dgs
import services.kh_03_r6_bounded_transport_service as bts
import services.kh_03_r6_custody_readiness_service as crs
import services.kh_03_r6_source_contract_service as scs
import services.kh_03_r6_a1_trust_service as trs
import services.kh_03_r6_a1_acquisition_service as acqs

now = datetime(2026, 11, 26, 5, 0, 0, tzinfo=timezone.utc)
now_ms = int(now.timestamp() * 1000)
reg = scs.build_r6_source_contract_registry()
ep_c = reg.endpoints["classic_v2_contracts"]
raw_meta = json.dumps({"code":"00000","msg":"success","data":[{"symbol":"XAUUSDT","baseCoin":"XAU","quoteCoin":"USDT","buyLimitPriceRatio":"0.01","sellLimitPriceRatio":"0.01","feeRateUpRatio":"0.005","makerFeeRate":"0.0002","takerFeeRate":"0.0006","openCostUpRatio":"0.01","supportMarginCoins":["USDT"],"minTradeNum":"0.01","priceEndStep":"1","volumePlace":"2","pricePlace":"2","sizeMultiplier":"0.01","symbolType":"perpetual","minTradeUSDT":"5","maxSymbolOrderNum":"999999","maxProductOrderNum":"999999","maxPositionNum":"150","symbolStatus":"normal","offTime":"-1","limitOpenTime":"-1","deliveryTime":"","deliveryStartTime":"","launchTime":"1700000000000","fundInterval":"8","minLever":"1","maxLever":"100","posLimit":"0.05","maintainTime":"1700000000000"}]}).encode("utf-8")

class _H(BaseHTTPRequestHandler):
    def log_message(self, *a): return
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw_meta)))
        self.end_headers()
        self.wfile.write(raw_meta)

srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
port = int(srv.server_address[1])
th = threading.Thread(target=srv.serve_forever, daemon=True)
th.start()

receipts = []
try:
    with tempfile.TemporaryDirectory(prefix="m0_133980a_") as td:
        wdir = Path(td)
        dest = wdir / "dest"; dest.mkdir()
        fake_repo = wdir / "repo"; fake_repo.mkdir()
        sid = "m0_133980a_store"
        tr_p, priv_k, _ = dgs.provision_qa_isolated_trust_root(wdir / "trust", store_id=sid)
        tr_qa = dgs.load_r6_owner_trust_root(tr_p, expected_namespace=dgs.QA_ISOLATED_TRUST_NAMESPACE_R6)["trust_root"]
        sroot = wdir / "ledger"
        dgs.provision_r6_durable_ledger(sroot, store_id=sid, trust_namespace=dgs.QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now)
        led = dgs.open_r6_durable_ledger({"ledger_store_root": str(sroot), "store_id": sid}, sid, expected_trust_namespace=dgs.QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now)["ledger"]

        def _mk_vg_plan(gid, pay_cap=8192, disk_cap=16384, eps=None):
            eps = eps or ["classic_v2_contracts", "classic_v2_history_fund_rate"]
            gp = {
                "grant_id": gid, "issuer": "QA_ISOLATED_HARNESS", "trust_namespace": dgs.QA_ISOLATED_TRUST_NAMESPACE_R6,
                "issued_utc_ms": now_ms - 1000, "expires_utc_ms": now_ms + 600_000,
                "repo": "quocan050302/Build-app-crypto", "branch": scs.MANDATORY_BRANCH_KH03_R6,
                "tested_executable_sha": "133980ac9e28e663df8284617bc4306791a0f86a", "release_manifest_sha256": "a" * 64,
                "purpose": "QA_FIXTURE", "stage": "QA_LOOPBACK", "venue": scs.APPROVED_VENUE_R6,
                "api_family": scs.API_FAMILY_CLASSIC_V2, "symbol": scs.APPROVED_SYMBOL_R6, "product_type": scs.APPROVED_PRODUCT_TYPE_V2_R6,
                "allowed_endpoints": eps, "allowed_ws_channels": [],
                "window_start_ms": 1794000000000, "window_end_ms": 1794003600000,
                "destination_store_id": sid, "destination_root": str(dest),
                "max_session_duration_sec": 120, "max_concurrency": 2,
                "caps": {"connections": 1, "requests": 10, "retries": 1, "subscriptions": 0, "messages": 0, "payload_bytes": pay_cap, "decoded_bytes": pay_cap * 2, "pages": 2, "disk_bytes": disk_cap, "records": 100},
                "retention_policy": {"max_retention_days": 7, "git_commit_raw_payloads": False},
            }
            vg = dgs.verify_r6_owner_grant(
                dgs.sign_r6_grant_payload_with_ed25519(gp, priv_k), tr_qa, now,
                expected_executable_sha="133980ac9e28e663df8284617bc4306791a0f86a",
                expected_release_manifest_sha256="a" * 64, expected_store_id=sid, expected_stage="QA_LOOPBACK",
            )["verified_grant"]
            pl = bts.build_r6_bound_capture_plan(vg, reg, {}, {"ok": True}, plan_id=f"pl_{gid}")["plan"]
            return vg, pl

        def _real_att(lease, pl, vg, aid):
            caps = trs.complete_caps_vector_a1({"requests": 1, "payload_bytes": 4096, "decoded_bytes": 8192})
            auth = trs.authorize_r6_a1_dispatch_request(
                plan=pl, verified_grant=vg, lease=lease, registry=reg, endpoint_id="classic_v2_contracts",
                attempt_id=aid, partition="QA_FIXTURE", requested_caps=caps, deadline_monotonic=time.monotonic() + 15.0,
                params={"productType": scs.APPROVED_PRODUCT_TYPE_V2_R6, "symbol": scs.APPROVED_SYMBOL_R6},
                qa_loopback_mode=True, qa_loopback_url=f"http://127.0.0.1:{port}/api/v2/mix/market/contracts", actual_now=now,
            )["authorized_request"]
            return bts.supervise_r6_session(
                ledger=led, lease=lease, plan=pl, verified_grant=vg,
                validated_request={"kind": "rest_get", "url": auth.dispatch_url, "params": dict(auth.params)},
                attempt_id=aid, request_type="rest_get", reserved_caps=caps,
                resource_sample_fn=lambda: bts.collect_real_r6_resource_sample({"dest": dest}, active_workers=1),
                expected_volumes={"dest": dest}, session_deadline_monotonic=time.monotonic() + 15.0,
                cancel_event=threading.Event(), utc_now_fn=lambda: now, qa_loopback_mode=True,
                allow_fixture_resource_sample=True, registry=reg, authorized_request=auth,
                run_id="red_133980a", partition="QA_FIXTURE", endpoint_id="classic_v2_contracts",
                contract_digest=ep_c.content_digest,
            )

        # ---- RED-L1: Concurrent R6CustodyJournal.persist_r6_verified_chunk with real Barrier(2) -> duplicate seq & broken prev_hash! ----
        t0 = time.perf_counter()
        vg_l, pl_l = _mk_vg_plan("G_RED_L1", pay_cap=65536, disk_cap=65536)
        lease_l = led.claim_r6_grant_session_atomic(vg_l, pl_l, "sess_red_l1", actual_now=now)["lease"]
        sup1 = _real_att(lease_l, pl_l, vg_l, "att_red_l1_1")
        sup2 = _real_att(lease_l, pl_l, vg_l, "att_red_l1_2")
        p1 = crs.mint_r6_raw_provenance_receipt(tested_executable_sha="133980ac9e28e663df8284617bc4306791a0f86a", run_id="red_133980a", session_id="sess_red_l1", lease_id=lease_l.lease_id, attempt_id="att_red_l1_1", endpoint_id="classic_v2_contracts", contract_digest=ep_c.content_digest, partition="QA_FIXTURE", qa_loopback_mode=True, transport_kind="REST_GET", supervisor_result=sup1["trusted_result"], raw_payload_bytes_data=sup1["raw_payload_bytes"], settlement_receipt=sup1["settlement_receipt"])
        p2 = crs.mint_r6_raw_provenance_receipt(tested_executable_sha="133980ac9e28e663df8284617bc4306791a0f86a", run_id="red_133980a", session_id="sess_red_l1", lease_id=lease_l.lease_id, attempt_id="att_red_l1_2", endpoint_id="classic_v2_contracts", contract_digest=ep_c.content_digest, partition="QA_FIXTURE", qa_loopback_mode=True, transport_kind="REST_GET", supervisor_result=sup2["trusted_result"], raw_payload_bytes_data=sup2["raw_payload_bytes"], settlement_receipt=sup2["settlement_receipt"])
        jnl_l = crs.R6CustodyJournal(approved_root=dest, partition="QA_FIXTURE", run_id="red_l1_run", repo_root=fake_repo)
        # Deterministic interleaving at exact boundary before seq read via Barrier(2) inside _fsync_file_and_parent!
        bar = threading.Barrier(2, timeout=5.0)
        orig_fsync = crs._fsync_file_and_parent
        bar_hits = [0]
        def _bar_fsync(pth):
            orig_fsync(pth)
            if str(pth).endswith(".norm.json"):
                bar_hits[0] += 1
                try: bar.wait()
                except Exception: pass
        crs._fsync_file_and_parent = _bar_fsync
        res_l = {}
        try:
            def _w(tag, cid, rawb, prov):
                res_l[tag] = jnl_l.persist_r6_verified_chunk(chunk_id=cid, raw_payload=rawb, normalized_rows=[{"w": tag}], provenance_receipt=prov)
            t1 = threading.Thread(target=_w, args=("w1", "chk_1", sup1["raw_payload_bytes"], p1))
            t2 = threading.Thread(target=_w, args=("w2", "chk_2", sup2["raw_payload_bytes"], p2))
            t1.start(); t2.start(); t1.join(timeout=10.0); t2.join(timeout=10.0)
        finally:
            crs._fsync_file_and_parent = orig_fsync
        chain_rows = [json.loads(ln) for ln in jnl_l.chain_path.read_text("utf-8").splitlines() if ln.strip()]
        seqs = [r["seq"] for r in chain_rows]
        ro_l = crs.verify_r6_custody_readonly(jnl_l.store_dir, expected_partition="QA_FIXTURE", expected_run_id="red_l1_run")
        has_tx_lock = hasattr(jnl_l, "_tx_lock")
        led.finalize_r6_session(lease_l, quarantine=False, reason="RED_L1_DONE", actual_now=now)
        receipts.append({
            "case_id": "RED-L1", "defect": "P1-L", "baseline_sha": "133980ac9e28e663df8284617bc4306791a0f86a",
            "reproduced": bool((not has_tx_lock) and (seqs == [1, 1] or not ro_l["valid"])),
            "pid": os.getpid(), "elapsed_sec": round(time.perf_counter() - t0, 4),
            "fault_boundary": "Barrier(2) inside R6CustodyJournal.persist_r6_verified_chunk before seq/prev_hash read",
            "witness": {"has_tx_lock": has_tx_lock, "observed_seqs": seqs, "readonly_valid": ro_l["valid"], "readonly_violations": ro_l["violations"], "barrier_hits": bar_hits[0]},
        })

        # ---- RED-M1: max(65536, plan.caps[payload_bytes]) / max(262144, plan.caps[disk_bytes]) enlargement + uncounted norm JSON bytes! ----
        t0 = time.perf_counter()
        acq_src = inspect.getsource(acqs.run_r6_a1_acquisition_session)
        enlargement_in_src = ("max(65_536," in acq_src) and ("max(262_144," in acq_src)
        # Also demonstrate uncounted normalized JSON bytes in R6CustodyJournal on 133980a!
        vg_m, pl_m = _mk_vg_plan("G_RED_M1", pay_cap=8192, disk_cap=16384)
        lease_m = led.claim_r6_grant_session_atomic(vg_m, pl_m, "sess_red_m1", actual_now=now)["lease"]
        sup_m = _real_att(lease_m, pl_m, vg_m, "att_red_m1")
        pm = crs.mint_r6_raw_provenance_receipt(tested_executable_sha="133980ac9e28e663df8284617bc4306791a0f86a", run_id="red_133980a", session_id="sess_red_m1", lease_id=lease_m.lease_id, attempt_id="att_red_m1", endpoint_id="classic_v2_contracts", contract_digest=ep_c.content_digest, partition="QA_FIXTURE", qa_loopback_mode=True, transport_kind="REST_GET", supervisor_result=sup_m["trusted_result"], raw_payload_bytes_data=sup_m["raw_payload_bytes"], settlement_receipt=sup_m["settlement_receipt"])
        jnl_m = crs.R6CustodyJournal(approved_root=dest, partition="QA_FIXTURE", run_id="red_m1_run", repo_root=fake_repo, max_chunk_bytes=2000, max_total_bytes=2000)
        big_norm = [{"pad": "X" * 5000}]  # 5000+ bytes UTF-8 > max_total_bytes=2000!
        pers_m = jnl_m.persist_r6_verified_chunk(chunk_id="chk_m1", raw_payload=sup_m["raw_payload_bytes"], normalized_rows=big_norm, provenance_receipt=pm)
        actual_disk_written = (jnl_m.chunks_dir / "chk_m1.raw.bin").stat().st_size + (jnl_m.chunks_dir / "chk_m1.norm.json").stat().st_size if pers_m.get("persisted") else 0
        led.finalize_r6_session(lease_m, quarantine=False, reason="RED_M1_DONE", actual_now=now)
        receipts.append({
            "case_id": "RED-M1", "defect": "P1-M", "baseline_sha": "133980ac9e28e663df8284617bc4306791a0f86a",
            "reproduced": bool(enlargement_in_src and pers_m.get("persisted") is True and actual_disk_written > 2000),
            "pid": os.getpid(), "elapsed_sec": round(time.perf_counter() - t0, 4),
            "fault_boundary": "run_r6_a1_acquisition_session max() cap enlargement + R6CustodyJournal omitting norm_bytes",
            "witness": {"enlargement_max_in_133980a_source": enlargement_in_src, "configured_max_total_bytes": 2000, "raw_bytes_len": len(sup_m["raw_payload_bytes"]), "actual_raw_plus_norm_written_bytes": actual_disk_written, "persisted_despite_exceeding_cap": bool(pers_m.get("persisted"))},
        })

        # ---- RED-N1a: Telemetry exception inside finally: skips finalize_r6_a1_acquisition_session -> grant stuck CLAIMED! ----
        t0 = time.perf_counter()
        vg_n1, pl_n1 = _mk_vg_plan("G_RED_N1A", pay_cap=65536, disk_cap=262144)
        calls_n1 = [0]
        def _flaky_res():
            calls_n1[0] += 1
            if calls_n1[0] >= 3:  # 1=build, 2=pre-claim, 3=finally!
                raise RuntimeError("INJECTED_TELEMETRY_ERROR_IN_FINALLY")
            return bts.collect_real_r6_resource_sample({"dest": dest, "ledger": sroot}, active_workers=1)
        b_n1 = acqs.build_r6_a1_acquisition_session(
            run_id="red_n1a", session_id="sess_red_n1a", verified_grant=vg_n1, plan=pl_n1, registry=reg,
            ledger=led, resource_sample_fn=_flaky_res, expected_volumes={"dest": dest, "ledger": sroot},
            repo_root=fake_repo, logical_profile="smoke", qa_loopback_mode=True,
            qa_http_base_url=f"http://127.0.0.1:{port}", actual_now=now,
        )
        n1a_exc = None
        try:
            acqs.run_r6_a1_acquisition_session(
                ctx=b_n1["context"], verified_grant=vg_n1, plan=pl_n1, registry=reg, ledger=led,
                resource_sample_fn=_flaky_res, expected_volumes={"dest": dest, "ledger": sroot},
                repo_root=fake_repo, utc_now_fn=lambda: now, cancel_between_claim_and_first_attempt=True,
            )
        except RuntimeError as e:
            n1a_exc = str(e)
        snap_n1a = led.snapshot_grant(vg_n1.grant_identity_key)
        receipts.append({
            "case_id": "RED-N1a", "defect": "P1-N", "baseline_sha": "133980ac9e28e663df8284617bc4306791a0f86a",
            "reproduced": bool(n1a_exc == "INJECTED_TELEMETRY_ERROR_IN_FINALLY" and snap_n1a is not None and snap_n1a["state"] == "CLAIMED"),
            "pid": os.getpid(), "elapsed_sec": round(time.perf_counter() - t0, 4),
            "fault_boundary": "resource_sample_fn() raises inside run_r6_a1_acquisition_session finally block",
            "witness": {"exception_raised": n1a_exc, "grant_state_left_in_db": snap_n1a["state"] if snap_n1a else None, "finalized_skipped": bool(snap_n1a and snap_n1a["state"] == "CLAIMED")},
        })

        # ---- RED-N1b: Exception between claim (outside try:) and try: leaves grant permanently CLAIMED! ----
        t0 = time.perf_counter()
        vg_n2, pl_n2 = _mk_vg_plan("G_RED_N1B", pay_cap=65536, disk_cap=262144)
        b_n2 = acqs.build_r6_a1_acquisition_session(
            run_id="red_n1b", session_id="sess_red_n1b", verified_grant=vg_n2, plan=pl_n2, registry=reg,
            ledger=led, resource_sample_fn=lambda: bts.collect_real_r6_resource_sample({"dest": dest, "ledger": sroot}, active_workers=1),
            expected_volumes={"dest": dest, "ledger": sroot}, repo_root=fake_repo, logical_profile="smoke",
            qa_loopback_mode=True, qa_http_base_url=f"http://127.0.0.1:{port}", actual_now=now,
        )
        orig_cj = acqs.R6CustodyJournal
        def _boom_cj(*a, **kw):
            raise OSError("INJECTED_CUSTODY_INIT_FAILURE_AFTER_CLAIM")
        acqs.R6CustodyJournal = _boom_cj
        n1b_exc = None
        try:
            acqs.run_r6_a1_acquisition_session(
                ctx=b_n2["context"], verified_grant=vg_n2, plan=pl_n2, registry=reg, ledger=led,
                resource_sample_fn=lambda: bts.collect_real_r6_resource_sample({"dest": dest, "ledger": sroot}, active_workers=1),
                expected_volumes={"dest": dest, "ledger": sroot}, repo_root=fake_repo, utc_now_fn=lambda: now,
            )
        except OSError as e:
            n1b_exc = str(e)
        finally:
            acqs.R6CustodyJournal = orig_cj
        snap_n1b = led.snapshot_grant(vg_n2.grant_identity_key)
        receipts.append({
            "case_id": "RED-N1b", "defect": "P1-N", "baseline_sha": "133980ac9e28e663df8284617bc4306791a0f86a",
            "reproduced": bool(n1b_exc == "INJECTED_CUSTODY_INIT_FAILURE_AFTER_CLAIM" and snap_n1b is not None and snap_n1b["state"] == "CLAIMED"),
            "pid": os.getpid(), "elapsed_sec": round(time.perf_counter() - t0, 4),
            "fault_boundary": "R6CustodyJournal init raises between claim (line 1345) and try: (line 1380)",
            "witness": {"exception_raised": n1b_exc, "grant_state_left_in_db": snap_n1b["state"] if snap_n1b else None, "finally_never_entered": bool(snap_n1b and snap_n1b["state"] == "CLAIMED")},
        })

        # ---- Positive control on untouched 133980a: single-thread clean persist works ----
        t0 = time.perf_counter()
        vg_pos, pl_pos = _mk_vg_plan("G_POS_133980A", pay_cap=65536, disk_cap=262144)
        lease_pos = led.claim_r6_grant_session_atomic(vg_pos, pl_pos, "sess_pos_133980a", actual_now=now)["lease"]
        sup_pos = _real_att(lease_pos, pl_pos, vg_pos, "att_pos_1")
        p_pos = crs.mint_r6_raw_provenance_receipt(tested_executable_sha="133980ac9e28e663df8284617bc4306791a0f86a", run_id="red_133980a", session_id="sess_pos_133980a", lease_id=lease_pos.lease_id, attempt_id="att_pos_1", endpoint_id="classic_v2_contracts", contract_digest=ep_c.content_digest, partition="QA_FIXTURE", qa_loopback_mode=True, transport_kind="REST_GET", supervisor_result=sup_pos["trusted_result"], raw_payload_bytes_data=sup_pos["raw_payload_bytes"], settlement_receipt=sup_pos["settlement_receipt"])
        jnl_pos = crs.R6CustodyJournal(approved_root=dest, partition="QA_FIXTURE", run_id="pos_133980a", repo_root=fake_repo)
        pers_pos = jnl_pos.persist_r6_verified_chunk(chunk_id="chk_pos", raw_payload=sup_pos["raw_payload_bytes"], normalized_rows=[{"ok": True}], provenance_receipt=p_pos)
        ro_pos = crs.verify_r6_custody_readonly(jnl_pos.store_dir, expected_partition="QA_FIXTURE", expected_run_id="pos_133980a")
        led.finalize_r6_session(lease_pos, quarantine=False, reason="POS_DONE", actual_now=now)
        receipts.append({
            "case_id": "POS-133980A", "defect": "POSITIVE_CONTROL", "baseline_sha": "133980ac9e28e663df8284617bc4306791a0f86a",
            "reproduced": bool(pers_pos.get("persisted") is True and ro_pos.get("valid") is True),
            "pid": os.getpid(), "elapsed_sec": round(time.perf_counter() - t0, 4),
            "fault_boundary": "clean serial single-chunk control on untouched 133980a",
            "witness": {"persisted": bool(pers_pos.get("persisted")), "readonly_valid": bool(ro_pos.get("valid")), "committed_chunks": ro_pos.get("committed_chunks")},
        })
finally:
    srv.shutdown(); srv.server_close(); th.join(timeout=2.0)

print(json.dumps({"pid": os.getpid(), "receipts": receipts}, sort_keys=True))
''',
            encoding="utf-8",
        )

        t0 = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, str(probe_script), str(overlay), str(repo_root)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        elapsed = round(time.perf_counter() - t0, 4)

    if proc.returncode != 0:
        return {
            "spec_version": SPEC_VERSION_KH03_R6_A1_CORRECTIVE,
            "schema_version": SCHEMA_VERSION_KH03_R6_A1_CORRECTIVE,
            "run_id": run_id,
            "tested_executable_sha": tested_sha,
            "status": "FAIL_M0_SUBPROCESS_ERROR",
            "baseline_sha": PRIOR_A1_REVIEWED_TESTED_SHA,
            "subprocess_exit_code": int(proc.returncode),
            "stderr_tail": proc.stderr[-1500:],
            "extracted_files_sha256": extracted_hashes,
            "reproduced_count": 0,
            "all_reproduced": False,
            "receipts": [],
        }

    parsed = json.loads(proc.stdout)
    recs = parsed["receipts"]
    red_recs = [r for r in recs if r["defect"] != "POSITIVE_CONTROL"]
    pos_recs = [r for r in recs if r["defect"] == "POSITIVE_CONTROL"]
    repro_cnt = sum(1 for r in red_recs if r.get("reproduced") is True)
    pos_ok = all(r.get("reproduced") is True for r in pos_recs) and len(pos_recs) >= 1
    all_ok = (repro_cnt == len(red_recs) and len(red_recs) == 4 and pos_ok)
    return {
        "spec_version": SPEC_VERSION_KH03_R6_A1_CORRECTIVE,
        "schema_version": SCHEMA_VERSION_KH03_R6_A1_CORRECTIVE,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "status": "PASS_ALL_P1_L_M_N_BASELINE_RED_REPRODUCED" if all_ok else "PARTIAL_OR_NOT_REPRODUCED",
        "baseline_sha": PRIOR_A1_REVIEWED_TESTED_SHA,
        "isolation_method": "git_show_133980ac_isolated_temp_overlay_zero_branch_mutation",
        "probe_pid": int(parsed["pid"]),
        "subprocess_exit_code": int(proc.returncode),
        "elapsed_sec": elapsed,
        "extracted_files_sha256": extracted_hashes,
        "red_hypotheses_total": len(red_recs),
        "reproduced_count": repro_cnt,
        "positive_control_ok": pos_ok,
        "all_reproduced": all_ok,
        "receipts": recs,
        "raw_stdout_sha256": hashlib.sha256(proc.stdout.encode("utf-8")).hexdigest(),
    }


# =====================================================================
# M6: Corrective Paired Fault Truth Engine (L01..L12, M01..M14, N01..N15)
# =====================================================================
CORRECTIVE_FAULT_PREDICATES: Dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "corr_neg_rejected_or_conserved": lambda obs: bool(obs.get("negative_enforced") is True and int(obs.get("violations_or_guards_triggered", 0)) >= 1),
    "corr_pos_clean_verified": lambda obs: bool(obs.get("positive_verified") is True and int(obs.get("unexpected_violations", 0)) == 0),
}


def execute_corrective_fault_pair(
    *,
    control_id: str,
    group: str,  # "L" | "M" | "N"
    fault_kind: str,  # "BASELINE_BUG_RED" | "INTEGRATION_FAULT" | "NEGATIVE_CONTRACT"
    target_symbol: str,
    description: str,
    negative_fn: Callable[[], Tuple[Dict[str, Any], Dict[str, Any]]],
    positive_fn: Callable[[], Tuple[Dict[str, Any], Dict[str, Any]]],
    executable_sha: str,
    run_id: str,
) -> Dict[str, Any]:
    t0_n = time.perf_counter()
    neg_in, neg_obs = negative_fn()
    neg_ms = round((time.perf_counter() - t0_n) * 1000.0, 3)
    neg_pass = CORRECTIVE_FAULT_PREDICATES["corr_neg_rejected_or_conserved"](neg_obs)

    t0_p = time.perf_counter()
    pos_in, pos_obs = positive_fn()
    pos_ms = round((time.perf_counter() - t0_p) * 1000.0, 3)
    pos_pass = CORRECTIVE_FAULT_PREDICATES["corr_pos_clean_verified"](pos_obs)

    rec = {
        "schema_version": "kh_03_r6_a1_corrective_paired_fault_v1",
        "control_id": control_id,
        "group": group,
        "fault_kind": fault_kind,
        "target_symbol": target_symbol,
        "description": description,
        "run_id": run_id,
        "tested_executable_sha": executable_sha,
        "negative": {
            "execution_id": f"{run_id}_{control_id}_neg_{uuid.uuid4().hex[:10]}",
            "predicate": "corr_neg_rejected_or_conserved",
            "elapsed_ms": neg_ms,
            "pid": int(os.getpid()),
            "input": neg_in,
            "observed": neg_obs,
            "passed": bool(neg_pass),
        },
        "positive": {
            "execution_id": f"{run_id}_{control_id}_pos_{uuid.uuid4().hex[:10]}",
            "predicate": "corr_pos_clean_verified",
            "elapsed_ms": pos_ms,
            "pid": int(os.getpid()),
            "input": pos_in,
            "observed": pos_obs,
            "passed": bool(pos_pass),
        },
        "status": "PASS" if (neg_pass and pos_pass) else "FAIL",
    }
    rec["receipt_sha256"] = _sha256_canonical_a1(rec)
    return rec


def derive_corrective_fault_verdict(
    receipts: Sequence[Mapping[str, Any]],
    *,
    expected_executable_sha: str,
    expected_run_id: str,
) -> Dict[str, Any]:
    viols: List[str] = []
    seen_ids: Set[str] = set()
    seen_exec_ids: Set[str] = set()
    by_group = {"L": 0, "M": 0, "N": 0}
    passed = 0
    for r in receipts:
        cid = str(r.get("control_id") or "")
        grp = str(r.get("group") or "")
        if cid in seen_ids:
            viols.append(f"DUP_CONTROL_ID:{cid}")
        seen_ids.add(cid)
        if r.get("run_id") != expected_run_id or r.get("tested_executable_sha") != expected_executable_sha:
            viols.append(f"PROVENANCE_DRIFT:{cid}")
        neg = r.get("negative") or {}
        pos = r.get("positive") or {}
        nid = str(neg.get("execution_id") or "")
        pid = str(pos.get("execution_id") or "")
        if not nid or not pid or nid == pid or nid in seen_exec_ids or pid in seen_exec_ids:
            viols.append(f"EXEC_ID_COLLISION:{cid}")
        seen_exec_ids.add(nid)
        seen_exec_ids.add(pid)
        n_ok = CORRECTIVE_FAULT_PREDICATES["corr_neg_rejected_or_conserved"](neg.get("observed") or {})
        p_ok = CORRECTIVE_FAULT_PREDICATES["corr_pos_clean_verified"](pos.get("observed") or {})
        if n_ok and p_ok:
            passed += 1
            if grp in by_group:
                by_group[grp] += 1
        else:
            viols.append(f"CONTROL_FAILED:{cid}:neg={n_ok}:pos={p_ok}")
    if by_group["L"] < 12:
        viols.append(f"GROUP_L_BELOW_12:{by_group['L']}")
    if by_group["M"] < 14:
        viols.append(f"GROUP_M_BELOW_14:{by_group['M']}")
    if by_group["N"] < 15:
        viols.append(f"GROUP_N_BELOW_15:{by_group['N']}")
    return {
        "valid": len(viols) == 0,
        "total_controls": len(receipts),
        "passed_controls": passed,
        "failed_controls": len(receipts) - passed,
        "counts_by_group": by_group,
        "violations": viols,
    }


# =====================================================================
# M6: 12 Mandatory Corrective Gates Engine (Separate from 36 A1 + 0/4 B/C!)
# =====================================================================
def derive_r6_a1_corrective_gates(
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    expected_run_id: str,
    expected_tested_sha: str,
    pre_verifier_mode: bool = False,
    remote_readback: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Independently evaluates the 12 mandatory corrective gates (`C01..C12`) strictly from raw
    artifact facts `00..24`. Keeps original 36 A1 gates & 0/4 external R6.B/C/D completely separate!
    """
    a00 = artifacts.get("00") or {}
    a01 = artifacts.get("01") or {}
    a02 = artifacts.get("02") or {}
    a03 = artifacts.get("03") or {}
    a04 = artifacts.get("04") or {}
    a05 = artifacts.get("05") or {}
    a06 = artifacts.get("06") or {}
    a07 = artifacts.get("07") or {}
    a08 = artifacts.get("08") or {}
    a09 = artifacts.get("09") or {}
    a10 = artifacts.get("10") or {}
    a11 = artifacts.get("11") or {}
    a12 = artifacts.get("12") or {}
    a13 = artifacts.get("13") or {}
    a14 = artifacts.get("14") or {}
    a15 = artifacts.get("15") or {}
    a16 = artifacts.get("16") or {}
    a17 = artifacts.get("17") or {}
    a18 = artifacts.get("18") or {}
    a19 = artifacts.get("19") or {}
    a20 = artifacts.get("20") or {}
    a21 = artifacts.get("21") or {}
    a23 = artifacts.get("23") or {}
    a24 = artifacts.get("24") or {}

    gates: Dict[str, str] = {}

    # C01: Real baseline RED reproduction on untouched 133980ac
    c01 = (
        a03.get("baseline_sha") == PRIOR_A1_REVIEWED_TESTED_SHA
        and a03.get("all_reproduced") is True
        and _is_strict_int_a1(a03.get("reproduced_count")) and int(a03["reproduced_count"]) >= 4
        and a03.get("positive_control_ok") is True
    )
    gates["C01_BASELINE_RED_REPRODUCTION"] = "PASS" if c01 else "FAIL"

    # C02: Single-writer ownership, full custody atomicity & crash restart
    c02 = (
        a04.get("concurrent_writers_serialized_unique_seq") is True
        and a04.get("cross_process_single_writer_enforced") is True
        and a04.get("direct_unauthorized_persist_denied") is True
        and a06.get("crash_intent_restart_zero_duplicates") is True
        and a06.get("concurrent_seal_before_writer_done_rejected") is True
    )
    gates["C02_SINGLE_WRITER_ATOMICITY_RESTART"] = "PASS" if c02 else "FAIL"

    # C03: Deterministic W1/W2/W4 logical custody identity & byte conservation
    c03 = (
        a05.get("w1_w2_w4_shuffled_order_identical_semantic_digest") is True
        and a05.get("w4_repeated_seeds_byte_conserved") is True
        and bool(a05.get("canonical_custody_semantic_sha256"))
    )
    gates["C03_DETERMINISTIC_W1_W2_W4_CUSTODY"] = "PASS" if c03 else "FAIL"

    # C04: Bounded queue, backpressure, anti-starvation, deadlock/cancel/late-write prevention
    c04 = (
        a05.get("bounded_queue_item_and_byte_backpressure_verified") is True
        and a05.get("head_slot_anti_starvation_verified") is True
        and a05.get("producer_failure_queue_full_no_deadlock_no_late_commit") is True
        and a05.get("forged_or_expired_envelope_denied") is True
    )
    gates["C04_BOUNDED_QUEUE_BACKPRESSURE_NO_LATE_WRITE"] = "PASS" if c04 else "FAIL"

    # C05: Hard signed/plan/policy cap intersection (NEVER `max()` enlargement!)
    c05 = (
        a07.get("max_cap_enlargement_removed_verified") is True
        and a07.get("narrow_plan_chunk_cap_plus1_rejected") is True
        and a07.get("conflicting_cap_cannot_enlarge_via_override") is True
        and a07.get("strict_int_bool_neg_overflow_rejected") is True
    )
    gates["C05_HARD_CAP_MIN_INTERSECTION_NO_ENLARGEMENT"] = "PASS" if c05 else "FAIL"

    # C06: Persistent atomic disk reservations incl. normalized UTF-8, peak overhead & global volume
    c06 = (
        a08.get("normalized_utf8_expansion_prewrite_rejected") is True
        and a08.get("peak_staging_manifest_wal_log_overhead_enforced") is True
        and a08.get("w4_concurrent_reservations_conserved") is True
        and a08.get("interrupted_write_uncertain_bytes_retained") is True
        and a08.get("emergency_reserve_and_wal_bounded") is True
    )
    gates["C06_ATOMIC_DISK_RESERVATIONS_PEAK_OVERHEAD"] = "PASS" if c06 else "FAIL"

    # C07: Real telemetry/pressure/cleanup/ENOSPC fail-closed & symlink/volume guard
    c07 = (
        a09.get("enospc_pressure_fail_closed_no_seal") is True
        and a09.get("cleanup_fail_retains_uncertain_no_outside_delete") is True
        and a09.get("symlink_junction_volume_substitution_rejected") is True
        and a09.get("stale_or_nan_telemetry_rejected") is True
        and _is_finite_num_a1(a09.get("min_observed_disk_free_gib")) and float(a09["min_observed_disk_free_gib"]) >= 20.0
        and _is_finite_num_a1(a09.get("min_observed_ram_available_mib")) and float(a09["min_observed_ram_available_mib"]) >= 512.0
        and _is_strict_int_a1(a09.get("max_observed_descendants")) and int(a09["max_observed_descendants"]) <= 4
        and a09.get("orphan_workers_count") == 0
    )
    gates["C07_REAL_TELEMETRY_PRESSURE_CLEANUP_ENOSPC"] = "PASS" if c07 else "FAIL"

    # C08: Claim exception & durable terminal state reconciliation
    c08 = (
        a10.get("preclaim_exception_zero_consumption") is True
        and a10.get("postclaim_return_exception_reconciled_once") is True
        and a10.get("immediate_postclaim_telemetry_exception_finalized") is True
        and a10.get("competing_finalizers_cas_idempotent") is True
        and a10.get("persist_failure_classified_unproven_terminal") is True
    )
    gates["C08_CLAIM_EXCEPTION_DURABLE_TERMINAL_RECONCILE"] = "PASS" if c08 else "FAIL"

    # C09: Finalizer exception isolation, cancellation, expired cleanup & owned worker reap
    c09 = (
        a11.get("finally_telemetry_exception_still_finalizes_ledger") is True
        and a11.get("original_and_cleanup_errors_preserved_separately") is True
        and a11.get("keyboard_interrupt_cancel_cleans_up_no_hidden_pass") is True
        and a11.get("unreaped_worker_quarantines_and_blocks_new_session") is True
        and a11.get("expired_deadline_cleanup_bounded_no_late_io") is True
    )
    gates["C09_FINALIZER_ISOLATION_CANCEL_OWNED_REAP"] = "PASS" if c09 else "FAIL"

    # C10: Compound crash/quarantine/restart/replay & no false seal
    c10 = (
        a12.get("compound_l_m_n_fault_quarantined_conserved") is True
        and a12.get("crash_restart_one_shot_spent_remains_spent") is True
        and a12.get("durable_finalized_after_aborted_not_acquisition_pass") is True
        and a12.get("positive_qa_e2e_full_chain_verified") is True
    )
    gates["C10_COMPOUND_CRASH_QUARANTINE_REPLAY_NO_SEAL"] = "PASS" if c10 else "FAIL"

    # C11: Independent verifier + fresh corrective mutations + all inherited gates/tests truth
    c11_base = (
        a14.get("original_36_a1_prepush_gates_passed") == 35
        and a14.get("external_r6_bcd_executed_count") == 0
        and a14.get("inherited_a1_55_paired_passed") >= 55
        and a14.get("inherited_r6_56_paired_passed") >= 56
        and a14.get("inherited_c1_c2_91_paired_passed") >= 91
        and a14.get("inherited_regression_288_passed") >= 288
        and a15.get("junit_bijection_exact") is True
        and a15.get("failed_count") == 0
        and a15.get("error_count") == 0
        and a15.get("mandatory_skipped_count") == 0
        and a20.get("new_lmn_paired_passed_count") >= 41
        and a20.get("new_lmn_paired_failed_count") == 0
        and a21.get("clean_clone_verifier_green") is True
        and _is_strict_int_a1(a21.get("new_corrective_mutations_tested_count")) and int(a21["new_corrective_mutations_tested_count"]) >= 18
        and a21.get("new_corrective_mutations_detected_count") == a21.get("new_corrective_mutations_tested_count")
        and _is_strict_int_a1(a21.get("inherited_mutations_tested_count")) and int(a21["inherited_mutations_tested_count"]) >= 201
        and a21.get("inherited_mutations_detected_count") == a21.get("inherited_mutations_tested_count")
    )
    if pre_verifier_mode:
        gates["C11_INDEPENDENT_VERIFIER_MUTATIONS_INHERITED"] = "PENDING_SEPARATE_OS_VERIFIER" if c11_base else "FAIL"
    else:
        v_ok = (
            a23.get("distinct_os_process_verified") is True
            and a23.get("verifier_status") == "PASS"
            and a23.get("violations_count") == 0
        )
        gates["C11_INDEPENDENT_VERIFIER_MUTATIONS_INHERITED"] = "PASS" if (c11_base and v_ok) else "FAIL"

    # C12: Protected source, prior sealed immutability, economic parity, frozen SHA & remote proof
    c12_local = (
        a00.get("branch") == MANDATORY_BRANCH_KH03_R6
        and a00.get("tested_executable_sha") == expected_tested_sha
        and a00.get("run_id") == expected_run_id
        and a00.get("worktree_clean_at_start") is True
        and a01.get("locked_files_modified_count") == 0
        and a01.get("unauthorized_changed_files_count") == 0
        and a01.get("secrets_detected_count") == 0
        and a01.get("forbidden_order_imports_count") == 0
        and a02.get("all_prior_sealed_artifacts_intact") is True
        and a02.get("oos_reads_executed") == 0
        and a02.get("oos_evaluations") == 0
        and a13.get("external_market_requests_attempted") == 0
        and a13.get("accepted_real_observed_records") == 0
        and a13.get("activation_ready") == "NO"
        and a13.get("economic_verdict") == "NO_PROVEN_EDGE"
        and a16.get("r4_decimal_bridge_exact_match") is True
        and a16.get("n_closed") == 203 and a16.get("realized_losses_gt_2p50_count") == 139
        and a17.get("all_six_scenarios_exact_match") is True and a17.get("all_three_arms_exact_match") is True
        and a18.get("a1_checked") == 56710 and a18.get("a1_mismatches") == 0
        and a18.get("si8_checked") == 6375 and a18.get("si8_mismatches") == 0
        and a18.get("adapters_checked") == 246 and a18.get("adapters_mismatches") == 0
        and a18.get("d4_mismatches") == 0
        and a19.get("all_workloads_deterministic") is True
        and a24.get("phase1_executable_committed") is True
        and a24.get("tested_executable_sha") == expected_tested_sha
    )
    if not c12_local:
        gates["C12_PROTECTED_SOURCE_PARITY_FROZEN_REMOTE"] = "FAIL"
    elif remote_readback is not None and remote_readback.get("all_sealed_files_remote_verified") is True and remote_readback.get("remote_tested_sha_verified") is True:
        gates["C12_PROTECTED_SOURCE_PARITY_FROZEN_REMOTE"] = "PASS_REMOTE_VERIFIED"
    else:
        gates["C12_PROTECTED_SOURCE_PARITY_FROZEN_REMOTE"] = "PENDING_REMOTE_READBACK"

    c_prepush_core = [g for g in CORRECTIVE_12_GATE_KEYS if g != "C12_PROTECTED_SOURCE_PARITY_FROZEN_REMOTE"]
    c_passed_core = sum(1 for g in c_prepush_core if gates[g] == "PASS")
    c12_remote_ok = gates["C12_PROTECTED_SOURCE_PARITY_FROZEN_REMOTE"] == "PASS_REMOTE_VERIFIED"
    c12_prepush_ok = gates["C12_PROTECTED_SOURCE_PARITY_FROZEN_REMOTE"] in ("PENDING_REMOTE_READBACK", "PASS_REMOTE_VERIFIED")

    if pre_verifier_mode:
        non_v = [g for g in c_prepush_core if g != "C11_INDEPENDENT_VERIFIER_MUTATIONS_INHERITED"]
        pre_ok = all(gates[g] == "PASS" for g in non_v) and gates["C11_INDEPENDENT_VERIFIER_MUTATIONS_INHERITED"] == "PENDING_SEPARATE_OS_VERIFIER" and c12_prepush_ok
        overall = "PENDING_SEPARATE_OS_VERIFIER_CORRECTIVE_10_OF_10_PRE_VERIFIER_PASS" if pre_ok else "FAIL_OR_BLOCKED_KH03_R6_A1_CORRECTIVE"
    else:
        all_prepush = (c_passed_core == len(c_prepush_core)) and c12_prepush_ok
        overall = CLASSIFICATION_PASS_OFFLINE_CORRECTIVE if all_prepush else "FAIL_OR_BLOCKED_KH03_R6_A1_CORRECTIVE"

    return {
        "spec_version": SPEC_VERSION_KH03_R6_A1_CORRECTIVE,
        "schema_version": SCHEMA_VERSION_KH03_R6_A1_CORRECTIVE,
        "run_id": expected_run_id,
        "tested_executable_sha": expected_tested_sha,
        "corrective_12_gates": gates,
        "corrective_denominators": {
            "corrective_mandatory_total": 12,
            "corrective_prepush_passed": c_passed_core,
            "corrective_passed_including_remote": c_passed_core + (1 if c12_remote_ok else 0),
            "original_a1_mandatory_total": 36,
            "original_a1_prepush_passed": int(a14.get("original_36_a1_prepush_gates_passed") or 0),
            "external_r6_bcd_total": 4,
            "external_r6_bcd_executed": 0,
            "never_conflate_denominators_into_40_or_48": True,
        },
        "overall_classification": overall,
        "status_matrix": {
            "A1_CORRECTIVE_ENGINEERING": "PASS" if overall == CLASSIFICATION_PASS_OFFLINE_CORRECTIVE else ("PENDING_VERIFIER" if pre_verifier_mode else "FAIL"),
            "CUSTODY_CONCURRENCY": "PASS_SINGLE_WRITER_ATOMIC_DETERMINISTIC" if (c02 and c03 and c04) else "FAIL",
            "DISK_BUDGET_CONSERVATION": "PASS_HARD_MIN_CAPS_ATOMIC_RESERVATIONS" if (c05 and c06 and c07) else "FAIL",
            "SESSION_FINALIZATION": "PASS_EXCEPTION_SAFE_CAS_QUARANTINE" if (c08 and c09 and c10) else "FAIL",
            "EXTERNAL_MARKET_CAPTURE": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED",
            "R6_B_C_EXTERNAL_GATES": "0/4 executed",
            "OBSERVED_PUBLIC_MARKET_DATA": "0 new records / 0 external requests",
            "ECONOMIC_EDGE": "NO_PROVEN_EDGE",
            "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA",
            "OOS_EVALUATIONS": 0,
            "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE",
            "ACTIVATION_READY": "NO",
            "TRADING_MODES": "LIVE=OFF | PAPER=OFF | AUTO=OFF | PRODUCTION=OFF",
        },
    }
