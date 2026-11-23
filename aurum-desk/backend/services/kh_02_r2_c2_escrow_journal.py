"""
aurum-desk/backend/services/kh_02_r2_c2_escrow_journal.py

AURUM DESK — KH-02.R2.C2 Durable Append-Only Concurrent Crash-Safe Escrow Journal
SPEC VERSION: KH-02.R2.C2-v1.0 | STDLIB ONLY (fcntl + os.open O_CREAT|O_EXCL / O_APPEND + fsync)

Closes the KH-02.R2.C1 P1 escrow weakness ("rewrite entire registry with os.replace"):
  - Content-addressed immutable snapshot blobs via O_CREAT|O_EXCL + fsync(blob) + fsync(dir).
  - Inter-process exclusive OS file lock (fcntl.flock LOCK_EX) over verify -> dedup -> append.
  - True append-only durable journal (O_APPEND + fsync + dir fsync) — NEVER rewrites prior lines.
  - Explicit crash-phase fault hooks and fail-closed corruption/torn-tail/orphan-blob detection
    with explicit quarantine (NEVER silently discards corrupt tail bytes).
  - Explicit threat-model distinction between LOCAL_TAMPER_EVIDENT_ONLY and external anchor
    rollback detection (NEVER claims hardware WORM when unanchored).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses msvcrt OS file locking
    fcntl = None  # type: ignore[assignment]
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

if sys.platform == "win32":
    import msvcrt

from services.kh_02_r2_c3_provenance_guard import (
    ProvenanceClassC3,
    TrustedCaptureReceiptC3,
    evaluate_c3_prospective_admission,
    reparse_and_verify_raw_market_bytes_c3,
)
from services.kh_02_r2_c4_trusted_transport_boundary import (
    bind_capture_proof_consumption_c4,
)
from services.kh_02_r2_prospective_data_service import (
    CANONICAL_DATASET_SHA256_KH02,
    KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
    R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    R2_AUTHORITATIVE_FROZEN_CODE_SHA,
    R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256,
    STRICT_RUNTIME_DISK_MIN_GIB_SHARED,
    validate_prospective_snapshot_contract_c1,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

SPEC_VERSION_KH02_R2_C2 = "KH-02.R2.C2-v1.0"
SCHEMA_VERSION_KH02_R2_C2_JOURNAL = "kh_02_r2_c2_durable_escrow_journal_v1"
GENESIS_PREV_SHA256_C2 = "0" * 64
SAFE_SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_json_bytes(obj: Mapping[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


_WIN_PATH_LOCKS_GUARD = threading.Lock()
_WIN_PATH_LOCKS: Dict[str, threading.RLock] = {}


def _get_thread_lock_for_path(lock_path: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(os.fspath(lock_path)))
    with _WIN_PATH_LOCKS_GUARD:
        lk = _WIN_PATH_LOCKS.get(key)
        if lk is None:
            lk = threading.RLock()
            _WIN_PATH_LOCKS[key] = lk
        return lk


@contextmanager
def _cross_platform_exclusive_fd_lock(lock_path: Path) -> Iterator[int]:
    """
    Inter-process + inter-thread exclusive OS file lock:
      - POSIX (macOS/Linux): fcntl.flock(LOCK_EX / LOCK_UN)
      - Windows (win32): per-path threading.RLock + msvcrt.locking(LK_NBLCK) retry loop on byte 0.
    """
    tlock = _get_thread_lock_for_path(lock_path) if sys.platform == "win32" else None
    if tlock is not None:
        tlock.acquire()
    lfd = os.open(_win_long_path(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    locked = False
    try:
        if fcntl is not None:
            fcntl.flock(lfd, fcntl.LOCK_EX)
            locked = True
        else:
            # Ensure >= 1 byte exists for Windows byte-range locking on byte 0
            st_size = os.fstat(lfd).st_size
            if st_size < 1:
                os.lseek(lfd, 0, os.SEEK_SET)
                os.write(lfd, b"0\n")
            deadline = time.monotonic() + 30.0
            while True:
                os.lseek(lfd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(lfd, msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"WINDOWS_OS_FILE_LOCK_TIMEOUT:{lock_path}")
                    time.sleep(0.005)
        yield lfd
    finally:
        try:
            if locked:
                if fcntl is not None:
                    fcntl.flock(lfd, fcntl.LOCK_UN)
                else:
                    os.lseek(lfd, 0, os.SEEK_SET)
                    msvcrt.locking(lfd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(lfd)
            if tlock is not None:
                tlock.release()


def _fsync_directory(dir_path: Path) -> None:
    """Durable directory fsync on POSIX; safe NTFS no-op on Windows after file fsync."""
    if sys.platform == "win32":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    dfd = os.open(os.fspath(dir_path), flags)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _win_long_path(path: Path) -> str:
    """Applies Windows extended-length path prefix (`\\\\?\\`) when path exceeds 230 chars."""
    s = os.path.abspath(os.fspath(path))
    if sys.platform == "win32" and len(s) >= 230 and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + s
    return s


def _exists_safe(path: Path) -> bool:
    return os.path.exists(_win_long_path(path))


def _is_file_safe(path: Path) -> bool:
    return os.path.isfile(_win_long_path(path))


def _is_dir_safe(path: Path) -> bool:
    return os.path.isdir(_win_long_path(path))


def _read_bytes_safe(path: Path) -> bytes:
    with open(_win_long_path(path), "rb") as _fh:
        return _fh.read()


def _mkdir_parents_safe(path: Path) -> None:
    os.makedirs(_win_long_path(path), exist_ok=True)


def _is_symlink_or_junction(path: Path) -> bool:
    """Detects POSIX symlinks AND Windows NTFS directory junctions (long-path safe)."""
    lp = _win_long_path(path)
    try:
        if os.path.islink(lp):
            return True
    except OSError:
        pass
    if hasattr(os.path, "isjunction"):
        try:
            return bool(os.path.isjunction(lp))
        except OSError:
            return False
    return False


def _strip_win_long_prefix(s: str) -> str:
    if sys.platform == "win32" and s.startswith("\\\\?\\"):
        return s[4:]
    return s


def _assert_not_symlink_and_contained(path: Path, root: Path) -> None:
    if _is_symlink_or_junction(path):
        raise PermissionError(f"SYMLINK_ESCAPE_FORBIDDEN:{path}")
    try:
        resolved_root = Path(_strip_win_long_prefix(os.path.realpath(_win_long_path(root))))
        resolved_path = Path(_strip_win_long_prefix(os.path.realpath(_win_long_path(path))))
        resolved_path.relative_to(resolved_root)
    except Exception as exc:
        raise PermissionError(f"PATH_ESCAPE_OUTSIDE_ESCROW_ROOT:{path}") from exc


def _write_all_eintr_safe(
    fd: int,
    data: bytes,
    *,
    _write_fn: Optional[Callable[[int, bytes], int]] = None,
) -> int:
    """
    Robust full-buffer write looping across EINTR and short kernel writes.
    Fails closed (`IOError`) if a short write returns 0 bytes (unable to make progress).
    """
    writer = _write_fn or os.write
    total = 0
    mv = memoryview(data)
    n = len(data)
    while total < n:
        try:
            w = int(writer(fd, bytes(mv[total:])))
        except InterruptedError:
            continue
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            raise
        if w <= 0:
            raise IOError(f"SHORT_WRITE_ZERO_PROGRESS:written={total},expected={n}")
        total += w
    return total


def _validate_snapshot_id_safe(snapshot_id: str) -> str:
    sid = str(snapshot_id or "").strip()
    if not sid or not SAFE_SNAPSHOT_ID_RE.match(sid) or ".." in sid:
        raise ValueError(f"UNSAFE_OR_INVALID_SNAPSHOT_ID:{snapshot_id!r}")
    return sid


class ExternalHeadAnchorC2:
    """
    Optional independent append-only head anchor stored at a separate destination.
    Uses inter-process fcntl.flock(LOCK_EX) + O_APPEND + fsync + dir fsync for one-writer
    atomicity. Distinguishes LOCAL_TAMPER_EVIDENT_ONLY (no anchor) from EXTERNAL_ANCHOR_ATTESTED
    and UNANCHORED_PENDING_ATTESTATION. Never claims hardware WORM on local disk.
    """

    def __init__(
        self,
        anchor_path: Path,
        *,
        anchor_trust_tier: str = "SEPARATE_LOCAL_APPEND_ONLY_FILE_NOT_HARDWARE_WORM",
    ) -> None:
        self.anchor_path = Path(anchor_path)
        self.anchor_lock_path = self.anchor_path.parent / f".{self.anchor_path.name}.lock"
        self.anchor_trust_tier = str(anchor_trust_tier)
        if _is_symlink_or_junction(self.anchor_path):
            raise PermissionError(f"SYMLINK_EXTERNAL_ANCHOR_FORBIDDEN:{self.anchor_path}")

    def read_anchors(self) -> List[Dict[str, Any]]:
        if not _exists_safe(self.anchor_path):
            return []
        if _is_symlink_or_junction(self.anchor_path):
            raise PermissionError(f"SYMLINK_EXTERNAL_ANCHOR_FORBIDDEN:{self.anchor_path}")
        raw = _read_bytes_safe(self.anchor_path)
        if not raw:
            return []
        if not raw.endswith(b"\n"):
            raise ValueError("CORRUPT_OR_TORN_EXTERNAL_ANCHOR_TAIL")
        out: List[Dict[str, Any]] = []
        prev = GENESIS_PREV_SHA256_C2
        for idx, raw_line in enumerate(raw.split(b"\n")[:-1]):
            rec = json.loads(raw_line.decode("utf-8"))
            if rec.get("prev_anchor_sha256") != prev:
                raise ValueError(f"BROKEN_EXTERNAL_ANCHOR_CHAIN:idx={idx}")
            body = {k: v for k, v in rec.items() if k != "anchor_entry_sha256"}
            if rec.get("anchor_entry_sha256") != canonical_hash(body):
                raise ValueError(f"TAMPERED_EXTERNAL_ANCHOR_ENTRY:idx={idx}")
            prev = str(rec["anchor_entry_sha256"])
            out.append(rec)
        return out

    def append_head_attestation(
        self,
        *,
        journal_record_count: int,
        head_seq_index: int,
        head_entry_sha256: str,
        journal_id: str,
    ) -> Dict[str, Any]:
        _mkdir_parents_safe(self.anchor_path.parent)
        if _is_symlink_or_junction(self.anchor_path) or _is_symlink_or_junction(self.anchor_lock_path):
            raise PermissionError(f"SYMLINK_EXTERNAL_ANCHOR_FORBIDDEN:{self.anchor_path}")
        with _cross_platform_exclusive_fd_lock(self.anchor_lock_path):
            existing = self.read_anchors()
            prev_sha = existing[-1]["anchor_entry_sha256"] if existing else GENESIS_PREV_SHA256_C2
            if existing and int(journal_record_count) < int(existing[-1]["journal_record_count"]):
                raise PermissionError(
                    f"ANCHOR_REFUSES_ROLLBACK:new_count={journal_record_count}<anchored={existing[-1]['journal_record_count']}"
                )
            body = {
                "anchor_seq": len(existing),
                "prev_anchor_sha256": prev_sha,
                "journal_id": str(journal_id),
                "journal_record_count": int(journal_record_count),
                "head_seq_index": int(head_seq_index),
                "head_entry_sha256": str(head_entry_sha256),
                "anchored_utc": _utc_now_iso(),
                "anchor_trust_tier": self.anchor_trust_tier,
            }
            rec = dict(body, anchor_entry_sha256=canonical_hash(body))
            line_bytes = _canonical_json_bytes(rec) + b"\n"
            fd = os.open(_win_long_path(self.anchor_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                _write_all_eintr_safe(fd, line_bytes)
                os.fsync(fd)
            finally:
                os.close(fd)
            _fsync_directory(self.anchor_path.parent)
            return rec


class C2DurableEscrowJournal:
    """
    KH-02.R2.C2/C3 Durable Append-Only Escrow Journal:
      <escrow_root>/
        .escrow.lock            (inter-process fcntl.flock exclusive lock)
        blobs/<sha256>.blob     (content-addressed immutable payload blobs via O_CREAT|O_EXCL)
        journal.jsonl           (strict O_APPEND hash-linked durable log; never rewritten on success)
        quarantine/             (explicit owner-reviewed quarantine for corrupt tails/orphans)
    """

    def __init__(
        self,
        escrow_root: Path,
        *,
        external_anchor: Optional[ExternalHeadAnchorC2] = None,
        journal_id: str = "kh_02_r2_c2_prospective_escrow",
        allow_synthetic_in_isolated_tmp: bool = False,
        strict_real_oos_admission: bool = False,
        isolated_qa_mode: bool = False,
        os_disk_probe_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        raw_root = Path(escrow_root)
        if _is_symlink_or_junction(raw_root):
            raise PermissionError(f"SYMLINK_ESCROW_ROOT_FORBIDDEN:{raw_root}")
        self.escrow_root = raw_root.resolve()
        self.blobs_dir = self.escrow_root / "blobs"
        self.journal_path = self.escrow_root / "journal.jsonl"
        self.lock_path = self.escrow_root / ".escrow.lock"
        self.quarantine_dir = self.escrow_root / "quarantine"
        self.external_anchor = external_anchor
        self.journal_id = str(journal_id)
        self.allow_synthetic_in_isolated_tmp = bool(allow_synthetic_in_isolated_tmp)
        self.strict_real_oos_admission = bool(strict_real_oos_admission)
        self.isolated_qa_mode = bool(isolated_qa_mode)
        self._os_disk_probe_fn = os_disk_probe_fn
        self._active_lock_owner_pid: Optional[int] = None

    @classmethod
    def open_real_oos_escrow(
        cls,
        escrow_root: Path,
        *,
        external_anchor: Optional[ExternalHeadAnchorC2] = None,
        journal_id: str = "kh_02_r2_c3_real_oos_escrow",
        os_disk_probe_fn: Optional[Callable[[], float]] = None,
    ) -> "C2DurableEscrowJournal":
        """Opens a fail-closed C3 real-OOS escrow refusing fake, synthetic, or metadata-only inputs."""
        return cls(
            escrow_root,
            external_anchor=external_anchor,
            journal_id=journal_id,
            allow_synthetic_in_isolated_tmp=False,
            strict_real_oos_admission=True,
            isolated_qa_mode=False,
            os_disk_probe_fn=os_disk_probe_fn,
        )

    def _ensure_layout(self) -> None:
        if _is_symlink_or_junction(self.escrow_root):
            raise PermissionError(f"SYMLINK_ESCROW_ROOT_FORBIDDEN:{self.escrow_root}")
        _mkdir_parents_safe(self.escrow_root)
        for sub in (self.blobs_dir, self.quarantine_dir):
            if _is_symlink_or_junction(sub):
                raise PermissionError(f"SYMLINK_ESCROW_SUBDIR_FORBIDDEN:{sub}")
            _mkdir_parents_safe(sub)
        for fpath in (self.journal_path, self.lock_path):
            if _is_symlink_or_junction(fpath):
                raise PermissionError(f"SYMLINK_ESCROW_FILE_FORBIDDEN:{fpath}")

    @contextmanager
    def exclusive_lock(self, *, expected_owner_pid: Optional[int] = None) -> Iterator[int]:
        """Acquires inter-process exclusive OS lock (POSIX fcntl.flock / Windows msvcrt.locking)."""
        self._ensure_layout()
        pid_now = os.getpid()
        if expected_owner_pid is not None and int(expected_owner_pid) != pid_now:
            raise PermissionError(f"WRONG_LOCK_OWNER:expected={expected_owner_pid},actual={pid_now}")
        with _cross_platform_exclusive_fd_lock(self.lock_path) as lfd:
            try:
                self._active_lock_owner_pid = pid_now
                if sys.platform != "win32":
                    os.ftruncate(lfd, 0)
                    os.lseek(lfd, 0, os.SEEK_SET)
                    os.write(lfd, f"{pid_now}\n".encode("utf-8"))
                else:
                    # Keep byte 0 locked intact; write PID metadata starting at byte 1
                    os.lseek(lfd, 1, os.SEEK_SET)
                    os.write(lfd, f"{pid_now:<15}\n".encode("utf-8"))
                os.fsync(lfd)
                yield lfd
            finally:
                self._active_lock_owner_pid = None

    def _parse_journal_bytes(self) -> Tuple[List[Dict[str, Any]], List[str], Optional[bytes]]:
        """
        Parses raw journal bytes strictly. Returns (records, violations, corrupt_tail_bytes).
        NEVER silently drops a torn/unterminated tail line.
        """
        if not _exists_safe(self.journal_path):
            return [], [], None
        _assert_not_symlink_and_contained(self.journal_path, self.escrow_root)
        raw = _read_bytes_safe(self.journal_path)
        if not raw:
            return [], [], None

        violations: List[str] = []
        records: List[Dict[str, Any]] = []
        corrupt_tail: Optional[bytes] = None

        parts = raw.split(b"\n")
        if not raw.endswith(b"\n"):
            corrupt_tail = parts[-1]
            violations.append(f"CORRUPT_OR_TORN_JOURNAL_TAIL:unterminated_bytes={len(corrupt_tail)}")
            lines_to_parse = parts[:-1]
        else:
            lines_to_parse = parts[:-1]

        for idx, line_b in enumerate(lines_to_parse):
            if not line_b.strip():
                violations.append(f"EMPTY_OR_BLANK_JOURNAL_LINE:idx={idx}")
                continue
            try:
                rec = json.loads(line_b.decode("utf-8"))
                if not isinstance(rec, dict):
                    raise ValueError("RECORD_NOT_JSON_OBJECT")
                records.append(rec)
            except Exception as exc:
                if idx == len(lines_to_parse) - 1 and corrupt_tail is None:
                    corrupt_tail = line_b + b"\n"
                violations.append(f"CORRUPT_OR_TORN_JOURNAL_JSON:idx={idx}:{type(exc).__name__}")
                break
        return records, violations, corrupt_tail

    def verify_integrity(self) -> Dict[str, Any]:
        """
        Independently verifies EVERY journal line's hash chain, sequence number, duplicate IDs,
        EVERY referenced blob's raw bytes SHA256 on disk, orphan blobs, AND external anchor
        rollback consistency (explicitly reporting LOCAL_TAMPER_EVIDENT_ONLY if unanchored).
        """
        violations: List[str] = []
        orphan_blobs: List[str] = []
        if _is_symlink_or_junction(self.escrow_root):
            return {
                "spec_version": SPEC_VERSION_KH02_R2_C2,
                "valid": False,
                "status": "FAIL_SYMLINK_ROOT",
                "record_count": 0,
                "violations": ["SYMLINK_ESCROW_ROOT_FORBIDDEN"],
            }

        records, parse_violations, corrupt_tail = self._parse_journal_bytes()
        violations.extend(parse_violations)

        prev_sha = GENESIS_PREV_SHA256_C2
        seen_ids: set[str] = set()
        referenced_blob_names: set[str] = set()
        seq_to_sha: Dict[int, str] = {}

        for idx, rec in enumerate(records):
            seq = rec.get("seq_index")
            if seq != idx:
                violations.append(f"NON_CONTIGUOUS_SEQ_INDEX:expected={idx},actual={seq}")
            sid = str(rec.get("snapshot_id") or "")
            if not sid:
                violations.append(f"MISSING_SNAPSHOT_ID:idx={idx}")
            elif sid in seen_ids:
                violations.append(f"DUPLICATE_SNAPSHOT_ID_IN_JOURNAL:{sid}")
            seen_ids.add(sid)

            if rec.get("prev_entry_sha256") != prev_sha:
                violations.append(f"BROKEN_HASH_CHAIN_PREV_MISMATCH:idx={idx}")
            body = {k: v for k, v in rec.items() if k != "entry_sha256"}
            recomp = canonical_hash(body)
            if rec.get("entry_sha256") != recomp:
                violations.append(f"TAMPERED_JOURNAL_ENTRY_SHA256:idx={idx}:sid={sid}")
            if int(rec.get("OOS_EVALUATIONS", -1)) != 0:
                violations.append(f"NONZERO_OOS_EVALUATIONS_IN_JOURNAL:idx={idx}")

            # Verify raw blob bytes on disk match recorded content_sha256
            blob_rel = str(rec.get("blob_rel_path") or "")
            expected_csha = str(rec.get("content_sha256") or "").lower()
            blob_p = self.escrow_root / blob_rel
            try:
                _assert_not_symlink_and_contained(blob_p, self.escrow_root)
            except PermissionError as exc:
                violations.append(str(exc))
            else:
                if not _is_file_safe(blob_p):
                    violations.append(f"RECORD_WITHOUT_BLOB:idx={idx}:blob={blob_rel}")
                else:
                    referenced_blob_names.add(blob_p.name)
                    actual_blob_sha = hashlib.sha256(_read_bytes_safe(blob_p)).hexdigest()
                    if actual_blob_sha != expected_csha:
                        violations.append(
                            f"BLOB_BYTES_SHA256_MISMATCH:idx={idx}:expected={expected_csha},actual={actual_blob_sha}"
                        )

            prev_sha = str(rec.get("entry_sha256") or "")
            seq_to_sha[idx] = prev_sha

        # Detect orphan uncommitted blobs in blobs/
        if _is_dir_safe(self.blobs_dir) and not _is_symlink_or_junction(self.blobs_dir):
            for bname in sorted(os.listdir(_win_long_path(self.blobs_dir))):
                bp = self.blobs_dir / bname
                if _is_symlink_or_junction(bp):
                    violations.append(f"SYMLINK_IN_BLOBS_DIR:{bp.name}")
                elif _is_file_safe(bp) and bp.name.endswith(".blob") and bp.name not in referenced_blob_names:
                    orphan_blobs.append(bp.name)
                    violations.append(f"ORPHAN_UNCOMMITTED_BLOB:{bp.name}")

        # External anchor rollback check & honest WORM distinction (§5.2)
        if self.external_anchor is None:
            immutability_class = "LOCAL_TAMPER_EVIDENT_ONLY"
            worm_verified = False
            rollback_status = "ROLLBACK_NOT_DETECTABLE_WITHOUT_EXTERNAL_ANCHOR"
            anchor_summary: Dict[str, Any] = {"configured": False, "anchored_count": 0}
        else:
            worm_verified = False  # Never claim hardware WORM from a local file anchor
            try:
                anchors = self.external_anchor.read_anchors()
            except Exception as exc:
                violations.append(f"EXTERNAL_ANCHOR_CORRUPT:{exc}")
                immutability_class = "EXTERNAL_ANCHOR_CORRUPT"
                rollback_status = "FAIL_EXTERNAL_ANCHOR_CORRUPT"
                anchor_summary = {"configured": True, "error": str(exc)}
            else:
                if not anchors:
                    if len(records) > 0:
                        immutability_class = "LOCAL_COMMITTED_UNANCHORED_PENDING_ATTESTATION"
                        rollback_status = "UNANCHORED_PENDING_ATTESTATION"
                    else:
                        immutability_class = "LOCAL_TAMPER_EVIDENT_ONLY"
                        rollback_status = "EXTERNAL_ANCHOR_EMPTY_NO_ATTESTATION_YET"
                    anchor_summary = {"configured": True, "anchored_count": 0, "head_attested": False}
                else:
                    latest = anchors[-1]
                    anc_cnt = int(latest["journal_record_count"])
                    anc_seq = int(latest["head_seq_index"])
                    anc_sha = str(latest["head_entry_sha256"])
                    if len(records) < anc_cnt:
                        violations.append(
                            f"JOURNAL_ROLLBACK_DETECTED:record_count={len(records)}<anchored={anc_cnt}"
                        )
                        immutability_class = "ROLLBACK_TAMPER_DETECTED"
                        rollback_status = "FAIL_ROLLBACK_DETECTED_BELOW_EXTERNAL_ANCHOR"
                        head_att = False
                    elif anc_seq >= 0 and seq_to_sha.get(anc_seq) != anc_sha:
                        violations.append(
                            f"JOURNAL_FORK_OR_REWRITE_DETECTED:seq={anc_seq}:actual={seq_to_sha.get(anc_seq)}!=anchored={anc_sha}"
                        )
                        immutability_class = "FORK_TAMPER_DETECTED"
                        rollback_status = "FAIL_JOURNAL_DIVERGED_FROM_EXTERNAL_ANCHOR"
                        head_att = False
                    elif len(records) > anc_cnt:
                        # P0-C / §09-20: Crash after journal fsync before anchor append!
                        immutability_class = "LOCAL_COMMITTED_UNANCHORED_PENDING_ATTESTATION"
                        rollback_status = "UNANCHORED_PENDING_ATTESTATION"
                        head_att = False
                    else:
                        immutability_class = "LOCAL_TAMPER_EVIDENT_WITH_EXTERNAL_ANCHOR_ATTESTED"
                        rollback_status = "EXTERNAL_ANCHOR_CONSISTENT"
                        head_att = True
                    anchor_summary = {
                        "configured": True,
                        "anchored_count": len(anchors),
                        "latest_anchored_record_count": anc_cnt,
                        "latest_anchored_head_sha256": anc_sha,
                        "head_attested": head_att,
                        "anchor_trust_tier": self.external_anchor.anchor_trust_tier,
                    }

        valid = len(violations) == 0
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "schema_version": SCHEMA_VERSION_KH02_R2_C2_JOURNAL,
            "escrow_root": str(self.escrow_root),
            "valid": valid,
            "status": "PASS" if valid else "FAIL_CLOSED_INTEGRITY_VIOLATION",
            "record_count": len(records),
            "head_seq_index": len(records) - 1,
            "head_entry_sha256": prev_sha,
            "has_corrupt_tail": corrupt_tail is not None,
            "orphan_blobs": orphan_blobs,
            "immutability_class": immutability_class,
            "worm_verified": worm_verified,
            "rollback_detection_status": rollback_status,
            "external_anchor": anchor_summary,
            "threat_model_disclosure": (
                "An append-only local hash chain detects accidental corruption and naive edits "
                "(LOCAL_TAMPER_EVIDENT_ONLY), but a privileged actor with write/root access to the "
                "same host filesystem could rewrite both journal and local anchor unless anchored "
                "to an independent remote WORM/signed store."
            ),
            "violations": violations,
        }

    def append_verified_prospective_snapshot(
        self,
        entry: Mapping[str, Any],
        *,
        raw_payload_bytes: Optional[bytes],
        capture_receipt: Optional[TrustedCaptureReceiptC3],
        freeze_timestamp_ms: int = R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
        known_dev_shas: Sequence[str] = (CANONICAL_DATASET_SHA256_KH02, KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256),
        _crash_hook: Optional[Callable[[str], None]] = None,
        _expected_lock_owner_pid: Optional[int] = None,
        _write_fn: Optional[Callable[[int, bytes], int]] = None,
    ) -> Dict[str, Any]:
        """
        KH-02.R2.C3 fail-closed provenance-verified escrow append entrypoint.
        Enforces P0-A + P0-C: requires raw HTTP bytes + HMAC-verified TrustedCaptureReceiptC3
        and re-validates inside exclusive lock. Rejects fake/synthetic/metadata-only for real OOS.
        """
        return self.append_snapshot(
            entry,
            raw_payload_bytes=raw_payload_bytes,
            capture_receipt=capture_receipt,
            require_c3_provenance=True,
            freeze_timestamp_ms=freeze_timestamp_ms,
            known_dev_shas=known_dev_shas,
            _crash_hook=_crash_hook,
            _expected_lock_owner_pid=_expected_lock_owner_pid,
            _write_fn=_write_fn,
        )

    def append_snapshot(
        self,
        entry: Mapping[str, Any],
        *,
        raw_payload_bytes: Optional[bytes] = None,
        capture_receipt: Optional[TrustedCaptureReceiptC3] = None,
        require_c3_provenance: bool = False,
        freeze_timestamp_ms: int = R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
        known_dev_shas: Sequence[str] = (CANONICAL_DATASET_SHA256_KH02, KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256),
        _crash_hook: Optional[Callable[[str], None]] = None,
        _expected_lock_owner_pid: Optional[int] = None,
        _write_fn: Optional[Callable[[int, bytes], int]] = None,
    ) -> Dict[str, Any]:
        """
        Atomically & durably appends one validated prospective snapshot under an exclusive
        inter-process OS lock (`fcntl.flock LOCK_EX`):
          1. Validate snapshot contract AND (under C3 strict/provenance mode) raw bytes +
             HMAC-verified TrustedCaptureReceiptC3 + raw-byte reparse BEFORE and INSIDE lock.
          2. Under lock: verify fresh OS disk >= 20.0 GiB (when strict/probed), verify journal +
             blobs integrity (fail closed on corruption).
          3. Idempotency check: same snapshot_id + same content_sha -> IDEMPOTENT_ALREADY_RECORDED_NOOP;
             same snapshot_id + different content_sha -> PermissionError.
          4. Write immutable content-addressed blob via O_CREAT|O_EXCL + EINTR-safe write -> fsync(blob) -> fsync(blobs_dir).
          5. Append single canonical JSONL record via O_APPEND + EINTR-safe write -> fsync(journal) -> fsync(root).
          6. If external_anchor configured: append durable locked head attestation.
        """
        if bool(entry.get("is_synthetic_fixture", False)) and not self.allow_synthetic_in_isolated_tmp:
            raise ValueError("SYNTHETIC_FIXTURE_FORBIDDEN_IN_ESCROW_JOURNAL")

        enforce_c3 = bool(self.strict_real_oos_admission or require_c3_provenance)
        require_live_real = bool(self.strict_real_oos_admission or (require_c3_provenance and not self.isolated_qa_mode))

        sid = _validate_snapshot_id_safe(str(entry.get("snapshot_id") or ""))

        def _validate_all() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            val_c1 = validate_prospective_snapshot_contract_c1(
                entry,
                freeze_timestamp_ms=freeze_timestamp_ms,
                known_dev_shas=known_dev_shas,
            )
            if not val_c1["valid_for_escrow_admission"]:
                raise ValueError(f"INVALID_PROSPECTIVE_SNAPSHOT:{val_c1['violations']}")

            if enforce_c3:
                if raw_payload_bytes is None:
                    raise ValueError("RAW_PAYLOAD_BYTES_REQUIRED_FOR_REAL_OOS")
                adm = evaluate_c3_prospective_admission(
                    entry,
                    raw_payload_bytes=raw_payload_bytes,
                    capture_receipt=capture_receipt,
                    require_live_real_oos=require_live_real,
                    freeze_timestamp_ms=freeze_timestamp_ms,
                    known_dev_shas=known_dev_shas,
                )
                if not adm["admitted"]:
                    viols_str = ";".join(adm["violations"])
                    if any(
                        tok in viols_str
                        for tok in (
                            "UNTRUSTED_PROVENANCE_CLASS",
                            "FORGED_OR_TAMPERED_CAPTURE_RECEIPT",
                            "MISSING_TRUSTED_CAPTURE_RECEIPT",
                            "RECEIPT_NOT_TRUSTED",
                            "LIVE_RATE_LIMITER_NOT_ACTIVE",
                            "C4_PROOF_",
                            "EXPIRED_C4_CAPTURE_PROOF",
                            "UNREGISTERED_OR_CROSS_PROCESS",
                            "MISSING_C4_CAPTURE_PROOF",
                            "FORGED_OR_CLONED_C4_ORIGIN_TOKEN",
                        )
                    ):
                        raise PermissionError(f"UNTRUSTED_OR_SYNTHETIC_PROVENANCE_FORBIDDEN:{viols_str}")
                    raise ValueError(f"C3_PROVENANCE_OR_RAW_REPARSE_REJECTED:{viols_str}")
                return val_c1, adm

            # Non-strict backward-compat mode: still verify raw bytes strictly if provided,
            # and NEVER classify metadata-only or fake-transport records as eligible fresh OOS!
            if raw_payload_bytes is not None:
                actual_raw_sha = hashlib.sha256(raw_payload_bytes).hexdigest()
                if actual_raw_sha != str(entry["raw_response_sha256"]).lower():
                    raise ValueError(
                        f"RAW_PAYLOAD_BYTES_SHA256_MISMATCH:claimed={entry['raw_response_sha256']},actual={actual_raw_sha}"
                    )
                if capture_receipt is not None:
                    rp = reparse_and_verify_raw_market_bytes_c3(
                        raw_payload_bytes,
                        entry,
                        capture_receipt=capture_receipt,
                        freeze_timestamp_ms=freeze_timestamp_ms,
                        known_dev_shas=known_dev_shas,
                    )
                    if not rp["valid"]:
                        raise ValueError(f"RAW_BYTES_REPARSE_FAILED:{rp['violations']}")
                p_cls = capture_receipt.provenance_class.value if isinstance(capture_receipt, TrustedCaptureReceiptC3) else ProvenanceClassC3.UNVERIFIED.value
                adm_fallback = {
                    "provenance_class": p_cls,
                    "admission_tier": (
                        "TEST_ONLY_NOT_ELIGIBLE_OOS"
                        if p_cls == ProvenanceClassC3.QA_FAKE_TRANSPORT.value
                        else "LEGACY_UNVERIFIED_NON_OOS"
                    ),
                    "eligible_for_fresh_oos": False,
                }
                return val_c1, adm_fallback

            adm_meta = {
                "provenance_class": ProvenanceClassC3.LEGACY_UNVERIFIED.value,
                "admission_tier": "LEGACY_METADATA_ONLY_NON_OOS",
                "eligible_for_fresh_oos": False,
            }
            return val_c1, adm_meta

        # Pre-lock validation
        val, adm_info = _validate_all()

        blob_bytes = raw_payload_bytes if raw_payload_bytes is not None else _canonical_json_bytes(entry)
        content_sha = hashlib.sha256(blob_bytes).hexdigest()
        blob_rel = f"blobs/{content_sha}.blob"
        blob_path = self.escrow_root / blob_rel

        def _hook(phase: str) -> None:
            if _crash_hook is not None:
                _crash_hook(phase)

        with self.exclusive_lock(expected_owner_pid=_expected_lock_owner_pid):
            # Re-validate INSIDE exclusive lock (zero TOCTOU between validator and durable append!)
            val, adm_info = _validate_all()

            if enforce_c3 and isinstance(capture_receipt, TrustedCaptureReceiptC3) and str(capture_receipt.capture_proof_id or "").strip():
                ok_bind, bind_errs = bind_capture_proof_consumption_c4(
                    capture_receipt.capture_proof_id,
                    snapshot_id=sid,
                    content_sha256=content_sha,
                )
                if not ok_bind:
                    raise PermissionError(";".join(bind_errs))

            if enforce_c3 or self._os_disk_probe_fn is not None:
                free_gib = (
                    float(self._os_disk_probe_fn())
                    if self._os_disk_probe_fn is not None
                    else (shutil.disk_usage(self.escrow_root).free / float(1024 ** 3))
                )
                if free_gib < STRICT_RUNTIME_DISK_MIN_GIB_SHARED:
                    raise RuntimeError(
                        f"BLOCKED_LOW_DISK_IN_ESCROW_APPEND:free_gib={free_gib:.4f}<{STRICT_RUNTIME_DISK_MIN_GIB_SHARED}"
                    )

            integrity = self.verify_integrity()
            if not integrity["valid"]:
                raise RuntimeError(f"FAIL_CLOSED_CORRUPT_ESCROW_JOURNAL:{integrity['violations']}")

            records, _, _ = self._parse_journal_bytes()
            for existing in records:
                if existing.get("snapshot_id") == sid:
                    if existing.get("content_sha256") == content_sha:
                        return {
                            "action": "IDEMPOTENT_ALREADY_RECORDED_NOOP",
                            "snapshot_id": sid,
                            "content_sha256": content_sha,
                            "entry_sha256": existing["entry_sha256"],
                            "seq_index": existing["seq_index"],
                            "record_count": len(records),
                            "journal_prior_bytes_untouched": True,
                            "provenance_class": adm_info["provenance_class"],
                            "admission_tier": adm_info["admission_tier"],
                            "eligible_for_fresh_oos": bool(adm_info["eligible_for_fresh_oos"]),
                        }
                    raise PermissionError(f"IMMUTABLE_REGISTRY_OVERWRITE_FORBIDDEN:{sid}")

            # Phase 1: Write immutable blob exclusively via O_CREAT | O_EXCL + EINTR-safe full write
            _assert_not_symlink_and_contained(blob_path, self.escrow_root)
            _hook("BEFORE_BLOB_WRITE")
            blob_created = False
            try:
                bfd = os.open(_win_long_path(blob_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                blob_created = True
                try:
                    _hook("ENOSPC_BLOB_WRITE")
                    _hook("EIO_BLOB_WRITE")
                    _write_all_eintr_safe(bfd, blob_bytes, _write_fn=_write_fn)
                    _hook("AFTER_BLOB_WRITE_BEFORE_FSYNC")
                    _hook("FSYNC_FAILURE")
                    os.fsync(bfd)
                finally:
                    os.close(bfd)
                _fsync_directory(self.blobs_dir)
            except FileExistsError:
                existing_sha = hashlib.sha256(_read_bytes_safe(blob_path)).hexdigest()
                if existing_sha != content_sha:
                    raise PermissionError(f"EXISTING_BLOB_CORRUPT_OR_COLLISION:{blob_rel}")

            _hook("AFTER_BLOB_FSYNC_BEFORE_JOURNAL_APPEND")

            # Phase 2: Build strict append-only journal envelope
            prev_sha = records[-1]["entry_sha256"] if records else GENESIS_PREV_SHA256_C2
            seq_idx = len(records)
            record_body = {
                "schema_version": SCHEMA_VERSION_KH02_R2_C2_JOURNAL,
                "validation_version": SPEC_VERSION_KH02_R2_C2,
                "seq_index": seq_idx,
                "prev_entry_sha256": prev_sha,
                "snapshot_id": sid,
                "content_sha256": content_sha,
                "blob_rel_path": blob_rel,
                "blob_size_bytes": len(blob_bytes),
                "has_raw_payload_bytes": raw_payload_bytes is not None,
                "provenance_class": adm_info["provenance_class"],
                "admission_tier": adm_info["admission_tier"],
                "eligible_for_fresh_oos": bool(adm_info["eligible_for_fresh_oos"]),
                "capture_receipt_id": capture_receipt.receipt_id if isinstance(capture_receipt, TrustedCaptureReceiptC3) else None,
                "dataset_sha256": val["dataset_sha256"],
                "raw_response_sha256": str(entry["raw_response_sha256"]).lower(),
                "canonical_parsed_sha256": val["canonical_parsed_sha256"],
                "data_origin": val["data_origin"],
                "source_provenance": {
                    "provider": str(entry.get("provider") or ""),
                    "exchange": str(entry.get("exchange") or ""),
                    "product_type": str(entry.get("product_type") or ""),
                    "symbol": str(entry.get("symbol") or ""),
                    "endpoint": str(entry.get("endpoint") or ""),
                    "http_status": int(entry.get("http_status") or 0),
                },
                "event_time_ms": int(entry["event_or_close_utc_ms"]),
                "receive_time_ms": int(entry["response_utc_ms"]),
                "capture_time_ms": int(entry["capture_time_ms"]),
                "known_at_ms": int(entry["known_at_ms"]),
                "start_utc_ms": int(entry["start_utc_ms"]),
                "end_utc_ms": int(entry["end_utc_ms"]),
                "frozen_candidate_code_sha": R2_AUTHORITATIVE_FROZEN_CODE_SHA,
                "frozen_config_sha256": R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256,
                "OOS_EVALUATIONS": 0,
            }
            full_rec = dict(record_body, entry_sha256=canonical_hash(record_body))
            line_bytes = _canonical_json_bytes(full_rec) + b"\n"

            # Phase 3: Strict O_APPEND single line + EINTR-safe full write (NEVER rewrites prior bytes!)
            jfd = os.open(_win_long_path(self.journal_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                if _crash_hook is not None:
                    try:
                        _hook("TORN_JOURNAL_LINE")
                    except _InjectTornWriteSignal:
                        _write_all_eintr_safe(jfd, line_bytes[: max(8, len(line_bytes) // 2)])
                        os.fsync(jfd)
                        raise RuntimeError("SIMULATED_CRASH_TORN_JOURNAL_LINE")
                written = _write_all_eintr_safe(jfd, line_bytes, _write_fn=_write_fn)
                if written != len(line_bytes):
                    raise IOError(f"SHORT_JOURNAL_APPEND_WRITE:{written}!={len(line_bytes)}")
                _hook("AFTER_JOURNAL_WRITE_BEFORE_FSYNC")
                os.fsync(jfd)
            finally:
                os.close(jfd)
            _fsync_directory(self.escrow_root)

            # Phase 4: Optional external head anchor attestation
            _hook("AFTER_JOURNAL_FSYNC_BEFORE_ANCHOR")
            anchor_rec = None
            if self.external_anchor is not None:
                anchor_rec = self.external_anchor.append_head_attestation(
                    journal_record_count=seq_idx + 1,
                    head_seq_index=seq_idx,
                    head_entry_sha256=full_rec["entry_sha256"],
                    journal_id=self.journal_id,
                )

            return {
                "action": "APPENDED_NEW_ENTRY",
                "snapshot_id": sid,
                "seq_index": seq_idx,
                "content_sha256": content_sha,
                "blob_rel_path": blob_rel,
                "blob_created": blob_created,
                "entry_sha256": full_rec["entry_sha256"],
                "record_count": seq_idx + 1,
                "journal_prior_bytes_untouched": True,
                "external_anchor_attested": anchor_rec is not None,
                "provenance_class": adm_info["provenance_class"],
                "admission_tier": adm_info["admission_tier"],
                "eligible_for_fresh_oos": bool(adm_info["eligible_for_fresh_oos"]),
            }

    def quarantine_corrupt_tail_and_orphans(
        self,
        *,
        operator_reason: str,
    ) -> Dict[str, Any]:
        """
        Explicit fail-closed recovery protocol: moves any corrupt/torn tail bytes AND/OR
        orphan uncommitted blobs into `quarantine/<UTC>/` with a SHA256 audit receipt
        (NEVER silently discards bytes), restoring clean valid committed prefix.
        """
        if not str(operator_reason).strip():
            raise ValueError("EXPLICIT_OPERATOR_REASON_REQUIRED_FOR_QUARANTINE")

        with self.exclusive_lock():
            _assert_not_symlink_and_contained(self.quarantine_dir, self.escrow_root)
            _assert_not_symlink_and_contained(self.blobs_dir, self.escrow_root)
            records, parse_violations, corrupt_tail = self._parse_journal_bytes()
            utc_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
            q_dir = self.quarantine_dir / f"quarantine_{utc_tag}_{os.getpid()}"
            _assert_not_symlink_and_contained(q_dir, self.escrow_root)
            _mkdir_parents_safe(q_dir)
            quarantined_items: List[Dict[str, Any]] = []

            # Quarantine torn tail bytes by truncating journal strictly to end of last valid line
            if corrupt_tail is not None and _is_file_safe(self.journal_path):
                _assert_not_symlink_and_contained(self.journal_path, self.escrow_root)
                raw = _read_bytes_safe(self.journal_path)
                tail_path = q_dir / "corrupt_journal_tail.bin"
                tfd = os.open(_win_long_path(tail_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                try:
                    _write_all_eintr_safe(tfd, corrupt_tail)
                    os.fsync(tfd)
                finally:
                    os.close(tfd)
                quarantined_items.append(
                    {
                        "kind": "CORRUPT_JOURNAL_TAIL",
                        "quarantined_path": str(tail_path.relative_to(self.escrow_root)),
                        "size_bytes": len(corrupt_tail),
                        "sha256": hashlib.sha256(corrupt_tail).hexdigest(),
                    }
                )
                valid_prefix = raw[: len(raw) - len(corrupt_tail)]
                # Only rewrite/truncate under explicit owner quarantine recovery (NEVER on normal append)
                jfd = os.open(_win_long_path(self.journal_path), os.O_RDWR)
                try:
                    os.ftruncate(jfd, len(valid_prefix))
                    os.fsync(jfd)
                finally:
                    os.close(jfd)

            # Quarantine orphan uncommitted blobs
            referenced_names = {
                Path(str(r.get("blob_rel_path") or "")).name for r in records
            }
            if _is_dir_safe(self.blobs_dir):
                for bname in sorted(os.listdir(_win_long_path(self.blobs_dir))):
                    bp = self.blobs_dir / bname
                    if _is_symlink_or_junction(bp):
                        continue
                    if _is_file_safe(bp) and bp.name.endswith(".blob") and bp.name not in referenced_names:
                        _assert_not_symlink_and_contained(bp, self.escrow_root)
                        dst = q_dir / bp.name
                        bp_s = _win_long_path(bp)
                        dst_s = _win_long_path(dst)
                        with open(bp_s, "rb") as _bfh:
                            b_bytes = _bfh.read()
                        os.replace(bp_s, dst_s)
                        quarantined_items.append(
                            {
                                "kind": "ORPHAN_UNCOMMITTED_BLOB",
                                "original_blob": bp.name,
                                "quarantined_path": str(dst.relative_to(self.escrow_root)),
                                "size_bytes": len(b_bytes),
                                "sha256": hashlib.sha256(b_bytes).hexdigest(),
                            }
                        )
            _fsync_directory(self.blobs_dir)

            receipt = {
                "spec_version": SPEC_VERSION_KH02_R2_C2,
                "quarantined_utc": _utc_now_iso(),
                "operator_reason": str(operator_reason),
                "prior_parse_violations": parse_violations,
                "quarantined_items": quarantined_items,
            }
            r_bytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
            r_path = q_dir / "quarantine_receipt.json"
            rfd = os.open(_win_long_path(r_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                _write_all_eintr_safe(rfd, r_bytes)
                os.fsync(rfd)
            finally:
                os.close(rfd)
            _fsync_directory(q_dir)
            _fsync_directory(self.quarantine_dir)
            _fsync_directory(self.escrow_root)
            post_check = self.verify_integrity()
            return {
                "quarantine_dir": str(q_dir),
                "quarantined_count": len(quarantined_items),
                "quarantined_items": quarantined_items,
                "post_recovery_integrity": post_check,
            }

    def import_legacy_c1_registry_readonly(self, legacy_c1_jsonl_path: Path) -> Dict[str, Any]:
        """
        §5.3: Audits a legacy C1 hash-linked JSONL file read-only without mutating it,
        verifies its SHA256 & chain integrity, and writes an immutable base provenance
        reference into `<escrow_root>/legacy_c1_base_reference.json` via O_CREAT|O_EXCL.
        """
        src = Path(legacy_c1_jsonl_path).resolve()
        if not _is_file_safe(src) or _is_symlink_or_junction(src):
            raise FileNotFoundError(f"LEGACY_C1_REGISTRY_NOT_FOUND:{src}")
        raw_bytes = _read_bytes_safe(src)
        src_sha = hashlib.sha256(raw_bytes).hexdigest()
        prev = GENESIS_PREV_SHA256_C2
        count = 0
        for idx, line in enumerate(raw_bytes.decode("utf-8").splitlines()):
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("prev_entry_sha256") != prev:
                raise ValueError(f"LEGACY_C1_BROKEN_CHAIN:idx={idx}")
            body = {k: v for k, v in rec.items() if k != "entry_sha256"}
            if rec.get("entry_sha256") != canonical_hash(body):
                raise ValueError(f"LEGACY_C1_TAMPERED_ENTRY:idx={idx}")
            prev = str(rec["entry_sha256"])
            count += 1

        with self.exclusive_lock():
            ref_path = self.escrow_root / "legacy_c1_base_reference.json"
            ref_doc = {
                "spec_version": SPEC_VERSION_KH02_R2_C2,
                "imported_utc": _utc_now_iso(),
                "legacy_c1_source_path": str(src),
                "legacy_c1_source_sha256": src_sha,
                "legacy_c1_record_count": count,
                "legacy_c1_head_entry_sha256": prev,
                "legacy_c1_source_mutated": False,
            }
            ref_bytes = _canonical_json_bytes(ref_doc) + b"\n"
            try:
                rfd = os.open(_win_long_path(ref_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                try:
                    os.write(rfd, ref_bytes)
                    os.fsync(rfd)
                finally:
                    os.close(rfd)
                _fsync_directory(self.escrow_root)
            except FileExistsError:
                existing = json.loads(_read_bytes_safe(ref_path).decode("utf-8"))
                if existing.get("legacy_c1_source_sha256") != src_sha:
                    raise PermissionError("LEGACY_C1_BASE_REFERENCE_OVERWRITE_FORBIDDEN")
            return ref_doc


class _InjectTornWriteSignal(Exception):
    """Internal test signal used exclusively by crash-injection unit tests to trigger torn append."""


def make_crash_hook_for_phase(phase_to_crash: str) -> Callable[[str], None]:
    """Helper for deterministic crash-injection tests at every commit phase."""
    def _hook(current_phase: str) -> None:
        if current_phase != phase_to_crash:
            return
        if phase_to_crash == "TORN_JOURNAL_LINE":
            raise _InjectTornWriteSignal()
        if phase_to_crash in ("FSYNC_FAILURE", "EIO_BLOB_WRITE"):
            raise OSError(errno.EIO, f"SIMULATED_EIO_AT_{phase_to_crash}")
        if phase_to_crash == "ENOSPC_BLOB_WRITE":
            raise OSError(errno.ENOSPC, "SIMULATED_ENOSPC_NO_SPACE_LEFT_ON_DEVICE")
        raise RuntimeError(f"SIMULATED_CRASH_AT_{phase_to_crash}")
    return _hook
