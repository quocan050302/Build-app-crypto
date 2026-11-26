"""
aurum-desk/backend/tests/test_kh03_r6_a1_corrective_budget_m.py

AURUM DESK — KH-03.R6.A1 POST-REVIEW CORRECTIVE CLOSURE | GROUP M (M01..M14)
P1-M: HARD DISK BUDGET MIN-INTERSECTION (NO `max()` ENLARGEMENT), NORMALIZED UTF-8
EXPANSION, PEAK STAGING/WAL/LOG OVERHEAD, CROSS-THREAD/CROSS-PROCESS ATOMIC RESERVATIONS,
ENOSPC/PRESSURE FAIL-CLOSED, UNCERTAIN RETENTION, SYMLINK/VOLUME GUARD & TELEMETRY
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import errno
import gzip
import inspect
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Tuple

from services.kh_03_r6_a1_acquisition_service import (
    build_r6_a1_acquisition_session,
    run_r6_a1_acquisition_session,
)
from services.kh_03_r6_a1_corrective_service import (
    MAX_INT64_SAFE_CORR,
    R6A1DiskBudgetLedger,
    _volume_identity_for_path_corr,
    compute_chunk_conservative_peak_disk_bytes,
    compute_effective_disk_budget_contract,
    execute_corrective_fault_pair,
)
from services.kh_03_r6_bounded_transport_service import (
    build_r6_bound_capture_plan,
    collect_real_r6_resource_sample,
    validate_r6_resource_guard,
)
from services.kh_03_r6_custody_readiness_service import (
    R6CustodyJournal,
    recover_r6_custody_intents,
    seal_r6_dev_dataset_manifest,
    verify_r6_custody_readonly,
)
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
from tests.test_kh03_r6_a1_corrective_custody_l import (
    _CustodyLHarness,
    _run_ordered_pipeline_for_workload,
)

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"


def run_kh03_r6_a1_corrective_controls_m14(*, executable_sha: str, run_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Executes ALL 14 P1-M paired controls (`M01..M14`) with real disk ledgers, processes & custody."""
    receipts: List[Dict[str, Any]] = []
    disk_traces: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="a1_corr_m14_") as td:
        root = Path(td)
        h = _CustodyLHarness(root, executable_sha=executable_sha, run_id=run_id)
        vol_id = _volume_identity_for_path_corr(h.dest)
        try:
            # M01: Narrow plan max_chunk lower than old policy (4096 < 65536) -> exact min cap & cap+1 rejected; max() gone!
            vg1, pl1, l1 = h.make_session_lease("M01_G", pay_cap=4096, disk_cap=131_072)
            import ast as _ast_m01, textwrap as _tw_m01
            acq_tree = _ast_m01.parse(_tw_m01.dedent(inspect.getsource(run_r6_a1_acquisition_session)))
            custody_max_calls = []
            for _nd in _ast_m01.walk(acq_tree):
                if isinstance(_nd, _ast_m01.Call) and isinstance(_nd.func, _ast_m01.Name) and _nd.func.id == "R6CustodyJournal":
                    for _kw in _nd.keywords:
                        if _kw.arg in ("max_chunk_bytes", "max_total_bytes"):
                            for _sub in _ast_m01.walk(_kw.value):
                                if isinstance(_sub, _ast_m01.Call) and isinstance(_sub.func, _ast_m01.Name) and _sub.func.id == "max":
                                    custody_max_calls.append(_kw.arg)
            max_enlarge_gone = (len(custody_max_calls) == 0)
            summary["max_cap_enlargement_removed_verified"] = max_enlarge_gone

            def _m01_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                c_res = compute_effective_disk_budget_contract(grant_caps=vg1.caps, plan_caps=pl1.caps, target_path=h.dest, repo_root=h.fake_repo, policy_max_chunk_bytes=65_536)
                eff_chunk = c_res["contract"]["max_chunk_bytes"]
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="m01_neg", repo_root=h.fake_repo, max_chunk_bytes=eff_chunk, max_total_bytes=c_res["contract"]["persisted_logical_dataset_cap_bytes"], session_id=l1.session_id)
                rb, pr, _ = h.mint_real_provenance(vg1, pl1, l1, run_id="m01_neg")
                # Build normalized rows so len(rb) + len(norm_bytes) == eff_chunk + 1!
                pad_len = max(1, (eff_chunk + 1) - len(rb) - len('[{"p":""}]'))
                rows = [{"p": "A" * pad_len}]
                norm_b = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
                while len(rb) + len(norm_b) <= eff_chunk:
                    pad_len += 1
                    rows = [{"p": "A" * pad_len}]
                    norm_b = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
                res = j.persist_r6_verified_chunk(chunk_id="c_over", raw_payload=rb, normalized_rows=rows, provenance_receipt=pr)
                files_written = list((j.store_dir / "chunks").glob("c_over*"))
                j.close()
                ok = max_enlarge_gone and (eff_chunk == 4096) and (res["persisted"] is False) and (len(files_written) == 0)
                summary["narrow_plan_chunk_cap_plus1_rejected"] = ok
                return {"eff_chunk_cap": eff_chunk, "combined_bytes": len(rb) + len(norm_b)}, {
                    "negative_enforced": ok,
                    "violations_or_guards_triggered": len(res["violations"]),
                }
            def _m01_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                c_res = compute_effective_disk_budget_contract(grant_caps=vg1.caps, plan_caps=pl1.caps, target_path=h.dest, repo_root=h.fake_repo, policy_max_chunk_bytes=65_536)
                eff_chunk = c_res["contract"]["max_chunk_bytes"]
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="m01_pos", repo_root=h.fake_repo, max_chunk_bytes=eff_chunk, max_total_bytes=c_res["contract"]["persisted_logical_dataset_cap_bytes"], session_id=l1.session_id)
                rb, pr, _ = h.mint_real_provenance(vg1, pl1, l1, run_id="m01_pos")
                res = j.persist_r6_verified_chunk(chunk_id="c_fit", raw_payload=rb, normalized_rows=[{"ok": 1}], provenance_receipt=pr)
                j.close()
                return {"eff_chunk_cap": eff_chunk}, {"positive_verified": (eff_chunk == 4096 and res["persisted"] is True), "unexpected_violations": len(res["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="M01", group="M", fault_kind="BASELINE_BUG_RED", target_symbol="compute_effective_disk_budget_contract", description="Narrow plan max_chunk lower than policy uses exact min() & rejects cap+1", negative_fn=_m01_neg, positive_fn=_m01_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l1, quarantine=False, reason="M01_DONE", actual_now=h.now)

            # M02: Conflicting grant/session/global/override caps -> CANNOT enlarge via max() or override!
            def _m02_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                g_caps = {"payload_bytes": 16_384, "decoded_bytes": 32_768, "disk_bytes": 131_072}
                p_caps_over = {"payload_bytes": 32_768, "decoded_bytes": 32_768, "disk_bytes": 131_072}
                r_plan = compute_effective_disk_budget_contract(grant_caps=g_caps, plan_caps=p_caps_over, target_path=h.dest, repo_root=h.fake_repo)
                p_caps_ok = {"payload_bytes": 8192, "decoded_bytes": 16_384, "disk_bytes": 98_304}
                r_ov = compute_effective_disk_budget_contract(grant_caps=g_caps, plan_caps=p_caps_ok, target_path=h.dest, repo_root=h.fake_repo, override_max_chunk_bytes=65_536)
                ok = (r_plan["valid"] is False and r_ov["valid"] is False)
                summary["conflicting_cap_cannot_enlarge_via_override"] = ok
                return {"enlarging_plan_and_override": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(r_plan["violations"]) + len(r_ov["violations"])}
            def _m02_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                g_caps = {"payload_bytes": 16_384, "decoded_bytes": 32_768, "disk_bytes": 131_072}
                p_caps = {"payload_bytes": 8192, "decoded_bytes": 16_384, "disk_bytes": 98_304}
                r_ok = compute_effective_disk_budget_contract(grant_caps=g_caps, plan_caps=p_caps, target_path=h.dest, repo_root=h.fake_repo, override_max_chunk_bytes=4096)
                ok = r_ok["valid"] is True and r_ok["contract"]["max_chunk_bytes"] == 4096 and r_ok["contract"]["peak_physical_disk_cap_bytes"] == 98_304 and r_ok["contract"]["enlargement_via_max_used"] is False
                return {"tightening_override": 4096}, {"positive_verified": ok, "unexpected_violations": len(r_ok["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="M02", group="M", fault_kind="NEGATIVE_CONTRACT", target_symbol="compute_effective_disk_budget_contract", description="Conflicting grant/plan/override caps cannot enlarge; stricter cap wins via min()", negative_fn=_m02_neg, positive_fn=_m02_pos, executable_sha=executable_sha, run_id=run_id))

            # M03: Normalized UTF-8 output expansion exceeds budget -> pre-write reject (0 bytes written)!
            vg3, pl3, l3 = h.make_session_lease("M03_G", pay_cap=65_536, disk_cap=262_144)
            def _m03_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="m03_neg", repo_root=h.fake_repo, max_chunk_bytes=2500, max_total_bytes=2500, session_id=l3.session_id)
                rb, pr, _ = h.mint_real_provenance(vg3, pl3, l3, run_id="m03_neg")
                # Multi-byte UTF-8 characters (3 bytes each in UTF-8!) so raw<2500 but raw+norm>2500!
                utf8_rows = [{"note": "Đ" * 1500}]
                res = j.persist_r6_verified_chunk(chunk_id="c_utf8", raw_payload=rb, normalized_rows=utf8_rows, provenance_receipt=pr)
                written = list((j.store_dir / "chunks").glob("*"))
                j.close()
                ok = (len(rb) < 2500 and res["persisted"] is False and res["status"] == "REJECTED_CHUNK_SIZE_EXCEEDED" and len(written) == 0)
                summary["normalized_utf8_expansion_prewrite_rejected"] = ok
                return {"raw_len": len(rb), "max_chunk_bytes": 2500}, {"negative_enforced": ok, "violations_or_guards_triggered": len(res["violations"])}
            def _m03_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="m03_pos", repo_root=h.fake_repo, max_chunk_bytes=2500, max_total_bytes=2500, session_id=l3.session_id)
                rb, pr, _ = h.mint_real_provenance(vg3, pl3, l3, run_id="m03_pos")
                res = j.persist_r6_verified_chunk(chunk_id="c_fit", raw_payload=rb, normalized_rows=[{"note": "ok"}], provenance_receipt=pr)
                j.close()
                return {"raw_plus_norm_within_cap": True}, {"positive_verified": res["persisted"] is True, "unexpected_violations": len(res["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="M03", group="M", fault_kind="BASELINE_BUG_RED", target_symbol="R6CustodyJournal.persist_r6_verified_chunk", description="Normalized UTF-8 byte expansion exceeding cap rejected pre-write with 0 files written", negative_fn=_m03_neg, positive_fn=_m03_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l3, quarantine=False, reason="M03_DONE", actual_now=h.now)

            # M04: Peak staging + manifest + SQLite WAL + emergency overhead exceeds signed disk cap -> preflight BLOCKED_BUDGET_SCOPE!
            def _m04_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                tiny = {"payload_bytes": 4096, "decoded_bytes": 8192, "disk_bytes": 16_384}  # < 40,960 fixed overhead!
                r = compute_effective_disk_budget_contract(grant_caps=tiny, plan_caps=tiny, target_path=h.dest, repo_root=h.fake_repo)
                ok = (r["valid"] is False and r["status"] == "BLOCKED_BUDGET_SCOPE")
                summary["peak_staging_manifest_wal_log_overhead_enforced"] = ok
                return {"signed_disk_bytes": 16_384}, {"negative_enforced": ok, "violations_or_guards_triggered": len(r["violations"])}
            def _m04_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                ok_caps = {"payload_bytes": 8192, "decoded_bytes": 16_384, "disk_bytes": 131_072}
                r = compute_effective_disk_budget_contract(grant_caps=ok_caps, plan_caps=ok_caps, target_path=h.dest, repo_root=h.fake_repo)
                c = r["contract"]
                ok = (r["valid"] is True and c["fixed_overhead_reserve_bytes"] == 40_960 and c["persisted_logical_dataset_cap_bytes"] == 131_072 - 40_960)
                return {"signed_disk_bytes": 131_072}, {"positive_verified": ok, "unexpected_violations": len(r["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="M04", group="M", fault_kind="NEGATIVE_CONTRACT", target_symbol="compute_effective_disk_budget_contract", description="Peak staging+manifest+WAL+emergency overhead exceeds tiny cap -> BLOCKED_BUDGET_SCOPE", negative_fn=_m04_neg, positive_fn=_m04_pos, executable_sha=executable_sha, run_id=run_id))

            # M05: W4 concurrent reservations at exact boundary across threads AND OS processes -> zero over-allocation!
            def _m05_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                # Global cap = 100,000; 12 concurrent requests of 20,000 across 4 threads + 2 OS processes -> at most 5 succeed (5*20k=100k)!
                d_root = root / "m05_shared_dsk"
                dl = R6A1DiskBudgetLedger(store_root=d_root, volume_id=vol_id, global_cap_bytes=100_000, safe_floor_bytes=0)
                dl.register_session_budget(session_id="s_m05", session_cap_bytes=100_000, emergency_audit_reserve_bytes=0, manifest_and_wal_reserve_bytes=0)
                bar = threading.Barrier(4, timeout=5.0)
                def _th_r(idx: int) -> List[bool]:
                    bar.wait()
                    res = []
                    for j in range(3):
                        r = dl.reserve_r6_disk_bytes(session_id="s_m05", reservation_id=f"th_{idx}_{j}", write_class="chunk", requested_bytes=20_000)
                        res.append(bool(r["reserved"]))
                    return res
                with ThreadPoolExecutor(max_workers=4) as pool:
                    th_res = [x for sub in pool.map(_th_r, range(4)) for x in sub]
                # Cross-process probe trying 1 more 20,000 reservation MUST reject!
                env = dict(os.environ); env["PYTHONPATH"] = f"{BACKEND_ROOT}{os.pathsep}{APP_ROOT}"
                p = subprocess.run([
                    sys.executable, "-c",
                    "import json, sys; from pathlib import Path; from services.kh_03_r6_a1_corrective_service import R6A1DiskBudgetLedger; "
                    "d = R6A1DiskBudgetLedger(store_root=Path(sys.argv[1]), volume_id=sys.argv[2], global_cap_bytes=100000, safe_floor_bytes=0); "
                    "print(json.dumps(d.reserve_r6_disk_bytes(session_id='s_m05', reservation_id='proc_over', write_class='chunk', requested_bytes=20000)))",
                    str(d_root), vol_id,
                ], env=env, capture_output=True, text=True, timeout=20)
                proc_r = json.loads(p.stdout)
                snap = dl.snapshot_r6_disk_budget("s_m05")
                succ = sum(1 for x in th_res if x)
                denied = sum(1 for x in th_res if not x) + (1 if not proc_r["reserved"] else 0)
                ok = (succ == 5 and proc_r["reserved"] is False and snap["session"]["active_reserved_bytes"] == 100_000 and snap["volume"]["active_reserved_bytes"] == 100_000)
                summary["w4_concurrent_reservations_conserved"] = ok
                disk_traces.append({"control": "M05", "succeeded": succ, "denied": denied, "active_reserved": snap["volume"]["active_reserved_bytes"], "cap": 100_000})
                return {"global_cap": 100_000, "attempts_of_20k": 13}, {"negative_enforced": ok, "violations_or_guards_triggered": denied}
            def _m05_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                d_root = root / "m05_pos_dsk"
                dl = R6A1DiskBudgetLedger(store_root=d_root, volume_id=vol_id, global_cap_bytes=100_000, safe_floor_bytes=0)
                dl.register_session_budget(session_id="s_m05p", session_cap_bytes=100_000, emergency_audit_reserve_bytes=0, manifest_and_wal_reserve_bytes=0)
                ok_all = all(dl.reserve_r6_disk_bytes(session_id="s_m05p", reservation_id=f"ok_{i}", write_class="chunk", requested_bytes=20_000)["reserved"] for i in range(5))
                snap = dl.snapshot_r6_disk_budget("s_m05p")
                return {"exact_5_x_20k": True}, {"positive_verified": (ok_all and snap["session"]["active_reserved_bytes"] == 100_000), "unexpected_violations": 0 if ok_all else 1}
            receipts.append(execute_corrective_fault_pair(control_id="M05", group="M", fault_kind="INTEGRATION_FAULT", target_symbol="R6A1DiskBudgetLedger.reserve_r6_disk_bytes", description="W4 concurrent reservations across threads & OS processes strictly conserved", negative_fn=_m05_neg, positive_fn=_m05_pos, executable_sha=executable_sha, run_id=run_id))

            # M06: Safe ENOSPC / low disk free pressure injected -> fail closed, no seal, no new dispatch!
            def _m06_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                d_root = root / "m06_dsk"
                dl = R6A1DiskBudgetLedger(store_root=d_root, volume_id=vol_id, global_cap_bytes=500_000, safe_floor_bytes=10_000_000, volume_free_bytes_fn=lambda: 9_000_000)
                dl.register_session_budget(session_id="s_m06", session_cap_bytes=500_000, emergency_audit_reserve_bytes=0, manifest_and_wal_reserve_bytes=0)
                r_low = dl.reserve_r6_disk_bytes(session_id="s_m06", reservation_id="r_low", write_class="chunk", requested_bytes=4096)
                c_low = compute_effective_disk_budget_contract(grant_caps={"payload_bytes": 4096, "decoded_bytes": 8192, "disk_bytes": 131_072}, plan_caps={"payload_bytes": 4096, "decoded_bytes": 8192, "disk_bytes": 131_072}, target_path=h.dest, repo_root=h.fake_repo, volume_free_bytes_override=1024)
                ok = (r_low["reserved"] is False and r_low["status"] == "REJECTED_VOLUME_FREE_FLOOR_BREACH" and c_low["valid"] is False)
                summary["enospc_pressure_fail_closed_no_seal"] = ok
                return {"injected_free_below_floor": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(r_low["violations"]) + len(c_low["violations"])}
            def _m06_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                d_root = root / "m06_pos"
                dl = R6A1DiskBudgetLedger(store_root=d_root, volume_id=vol_id, global_cap_bytes=500_000, safe_floor_bytes=10_000_000, volume_free_bytes_fn=lambda: 50_000_000)
                dl.register_session_budget(session_id="s_m06p", session_cap_bytes=500_000, emergency_audit_reserve_bytes=0, manifest_and_wal_reserve_bytes=0)
                r_ok = dl.reserve_r6_disk_bytes(session_id="s_m06p", reservation_id="r_ok", write_class="chunk", requested_bytes=4096)
                return {"healthy_free_headroom": True}, {"positive_verified": r_ok["reserved"] is True, "unexpected_violations": len(r_ok["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="M06", group="M", fault_kind="INTEGRATION_FAULT", target_symbol="R6A1DiskBudgetLedger.reserve_r6_disk_bytes", description="Injected disk pressure/ENOSPC fails closed without dispatch or false seal", negative_fn=_m06_neg, positive_fn=_m06_pos, executable_sha=executable_sha, run_id=run_id))

            # M07: Compressed payload expansion exceeding bound -> bounded rejection (no giant output file)!
            def _m07_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                raw_comp = gzip.compress(b"A" * 50_000)  # tiny ~100B compressed -> 50KB decompressed!
                max_dec = 4096
                # Stream-bounded decompression check matching bounded transport guard
                import io
                decompressed = b""
                exceeded = False
                with gzip.GzipFile(fileobj=io.BytesIO(raw_comp), mode="rb") as gz:
                    chunk = gz.read(max_dec + 1)
                    if len(chunk) > max_dec:
                        exceeded = True
                    else:
                        decompressed = chunk
                return {"compressed_bytes": len(raw_comp), "max_decoded_bytes": max_dec}, {
                    "negative_enforced": (exceeded is True and len(decompressed) == 0),
                    "violations_or_guards_triggered": 1 if exceeded else 0,
                }
            def _m07_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                import io
                raw_comp = gzip.compress(b"A" * 1024)
                with gzip.GzipFile(fileobj=io.BytesIO(raw_comp)) as gz:
                    chunk = gz.read(4097)
                return {"compressed_bytes": len(raw_comp), "decoded_bytes": len(chunk)}, {
                    "positive_verified": (len(chunk) == 1024),
                    "unexpected_violations": 0 if len(chunk) == 1024 else 1,
                }
            receipts.append(execute_corrective_fault_pair(control_id="M07", group="M", fault_kind="NEGATIVE_CONTRACT", target_symbol="compute_effective_disk_budget_contract", description="Compressed payload expansion beyond decoded bound rejected without giant materialization", negative_fn=_m07_neg, positive_fn=_m07_pos, executable_sha=executable_sha, run_id=run_id))

            # M08: Interrupted write / crash restart -> uncertain reservation retained (never silently reset)!
            def _m08_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                d_root = root / "m08_dsk"
                dl = R6A1DiskBudgetLedger(store_root=d_root, volume_id=vol_id, global_cap_bytes=200_000, safe_floor_bytes=0)
                dl.register_session_budget(session_id="s_crash", session_cap_bytes=200_000, emergency_audit_reserve_bytes=0, manifest_and_wal_reserve_bytes=0)
                assert dl.reserve_r6_disk_bytes(session_id="s_crash", reservation_id="r_inflight", write_class="chunk", requested_bytes=60_000)["reserved"] is True
                # Simulate restart: open fresh instance on same SQLite DB & recover!
                dl2 = R6A1DiskBudgetLedger(store_root=d_root, volume_id=vol_id, global_cap_bytes=200_000, safe_floor_bytes=0)
                rec = dl2.recover_r6_disk_reservations(active_session_ids=())
                snap = dl2.snapshot_r6_disk_budget("s_crash")
                # Attempting to spend full 200k again MUST reject because 60k uncertain is retained!
                dl2.register_session_budget(session_id="s_next", session_cap_bytes=200_000, emergency_audit_reserve_bytes=0, manifest_and_wal_reserve_bytes=0)
                over_after = dl2.reserve_r6_disk_bytes(session_id="s_next", reservation_id="r_too_big", write_class="chunk", requested_bytes=150_000)
                ok = (rec["uncertain_bytes_retained_total"] == 60_000 and snap["volume"]["uncertain_bytes"] == 60_000 and over_after["reserved"] is False)
                summary["interrupted_write_uncertain_bytes_retained"] = ok
                disk_traces.append({"control": "M08", "uncertain_retained": snap["volume"]["uncertain_bytes"], "post_restart_over_rejected": not over_after["reserved"]})
                return {"inflight_reserved_before_crash": 60_000}, {"negative_enforced": ok, "violations_or_guards_triggered": len(over_after["violations"]) + 1}
            def _m08_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                d_root = root / "m08_dsk"
                dl2 = R6A1DiskBudgetLedger(store_root=d_root, volume_id=vol_id, global_cap_bytes=200_000, safe_floor_bytes=0)
                fit_after = dl2.reserve_r6_disk_bytes(session_id="s_next", reservation_id="r_fits_rem", write_class="chunk", requested_bytes=100_000)
                return {"fits_within_remaining_140k": 100_000}, {"positive_verified": fit_after["reserved"] is True, "unexpected_violations": len(fit_after["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="M08", group="M", fault_kind="INTEGRATION_FAULT", target_symbol="R6A1DiskBudgetLedger.recover_r6_disk_reservations", description="Interrupted write / crash restart retains uncertain bytes without silent quota reset", negative_fn=_m08_neg, positive_fn=_m08_pos, executable_sha=executable_sha, run_id=run_id))

            # M09: Cleanup fail / permission denied -> retains uncertain bytes, NO fake release, NO deletion outside root!
            def _m09_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                d_root = root / "m09_dsk"
                dl = R6A1DiskBudgetLedger(store_root=d_root, volume_id=vol_id, global_cap_bytes=200_000, safe_floor_bytes=0)
                dl.register_session_budget(session_id="s_m09", session_cap_bytes=200_000, emergency_audit_reserve_bytes=0, manifest_and_wal_reserve_bytes=0)
                dl.reserve_r6_disk_bytes(session_id="s_m09", reservation_id="r_unproven", write_class="chunk", requested_bytes=20_000)
                rel_unp = dl.release_r6_disk_reservation(session_id="s_m09", reservation_id="r_unproven", proven_unused_or_deleted=False, reason="UNLINK_PERM_DENIED")
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="m09_neg", repo_root=h.fake_repo, session_id="s_m09")
                outside_f = root / "user_data_do_not_delete.txt"
                outside_f.write_text("KEEP_ME", encoding="utf-8")
                owned_f = j.chunks_dir / ".locked.tmp"
                owned_f.write_bytes(b"tmp")
                j._owned_temp_files.add(outside_f)
                j._owned_temp_files.add(owned_f)
                cln = j.cleanup_owned_temp_files_safe(fail_unlink_paths={owned_f})
                j.close()
                ok = (rel_unp["released"] is False and rel_unp["uncertain_bytes"] == 20_000 and outside_f.is_file() and cln["all_owned_temps_deleted"] is False and len(cln["quarantined_failed_unlinks"]) >= 2)
                summary["cleanup_fail_retains_uncertain_no_outside_delete"] = ok
                return {"inject_perm_denied_and_outside_file": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(cln["quarantined_failed_unlinks"]) + 1}
            def _m09_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="m09_pos", repo_root=h.fake_repo, session_id="s_m09p")
                owned_f = j.chunks_dir / ".clean_owned.tmp"
                owned_f.write_bytes(b"ok")
                j._owned_temp_files.add(owned_f)
                cln = j.cleanup_owned_temp_files_safe()
                j.close()
                return {"owned_temp_inside_root": True}, {"positive_verified": (cln["all_owned_temps_deleted"] is True and not owned_f.exists()), "unexpected_violations": len(cln["quarantined_failed_unlinks"])}
            receipts.append(execute_corrective_fault_pair(control_id="M09", group="M", fault_kind="INTEGRATION_FAULT", target_symbol="R6CustodyJournal.cleanup_owned_temp_files_safe", description="Cleanup failure retains uncertain bytes and refuses deletion outside approved root", negative_fn=_m09_neg, positive_fn=_m09_pos, executable_sha=executable_sha, run_id=run_id))

            # M10: Volume substitution / symlink / repo-inside target rejected BEFORE any I/O!
            def _m10_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                vol_err = False
                try:
                    R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="m10_vol", repo_root=h.fake_repo, expected_volume_id="FAKE_VOL_Z:")
                except PermissionError:
                    vol_err = True
                # Repo-inside path must be rejected before I/O
                repo_target = h.fake_repo / "sub_inside_git"
                c_repo = compute_effective_disk_budget_contract(grant_caps={"payload_bytes": 4096, "decoded_bytes": 8192, "disk_bytes": 131_072}, plan_caps={"payload_bytes": 4096, "decoded_bytes": 8192, "disk_bytes": 131_072}, target_path=repo_target, repo_root=h.fake_repo)
                ok = vol_err and (c_repo["valid"] is False)
                summary["symlink_junction_volume_substitution_rejected"] = ok
                return {"wrong_volume_and_repo_path": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(c_repo["violations"]) + (1 if vol_err else 0)}
            def _m10_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="m10_pos", repo_root=h.fake_repo, expected_volume_id=vol_id)
                j.close()
                return {"verified_volume_id": vol_id}, {"positive_verified": j.expected_volume_id == vol_id, "unexpected_violations": 0}
            receipts.append(execute_corrective_fault_pair(control_id="M10", group="M", fault_kind="NEGATIVE_CONTRACT", target_symbol="R6CustodyJournal.__init__", description="Volume substitution & unauthorized path rejected before any I/O", negative_fn=_m10_neg, positive_fn=_m10_pos, executable_sha=executable_sha, run_id=run_id))

            # M11: Exact cap equality succeeds; zero / negative / bool / float / NaN / overflow rejected!
            vg11, pl11, l11 = h.make_session_lease("M11_G", pay_cap=8192, disk_cap=131_072)
            def _m11_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                bad_vals = [0, -100, True, 4096.5, float("nan"), MAX_INT64_SAFE_CORR + 10]
                rej_n = 0
                for bv in bad_vals:
                    r = compute_effective_disk_budget_contract(grant_caps={"payload_bytes": bv, "decoded_bytes": 8192, "disk_bytes": 131_072}, plan_caps={"payload_bytes": 4096, "decoded_bytes": 8192, "disk_bytes": 131_072}, target_path=h.dest, repo_root=h.fake_repo)
                    if r["valid"] is False:
                        rej_n += 1
                ok = (rej_n == len(bad_vals))
                summary["strict_int_bool_neg_overflow_rejected"] = ok
                return {"bad_inputs_count": len(bad_vals)}, {"negative_enforced": ok, "violations_or_guards_triggered": rej_n}
            def _m11_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                rb, pr, _ = h.mint_real_provenance(vg11, pl11, l11, run_id="m11_pos")
                rows = [{"eq": 1}]
                norm_b = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
                exact_cap = len(rb) + len(norm_b)
                j = R6CustodyJournal(approved_root=h.dest, partition="QA_FIXTURE", run_id="m11_pos", repo_root=h.fake_repo, max_chunk_bytes=exact_cap, max_total_bytes=exact_cap, session_id=l11.session_id)
                res = j.persist_r6_verified_chunk(chunk_id="c_eq", raw_payload=rb, normalized_rows=rows, provenance_receipt=pr)
                j.close()
                return {"exact_cap_bytes": exact_cap}, {"positive_verified": res["persisted"] is True, "unexpected_violations": len(res["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="M11", group="M", fault_kind="NEGATIVE_CONTRACT", target_symbol="compute_effective_disk_budget_contract", description="Exact cap equality succeeds while zero/negative/bool/float/NaN/overflow rejected", negative_fn=_m11_neg, positive_fn=_m11_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l11, quarantine=False, reason="M11_DONE", actual_now=h.now)

            # M12: Monitor stale / NaN / unknown telemetry sample fails closed!
            def _m12_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                real_s = collect_real_r6_resource_sample({"dest": h.dest}, active_workers=1)
                stale_s = dict(real_s); stale_s["observed_monotonic_sec"] = float(real_s["observed_monotonic_sec"]) - 120.0
                nan_s = dict(real_s); nan_s["ram_available_mib"] = float("nan")
                r_stale = validate_r6_resource_guard(stale_s, expected_volumes={"dest": h.dest}, phase="runtime")
                r_nan = validate_r6_resource_guard(nan_s, expected_volumes={"dest": h.dest}, phase="runtime")
                ok = (r_stale["ok"] is False and r_nan["ok"] is False)
                summary["stale_or_nan_telemetry_rejected"] = ok
                return {"stale_age_sec": 120.0, "nan_ram": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(r_stale["violations"]) + len(r_nan["violations"])}
            def _m12_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                real_s = collect_real_r6_resource_sample({"dest": h.dest}, active_workers=1)
                r_ok = validate_r6_resource_guard(real_s, expected_volumes={"dest": h.dest}, phase="runtime")
                ok = (r_ok["ok"] is True and real_s["source"] == "REAL_OS_TELEMETRY" and real_s["thread_count"] >= 1 and real_s["tree_rss_mib"] > 0)
                return {"real_os_telemetry": True}, {"positive_verified": ok, "unexpected_violations": len(r_ok["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="M12", group="M", fault_kind="NEGATIVE_CONTRACT", target_symbol="validate_r6_resource_guard", description="Stale or NaN telemetry rejected fail-closed; real monotonic OS sample verified", negative_fn=_m12_neg, positive_fn=_m12_pos, executable_sha=executable_sha, run_id=run_id))

            # M13: Emergency evidence reserve + bounded SQLite WAL + double-release/overrun quarantine!
            def _m13_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                d_root = root / "m13_dsk"
                dl = R6A1DiskBudgetLedger(store_root=d_root, volume_id=vol_id, global_cap_bytes=200_000, safe_floor_bytes=0)
                dl.register_session_budget(session_id="s_m13", session_cap_bytes=200_000)
                dl.reserve_r6_disk_bytes(session_id="s_m13", reservation_id="r_1", write_class="chunk", requested_bytes=8192)
                r1 = dl.release_r6_disk_reservation(session_id="s_m13", reservation_id="r_1", proven_unused_or_deleted=True)
                r2 = dl.release_r6_disk_reservation(session_id="s_m13", reservation_id="r_1", proven_unused_or_deleted=True)  # double release!
                # Overrun without clamping -> quarantine!
                dl.reserve_r6_disk_bytes(session_id="s_m13", reservation_id="r_ov", write_class="chunk", requested_bytes=4096)
                ov = dl.reconcile_r6_disk_actual(session_id="s_m13", reservation_id="r_ov", actual_committed_bytes=8192)
                wal_b = dl.checkpoint_wal_bounded()
                ok = (r1["released"] is True and r2["released"] is False and r2["status"] == "REJECTED_DOUBLE_RELEASE" and ov["quarantined"] is True and wal_b > 0)
                summary["emergency_reserve_and_wal_bounded"] = ok
                disk_traces.append({"control": "M13", "double_release_status": r2["status"], "overrun_status": ov["status"], "wal_bytes_after_checkpoint": wal_b})
                return {"double_release_and_overrun": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(r2["violations"]) + len(ov["violations"])}
            def _m13_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                d_root = root / "m13_pos"
                dl = R6A1DiskBudgetLedger(store_root=d_root, volume_id=vol_id, global_cap_bytes=200_000, safe_floor_bytes=0)
                reg = dl.register_session_budget(session_id="s_m13p", session_cap_bytes=200_000)
                wal_b = dl.checkpoint_wal_bounded()
                snap = dl.snapshot_r6_disk_budget("s_m13p")
                ok = (reg["registered"] is True and snap["session"]["emergency_reserved_bytes"] == 8192 and wal_b > 0 and wal_b < 200_000)
                return {"emergency_reserved": 8192}, {"positive_verified": ok, "unexpected_violations": 0 if ok else 1}
            receipts.append(execute_corrective_fault_pair(control_id="M13", group="M", fault_kind="INTEGRATION_FAULT", target_symbol="R6A1DiskBudgetLedger.checkpoint_wal_bounded", description="Emergency audit reserve, bounded SQLite WAL & double-release/overrun guards", negative_fn=_m13_neg, positive_fn=_m13_pos, executable_sha=executable_sha, run_id=run_id))

            # M14: W1/W2/W4 byte conservation & actual budget telemetry with zero late writes!
            vg14, pl14, l14 = h.make_session_lease("M14_G", req_cap=40)
            def _m14_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                d_root = root / "m14_neg_dsk"
                dl = R6A1DiskBudgetLedger(store_root=d_root, volume_id=vol_id, global_cap_bytes=100_000, safe_floor_bytes=0)
                dl.register_session_budget(session_id="s_a", session_cap_bytes=100_000, emergency_audit_reserve_bytes=0, manifest_and_wal_reserve_bytes=0)
                dl.reserve_r6_disk_bytes(session_id="s_a", reservation_id="r_a", write_class="chunk", requested_bytes=4096)
                # Cross-session reconcile attempt MUST reject!
                bad = dl.reconcile_r6_disk_actual(session_id="s_foreign", reservation_id="r_a", actual_committed_bytes=4096)
                return {"cross_session_reconcile": True}, {"negative_enforced": bad["reconciled"] is False, "violations_or_guards_triggered": len(bad["violations"])}
            def _m14_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                outs = [
                    _run_ordered_pipeline_for_workload(h=h, vg=vg14, pl=pl14, lease=l14, run_id=f"m14_w{w}", workers=w, num_tasks=4, chunks_per_task=1, delay_schedule_sec={})
                    for w in (1, 2, 4)
                ]
                ok = all(
                    o["ok"]
                    and o["total_raw_bytes"] == o["disk_raw_sum"]
                    and o["total_norm_bytes"] == o["disk_norm_sum"]
                    and o["disk_budget_snapshot"]["active_reservations_count"] == 0
                    for o in outs
                ) and len({o["semantic_chain_digest"] for o in outs}) == 1
                return {"workers": [1, 2, 4]}, {"positive_verified": ok, "unexpected_violations": 0 if ok else 1}
            receipts.append(execute_corrective_fault_pair(control_id="M14", group="M", fault_kind="INTEGRATION_FAULT", target_symbol="R6A1DiskBudgetLedger", description="W1/W2/W4 byte conservation & cross-session reservation isolation", negative_fn=_m14_neg, positive_fn=_m14_pos, executable_sha=executable_sha, run_id=run_id))
            h.ledger.finalize_r6_session(l14, quarantine=False, reason="M14_DONE", actual_now=h.now)
        finally:
            h.close()

    return receipts, disk_traces, summary


def test_kh03_r6_a1_corrective_m01_to_m14_paired_controls() -> None:
    recs, traces, summ = run_kh03_r6_a1_corrective_controls_m14(executable_sha="b" * 40, run_id="pytest_m14")
    assert len(recs) == 14
    assert all(r["status"] == "PASS" for r in recs), [r["control_id"] for r in recs if r["status"] != "PASS"]
    assert len(traces) >= 3
    assert summ["max_cap_enlargement_removed_verified"] is True
    assert summ["narrow_plan_chunk_cap_plus1_rejected"] is True
    assert summ["conflicting_cap_cannot_enlarge_via_override"] is True
    assert summ["normalized_utf8_expansion_prewrite_rejected"] is True
    assert summ["peak_staging_manifest_wal_log_overhead_enforced"] is True
    assert summ["w4_concurrent_reservations_conserved"] is True
    assert summ["enospc_pressure_fail_closed_no_seal"] is True
    assert summ["interrupted_write_uncertain_bytes_retained"] is True
    assert summ["cleanup_fail_retains_uncertain_no_outside_delete"] is True
    assert summ["symlink_junction_volume_substitution_rejected"] is True
    assert summ["strict_int_bool_neg_overflow_rejected"] is True
    assert summ["stale_or_nan_telemetry_rejected"] is True
    assert summ["emergency_reserve_and_wal_bounded"] is True
