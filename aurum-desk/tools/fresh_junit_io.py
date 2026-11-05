"""Capture JUnit bytes and metadata after writers have released the file."""
from __future__ import annotations

import os
from pathlib import Path
import time


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
