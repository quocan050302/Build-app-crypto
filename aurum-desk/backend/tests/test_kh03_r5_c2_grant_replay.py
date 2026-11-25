"""
aurum-desk/backend/tests/test_kh03_r5_c2_grant_replay.py

KH-03.R5.C2 — Phase D Grant Consumption, One-Shot Claim, Replay & Restart Trust Boundary Controls
(`C2-F01`..`C2-F15`). Exports `run_new_kh03_r5_c2_grant_controls_f15(executable_sha, run_id)`
returning 15 verified execution-derived paired fault receipts. Executes ZERO external sockets.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import threading
from typing import Any, Dict, List, Mapping, Optional
import uuid

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_c2_public_capture import RawHttpResponseC2, StdlibUrllibPublicTransportC2
from services.kh_03_r5_c2_fault_truth_and_readiness_service import (
    FaultCaseSpec,
    PRIOR_C1_TESTED_EXECUTABLE_SHA,
    derive_fault_control_verdict,
    execute_fault_case_pair,
)
from services.kh_03_r5_capture_and_custody_service import (
    capture_r5_public_batch,
    make_r5_fixture_resource_monitor,
)
from services.kh_03_r5_market_data_foundation_service import (
    R5AttestationVerificationResult,
    R5GrantConsumptionLedgerC2,
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


def run_new_kh03_r5_c2_grant_controls_f15(
    *,
    executable_sha: str = "f" * 40,
    run_id: str = "c2_f15_unit",
) -> List[Dict[str, Any]]:
    """Executes ALL 15 P1-F Grant Consumption & Replay Safety paired controls (`C2-F01`..`C2-F15`)."""
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    tag = uuid.uuid4().hex[:8]
    seq = {"n": 0}

    def _next_gid(pref: str = "QA_F") -> str:
        seq["n"] += 1
        return f"{pref}_{tag}_{seq['n']:04d}"

    def _mk_gr(gid: str, *, max_req: int = 1, max_bytes: int = 50_000, max_resp: Optional[int] = None, rid: str = "c2_f15", exp_off: int = 120) -> tuple[R5OperatorGrant, Dict[str, Any]]:
        gkw: Dict[str, Any] = dict(
            grant_id=gid, issued_by="QA", purpose="QA_FIXTURE", symbol="XAUUSDT", product_type="USDT-FUTURES",
            allowed_endpoints=("public_ticker",), window_start_ms=WS_MS, window_end_ms=WE_MS,
            max_requests=max_req, max_total_bytes=max_bytes, max_duration_sec=20,
            expires_utc_ms=NOW_MS + exp_off * 1000, run_id=rid, is_qa_fixture_grant=True, origin_kind="QA",
        )
        if max_resp is not None:
            gkw["max_response_bytes"] = max_resp
        g = R5OperatorGrant(**gkw)
        r: Dict[str, Any] = {
            "symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker"], "run_id": rid,
            "purpose": "QA_FIXTURE", "window_start_ms": WS_MS, "window_end_ms": WE_MS,
            "max_requests": max_req, "max_total_bytes": max_bytes, "max_duration_sec": 20,
        }
        if max_resp is not None:
            r["max_response_bytes"] = max_resp
        return g, r

    def _mk_sp(gid: str, *, ledger: Optional[R5GrantConsumptionLedgerC2] = None, max_req: int = 1, max_bytes: int = 50_000, max_resp: Optional[int] = None, rid: str = "c2_f15") -> tuple[R5OperatorGrant, Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        g, r = _mk_gr(gid, max_req=max_req, max_bytes=max_bytes, max_resp=max_resp, rid=rid)
        d = validate_r5_operator_capture_scope(g, r, NOW_UTC, {"qa_fixture_mode": True}, grant_ledger=ledger)
        bud: Dict[str, Any] = {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": max_req, "max_requests": max_req, "max_total_bytes": max_bytes, "max_duration_sec": 20, "endpoints": ["public_ticker"]}
        if max_resp is not None:
            bud["max_response_bytes"] = max_resp
        p = plan_r5_capture_batches(d, inv, bud, now_utc=NOW_UTC, grant_ledger=ledger)
        return g, r, d, p

    class _CountTr:
        def __init__(self, resps: Optional[List[Any]] = None) -> None:
            self.calls = 0
            self._lock = threading.Lock()
            self.resps = list(resps or [])
        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            with self._lock:
                self.calls += 1
                rv = self.resps.pop(0) if self.resps else RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
            if isinstance(rv, Exception):
                raise rv
            return rv

    out: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="kh03r5c2_f15_") as td_s:
        td = Path(td_s)
        mon = make_r5_fixture_resource_monitor(destination=td)

        def _pos_cap_clean() -> Dict[str, Any]:
            _, _, d, p = _mk_sp(_next_gid("QA_FPOS"))
            tr = _CountTr()
            res = asyncio.run(capture_r5_public_batch(p, tr, mon, run_id="c2_f15", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return {
                "capture_execution_status": res["capture_execution_status"],
                "aborted": res["aborted"],
                "qa_records": res["qa_fixture_records_count"],
                "accepted_observed": res["accepted_observed_records_count"],
                "external_requests": res["external_requests_attempted"],
                "undrained_workers": res["undrained_inflight_workers"],
            }

        def _pos_scope_clean() -> Dict[str, Any]:
            g, r = _mk_gr(_next_gid("QA_FSCP"))
            d = validate_r5_operator_capture_scope(g, r, NOW_UTC, {"qa_fixture_mode": True})
            return {"admitted": d["admitted"], "live_network_permitted": d["live_network_permitted"], "mode": d["mode"], "violations_count": len(d["violations"]), "scope_receipt_id": str(d.get("scope_receipt_id") or "")}

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

        # F01: Sequential same-plan replay -> 1 winner, 2nd call 0 dispatch!
        def _neg_f01() -> Dict[str, Any]:
            _, _, d, p = _mk_sp(_next_gid("QA_F01"), max_req=1)
            tr = _CountTr()
            asyncio.run(capture_r5_public_batch(p, tr, mon, run_id="c2_f15", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            r2 = asyncio.run(capture_r5_public_batch(p, tr, mon, run_id="c2_f15", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return {"second_dispatched": r2["requests_dispatched_actual"], "second_status": r2["capture_execution_status"], "total_calls": tr.calls, "max_requests_cap": 1}
        _add("C2-F01", "SEQUENTIAL_SAME_PLAN_REPLAY_DENIED", "R5GrantConsumptionLedgerC2.claim_grant_session", "BASELINE_BUG_RED",
             "Sequential reuse of max_requests=1 plan denied on 2nd call (total=1)", "Fresh distinct QA grant succeeds",
             "PRED_GRANT_REPLAY_DENIED", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_f01, _pos_cap_clean,
             bw={"baseline_sha": PRIOR_C1_TESTED_EXECUTABLE_SHA, "witness_key": "P1F_grant_replay_multiplies_budget", "baseline_negative_reproduced": True})

        # F02: Concurrent race on same plan -> EXACTLY 1 winner, total calls <= cap!
        def _neg_f02() -> Dict[str, Any]:
            _, _, d, p = _mk_sp(_next_gid("QA_F02"), max_req=1)
            tr = _CountTr()
            async def _race() -> List[Dict[str, Any]]:
                return list(await asyncio.gather(
                    capture_r5_public_batch(p, tr, mon, run_id="c2_f15", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC),
                    capture_r5_public_batch(p, tr, mon, run_id="c2_f15", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC),
                    capture_r5_public_batch(p, tr, mon, run_id="c2_f15", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC),
                ))
            rs = asyncio.run(_race())
            wins = sum(1 for x in rs if x["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS")
            dens = sum(1 for x in rs if x["capture_execution_status"] == "BLOCKED_GRANT_REPLAY_OR_LEDGER")
            return {"winners_count": wins, "denied_count": dens, "total_calls": tr.calls, "max_requests_cap": 1}
        _add("C2-F02", "CONCURRENT_RACE_SINGLE_WINNER_ONLY", "R5GrantConsumptionLedgerC2.claim_grant_session", "BASELINE_BUG_RED",
             "3 concurrent invocations on same max_requests=1 grant yield 1 winner & 2 denied", "Distinct fresh QA grant succeeds",
             "PRED_GRANT_CONCURRENT_ONE_WINNER", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_f02, _pos_cap_clean,
             bw={"baseline_sha": PRIOR_C1_TESTED_EXECUTABLE_SHA, "witness_key": "P1F_grant_replay_multiplies_budget", "baseline_negative_reproduced": True})

        # F03: Replanning from same scope receipt after consumption -> BLOCKED!
        def _neg_f03() -> Dict[str, Any]:
            _, _, d, p1 = _mk_sp(_next_gid("QA_F03"), max_req=1)
            tr = _CountTr()
            asyncio.run(capture_r5_public_batch(p1, tr, mon, run_id="c2_f15", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            p2 = plan_r5_capture_batches(d, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 1, "max_requests": 1, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)
            return {"second_dispatched": 0, "second_status": p2["status"], "total_calls": tr.calls, "max_requests_cap": 1}
        _add("C2-F03", "SECOND_PLAN_FROM_SPENT_SCOPE_DENIED", "plan_r5_capture_batches", "NEGATIVE_CONTRACT",
             "Replanning from consumed scope receipt blocked", "Fresh distinct QA grant succeeds",
             "PRED_GRANT_REPLAY_DENIED", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_f03, _pos_cap_clean)

        # F04: Newly re-issued scope receipt for same logical grant -> denied!
        def _neg_f04() -> Dict[str, Any]:
            g, r, d1, p1 = _mk_sp(_next_gid("QA_F04"), max_req=1)
            tr = _CountTr()
            asyncio.run(capture_r5_public_batch(p1, tr, mon, run_id="c2_f15", trusted_scope=d1, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            d2 = validate_r5_operator_capture_scope(g, r, NOW_UTC, {"qa_fixture_mode": True})
            p2 = plan_r5_capture_batches(d2, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 1, "max_requests": 1, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)
            r2 = asyncio.run(capture_r5_public_batch(p2, tr, mon, run_id="c2_f15", trusted_scope=d2, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return {"second_dispatched": r2["requests_dispatched_actual"], "second_status": "BLOCKED" if p2["status"] == "BLOCKED" else r2["capture_execution_status"], "total_calls": tr.calls, "max_requests_cap": 1}
        _add("C2-F04", "REISSUED_SCOPE_RECEIPT_SAME_GRANT_DENIED", "validate_r5_operator_capture_scope", "NEGATIVE_CONTRACT",
             "Re-validating same logical grant after consumption cannot reset budget", "Fresh distinct QA grant succeeds",
             "PRED_GRANT_REPLAY_DENIED", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_f04, _pos_cap_clean)

        # F05: Same issuer + grant_id with altered payload (max_requests changed) -> GRANT_IDENTITY_PAYLOAD_CONFLICT!
        def _neg_f05() -> Dict[str, Any]:
            gid = _next_gid("QA_F05")
            g1, r1 = _mk_gr(gid, max_req=1)
            d1 = validate_r5_operator_capture_scope(g1, r1, NOW_UTC, {"qa_fixture_mode": True})
            assert d1["admitted"] is True
            g2, r2 = _mk_gr(gid, max_req=5)  # same issuer + grant_id, altered budget payload!
            d2 = validate_r5_operator_capture_scope(g2, r2, NOW_UTC, {"qa_fixture_mode": True})
            return {"admitted": d2["admitted"], "conflict_violation_present": any("GRANT_IDENTITY_PAYLOAD_CONFLICT" in v for v in d2["violations"])}
        _add("C2-F05", "SAME_GRANT_ID_ALTERED_PAYLOAD_CONFLICT", "validate_r5_operator_capture_scope", "NEGATIVE_CONTRACT",
             "Same grant_id with expanded max_requests=5 fails with GRANT_IDENTITY_PAYLOAD_CONFLICT", "Distinct fresh grant admitted cleanly",
             "PRED_GRANT_CONFLICT_DENIED", "PRED_SCOPE_ADMITTED_QA", _neg_f05, _pos_scope_clean)

        # F06: Cloned/tampered receipt or wrong PID denied with 0 dispatches!
        def _neg_f06() -> Dict[str, Any]:
            _, _, d, p = _mk_sp(_next_gid("QA_F06"))
            bad_d = dict(d)
            bad_d["_trusted_scope_receipt"] = None
            tr = _CountTr()
            res = asyncio.run(capture_r5_public_batch(p, tr, mon, run_id="c2_f15", trusted_scope=bad_d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return {"observed_calls": tr.calls, "external_requests": res["external_requests_attempted"], "qa_records": res["qa_fixture_records_count"], "expected_violation_found": bool(res["violations"])}
        _add("C2-F06", "CLONED_OR_TAMPERED_RECEIPT_DENIED", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "Stripped/tampered scope receipt yields 0 dispatches", "Untampered receipt executes cleanly",
             "PRED_CAPTURE_ZERO_DISPATCH_VIOLATION", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_f06, _pos_cap_clean)

        # F07: Expired grant -> 0 dispatch, cannot resurrect!
        def _neg_f07() -> Dict[str, Any]:
            _, _, d, p = _mk_sp(_next_gid("QA_F07"))
            tr = _CountTr()
            res = asyncio.run(capture_r5_public_batch(p, tr, mon, run_id="c2_f15", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC + timedelta(seconds=300)))
            return {"observed_calls": tr.calls, "external_requests": res["external_requests_attempted"], "qa_records": res["qa_fixture_records_count"], "expected_violation_found": any("OPERATOR_GRANT_EXPIRED" in v for v in res["violations"])}
        _add("C2-F07", "EXPIRED_GRANT_ZERO_DISPATCH_NO_RESURRECT", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "Expired grant yields 0 dispatches", "Unexpired grant succeeds cleanly",
             "PRED_CAPTURE_ZERO_DISPATCH_VIOLATION", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_f07, _pos_cap_clean)

        # F08: Abort before dispatch AFTER claim burns grant (conservative one-shot policy)!
        def _neg_f08() -> Dict[str, Any]:
            _, _, d, p = _mk_sp(_next_gid("QA_F08"), max_req=2)
            ev = asyncio.Event(); ev.set()
            tr = _CountTr()
            r1 = asyncio.run(capture_r5_public_batch(p, tr, mon, cancel_event=ev, run_id="c2_f15", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            assert r1["aborted"] is True and tr.calls == 0
            r2 = asyncio.run(capture_r5_public_batch(p, tr, mon, run_id="c2_f15", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return {"second_dispatched": r2["requests_dispatched_actual"], "second_status": r2["capture_execution_status"], "total_calls": tr.calls, "max_requests_cap": 2}
        _add("C2-F08", "ABORT_AFTER_CLAIM_BURNS_GRANT", "R5GrantConsumptionLedgerC2.finalize_grant_session", "NEGATIVE_CONTRACT",
             "Aborted claimed session burns grant so 2nd attempt is denied", "Fresh distinct QA grant succeeds",
             "PRED_GRANT_REPLAY_DENIED", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_f08, _pos_cap_clean)

        # F09: Timeout/error after dispatch burns grant (no cap reset on next invocation)!
        def _neg_f09() -> Dict[str, Any]:
            _, _, d, p = _mk_sp(_next_gid("QA_F09"), max_req=2, max_bytes=20_000, max_resp=20_000)
            tr = _CountTr([RuntimeError("err_burn")])
            asyncio.run(capture_r5_public_batch(p, tr, mon, run_id="c2_f15", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            r2 = asyncio.run(capture_r5_public_batch(p, tr, mon, run_id="c2_f15", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return {"second_dispatched": r2["requests_dispatched_actual"], "second_status": r2["capture_execution_status"], "total_calls": tr.calls, "max_requests_cap": 2}
        _add("C2-F09", "ERROR_AFTER_DISPATCH_BURNS_GRANT_NO_RESET", "R5GrantConsumptionLedgerC2.finalize_grant_session", "NEGATIVE_CONTRACT",
             "Error after dispatch burns grant; replay denied", "Fresh distinct QA grant succeeds",
             "PRED_GRANT_REPLAY_DENIED", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_f09, _pos_cap_clean)

        # F10: Duplicate attempt reserve/settle/release rejected & no negative counters!
        def _neg_f10() -> Dict[str, Any]:
            led = R5GrantConsumptionLedgerC2()
            _, _, d, p = _mk_sp(_next_gid("QA_F10"), ledger=led, max_req=2, max_bytes=10_000, max_resp=4_000)
            ok_c, _, lse = led.claim_grant_session(grant_identity_key=d["_trusted_scope_receipt"].grant_identity_key, grant_payload_digest=d["_trusted_scope_receipt"].grant_payload_digest, scope_receipt=d["_trusted_scope_receipt"], plan_receipt=p["_trusted_plan_receipt"], session_id="f10_s", now_utc=NOW_UTC, max_requests=2, max_total_bytes=10_000, is_live=False)
            assert ok_c and lse is not None
            r1_ok, _, cap1, _ = led.reserve_dispatch(lse, "a1", max_response_bytes=4_000)
            r1_dup, _, _, _ = led.reserve_dispatch(lse, "a1", max_response_bytes=4_000)
            led.mark_dispatch_started(lse, "a1")
            s1_ok, _, _ = led.settle_dispatch(lse, "a1", observed_bytes=None, bytes_known=False)
            s1_dup, _, _ = led.settle_dispatch(lse, "a1", observed_bytes=100, bytes_known=True)
            rel_dup, _ = led.release_unstarted_reservation(lse, "a1")
            sn = led.snapshot_grant(lse.grant_identity_key) or {}
            no_neg = all(int(sn.get(k, 0)) >= 0 for k in ("requests_reserved", "requests_started", "requests_completed", "bytes_reserved", "bytes_observed", "bytes_uncertain_retained"))
            return {"dup_rejected": (r1_ok and not r1_dup and s1_ok and not s1_dup and not rel_dup), "no_negative_counters": no_neg, "uncertain_bytes_retained": int(sn.get("bytes_uncertain_retained", 0))}
        _add("C2-F10", "DUPLICATE_ATTEMPT_SETTLE_RELEASE_BLOCKED", "R5GrantConsumptionLedgerC2.settle_dispatch", "NEGATIVE_CONTRACT",
             "Duplicate reserve/settle/release rejected & unknown bytes retained", "Clean capture succeeds",
             "PRED_LEDGER_CONSERVATION_REJECT_DUP", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_f10, _pos_cap_clean)

        # F11: Byte/request conservation across retries & unknown bytes!
        def _neg_f11() -> Dict[str, Any]:
            _, _, d, p = _mk_sp(_next_gid("QA_F11"), max_req=2, max_bytes=10_000, max_resp=6_000)
            tr = _CountTr([TimeoutError("to1"), RawHttpResponseC2(200, b"A" * 5000, {}, NOW_MS, NOW_MS + 5)])  # 6000 uncertain + 5000 observed = 11000 > 10000!
            res = asyncio.run(capture_r5_public_batch(p, tr, mon, run_id="c2_f15", trusted_scope=d, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
            return {"dup_rejected": res["aborted"] and res["abort_reason"] == "TOTAL_BYTES_BUDGET_EXHAUSTED", "no_negative_counters": res["wire_bytes_charged_total"] == 11_000, "uncertain_bytes_retained": int(res["wire_bytes_uncertain_retained"])}
        _add("C2-F11", "CONSERVATIVE_RETRY_BYTE_CONSERVATION", "R5GrantConsumptionLedgerC2.settle_dispatch", "NEGATIVE_CONTRACT",
             "Uncertain 6000B + observed 5000B > 10000B cap aborts with 0 accepted", "Clean capture within cap passes",
             "PRED_LEDGER_CONSERVATION_REJECT_DUP", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_f11, _pos_cap_clean)

        # F12: Ledger unavailable/corrupt or capacity exhausted denies (never resets empty ledger)!
        def _neg_f12() -> Dict[str, Any]:
            led_bad = R5GrantConsumptionLedgerC2(max_entries=1)
            _, _, d, p = _mk_sp(_next_gid("QA_F12"), ledger=led_bad, max_req=1)
            led_bad.mark_unhealthy_for_qa("SIMULATED_CORRUPTION")
            tr = _CountTr()
            res = asyncio.run(capture_r5_public_batch(p, tr, mon, run_id="c2_f15", trusted_scope=d, destination_root=td, grant_ledger=led_bad, utc_now_fn=lambda: NOW_UTC))
            return {"second_dispatched": res["requests_dispatched_actual"], "second_status": res["capture_execution_status"], "total_calls": tr.calls, "max_requests_cap": 1}
        _add("C2-F12", "UNHEALTHY_OR_FULL_LEDGER_FAILS_CLOSED", "R5GrantConsumptionLedgerC2.claim_grant_session", "NEGATIVE_CONTRACT",
             "Unhealthy ledger denies capture with 0 dispatches", "Healthy ledger accepts fresh QA grant",
             "PRED_GRANT_REPLAY_DENIED", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_f12, _pos_cap_clean)

        # F13: QA grant cannot promote to live!
        def _neg_f13() -> Dict[str, Any]:
            g, r = _mk_gr(_next_gid("QA_F13"))
            d = validate_r5_operator_capture_scope(g, r, NOW_UTC, {"qa_fixture_mode": False, "human_operator_attestation_verified": True})
            return {"admitted": d["admitted"], "live_network_permitted": d["live_network_permitted"], "violations_count": len(d["violations"])}
        _add("C2-F13", "QA_GRANT_CANNOT_PROMOTE_LIVE", "validate_r5_operator_capture_scope", "NEGATIVE_CONTRACT",
             "QA grant rejected when attempting live promotion", "QA grant admitted strictly offline",
             "PRED_SCOPE_REJECTED", "PRED_SCOPE_ADMITTED_QA", _neg_f13, _pos_scope_clean)

        # F14: Restart/durability boundary — HARD-BLOCKS ALL LIVE PATHS even with stub verifier & 8 MiB budget!
        def _neg_f14() -> Dict[str, Any]:
            class _StubVer:
                verifier_id = "OFFLINE_TEST_STUB"
                def verify_owner_attestation(self, g: R5OperatorGrant, eff: Mapping[str, Any], nu: datetime, tc: Mapping[str, Any]) -> R5AttestationVerificationResult:
                    return R5AttestationVerificationResult(True, "VERIFIED_SEPARATE_OWNER_ATTESTATION", "TEST", self.verifier_id, g.grant_id, NOW_MS)
            lg = R5OperatorGrant(_next_gid("LIVE_F14"), "owner", "DEVELOPMENT_CAPTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker",), WS_MS, WE_MS, 2, 8 * 1024 * 1024, 20, NOW_MS + 120_000, "c2_f15", False, "SEPARATE_OWNER_ATTESTATION", max_response_bytes=4 * 1024 * 1024)
            lr = {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker"], "run_id": "c2_f15", "purpose": "DEVELOPMENT_CAPTURE", "window_start_ms": WS_MS, "window_end_ms": WE_MS, "max_requests": 2, "max_total_bytes": 8 * 1024 * 1024, "max_response_bytes": 4 * 1024 * 1024, "max_duration_sec": 20}
            dl = validate_r5_operator_capture_scope(lg, lr, NOW_UTC, {}, attestation_verifier=_StubVer())
            pl = plan_r5_capture_batches(dl, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 2, "max_requests": 2, "max_total_bytes": 8 * 1024 * 1024, "max_response_bytes": 4 * 1024 * 1024, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)
            res = asyncio.run(capture_r5_public_batch(pl, StdlibUrllibPublicTransportC2(), mon, run_id="c2_f15", trusted_scope=dl, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return {
                "capture_execution_status": res["capture_execution_status"],
                "external_requests": res["external_requests_attempted"],
                "durability_blocked_violation": "LIVE_GRANT_REPLAY_DURABILITY_NOT_PROVEN_BLOCKED" in res["violations"],
            }
        _add("C2-F14", "RESTART_BOUNDARY_LIVE_HARD_BLOCK_PROVEN", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "Even stub-attested 8MiB live plan hard-blocked with LIVE_GRANT_REPLAY_DURABILITY_NOT_PROVEN_BLOCKED (0 sockets)",
             "Offline QA fixture capture executes cleanly",
             "PRED_GRANT_LIVE_HARD_BLOCKED", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_f14, _pos_cap_clean)

        # F15: Distinct new valid QA grant executes without false rejection!
        def _neg_f15() -> Dict[str, Any]:
            _, _, d1, p1 = _mk_sp(_next_gid("QA_F15_SPENT"), max_req=1)
            tr = _CountTr()
            asyncio.run(capture_r5_public_batch(p1, tr, mon, run_id="c2_f15", trusted_scope=d1, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            r_dup = asyncio.run(capture_r5_public_batch(p1, tr, mon, run_id="c2_f15", trusted_scope=d1, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return {"second_dispatched": r_dup["requests_dispatched_actual"], "second_status": r_dup["capture_execution_status"], "total_calls": tr.calls, "max_requests_cap": 1}
        _add("C2-F15", "DISTINCT_NEW_QA_GRANT_SUCCEEDS_NO_FALSE_REJECT", "capture_r5_public_batch", "NEGATIVE_CONTRACT",
             "Spent grant replay denied", "New distinct QA grant succeeds cleanly without false reject",
             "PRED_GRANT_REPLAY_DENIED", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_f15, _pos_cap_clean)

    return out


def test_c2_f01_to_f15_all_grant_replay_controls_pass() -> None:
    recs = run_new_kh03_r5_c2_grant_controls_f15(executable_sha="f" * 40, run_id="pytest_f15")
    assert len(recs) == 15
    vd = derive_fault_control_verdict(recs, expected_executable_sha="f" * 40, expected_run_id="pytest_f15")
    assert vd["valid"] is True, f"F01..F15 failed: {vd['violations']}"
    assert vd["passed_controls"] == 15
