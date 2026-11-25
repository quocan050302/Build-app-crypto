"""
aurum-desk/backend/services/kh_03_r6_bounded_transport_service.py

AURUM DESK — KH-03.R6 (M3) | BOUNDED PUBLIC TRANSPORT, OS TREE TERMINATION & RESOURCE SAFETY
SPEC VERSION: KH-03.R6-v1.0 | OWNED SUBPROCESS ISOLATION | WINDOWS JOB OBJECT / POSIX PGID REAP

Implements:
  - F13: build_r6_bound_capture_plan(verified_grant, registry, proposed_budget, resource_check, ...)
  - F14: start_r6_owned_transport_worker(lease, validated_request, limits, ...)
  - F15: execute_r6_bounded_rest_get(...)
  - F16: execute_r6_bounded_ws_session(...)
  - F17: supervise_r6_session(...) / cancel_and_reap_r6_transport(...)
  - F18: validate_r6_resource_guard(...) / R6BoundedQueueWriter
  - Isolated `127.0.0.1` loopback HTTP & WS QA fixture servers (OFFLINE QA ONLY; never binds 0.0.0.0).
  - Owned child worker CLI (`python -m services.kh_03_r6_bounded_transport_service --child-worker`).

Hard Safety Invariants:
  - Distinct R6 transport boundary: NEVER touches legacy C2 live hard block or mints legacy C4 proof.
  - Parent owns durable lease, budget accounting, lifecycle, provenance, and staging.
  - Child worker receives ONLY one immutable validated request via stdin and has zero access to
    journal/ledger/OOS/trading/secrets.
  - External sockets are IMPOSSIBLE unless `verified_grant.external_capture_permitted is True`,
    `lease.external_capture_permitted is True`, AND `qa_loopback_mode is False`.
  - Windows Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + `TerminateJobObject`) on Windows
    and `os.setsid` + `os.killpg` on POSIX, with `psutil` PID + `create_time` OS exit verification.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import hmac
import http.client
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode, urlsplit
import zlib

import certifi
import psutil

from services.kh_03_r6_durable_grant_service import (
    BUDGET_FIELDS_R6,
    R6DurableGrantLedger,
    R6DurableSessionLease,
    R6VerifiedGrant,
    _canonical_json_bytes,
    _is_strict_int,
    _sha256_canonical,
)
from services.kh_03_r6_source_contract_service import (
    APPROVED_REST_HOST_R6,
    APPROVED_WS_HOST_R6,
    R6SourceContractRegistry,
    SCHEMA_VERSION_KH03_R6,
    SPEC_VERSION_KH03_R6,
    validate_r6_endpoint_contract,
)

PREFLIGHT_DISK_MIN_GIB_R6 = 20.5
RUNTIME_DISK_MIN_GIB_R6 = 20.0
HARD_RAM_FLOOR_MIB_R6 = 512.0
GLOBAL_MAX_WORKERS_R6 = 4
DEFAULT_TELEMETRY_MAX_AGE_SEC_R6 = 2.0
DEFAULT_TELEMETRY_MAX_FUTURE_SEC_R6 = 1.0
MAX_HTTP_HEADER_BYTES_R6 = 32_768
MAX_WS_DECODED_MULTIPLIER_R6 = 8

_PLAN_MAC_SECRET_R6 = secrets.token_bytes(32)


def _is_finite_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _volume_key_for_path(p: Path) -> str:
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


def _disk_free_gib_for_path(p: Path) -> float:
    cur = p.resolve(strict=False)
    while not cur.exists() and cur.parent != cur:
        cur = cur.parent
    du = shutil.disk_usage(cur)
    return float(du.free) / float(1024 ** 3)


def collect_real_r6_resource_sample(
    volumes: Mapping[str, Path],
    *,
    projected_writes_bytes_by_role: Optional[Mapping[str, int]] = None,
    active_workers: int = 0,
) -> Dict[str, Any]:
    """Collects an authentic OS resource telemetry sample (real psutil RAM + real shutil disk)."""
    now_mono = time.monotonic()
    now_utc_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    vm = psutil.virtual_memory()
    proc = psutil.Process(os.getpid())
    rss_bytes = int(proc.memory_info().rss)
    for ch in proc.children(recursive=True):
        try:
            rss_bytes += int(ch.memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    vol_map: Dict[str, Dict[str, Any]] = {}
    proj_by_vol: Dict[str, int] = {}
    role_proj = dict(projected_writes_bytes_by_role or {})
    for role, path in volumes.items():
        vk = _volume_key_for_path(path)
        free_gib = _disk_free_gib_for_path(path)
        proj_by_vol[vk] = proj_by_vol.get(vk, 0) + int(role_proj.get(role, 0))
        vol_map[role] = {
            "path": str(path.resolve(strict=False)),
            "volume_id": vk,
            "free_gib": round(free_gib, 4),
        }

    return {
        "source": "REAL_OS_TELEMETRY",
        "observed_monotonic_sec": now_mono,
        "observed_utc_ms": now_utc_ms,
        "ram_available_mib": round(float(vm.available) / float(1024 ** 2), 3),
        "ram_total_mib": round(float(vm.total) / float(1024 ** 2), 3),
        "tree_rss_mib": round(float(rss_bytes) / float(1024 ** 2), 3),
        "active_workers": int(active_workers),
        "volumes": vol_map,
        "projected_writes_bytes_by_volume": proj_by_vol,
    }


# =====================================================================
# F18: validate_r6_resource_guard & R6BoundedQueueWriter
# =====================================================================
def validate_r6_resource_guard(
    sample: Any,
    *,
    expected_volumes: Mapping[str, Path],
    phase: str = "runtime",  # "preflight" | "runtime"
    now_monotonic: Optional[float] = None,
    max_age_sec: float = DEFAULT_TELEMETRY_MAX_AGE_SEC_R6,
    allow_fixture_sample: bool = False,
    adaptive_ram_reserve_mib: float = HARD_RAM_FLOOR_MIB_R6,
) -> Dict[str, Any]:
    """
    Fail-closed multi-volume resource validator.
    Enforces >=20.5 GiB preflight, >=20.0 GiB runtime (accounting for deduplicated same-volume
    projected writes), >=512 MiB hard RAM floor, global worker cap <=4, and rejects missing,
    stale, future, nonfinite, bool-as-num, wrong-volume, or QA-fixture samples in live mode.
    """
    viols: List[str] = []
    if not isinstance(sample, Mapping):
        return {"ok": False, "status": "FAIL_MISSING_TELEMETRY", "violations": ["RESOURCE_SAMPLE_MISSING_OR_NOT_MAPPING"]}

    src = str(sample.get("source") or "").strip()
    if src == "QA_FIXTURE_TELEMETRY" and not allow_fixture_sample:
        viols.append("QA_FIXTURE_TELEMETRY_FORBIDDEN_IN_LIVE_MODE")
    elif src not in ("REAL_OS_TELEMETRY", "QA_FIXTURE_TELEMETRY"):
        viols.append(f"UNTRUSTED_TELEMETRY_SOURCE:{src}")

    mono = sample.get("observed_monotonic_sec")
    ref_mono = time.monotonic() if now_monotonic is None else float(now_monotonic)
    if not _is_finite_num(mono):
        viols.append("NONFINITE_OR_MISSING_MONOTONIC_TIMESTAMP")
    else:
        age = ref_mono - float(mono)
        if age > float(max_age_sec):
            viols.append(f"STALE_TELEMETRY_SAMPLE:age={age:.3f}s>{max_age_sec}s")
        if age < -DEFAULT_TELEMETRY_MAX_FUTURE_SEC_R6:
            viols.append(f"FUTURE_TELEMETRY_SAMPLE:delta={-age:.3f}s")

    ram_avail = sample.get("ram_available_mib")
    ram_floor = max(HARD_RAM_FLOOR_MIB_R6, float(adaptive_ram_reserve_mib))
    if not _is_finite_num(ram_avail):
        viols.append("NONFINITE_OR_MISSING_RAM_AVAILABLE")
    elif float(ram_avail) < ram_floor:
        viols.append(f"RAM_BELOW_FLOOR:{float(ram_avail):.1f}MiB<{ram_floor:.1f}MiB")

    act_w = sample.get("active_workers")
    if not _is_strict_int(act_w) or int(act_w) < 0:
        viols.append("INVALID_ACTIVE_WORKERS_COUNT")
    elif int(act_w) > GLOBAL_MAX_WORKERS_R6:
        viols.append(f"GLOBAL_WORKER_CAP_EXCEEDED:{act_w}>{GLOBAL_MAX_WORKERS_R6}")

    min_disk_gib = PREFLIGHT_DISK_MIN_GIB_R6 if phase == "preflight" else RUNTIME_DISK_MIN_GIB_R6
    vols = sample.get("volumes")
    proj_by_vol = sample.get("projected_writes_bytes_by_volume") or {}
    if not isinstance(vols, Mapping):
        viols.append("MISSING_VOLUMES_TELEMETRY")
    else:
        checked_vols: Dict[str, float] = {}
        for role, exp_path in expected_volumes.items():
            vinfo = vols.get(role)
            if not isinstance(vinfo, Mapping):
                viols.append(f"MISSING_VOLUME_ROLE:{role}")
                continue
            exp_vk = _volume_key_for_path(exp_path)
            got_vk = str(vinfo.get("volume_id") or "")
            if got_vk != exp_vk:
                viols.append(f"WRONG_VOLUME_IDENTITY:{role}:expected={exp_vk}:got={got_vk}")
            fg = vinfo.get("free_gib")
            if not _is_finite_num(fg):
                viols.append(f"NONFINITE_DISK_FREE:{role}")
                continue
            # Deduplicate same-volume projected writes once per physical volume
            proj_b = proj_by_vol.get(got_vk, 0)
            if not _is_strict_int(proj_b) or int(proj_b) < 0:
                viols.append(f"INVALID_PROJECTED_BYTES:{got_vk}")
                continue
            eff_free_gib = float(fg) - (float(proj_b) / float(1024 ** 3))
            checked_vols[got_vk] = min(checked_vols.get(got_vk, eff_free_gib), eff_free_gib)
            if eff_free_gib < min_disk_gib:
                viols.append(f"DISK_BELOW_FLOOR:{role}:{got_vk}:eff_free={eff_free_gib:.3f}GiB<{min_disk_gib}GiB")

    return {
        "ok": len(viols) == 0,
        "status": "RESOURCE_GUARD_OK" if not viols else "FAIL_RESOURCE_GUARD_CLOSED",
        "phase": phase,
        "min_disk_gib_required": min_disk_gib,
        "min_ram_mib_required": ram_floor,
        "violations": viols,
    }


class R6BoundedQueueWriter:
    """
    Bounded in-memory queue capping BOTH item count and total byte size with explicit backpressure
    rejection (ZERO silent drops!).
    """

    def __init__(self, *, max_items: int, max_bytes: int) -> None:
        if not _is_strict_int(max_items) or max_items <= 0 or not _is_strict_int(max_bytes) or max_bytes <= 0:
            raise ValueError("BOUNDED_QUEUE_CAPS_MUST_BE_POSITIVE_INTS")
        self.max_items = int(max_items)
        self.max_bytes = int(max_bytes)
        self._lock = threading.Lock()
        self._items: List[Tuple[bytes, Dict[str, Any]]] = []
        self._current_bytes = 0
        self.overflow_events = 0
        self.accepted_items = 0
        self.accepted_bytes = 0

    def try_enqueue(self, raw_bytes: bytes, meta: Mapping[str, Any]) -> Dict[str, Any]:
        sz = len(raw_bytes)
        with self._lock:
            if len(self._items) + 1 > self.max_items or self._current_bytes + sz > self.max_bytes:
                self.overflow_events += 1
                return {
                    "enqueued": False,
                    "status": "QUEUE_OVERFLOW_BACKPRESSURE_ABORT",
                    "queued_items": len(self._items),
                    "queued_bytes": self._current_bytes,
                    "incoming_bytes": sz,
                }
            self._items.append((raw_bytes, dict(meta)))
            self._current_bytes += sz
            self.accepted_items += 1
            self.accepted_bytes += sz
            return {"enqueued": True, "status": "ENQUEUED", "queued_items": len(self._items), "queued_bytes": self._current_bytes}

    def drain_all(self) -> List[Tuple[bytes, Dict[str, Any]]]:
        with self._lock:
            out = list(self._items)
            self._items.clear()
            self._current_bytes = 0
            return out


# =====================================================================
# F13: build_r6_bound_capture_plan (Frozen & MAC-bound)
# =====================================================================
@dataclass(frozen=True)
class R6BoundCapturePlan:
    plan_id: str
    stage: str
    purpose: str
    grant_identity_key: str
    grant_payload_digest: str
    tested_executable_sha: str
    registry_digest: str
    destination_store_id: str
    destination_root: str
    endpoints: Tuple[str, ...]
    ws_channels: Tuple[str, ...]
    window_start_ms: int
    window_end_ms: int
    max_session_duration_sec: int
    max_concurrency: int
    caps: Mapping[str, int]
    expires_utc_ms: int
    external_capture_permitted: bool
    plan_digest: str
    plan_mac_hex: str

    def to_redacted_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "stage": self.stage,
            "purpose": self.purpose,
            "grant_identity_key": self.grant_identity_key,
            "grant_payload_digest": self.grant_payload_digest,
            "tested_executable_sha": self.tested_executable_sha,
            "registry_digest": self.registry_digest,
            "destination_store_id": self.destination_store_id,
            "endpoints": list(self.endpoints),
            "ws_channels": list(self.ws_channels),
            "window_start_ms": self.window_start_ms,
            "window_end_ms": self.window_end_ms,
            "max_session_duration_sec": self.max_session_duration_sec,
            "max_concurrency": self.max_concurrency,
            "caps": dict(self.caps),
            "expires_utc_ms": self.expires_utc_ms,
            "external_capture_permitted": self.external_capture_permitted,
            "plan_digest": self.plan_digest,
        }


def _compute_plan_mac(plan_digest: str, grant_payload_digest: str, external_flag: bool) -> str:
    msg = f"{plan_digest}|{grant_payload_digest}|{int(external_flag)}".encode("utf-8")
    return hmac.new(_PLAN_MAC_SECRET_R6, msg, hashlib.sha256).hexdigest()


def verify_r6_bound_plan_integrity(plan: R6BoundCapturePlan, verified_grant: R6VerifiedGrant) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    if not isinstance(plan, R6BoundCapturePlan):
        return False, ["PLAN_NOT_R6_BOUND_CAPTURE_PLAN"]
    if plan.grant_identity_key != verified_grant.grant_identity_key:
        viols.append("PLAN_GRANT_IDENTITY_MISMATCH")
    if plan.grant_payload_digest != verified_grant.grant_payload_digest:
        viols.append("PLAN_GRANT_PAYLOAD_DIGEST_MISMATCH")
    if plan.external_capture_permitted != verified_grant.external_capture_permitted:
        viols.append("PLAN_EXTERNAL_PERMISSION_MISMATCH")
    expected_mac = _compute_plan_mac(plan.plan_digest, verified_grant.grant_payload_digest, plan.external_capture_permitted)
    if not hmac.compare_digest(plan.plan_mac_hex, expected_mac):
        viols.append("PLAN_MAC_INTEGRITY_FAILED")
    return len(viols) == 0, viols


def build_r6_bound_capture_plan(
    verified_grant: R6VerifiedGrant,
    registry: R6SourceContractRegistry,
    proposed_budget: Mapping[str, Any],
    resource_check: Mapping[str, Any],
    *,
    plan_id: str,
) -> Dict[str, Any]:
    """
    Freezes a canonical capture plan strictly as a subset of `verified_grant` and `registry`.
    NEVER silently clamps unauthorized budget/window/endpoint values — rejects fail-closed!
    """
    viols: List[str] = []
    if not isinstance(verified_grant, R6VerifiedGrant):
        return {"valid": False, "status": "BLOCKED_INVALID_GRANT", "plan": None, "violations": ["GRANT_NOT_VERIFIED_OBJECT"]}
    if registry.status != "DOC_VERIFIED_READY_FOR_STAGE_B_REVIEW":
        viols.append(f"SOURCE_REGISTRY_NOT_READY:{registry.status}")
    if resource_check.get("ok") is not True:
        viols.append("RESOURCE_PREFLIGHT_NOT_OK")

    # Reject any callable injection in proposed_budget!
    for k, v in proposed_budget.items():
        if callable(v):
            viols.append(f"CALLABLE_INJECTION_FORBIDDEN:{k}")

    prop_stage = str(proposed_budget.get("stage") or verified_grant.stage)
    if prop_stage != verified_grant.stage:
        viols.append(f"PLAN_STAGE_MISMATCH:plan={prop_stage}:grant={verified_grant.stage}")

    prop_eps = tuple(str(x) for x in (proposed_budget.get("endpoints") or verified_grant.allowed_endpoints))
    if not prop_eps:
        viols.append("PLAN_EMPTY_ENDPOINTS")
    for ep_id in prop_eps:
        if ep_id not in verified_grant.allowed_endpoints:
            viols.append(f"PLAN_ENDPOINT_NOT_IN_GRANT:{ep_id}")
        if ep_id not in registry.endpoints:
            viols.append(f"PLAN_ENDPOINT_NOT_IN_REGISTRY:{ep_id}")

    prop_ws = tuple(str(x) for x in (proposed_budget.get("ws_channels") or ()))
    for ch in prop_ws:
        if ch not in verified_grant.allowed_ws_channels:
            viols.append(f"PLAN_WS_CHANNEL_NOT_IN_GRANT:{ch}")

    ws = proposed_budget.get("window_start_ms", verified_grant.window_start_ms)
    we = proposed_budget.get("window_end_ms", verified_grant.window_end_ms)
    if not _is_strict_int(ws) or not _is_strict_int(we) or ws >= we:
        viols.append("PLAN_INVALID_WINDOW")
    elif ws < verified_grant.window_start_ms or we > verified_grant.window_end_ms:
        viols.append("PLAN_WINDOW_EXCEEDS_GRANT")

    dur = proposed_budget.get("max_session_duration_sec", verified_grant.max_session_duration_sec)
    if not _is_strict_int(dur) or dur <= 0 or dur > verified_grant.max_session_duration_sec:
        viols.append(f"PLAN_DURATION_EXCEEDS_GRANT:{dur}>{verified_grant.max_session_duration_sec}")

    conc = proposed_budget.get("max_concurrency", verified_grant.max_concurrency)
    if not _is_strict_int(conc) or conc <= 0 or conc > verified_grant.max_concurrency:
        viols.append(f"PLAN_CONCURRENCY_EXCEEDS_GRANT:{conc}>{verified_grant.max_concurrency}")

    prop_dest = str(proposed_budget.get("destination_root") or verified_grant.destination_root)
    if Path(prop_dest).resolve(strict=False) != Path(verified_grant.destination_root).resolve(strict=False):
        viols.append("PLAN_DESTINATION_ROOT_DRIFT")

    prop_caps_in = proposed_budget.get("caps") if isinstance(proposed_budget.get("caps"), Mapping) else dict(verified_grant.caps)
    plan_caps: Dict[str, int] = {}
    for k in BUDGET_FIELDS_R6:
        val = prop_caps_in.get(k, verified_grant.caps[k])
        if not _is_strict_int(val) or int(val) < 0:
            viols.append(f"PLAN_INVALID_CAP:{k}")
        elif int(val) > int(verified_grant.caps[k]):
            viols.append(f"PLAN_CAP_EXCEEDS_GRANT_NO_SILENT_CLAMP:{k}:{val}>{verified_grant.caps[k]}")
        else:
            plan_caps[k] = int(val)

    if viols:
        return {"valid": False, "status": "REJECTED_PLAN_BINDING", "plan": None, "violations": viols}

    core = {
        "plan_id": str(plan_id),
        "stage": verified_grant.stage,
        "purpose": verified_grant.purpose,
        "grant_identity_key": verified_grant.grant_identity_key,
        "grant_payload_digest": verified_grant.grant_payload_digest,
        "tested_executable_sha": verified_grant.tested_executable_sha,
        "registry_digest": registry.registry_digest,
        "destination_store_id": verified_grant.destination_store_id,
        "destination_root": verified_grant.destination_root,
        "endpoints": list(prop_eps),
        "ws_channels": list(prop_ws),
        "window_start_ms": int(ws),
        "window_end_ms": int(we),
        "max_session_duration_sec": int(dur),
        "max_concurrency": int(conc),
        "caps": plan_caps,
        "expires_utc_ms": verified_grant.expires_utc_ms,
        "external_capture_permitted": verified_grant.external_capture_permitted,
    }
    p_digest = _sha256_canonical(core)
    p_mac = _compute_plan_mac(p_digest, verified_grant.grant_payload_digest, verified_grant.external_capture_permitted)
    plan = R6BoundCapturePlan(
        plan_id=str(plan_id),
        stage=verified_grant.stage,
        purpose=verified_grant.purpose,
        grant_identity_key=verified_grant.grant_identity_key,
        grant_payload_digest=verified_grant.grant_payload_digest,
        tested_executable_sha=verified_grant.tested_executable_sha,
        registry_digest=registry.registry_digest,
        destination_store_id=verified_grant.destination_store_id,
        destination_root=verified_grant.destination_root,
        endpoints=prop_eps,
        ws_channels=prop_ws,
        window_start_ms=int(ws),
        window_end_ms=int(we),
        max_session_duration_sec=int(dur),
        max_concurrency=int(conc),
        caps=plan_caps,
        expires_utc_ms=verified_grant.expires_utc_ms,
        external_capture_permitted=verified_grant.external_capture_permitted,
        plan_digest=p_digest,
        plan_mac_hex=p_mac,
    )
    return {"valid": True, "status": "PLAN_BOUND_FROZEN", "plan": plan, "violations": []}


# =====================================================================
# Platform Process Tree Containment (Windows Job Object / POSIX PGID)
# =====================================================================
class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD if os.name == "nt" else ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD if os.name == "nt" else ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD if os.name == "nt" else ctypes.c_uint32),
        ("SchedulingClass", wintypes.DWORD if os.name == "nt" else ctypes.c_uint32),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _WindowsJobObjectGuard:
    """Thin stdlib ctypes wrapper around Windows Job Objects for guaranteed OS process-tree termination."""

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9

    def __init__(self) -> None:
        self.handle: Optional[int] = None
        if os.name != "nt":
            return
        k32 = ctypes.windll.kernel32
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.restype = wintypes.BOOL
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        h = k32.CreateJobObjectW(None, None)
        if not h:
            raise OSError("CREATE_WINDOWS_JOB_OBJECT_FAILED")
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(
            h,
            self.JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            k32.CloseHandle(h)
            raise OSError("SET_WINDOWS_JOB_OBJECT_LIMIT_FAILED")
        self.handle = int(h)

    def assign(self, proc_handle: int) -> bool:
        if os.name != "nt" or not self.handle:
            return False
        return bool(ctypes.windll.kernel32.AssignProcessToJobObject(wintypes.HANDLE(self.handle), wintypes.HANDLE(proc_handle)))

    def terminate(self, exit_code: int = 137) -> bool:
        if os.name != "nt" or not self.handle:
            return False
        return bool(ctypes.windll.kernel32.TerminateJobObject(wintypes.HANDLE(self.handle), int(exit_code)))

    def close(self) -> None:
        if os.name == "nt" and self.handle:
            try:
                ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self.handle))
            finally:
                self.handle = None


def _is_same_os_process_alive(pid: int, create_time: float) -> bool:
    try:
        p = psutil.Process(int(pid))
        if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
            return False
        return abs(float(p.create_time()) - float(create_time)) < 1.0
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


# =====================================================================
# F15: Bounded Streaming REST GET (Deadline + Caps + Decompress + Retry-After)
# =====================================================================
def _parse_retry_after_strict(header_val: Optional[str], now_utc: datetime) -> Optional[float]:
    if header_val is None:
        return None
    s = str(header_val).strip()
    if not s:
        return None
    try:
        secs = float(s)
        if math.isfinite(secs) and secs >= 0.0:
            return secs
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - now_utc).total_seconds()
        return max(0.0, float(delta))
    except Exception:
        return None


def execute_r6_bounded_rest_get(
    url: str,
    params: Mapping[str, Any],
    *,
    remaining_deadline_sec: float,
    max_payload_bytes: int,
    max_decoded_bytes: int,
    max_header_bytes: int = MAX_HTTP_HEADER_BYTES_R6,
    max_retries: int = 2,
    qa_loopback_mode: bool = False,
    external_capture_permitted: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """
    Bounded streaming public HTTP/HTTPS GET client:
      - Enforces strict TLS (`certifi`) + host verification for external `api.bitget.com`.
      - Refuses external connections unless `external_capture_permitted is True` (loopback `127.0.0.1`
        only when `qa_loopback_mode is True`).
      - Never follows redirects (301/302/303/307/308 -> non-retryable fail-closed).
      - Enforces total monotonic deadline across connect/headers/every read chunk (slow-drip safe!).
      - Enforces pre-read `Content-Length` + streaming `max_payload_bytes + 1` sentinel cap (chunked safe!).
      - Enforces streaming gzip decompression cap (`max_decoded_bytes + 1` -> gzip bomb safe!).
      - Honors `Retry-After` strictly without shortening; aborts if `retry_after > remaining_deadline`.
    """
    parsed = urlsplit(str(url))
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port or (443 if scheme == "https" else 80)

    is_loopback = host in ("127.0.0.1", "::1")
    if is_loopback:
        if not qa_loopback_mode:
            return {"ok": False, "status": "BLOCKED_LOOPBACK_WITHOUT_QA_MODE", "attempts": 0, "payload_bytes_received": 0, "decoded_bytes": 0, "violations": ["LOOPBACK_FORBIDDEN_IN_LIVE_MODE"]}
    else:
        if qa_loopback_mode or not external_capture_permitted:
            return {"ok": False, "status": "BLOCKED_EXTERNAL_SOCKET_FORBIDDEN", "attempts": 0, "payload_bytes_received": 0, "decoded_bytes": 0, "violations": ["EXTERNAL_NETWORK_SOCKET_NOT_AUTHORIZED"]}
        if scheme != "https" or host != APPROVED_REST_HOST_R6 or port != 443:
            return {"ok": False, "status": "BLOCKED_UNALLOWLISTED_HOST_OR_SCHEME", "attempts": 0, "payload_bytes_received": 0, "decoded_bytes": 0, "violations": [f"FORBIDDEN_EXTERNAL_TARGET:{scheme}://{host}:{port}"]}

    qs = urlencode(sorted((str(k), str(v)) for k, v in params.items()))
    req_path = f"{parsed.path}?{qs}" if qs else parsed.path

    t_start = time.monotonic()
    deadline = t_start + max(0.0, float(remaining_deadline_sec))
    attempts = 0
    total_raw_bytes = 0
    total_decoded_bytes = 0
    sleeps_honored: List[float] = []
    last_error = ""
    last_status_code: Optional[int] = None

    while True:
        rem = deadline - time.monotonic()
        if rem <= 0.0:
            return {
                "ok": False,
                "status": "ABORTED_TOTAL_DEADLINE_EXCEEDED",
                "http_status": last_status_code,
                "attempts": attempts,
                "payload_bytes_received": total_raw_bytes,
                "decoded_bytes": total_decoded_bytes,
                "sleeps_honored": sleeps_honored,
                "violations": [last_error or "TOTAL_DEADLINE_EXCEEDED"],
            }

        attempts += 1
        conn: Optional[http.client.HTTPConnection] = None
        try:
            if scheme == "https":
                ctx = ssl.create_default_context(cafile=certifi.where())
                ctx.check_hostname = True
                ctx.verify_mode = ssl.CERT_REQUIRED
                conn = http.client.HTTPSConnection(host, port=port, timeout=rem, context=ctx)
            else:
                conn = http.client.HTTPConnection(host, port=port, timeout=rem)

            conn.connect()
            if conn.sock is not None:
                rem_c = deadline - time.monotonic()
                if rem_c <= 0.0:
                    raise TimeoutError("DEADLINE_EXCEEDED_AFTER_CONNECT")
                conn.sock.settimeout(rem_c)

            conn.putrequest("GET", req_path, skip_accept_encoding=False)
            conn.putheader("Accept", "application/json")
            conn.putheader("Accept-Encoding", "gzip, identity")
            conn.putheader("User-Agent", "AurumDesk-KH03-R6-PublicData/1.0")
            conn.endheaders()

            resp = conn.getresponse()
            last_status_code = int(resp.status)

            # Bounded header size check
            hdr_bytes = sum(len(k.encode("latin-1", "replace")) + len(v.encode("latin-1", "replace")) + 4 for k, v in resp.getheaders())
            if hdr_bytes > max_header_bytes:
                return {
                    "ok": False,
                    "status": "REJECTED_OVERSIZED_HEADERS",
                    "http_status": last_status_code,
                    "attempts": attempts,
                    "payload_bytes_received": total_raw_bytes,
                    "decoded_bytes": total_decoded_bytes,
                    "violations": [f"HEADER_BYTES_EXCEEDED:{hdr_bytes}>{max_header_bytes}"],
                }

            headers_lower = {k.lower(): v for k, v in resp.getheaders()}

            # Fail closed on ANY redirect (301/302/303/307/308) — non-retryable!
            if last_status_code in (301, 302, 303, 307, 308):
                return {
                    "ok": False,
                    "status": "REJECTED_HTTP_REDIRECT_NONRETRY",
                    "http_status": last_status_code,
                    "attempts": attempts,
                    "payload_bytes_received": total_raw_bytes,
                    "decoded_bytes": total_decoded_bytes,
                    "violations": [f"HTTP_REDIRECT_FORBIDDEN:status={last_status_code}:location={headers_lower.get('location', '')}"],
                }

            # Non-retryable client auth/bad-request errors
            if last_status_code in (400, 401, 403, 404, 405):
                return {
                    "ok": False,
                    "status": "REJECTED_HTTP_NONRETRYABLE_ERROR",
                    "http_status": last_status_code,
                    "attempts": attempts,
                    "payload_bytes_received": total_raw_bytes,
                    "decoded_bytes": total_decoded_bytes,
                    "violations": [f"NONRETRYABLE_HTTP_STATUS:{last_status_code}"],
                }

            # Pre-read Content-Length cap BEFORE allocating/reading body!
            cl_hdr = headers_lower.get("content-length")
            if cl_hdr is not None:
                try:
                    cl_int = int(cl_hdr.strip())
                    if cl_int > max_payload_bytes:
                        return {
                            "ok": False,
                            "status": "REJECTED_DECLARED_CONTENT_LENGTH_OVERFLOW",
                            "http_status": last_status_code,
                            "attempts": attempts,
                            "payload_bytes_received": total_raw_bytes,
                            "decoded_bytes": total_decoded_bytes,
                            "violations": [f"CONTENT_LENGTH_EXCEEDS_CAP:{cl_int}>{max_payload_bytes}"],
                        }
                except ValueError:
                    return {
                        "ok": False,
                        "status": "REJECTED_INVALID_CONTENT_LENGTH",
                        "http_status": last_status_code,
                        "attempts": attempts,
                        "payload_bytes_received": total_raw_bytes,
                        "decoded_bytes": total_decoded_bytes,
                        "violations": [f"INVALID_CONTENT_LENGTH:{cl_hdr}"],
                    }

            # Stream body in bounded chunks with per-chunk remaining deadline (defeats slow-drip & chunked bombs!)
            is_gzip = "gzip" in str(headers_lower.get("content-encoding") or "").lower()
            decomp = zlib.decompressobj(16 + zlib.MAX_WBITS) if is_gzip else None
            raw_chunks: List[bytes] = []
            dec_chunks: List[bytes] = []
            raw_len = 0
            dec_len = 0

            while True:
                rem_r = deadline - time.monotonic()
                if rem_r <= 0.0:
                    raise TimeoutError("SLOW_DRIP_TOTAL_DEADLINE_EXCEEDED")
                if conn.sock is not None:
                    conn.sock.settimeout(rem_r)
                to_read = min(4096, (max_payload_bytes - raw_len) + 1)
                chunk = resp.read(to_read)
                if not chunk:
                    break
                raw_len += len(chunk)
                total_raw_bytes += len(chunk)
                if raw_len > max_payload_bytes:
                    return {
                        "ok": False,
                        "status": "REJECTED_PAYLOAD_BYTES_EXCEEDED",
                        "http_status": last_status_code,
                        "attempts": attempts,
                        "payload_bytes_received": total_raw_bytes,
                        "decoded_bytes": total_decoded_bytes,
                        "violations": [f"STREAM_PAYLOAD_EXCEEDED_CAP:{raw_len}>{max_payload_bytes}"],
                    }
                raw_chunks.append(chunk)
                if decomp is not None:
                    max_dec_step = (max_decoded_bytes - dec_len) + 1
                    dchunk = decomp.decompress(chunk, max_dec_step)
                    dec_len += len(dchunk)
                    total_decoded_bytes += len(dchunk)
                    if dec_len > max_decoded_bytes or decomp.unconsumed_tail:
                        return {
                            "ok": False,
                            "status": "REJECTED_DECOMPRESSED_BOMB_OVERFLOW",
                            "http_status": last_status_code,
                            "attempts": attempts,
                            "payload_bytes_received": total_raw_bytes,
                            "decoded_bytes": total_decoded_bytes,
                            "violations": [f"DECOMPRESSED_BYTES_EXCEEDED_CAP:>{max_decoded_bytes}"],
                        }
                    dec_chunks.append(dchunk)
                else:
                    dec_len += len(chunk)
                    total_decoded_bytes += len(chunk)
                    if dec_len > max_decoded_bytes:
                        return {
                            "ok": False,
                            "status": "REJECTED_DECODED_BYTES_EXCEEDED",
                            "http_status": last_status_code,
                            "attempts": attempts,
                            "payload_bytes_received": total_raw_bytes,
                            "decoded_bytes": total_decoded_bytes,
                            "violations": [f"DECODED_BYTES_EXCEEDED_CAP:{dec_len}>{max_decoded_bytes}"],
                        }

            if decomp is not None:
                rem_tail = decomp.flush((max_decoded_bytes - dec_len) + 1)
                dec_len += len(rem_tail)
                total_decoded_bytes += len(rem_tail)
                if dec_len > max_decoded_bytes:
                    return {
                        "ok": False,
                        "status": "REJECTED_DECOMPRESSED_BOMB_OVERFLOW",
                        "http_status": last_status_code,
                        "attempts": attempts,
                        "payload_bytes_received": total_raw_bytes,
                        "decoded_bytes": total_decoded_bytes,
                        "violations": [f"DECOMPRESSED_FLUSH_EXCEEDED_CAP:{dec_len}>{max_decoded_bytes}"],
                    }
                dec_chunks.append(rem_tail)

            raw_body = b"".join(raw_chunks)
            decoded_body = b"".join(dec_chunks) if is_gzip else raw_body

            # Transient retryable HTTP statuses: 429, 500, 502, 503, 504
            if last_status_code in (429, 500, 502, 503, 504):
                if attempts > max_retries:
                    return {
                        "ok": False,
                        "status": "ABORTED_TRANSIENT_RETRIES_EXHAUSTED",
                        "http_status": last_status_code,
                        "attempts": attempts,
                        "payload_bytes_received": total_raw_bytes,
                        "decoded_bytes": total_decoded_bytes,
                        "sleeps_honored": sleeps_honored,
                        "violations": [f"TRANSIENT_HTTP_{last_status_code}_RETRIES_EXHAUSTED"],
                    }
                ra_sec = _parse_retry_after_strict(headers_lower.get("retry-after"), datetime.now(timezone.utc))
                wait_sec = ra_sec if ra_sec is not None else min(1.0 * attempts, 5.0)
                rem_after = deadline - time.monotonic()
                if wait_sec >= rem_after:
                    return {
                        "ok": False,
                        "status": "ABORTED_RETRY_AFTER_EXCEEDS_DEADLINE",
                        "http_status": last_status_code,
                        "attempts": attempts,
                        "retry_after_sec": wait_sec,
                        "remaining_deadline_sec": round(rem_after, 4),
                        "payload_bytes_received": total_raw_bytes,
                        "decoded_bytes": total_decoded_bytes,
                        "sleeps_honored": sleeps_honored,
                        "violations": [f"RETRY_AFTER_EXCEEDS_REMAINING_BUDGET:{wait_sec:.3f}s>={rem_after:.3f}s"],
                    }
                sleeps_honored.append(round(wait_sec, 4))
                sleep_fn(wait_sec)
                continue

            if last_status_code != 200:
                return {
                    "ok": False,
                    "status": "REJECTED_UNEXPECTED_HTTP_STATUS",
                    "http_status": last_status_code,
                    "attempts": attempts,
                    "payload_bytes_received": total_raw_bytes,
                    "decoded_bytes": total_decoded_bytes,
                    "violations": [f"UNEXPECTED_HTTP_STATUS:{last_status_code}"],
                }

            return {
                "ok": True,
                "status": "HTTP_200_BOUNDED_OK",
                "http_status": 200,
                "attempts": attempts,
                "payload_bytes_received": total_raw_bytes,
                "decoded_bytes": total_decoded_bytes,
                "raw_body_sha256": hashlib.sha256(raw_body).hexdigest(),
                "decoded_body_sha256": hashlib.sha256(decoded_body).hexdigest(),
                "decoded_body_b64": base64.b64encode(decoded_body).decode("ascii"),
                "headers_redacted": {"content-type": headers_lower.get("content-type", ""), "content-encoding": headers_lower.get("content-encoding", "")},
                "sleeps_honored": sleeps_honored,
                "elapsed_sec": round(time.monotonic() - t_start, 4),
                "tls_verified": scheme == "https",
                "wire_overhead_disclosure": "HTTP_PAYLOAD_BYTES_MEASURED_TCP_TLS_WIRE_OVERHEAD_NOT_MEASURED",
                "violations": [],
            }
        except (TimeoutError, socket.timeout) as exc:
            last_error = f"TIMEOUT:{exc}"
            if (deadline - time.monotonic()) <= 0.0 or attempts > max_retries:
                return {
                    "ok": False,
                    "status": "ABORTED_TOTAL_DEADLINE_EXCEEDED",
                    "http_status": last_status_code,
                    "attempts": attempts,
                    "payload_bytes_received": total_raw_bytes,
                    "decoded_bytes": total_decoded_bytes,
                    "sleeps_honored": sleeps_honored,
                    "violations": [last_error],
                }
        except ssl.SSLError as exc:
            return {
                "ok": False,
                "status": "REJECTED_TLS_VERIFICATION_FAILED_NONRETRY",
                "http_status": None,
                "attempts": attempts,
                "payload_bytes_received": total_raw_bytes,
                "decoded_bytes": total_decoded_bytes,
                "violations": [f"TLS_ERROR_NONRETRY:{exc}"],
            }
        except OSError as exc:
            last_error = f"SOCKET_ERROR:{exc}"
            if attempts > max_retries or (deadline - time.monotonic()) <= 0.05:
                return {
                    "ok": False,
                    "status": "ABORTED_SOCKET_ERROR",
                    "http_status": last_status_code,
                    "attempts": attempts,
                    "payload_bytes_received": total_raw_bytes,
                    "decoded_bytes": total_decoded_bytes,
                    "violations": [last_error],
                }
            sleep_fn(min(0.1 * attempts, max(0.0, deadline - time.monotonic() - 0.01)))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


# =====================================================================
# F16: Bounded Public WS Session (Ack, Epochs, Heartbeat, Caps, Reconnect)
# =====================================================================
async def _execute_r6_bounded_ws_session_async(
    ws_url: str,
    subscribe_payload: Mapping[str, Any],
    *,
    remaining_deadline_sec: float,
    max_frame_bytes: int,
    max_messages: int,
    max_payload_bytes: int,
    max_queue_items: int,
    max_queue_bytes: int,
    max_reconnects: int = 2,
    heartbeat_interval_sec: float = 20.0,
    pong_timeout_sec: float = 5.0,
    qa_loopback_mode: bool = False,
    external_capture_permitted: bool = False,
) -> Dict[str, Any]:
    import websockets

    parsed = urlsplit(str(ws_url))
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port or (443 if scheme == "wss" else 80)

    is_loopback = host in ("127.0.0.1", "::1")
    if is_loopback:
        if not qa_loopback_mode:
            return {"ok": False, "status": "BLOCKED_LOOPBACK_WITHOUT_QA_MODE", "violations": ["LOOPBACK_WS_FORBIDDEN_IN_LIVE"]}
    else:
        if qa_loopback_mode or not external_capture_permitted:
            return {"ok": False, "status": "BLOCKED_EXTERNAL_WS_FORBIDDEN", "violations": ["EXTERNAL_WS_NOT_AUTHORIZED"]}
        if scheme != "wss" or host != APPROVED_WS_HOST_R6 or port != 443:
            return {"ok": False, "status": "BLOCKED_UNALLOWLISTED_WS_TARGET", "violations": [f"FORBIDDEN_WS_TARGET:{ws_url}"]}

    op = str(subscribe_payload.get("op") or "").strip().lower()
    args = subscribe_payload.get("args")
    if op != "subscribe" or not isinstance(args, list) or len(args) != 1 or not isinstance(args[0], Mapping):
        return {"ok": False, "status": "REJECTED_FORBIDDEN_WS_OP_OR_ARGS", "violations": [f"INVALID_WS_SUBSCRIBE_OP:{op}"]}
    arg0 = args[0]
    ch = str(arg0.get("channel") or "").strip()
    if ch not in ("books1", "ticker"):
        return {"ok": False, "status": "REJECTED_UNALLOWLISTED_WS_CHANNEL", "violations": [f"FORBIDDEN_WS_CHANNEL:{ch}"]}
    if str(arg0.get("instType") or "") != "USDT-FUTURES" or str(arg0.get("instId") or "") != "XAUUSDT":
        return {"ok": False, "status": "REJECTED_WS_INSTRUMENT_MISMATCH", "violations": ["WS_INST_TYPE_OR_ID_MISMATCH"]}

    ssl_ctx = None
    if scheme == "wss":
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        ssl_ctx.check_hostname = True
        ssl_ctx.verify_mode = ssl.CERT_REQUIRED

    t_start = time.monotonic()
    deadline = t_start + max(0.0, float(remaining_deadline_sec))
    queue_writer = R6BoundedQueueWriter(max_items=max_queue_items, max_bytes=max_queue_bytes)

    epochs: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    discontinuities: List[Dict[str, Any]] = []
    connections_opened = 0
    reconnects_used = 0
    total_messages = 0
    total_payload_bytes = 0
    pongs_received = 0
    abort_reason: Optional[str] = None
    violations: List[str] = []

    epoch_num = 0
    while True:
        rem = deadline - time.monotonic()
        if rem <= 0.05:
            break
        if epoch_num > 0:
            if reconnects_used >= max_reconnects:
                abort_reason = "RECONNECT_CAP_REACHED"
                break
            reconnects_used += 1
            # Deterministic bounded backoff (no unbounded sleep)
            backoff = min(0.05 * reconnects_used, max(0.01, rem * 0.25))
            await asyncio.sleep(backoff)

        epoch_num += 1
        ep_start_utc_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        ep_record: Dict[str, Any] = {
            "epoch": epoch_num,
            "connected_utc_ms": ep_start_utc_ms,
            "acked": False,
            "snapshot_received": False,
            "epoch_healthy": False,
            "closed_utc_ms": None,
            "close_reason": None,
            "messages_in_epoch": 0,
        }
        epochs.append(ep_record)

        try:
            rem_open = max(0.05, deadline - time.monotonic())
            async with websockets.connect(
                ws_url,
                ssl=ssl_ctx,
                max_size=max_frame_bytes,
                max_queue=max_queue_items,
                open_timeout=min(5.0, rem_open),
                close_timeout=min(1.0, rem_open),
                ping_interval=None,
            ) as ws:
                connections_opened += 1
                await ws.send(json.dumps(dict(subscribe_payload), separators=(",", ":")))

                last_ping_sent = time.monotonic()
                awaiting_pong_since: Optional[float] = None

                while True:
                    rem_loop = deadline - time.monotonic()
                    if rem_loop <= 0.02:
                        ep_record["close_reason"] = "DEADLINE_REACHED"
                        break

                    # Send documented Bitget text "ping" heartbeat when interval elapsed
                    now_m = time.monotonic()
                    if awaiting_pong_since is None and (now_m - last_ping_sent) >= heartbeat_interval_sec:
                        await ws.send("ping")
                        last_ping_sent = now_m
                        awaiting_pong_since = now_m
                    if awaiting_pong_since is not None and (now_m - awaiting_pong_since) > pong_timeout_sec:
                        ep_record["close_reason"] = "HEARTBEAT_PONG_TIMEOUT"
                        discontinuities.append({
                            "epoch": epoch_num,
                            "utc_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
                            "reason": "HEARTBEAT_PONG_TIMEOUT",
                        })
                        break

                    wait_slice = min(rem_loop, max(0.02, heartbeat_interval_sec))
                    try:
                        raw_msg = await asyncio.wait_for(ws.recv(), timeout=wait_slice)
                    except asyncio.TimeoutError:
                        continue

                    recv_utc_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                    recv_mono = time.monotonic()
                    raw_bytes = raw_msg.encode("utf-8") if isinstance(raw_msg, str) else bytes(raw_msg)
                    sz = len(raw_bytes)
                    if sz > max_frame_bytes:
                        abort_reason = "WS_FRAME_EXCEEDS_MAX_BYTES"
                        violations.append(f"WS_FRAME_OVERSIZE:{sz}>{max_frame_bytes}")
                        ep_record["close_reason"] = abort_reason
                        break

                    total_messages += 1
                    total_payload_bytes += sz
                    if total_messages > max_messages:
                        abort_reason = "WS_MAX_MESSAGES_REACHED"
                        ep_record["close_reason"] = abort_reason
                        break
                    if total_payload_bytes > max_payload_bytes:
                        abort_reason = "WS_MAX_PAYLOAD_BYTES_REACHED"
                        ep_record["close_reason"] = abort_reason
                        break

                    if raw_msg == "pong":
                        pongs_received += 1
                        awaiting_pong_since = None
                        continue

                    try:
                        parsed_msg = json.loads(raw_bytes.decode("utf-8"))
                    except Exception:
                        abort_reason = "WS_MALFORMED_JSON_FRAME"
                        violations.append("WS_MALFORMED_JSON")
                        ep_record["close_reason"] = abort_reason
                        break

                    if not isinstance(parsed_msg, dict):
                        abort_reason = "WS_NON_OBJECT_JSON_FRAME"
                        violations.append("WS_NON_OBJECT_FRAME")
                        ep_record["close_reason"] = abort_reason
                        break

                    ev_type = parsed_msg.get("event")
                    if ev_type == "error":
                        abort_reason = f"WS_EXCHANGE_ERROR:{parsed_msg.get('code')}:{parsed_msg.get('msg')}"
                        violations.append(abort_reason)
                        ep_record["close_reason"] = abort_reason
                        break
                    if ev_type == "subscribe":
                        ack_arg = parsed_msg.get("arg") or {}
                        if ack_arg.get("channel") == ch and ack_arg.get("instId") == "XAUUSDT" and ack_arg.get("instType") == "USDT-FUTURES":
                            ep_record["acked"] = True
                            continue
                        abort_reason = "WS_INVALID_SUBSCRIBE_ACK"
                        violations.append("WS_INVALID_SUBSCRIBE_ACK_CHANNEL_OR_SYMBOL")
                        ep_record["close_reason"] = abort_reason
                        break

                    # Data push frame — MUST have valid ack first!
                    if not ep_record["acked"]:
                        abort_reason = "WS_DATA_BEFORE_SUBSCRIBE_ACK"
                        violations.append("WS_DATA_RECEIVED_WITHOUT_VALID_ACK")
                        ep_record["close_reason"] = abort_reason
                        break

                    # Queue backpressure bound check (ZERO silent drops!)
                    enq = queue_writer.try_enqueue(raw_bytes, {"epoch": epoch_num, "recv_utc_ms": recv_utc_ms})
                    if not enq["enqueued"]:
                        abort_reason = "QUEUE_OVERFLOW_BACKPRESSURE_ABORT"
                        violations.append(f"WS_QUEUE_OVERFLOW:items={enq['queued_items']}:bytes={enq['queued_bytes']}")
                        ep_record["close_reason"] = abort_reason
                        break

                    action = str(parsed_msg.get("action") or "snapshot").lower()
                    if action == "snapshot" or not ep_record["snapshot_received"]:
                        if action == "snapshot":
                            ep_record["snapshot_received"] = True
                            ep_record["epoch_healthy"] = True

                    ep_record["messages_in_epoch"] += 1
                    events.append({
                        "epoch": epoch_num,
                        "epoch_healthy": ep_record["epoch_healthy"],
                        "action": action,
                        "receive_utc_ms": recv_utc_ms,
                        "receive_monotonic_sec": round(recv_mono, 6),
                        "raw_bytes_len": sz,
                        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                        "payload": parsed_msg,
                    })

                    # Drain queue immediately if consumer is healthy (in tests we can hold queue via tiny max_queue)
                    if total_messages >= max_messages:
                        abort_reason = "WS_MAX_MESSAGES_REACHED"
                        ep_record["close_reason"] = abort_reason
                        break

        except websockets.exceptions.PayloadTooBig as exc:
            abort_reason = "WS_FRAME_EXCEEDS_MAX_BYTES"
            violations.append(f"WS_PAYLOAD_TOO_BIG:{exc}")
            ep_record["close_reason"] = abort_reason
        except websockets.exceptions.ConnectionClosed as exc:
            sent_code = exc.sent.code if getattr(exc, "sent", None) is not None else None
            rcvd_code = exc.rcvd.code if getattr(exc, "rcvd", None) is not None else None
            close_code = sent_code if sent_code is not None else (rcvd_code if rcvd_code is not None else 1006)
            if sent_code == 1009 or rcvd_code == 1009 or "too big" in str(exc).lower():
                abort_reason = "WS_FRAME_EXCEEDS_MAX_BYTES"
                violations.append(f"WS_PAYLOAD_TOO_BIG:close={close_code}:{exc}")
                ep_record["close_reason"] = abort_reason
            else:
                ep_record["close_reason"] = f"DISCONNECTED:{close_code}"
                discontinuities.append({
                    "epoch": epoch_num,
                    "utc_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
                    "reason": f"DISCONNECT_EPOCH_CLOSED:{close_code}",
                })
        except (OSError, asyncio.TimeoutError) as exc:
            ep_record["close_reason"] = f"TRANSPORT_ERROR:{exc}"
            discontinuities.append({
                "epoch": epoch_num,
                "utc_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
                "reason": f"WS_CONNECT_OR_IO_ERROR:{exc}",
            })
        finally:
            ep_record["closed_utc_ms"] = int(datetime.now(timezone.utc).timestamp() * 1000)

        # Fatal protocol/cap violations do NOT reconnect!
        if abort_reason in (
            "WS_FRAME_EXCEEDS_MAX_BYTES",
            "WS_MALFORMED_JSON_FRAME",
            "WS_NON_OBJECT_JSON_FRAME",
            "WS_INVALID_SUBSCRIBE_ACK",
            "WS_DATA_BEFORE_SUBSCRIBE_ACK",
            "QUEUE_OVERFLOW_BACKPRESSURE_ABORT",
            "WS_MAX_MESSAGES_REACHED",
            "WS_MAX_PAYLOAD_BYTES_REACHED",
        ) or (abort_reason and abort_reason.startswith("WS_EXCHANGE_ERROR")):
            break
        if ep_record.get("close_reason") == "DEADLINE_REACHED":
            break

    ok = len(violations) == 0 and any(e["acked"] for e in epochs)
    return {
        "ok": ok,
        "status": "WS_SESSION_BOUNDED_OK" if ok else (abort_reason or "WS_SESSION_FAILED"),
        "connections_opened": connections_opened,
        "reconnects_used": reconnects_used,
        "total_messages": total_messages,
        "total_payload_bytes": total_payload_bytes,
        "pongs_received": pongs_received,
        "queue_overflow_events": queue_writer.overflow_events,
        "epochs": epochs,
        "discontinuities": discontinuities,
        "events": events,
        "elapsed_sec": round(time.monotonic() - t_start, 4),
        "violations": violations,
    }


def execute_r6_bounded_ws_session(
    ws_url: str,
    subscribe_payload: Mapping[str, Any],
    *,
    remaining_deadline_sec: float,
    max_frame_bytes: int,
    max_messages: int,
    max_payload_bytes: int,
    max_queue_items: int,
    max_queue_bytes: int,
    max_reconnects: int = 2,
    heartbeat_interval_sec: float = 20.0,
    pong_timeout_sec: float = 5.0,
    qa_loopback_mode: bool = False,
    external_capture_permitted: bool = False,
) -> Dict[str, Any]:
    return asyncio.run(
        _execute_r6_bounded_ws_session_async(
            ws_url,
            subscribe_payload,
            remaining_deadline_sec=remaining_deadline_sec,
            max_frame_bytes=max_frame_bytes,
            max_messages=max_messages,
            max_payload_bytes=max_payload_bytes,
            max_queue_items=max_queue_items,
            max_queue_bytes=max_queue_bytes,
            max_reconnects=max_reconnects,
            heartbeat_interval_sec=heartbeat_interval_sec,
            pong_timeout_sec=pong_timeout_sec,
            qa_loopback_mode=qa_loopback_mode,
            external_capture_permitted=external_capture_permitted,
        )
    )


# =====================================================================
# F14 & F17: Owned Subprocess Isolation, Supervision & OS Reap Proof
# =====================================================================
@dataclass
class R6OwnedWorkerHandle:
    attempt_id: str
    pid: int
    create_time: float
    containment_kind: str  # "WINDOWS_JOB_OBJECT" | "POSIX_PGID"
    containment_id: str
    proc: subprocess.Popen[bytes]
    job_guard: Optional[_WindowsJobObjectGuard]
    launched_utc_ms: int
    launched_monotonic: float
    observed_descendant_identities: List[Tuple[int, float]]


def _sanitized_child_env() -> Dict[str, str]:
    """Builds minimal sanitized child environment (strips secrets/API keys/tokens/waivers)."""
    keep_keys = ("PATH", "PATHEXT", "SYSTEMROOT", "COMSPEC", "WINDIR", "TEMP", "TMP", "PYTHONPATH", "SSL_CERT_FILE")
    env: Dict[str, str] = {"PYTHONHASHSEED": "0", "PYTHONIOENCODING": "utf-8"}
    for k in keep_keys:
        v = os.environ.get(k)
        if v is not None:
            env[k] = v
    # Ensure backend/app root is on PYTHONPATH
    app_backend = str(Path(__file__).resolve().parents[1])
    cur_pp = env.get("PYTHONPATH", "")
    if app_backend not in cur_pp.split(os.pathsep):
        env["PYTHONPATH"] = f"{app_backend}{os.pathsep}{cur_pp}" if cur_pp else app_backend
    return env


def start_r6_owned_transport_worker(
    lease: R6DurableSessionLease,
    validated_request: Mapping[str, Any],
    limits: Mapping[str, Any],
) -> R6OwnedWorkerHandle:
    """
    Spawns an owned isolated child subprocess inside a Windows Job Object (or POSIX process group)
    with sanitized environment and zero shell execution. Sends the immutable validated request over stdin.
    """
    payload = {
        "lease_id": lease.lease_id,
        "session_id": lease.session_id,
        "external_capture_permitted": bool(lease.external_capture_permitted),
        "request": dict(validated_request),
        "limits": dict(limits),
    }
    cmd = [sys.executable, "-m", "services.kh_03_r6_bounded_transport_service", "--child-worker"]
    job_guard: Optional[_WindowsJobObjectGuard] = None
    popen_kwargs: Dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": _sanitized_child_env(),
        "close_fds": True,
    }
    if os.name == "nt":
        job_guard = _WindowsJobObjectGuard()
        containment_kind = "WINDOWS_JOB_OBJECT"
        containment_id = f"winjob:{job_guard.handle}"
    else:
        popen_kwargs["start_new_session"] = True
        containment_kind = "POSIX_PGID"
        containment_id = "posix_pgid:pending"

    t_launch_mono = time.monotonic()
    t_launch_utc_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    proc = subprocess.Popen(cmd, **popen_kwargs)

    if os.name == "nt" and job_guard is not None:
        assigned = job_guard.assign(int(proc._handle))  # type: ignore[attr-defined]
        if not assigned:
            proc.kill()
            proc.wait(timeout=3.0)
            job_guard.close()
            raise OSError("FAILED_ASSIGN_CHILD_TO_WINDOWS_JOB_OBJECT")
    elif os.name != "nt":
        containment_id = f"posix_pgid:{os.getpgid(proc.pid)}"

    try:
        ctime = float(psutil.Process(proc.pid).create_time())
    except Exception:
        ctime = time.time()

    # Deliver immutable request and close stdin immediately (one-shot request channel)
    req_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert proc.stdin is not None
    try:
        proc.stdin.write(req_bytes)
        proc.stdin.flush()
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass

    return R6OwnedWorkerHandle(
        attempt_id=str(validated_request.get("attempt_id") or "unknown"),
        pid=int(proc.pid),
        create_time=ctime,
        containment_kind=containment_kind,
        containment_id=containment_id,
        proc=proc,
        job_guard=job_guard,
        launched_utc_ms=t_launch_utc_ms,
        launched_monotonic=t_launch_mono,
        observed_descendant_identities=[],
    )


def cancel_and_reap_r6_transport(
    handle: R6OwnedWorkerHandle,
    *,
    grace_sec: float = 0.25,
    reap_timeout_sec: float = 3.0,
    simulate_unverifiable_os_proof: bool = False,
) -> Dict[str, Any]:
    """
    Forcibly stops an owned transport worker and ALL its descendants via Windows Job Object
    (`TerminateJobObject`) or POSIX `os.killpg`, reaps the process, and verifies actual OS exit
    using PID + `create_time`. If verification fails or any descendant survives -> FAIL_UNDRAINED_TRANSPORT.
    """
    t0 = time.monotonic()
    # Snapshot any currently alive descendants BEFORE termination so we prove they exited too!
    descendants: List[Tuple[int, float]] = list(handle.observed_descendant_identities)
    try:
        parent_p = psutil.Process(handle.pid)
        for ch in parent_p.children(recursive=True):
            try:
                pair = (int(ch.pid), float(ch.create_time()))
                if pair not in descendants:
                    descendants.append(pair)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    force_terminated = False
    if handle.proc.poll() is None:
        # Cooperative terminate first
        try:
            handle.proc.terminate()
        except Exception:
            pass
        try:
            handle.proc.wait(timeout=max(0.05, float(grace_sec)))
        except subprocess.TimeoutExpired:
            force_terminated = True

    # Force terminate entire owned process tree if still alive OR if descendants were observed!
    any_desc_alive = any(_is_same_os_process_alive(dpid, dctime) for dpid, dctime in descendants)
    if handle.proc.poll() is None or any_desc_alive:
        force_terminated = True
        if os.name == "nt" and handle.job_guard is not None:
            handle.job_guard.terminate(137)
        elif os.name != "nt":
            try:
                os.killpg(os.getpgid(handle.pid), signal.SIGKILL)
            except Exception:
                try:
                    handle.proc.kill()
                except Exception:
                    pass
        else:
            try:
                handle.proc.kill()
            except Exception:
                pass
        try:
            handle.proc.wait(timeout=max(0.2, float(reap_timeout_sec)))
        except subprocess.TimeoutExpired:
            pass

    # Close pipes and Job Object handle
    for stream in (handle.proc.stdout, handle.proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
    if handle.job_guard is not None:
        handle.job_guard.close()

    # Verify OS identity exit (brief spin up to 0.5s for kernel process table cleanup)
    verify_deadline = time.monotonic() + 0.5
    child_alive = _is_same_os_process_alive(handle.pid, handle.create_time)
    surviving_desc = [dpid for dpid, dctime in descendants if _is_same_os_process_alive(dpid, dctime)]
    while (child_alive or surviving_desc) and time.monotonic() < verify_deadline:
        time.sleep(0.02)
        child_alive = _is_same_os_process_alive(handle.pid, handle.create_time)
        surviving_desc = [dpid for dpid, dctime in descendants if _is_same_os_process_alive(dpid, dctime)]

    os_proof_available = not simulate_unverifiable_os_proof
    reaped_verified = os_proof_available and (not child_alive) and (len(surviving_desc) == 0) and (handle.proc.poll() is not None)
    status = "REAPED_VERIFIED" if reaped_verified else "FAIL_UNDRAINED_TRANSPORT"

    return {
        "status": status,
        "reaped_verified": reaped_verified,
        "os_proof_available": os_proof_available,
        "child_pid": handle.pid,
        "child_create_time": handle.create_time,
        "child_returncode": handle.proc.poll(),
        "child_still_alive": child_alive,
        "containment_kind": handle.containment_kind,
        "containment_id": handle.containment_id,
        "force_terminated_tree": force_terminated,
        "observed_descendant_pids": [d[0] for d in descendants],
        "surviving_descendant_pids": surviving_desc,
        "reap_elapsed_sec": round(time.monotonic() - t0, 4),
    }


def supervise_r6_session(
    *,
    ledger: R6DurableGrantLedger,
    lease: R6DurableSessionLease,
    plan: R6BoundCapturePlan,
    verified_grant: R6VerifiedGrant,
    validated_request: Mapping[str, Any],
    attempt_id: str,
    request_type: str,
    reserved_caps: Mapping[str, int],
    resource_sample_fn: Callable[[], Mapping[str, Any]],
    expected_volumes: Mapping[str, Path],
    session_deadline_monotonic: float,
    cancel_event: Optional[threading.Event] = None,
    utc_now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    qa_loopback_mode: bool = False,
    allow_fixture_resource_sample: bool = False,
    simulate_unverifiable_os_proof: bool = False,
) -> Dict[str, Any]:
    """
    Parent-side R6 transport supervisor:
      1. Revalidates plan MAC, remaining session/grant deadline, cancel flag, and strict OS resource guard BEFORE dispatch.
      2. Persists durable budget reservation (QUEUED) + attempt start (STARTED) BEFORE launching child subprocess.
      3. Launches isolated owned subprocess (Windows Job Object / POSIX PGID), monitors deadline/cancel/resource guard.
      4. Reaps & verifies OS exit; discards any late result after cancellation/expiry; settles budget conservatively
         (retaining unknown bytes as UNCERTAIN on abort/kill; quarantining ledger on FAIL_UNDRAINED_TRANSPORT!).
    """
    lifecycle_trace: List[Dict[str, Any]] = []
    now_dt = utc_now_fn()
    now_ms = int(now_dt.astimezone(timezone.utc).timestamp() * 1000)

    def _ev(stage_name: str, **extra: Any) -> None:
        lifecycle_trace.append({
            "event": stage_name,
            "attempt_id": attempt_id,
            "session_id": lease.session_id,
            "utc_ms": int(utc_now_fn().astimezone(timezone.utc).timestamp() * 1000),
            "monotonic_sec": round(time.monotonic(), 6),
            **extra,
        })

    # 1. Plan integrity, external/QA boundary, cancel, deadline, and resource checks at ACTUAL dispatch
    plan_ok, plan_viols = verify_r6_bound_plan_integrity(plan, verified_grant)
    if not plan_ok:
        return {"ok": False, "status": "REJECTED_PLAN_INTEGRITY", "accepted_result": None, "lifecycle_trace": lifecycle_trace, "violations": plan_viols}

    if not qa_loopback_mode and (not verified_grant.external_capture_permitted or not lease.external_capture_permitted):
        return {"ok": False, "status": "BLOCKED_OWNER_AUTHORIZATION", "accepted_result": None, "lifecycle_trace": lifecycle_trace, "violations": ["EXTERNAL_CAPTURE_NOT_PERMITTED"]}

    if cancel_event is not None and cancel_event.is_set():
        return {"ok": False, "status": "ABORTED_CANCELLED_PRE_DISPATCH", "accepted_result": None, "lifecycle_trace": lifecycle_trace, "violations": ["CANCELLED_BEFORE_DISPATCH"]}

    rem_sec = min(session_deadline_monotonic - time.monotonic(), (lease.expires_utc_ms - now_ms) / 1000.0)
    if rem_sec <= 0.05:
        return {"ok": False, "status": "ABORTED_EXPIRED_AT_ACTUAL_START", "accepted_result": None, "lifecycle_trace": lifecycle_trace, "violations": ["SESSION_OR_GRANT_EXPIRED_BEFORE_DISPATCH"]}

    res_check = validate_r6_resource_guard(
        resource_sample_fn(),
        expected_volumes=expected_volumes,
        phase="runtime",
        allow_fixture_sample=allow_fixture_resource_sample,
    )
    if not res_check["ok"]:
        return {"ok": False, "status": "ABORTED_RESOURCE_GUARD_PRE_DISPATCH", "accepted_result": None, "lifecycle_trace": lifecycle_trace, "violations": res_check["violations"]}

    # 2. Durable reservation BEFORE subprocess launch
    res_rec = ledger.reserve_r6_io_budget_atomic(lease, attempt_id, request_type, reserved_caps, actual_now=now_dt)
    if not res_rec["reserved"]:
        return {"ok": False, "status": res_rec["status"], "accepted_result": None, "lifecycle_trace": lifecycle_trace, "violations": res_rec["violations"]}
    _ev("BUDGET_RESERVED_DURABLE", reserved_caps=dict(reserved_caps))

    # Re-check cancel/expiry immediately before marking STARTED (allows clean pre-start release if cancelled right here)
    now_dt2 = utc_now_fn()
    rem_sec2 = min(session_deadline_monotonic - time.monotonic(), (lease.expires_utc_ms - int(now_dt2.timestamp() * 1000)) / 1000.0)
    if (cancel_event is not None and cancel_event.is_set()) or rem_sec2 <= 0.05:
        ledger.settle_r6_io_budget(lease, attempt_id, outcome="ABORTED_PRE_START", reason="EXPIRED_OR_CANCELLED_BEFORE_START", actual_now=now_dt2)
        return {"ok": False, "status": "ABORTED_EXPIRED_AT_ACTUAL_START", "accepted_result": None, "lifecycle_trace": lifecycle_trace, "violations": ["EXPIRED_OR_CANCELLED_PRE_START"]}

    st_rec = ledger.mark_r6_attempt_started(lease, attempt_id, actual_now=now_dt2)
    if not st_rec["started"]:
        ledger.settle_r6_io_budget(lease, attempt_id, outcome="ABORTED_PRE_START", reason="START_FAILED", actual_now=now_dt2)
        return {"ok": False, "status": st_rec["status"], "accepted_result": None, "lifecycle_trace": lifecycle_trace, "violations": st_rec["violations"]}
    _ev("ATTEMPT_STARTED_DURABLE")

    # 3. Launch owned isolated child subprocess
    req_for_child = dict(validated_request)
    req_for_child["attempt_id"] = attempt_id
    req_for_child["qa_loopback_mode"] = bool(qa_loopback_mode)
    limits_for_child = {
        "remaining_deadline_sec": max(0.05, rem_sec2),
        "max_payload_bytes": int(reserved_caps.get("payload_bytes", 65_536)),
        "max_decoded_bytes": int(reserved_caps.get("decoded_bytes", 131_072)),
        "max_messages": int(reserved_caps.get("messages", 100)),
    }
    handle = start_r6_owned_transport_worker(lease, req_for_child, limits_for_child)
    _ev("CHILD_PROCESS_LAUNCHED", child_pid=handle.pid, child_create_time=handle.create_time, containment_kind=handle.containment_kind)

    # Read child stdout in a bounded helper thread so parent loop can enforce cancel/deadline/resource checks at 20ms granularity!
    stdout_holder: Dict[str, Any] = {"data": b"", "stderr": b"", "done": False}

    def _drain_pipes() -> None:
        try:
            out_b = handle.proc.stdout.read(max(4 * 1024 * 1024, limits_for_child["max_decoded_bytes"] * 2 + 262_144)) if handle.proc.stdout else b""
            err_b = handle.proc.stderr.read(65_536) if handle.proc.stderr else b""
            stdout_holder["data"] = out_b
            stdout_holder["stderr"] = err_b
        except Exception:
            pass
        finally:
            stdout_holder["done"] = True

    reader_t = threading.Thread(target=_drain_pipes, daemon=True)
    reader_t.start()

    aborted_reason: Optional[str] = None
    while handle.proc.poll() is None:
        # Track any spawned grandchildren while child runs so reap proof covers them too!
        try:
            for ch in psutil.Process(handle.pid).children(recursive=True):
                pair = (int(ch.pid), float(ch.create_time()))
                if pair not in handle.observed_descendant_identities:
                    handle.observed_descendant_identities.append(pair)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        if cancel_event is not None and cancel_event.is_set():
            aborted_reason = "CANCELLED_MIDFLIGHT"
            break
        if time.monotonic() >= session_deadline_monotonic:
            aborted_reason = "SESSION_DEADLINE_EXCEEDED_MIDFLIGHT"
            break
        if int(utc_now_fn().timestamp() * 1000) >= lease.expires_utc_ms:
            aborted_reason = "GRANT_EXPIRED_MIDFLIGHT"
            break
        rc_rt = validate_r6_resource_guard(
            resource_sample_fn(),
            expected_volumes=expected_volumes,
            phase="runtime",
            allow_fixture_sample=allow_fixture_resource_sample,
        )
        if not rc_rt["ok"]:
            aborted_reason = f"RESOURCE_FLOOR_BREACH:{rc_rt['violations'][0]}"
            break
        time.sleep(0.02)

    # Always reap & verify OS identity exit!
    reap_info = cancel_and_reap_r6_transport(
        handle,
        grace_sec=0.15,
        reap_timeout_sec=3.0,
        simulate_unverifiable_os_proof=simulate_unverifiable_os_proof,
    )
    reader_t.join(timeout=1.0)
    _ev("CHILD_PROCESS_REAPED", **reap_info)

    if not reap_info["reaped_verified"]:
        ledger.settle_r6_io_budget(lease, attempt_id, outcome="UNCERTAIN", reason="FAIL_UNDRAINED_TRANSPORT", actual_now=utc_now_fn())
        ledger.finalize_r6_session(lease, quarantine=True, reason="FAIL_UNDRAINED_TRANSPORT", actual_now=utc_now_fn())
        return {
            "ok": False,
            "status": "FAIL_UNDRAINED_TRANSPORT",
            "accepted_result": None,
            "late_result_discarded": bool(stdout_holder["data"]),
            "reap_info": reap_info,
            "lifecycle_trace": lifecycle_trace,
            "violations": ["UNDRAINED_OR_UNVERIFIED_OS_TRANSPORT_QUARANTINED"],
        }

    # If aborted/cancelled/expired, ANY output that raced in MUST be discarded (never accepted!)
    if aborted_reason is not None:
        late_discarded = bool(stdout_holder["data"])
        ledger.settle_r6_io_budget(lease, attempt_id, outcome="ABORTED_MIDFLIGHT", reason=aborted_reason, actual_now=utc_now_fn())
        _ev("ATTEMPT_SETTLED_ABORTED_MIDFLIGHT", reason=aborted_reason, late_result_discarded=late_discarded)
        return {
            "ok": False,
            "status": f"ABORTED_{aborted_reason}",
            "accepted_result": None,
            "late_result_discarded": late_discarded,
            "reap_info": reap_info,
            "lifecycle_trace": lifecycle_trace,
            "violations": [aborted_reason],
        }

    # Parse child result
    try:
        child_res = json.loads(stdout_holder["data"].decode("utf-8"))
    except Exception as exc:
        ledger.settle_r6_io_budget(lease, attempt_id, outcome="UNCERTAIN", reason=f"CHILD_IPC_DECODE_ERROR:{exc}", actual_now=utc_now_fn())
        return {
            "ok": False,
            "status": "ABORTED_CHILD_IPC_MALFORMED",
            "accepted_result": None,
            "reap_info": reap_info,
            "lifecycle_trace": lifecycle_trace,
            "violations": [f"CHILD_IPC_DECODE_ERROR:{exc}"],
        }

    # Settle actual known bytes/requests/messages/connections
    known_actual = {
        "requests": int(child_res.get("attempts", 1 if request_type == "rest_get" else 0)),
        "connections": int(child_res.get("connections_opened", 1 if request_type == "ws_session" else 0)),
        "subscriptions": int(1 if (request_type == "ws_session" and child_res.get("connections_opened", 0) > 0) else 0),
        "messages": int(child_res.get("total_messages", 0)),
        "payload_bytes": int(child_res.get("payload_bytes_received", child_res.get("total_payload_bytes", 0))),
        "decoded_bytes": int(child_res.get("decoded_bytes", child_res.get("total_payload_bytes", 0))),
        "retries": max(0, int(child_res.get("attempts", 1)) - 1) if request_type == "rest_get" else int(child_res.get("reconnects_used", 0)),
    }
    settle_outcome = "COMPLETED" if child_res.get("ok") is True else "UNCERTAIN"
    ledger.settle_r6_io_budget(
        lease,
        attempt_id,
        outcome=settle_outcome,
        actual_known=known_actual,
        reason=str(child_res.get("status") or ""),
        actual_now=utc_now_fn(),
    )
    _ev("ATTEMPT_SETTLED_COMPLETE", child_status=child_res.get("status"), known_actual=known_actual)

    return {
        "ok": bool(child_res.get("ok") is True),
        "status": str(child_res.get("status") or "UNKNOWN"),
        "accepted_result": child_res if child_res.get("ok") is True else None,
        "child_raw_outcome": child_res,
        "late_result_discarded": False,
        "reap_info": reap_info,
        "lifecycle_trace": lifecycle_trace,
        "violations": list(child_res.get("violations") or ()),
    }


# =====================================================================
# Isolated Child Worker Entrypoint (`--child-worker`)
# =====================================================================
def _run_child_worker_cli() -> int:
    raw_in = sys.stdin.buffer.read(1_048_576)
    try:
        msg = json.loads(raw_in.decode("utf-8"))
    except Exception as exc:
        sys.stdout.write(json.dumps({"ok": False, "status": "CHILD_BAD_INPUT", "violations": [str(exc)]}))
        sys.stdout.flush()
        return 2

    req = msg.get("request") or {}
    lims = msg.get("limits") or {}
    ext_perm = bool(msg.get("external_capture_permitted", False))
    qa_loop = bool(req.get("qa_loopback_mode", False))
    kind = str(req.get("kind") or "")

    if kind == "rest_get":
        out = execute_r6_bounded_rest_get(
            str(req["url"]),
            dict(req.get("params") or {}),
            remaining_deadline_sec=float(req.get("remaining_deadline_sec", lims.get("remaining_deadline_sec", 5.0))),
            max_payload_bytes=int(req.get("max_payload_bytes", lims.get("max_payload_bytes", 65_536))),
            max_decoded_bytes=int(req.get("max_decoded_bytes", lims.get("max_decoded_bytes", 131_072))),
            max_retries=int(req.get("max_retries", 2)),
            qa_loopback_mode=qa_loop,
            external_capture_permitted=ext_perm,
        )
    elif kind == "ws_session":
        out = execute_r6_bounded_ws_session(
            str(req["ws_url"]),
            dict(req.get("subscribe_payload") or {}),
            remaining_deadline_sec=float(req.get("remaining_deadline_sec", lims.get("remaining_deadline_sec", 5.0))),
            max_frame_bytes=int(req.get("max_frame_bytes", 65_536)),
            max_messages=int(req.get("max_messages", lims.get("max_messages", 50))),
            max_payload_bytes=int(req.get("max_payload_bytes", lims.get("max_payload_bytes", 131_072))),
            max_queue_items=int(req.get("max_queue_items", 64)),
            max_queue_bytes=int(req.get("max_queue_bytes", 131_072)),
            max_reconnects=int(req.get("max_reconnects", 2)),
            heartbeat_interval_sec=float(req.get("heartbeat_interval_sec", 20.0)),
            pong_timeout_sec=float(req.get("pong_timeout_sec", 5.0)),
            qa_loopback_mode=qa_loop,
            external_capture_permitted=ext_perm,
        )
    elif kind == "qa_stall_noncooperative" and qa_loop:
        # Strictly offline QA fault-injection child that ignores SIGTERM and optionally spawns a grandchild!
        try:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        except Exception:
            pass
        if req.get("spawn_grandchild"):
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        stall_sec = float(req.get("stall_sec", 5.0))
        time.sleep(stall_sec)
        out = {"ok": True, "status": "QA_STALL_LATE_COMPLETE", "attempts": 1, "payload_bytes_received": 16, "decoded_bytes": 16, "violations": []}
    else:
        out = {"ok": False, "status": "REJECTED_UNKNOWN_CHILD_KIND", "violations": [f"UNKNOWN_KIND:{kind}"]}

    sys.stdout.write(json.dumps(out, sort_keys=True, separators=(",", ":")))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child-worker", action="store_true")
    ns = parser.parse_args()
    if ns.child_worker:
        raise SystemExit(_run_child_worker_cli())
