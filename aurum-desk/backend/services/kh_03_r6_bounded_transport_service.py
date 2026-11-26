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
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode, urlsplit
import zlib

import certifi
import psutil

from services.kh_03_r6_a1_trust_service import (
    A1_EXTERNAL_CAPTURE_ENABLED,
    FORBIDDEN_REQUEST_OVERRIDE_KEYS_A1,
    R6AuthorizedDispatchRequest,
    R6DurableReservationReceipt,
    R6DurableSettlementReceipt,
    R6TrustedSupervisorResult,
    R6WorkerEnvelope,
    _A1_INTERNAL_CAPABILITY,
    _issue_r6_a1_trusted_supervisor_result,
    build_r6_a1_worker_envelope,
    canonicalize_r6_bound_plan_core,
    complete_caps_vector_a1,
    freeze_strict_int_caps_a1,
    validate_r6_a1_worker_envelope_strict,
    verify_authorized_dispatch_request_a1,
    verify_durable_settlement_receipt_a1,
    verify_registered_grant_authenticity_a1,
    verify_registered_lease_authenticity_a1,
)
from services.kh_03_r6_durable_grant_service import (
    BUDGET_FIELDS_R6,
    R6DurableGrantLedger,
    R6DurableSessionLease,
    R6VerifiedGrant,
    _canonical_json_bytes,
    _is_strict_int,
    _sha256_canonical,
    _strict_loads_r6,
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
# F08 / F09 / F10: Deep-Frozen Bound Capture Plan & Full Recompute Verifier
# =====================================================================
@dataclass(frozen=True)
class R6BoundCapturePlan:
    plan_id: str
    stage: str
    purpose: str
    grant_identity_key: str
    grant_payload_digest: str
    tested_executable_sha: str
    release_manifest_sha256: str
    registry_digest: str
    venue: str
    api_family: str
    symbol: str
    product_type: str
    destination_store_id: str
    destination_root: str
    endpoints: Tuple[str, ...]
    ws_channels: Tuple[str, ...]
    window_start_ms: int
    window_end_ms: int
    max_session_duration_sec: int
    max_concurrency: int
    caps: Mapping[str, int]
    caps_pairs: Tuple[Tuple[str, int], ...]
    expires_utc_ms: int
    external_capture_permitted: bool
    task_policy_digest: str
    plan_digest: str
    plan_mac_hex: str

    def verify_mac_against_grant(self, verified_grant: R6VerifiedGrant, *, recomputed_digest: str) -> bool:
        expected_mac = _compute_plan_mac(
            recomputed_digest,
            verified_grant.grant_payload_digest,
            verified_grant.release_manifest_sha256,
            self.registry_digest,
            self.external_capture_permitted,
        )
        return hmac.compare_digest(self.plan_mac_hex, expected_mac)

    def to_redacted_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "stage": self.stage,
            "purpose": self.purpose,
            "grant_identity_key": self.grant_identity_key,
            "grant_payload_digest": self.grant_payload_digest,
            "tested_executable_sha": self.tested_executable_sha,
            "release_manifest_sha256": self.release_manifest_sha256,
            "registry_digest": self.registry_digest,
            "venue": self.venue,
            "api_family": self.api_family,
            "symbol": self.symbol,
            "product_type": self.product_type,
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
            "task_policy_digest": self.task_policy_digest,
            "plan_digest": self.plan_digest,
        }


def _compute_plan_mac(
    plan_digest: str,
    grant_payload_digest: str,
    release_manifest_sha256: str,
    registry_digest: str,
    external_flag: bool,
) -> str:
    msg = f"{plan_digest}|{grant_payload_digest}|{release_manifest_sha256}|{registry_digest}|{int(external_flag)}".encode("utf-8")
    return hmac.new(_PLAN_MAC_SECRET_R6, msg, hashlib.sha256).hexdigest()


def verify_r6_bound_plan_integrity(
    plan: R6BoundCapturePlan,
    verified_grant: R6VerifiedGrant,
    *,
    registry: Optional[R6SourceContractRegistry] = None,
    lease: Optional[R6DurableSessionLease] = None,
    expected_executable_sha: Optional[str] = None,
    expected_release_manifest_sha256: Optional[str] = None,
    actual_now: Optional[datetime] = None,
) -> Tuple[bool, List[str]]:
    """
    F10: Full fail-closed bound plan integrity verifier.
    ALWAYS recomputes canonical core digest via F08 (`canonicalize_r6_bound_plan_core`), verifies
    stored `plan_digest` AND constant-time `plan_mac_hex`, verifies registered `verified_grant`
    authenticity, re-evaluates strict grant/registry/release/window/caps/destination/lease subset,
    and fails closed on ANY exception!
    """
    viols: List[str] = []
    try:
        if not isinstance(plan, R6BoundCapturePlan):
            return False, ["PLAN_NOT_R6_BOUND_CAPTURE_PLAN"]
        if not isinstance(verified_grant, R6VerifiedGrant):
            return False, ["GRANT_NOT_R6_VERIFIED_GRANT"]

        ok_vg, vg_viols = verify_registered_grant_authenticity_a1(verified_grant)
        if not ok_vg:
            viols.extend(vg_viols)

        # Reject mutable dict inside caps even if constructed via dataclasses.replace!
        if isinstance(plan.caps, dict):
            viols.append("PLAN_CAPS_MUST_BE_IMMUTABLE_MAPPING_PROXY")

        _, recomp_digest, core_viols = canonicalize_r6_bound_plan_core(
            plan_id=plan.plan_id,
            stage=plan.stage,
            purpose=plan.purpose,
            grant_identity_key=plan.grant_identity_key,
            grant_payload_digest=plan.grant_payload_digest,
            tested_executable_sha=plan.tested_executable_sha,
            release_manifest_sha256=plan.release_manifest_sha256,
            registry_digest=plan.registry_digest,
            venue=plan.venue,
            api_family=plan.api_family,
            symbol=plan.symbol,
            product_type=plan.product_type,
            destination_store_id=plan.destination_store_id,
            destination_root=plan.destination_root,
            endpoints=plan.endpoints,
            ws_channels=plan.ws_channels,
            window_start_ms=plan.window_start_ms,
            window_end_ms=plan.window_end_ms,
            max_session_duration_sec=plan.max_session_duration_sec,
            max_concurrency=plan.max_concurrency,
            caps=plan.caps,
            expires_utc_ms=plan.expires_utc_ms,
            external_capture_permitted=plan.external_capture_permitted,
            task_policy_digest=plan.task_policy_digest,
        )
        viols.extend(core_viols)

        if recomp_digest is None or not hmac.compare_digest(recomp_digest, str(plan.plan_digest)):
            viols.append("PLAN_CANONICAL_DIGEST_MISMATCH")

        expected_mac = _compute_plan_mac(
            recomp_digest or "",
            verified_grant.grant_payload_digest,
            verified_grant.release_manifest_sha256,
            plan.registry_digest,
            plan.external_capture_permitted,
        )
        if not hmac.compare_digest(str(plan.plan_mac_hex), expected_mac):
            viols.append("PLAN_MAC_INTEGRITY_FAILED")

        # Strict cross-binding vs verified_grant
        if plan.grant_identity_key != verified_grant.grant_identity_key:
            viols.append("PLAN_GRANT_IDENTITY_MISMATCH")
        if plan.grant_payload_digest != verified_grant.grant_payload_digest:
            viols.append("PLAN_GRANT_PAYLOAD_DIGEST_MISMATCH")
        if plan.stage != verified_grant.stage:
            viols.append("PLAN_STAGE_MISMATCH")
        if plan.purpose != verified_grant.purpose:
            viols.append("PLAN_PURPOSE_MISMATCH")
        if plan.tested_executable_sha != verified_grant.tested_executable_sha:
            viols.append("PLAN_EXECUTABLE_SHA_MISMATCH")
        if plan.release_manifest_sha256 != verified_grant.release_manifest_sha256:
            viols.append("PLAN_RELEASE_MANIFEST_MISMATCH")
        if plan.venue != verified_grant.venue or plan.api_family != verified_grant.api_family or plan.symbol != verified_grant.symbol or plan.product_type != verified_grant.product_type:
            viols.append("PLAN_INSTRUMENT_OR_FAMILY_MISMATCH")
        if plan.destination_store_id != verified_grant.destination_store_id:
            viols.append("PLAN_STORE_ID_MISMATCH")
        if Path(plan.destination_root).resolve(strict=False) != Path(verified_grant.destination_root).resolve(strict=False):
            viols.append("PLAN_DESTINATION_ROOT_DRIFT")
        if plan.external_capture_permitted != verified_grant.external_capture_permitted:
            viols.append("PLAN_EXTERNAL_PERMISSION_MISMATCH")
        if plan.expires_utc_ms != verified_grant.expires_utc_ms:
            viols.append("PLAN_EXPIRY_MISMATCH")
        if plan.window_start_ms < verified_grant.window_start_ms or plan.window_end_ms > verified_grant.window_end_ms:
            viols.append("PLAN_WINDOW_EXCEEDS_GRANT")
        if plan.max_session_duration_sec > verified_grant.max_session_duration_sec:
            viols.append("PLAN_DURATION_EXCEEDS_GRANT")
        if plan.max_concurrency > verified_grant.max_concurrency:
            viols.append("PLAN_CONCURRENCY_EXCEEDS_GRANT")

        for ep in plan.endpoints:
            if ep not in verified_grant.allowed_endpoints:
                viols.append(f"PLAN_ENDPOINT_NOT_IN_GRANT:{ep}")
        for ch in plan.ws_channels:
            if ch not in verified_grant.allowed_ws_channels:
                viols.append(f"PLAN_WS_CHANNEL_NOT_IN_GRANT:{ch}")

        frozen_c, c_pairs, c_viols = freeze_strict_int_caps_a1(plan.caps, require_complete=True)
        viols.extend(c_viols)
        if frozen_c is not None:
            if tuple(plan.caps_pairs) != c_pairs:
                viols.append("PLAN_CAPS_PAIRS_DRIFT")
            for k in BUDGET_FIELDS_R6:
                if frozen_c[k] > int(verified_grant.caps[k]):
                    viols.append(f"PLAN_CAP_EXCEEDS_GRANT:{k}:{frozen_c[k]}>{verified_grant.caps[k]}")

        # Registry re-evaluation
        if registry is not None:
            if registry.status != "DOC_VERIFIED_READY_FOR_STAGE_B_REVIEW":
                viols.append("REGISTRY_NOT_READY")
            if plan.registry_digest != registry.registry_digest:
                viols.append("PLAN_REGISTRY_DIGEST_DRIFT")
            for ep in plan.endpoints:
                if ep not in registry.endpoints:
                    viols.append(f"PLAN_ENDPOINT_NOT_IN_REGISTRY:{ep}")

        # Release / Executable pin check
        if expected_executable_sha is not None and plan.tested_executable_sha != expected_executable_sha:
            viols.append("PLAN_EXPECTED_EXECUTABLE_SHA_MISMATCH")
        if expected_release_manifest_sha256 is not None and plan.release_manifest_sha256 != expected_release_manifest_sha256:
            viols.append("PLAN_EXPECTED_RELEASE_MANIFEST_MISMATCH")

        # Lease re-evaluation
        if lease is not None:
            ok_l, l_viols = verify_registered_lease_authenticity_a1(lease)
            if not ok_l:
                viols.extend(l_viols)
            if lease.plan_digest != plan.plan_digest:
                viols.append("LEASE_PLAN_DIGEST_MISMATCH")
            if lease.grant_identity_key != plan.grant_identity_key:
                viols.append("LEASE_GRANT_IDENTITY_MISMATCH")
            if lease.store_id != plan.destination_store_id:
                viols.append("LEASE_STORE_ID_MISMATCH")
            if lease.stage != plan.stage:
                viols.append("LEASE_STAGE_MISMATCH")

        # Expiry check
        if actual_now is not None:
            now_ms = int(actual_now.astimezone(timezone.utc).timestamp() * 1000)
            if now_ms >= plan.expires_utc_ms:
                viols.append("PLAN_EXPIRED")

        return len(viols) == 0, viols
    except Exception as exc:
        return False, [f"PLAN_VERIFY_FAIL_CLOSED_EXCEPTION:{exc}"]


_ALLOWED_PROPOSED_BUDGET_KEYS_A1 = frozenset({
    "stage",
    "endpoints",
    "ws_channels",
    "window_start_ms",
    "window_end_ms",
    "max_session_duration_sec",
    "max_concurrency",
    "destination_root",
    "caps",
    "task_policy_digest",
})


def build_r6_bound_capture_plan(
    verified_grant: R6VerifiedGrant,
    registry: R6SourceContractRegistry,
    proposed_budget: Mapping[str, Any],
    resource_check: Mapping[str, Any],
    *,
    plan_id: str,
) -> Dict[str, Any]:
    """
    F09: Freezes a deep-immutable canonical capture plan strictly as a subset of `verified_grant`
    and `registry` using F08 (`canonicalize_r6_bound_plan_core`).
    NEVER silently clamps unauthorized budget/window/endpoint values; explicit empty `ws_channels=[]`
    stays empty and never widens; rejects unknown fields, callables, and unverified grants!
    """
    viols: List[str] = []
    if not isinstance(verified_grant, R6VerifiedGrant):
        return {"valid": False, "status": "BLOCKED_INVALID_GRANT", "plan": None, "violations": ["GRANT_NOT_VERIFIED_OBJECT"]}
    ok_vg, vg_viols = verify_registered_grant_authenticity_a1(verified_grant)
    if not ok_vg:
        return {"valid": False, "status": "BLOCKED_FORGED_GRANT", "plan": None, "violations": vg_viols}
    if not isinstance(registry, R6SourceContractRegistry) or registry.status != "DOC_VERIFIED_READY_FOR_STAGE_B_REVIEW":
        viols.append(f"SOURCE_REGISTRY_NOT_READY:{getattr(registry, 'status', None)}")
    if not isinstance(resource_check, Mapping) or resource_check.get("ok") is not True:
        viols.append("RESOURCE_PREFLIGHT_NOT_OK")
    elif verified_grant.external_capture_permitted and resource_check.get("status") != "RESOURCE_GUARD_OK":
        viols.append("RESOURCE_CHECK_UNTRUSTED_FOR_EXTERNAL_PLAN")

    if not isinstance(proposed_budget, Mapping):
        return {"valid": False, "status": "REJECTED_PLAN_BINDING", "plan": None, "violations": ["PROPOSED_BUDGET_NOT_MAPPING"]}

    extra_pb = sorted(set(proposed_budget.keys()) - _ALLOWED_PROPOSED_BUDGET_KEYS_A1)
    if extra_pb:
        viols.append(f"UNKNOWN_PROPOSED_BUDGET_KEYS:{','.join(extra_pb)}")
    for k, v in proposed_budget.items():
        if callable(v):
            viols.append(f"CALLABLE_INJECTION_FORBIDDEN:{k}")

    prop_stage = str(proposed_budget.get("stage") if "stage" in proposed_budget else verified_grant.stage)
    if prop_stage != verified_grant.stage:
        viols.append(f"PLAN_STAGE_MISMATCH:plan={prop_stage}:grant={verified_grant.stage}")

    if "endpoints" in proposed_budget:
        raw_eps = proposed_budget["endpoints"]
        if not isinstance(raw_eps, (list, tuple)):
            viols.append("PLAN_ENDPOINTS_MUST_BE_SEQUENCE")
            prop_eps: Tuple[str, ...] = ()
        else:
            prop_eps = tuple(str(x).strip() for x in raw_eps)
    else:
        prop_eps = tuple(verified_grant.allowed_endpoints)

    if not prop_eps:
        viols.append("PLAN_EMPTY_ENDPOINTS")
    elif len(set(prop_eps)) != len(prop_eps):
        viols.append("PLAN_DUPLICATE_ENDPOINTS")
    for ep_id in prop_eps:
        if ep_id not in verified_grant.allowed_endpoints:
            viols.append(f"PLAN_ENDPOINT_NOT_IN_GRANT:{ep_id}")
        if ep_id not in registry.endpoints:
            viols.append(f"PLAN_ENDPOINT_NOT_IN_REGISTRY:{ep_id}")

    # F09: Explicit empty `ws_channels=[]` must stay empty (NEVER widen to all!)
    if "ws_channels" in proposed_budget:
        raw_ws = proposed_budget["ws_channels"]
        if not isinstance(raw_ws, (list, tuple)):
            viols.append("PLAN_WS_CHANNELS_MUST_BE_SEQUENCE")
            prop_ws: Tuple[str, ...] = ()
        else:
            prop_ws = tuple(str(x).strip() for x in raw_ws)
    else:
        has_ws_ep = any(ep_id in registry.endpoints and registry.endpoints[ep_id].transport_kind == "WS_PUBLIC" for ep_id in prop_eps)
        prop_ws = tuple(verified_grant.allowed_ws_channels) if has_ws_ep else ()

    if len(set(prop_ws)) != len(prop_ws):
        viols.append("PLAN_DUPLICATE_WS_CHANNELS")
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

    prop_dest = str(proposed_budget.get("destination_root") if "destination_root" in proposed_budget else verified_grant.destination_root)
    if ".." in Path(prop_dest).parts or not Path(prop_dest).is_absolute():
        viols.append("PLAN_DESTINATION_ROOT_NOT_CANONICAL_ABSOLUTE")
    elif Path(prop_dest).resolve(strict=False) != Path(verified_grant.destination_root).resolve(strict=False):
        viols.append("PLAN_DESTINATION_ROOT_DRIFT")

    if "caps" in proposed_budget:
        prop_caps_in = proposed_budget["caps"]
        if not isinstance(prop_caps_in, Mapping):
            viols.append("PLAN_CAPS_NOT_MAPPING")
            prop_caps_in = {}
        else:
            extra_c = sorted(set(prop_caps_in.keys()) - set(BUDGET_FIELDS_R6))
            if extra_c:
                viols.append(f"PLAN_UNKNOWN_CAP_KEYS:{','.join(extra_c)}")
    else:
        prop_caps_in = dict(verified_grant.caps)

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

    frozen_caps, cap_pairs, f_viols = freeze_strict_int_caps_a1(plan_caps, require_complete=True)
    if f_viols or frozen_caps is None:
        return {"valid": False, "status": "REJECTED_PLAN_BINDING", "plan": None, "violations": f_viols}

    tpol_dig = str(proposed_budget.get("task_policy_digest") or "")
    _, p_digest, core_viols = canonicalize_r6_bound_plan_core(
        plan_id=str(plan_id),
        stage=verified_grant.stage,
        purpose=verified_grant.purpose,
        grant_identity_key=verified_grant.grant_identity_key,
        grant_payload_digest=verified_grant.grant_payload_digest,
        tested_executable_sha=verified_grant.tested_executable_sha,
        release_manifest_sha256=verified_grant.release_manifest_sha256,
        registry_digest=registry.registry_digest,
        venue=verified_grant.venue,
        api_family=verified_grant.api_family,
        symbol=verified_grant.symbol,
        product_type=verified_grant.product_type,
        destination_store_id=verified_grant.destination_store_id,
        destination_root=verified_grant.destination_root,
        endpoints=prop_eps,
        ws_channels=prop_ws,
        window_start_ms=int(ws),
        window_end_ms=int(we),
        max_session_duration_sec=int(dur),
        max_concurrency=int(conc),
        caps=frozen_caps,
        expires_utc_ms=verified_grant.expires_utc_ms,
        external_capture_permitted=verified_grant.external_capture_permitted,
        task_policy_digest=tpol_dig,
    )
    if core_viols or p_digest is None:
        return {"valid": False, "status": "REJECTED_PLAN_BINDING", "plan": None, "violations": core_viols}

    p_mac = _compute_plan_mac(
        p_digest,
        verified_grant.grant_payload_digest,
        verified_grant.release_manifest_sha256,
        registry.registry_digest,
        verified_grant.external_capture_permitted,
    )
    plan = R6BoundCapturePlan(
        plan_id=str(plan_id),
        stage=verified_grant.stage,
        purpose=verified_grant.purpose,
        grant_identity_key=verified_grant.grant_identity_key,
        grant_payload_digest=verified_grant.grant_payload_digest,
        tested_executable_sha=verified_grant.tested_executable_sha,
        release_manifest_sha256=verified_grant.release_manifest_sha256,
        registry_digest=registry.registry_digest,
        venue=verified_grant.venue,
        api_family=verified_grant.api_family,
        symbol=verified_grant.symbol,
        product_type=verified_grant.product_type,
        destination_store_id=verified_grant.destination_store_id,
        destination_root=verified_grant.destination_root,
        endpoints=prop_eps,
        ws_channels=prop_ws,
        window_start_ms=int(ws),
        window_end_ms=int(we),
        max_session_duration_sec=int(dur),
        max_concurrency=int(conc),
        caps=frozen_caps,
        caps_pairs=cap_pairs,
        expires_utc_ms=verified_grant.expires_utc_ms,
        external_capture_permitted=verified_grant.external_capture_permitted,
        task_policy_digest=tpol_dig,
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
    max_requests: Optional[int] = None,
    strict_reservation_byte_cap: Optional[int] = None,
    qa_loopback_mode: bool = False,
    external_capture_permitted: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """
    F17: Bounded streaming public HTTP/HTTPS GET client:
      - Unconditionally hard-blocks non-loopback external sockets in A1 (`BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED`).
      - Never follows redirects (301/302/303/307/308 -> non-retryable fail-closed).
      - Enforces total monotonic deadline across connect/headers/every read chunk (slow-drip safe!).
      - Enforces CUMULATIVE requests, retries, payload bytes (incl. error bodies & sentinel probe),
        and decoded bytes across ALL retries so `total_raw_bytes <= strict_reservation_byte_cap` ALWAYS!
      - Never reads a `cap + 1` sentinel byte outside reservation.
      - Honors `Retry-After` strictly without shortening; aborts if `retry_after >= remaining_deadline`.
    """
    parsed = urlsplit(str(url))
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port or (443 if scheme == "https" else 80)

    if parsed.username is not None or parsed.password is not None:
        return {"ok": False, "status": "REJECTED_URL_CREDENTIALS_FORBIDDEN", "attempts": 0, "retries_used": 0, "payload_bytes_received": 0, "decoded_bytes": 0, "violations": ["URL_EMBEDDED_CREDENTIALS_FORBIDDEN"]}

    is_loopback = host in ("127.0.0.1", "::1")
    if is_loopback:
        if not qa_loopback_mode:
            return {"ok": False, "status": "BLOCKED_LOOPBACK_WITHOUT_QA_MODE", "attempts": 0, "retries_used": 0, "payload_bytes_received": 0, "decoded_bytes": 0, "violations": ["LOOPBACK_FORBIDDEN_IN_LIVE_MODE"]}
    else:
        if not A1_EXTERNAL_CAPTURE_ENABLED:
            return {"ok": False, "status": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED", "attempts": 0, "retries_used": 0, "payload_bytes_received": 0, "decoded_bytes": 0, "violations": ["A1_EXTERNAL_CAPTURE_DISABLED"]}
        if qa_loopback_mode or not external_capture_permitted:
            return {"ok": False, "status": "BLOCKED_EXTERNAL_SOCKET_FORBIDDEN", "attempts": 0, "retries_used": 0, "payload_bytes_received": 0, "decoded_bytes": 0, "violations": ["EXTERNAL_NETWORK_SOCKET_NOT_AUTHORIZED"]}
        if scheme != "https" or host != APPROVED_REST_HOST_R6 or port != 443:
            return {"ok": False, "status": "BLOCKED_UNALLOWLISTED_HOST_OR_SCHEME", "attempts": 0, "retries_used": 0, "payload_bytes_received": 0, "decoded_bytes": 0, "violations": [f"FORBIDDEN_EXTERNAL_TARGET:{scheme}://{host}:{port}"]}

    if not _is_strict_int(max_payload_bytes) or max_payload_bytes < 1 or not _is_strict_int(max_decoded_bytes) or max_decoded_bytes < 1:
        return {"ok": False, "status": "REJECTED_ZERO_OR_INVALID_BYTE_CAPS", "attempts": 0, "retries_used": 0, "payload_bytes_received": 0, "decoded_bytes": 0, "violations": ["INVALID_BYTE_CAPS"]}
    if not _is_strict_int(max_retries) or max_retries < 0:
        return {"ok": False, "status": "REJECTED_INVALID_RETRY_CAP", "attempts": 0, "retries_used": 0, "payload_bytes_received": 0, "decoded_bytes": 0, "violations": ["INVALID_RETRY_CAP"]}
    eff_max_requests = int(max_requests) if max_requests is not None else (int(max_retries) + 1)
    if not _is_strict_int(eff_max_requests) or eff_max_requests < 1:
        return {"ok": False, "status": "REJECTED_ZERO_REQUEST_CAP", "attempts": 0, "retries_used": 0, "payload_bytes_received": 0, "decoded_bytes": 0, "violations": ["ZERO_REQUEST_CAP_NO_SOCKET"]}
    eff_res_cap = int(strict_reservation_byte_cap) if strict_reservation_byte_cap is not None else int(max_payload_bytes)
    if eff_res_cap < max_payload_bytes:
        max_payload_bytes = eff_res_cap

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
                "retries_used": max(0, attempts - 1),
                "payload_bytes_received": total_raw_bytes,
                "decoded_bytes": total_decoded_bytes,
                "sleeps_honored": sleeps_honored,
                "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                "violations": [last_error or "TOTAL_DEADLINE_EXCEEDED"],
            }
        if attempts >= eff_max_requests:
            return {
                "ok": False,
                "status": "ABORTED_REQUEST_RESERVATION_EXHAUSTED",
                "http_status": last_status_code,
                "attempts": attempts,
                "retries_used": max(0, attempts - 1),
                "payload_bytes_received": total_raw_bytes,
                "decoded_bytes": total_decoded_bytes,
                "sleeps_honored": sleeps_honored,
                "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                "violations": [f"REQUEST_CAP_EXHAUSTED:{attempts}>={eff_max_requests}"],
            }
        if total_raw_bytes >= eff_res_cap or total_decoded_bytes >= max_decoded_bytes:
            return {
                "ok": False,
                "status": "REJECTED_CUMULATIVE_BYTE_RESERVATION_EXHAUSTED",
                "http_status": last_status_code,
                "attempts": attempts,
                "retries_used": max(0, attempts - 1),
                "payload_bytes_received": total_raw_bytes,
                "decoded_bytes": total_decoded_bytes,
                "sleeps_honored": sleeps_honored,
                "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                "violations": ["CUMULATIVE_BYTE_CAP_EXHAUSTED_BEFORE_RETRY"],
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

            hdr_bytes = sum(len(k.encode("latin-1", "replace")) + len(v.encode("latin-1", "replace")) + 4 for k, v in resp.getheaders())
            if hdr_bytes > max_header_bytes:
                return {
                    "ok": False,
                    "status": "REJECTED_OVERSIZED_HEADERS",
                    "http_status": last_status_code,
                    "attempts": attempts,
                    "retries_used": max(0, attempts - 1),
                    "payload_bytes_received": total_raw_bytes,
                    "decoded_bytes": total_decoded_bytes,
                    "violations": [f"HEADER_BYTES_EXCEEDED:{hdr_bytes}>{max_header_bytes}"],
                }

            headers_lower = {k.lower(): v for k, v in resp.getheaders()}

            if last_status_code in (301, 302, 303, 307, 308):
                return {
                    "ok": False,
                    "status": "REJECTED_HTTP_REDIRECT_NONRETRY",
                    "http_status": last_status_code,
                    "attempts": attempts,
                    "retries_used": max(0, attempts - 1),
                    "payload_bytes_received": total_raw_bytes,
                    "decoded_bytes": total_decoded_bytes,
                    "violations": [f"HTTP_REDIRECT_FORBIDDEN:status={last_status_code}:location={headers_lower.get('location', '')}"],
                }

            if last_status_code in (400, 401, 403, 404, 405):
                return {
                    "ok": False,
                    "status": "REJECTED_HTTP_NONRETRYABLE_ERROR",
                    "http_status": last_status_code,
                    "attempts": attempts,
                    "retries_used": max(0, attempts - 1),
                    "payload_bytes_received": total_raw_bytes,
                    "decoded_bytes": total_decoded_bytes,
                    "violations": [f"NONRETRYABLE_HTTP_STATUS:{last_status_code}"],
                }

            rem_payload_budget = min(max_payload_bytes - total_raw_bytes, eff_res_cap - total_raw_bytes)
            cl_hdr = headers_lower.get("content-length")
            if cl_hdr is not None:
                try:
                    cl_int = int(cl_hdr.strip())
                    if cl_int < 0 or cl_int > rem_payload_budget:
                        return {
                            "ok": False,
                            "status": "REJECTED_DECLARED_CONTENT_LENGTH_OVERFLOW",
                            "http_status": last_status_code,
                            "attempts": attempts,
                            "retries_used": max(0, attempts - 1),
                            "payload_bytes_received": total_raw_bytes,
                            "decoded_bytes": total_decoded_bytes,
                            "violations": [f"CONTENT_LENGTH_EXCEEDS_REMAINING_CAP:{cl_int}>{rem_payload_budget}"],
                        }
                except ValueError:
                    return {
                        "ok": False,
                        "status": "REJECTED_INVALID_CONTENT_LENGTH",
                        "http_status": last_status_code,
                        "attempts": attempts,
                        "retries_used": max(0, attempts - 1),
                        "payload_bytes_received": total_raw_bytes,
                        "decoded_bytes": total_decoded_bytes,
                        "violations": [f"INVALID_CONTENT_LENGTH:{cl_hdr}"],
                    }

            # Stream body in bounded chunks with per-chunk remaining deadline and STRICT cumulative
            # reservation cap (F17: defeats slow-drip, chunked bombs, retry amplification, and
            # sentinel-byte over-reading beyond reserved_payload_bytes!).
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
                rem_raw_budget = min(max_payload_bytes - total_raw_bytes, eff_res_cap - total_raw_bytes)
                if rem_raw_budget <= 0:
                    # Exact boundary check without reading any byte past eff_res_cap!
                    if resp.isclosed() or (not getattr(resp, "chunked", False) and getattr(resp, "length", None) == 0):
                        break
                    if getattr(resp, "chunked", False):
                        cleft = getattr(resp, "chunk_left", None)
                        if cleft is not None and int(cleft) > 0:
                            return {
                                "ok": False,
                                "status": "REJECTED_PAYLOAD_BYTES_EXCEEDED",
                                "http_status": last_status_code,
                                "attempts": attempts,
                                "retries_used": max(0, attempts - 1),
                                "payload_bytes_received": total_raw_bytes,
                                "decoded_bytes": total_decoded_bytes,
                                "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                                "violations": [f"STREAM_PAYLOAD_EXCEEDED_CAP:{total_raw_bytes}>={eff_res_cap}:chunk_left={cleft}"],
                            }
                        # Peek chunk terminator without advancing stream over payload bytes
                        peek_b = b""
                        try:
                            if getattr(resp, "fp", None) is not None and hasattr(resp.fp, "peek"):
                                peek_b = bytes(resp.fp.peek(16))
                        except Exception:
                            peek_b = b""
                        if peek_b.startswith(b"\r\n0\r\n") or peek_b.startswith(b"0\r\n"):
                            break
                        return {
                            "ok": False,
                            "status": "REJECTED_PAYLOAD_BYTES_EXCEEDED",
                            "http_status": last_status_code,
                            "attempts": attempts,
                            "retries_used": max(0, attempts - 1),
                            "payload_bytes_received": total_raw_bytes,
                            "decoded_bytes": total_decoded_bytes,
                            "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                            "violations": [f"STREAM_PAYLOAD_EXCEEDED_CAP:{total_raw_bytes}>={eff_res_cap}"],
                        }
                    if getattr(resp, "length", None) is not None and int(resp.length) > 0:
                        return {
                            "ok": False,
                            "status": "REJECTED_PAYLOAD_BYTES_EXCEEDED",
                            "http_status": last_status_code,
                            "attempts": attempts,
                            "retries_used": max(0, attempts - 1),
                            "payload_bytes_received": total_raw_bytes,
                            "decoded_bytes": total_decoded_bytes,
                            "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                            "violations": [f"STREAM_PAYLOAD_EXCEEDED_CAP:{total_raw_bytes}+rem={resp.length}>{eff_res_cap}"],
                        }
                    peek_b = b""
                    try:
                        if getattr(resp, "fp", None) is not None and hasattr(resp.fp, "peek"):
                            peek_b = bytes(resp.fp.peek(1))
                    except Exception:
                        peek_b = b""
                    if not peek_b:
                        break
                    return {
                        "ok": False,
                        "status": "REJECTED_PAYLOAD_BYTES_EXCEEDED",
                        "http_status": last_status_code,
                        "attempts": attempts,
                        "retries_used": max(0, attempts - 1),
                        "payload_bytes_received": total_raw_bytes,
                        "decoded_bytes": total_decoded_bytes,
                        "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                        "violations": [f"STREAM_PAYLOAD_EXCEEDED_CAP:{total_raw_bytes}>={eff_res_cap}"],
                    }

                # Strictly bound read to remaining cumulative cap (never read cap+1 past eff_res_cap!)
                to_read = min(4096, rem_raw_budget)
                chunk = resp.read(to_read)
                if not chunk:
                    break
                raw_len += len(chunk)
                total_raw_bytes += len(chunk)
                if total_raw_bytes > max_payload_bytes or total_raw_bytes > eff_res_cap:
                    return {
                        "ok": False,
                        "status": "REJECTED_PAYLOAD_BYTES_EXCEEDED",
                        "http_status": last_status_code,
                        "attempts": attempts,
                        "retries_used": max(0, attempts - 1),
                        "payload_bytes_received": total_raw_bytes,
                        "decoded_bytes": total_decoded_bytes,
                        "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                        "violations": [f"STREAM_PAYLOAD_EXCEEDED_CAP:{total_raw_bytes}>{min(max_payload_bytes, eff_res_cap)}"],
                    }
                raw_chunks.append(chunk)
                if decomp is not None:
                    rem_dec_budget = max_decoded_bytes - total_decoded_bytes
                    if rem_dec_budget <= 0:
                        probe_d = decomp.decompress(chunk, 1)
                        if probe_d or decomp.unconsumed_tail:
                            return {
                                "ok": False,
                                "status": "REJECTED_DECOMPRESSED_BOMB_OVERFLOW",
                                "http_status": last_status_code,
                                "attempts": attempts,
                                "retries_used": max(0, attempts - 1),
                                "payload_bytes_received": total_raw_bytes,
                                "decoded_bytes": total_decoded_bytes,
                                "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                                "violations": [f"DECOMPRESSED_BYTES_EXCEEDED_CAP:>{max_decoded_bytes}"],
                            }
                        continue
                    dchunk = decomp.decompress(chunk, rem_dec_budget)
                    dec_len += len(dchunk)
                    total_decoded_bytes += len(dchunk)
                    if total_decoded_bytes > max_decoded_bytes or decomp.unconsumed_tail:
                        return {
                            "ok": False,
                            "status": "REJECTED_DECOMPRESSED_BOMB_OVERFLOW",
                            "http_status": last_status_code,
                            "attempts": attempts,
                            "retries_used": max(0, attempts - 1),
                            "payload_bytes_received": total_raw_bytes,
                            "decoded_bytes": total_decoded_bytes,
                            "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                            "violations": [f"DECOMPRESSED_BYTES_EXCEEDED_CAP:>{max_decoded_bytes}"],
                        }
                    dec_chunks.append(dchunk)
                else:
                    dec_len += len(chunk)
                    total_decoded_bytes += len(chunk)
                    if total_decoded_bytes > max_decoded_bytes:
                        return {
                            "ok": False,
                            "status": "REJECTED_DECODED_BYTES_EXCEEDED",
                            "http_status": last_status_code,
                            "attempts": attempts,
                            "retries_used": max(0, attempts - 1),
                            "payload_bytes_received": total_raw_bytes,
                            "decoded_bytes": total_decoded_bytes,
                            "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                            "violations": [f"DECODED_BYTES_EXCEEDED_CAP:{total_decoded_bytes}>{max_decoded_bytes}"],
                        }

            if decomp is not None:
                rem_dec_flush = max(0, max_decoded_bytes - total_decoded_bytes)
                rem_tail = decomp.flush(max(1, rem_dec_flush))
                if len(rem_tail) > rem_dec_flush:
                    return {
                        "ok": False,
                        "status": "REJECTED_DECOMPRESSED_BOMB_OVERFLOW",
                        "http_status": last_status_code,
                        "attempts": attempts,
                        "retries_used": max(0, attempts - 1),
                        "payload_bytes_received": total_raw_bytes,
                        "decoded_bytes": total_decoded_bytes,
                        "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                        "violations": [f"DECOMPRESSED_FLUSH_EXCEEDED_CAP:>{max_decoded_bytes}"],
                    }
                dec_len += len(rem_tail)
                total_decoded_bytes += len(rem_tail)
                dec_chunks.append(rem_tail)

            raw_body = b"".join(raw_chunks)
            decoded_body = b"".join(dec_chunks) if is_gzip else raw_body

            # Transient retryable HTTP statuses: 429, 500, 502, 503, 504
            if last_status_code in (429, 500, 502, 503, 504):
                if attempts > max_retries or attempts >= eff_max_requests:
                    return {
                        "ok": False,
                        "status": "ABORTED_TRANSIENT_RETRIES_EXHAUSTED",
                        "http_status": last_status_code,
                        "attempts": attempts,
                        "retries_used": max(0, attempts - 1),
                        "payload_bytes_received": total_raw_bytes,
                        "decoded_bytes": total_decoded_bytes,
                        "sleeps_honored": sleeps_honored,
                        "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                        "violations": [f"TRANSIENT_HTTP_{last_status_code}_RETRIES_EXHAUSTED"],
                    }
                if total_raw_bytes >= eff_res_cap or total_decoded_bytes >= max_decoded_bytes:
                    return {
                        "ok": False,
                        "status": "REJECTED_CUMULATIVE_BYTE_RESERVATION_EXHAUSTED",
                        "http_status": last_status_code,
                        "attempts": attempts,
                        "retries_used": max(0, attempts - 1),
                        "payload_bytes_received": total_raw_bytes,
                        "decoded_bytes": total_decoded_bytes,
                        "sleeps_honored": sleeps_honored,
                        "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                        "violations": ["CUMULATIVE_BYTE_CAP_EXHAUSTED_AFTER_ERROR_BODY"],
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
                        "retries_used": max(0, attempts - 1),
                        "retry_after_sec": wait_sec,
                        "remaining_deadline_sec": round(rem_after, 4),
                        "payload_bytes_received": total_raw_bytes,
                        "decoded_bytes": total_decoded_bytes,
                        "sleeps_honored": sleeps_honored,
                        "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
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
                    "retries_used": max(0, attempts - 1),
                    "payload_bytes_received": total_raw_bytes,
                    "decoded_bytes": total_decoded_bytes,
                    "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                    "violations": [f"UNEXPECTED_HTTP_STATUS:{last_status_code}"],
                }

            return {
                "ok": True,
                "status": "HTTP_200_BOUNDED_OK",
                "http_status": 200,
                "attempts": attempts,
                "retries_used": max(0, attempts - 1),
                "payload_bytes_received": total_raw_bytes,
                "decoded_bytes": total_decoded_bytes,
                "raw_body_bytes": len(raw_body),
                "decoded_body_bytes": len(decoded_body),
                "raw_body_sha256": hashlib.sha256(raw_body).hexdigest(),
                "decoded_body_sha256": hashlib.sha256(decoded_body).hexdigest(),
                "raw_body_b64": base64.b64encode(raw_body).decode("ascii"),
                "decoded_body_b64": base64.b64encode(decoded_body).decode("ascii"),
                "headers_redacted": {"content-type": headers_lower.get("content-type", ""), "content-encoding": headers_lower.get("content-encoding", "")},
                "sleeps_honored": sleeps_honored,
                "elapsed_sec": round(time.monotonic() - t_start, 4),
                "tls_verified": scheme == "https",
                "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                "wire_overhead_disclosure": "HTTP_PAYLOAD_BYTES_MEASURED_TCP_TLS_WIRE_OVERHEAD_NOT_MEASURED",
                "violations": [],
            }
        except (TimeoutError, socket.timeout) as exc:
            last_error = f"TIMEOUT:{exc}"
            if (deadline - time.monotonic()) <= 0.0 or attempts > max_retries or attempts >= eff_max_requests:
                return {
                    "ok": False,
                    "status": "ABORTED_TOTAL_DEADLINE_EXCEEDED",
                    "http_status": last_status_code,
                    "attempts": attempts,
                    "retries_used": max(0, attempts - 1),
                    "payload_bytes_received": total_raw_bytes,
                    "decoded_bytes": total_decoded_bytes,
                    "sleeps_honored": sleeps_honored,
                    "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                    "violations": [last_error],
                }
        except ssl.SSLError as exc:
            return {
                "ok": False,
                "status": "REJECTED_TLS_VERIFICATION_FAILED_NONRETRY",
                "http_status": None,
                "attempts": attempts,
                "retries_used": max(0, attempts - 1),
                "payload_bytes_received": total_raw_bytes,
                "decoded_bytes": total_decoded_bytes,
                "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
                "violations": [f"TLS_ERROR_NONRETRY:{exc}"],
            }
        except OSError as exc:
            last_error = f"SOCKET_ERROR:{exc}"
            if attempts > max_retries or attempts >= eff_max_requests or (deadline - time.monotonic()) <= 0.05:
                return {
                    "ok": False,
                    "status": "ABORTED_SOCKET_ERROR",
                    "http_status": last_status_code,
                    "attempts": attempts,
                    "retries_used": max(0, attempts - 1),
                    "payload_bytes_received": total_raw_bytes,
                    "decoded_bytes": total_decoded_bytes,
                    "accounting_units_disclosure": "HTTP_APPLICATION_BODY_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
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
# F18: Bounded Public WS Session (Cumulative Connections/Subs/Frames/Bytes/Queue)
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
    max_connections: Optional[int] = None,
    max_subscriptions: Optional[int] = None,
    max_decoded_bytes: Optional[int] = None,
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
            return {"ok": False, "status": "BLOCKED_LOOPBACK_WITHOUT_QA_MODE", "connections_opened": 0, "subscriptions_sent": 0, "reconnects_used": 0, "total_messages": 0, "total_payload_bytes": 0, "decoded_bytes": 0, "violations": ["LOOPBACK_WS_FORBIDDEN_IN_LIVE"]}
    else:
        if not A1_EXTERNAL_CAPTURE_ENABLED:
            return {"ok": False, "status": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED", "connections_opened": 0, "subscriptions_sent": 0, "reconnects_used": 0, "total_messages": 0, "total_payload_bytes": 0, "decoded_bytes": 0, "violations": ["A1_EXTERNAL_CAPTURE_DISABLED"]}
        if qa_loopback_mode or not external_capture_permitted:
            return {"ok": False, "status": "BLOCKED_EXTERNAL_WS_FORBIDDEN", "connections_opened": 0, "subscriptions_sent": 0, "reconnects_used": 0, "total_messages": 0, "total_payload_bytes": 0, "decoded_bytes": 0, "violations": ["EXTERNAL_WS_NOT_AUTHORIZED"]}
        if scheme != "wss" or host != APPROVED_WS_HOST_R6 or port != 443:
            return {"ok": False, "status": "BLOCKED_UNALLOWLISTED_WS_TARGET", "connections_opened": 0, "subscriptions_sent": 0, "reconnects_used": 0, "total_messages": 0, "total_payload_bytes": 0, "decoded_bytes": 0, "violations": [f"FORBIDDEN_WS_TARGET:{ws_url}"]}

    op = str(subscribe_payload.get("op") or "").strip().lower()
    args = subscribe_payload.get("args")
    if op != "subscribe" or not isinstance(args, list) or len(args) != 1 or not isinstance(args[0], Mapping):
        return {"ok": False, "status": "REJECTED_FORBIDDEN_WS_OP_OR_ARGS", "connections_opened": 0, "subscriptions_sent": 0, "reconnects_used": 0, "total_messages": 0, "total_payload_bytes": 0, "decoded_bytes": 0, "violations": [f"INVALID_WS_SUBSCRIBE_OP:{op}"]}
    arg0 = args[0]
    ch = str(arg0.get("channel") or "").strip()
    if ch not in ("books1", "ticker"):
        return {"ok": False, "status": "REJECTED_UNALLOWLISTED_WS_CHANNEL", "connections_opened": 0, "subscriptions_sent": 0, "reconnects_used": 0, "total_messages": 0, "total_payload_bytes": 0, "decoded_bytes": 0, "violations": [f"FORBIDDEN_WS_CHANNEL:{ch}"]}
    if str(arg0.get("instType") or "") != "USDT-FUTURES" or str(arg0.get("instId") or "") != "XAUUSDT":
        return {"ok": False, "status": "REJECTED_WS_INSTRUMENT_MISMATCH", "connections_opened": 0, "subscriptions_sent": 0, "reconnects_used": 0, "total_messages": 0, "total_payload_bytes": 0, "decoded_bytes": 0, "violations": ["WS_INST_TYPE_OR_ID_MISMATCH"]}

    eff_max_conn = int(max_connections) if max_connections is not None else (int(max_reconnects) + 1)
    eff_max_sub = int(max_subscriptions) if max_subscriptions is not None else eff_max_conn
    eff_max_dec = int(max_decoded_bytes) if max_decoded_bytes is not None else int(max_payload_bytes)
    if eff_max_conn < 1 or eff_max_sub < 1 or max_messages < 1 or max_payload_bytes < 1 or eff_max_dec < 1:
        return {"ok": False, "status": "REJECTED_ZERO_WS_RESERVATION", "connections_opened": 0, "subscriptions_sent": 0, "reconnects_used": 0, "total_messages": 0, "total_payload_bytes": 0, "decoded_bytes": 0, "violations": ["ZERO_WS_CAP_FORBIDDEN"]}

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
    raw_frames_accum: List[bytes] = []
    connections_opened = 0
    subscriptions_sent = 0
    reconnects_used = 0
    total_messages = 0
    total_payload_bytes = 0
    total_decoded_bytes = 0
    pongs_received = 0
    abort_reason: Optional[str] = None
    violations: List[str] = []

    epoch_num = 0
    while True:
        rem = deadline - time.monotonic()
        if rem <= 0.05:
            break
        if epoch_num > 0:
            if reconnects_used >= max_reconnects or connections_opened >= eff_max_conn or subscriptions_sent >= eff_max_sub:
                abort_reason = "RECONNECT_OR_CONNECTION_CAP_REACHED"
                break
            reconnects_used += 1
            backoff = min(0.05 * reconnects_used, max(0.01, rem * 0.25))
            await asyncio.sleep(backoff)
        elif connections_opened >= eff_max_conn or subscriptions_sent >= eff_max_sub:
            abort_reason = "WS_CONNECTION_OR_SUBSCRIPTION_CAP_EXHAUSTED"
            break

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
            # compression=None disables permessage-deflate decompression bombs cleanly!
            async with websockets.connect(
                ws_url,
                ssl=ssl_ctx,
                max_size=max_frame_bytes,
                max_queue=max_queue_items,
                open_timeout=min(5.0, rem_open),
                close_timeout=min(1.0, rem_open),
                ping_interval=None,
                compression=None,
            ) as ws:
                connections_opened += 1
                subscriptions_sent += 1
                await ws.send(json.dumps(dict(subscribe_payload), separators=(",", ":")))

                last_ping_sent = time.monotonic()
                awaiting_pong_since: Optional[float] = None

                while True:
                    rem_loop = deadline - time.monotonic()
                    if rem_loop <= 0.02:
                        ep_record["close_reason"] = "DEADLINE_REACHED"
                        break

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

                    # Enforce cumulative message/byte caps strictly BEFORE accepting frame!
                    if total_messages + 1 > max_messages:
                        abort_reason = "WS_MAX_MESSAGES_REACHED"
                        ep_record["close_reason"] = abort_reason
                        break
                    if total_payload_bytes + sz > max_payload_bytes:
                        abort_reason = "WS_MAX_PAYLOAD_BYTES_REACHED"
                        violations.append(f"WS_PAYLOAD_CAP_EXCEEDED:{total_payload_bytes + sz}>{max_payload_bytes}")
                        ep_record["close_reason"] = abort_reason
                        break
                    if total_decoded_bytes + sz > eff_max_dec:
                        abort_reason = "WS_MAX_DECODED_BYTES_REACHED"
                        violations.append(f"WS_DECODED_CAP_EXCEEDED:{total_decoded_bytes + sz}>{eff_max_dec}")
                        ep_record["close_reason"] = abort_reason
                        break

                    total_messages += 1
                    total_payload_bytes += sz
                    total_decoded_bytes += sz
                    raw_frames_accum.append(raw_bytes)

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

                    if not ep_record["acked"]:
                        abort_reason = "WS_DATA_BEFORE_SUBSCRIBE_ACK"
                        violations.append("WS_DATA_RECEIVED_WITHOUT_VALID_ACK")
                        ep_record["close_reason"] = abort_reason
                        break

                    enq = queue_writer.try_enqueue(raw_bytes, {"epoch": epoch_num, "recv_utc_ms": recv_utc_ms})
                    if not enq["enqueued"]:
                        abort_reason = "QUEUE_OVERFLOW_BACKPRESSURE_ABORT"
                        violations.append(f"WS_QUEUE_OVERFLOW:items={enq['queued_items']}:bytes={enq['queued_bytes']}")
                        ep_record["close_reason"] = abort_reason
                        break

                    action = str(parsed_msg.get("action") or "snapshot").lower()
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

        if abort_reason in (
            "WS_FRAME_EXCEEDS_MAX_BYTES",
            "WS_MALFORMED_JSON_FRAME",
            "WS_NON_OBJECT_JSON_FRAME",
            "WS_INVALID_SUBSCRIBE_ACK",
            "WS_DATA_BEFORE_SUBSCRIBE_ACK",
            "QUEUE_OVERFLOW_BACKPRESSURE_ABORT",
            "WS_MAX_MESSAGES_REACHED",
            "WS_MAX_PAYLOAD_BYTES_REACHED",
            "WS_MAX_DECODED_BYTES_REACHED",
        ) or (abort_reason and abort_reason.startswith("WS_EXCHANGE_ERROR")):
            break
        if ep_record.get("close_reason") == "DEADLINE_REACHED":
            break

    raw_concat = b"".join(raw_frames_accum)
    ok = len(violations) == 0 and any(e["acked"] for e in epochs)
    return {
        "ok": ok,
        "status": "WS_SESSION_BOUNDED_OK" if ok else (abort_reason or "WS_SESSION_FAILED"),
        "connections_opened": connections_opened,
        "subscriptions_sent": subscriptions_sent,
        "reconnects_used": reconnects_used,
        "total_messages": total_messages,
        "total_payload_bytes": total_payload_bytes,
        "decoded_bytes": total_decoded_bytes,
        "raw_body_sha256": hashlib.sha256(raw_concat).hexdigest(),
        "decoded_body_sha256": hashlib.sha256(raw_concat).hexdigest(),
        "raw_body_b64": base64.b64encode(raw_concat).decode("ascii"),
        "decoded_body_b64": base64.b64encode(raw_concat).decode("ascii"),
        "pongs_received": pongs_received,
        "queue_overflow_events": queue_writer.overflow_events,
        "epochs": epochs,
        "discontinuities": discontinuities,
        "events": events,
        "elapsed_sec": round(time.monotonic() - t_start, 4),
        "tls_verified": scheme == "wss",
        "accounting_units_disclosure": "WS_APPLICATION_FRAME_PAYLOAD_BYTES_MEASURED_NOT_TCP_TLS_WIRE_BYTES",
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
    max_connections: Optional[int] = None,
    max_subscriptions: Optional[int] = None,
    max_decoded_bytes: Optional[int] = None,
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
            max_connections=max_connections,
            max_subscriptions=max_subscriptions,
            max_decoded_bytes=max_decoded_bytes,
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
    *,
    worker_envelope: Optional[R6WorkerEnvelope] = None,
) -> R6OwnedWorkerHandle:
    """
    F16: Spawns an owned isolated child subprocess inside a Windows Job Object (or POSIX PGID)
    with sanitized environment and zero shell execution. Sends the strict parent-authoritative
    `R6WorkerEnvelope` over stdin ONLY AFTER containment assignment succeeds.
    Guarantees process termination & handle cleanup on ANY spawn/assign/write/identity error!
    """
    if worker_envelope is None or not isinstance(worker_envelope, R6WorkerEnvelope):
        raise ValueError("STRICT_WORKER_ENVELOPE_REQUIRED_FOR_CHILD_LAUNCH")
    env_dict = worker_envelope.to_ipc_dict()
    env_ok, env_viols = validate_r6_a1_worker_envelope_strict(env_dict)
    if not env_ok:
        raise ValueError(f"INVALID_WORKER_ENVELOPE:{','.join(env_viols)}")
    if worker_envelope.lease_id != lease.lease_id or worker_envelope.session_id != lease.session_id:
        raise ValueError("WORKER_ENVELOPE_LEASE_MISMATCH")

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
    proc: Optional[subprocess.Popen[bytes]] = None
    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)

        if os.name == "nt" and job_guard is not None:
            assigned = job_guard.assign(int(proc._handle))  # type: ignore[attr-defined]
            if not assigned:
                raise OSError("FAILED_ASSIGN_CHILD_TO_WINDOWS_JOB_OBJECT")
        elif os.name != "nt":
            containment_id = f"posix_pgid:{os.getpgid(proc.pid)}"

        # Authentic OS process identity proof (NO fabricated time.time() fallback!)
        try:
            ctime = float(psutil.Process(proc.pid).create_time())
        except Exception as exc:
            raise OSError(f"CHILD_PROCESS_CREATE_TIME_UNAVAILABLE:{exc}") from exc

        req_bytes = json.dumps(env_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
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
            attempt_id=worker_envelope.attempt_id,
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
    except Exception:
        if proc is not None:
            try:
                if os.name == "nt" and job_guard is not None:
                    job_guard.terminate(137)
                else:
                    proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=3.0)
            except Exception:
                pass
            for s in (proc.stdin, proc.stdout, proc.stderr):
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass
        if job_guard is not None:
            job_guard.close()
        raise


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
    registry: Optional[R6SourceContractRegistry] = None,
    authorized_request: Optional[R6AuthorizedDispatchRequest] = None,
    run_id: str = "qa_run",
    partition: str = "QA_FIXTURE",
    endpoint_id: str = "qa_loopback",
    contract_digest: str = "",
    max_frame_bytes: Optional[int] = None,
    max_queue_items: int = 64,
    max_queue_bytes: Optional[int] = None,
    heartbeat_interval_sec: float = 20.0,
    pong_timeout_sec: float = 5.0,
    post_exit_hook: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """
    F19: Fail-closed parent-side R6 transport supervisor:
      1. Enforces A1 external hard-block (`BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED`) BEFORE reserve.
      2. Revalidates full bound plan canonical digest + MAC + grant + registry + lease + authorized_request
         + cancel + deadline + resource guard BEFORE dispatch.
      3. Persists strict complete durable reservation (QUEUED) + builds parent-authoritative worker envelope
         (F15, rejecting ANY child request overrides!) + marks attempt STARTED durably BEFORE child spawn.
      4. Launches owned isolated child inside Windows Job Object / POSIX PGID; guarantees reap + pipe close
         + reader thread join in `try/finally` across EVERY path.
      5. Re-checks cancellation/deadline/expiry AFTER child exit and BEFORE acceptance (`G17`).
      6. Strictly parses & validates child IPC (attempt/request echo, strict int counters, raw/decoded SHA-256,
         counter bounds <= reserved), calls durable `settle_r6_io_budget`, and INSPECTS the settlement result
         & receipt BEFORE ANY acceptance!
      7. Mints process-registered `R6TrustedSupervisorResult` (F22) ONLY via internal capability after verified
         OS reap + verified durable settlement!
    """
    lifecycle_trace: List[Dict[str, Any]] = []
    started_mono = time.monotonic()
    now_dt = utc_now_fn()
    started_utc_ms = int(now_dt.astimezone(timezone.utc).timestamp() * 1000)

    def _ev(stage_name: str, **extra: Any) -> None:
        lifecycle_trace.append({
            "event": stage_name,
            "attempt_id": attempt_id,
            "session_id": lease.session_id,
            "utc_ms": int(utc_now_fn().astimezone(timezone.utc).timestamp() * 1000),
            "monotonic_sec": round(time.monotonic(), 6),
            **extra,
        })

    def _fail_early(status: str, viols: List[str], **extra: Any) -> Dict[str, Any]:
        return {
            "ok": False,
            "status": status,
            "accepted_result": None,
            "trusted_result": None,
            "settlement_receipt": None,
            "raw_payload_bytes": None,
            "decoded_payload_bytes": None,
            "process_launched": False,
            "actual_dispatch_started": False,
            "reader_joined": True,
            "late_result_discarded": False,
            "lifecycle_trace": lifecycle_trace,
            "violations": viols,
            **extra,
        }

    # 0. Hard A1 policy: external non-loopback capture is unconditionally disabled BEFORE claim/reserve!
    if not A1_EXTERNAL_CAPTURE_ENABLED and not qa_loopback_mode:
        return _fail_early("BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED", ["A1_EXTERNAL_CAPTURE_DISABLED_AT_SUPERVISOR"])

    # 1. Full plan, grant, registry, lease & authorized_request verification at ACTUAL dispatch
    plan_ok, plan_viols = verify_r6_bound_plan_integrity(
        plan,
        verified_grant,
        registry=registry,
        lease=lease,
        actual_now=now_dt,
    )
    if not plan_ok:
        return _fail_early("REJECTED_PLAN_INTEGRITY", plan_viols)

    if authorized_request is not None:
        ar_ok, ar_viols = verify_authorized_dispatch_request_a1(
            authorized_request,
            expected_lease=lease,
            expected_plan=plan,
            actual_now=now_dt,
        )
        if not ar_ok:
            return _fail_early("REJECTED_DISPATCH_AUTHORIZATION", ar_viols)
        if authorized_request.attempt_id != attempt_id:
            return _fail_early("REJECTED_DISPATCH_ATTEMPT_MISMATCH", ["AUTHORIZED_REQUEST_ATTEMPT_ID_MISMATCH"])
        req_digest = authorized_request.request_digest
        eff_endpoint_id = authorized_request.endpoint_id
        eff_contract_digest = authorized_request.contract_digest
        eff_partition = authorized_request.partition
    else:
        req_digest = _sha256_canonical({
            "attempt_id": attempt_id,
            "lease_id": lease.lease_id,
            "plan_digest": plan.plan_digest,
            "request_type": request_type,
            "request": dict(validated_request),
        })
        eff_endpoint_id = endpoint_id
        eff_contract_digest = contract_digest or plan.registry_digest
        eff_partition = partition

    # Reject any forbidden override keys inside validated_request BEFORE touching ledger!
    bad_req_keys = [str(k) for k in validated_request.keys() if str(k) in FORBIDDEN_REQUEST_OVERRIDE_KEYS_A1]
    if bad_req_keys:
        return _fail_early(
            "REJECTED_CHILD_REQUEST_OVERRIDE",
            [f"CHILD_REQUEST_OVERRIDE_FORBIDDEN:{k}" for k in sorted(bad_req_keys)],
        )

    if not qa_loopback_mode and (not verified_grant.external_capture_permitted or not lease.external_capture_permitted):
        return _fail_early("BLOCKED_OWNER_AUTHORIZATION", ["EXTERNAL_CAPTURE_NOT_PERMITTED"])

    if cancel_event is not None and cancel_event.is_set():
        return _fail_early("ABORTED_CANCELLED_PRE_DISPATCH", ["CANCELLED_BEFORE_DISPATCH"])

    rem_sec = min(session_deadline_monotonic - time.monotonic(), (lease.expires_utc_ms - started_utc_ms) / 1000.0)
    if rem_sec <= 0.05:
        return _fail_early("ABORTED_EXPIRED_AT_ACTUAL_START", ["SESSION_OR_GRANT_EXPIRED_BEFORE_DISPATCH"])

    try:
        res_sample = resource_sample_fn()
    except Exception as exc:
        return _fail_early("ABORTED_RESOURCE_SAMPLE_EXCEPTION", [f"RESOURCE_SAMPLE_EXCEPTION:{exc}"])
    res_check = validate_r6_resource_guard(
        res_sample,
        expected_volumes=expected_volumes,
        phase="runtime",
        allow_fixture_sample=allow_fixture_resource_sample,
    )
    if not res_check["ok"]:
        return _fail_early("ABORTED_RESOURCE_GUARD_PRE_DISPATCH", res_check["violations"])

    # 2. Durable reservation BEFORE subprocess launch (strict complete cap vector!)
    res_rec = ledger.reserve_r6_io_budget_atomic(
        lease,
        attempt_id,
        request_type,
        reserved_caps,
        actual_now=now_dt,
        request_digest=req_digest,
        require_complete_caps=True,
    )
    if not res_rec["reserved"]:
        return _fail_early(res_rec["status"], res_rec["violations"])
    reservation_receipt: R6DurableReservationReceipt = res_rec["reservation_receipt"]
    _ev("BUDGET_RESERVED_DURABLE", reserved_caps=dict(reservation_receipt.reserved_caps), request_digest=req_digest)

    # Re-check cancel/expiry/resource/authorized_request immediately after durable reservation!
    now_dt2 = utc_now_fn()
    rem_sec2 = min(session_deadline_monotonic - time.monotonic(), (lease.expires_utc_ms - int(now_dt2.timestamp() * 1000)) / 1000.0)
    post_res_err: Optional[str] = None
    if cancel_event is not None and cancel_event.is_set():
        post_res_err = "CANCELLED_BEFORE_START"
    elif rem_sec2 <= 0.05:
        post_res_err = "EXPIRED_BEFORE_START"
    elif authorized_request is not None:
        ar2_ok, ar2_v = verify_authorized_dispatch_request_a1(authorized_request, expected_lease=lease, expected_plan=plan, actual_now=now_dt2)
        if not ar2_ok:
            post_res_err = f"POST_RESERVE_AUTH_FAILED:{','.join(ar2_v)}"
    else:
        try:
            rc2 = validate_r6_resource_guard(resource_sample_fn(), expected_volumes=expected_volumes, phase="runtime", allow_fixture_sample=allow_fixture_resource_sample)
            if not rc2["ok"]:
                post_res_err = f"POST_RESERVE_RESOURCE_BREACH:{rc2['violations'][0]}"
        except Exception as exc:
            post_res_err = f"POST_RESERVE_RESOURCE_EXC:{exc}"

    if post_res_err is not None:
        ledger.settle_r6_io_budget(lease, attempt_id, outcome="ABORTED_PRE_START", reason=post_res_err, actual_now=now_dt2, expected_request_digest=req_digest)
        return _fail_early("ABORTED_EXPIRED_AT_ACTUAL_START", [post_res_err])

    # Build strict parent-authoritative worker envelope (F15) BEFORE marking STARTED!
    env_build = build_r6_a1_worker_envelope(
        lease=lease,
        plan=plan,
        reservation_receipt=reservation_receipt,
        request_payload=dict(validated_request),
        request_digest=req_digest,
        remaining_deadline_sec=max(0.05, rem_sec2),
        parent_abs_deadline_monotonic=session_deadline_monotonic,
        qa_loopback_mode=qa_loopback_mode,
        max_frame_bytes=max_frame_bytes,
        max_queue_items=max_queue_items,
        max_queue_bytes=max_queue_bytes,
        heartbeat_interval_sec=heartbeat_interval_sec,
        pong_timeout_sec=pong_timeout_sec,
    )
    if not env_build["valid"] or env_build["envelope"] is None:
        ledger.settle_r6_io_budget(lease, attempt_id, outcome="ABORTED_PRE_START", reason="ENVELOPE_REJECTED", actual_now=now_dt2, expected_request_digest=req_digest)
        return _fail_early(env_build["status"], env_build["violations"])
    worker_envelope: R6WorkerEnvelope = env_build["envelope"]

    st_rec = ledger.mark_r6_attempt_started(lease, attempt_id, actual_now=now_dt2, expected_request_digest=req_digest)
    if not st_rec["started"]:
        ledger.settle_r6_io_budget(lease, attempt_id, outcome="ABORTED_PRE_START", reason="START_FAILED", actual_now=now_dt2, expected_request_digest=req_digest)
        return _fail_early(st_rec["status"], st_rec["violations"])
    _ev("ATTEMPT_STARTED_DURABLE")

    # 3. Launch owned isolated child subprocess with guaranteed try/finally reap + reader join!
    handle: Optional[R6OwnedWorkerHandle] = None
    reader_t: Optional[threading.Thread] = None
    stdout_holder: Dict[str, Any] = {"data": b"", "stderr": b"", "done": False, "overflow": False, "error": None}
    aborted_reason: Optional[str] = None
    reap_info: Optional[Dict[str, Any]] = None
    reader_joined = False

    try:
        handle = start_r6_owned_transport_worker(
            lease,
            validated_request,
            worker_envelope.parent_limits,
            worker_envelope=worker_envelope,
        )
        _ev("CHILD_PROCESS_LAUNCHED", child_pid=handle.pid, child_create_time=handle.create_time, containment_kind=handle.containment_kind)

        max_ipc_read = max(4 * 1024 * 1024, int(worker_envelope.parent_limits["max_decoded_bytes"]) * 4 + 524_288)

        def _drain_pipes() -> None:
            try:
                assert handle is not None
                out_b = handle.proc.stdout.read(max_ipc_read + 1) if handle.proc.stdout else b""
                if len(out_b) > max_ipc_read:
                    stdout_holder["overflow"] = True
                    out_b = out_b[:max_ipc_read]
                err_b = handle.proc.stderr.read(65_536) if handle.proc.stderr else b""
                stdout_holder["data"] = out_b
                stdout_holder["stderr"] = err_b
            except Exception as exc_r:
                stdout_holder["error"] = str(exc_r)
            finally:
                stdout_holder["done"] = True

        reader_t = threading.Thread(target=_drain_pipes, daemon=True)
        reader_t.start()

        while handle.proc.poll() is None:
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
            try:
                rc_rt = validate_r6_resource_guard(
                    resource_sample_fn(),
                    expected_volumes=expected_volumes,
                    phase="runtime",
                    allow_fixture_sample=allow_fixture_resource_sample,
                )
            except Exception as exc_rt:
                aborted_reason = f"RESOURCE_SAMPLE_EXCEPTION:{exc_rt}"
                break
            if not rc_rt["ok"]:
                aborted_reason = f"RESOURCE_FLOOR_BREACH:{rc_rt['violations'][0]}"
                break
            time.sleep(0.02)
    except Exception as exc_spawn:
        if handle is None:
            ledger.settle_r6_io_budget(lease, attempt_id, outcome="UNCERTAIN", reason=f"CHILD_LAUNCH_FAILED:{exc_spawn}", actual_now=utc_now_fn(), expected_request_digest=req_digest)
            return {
                "ok": False,
                "status": "ABORTED_CHILD_LAUNCH_FAILED",
                "accepted_result": None,
                "trusted_result": None,
                "settlement_receipt": None,
                "raw_payload_bytes": None,
                "decoded_payload_bytes": None,
                "process_launched": False,
                "actual_dispatch_started": True,
                "reader_joined": True,
                "late_result_discarded": False,
                "lifecycle_trace": lifecycle_trace,
                "violations": [f"CHILD_LAUNCH_ERROR:{exc_spawn}"],
            }
        aborted_reason = f"SUPERVISOR_LOOP_EXCEPTION:{exc_spawn}"
    finally:
        if handle is not None:
            reap_info = cancel_and_reap_r6_transport(
                handle,
                grace_sec=0.15,
                reap_timeout_sec=3.0,
                simulate_unverifiable_os_proof=simulate_unverifiable_os_proof,
            )
            if reader_t is not None:
                reader_t.join(timeout=2.0)
                reader_joined = not reader_t.is_alive()
            else:
                reader_joined = True
            _ev("CHILD_PROCESS_REAPED", reader_joined=reader_joined, **reap_info)

    assert reap_info is not None
    if not reap_info["reaped_verified"] or not reader_joined:
        ledger.settle_r6_io_budget(lease, attempt_id, outcome="UNCERTAIN", reason="FAIL_UNDRAINED_TRANSPORT", actual_now=utc_now_fn(), expected_request_digest=req_digest)
        ledger.finalize_r6_session(lease, quarantine=True, reason="FAIL_UNDRAINED_TRANSPORT", actual_now=utc_now_fn())
        return {
            "ok": False,
            "status": "FAIL_UNDRAINED_TRANSPORT",
            "accepted_result": None,
            "trusted_result": None,
            "settlement_receipt": None,
            "raw_payload_bytes": None,
            "decoded_payload_bytes": None,
            "process_launched": True,
            "actual_dispatch_started": True,
            "reader_joined": reader_joined,
            "late_result_discarded": bool(stdout_holder["data"]),
            "reap_info": reap_info,
            "lifecycle_trace": lifecycle_trace,
            "violations": ["UNDRAINED_OR_UNVERIFIED_OS_TRANSPORT_QUARANTINED"],
        }

    # Optional test hook strictly after child exit & BEFORE parent acceptance (for deterministic G17/I11 test!)
    if post_exit_hook is not None:
        post_exit_hook()

    # F19 / G17: Re-check cancel/deadline/expiry AFTER child exit and BEFORE acceptance!
    if aborted_reason is None:
        if cancel_event is not None and cancel_event.is_set():
            aborted_reason = "CANCELLED_AFTER_CHILD_EXIT_BEFORE_ACCEPT"
        elif time.monotonic() >= session_deadline_monotonic:
            aborted_reason = "DEADLINE_EXPIRED_AFTER_CHILD_EXIT_BEFORE_ACCEPT"
        elif int(utc_now_fn().timestamp() * 1000) >= lease.expires_utc_ms:
            aborted_reason = "GRANT_EXPIRED_AFTER_CHILD_EXIT_BEFORE_ACCEPT"

    if aborted_reason is not None:
        late_discarded = bool(stdout_holder["data"])
        ledger.settle_r6_io_budget(lease, attempt_id, outcome="ABORTED_MIDFLIGHT", reason=aborted_reason, actual_now=utc_now_fn(), expected_request_digest=req_digest)
        _ev("ATTEMPT_SETTLED_ABORTED_MIDFLIGHT", reason=aborted_reason, late_result_discarded=late_discarded)
        return {
            "ok": False,
            "status": f"ABORTED_{aborted_reason}",
            "accepted_result": None,
            "trusted_result": None,
            "settlement_receipt": None,
            "raw_payload_bytes": None,
            "decoded_payload_bytes": None,
            "process_launched": True,
            "actual_dispatch_started": True,
            "reader_joined": reader_joined,
            "late_result_discarded": late_discarded,
            "reap_info": reap_info,
            "lifecycle_trace": lifecycle_trace,
            "violations": [aborted_reason],
        }

    # Strict bounded IPC decode (duplicate-key & non-finite rejecting!)
    if stdout_holder["overflow"] or stdout_holder["error"]:
        ledger.settle_r6_io_budget(lease, attempt_id, outcome="UNCERTAIN", reason="CHILD_IPC_OVERFLOW_OR_READ_ERR", actual_now=utc_now_fn(), expected_request_digest=req_digest)
        return {
            "ok": False,
            "status": "ABORTED_CHILD_IPC_OVERFLOW",
            "accepted_result": None,
            "trusted_result": None,
            "settlement_receipt": None,
            "raw_payload_bytes": None,
            "decoded_payload_bytes": None,
            "process_launched": True,
            "actual_dispatch_started": True,
            "reader_joined": reader_joined,
            "reap_info": reap_info,
            "lifecycle_trace": lifecycle_trace,
            "violations": ["CHILD_IPC_STDOUT_OVERFLOW_OR_ERROR"],
        }

    try:
        child_res = _strict_loads_r6(stdout_holder["data"], max_bytes=max(4 * 1024 * 1024, int(worker_envelope.parent_limits["max_decoded_bytes"]) * 4 + 524_288))
    except Exception as exc:
        ledger.settle_r6_io_budget(lease, attempt_id, outcome="UNCERTAIN", reason=f"CHILD_IPC_DECODE_ERROR:{exc}", actual_now=utc_now_fn(), expected_request_digest=req_digest)
        return {
            "ok": False,
            "status": "ABORTED_CHILD_IPC_MALFORMED",
            "accepted_result": None,
            "trusted_result": None,
            "settlement_receipt": None,
            "raw_payload_bytes": None,
            "decoded_payload_bytes": None,
            "process_launched": True,
            "actual_dispatch_started": True,
            "reader_joined": reader_joined,
            "reap_info": reap_info,
            "lifecycle_trace": lifecycle_trace,
            "violations": [f"CHILD_IPC_DECODE_ERROR:{exc}"],
        }

    if not isinstance(child_res, Mapping):
        ledger.settle_r6_io_budget(lease, attempt_id, outcome="UNCERTAIN", reason="CHILD_IPC_NOT_OBJECT", actual_now=utc_now_fn(), expected_request_digest=req_digest)
        return {
            "ok": False,
            "status": "ABORTED_CHILD_IPC_MALFORMED",
            "accepted_result": None,
            "trusted_result": None,
            "settlement_receipt": None,
            "raw_payload_bytes": None,
            "decoded_payload_bytes": None,
            "process_launched": True,
            "actual_dispatch_started": True,
            "reader_joined": reader_joined,
            "reap_info": reap_info,
            "lifecycle_trace": lifecycle_trace,
            "violations": ["CHILD_IPC_NOT_OBJECT"],
        }

    # Validate echo bindings, child exit code & strict counter types (NO bool/float/str coercion!)
    ipc_viols: List[str] = []
    if child_res.get("attempt_id") != attempt_id or child_res.get("request_digest") != req_digest:
        ipc_viols.append("CHILD_IPC_ATTEMPT_OR_REQUEST_DIGEST_MISMATCH")
    if reap_info.get("child_returncode") != 0 and child_res.get("ok") is True:
        ipc_viols.append(f"CHILD_NONZERO_EXIT_WITH_OK_TRUE:{reap_info.get('child_returncode')}")

    def _strict_counter(key_name: str, default_zero_if_absent: bool = False) -> Optional[int]:
        if key_name not in child_res:
            return 0 if default_zero_if_absent else None
        val = child_res[key_name]
        if not _is_strict_int(val) or int(val) < 0:
            ipc_viols.append(f"CHILD_INVALID_COUNTER_TYPE:{key_name}:{val!r}")
            return None
        return int(val)

    child_ok = child_res.get("ok") is True
    if request_type == "rest_get":
        c_req = _strict_counter("attempts", default_zero_if_absent=not child_ok)
        c_ret = _strict_counter("retries_used", default_zero_if_absent=True)
        c_pay = _strict_counter("payload_bytes_received", default_zero_if_absent=not child_ok)
        c_dec = _strict_counter("decoded_bytes", default_zero_if_absent=not child_ok)
        c_conn, c_sub, c_msg = 0, 0, 0
    else:
        c_conn = _strict_counter("connections_opened", default_zero_if_absent=not child_ok)
        c_sub = _strict_counter("subscriptions_sent", default_zero_if_absent=not child_ok)
        c_ret = _strict_counter("reconnects_used", default_zero_if_absent=not child_ok)
        c_msg = _strict_counter("total_messages", default_zero_if_absent=not child_ok)
        c_pay = _strict_counter("total_payload_bytes", default_zero_if_absent=not child_ok)
        c_dec = _strict_counter("decoded_bytes", default_zero_if_absent=not child_ok)
        c_req = 0

    if any(x is None for x in (c_req, c_ret, c_pay, c_dec, c_conn, c_sub, c_msg)):
        ipc_viols.append("CHILD_MISSING_REQUIRED_COUNTERS")

    # Decode & verify raw/decoded payload bytes hashes when child reports ok=True
    raw_bytes_out = b""
    dec_bytes_out = b""
    if child_ok and not ipc_viols:
        try:
            raw_b64 = child_res.get("raw_body_b64")
            dec_b64 = child_res.get("decoded_body_b64")
            if not isinstance(raw_b64, str) or not isinstance(dec_b64, str):
                ipc_viols.append("CHILD_MISSING_RAW_OR_DECODED_B64")
            else:
                raw_bytes_out = base64.b64decode(raw_b64.encode("ascii"), validate=True)
                dec_bytes_out = base64.b64decode(dec_b64.encode("ascii"), validate=True)
                if hashlib.sha256(raw_bytes_out).hexdigest() != str(child_res.get("raw_body_sha256") or ""):
                    ipc_viols.append("CHILD_RAW_BODY_SHA256_MISMATCH")
                if hashlib.sha256(dec_bytes_out).hexdigest() != str(child_res.get("decoded_body_sha256") or ""):
                    ipc_viols.append("CHILD_DECODED_BODY_SHA256_MISMATCH")
                if len(raw_bytes_out) > int(c_pay or 0) or len(dec_bytes_out) > int(c_dec or 0):
                    ipc_viols.append("CHILD_REPORTED_BYTES_LESS_THAN_ACTUAL_PAYLOAD")
        except Exception as exc_b64:
            ipc_viols.append(f"CHILD_PAYLOAD_B64_DECODE_ERR:{exc_b64}")

    known_actual = complete_caps_vector_a1({
        "connections": int(c_conn or 0),
        "requests": int(c_req or 0),
        "retries": int(c_ret or 0),
        "subscriptions": int(c_sub or 0),
        "messages": int(c_msg or 0),
        "payload_bytes": int(c_pay or 0),
        "decoded_bytes": int(c_dec or 0),
        "pages": 1 if (child_ok and request_type == "rest_get" and not ipc_viols and int(reservation_receipt.reserved_caps["pages"]) >= 1) else 0,
        "disk_bytes": 0,
        "records": 0,
    })

    child_res_digest = _sha256_canonical(dict(child_res))
    raw_sha_for_settle = hashlib.sha256(raw_bytes_out).hexdigest() if raw_bytes_out else ""
    settle_outcome = "COMPLETED" if (child_ok and not ipc_viols) else "UNCERTAIN"

    # F19: Inspect settlement result & catch settlement exceptions — NEVER ignore settlement return value!
    try:
        settle_res = ledger.settle_r6_io_budget(
            lease,
            attempt_id,
            outcome=settle_outcome,
            actual_known=known_actual,
            reason=str(child_res.get("status") or ("IPC_VIOLATION" if ipc_viols else "")),
            actual_now=utc_now_fn(),
            raw_payload_sha256=raw_sha_for_settle,
            child_result_digest=child_res_digest,
            expected_request_digest=req_digest,
        )
    except Exception as exc_settle:
        _ev("ATTEMPT_SETTLEMENT_EXCEPTION", error=str(exc_settle))
        return {
            "ok": False,
            "status": "ABORTED_SETTLEMENT_EXCEPTION",
            "accepted_result": None,
            "trusted_result": None,
            "settlement_receipt": None,
            "raw_payload_bytes": None,
            "decoded_payload_bytes": None,
            "process_launched": True,
            "actual_dispatch_started": True,
            "reader_joined": reader_joined,
            "late_result_discarded": False,
            "reap_info": reap_info,
            "lifecycle_trace": lifecycle_trace,
            "violations": [f"SETTLEMENT_EXCEPTION:{exc_settle}"],
        }

    _ev("ATTEMPT_SETTLED", settled=bool(settle_res.get("settled")), settle_status=settle_res.get("status"), known_actual=known_actual)

    if not settle_res.get("settled") or settle_res.get("settlement_receipt") is None:
        return {
            "ok": False,
            "status": str(settle_res.get("status") or "REJECTED_SETTLEMENT_FAILED"),
            "accepted_result": None,
            "trusted_result": None,
            "settlement_receipt": None,
            "raw_payload_bytes": None,
            "decoded_payload_bytes": None,
            "process_launched": True,
            "actual_dispatch_started": True,
            "reader_joined": reader_joined,
            "late_result_discarded": False,
            "reap_info": reap_info,
            "lifecycle_trace": lifecycle_trace,
            "violations": list(settle_res.get("violations") or ["SETTLEMENT_FAILED"]),
        }

    s_receipt: R6DurableSettlementReceipt = settle_res["settlement_receipt"]
    ok_sr, sr_viols = verify_durable_settlement_receipt_a1(s_receipt)
    if not ok_sr:
        return {
            "ok": False,
            "status": "REJECTED_UNVERIFIED_SETTLEMENT_RECEIPT",
            "accepted_result": None,
            "trusted_result": None,
            "settlement_receipt": None,
            "raw_payload_bytes": None,
            "decoded_payload_bytes": None,
            "process_launched": True,
            "actual_dispatch_started": True,
            "reader_joined": reader_joined,
            "late_result_discarded": False,
            "reap_info": reap_info,
            "lifecycle_trace": lifecycle_trace,
            "violations": sr_viols,
        }

    if ipc_viols or not child_ok:
        return {
            "ok": False,
            "status": "ABORTED_CHILD_IPC_VALIDATION_FAILED" if ipc_viols else str(child_res.get("status") or "CHILD_FAILED"),
            "accepted_result": None,
            "trusted_result": None,
            "settlement_receipt": s_receipt,
            "child_raw_outcome": child_res,
            "raw_payload_bytes": None,
            "decoded_payload_bytes": None,
            "process_launched": True,
            "actual_dispatch_started": True,
            "reader_joined": reader_joined,
            "late_result_discarded": False,
            "reap_info": reap_info,
            "lifecycle_trace": lifecycle_trace,
            "violations": ipc_viols + list(child_res.get("violations") or ()),
        }

    # F22: Mint internal capability-gated trusted supervisor result ONLY after verified OS reap + verified settlement!
    fin_utc_ms = int(utc_now_fn().astimezone(timezone.utc).timestamp() * 1000)
    trusted_res = _issue_r6_a1_trusted_supervisor_result(
        capability=_A1_INTERNAL_CAPABILITY,
        tested_executable_sha=verified_grant.tested_executable_sha,
        run_id=run_id,
        session_id=lease.session_id,
        lease_id=lease.lease_id,
        attempt_id=attempt_id,
        plan_digest=plan.plan_digest,
        partition=eff_partition,
        endpoint_id=eff_endpoint_id,
        contract_digest=eff_contract_digest,
        request_digest=req_digest,
        started_utc_ms=started_utc_ms,
        finished_utc_ms=fin_utc_ms,
        started_monotonic_sec=started_mono,
        finished_monotonic_sec=time.monotonic(),
        child_pid=int(reap_info["child_pid"]),
        child_create_time=float(reap_info["child_create_time"]),
        child_exit_code=int(reap_info["child_returncode"]),
        reaped_verified=True,
        containment_kind=str(reap_info["containment_kind"]),
        transport_kind="REST_GET" if request_type == "rest_get" else "WS_PUBLIC",
        qa_loopback_mode=qa_loopback_mode,
        tls_verified=bool(child_res.get("tls_verified", False)),
        raw_payload_bytes=raw_bytes_out,
        decoded_payload_bytes=dec_bytes_out,
        known_counters=known_actual,
        settlement_receipt=s_receipt,
        child_result_digest=child_res_digest,
    )

    return {
        "ok": True,
        "status": str(child_res.get("status") or "UNKNOWN"),
        "accepted_result": child_res,
        "trusted_result": trusted_res,
        "settlement_receipt": s_receipt,
        "raw_payload_bytes": raw_bytes_out,
        "decoded_payload_bytes": dec_bytes_out,
        "child_raw_outcome": child_res,
        "process_launched": True,
        "actual_dispatch_started": True,
        "reader_joined": reader_joined,
        "late_result_discarded": False,
        "reap_info": reap_info,
        "lifecycle_trace": lifecycle_trace,
        "violations": [],
    }


# =====================================================================
# F15 / F16: Isolated Child Worker Entrypoint (`--child-worker`)
# =====================================================================
def _run_child_worker_cli() -> int:
    raw_in = sys.stdin.buffer.read(1_048_576 + 1)
    if len(raw_in) > 1_048_576:
        sys.stdout.write(json.dumps({"ok": False, "status": "CHILD_STDIN_OVERFLOW", "violations": ["CHILD_STDIN_EXCEEDED_1MIB"]}))
        sys.stdout.flush()
        return 2
    try:
        msg = _strict_loads_r6(raw_in, max_bytes=1_048_576)
    except Exception as exc:
        sys.stdout.write(json.dumps({"ok": False, "status": "CHILD_BAD_INPUT", "violations": [str(exc)]}))
        sys.stdout.flush()
        return 2

    # F15 / F16: Validate strict parent-authoritative envelope BEFORE ANY socket!
    # Rejects child request overrides, missing parent limits, default retries, and non-loopback in A1!
    env_ok, env_viols = validate_r6_a1_worker_envelope_strict(msg)
    if not env_ok:
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "status": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED" if "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED" in env_viols else "REJECTED_CHILD_ENVELOPE_INVALID",
                    "attempt_id": msg.get("attempt_id") if isinstance(msg, Mapping) else "",
                    "request_digest": msg.get("request_digest") if isinstance(msg, Mapping) else "",
                    "attempts": 0,
                    "retries_used": 0,
                    "connections_opened": 0,
                    "subscriptions_sent": 0,
                    "reconnects_used": 0,
                    "total_messages": 0,
                    "payload_bytes_received": 0,
                    "total_payload_bytes": 0,
                    "decoded_bytes": 0,
                    "violations": env_viols,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        sys.stdout.flush()
        return 2

    req = msg["request"]
    plims = msg["parent_limits"]
    qa_loop = bool(msg["qa_loopback_mode"])
    ext_perm = bool(msg["external_capture_permitted"]) and A1_EXTERNAL_CAPTURE_ENABLED
    kind = str(req["kind"])

    # Parent-authoritative remaining deadline only (strictly <= parent_limits["remaining_deadline_sec"]!)
    rem_dl = float(plims["remaining_deadline_sec"])

    if kind == "rest_get":
        out = execute_r6_bounded_rest_get(
            str(req["url"]),
            dict(req.get("params") or {}),
            remaining_deadline_sec=rem_dl,
            max_payload_bytes=int(plims["max_payload_bytes"]),
            max_decoded_bytes=int(plims["max_decoded_bytes"]),
            max_header_bytes=int(plims["max_header_bytes"]),
            max_retries=int(plims["max_retries"]),
            max_requests=int(plims["max_requests"]),
            strict_reservation_byte_cap=int(plims["max_payload_bytes"]),
            qa_loopback_mode=qa_loop,
            external_capture_permitted=ext_perm,
        )
    elif kind == "ws_session":
        out = execute_r6_bounded_ws_session(
            str(req["ws_url"]),
            dict(req.get("subscribe_payload") or {}),
            remaining_deadline_sec=rem_dl,
            max_frame_bytes=int(plims["max_frame_bytes"]),
            max_messages=int(plims["max_messages"]),
            max_payload_bytes=int(plims["max_payload_bytes"]),
            max_decoded_bytes=int(plims["max_decoded_bytes"]),
            max_queue_items=int(plims["max_queue_items"]),
            max_queue_bytes=int(plims["max_queue_bytes"]),
            max_reconnects=int(plims["max_reconnects"]),
            max_connections=int(plims["max_connections"]),
            max_subscriptions=int(plims["max_subscriptions"]),
            heartbeat_interval_sec=float(plims["heartbeat_interval_sec"]),
            pong_timeout_sec=float(plims["pong_timeout_sec"]),
            qa_loopback_mode=qa_loop,
            external_capture_permitted=ext_perm,
        )
    elif kind == "qa_stall_noncooperative" and qa_loop:
        try:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        except Exception:
            pass
        if req.get("spawn_grandchild"):
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        stall_sec = float(req.get("stall_sec", 5.0))
        time.sleep(stall_sec)
        raw_stub = b'{"qa_stall":"late"}'
        out = {
            "ok": True,
            "status": "QA_STALL_LATE_COMPLETE",
            "attempts": 1,
            "retries_used": 0,
            "payload_bytes_received": len(raw_stub),
            "decoded_bytes": len(raw_stub),
            "raw_body_sha256": hashlib.sha256(raw_stub).hexdigest(),
            "decoded_body_sha256": hashlib.sha256(raw_stub).hexdigest(),
            "raw_body_b64": base64.b64encode(raw_stub).decode("ascii"),
            "decoded_body_b64": base64.b64encode(raw_stub).decode("ascii"),
            "tls_verified": False,
            "violations": [],
        }
    else:
        out = {"ok": False, "status": "REJECTED_UNKNOWN_CHILD_KIND", "violations": [f"UNKNOWN_KIND:{kind}"]}

    out["attempt_id"] = msg["attempt_id"]
    out["request_digest"] = msg["request_digest"]
    sys.stdout.write(json.dumps(out, sort_keys=True, separators=(",", ":")))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child-worker", action="store_true")
    ns = parser.parse_args()
    if ns.child_worker:
        raise SystemExit(_run_child_worker_cli())
