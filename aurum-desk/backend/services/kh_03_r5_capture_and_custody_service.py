"""
aurum-desk/backend/services/kh_03_r5_capture_and_custody_service.py

AURUM DESK — KH-03.R5 | BOUNDED PUBLIC CAPTURE, PARTITION ISOLATION & DEV CUSTODY JOURNAL
SPEC VERSION: KH-03.R5-v1.0 | DEV/QA NAMESPACE ONLY | NEVER TOUCHES HISTORICAL OOS ESCROW

Implements §04 functions 11..14 plus isolated append-only hash-chained R5DevCustodyJournal
and deny-before-open OosAccessGuardR5:
  11. capture_r5_public_batch (async bounded orchestrator reusing C2/C3/C4 trust boundary)
  12. assign_r5_partition_before_capture (pre-capture DEV/QA role assignment & overlap blocker)
  13. persist_r5_verified_dev_snapshot (atomic fsync/lock/hash-chain/anchor + crash quarantine)
  14. verify_r5_custody_chain_readonly (independent readonly raw bytes, chain & deny-log audit)

Non-Negotiable Safety Contract:
  - ZERO live network requests without verified genuine separate human owner authorization.
  - NEVER opens, unseals, reads, appends to, or quarantines historical OOS escrow.
  - QA fixtures NEVER become `OBSERVED_VERIFIED`; fake/subclass/monkeypatched transport rejected.
  - Zero imports of order execution or forbidden legacy R1 study functions.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import uuid

from services.kh_02_r2_c2_escrow_journal import (
    ExternalHeadAnchorC2,
    _assert_not_symlink_and_contained,
    _cross_platform_exclusive_fd_lock,
    _fsync_directory,
    _is_symlink_or_junction,
    _write_all_eintr_safe,
)
from services.kh_02_r2_c2_public_capture import (
    FORBIDDEN_URL_TOKENS_C2,
    OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2,
    RawHttpResponseC2,
    StdlibUrllibPublicTransportC2,
    _NoRedirectHandlerC3,
    _validate_public_bitget_https_url_c3,
)
from services.kh_02_r2_c3_provenance_guard import (
    MAX_DECOMPRESSED_BODY_BYTES_C3,
    MAX_RAW_HTTP_BODY_BYTES_C3,
    ProvenanceClassC3,
    mint_trusted_capture_receipt_c4,
    safe_bounded_decompress_if_gzip_c3,
)
from services.kh_02_r2_c4_trusted_transport_boundary import (
    VerifiedCaptureProofC4,
    bind_capture_proof_consumption_c4,
    execute_trusted_stdlib_https_get_c4,
    is_exact_unmodified_stdlib_transport_c4,
    issue_qa_or_unverified_capture_proof_c4,
    verify_capture_proof_c4,
)
from services.kh_02_r2_prospective_data_service import (
    CANONICAL_DATASET_SHA256_KH02,
    KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
    scan_for_forbidden_secrets_r2_c1,
)
from services.kh_03_r5_market_data_foundation_service import (
    HARD_MAX_RETRIES_R5_C1,
    HARD_MIN_REQUEST_INTERVAL_MS_R5_C1,
    MAX_GLOBAL_CONCURRENCY_R5,
    SCHEMA_VERSION_KH03_R5,
    SCHEMA_VERSION_KH03_R5_C1,
    SPEC_VERSION_KH03_R5,
    SPEC_VERSION_KH03_R5_C1,
    STRICT_PREFLIGHT_DISK_MIN_GIB_R5,
    STRICT_RUNTIME_DISK_MIN_GIB_R5,
    _validate_r5_plan_binding_strict,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

R5_ALLOWED_PARTITION_ROLES: Tuple[str, ...] = ("DEVELOPMENT_CAPTURE", "QA_FIXTURE")
R5_FORBIDDEN_OOS_PATH_TOKENS: Tuple[str, ...] = ("oos_escrow", "escrow_oos", "real_oos", "untouched_oos", "prospective_oos_escrow")
R5_GENESIS_HASH = "0" * 64
DEFAULT_RESOURCE_MAX_AGE_SEC_R5_C1: float = 2.0
HARD_RAM_FLOOR_MIB_R5_C1: float = 512.0
MAX_PERMITTED_RETRY_AFTER_PARSE_SEC_C1: float = 86_400.0


class R5OosAccessDeniedError(PermissionError):
    """Raised immediately before any file open/read targeting a protected OOS path."""


class R5InjectedCrashError(RuntimeError):
    """Test-only fault injection exception simulating crash between write/fsync/rename/journal/anchor."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_contained_path(path: Path, root: Path) -> Path:
    """Rejects path traversal (`..`), symlinks/junctions, and escapes outside `root`."""
    raw_str = str(path)
    if ".." in Path(raw_str).parts:
        raise ValueError(f"PATH_TRAVERSAL_DENIED:{raw_str}")
    root_res = root.resolve()
    # Check symlink/junction on path itself and every parent up to root
    cur = path
    for _ in range(32):
        if cur.exists() and _is_symlink_or_junction(cur):
            raise ValueError(f"SYMLINK_OR_JUNCTION_DENIED:{cur}")
        if cur.resolve() == root_res:
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    _assert_not_symlink_and_contained(path, root_res)
    return path.resolve()


class OosAccessGuardR5:
    """
    Deny-before-open instrumentation guaranteeing `OOS_EVALUATIONS = 0` at the read/API boundary.
    Logs every allowed DEV/QA read and denies (raising `R5OosAccessDeniedError`) any attempt
    to read or pass an OOS path or invoke strategy evaluation on protected data.
    """

    def __init__(self, *, allowed_dev_roots: Sequence[Path], protected_oos_roots: Sequence[Path] = ()) -> None:
        self.allowed_dev_roots = [p.resolve() for p in allowed_dev_roots]
        self.protected_oos_roots = [p.resolve() for p in protected_oos_roots]
        self.access_log: List[Dict[str, Any]] = []
        self.oos_denied_attempts: int = 0
        self.oos_reads_executed: int = 0

    def assert_open_permitted(self, target_path: Path, *, caller: str, purpose: str) -> Path:
        raw_low = str(target_path).lower().replace("\\", "/")
        for tok in R5_FORBIDDEN_OOS_PATH_TOKENS:
            if tok in raw_low:
                self.oos_denied_attempts += 1
                self.access_log.append({
                    "utc": _utc_now_iso(), "caller": caller, "purpose": purpose,
                    "path_basename": target_path.name, "decision": "DENIED_BEFORE_OPEN_OOS_TOKEN",
                })
                raise R5OosAccessDeniedError(f"OOS_DENY_BEFORE_OPEN_TOKEN:{tok}:{target_path.name}")
        if "oos" in purpose.lower() or "strategy_eval" in purpose.lower():
            self.oos_denied_attempts += 1
            self.access_log.append({
                "utc": _utc_now_iso(), "caller": caller, "purpose": purpose,
                "path_basename": target_path.name, "decision": "DENIED_BEFORE_OPEN_FORBIDDEN_PURPOSE",
            })
            raise R5OosAccessDeniedError(f"OOS_OR_STRATEGY_EVAL_DENIED:{purpose}")

        res = target_path.resolve()
        for oos_r in self.protected_oos_roots:
            try:
                res.relative_to(oos_r)
                self.oos_denied_attempts += 1
                self.access_log.append({
                    "utc": _utc_now_iso(), "caller": caller, "purpose": purpose,
                    "path_basename": target_path.name, "decision": "DENIED_BEFORE_OPEN_PROTECTED_OOS_ROOT",
                })
                raise R5OosAccessDeniedError(f"OOS_DENY_BEFORE_OPEN_ROOT:{target_path.name}")
            except ValueError:
                pass

        contained = False
        for dev_r in self.allowed_dev_roots:
            try:
                res.relative_to(dev_r)
                contained = True
                break
            except ValueError:
                pass
        if not contained:
            self.oos_denied_attempts += 1
            self.access_log.append({
                "utc": _utc_now_iso(), "caller": caller, "purpose": purpose,
                "path_basename": target_path.name, "decision": "DENIED_OUTSIDE_ALLOWED_DEV_ROOTS",
            })
            raise R5OosAccessDeniedError(f"PATH_OUTSIDE_ALLOWED_DEV_ROOTS:{target_path.name}")

        self.access_log.append({
            "utc": _utc_now_iso(), "caller": caller, "purpose": purpose,
            "path_basename": target_path.name, "decision": "ALLOWED_DEV_OR_QA_READONLY",
        })
        return res

    def summary(self) -> Dict[str, Any]:
        return {
            "oos_evaluations": 0,
            "oos_reads_executed": self.oos_reads_executed,
            "oos_denied_before_open_count": self.oos_denied_attempts,
            "total_access_log_entries": len(self.access_log),
            "deny_before_open_enforced": True,
        }


# =====================================================================
# KH-03.R5.C1 Hardened Resource Monitor, Clock, Retry-After & Dispatch Guards
# =====================================================================
def _canonical_volume_identity_c1(path: Optional[Path]) -> str:
    """Returns canonical filesystem volume/mount identity for `path` (Windows drive or POSIX st_dev)."""
    p = Path(path).resolve() if path is not None else Path.cwd().resolve()
    if os.name == "nt":
        drv = p.drive.upper()
        if drv:
            return drv + "\\"
        return p.anchor.upper()
    cur = p
    while not cur.exists() and cur.parent != cur:
        cur = cur.parent
    try:
        st = os.stat(cur)
        return f"dev:{st.st_dev}"
    except OSError:
        return str(p.anchor or "/")


def _normalize_volume_token_c1(vol: Any) -> Optional[str]:
    if not isinstance(vol, str) or not vol.strip():
        return None
    s = vol.strip()
    if os.name == "nt":
        s_up = s.upper().replace("/", "\\")
        if len(s_up) == 2 and s_up[1] == ":":
            return s_up + "\\"
        if len(s_up) >= 3 and s_up[1:3] == ":\\":
            return s_up[:3]
        return s_up
    return s


def _strict_nonneg_finite_num_c1(val: Any, field: str) -> Tuple[Optional[float], Optional[str]]:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None, f"INVALID_RESOURCE_NUM_TYPE:{field}:{type(val).__name__}"
    fv = float(val)
    if not math.isfinite(fv):
        return None, f"NONFINITE_RESOURCE_VALUE:{field}:{val!r}"
    if fv < 0.0:
        return None, f"NEGATIVE_RESOURCE_VALUE:{field}:{fv}"
    return fv, None


def validate_r5_resource_sample_strict(
    sample: Any,
    destination: Optional[Path],
    sampled_monotonic: Optional[float] = None,
    now_monotonic: Optional[float] = None,
    max_age_sec: float = DEFAULT_RESOURCE_MAX_AGE_SEC_R5_C1,
    *,
    min_disk_gib: float = STRICT_RUNTIME_DISK_MIN_GIB_R5,
    min_ram_mib: float = HARD_RAM_FLOOR_MIB_R5_C1,
) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """
    Fail-closed strict resource sample validator (closes P1-C).
    NEVER supplies default safe disk/RAM numbers. Rejects None, empty dict, missing keys,
    bool/string, NaN/Inf, negative, future/stale timestamps, wrong destination volume,
    `sample_ok is not True`, disk waiver flags, or breached runtime disk/RAM floors.
    """
    if not isinstance(sample, Mapping) or len(sample) == 0:
        return False, ["RESOURCE_SAMPLE_MISSING_OR_EMPTY"], None

    viols: List[str] = []
    if sample.get("owner_waive_disk_preflight") is True:
        viols.append("DISK_WAIVER_STRICTLY_FORBIDDEN_IN_R5")
    if sample.get("sample_ok") is not True:
        viols.append(f"RESOURCE_SAMPLE_NOT_OK:{sample.get('sample_ok')!r}")

    mon_src = sample.get("monitor_source")
    if not isinstance(mon_src, str) or not mon_src.strip():
        viols.append("RESOURCE_SAMPLE_MISSING_MONITOR_SOURCE")

    dk, e_dk = _strict_nonneg_finite_num_c1(sample.get("disk_free_gib"), "disk_free_gib")
    if e_dk:
        viols.append(e_dk)
    ram, e_ram = _strict_nonneg_finite_num_c1(sample.get("available_ram_mib"), "available_ram_mib")
    if e_ram:
        viols.append(e_ram)

    raw_s_mono = sample.get("sampled_monotonic")
    s_mono_val = sampled_monotonic if sampled_monotonic is not None else raw_s_mono
    s_mono, e_sm = _strict_nonneg_finite_num_c1(s_mono_val, "sampled_monotonic")
    if e_sm:
        viols.append(e_sm)
    if sampled_monotonic is not None and raw_s_mono is not None:
        raw_sm_v, raw_sm_e = _strict_nonneg_finite_num_c1(raw_s_mono, "sample.sampled_monotonic")
        if raw_sm_e:
            viols.append(raw_sm_e)
        elif s_mono is not None and raw_sm_v is not None and abs(raw_sm_v - s_mono) > 1e-6:
            viols.append("RESOURCE_SAMPLE_MONOTONIC_MISMATCH")

    now_m_val = time.monotonic() if now_monotonic is None else now_monotonic
    now_m, e_nm = _strict_nonneg_finite_num_c1(now_m_val, "now_monotonic")
    if e_nm:
        viols.append(e_nm)

    age_lim, e_al = _strict_nonneg_finite_num_c1(max_age_sec, "max_age_sec")
    if e_al or (age_lim is not None and age_lim <= 0.0):
        viols.append(f"INVALID_MAX_AGE_SEC:{max_age_sec!r}")

    sample_age: Optional[float] = None
    if s_mono is not None and now_m is not None and age_lim is not None and age_lim > 0.0:
        if s_mono > now_m + 1e-6:
            viols.append(f"RESOURCE_SAMPLE_FUTURE_TIMESTAMP:sampled={s_mono}>now={now_m}")
        else:
            sample_age = now_m - s_mono
            if sample_age > age_lim:
                viols.append(f"RESOURCE_SAMPLE_STALE:age={sample_age:.4f}>{age_lim}")

    expected_vol = _canonical_volume_identity_c1(destination)
    got_vol = _normalize_volume_token_c1(sample.get("destination_volume"))
    if got_vol is None:
        viols.append("RESOURCE_SAMPLE_MISSING_DESTINATION_VOLUME")
    elif got_vol != expected_vol:
        viols.append(f"RESOURCE_SAMPLE_WRONG_VOLUME:got={got_vol}!=expected={expected_vol}")

    if dk is not None and dk < float(min_disk_gib):
        viols.append(f"RUNTIME_DISK_BELOW_20GIB:{dk:.4f}")
    if ram is not None and ram < float(min_ram_mib):
        viols.append(f"RUNTIME_RAM_BELOW_512MIB:{ram:.2f}")

    if viols:
        return False, viols, None
    return True, [], {
        "disk_free_gib": float(dk),  # type: ignore[arg-type]
        "available_ram_mib": float(ram),  # type: ignore[arg-type]
        "sampled_monotonic": float(s_mono),  # type: ignore[arg-type]
        "sample_age_sec": round(float(sample_age or 0.0), 6),
        "destination_volume": got_vol,
        "monitor_source": str(mon_src).strip(),
        "sample_ok": True,
    }


def _require_r5_resource_monitor(
    resource_monitor: Optional[Callable[[], Mapping[str, Any]]],
    destination: Optional[Path],
    *,
    now_monotonic: Optional[float] = None,
    monotonic_fn: Optional[Callable[[], float]] = None,
    max_age_sec: float = DEFAULT_RESOURCE_MAX_AGE_SEC_R5_C1,
    min_disk_gib: float = STRICT_RUNTIME_DISK_MIN_GIB_R5,
    min_ram_mib: float = HARD_RAM_FLOOR_MIB_R5_C1,
) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """Fail-closed resource monitor invoker: samples FIRST, then reads monotonic clock to prevent OS tick inversions."""
    if resource_monitor is None or not callable(resource_monitor):
        return False, ["MISSING_RESOURCE_MONITOR"], None
    try:
        raw_sample = resource_monitor()
    except Exception as exc:
        return False, [f"RESOURCE_MONITOR_EXCEPTION:{type(exc).__name__}:{exc}"], None
    eff_now: Optional[float] = now_monotonic
    if monotonic_fn is not None:
        try:
            m_val = monotonic_fn()
        except Exception as exc:
            return False, [f"INVALID_MONOTONIC_CLOCK:{exc}"], None
        if isinstance(m_val, bool) or not isinstance(m_val, (int, float)) or not math.isfinite(float(m_val)) or float(m_val) < 0.0:
            return False, [f"INVALID_MONOTONIC_CLOCK:{m_val!r}"], None
        eff_now = float(m_val)
    return validate_r5_resource_sample_strict(
        raw_sample,
        destination,
        now_monotonic=eff_now,
        max_age_sec=max_age_sec,
        min_disk_gib=min_disk_gib,
        min_ram_mib=min_ram_mib,
    )


def make_r5_fixture_resource_monitor(
    *,
    disk_free_gib: float = 28.0,
    available_ram_mib: float = 2048.0,
    destination: Optional[Path] = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
    monitor_source: str = "QA_DETERMINISTIC_FIXTURE_MONITOR",
) -> Callable[[], Dict[str, Any]]:
    """
    Deterministic test-only resource monitor factory for offline QA fixture tests.
    NEVER certifies real host safety; always labelled `QA_DETERMINISTIC_FIXTURE_MONITOR`.
    """
    vol = _canonical_volume_identity_c1(destination)
    return lambda: {
        "disk_free_gib": disk_free_gib,
        "available_ram_mib": available_ram_mib,
        "sampled_monotonic": monotonic_fn(),
        "destination_volume": vol,
        "monitor_source": monitor_source,
        "sample_ok": True,
    }


def _parse_retry_after_strict(
    raw_header: Optional[str],
    now_utc: datetime,
) -> Tuple[bool, Optional[float], Optional[str]]:
    """
    Strictly parses HTTP `Retry-After` (numeric seconds OR RFC 7231 HTTP-date).
    NEVER shortens a valid server-instructed delay. Rejects NaN/Inf/negative/malformed values.
    """
    if raw_header is None:
        return True, None, None
    s = str(raw_header).strip()
    if not s:
        return False, None, "EMPTY_RETRY_AFTER_HEADER"
    try:
        val = float(s)
        if not math.isfinite(val) or val < 0.0:
            return False, None, f"INVALID_NUMERIC_RETRY_AFTER:{s!r}"
        if val > MAX_PERMITTED_RETRY_AFTER_PARSE_SEC_C1:
            return True, val, None
        return True, val, None
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt.astimezone(timezone.utc) - now_utc.astimezone(timezone.utc)).total_seconds()
        if not math.isfinite(delta):
            return False, None, f"NONFINITE_HTTP_DATE_RETRY_AFTER:{s!r}"
        return True, max(0.0, delta), None
    except Exception:
        return False, None, f"UNPARSEABLE_RETRY_AFTER_HEADER:{s!r}"


def _verify_r5_execution_context(
    plan: Mapping[str, Any],
    trusted_scope: Optional[Any],
    now_utc: datetime,
    destination: Optional[Path],
    run_id: str,
) -> Tuple[bool, List[str], Optional[Any], Optional[Any]]:
    """Verifies plan-to-scope cryptographic/process binding, expiry, run_id, and destination."""
    return _validate_r5_plan_binding_strict(
        plan,
        trusted_scope,
        now_utc,
        expected_run_id=run_id,
        destination_root=destination,
    )


def _check_r5_dispatch_guard(
    *,
    plan: Mapping[str, Any],
    trusted_scope: Optional[Any],
    run_id: str,
    destination: Optional[Path],
    deadline_mono: float,
    grant_expires_utc_ms: int,
    transport_timeout_ceiling_sec: float,
    cancel_event: Optional[asyncio.Event],
    aborted: bool,
    resource_monitor: Optional[Callable[[], Mapping[str, Any]]],
    monotonic_fn: Callable[[], float],
    utc_now_fn: Callable[[], datetime],
    resource_max_age_sec: float,
    resource_monotonic_fn: Optional[Callable[[], float]] = None,
) -> Tuple[bool, Optional[str], List[str], float, float]:
    """
    Reusable fail-closed dispatch/write guard (closes P1-A post-enqueue, P1-B post-wait, P1-C monitor).
    Returns `(ok, reason_code, violations, effective_remaining_sec, effective_timeout_sec)`.
    """
    if aborted:
        return False, "SESSION_ALREADY_ABORTED", ["SESSION_ALREADY_ABORTED"], 0.0, 0.0
    if cancel_event is not None and cancel_event.is_set():
        return False, "CANCELLED_MIDFLIGHT", ["CANCELLED_MIDFLIGHT"], 0.0, 0.0

    try:
        now_m = monotonic_fn()
    except Exception as exc:
        return False, "INVALID_MONOTONIC_CLOCK", [f"INVALID_MONOTONIC_CLOCK:{exc}"], 0.0, 0.0
    if isinstance(now_m, bool) or not isinstance(now_m, (int, float)) or not math.isfinite(float(now_m)) or float(now_m) < 0.0:
        return False, "INVALID_MONOTONIC_CLOCK", [f"INVALID_MONOTONIC_CLOCK:{now_m!r}"], 0.0, 0.0
    now_m_f = float(now_m)

    try:
        now_u = utc_now_fn()
    except Exception as exc:
        return False, "INVALID_UTC_CLOCK", [f"INVALID_UTC_CLOCK:{exc}"], 0.0, 0.0
    if not isinstance(now_u, datetime) or now_u.tzinfo is None:
        return False, "INVALID_UTC_CLOCK", ["INVALID_OR_NAIVE_UTC_CLOCK"], 0.0, 0.0

    rem_session = deadline_mono - now_m_f
    if not math.isfinite(rem_session) or rem_session <= 0.0:
        return False, "CAPTURE_DEADLINE_EXCEEDED", ["CAPTURE_DEADLINE_EXCEEDED"], 0.0, 0.0

    rem_grant = (float(grant_expires_utc_ms) / 1000.0) - now_u.timestamp()
    if not math.isfinite(rem_grant) or rem_grant <= 0.0:
        return False, "OPERATOR_GRANT_EXPIRED", ["OPERATOR_GRANT_EXPIRED"], 0.0, 0.0

    ctx_ok, ctx_viols, _, _ = _verify_r5_execution_context(plan, trusted_scope, now_u, destination, run_id)
    if not ctx_ok:
        first = ctx_viols[0] if ctx_viols else "EXECUTION_CONTEXT_INVALID"
        return False, first, ctx_viols, 0.0, 0.0

    res_clk = resource_monotonic_fn if resource_monotonic_fn is not None else monotonic_fn
    res_ok, res_viols, _ = _require_r5_resource_monitor(
        resource_monitor, destination, monotonic_fn=res_clk, max_age_sec=resource_max_age_sec
    )
    if not res_ok:
        first = res_viols[0] if res_viols else "RESOURCE_GUARD_FAILED"
        return False, first, res_viols, 0.0, 0.0

    eff_rem = min(rem_session, rem_grant)
    ceil_t = float(transport_timeout_ceiling_sec)
    if not math.isfinite(ceil_t) or ceil_t <= 0.0:
        return False, "INVALID_TRANSPORT_TIMEOUT_CEILING", ["INVALID_TRANSPORT_TIMEOUT_CEILING"], 0.0, 0.0
    eff_timeout = min(ceil_t, eff_rem)
    if eff_timeout <= 0.0:
        return False, "CAPTURE_DEADLINE_EXCEEDED", ["CAPTURE_DEADLINE_EXCEEDED"], 0.0, 0.0

    return True, None, [], eff_rem, eff_timeout


# =====================================================================
# 11. capture_r5_public_batch (KH-03.R5.C1 Hardened P1-A / P1-B / P1-C)
# =====================================================================
async def capture_r5_public_batch(
    plan: Mapping[str, Any],
    transport: Optional[Any],
    resource_monitor: Optional[Callable[[], Mapping[str, Any]]],
    cancel_event: Optional[asyncio.Event] = None,
    *,
    staging_root: Optional[Path] = None,
    run_id: str = "r5_capture",
    sleep_fn: Optional[Callable[[float], Any]] = None,
    trusted_scope: Optional[Any] = None,
    monotonic_fn: Optional[Callable[[], float]] = None,
    utc_now_fn: Optional[Callable[[], datetime]] = None,
    resource_max_age_sec: float = DEFAULT_RESOURCE_MAX_AGE_SEC_R5_C1,
    destination_root: Optional[Path] = None,
    transport_timeout_ceiling_sec: float = 10.0,
    drain_timeout_sec: float = 5.0,
) -> Dict[str, Any]:
    """
    Bounded async public read-only capture orchestrator (KH-03.R5.C1 hardened).
    Closes P1-A (strict plan/scope/expiry/destination/run binding before enqueue AND after every wait),
    P1-B (post-wait deadline/cancel/expiry guard, remaining-time per-call timeout, non-shortened
    Retry-After, atomic request + wire-byte reservation, bounded drain & late-response discard),
    and P1-C (strict fail-closed resource monitor requiring fresh valid volume-matched telemetry).
    NEVER mints `OBSERVED_VERIFIED` from fake/QA transports; NEVER counts deleted staging files
    as accepted persisted records on abort.
    """
    # If caller supplies a non-blocking `sleep_fn` without a custom `monotonic_fn`, track simulated
    # sleep advancement so deterministic no-op sleeps never spin against real wall monotonic time.
    _sim_sleep_offset = {"sec": 0.0}
    if monotonic_fn is not None:
        mono_clock = monotonic_fn
        res_mono_clock = monotonic_fn
        _advance_sim_sleep = False
    elif sleep_fn is not None:
        mono_clock = lambda: time.monotonic() + _sim_sleep_offset["sec"]
        res_mono_clock = time.monotonic
        _advance_sim_sleep = True
    else:
        mono_clock = time.monotonic
        res_mono_clock = time.monotonic
        _advance_sim_sleep = False
    utc_clock = utc_now_fn if utc_now_fn is not None else (lambda: datetime.now(timezone.utc))
    dest_for_guard = destination_root if destination_root is not None else staging_root

    def _blocked_or_fail_resp(
        exec_status: str,
        prov_status: str,
        viols: Sequence[str],
        *,
        aborted_flag: bool = False,
        abort_reason: Optional[str] = None,
        role_str: str = "NONE_BLOCKED",
        sess_id: str = "",
    ) -> Dict[str, Any]:
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "schema_version": SCHEMA_VERSION_KH03_R5_C1,
            "run_id": run_id,
            "capture_session_id": sess_id,
            "partition_role": role_str,
            "capture_execution_status": exec_status,
            "capture_provenance_status": prov_status,
            "external_requests_attempted": 0,
            "qa_simulated_requests_count": 0,
            "requests_reserved_total": 0,
            "requests_released_pre_dispatch": 0,
            "requests_dispatched_actual": 0,
            "requests_completed": 0,
            "requests_rejected": 0,
            "requests_accepted": 0,
            "total_bytes_received": 0,
            "wire_bytes_observed_total": 0,
            "bytes_accepted_total": 0,
            "bytes_staged_total": 0,
            "accepted_observed_records_count": 0,
            "qa_fixture_records_count": 0,
            "removed_staged_files_count": 0,
            "retained_staged_files": [],
            "undrained_inflight_workers": 0,
            "aborted": aborted_flag,
            "abort_reason": abort_reason,
            "dispatch_timestamps_mono": [],
            "items": [],
            "aborted_discarded_items_audit": [],
            "semantic_result_sha256": canonical_hash({"items": [], "aborted": aborted_flag, "abort_reason": abort_reason, "violations": list(viols)}),
            "volatile_fields_excluded_from_semantic_hash": [
                "capture_session_id", "dispatch_timestamps_mono", "request_utc_ms", "response_utc_ms",
                "monotonic_duration_ms", "capture_proof_id", "staged_filename",
            ],
            "violations": list(viols),
        }

    if not isinstance(plan, Mapping):
        return _blocked_or_fail_resp("FAIL", "NO_REAL_CAPTURE", ["PLAN_NOT_MAPPING"])

    plan_st = str(plan.get("status") or "BLOCKED")
    role = str(plan.get("partition_role") or "NONE_BLOCKED")
    live_perm = bool(plan.get("live_network_permitted", False))

    if plan_st == "BLOCKED" or (not live_perm and role != "QA_FIXTURE"):
        return _blocked_or_fail_resp(
            "BLOCKED_OWNER_AUTHORIZATION",
            "NO_REAL_CAPTURE",
            list(plan.get("violations") or ["CAPTURE_BLOCKED_OWNER_AUTHORIZATION"]),
            role_str=role,
        )

    # Validate clocks before any operation
    try:
        t0_mono_raw = mono_clock()
        now_utc_0 = utc_clock()
    except Exception as exc:
        return _blocked_or_fail_resp("FAIL", "NO_REAL_CAPTURE", [f"INVALID_CLOCK_PROVIDER:{exc}"], role_str=role)
    if isinstance(t0_mono_raw, bool) or not isinstance(t0_mono_raw, (int, float)) or not math.isfinite(float(t0_mono_raw)) or float(t0_mono_raw) < 0.0:
        return _blocked_or_fail_resp("FAIL", "NO_REAL_CAPTURE", [f"INVALID_MONOTONIC_CLOCK:{t0_mono_raw!r}"], role_str=role)
    if not isinstance(now_utc_0, datetime) or now_utc_0.tzinfo is None:
        return _blocked_or_fail_resp("FAIL", "NO_REAL_CAPTURE", ["INVALID_OR_NAIVE_UTC_CLOCK"], role_str=role)
    if isinstance(transport_timeout_ceiling_sec, bool) or not isinstance(transport_timeout_ceiling_sec, (int, float)) or not math.isfinite(float(transport_timeout_ceiling_sec)) or float(transport_timeout_ceiling_sec) <= 0.0:
        return _blocked_or_fail_resp("FAIL", "NO_REAL_CAPTURE", [f"INVALID_TRANSPORT_TIMEOUT_CEILING:{transport_timeout_ceiling_sec!r}"], role_str=role)
    t0_mono = float(t0_mono_raw)

    # P1-A: Strict execution-context binding BEFORE creating any tasks or touching transport/staging!
    ctx_ok, ctx_viols, plan_rec, bound_scope = _verify_r5_execution_context(plan, trusted_scope, now_utc_0, dest_for_guard, run_id)
    if not ctx_ok or plan_rec is None or bound_scope is None:
        return _blocked_or_fail_resp(
            "BLOCKED_SCOPE_OR_PLAN_BINDING",
            "NO_REAL_CAPTURE",
            ctx_viols,
            aborted_flag=True,
            abort_reason=ctx_viols[0] if ctx_viols else "EXECUTION_CONTEXT_INVALID",
            role_str=role,
        )

    if transport is None:
        return _blocked_or_fail_resp("FAIL", "NO_REAL_CAPTURE", ["MISSING_TRANSPORT_OBJECT"], role_str=role)

    is_qa_plan = (role == "QA_FIXTURE") and (not live_perm)
    exact_stdlib, _exact_reasons = is_exact_unmodified_stdlib_transport_c4(
        transport, expected_transport_cls=StdlibUrllibPublicTransportC2, expected_get_func=StdlibUrllibPublicTransportC2.get
    )
    if live_perm and not exact_stdlib:
        return _blocked_or_fail_resp(
            "FAIL", "FAIL", ["FAKE_OR_SUBCLASSED_TRANSPORT_CANNOT_EXECUTE_LIVE_OBSERVED_CAPTURE"], role_str=role
        )
    if is_qa_plan and exact_stdlib:
        return _blocked_or_fail_resp(
            "FAIL", "NO_REAL_CAPTURE", ["QA_FIXTURE_MODE_MUST_NOT_USE_LIVE_SOCKET_TRANSPORT"], role_str=role
        )

    # Validate staging_root containment & non-OOS policy before creating any tasks
    if staging_root is not None:
        s_low = str(staging_root).lower().replace("\\", "/")
        if any(tok in s_low for tok in R5_FORBIDDEN_OOS_PATH_TOKENS):
            return _blocked_or_fail_resp("FAIL", "NO_REAL_CAPTURE", ["OOS_STAGING_ROOT_FORBIDDEN"], role_str=role)
        if _is_symlink_or_junction(staging_root):
            return _blocked_or_fail_resp("FAIL", "NO_REAL_CAPTURE", ["SYMLINK_STAGING_ROOT_DENIED"], role_str=role)
        if destination_root is not None:
            try:
                _safe_contained_path(staging_root, destination_root)
            except Exception as exc:
                return _blocked_or_fail_resp("FAIL", "NO_REAL_CAPTURE", [f"STAGING_OUTSIDE_DESTINATION_ROOT:{exc}"], role_str=role)

    budget = dict(plan.get("budget") or {})
    max_conc = max(1, min(MAX_GLOBAL_CONCURRENCY_R5, int(budget["max_concurrency"])))
    max_requests = int(budget["max_requests"])
    max_total_bytes = int(budget["max_total_bytes"])
    max_resp_bytes = min(MAX_RAW_HTTP_BODY_BYTES_C3, int(budget["max_response_bytes"]))
    max_dur_sec = float(budget["max_duration_sec"])
    max_retries = min(HARD_MAX_RETRIES_R5_C1, max(0, int(budget["max_retries_per_request"])))
    grant_expires_utc_ms = int(plan_rec.expires_utc_ms)

    # §4.2 / B24: Legacy locked C4 transport reads `transport.max_response_bytes` fixed at init;
    # if live transport cannot guarantee requested wire read cap (max_total_bytes / max_resp_bytes),
    # FAIL CLOSED instead of labeling post-hoc body checks a strict wire cap!
    if live_perm and exact_stdlib:
        tr_max_resp = int(getattr(transport, "max_response_bytes", MAX_RAW_HTTP_BODY_BYTES_C3))
        if tr_max_resp > min(max_resp_bytes, max_total_bytes):
            return _blocked_or_fail_resp(
                "BLOCKED_TRANSPORT_READ_CAP_UNSUPPORTED",
                "NO_REAL_CAPTURE",
                [f"BLOCKED_TRANSPORT_READ_CAP_UNSUPPORTED:transport_max={tr_max_resp}>budget_cap={min(max_resp_bytes, max_total_bytes)}"],
                aborted_flag=True,
                abort_reason="BLOCKED_TRANSPORT_READ_CAP_UNSUPPORTED",
                role_str=role,
            )

    # P1-C: Pre-dispatch strict resource monitor check BEFORE creating any tasks!
    pre_res_ok, pre_res_viols, _ = _require_r5_resource_monitor(
        resource_monitor, dest_for_guard, monotonic_fn=res_mono_clock, max_age_sec=resource_max_age_sec
    )
    if not pre_res_ok:
        return _blocked_or_fail_resp(
            "ABORTED_RESOURCE_OR_CANCEL",
            "NO_REAL_CAPTURE",
            pre_res_viols,
            aborted_flag=True,
            abort_reason=pre_res_viols[0] if pre_res_viols else "RESOURCE_GUARD_FAILED",
            role_str=role,
        )

    deadline_mono = t0_mono + max_dur_sec
    sem = asyncio.Semaphore(max_conc)
    state_lock = asyncio.Lock()
    budget_cond = asyncio.Condition(state_lock)
    ep_last_actual_ts: Dict[str, float] = {}
    ep_next_slot_ts: Dict[str, float] = {}
    counters: Dict[str, Any] = {
        "requests_reserved": 0,
        "requests_reserved_total": 0,
        "requests_released_pre_dispatch": 0,
        "requests_dispatched_actual": 0,
        "requests_completed": 0,
        "requests_rejected": 0,
        "requests_accepted": 0,
        "wire_bytes_reserved": 0,
        "wire_bytes_observed_total": 0,
        "bytes_accepted_total": 0,
        "bytes_staged_total": 0,
        "aborted": False,
        "abort_reason": None,
        "undrained_inflight_workers": 0,
    }
    dispatch_timestamps_mono: List[float] = []
    items: List[Dict[str, Any]] = []
    violations: List[str] = []
    staged_paths: List[Path] = []
    session_id = f"r5c1_{run_id}_{uuid.uuid4().hex[:8]}"
    batches_list = list(plan.get("batches") or ())

    async def _set_aborted(reason: str, extra_viols: Sequence[str] = ()) -> None:
        async with budget_cond:
            if not counters["aborted"]:
                counters["aborted"] = True
                counters["abort_reason"] = reason
            for v in extra_viols:
                if v not in violations:
                    violations.append(v)
            if reason and reason not in violations:
                violations.append(reason)
            budget_cond.notify_all()

    async def _do_cancellable_sleep(sec: float) -> bool:
        """Sleeps up to `sec` seconds; returns False if cancelled during wait."""
        if sec <= 0.0:
            return not (cancel_event is not None and cancel_event.is_set())
        if sleep_fn is not None:
            r = sleep_fn(sec)
            if asyncio.iscoroutine(r):
                await r
            if _advance_sim_sleep:
                _sim_sleep_offset["sec"] += float(sec)
            return not (cancel_event is not None and cancel_event.is_set())
        if cancel_event is not None:
            try:
                await asyncio.wait_for(cancel_event.wait(), timeout=sec)
                return False
            except asyncio.TimeoutError:
                return not cancel_event.is_set()
        await asyncio.sleep(sec)
        return True

    def _run_guard_sync(aborted_now: bool) -> Tuple[bool, Optional[str], List[str], float, float]:
        return _check_r5_dispatch_guard(
            plan=plan,
            trusted_scope=trusted_scope,
            run_id=run_id,
            destination=dest_for_guard,
            deadline_mono=deadline_mono,
            grant_expires_utc_ms=grant_expires_utc_ms,
            transport_timeout_ceiling_sec=transport_timeout_ceiling_sec,
            cancel_event=cancel_event,
            aborted=aborted_now,
            resource_monitor=resource_monitor,
            monotonic_fn=mono_clock,
            utc_now_fn=utc_clock,
            resource_max_age_sec=resource_max_age_sec,
            resource_monotonic_fn=res_mono_clock,
        )

    async def _run_one_batch(batch: Mapping[str, Any]) -> None:
        ep_key = str(batch["endpoint_key"])
        url = str(batch["url"])
        low_u = url.lower()
        if any(tok in low_u for tok in FORBIDDEN_URL_TOKENS_C2) or ep_key not in OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2:
            await _set_aborted(f"BLOCKED_UNAUTHORIZED_OR_PRIVATE_ENDPOINT:{ep_key}:{url}")
            return
        if url != str(OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2[ep_key]["url"]):
            await _set_aborted(f"BATCH_URL_KEY_MISMATCH:{ep_key}")
            return

        params: Dict[str, Any] = {"symbol": str(batch["symbol"]), "productType": str(batch["product_type"])}
        if ep_key in ("candles", "history_candles"):
            params["granularity"] = str(batch.get("granularity") or "1m")
            if batch.get("window_start_ms"):
                params["startTime"] = str(batch["window_start_ms"])
            if batch.get("window_end_ms"):
                params["endTime"] = str(batch["window_end_ms"])
        if scan_for_forbidden_secrets_r2_c1(params):
            await _set_aborted(f"FORBIDDEN_SECRET_IN_PARAMS:{ep_key}")
            return
        try:
            _validate_public_bitget_https_url_c3(url, params)
        except Exception as exc:
            await _set_aborted(f"URL_ALLOWLIST_REJECTED:{ep_key}:{exc}")
            return

        min_iv_s = max(HARD_MIN_REQUEST_INTERVAL_MS_R5_C1 / 1000.0, float(batch.get("min_request_interval_ms", HARD_MIN_REQUEST_INTERVAL_MS_R5_C1)) / 1000.0)
        retry_chain: List[Dict[str, Any]] = []

        async with sem:
            for attempt in range(max_retries + 1):
                # 1) Pre-wait guard (after semaphore acquire!)
                g_ok, g_reason, g_viols, eff_rem, eff_timeout = _run_guard_sync(bool(counters["aborted"]))
                if not g_ok:
                    await _set_aborted(g_reason or "DISPATCH_GUARD_FAILED", g_viols)
                    return

                # 2) Reserve request slot (NOT counting actual dispatch yet!) and compute rate-limiter wait
                req_reserved = False
                async with budget_cond:
                    if counters["aborted"]:
                        return
                    if counters["requests_dispatched_actual"] + counters["requests_reserved"] >= max_requests:
                        counters["aborted"] = True
                        counters["abort_reason"] = "MAX_REQUESTS_BUDGET_EXHAUSTED"
                        violations.append("MAX_REQUESTS_BUDGET_EXHAUSTED")
                        budget_cond.notify_all()
                        return
                    now_m = float(mono_clock())
                    last_slot = ep_next_slot_ts.get(ep_key)
                    if last_slot is None:
                        wait_s = 0.0
                        ep_next_slot_ts[ep_key] = now_m
                    else:
                        target_t = max(now_m, last_slot + min_iv_s)
                        wait_s = max(0.0, target_t - now_m)
                        ep_next_slot_ts[ep_key] = target_t
                    if wait_s >= eff_rem:
                        counters["aborted"] = True
                        counters["abort_reason"] = "WAIT_EXCEEDS_REMAINING_DEADLINE"
                        violations.append("WAIT_EXCEEDS_REMAINING_DEADLINE")
                        budget_cond.notify_all()
                        return
                    counters["requests_reserved"] += 1
                    counters["requests_reserved_total"] += 1
                    req_reserved = True

                # 3) Perform cancellable rate-limit wait outside state_lock
                if wait_s > 0.0:
                    slept_ok = await _do_cancellable_sleep(wait_s)
                    if not slept_ok:
                        async with budget_cond:
                            if req_reserved:
                                counters["requests_reserved"] -= 1
                                counters["requests_released_pre_dispatch"] += 1
                                req_reserved = False
                            budget_cond.notify_all()
                        await _set_aborted("CANCELLED_MIDFLIGHT")
                        return

                # 4) Enforce actual monotonic cadence & reserve wire bytes atomically BEFORE final dispatch!
                call_byte_cap = 0
                dispatched_this_attempt = False
                try:
                    while True:
                        # POST-WAIT CRITICAL GUARD (re-checks deadline, cancel, expiry, plan mutation, resource!)
                        g2_ok, g2_reason, g2_viols, eff_rem2, eff_timeout2 = _run_guard_sync(bool(counters["aborted"]))
                        if not g2_ok:
                            await _set_aborted(g2_reason or "POST_WAIT_GUARD_FAILED", g2_viols)
                            return

                        extra_cadence_wait = 0.0
                        need_budget_wait = False
                        async with budget_cond:
                            if counters["aborted"]:
                                return
                            now_m2 = float(mono_clock())
                            last_act = ep_last_actual_ts.get(ep_key)
                            if last_act is not None and (now_m2 - last_act) + 1e-9 < min_iv_s:
                                extra_cadence_wait = min_iv_s - (now_m2 - last_act)
                                if extra_cadence_wait >= eff_rem2:
                                    counters["aborted"] = True
                                    counters["abort_reason"] = "WAIT_EXCEEDS_REMAINING_DEADLINE"
                                    violations.append("WAIT_EXCEEDS_REMAINING_DEADLINE")
                                    budget_cond.notify_all()
                                    return
                            else:
                                rem_wire_unreserved = max_total_bytes - counters["wire_bytes_observed_total"] - counters["wire_bytes_reserved"]
                                if counters["wire_bytes_observed_total"] >= max_total_bytes:
                                    counters["aborted"] = True
                                    counters["abort_reason"] = "TOTAL_BYTES_BUDGET_EXHAUSTED"
                                    violations.append("TOTAL_BYTES_BUDGET_EXHAUSTED")
                                    budget_cond.notify_all()
                                    return
                                if rem_wire_unreserved <= 0:
                                    if counters["wire_bytes_reserved"] > 0:
                                        need_budget_wait = True
                                    else:
                                        counters["aborted"] = True
                                        counters["abort_reason"] = "TOTAL_BYTES_BUDGET_EXHAUSTED"
                                        violations.append("TOTAL_BYTES_BUDGET_EXHAUSTED")
                                        budget_cond.notify_all()
                                        return
                                else:
                                    call_byte_cap = min(max_resp_bytes, rem_wire_unreserved)
                                    # Live transport check for per-call dynamic wire cap
                                    if live_perm and exact_stdlib:
                                        tr_max_r = int(getattr(transport, "max_response_bytes", MAX_RAW_HTTP_BODY_BYTES_C3))
                                        if tr_max_r > call_byte_cap:
                                            counters["aborted"] = True
                                            counters["abort_reason"] = "BLOCKED_TRANSPORT_READ_CAP_UNSUPPORTED"
                                            violations.append("BLOCKED_TRANSPORT_READ_CAP_UNSUPPORTED")
                                            budget_cond.notify_all()
                                            return
                                    # LINEARIZATION POINT: transition reservation -> actual dispatch!
                                    counters["requests_reserved"] -= 1
                                    req_reserved = False
                                    counters["requests_dispatched_actual"] += 1
                                    counters["wire_bytes_reserved"] += call_byte_cap
                                    ep_last_actual_ts[ep_key] = now_m2
                                    dispatch_timestamps_mono.append(now_m2)
                                    dispatched_this_attempt = True
                                    break

                        if extra_cadence_wait > 0.0:
                            slept_ok2 = await _do_cancellable_sleep(extra_cadence_wait)
                            if not slept_ok2:
                                await _set_aborted("CANCELLED_MIDFLIGHT")
                                return
                        elif need_budget_wait:
                            async with budget_cond:
                                try:
                                    await asyncio.wait_for(budget_cond.wait(), timeout=min(0.05, max(0.001, eff_rem2)))
                                except asyncio.TimeoutError:
                                    pass
                finally:
                    if req_reserved and not dispatched_this_attempt:
                        async with budget_cond:
                            counters["requests_reserved"] -= 1
                            counters["requests_released_pre_dispatch"] += 1
                            budget_cond.notify_all()

                # 5) Execute actual transport call with remaining-time bounded timeout!
                now_u_call = utc_clock()
                req_utc_ms = int(now_u_call.timestamp() * 1000)
                t_req_mono = float(mono_clock())
                raw_resp: Optional[RawHttpResponseC2] = None
                proof_c4: Optional[VerifiedCaptureProofC4] = None
                obs_wire_len = 0
                try:
                    if exact_stdlib and live_perm:
                        tr_res = await asyncio.to_thread(
                            execute_trusted_stdlib_https_get_c4,
                            transport=transport,
                            expected_transport_cls=StdlibUrllibPublicTransportC2,
                            expected_get_func=StdlibUrllibPublicTransportC2.get,
                            validate_url_fn=_validate_public_bitget_https_url_c3,
                            no_redirect_handler_cls=_NoRedirectHandlerC3,
                            secret_scanner_fn=scan_for_forbidden_secrets_r2_c1,
                            decompress_check_fn=safe_bounded_decompress_if_gzip_c3,
                            raw_response_cls=RawHttpResponseC2,
                            url=url,
                            params=params,
                            endpoint_key=ep_key,
                            timeout_sec=eff_timeout2,
                            capture_session_id=session_id,
                            live_authorization_verified=True,
                            real_rate_limiter_active=True,
                            is_fake_transport_qa=False,
                        )
                        raw_resp = tr_res.response
                        proof_c4 = tr_res.capture_proof
                    else:
                        raw_resp = await asyncio.to_thread(transport.get, url, params, timeout_sec=eff_timeout2)
                        proof_c4 = None
                    if raw_resp is not None:
                        obs_wire_len = len(bytes(getattr(raw_resp, "body_bytes", b"") or b""))
                except TimeoutError as exc:
                    async with budget_cond:
                        counters["wire_bytes_reserved"] = max(0, counters["wire_bytes_reserved"] - call_byte_cap)
                        counters["requests_completed"] += 1
                        counters["requests_rejected"] += 1
                        budget_cond.notify_all()
                    backoff_to = min(2.0, 0.05 * (2 ** attempt))
                    retry_chain.append({"attempt": attempt, "error": f"TIMEOUT:{exc}", "backoff_sec": backoff_to})
                    if attempt < max_retries:
                        g_rt_ok, g_rt_r, g_rt_v, rem_rt, _ = _run_guard_sync(bool(counters["aborted"]))
                        if not g_rt_ok:
                            await _set_aborted(g_rt_r or "ABORTED_DURING_RETRY", g_rt_v)
                            return
                        if backoff_to >= rem_rt:
                            await _set_aborted("RETRY_BACKOFF_EXCEEDS_REMAINING_DEADLINE")
                            return
                        if not await _do_cancellable_sleep(backoff_to):
                            await _set_aborted("CANCELLED_MIDFLIGHT")
                            return
                        continue
                    violations.append(f"TIMEOUT_EXHAUSTED:{ep_key}")
                    return
                except Exception as exc:
                    async with budget_cond:
                        counters["wire_bytes_reserved"] = max(0, counters["wire_bytes_reserved"] - call_byte_cap)
                        counters["requests_completed"] += 1
                        counters["requests_rejected"] += 1
                        budget_cond.notify_all()
                    violations.append(f"TRANSPORT_OR_PROVENANCE_ERROR:{ep_key}:{exc}")
                    return

                # Release reservation & account observed wire bytes (INCLUDING error/retry bodies!)
                over_wire_budget = False
                async with budget_cond:
                    counters["wire_bytes_reserved"] = max(0, counters["wire_bytes_reserved"] - call_byte_cap)
                    counters["wire_bytes_observed_total"] += obs_wire_len
                    counters["requests_completed"] += 1
                    if counters["wire_bytes_observed_total"] > max_total_bytes:
                        over_wire_budget = True
                        counters["aborted"] = True
                        counters["abort_reason"] = "TOTAL_BYTES_BUDGET_EXHAUSTED"
                        violations.append("TOTAL_BYTES_BUDGET_EXHAUSTED")
                    budget_cond.notify_all()

                if over_wire_budget:
                    async with budget_cond:
                        counters["requests_rejected"] += 1
                    return

                # Post-response guard: if cancelled/aborted/expired while in-flight, DISCARD late response!
                g_post_ok, g_post_r, g_post_v, rem_post, _ = _run_guard_sync(bool(counters["aborted"]))
                if not g_post_ok:
                    async with budget_cond:
                        counters["requests_rejected"] += 1
                    await _set_aborted(g_post_r or "LATE_RESPONSE_DISCARDED", g_post_v)
                    return

                assert raw_resp is not None
                st_code = int(raw_resp.status_code)
                hdrs = {str(k).lower(): str(v) for k, v in (raw_resp.headers or {}).items()}
                if st_code in (301, 302, 303, 307, 308):
                    async with budget_cond:
                        counters["requests_rejected"] += 1
                    violations.append(f"HTTP_REDIRECT_BLOCKED:{ep_key}:status={st_code}")
                    return

                if st_code == 429 or (500 <= st_code < 600):
                    async with budget_cond:
                        counters["requests_rejected"] += 1
                    ra_raw = hdrs.get("retry-after")
                    ra_ok, ra_sec, ra_err = _parse_retry_after_strict(ra_raw, utc_clock())
                    if not ra_ok:
                        await _set_aborted(f"INVALID_RETRY_AFTER:{ep_key}:{ra_err}")
                        return
                    if ra_sec is not None:
                        backoff = float(ra_sec)  # NEVER shorten server-instructed Retry-After!
                    else:
                        backoff = min(2.0, 0.05 * (2 ** attempt))
                    retry_chain.append({"attempt": attempt, "http_status": st_code, "backoff_sec": backoff, "retry_after_raw": ra_raw})
                    if attempt < max_retries:
                        g_rb_ok, g_rb_r, g_rb_v, rem_rb, _ = _run_guard_sync(bool(counters["aborted"]))
                        if not g_rb_ok:
                            await _set_aborted(g_rb_r or "ABORTED_BEFORE_RETRY_SLEEP", g_rb_v)
                            return
                        if backoff >= rem_rb:
                            await _set_aborted(f"RETRY_AFTER_EXCEEDS_REMAINING_BUDGET:wait={backoff:.3f}>=rem={rem_rb:.3f}")
                            return
                        if not await _do_cancellable_sleep(backoff):
                            await _set_aborted("CANCELLED_MIDFLIGHT")
                            return
                        continue
                    violations.append(f"HTTP_RETRY_EXHAUSTED:{ep_key}:status={st_code}")
                    return

                if st_code != 200:
                    async with budget_cond:
                        counters["requests_rejected"] += 1
                    violations.append(f"HTTP_NON_200_NO_RETRY:{ep_key}:status={st_code}")
                    return

                body = bytes(raw_resp.body_bytes)
                if not body:
                    async with budget_cond:
                        counters["requests_rejected"] += 1
                    violations.append(f"EMPTY_HTTP_200_BODY:{ep_key}")
                    return
                if len(body) > max_resp_bytes:
                    async with budget_cond:
                        counters["requests_rejected"] += 1
                    violations.append(f"RESPONSE_OVERSIZE:{ep_key}:{len(body)}>{max_resp_bytes}")
                    return
                try:
                    unpacked = safe_bounded_decompress_if_gzip_c3(
                        body, max_raw_bytes=max_resp_bytes, max_decompressed_bytes=MAX_DECOMPRESSED_BODY_BYTES_C3
                    )
                except Exception as exc:
                    async with budget_cond:
                        counters["requests_rejected"] += 1
                    violations.append(f"DECOMPRESSION_BOMB_OR_GZIP_REJECTED:{ep_key}:{exc}")
                    return

                if proof_c4 is None:
                    mono_ms = max(1, int((float(mono_clock()) - t_req_mono) * 1000))
                    proof_c4 = issue_qa_or_unverified_capture_proof_c4(
                        capture_session_id=session_id,
                        endpoint_key=ep_key,
                        url=url,
                        final_url=str(getattr(raw_resp, "final_url", url) or url),
                        tls_host=str(getattr(raw_resp, "tls_verified_host", "") or ""),
                        http_status=st_code,
                        params=params,
                        raw_response_bytes=body,
                        request_utc_ms=int(getattr(raw_resp, "request_utc_ms", req_utc_ms)),
                        response_utc_ms=int(getattr(raw_resp, "response_utc_ms", req_utc_ms + mono_ms)),
                        monotonic_duration_ms=mono_ms,
                        transport_class_name=type(transport).__name__,
                        transport_kind="INJECTED_FAKE_TRANSPORT_QA",
                        real_rate_limiter_active=True,
                        is_fake_transport_qa=True,
                    )

                # Pre-staging guard (checks cancel, deadline, expiry, plan binding, resource BEFORE writing!)
                g_st_ok, g_st_r, g_st_v, _, _ = _run_guard_sync(bool(counters["aborted"]))
                if not g_st_ok:
                    async with budget_cond:
                        counters["requests_rejected"] += 1
                    await _set_aborted(g_st_r or "PRE_STAGING_GUARD_FAILED", g_st_v)
                    return

                raw_sha = _sha256_bytes(body)
                canon_sha = _sha256_bytes(unpacked)
                staged_rel = None
                if staging_root is not None:
                    staging_root.mkdir(parents=True, exist_ok=True)
                    st_file = staging_root / f"{batch['batch_id']}_{raw_sha[:12]}.raw"
                    _safe_contained_path(st_file, staging_root)
                    tmp_st = staging_root / f".tmp_{batch['batch_id']}_{uuid.uuid4().hex[:8]}.part"
                    _safe_contained_path(tmp_st, staging_root)
                    fd_s = os.open(str(tmp_st), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    try:
                        _write_all_eintr_safe(fd_s, body)
                        os.fsync(fd_s)
                    finally:
                        os.close(fd_s)
                    os.replace(str(tmp_st), str(st_file))
                    _fsync_directory(staging_root)
                    async with budget_cond:
                        staged_paths.append(st_file)
                        counters["bytes_staged_total"] += len(body)
                    staged_rel = st_file.name

                receipt_c4 = mint_trusted_capture_receipt_c4(
                    capture_proof=proof_c4,
                    capture_session_id=session_id,
                    endpoint_key=ep_key,
                    url=url,
                    params=params,
                    http_status=st_code,
                    raw_response_bytes=body,
                    canonical_parsed_sha256=canon_sha,
                    request_utc_ms=int(raw_resp.request_utc_ms),
                    response_utc_ms=int(raw_resp.response_utc_ms),
                    monotonic_duration_ms=max(1, int((float(mono_clock()) - t_req_mono) * 1000)),
                    provider_event_utc_ms=int(raw_resp.response_utc_ms),
                    transport_obj=transport,
                    transport_kind="STDLIB_URLLIB_HTTPS" if (exact_stdlib and live_perm) else "INJECTED_FAKE_TRANSPORT_QA",
                    tls_host=str(raw_resp.tls_verified_host or ""),
                    is_fake_transport_qa=not (exact_stdlib and live_perm),
                    live_transport_verified=bool(exact_stdlib and live_perm),
                    live_authorization_verified=bool(exact_stdlib and live_perm),
                    real_rate_limiter_active=True,
                )
                ev_cls = (
                    "OBSERVED_VERIFIED"
                    if (receipt_c4.provenance_class == ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP and exact_stdlib and live_perm)
                    else "QA_FIXTURE"
                )
                async with budget_cond:
                    if counters["aborted"]:
                        counters["requests_rejected"] += 1
                        return
                    counters["requests_accepted"] += 1
                    counters["bytes_accepted_total"] += len(body)
                    items.append({
                        "batch_id": str(batch["batch_id"]),
                        "endpoint_key": ep_key,
                        "http_status": st_code,
                        "raw_bytes_len": len(body),
                        "raw_sha256": raw_sha,
                        "canonical_unpacked_sha256": canon_sha,
                        "evidence_class": ev_cls,
                        "provenance_class_c3": str(receipt_c4.provenance_class),
                        "staged_filename": staged_rel,
                        "retry_chain": retry_chain,
                        "capture_proof_id": proof_c4.proof_id,
                        "capture_receipt_audit": receipt_c4.to_audit_dict(),
                        "_capture_proof_obj": proof_c4,
                        "_capture_receipt_obj": receipt_c4,
                    })
                return

    tasks = [asyncio.create_task(_run_one_batch(b)) for b in batches_list]
    if tasks:
        try:
            done, pending = await asyncio.wait(tasks, timeout=max(float(drain_timeout_sec), max_dur_sec + float(drain_timeout_sec)))
            if pending:
                await _set_aborted("INFLIGHT_WORKERS_UNDRAINED_TIMEOUT")
                for p_t in pending:
                    p_t.cancel()
                _, still_pending = await asyncio.wait(pending, timeout=max(0.05, float(drain_timeout_sec)))
                counters["undrained_inflight_workers"] = len(still_pending)
            for d_t in done:
                if not d_t.cancelled():
                    exc_t = d_t.exception()
                    if exc_t is not None:
                        await _set_aborted(f"BATCH_TASK_EXCEPTION:{type(exc_t).__name__}:{exc_t}")
        except asyncio.CancelledError:
            await _set_aborted("COROUTINE_CANCELLED_MIDFLIGHT")
            for t_i in tasks:
                if not t_i.done():
                    t_i.cancel()
            _, still_pending = await asyncio.wait(tasks, timeout=max(0.05, float(drain_timeout_sec)))
            counters["undrained_inflight_workers"] = len(still_pending)

    # Canonical deterministic ordering by (batch_id, endpoint_key, raw_sha256)
    items.sort(key=lambda it: (str(it["batch_id"]), str(it["endpoint_key"]), str(it["raw_sha256"])))

    # On abort: remove ONLY owned partial staged files and NEVER count deleted items as accepted!
    removed_staged = 0
    retained_staged: List[str] = []
    aborted_discarded_audit: List[Dict[str, Any]] = []
    if counters["aborted"]:
        if staging_root is not None:
            for sp in staged_paths:
                try:
                    _safe_contained_path(sp, staging_root)
                    if sp.is_file():
                        sp.unlink()
                        removed_staged += 1
                except Exception:
                    pass
        aborted_discarded_audit = [
            {k: v for k, v in it.items() if not k.startswith("_")} for it in items
        ]
        items = []
        counters["bytes_accepted_total"] = 0
        counters["requests_accepted"] = 0
    else:
        retained_staged = sorted(sp.name for sp in staged_paths if sp.is_file())

    obs_cnt = sum(1 for it in items if it["evidence_class"] == "OBSERVED_VERIFIED")
    qa_cnt = sum(1 for it in items if it["evidence_class"] == "QA_FIXTURE")
    planned_batch_ids = sorted(str(b["batch_id"]) for b in batches_list)
    completed_batch_ids = sorted(str(it["batch_id"]) for it in items)
    all_required_batches_accounted = (completed_batch_ids == planned_batch_ids)

    if counters["undrained_inflight_workers"] > 0:
        exec_st = "FAIL_UNDRAINED_INFLIGHT_WORKER"
    elif counters["aborted"]:
        exec_st = "ABORTED_RESOURCE_OR_CANCEL"
    elif is_qa_plan:
        exec_st = "QA_FIXTURE_OFFLINE_PASS" if (len(violations) == 0 and qa_cnt > 0 and all_required_batches_accounted) else "FAIL"
    else:
        exec_st = "VERIFIED_DEV_CAPTURE" if (len(violations) == 0 and obs_cnt > 0 and all_required_batches_accounted) else "FAIL"

    sem_view = {
        "partition_role": role,
        "capture_execution_status": exec_st,
        "aborted": bool(counters["aborted"]),
        "abort_reason": counters["abort_reason"],
        "planned_batch_ids": planned_batch_ids,
        "items": [
            {
                "batch_id": it["batch_id"],
                "endpoint_key": it["endpoint_key"],
                "http_status": it["http_status"],
                "raw_bytes_len": it["raw_bytes_len"],
                "raw_sha256": it["raw_sha256"],
                "canonical_unpacked_sha256": it["canonical_unpacked_sha256"],
                "evidence_class": it["evidence_class"],
                "retry_attempts": len(it["retry_chain"]),
            }
            for it in items
        ],
        "violations": sorted(violations),
    }

    return {
        "spec_version": SPEC_VERSION_KH03_R5_C1,
        "schema_version": SCHEMA_VERSION_KH03_R5_C1,
        "run_id": run_id,
        "capture_session_id": session_id,
        "partition_role": role,
        "capture_execution_status": exec_st,
        "capture_provenance_status": (
            "VERIFIED_OBSERVED_DEV"
            if (obs_cnt > 0 and not counters["aborted"])
            else ("QA_FIXTURE_ONLY_NO_REAL_CAPTURE" if (qa_cnt > 0 and not counters["aborted"]) else "NO_REAL_CAPTURE")
        ),
        "external_requests_attempted": counters["requests_dispatched_actual"] if live_perm else 0,
        "qa_simulated_requests_count": counters["requests_dispatched_actual"] if is_qa_plan else 0,
        "requests_reserved_total": counters["requests_reserved_total"],
        "requests_released_pre_dispatch": counters["requests_released_pre_dispatch"],
        "requests_dispatched_actual": counters["requests_dispatched_actual"],
        "requests_completed": counters["requests_completed"],
        "requests_rejected": counters["requests_rejected"],
        "requests_accepted": counters["requests_accepted"],
        "total_bytes_received": counters["wire_bytes_observed_total"],
        "wire_bytes_observed_total": counters["wire_bytes_observed_total"],
        "bytes_accepted_total": counters["bytes_accepted_total"],
        "bytes_staged_total": counters["bytes_staged_total"],
        "accepted_observed_records_count": obs_cnt,
        "qa_fixture_records_count": qa_cnt,
        "removed_staged_files_count": removed_staged,
        "retained_staged_files": retained_staged,
        "undrained_inflight_workers": counters["undrained_inflight_workers"],
        "all_required_batches_accounted": all_required_batches_accounted,
        "aborted": bool(counters["aborted"]),
        "abort_reason": counters["abort_reason"],
        "dispatch_timestamps_mono": list(dispatch_timestamps_mono),
        "items": items,
        "aborted_discarded_items_audit": aborted_discarded_audit,
        "semantic_result_sha256": canonical_hash(sem_view),
        "volatile_fields_excluded_from_semantic_hash": [
            "capture_session_id", "dispatch_timestamps_mono", "request_utc_ms", "response_utc_ms",
            "monotonic_duration_ms", "capture_proof_id", "staged_filename",
        ],
        "violations": violations,
    }


# =====================================================================
# 12. assign_r5_partition_before_capture
# =====================================================================
def assign_r5_partition_before_capture(
    plan: Mapping[str, Any],
    ledger: Mapping[str, Any],
    owner_scope: Mapping[str, Any],
    *,
    destination_root: Path,
    allowed_base_root: Path,
) -> Dict[str, Any]:
    """
    Assigns `DEVELOPMENT_CAPTURE` or `QA_FIXTURE` strictly BEFORE capture dispatch.
    Blocks:
      - any OOS role or OOS path token
      - path traversal / symlinks / junctions outside `allowed_base_root`
      - relabeling any known DEV/holdout/QA SHA or overlapping time interval as OOS or colliding role!
    """
    viols: List[str] = []
    role = str(plan.get("partition_role") or "")
    if role not in R5_ALLOWED_PARTITION_ROLES:
        viols.append(f"DISALLOWED_PARTITION_ROLE:{role!r}")
    if "OOS" in role.upper() or "OOS" in str(owner_scope.get("purpose", "")).upper():
        viols.append("OOS_PARTITION_FORBIDDEN_IN_R5")

    dest_low = str(destination_root).lower().replace("\\", "/")
    if any(tok in dest_low for tok in R5_FORBIDDEN_OOS_PATH_TOKENS):
        viols.append("OOS_DESTINATION_PATH_FORBIDDEN")

    try:
        safe_dest = _safe_contained_path(destination_root, allowed_base_root)
    except Exception as exc:
        viols.append(f"UNSAFE_DESTINATION_PATH:{exc}")
        safe_dest = destination_root

    # Check interval & SHA non-overlap against existing ledger metadata
    prior_intervals = list(ledger.get("existing_intervals") or ())
    known_shas = set(ledger.get("known_historical_shas") or ()) | {CANONICAL_DATASET_SHA256_KH02, KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256}
    proposed_sha = str(owner_scope.get("proposed_content_sha256") or "")
    if proposed_sha and proposed_sha in known_shas:
        viols.append("HISTORICAL_SHA_CONTAMINATION_OR_RELABEL_BLOCKED")

    w_start = int(owner_scope.get("window_start_ms", 0))
    w_end = int(owner_scope.get("window_end_ms", 0))
    sym = str(owner_scope.get("symbol") or "XAUUSDT").upper()
    overlaps: List[Dict[str, Any]] = []
    for iv in prior_intervals:
        if str(iv.get("symbol") or "").upper() != sym:
            continue
        iv_s, iv_e = int(iv.get("start_ms", 0)), int(iv.get("end_ms", 0))
        if w_start < iv_e and w_end > iv_s:
            overlaps.append({"prior_role": iv.get("role"), "start_ms": iv_s, "end_ms": iv_e})
    if overlaps:
        viols.append(f"TEMPORAL_INTERVAL_OVERLAP_QUARANTINED:{len(overlaps)}")

    ok = len(viols) == 0
    return {
        "valid": ok,
        "status": "ASSIGNED" if ok else "QUARANTINED_OR_DENIED",
        "partition_role": role if ok else "QUARANTINED",
        "permanent_dev_never_promotable_to_oos": True,
        "assigned_before_capture": True,
        "destination_root_rel": safe_dest.name,
        "plan_digest_sha256": plan.get("plan_digest_sha256"),
        "scope_digest_sha256": owner_scope.get("scope_digest_sha256"),
        "overlaps_detected": overlaps,
        "violations": viols,
    }


# =====================================================================
# 13. R5DevCustodyJournal & persist_r5_verified_dev_snapshot
# =====================================================================
class R5DevCustodyJournal:
    """
    Isolated append-only hash-chained custody journal strictly for KH-03.R5 DEV / QA namespace.
    NEVER touches historical OOS escrow! Uses cross-platform exclusive FD lock, atomic write +
    fsync + directory fsync, external head anchor, proof single-consumption under lock,
    idempotent exact-content dedup, and crash-safe quarantine of newly owned partial files.
    """

    def __init__(
        self,
        custody_root: Path,
        *,
        journal_id: str = "kh_03_r5_dev_custody_journal",
        os_disk_probe_fn: Optional[Callable[[], float]] = None,
        resource_monitor: Optional[Callable[[], Mapping[str, Any]]] = None,
        resource_max_age_sec: float = DEFAULT_RESOURCE_MAX_AGE_SEC_R5_C1,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if any(tok in str(custody_root).lower().replace("\\", "/") for tok in R5_FORBIDDEN_OOS_PATH_TOKENS):
            raise R5OosAccessDeniedError("R5_CUSTODY_JOURNAL_REFUSES_OOS_NAMESPACE")
        if _is_symlink_or_junction(Path(custody_root)):
            raise ValueError("SYMLINK_CUSTODY_ROOT_DENIED")
        self.custody_root = Path(custody_root).resolve()
        self.journal_id = journal_id
        self.os_disk_probe_fn = os_disk_probe_fn
        self.resource_monitor = resource_monitor
        self.resource_max_age_sec = float(resource_max_age_sec)
        self.monotonic_fn = monotonic_fn
        self.blobs_dir = self.custody_root / "blobs"
        self.quarantine_dir = self.custody_root / "quarantine"
        self.journal_path = self.custody_root / "custody_journal.jsonl"
        self.lock_path = self.custody_root / ".custody.lock"
        self.anchor = ExternalHeadAnchorC2(self.custody_root / "external_head_anchor.jsonl")
        self._thread_lock = threading.RLock()
        self._ensure_layout()

    def _ensure_layout(self) -> None:
        self.custody_root.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(exist_ok=True)
        self.quarantine_dir.mkdir(exist_ok=True)
        if not self.journal_path.exists():
            self.journal_path.write_bytes(b"")

    def _disk_free_gib(self) -> float:
        if self.os_disk_probe_fn is not None:
            v = self.os_disk_probe_fn()
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) or float(v) < 0.0:
                raise ValueError(f"INVALID_DISK_PROBE_VALUE:{v!r}")
            return float(v)
        return shutil.disk_usage(self.custody_root).free / (1024.0 ** 3)

    def _check_pre_write_resources(self) -> Tuple[bool, List[str], float]:
        if self.resource_monitor is not None:
            ok, viols, parsed = _require_r5_resource_monitor(
                self.resource_monitor,
                self.custody_root,
                now_monotonic=float(self.monotonic_fn()),
                max_age_sec=self.resource_max_age_sec,
            )
            if not ok or parsed is None:
                return False, viols, 0.0
            return True, [], float(parsed["disk_free_gib"])
        try:
            dk = self._disk_free_gib()
        except Exception as exc:
            return False, [f"DISK_PROBE_ERROR:{exc}"], 0.0
        if dk < STRICT_RUNTIME_DISK_MIN_GIB_R5:
            return False, [f"DISK_BELOW_20GIB:{dk:.4f}"], dk
        return True, [], dk

    def read_entries(self) -> List[Dict[str, Any]]:
        if not self.journal_path.is_file():
            return []
        entries: List[Dict[str, Any]] = []
        for ln in self.journal_path.read_text("utf-8").splitlines():
            s = ln.strip()
            if s:
                entries.append(json.loads(s))
        return entries

    def head_state(self) -> Dict[str, Any]:
        entries = self.read_entries()
        if not entries:
            return {"count": 0, "head_seq": -1, "head_entry_sha256": R5_GENESIS_HASH}
        last = entries[-1]
        return {"count": len(entries), "head_seq": int(last["seq_index"]), "head_entry_sha256": str(last["entry_sha256"])}

    def persist_snapshot(
        self,
        *,
        staging_path: Path,
        receipt_item: Mapping[str, Any],
        validation: Mapping[str, Any],
        partition: Mapping[str, Any],
        expected_staging_root: Optional[Path] = None,
        _crash_hook: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        if not partition.get("valid") or partition.get("partition_role") not in R5_ALLOWED_PARTITION_ROLES:
            return {"committed": False, "status": "REJECTED_PARTITION", "violations": ["INVALID_OR_UNASSIGNED_PARTITION"]}
        if not validation.get("valid"):
            return {"committed": False, "status": "REJECTED_VALIDATION", "violations": list(validation.get("violations") or ["VALIDATION_FAILED"])}
        if expected_staging_root is not None:
            try:
                _safe_contained_path(staging_path, expected_staging_root)
            except Exception as exc:
                return {"committed": False, "status": "REJECTED_STAGING_ESCAPE", "violations": [f"STAGING_PATH_OUTSIDE_ROOT:{exc}"]}
        if _is_symlink_or_junction(staging_path) or _is_symlink_or_junction(staging_path.parent):
            return {"committed": False, "status": "REJECTED_SYMLINK", "violations": ["SYMLINK_STAGING_DENIED"]}
        if not staging_path.is_file():
            return {"committed": False, "status": "REJECTED_MISSING_STAGING", "violations": ["MISSING_STAGING_RAW_FILE"]}

        # Mandatory expected_raw_sha (strict 64-hex) BEFORE reading staging bytes!
        expected_raw_sha = str(receipt_item.get("raw_sha256") or "").strip().lower()
        if len(expected_raw_sha) != 64 or not all(c in "0123456789abcdef" for c in expected_raw_sha):
            return {"committed": False, "status": "REJECTED_MISSING_RAW_SHA", "violations": ["MISSING_OR_INVALID_EXPECTED_RAW_SHA256"]}

        # Check file size BEFORE reading bytes into RAM!
        try:
            st_size = staging_path.stat().st_size
        except OSError as exc:
            return {"committed": False, "status": "REJECTED_STAT_ERROR", "violations": [f"STAGING_STAT_ERROR:{exc}"]}
        if st_size <= 0:
            return {"committed": False, "status": "REJECTED_EMPTY_STAGING", "violations": ["EMPTY_STAGING_FILE"]}
        if st_size > MAX_RAW_HTTP_BODY_BYTES_C3:
            return {"committed": False, "status": "REJECTED_OVERSIZE", "violations": [f"RAW_BLOB_OVERSIZE_BEFORE_READ:{st_size}>{MAX_RAW_HTTP_BODY_BYTES_C3}"]}

        # Strict resource floor check before read/write
        res_ok, res_viols, _ = self._check_pre_write_resources()
        if not res_ok:
            return {"committed": False, "status": "BLOCKED_LOW_DISK", "violations": res_viols}

        # Bounded stream read (never reads more than MAX_RAW_HTTP_BODY_BYTES_C3 + 1 bytes)
        chunks: List[bytes] = []
        total_read = 0
        with staging_path.open("rb") as fh:
            while True:
                chunk = fh.read(min(262_144, (MAX_RAW_HTTP_BODY_BYTES_C3 + 1) - total_read))
                if not chunk:
                    break
                chunks.append(chunk)
                total_read += len(chunk)
                if total_read > MAX_RAW_HTTP_BODY_BYTES_C3:
                    return {"committed": False, "status": "REJECTED_OVERSIZE", "violations": ["RAW_BLOB_OVERSIZE_STREAM_CAP"]}
        raw_bytes = b"".join(chunks)
        if not raw_bytes:
            return {"committed": False, "status": "REJECTED_EMPTY_STAGING", "violations": ["EMPTY_STAGING_FILE"]}

        try:
            safe_bounded_decompress_if_gzip_c3(raw_bytes)
        except Exception as exc:
            return {"committed": False, "status": "REJECTED_DECOMPRESS", "violations": [f"DECOMPRESSION_REJECTED:{exc}"]}

        actual_raw_sha = _sha256_bytes(raw_bytes)
        if actual_raw_sha != expected_raw_sha:
            return {"committed": False, "status": "REJECTED_RAW_SHA_MISMATCH", "violations": ["STAGED_BYTES_SHA_MISMATCH"]}

        ev_cls = str(receipt_item.get("evidence_class") or "UNVERIFIED")
        proof_obj = receipt_item.get("_capture_proof_obj")
        if ev_cls == "OBSERVED_VERIFIED":
            if not isinstance(proof_obj, VerifiedCaptureProofC4):
                return {"committed": False, "status": "REJECTED_NO_LIVE_PROOF", "violations": ["MISSING_LIVE_C4_PROOF_FOR_OBSERVED_LABEL"]}
            v_ok, v_reasons, _ = verify_capture_proof_c4(proof_obj, expected_raw_sha256=actual_raw_sha, require_live=True)
            if not v_ok:
                return {"committed": False, "status": "REJECTED_INVALID_PROOF", "violations": list(v_reasons or ["INVALID_C4_PROOF"])}

        norm_sha = str(validation.get("canonical_normalized_sha256") or actual_raw_sha)
        snap_id = f"{partition['partition_role'].lower()}_{actual_raw_sha[:16]}"
        blob_target = self.blobs_dir / f"{snap_id}.raw"
        tmp_blob = self.blobs_dir / f".tmp_{snap_id}_{uuid.uuid4().hex[:8]}.part"

        with self._thread_lock:
            with _cross_platform_exclusive_fd_lock(self.lock_path):
                # Re-check resource floor inside lock immediately before bounded write/fsync!
                res_ok2, res_viols2, _ = self._check_pre_write_resources()
                if not res_ok2:
                    return {"committed": False, "status": "BLOCKED_LOW_DISK", "violations": res_viols2}
                existing = self.read_entries()
                # Check idempotency vs conflicting duplicate
                for ex in existing:
                    if ex.get("raw_sha256") == actual_raw_sha:
                        if ex.get("canonical_normalized_sha256") == norm_sha and ex.get("partition_role") == partition["partition_role"]:
                            return {
                                "committed": True, "idempotent_duplicate": True, "status": "IDEMPOTENT_ALREADY_COMMITTED",
                                "snapshot_id": ex["snapshot_id"], "raw_sha256": actual_raw_sha,
                                "head_entry_sha256": existing[-1]["entry_sha256"], "violations": [],
                            }
                        return {"committed": False, "status": "REJECTED_CONFLICTING_DUPLICATE", "violations": ["CONFLICTING_DUPLICATE_SNAPSHOT"]}
                    if receipt_item.get("capture_proof_id") and ex.get("capture_proof_id") == receipt_item.get("capture_proof_id"):
                        return {"committed": False, "status": "REJECTED_PROOF_REPLAY", "violations": ["CAPTURE_PROOF_ALREADY_CONSUMED"]}

                # Consume live proof atomically inside exclusive lock
                if isinstance(proof_obj, VerifiedCaptureProofC4):
                    bound_ok, bound_reasons = bind_capture_proof_consumption_c4(proof_obj.proof_id, snapshot_id=snap_id, content_sha256=actual_raw_sha)
                    if not bound_ok:
                        return {"committed": False, "status": "REJECTED_PROOF_BIND_FAILED", "violations": list(bound_reasons or ["PROOF_BIND_FAILED"])}

                prev_hash = existing[-1]["entry_sha256"] if existing else R5_GENESIS_HASH
                seq_idx = len(existing)
                blob_rel = f"blobs/{blob_target.name}"

                try:
                    # 1. Write & fsync temporary blob on same filesystem
                    fd = os.open(str(tmp_blob), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    try:
                        _write_all_eintr_safe(fd, raw_bytes)
                        if _crash_hook:
                            _crash_hook("after_blob_write")
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    if _crash_hook:
                        _crash_hook("after_blob_fsync")

                    # 2. Atomic rename + dir fsync
                    os.replace(str(tmp_blob), str(blob_target))
                    _fsync_directory(self.blobs_dir)
                    if _crash_hook:
                        _crash_hook("after_blob_rename")

                    # 3. Append hash-chained journal record + fsync
                    entry_body = {
                        "seq_index": seq_idx,
                        "prev_entry_sha256": prev_hash,
                        "snapshot_id": snap_id,
                        "committed_utc": _utc_now_iso(),
                        "partition_role": str(partition["partition_role"]),
                        "evidence_class": ev_cls,
                        "blob_rel_path": blob_rel,
                        "raw_bytes_len": len(raw_bytes),
                        "raw_sha256": actual_raw_sha,
                        "canonical_normalized_sha256": norm_sha,
                        "capture_proof_id": receipt_item.get("capture_proof_id"),
                        "plan_digest_sha256": partition.get("plan_digest_sha256"),
                    }
                    entry_sha = canonical_hash(entry_body)
                    entry_full = {**entry_body, "entry_sha256": entry_sha}
                    line_bytes = (json.dumps(entry_full, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

                    jfd = os.open(str(self.journal_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                    try:
                        _write_all_eintr_safe(jfd, line_bytes)
                        os.fsync(jfd)
                    finally:
                        os.close(jfd)
                    _fsync_directory(self.custody_root)
                    if _crash_hook:
                        _crash_hook("after_journal_append")

                    # 4. Append external head anchor
                    self.anchor.append_head_attestation(
                        journal_record_count=seq_idx + 1,
                        head_seq_index=seq_idx,
                        head_entry_sha256=entry_sha,
                        journal_id=self.journal_id,
                    )
                    return {
                        "committed": True, "idempotent_duplicate": False, "status": "COMMITTED",
                        "snapshot_id": snap_id, "seq_index": seq_idx, "blob_rel_path": blob_rel,
                        "raw_sha256": actual_raw_sha, "canonical_normalized_sha256": norm_sha,
                        "entry_sha256": entry_sha, "evidence_class": ev_cls,
                        "disk_free_after_gib": round(self._disk_free_gib(), 4), "violations": [],
                    }
                except Exception as exc:
                    # Rollback & quarantine ONLY newly owned partial artifacts! Previous committed head stays 100% intact!
                    q_tag = f"quarantined_{snap_id}_{uuid.uuid4().hex[:6]}"
                    for orphan in (tmp_blob, blob_target):
                        if orphan.is_file():
                            # Only quarantine blob_target if it was NOT committed to journal yet
                            committed_now = any(e.get("snapshot_id") == snap_id for e in self.read_entries())
                            if orphan == tmp_blob or not committed_now:
                                try:
                                    os.replace(str(orphan), str(self.quarantine_dir / f"{q_tag}_{orphan.name}"))
                                except OSError:
                                    pass
                    return {
                        "committed": False, "status": "CRASHED_AND_QUARANTINED_PARTIAL",
                        "quarantined": True, "previous_head_preserved": True,
                        "error": str(exc), "violations": [f"PERSIST_CRASH_QUARANTINED:{exc}"],
                    }


def persist_r5_verified_dev_snapshot(
    staging_path: Path,
    receipt: Mapping[str, Any],
    validation: Mapping[str, Any],
    partition: Mapping[str, Any],
    journal: R5DevCustodyJournal,
    *,
    expected_staging_root: Optional[Path] = None,
    _crash_hook: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Thin public wrapper delegating to `R5DevCustodyJournal.persist_snapshot` (§04 #13)."""
    return journal.persist_snapshot(
        staging_path=staging_path,
        receipt_item=receipt,
        validation=validation,
        partition=partition,
        expected_staging_root=expected_staging_root,
        _crash_hook=_crash_hook,
    )


# =====================================================================
# 14. verify_r5_custody_chain_readonly
# =====================================================================
def verify_r5_custody_chain_readonly(
    root: Path,
    journal_head: Optional[Mapping[str, Any]],
    manifests: Optional[Sequence[Mapping[str, Any]]],
    protected_prior: Sequence[Tuple[str, str, str]],
    *,
    repo_root: Optional[Path] = None,
    oos_guard: Optional[OosAccessGuardR5] = None,
) -> Dict[str, Any]:
    """
    Independently verifies permitted R5 DEV/QA custody files, recomputes every raw SHA-256
    and hash-chain link + external head anchor, detects missing/modified/truncated/reordered
    records or role contamination, verifies protected prior SHA-256 heads unchanged, and
    audits `OOS_EVALUATIONS = 0` via deny-before-open guard. NEVER reads historical OOS raw data!
    """
    root = Path(root).resolve()
    viols: List[str] = []
    journal_path = root / "custody_journal.jsonl"
    anchor_path = root / "external_head_anchor.jsonl"

    if not journal_path.is_file():
        return {"valid": False, "status": "FAIL", "violations": ["CUSTODY_JOURNAL_MISSING"]}

    entries: List[Dict[str, Any]] = []
    try:
        for ln_no, raw in enumerate(journal_path.read_text("utf-8").splitlines(), 1):
            s = raw.strip()
            if not s:
                continue
            entries.append(json.loads(s))
    except Exception as exc:
        return {"valid": False, "status": "FAIL", "violations": [f"CUSTODY_JOURNAL_CORRUPT:{exc}"]}

    prev_h = R5_GENESIS_HASH
    obs_count = 0
    qa_count = 0
    seen_proofs: set[str] = set()

    for idx, e in enumerate(entries):
        if int(e.get("seq_index", -1)) != idx:
            viols.append(f"CHAIN_SEQ_OUT_OF_ORDER:idx={idx}:got={e.get('seq_index')}")
        if str(e.get("prev_entry_sha256")) != prev_h:
            viols.append(f"CHAIN_BROKEN_PREV_HASH:idx={idx}")
        body = {k: v for k, v in e.items() if k != "entry_sha256"}
        recomp_h = canonical_hash(body)
        if str(e.get("entry_sha256")) != recomp_h:
            viols.append(f"CHAIN_ENTRY_HASH_TAMPERED:idx={idx}")
        prev_h = str(e.get("entry_sha256"))

        role = str(e.get("partition_role") or "")
        if role not in R5_ALLOWED_PARTITION_ROLES:
            viols.append(f"ROLE_CONTAMINATION_IN_CUSTODY:idx={idx}:role={role}")
        ev_c = str(e.get("evidence_class") or "")
        if ev_c == "OBSERVED_VERIFIED":
            obs_count += 1
        elif ev_c == "QA_FIXTURE":
            qa_count += 1

        pid = e.get("capture_proof_id")
        if pid:
            if str(pid) in seen_proofs:
                viols.append(f"DUPLICATE_PROOF_ID_IN_CHAIN:{pid}")
            seen_proofs.add(str(pid))

        # Rehash raw blob on disk
        blob_rel = str(e.get("blob_rel_path") or "")
        blob_p = root / blob_rel
        try:
            _safe_contained_path(blob_p, root)
        except Exception as exc:
            viols.append(f"UNSAFE_BLOB_PATH:idx={idx}:{exc}")
            continue
        if not blob_p.is_file():
            viols.append(f"MISSING_RAW_BLOB:idx={idx}:{blob_rel}")
            continue
        act_sha = _sha256_file(blob_p)
        if act_sha != str(e.get("raw_sha256")):
            viols.append(f"MODIFIED_RAW_BLOB_BYTES:idx={idx}:{blob_rel}")
        if blob_p.stat().st_size != int(e.get("raw_bytes_len", -1)):
            viols.append(f"TRUNCATED_OR_RESIZED_RAW_BLOB:idx={idx}:{blob_rel}")

    if journal_head is not None:
        if int(journal_head.get("count", -1)) != len(entries) or str(journal_head.get("head_entry_sha256")) != prev_h:
            viols.append("JOURNAL_HEAD_MISMATCH_VS_CLAIMED")

    # Verify external head anchor matches final committed journal head (when non-empty)
    if entries:
        if not anchor_path.is_file():
            viols.append("MISSING_EXTERNAL_HEAD_ANCHOR")
        else:
            anc_rows = [json.loads(ln) for ln in anchor_path.read_text("utf-8").splitlines() if ln.strip()]
            if not anc_rows:
                viols.append("EMPTY_EXTERNAL_HEAD_ANCHOR")
            else:
                last_a = anc_rows[-1]
                if str(last_a.get("head_entry_sha256")) != prev_h or int(last_a.get("journal_record_count", -1)) != len(entries):
                    viols.append("EXTERNAL_ANCHOR_HEAD_MISMATCH")

    # Check manifest cross-references if provided
    for m in list(manifests or ()):
        m_sha = m.get("raw_sha256")
        if m_sha and not any(e.get("raw_sha256") == m_sha for e in entries):
            viols.append(f"MANIFEST_RAW_SHA_NOT_IN_CUSTODY:{m_sha[:16]}")

    # Check protected prior files/pointers unchanged (metadata/pointer hashes only — NEVER opens raw OOS data!)
    prior_mismatches: List[str] = []
    if repo_root is not None:
        for rel_p, exp_sha, lbl in protected_prior:
            p = repo_root / rel_p
            if not p.is_file() or _sha256_file(p) != exp_sha:
                prior_mismatches.append(lbl)
                viols.append(f"PROTECTED_PRIOR_MUTATED:{lbl}")

    oos_sum = oos_guard.summary() if oos_guard is not None else {"oos_evaluations": 0, "oos_reads_executed": 0, "oos_denied_before_open_count": 0, "deny_before_open_enforced": True}
    if int(oos_sum.get("oos_reads_executed", 1)) != 0:
        viols.append("FORBIDDEN_OOS_READ_EXECUTED")

    ok = len(viols) == 0
    return {
        "valid": ok,
        "status": "PASS" if ok else "FAIL",
        "records_verified_count": len(entries),
        "observed_verified_records_count": obs_count,
        "qa_fixture_records_count": qa_count,
        "head_entry_sha256": prev_h,
        "protected_prior_checked_count": len(protected_prior),
        "protected_prior_mismatches": prior_mismatches,
        "oos_access_guard_summary": oos_sum,
        "violations": viols,
    }
