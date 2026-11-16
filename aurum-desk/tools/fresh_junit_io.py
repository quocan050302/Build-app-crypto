"""Capture JUnit bytes and metadata after writers have released the file, plus durable atomic artifact writers."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Iterable, Mapping


class DurableArtifactWriteError(OSError):
    """Raised when an artifact write, flush, fsync, atomic rename, or readback verification fails."""


def write_bytes_durable_atomic(path: Path, payload: bytes) -> str:
    """
    Atomically write bytes to `path.partial`, flush + `os.fsync`, `os.replace` to `path`,
    and verify closed disk readback size and SHA-256. Cleans up partial file on any OSError
    (such as simulated or real ENOSPC) and fails closed.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.partial")
    expected_sha = hashlib.sha256(payload).hexdigest()
    try:
        with tmp.open("wb") as handle:
            written = handle.write(payload)
            if written != len(payload):
                raise DurableArtifactWriteError(
                    f"PARTIAL_WRITE_BYTE_COUNT_MISMATCH:{target}:wrote={written},expected={len(payload)}"
                )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        # Verify durable closed readback
        st = target.stat()
        if st.st_size != len(payload):
            raise DurableArtifactWriteError(
                f"READBACK_SIZE_MISMATCH:{target}:size={st.st_size},expected={len(payload)}"
            )
        h = hashlib.sha256()
        with target.open("rb") as rb:
            for chunk in iter(lambda: rb.read(1024 * 1024), b""):
                h.update(chunk)
        actual_sha = h.hexdigest()
        if actual_sha != expected_sha:
            raise DurableArtifactWriteError(
                f"READBACK_SHA256_MISMATCH:{target}:actual={actual_sha},expected={expected_sha}"
            )
        return actual_sha
    except BaseException as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        if isinstance(exc, DurableArtifactWriteError):
            raise
        raise DurableArtifactWriteError(f"DURABLE_WRITE_FAILED:{target}:{exc}") from exc


def write_text_durable_atomic(path: Path, content: str) -> str:
    data = content.replace("\r\n", "\n").encode("utf-8")
    return write_bytes_durable_atomic(path, data)


def write_json_durable_atomic(path: Path, payload: Any) -> str:
    serialized = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n"
    return write_text_durable_atomic(path, serialized)


def write_jsonl_gz_durable_atomic(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.partial")
    try:
        with tmp.open("wb") as raw_fh:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_fh, mtime=0) as gz_fh:
                for row in rows:
                    line = (json.dumps(row, sort_keys=True, allow_nan=False, default=str) + "\n").encode("utf-8")
                    gz_fh.write(line)
            raw_fh.flush()
            os.fsync(raw_fh.fileno())
        os.replace(tmp, target)
        h = hashlib.sha256()
        total_b = 0
        with target.open("rb") as rb:
            for chunk in iter(lambda: rb.read(1024 * 1024), b""):
                total_b += len(chunk)
                h.update(chunk)
        if total_b <= 0:
            raise DurableArtifactWriteError(f"EMPTY_GZIP_ARTIFACT_ON_READBACK:{target}")
        # Verify gzip header & full stream decompressibility on readback
        with gzip.open(target, "rb") as gz_verify:
            for _ in iter(lambda: gz_verify.read(1024 * 1024), b""):
                pass
        return h.hexdigest()
    except BaseException as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        if isinstance(exc, DurableArtifactWriteError):
            raise
        raise DurableArtifactWriteError(f"DURABLE_GZIP_WRITE_FAILED:{target}:{exc}") from exc


def read_closed_junit_artifact(path: Path, *, timeout_seconds: float = 30):
    """Return one coherent read without changing bytes, timestamps or permissions.

    Windows write timestamps can finalize when a writer closes its handle.
    Deny write/delete sharing while capturing; do not bless a later mutation.
    """
    path = Path(path)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        deadline = time.monotonic() + timeout_seconds
        while True:
            # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING. No writer or delete
            # handle may overlap this capture; the handle is not inheritable.
            handle = create(str(path.resolve()), 0x80000000, 1, None, 3, 0, None)
            if handle != ctypes.c_void_p(-1).value:
                break
            error = ctypes.get_last_error()
            if error not in (32, 33):
                raise ctypes.WinError(error)
            if time.monotonic() >= deadline:
                raise RuntimeError("JUNIT_WRITER_STILL_OPEN:" + str(path))
            time.sleep(.05)
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            close(handle)
            raise
        source = os.fdopen(fd, "rb")  # Owns and closes the Windows handle.
    else:
        source = path.open("rb")
    with source:
        before = os.fstat(source.fileno())
        data = source.read()
        after = os.fstat(source.fileno())
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or len(data) != after.st_size:
            raise RuntimeError("JUNIT_CHANGED_DURING_CAPTURE:" + str(path))
        return data, after
