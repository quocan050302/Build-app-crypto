"""
aurum-desk/backend/tests/test_kh03_r6_a1_corrective_custody_l.py

AURUM DESK — KH-03.R6.A1 POST-REVIEW CORRECTIVE CLOSURE | GROUP L (L01..L12)
P1-L: CUSTODY SINGLE-WRITER, DETERMINISM, ATOMICITY, CROSS-PROCESS LOCK,
BOUNDED ANTI-STARVATION QUEUE, CRASH INTENT RECOVERY & W1/W2/W4 CUSTODY BENCHMARK
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import errno
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from services.kh_03_r6_a1_corrective_service import (
    R6A1BoundedOrderedCustodyQueue,
    R6A1CrossProcessCustodyLock,
    R6A1CustodyChunkEnvelope,
    R6A1CustodyWriterAuthority,
    R6A1DiskBudgetLedger,
    _volume_identity_for_path_corr,
    build_r6_a1_custody_envelope,
    compute_chunk_conservative_peak_disk_bytes,
    derive_corrective_fault_verdict,
    execute_corrective_fault_pair,
    run_r6_a1_single_custody_writer,
)
from services.kh_03_r6_a1_trust_service import (
    authorize_r6_a1_dispatch_request,
    complete_caps_vector_a1,
)
from services.kh_03_r6_bounded_transport_service import (
    build_r6_bound_capture_plan,
    collect_real_r6_resource_sample,
    supervise_r6_session,
)
from services.kh_03_r6_custody_readiness_service import (
    R6CustodyJournal,
    mint_r6_raw_provenance_receipt,
    recover_r6_custody_intents,
    seal_r6_dev_dataset_manifest,
    verify_r6_custody_readonly,
)
import services.kh_03_r6_custody_readiness_service as crs_mod
from services.kh_03_r6_durable_grant_service import (
    QA_ISOLATED_TRUST_NAMESPACE_R6,
    load_r6_owner_trust_root,
    open_r6_durable_ledger,
    provision_qa_isolated_trust_root,
    provision_r6_durable_ledger,
    sign_r6_grant_payload_with_ed25519,
    verify_r6_owner_grant,
)
from services.kh_03_r6_source_contract_service import (
    API_FAMILY_CLASSIC_V2,
    APPROVED_PRODUCT_TYPE_V2_R6,
    APPROVED_SYMBOL_R6,
    APPROVED_VENUE_R6,
    MANDATORY_BRANCH_KH03_R6,
    build_r6_source_contract_registry,
)
from tests.test_kh03_r6_source_contracts import make_valid_instrument_raw

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"


class _CustodyLHarness:
    """Self-contained isolated QA loopback harness for minting genuine provenance receipts."""

    def __init__(self, root: Path, *, executable_sha: str = "a" * 40, run_id: str = "l_test") -> None:
        self.root = root
        self.dest = root / "dest"
        self.dest.mkdir(parents=True, exist_ok=True)
        self.fake_repo = root / "fake_repo"
        self.fake_repo.mkdir(parents=True, exist_ok=True)
        self.store_root = root / "ledger"
        self.store_id = f"qa_l_{abs(hash(str(root))) % 100000}"
        self.now = datetime(2026, 11, 26, 6, 0, 0, tzinfo=timezone.utc)
        self.now_ms = int(self.now.timestamp() * 1000)
        self.executable_sha = executable_sha
        self.run_id = run_id
        self.reg = build_r6_source_contract_registry()
        self.ep_c = self.reg.endpoints["classic_v2_contracts"]
        tr_p, self.priv_k, _ = provision_qa_isolated_trust_root(root / "trust", store_id=self.store_id)
        self.tr_qa = load_r6_owner_trust_root(tr_p, expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)["trust_root"]
        provision_r6_durable_ledger(self.store_root, store_id=self.store_id, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=self.now)
        self.ledger = open_r6_durable_ledger(
            {"ledger_store_root": str(self.store_root), "store_id": self.store_id},
            self.store_id,
            expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6,
            actual_now=self.now,
        )["ledger"]
        self.raw_meta = json.dumps({"code": "00000", "msg": "success", "data": [make_valid_instrument_raw()]}, separators=(",", ":")).encode("utf-8")
        parent = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *a): return

            def do_GET(self):
                body = parent.raw_meta
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.port = int(self._srv.server_address[1])
        self._th = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._th.start()
        self._seq = 0
        self._lock = threading.Lock()

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()
        self._th.join(timeout=2.0)

    def make_session_lease(self, gid: str, *, req_cap: int = 80, pay_cap: int = 2_000_000, disk_cap: int = 4_000_000) -> Tuple[Any, Any, Any]:
        gp = {
            "grant_id": gid,
            "issuer": "QA_ISOLATED_HARNESS",
            "trust_namespace": QA_ISOLATED_TRUST_NAMESPACE_R6,
            "issued_utc_ms": self.now_ms - 1000,
            "expires_utc_ms": self.now_ms + 600_000,
            "repo": "quocan050302/Build-app-crypto",
            "branch": MANDATORY_BRANCH_KH03_R6,
            "tested_executable_sha": self.executable_sha,
            "release_manifest_sha256": "b" * 64,
            "purpose": "QA_FIXTURE",
            "stage": "QA_LOOPBACK",
            "venue": APPROVED_VENUE_R6,
            "api_family": API_FAMILY_CLASSIC_V2,
            "symbol": APPROVED_SYMBOL_R6,
            "product_type": APPROVED_PRODUCT_TYPE_V2_R6,
            "allowed_endpoints": ["classic_v2_contracts", "classic_v2_history_candles", "classic_v2_history_fund_rate"],
            "allowed_ws_channels": ["books1"],
            "window_start_ms": 1794000000000,
            "window_end_ms": 1794003600000,
            "destination_store_id": self.store_id,
            "destination_root": str(self.dest),
            "max_session_duration_sec": 300,
            "max_concurrency": 4,
            "caps": {
                "connections": 4,
                "requests": req_cap,
                "retries": 10,
                "subscriptions": 2,
                "messages": 500,
                "payload_bytes": pay_cap,
                "decoded_bytes": pay_cap * 2,
                "pages": 20,
                "disk_bytes": disk_cap,
                "records": 5000,
            },
            "retention_policy": {"max_retention_days": 7, "git_commit_raw_payloads": False},
        }
        vg = verify_r6_owner_grant(
            sign_r6_grant_payload_with_ed25519(gp, self.priv_k),
            self.tr_qa,
            self.now,
            expected_executable_sha=self.executable_sha,
            expected_release_manifest_sha256="b" * 64,
            expected_store_id=self.store_id,
            expected_stage="QA_LOOPBACK",
        )["verified_grant"]
        pl = build_r6_bound_capture_plan(vg, self.reg, {}, {"ok": True}, plan_id=f"pl_{gid}")["plan"]
        lease = self.ledger.claim_r6_grant_session_atomic(vg, pl, f"sess_{gid}", actual_now=self.now)["lease"]
        return vg, pl, lease

    def mint_real_provenance(self, vg: Any, pl: Any, lease: Any, *, attempt_id: Optional[str] = None, run_id: Optional[str] = None) -> Tuple[bytes, Any, str]:
        with self._lock:
            self._seq += 1
            aid = attempt_id or f"att_l_{self._seq}"
        rid = run_id or self.run_id
        per_pay = min(4096, max(1024, int(pl.caps["payload_bytes"]) // 2))
        per_dec = min(8192, max(2048, int(pl.caps["decoded_bytes"]) // 2))
        caps = complete_caps_vector_a1({"requests": 1, "payload_bytes": per_pay, "decoded_bytes": per_dec})
        auth = authorize_r6_a1_dispatch_request(
            plan=pl,
            verified_grant=vg,
            lease=lease,
            registry=self.reg,
            endpoint_id="classic_v2_contracts",
            attempt_id=aid,
            partition="QA_FIXTURE",
            requested_caps=caps,
            deadline_monotonic=time.monotonic() + 20.0,
            params={"productType": APPROVED_PRODUCT_TYPE_V2_R6, "symbol": APPROVED_SYMBOL_R6},
            qa_loopback_mode=True,
            qa_loopback_url=f"http://127.0.0.1:{self.port}/api/v2/mix/market/contracts",
            actual_now=self.now,
        )["authorized_request"]
        sup = supervise_r6_session(
            ledger=self.ledger,
            lease=lease,
            plan=pl,
            verified_grant=vg,
            validated_request={"kind": "rest_get", "url": auth.dispatch_url, "params": dict(auth.params)},
            attempt_id=aid,
            request_type="rest_get",
            reserved_caps=caps,
            resource_sample_fn=lambda: collect_real_r6_resource_sample({"dest": self.dest}, active_workers=1),
            expected_volumes={"dest": self.dest},
            session_deadline_monotonic=time.monotonic() + 20.0,
            cancel_event=threading.Event(),
            utc_now_fn=lambda: self.now,
            qa_loopback_mode=True,
            allow_fixture_resource_sample=True,
            registry=self.reg,
            authorized_request=auth,
            run_id=rid,
            partition="QA_FIXTURE",
            endpoint_id="classic_v2_contracts",
            contract_digest=self.ep_c.content_digest,
        )
        assert sup["ok"] is True, f"MINT_SUP_FAIL:{sup}"
        prov = mint_r6_raw_provenance_receipt(
            tested_executable_sha=self.executable_sha,
            run_id=rid,
            session_id=lease.session_id,
            lease_id=lease.lease_id,
            attempt_id=aid,
            endpoint_id="classic_v2_contracts",
            contract_digest=self.ep_c.content_digest,
            partition="QA_FIXTURE",
            qa_loopback_mode=True,
            transport_kind="REST_GET",
            supervisor_result=sup["trusted_result"],
            raw_payload_bytes_data=sup["raw_payload_bytes"],
            settlement_receipt=sup["settlement_receipt"],
        )
        assert prov.accepted_for_custody is True
        return sup["raw_payload_bytes"], prov, aid


def _run_ordered_pipeline_for_workload(
    *,
    h: _CustodyLHarness,
    vg: Any,
    pl: Any,
    lease: Any,
    run_id: str,
    workers: int,
    num_tasks: int,
    chunks_per_task: int,
    delay_schedule_sec: Mapping[int, float],
    queue_max_items: int = 8,
    queue_max_bytes: int = 262_144,
    writer_delay_sec: float = 0.0,
) -> Dict[str, Any]:
    """Runs real parallel producers -> R6A1BoundedOrderedCustodyQueue -> SINGLE custody writer -> R6CustodyJournal."""
    vol_id = _volume_identity_for_path_corr(h.dest)
    dsk_root = h.root / f"dsk_{run_id}"
    dsk_led = R6A1DiskBudgetLedger(store_root=dsk_root, volume_id=vol_id, global_cap_bytes=4_000_000, safe_floor_bytes=0)
    dsk_led.register_session_budget(session_id=lease.session_id, session_cap_bytes=4_000_000, emergency_audit_reserve_bytes=4096, manifest_and_wal_reserve_bytes=8192)
    jnl = R6CustodyJournal(
        approved_root=h.dest,
        partition="QA_FIXTURE",
        run_id=run_id,
        repo_root=h.fake_repo,
        max_chunk_bytes=65_536,
        max_total_bytes=2_000_000,
        strict_authority_required=True,
        session_id=lease.session_id,
    )
    wa = jnl.acquire_writer_authority(session_id=lease.session_id, cancel_epoch=0, deadline_monotonic=time.monotonic() + 60.0, disk_budget_ledger=dsk_led)
    assert wa["acquired"] is True
    auth = wa["authority"]
    q = R6A1BoundedOrderedCustodyQueue(ordered_task_ordinals=list(range(num_tasks)), max_items=queue_max_items, max_bytes=queue_max_bytes)
    cancel_ev = threading.Event()
    writer_box: List[Dict[str, Any]] = []

    def _w_hook(_env: Any) -> None:
        if writer_delay_sec > 0.0:
            time.sleep(writer_delay_sec)

    def _writer_fn() -> None:
        writer_box.append(
            run_r6_a1_single_custody_writer(
                queue=q,
                custody=jnl,
                writer_authority=auth,
                disk_budget_ledger=dsk_led,
                deadline_monotonic=time.monotonic() + 60.0,
                cancel_event=cancel_ev,
                get_cancel_epoch=lambda: 0,
                per_commit_hook=_w_hook if writer_delay_sec > 0 else None,
            )
        )

    wth = threading.Thread(target=_writer_fn, daemon=False)
    wth.start()

    # Pre-mint provenance receipts so transport noise doesn't mask pure custody/queue timing
    pre_items: List[Tuple[int, int, bytes, Any, str]] = []
    for t_ord in range(num_tasks):
        for c_ord in range(chunks_per_task):
            raw_b, prov, aid = h.mint_real_provenance(vg, pl, lease, run_id=run_id)
            pre_items.append((t_ord, c_ord, raw_b, prov, aid))

    by_task: Dict[int, List[Tuple[int, bytes, Any, str]]] = {t: [] for t in range(num_tasks)}
    for t_ord, c_ord, raw_b, prov, aid in pre_items:
        by_task[t_ord].append((c_ord, raw_b, prov, aid))

    t0 = time.perf_counter()

    def _produce_task(t_ord: int) -> bool:
        try:
            d_s = float(delay_schedule_sec.get(t_ord, 0.0))
            if d_s > 0.0:
                time.sleep(d_s)
            for c_ord, raw_b, prov, aid in by_task[t_ord]:
                cid = f"t{t_ord:02d}_c{c_ord:02d}"
                rows = [{"task": t_ord, "chunk": c_ord, "symbol": APPROVED_SYMBOL_R6}]
                norm_len = len(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8"))
                pk = compute_chunk_conservative_peak_disk_bytes(raw_bytes_len=len(raw_b), norm_bytes_len=norm_len)
                rid_dsk = f"r_{run_id}_{cid}"
                r_res = dsk_led.reserve_r6_disk_bytes(session_id=lease.session_id, reservation_id=rid_dsk, write_class="chunk_persist", requested_bytes=pk["conservative_peak_bytes"])
                assert r_res["reserved"] is True
                env_res = build_r6_a1_custody_envelope(
                    session_id=lease.session_id,
                    run_id=run_id,
                    partition="QA_FIXTURE",
                    task_ordinal=t_ord,
                    task_id=f"task_{t_ord:02d}",
                    chunk_ordinal=c_ord,
                    chunk_id=cid,
                    attempt_id=aid,
                    plan_digest=pl.plan_digest,
                    raw_payload=raw_b,
                    normalized_rows=rows,
                    provenance_receipt=prov,
                    disk_reservation_id=rid_dsk,
                    cancel_epoch=0,
                )
                assert env_res["valid"] is True
                enq = q.put_envelope(env_res["envelope"], deadline_monotonic=time.monotonic() + 30.0, cancel_event=cancel_ev)
                assert enq["enqueued"] is True, f"ENQ_FAIL:{enq}"
            return True
        finally:
            q.mark_task_complete(t_ord)

    if workers <= 1:
        for t_ord in range(num_tasks):
            _produce_task(t_ord)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_produce_task, range(num_tasks)))

    q.close_intake()
    wth.join(timeout=30.0)
    elapsed = round(time.perf_counter() - t0, 4)
    jnl.release_writer_authority(auth)
    jnl.close()
    dsk_led.reconcile_r6_disk_actual(session_id=lease.session_id, reservation_id=f"fixed_overhead_{lease.session_id}", actual_committed_bytes=4096, uncertain_bytes=0)

    ro = verify_r6_custody_readonly(jnl.store_dir, expected_partition="QA_FIXTURE", expected_run_id=run_id, ledger_store_root=h.store_root)
    disk_raw_sum = sum(p.stat().st_size for p in (jnl.store_dir / "chunks").glob("*.raw.bin"))
    disk_norm_sum = sum(p.stat().st_size for p in (jnl.store_dir / "chunks").glob("*.norm.json"))
    return {
        "ok": bool(writer_box and writer_box[0]["ok"] and ro["valid"]),
        "wall_sec": elapsed,
        "workers": workers,
        "writer_report": writer_box[0] if writer_box else None,
        "queue_stats": q.snapshot_stats(),
        "readonly_audit": ro,
        "semantic_chain_digest": ro["semantic_chain_digest"],
        "committed_chunks": ro["committed_chunks"],
        "total_raw_bytes": ro["total_raw_bytes"],
        "total_norm_bytes": ro["total_norm_bytes"],
        "disk_raw_sum": disk_raw_sum,
        "disk_norm_sum": disk_norm_sum,
        "disk_budget_snapshot": dsk_led.snapshot_r6_disk_budget(lease.session_id),
        "store_dir": str(jnl.store_dir),
    }


def run_kh03_r6_a1_corrective_w1_w2_w4_custody_benchmark(*, num_tasks: int = 8, chunks_per_task: int = 1) -> Dict[str, Any]:
    """
    M4 real W1 / W2 / W4 / W1_repeat deterministic custody benchmark with shuffled completion order
    (cold vs warm separated) through the REAL bounded ordered queue + single custody writer + disk ledger.
    """
    runs: List[Dict[str, Any]] = []
    # Reversed completion delays so higher ordinals complete before lower ordinals under W>1!
    delays = {t: round((num_tasks - 1 - t) * 0.004, 4) for t in range(num_tasks)}
    with tempfile.TemporaryDirectory(prefix="a1_corr_wbench_") as td:
        h = _CustodyLHarness(Path(td), run_id="custody_wbench")
        try:
            for idx, (label, w) in enumerate((("w=1", 1), ("w=2", 2), ("w=4", 4), ("w=1_repeat", 1))):
                vg, pl, lease = h.make_session_lease(f"WBENCH_{idx}_{w}", req_cap=60)
                rid = f"wbench_run_{idx}_{w}"
                out = _run_ordered_pipeline_for_workload(
                    h=h, vg=vg, pl=pl, lease=lease, run_id=rid, workers=w,
                    num_tasks=num_tasks, chunks_per_task=chunks_per_task,
                    delay_schedule_sec=delays, queue_max_items=6, queue_max_bytes=262_144,
                )
                h.ledger.finalize_r6_session(lease, quarantine=False, reason="WBENCH_DONE", actual_now=h.now)
                runs.append({
                    "mode_label": label,
                    "workers": w,
                    "cache_state": "cold" if idx == 0 else "warm",
                    "wall_sec": out["wall_sec"],
                    "ok": out["ok"],
                    "committed_chunks": out["committed_chunks"],
                    "total_raw_bytes": out["total_raw_bytes"],
                    "total_norm_bytes": out["total_norm_bytes"],
                    "bytes_conserved": (out["total_raw_bytes"] == out["disk_raw_sum"] and out["total_norm_bytes"] == out["disk_norm_sum"]),
                    "semantic_result_sha256": out["semantic_chain_digest"],
                    "queue_peak_items": out["queue_stats"]["peak_items"],
                    "queue_peak_bytes": out["queue_stats"]["peak_bytes"],
                })
        finally:
            h.close()

    shas = {r["semantic_result_sha256"] for r in runs}
    all_ok = all(r["ok"] and r["bytes_conserved"] for r in runs)
    all_id = (len(shas) == 1) and bool(next(iter(shas)))
    return {
        "valid": bool(all_ok and all_id and len(runs) == 4),
        "all_4_identical": bool(all_id),
        "canonical_semantic_sha256": next(iter(shas)) if len(shas) == 1 else "",
        "num_tasks": num_tasks,
        "chunks_per_task": chunks_per_task,
        "runs": runs,
    }


def run_kh03_r6_a1_corrective_controls_l12(*, executable_sha: str, run_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Executes ALL 12 P1-L paired controls (`L01..L12`) with real barriers, threads & OS subprocesses."""
    receipts: List[Dict[str, Any]] = []
    traces: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="a1_corr_l12_") as td:
        h = _CustodyLHarness(Path(td), executable_sha=executable_sha, run_id=run_id)
        try:
            # L01: 2 workers hitting Barrier(2) same observed head -> unique seq=[1,2], exact prev_hash!
            vg1, pl1, l1 = h.make_session_lease("L01_G", req_cap=20)
            def _l01_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="l01_neg", repo_root=h.fake_repo, strict_authority_required=True, session_id=l1.session_id)
                r1, p1, _ = h.mint_real_provenance(vg1, pl1, l1, run_id="l01_neg")
                bad_res = j.persist_r6_verified_chunk(chunk_id="chk_unauth", raw_payload=r1, normalized_rows=[{"a": 1}], provenance_receipt=p1, writer_authority=None)
                j.close()
                return {"strict_authority": True, "authority": None}, {
                    "negative_enforced": (bad_res["persisted"] is False and bad_res["status"] == "REJECTED_MISSING_OR_INVALID_WRITER_AUTHORITY"),
                    "violations_or_guards_triggered": len(bad_res["violations"]),
                    "status": bad_res["status"],
                }

            def _l01_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="l01_pos", repo_root=h.fake_repo, session_id=l1.session_id)
                r1, p1, _ = h.mint_real_provenance(vg1, pl1, l1, run_id="l01_pos")
                r2, p2, _ = h.mint_real_provenance(vg1, pl1, l1, run_id="l01_pos")
                bar = threading.Barrier(2, timeout=5.0)
                outs: Dict[str, Dict[str, Any]] = {}
                def _w(tag: str, cid: str, rb: bytes, pr: Any) -> None:
                    bar.wait()
                    outs[tag] = j.persist_r6_verified_chunk(chunk_id=cid, raw_payload=rb, normalized_rows=[{"w": tag}], provenance_receipt=pr)
                t1 = threading.Thread(target=_w, args=("t1", "chk_1", r1, p1))
                t2 = threading.Thread(target=_w, args=("t2", "chk_2", r2, p2))
                t1.start(); t2.start(); t1.join(timeout=10.0); t2.join(timeout=10.0)
                j.close()
                seqs = sorted([outs["t1"]["seq"], outs["t2"]["seq"]])
                ro = verify_r6_custody_readonly(j.store_dir, expected_partition="QA_FIXTURE", expected_run_id="l01_pos")
                traces.append({"control": "L01", "seqs": seqs, "readonly_valid": ro["valid"], "tip_hash": ro["tip_hash"]})
                summary["concurrent_writers_serialized_unique_seq"] = (seqs == [1, 2] and ro["valid"])
                return {"workers": 2, "barrier": True}, {
                    "positive_verified": (seqs == [1, 2] and ro["valid"] is True and ro["committed_chunks"] == 2),
                    "unexpected_violations": len(ro["violations"]),
                    "seqs": seqs,
                }
            receipts.append(execute_corrective_fault_pair(control_id="L01", group="L", fault_kind="BASELINE_BUG_RED", target_symbol="R6CustodyJournal.persist_r6_verified_chunk", description="2 workers Barrier(2) same head serialized with unique seq & prev_hash", negative_fn=_l01_neg, positive_fn=_l01_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l1, quarantine=False, reason="L01_DONE", actual_now=h.now)

            # L02: W1/W2/W4 shuffled completions -> identical logical order, semantic_chain_digest & byte totals!
            vg2, pl2, l2 = h.make_session_lease("L02_G", req_cap=40)
            def _l02_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                q_bad = R6A1BoundedOrderedCustodyQueue(ordered_task_ordinals=[0, 1], max_items=4, max_bytes=65536)
                r1, p1, a1 = h.mint_real_provenance(vg2, pl2, l2, run_id="l02_neg")
                env_b = build_r6_a1_custody_envelope(session_id=l2.session_id, run_id="l02_neg", partition="QA_FIXTURE", task_ordinal=99, task_id="t99", chunk_ordinal=0, chunk_id="c99", attempt_id=a1, plan_digest=pl2.plan_digest, raw_payload=r1, normalized_rows=[{"k": 1}], provenance_receipt=p1)
                enq = q_bad.put_envelope(env_b["envelope"], deadline_monotonic=time.monotonic() + 5.0)
                return {"unregistered_task_ordinal": 99}, {
                    "negative_enforced": (enq["enqueued"] is False and enq["status"] == "REJECTED_UNREGISTERED_TASK_ORDINAL"),
                    "violations_or_guards_triggered": len(enq["violations"]),
                }
            def _l02_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                delays = {0: 0.03, 1: 0.02, 2: 0.01, 3: 0.0}
                res_w1 = _run_ordered_pipeline_for_workload(h=h, vg=vg2, pl=pl2, lease=l2, run_id="l02_w1", workers=1, num_tasks=4, chunks_per_task=1, delay_schedule_sec=delays)
                res_w2 = _run_ordered_pipeline_for_workload(h=h, vg=vg2, pl=pl2, lease=l2, run_id="l02_w2", workers=2, num_tasks=4, chunks_per_task=1, delay_schedule_sec=delays)
                res_w4 = _run_ordered_pipeline_for_workload(h=h, vg=vg2, pl=pl2, lease=l2, run_id="l02_w4", workers=4, num_tasks=4, chunks_per_task=1, delay_schedule_sec=delays)
                digs = {res_w1["semantic_chain_digest"], res_w2["semantic_chain_digest"], res_w4["semantic_chain_digest"]}
                bytes_eq = (
                    res_w1["total_raw_bytes"] == res_w2["total_raw_bytes"] == res_w4["total_raw_bytes"]
                    and res_w1["total_norm_bytes"] == res_w2["total_norm_bytes"] == res_w4["total_norm_bytes"]
                )
                ok_all = res_w1["ok"] and res_w2["ok"] and res_w4["ok"] and len(digs) == 1 and bytes_eq
                summary["w1_w2_w4_shuffled_order_identical_semantic_digest"] = ok_all
                summary["canonical_custody_semantic_sha256"] = res_w1["semantic_chain_digest"]
                traces.append({"control": "L02", "w1_sha": res_w1["semantic_chain_digest"], "w2_sha": res_w2["semantic_chain_digest"], "w4_sha": res_w4["semantic_chain_digest"]})
                return {"workers_tested": [1, 2, 4]}, {
                    "positive_verified": ok_all,
                    "unexpected_violations": 0 if ok_all else 1,
                    "semantic_digest": res_w1["semantic_chain_digest"],
                }
            receipts.append(execute_corrective_fault_pair(control_id="L02", group="L", fault_kind="INTEGRATION_FAULT", target_symbol="R6A1BoundedOrderedCustodyQueue", description="W1/W2/W4 shuffled completions produce identical semantic digest & byte totals", negative_fn=_l02_neg, positive_fn=_l02_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l2, quarantine=False, reason="L02_DONE", actual_now=h.now)

            # L03: 4+ concurrent workers same chunk_id / conflicting duplicate -> at most 1 commit!
            vg3, pl3, l3 = h.make_session_lease("L03_G", req_cap=20)
            def _l03_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="l03_neg", repo_root=h.fake_repo, session_id=l3.session_id)
                items = [h.mint_real_provenance(vg3, pl3, l3, run_id="l03_neg") for _ in range(4)]
                bar = threading.Barrier(4, timeout=5.0)
                outs: List[Dict[str, Any]] = []
                olock = threading.Lock()
                def _w(idx: int) -> None:
                    rb, pr, _ = items[idx]
                    bar.wait()
                    r = j.persist_r6_verified_chunk(chunk_id="same_chk_id", raw_payload=rb, normalized_rows=[{"conflict_val": idx}], provenance_receipt=pr)
                    with olock:
                        outs.append(r)
                ths = [threading.Thread(target=_w, args=(i,)) for i in range(4)]
                for t in ths: t.start()
                for t in ths: t.join(timeout=10.0)
                j.close()
                pers_cnt = sum(1 for o in outs if o.get("persisted") is True)
                rej_cnt = sum(1 for o in outs if o.get("persisted") is False)
                ro = verify_r6_custody_readonly(j.store_dir, expected_partition="QA_FIXTURE", expected_run_id="l03_neg")
                return {"workers": 4, "same_chunk_id": "same_chk_id"}, {
                    "negative_enforced": (pers_cnt == 1 and rej_cnt == 3 and ro["valid"] and ro["committed_chunks"] == 1),
                    "violations_or_guards_triggered": rej_cnt,
                    "committed_count": pers_cnt,
                }
            def _l03_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="l03_pos", repo_root=h.fake_repo, session_id=l3.session_id)
                items = [h.mint_real_provenance(vg3, pl3, l3, run_id="l03_pos") for _ in range(4)]
                bar = threading.Barrier(4, timeout=5.0)
                outs: List[Dict[str, Any]] = []
                olock = threading.Lock()
                def _w(idx: int) -> None:
                    rb, pr, _ = items[idx]
                    bar.wait()
                    r = j.persist_r6_verified_chunk(chunk_id=f"uniq_chk_{idx}", raw_payload=rb, normalized_rows=[{"idx": idx}], provenance_receipt=pr)
                    with olock:
                        outs.append(r)
                ths = [threading.Thread(target=_w, args=(i,)) for i in range(4)]
                for t in ths: t.start()
                for t in ths: t.join(timeout=10.0)
                j.close()
                ro = verify_r6_custody_readonly(j.store_dir, expected_partition="QA_FIXTURE", expected_run_id="l03_pos")
                return {"workers": 4, "distinct_chunks": 4}, {
                    "positive_verified": (all(o.get("persisted") is True for o in outs) and ro["valid"] and ro["committed_chunks"] == 4),
                    "unexpected_violations": len(ro["violations"]),
                }
            receipts.append(execute_corrective_fault_pair(control_id="L03", group="L", fault_kind="INTEGRATION_FAULT", target_symbol="R6CustodyJournal.persist_r6_verified_chunk", description="4+ concurrent workers conflicting duplicate chunk -> at most 1 commit", negative_fn=_l03_neg, positive_fn=_l03_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l3, quarantine=False, reason="L03_DONE", actual_now=h.now)

            # L04: Two real OS processes same custody store -> exactly 1 owner, live lock NEVER stolen!
            def _l04_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                sdir = h.dest / "qa_fixture" / "l04_cross_proc"
                sdir.mkdir(parents=True, exist_ok=True)
                lck1 = R6A1CrossProcessCustodyLock(sdir, session_id="s_proc1", authority_id="auth_proc1", partition="QA_FIXTURE")
                a1 = lck1.acquire()
                assert a1["acquired"] is True
                env = dict(os.environ)
                env["PYTHONPATH"] = f"{BACKEND_ROOT}{os.pathsep}{APP_ROOT}"
                code = (
                    "import json, sys; from pathlib import Path; "
                    "from services.kh_03_r6_a1_corrective_service import R6A1CrossProcessCustodyLock; "
                    "lk = R6A1CrossProcessCustodyLock(Path(sys.argv[1]), session_id='s_proc2', authority_id='auth_proc2', partition='QA_FIXTURE', timeout_sec=0.1); "
                    "print(json.dumps(lk.acquire()))"
                )
                try:
                    p = subprocess.run([sys.executable, "-c", code, str(sdir)], env=env, capture_output=True, text=True, timeout=20)
                    res2 = json.loads(p.stdout)
                    meta_still_ours = json.loads((sdir / "writer.lock.json").read_text("utf-8"))["authority_id"] == "auth_proc1"
                finally:
                    lck1.release()
                summary["cross_process_single_writer_enforced"] = (res2["acquired"] is False and meta_still_ours)
                traces.append({"control": "L04", "proc2_status": res2["status"], "live_lock_preserved": meta_still_ours})
                return {"store_dir": str(sdir), "two_os_processes": True}, {
                    "negative_enforced": (res2["acquired"] is False and meta_still_ours),
                    "violations_or_guards_triggered": len(res2.get("violations") or ()),
                    "proc2_status": res2["status"],
                }
            def _l04_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                sdir = h.dest / "qa_fixture" / "l04_cross_proc"
                env = dict(os.environ)
                env["PYTHONPATH"] = f"{BACKEND_ROOT}{os.pathsep}{APP_ROOT}"
                code = (
                    "import json, sys; from pathlib import Path; "
                    "from services.kh_03_r6_a1_corrective_service import R6A1CrossProcessCustodyLock; "
                    "lk = R6A1CrossProcessCustodyLock(Path(sys.argv[1]), session_id='s_proc2', authority_id='auth_proc2', partition='QA_FIXTURE', timeout_sec=0.2); "
                    "r = lk.acquire(); lk.release(); print(json.dumps(r))"
                )
                p = subprocess.run([sys.executable, "-c", code, str(sdir)], env=env, capture_output=True, text=True, timeout=20)
                res2 = json.loads(p.stdout)
                return {"after_proc1_release": True}, {
                    "positive_verified": (res2["acquired"] is True and not (sdir / "writer.lock.json").exists()),
                    "unexpected_violations": len(res2.get("violations") or ()),
                }
            receipts.append(execute_corrective_fault_pair(control_id="L04", group="L", fault_kind="INTEGRATION_FAULT", target_symbol="R6A1CrossProcessCustodyLock.acquire", description="Two OS processes same custody store -> 1 writer owner, live lock never stolen", negative_fn=_l04_neg, positive_fn=_l04_pos, executable_sha=executable_sha, run_id=run_id))

            # L05: Queue item + byte cap with slow writer & head anti-starvation -> no deadlock/starvation!
            vg5, pl5, l5 = h.make_session_lease("L05_G", req_cap=20)
            def _l05_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                q_sm = R6A1BoundedOrderedCustodyQueue(ordered_task_ordinals=[0], max_items=2, max_bytes=1024)
                r1, p1, a1 = h.mint_real_provenance(vg5, pl5, l5, run_id="l05_neg")
                env_big = build_r6_a1_custody_envelope(session_id=l5.session_id, run_id="l05_neg", partition="QA_FIXTURE", task_ordinal=0, task_id="t0", chunk_ordinal=0, chunk_id="big", attempt_id=a1, plan_digest=pl5.plan_digest, raw_payload=r1, normalized_rows=[{"pad": "Z" * 1200}], provenance_receipt=p1)
                enq = q_sm.put_envelope(env_big["envelope"], deadline_monotonic=time.monotonic() + 2.0)
                return {"queue_max_bytes": 1024, "envelope_bytes": env_big["envelope"].total_queued_bytes}, {
                    "negative_enforced": (enq["enqueued"] is False and enq["status"] == "REJECTED_ENVELOPE_EXCEEDS_QUEUE_BYTE_CAP"),
                    "violations_or_guards_triggered": len(enq["violations"]),
                }
            def _l05_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                # Non-head tasks (1, 2, 3) arrive immediately while head (0) arrives after 25ms with slow writer (25ms/item) and max_items=2!
                out = _run_ordered_pipeline_for_workload(
                    h=h, vg=vg5, pl=pl5, lease=l5, run_id="l05_pos", workers=4,
                    num_tasks=4, chunks_per_task=1, delay_schedule_sec={0: 0.025, 1: 0.0, 2: 0.0, 3: 0.0},
                    queue_max_items=2, queue_max_bytes=16_384, writer_delay_sec=0.02,
                )
                qs = out["queue_stats"]
                ok = out["ok"] and qs["peak_items"] <= 2 and qs["peak_bytes"] <= 16_384 and qs["backpressure_waits"] >= 1 and out["committed_chunks"] == 4
                summary["bounded_queue_item_and_byte_backpressure_verified"] = ok
                summary["head_slot_anti_starvation_verified"] = ok
                return {"max_items": 2, "max_bytes": 16384}, {
                    "positive_verified": ok,
                    "unexpected_violations": 0 if ok else 1,
                    "backpressure_waits": qs["backpressure_waits"],
                    "peak_items": qs["peak_items"],
                }
            receipts.append(execute_corrective_fault_pair(control_id="L05", group="L", fault_kind="INTEGRATION_FAULT", target_symbol="R6A1BoundedOrderedCustodyQueue.put_envelope", description="Queue item+byte cap with slow writer & head anti-starvation prevents deadlock", negative_fn=_l05_neg, positive_fn=_l05_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l5, quarantine=False, reason="L05_DONE", actual_now=h.now)

            # L06: Producer failure while queue full -> cancel, drain/reap without deadlock, zero late commit!
            vg6, pl6, l6 = h.make_session_lease("L06_G", req_cap=20)
            def _l06_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="l06_neg", repo_root=h.fake_repo, strict_authority_required=True, session_id=l6.session_id)
                auth = j.acquire_writer_authority(session_id=l6.session_id, cancel_epoch=0, deadline_monotonic=time.monotonic() + 20.0)["authority"]
                q = R6A1BoundedOrderedCustodyQueue(ordered_task_ordinals=[0, 1, 2], max_items=2, max_bytes=32_768)
                cancel_ev = threading.Event()
                epoch_box = [0]
                r1, p1, a1 = h.mint_real_provenance(vg6, pl6, l6, run_id="l06_neg")
                r2, p2, a2 = h.mint_real_provenance(vg6, pl6, l6, run_id="l06_neg")
                env1 = build_r6_a1_custody_envelope(session_id=l6.session_id, run_id="l06_neg", partition="QA_FIXTURE", task_ordinal=1, task_id="t1", chunk_ordinal=0, chunk_id="c1", attempt_id=a1, plan_digest=pl6.plan_digest, raw_payload=r1, normalized_rows=[{"k": 1}], provenance_receipt=p1)["envelope"]
                env2 = build_r6_a1_custody_envelope(session_id=l6.session_id, run_id="l06_neg", partition="QA_FIXTURE", task_ordinal=2, task_id="t2", chunk_ordinal=0, chunk_id="c2", attempt_id=a2, plan_digest=pl6.plan_digest, raw_payload=r2, normalized_rows=[{"k": 2}], provenance_receipt=p2)["envelope"]
                # Fill non-head slot (1 of 2), block second producer waiting on full queue!
                assert q.put_envelope(env1, deadline_monotonic=time.monotonic() + 5.0)["enqueued"] is True
                blocked_res: List[Dict[str, Any]] = []
                def _blocked_prod() -> None:
                    blocked_res.append(q.put_envelope(env2, deadline_monotonic=time.monotonic() + 5.0, cancel_event=cancel_ev))
                pt = threading.Thread(target=_blocked_prod)
                wbox: List[Dict[str, Any]] = []
                wt = threading.Thread(target=lambda: wbox.append(run_r6_a1_single_custody_writer(queue=q, custody=j, writer_authority=auth, disk_budget_ledger=None, deadline_monotonic=time.monotonic() + 5.0, cancel_event=cancel_ev, get_cancel_epoch=lambda: epoch_box[0])))
                pt.start(); wt.start()
                time.sleep(0.05)
                # Producer 0 fails -> bump epoch, set cancel, abort queue!
                epoch_box[0] += 1
                cancel_ev.set()
                q.abort("PRODUCER_0_FAILED")
                pt.join(timeout=3.0); wt.join(timeout=3.0)
                # Late commit attempt with stale epoch must be denied!
                late = j.commit_r6_custody_transaction(envelope=env1, writer_authority=auth, current_cancel_epoch=epoch_box[0])
                j.close()
                ok = (not pt.is_alive()) and (not wt.is_alive()) and blocked_res[0]["enqueued"] is False and late["persisted"] is False and len(wbox[0]["committed_chunks"]) == 0
                summary["producer_failure_queue_full_no_deadlock_no_late_commit"] = ok
                return {"queue_full_on_producer_fail": True}, {
                    "negative_enforced": ok,
                    "violations_or_guards_triggered": len(late["violations"]) + (1 if not blocked_res[0]["enqueued"] else 0),
                }
            def _l06_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                out = _run_ordered_pipeline_for_workload(h=h, vg=vg6, pl=pl6, lease=l6, run_id="l06_pos", workers=2, num_tasks=2, chunks_per_task=1, delay_schedule_sec={})
                return {"clean_drain": True}, {"positive_verified": out["ok"] and out["committed_chunks"] == 2, "unexpected_violations": 0 if out["ok"] else 1}
            receipts.append(execute_corrective_fault_pair(control_id="L06", group="L", fault_kind="INTEGRATION_FAULT", target_symbol="R6A1BoundedOrderedCustodyQueue.abort", description="Producer failure while queue full aborts cleanly with zero deadlock and zero late commit", negative_fn=_l06_neg, positive_fn=_l06_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l6, quarantine=False, reason="L06_DONE", actual_now=h.now)

            # L07: Writer failure mid-commit -> durable intent recovery/quarantine, NO false seal!
            vg7, pl7, l7 = h.make_session_lease("L07_G", req_cap=20)
            def _l07_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="l07_neg", repo_root=h.fake_repo, strict_authority_required=True, session_id=l7.session_id)
                auth = j.acquire_writer_authority(session_id=l7.session_id, cancel_epoch=0, deadline_monotonic=time.monotonic() + 20.0)["authority"]
                r1, p1, a1 = h.mint_real_provenance(vg7, pl7, l7, run_id="l07_neg")
                env1 = build_r6_a1_custody_envelope(session_id=l7.session_id, run_id="l07_neg", partition="QA_FIXTURE", task_ordinal=0, task_id="t0", chunk_ordinal=0, chunk_id="c_fail", attempt_id=a1, plan_digest=pl7.plan_digest, raw_payload=r1, normalized_rows=[{"k": 1}], provenance_receipt=p1)["envelope"]
                w_res = j.commit_r6_custody_transaction(envelope=env1, writer_authority=auth, inject_write_error=OSError(errno.ENOSPC, "injected_mid_commit_enospc"))
                j.close()
                rec = recover_r6_custody_intents(j.store_dir, expected_partition="QA_FIXTURE", expected_run_id="l07_neg")
                ro = verify_r6_custody_readonly(j.store_dir, expected_partition="QA_FIXTURE", expected_run_id="l07_neg")
                seal = seal_r6_dev_dataset_manifest(
                    run_id="l07_neg", stage="QA_LOOPBACK", partition="QA_FIXTURE", tested_executable_sha=executable_sha,
                    source_registry_digest=h.reg.registry_digest, instrument_metadata_versions=[make_valid_instrument_raw()],
                    window_start_ms=1794000000000, window_end_ms=1794003600000, custody_audit=ro, gap_ledger={"gap_count": 0},
                    raw_observed_count=1, normalized_valid_count=1, rejected_or_quarantined_count=0, persisted_accepted_count=0,
                    provenance_classes=["QA_LOOPBACK"], retention_policy={"max_retention_days": 7, "git_commit_raw_payloads": False},
                    local_store_redacted_id="qa:l07", quarantined_artifacts_count=len(rec["quarantined_intents"]),
                )
                return {"inject_write_error": "ENOSPC"}, {
                    "negative_enforced": (w_res["persisted"] is False and w_res["terminal_status"] == "QUARANTINED" and seal["sealed"] is False and ro["valid"] is False),
                    "violations_or_guards_triggered": len(w_res["violations"]) + len(seal["violations"]),
                }
            def _l07_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                out = _run_ordered_pipeline_for_workload(h=h, vg=vg7, pl=pl7, lease=l7, run_id="l07_pos", workers=1, num_tasks=1, chunks_per_task=1, delay_schedule_sec={})
                return {"clean_commit": True}, {"positive_verified": out["ok"], "unexpected_violations": 0 if out["ok"] else 1}
            receipts.append(execute_corrective_fault_pair(control_id="L07", group="L", fault_kind="INTEGRATION_FAULT", target_symbol="R6CustodyJournal.commit_r6_custody_transaction", description="Writer mid-commit failure quarantines intent & blocks false seal", negative_fn=_l07_neg, positive_fn=_l07_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l7, quarantine=False, reason="L07_DONE", actual_now=h.now)

            # L08: Injected crash at staging / raw_replace / norm_replace / chain_append -> restart recovery zero duplicates!
            vg8, pl8, l8 = h.make_session_lease("L08_G", req_cap=20)
            def _l08_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                # Incomplete crashes (after_staging, after_raw_replace) -> quarantined, chain stays 0; post-replace crash (after_norm_replace, after_chain_append) -> completed ONCE, 0 dupes!
                stage_results: Dict[str, Any] = {}
                for stg in ("after_staging", "after_raw_replace", "after_norm_replace", "after_chain_append"):
                    rid_s = f"l08_{stg}"
                    j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id=rid_s, repo_root=h.fake_repo, strict_authority_required=True, session_id=l8.session_id)
                    auth = j.acquire_writer_authority(session_id=l8.session_id, cancel_epoch=0, deadline_monotonic=time.monotonic() + 20.0)["authority"]
                    r1, p1, a1 = h.mint_real_provenance(vg8, pl8, l8, run_id=rid_s)
                    env1 = build_r6_a1_custody_envelope(session_id=l8.session_id, run_id=rid_s, partition="QA_FIXTURE", task_ordinal=0, task_id="t0", chunk_ordinal=0, chunk_id="chk_0", attempt_id=a1, plan_digest=pl8.plan_digest, raw_payload=r1, normalized_rows=[{"stg": stg}], provenance_receipt=p1)["envelope"]
                    cr = j.commit_r6_custody_transaction(envelope=env1, writer_authority=auth, crash_stage=stg)
                    j.close()
                    rec = recover_r6_custody_intents(j.store_dir, expected_partition="QA_FIXTURE", expected_run_id=rid_s)
                    ro = verify_r6_custody_readonly(j.store_dir, expected_partition="QA_FIXTURE", expected_run_id=rid_s)
                    stage_results[stg] = {"crash_persisted": cr["persisted"], "rec_status": rec["status"], "completed": len(rec["completed_intents"]), "quarantined": len(rec["quarantined_intents"]), "committed_chunks": ro["committed_chunks"], "ro_valid": ro["valid"]}
                ok = (
                    stage_results["after_staging"]["quarantined"] == 1 and stage_results["after_staging"]["committed_chunks"] == 0
                    and stage_results["after_raw_replace"]["quarantined"] == 1 and stage_results["after_raw_replace"]["committed_chunks"] == 0
                    and stage_results["after_norm_replace"]["completed"] == 1 and stage_results["after_norm_replace"]["committed_chunks"] == 1 and stage_results["after_norm_replace"]["ro_valid"]
                    and stage_results["after_chain_append"]["completed"] == 1 and stage_results["after_chain_append"]["committed_chunks"] == 1 and stage_results["after_chain_append"]["ro_valid"]
                )
                summary["crash_intent_restart_zero_duplicates"] = ok
                return {"crash_stages": list(stage_results.keys())}, {
                    "negative_enforced": ok,
                    "violations_or_guards_triggered": sum(1 for v in stage_results.values() if not v["crash_persisted"]),
                    "stage_results": stage_results,
                }
            def _l08_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                out = _run_ordered_pipeline_for_workload(h=h, vg=vg8, pl=pl8, lease=l8, run_id="l08_pos", workers=1, num_tasks=2, chunks_per_task=1, delay_schedule_sec={})
                return {"no_crash": True}, {"positive_verified": out["ok"] and out["committed_chunks"] == 2, "unexpected_violations": 0 if out["ok"] else 1}
            receipts.append(execute_corrective_fault_pair(control_id="L08", group="L", fault_kind="INTEGRATION_FAULT", target_symbol="recover_r6_custody_intents", description="Crash recovery across staging/raw/norm/chain boundaries with zero duplicates", negative_fn=_l08_neg, positive_fn=_l08_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l8, quarantine=False, reason="L08_DONE", actual_now=h.now)

            # L09: Concurrent seal attempt before writer done -> rejected; stable sealed snapshot afterward!
            vg9, pl9, l9 = h.make_session_lease("L09_G", req_cap=10)
            def _l09_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="l09_both", repo_root=h.fake_repo, strict_authority_required=True, session_id=l9.session_id)
                auth = j.acquire_writer_authority(session_id=l9.session_id, cancel_epoch=0, deadline_monotonic=time.monotonic() + 20.0)["authority"]
                r1, p1, a1 = h.mint_real_provenance(vg9, pl9, l9, run_id="l09_both")
                env1 = build_r6_a1_custody_envelope(session_id=l9.session_id, run_id="l09_both", partition="QA_FIXTURE", task_ordinal=0, task_id="t0", chunk_ordinal=0, chunk_id="c0", attempt_id=a1, plan_digest=pl9.plan_digest, raw_payload=r1, normalized_rows=[{"ok": 1}], provenance_receipt=p1)["envelope"]
                assert j.commit_r6_custody_transaction(envelope=env1, writer_authority=auth)["persisted"] is True
                # While writer still holds authority (writer.lock.json live!), readonly audit & seal MUST reject!
                ro_live = verify_r6_custody_readonly(j.store_dir, expected_partition="QA_FIXTURE", expected_run_id="l09_both")
                seal_live = seal_r6_dev_dataset_manifest(
                    run_id="l09_both", stage="QA_LOOPBACK", partition="QA_FIXTURE", tested_executable_sha=executable_sha,
                    source_registry_digest=h.reg.registry_digest, instrument_metadata_versions=[make_valid_instrument_raw()],
                    window_start_ms=1794000000000, window_end_ms=1794003600000, custody_audit=ro_live, gap_ledger={"gap_count": 0},
                    raw_observed_count=1, normalized_valid_count=1, rejected_or_quarantined_count=0, persisted_accepted_count=1,
                    provenance_classes=["QA_LOOPBACK"], retention_policy={"max_retention_days": 7, "git_commit_raw_payloads": False},
                    local_store_redacted_id="qa:l09", active_writer_present=True,
                )
                j.close()
                ok = (ro_live["valid"] is False and seal_live["sealed"] is False)
                summary["concurrent_seal_before_writer_done_rejected"] = ok
                return {"active_writer_holding_lock": True}, {
                    "negative_enforced": ok,
                    "violations_or_guards_triggered": len(ro_live["violations"]) + len(seal_live["violations"]),
                }
            def _l09_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                sdir = h.dest / "qa_fixture" / "l09_both"
                ro_done = verify_r6_custody_readonly(sdir, expected_partition="QA_FIXTURE", expected_run_id="l09_both")
                seal_done = seal_r6_dev_dataset_manifest(
                    run_id="l09_both", stage="QA_LOOPBACK", partition="QA_FIXTURE", tested_executable_sha=executable_sha,
                    source_registry_digest=h.reg.registry_digest, instrument_metadata_versions=[make_valid_instrument_raw()],
                    window_start_ms=1794000000000, window_end_ms=1794003600000, custody_audit=ro_done, gap_ledger={"gap_count": 0},
                    raw_observed_count=1, normalized_valid_count=1, rejected_or_quarantined_count=0, persisted_accepted_count=1,
                    provenance_classes=["QA_LOOPBACK"], retention_policy={"max_retention_days": 7, "git_commit_raw_payloads": False},
                    local_store_redacted_id="qa:l09", active_writer_present=False,
                )
                return {"after_writer_released": True}, {
                    "positive_verified": (ro_done["valid"] is True and seal_done["sealed"] is True),
                    "unexpected_violations": len(ro_done.get("violations") or ()) + len(seal_done.get("violations") or ()),
                }
            receipts.append(execute_corrective_fault_pair(control_id="L09", group="L", fault_kind="NEGATIVE_CONTRACT", target_symbol="seal_r6_dev_dataset_manifest", description="Concurrent seal attempt before writer done rejected; stable snapshot afterward", negative_fn=_l09_neg, positive_fn=_l09_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l9, quarantine=False, reason="L09_DONE", actual_now=h.now)

            # L10: Direct persist caller without registered writer authority denied; valid authority passes!
            vg10, pl10, l10 = h.make_session_lease("L10_G", req_cap=10)
            def _l10_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="l10_run", repo_root=h.fake_repo, strict_authority_required=True, session_id=l10.session_id)
                r1, p1, _ = h.mint_real_provenance(vg10, pl10, l10, run_id="l10_run")
                forged = R6A1CustodyWriterAuthority(
                    authority_id="forged", store_dir_resolved=str(j.store_dir.resolve()), volume_id=_volume_identity_for_path_corr(j.store_dir),
                    session_id=l10.session_id, run_id="l10_run", partition="QA_FIXTURE", owner_pid=os.getpid(), owner_create_time=0.0,
                    cancel_epoch_snapshot=0, deadline_monotonic=time.monotonic() + 60.0, token="forged_token_hex",
                )
                r_none = j.persist_r6_verified_chunk(chunk_id="c_none", raw_payload=r1, normalized_rows=[{"a": 1}], provenance_receipt=p1, writer_authority=None)
                r_forg = j.persist_r6_verified_chunk(chunk_id="c_forg", raw_payload=r1, normalized_rows=[{"a": 1}], provenance_receipt=p1, writer_authority=forged)
                j.close()
                ok = (r_none["persisted"] is False and r_forg["persisted"] is False)
                summary["direct_unauthorized_persist_denied"] = ok
                return {"none_and_forged_authority": True}, {
                    "negative_enforced": ok,
                    "violations_or_guards_triggered": len(r_none["violations"]) + len(r_forg["violations"]),
                }
            def _l10_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="l10_pos", repo_root=h.fake_repo, strict_authority_required=True, session_id=l10.session_id)
                auth = j.acquire_writer_authority(session_id=l10.session_id, cancel_epoch=0, deadline_monotonic=time.monotonic() + 20.0)["authority"]
                r1, p1, _ = h.mint_real_provenance(vg10, pl10, l10, run_id="l10_pos")
                res = j.persist_r6_verified_chunk(chunk_id="c_ok", raw_payload=r1, normalized_rows=[{"a": 1}], provenance_receipt=p1, writer_authority=auth)
                j.close()
                return {"registered_writer_authority": True}, {"positive_verified": res["persisted"] is True, "unexpected_violations": len(res["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="L10", group="L", fault_kind="NEGATIVE_CONTRACT", target_symbol="R6CustodyJournal.persist_r6_verified_chunk", description="Direct unauthorized or forged writer authority denied; valid authority passes", negative_fn=_l10_neg, positive_fn=_l10_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l10, quarantine=False, reason="L10_DONE", actual_now=h.now)

            # L11: W4 larger bounded workload across 3 randomized seeds -> exact byte conservation & identical semantic digest!
            vg11, pl11, l11 = h.make_session_lease("L11_G", req_cap=80)
            def _l11_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                out = _run_ordered_pipeline_for_workload(h=h, vg=vg11, pl=pl11, lease=l11, run_id="l11_tamper", workers=2, num_tasks=2, chunks_per_task=1, delay_schedule_sec={})
                # Flip 1 byte in normalized chunk -> read-only audit MUST detect!
                np = Path(out["store_dir"]) / "chunks" / "t00_c00.norm.json"
                np.write_bytes(np.read_bytes() + b" ")
                ro = verify_r6_custody_readonly(Path(out["store_dir"]), expected_partition="QA_FIXTURE", expected_run_id="l11_tamper")
                return {"tampered_1_byte": True}, {"negative_enforced": ro["valid"] is False, "violations_or_guards_triggered": len(ro["violations"])}
            def _l11_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                digs: List[str] = []
                conserved_all = True
                for seed in (101, 202, 303):
                    rng = random.Random(seed)
                    dly = {t: round(rng.uniform(0.0, 0.015), 4) for t in range(8)}
                    out = _run_ordered_pipeline_for_workload(h=h, vg=vg11, pl=pl11, lease=l11, run_id=f"l11_s{seed}", workers=4, num_tasks=8, chunks_per_task=2, delay_schedule_sec=dly, queue_max_items=6)
                    digs.append(out["semantic_chain_digest"])
                    conserved_all = conserved_all and out["ok"] and (out["total_raw_bytes"] == out["disk_raw_sum"]) and (out["total_norm_bytes"] == out["disk_norm_sum"]) and (out["committed_chunks"] == 16)
                ok = conserved_all and len(set(digs)) == 1
                summary["w4_repeated_seeds_byte_conserved"] = ok
                return {"seeds": [101, 202, 303], "tasks": 8, "chunks_per_task": 2}, {
                    "positive_verified": ok,
                    "unexpected_violations": 0 if ok else 1,
                    "semantic_digest": digs[0],
                }
            receipts.append(execute_corrective_fault_pair(control_id="L11", group="L", fault_kind="INTEGRATION_FAULT", target_symbol="run_r6_a1_single_custody_writer", description="W4 16-chunk workload across 3 randomized seeds -> exact conservation & digest equality", negative_fn=_l11_neg, positive_fn=_l11_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l11, quarantine=False, reason="L11_DONE", actual_now=h.now)

            # L12: Forged/malformed envelope, wrong partition & expired permit denied!
            vg12, pl12, l12 = h.make_session_lease("L12_G", req_cap=10)
            def _l12_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="l12_neg", repo_root=h.fake_repo, strict_authority_required=True, session_id=l12.session_id)
                r1, p1, a1 = h.mint_real_provenance(vg12, pl12, l12, run_id="l12_neg")
                # 1. Forged unregistered envelope
                forged_env = R6A1CustodyChunkEnvelope(
                    envelope_id="forged", session_id=l12.session_id, run_id="l12_neg", partition="QA_FIXTURE",
                    task_ordinal=0, task_id="t0", chunk_ordinal=0, chunk_id="c0", tie_key="0", attempt_id=a1,
                    plan_digest=pl12.plan_digest, raw_payload=r1, raw_sha256="0" * 64, raw_bytes_len=len(r1),
                    normalized_bytes=b"[]", norm_sha256="0" * 64, norm_bytes_len=2, norm_rows_count=0,
                    provenance_receipt=p1, disk_reservation_id=None, cancel_epoch=0, envelope_digest="0" * 64, envelope_token="forged_tok",
                )
                auth_ok = j.acquire_writer_authority(session_id=l12.session_id, cancel_epoch=0, deadline_monotonic=time.monotonic() + 20.0)["authority"]
                res_forged = j.commit_r6_custody_transaction(envelope=forged_env, writer_authority=auth_ok)
                # 2. Wrong partition envelope build
                res_part = build_r6_a1_custody_envelope(session_id=l12.session_id, run_id="l12_neg", partition="DEV_ONLY", task_ordinal=0, task_id="t0", chunk_ordinal=0, chunk_id="c0", attempt_id=a1, plan_digest=pl12.plan_digest, raw_payload=r1, normalized_rows=[{"a": 1}], provenance_receipt=p1)
                # 3. Expired writer authority deadline
                j.release_writer_authority(auth_ok)
                auth_exp = j.acquire_writer_authority(session_id=l12.session_id, cancel_epoch=0, deadline_monotonic=time.monotonic() - 1.0)["authority"]
                good_env = build_r6_a1_custody_envelope(session_id=l12.session_id, run_id="l12_neg", partition="QA_FIXTURE", task_ordinal=0, task_id="t0", chunk_ordinal=0, chunk_id="c_exp", attempt_id=a1, plan_digest=pl12.plan_digest, raw_payload=r1, normalized_rows=[{"a": 1}], provenance_receipt=p1)["envelope"]
                res_exp = j.commit_r6_custody_transaction(envelope=good_env, writer_authority=auth_exp)
                j.close()
                ok = (res_forged["persisted"] is False and res_part["valid"] is False and res_exp["persisted"] is False)
                summary["forged_or_expired_envelope_denied"] = ok
                return {"forged_wrong_partition_expired": True}, {
                    "negative_enforced": ok,
                    "violations_or_guards_triggered": len(res_forged["violations"]) + len(res_part["violations"]) + len(res_exp["violations"]),
                }
            def _l12_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                out = _run_ordered_pipeline_for_workload(h=h, vg=vg12, pl=pl12, lease=l12, run_id="l12_pos", workers=1, num_tasks=1, chunks_per_task=1, delay_schedule_sec={})
                return {"valid_envelope_and_authority": True}, {"positive_verified": out["ok"], "unexpected_violations": 0 if out["ok"] else 1}
            receipts.append(execute_corrective_fault_pair(control_id="L12", group="L", fault_kind="NEGATIVE_CONTRACT", target_symbol="build_r6_a1_custody_envelope", description="Forged envelope, wrong partition & expired writer permit denied", negative_fn=_l12_neg, positive_fn=_l12_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l12, quarantine=False, reason="L12_DONE", actual_now=h.now)
        finally:
            h.close()

    return receipts, traces, summary


def test_kh03_r6_a1_corrective_l01_to_l12_paired_controls() -> None:
    recs, traces, summ = run_kh03_r6_a1_corrective_controls_l12(executable_sha="a" * 40, run_id="pytest_l12")
    assert len(recs) == 12
    assert all(r["status"] == "PASS" for r in recs), [r["control_id"] for r in recs if r["status"] != "PASS"]
    assert len(traces) >= 3
    assert summ["concurrent_writers_serialized_unique_seq"] is True
    assert summ["cross_process_single_writer_enforced"] is True
    assert summ["w1_w2_w4_shuffled_order_identical_semantic_digest"] is True
    assert summ["bounded_queue_item_and_byte_backpressure_verified"] is True
    assert summ["head_slot_anti_starvation_verified"] is True
    assert summ["producer_failure_queue_full_no_deadlock_no_late_commit"] is True
    assert summ["crash_intent_restart_zero_duplicates"] is True
    assert summ["concurrent_seal_before_writer_done_rejected"] is True
    assert summ["direct_unauthorized_persist_denied"] is True
    assert summ["w4_repeated_seeds_byte_conserved"] is True
    assert summ["forged_or_expired_envelope_denied"] is True


def test_kh03_r6_a1_corrective_w1_w2_w4_custody_benchmark() -> None:
    b = run_kh03_r6_a1_corrective_w1_w2_w4_custody_benchmark(num_tasks=6, chunks_per_task=1)
    assert b["valid"] is True
    assert b["all_4_identical"] is True
    assert [r["mode_label"] for r in b["runs"]] == ["w=1", "w=2", "w=4", "w=1_repeat"]
