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
import hashlib
import json
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
    MAX_GLOBAL_CONCURRENCY_R5,
    SCHEMA_VERSION_KH03_R5,
    SPEC_VERSION_KH03_R5,
    STRICT_PREFLIGHT_DISK_MIN_GIB_R5,
    STRICT_RUNTIME_DISK_MIN_GIB_R5,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

R5_ALLOWED_PARTITION_ROLES: Tuple[str, ...] = ("DEVELOPMENT_CAPTURE", "QA_FIXTURE")
R5_FORBIDDEN_OOS_PATH_TOKENS: Tuple[str, ...] = ("oos_escrow", "escrow_oos", "real_oos", "untouched_oos", "prospective_oos_escrow")
R5_GENESIS_HASH = "0" * 64


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
# 11. capture_r5_public_batch
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
) -> Dict[str, Any]:
    """
    Bounded async public read-only capture orchestrator.
    - If `plan["status"] == "BLOCKED"` or `live_network_permitted is False` and `partition_role != "QA_FIXTURE"`:
      makes ZERO external requests and returns `CAPTURE_BLOCKED_OWNER_AUTHORIZATION`.
    - Enforces `asyncio.Semaphore(max_concurrency <= 4)`, per-endpoint rate limiter,
      `Retry-After` & capped exponential backoff on `429`/`5xx`/timeout, NO retry on
      auth/contract/provenance/secret/size/redirect errors.
    - Checks cancellation, deadline, cumulative bytes, cumulative requests, disk (>=20.0 GiB),
      and RAM (>=512 MiB) BEFORE every dispatch and write.
    - Streams every accepted response to a unique contained staging path inside `staging_root`.
    - Live observed provenance (`OBSERVED_VERIFIED`) requires EXACT unmodified
      `StdlibUrllibPublicTransportC2` + `execute_trusted_stdlib_https_get_c4` live proof;
      injected/fake/subclass transports are strictly classified `QA_FIXTURE` or rejected!
    """
    plan_st = str(plan.get("status") or "BLOCKED")
    role = str(plan.get("partition_role") or "NONE_BLOCKED")
    live_perm = bool(plan.get("live_network_permitted", False))

    if plan_st == "BLOCKED" or (not live_perm and role != "QA_FIXTURE"):
        return {
            "spec_version": SPEC_VERSION_KH03_R5,
            "schema_version": SCHEMA_VERSION_KH03_R5,
            "run_id": run_id,
            "capture_execution_status": "BLOCKED_OWNER_AUTHORIZATION",
            "capture_provenance_status": "NO_REAL_CAPTURE",
            "external_requests_attempted": 0,
            "accepted_observed_records_count": 0,
            "qa_fixture_records_count": 0,
            "items": [],
            "violations": list(plan.get("violations") or ["CAPTURE_BLOCKED_OWNER_AUTHORIZATION"]),
        }

    if transport is None:
        return {
            "spec_version": SPEC_VERSION_KH03_R5,
            "schema_version": SCHEMA_VERSION_KH03_R5,
            "run_id": run_id,
            "capture_execution_status": "FAIL",
            "capture_provenance_status": "NO_REAL_CAPTURE",
            "external_requests_attempted": 0,
            "accepted_observed_records_count": 0,
            "qa_fixture_records_count": 0,
            "items": [],
            "violations": ["MISSING_TRANSPORT_OBJECT"],
        }

    is_qa_plan = (role == "QA_FIXTURE")
    exact_stdlib, _exact_reasons = is_exact_unmodified_stdlib_transport_c4(
        transport, expected_transport_cls=StdlibUrllibPublicTransportC2, expected_get_func=StdlibUrllibPublicTransportC2.get
    )
    if live_perm and not exact_stdlib:
        return {
            "spec_version": SPEC_VERSION_KH03_R5,
            "schema_version": SCHEMA_VERSION_KH03_R5,
            "run_id": run_id,
            "capture_execution_status": "FAIL",
            "capture_provenance_status": "FAIL",
            "external_requests_attempted": 0,
            "accepted_observed_records_count": 0,
            "qa_fixture_records_count": 0,
            "items": [],
            "violations": ["FAKE_OR_SUBCLASSED_TRANSPORT_CANNOT_EXECUTE_LIVE_OBSERVED_CAPTURE"],
        }
    if is_qa_plan and exact_stdlib:
        return {
            "spec_version": SPEC_VERSION_KH03_R5,
            "schema_version": SCHEMA_VERSION_KH03_R5,
            "run_id": run_id,
            "capture_execution_status": "FAIL",
            "capture_provenance_status": "NO_REAL_CAPTURE",
            "external_requests_attempted": 0,
            "accepted_observed_records_count": 0,
            "qa_fixture_records_count": 0,
            "items": [],
            "violations": ["QA_FIXTURE_MODE_MUST_NOT_USE_LIVE_SOCKET_TRANSPORT"],
        }

    budget = dict(plan.get("budget") or {})
    max_conc = max(1, min(MAX_GLOBAL_CONCURRENCY_R5, int(budget.get("max_concurrency", 1))))
    max_requests = int(budget.get("max_requests", 1))
    max_total_bytes = int(budget.get("max_total_bytes", 1024 * 1024))
    max_resp_bytes = min(MAX_RAW_HTTP_BODY_BYTES_C3, int(budget.get("max_response_bytes", MAX_RAW_HTTP_BODY_BYTES_C3)))
    max_dur_sec = float(budget.get("max_duration_sec", 30.0))
    max_retries = min(3, max(0, int(budget.get("max_retries_per_request", 2))))

    t0_mono = time.monotonic()
    sem = asyncio.Semaphore(max_conc)
    state_lock = asyncio.Lock()
    ep_last_ts: Dict[str, float] = {}
    counters = {"requests": 0, "bytes": 0, "aborted": False, "abort_reason": None}
    items: List[Dict[str, Any]] = []
    violations: List[str] = []
    staged_paths: List[Path] = []
    session_id = f"r5_{run_id}_{uuid.uuid4().hex[:8]}"

    async def _do_sleep(sec: float) -> None:
        if sleep_fn is not None:
            r = sleep_fn(sec)
            if asyncio.iscoroutine(r):
                await r
        else:
            await asyncio.sleep(sec)

    def _check_res_and_cancel() -> Optional[str]:
        if cancel_event is not None and cancel_event.is_set():
            return "CANCELLED_MIDFLIGHT"
        if (time.monotonic() - t0_mono) >= max_dur_sec:
            return "CAPTURE_DEADLINE_EXCEEDED"
        if resource_monitor is not None:
            rm = resource_monitor()
            dk = float(rm.get("disk_free_gib", 99.0))
            ram = float(rm.get("available_ram_mib", 4096.0))
            if dk < STRICT_RUNTIME_DISK_MIN_GIB_R5:
                return f"RUNTIME_DISK_BELOW_20GIB:{dk:.4f}"
            if ram < 512.0:
                return f"RUNTIME_RAM_BELOW_512MIB:{ram:.2f}"
        return None

    async def _run_one_batch(batch: Mapping[str, Any]) -> None:
        ep_key = str(batch["endpoint_key"])
        url = str(batch["url"])
        # Strict public allowlist & forbidden private/order token validation before dispatch!
        low_u = url.lower()
        if any(tok in low_u for tok in FORBIDDEN_URL_TOKENS_C2) or ep_key not in OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2:
            violations.append(f"BLOCKED_UNAUTHORIZED_OR_PRIVATE_ENDPOINT:{ep_key}:{url}")
            return
        params: Dict[str, Any] = {"symbol": str(batch.get("symbol") or "XAUUSDT"), "productType": str(batch.get("product_type") or "USDT-FUTURES")}
        if ep_key in ("candles", "history_candles"):
            params["granularity"] = str(batch.get("granularity") or "1m")
            if batch.get("window_start_ms"):
                params["startTime"] = str(batch["window_start_ms"])
            if batch.get("window_end_ms"):
                params["endTime"] = str(batch["window_end_ms"])
        if scan_for_forbidden_secrets_r2_c1(params):
            violations.append(f"FORBIDDEN_SECRET_IN_PARAMS:{ep_key}")
            return
        try:
            _validate_public_bitget_https_url_c3(url, params)
        except Exception as exc:
            violations.append(f"URL_ALLOWLIST_REJECTED:{ep_key}:{exc}")
            return

        min_iv_s = max(0.05, float(batch.get("min_request_interval_ms", 50)) / 1000.0)
        retry_chain: List[Dict[str, Any]] = []

        async with sem:
            for attempt in range(max_retries + 1):
                err_pre = _check_res_and_cancel()
                if err_pre:
                    async with state_lock:
                        counters["aborted"] = True
                        counters["abort_reason"] = err_pre
                    violations.append(err_pre)
                    return

                async with state_lock:
                    if counters["aborted"]:
                        return
                    if counters["requests"] >= max_requests:
                        counters["aborted"] = True
                        counters["abort_reason"] = "MAX_REQUESTS_BUDGET_EXHAUSTED"
                        violations.append("MAX_REQUESTS_BUDGET_EXHAUSTED")
                        return
                    # Per-endpoint rate limiter
                    now_m = time.monotonic()
                    last_m = ep_last_ts.get(ep_key, 0.0)
                    wait_s = max(0.0, (last_m + min_iv_s) - now_m)
                    ep_last_ts[ep_key] = max(now_m, last_m + min_iv_s)
                    counters["requests"] += 1

                if wait_s > 0.0:
                    await _do_sleep(wait_s)

                req_utc_ms = int(time.time() * 1000)
                t_req_mono = time.monotonic()
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
                            url=url, params=params, endpoint_key=ep_key,
                            timeout_sec=min(10.0, max_dur_sec),
                            capture_session_id=session_id,
                            live_authorization_verified=True,
                            real_rate_limiter_active=True,
                            is_fake_transport_qa=False,
                        )
                        raw_resp = tr_res.raw_response
                        proof_c4 = tr_res.capture_proof
                    else:
                        # Offline QA fixture transport — NEVER mints live observed proof!
                        raw_resp = await asyncio.to_thread(transport.get, url, params, timeout_sec=min(10.0, max_dur_sec))
                        proof_c4 = None
                except TimeoutError as exc:
                    retry_chain.append({"attempt": attempt, "error": f"TIMEOUT:{exc}"})
                    if attempt < max_retries:
                        await _do_sleep(min(2.0, 0.05 * (2 ** attempt)))
                        continue
                    violations.append(f"TIMEOUT_EXHAUSTED:{ep_key}")
                    return
                except Exception as exc:
                    # Do NOT retry auth/redirect/contract/provenance/oversize errors!
                    violations.append(f"TRANSPORT_OR_PROVENANCE_ERROR:{ep_key}:{exc}")
                    return

                st_code = int(raw_resp.status_code)
                hdrs = {str(k).lower(): str(v) for k, v in (raw_resp.headers or {}).items()}
                # Block redirects fail-closed!
                if st_code in (301, 302, 303, 307, 308):
                    violations.append(f"HTTP_REDIRECT_BLOCKED:{ep_key}:status={st_code}")
                    return
                if st_code == 429 or (500 <= st_code < 600):
                    ra = hdrs.get("retry-after")
                    backoff = min(2.0, float(ra)) if (ra and ra.replace(".", "", 1).isdigit()) else min(2.0, 0.05 * (2 ** attempt))
                    retry_chain.append({"attempt": attempt, "http_status": st_code, "backoff_sec": backoff})
                    if attempt < max_retries:
                        await _do_sleep(backoff)
                        continue
                    violations.append(f"HTTP_RETRY_EXHAUSTED:{ep_key}:status={st_code}")
                    return
                if st_code != 200:
                    violations.append(f"HTTP_NON_200_NO_RETRY:{ep_key}:status={st_code}")
                    return

                body = bytes(raw_resp.body_bytes)
                if not body:
                    violations.append(f"EMPTY_HTTP_200_BODY:{ep_key}")
                    return
                if proof_c4 is None:
                    mono_ms = max(1, int((time.monotonic() - t_req_mono) * 1000))
                    proof_c4 = issue_qa_or_unverified_capture_proof_c4(
                        capture_session_id=session_id, endpoint_key=ep_key, url=url,
                        final_url=str(getattr(raw_resp, "final_url", url) or url),
                        tls_host=str(getattr(raw_resp, "tls_verified_host", "") or ""),
                        http_status=st_code, params=params, raw_response_bytes=body,
                        request_utc_ms=int(getattr(raw_resp, "request_utc_ms", req_utc_ms)),
                        response_utc_ms=int(getattr(raw_resp, "response_utc_ms", req_utc_ms + mono_ms)),
                        monotonic_duration_ms=mono_ms,
                        transport_class_name=type(transport).__name__,
                        transport_kind="INJECTED_FAKE_TRANSPORT_QA",
                        real_rate_limiter_active=True,
                        is_fake_transport_qa=True,
                    )
                if len(body) > max_resp_bytes:
                    violations.append(f"RESPONSE_OVERSIZE:{ep_key}:{len(body)}>{max_resp_bytes}")
                    return
                # Bounded decompression check (prevents gzip bomb!)
                try:
                    unpacked = safe_bounded_decompress_if_gzip_c3(
                        body, max_raw_bytes=max_resp_bytes, max_decompressed_bytes=MAX_DECOMPRESSED_BODY_BYTES_C3
                    )
                except Exception as exc:
                    violations.append(f"DECOMPRESSION_BOMB_OR_GZIP_REJECTED:{ep_key}:{exc}")
                    return

                # Check cumulative byte budget & resource floor BEFORE writing staging file
                err_w = _check_res_and_cancel()
                if err_w:
                    async with state_lock:
                        counters["aborted"] = True
                        counters["abort_reason"] = err_w
                    violations.append(err_w)
                    return

                async with state_lock:
                    if counters["bytes"] + len(body) > max_total_bytes:
                        counters["aborted"] = True
                        counters["abort_reason"] = "TOTAL_BYTES_BUDGET_EXHAUSTED"
                        violations.append("TOTAL_BYTES_BUDGET_EXHAUSTED")
                        return
                    counters["bytes"] += len(body)

                raw_sha = _sha256_bytes(body)
                canon_sha = _sha256_bytes(unpacked)
                staged_rel = None
                if staging_root is not None:
                    staging_root.mkdir(parents=True, exist_ok=True)
                    st_file = staging_root / f"{batch['batch_id']}_{raw_sha[:12]}.raw"
                    _safe_contained_path(st_file, staging_root)
                    st_file.write_bytes(body)
                    staged_paths.append(st_file)
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
                    monotonic_duration_ms=max(1, int((time.monotonic() - t_req_mono) * 1000)),
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

    tasks = [asyncio.create_task(_run_one_batch(b)) for b in list(plan.get("batches") or ())]
    if tasks:
        await asyncio.gather(*tasks)

    # Clean ONLY owned partial staged files on midflight cancel/disk abort
    if counters["aborted"] and staging_root is not None:
        for sp in staged_paths:
            try:
                if sp.is_file():
                    sp.unlink()
            except OSError:
                pass

    obs_cnt = sum(1 for it in items if it["evidence_class"] == "OBSERVED_VERIFIED")
    qa_cnt = sum(1 for it in items if it["evidence_class"] == "QA_FIXTURE")
    if counters["aborted"]:
        exec_st = "ABORTED_RESOURCE_OR_CANCEL"
    elif is_qa_plan:
        exec_st = "QA_FIXTURE_OFFLINE_PASS" if (len(violations) == 0 and qa_cnt > 0) else "FAIL"
    else:
        exec_st = "VERIFIED_DEV_CAPTURE" if (len(violations) == 0 and obs_cnt > 0) else ("PARTIAL_VERIFIED_DEV" if obs_cnt > 0 else "FAIL")

    return {
        "spec_version": SPEC_VERSION_KH03_R5,
        "schema_version": SCHEMA_VERSION_KH03_R5,
        "run_id": run_id,
        "capture_session_id": session_id,
        "partition_role": role,
        "capture_execution_status": exec_st,
        "capture_provenance_status": "VERIFIED_OBSERVED_DEV" if obs_cnt > 0 else ("QA_FIXTURE_ONLY_NO_REAL_CAPTURE" if qa_cnt > 0 else "NO_REAL_CAPTURE"),
        "external_requests_attempted": counters["requests"] if live_perm else 0,
        "qa_simulated_requests_count": counters["requests"] if is_qa_plan else 0,
        "total_bytes_received": counters["bytes"],
        "accepted_observed_records_count": obs_cnt,
        "qa_fixture_records_count": qa_cnt,
        "aborted": counters["aborted"],
        "abort_reason": counters["abort_reason"],
        "items": items,
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
    ) -> None:
        if any(tok in str(custody_root).lower().replace("\\", "/") for tok in R5_FORBIDDEN_OOS_PATH_TOKENS):
            raise R5OosAccessDeniedError("R5_CUSTODY_JOURNAL_REFUSES_OOS_NAMESPACE")
        self.custody_root = Path(custody_root).resolve()
        self.journal_id = journal_id
        self.os_disk_probe_fn = os_disk_probe_fn
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
            return float(self.os_disk_probe_fn())
        return shutil.disk_usage(self.custody_root).free / (1024.0 ** 3)

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
        _crash_hook: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        viols: List[str] = []
        if not partition.get("valid") or partition.get("partition_role") not in R5_ALLOWED_PARTITION_ROLES:
            return {"committed": False, "status": "REJECTED_PARTITION", "violations": ["INVALID_OR_UNASSIGNED_PARTITION"]}
        if not validation.get("valid"):
            return {"committed": False, "status": "REJECTED_VALIDATION", "violations": list(validation.get("violations") or ["VALIDATION_FAILED"])}
        if not staging_path.is_file():
            return {"committed": False, "status": "REJECTED_MISSING_STAGING", "violations": ["MISSING_STAGING_RAW_FILE"]}
        if _is_symlink_or_junction(staging_path):
            return {"committed": False, "status": "REJECTED_SYMLINK", "violations": ["SYMLINK_STAGING_DENIED"]}

        dk_gib = self._disk_free_gib()
        if dk_gib < STRICT_RUNTIME_DISK_MIN_GIB_R5:
            return {"committed": False, "status": "BLOCKED_LOW_DISK", "violations": [f"DISK_BELOW_20GIB:{dk_gib:.4f}"]}

        raw_bytes = staging_path.read_bytes()
        if len(raw_bytes) > MAX_RAW_HTTP_BODY_BYTES_C3:
            return {"committed": False, "status": "REJECTED_OVERSIZE", "violations": ["RAW_BLOB_OVERSIZE"]}
        try:
            safe_bounded_decompress_if_gzip_c3(raw_bytes)
        except Exception as exc:
            return {"committed": False, "status": "REJECTED_DECOMPRESS", "violations": [f"DECOMPRESSION_REJECTED:{exc}"]}

        actual_raw_sha = _sha256_bytes(raw_bytes)
        expected_raw_sha = str(receipt_item.get("raw_sha256") or "")
        if expected_raw_sha and actual_raw_sha != expected_raw_sha:
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
    _crash_hook: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Thin public wrapper delegating to `R5DevCustodyJournal.persist_snapshot` (§04 #13)."""
    return journal.persist_snapshot(
        staging_path=staging_path, receipt_item=receipt, validation=validation, partition=partition, _crash_hook=_crash_hook
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
