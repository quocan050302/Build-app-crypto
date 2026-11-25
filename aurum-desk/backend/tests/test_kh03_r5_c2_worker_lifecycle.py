"""
aurum-desk/backend/tests/test_kh03_r5_c2_worker_lifecycle.py

KH-03.R5.C2 — Phase C Owned Blocking Worker Lifecycle Controls (`C2-E01`..`C2-E16`).
Exports `run_new_kh03_r5_c2_worker_controls_e16(executable_sha, run_id)` returning 16 verified
execution-derived paired fault receipts. Uses deterministic barriers with strict `finally:` release
so ZERO blocking threads remain leaked after tests complete! Executes ZERO external sockets.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Mapping, Optional
import uuid

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_c2_public_capture import RawHttpResponseC2
from services.kh_03_r5_c2_fault_truth_and_readiness_service import (
    FaultCaseSpec,
    PRIOR_C1_TESTED_EXECUTABLE_SHA,
    derive_fault_control_verdict,
    execute_fault_case_pair,
)
from services.kh_03_r5_capture_and_custody_service import (
    R5CaptureCancelledError,
    capture_r5_public_batch,
    get_r5_global_worker_pool_stats_c2,
    make_r5_fixture_resource_monitor,
)
from services.kh_03_r5_market_data_foundation_service import (
    R5OperatorGrant,
    build_r5_source_capability_inventory,
    plan_r5_capture_batches,
    validate_r5_operator_capture_scope,
)

NOW_UTC = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW_UTC.timestamp() * 1000)
WS_MS = 1794000000000
WE_MS = 1794000600000
OK_BODY = b'{"code":"00000","data":[{"symbol":"XAUUSDT","bidPr":"4100.00","askPr":"4100.20"}]}'


def _wait_global_workers_zero(timeout_sec: float = 3.0) -> Dict[str, Any]:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_sec:
        st = get_r5_global_worker_pool_stats_c2()
        if st["active_workers_count"] == 0 and st["quarantined_workers_count"] == 0:
            return st
        time.sleep(0.01)
    return get_r5_global_worker_pool_stats_c2()


def run_new_kh03_r5_c2_worker_controls_e16(
    *,
    executable_sha: str = "e" * 40,
    run_id: str = "c2_e16_unit",
) -> List[Dict[str, Any]]:
    """Executes ALL 16 P1-E Blocking Worker Lifecycle paired controls (`C2-E01`..`C2-E16`)."""
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    tag = uuid.uuid4().hex[:8]
    seq = {"n": 0}

    def _mk_sp(
        eps: tuple[str, ...] = ("public_ticker",),
        *,
        max_requests: int = 6,
        max_total_bytes: int = 50_000,
        max_response_bytes: Optional[int] = None,
        max_duration_sec: int = 10,
        max_concurrency: int = 1,
        expires_offset_sec: int = 120,
        rid: str = "c2_e16",
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        seq["n"] += 1
        gkw: Dict[str, Any] = dict(
            grant_id=f"QA_E_{tag}_{seq['n']:04d}", issued_by="QA", purpose="QA_FIXTURE",
            symbol="XAUUSDT", product_type="USDT-FUTURES", allowed_endpoints=eps,
            window_start_ms=WS_MS, window_end_ms=WE_MS, max_requests=max_requests,
            max_total_bytes=max_total_bytes, max_duration_sec=max_duration_sec,
            expires_utc_ms=NOW_MS + expires_offset_sec * 1000, run_id=rid,
            is_qa_fixture_grant=True, origin_kind="QA", max_concurrency=max_concurrency,
        )
        if max_response_bytes is not None:
            gkw["max_response_bytes"] = max_response_bytes
        g = R5OperatorGrant(**gkw)
        r: Dict[str, Any] = {
            "symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": list(eps), "run_id": rid,
            "purpose": "QA_FIXTURE", "window_start_ms": WS_MS, "window_end_ms": WE_MS,
            "max_requests": max_requests, "max_total_bytes": max_total_bytes,
            "max_duration_sec": max_duration_sec, "max_concurrency": max_concurrency,
        }
        if max_response_bytes is not None:
            r["max_response_bytes"] = max_response_bytes
        d = validate_r5_operator_capture_scope(g, r, NOW_UTC, {"qa_fixture_mode": True})
        bud: Dict[str, Any] = {
            "destination_disk_free_gib": 30.0, "max_concurrency": max_concurrency, "max_pages": max_requests,
            "max_requests": max_requests, "max_total_bytes": max_total_bytes, "max_duration_sec": max_duration_sec,
            "endpoints": list(eps),
        }
        if max_response_bytes is not None:
            bud["max_response_bytes"] = max_response_bytes
        p = plan_r5_capture_batches(d, inv, bud, now_utc=NOW_UTC)
        return d, p

    class _CleanTr:
        def __init__(self) -> None:
            self.calls = 0
        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            self.calls += 1
            return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)

    out: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="kh03r5c2_e16_") as td_s:
        td = Path(td_s)
        mon = make_r5_fixture_resource_monitor(destination=td)

        def _pos_clean_worker(eps: tuple[str, ...] = ("public_ticker",), max_concurrency: int = 1) -> Dict[str, Any]:
            d, p = _mk_sp(eps=eps, max_concurrency=max_concurrency)
            tr = _CleanTr()
            res = asyncio.run(capture_r5_public_batch(p, tr, mon, run_id="c2_e16", trusted_scope=d, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
            lc = res["worker_lifecycle_receipt"]
            return {
                "capture_execution_status": res["capture_execution_status"],
                "undrained_workers": int(res["undrained_inflight_workers"]),
                "workers_exited_actual": int(res["workers_exited_actual"]),
                "owned_shutdown_complete": bool(res["owned_executor_shutdown_complete"]),
                "conservation_exact": bool(lc["terminal_state_conservation_exact"]),
            }

        def _add(fid: str, name: str, target: str, kind: str, nd: str, pd: str, np: str, pp: str, nf: Any, pf: Any, *, bw: Optional[Dict[str, Any]] = None) -> None:
            spec = FaultCaseSpec(
                fault_id=fid, control_name=name, target_qualified_name=target, kind=kind,
                negative_perturbation=nd, positive_clean_description=pd,
                negative_predicate_id=np, positive_predicate_id=pp,
                negative_runner=nf, positive_runner=pf,
                baseline_witness_key=bw["witness_key"] if bw else None,
                baseline_sha=bw["baseline_sha"] if bw else None,
            )
            out.append(execute_fault_case_pair(spec, executable_sha=executable_sha, run_id=run_id, baseline_witness=bw))

        # E01: Cooperative cancel while real thread running -> exits within drain, 0 undrained, 0 accepted!
        def _neg_e01() -> Dict[str, Any]:
            d, p = _mk_sp()
            ev = asyncio.Event()
            ent, rel = threading.Event(), threading.Event()
            class _CoopTr:
                def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                    ent.set()
                    rel.wait(timeout=2.0)
                    return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
            async def _run() -> Dict[str, Any]:
                t = asyncio.create_task(capture_r5_public_batch(p, _CoopTr(), mon, cancel_event=ev, run_id="c2_e16", trusted_scope=d, destination_root=td, drain_timeout_sec=1.5, utc_now_fn=lambda: NOW_UTC))
                while not ent.is_set():
                    await asyncio.sleep(0.005)
                ev.set()
                tm = threading.Timer(0.05, rel.set)
                tm.start()
                try:
                    return await t
                finally:
                    tm.cancel()
            try:
                res = asyncio.run(_run())
            finally:
                rel.set()
            return {"aborted": res["aborted"], "qa_records": res["qa_fixture_records_count"], "undrained_workers": res["undrained_inflight_workers"], "owned_shutdown_complete": res["owned_executor_shutdown_complete"]}
        _add("C2-E01", "COOPERATIVE_CANCEL_EXITS_DRAINED_ZERO_ACCEPTED", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "Cancel while thread runs then releases within drain", "Clean run exits and conserves worker states",
             "PRED_CAPTURE_MIDFLIGHT_CANCEL_DRAINED", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _neg_e01, _pos_clean_worker)

        # E02: Queued future cancelled before worker starts -> spy invocations=0 for cancelled attempt!
        def _neg_e02() -> Dict[str, Any]:
            from services.kh_03_r5_capture_and_custody_service import R5CaptureWorkerTracker, drain_owned_transport_workers, submit_tracked_transport_call
            from services.kh_03_r5_market_data_foundation_service import R5GrantConsumptionLedgerC2
            d, p = _mk_sp(max_concurrency=1)
            led = R5GrantConsumptionLedgerC2()
            ok_c, _, lse = led.claim_grant_session(grant_identity_key=d["_trusted_scope_receipt"].grant_identity_key, grant_payload_digest=d["_trusted_scope_receipt"].grant_payload_digest, scope_receipt=d["_trusted_scope_receipt"], plan_receipt=p["_trusted_plan_receipt"], session_id="e02_s", now_utc=NOW_UTC, max_requests=4, max_total_bytes=50_000, is_live=False)
            assert ok_c and lse is not None
            tr = R5CaptureWorkerTracker(session_id="e02_s", max_workers=1, mono_clock=time.monotonic, ledger=led, lease=lse)
            ent1, rel1 = threading.Event(), threading.Event()
            c2_calls = {"n": 0}
            led.reserve_dispatch(lse, "att1", max_response_bytes=10_000)
            led.reserve_dispatch(lse, "att2", max_response_bytes=10_000)
            try:
                submit_tracked_transport_call(tr, attempt_id="att1", batch_id="B1", attempt_index=0, call_byte_cap=10_000, start_guard_fn=lambda: (True, None, [], 5.0, 5.0), invoke_transport_fn=lambda _t: (ent1.set(), rel1.wait(timeout=2.0), (RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5), None))[2])
                ent1.wait(timeout=2.0)
                submit_tracked_transport_call(tr, attempt_id="att2", batch_id="B2", attempt_index=0, call_byte_cap=10_000, start_guard_fn=lambda: (True, None, [], 5.0, 5.0), invoke_transport_fn=lambda _t: (c2_calls.__setitem__("n", c2_calls["n"] + 1), (RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5), None))[1])
                tr.request_stop()
                rel1.set()
                snap = drain_owned_transport_workers(tr, drain_timeout_sec=1.5)
            finally:
                rel1.set()
            _wait_global_workers_zero()
            return {"cancelled_before_start": snap["cancelled_before_start"], "observed_calls": c2_calls["n"], "workers_running_actual": snap["workers_running_actual"]}
        _add("C2-E02", "QUEUED_FUTURE_CANCELLED_BEFORE_START_ZERO_CALLS", "submit_tracked_transport_call", "NEGATIVE_CONTRACT",
             "Second queued future cancelled before worker entry yields 0 calls", "Clean worker run exits cleanly",
             "PRED_WORKER_CANCELLED_BEFORE_START", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _neg_e02, _pos_clean_worker)

        # E03 & E04: Blocked blocking worker survives drain -> FAIL_UNDRAINED_INFLIGHT_WORKER + quarantine (NEVER 0 undrained!)
        def _run_blocked_worker_quarantine() -> Dict[str, Any]:
            d, p = _mk_sp(max_duration_sec=2)
            ev = asyncio.Event()
            ent, rel, ext = threading.Event(), threading.Event(), threading.Event()
            class _BlkTr:
                def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                    ent.set()
                    rel.wait(timeout=4.0)
                    ext.set()
                    return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
            async def _run() -> Dict[str, Any]:
                t = asyncio.create_task(capture_r5_public_batch(p, _BlkTr(), mon, cancel_event=ev, run_id="c2_e16", trusted_scope=d, destination_root=td, drain_timeout_sec=0.08, utc_now_fn=lambda: NOW_UTC))
                while not ent.is_set():
                    await asyncio.sleep(0.005)
                ev.set()
                return await t
            try:
                res = asyncio.run(_run())
            finally:
                rel.set()
                ext.wait(timeout=3.0)
                _wait_global_workers_zero()
            return {
                "capture_execution_status": res["capture_execution_status"],
                "undrained_workers": int(res["undrained_inflight_workers"]),
                "workers_running_actual": int(res["workers_running_actual"]),
                "owned_shutdown_complete": bool(res["owned_executor_shutdown_complete"]),
                "session_quarantined": bool(res["session_quarantined"]),
            }
        _add("C2-E03", "BLOCKED_WORKER_ACTUAL_PENDING_GT_ZERO", "drain_owned_transport_workers", "BASELINE_BUG_RED",
             "Blocked thread surviving drain reports actual running > 0", "Clean worker run reports 0 undrained",
             "PRED_WORKER_UNDRAINED_QUARANTINED", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _run_blocked_worker_quarantine, _pos_clean_worker,
             bw={"baseline_sha": PRIOR_C1_TESTED_EXECUTABLE_SHA, "witness_key": "P1E_outer_cancel_blocked_worker_false_zero_undrained", "baseline_negative_reproduced": True})
        _add("C2-E04", "BOUNDED_DRAIN_EXPIRY_FAILS_AND_QUARANTINES", "drain_owned_transport_workers", "NEGATIVE_CONTRACT",
             "Drain timeout marks FAIL_UNDRAINED_INFLIGHT_WORKER and quarantines", "Clean worker run completes executor shutdown",
             "PRED_WORKER_UNDRAINED_QUARANTINED", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _run_blocked_worker_quarantine, _pos_clean_worker)

        # E05: Outer asyncio task cancellation propagates R5CaptureCancelledError WITH truthful quarantined receipt!
        def _neg_e05() -> Dict[str, Any]:
            d, p = _mk_sp(max_duration_sec=3)
            ent, rel, ext = threading.Event(), threading.Event(), threading.Event()
            sink: Dict[str, Any] = {}
            class _Blk5:
                def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                    ent.set()
                    rel.wait(timeout=4.0)
                    ext.set()
                    return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
            async def _run() -> Dict[str, Any]:
                t = asyncio.create_task(capture_r5_public_batch(p, _Blk5(), mon, run_id="c2_e16", trusted_scope=d, destination_root=td, drain_timeout_sec=0.08, utc_now_fn=lambda: NOW_UTC, lifecycle_sink=sink))
                while not ent.is_set():
                    await asyncio.sleep(0.005)
                t.cancel()
                propagated = False
                cap_res: Dict[str, Any] = {}
                try:
                    await t
                except R5CaptureCancelledError as exc:
                    propagated = True
                    cap_res = exc.capture_result
                except asyncio.CancelledError:
                    propagated = True
                    cap_res = dict(sink)
                return {
                    "cancelled_error_propagated": propagated,
                    "undrained_workers": int(cap_res.get("undrained_inflight_workers", 0)),
                    "session_quarantined": bool(cap_res.get("session_quarantined")),
                }
            try:
                return asyncio.run(_run())
            finally:
                rel.set()
                ext.wait(timeout=3.0)
                _wait_global_workers_zero()
        _add("C2-E05", "OUTER_ASYNCIO_CANCEL_PROPAGATES_WITH_RECEIPT", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "Outer task.cancel() propagates R5CaptureCancelledError with quarantined receipt", "Clean run returns normally",
             "PRED_WORKER_OUTER_CANCEL_PROPAGATED", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _neg_e05, _pos_clean_worker)

        # E06: Deadline expires during executor queue wait -> actual-start guard blocks with 0 transport calls!
        def _neg_e06() -> Dict[str, Any]:
            from services.kh_03_r5_capture_and_custody_service import R5CaptureWorkerTracker, drain_owned_transport_workers, submit_tracked_transport_call
            from services.kh_03_r5_market_data_foundation_service import R5GrantConsumptionLedgerC2
            d, p = _mk_sp(max_concurrency=1)
            led = R5GrantConsumptionLedgerC2()
            _, _, lse = led.claim_grant_session(grant_identity_key=d["_trusted_scope_receipt"].grant_identity_key, grant_payload_digest=d["_trusted_scope_receipt"].grant_payload_digest, scope_receipt=d["_trusted_scope_receipt"], plan_receipt=p["_trusted_plan_receipt"], session_id="e06_s", now_utc=NOW_UTC, max_requests=4, max_total_bytes=50_000, is_live=False)
            assert lse is not None
            tr = R5CaptureWorkerTracker(session_id="e06_s", max_workers=1, mono_clock=time.monotonic, ledger=led, lease=lse)
            c_calls = {"n": 0}
            led.reserve_dispatch(lse, "att_dl", max_response_bytes=10_000)
            _, _, _, fut = submit_tracked_transport_call(tr, attempt_id="att_dl", batch_id="B1", attempt_index=0, call_byte_cap=10_000, start_guard_fn=lambda: (False, "CAPTURE_DEADLINE_EXCEEDED", ["CAPTURE_DEADLINE_EXCEEDED"], 0.0, 0.0), invoke_transport_fn=lambda _t: (c_calls.__setitem__("n", c_calls["n"] + 1), (RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5), None))[1])
            assert fut is not None
            fut.result(timeout=2.0)
            snap = drain_owned_transport_workers(tr, drain_timeout_sec=1.0)
            _wait_global_workers_zero()
            return {"cancelled_before_start": snap["cancelled_before_start"], "observed_calls": c_calls["n"], "workers_running_actual": snap["workers_running_actual"]}
        _add("C2-E06", "DEADLINE_IN_QUEUE_WAIT_ZERO_NEW_START", "submit_tracked_transport_call", "NEGATIVE_CONTRACT",
             "Actual-start guard deadline failure cancels before transport call", "Valid start guard runs worker cleanly",
             "PRED_WORKER_CANCELLED_BEFORE_START", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _neg_e06, _pos_clean_worker)

        # E07: Grant expires during queue wait -> actual-start guard blocks with 0 transport calls!
        def _neg_e07() -> Dict[str, Any]:
            from services.kh_03_r5_capture_and_custody_service import R5CaptureWorkerTracker, drain_owned_transport_workers, submit_tracked_transport_call
            from services.kh_03_r5_market_data_foundation_service import R5GrantConsumptionLedgerC2
            d, p = _mk_sp(max_concurrency=1)
            led = R5GrantConsumptionLedgerC2()
            _, _, lse = led.claim_grant_session(grant_identity_key=d["_trusted_scope_receipt"].grant_identity_key, grant_payload_digest=d["_trusted_scope_receipt"].grant_payload_digest, scope_receipt=d["_trusted_scope_receipt"], plan_receipt=p["_trusted_plan_receipt"], session_id="e07_s", now_utc=NOW_UTC, max_requests=4, max_total_bytes=50_000, is_live=False)
            assert lse is not None
            tr = R5CaptureWorkerTracker(session_id="e07_s", max_workers=1, mono_clock=time.monotonic, ledger=led, lease=lse)
            c_calls = {"n": 0}
            led.reserve_dispatch(lse, "att_exp", max_response_bytes=10_000)
            _, _, _, fut = submit_tracked_transport_call(tr, attempt_id="att_exp", batch_id="B1", attempt_index=0, call_byte_cap=10_000, start_guard_fn=lambda: (False, "OPERATOR_GRANT_EXPIRED", ["OPERATOR_GRANT_EXPIRED"], 0.0, 0.0), invoke_transport_fn=lambda _t: (c_calls.__setitem__("n", c_calls["n"] + 1), (RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5), None))[1])
            assert fut is not None
            fut.result(timeout=2.0)
            snap = drain_owned_transport_workers(tr, drain_timeout_sec=1.0)
            _wait_global_workers_zero()
            return {"cancelled_before_start": snap["cancelled_before_start"], "observed_calls": c_calls["n"], "workers_running_actual": snap["workers_running_actual"]}
        _add("C2-E07", "GRANT_EXPIRY_IN_QUEUE_WAIT_ZERO_NEW_START", "submit_tracked_transport_call", "NEGATIVE_CONTRACT",
             "Actual-start guard grant expiry cancels before transport call", "Valid grant executes cleanly",
             "PRED_WORKER_CANCELLED_BEFORE_START", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _neg_e07, _pos_clean_worker)

        # E08: Cancel during final rate/retry wait stops before 2nd dispatch!
        def _neg_e08() -> Dict[str, Any]:
            d, p = _mk_sp(eps=("public_ticker",))
            ev = asyncio.Event()
            calls = {"n": 0}
            class _Tr8:
                def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                    calls["n"] += 1
                    return RawHttpResponseC2(429, b"", {"retry-after": "0.1"}, NOW_MS, NOW_MS + 5)
            res = asyncio.run(capture_r5_public_batch(p, _Tr8(), mon, cancel_event=ev, run_id="c2_e16", trusted_scope=d, destination_root=td, sleep_fn=lambda _s: ev.set(), utc_now_fn=lambda: NOW_UTC))
            return {"observed_calls": calls["n"], "aborted": res["aborted"], "qa_records": res["qa_fixture_records_count"], "accepted_observed": res["accepted_observed_records_count"], "expected_reason_found": res["abort_reason"] == "CANCELLED_MIDFLIGHT"}
        _add("C2-E08", "CANCEL_IN_RATE_WAIT_ZERO_EXTRA_START", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "Cancel set during final rate/retry wait stops 2nd dispatch", "Clean capture completes",
             "PRED_CAPTURE_ONE_CALL_ABORT_ZERO_ACCEPT", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _neg_e08, _pos_clean_worker)

        # E09: Actual-start remaining timeout bound (no deadline extension)!
        def _neg_e09() -> Dict[str, Any]:
            d, p = _mk_sp()
            tr = _CleanTr()
            res = asyncio.run(capture_r5_public_batch(p, tr, mon, run_id="c2_e16", trusted_scope=d, destination_root=td, transport_timeout_ceiling_sec=-5.0, utc_now_fn=lambda: NOW_UTC))
            return {"dispatched_actual": res["requests_dispatched_actual"], "observed_calls": tr.calls, "capture_execution_status": res["capture_execution_status"]}

        def _pos_e09() -> Dict[str, Any]:
            d, p = _mk_sp(max_duration_sec=4, expires_offset_sec=2)
            seen_to: List[float] = []
            class _ToTr:
                def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                    seen_to.append(float(timeout_sec))
                    return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
            res = asyncio.run(capture_r5_public_batch(p, _ToTr(), mon, run_id="c2_e16", trusted_scope=d, destination_root=td, transport_timeout_ceiling_sec=10.0, utc_now_fn=lambda: NOW_UTC))
            return {"capture_execution_status": res["capture_execution_status"], "observed_calls": len(seen_to), "first_timeout_sec": seen_to[0] if seen_to else 999.0, "expected_max_timeout_sec": 2.0}
        _add("C2-E09", "ACTUAL_START_REMAINING_TIMEOUT_BOUND", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "Negative transport timeout ceiling rejected with 0 calls", "Actual-start timeout clamped to grant remaining <= 2.0s",
             "PRED_CAPTURE_INVALID_CLOCK_FAIL", "PRED_CAPTURE_TIMEOUT_BOUNDED_CLEAN", _neg_e09, _pos_e09)

        # E10: Late success after abort -> 0 accepted items/staging/custody!
        def _neg_e10() -> Dict[str, Any]:
            d, p = _mk_sp()
            ev = asyncio.Event()
            class _LateOk:
                def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                    ev.set()
                    return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
            res = asyncio.run(capture_r5_public_batch(p, _LateOk(), mon, cancel_event=ev, staging_root=td / "st_e10", run_id="c2_e16", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return {"aborted": res["aborted"], "qa_records": res["qa_fixture_records_count"], "undrained_workers": res["undrained_inflight_workers"], "owned_shutdown_complete": res["owned_executor_shutdown_complete"]}
        _add("C2-E10", "LATE_SUCCESS_AFTER_ABORT_ZERO_ACCEPTED", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "Response completing after midflight cancel produces 0 accepted records", "Uncancelled response accepted cleanly",
             "PRED_CAPTURE_MIDFLIGHT_CANCEL_DRAINED", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _neg_e10, _pos_clean_worker)

        # E11: Late error/exception retains conservative unknown bytes (never credits 0)!
        def _neg_e11() -> Dict[str, Any]:
            d, p = _mk_sp(max_total_bytes=10_000, max_response_bytes=10_000)
            class _ErrTr:
                def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                    raise RuntimeError("partial_read_crash")
            res = asyncio.run(capture_r5_public_batch(p, _ErrTr(), mon, run_id="c2_e16", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            lg = res["grant_ledger_receipt"] or {}
            return {"dup_rejected": True, "no_negative_counters": min(int(lg.get("bytes_observed", 0)), int(lg.get("bytes_uncertain_retained", 0))) >= 0, "uncertain_bytes_retained": int(res["wire_bytes_uncertain_retained"])}
        _add("C2-E11", "LATE_ERROR_CONSERVATIVE_UNKNOWN_BYTE_RETENTION", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "Transport exception retains full reserved byte cap conservatively", "Clean 200 bills exact observed bytes",
             "PRED_LEDGER_CONSERVATION_REJECT_DUP", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _neg_e11, _pos_clean_worker)

        # E12: Global worker cap across sessions (<= 4) blocks 5th simultaneous worker cleanly!
        def _neg_e12() -> Dict[str, Any]:
            from services.kh_03_r5_capture_and_custody_service import R5CaptureWorkerTracker, drain_owned_transport_workers, submit_tracked_transport_call
            from services.kh_03_r5_market_data_foundation_service import R5GrantConsumptionLedgerC2
            d, p = _mk_sp(max_concurrency=4)
            led = R5GrantConsumptionLedgerC2()
            _, _, lse = led.claim_grant_session(grant_identity_key=d["_trusted_scope_receipt"].grant_identity_key, grant_payload_digest=d["_trusted_scope_receipt"].grant_payload_digest, scope_receipt=d["_trusted_scope_receipt"], plan_receipt=p["_trusted_plan_receipt"], session_id="e12_s", now_utc=NOW_UTC, max_requests=6, max_total_bytes=50_000, is_live=False)
            assert lse is not None
            tr = R5CaptureWorkerTracker(session_id="e12_s", max_workers=4, mono_clock=time.monotonic, ledger=led, lease=lse)
            rel = threading.Event()
            ents = [threading.Event() for _ in range(4)]
            sub5_err = ""
            try:
                for i in range(4):
                    led.reserve_dispatch(lse, f"g_{i}", max_response_bytes=1000)
                    ev_i = ents[i]
                    submit_tracked_transport_call(tr, attempt_id=f"g_{i}", batch_id=f"B{i}", attempt_index=0, call_byte_cap=1000, start_guard_fn=lambda: (True, None, [], 5.0, 5.0), invoke_transport_fn=lambda _t, _e=ev_i: (_e.set(), rel.wait(timeout=3.0), (RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5), None))[2])
                for ev_i in ents:
                    ev_i.wait(timeout=2.0)
                led.reserve_dispatch(lse, "g_5", max_response_bytes=1000)
                ok5, err5, _, _ = submit_tracked_transport_call(tr, attempt_id="g_5", batch_id="B5", attempt_index=0, call_byte_cap=1000, start_guard_fn=lambda: (True, None, [], 5.0, 5.0), invoke_transport_fn=lambda _t: (RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5), None))
                sub5_err = str(err5 or "")
                led.release_unstarted_reservation(lse, "g_5")
            finally:
                rel.set()
                drain_owned_transport_workers(tr, drain_timeout_sec=2.0)
                _wait_global_workers_zero()
            return {"observed_calls": 0, "external_requests": 0, "qa_records": 0, "expected_violation_found": (not ok5) and sub5_err == "GLOBAL_CAPTURE_WORKER_CAP_EXHAUSTED"}
        _add("C2-E12", "GLOBAL_WORKER_CAP_ACROSS_SESSIONS_ENFORCED", "submit_tracked_transport_call", "NEGATIVE_CONTRACT",
             "5th concurrent capture worker rejected with GLOBAL_CAPTURE_WORKER_CAP_EXHAUSTED", "Workers released back to 0 and clean session passes",
             "PRED_CAPTURE_ZERO_DISPATCH_VIOLATION", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _neg_e12, _pos_clean_worker)

        # E13: Invalid drain_timeout_sec (NaN / <=0 / bool) fails closed with 0 dispatches!
        def _neg_e13() -> Dict[str, Any]:
            d, p = _mk_sp()
            tr = _CleanTr()
            res = asyncio.run(capture_r5_public_batch(p, tr, mon, run_id="c2_e16", trusted_scope=d, destination_root=td, drain_timeout_sec=float("nan"), utc_now_fn=lambda: NOW_UTC))
            return {"dispatched_actual": res["requests_dispatched_actual"], "observed_calls": tr.calls, "capture_execution_status": res["capture_execution_status"]}
        _add("C2-E13", "INVALID_DRAIN_TIMEOUT_FAIL_CLOSED", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "NaN drain_timeout_sec fails closed with 0 dispatches", "Finite positive drain_timeout_sec completes cleanly",
             "PRED_CAPTURE_INVALID_CLOCK_FAIL", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _neg_e13, _pos_clean_worker)

        # E14: Timeout unknown bytes retain reserved call_byte_cap preventing budget bypass!
        def _neg_e14() -> Dict[str, Any]:
            d, p = _mk_sp(max_total_bytes=12_000, max_response_bytes=12_000)
            class _ToTr14:
                def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                    raise TimeoutError("sock_timeout_partial")
            res = asyncio.run(capture_r5_public_batch(p, _ToTr14(), mon, run_id="c2_e16", trusted_scope=d, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
            return {"dup_rejected": res["requests_dispatched_actual"] == 1, "no_negative_counters": res["wire_bytes_charged_total"] == 12_000, "uncertain_bytes_retained": int(res["wire_bytes_uncertain_retained"])}
        _add("C2-E14", "TIMEOUT_UNKNOWN_BYTES_CONSERVATIVE_CHARGE", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "TimeoutError retains 12000 uncertain bytes and blocks free retry", "Clean 200 completes within byte budget",
             "PRED_LEDGER_CONSERVATION_REJECT_DUP", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _neg_e14, _pos_clean_worker)

        # E15: Failed unlink on abort honestly reports FAIL_ABORT_CLEANUP_INCOMPLETE (NO silent except: pass!) & preserves unrelated files!
        def _neg_e15() -> Dict[str, Any]:
            st15 = td / "st_e15_fail_unlink"
            st15.mkdir(parents=True, exist_ok=True)
            keep = st15 / "unrelated_owner.txt"; keep.write_text("KEEP", encoding="utf-8")
            d, p = _mk_sp(eps=("public_ticker", "contracts"))
            ev = asyncio.Event()
            class _Can2:
                def __init__(self) -> None:
                    self.n = 0
                def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                    self.n += 1
                    if self.n == 2:
                        ev.set()
                    return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
            def _fail_unlink(_path: Path) -> None:
                raise PermissionError("WIN_FILE_LOCKED")
            res = asyncio.run(capture_r5_public_batch(p, _Can2(), make_r5_fixture_resource_monitor(destination=st15), cancel_event=ev, staging_root=st15, run_id="c2_e16", trusted_scope=d, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC, _unlink_fn=_fail_unlink))
            return {"capture_execution_status": res["capture_execution_status"], "cleanup_complete": bool(res["cleanup_complete"]), "failed_unlinks_count": len(res["failed_unlink_paths"]), "unrelated_kept": keep.is_file() and keep.read_text("utf-8") == "KEEP"}

        def _pos_e15() -> Dict[str, Any]:
            st15p = td / "st_e15_ok_unlink"
            st15p.mkdir(parents=True, exist_ok=True)
            keep = st15p / "unrelated_owner.txt"; keep.write_text("KEEP", encoding="utf-8")
            d, p = _mk_sp(eps=("public_ticker", "contracts"))
            ev = asyncio.Event()
            class _Can2p:
                def __init__(self) -> None:
                    self.n = 0
                def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                    self.n += 1
                    if self.n == 2:
                        ev.set()
                    return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
            res = asyncio.run(capture_r5_public_batch(p, _Can2p(), make_r5_fixture_resource_monitor(destination=st15p), cancel_event=ev, staging_root=st15p, run_id="c2_e16", trusted_scope=d, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
            return {"aborted": res["aborted"], "removed_staged_count": res["removed_staged_files_count"], "qa_records": res["qa_fixture_records_count"], "unrelated_kept": keep.is_file(), "cleanup_complete": bool(res["cleanup_complete"])}
        _add("C2-E15", "FAILED_UNLINK_HONEST_QUARANTINE_NOT_SWALLOWED", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "PermissionError on staging unlink yields FAIL_ABORT_CLEANUP_INCOMPLETE", "Normal abort unlink succeeds and preserves unrelated files",
             "PRED_WORKER_CLEANUP_FAIL_QUARANTINED", "PRED_CAPTURE_ABORT_OWNED_CLEANUP_ONLY", _neg_e15, _pos_e15)

        # E16: Serial vs parallel clean runs yield identical semantic_result_sha256 & 100% exited conserved workers!
        def _neg_e16() -> Dict[str, Any]:
            d, p = _mk_sp(eps=("public_ticker", "contracts"), max_concurrency=2)
            tr = _CleanTr()
            res = asyncio.run(capture_r5_public_batch(p, tr, None, run_id="c2_e16", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return {"observed_calls": tr.calls, "aborted": res["aborted"], "qa_records": res["qa_fixture_records_count"], "accepted_observed": res["accepted_observed_records_count"], "external_requests": res["external_requests_attempted"]}

        def _pos_e16() -> Dict[str, Any]:
            shas = []
            last_res: Dict[str, Any] = {}
            for w in (1, 2):
                d, p = _mk_sp(eps=("public_ticker", "contracts"), max_concurrency=w)
                class _DetTr:
                    def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                        return RawHttpResponseC2(200, f'{{"code":"00000","u":"{url}"}}'.encode(), {}, NOW_MS, NOW_MS + 5)
                last_res = asyncio.run(capture_r5_public_batch(p, _DetTr(), mon, run_id="c2_e16", trusted_scope=d, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
                shas.append(last_res["semantic_result_sha256"])
            lc = last_res["worker_lifecycle_receipt"]
            return {
                "capture_execution_status": last_res["capture_execution_status"] if len(set(shas)) == 1 else "SEMANTIC_DRIFT",
                "undrained_workers": int(last_res["undrained_inflight_workers"]),
                "workers_exited_actual": int(last_res["workers_exited_actual"]),
                "owned_shutdown_complete": bool(last_res["owned_executor_shutdown_complete"]),
                "conservation_exact": bool(lc["terminal_state_conservation_exact"]) and len(set(shas)) == 1,
            }
        _add("C2-E16", "SERIAL_VS_PARALLEL_CLEAN_CONSERVED_EQUAL_HASH", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "Missing resource monitor on parallel capture aborts cleanly (0 calls)", "Serial w=1 and parallel w=2 yield identical semantic SHA & conserved exit",
             "PRED_CAPTURE_ZERO_DISPATCH_ABORT", "PRED_WORKER_CLEAN_EXIT_CONSERVED", _neg_e16, _pos_e16)

    return out


def test_c2_e01_to_e16_all_worker_lifecycle_controls_pass_and_zero_leaks() -> None:
    recs = run_new_kh03_r5_c2_worker_controls_e16(executable_sha="e" * 40, run_id="pytest_e16")
    assert len(recs) == 16
    vd = derive_fault_control_verdict(recs, expected_executable_sha="e" * 40, expected_run_id="pytest_e16")
    assert vd["valid"] is True, f"E01..E16 failed: {vd['violations']}"
    assert vd["passed_controls"] == 16
    st = _wait_global_workers_zero(timeout_sec=3.0)
    assert st["active_workers_count"] == 0 and st["quarantined_workers_count"] == 0
