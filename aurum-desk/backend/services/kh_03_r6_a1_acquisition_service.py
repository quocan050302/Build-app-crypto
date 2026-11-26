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

from services.kh_03_r6_a1_corrective_service import (
    DEFAULT_EMERGENCY_AUDIT_RESERVE_BYTES,
    DEFAULT_MANIFEST_AND_ANCHOR_RESERVE_BYTES,
    DEFAULT_SQLITE_WAL_OVERHEAD_RESERVE_BYTES,
    R6A1BoundedOrderedCustodyQueue,
    R6A1CustodyWriterAuthority,
    R6A1DiskBudgetLedger,
    build_r6_a1_custody_envelope,
    compute_chunk_conservative_peak_disk_bytes,
    compute_effective_disk_budget_contract,
    derive_r6_a1_terminal_disposition,
    run_r6_a1_single_custody_writer,
)
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
    recover_r6_custody_intents,
    seal_r6_dev_dataset_manifest,
    verify_r6_custody_readonly,
)
from services.kh_03_r6_durable_grant_service import (
    R6DurableGrantLedger,
    R6DurableSessionLease,
    R6VerifiedGrant,
    _atomic_write_json_fsync,
    _strict_loads_r6,
    reconcile_r6_a1_claim_after_exception,
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
    budget_contract: Optional[Mapping[str, Any]] = None


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
    override_max_chunk_bytes: Optional[int] = None,
    override_max_total_bytes: Optional[int] = None,
    volume_free_bytes_override: Optional[int] = None,
    volume_total_bytes_override: Optional[int] = None,
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

    # P1-M / F05: Strict integer MIN-intersection disk budget contract BEFORE claim!
    bud_res = compute_effective_disk_budget_contract(
        grant_caps=verified_grant.caps,
        plan_caps=plan.caps,
        target_path=Path(verified_grant.destination_root),
        repo_root=repo_root,
        oos_roots=oos_roots,
        override_max_chunk_bytes=override_max_chunk_bytes,
        override_max_total_bytes=override_max_total_bytes,
        volume_free_bytes_override=volume_free_bytes_override,
        volume_total_bytes_override=volume_total_bytes_override,
    )
    if not bud_res["valid"] or bud_res["contract"] is None:
        viols.extend(bud_res["violations"])

    if viols:
        err_status = "BLOCKED_BUDGET_SCOPE" if (not bud_res["valid"] and not any("RESOURCE_PREFLIGHT" in v for v in viols)) else "BLOCKED_ACQUISITION_SESSION_PRECHECK"
        return {
            "built": False,
            "status": err_status,
            "context": None,
            "violations": viols,
        }

    eff_conc = max(1, min(int(plan.max_concurrency), int(verified_grant.max_concurrency), 4 if qa_loopback_mode else 2, GLOBAL_MAX_WORKERS_R6))
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
        "budget_contract_digest": bud_res["contract"]["contract_digest"],
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
        budget_contract=bud_res["contract"],
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
    custody: Optional[R6CustodyJournal] = None,
    resource_sample_fn: Callable[[], Mapping[str, Any]],
    expected_volumes: Mapping[str, Path],
    session_deadline_monotonic: float,
    cancel_event: threading.Event,
    utc_now_fn: Callable[[], datetime],
    allow_fixture_resource_sample: bool = False,
    observed_instrument_meta: Optional[Mapping[str, Any]] = None,
    post_exit_hook: Optional[Callable[[], None]] = None,
    custody_queue: Optional[R6A1BoundedOrderedCustodyQueue] = None,
    disk_budget_ledger: Optional[R6A1DiskBudgetLedger] = None,
    task_ordinal: int = 0,
    get_cancel_epoch: Optional[Callable[[], int]] = None,
    max_chunk_bytes_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    F05 (REST) + P1-L/P1-M: Executes a single REST task through F12 -> F19 -> validators -> F23.
    When `custody_queue` is provided (ALWAYS inside `run_r6_a1_acquisition_session`), workers NEVER
    touch `R6CustodyJournal` directly — they pre-reserve peak disk bytes in `R6A1DiskBudgetLedger`
    and enqueue immutable `R6A1CustodyChunkEnvelope` records for the single custody writer thread!
    """
    ep = registry.endpoints[task.endpoint_id]
    loopback_url = f"{ctx.qa_http_base_url}{ep.path}" if ctx.qa_loopback_mode and ctx.qa_http_base_url else None
    lifecycle_traces: List[Dict[str, Any]] = []
    attempt_records: List[Dict[str, Any]] = []
    committed_chunks: List[Dict[str, Any]] = []
    enqueued_chunks: List[Dict[str, Any]] = []
    violations: List[str] = []
    raw_bytes_total = 0
    decoded_bytes_total = 0
    requests_count = 0
    chunk_ord_box = [0]

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
        # Re-check cancellation strictly BEFORE persisting/enqueueing to custody!
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

        # P1-L / P1-M path: enqueue immutable envelope for single custody writer (NEVER direct worker write!)
        if custody_queue is not None:
            chunk_ord_box[0] += 1
            c_ord = chunk_ord_box[0]
            rows_list = list(norm_rows)
            norm_b_pre = json.dumps(rows_list, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            if max_chunk_bytes_limit is not None and (len(raw_b) + len(norm_b_pre)) > int(max_chunk_bytes_limit):
                v = f"CHUNK_RAW_PLUS_NORM_EXCEEDS_CONTRACT:{len(raw_b) + len(norm_b_pre)}>{max_chunk_bytes_limit}"
                violations.append(v)
                return {"persisted": False, "status": "REJECTED_CHUNK_SIZE_EXCEEDED", "violations": [v]}

            dsk_res_id: Optional[str] = None
            if disk_budget_ledger is not None:
                peak_b = compute_chunk_conservative_peak_disk_bytes(raw_bytes_len=len(raw_b), norm_bytes_len=len(norm_b_pre))["conservative_peak_bytes"]
                dsk_res_id = f"dsk_{ctx.session_id}_{task_ordinal:02d}_{c_ord:02d}_{chunk_id}"
                r_dsk = disk_budget_ledger.reserve_r6_disk_bytes(
                    session_id=ctx.session_id,
                    reservation_id=dsk_res_id,
                    write_class="chunk_persist",
                    requested_bytes=peak_b,
                )
                if not r_dsk["reserved"]:
                    violations.extend(r_dsk["violations"])
                    return {"persisted": False, "status": str(r_dsk["status"]), "violations": r_dsk["violations"]}

            env_res = build_r6_a1_custody_envelope(
                session_id=ctx.session_id,
                run_id=ctx.run_id,
                partition=ctx.partition,
                task_ordinal=task_ordinal,
                task_id=task.task_id,
                chunk_ordinal=c_ord,
                chunk_id=chunk_id,
                attempt_id=t_res.attempt_id,
                plan_digest=plan.plan_digest,
                raw_payload=raw_b,
                normalized_rows=rows_list,
                provenance_receipt=prov,
                disk_reservation_id=dsk_res_id,
                cancel_epoch=int(get_cancel_epoch()) if get_cancel_epoch is not None else 0,
            )
            if not env_res["valid"] or env_res["envelope"] is None:
                if disk_budget_ledger is not None and dsk_res_id:
                    disk_budget_ledger.release_r6_disk_reservation(session_id=ctx.session_id, reservation_id=dsk_res_id, proven_unused_or_deleted=True, reason="ENVELOPE_BUILD_REJECTED")
                violations.extend(env_res["violations"])
                return {"persisted": False, "status": env_res["status"], "violations": env_res["violations"]}

            enq = custody_queue.put_envelope(env_res["envelope"], deadline_monotonic=session_deadline_monotonic, cancel_event=cancel_event)
            if not enq["enqueued"]:
                if disk_budget_ledger is not None and dsk_res_id:
                    disk_budget_ledger.release_r6_disk_reservation(session_id=ctx.session_id, reservation_id=dsk_res_id, proven_unused_or_deleted=True, reason="ENQUEUE_ABORTED_NO_IO")
                violations.extend(enq["violations"])
                return {"persisted": False, "status": enq["status"], "violations": enq["violations"]}
            enqueued_chunks.append({"chunk_id": chunk_id, "task_ordinal": task_ordinal, "chunk_ordinal": c_ord, "disk_reservation_id": dsk_res_id})
            return {"persisted": True, "queued_for_single_writer": True, "status": "ENQUEUED_FOR_SINGLE_WRITER", "violations": []}

        # Fallback direct standalone call (only when custody_queue is None)
        if custody is None:
            violations.append("MISSING_CUSTODY_JOURNAL")
            return {"persisted": False, "status": "REJECTED_MISSING_CUSTODY", "violations": ["MISSING_CUSTODY_JOURNAL"]}
        pers = custody.persist_r6_verified_chunk(
            chunk_id=chunk_id,
            raw_payload=raw_b,
            normalized_rows=norm_rows,
            provenance_receipt=prov,
            disk_budget_ledger=disk_budget_ledger,
            task_ordinal=task_ordinal,
            chunk_ordinal=chunk_ord_box[0] + 1,
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
            "ok": all_pers and (len(committed_chunks) > 0 or len(enqueued_chunks) > 0),
            "terminal_status": "COMPLETED" if (all_pers and (len(committed_chunks) > 0 or len(enqueued_chunks) > 0)) else "FAILED_CUSTODY_PERSIST",
            "valid_rows_count": int(ohlcv_res["canonical_valid_count"]),
            "invalid_rows_count": int(ohlcv_res["invalid_rows_count"]),
            "raw_bytes": raw_bytes_total, "decoded_bytes": decoded_bytes_total, "requests_count": requests_count,
            "ohlcv_result": {k: v for k, v in ohlcv_res.items() if k != "canonical_rows"},
            "attempt_records": attempt_records, "committed_chunks": committed_chunks, "enqueued_chunks": enqueued_chunks,
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
    custody: Optional[R6CustodyJournal] = None,
    resource_sample_fn: Callable[[], Mapping[str, Any]],
    expected_volumes: Mapping[str, Path],
    session_deadline_monotonic: float,
    cancel_event: threading.Event,
    utc_now_fn: Callable[[], datetime],
    allow_fixture_resource_sample: bool = False,
    observed_instrument_meta: Optional[Mapping[str, Any]] = None,
    custody_queue: Optional[R6A1BoundedOrderedCustodyQueue] = None,
    disk_budget_ledger: Optional[R6A1DiskBudgetLedger] = None,
    task_ordinal: int = 0,
    get_cancel_epoch: Optional[Callable[[], int]] = None,
    max_chunk_bytes_limit: Optional[int] = None,
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
    chunks_out: List[Dict[str, Any]] = []
    enqueued_out: List[Dict[str, Any]] = []
    ok_ws = False
    ws_term_status = "FAILED_CUSTODY_PERSIST"
    chunk_id_ws = f"{task.task_id}_chunk_01"

    if custody_queue is not None:
        raw_b_ws = sup["raw_payload_bytes"]
        norm_b_ws = json.dumps(norm_events, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if max_chunk_bytes_limit is not None and (len(raw_b_ws) + len(norm_b_ws)) > int(max_chunk_bytes_limit):
            v = f"WS_CHUNK_RAW_PLUS_NORM_EXCEEDS_CONTRACT:{len(raw_b_ws) + len(norm_b_ws)}>{max_chunk_bytes_limit}"
            violations.append(v)
            ws_term_status = "REJECTED_CHUNK_SIZE_EXCEEDED"
        else:
            dsk_res_id_ws: Optional[str] = None
            res_ok = True
            if disk_budget_ledger is not None:
                peak_ws = compute_chunk_conservative_peak_disk_bytes(raw_bytes_len=len(raw_b_ws), norm_bytes_len=len(norm_b_ws))["conservative_peak_bytes"]
                dsk_res_id_ws = f"dsk_{ctx.session_id}_{task_ordinal:02d}_01_{chunk_id_ws}"
                r_dsk = disk_budget_ledger.reserve_r6_disk_bytes(
                    session_id=ctx.session_id,
                    reservation_id=dsk_res_id_ws,
                    write_class="chunk_persist",
                    requested_bytes=peak_ws,
                )
                if not r_dsk["reserved"]:
                    res_ok = False
                    violations.extend(r_dsk["violations"])
                    ws_term_status = str(r_dsk["status"])
            if res_ok:
                env_res = build_r6_a1_custody_envelope(
                    session_id=ctx.session_id,
                    run_id=ctx.run_id,
                    partition=ctx.partition,
                    task_ordinal=task_ordinal,
                    task_id=task.task_id,
                    chunk_ordinal=1,
                    chunk_id=chunk_id_ws,
                    attempt_id=t_res.attempt_id,
                    plan_digest=plan.plan_digest,
                    raw_payload=raw_b_ws,
                    normalized_rows=norm_events,
                    provenance_receipt=prov,
                    disk_reservation_id=dsk_res_id_ws,
                    cancel_epoch=int(get_cancel_epoch()) if get_cancel_epoch is not None else 0,
                )
                if not env_res["valid"] or env_res["envelope"] is None:
                    if disk_budget_ledger is not None and dsk_res_id_ws:
                        disk_budget_ledger.release_r6_disk_reservation(session_id=ctx.session_id, reservation_id=dsk_res_id_ws, proven_unused_or_deleted=True, reason="WS_ENVELOPE_REJECTED")
                    violations.extend(env_res["violations"])
                    ws_term_status = env_res["status"]
                else:
                    enq = custody_queue.put_envelope(env_res["envelope"], deadline_monotonic=session_deadline_monotonic, cancel_event=cancel_event)
                    if not enq["enqueued"]:
                        if disk_budget_ledger is not None and dsk_res_id_ws:
                            disk_budget_ledger.release_r6_disk_reservation(session_id=ctx.session_id, reservation_id=dsk_res_id_ws, proven_unused_or_deleted=True, reason="WS_ENQUEUE_ABORTED")
                        violations.extend(enq["violations"])
                        ws_term_status = enq["status"]
                    else:
                        ok_ws = True
                        ws_term_status = "COMPLETED"
                        enqueued_out.append({"chunk_id": chunk_id_ws, "task_ordinal": task_ordinal, "chunk_ordinal": 1, "disk_reservation_id": dsk_res_id_ws})
    else:
        if custody is None:
            violations.append("MISSING_CUSTODY_JOURNAL")
            ws_term_status = "REJECTED_MISSING_CUSTODY"
        else:
            pers = custody.persist_r6_verified_chunk(
                chunk_id=chunk_id_ws,
                raw_payload=sup["raw_payload_bytes"],
                normalized_rows=norm_events,
                provenance_receipt=prov,
                disk_budget_ledger=disk_budget_ledger,
                task_ordinal=task_ordinal,
                chunk_ordinal=1,
            )
            ok_ws = bool(pers.get("persisted"))
            ws_term_status = "COMPLETED" if ok_ws else str(pers.get("status"))
            if ok_ws:
                chunks_out.append({
                    "chunk_id": chunk_id_ws,
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
        "ok": ok_ws, "terminal_status": ws_term_status,
        "valid_rows_count": len(norm_events), "invalid_rows_count": invalid_ev,
        "raw_bytes": len(sup["raw_payload_bytes"]), "decoded_bytes": len(sup["decoded_payload_bytes"]),
        "requests_count": 0, "connections_opened": int(acc.get("connections_opened", 0)),
        "subscriptions_sent": int(acc.get("subscriptions_sent", 0)),
        "reconnects_used": int(acc.get("reconnects_used", 0)),
        "messages_count": int(acc.get("total_messages", 0)),
        "bbo_coverage": bbo_cov, "attempt_records": [att_rec], "committed_chunks": chunks_out, "enqueued_chunks": enqueued_out,
        "lifecycle_traces": traces, "violations": violations,
    }


# =====================================================================
# F03 / F06 / F07 / P1-L / P1-M / P1-N: Single-Writer Orchestrator & Exception-Safe Finalization
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
    deadline_expired: bool = False,
    original_error: Optional[str] = None,
    cleanup_errors: Sequence[str] = (),
    ledger_terminal_verified_from_db: bool = True,
    unproven_terminal: bool = False,
    unreaped_worker_detected: bool = False,
    disk_budget_snapshot: Optional[Mapping[str, Any]] = None,
    custody_queue_stats: Optional[Mapping[str, Any]] = None,
    writer_report: Optional[Mapping[str, Any]] = None,
    committed_chunks_override: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    F07 / F11: Computes immutable terminal status & exit code STRICTLY via
    `derive_r6_a1_terminal_disposition()` from observed durable facts — NEVER equating durable
    ledger `SPENT`/`FINALIZED` after `ABORTED` with acquisition PASS!
    """
    expected_tasks = len(ctx.tasks) if ctx is not None else 0
    completed_tasks = sum(1 for t in task_results if t.get("ok") is True)
    failed_tasks = sum(1 for t in task_results if t.get("ok") is not True)
    if committed_chunks_override is not None:
        committed_chunks = list(committed_chunks_override)
    else:
        committed_chunks = [c for t in task_results for c in (t.get("committed_chunks") or ())]
    total_raw_bytes = sum(int(c.get("raw_bytes_len", 0)) for c in committed_chunks)
    total_norm_rows = sum(int(c.get("norm_rows_count", 0)) for c in committed_chunks)
    all_attempts = [a for t in task_results for a in (t.get("attempt_records") or ())]
    all_reaped = all(bool(a.get("reaped_verified")) for a in all_attempts if a.get("child_pid")) and (not unreaped_worker_detected)
    custody_ok = bool(custody_audit and custody_audit.get("valid") is True)
    unresolved_dsk = int((disk_budget_snapshot or {}).get("active_reservations_count", 0)) if disk_budget_snapshot else 0

    disp = derive_r6_a1_terminal_disposition(
        has_context=(ctx is not None),
        qa_loopback_mode=bool(ctx.qa_loopback_mode) if ctx else False,
        expected_tasks=expected_tasks,
        completed_tasks=completed_tasks,
        failed_tasks=failed_tasks,
        committed_chunks_count=len(committed_chunks),
        total_raw_bytes=total_raw_bytes,
        total_norm_rows=total_norm_rows,
        custody_readonly_valid=custody_ok,
        all_children_reaped_verified=all_reaped,
        unreaped_worker_detected=unreaped_worker_detected,
        ledger_final_status=ledger_final_status,
        ledger_terminal_verified_from_db=ledger_terminal_verified_from_db,
        unproven_terminal=unproven_terminal,
        quarantined=quarantined,
        cancelled=cancelled,
        deadline_expired=deadline_expired,
        original_error=original_error,
        cleanup_errors=cleanup_errors,
        violations=violations,
        unresolved_disk_reservations=unresolved_dsk,
        claim_status=claim_status,
    )
    final_status = disp["acquisition_status"]
    exit_code = int(disp["exit_code"])

    core = {
        "spec_version": SPEC_VERSION_KH03_R6_A1,
        "schema_version": SCHEMA_VERSION_KH03_R6_A1,
        "status": final_status,
        "exit_code": exit_code,
        "terminal_disposition": disp,
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
        "ledger_final_status": disp["ledger_terminal_state"],
        "ledger_terminal_verified_from_db": bool(ledger_terminal_verified_from_db),
        "original_error": original_error,
        "cleanup_errors": list(cleanup_errors),
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
        "unreaped_worker_detected": bool(unreaped_worker_detected),
        "custody_readonly_valid": custody_ok,
        "custody_tip_hash": custody_audit.get("tip_hash") if custody_audit else None,
        "custody_semantic_chain_digest": custody_audit.get("semantic_chain_digest") if custody_audit else None,
        "dataset_manifest_sha256": dataset_manifest.get("manifest_sha256") if dataset_manifest else None,
        "budget_contract": dict(ctx.budget_contract) if (ctx and ctx.budget_contract) else None,
        "disk_budget_snapshot": disk_budget_snapshot,
        "custody_queue_stats": custody_queue_stats,
        "writer_summary": {
            "status": writer_report.get("status"),
            "authority_id": writer_report.get("authority_id"),
            "writer_pid": writer_report.get("writer_pid"),
            "writer_thread_ident": writer_report.get("writer_thread_ident"),
            "committed_count": len(writer_report.get("committed_chunks") or ()),
        } if writer_report else None,
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


def finalize_r6_a1_session_safely(
    *,
    ctx: R6A1AcquisitionSessionContext,
    verified_grant: R6VerifiedGrant,
    ledger: R6DurableGrantLedger,
    lease: Optional[R6DurableSessionLease],
    claim_attempted: bool,
    plan: R6BoundCapturePlan,
    custody: Optional[R6CustodyJournal],
    writer_authority: Optional[R6A1CustodyWriterAuthority],
    writer_thread: Optional[threading.Thread],
    writer_report_box: List[Dict[str, Any]],
    custody_queue: Optional[R6A1BoundedOrderedCustodyQueue],
    disk_budget_ledger: Optional[R6A1DiskBudgetLedger],
    task_results: Sequence[Mapping[str, Any]],
    state_history: List[str],
    resource_samples: List[Dict[str, Any]],
    resource_sample_fn: Callable[[], Mapping[str, Any]],
    violations: List[str],
    original_error: Optional[str],
    quarantine_required: bool,
    cancelled: bool,
    deadline_expired: bool,
    cancel_event: threading.Event,
    bump_cancel_epoch: Callable[[], int],
    utc_now_fn: Callable[[], datetime],
    metadata_versions: Sequence[Mapping[str, Any]],
    cleanup_deadline_sec: float = 10.0,
    inject_db_finalize_error: bool = False,
    simulate_unreaped_worker: bool = False,
) -> Dict[str, Any]:
    """
    F06 / F09 / F11 (P1-N): Exception-isolated, bounded-deadline session finalizer!
    Every substep runs inside its own `try/except`: telemetry/custody/temp-cleanup errors are
    recorded in `cleanup_errors` and NEVER prevent durable ledger finalization or quarantine!
    Order:
      1. Bump cancel_epoch + set cancel_event + abort/close custody_queue (releasing proven-unused dropped reservations)
      2. Drain & join single custody writer thread + release writer authority + close OS custody lock
      3. Check owned descendants reap (unreaped worker -> quarantine + poison store!)
      4. Guarded post-run telemetry sample (failure NEVER skips ledger finalize!)
      5. Guarded temp cleanup + read-only custody verify + disk budget fixed overhead reconciliation
      6. Manifest seal ONLY if ALL positive invariants hold (writer stopped, 0 intents, 0 quarantine, 0 errors, complete coverage)
      7. CAS durable ledger finalize (or claim-exception reconcile if lease wasn't returned) + DB read-back verification!
    """
    cleanup_errors: List[str] = []
    cleanup_deadline = time.monotonic() + max(2.0, float(cleanup_deadline_sec))
    unreaped_worker = bool(simulate_unreaped_worker)

    # Substep 1: Stop intake & cancel epoch so ZERO late writes can ever occur!
    try:
        bump_cancel_epoch()
        if cancelled or deadline_expired or original_error or quarantine_required or violations:
            cancel_event.set()
            if custody_queue is not None:
                dropped_envs = custody_queue.abort("FINALIZE_CLEANUP_ABORT")
                if disk_budget_ledger is not None:
                    for de in dropped_envs:
                        if de.disk_reservation_id:
                            try:
                                disk_budget_ledger.release_r6_disk_reservation(
                                    session_id=ctx.session_id,
                                    reservation_id=de.disk_reservation_id,
                                    proven_unused_or_deleted=True,
                                    reason="DROPPED_BEFORE_CUSTODY_WRITE",
                                )
                            except Exception as exc_r:
                                cleanup_errors.append(f"DROP_RES_RELEASE_ERR:{exc_r}")
        elif custody_queue is not None:
            custody_queue.close_intake()
    except Exception as exc:
        cleanup_errors.append(f"SUBSTEP1_STOP_INTAKE_ERR:{type(exc).__name__}:{exc}")
        quarantine_required = True

    # Substep 2: Join single custody writer thread & release writer authority + OS lock!
    writer_rep: Optional[Dict[str, Any]] = None
    try:
        if writer_thread is not None and writer_thread.is_alive():
            rem_join = max(0.5, cleanup_deadline - time.monotonic())
            writer_thread.join(timeout=rem_join)
            if writer_thread.is_alive():
                unreaped_worker = True
                quarantine_required = True
                cleanup_errors.append("SINGLE_CUSTODY_WRITER_THREAD_UNDRAINED")
        if writer_report_box:
            writer_rep = writer_report_box[0]
            if not writer_rep.get("ok") and writer_rep.get("violations"):
                violations.extend(v for v in writer_rep["violations"] if v not in violations)
                if writer_rep.get("status") in ("WRITER_QUARANTINED", "WRITER_EXCEPTION"):
                    quarantine_required = True
    except Exception as exc:
        cleanup_errors.append(f"SUBSTEP2_WRITER_JOIN_ERR:{type(exc).__name__}:{exc}")
        quarantine_required = True
    finally:
        if custody is not None:
            try:
                custody.release_writer_authority(writer_authority)
            except Exception as exc:
                cleanup_errors.append(f"SUBSTEP2_RELEASE_AUTH_ERR:{exc}")
                quarantine_required = True

    # Substep 3: Verify ALL spawned owned children reaped (unreaped -> quarantine + hard block new sessions!)
    try:
        all_atts = [a for t in task_results for a in (t.get("attempt_records") or ())]
        if any(a.get("child_pid") and not bool(a.get("reaped_verified")) for a in all_atts):
            unreaped_worker = True
        if unreaped_worker:
            quarantine_required = True
            violations.append("UNREAPED_OWNED_WORKER_QUARANTINED_STORE_BLOCKED")
            ledger.mark_store_unproven_terminal("UNREAPED_OWNED_WORKER_DETECTED", session_id=ctx.session_id, grant_identity_key=verified_grant.grant_identity_key)
    except Exception as exc:
        cleanup_errors.append(f"SUBSTEP3_REAP_CHECK_ERR:{exc}")
        quarantine_required = True

    # Substep 4: Guarded post-run telemetry sample (NEVER blocks ledger finalization if telemetry raises!)
    try:
        samp = resource_sample_fn()
        if isinstance(samp, Mapping):
            resource_samples.append(dict(samp))
        else:
            cleanup_errors.append("FINALIZE_TELEMETRY_NOT_MAPPING")
    except Exception as exc:
        cleanup_errors.append(f"FINALIZE_TELEMETRY_ERROR:{type(exc).__name__}:{exc}")

    # Substep 5: Guarded temp cleanup, custody read-only audit & disk budget fixed reservation reconciliation
    custody_audit: Optional[Dict[str, Any]] = None
    if custody is not None:
        try:
            cln = custody.cleanup_owned_temp_files_safe(disk_budget_ledger=disk_budget_ledger)
            if not cln["all_owned_temps_deleted"]:
                quarantine_required = True
                cleanup_errors.append("CUSTODY_OWNED_TEMP_UNLINK_FAILED_QUARANTINED")
                violations.append("CUSTODY_OWNED_TEMP_UNLINK_FAILED_QUARANTINED")
        except Exception as exc:
            quarantine_required = True
            cleanup_errors.append(f"SUBSTEP5_TEMP_CLEANUP_ERR:{type(exc).__name__}:{exc}")

        try:
            custody.close()
        except Exception as exc:
            cleanup_errors.append(f"SUBSTEP5_CUSTODY_CLOSE_ERR:{exc}")

        try:
            custody_audit = verify_r6_custody_readonly(
                custody.store_dir,
                expected_partition=ctx.partition,
                expected_run_id=ctx.run_id,
                ledger_store_root=ledger.store_root,
                expected_volume_id=custody.expected_volume_id,
            )
            if not custody_audit["valid"]:
                quarantine_required = True
                violations.extend(v for v in custody_audit["violations"] if v not in violations)
        except Exception as exc:
            quarantine_required = True
            cleanup_errors.append(f"SUBSTEP5_CUSTODY_AUDIT_ERR:{type(exc).__name__}:{exc}")

    # Substep 6: Seal QA dataset manifest ONLY if ALL positive invariants hold!
    dataset_manifest: Optional[Dict[str, Any]] = None
    committed_all = list(writer_rep.get("committed_chunks") or ()) if writer_rep is not None else [c for t in task_results for c in (t.get("committed_chunks") or ())]
    expected_tasks = len(ctx.tasks)
    completed_tasks = sum(1 for t in task_results if t.get("ok") is True)
    missing_cov = max(0, expected_tasks - completed_tasks)

    if disk_budget_ledger is not None:
        try:
            # Reconcile fixed overhead reservation (actual manifest/anchor/WAL bytes vs reserved)
            fixed_rid = f"fixed_overhead_{ctx.session_id}"
            wal_b = disk_budget_ledger.checkpoint_wal_bounded()
            clean_reconcile = (not quarantine_required and not original_error and not cleanup_errors and not unreaped_worker)
            if clean_reconcile:
                disk_budget_ledger.reconcile_r6_disk_actual(
                    session_id=ctx.session_id,
                    reservation_id=fixed_rid,
                    actual_committed_bytes=min(wal_b, DEFAULT_EMERGENCY_AUDIT_RESERVE_BYTES + DEFAULT_MANIFEST_AND_ANCHOR_RESERVE_BYTES + DEFAULT_SQLITE_WAL_OVERHEAD_RESERVE_BYTES),
                    uncertain_bytes=0,
                    reason="FIXED_OVERHEAD_RECONCILED",
                )
            else:
                disk_budget_ledger.release_r6_disk_reservation(
                    session_id=ctx.session_id,
                    reservation_id=fixed_rid,
                    proven_unused_or_deleted=False,
                    reason="FIXED_OVERHEAD_UNCERTAIN_ON_ERROR",
                )
        except Exception as exc:
            cleanup_errors.append(f"SUBSTEP6_DISK_FIXED_RECONCILE_ERR:{exc}")

    dsk_snap: Optional[Dict[str, Any]] = None
    if disk_budget_ledger is not None:
        try:
            dsk_snap = disk_budget_ledger.snapshot_r6_disk_budget(ctx.session_id)
        except Exception as exc:
            cleanup_errors.append(f"SUBSTEP6_DISK_SNAP_ERR:{exc}")

    unresolved_dsk_n = int((dsk_snap or {}).get("active_reservations_count", 0)) if dsk_snap else 0

    try:
        if (
            custody is not None
            and custody_audit is not None
            and custody_audit.get("valid") is True
            and int(custody_audit.get("committed_chunks", 0)) > 0
            and not quarantine_required
            and not cancelled
            and not deadline_expired
            and not original_error
            and not cleanup_errors
            and not unreaped_worker
            and missing_cov == 0
            and unresolved_dsk_n == 0
        ):
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

            dm = seal_r6_dev_dataset_manifest(
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
                active_writer_present=False,
                pending_intents_count=0,
                quarantined_artifacts_count=0,
                unresolved_reservations_count=unresolved_dsk_n,
                cancelled=cancelled,
                deadline_expired=deadline_expired,
                missing_task_coverage_count=missing_cov,
            )
            if dm.get("sealed") is True:
                dataset_manifest = dm
                state_history.append("SEALED")
                _atomic_write_json_fsync(custody.store_dir / "qa_dataset_manifest.json", dataset_manifest)
            else:
                cleanup_errors.append(f"MANIFEST_SEAL_REJECTED:{dm.get('violations')}")
                quarantine_required = True
    except Exception as exc:
        cleanup_errors.append(f"SUBSTEP6_MANIFEST_SEAL_ERR:{type(exc).__name__}:{exc}")
        quarantine_required = True

    # Substep 7: CAS Durable Ledger Finalization (or post-claim exception reconciliation) + DB verification!
    ledger_final_status = "NOT_CLAIMED"
    ledger_terminal_verified = True
    unproven_terminal = False
    snap: Optional[Dict[str, Any]] = None

    fin_reason = (
        "QA_E2E_ACQUISITION_COMPLETE"
        if (not quarantine_required and not cancelled and not deadline_expired and not original_error and not cleanup_errors and not violations and missing_cov == 0)
        else ("ABORTED_CANCELLED" if cancelled else ("ABORTED_DEADLINE" if deadline_expired else "PARTIAL_OR_QUARANTINED"))
    )

    try:
        if lease is not None:
            fin_res = ledger.finalize_r6_session(
                lease,
                quarantine=bool(quarantine_required),
                reason=fin_reason,
                actual_now=utc_now_fn(),
                inject_db_finalize_error=inject_db_finalize_error,
            )
            ledger_final_status = str(fin_res.get("status") or "UNPROVEN_TERMINAL")
            ledger_terminal_verified = bool(fin_res.get("terminal_verified_from_db"))
            if not fin_res.get("finalized") or not ledger_terminal_verified or ledger_final_status == "UNPROVEN_TERMINAL":
                unproven_terminal = True
                cleanup_errors.extend(fin_res.get("violations") or ["LEDGER_FINALIZE_UNPROVEN_TERMINAL"])
            state_history.append("FINALIZED" if (not quarantine_required and not unproven_terminal) else ("UNPROVEN_TERMINAL" if unproven_terminal else "QUARANTINED"))
            snap = ledger.snapshot_grant(lease.grant_identity_key)
        elif claim_attempted:
            rec_cl = reconcile_r6_a1_claim_after_exception(
                ledger,
                verified_grant,
                plan,
                ctx.session_id,
                actual_now=utc_now_fn(),
                quarantine=True,
                reason=f"CLAIM_EXCEPTION_RECONCILED:{original_error or 'UNKNOWN'}",
            )
            ledger_final_status = str(rec_cl.get("status") or "UNPROVEN_TERMINAL")
            ledger_terminal_verified = bool(rec_cl.get("terminal_verified_from_db"))
            if not rec_cl.get("reconciled") or not ledger_terminal_verified or ledger_final_status == "UNPROVEN_TERMINAL":
                unproven_terminal = True
                cleanup_errors.extend(rec_cl.get("violations") or ["CLAIM_RECONCILE_UNPROVEN"])
            state_history.append("CLAIM_RECONCILED_TERMINAL" if ledger_terminal_verified else "UNPROVEN_TERMINAL")
            snap = ledger.snapshot_grant(verified_grant.grant_identity_key)
    except Exception as exc:
        unproven_terminal = True
        ledger_terminal_verified = False
        ledger_final_status = "UNPROVEN_TERMINAL"
        cleanup_errors.append(f"SUBSTEP7_LEDGER_FINALIZE_EXC:{type(exc).__name__}:{exc}")
        try:
            ledger.mark_store_unproven_terminal(f"FINALIZE_EXC:{exc}", session_id=ctx.session_id, grant_identity_key=verified_grant.grant_identity_key)
        except Exception:
            pass

    q_stats = custody_queue.snapshot_stats() if custody_queue is not None else None
    claim_st = "CLAIMED_ONE_SHOT" if lease is not None else ("CLAIM_EXCEPTION_RECONCILED" if claim_attempted else "NOT_CLAIMED")
    return build_r6_a1_session_receipt(
        ctx=ctx,
        state_history=state_history,
        claim_status=claim_st,
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
        deadline_expired=deadline_expired,
        original_error=original_error,
        cleanup_errors=cleanup_errors,
        ledger_terminal_verified_from_db=ledger_terminal_verified,
        unproven_terminal=unproven_terminal,
        unreaped_worker_detected=unreaped_worker,
        disk_budget_snapshot=dsk_snap,
        custody_queue_stats=q_stats,
        writer_report=writer_rep,
        committed_chunks_override=committed_all,
    )


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
    """Backward-compatible wrapper delegating cleanly to `finalize_r6_a1_session_safely`."""
    return finalize_r6_a1_session_safely(
        ctx=ctx,
        verified_grant=type("_VG", (), {"grant_identity_key": lease.grant_identity_key})(),  # type: ignore[arg-type]
        ledger=ledger,
        lease=lease,
        claim_attempted=True,
        plan=plan,
        custody=custody,
        writer_authority=None,
        writer_thread=None,
        writer_report_box=[],
        custody_queue=None,
        disk_budget_ledger=None,
        task_results=task_results,
        state_history=state_history,
        resource_samples=list(resource_samples),
        resource_sample_fn=lambda: {},
        violations=violations,
        original_error=None,
        quarantine_required=quarantine_required,
        cancelled=cancelled,
        deadline_expired=False,
        cancel_event=threading.Event(),
        bump_cancel_epoch=lambda: 1,
        utc_now_fn=utc_now_fn,
        metadata_versions=metadata_versions,
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
    # Corrective P1-L / P1-M / P1-N explicit controls & fault hooks
    disk_budget_ledger: Optional[R6A1DiskBudgetLedger] = None,
    queue_max_items: int = 8,
    queue_max_bytes: Optional[int] = None,
    claim_crash_hook: Optional[Callable[[str], None]] = None,
    post_claim_hook: Optional[Callable[[], None]] = None,
    writer_per_commit_hook: Optional[Callable[[Any], None]] = None,
    inject_db_finalize_error: bool = False,
    simulate_unreaped_worker: bool = False,
    propagate_base_exceptions: bool = True,
    receipt_sink: Optional[List[Dict[str, Any]]] = None,
    volume_free_bytes_override: Optional[int] = None,
    volume_total_bytes_override: Optional[int] = None,
) -> Dict[str, Any]:
    """
    F01 / F03 / F05 / F09: Single end-to-end acquisition orchestration authority (P1-L + P1-M + P1-N).
      - All pre-claim checks (external block, strict MIN disk budget contract, initial telemetry)
        execute BEFORE claim so pre-claim exceptions consume ZERO grants/workers/custody!
      - Enters guarded `try / except BaseException / finally` BEFORE calling claim!
      - Parallel transport/validation workers NEVER write custody directly — they enqueue immutable
        envelopes into `R6A1BoundedOrderedCustodyQueue` consumed by ONE single custody writer thread
        holding `R6A1CustodyWriterAuthority` + OS cross-process store lock!
      - ZERO `max()` cap enlargement: `max_chunk_bytes` and `max_total_bytes` come strictly from
        `compute_effective_disk_budget_contract()` MIN intersection!
      - Exception-isolated `finalize_r6_a1_session_safely()` runs in `finally:` (no `return` in `finally`).
    """
    state_history: List[str] = ["PRECHECK", "PLAN_BOUND", "PARTITION_ASSIGNED"]
    violations: List[str] = []
    resource_samples: List[Dict[str, Any]] = []
    task_results: List[Dict[str, Any]] = []
    all_lifecycle_traces: List[Dict[str, Any]] = []
    ev_cancel = cancel_event if cancel_event is not None else threading.Event()
    lifecycle_lock = threading.Lock()
    cancel_epoch_box = [0]

    def _get_cancel_epoch() -> int:
        with lifecycle_lock:
            return cancel_epoch_box[0]

    def _bump_cancel_epoch() -> int:
        with lifecycle_lock:
            cancel_epoch_box[0] += 1
            return cancel_epoch_box[0]

    # 1. Hard A1 external block BEFORE claiming any grant!
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

    # 2. Pre-claim strict MIN disk budget contract (ZERO `max()` enlargement!)
    bud_res = compute_effective_disk_budget_contract(
        grant_caps=verified_grant.caps,
        plan_caps=plan.caps,
        target_path=Path(verified_grant.destination_root),
        repo_root=repo_root,
        oos_roots=oos_roots,
        volume_free_bytes_override=volume_free_bytes_override,
        volume_total_bytes_override=volume_total_bytes_override,
    )
    if not bud_res["valid"] or bud_res["contract"] is None:
        return build_r6_a1_session_receipt(
            ctx=ctx,
            state_history=state_history,
            claim_status=str(bud_res["status"]),
            lease=None,
            plan=plan,
            task_results=[],
            custody_audit=None,
            dataset_manifest=None,
            ledger_snapshot=None,
            ledger_final_status="NOT_CLAIMED",
            resource_samples=[],
            violations=list(bud_res["violations"]),
            quarantined=False,
            cancelled=False,
        )
    bud_contract = bud_res["contract"]

    # 3. Pre-claim initial resource sample (exception BEFORE claim -> ZERO grant consumption!)
    try:
        init_samp = resource_sample_fn()
        if not isinstance(init_samp, Mapping):
            raise ValueError("PRECLAIM_TELEMETRY_NOT_MAPPING")
        resource_samples.append(dict(init_samp))
        rg_pre = validate_r6_resource_guard(
            init_samp,
            expected_volumes=expected_volumes,
            phase="preflight",
            allow_fixture_sample=allow_fixture_resource_sample,
        )
        if not rg_pre["ok"]:
            return build_r6_a1_session_receipt(
                ctx=ctx,
                state_history=state_history,
                claim_status="BLOCKED_PRECLAIM_RESOURCE_GUARD",
                lease=None,
                plan=plan,
                task_results=[],
                custody_audit=None,
                dataset_manifest=None,
                ledger_snapshot=None,
                ledger_final_status="NOT_CLAIMED",
                resource_samples=resource_samples,
                violations=list(rg_pre["violations"]),
                quarantined=False,
                cancelled=False,
            )
    except Exception as exc:
        return build_r6_a1_session_receipt(
            ctx=ctx,
            state_history=state_history,
            claim_status="FAILED_PRECLAIM_EXCEPTION",
            lease=None,
            plan=plan,
            task_results=[],
            custody_audit=None,
            dataset_manifest=None,
            ledger_snapshot=ledger.snapshot_grant(verified_grant.grant_identity_key) if not ledger.is_store_unproven_terminal() else None,
            ledger_final_status="NOT_CLAIMED",
            resource_samples=resource_samples,
            violations=[f"PRECLAIM_EXCEPTION:{type(exc).__name__}:{exc}"],
            original_error=f"{type(exc).__name__}:{exc}",
            quarantined=False,
            cancelled=False,
        )

    now_start = utc_now_fn()
    now_start_ms = int(now_start.astimezone(timezone.utc).timestamp() * 1000)
    rem_grant_sec = max(0.0, (verified_grant.expires_utc_ms - now_start_ms) / 1000.0)
    session_deadline_mono = time.monotonic() + min(float(plan.max_session_duration_sec), rem_grant_sec)

    # 4. Enter guarded lifecycle BEFORE calling claim (P1-N / F09)!
    claim_attempted = False
    lease: Optional[R6DurableSessionLease] = None
    custody: Optional[R6CustodyJournal] = None
    writer_authority: Optional[R6A1CustodyWriterAuthority] = None
    writer_thread: Optional[threading.Thread] = None
    writer_report_box: List[Dict[str, Any]] = []
    custody_queue: Optional[R6A1BoundedOrderedCustodyQueue] = None
    eff_disk_ledger: Optional[R6A1DiskBudgetLedger] = disk_budget_ledger
    quarantine_req = False
    deadline_expired = False
    original_error: Optional[str] = None
    base_exc_to_reraise: Optional[BaseException] = None
    metadata_versions: List[Dict[str, Any]] = []
    early_claim_rejection_receipt: Optional[Dict[str, Any]] = None
    final_receipt: Optional[Dict[str, Any]] = None

    try:
        claim_attempted = True
        claim_res = ledger.claim_r6_grant_session_atomic(
            verified_grant,
            plan,
            ctx.session_id,
            actual_now=now_start,
            crash_hook=claim_crash_hook,
        )
        if not claim_res.get("claimed") or claim_res.get("lease") is None:
            claim_attempted = False
            early_claim_rejection_receipt = build_r6_a1_session_receipt(
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
        else:
            lease = claim_res["lease"]
            state_history.append("CLAIMED")

            if post_claim_hook is not None:
                post_claim_hook()

            # Initialize atomic disk budget ledger bound to verified destination store volume
            if eff_disk_ledger is None:
                eff_disk_ledger = R6A1DiskBudgetLedger(
                    store_root=ledger.store_root,
                    volume_id=bud_contract["volume_id"],
                    global_cap_bytes=int(bud_contract["peak_physical_disk_cap_bytes"]),
                    safe_floor_bytes=int(bud_contract["volume_safe_floor_bytes"]),
                    volume_free_bytes_fn=(lambda: int(volume_free_bytes_override)) if volume_free_bytes_override is not None else None,
                )
            reg_dsk = eff_disk_ledger.register_session_budget(
                session_id=ctx.session_id,
                session_cap_bytes=int(bud_contract["peak_physical_disk_cap_bytes"]),
                emergency_audit_reserve_bytes=int(bud_contract["emergency_audit_reserve_bytes"]),
                manifest_and_wal_reserve_bytes=int(bud_contract["manifest_and_anchor_reserve_bytes"]) + int(bud_contract["sqlite_wal_overhead_reserve_bytes"]),
            )
            if not reg_dsk["registered"]:
                quarantine_req = True
                violations.extend(reg_dsk["violations"])
                raise RuntimeError(f"DISK_BUDGET_SESSION_REGISTER_FAILED:{reg_dsk['status']}")

            # STRICT MIN CAPS (ZERO `max(65_536, ...)` / `max(262_144, ...)` enlargement!)
            custody = R6CustodyJournal(
                approved_root=Path(verified_grant.destination_root),
                partition=ctx.partition,
                run_id=ctx.run_id,
                repo_root=repo_root,
                oos_roots=oos_roots,
                max_chunk_bytes=int(bud_contract["max_chunk_bytes"]),
                max_total_bytes=int(bud_contract["persisted_logical_dataset_cap_bytes"]),
                strict_authority_required=True,
                session_id=ctx.session_id,
                expected_volume_id=bud_contract["volume_id"],
            )
            wa_res = custody.acquire_writer_authority(
                session_id=ctx.session_id,
                cancel_epoch=_get_cancel_epoch(),
                deadline_monotonic=session_deadline_mono,
                disk_budget_ledger=eff_disk_ledger,
            )
            if not wa_res["acquired"] or wa_res["authority"] is None:
                quarantine_req = True
                violations.extend(wa_res["violations"])
                raise RuntimeError(f"CUSTODY_WRITER_AUTHORITY_DENIED:{wa_res['status']}")
            writer_authority = wa_res["authority"]

            if cancel_between_claim_and_first_attempt:
                ev_cancel.set()

            if ev_cancel.is_set():
                state_history.append("ABORTED")
                violations.append("CANCELLED_BEFORE_FIRST_ATTEMPT")
            elif time.monotonic() >= session_deadline_mono:
                deadline_expired = True
                state_history.append("ABORTED")
                violations.append("DEADLINE_EXPIRED_BEFORE_FIRST_ATTEMPT")
            else:
                # Start bounded ordered custody queue + SINGLE custody writer thread!
                eff_q_bytes = int(queue_max_bytes) if queue_max_bytes is not None else max(4096, int(bud_contract["ram_queue_cap_bytes"]))
                custody_queue = R6A1BoundedOrderedCustodyQueue(
                    ordered_task_ordinals=list(range(len(ctx.tasks))),
                    max_items=max(2, int(queue_max_items)),
                    max_bytes=eff_q_bytes,
                )

                def _writer_entry() -> None:
                    wres = run_r6_a1_single_custody_writer(
                        queue=custody_queue,
                        custody=custody,
                        writer_authority=writer_authority,
                        disk_budget_ledger=eff_disk_ledger,
                        deadline_monotonic=session_deadline_mono,
                        cancel_event=ev_cancel,
                        get_cancel_epoch=_get_cancel_epoch,
                        on_writer_error=lambda _msg: _bump_cancel_epoch(),
                        per_commit_hook=writer_per_commit_hook,
                    )
                    writer_report_box.append(wres)

                writer_thread = threading.Thread(target=_writer_entry, name=f"custody_writer_{ctx.session_id[:16]}", daemon=False)
                writer_thread.start()

                state_history.append("ACQUIRING")
                # 1. Mandatory metadata identity gate FIRST (task_ordinal=0)!
                meta_task = ctx.tasks[0]
                try:
                    meta_res = execute_r6_a1_rest_task(
                        ctx=ctx,
                        task=meta_task,
                        ledger=ledger,
                        lease=lease,
                        plan=plan,
                        verified_grant=verified_grant,
                        registry=registry,
                        custody=None,
                        resource_sample_fn=resource_sample_fn,
                        expected_volumes=expected_volumes,
                        session_deadline_monotonic=session_deadline_mono,
                        cancel_event=ev_cancel,
                        utc_now_fn=utc_now_fn,
                        allow_fixture_resource_sample=allow_fixture_resource_sample,
                        post_exit_hook=post_exit_hook,
                        custody_queue=custody_queue,
                        disk_budget_ledger=eff_disk_ledger,
                        task_ordinal=0,
                        get_cancel_epoch=_get_cancel_epoch,
                        max_chunk_bytes_limit=int(bud_contract["max_chunk_bytes"]),
                    )
                finally:
                    custody_queue.mark_task_complete(0)
                task_results.append(meta_res)
                all_lifecycle_traces.extend(meta_res.get("lifecycle_traces") or ())

                if not meta_res.get("ok"):
                    violations.extend(meta_res.get("violations") or ["METADATA_IDENTITY_GATE_FAILED"])
                    state_history.append("ABORTED")
                    for rem_ord in range(1, len(ctx.tasks)):
                        custody_queue.mark_task_complete(rem_ord)
                    custody_queue.close_intake()
                else:
                    obs_meta = meta_res.get("normalized_instrument") or {}
                    metadata_versions.append(dict(obs_meta))
                    state_history.append("VALIDATING")

                    # 2. Execute remaining planned tasks in parallel; workers ONLY enqueue envelopes!
                    remaining_indexed = list(enumerate(ctx.tasks[1:], start=1))

                    async def _run_remaining_async() -> List[Dict[str, Any]]:
                        sem = asyncio.Semaphore(ctx.effective_concurrency)
                        loop = asyncio.get_running_loop()
                        with ThreadPoolExecutor(max_workers=ctx.effective_concurrency) as pool:

                            async def _run_one(t_ord: int, t: R6A1AcquisitionTask) -> Dict[str, Any]:
                                try:
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
                                                    verified_grant=verified_grant, registry=registry, custody=None,
                                                    resource_sample_fn=resource_sample_fn, expected_volumes=expected_volumes,
                                                    session_deadline_monotonic=session_deadline_mono, cancel_event=ev_cancel,
                                                    utc_now_fn=utc_now_fn, allow_fixture_resource_sample=allow_fixture_resource_sample,
                                                    observed_instrument_meta=obs_meta,
                                                    custody_queue=custody_queue, disk_budget_ledger=eff_disk_ledger,
                                                    task_ordinal=t_ord, get_cancel_epoch=_get_cancel_epoch,
                                                    max_chunk_bytes_limit=int(bud_contract["max_chunk_bytes"]),
                                                ),
                                            )
                                        return await loop.run_in_executor(
                                            pool,
                                            lambda: execute_r6_a1_rest_task(
                                                ctx=ctx, task=t, ledger=ledger, lease=lease, plan=plan,
                                                verified_grant=verified_grant, registry=registry, custody=None,
                                                resource_sample_fn=resource_sample_fn, expected_volumes=expected_volumes,
                                                session_deadline_monotonic=session_deadline_mono, cancel_event=ev_cancel,
                                                utc_now_fn=utc_now_fn, allow_fixture_resource_sample=allow_fixture_resource_sample,
                                                observed_instrument_meta=obs_meta,
                                                custody_queue=custody_queue, disk_budget_ledger=eff_disk_ledger,
                                                task_ordinal=t_ord, get_cancel_epoch=_get_cancel_epoch,
                                                max_chunk_bytes_limit=int(bud_contract["max_chunk_bytes"]),
                                            ),
                                        )
                                finally:
                                    custody_queue.mark_task_complete(t_ord)

                            coros = [_run_one(ord_i, t_obj) for ord_i, t_obj in remaining_indexed]
                            return list(await asyncio.gather(*coros))

                    if remaining_indexed:
                        rem_out = asyncio.run(_run_remaining_async())
                        for r_t in rem_out:
                            task_results.append(r_t)
                            all_lifecycle_traces.extend(r_t.get("lifecycle_traces") or ())
                            if not r_t.get("ok"):
                                violations.extend(r_t.get("violations") or [str(r_t.get("terminal_status"))])
                                if "QUARANTINED" in str(r_t.get("terminal_status") or "") or "FAIL_UNDRAINED" in str(r_t.get("terminal_status") or ""):
                                    quarantine_req = True

                    # Close intake & wait for single custody writer to finish draining strictly in canonical order!
                    custody_queue.close_intake()
                    state_history.append("PERSISTING")
                    writer_thread.join(timeout=max(2.0, session_deadline_mono - time.monotonic() + 2.0))
                    if writer_thread.is_alive():
                        ev_cancel.set()
                        _bump_cancel_epoch()
                        custody_queue.abort("WRITER_JOIN_TIMEOUT")
                        quarantine_req = True
                        violations.append("SINGLE_WRITER_DRAIN_TIMEOUT")
                    elif writer_report_box:
                        # Map deterministically committed chunks back to their originating task records
                        by_task_id: Dict[str, List[Dict[str, Any]]] = {}
                        for cc in writer_report_box[0].get("committed_chunks") or ():
                            by_task_id.setdefault(str(cc.get("task_id") or ""), []).append(cc)
                        for tr in task_results:
                            tid = str(tr.get("task_id") or "")
                            if tid in by_task_id:
                                tr["committed_chunks"] = by_task_id[tid]
                        if not writer_report_box[0].get("ok"):
                            violations.extend(writer_report_box[0].get("violations") or ["SINGLE_WRITER_FAILED"])
                            if writer_report_box[0].get("status") in ("WRITER_QUARANTINED", "WRITER_EXCEPTION"):
                                quarantine_req = True
                    if time.monotonic() >= session_deadline_mono and not ev_cancel.is_set():
                        deadline_expired = True
    except BaseException as exc:
        original_error = f"{type(exc).__name__}:{exc}"
        ev_cancel.set()
        _bump_cancel_epoch()
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            base_exc_to_reraise = exc
            violations.append(f"BASE_EXCEPTION_INTERRUPTED:{type(exc).__name__}")
        else:
            quarantine_req = True
            violations.append(f"ORCHESTRATOR_UNHANDLED_EXCEPTION:{type(exc).__name__}:{exc}")
    finally:
        # NEVER put `return` inside `finally:`!
        if early_claim_rejection_receipt is None:
            final_receipt = finalize_r6_a1_session_safely(
                ctx=ctx,
                verified_grant=verified_grant,
                ledger=ledger,
                lease=lease,
                claim_attempted=claim_attempted,
                plan=plan,
                custody=custody,
                writer_authority=writer_authority,
                writer_thread=writer_thread,
                writer_report_box=writer_report_box,
                custody_queue=custody_queue,
                disk_budget_ledger=eff_disk_ledger,
                task_results=task_results,
                state_history=state_history,
                resource_samples=resource_samples,
                resource_sample_fn=resource_sample_fn,
                violations=violations,
                original_error=original_error,
                quarantine_required=quarantine_req,
                cancelled=ev_cancel.is_set(),
                deadline_expired=deadline_expired,
                cancel_event=ev_cancel,
                bump_cancel_epoch=_bump_cancel_epoch,
                utc_now_fn=utc_now_fn,
                metadata_versions=metadata_versions,
                inject_db_finalize_error=inject_db_finalize_error,
                simulate_unreaped_worker=simulate_unreaped_worker,
            )
            final_receipt["lifecycle_traces"] = all_lifecycle_traces
            if receipt_sink is not None:
                receipt_sink.append(final_receipt)

    if base_exc_to_reraise is not None and propagate_base_exceptions:
        raise base_exc_to_reraise
    if early_claim_rejection_receipt is not None:
        early_claim_rejection_receipt["lifecycle_traces"] = all_lifecycle_traces
        return early_claim_rejection_receipt
    assert final_receipt is not None
    return final_receipt
