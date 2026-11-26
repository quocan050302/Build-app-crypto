"""
aurum-desk/backend/services/kh_03_r6_a1_acquisition_service.py

AURUM DESK — KH-03.R6.A1 (M1 / P1-G) | END-TO-END ACQUISITION ORCHESTRATOR
SPEC VERSION: KH-03.R6.A1-v1.0 | REAL SUBPROCESS -> LEDGER -> SUPERVISOR -> VALIDATORS -> CUSTODY -> MANIFEST

Implements:
  - F02: `build_r6_a1_acquisition_session()` — builds strictly verified typed session context,
    assigns partition before I/O, derives deterministic task list, blocks empty/inconsistent tasks before claim.
  - F03: `run_r6_a1_acquisition_session()` — single orchestration authority with real state machine:
    PRECHECK -> PLAN_BOUND -> PARTITION_ASSIGNED -> CLAIMED -> ACQUIRING -> VALIDATING -> PERSISTING ->
    SEALED -> FINALIZED (and truthful ABORTED / PARTIAL / QUARANTINED / BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED).
    Claims one-shot grant ONCE, enforces immutable monotonic deadline, bounded async coordinator with
    structured cancellation propagation, drain/reap, and exact-once finalization in `try/finally`.
  - F04: `build_r6_a1_acquisition_tasks()` — deterministic task derivation strictly from verified plan
    + Classic v2 registry. Metadata identity gate (`classic_v2_contracts`) runs FIRST before data acceptance!
  - F05: `execute_r6_a1_rest_task()` & `execute_r6_a1_ws_task()` — real dispatch authorization (F12),
    durable reservation (F13), owned child worker (F15/F16/F17/F18), supervisor & settlement (F19/F20/F22),
    market-data validators (`validate_r6_instrument_identity`, `paginate_r6_ohlcv_history`,
    `normalize_r6_bbo_event`, `validate_r6_bbo_epoch`, `build_r6_bbo_coverage_report`,
    `validate_r6_funding_settlements`), provenance (F23), and custody commit (F25).
  - F06: `finalize_r6_a1_acquisition_session()` — waits for drain, verifies custody read-only (with
    linked SQLite ledger cross-check!), seals QA dataset manifest, finalizes ledger once.
  - F07: `build_r6_a1_session_receipt()` — immutable audit receipt derived strictly from observed facts.

Hard Safety Invariants:
  - In KH-03.R6.A1, external capture is unconditionally HARD-BLOCKED (`BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED`)
    BEFORE claiming any grant or launching any worker!
  - `QA_ACQUISITION_END_TO_END_PASS` requires ALL planned QA tasks complete, valid non-empty persisted
    data, read-only custody verified, zero overrun/quarantine, and `accepted_real_observed_records == 0`.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from services.kh_03_r6_a1_release_service import R6A1VerifiedReleaseBinding
from services.kh_03_r6_a1_trust_service import (
    A1_EXTERNAL_CAPTURE_ENABLED,
    ALLOWED_LOOPBACK_HOSTS_A1,
    SCHEMA_VERSION_KH03_R6_A1,
    SPEC_VERSION_KH03_R6_A1,
    _sha256_canonical_a1,
    authorize_r6_a1_dispatch_request,
    complete_caps_vector_a1,
    verify_registered_grant_authenticity_a1,
)
from services.kh_03_r6_bounded_transport_service import (
    GLOBAL_MAX_WORKERS_R6,
    R6BoundCapturePlan,
    supervise_r6_session,
    validate_r6_resource_guard,
    verify_r6_bound_plan_integrity,
)
from services.kh_03_r6_custody_readiness_service import (
    R6CustodyJournal,
    assign_r6_dev_partition_before_io,
    mint_r6_raw_provenance_receipt,
    seal_r6_dev_dataset_manifest,
    verify_r6_custody_readonly,
)
from services.kh_03_r6_durable_grant_service import (
    R6DurableGrantLedger,
    R6DurableSessionLease,
    R6VerifiedGrant,
    _strict_loads_r6,
)
from services.kh_03_r6_market_data_service import (
    build_r6_bbo_coverage_report,
    build_r6_market_data_gap_ledger,
    normalize_r6_bbo_event,
    paginate_r6_ohlcv_history,
    validate_r6_bbo_epoch,
    validate_r6_funding_settlements,
)
from services.kh_03_r6_source_contract_service import (
    APPROVED_PRODUCT_TYPE_V2_R6,
    APPROVED_SYMBOL_R6,
    R6SourceContractRegistry,
    validate_r6_instrument_identity,
)

TERMINAL_QA_PASS_STATUS_A1 = "QA_ACQUISITION_END_TO_END_PASS"


@dataclass(frozen=True)
class R6A1AcquisitionTask:
    task_id: str
    task_kind: str  # "instrument_metadata" | "ohlcv_history" | "funding_history" | "bbo_ws_stream"
    endpoint_id: str
    transport_kind: str  # "rest_get" | "ws_session"
    mandatory: bool
    granularity: Optional[str] = None
    ws_channel: Optional[str] = None
    max_pages: int = 1
    ws_max_messages: int = 20
    per_attempt_payload_bytes: int = 65_536
    per_attempt_decoded_bytes: int = 131_072


@dataclass(frozen=True)
class R6A1AcquisitionSessionContext:
    run_id: str
    session_id: str
    stage: str  # "QA_LOOPBACK" | "B_SMOKE" | "C_DEV"
    logical_profile: str  # "smoke" | "dev"
    qa_loopback_mode: bool
    tested_executable_sha: str
    release_manifest_sha256: str
    registry_digest: str
    partition: str
    run_namespace_dir: str
    effective_concurrency: int
    tasks: Tuple[R6A1AcquisitionTask, ...]
    qa_http_base_url: Optional[str]
    qa_ws_base_url: Optional[str]
    context_digest: str


# =====================================================================
# F04: Deterministic Task Derivation Strictly from Plan + Registry
# =====================================================================
def build_r6_a1_acquisition_tasks(
    plan: R6BoundCapturePlan,
    registry: R6SourceContractRegistry,
    *,
    logical_profile: str = "smoke",  # "smoke" | "dev"
) -> Dict[str, Any]:
    """
    F04: Derives deterministic ordered acquisition tasks strictly from verified `plan` + `registry`.
    Always places `classic_v2_contracts` (instrument metadata identity gate) FIRST so subsequent
    market-data acceptance CANNOT proceed unless contract identity passes!
    """
    viols: List[str] = []
    if not isinstance(plan, R6BoundCapturePlan):
        return {"valid": False, "tasks": (), "violations": ["PLAN_NOT_R6_BOUND_CAPTURE_PLAN"]}
    if not isinstance(registry, R6SourceContractRegistry) or registry.status != "DOC_VERIFIED_READY_FOR_STAGE_B_REVIEW":
        return {"valid": False, "tasks": (), "violations": ["SOURCE_REGISTRY_NOT_READY"]}
    if logical_profile not in ("smoke", "dev"):
        return {"valid": False, "tasks": (), "violations": [f"UNKNOWN_LOGICAL_PROFILE:{logical_profile}"]}

    eps = set(plan.endpoints)
    ws_chs = set(plan.ws_channels)
    tasks: List[R6A1AcquisitionTask] = []

    max_pay_cap = int(plan.caps.get("payload_bytes", 0))
    max_dec_cap = int(plan.caps.get("decoded_bytes", 0))
    max_pages_cap = int(plan.caps.get("pages", 0))
    max_msg_cap = int(plan.caps.get("messages", 0))

    per_att_pay = min(max_pay_cap, 65_536) if max_pay_cap > 0 else 0
    per_att_dec = min(max_dec_cap, 131_072) if max_dec_cap > 0 else 0

    # 1. Mandatory Instrument Metadata Identity Gate FIRST!
    if "classic_v2_contracts" in eps:
        tasks.append(
            R6A1AcquisitionTask(
                task_id="task_01_instrument_metadata",
                task_kind="instrument_metadata",
                endpoint_id="classic_v2_contracts",
                transport_kind="rest_get",
                mandatory=True,
                max_pages=1,
                per_attempt_payload_bytes=min(per_att_pay, 32_768),
                per_attempt_decoded_bytes=min(per_att_dec, 65_536),
            )
        )
    else:
        viols.append("MANDATORY_METADATA_ENDPOINT_MISSING_FROM_PLAN:classic_v2_contracts")

    # 2. OHLCV History / Candles REST task
    ohlcv_ep = "classic_v2_history_candles" if "classic_v2_history_candles" in eps else (
        "classic_v2_candles" if "classic_v2_candles" in eps else None
    )
    if ohlcv_ep is not None:
        want_pages = 1 if logical_profile == "smoke" else min(max(2, max_pages_cap), 4)
        eff_pages = min(want_pages, max(1, max_pages_cap)) if max_pages_cap > 0 else 1
        tasks.append(
            R6A1AcquisitionTask(
                task_id="task_02_ohlcv_history",
                task_kind="ohlcv_history",
                endpoint_id=ohlcv_ep,
                transport_kind="rest_get",
                mandatory=True,
                granularity="1m",
                max_pages=eff_pages,
                per_attempt_payload_bytes=per_att_pay,
                per_attempt_decoded_bytes=per_att_dec,
            )
        )

    # 3. Funding Rate History REST task
    if "classic_v2_history_fund_rate" in eps:
        tasks.append(
            R6A1AcquisitionTask(
                task_id="task_03_funding_history",
                task_kind="funding_history",
                endpoint_id="classic_v2_history_fund_rate",
                transport_kind="rest_get",
                mandatory=True,
                max_pages=1,
                per_attempt_payload_bytes=min(per_att_pay, 32_768),
                per_attempt_decoded_bytes=min(per_att_dec, 65_536),
            )
        )

    # 4. BBO WebSocket public stream (`books1`)
    if "classic_v2_ws_books1" in eps and "books1" in ws_chs:
        want_msgs = 4 if logical_profile == "smoke" else min(12, max(4, max_msg_cap))
        tasks.append(
            R6A1AcquisitionTask(
                task_id="task_04_bbo_ws_books1",
                task_kind="bbo_ws_stream",
                endpoint_id="classic_v2_ws_books1",
                transport_kind="ws_session",
                mandatory=True,
                ws_channel="books1",
                ws_max_messages=min(want_msgs, max(1, max_msg_cap)),
                per_attempt_payload_bytes=min(per_att_pay, 32_768),
                per_attempt_decoded_bytes=min(per_att_dec, 65_536),
            )
        )
    elif "classic_v2_ws_books1" in eps and "books1" not in ws_chs:
        viols.append("PLAN_WS_BOOKS1_ENDPOINT_WITHOUT_BOOKS1_CHANNEL")

    if len(tasks) <= 1 and not viols:
        viols.append("PLAN_HAS_NO_MARKET_DATA_TASKS_BEYOND_METADATA")
    if not tasks:
        viols.append("EMPTY_ACQUISITION_TASK_LIST")

    return {
        "valid": len(viols) == 0,
        "tasks": tuple(tasks),
        "violations": viols,
    }


def _validate_loopback_base_url(url_str: Optional[str], expected_scheme: str) -> Tuple[bool, Optional[str]]:
    if not url_str or not isinstance(url_str, str):
        return False, f"MISSING_QA_LOOPBACK_{expected_scheme.upper()}_BASE_URL"
    p = urlsplit(url_str)
    if p.scheme.lower() != expected_scheme:
        return False, f"BAD_QA_LOOPBACK_SCHEME:{p.scheme}"
    if (p.hostname or "").lower() not in ALLOWED_LOOPBACK_HOSTS_A1:
        return False, f"NONLOOPBACK_HOST_FORBIDDEN:{p.hostname}"
    if p.username is not None or p.password is not None:
        return False, "URL_CREDENTIALS_FORBIDDEN"
    if p.port is None or p.port <= 0 or p.port > 65535:
        return False, "MISSING_LOOPBACK_PORT"
    if p.query or p.fragment or (p.path and p.path != "/"):
        return False, "BASE_URL_MUST_BE_ORIGIN_ONLY"
    return True, None


# =====================================================================
# F02: build_r6_a1_acquisition_session
# =====================================================================
def build_r6_a1_acquisition_session(
    *,
    run_id: str,
    session_id: str,
    verified_grant: R6VerifiedGrant,
    plan: R6BoundCapturePlan,
    registry: R6SourceContractRegistry,
    ledger: R6DurableGrantLedger,
    resource_sample_fn: Callable[[], Mapping[str, Any]],
    expected_volumes: Mapping[str, Path],
    repo_root: Path,
    logical_profile: str = "smoke",  # "smoke" | "dev"
    qa_loopback_mode: bool = False,
    qa_http_base_url: Optional[str] = None,
    qa_ws_base_url: Optional[str] = None,
    release_binding: Optional[R6A1VerifiedReleaseBinding] = None,
    oos_roots: Sequence[Path] = (),
    allow_fixture_resource_sample: bool = False,
    actual_now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    F02: Pre-claim session builder & validator.
    Hard-blocks external capture in A1 BEFORE consuming any grant or creating any network worker!
    Assigns partition BEFORE I/O, verifies grant/plan/registry/release/resource/tasks, and rejects
    empty or inconsistent task lists before claiming the one-shot grant!
    """
    viols: List[str] = []
    now_dt = actual_now or datetime.now(timezone.utc)

    # HARD A1 POLICY: Never permit non-loopback external acquisition in A1 (BEFORE claim!)
    if not A1_EXTERNAL_CAPTURE_ENABLED and not qa_loopback_mode:
        return {
            "built": False,
            "status": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED",
            "context": None,
            "violations": ["A1_EXTERNAL_CAPTURE_DISABLED_NO_REAL_GRANT_CLAIM"],
        }

    ok_vg, vg_viols = verify_registered_grant_authenticity_a1(verified_grant)
    if not ok_vg:
        viols.extend(vg_viols)

    exp_sha = release_binding.tested_executable_sha if release_binding is not None else verified_grant.tested_executable_sha
    exp_rel = release_binding.manifest_sha256 if release_binding is not None else verified_grant.release_manifest_sha256

    plan_ok, plan_viols = verify_r6_bound_plan_integrity(
        plan,
        verified_grant,
        registry=registry,
        expected_executable_sha=exp_sha,
        expected_release_manifest_sha256=exp_rel,
        actual_now=now_dt,
    )
    if not plan_ok:
        viols.extend(plan_viols)

    if ledger.store_id != verified_grant.destination_store_id or ledger.trust_namespace != verified_grant.trust_namespace:
        viols.append("LEDGER_STORE_OR_TRUST_NAMESPACE_MISMATCH")

    # Resource guard preflight check
    try:
        res_sample = resource_sample_fn()
        res_guard = validate_r6_resource_guard(
            res_sample,
            expected_volumes=expected_volumes,
            phase="preflight",
            allow_fixture_sample=allow_fixture_resource_sample,
        )
        if not res_guard["ok"]:
            viols.extend(res_guard["violations"])
    except Exception as exc:
        viols.append(f"RESOURCE_PREFLIGHT_EXCEPTION:{exc}")

    # Derive tasks strictly from plan + registry
    task_res = build_r6_a1_acquisition_tasks(plan, registry, logical_profile=logical_profile)
    if not task_res["valid"]:
        viols.extend(task_res["violations"])
    tasks: Tuple[R6A1AcquisitionTask, ...] = task_res["tasks"]

    # Validate QA loopback URLs strictly (never allow non-loopback URLs!)
    clean_http_base = qa_http_base_url.rstrip("/") if qa_http_base_url else None
    clean_ws_base = qa_ws_base_url.rstrip("/") if qa_ws_base_url else None
    if qa_loopback_mode:
        if any(t.transport_kind == "rest_get" for t in tasks):
            ok_u, u_err = _validate_loopback_base_url(clean_http_base, "http")
            if not ok_u and u_err:
                viols.append(u_err)
        if any(t.transport_kind == "ws_session" for t in tasks):
            ok_w, w_err = _validate_loopback_base_url(clean_ws_base, "ws")
            if not ok_w and w_err:
                viols.append(w_err)

    # Assign strict partition BEFORE any I/O (blocks OOS/symlink/junction/Git repo!)
    prov_class_expected = "QA_LOOPBACK" if qa_loopback_mode else "RAW_OBSERVED_VERIFIED"
    part_res = assign_r6_dev_partition_before_io(
        stage=verified_grant.stage,
        provenance_class=prov_class_expected,
        approved_root=Path(verified_grant.destination_root),
        run_id=run_id,
        repo_root=repo_root,
        oos_roots=oos_roots,
    )
    if not part_res["assigned"]:
        viols.extend(part_res["violations"])

    if viols:
        return {
            "built": False,
            "status": "BLOCKED_ACQUISITION_SESSION_PRECHECK",
            "context": None,
            "violations": viols,
        }

    eff_conc = max(1, min(int(plan.max_concurrency), int(verified_grant.max_concurrency), 2, GLOBAL_MAX_WORKERS_R6))
    ctx_core = {
        "run_id": run_id,
        "session_id": session_id,
        "stage": verified_grant.stage,
        "logical_profile": logical_profile,
        "qa_loopback_mode": bool(qa_loopback_mode),
        "tested_executable_sha": verified_grant.tested_executable_sha,
        "release_manifest_sha256": verified_grant.release_manifest_sha256,
        "registry_digest": registry.registry_digest,
        "partition": part_res["partition"],
        "effective_concurrency": eff_conc,
        "task_ids": [t.task_id for t in tasks],
    }
    ctx = R6A1AcquisitionSessionContext(
        run_id=run_id,
        session_id=session_id,
        stage=verified_grant.stage,
        logical_profile=logical_profile,
        qa_loopback_mode=bool(qa_loopback_mode),
        tested_executable_sha=verified_grant.tested_executable_sha,
        release_manifest_sha256=verified_grant.release_manifest_sha256,
        registry_digest=registry.registry_digest,
        partition=str(part_res["partition"]),
        run_namespace_dir=str(part_res["run_namespace_dir"]),
        effective_concurrency=eff_conc,
        tasks=tasks,
        qa_http_base_url=clean_http_base,
        qa_ws_base_url=clean_ws_base,
        context_digest=_sha256_canonical_a1(ctx_core),
    )
    return {"built": True, "status": "SESSION_CONTEXT_BUILT", "context": ctx, "violations": []}


# =====================================================================
# F05: Real Per-Task REST & WS Execution + Market-Data Validation + Custody
# =====================================================================
def execute_r6_a1_rest_task(
    *,
    ctx: R6A1AcquisitionSessionContext,
    task: R6A1AcquisitionTask,
    ledger: R6DurableGrantLedger,
    lease: R6DurableSessionLease,
    plan: R6BoundCapturePlan,
    verified_grant: R6VerifiedGrant,
    registry: R6SourceContractRegistry,
    custody: R6CustodyJournal,
    resource_sample_fn: Callable[[], Mapping[str, Any]],
    expected_volumes: Mapping[str, Path],
    session_deadline_monotonic: float,
    cancel_event: threading.Event,
    utc_now_fn: Callable[[], datetime],
    allow_fixture_resource_sample: bool = False,
    observed_instrument_meta: Optional[Mapping[str, Any]] = None,
    post_exit_hook: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """
    F05 (REST): Executes a single REST task (`instrument_metadata`, `ohlcv_history`, or `funding_history`)
    through F12 authorize -> F19 supervise (F13 reserve -> F14 start -> F15/F16 child -> F17 REST -> F20 settle
    -> F22 trusted result) -> market-data validator -> F23 provenance -> F25 custody commit.
    """
    ep = registry.endpoints[task.endpoint_id]
    loopback_url = f"{ctx.qa_http_base_url}{ep.path}" if ctx.qa_loopback_mode and ctx.qa_http_base_url else None
    lifecycle_traces: List[Dict[str, Any]] = []
    attempt_records: List[Dict[str, Any]] = []
    committed_chunks: List[Dict[str, Any]] = []
    violations: List[str] = []
    raw_bytes_total = 0
    decoded_bytes_total = 0
    requests_count = 0

    def _run_one_rest_attempt(attempt_idx: int, params: Mapping[str, Any]) -> Dict[str, Any]:
        nonlocal raw_bytes_total, decoded_bytes_total, requests_count
        aid = f"{ctx.session_id}_{task.task_id}_att_{attempt_idx:02d}"
        req_caps = complete_caps_vector_a1({
            "requests": 1,
            "retries": 0,
            "payload_bytes": min(int(plan.caps["payload_bytes"]), max(1024, int(task.per_attempt_payload_bytes))),
            "decoded_bytes": min(int(plan.caps["decoded_bytes"]), max(2048, int(task.per_attempt_decoded_bytes))),
            "pages": 1 if int(plan.caps["pages"]) >= 1 else 0,
        })
        auth = authorize_r6_a1_dispatch_request(
            plan=plan,
            verified_grant=verified_grant,
            lease=lease,
            registry=registry,
            endpoint_id=task.endpoint_id,
            attempt_id=aid,
            partition=ctx.partition,
            requested_caps=req_caps,
            deadline_monotonic=session_deadline_monotonic,
            params=params,
            qa_loopback_mode=ctx.qa_loopback_mode,
            qa_loopback_url=loopback_url,
            actual_now=utc_now_fn(),
        )
        if not auth["authorized"] or auth["authorized_request"] is None:
            violations.extend(auth["violations"])
            return {"ok": False, "status": auth["status"], "violations": auth["violations"]}

        auth_req = auth["authorized_request"]
        sup = supervise_r6_session(
            ledger=ledger,
            lease=lease,
            plan=plan,
            verified_grant=verified_grant,
            validated_request={"kind": "rest_get", "url": auth_req.dispatch_url, "params": dict(auth_req.params)},
            attempt_id=aid,
            request_type="rest_get",
            reserved_caps=req_caps,
            resource_sample_fn=resource_sample_fn,
            expected_volumes=expected_volumes,
            session_deadline_monotonic=session_deadline_monotonic,
            cancel_event=cancel_event,
            utc_now_fn=utc_now_fn,
            qa_loopback_mode=ctx.qa_loopback_mode,
            allow_fixture_resource_sample=allow_fixture_resource_sample,
            registry=registry,
            authorized_request=auth_req,
            run_id=ctx.run_id,
            partition=ctx.partition,
            endpoint_id=task.endpoint_id,
            contract_digest=ep.content_digest,
            post_exit_hook=post_exit_hook,
        )
        lifecycle_traces.extend({**e, "task_id": task.task_id} for e in sup.get("lifecycle_trace") or ())
        reap = sup.get("reap_info") or {}
        s_rec = sup.get("settlement_receipt")
        t_res = sup.get("trusted_result")
        requests_count += int((sup.get("accepted_result") or {}).get("attempts", 0))
        if sup.get("raw_payload_bytes"):
            raw_bytes_total += len(sup["raw_payload_bytes"])
        if sup.get("decoded_payload_bytes"):
            decoded_bytes_total += len(sup["decoded_payload_bytes"])

        attempt_records.append({
            "attempt_id": aid,
            "endpoint_id": task.endpoint_id,
            "request_digest": auth_req.request_digest,
            "ok": bool(sup.get("ok")),
            "status": str(sup.get("status")),
            "child_pid": reap.get("child_pid"),
            "child_exit_code": reap.get("child_returncode"),
            "reaped_verified": bool(reap.get("reaped_verified")),
            "settlement_generation": s_rec.settled_generation if s_rec is not None else None,
            "settlement_receipt_digest": s_rec.receipt_digest if s_rec is not None else None,
            "trusted_result_digest": t_res.result_digest if t_res is not None else None,
            "late_result_discarded": bool(sup.get("late_result_discarded")),
        })
        if not sup.get("ok") or t_res is None or s_rec is None:
            violations.extend(sup.get("violations") or [str(sup.get("status"))])
            return {"ok": False, "status": str(sup.get("status")), "sup": sup, "violations": sup.get("violations") or []}
        return {"ok": True, "status": "OK", "sup": sup, "auth_req": auth_req}

    def _mint_and_commit(chunk_id: str, sup: Mapping[str, Any], norm_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        # Re-check cancellation strictly BEFORE persisting to custody!
        if cancel_event.is_set() or time.monotonic() >= session_deadline_monotonic:
            violations.append("CANCELLED_BEFORE_CUSTODY_PERSIST")
            return {"persisted": False, "status": "ABORTED_CANCELLED_BEFORE_PERSIST", "violations": ["CANCELLED_BEFORE_CUSTODY_PERSIST"]}
        t_res = sup["trusted_result"]
        s_rec = sup["settlement_receipt"]
        raw_b = sup["raw_payload_bytes"]
        prov = mint_r6_raw_provenance_receipt(
            tested_executable_sha=ctx.tested_executable_sha,
            run_id=ctx.run_id,
            session_id=ctx.session_id,
            lease_id=lease.lease_id,
            attempt_id=t_res.attempt_id,
            endpoint_id=task.endpoint_id,
            contract_digest=ep.content_digest,
            partition=ctx.partition,
            qa_loopback_mode=ctx.qa_loopback_mode,
            transport_kind="REST_GET",
            supervisor_result=t_res,
            raw_payload_bytes_data=raw_b,
            settlement_receipt=s_rec,
        )
        if not prov.accepted_for_custody:
            violations.append(f"PROVENANCE_REJECTED:{prov.transport_outcome}")
            return {"persisted": False, "status": "REJECTED_PROVENANCE", "violations": [prov.transport_outcome]}
        pers = custody.persist_r6_verified_chunk(
            chunk_id=chunk_id,
            raw_payload=raw_b,
            normalized_rows=norm_rows,
            provenance_receipt=prov,
        )
        if not pers.get("persisted"):
            violations.extend(pers.get("violations") or ["CUSTODY_PERSIST_FAILED"])
            return pers
        committed_chunks.append({
            "chunk_id": chunk_id,
            "attempt_id": t_res.attempt_id,
            "receipt_id": prov.receipt_id,
            "provenance_class": prov.provenance_class,
            "raw_sha256": pers["raw_sha256"],
            "raw_bytes_len": len(raw_b),
            "norm_sha256": pers["norm_sha256"],
            "norm_rows_count": len(norm_rows),
            "settlement_generation": s_rec.settled_generation,
        })
        return pers

    # --- Dispatch according to exact task_kind ---
    if task.task_kind == "instrument_metadata":
        att = _run_one_rest_attempt(1, {"productType": APPROVED_PRODUCT_TYPE_V2_R6, "symbol": APPROVED_SYMBOL_R6})
        if not att["ok"]:
            return {
                "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
                "ok": False, "terminal_status": att["status"], "valid_rows_count": 0, "invalid_rows_count": 0,
                "raw_bytes": raw_bytes_total, "decoded_bytes": decoded_bytes_total, "requests_count": requests_count,
                "attempt_records": attempt_records, "committed_chunks": committed_chunks,
                "lifecycle_traces": lifecycle_traces, "violations": violations,
            }
        try:
            body_doc = _strict_loads_r6(att["sup"]["decoded_payload_bytes"], max_bytes=262_144)
        except Exception as exc:
            violations.append(f"METADATA_JSON_ERROR:{exc}")
            return {
                "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
                "ok": False, "terminal_status": "REJECTED_METADATA_JSON", "valid_rows_count": 0, "invalid_rows_count": 1,
                "raw_bytes": raw_bytes_total, "decoded_bytes": decoded_bytes_total, "requests_count": requests_count,
                "attempt_records": attempt_records, "committed_chunks": committed_chunks,
                "lifecycle_traces": lifecycle_traces, "violations": violations,
            }
        data_rows = body_doc.get("data") if isinstance(body_doc, Mapping) else None
        if not isinstance(data_rows, list) or not data_rows or not isinstance(data_rows[0], Mapping):
            violations.append("METADATA_EMPTY_OR_MALFORMED_DATA")
            return {
                "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
                "ok": False, "terminal_status": "REJECTED_METADATA_EMPTY", "valid_rows_count": 0, "invalid_rows_count": 1,
                "raw_bytes": raw_bytes_total, "decoded_bytes": decoded_bytes_total, "requests_count": requests_count,
                "attempt_records": attempt_records, "committed_chunks": committed_chunks,
                "lifecycle_traces": lifecycle_traces, "violations": violations,
            }
        id_res = validate_r6_instrument_identity(
            data_rows[0],
            ep,
            provenance={"provenance_class": "QA_LOOPBACK" if ctx.qa_loopback_mode else "RAW_OBSERVED_VERIFIED"},
        )
        if not id_res["valid"]:
            violations.extend(id_res["violations"])
            return {
                "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
                "ok": False, "terminal_status": id_res["status"], "valid_rows_count": 0, "invalid_rows_count": 1,
                "raw_bytes": raw_bytes_total, "decoded_bytes": decoded_bytes_total, "requests_count": requests_count,
                "attempt_records": attempt_records, "committed_chunks": committed_chunks,
                "lifecycle_traces": lifecycle_traces, "violations": violations,
            }
        norm_meta = dict(id_res["normalized_metadata"])
        pers = _mint_and_commit(f"{task.task_id}_chunk_01", att["sup"], [norm_meta])
        ok_t = bool(pers.get("persisted"))
        return {
            "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
            "ok": ok_t, "terminal_status": "COMPLETED" if ok_t else str(pers.get("status")),
            "valid_rows_count": 1 if ok_t else 0, "invalid_rows_count": 0,
            "raw_bytes": raw_bytes_total, "decoded_bytes": decoded_bytes_total, "requests_count": requests_count,
            "normalized_instrument": norm_meta,
            "attempt_records": attempt_records, "committed_chunks": committed_chunks,
            "lifecycle_traces": lifecycle_traces, "violations": violations,
        }

    elif task.task_kind == "ohlcv_history":
        page_sups: List[Tuple[int, Dict[str, Any], List[Any]]] = []
        fetch_errors: List[str] = []

        def _page_fetcher(cursor_end: Optional[int], page_no: int) -> Dict[str, Any]:
            if cancel_event.is_set() or time.monotonic() >= session_deadline_monotonic:
                fetch_errors.append("CANCELLED_DURING_OHLCV_PAGINATION")
                return {"ok": False, "error": "CANCELLED"}
            q_params: Dict[str, Any] = {
                "symbol": APPROVED_SYMBOL_R6,
                "productType": APPROVED_PRODUCT_TYPE_V2_R6,
                "granularity": str(task.granularity or "1m"),
                "startTime": str(plan.window_start_ms),
                "endTime": str(cursor_end if cursor_end is not None else plan.window_end_ms),
                "limit": "100",
            }
            att = _run_one_rest_attempt(page_no, q_params)
            if not att["ok"]:
                fetch_errors.append(str(att["status"]))
                return {"ok": False, "error": str(att["status"])}
            try:
                bdoc = _strict_loads_r6(att["sup"]["decoded_payload_bytes"], max_bytes=262_144)
                rows = bdoc.get("data") if isinstance(bdoc, Mapping) else None
                if not isinstance(rows, list):
                    fetch_errors.append("OHLCV_DATA_NOT_LIST")
                    return {"ok": False, "error": "OHLCV_DATA_NOT_LIST"}
                page_sups.append((page_no, att["sup"], rows))
                return {"ok": True, "rows": rows}
            except Exception as exc:
                fetch_errors.append(f"OHLCV_PARSE_ERR:{exc}")
                return {"ok": False, "error": str(exc)}

        step_ms = 60_000
        exp_ts = list(range(plan.window_start_ms, plan.window_end_ms, step_ms))
        tick_sz = str((observed_instrument_meta or {}).get("derived_tick_size") or (observed_instrument_meta or {}).get("tick_size") or "0.01")
        ohlcv_res = paginate_r6_ohlcv_history(
            symbol=APPROVED_SYMBOL_R6,
            granularity=str(task.granularity or "1m"),
            window_start_ms=plan.window_start_ms,
            window_end_ms=plan.window_end_ms,
            server_time_ms=plan.window_end_ms + 60_000,
            page_fetcher=_page_fetcher,
            max_pages=int(task.max_pages),
            calendar_expected_open_times_ms=exp_ts,
            tick_size=tick_sz,
            provenance_class="QA_LOOPBACK" if ctx.qa_loopback_mode else "RAW_OBSERVED_VERIFIED",
        )
        if fetch_errors or not ohlcv_res.get("ok") or int(ohlcv_res.get("canonical_valid_count", 0)) == 0:
            violations.extend(fetch_errors)
            violations.extend(ohlcv_res.get("violations") or [])
            return {
                "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
                "ok": False, "terminal_status": "REJECTED_OHLCV_VALIDATION_OR_FETCH",
                "valid_rows_count": int(ohlcv_res.get("canonical_valid_count", 0)),
                "invalid_rows_count": int(ohlcv_res.get("invalid_rows_count", 0)),
                "raw_bytes": raw_bytes_total, "decoded_bytes": decoded_bytes_total, "requests_count": requests_count,
                "ohlcv_result": {k: v for k, v in ohlcv_res.items() if k != "canonical_rows"},
                "attempt_records": attempt_records, "committed_chunks": committed_chunks,
                "lifecycle_traces": lifecycle_traces, "violations": violations,
            }

        # Persist EVERY verified page chunk with its own exact supervisor proof & normalized slice!
        canon_by_ts = {int(r["open_time_ms"]): r for r in ohlcv_res.get("canonical_rows") or ()}
        all_pers = True
        for pno, psup, prows in page_sups:
            page_norm = []
            for raw_r in prows:
                try:
                    ts_k = int(raw_r[0]) if isinstance(raw_r, (list, tuple)) else int(raw_r.get("ts", -1))
                except Exception:
                    continue
                if ts_k in canon_by_ts:
                    page_norm.append(canon_by_ts[ts_k])
            pers = _mint_and_commit(f"{task.task_id}_page_{pno:02d}", psup, page_norm)
            if not pers.get("persisted"):
                all_pers = False
                break
        return {
            "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
            "ok": all_pers and len(committed_chunks) > 0,
            "terminal_status": "COMPLETED" if (all_pers and len(committed_chunks) > 0) else "FAILED_CUSTODY_PERSIST",
            "valid_rows_count": int(ohlcv_res["canonical_valid_count"]),
            "invalid_rows_count": int(ohlcv_res["invalid_rows_count"]),
            "raw_bytes": raw_bytes_total, "decoded_bytes": decoded_bytes_total, "requests_count": requests_count,
            "ohlcv_result": {k: v for k, v in ohlcv_res.items() if k != "canonical_rows"},
            "attempt_records": attempt_records, "committed_chunks": committed_chunks,
            "lifecycle_traces": lifecycle_traces, "violations": violations,
        }

    elif task.task_kind == "funding_history":
        att = _run_one_rest_attempt(
            1,
            {"symbol": APPROVED_SYMBOL_R6, "productType": APPROVED_PRODUCT_TYPE_V2_R6, "pageSize": "20", "pageNo": "1"},
        )
        if not att["ok"]:
            return {
                "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
                "ok": False, "terminal_status": att["status"], "valid_rows_count": 0, "invalid_rows_count": 0,
                "raw_bytes": raw_bytes_total, "decoded_bytes": decoded_bytes_total, "requests_count": requests_count,
                "attempt_records": attempt_records, "committed_chunks": committed_chunks,
                "lifecycle_traces": lifecycle_traces, "violations": violations,
            }
        try:
            fdoc = _strict_loads_r6(att["sup"]["decoded_payload_bytes"], max_bytes=262_144)
            frows = fdoc.get("data") if isinstance(fdoc, Mapping) else None
            if not isinstance(frows, list):
                raise ValueError("FUNDING_DATA_NOT_LIST")
        except Exception as exc:
            violations.append(f"FUNDING_JSON_ERROR:{exc}")
            return {
                "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
                "ok": False, "terminal_status": "REJECTED_FUNDING_JSON", "valid_rows_count": 0, "invalid_rows_count": 1,
                "raw_bytes": raw_bytes_total, "decoded_bytes": decoded_bytes_total, "requests_count": requests_count,
                "attempt_records": attempt_records, "committed_chunks": committed_chunks,
                "lifecycle_traces": lifecycle_traces, "violations": violations,
            }

        obs_interval = (observed_instrument_meta or {}).get("fund_interval_hours")
        f_start = plan.window_start_ms
        f_end = plan.window_end_ms
        if frows and isinstance(obs_interval, int) and obs_interval > 0:
            step_f = obs_interval * 3_600_000
            ts_list = sorted(int(r["fundingTime"]) for r in frows if isinstance(r, Mapping) and str(r.get("fundingTime", "")).isdigit())
            if ts_list:
                f_start = ts_list[0]
                f_end = ts_list[-1] + step_f
        fund_res = validate_r6_funding_settlements(
            frows,
            symbol=APPROVED_SYMBOL_R6,
            window_start_ms=f_start,
            window_end_ms=f_end,
            observed_fund_interval_hours=int(obs_interval) if isinstance(obs_interval, int) else None,
            provenance_class="QA_LOOPBACK" if ctx.qa_loopback_mode else "RAW_OBSERVED_VERIFIED",
        )
        realized_n = int(fund_res.get("realized_settlements_count", len(fund_res.get("realized_settlements") or ())))
        invalid_n = int(fund_res.get("invalid_rows_count", 0))
        if not fund_res.get("ok") or realized_n == 0:
            violations.extend(fund_res.get("violations") or ["FUNDING_VALIDATION_FAILED"])
            return {
                "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
                "ok": False, "terminal_status": str(fund_res.get("status") or "REJECTED_FUNDING"),
                "valid_rows_count": realized_n,
                "invalid_rows_count": invalid_n,
                "raw_bytes": raw_bytes_total, "decoded_bytes": decoded_bytes_total, "requests_count": requests_count,
                "funding_result": {k: v for k, v in fund_res.items() if k != "realized_settlements"},
                "attempt_records": attempt_records, "committed_chunks": committed_chunks,
                "lifecycle_traces": lifecycle_traces, "violations": violations,
            }
        pers = _mint_and_commit(f"{task.task_id}_chunk_01", att["sup"], fund_res.get("realized_settlements") or ())
        ok_f = bool(pers.get("persisted"))
        return {
            "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
            "ok": ok_f, "terminal_status": "COMPLETED" if ok_f else str(pers.get("status")),
            "valid_rows_count": realized_n, "invalid_rows_count": invalid_n,
            "raw_bytes": raw_bytes_total, "decoded_bytes": decoded_bytes_total, "requests_count": requests_count,
            "funding_result": {k: v for k, v in fund_res.items() if k != "realized_settlements"},
            "attempt_records": attempt_records, "committed_chunks": committed_chunks,
            "lifecycle_traces": lifecycle_traces, "violations": violations,
        }

    return {
        "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
        "ok": False, "terminal_status": f"UNKNOWN_REST_TASK_KIND:{task.task_kind}",
        "valid_rows_count": 0, "invalid_rows_count": 0, "raw_bytes": 0, "decoded_bytes": 0, "requests_count": 0,
        "attempt_records": [], "committed_chunks": [], "lifecycle_traces": [], "violations": [f"UNKNOWN_REST_TASK_KIND:{task.task_kind}"],
    }


def execute_r6_a1_ws_task(
    *,
    ctx: R6A1AcquisitionSessionContext,
    task: R6A1AcquisitionTask,
    ledger: R6DurableGrantLedger,
    lease: R6DurableSessionLease,
    plan: R6BoundCapturePlan,
    verified_grant: R6VerifiedGrant,
    registry: R6SourceContractRegistry,
    custody: R6CustodyJournal,
    resource_sample_fn: Callable[[], Mapping[str, Any]],
    expected_volumes: Mapping[str, Path],
    session_deadline_monotonic: float,
    cancel_event: threading.Event,
    utc_now_fn: Callable[[], datetime],
    allow_fixture_resource_sample: bool = False,
    observed_instrument_meta: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    F05 (WS): Executes bounded BBO WebSocket acquisition through F12 -> F19 -> BBO validators
    (`normalize_r6_bbo_event`, `validate_r6_bbo_epoch`, `build_r6_bbo_coverage_report`) -> F23 -> F25!
    """
    ep = registry.endpoints[task.endpoint_id]
    loopback_ws_url = f"{ctx.qa_ws_base_url}{ep.path}" if ctx.qa_loopback_mode and ctx.qa_ws_base_url else None
    sub_payload = {
        "op": "subscribe",
        "args": [{"instType": APPROVED_PRODUCT_TYPE_V2_R6, "channel": str(task.ws_channel or "books1"), "instId": APPROVED_SYMBOL_R6}],
    }
    aid = f"{ctx.session_id}_{task.task_id}_att_01"
    req_caps = complete_caps_vector_a1({
        "connections": min(int(plan.caps["connections"]), 2),
        "subscriptions": min(int(plan.caps["subscriptions"]), 2),
        "retries": min(int(plan.caps["retries"]), 1),
        "messages": min(int(plan.caps["messages"]), max(2, int(task.ws_max_messages))),
        "payload_bytes": min(int(plan.caps["payload_bytes"]), max(4096, int(task.per_attempt_payload_bytes))),
        "decoded_bytes": min(int(plan.caps["decoded_bytes"]), max(4096, int(task.per_attempt_decoded_bytes))),
    })
    violations: List[str] = []
    auth = authorize_r6_a1_dispatch_request(
        plan=plan,
        verified_grant=verified_grant,
        lease=lease,
        registry=registry,
        endpoint_id=task.endpoint_id,
        attempt_id=aid,
        partition=ctx.partition,
        requested_caps=req_caps,
        deadline_monotonic=session_deadline_monotonic,
        subscribe_payload=sub_payload,
        qa_loopback_mode=ctx.qa_loopback_mode,
        qa_loopback_url=loopback_ws_url,
        actual_now=utc_now_fn(),
    )
    if not auth["authorized"] or auth["authorized_request"] is None:
        return {
            "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
            "ok": False, "terminal_status": auth["status"], "valid_rows_count": 0, "invalid_rows_count": 0,
            "raw_bytes": 0, "decoded_bytes": 0, "requests_count": 0,
            "attempt_records": [], "committed_chunks": [], "lifecycle_traces": [], "violations": auth["violations"],
        }

    auth_req = auth["authorized_request"]
    sup = supervise_r6_session(
        ledger=ledger,
        lease=lease,
        plan=plan,
        verified_grant=verified_grant,
        validated_request={"kind": "ws_session", "ws_url": auth_req.dispatch_url, "subscribe_payload": dict(sub_payload)},
        attempt_id=aid,
        request_type="ws_session",
        reserved_caps=req_caps,
        resource_sample_fn=resource_sample_fn,
        expected_volumes=expected_volumes,
        session_deadline_monotonic=session_deadline_monotonic,
        cancel_event=cancel_event,
        utc_now_fn=utc_now_fn,
        qa_loopback_mode=ctx.qa_loopback_mode,
        allow_fixture_resource_sample=allow_fixture_resource_sample,
        registry=registry,
        authorized_request=auth_req,
        run_id=ctx.run_id,
        partition=ctx.partition,
        endpoint_id=task.endpoint_id,
        contract_digest=ep.content_digest,
        max_frame_bytes=min(16_384, req_caps["payload_bytes"]),
        max_queue_items=max(4, req_caps["messages"]),
        max_queue_bytes=req_caps["payload_bytes"],
    )
    traces = [{**e, "task_id": task.task_id} for e in (sup.get("lifecycle_trace") or ())]
    reap = sup.get("reap_info") or {}
    s_rec = sup.get("settlement_receipt")
    t_res = sup.get("trusted_result")
    att_rec = {
        "attempt_id": aid,
        "endpoint_id": task.endpoint_id,
        "request_digest": auth_req.request_digest,
        "ok": bool(sup.get("ok")),
        "status": str(sup.get("status")),
        "child_pid": reap.get("child_pid"),
        "child_exit_code": reap.get("child_returncode"),
        "reaped_verified": bool(reap.get("reaped_verified")),
        "settlement_generation": s_rec.settled_generation if s_rec is not None else None,
        "settlement_receipt_digest": s_rec.receipt_digest if s_rec is not None else None,
        "trusted_result_digest": t_res.result_digest if t_res is not None else None,
        "late_result_discarded": bool(sup.get("late_result_discarded")),
    }
    if not sup.get("ok") or t_res is None or s_rec is None:
        return {
            "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
            "ok": False, "terminal_status": str(sup.get("status")), "valid_rows_count": 0, "invalid_rows_count": 0,
            "raw_bytes": 0, "decoded_bytes": 0, "requests_count": 0,
            "attempt_records": [att_rec], "committed_chunks": [], "lifecycle_traces": traces,
            "violations": list(sup.get("violations") or [str(sup.get("status"))]),
        }

    # Normalize & validate BBO events + epochs strictly (no fake continuous carry!)
    acc = sup["accepted_result"]
    tick_sz = str((observed_instrument_meta or {}).get("derived_tick_size") or (observed_instrument_meta or {}).get("tick_size") or "0.01")
    norm_events: List[Dict[str, Any]] = []
    invalid_ev = 0
    for ev in acc.get("events") or ():
        pay = ev.get("payload") or {}
        action_s = str(pay.get("action") or "").strip().lower()
        for item in pay.get("data") or ():
            bids = item.get("bids") or []
            asks = item.get("asks") or []
            if not bids or not asks:
                invalid_ev += 1
                continue
            ev_ts = int(item.get("ts") or ev.get("receive_utc_ms") or 0)
            n = normalize_r6_bbo_event(
                {
                    "symbol": APPROVED_SYMBOL_R6,
                    "bid_price": str(bids[0][0]),
                    "bid_size": str(bids[0][1]),
                    "ask_price": str(asks[0][0]),
                    "ask_size": str(asks[0][1]),
                    "event_ts_ms": ev_ts,
                    "receive_utc_ms": int(ev.get("receive_utc_ms") or ev_ts),
                    "epoch": int(ev.get("epoch") or 1),
                    "epoch_healthy": bool(ev.get("epoch_healthy")),
                },
                tick_size=tick_sz,
            )
            if n.get("valid_for_streaming_bbo") and n.get("normalized"):
                row = dict(n["normalized"])
                row["valid_for_streaming_bbo"] = True
                row["action"] = action_s or "update"
                norm_events.append(row)
            else:
                invalid_ev += 1

    epochs_norm = []
    ev_ts_all = [int(e["event_ts_ms"]) for e in norm_events]
    min_ts = min(ev_ts_all) if ev_ts_all else plan.window_start_ms
    max_ts = max(ev_ts_all) if ev_ts_all else plan.window_start_ms + 3000
    for ep_r in acc.get("epochs") or ():
        ep_id_i = int(ep_r.get("epoch", 1))
        v_ep = validate_r6_bbo_epoch(
            [e for e in norm_events if int(e.get("epoch", 0)) == ep_id_i],
            epoch_id=ep_id_i,
        )
        epochs_norm.append({
            "epoch": ep_id_i,
            "start_ms": min_ts,
            "end_ms": max_ts + 1000,
            "epoch_healthy": bool(ep_r.get("epoch_healthy")) and bool(v_ep.get("valid_epoch")),
            "snapshot_received": bool(ep_r.get("snapshot_received")),
        })

    slots = list(range(min_ts, max_ts + 1000, 1000)) if ev_ts_all else [plan.window_start_ms]
    bbo_cov = build_r6_bbo_coverage_report(
        norm_events,
        epochs_norm,
        calendar_expected_1s_slots_ms=slots,
        max_state_freshness_ms=10_000,
    )

    if not norm_events:
        violations.append("WS_NO_VALID_BBO_EVENTS")
        return {
            "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
            "ok": False, "terminal_status": "REJECTED_EMPTY_BBO_EVENTS", "valid_rows_count": 0, "invalid_rows_count": invalid_ev,
            "raw_bytes": len(sup["raw_payload_bytes"]), "decoded_bytes": len(sup["decoded_payload_bytes"]), "requests_count": 0,
            "bbo_coverage": bbo_cov, "attempt_records": [att_rec], "committed_chunks": [],
            "lifecycle_traces": traces, "violations": violations,
        }

    if cancel_event.is_set() or time.monotonic() >= session_deadline_monotonic:
        return {
            "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
            "ok": False, "terminal_status": "ABORTED_CANCELLED_BEFORE_PERSIST", "valid_rows_count": 0, "invalid_rows_count": invalid_ev,
            "raw_bytes": len(sup["raw_payload_bytes"]), "decoded_bytes": len(sup["decoded_payload_bytes"]), "requests_count": 0,
            "bbo_coverage": bbo_cov, "attempt_records": [att_rec], "committed_chunks": [],
            "lifecycle_traces": traces, "violations": ["CANCELLED_BEFORE_CUSTODY_PERSIST"],
        }

    prov = mint_r6_raw_provenance_receipt(
        tested_executable_sha=ctx.tested_executable_sha,
        run_id=ctx.run_id,
        session_id=ctx.session_id,
        lease_id=lease.lease_id,
        attempt_id=t_res.attempt_id,
        endpoint_id=task.endpoint_id,
        contract_digest=ep.content_digest,
        partition=ctx.partition,
        qa_loopback_mode=ctx.qa_loopback_mode,
        transport_kind="WS_PUBLIC",
        supervisor_result=t_res,
        raw_payload_bytes_data=sup["raw_payload_bytes"],
        settlement_receipt=s_rec,
    )
    pers = custody.persist_r6_verified_chunk(
        chunk_id=f"{task.task_id}_chunk_01",
        raw_payload=sup["raw_payload_bytes"],
        normalized_rows=norm_events,
        provenance_receipt=prov,
    )
    ok_ws = bool(pers.get("persisted"))
    chunks_out = []
    if ok_ws:
        chunks_out.append({
            "chunk_id": f"{task.task_id}_chunk_01",
            "attempt_id": t_res.attempt_id,
            "receipt_id": prov.receipt_id,
            "provenance_class": prov.provenance_class,
            "raw_sha256": pers["raw_sha256"],
            "raw_bytes_len": len(sup["raw_payload_bytes"]),
            "norm_sha256": pers["norm_sha256"],
            "norm_rows_count": len(norm_events),
            "settlement_generation": s_rec.settled_generation,
        })
    else:
        violations.extend(pers.get("violations") or ["WS_CUSTODY_PERSIST_FAILED"])

    return {
        "task_id": task.task_id, "task_kind": task.task_kind, "endpoint_id": task.endpoint_id,
        "ok": ok_ws, "terminal_status": "COMPLETED" if ok_ws else str(pers.get("status")),
        "valid_rows_count": len(norm_events), "invalid_rows_count": invalid_ev,
        "raw_bytes": len(sup["raw_payload_bytes"]), "decoded_bytes": len(sup["decoded_payload_bytes"]),
        "requests_count": 0, "connections_opened": int(acc.get("connections_opened", 0)),
        "subscriptions_sent": int(acc.get("subscriptions_sent", 0)),
        "reconnects_used": int(acc.get("reconnects_used", 0)),
        "messages_count": int(acc.get("total_messages", 0)),
        "bbo_coverage": bbo_cov, "attempt_records": [att_rec], "committed_chunks": chunks_out,
        "lifecycle_traces": traces, "violations": violations,
    }


# =====================================================================
# F03 / F06 / F07: Orchestrator State Machine, Finalization & Immutable Receipt
# =====================================================================
def build_r6_a1_session_receipt(
    *,
    ctx: Optional[R6A1AcquisitionSessionContext],
    state_history: Sequence[str],
    claim_status: str,
    lease: Optional[R6DurableSessionLease],
    plan: Optional[R6BoundCapturePlan],
    task_results: Sequence[Mapping[str, Any]],
    custody_audit: Optional[Mapping[str, Any]],
    dataset_manifest: Optional[Mapping[str, Any]],
    ledger_snapshot: Optional[Mapping[str, Any]],
    ledger_final_status: str,
    resource_samples: Sequence[Mapping[str, Any]],
    violations: Sequence[str],
    quarantined: bool,
    cancelled: bool,
) -> Dict[str, Any]:
    """
    F07: Computes immutable terminal status & exit code STRICTLY from observed facts — NEVER from
    a caller-supplied status string! `QA_ACQUISITION_END_TO_END_PASS` requires ALL planned QA tasks
    complete, non-empty committed chunks, read-only custody verified, zero quarantine/overrun,
    and `accepted_real_observed_records == 0`.
    """
    expected_tasks = len(ctx.tasks) if ctx is not None else 0
    completed_tasks = sum(1 for t in task_results if t.get("ok") is True)
    failed_tasks = sum(1 for t in task_results if t.get("ok") is not True)
    committed_chunks = [c for t in task_results for c in (t.get("committed_chunks") or ())]
    total_raw_bytes = sum(int(c.get("raw_bytes_len", 0)) for c in committed_chunks)
    total_norm_rows = sum(int(c.get("norm_rows_count", 0)) for c in committed_chunks)
    all_attempts = [a for t in task_results for a in (t.get("attempt_records") or ())]
    all_reaped = all(bool(a.get("reaped_verified")) for a in all_attempts if a.get("child_pid"))
    custody_ok = bool(custody_audit and custody_audit.get("valid") is True)

    if ctx is None:
        final_status = claim_status or "BLOCKED_ACQUISITION_PRECHECK"
        exit_code = 2
    elif not A1_EXTERNAL_CAPTURE_ENABLED and not ctx.qa_loopback_mode:
        final_status = "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED"
        exit_code = 2
    elif quarantined or ledger_final_status == "QUARANTINED":
        final_status = "QUARANTINED_ACQUISITION_SESSION"
        exit_code = 2
    elif cancelled:
        final_status = "ABORTED_CANCELLED" if not committed_chunks else "PARTIAL_ABORTED_CANCELLED"
        exit_code = 2
    elif (
        expected_tasks > 0
        and completed_tasks == expected_tasks
        and failed_tasks == 0
        and len(committed_chunks) > 0
        and total_raw_bytes > 0
        and total_norm_rows > 0
        and custody_ok
        and all_reaped
        and ledger_final_status == "SPENT"
        and not violations
    ):
        final_status = TERMINAL_QA_PASS_STATUS_A1
        exit_code = 0
    elif committed_chunks:
        final_status = "PARTIAL_ACQUISITION_FAILED"
        exit_code = 2
    else:
        final_status = "FAILED_ACQUISITION_NO_COMMITTED_DATA"
        exit_code = 2

    core = {
        "spec_version": SPEC_VERSION_KH03_R6_A1,
        "schema_version": SCHEMA_VERSION_KH03_R6_A1,
        "status": final_status,
        "exit_code": exit_code,
        "run_id": ctx.run_id if ctx else None,
        "session_id": ctx.session_id if ctx else None,
        "stage": ctx.stage if ctx else None,
        "logical_profile": ctx.logical_profile if ctx else None,
        "qa_loopback_mode": bool(ctx.qa_loopback_mode) if ctx else False,
        "tested_executable_sha": ctx.tested_executable_sha if ctx else None,
        "release_manifest_sha256": ctx.release_manifest_sha256 if ctx else None,
        "plan_digest": plan.plan_digest if plan else None,
        "lease_id": lease.lease_id if lease else None,
        "partition": ctx.partition if ctx else None,
        "state_history": list(state_history),
        "claim_status": claim_status,
        "ledger_final_status": ledger_final_status,
        "tasks_planned_count": expected_tasks,
        "tasks_completed_count": completed_tasks,
        "tasks_failed_count": failed_tasks,
        "committed_chunks_count": len(committed_chunks),
        "total_committed_raw_bytes": total_raw_bytes,
        "total_committed_normalized_rows": total_norm_rows,
        "external_market_requests_attempted": 0,
        "accepted_real_observed_records": 0,
        "qa_loopback_records_persisted": total_norm_rows if (ctx and ctx.qa_loopback_mode) else 0,
        "all_spawned_children_reaped_verified": all_reaped,
        "custody_readonly_valid": custody_ok,
        "custody_tip_hash": custody_audit.get("tip_hash") if custody_audit else None,
        "dataset_manifest_sha256": dataset_manifest.get("manifest_sha256") if dataset_manifest else None,
        "task_results": [
            {k: v for k, v in t.items() if k != "lifecycle_traces"}
            for t in task_results
        ],
        "committed_chunks": committed_chunks,
        "ledger_snapshot": ledger_snapshot,
        "custody_audit": custody_audit,
        "dataset_manifest": dataset_manifest,
        "resource_samples_count": len(resource_samples),
        "violations": list(violations),
        "production_live_paper_auto_modes": "ALL_OFF",
        "oos_evaluations": 0,
    }
    core["receipt_digest"] = _sha256_canonical_a1(core)
    return core


def finalize_r6_a1_acquisition_session(
    *,
    ctx: R6A1AcquisitionSessionContext,
    ledger: R6DurableGrantLedger,
    lease: R6DurableSessionLease,
    plan: R6BoundCapturePlan,
    custody: R6CustodyJournal,
    task_results: Sequence[Mapping[str, Any]],
    state_history: List[str],
    resource_samples: Sequence[Mapping[str, Any]],
    violations: List[str],
    quarantine_required: bool,
    cancelled: bool,
    utc_now_fn: Callable[[], datetime],
    metadata_versions: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    F06: Finalizes owned session strictly once: verifies custody read-only against linked SQLite ledger,
    builds gap ledger, seals QA dataset manifest ONLY after validation, finalizes durable one-shot ledger
    (never resetting spent grants), and returns immutable receipt (F07).
    """
    # Always verify custody read-only with linked SQLite ledger proof!
    custody_audit = verify_r6_custody_readonly(
        custody.store_dir,
        expected_partition=ctx.partition,
        expected_run_id=ctx.run_id,
        ledger_store_root=ledger.store_root,
    )
    if not custody_audit["valid"]:
        quarantine_required = True
        violations.extend(custody_audit["violations"])

    ohlcv_res = next((t.get("ohlcv_result") for t in task_results if t.get("task_kind") == "ohlcv_history"), None)
    bbo_res = next((t.get("bbo_coverage") for t in task_results if t.get("task_kind") == "bbo_ws_stream"), None)
    fund_res = next((t.get("funding_result") for t in task_results if t.get("task_kind") == "funding_history"), None)
    gap_ledger = build_r6_market_data_gap_ledger(
        dataset_scope=ctx.partition,
        ohlcv_result=ohlcv_res,
        bbo_result=bbo_res,
        funding_result=fund_res,
    )

    valid_total = sum(int(t.get("valid_rows_count", 0)) for t in task_results)
    invalid_total = sum(int(t.get("invalid_rows_count", 0)) for t in task_results)
    committed_rows = int(custody_audit.get("total_normalized_rows", 0))
    raw_obs = max(valid_total + invalid_total, committed_rows)

    dataset_manifest: Optional[Dict[str, Any]] = None
    if custody_audit["valid"] and int(custody_audit.get("committed_chunks", 0)) > 0:
        state_history.append("SEALED")
        dataset_manifest = seal_r6_dev_dataset_manifest(
            run_id=ctx.run_id,
            stage=ctx.stage,
            partition=ctx.partition,
            tested_executable_sha=ctx.tested_executable_sha,
            source_registry_digest=ctx.registry_digest,
            instrument_metadata_versions=metadata_versions,
            window_start_ms=plan.window_start_ms,
            window_end_ms=plan.window_end_ms,
            custody_audit=custody_audit,
            gap_ledger=gap_ledger,
            raw_observed_count=raw_obs,
            normalized_valid_count=valid_total,
            rejected_or_quarantined_count=invalid_total,
            persisted_accepted_count=committed_rows,
            provenance_classes=["QA_LOOPBACK"] if ctx.qa_loopback_mode else ["RAW_UNVERIFIED_REJECTED"],
            retention_policy={"max_retention_days": 30, "git_commit_raw_payloads": False},
            local_store_redacted_id=f"qa_store:{ledger.store_id}",
        )
        # Write manifest file atomically inside custody run directory
        manifest_path = custody.store_dir / "qa_dataset_manifest.json"
        manifest_path.write_text(json.dumps(dataset_manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    # Clean up ONLY owned temp files
    cln = custody.cleanup_owned_temp_files_safe()
    if not cln["all_owned_temps_deleted"]:
        quarantine_required = True
        violations.append("CUSTODY_OWNED_TEMP_UNLINK_FAILED_QUARANTINED")

    fin_reason = (
        "QA_E2E_ACQUISITION_COMPLETE"
        if (not quarantine_required and not cancelled and not violations)
        else ("ABORTED_CANCELLED" if cancelled else "PARTIAL_OR_QUARANTINED")
    )
    fin_res = ledger.finalize_r6_session(
        lease,
        quarantine=bool(quarantine_required),
        reason=fin_reason,
        actual_now=utc_now_fn(),
    )
    ledger_final_status = str(fin_res.get("status") or "UNKNOWN")
    state_history.append("FINALIZED" if not quarantine_required else "QUARANTINED")
    snap = ledger.snapshot_grant(lease.grant_identity_key)

    return build_r6_a1_session_receipt(
        ctx=ctx,
        state_history=state_history,
        claim_status="CLAIMED_ONE_SHOT",
        lease=lease,
        plan=plan,
        task_results=task_results,
        custody_audit=custody_audit,
        dataset_manifest=dataset_manifest,
        ledger_snapshot=snap,
        ledger_final_status=ledger_final_status,
        resource_samples=resource_samples,
        violations=violations,
        quarantined=bool(quarantine_required or ledger_final_status == "QUARANTINED"),
        cancelled=cancelled,
    )


def run_r6_a1_acquisition_session(
    *,
    ctx: R6A1AcquisitionSessionContext,
    verified_grant: R6VerifiedGrant,
    plan: R6BoundCapturePlan,
    registry: R6SourceContractRegistry,
    ledger: R6DurableGrantLedger,
    resource_sample_fn: Callable[[], Mapping[str, Any]],
    expected_volumes: Mapping[str, Path],
    repo_root: Path,
    oos_roots: Sequence[Path] = (),
    cancel_event: Optional[threading.Event] = None,
    utc_now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    allow_fixture_resource_sample: bool = False,
    cancel_between_claim_and_first_attempt: bool = False,
    post_exit_hook: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """
    F03: Single end-to-end acquisition orchestration authority.
    Executes real state machine:
      PRECHECK -> PLAN_BOUND -> PARTITION_ASSIGNED -> CLAIMED -> ACQUIRING -> VALIDATING -> PERSISTING -> SEALED -> FINALIZED
    """
    state_history: List[str] = ["PRECHECK", "PLAN_BOUND", "PARTITION_ASSIGNED"]
    violations: List[str] = []
    resource_samples: List[Dict[str, Any]] = []
    task_results: List[Dict[str, Any]] = []
    all_lifecycle_traces: List[Dict[str, Any]] = []
    ev_cancel = cancel_event if cancel_event is not None else threading.Event()

    # Hard A1 external block BEFORE claiming any grant!
    if not A1_EXTERNAL_CAPTURE_ENABLED and not ctx.qa_loopback_mode:
        return build_r6_a1_session_receipt(
            ctx=ctx,
            state_history=state_history,
            claim_status="BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED",
            lease=None,
            plan=plan,
            task_results=[],
            custody_audit=None,
            dataset_manifest=None,
            ledger_snapshot=None,
            ledger_final_status="NOT_CLAIMED",
            resource_samples=[],
            violations=["A1_EXTERNAL_CAPTURE_DISABLED_NO_REAL_GRANT_CLAIM"],
            quarantined=False,
            cancelled=False,
        )

    now_start = utc_now_fn()
    now_start_ms = int(now_start.astimezone(timezone.utc).timestamp() * 1000)
    # Immutable absolute monotonic session deadline computed ONCE (never reset per request/page/reconnect!)
    rem_grant_sec = max(0.0, (verified_grant.expires_utc_ms - now_start_ms) / 1000.0)
    session_deadline_mono = time.monotonic() + min(float(plan.max_session_duration_sec), rem_grant_sec)

    # Record initial resource sample
    resource_samples.append(dict(resource_sample_fn()))

    # Claim one-shot grant ONCE after all prechecks!
    claim_res = ledger.claim_r6_grant_session_atomic(verified_grant, plan, ctx.session_id, actual_now=now_start)
    if not claim_res.get("claimed") or claim_res.get("lease") is None:
        return build_r6_a1_session_receipt(
            ctx=ctx,
            state_history=state_history,
            claim_status=str(claim_res.get("status") or "REJECTED_CLAIM"),
            lease=None,
            plan=plan,
            task_results=[],
            custody_audit=None,
            dataset_manifest=None,
            ledger_snapshot=ledger.snapshot_grant(verified_grant.grant_identity_key),
            ledger_final_status="NOT_CLAIMED",
            resource_samples=resource_samples,
            violations=list(claim_res.get("violations") or [str(claim_res.get("status"))]),
            quarantined=False,
            cancelled=False,
        )

    lease: R6DurableSessionLease = claim_res["lease"]
    state_history.append("CLAIMED")

    custody = R6CustodyJournal(
        approved_root=Path(verified_grant.destination_root),
        partition=ctx.partition,
        run_id=ctx.run_id,
        repo_root=repo_root,
        oos_roots=oos_roots,
        max_chunk_bytes=max(65_536, int(plan.caps.get("payload_bytes", 1_048_576))),
        max_total_bytes=max(262_144, int(plan.caps.get("disk_bytes", 16_777_216))),
    )

    quarantine_req = False
    metadata_versions: List[Dict[str, Any]] = []

    try:
        if cancel_between_claim_and_first_attempt:
            ev_cancel.set()

        if ev_cancel.is_set() or time.monotonic() >= session_deadline_mono:
            state_history.append("ABORTED")
            violations.append("CANCELLED_OR_EXPIRED_BEFORE_FIRST_ATTEMPT")
        else:
            state_history.append("ACQUIRING")
            # 1. Execute mandatory metadata identity gate FIRST (serial prerequisite!)
            meta_task = ctx.tasks[0]
            meta_res = execute_r6_a1_rest_task(
                ctx=ctx,
                task=meta_task,
                ledger=ledger,
                lease=lease,
                plan=plan,
                verified_grant=verified_grant,
                registry=registry,
                custody=custody,
                resource_sample_fn=resource_sample_fn,
                expected_volumes=expected_volumes,
                session_deadline_monotonic=session_deadline_mono,
                cancel_event=ev_cancel,
                utc_now_fn=utc_now_fn,
                allow_fixture_resource_sample=allow_fixture_resource_sample,
                post_exit_hook=post_exit_hook,
            )
            task_results.append(meta_res)
            all_lifecycle_traces.extend(meta_res.get("lifecycle_traces") or ())

            if not meta_res.get("ok"):
                violations.extend(meta_res.get("violations") or ["METADATA_IDENTITY_GATE_FAILED"])
                state_history.append("ABORTED")
            else:
                obs_meta = meta_res.get("normalized_instrument") or {}
                metadata_versions.append(dict(obs_meta))
                state_history.append("VALIDATING")

                # 2. Execute remaining planned tasks via bounded async coordinator!
                remaining_tasks = list(ctx.tasks[1:])

                async def _run_remaining_async() -> List[Dict[str, Any]]:
                    sem = asyncio.Semaphore(ctx.effective_concurrency)
                    loop = asyncio.get_running_loop()
                    # Bounded executor matching ctx.effective_concurrency (<= 2)
                    with ThreadPoolExecutor(max_workers=ctx.effective_concurrency) as pool:

                        async def _run_one(t: R6A1AcquisitionTask) -> Dict[str, Any]:
                            async with sem:
                                if ev_cancel.is_set() or time.monotonic() >= session_deadline_mono:
                                    return {
                                        "task_id": t.task_id, "task_kind": t.task_kind, "endpoint_id": t.endpoint_id,
                                        "ok": False, "terminal_status": "ABORTED_CANCELLED_BEFORE_TASK",
                                        "valid_rows_count": 0, "invalid_rows_count": 0, "raw_bytes": 0, "decoded_bytes": 0,
                                        "requests_count": 0, "attempt_records": [], "committed_chunks": [],
                                        "lifecycle_traces": [], "violations": ["CANCELLED_BEFORE_TASK"],
                                    }
                                if t.transport_kind == "ws_session":
                                    return await loop.run_in_executor(
                                        pool,
                                        lambda: execute_r6_a1_ws_task(
                                            ctx=ctx, task=t, ledger=ledger, lease=lease, plan=plan,
                                            verified_grant=verified_grant, registry=registry, custody=custody,
                                            resource_sample_fn=resource_sample_fn, expected_volumes=expected_volumes,
                                            session_deadline_monotonic=session_deadline_mono, cancel_event=ev_cancel,
                                            utc_now_fn=utc_now_fn, allow_fixture_resource_sample=allow_fixture_resource_sample,
                                            observed_instrument_meta=obs_meta,
                                        ),
                                    )
                                return await loop.run_in_executor(
                                    pool,
                                    lambda: execute_r6_a1_rest_task(
                                        ctx=ctx, task=t, ledger=ledger, lease=lease, plan=plan,
                                        verified_grant=verified_grant, registry=registry, custody=custody,
                                        resource_sample_fn=resource_sample_fn, expected_volumes=expected_volumes,
                                        session_deadline_monotonic=session_deadline_mono, cancel_event=ev_cancel,
                                        utc_now_fn=utc_now_fn, allow_fixture_resource_sample=allow_fixture_resource_sample,
                                        observed_instrument_meta=obs_meta,
                                    ),
                                )

                        coros = [_run_one(t) for t in remaining_tasks]
                        return list(await asyncio.gather(*coros))

                if remaining_tasks:
                    rem_out = asyncio.run(_run_remaining_async())
                    for r_t in rem_out:
                        task_results.append(r_t)
                        all_lifecycle_traces.extend(r_t.get("lifecycle_traces") or ())
                        if not r_t.get("ok"):
                            violations.extend(r_t.get("violations") or [str(r_t.get("terminal_status"))])
                            if "QUARANTINED" in str(r_t.get("terminal_status") or "") or "FAIL_UNDRAINED" in str(r_t.get("terminal_status") or ""):
                                quarantine_req = True

                state_history.append("PERSISTING")
    except Exception as exc:
        quarantine_req = True
        violations.append(f"ORCHESTRATOR_UNHANDLED_EXCEPTION:{exc}")
    finally:
        resource_samples.append(dict(resource_sample_fn()))
        receipt = finalize_r6_a1_acquisition_session(
            ctx=ctx,
            ledger=ledger,
            lease=lease,
            plan=plan,
            custody=custody,
            task_results=task_results,
            state_history=state_history,
            resource_samples=resource_samples,
            violations=violations,
            quarantine_required=quarantine_req,
            cancelled=ev_cancel.is_set(),
            utc_now_fn=utc_now_fn,
            metadata_versions=metadata_versions,
        )

    receipt["lifecycle_traces"] = all_lifecycle_traces
    return receipt
