"""
aurum-desk/backend/tests/test_kh03_r5_c1_custody_abort_and_determinism.py

KH-03.R5.C1 (Repaired in KH-03.R5.C2 P1-D) — Staging/Custody Integrity, Abort Status Truth,
AND 48 Repaired C1 Fault Controls (`C1-A01..A18`, `C1-B01..B15`, `C1-C01..C15`) backed by
typed execution-derived paired negative + clean-positive receipts (`execute_fault_case_pair`),
ZERO literal boolean `_rec(..., True)` shortcuts, distinct QA grants per invocation, and
honest `BASELINE_BUG_RED` (exact 8 verified `09493feb` witnesses) vs `NEGATIVE_CONTRACT` (40).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Mapping, Optional
import uuid

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_c2_public_capture import RawHttpResponseC2, StdlibUrllibPublicTransportC2
from services.kh_02_r2_c3_provenance_guard import MAX_RAW_HTTP_BODY_BYTES_C3
from services.kh_03_r5_c2_fault_truth_and_readiness_service import (
    FaultCaseSpec,
    PRIOR_R5_BASELINE_SHA,
    VERIFIED_R5_BASELINE_WITNESS_MAP_C2,
    audit_fault_certification_ast_for_literal_flags,
    derive_fault_control_verdict,
    execute_fault_case_pair,
)
from services.kh_03_r5_capture_and_custody_service import (
    R5DevCustodyJournal,
    _canonical_volume_identity_c1,
    _parse_retry_after_strict,
    capture_r5_public_batch,
    make_r5_fixture_resource_monitor,
    persist_r5_verified_dev_snapshot,
    verify_r5_custody_chain_readonly,
)
from services.kh_03_r5_market_data_foundation_service import (
    R5AttestationVerificationResult,
    R5OperatorGrant,
    build_r5_source_capability_inventory,
    plan_r5_capture_batches,
    validate_r5_operator_capture_scope,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

NOW_UTC = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW_UTC.timestamp() * 1000)
WS_MS = 1794000000000
WE_MS = 1794000600000
OK_BODY = b'{"code":"00000","data":[{"symbol":"XAUUSDT","bidPr":"4100.00","askPr":"4100.20"}]}'


class _SpyOfflineC1:
    def __init__(self, resps: Optional[List[Any]] = None, on_call: Any = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.resps = list(resps or [])
        self.on_call = on_call

    def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
        idx = len(self.calls)
        self.calls.append({"url": url, "params": dict(params), "timeout_sec": float(timeout_sec)})
        if self.on_call:
            self.on_call(idx, url, params, timeout_sec)
        rv = self.resps.pop(0) if self.resps else RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
        if isinstance(rv, Exception):
            raise rv
        return rv


def run_new_kh03_r5_c1_fault_controls(
    *,
    executable_sha: str = "0" * 40,
    run_id: str = "c1_48_paired_unit",
) -> List[Dict[str, Any]]:
    """
    Executes ALL 48 repaired KH-03.R5.C1 controls (`C1-A01..A18`, `C1-B01..B15`, `C1-C01..C15`)
    as independent negative + clean-positive same-contract pairs using `execute_fault_case_pair(...)`.
    Executes ZERO external market sockets; mints a distinct QA grant per independent invocation.
    """
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    run_tag = uuid.uuid4().hex[:8]
    seq_box = {"n": 0}

    def _next_gid(prefix: str = "QA_C1R") -> str:
        seq_box["n"] += 1
        return f"{prefix}_{run_tag}_{seq_box['n']:04d}"

    def _mk_qa(
        rid: str = "c1_f48",
        eps: tuple[str, ...] = ("public_ticker",),
        **over: Any,
    ) -> tuple[R5OperatorGrant, Dict[str, Any]]:
        gkw: Dict[str, Any] = dict(
            grant_id=_next_gid(),
            issued_by="QA",
            purpose="QA_FIXTURE",
            symbol="XAUUSDT",
            product_type="USDT-FUTURES",
            allowed_endpoints=eps,
            window_start_ms=WS_MS,
            window_end_ms=WE_MS,
            max_requests=6,
            max_total_bytes=50_000,
            max_duration_sec=20,
            expires_utc_ms=NOW_MS + 120_000,
            run_id=rid,
            is_qa_fixture_grant=True,
            origin_kind="QA",
        )
        gkw.update(over)
        g = R5OperatorGrant(**gkw)
        r: Dict[str, Any] = {
            "symbol": g.symbol,
            "product_type": g.product_type,
            "endpoints": list(eps),
            "run_id": g.run_id,
            "purpose": g.purpose,
            "window_start_ms": g.window_start_ms,
            "window_end_ms": g.window_end_ms,
            "max_requests": g.max_requests,
            "max_total_bytes": g.max_total_bytes,
            "max_duration_sec": g.max_duration_sec,
        }
        if g.max_response_bytes is not None:
            r["max_response_bytes"] = g.max_response_bytes
        if g.max_concurrency is not None:
            r["max_concurrency"] = g.max_concurrency
        return g, r

    def _mk_live(rid: str = "c1_f48") -> tuple[R5OperatorGrant, Dict[str, Any]]:
        g = R5OperatorGrant(
            _next_gid("LIVE_C1R"), "owner", "DEVELOPMENT_CAPTURE", "XAUUSDT", "USDT-FUTURES",
            ("public_ticker",), WS_MS, WE_MS, 5, 50_000, 20, NOW_MS + 120_000,
            rid, False, "SEPARATE_OWNER_ATTESTATION",
        )
        r = {
            "symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker"],
            "run_id": rid, "purpose": "DEVELOPMENT_CAPTURE", "window_start_ms": WS_MS,
            "window_end_ms": WE_MS, "max_requests": 5, "max_total_bytes": 50_000, "max_duration_sec": 20,
        }
        return g, r

    def _mk_scope_plan(
        *,
        rid: str = "c1_f48",
        eps: tuple[str, ...] = ("public_ticker",),
        max_concurrency: int = 1,
        **over: Any,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        g, r = _mk_qa(rid=rid, eps=eps, **over)
        d = validate_r5_operator_capture_scope(g, r, NOW_UTC, {"qa_fixture_mode": True})
        bud: Dict[str, Any] = {
            "destination_disk_free_gib": 30.0,
            "max_concurrency": max_concurrency,
            "max_pages": g.max_requests,
            "max_requests": g.max_requests,
            "max_total_bytes": g.max_total_bytes,
            "max_duration_sec": g.max_duration_sec,
            "endpoints": list(eps),
        }
        if g.max_response_bytes is not None:
            bud["max_response_bytes"] = g.max_response_bytes
        p = plan_r5_capture_batches(d, inv, bud, now_utc=NOW_UTC)
        return d, p

    def _obs_scope(dec: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "admitted": bool(dec.get("admitted")),
            "live_network_permitted": bool(dec.get("live_network_permitted")),
            "authorization_status": str(dec.get("authorization_status") or ""),
            "mode": str(dec.get("mode") or ""),
            "violations_count": len(dec.get("violations") or ()),
            "scope_receipt_id": str(dec.get("scope_receipt_id") or ""),
        }

    def _obs_plan(pl: Mapping[str, Any]) -> Dict[str, Any]:
        bs = list(pl.get("batches") or ())
        return {
            "status": str(pl.get("status") or ""),
            "live_network_permitted": bool(pl.get("live_network_permitted")),
            "batches_count": len(bs),
            "violations_count": len(pl.get("violations") or ()),
            "pages_semantics": str(pl.get("pages_semantics") or ""),
            "first_batch_max_pages": int(bs[0]["max_pages"]) if bs and "max_pages" in bs[0] else 0,
        }

    def _obs_cap(res: Mapping[str, Any], spy_calls: int) -> Dict[str, Any]:
        return {
            "capture_execution_status": str(res.get("capture_execution_status") or ""),
            "aborted": bool(res.get("aborted")),
            "abort_reason": str(res.get("abort_reason") or ""),
            "observed_calls": int(spy_calls),
            "dispatched_actual": int(res.get("requests_dispatched_actual") or 0),
            "qa_records": int(res.get("qa_fixture_records_count") or 0),
            "accepted_observed": int(res.get("accepted_observed_records_count") or 0),
            "external_requests": int(res.get("external_requests_attempted") or 0),
            "undrained_workers": int(res.get("undrained_inflight_workers") or 0),
            "owned_shutdown_complete": bool(res.get("owned_executor_shutdown_complete")),
            "wire_bytes_observed": int(res.get("wire_bytes_observed_total") or 0),
            "bytes_accepted": int(res.get("bytes_accepted_total") or 0),
            "removed_staged_count": int(res.get("removed_staged_files_count") or 0),
            "cleanup_complete": bool(res.get("cleanup_complete", True)),
        }

    out: List[Dict[str, Any]] = []

    def _run_pair(
        fid: str,
        name: str,
        target: str,
        neg_desc: str,
        pos_desc: str,
        neg_pred: str,
        pos_pred: str,
        neg_fn: Any,
        pos_fn: Any,
    ) -> None:
        wk = VERIFIED_R5_BASELINE_WITNESS_MAP_C2.get(fid)
        kind = "BASELINE_BUG_RED" if wk is not None else "NEGATIVE_CONTRACT"
        bw = (
            {
                "baseline_sha": PRIOR_R5_BASELINE_SHA,
                "witness_key": wk,
                "baseline_negative_reproduced": True,
                "source_artifact": "kh_03_r5_c1_capture_safety/20261125T030440Z_3ad0c2973eea/03_real_baseline_red_reproduction.json",
            }
            if wk is not None
            else None
        )
        spec = FaultCaseSpec(
            fault_id=fid,
            control_name=name,
            target_qualified_name=target,
            kind=kind,
            negative_perturbation=neg_desc,
            positive_clean_description=pos_desc,
            negative_predicate_id=neg_pred,
            positive_predicate_id=pos_pred,
            negative_runner=neg_fn,
            positive_runner=pos_fn,
            baseline_witness_key=wk,
            baseline_sha=PRIOR_R5_BASELINE_SHA if wk is not None else None,
        )
        out.append(
            execute_fault_case_pair(
                spec, executable_sha=executable_sha, run_id=run_id, baseline_witness=bw
            )
        )

    with tempfile.TemporaryDirectory(prefix="kh03r5c1_f48_") as td_s:
        td = Path(td_s)
        mon = make_r5_fixture_resource_monitor(destination=td)

        def _pos_scope_clean() -> Dict[str, Any]:
            g, r = _mk_qa()
            return _obs_scope(validate_r5_operator_capture_scope(g, r, NOW_UTC, {"qa_fixture_mode": True}))

        def _pos_plan_clean() -> Dict[str, Any]:
            _, p = _mk_scope_plan()
            return _obs_plan(p)

        def _pos_capture_clean(eps: tuple[str, ...] = ("public_ticker",), max_concurrency: int = 1, **over: Any) -> Dict[str, Any]:
            d, p = _mk_scope_plan(eps=eps, max_concurrency=max_concurrency, **over)
            sp = _SpyOfflineC1()
            res = asyncio.run(capture_r5_public_batch(p, sp, mon, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
            return _obs_cap(res, len(sp.calls))

        # --- P1-A: 18 unique paired scope/trust/plan binding controls (C1-A01..C1-A18) ---
        _run_pair(
            "C1-A01", "MISSING_GRANT_BLOCKED", "validate_r5_operator_capture_scope",
            "Missing owner grant (None) on DEVELOPMENT_CAPTURE", "Valid QA_FIXTURE grant admitted offline",
            "PRED_LIVE_DEFAULT_DENIED", "PRED_SCOPE_ADMITTED_QA",
            lambda: _obs_scope(validate_r5_operator_capture_scope(None, _mk_live()[1], NOW_UTC, {})),
            _pos_scope_clean,
        )
        _run_pair(
            "C1-A02", "FORGED_TRUST_DICT_DENIED_DEFAULT_VERIFIER", "validate_r5_operator_capture_scope",
            "Forged trust_context dict claiming human_operator_attestation_verified=True", "Valid QA_FIXTURE grant admitted offline",
            "PRED_LIVE_DEFAULT_DENIED", "PRED_SCOPE_ADMITTED_QA",
            lambda: (lambda lg, lr: _obs_scope(validate_r5_operator_capture_scope(lg, lr, NOW_UTC, {"human_operator_attestation_verified": True, "attestation_source": "EXPLICIT_SEPARATE_OWNER_GRANT"})))(*_mk_live()),
            _pos_scope_clean,
        )
        _run_pair(
            "C1-A03", "CLI_ENV_PROMPT_REJECTED", "validate_r5_operator_capture_scope",
            "Trust context with from_cli_flag=True rejected", "Clean QA trust_context without CLI/env/prompt flags",
            "PRED_SCOPE_REJECTED", "PRED_SCOPE_ADMITTED_QA",
            lambda: (lambda g, r: _obs_scope(validate_r5_operator_capture_scope(g, r, NOW_UTC, {"qa_fixture_mode": True, "from_cli_flag": True})))(*_mk_qa()),
            _pos_scope_clean,
        )
        _run_pair(
            "C1-A04", "QA_GRANT_PROMOTE_LIVE_REJECTED", "validate_r5_operator_capture_scope",
            "QA grant promoted with qa_fixture_mode=False & human_operator_attestation_verified=True", "Clean QA grant in QA_FIXTURE_OFFLINE_ONLY mode",
            "PRED_SCOPE_REJECTED", "PRED_SCOPE_ADMITTED_QA",
            lambda: (lambda g, r: _obs_scope(validate_r5_operator_capture_scope(g, r, NOW_UTC, {"qa_fixture_mode": False, "human_operator_attestation_verified": True})))(*_mk_qa()),
            _pos_scope_clean,
        )
        _run_pair(
            "C1-A05", "BOOL_AS_INT_BUDGET_REJECTED", "validate_r5_operator_capture_scope",
            "max_requests=True boolean passed as int budget", "Clean int max_requests=6 scope",
            "PRED_SCOPE_REJECTED", "PRED_SCOPE_ADMITTED_QA",
            lambda: (lambda g, r: _obs_scope(validate_r5_operator_capture_scope(g, {**r, "max_requests": True}, NOW_UTC, {"qa_fixture_mode": True})))(*_mk_qa()),
            _pos_scope_clean,
        )
        _run_pair(
            "C1-A06", "NAN_INF_MISSING_DURATION_REJECTED", "validate_r5_operator_capture_scope",
            "NaN and missing max_duration_sec rejected", "Clean finite positive max_duration_sec=20",
            "PRED_SCOPE_REJECTED", "PRED_SCOPE_ADMITTED_QA",
            lambda: (lambda g, r: (lambda d1, d2: {**_obs_scope(d1), "admitted": bool(d1["admitted"] or d2["admitted"]), "violations_count": len(d1["violations"]) + len(d2["violations"])})(
                validate_r5_operator_capture_scope(g, {**r, "max_duration_sec": float("nan")}, NOW_UTC, {"qa_fixture_mode": True}),
                validate_r5_operator_capture_scope(g, {k: v for k, v in r.items() if k != "max_duration_sec"}, NOW_UTC, {"qa_fixture_mode": True}),
            ))(*_mk_qa()),
            _pos_scope_clean,
        )
        _run_pair(
            "C1-A07", "WINDOW_OUTSIDE_OR_INVERTED_REJECTED", "validate_r5_operator_capture_scope",
            "Window before grant start and inverted start>end rejected", "Clean in-bounds window [WS_MS, WE_MS]",
            "PRED_SCOPE_REJECTED", "PRED_SCOPE_ADMITTED_QA",
            lambda: (lambda g, r: (lambda d1, d2: {**_obs_scope(d1), "admitted": bool(d1["admitted"] or d2["admitted"]), "violations_count": len(d1["violations"]) + len(d2["violations"])})(
                validate_r5_operator_capture_scope(g, {**r, "window_start_ms": WS_MS - 1000}, NOW_UTC, {"qa_fixture_mode": True}),
                validate_r5_operator_capture_scope(g, {**r, "window_start_ms": WE_MS, "window_end_ms": WS_MS}, NOW_UTC, {"qa_fixture_mode": True}),
            ))(*_mk_qa()),
            _pos_scope_clean,
        )
        _run_pair(
            "C1-A08", "ENDPOINT_OUTSIDE_GRANT_REJECTED", "validate_r5_operator_capture_scope",
            "Endpoint contracts outside grant allowed_endpoints=(public_ticker,)", "Clean endpoint public_ticker inside grant",
            "PRED_SCOPE_REJECTED", "PRED_SCOPE_ADMITTED_QA",
            lambda: (lambda g, r: _obs_scope(validate_r5_operator_capture_scope(g, {**r, "endpoints": ["contracts"]}, NOW_UTC, {"qa_fixture_mode": True})))(*_mk_qa()),
            _pos_scope_clean,
        )
        _run_pair(
            "C1-A09", "DUPLICATE_ENDPOINT_AND_OOS_REJECTED", "validate_r5_operator_capture_scope",
            "Duplicate endpoint and real_oos_escrow destination rejected", "Clean single non-OOS endpoint",
            "PRED_SCOPE_REJECTED", "PRED_SCOPE_ADMITTED_QA",
            lambda: (lambda g, r: (lambda d1, d2: {**_obs_scope(d1), "admitted": bool(d1["admitted"] or d2["admitted"]), "violations_count": len(d1["violations"]) + len(d2["violations"])})(
                validate_r5_operator_capture_scope(g, {**r, "endpoints": ["public_ticker", "public_ticker"]}, NOW_UTC, {"qa_fixture_mode": True}),
                validate_r5_operator_capture_scope(g, {**r, "destination_scope": "/tmp/real_oos_escrow"}, NOW_UTC, {"qa_fixture_mode": True}),
            ))(*_mk_qa()),
            _pos_scope_clean,
        )
        _run_pair(
            "C1-A10", "PLANNER_REJECTS_BROADER_BUDGET_NO_CLAMP", "plan_r5_capture_batches",
            "Planner request max_requests=50 > grant cap 6 blocked (no silent clamp)", "Planner request within grant cap admitted",
            "PRED_PLAN_BLOCKED", "PRED_PLAN_ADMITTED_QA",
            lambda: (lambda d, _p: _obs_plan(plan_r5_capture_batches(d, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 6, "max_requests": 50, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)))(*_mk_scope_plan()),
            _pos_plan_clean,
        )
        _run_pair(
            "C1-A11", "PLANNER_REJECTS_DIFFERENT_SYMBOL_ENDPOINT", "plan_r5_capture_batches",
            "Grant A (XAUUSDT/public_ticker) paired with planner B (BTCUSDT/contracts) blocked", "Matching symbol/endpoint planned cleanly",
            "PRED_PLAN_BLOCKED", "PRED_PLAN_ADMITTED_QA",
            lambda: (lambda d, _p: _obs_plan(plan_r5_capture_batches(d, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["contracts"], "symbol": "BTCUSDT"}, now_utc=NOW_UTC)))(*_mk_scope_plan()),
            _pos_plan_clean,
        )
        _run_pair(
            "C1-A12", "PLANNER_REJECTS_FORGED_ADMITTED_DICT", "plan_r5_capture_batches",
            "Forged admitted scope dict stripped of process-local receipt token rejected", "Genuine trusted scope receipt planned cleanly",
            "PRED_PLAN_BLOCKED", "PRED_PLAN_ADMITTED_QA",
            lambda: (lambda d, _p: _obs_plan(plan_r5_capture_batches({k: v for k, v in d.items() if not k.startswith("_")}, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)))(*_mk_scope_plan()),
            _pos_plan_clean,
        )

        def _neg_a13() -> Dict[str, Any]:
            _, p = _mk_scope_plan()
            sp = _SpyOfflineC1()
            res = asyncio.run(capture_r5_public_batch({"status": "PLANNED_QA_OFFLINE", "partition_role": "QA_FIXTURE", "live_network_permitted": False, "budget": p["budget"], "batches": p["batches"]}, sp, mon, run_id="c1_f48", destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return _obs_cap(res, len(sp.calls))
        _run_pair("C1-A13", "FORGED_UNBOUND_PLAN_ZERO_DISPATCH", "capture_r5_public_batch", "Forged unbound plan dict yields 0 dispatches", "Bound trusted plan succeeds offline", "PRED_CAPTURE_ZERO_DISPATCH_ABORT", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_a13, _pos_capture_clean)

        def _neg_a14() -> Dict[str, Any]:
            d, p = _mk_scope_plan()
            p_re = dict(p, batches=[dict(p["batches"][0], symbol="BTCUSDT")])
            p_re["plan_digest_sha256"] = canonical_hash({k: v for k, v in p_re.items() if not k.startswith("_") and k != "plan_digest_sha256"})
            sp = _SpyOfflineC1()
            res = asyncio.run(capture_r5_public_batch(p_re, sp, mon, run_id="c1_f48", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return {**_obs_cap(res, len(sp.calls)), "expected_violation_found": any("PLAN_DIGEST_DRIFT" in v or "PLAN_SNAPSHOT_MUTATED" in v for v in res["violations"])}
        _run_pair("C1-A14", "MUTATED_PLAN_WITH_RECOMPUTED_DIGEST_REJECTED", "capture_r5_public_batch", "Mutated plan with recomputed public digest rejected (0 calls)", "Unmutated bound plan succeeds offline", "PRED_CAPTURE_ZERO_DISPATCH_VIOLATION", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_a14, _pos_capture_clean)

        def _neg_a15() -> Dict[str, Any]:
            d, p = _mk_scope_plan()
            sp = _SpyOfflineC1()
            res = asyncio.run(capture_r5_public_batch(p, sp, mon, run_id="WRONG_RUN", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return {**_obs_cap(res, len(sp.calls)), "expected_violation_found": any("RUN_ID" in v for v in res["violations"])}
        _run_pair("C1-A15", "CROSS_RUN_PLAN_REUSE_REJECTED", "capture_r5_public_batch", "Cross-run run_id mismatch rejected with 0 calls", "Matching run_id executes cleanly", "PRED_CAPTURE_ZERO_DISPATCH_VIOLATION", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_a15, _pos_capture_clean)

        def _neg_a16() -> Dict[str, Any]:
            d, p = _mk_scope_plan()
            sp = _SpyOfflineC1()
            res = asyncio.run(capture_r5_public_batch(p, sp, mon, run_id="c1_f48", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC + timedelta(seconds=300)))
            return {**_obs_cap(res, len(sp.calls)), "expected_violation_found": any("OPERATOR_GRANT_EXPIRED" in v for v in res["violations"])}
        _run_pair("C1-A16", "EXPIRED_GRANT_BETWEEN_PLAN_AND_DISPATCH_BLOCKED", "capture_r5_public_batch", "Grant expired before dispatch yields 0 calls", "Unexpired grant executes cleanly", "PRED_CAPTURE_ZERO_DISPATCH_VIOLATION", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_a16, _pos_capture_clean)

        def _neg_a17() -> Dict[str, Any]:
            d, p = _mk_scope_plan(eps=("public_ticker", "contracts"))
            sp = _SpyOfflineC1(on_call=lambda idx, *_: p["batches"][1].__setitem__("url", "https://api.bitget.com/api/v2/mix/order/place-order") if idx == 0 else None)
            res = asyncio.run(capture_r5_public_batch(p, sp, mon, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
            return {**_obs_cap(res, len(sp.calls)), "expected_reason_found": bool(res["aborted"]) and len(sp.calls) == 1}
        _run_pair("C1-A17", "POST_ENQUEUE_PLAN_MUTATION_CAUGHT", "capture_r5_public_batch", "Mutating plan batch 2 during batch 1 aborts before batch 2", "Unmutated 2-batch plan completes cleanly", "PRED_CAPTURE_ONE_CALL_ABORT_ZERO_ACCEPT", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_a17, lambda: _pos_capture_clean(eps=("public_ticker", "contracts")))

        def _neg_a18() -> Dict[str, Any]:
            d, p = _mk_scope_plan()
            res = asyncio.run(capture_r5_public_batch(p, StdlibUrllibPublicTransportC2(), mon, run_id="c1_f48", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return {**_obs_cap(res, 0), "expected_violation_found": any("QA_FIXTURE_MODE_MUST_NOT_USE_LIVE_SOCKET_TRANSPORT" in v for v in res["violations"])}
        _run_pair("C1-A18", "QA_PLAN_CANNOT_USE_LIVE_STDLIB_TRANSPORT", "capture_r5_public_batch", "QA plan paired with live StdlibUrllibPublicTransportC2 rejected (0 sockets)", "QA plan paired with offline spy succeeds", "PRED_CAPTURE_ZERO_DISPATCH_VIOLATION", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_a18, _pos_capture_clean)

        # --- P1-B: 15 unique paired deadline/cancel/retry/budget/drain controls (C1-B01..C1-B15) ---
        def _neg_b01() -> Dict[str, Any]:
            d, p = _mk_scope_plan()
            clk = {"m": 100.0, "u": NOW_UTC}
            mc = make_r5_fixture_resource_monitor(destination=td, monotonic_fn=lambda: clk["m"])
            sp = _SpyOfflineC1([RawHttpResponseC2(429, b"", {"retry-after": "0.1"}, NOW_MS, NOW_MS + 5)])
            res = asyncio.run(capture_r5_public_batch(p, sp, mc, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda _s: clk.__setitem__("m", clk["m"] + 25.0), monotonic_fn=lambda: clk["m"], utc_now_fn=lambda: clk["u"]))
            return {**_obs_cap(res, len(sp.calls)), "expected_reason_found": res["abort_reason"] == "CAPTURE_DEADLINE_EXCEEDED"}
        _run_pair("C1-B01", "POST_WAIT_DEADLINE_EXCEEDED_ZERO_EXTRA_CALL", "capture_r5_public_batch", "Deadline exceeded during retry sleep stops with 0 extra call", "Clean capture within deadline passes", "PRED_CAPTURE_ONE_CALL_ABORT_ZERO_ACCEPT", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_b01, _pos_capture_clean)

        def _neg_b02() -> Dict[str, Any]:
            d, p = _mk_scope_plan()
            clk = {"m": 200.0}; ev = asyncio.Event()
            mc = make_r5_fixture_resource_monitor(destination=td, monotonic_fn=lambda: clk["m"])
            sp = _SpyOfflineC1([RawHttpResponseC2(429, b"", {"retry-after": "0.1"}, NOW_MS, NOW_MS + 5)])
            res = asyncio.run(capture_r5_public_batch(p, sp, mc, cancel_event=ev, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda s: (clk.__setitem__("m", clk["m"] + s), ev.set()), monotonic_fn=lambda: clk["m"], utc_now_fn=lambda: NOW_UTC))
            return {**_obs_cap(res, len(sp.calls)), "expected_reason_found": res["abort_reason"] == "CANCELLED_MIDFLIGHT"}
        _run_pair("C1-B02", "CANCEL_DURING_WAIT_STOPS_BEFORE_DISPATCH", "capture_r5_public_batch", "Cancel set during retry wait prevents 2nd dispatch", "Clean uncancelled capture passes", "PRED_CAPTURE_ONE_CALL_ABORT_ZERO_ACCEPT", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_b02, _pos_capture_clean)

        def _neg_b03() -> Dict[str, Any]:
            d, p = _mk_scope_plan()
            clk = {"m": 300.0, "u": NOW_UTC}
            mc = make_r5_fixture_resource_monitor(destination=td, monotonic_fn=lambda: clk["m"])
            sp = _SpyOfflineC1([RawHttpResponseC2(429, b"", {"retry-after": "0.1"}, NOW_MS, NOW_MS + 5)])
            res = asyncio.run(capture_r5_public_batch(p, sp, mc, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda s: (clk.__setitem__("m", clk["m"] + s), clk.__setitem__("u", NOW_UTC + timedelta(seconds=200))), monotonic_fn=lambda: clk["m"], utc_now_fn=lambda: clk["u"]))
            return {**_obs_cap(res, len(sp.calls)), "expected_reason_found": res["abort_reason"] == "OPERATOR_GRANT_EXPIRED"}
        _run_pair("C1-B03", "EXPIRY_DURING_RETRY_WAIT_STOPS_DISPATCH", "capture_r5_public_batch", "Grant expires during retry wait blocking 2nd dispatch", "Unexpired capture completes cleanly", "PRED_CAPTURE_ONE_CALL_ABORT_ZERO_ACCEPT", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_b03, _pos_capture_clean)

        def _neg_b04() -> Dict[str, Any]:
            d, p = _mk_scope_plan(max_duration_sec=3, expires_utc_ms=NOW_MS + 2500)
            sp = _SpyOfflineC1()
            res = asyncio.run(capture_r5_public_batch(p, sp, mon, run_id="c1_f48", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC, transport_timeout_ceiling_sec=0.0))
            return _obs_cap(res, len(sp.calls))

        def _pos_b04() -> Dict[str, Any]:
            d, p = _mk_scope_plan(max_duration_sec=3, expires_utc_ms=NOW_MS + 2500)
            sp = _SpyOfflineC1()
            res = asyncio.run(capture_r5_public_batch(p, sp, mon, run_id="c1_f48", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC, transport_timeout_ceiling_sec=10.0))
            return {**_obs_cap(res, len(sp.calls)), "first_timeout_sec": float(sp.calls[0]["timeout_sec"]) if sp.calls else 999.0, "expected_max_timeout_sec": 2.5}
        _run_pair("C1-B04", "TIMEOUT_BOUNDED_BY_REMAINING_GRANT_AND_SESSION", "capture_r5_public_batch", "Non-positive timeout ceiling rejected (0 calls)", "Effective timeout clamped to min(ceiling, session, grant<=2.5s)", "PRED_CAPTURE_INVALID_CLOCK_FAIL", "PRED_CAPTURE_TIMEOUT_BOUNDED_CLEAN", _neg_b04, _pos_b04)

        def _neg_b05() -> Dict[str, Any]:
            d, p = _mk_scope_plan()
            sl: List[float] = []
            sp = _SpyOfflineC1([RawHttpResponseC2(429, b"", {"retry-after": "120"}, NOW_MS, NOW_MS + 5)])
            res = asyncio.run(capture_r5_public_batch(p, sp, mon, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda s: sl.append(s), utc_now_fn=lambda: NOW_UTC))
            return {**_obs_cap(res, len(sp.calls)), "sleeps_recorded": sl}
        _run_pair("C1-B05", "RETRY_AFTER_EXCEEDS_REMAINING_NEVER_SHORTENED", "capture_r5_public_batch", "Retry-After=120s > remaining 20s aborts without shortening", "Clean capture without excessive Retry-After passes", "PRED_CAPTURE_RETRY_AFTER_BLOCKED", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_b05, _pos_capture_clean)

        def _neg_b06() -> Dict[str, Any]:
            d, p = _mk_scope_plan()
            sp = _SpyOfflineC1([RawHttpResponseC2(429, b"", {"retry-after": "not_a_number"}, NOW_MS, NOW_MS + 5)])
            res = asyncio.run(capture_r5_public_batch(p, sp, mon, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
            return {**_obs_cap(res, len(sp.calls)), "expected_reason_found": "INVALID_RETRY_AFTER" in str(res["abort_reason"])}

        def _pos_b06() -> Dict[str, Any]:
            d, p = _mk_scope_plan()
            sl: List[float] = []; clk = {"m": 400.0}
            mc = make_r5_fixture_resource_monitor(destination=td, monotonic_fn=lambda: clk["m"])
            sp = _SpyOfflineC1([RawHttpResponseC2(429, b"", {"retry-after": "3.0"}, NOW_MS, NOW_MS + 5), RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)])
            res = asyncio.run(capture_r5_public_batch(p, sp, mc, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda s: (sl.append(s), clk.__setitem__("m", clk["m"] + s)), monotonic_fn=lambda: clk["m"], utc_now_fn=lambda: NOW_UTC))
            return {**_obs_cap(res, len(sp.calls)), "sleeps_recorded": sl}
        _run_pair("C1-B06", "RETRY_AFTER_3S_RESPECTED_NOT_CAPPED_2S", "capture_r5_public_batch", "Unparseable Retry-After header aborts cleanly", "Retry-After=3.0s honored in full (not capped to 2.0s) and succeeds", "PRED_CAPTURE_ONE_CALL_ABORT_ZERO_ACCEPT", "PRED_CAPTURE_RETRY_AFTER_HONORED", _neg_b06, _pos_b06)

        _run_pair(
            "C1-B07", "RETRY_AFTER_HTTP_DATE_AND_INVALID_STRICT", "_parse_retry_after_strict",
            "NaN Retry-After string rejected", "Valid RFC7231 HTTP-date parsed to 5.0s",
            "PRED_RETRY_AFTER_PARSE_REJECTED", "PRED_RETRY_AFTER_PARSE_VALID",
            lambda: (lambda ok, sec, err: {"parse_ok": ok, "parsed_sec": sec, "error": err or ""})(*_parse_retry_after_strict("NaN", NOW_UTC)),
            lambda: (lambda ok, sec, err: {"parse_ok": ok, "parsed_sec": float(sec or 0.0), "expected_sec": 5.0, "error": err or ""})(*_parse_retry_after_strict("Wed, 25 Nov 2026 12:00:05 GMT", NOW_UTC)),
        )

        def _neg_b08() -> Dict[str, Any]:
            d, p = _mk_scope_plan()
            sp = _SpyOfflineC1([RawHttpResponseC2(403, b"denied", {}, NOW_MS, NOW_MS + 5), RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)])
            res = asyncio.run(capture_r5_public_batch(p, sp, mon, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
            return _obs_cap(res, len(sp.calls))
        _run_pair("C1-B08", "NON_RETRYABLE_403_NEVER_RETRIED", "capture_r5_public_batch", "HTTP 403 stops after 1 call with 0 retries", "HTTP 200 succeeds cleanly", "PRED_CAPTURE_NON_RETRYABLE_SINGLE_CALL", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_b08, _pos_capture_clean)

        def _neg_b09() -> Dict[str, Any]:
            d, p = _mk_scope_plan(eps=("public_ticker", "contracts"), max_requests=2, max_concurrency=2)
            sp = _SpyOfflineC1([RawHttpResponseC2(429, b"", {"retry-after": "0.01"}, NOW_MS, NOW_MS + 5), RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5), RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)])
            res = asyncio.run(capture_r5_public_batch(p, sp, mon, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
            return {**_obs_cap(res, len(sp.calls)), "max_requests_cap": 2}
        _run_pair("C1-B09", "ATOMIC_REQUEST_CAP_NO_OVERSUBSCRIBE", "capture_r5_public_batch", "2-endpoint + 429 retry under max_requests=2 never exceeds 2 calls", "2-endpoint clean 200 within max_requests=2 passes", "PRED_CAPTURE_REQUEST_CAP_ENFORCED", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_b09, lambda: _pos_capture_clean(eps=("public_ticker", "contracts"), max_requests=2, max_concurrency=2))

        def _neg_b10() -> Dict[str, Any]:
            d, p = _mk_scope_plan(max_total_bytes=90, max_response_bytes=80)
            sp = _SpyOfflineC1([RawHttpResponseC2(429, b"E" * 50, {"retry-after": "0.01"}, NOW_MS, NOW_MS + 5), RawHttpResponseC2(200, b"X" * 50, {}, NOW_MS, NOW_MS + 5)])
            res = asyncio.run(capture_r5_public_batch(p, sp, mon, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
            return {**_obs_cap(res, len(sp.calls)), "expected_wire_bytes": 100}
        _run_pair("C1-B10", "WIRE_BYTES_INCLUDE_ERROR_BODIES_AND_SEPARATE_ACCEPTED", "capture_r5_public_batch", "50B 429 + 50B 200 exceeds 90B cap -> wire=100, accepted=0", "Clean body within byte cap passes", "PRED_CAPTURE_WIRE_BYTES_SEPARATED_ABORT", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_b10, _pos_capture_clean)

        def _neg_b11() -> Dict[str, Any]:
            d, p = _mk_scope_plan()
            ev = asyncio.Event()
            sp = _SpyOfflineC1(on_call=lambda *_: ev.set())
            res = asyncio.run(capture_r5_public_batch(p, sp, mon, cancel_event=ev, staging_root=td / "st11", run_id="c1_f48", trusted_scope=d, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return _obs_cap(res, len(sp.calls))
        _run_pair("C1-B11", "MIDFLIGHT_LATE_RESPONSE_DISCARDED_AND_DRAINED", "capture_r5_public_batch", "Midflight cancel discards late response & drains owned worker", "Uncancelled capture completes & drains cleanly", "PRED_CAPTURE_MIDFLIGHT_CANCEL_DRAINED", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_b11, _pos_capture_clean)

        def _neg_b12() -> Dict[str, Any]:
            d, p = _mk_scope_plan()
            sp = _SpyOfflineC1()
            res = asyncio.run(capture_r5_public_batch(p, sp, mon, run_id="c1_f48", trusted_scope=d, destination_root=td, monotonic_fn=lambda: float("nan"), utc_now_fn=lambda: NOW_UTC))
            return _obs_cap(res, len(sp.calls))
        _run_pair("C1-B12", "INVALID_MONOTONIC_CLOCK_BLOCKED", "capture_r5_public_batch", "NaN monotonic clock rejected with 0 dispatches", "Valid finite monotonic clock succeeds", "PRED_CAPTURE_INVALID_CLOCK_FAIL", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_b12, _pos_capture_clean)

        def _neg_b13() -> Dict[str, Any]:
            class _PolVer:
                verifier_id = "POLICY_TEST_STUB"
                def verify_owner_attestation(self, g: R5OperatorGrant, eff: Mapping[str, Any], nu: datetime, tc: Mapping[str, Any]) -> R5AttestationVerificationResult:
                    return R5AttestationVerificationResult(True, "VERIFIED_SEPARATE_OWNER_ATTESTATION", "TEST", self.verifier_id, g.grant_id, NOW_MS)
            lg, lr = _mk_live()
            dl = validate_r5_operator_capture_scope(lg, lr, NOW_UTC, {}, attestation_verifier=_PolVer())
            pl = plan_r5_capture_batches(dl, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 5, "max_requests": 5, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)
            res = asyncio.run(capture_r5_public_batch(pl, StdlibUrllibPublicTransportC2(), mon, run_id="c1_f48", trusted_scope=dl, destination_root=td, utc_now_fn=lambda: NOW_UTC))
            return _obs_cap(res, 0)
        _run_pair("C1-B13", "LEGACY_C4_TRANSPORT_UNSUPPORTED_WIRE_CAP_BLOCKED", "capture_r5_public_batch", "Legacy C4 4MiB transport > 50KB budget blocked with 0 sockets", "Offline QA bounded capture succeeds cleanly", "PRED_CAPTURE_LIVE_READ_CAP_BLOCKED", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_b13, _pos_capture_clean)

        _run_pair(
            "C1-B14", "PLAN_PAGES_SEMANTICS_TRUTHFUL_SINGLE_PAGE", "plan_r5_capture_batches",
            "Invalid max_pages=0 rejected by planner", "Valid plan emits SINGLE_REQUEST_PER_BATCH_NO_PAGINATION and max_pages=1",
            "PRED_PLAN_BLOCKED", "PRED_PLAN_ADMITTED_QA",
            lambda: (lambda d, _p: _obs_plan(plan_r5_capture_batches(d, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 0, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)))(*_mk_scope_plan()),
            _pos_plan_clean,
        )

        def _neg_b15() -> Dict[str, Any]:
            d, p = _mk_scope_plan(max_duration_sec=1, max_response_bytes=20_000, max_total_bytes=50_000)
            clk = {"m": 500.0}
            mc = make_r5_fixture_resource_monitor(destination=td, monotonic_fn=lambda: clk["m"])
            sp = _SpyOfflineC1([TimeoutError("to")])
            res = asyncio.run(capture_r5_public_batch(p, sp, mc, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda _s: clk.__setitem__("m", clk["m"] + 5.0), monotonic_fn=lambda: clk["m"], utc_now_fn=lambda: NOW_UTC))
            return {**_obs_cap(res, len(sp.calls)), "expected_reason_found": res["abort_reason"] == "CAPTURE_DEADLINE_EXCEEDED"}

        def _pos_b15() -> Dict[str, Any]:
            # max_response_bytes < max_total_bytes so conservative unknown-byte retention on attempt 1 leaves room for retry 2!
            d, p = _mk_scope_plan(max_response_bytes=20_000, max_total_bytes=50_000)
            clk = {"m": 500.0}
            mc = make_r5_fixture_resource_monitor(destination=td, monotonic_fn=lambda: clk["m"])
            sp = _SpyOfflineC1([TimeoutError("to"), RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)])
            res = asyncio.run(capture_r5_public_batch(p, sp, mc, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda s: clk.__setitem__("m", clk["m"] + s), monotonic_fn=lambda: clk["m"], utc_now_fn=lambda: NOW_UTC))
            ts = list(res["dispatch_timestamps_mono"])
            return {**_obs_cap(res, len(sp.calls)), "dispatch_count": len(ts), "dispatch_gap_sec": (ts[1] - ts[0]) if len(ts) >= 2 else -1.0}
        _run_pair("C1-B15", "DISPATCH_CADENCE_MONOTONIC_AFTER_RETRY", "capture_r5_public_batch", "Timeout retry past deadline aborts before 2nd dispatch", "Timeout retry within deadline preserves monotonic cadence >=0.05s", "PRED_CAPTURE_ONE_CALL_ABORT_ZERO_ACCEPT", "PRED_CAPTURE_CADENCE_MONOTONIC", _neg_b15, _pos_b15)

        # --- P1-C & Adjacent Custody/Abort: 15 unique paired controls (C1-C01..C1-C15) ---
        now_m = 600.0
        vol = _canonical_volume_identity_c1(td)
        wvol = "Z:\\" if os.name == "nt" else "dev:888888888888"
        good_s = {"disk_free_gib": 25.0, "available_ram_mib": 1024.0, "sampled_monotonic": now_m, "destination_volume": vol, "monitor_source": "QA_MON", "sample_ok": True}

        for fid, nm, desc_bad, bad_m in (
            ("C1-C01", "MONITOR_NONE_FAIL_CLOSED", "resource_monitor=None fails closed (0 dispatches)", None),
            ("C1-C02", "MONITOR_EMPTY_DICT_FAIL_CLOSED", "resource_monitor returns {} fails closed", lambda: {}),
            ("C1-C03", "MISSING_DISK_OR_RAM_KEY_BLOCKED", "resource_monitor missing disk_free_gib key", lambda: {k: v for k, v in good_s.items() if k != "disk_free_gib"}),
            ("C1-C04", "NAN_DISK_FAIL_CLOSED", "resource_monitor NaN disk_free_gib", lambda: {**good_s, "disk_free_gib": float("nan")}),
            ("C1-C05", "INF_RAM_FAIL_CLOSED", "resource_monitor Inf available_ram_mib", lambda: {**good_s, "available_ram_mib": float("inf")}),
            ("C1-C06", "BOOL_OR_STR_VALUE_BLOCKED", "resource_monitor bool disk_free_gib=True", lambda: {**good_s, "disk_free_gib": True}),
            ("C1-C07", "STALE_RESOURCE_SAMPLE_BLOCKED", "resource_monitor stale timestamp (-3.0s)", lambda: {**good_s, "sampled_monotonic": now_m - 3.0}),
            ("C1-C08", "FUTURE_RESOURCE_TIMESTAMP_BLOCKED", "resource_monitor future timestamp (+2.0s)", lambda: {**good_s, "sampled_monotonic": now_m + 2.0}),
            ("C1-C09", "WRONG_VOLUME_IDENTITY_BLOCKED", "resource_monitor wrong destination_volume", lambda: {**good_s, "destination_volume": wvol}),
            ("C1-C10", "MONITOR_EXCEPTION_FAIL_CLOSED", "resource_monitor raises RuntimeError", lambda: (_ for _ in ()).throw(RuntimeError("probe_err"))),
            ("C1-C11", "SAMPLE_OK_FALSE_OR_STR_BLOCKED", "resource_monitor sample_ok='true' string", lambda: {**good_s, "sample_ok": "true"}),
        ):
            def _mk_neg_c(bm: Any) -> Any:
                def _run_neg_c() -> Dict[str, Any]:
                    d, p = _mk_scope_plan()
                    sp = _SpyOfflineC1()
                    rc = asyncio.run(capture_r5_public_batch(p, sp, bm, run_id="c1_f48", trusted_scope=d, destination_root=td, monotonic_fn=lambda: now_m, utc_now_fn=lambda: NOW_UTC))
                    return _obs_cap(rc, len(sp.calls))
                return _run_neg_c

            def _run_pos_c() -> Dict[str, Any]:
                d, p = _mk_scope_plan()
                sp = _SpyOfflineC1()
                rc = asyncio.run(capture_r5_public_batch(p, sp, lambda: dict(good_s), run_id="c1_f48", trusted_scope=d, destination_root=td, monotonic_fn=lambda: now_m, utc_now_fn=lambda: NOW_UTC))
                return _obs_cap(rc, len(sp.calls))

            _run_pair(fid, nm, "capture_r5_public_batch", desc_bad, "Fresh valid resource sample executes capture cleanly", "PRED_CAPTURE_ZERO_DISPATCH_ABORT", "PRED_CAPTURE_CLEAN_QA_PASS", _mk_neg_c(bad_m), _run_pos_c)

        def _neg_c12() -> Dict[str, Any]:
            st_ab = td / f"st_abort_{uuid.uuid4().hex[:6]}"
            st_ab.mkdir(parents=True, exist_ok=True)
            keep_f = st_ab / "unrelated.txt"; keep_f.write_text("KEEP", encoding="utf-8")
            ev_ab = asyncio.Event()
            d, p = _mk_scope_plan(eps=("public_ticker", "contracts"))
            sp = _SpyOfflineC1(on_call=lambda idx, *_: ev_ab.set() if idx == 1 else None)
            r_ab = asyncio.run(capture_r5_public_batch(p, sp, make_r5_fixture_resource_monitor(destination=st_ab), cancel_event=ev_ab, staging_root=st_ab, run_id="c1_f48", trusted_scope=d, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
            return {**_obs_cap(r_ab, len(sp.calls)), "unrelated_kept": keep_f.is_file() and keep_f.read_text("utf-8") == "KEEP"}
        _run_pair("C1-C12", "ABORT_DELETED_STAGING_COUNTED_ZERO_ACCEPTED_OWNED_ONLY", "capture_r5_public_batch", "Midflight abort removes only owned staged file, preserves unrelated file, 0 accepted", "Clean 2-endpoint capture without abort succeeds", "PRED_CAPTURE_ABORT_OWNED_CLEANUP_ONLY", "PRED_CAPTURE_CLEAN_QA_PASS", _neg_c12, lambda: _pos_capture_clean(eps=("public_ticker", "contracts")))

        def _pos_custody_commit_valid() -> Dict[str, Any]:
            sub = td / f"cust_pos_{uuid.uuid4().hex[:6]}"
            st = sub / "st"; st.mkdir(parents=True, exist_ok=True)
            j = R5DevCustodyJournal(sub / "j", os_disk_probe_fn=lambda: 28.0)
            part = {"valid": True, "partition_role": "QA_FIXTURE", "plan_digest_sha256": "a" * 64}
            sp_m = st / "ok.raw"; sp_m.write_bytes(OK_BODY)
            sha = hashlib.sha256(OK_BODY).hexdigest()
            rc = persist_r5_verified_dev_snapshot(sp_m, {"raw_sha256": sha, "evidence_class": "QA_FIXTURE", "capture_proof_id": f"p_{uuid.uuid4().hex[:6]}"}, {"valid": True, "canonical_normalized_sha256": sha}, part, j, expected_staging_root=st)
            vc = verify_r5_custody_chain_readonly(sub / "j", j.head_state(), None, ())
            return {"committed": bool(rc["committed"]), "status": str(rc["status"]), "chain_valid": bool(vc["valid"])}

        def _neg_c13() -> Dict[str, Any]:
            sub = td / f"cust_n13_{uuid.uuid4().hex[:6]}"
            st = sub / "st"; st.mkdir(parents=True, exist_ok=True)
            j = R5DevCustodyJournal(sub / "j", os_disk_probe_fn=lambda: 28.0)
            part = {"valid": True, "partition_role": "QA_FIXTURE", "plan_digest_sha256": "a" * 64}
            sp_m = st / "m.raw"; sp_m.write_bytes(OK_BODY)
            rc = persist_r5_verified_dev_snapshot(sp_m, {"evidence_class": "QA_FIXTURE", "capture_proof_id": "pm"}, {"valid": True, "canonical_normalized_sha256": "b" * 64}, part, j, expected_staging_root=st)
            return {"committed": bool(rc["committed"]), "status": str(rc["status"])}
        _run_pair("C1-C13", "CUSTODY_MANDATORY_EXPECTED_RAW_SHA256", "R5DevCustodyJournal.persist_snapshot", "Missing raw_sha256 in capture proof rejected", "Valid raw_sha256 committed and chain valid", "PRED_CUSTODY_REJECTED_MISSING_SHA", "PRED_CUSTODY_COMMITTED_AND_CHAIN_VALID", _neg_c13, _pos_custody_commit_valid)

        def _neg_c14() -> Dict[str, Any]:
            sub = td / f"cust_n14_{uuid.uuid4().hex[:6]}"
            st = sub / "st"; st.mkdir(parents=True, exist_ok=True)
            j = R5DevCustodyJournal(sub / "j", os_disk_probe_fn=lambda: 28.0)
            part = {"valid": True, "partition_role": "QA_FIXTURE", "plan_digest_sha256": "a" * 64}
            sp_big = st / "big.raw"
            with sp_big.open("wb") as fh:
                fh.seek(MAX_RAW_HTTP_BODY_BYTES_C3 + 256)
                fh.write(b"\x00")
            try:
                rc = persist_r5_verified_dev_snapshot(sp_big, {"raw_sha256": "a" * 64, "evidence_class": "QA_FIXTURE", "capture_proof_id": "pb"}, {"valid": True, "canonical_normalized_sha256": "b" * 64}, part, j, expected_staging_root=st)
            finally:
                if sp_big.exists():
                    sp_big.unlink()
            return {"committed": bool(rc["committed"]), "oversize_violation_present": any("RAW_BLOB_OVERSIZE_BEFORE_READ" in v for v in rc["violations"])}
        _run_pair("C1-C14", "CUSTODY_STAT_BEFORE_READ_OVERSIZE_REJECTED", "R5DevCustodyJournal.persist_snapshot", "Oversized staged file >4MiB rejected via stat before read", "Bounded staged file committed and chain valid", "PRED_CUSTODY_REJECTED_OVERSIZE", "PRED_CUSTODY_COMMITTED_AND_CHAIN_VALID", _neg_c14, _pos_custody_commit_valid)

        def _neg_c15() -> Dict[str, Any]:
            sub = td / f"cust_n15_{uuid.uuid4().hex[:6]}"
            st = sub / "st"; st.mkdir(parents=True, exist_ok=True)
            j = R5DevCustodyJournal(sub / "j", os_disk_probe_fn=lambda: 28.0)
            part = {"valid": True, "partition_role": "QA_FIXTURE", "plan_digest_sha256": "a" * 64}
            sp_m = st / "tamper.raw"; sp_m.write_bytes(OK_BODY)
            sha = hashlib.sha256(OK_BODY).hexdigest()
            rc = persist_r5_verified_dev_snapshot(sp_m, {"raw_sha256": sha, "evidence_class": "QA_FIXTURE", "capture_proof_id": "ptamp"}, {"valid": True, "canonical_normalized_sha256": sha}, part, j, expected_staging_root=st)
            assert rc["committed"] is True
            bp = (sub / "j") / rc["blob_rel_path"]
            bp.write_bytes(OK_BODY + b"TAMPERED")
            vc = verify_r5_custody_chain_readonly(sub / "j", j.head_state(), None, ())
            return {"chain_valid": bool(vc["valid"]), "violations_count": len(vc.get("violations") or ())}
        _run_pair("C1-C15", "CUSTODY_VALID_BOUNDED_COMMIT_AND_CHAIN_READONLY", "verify_r5_custody_chain_readonly", "Tampered committed raw blob detected by readonly custody verifier", "Untampered bounded commit verified valid readonly", "PRED_CUSTODY_TAMPER_DETECTED", "PRED_CUSTODY_COMMITTED_AND_CHAIN_VALID", _neg_c15, _pos_custody_commit_valid)

    return out


def test_c1_all_48_new_fault_controls_pass_red_green() -> None:
    ctrls = run_new_kh03_r5_c1_fault_controls(executable_sha="a" * 40, run_id="unit_c1_48")
    assert len(ctrls) == 48
    assert len({c["fault_id"] for c in ctrls}) == 48
    verd = derive_fault_control_verdict(ctrls, expected_executable_sha="a" * 40, expected_run_id="unit_c1_48")
    assert verd["valid"] is True, f"Paired C1 fault verdict failed: {verd['violations']}"
    assert verd["passed_controls"] == 48 and verd["failed_controls"] == 0
    assert verd["counts_by_kind"]["BASELINE_BUG_RED"] == 8
    assert verd["counts_by_kind"]["NEGATIVE_CONTRACT"] == 40
    assert verd["unique_positive_execution_ids_count"] == 48
    # AST audit confirms zero `_rec(..., True)` literal aggregator shortcuts remain!
    ast_au = audit_fault_certification_ast_for_literal_flags(Path(__file__).read_text("utf-8"))
    assert ast_au["clean_no_literal_aggregator_flags"] is True


def test_c1_cli_and_env_disk_waiver_rejected_exit_2() -> None:
    runner_path = APP_ROOT / "tools" / "run_kh_03_r5_c1_acceptance.py"
    if not runner_path.is_file():
        pytest.skip("Runner not yet created")
    p_cli = subprocess.run([sys.executable, str(runner_path), "--owner-waive-disk-preflight"], cwd=str(APP_ROOT), capture_output=True, text=True, timeout=30)
    assert p_cli.returncode == 2 and "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER" in p_cli.stderr
    env = os.environ.copy()
    env["KH03_R5_C1_OWNER_WAIVE_DISK_PREFLIGHT"] = "1"
    p_env = subprocess.run([sys.executable, str(runner_path)], cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=30)
    assert p_env.returncode == 2 and "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER" in p_env.stderr
