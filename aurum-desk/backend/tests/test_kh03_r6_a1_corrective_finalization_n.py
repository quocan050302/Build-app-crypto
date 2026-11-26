"""
aurum-desk/backend/tests/test_kh03_r6_a1_corrective_finalization_n.py

AURUM DESK — KH-03.R6.A1 POST-REVIEW CORRECTIVE CLOSURE | GROUP N (N01..N15)
P1-N: EXCEPTION-SAFE CLAIM, FINALIZATION ISOLATION, CAS IDEMPOTENT TERMINAL TRANSITIONS,
UNPROVEN_TERMINAL STORE MARKER, KEYBOARDINTERRUPT CLEANUP, UNREAPED WORKER QUARANTINE,
RESTART RECOVERY & COMPOUND L+M+N FAULT CLOSURE
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import errno
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from services.kh_03_r6_a1_acquisition_service import (
    TERMINAL_QA_PASS_STATUS_A1,
    build_r6_a1_acquisition_session,
    run_r6_a1_acquisition_session,
)
from services.kh_03_r6_a1_corrective_service import (
    R6A1DiskBudgetLedger,
    _volume_identity_for_path_corr,
    derive_r6_a1_terminal_disposition,
    execute_corrective_fault_pair,
)
from services.kh_03_r6_bounded_transport_service import (
    build_r6_bound_capture_plan,
    collect_real_r6_resource_sample,
)
from services.kh_03_r6_custody_readiness_service import verify_r6_custody_readonly
from services.kh_03_r6_durable_grant_service import (
    QA_ISOLATED_TRUST_NAMESPACE_R6,
    load_r6_owner_trust_root,
    open_r6_durable_ledger,
    provision_qa_isolated_trust_root,
    provision_r6_durable_ledger,
    reconcile_r6_a1_claim_after_exception,
    recover_r6_a1_incomplete_sessions,
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
from tests.test_kh03_r6_a1_acquisition import LoopbackBitgetV2FixtureServer


def _make_n_env(root: Path, *, executable_sha: str, store_tag: str = "n") -> Dict[str, Any]:
    dest = root / f"dest_{store_tag}"
    dest.mkdir(parents=True, exist_ok=True)
    fake_repo = root / f"repo_{store_tag}"
    fake_repo.mkdir(parents=True, exist_ok=True)
    sroot = root / f"ledger_{store_tag}"
    sid = f"qa_n_{store_tag}"
    now = datetime(2026, 11, 26, 7, 0, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    reg = build_r6_source_contract_registry()
    tr_p, priv_k, _ = provision_qa_isolated_trust_root(root / f"trust_{store_tag}", store_id=sid)
    tr_qa = load_r6_owner_trust_root(tr_p, expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)["trust_root"]
    provision_r6_durable_ledger(sroot, store_id=sid, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now)
    led = open_r6_durable_ledger({"ledger_store_root": str(sroot), "store_id": sid}, sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now)["ledger"]

    def _mk_vg_plan(gid: str, *, profile: str = "smoke", max_conc: int = 2, dur_sec: int = 120, disk_cap: int = 2_000_000) -> Tuple[Any, Any]:
        eps = ["classic_v2_contracts", "classic_v2_history_candles", "classic_v2_history_fund_rate", "classic_v2_ws_books1"] if profile == "dev" else ["classic_v2_contracts", "classic_v2_history_fund_rate"]
        ws_ch = ["books1"] if profile == "dev" else []
        gp = {
            "grant_id": gid,
            "issuer": "QA_ISOLATED_HARNESS",
            "trust_namespace": QA_ISOLATED_TRUST_NAMESPACE_R6,
            "issued_utc_ms": now_ms - 1000,
            "expires_utc_ms": now_ms + 600_000,
            "repo": "quocan050302/Build-app-crypto",
            "branch": MANDATORY_BRANCH_KH03_R6,
            "tested_executable_sha": executable_sha,
            "release_manifest_sha256": "c" * 64,
            "purpose": "QA_FIXTURE",
            "stage": "QA_LOOPBACK",
            "venue": APPROVED_VENUE_R6,
            "api_family": API_FAMILY_CLASSIC_V2,
            "symbol": APPROVED_SYMBOL_R6,
            "product_type": APPROVED_PRODUCT_TYPE_V2_R6,
            "allowed_endpoints": eps,
            "allowed_ws_channels": ws_ch,
            "window_start_ms": 1794000000000,
            "window_end_ms": 1794000300000,
            "destination_store_id": sid,
            "destination_root": str(dest),
            "max_session_duration_sec": dur_sec,
            "max_concurrency": max_conc,
            "caps": {
                "connections": 4,
                "requests": 40,
                "retries": 8,
                "subscriptions": 2,
                "messages": 500,
                "payload_bytes": 524_288,
                "decoded_bytes": 1_048_576,
                "pages": 10,
                "disk_bytes": disk_cap,
                "records": 2000,
            },
            "retention_policy": {"max_retention_days": 7, "git_commit_raw_payloads": False},
        }
        vg = verify_r6_owner_grant(
            sign_r6_grant_payload_with_ed25519(gp, priv_k),
            tr_qa,
            now,
            expected_executable_sha=executable_sha,
            expected_release_manifest_sha256="c" * 64,
            expected_store_id=sid,
            expected_stage="QA_LOOPBACK",
        )["verified_grant"]
        pl = build_r6_bound_capture_plan(vg, reg, {}, {"ok": True}, plan_id=f"pl_{gid}")["plan"]
        return vg, pl

    return {"dest": dest, "fake_repo": fake_repo, "sroot": sroot, "sid": sid, "now": now, "reg": reg, "tr_qa": tr_qa, "priv_k": priv_k, "led": led, "mk": _mk_vg_plan}


def run_kh03_r6_a1_corrective_controls_n15(*, executable_sha: str, run_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Executes ALL 15 P1-N paired controls (`N01..N15`) end-to-end against real acquisition & ledger paths."""
    receipts: List[Dict[str, Any]] = []
    term_traces: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}

    srv = LoopbackBitgetV2FixtureServer(window_start_ms=1794000000000, window_end_ms=1794000300000, bars_per_page=3).start()
    try:
        with tempfile.TemporaryDirectory(prefix="a1_corr_n15_") as td:
            root = Path(td)
            e = _make_n_env(root, executable_sha=executable_sha, store_tag="main")
            vols = {"dest": e["dest"], "ledger": e["sroot"]}

            def _good_samp() -> Dict[str, Any]:
                return collect_real_r6_resource_sample(vols, active_workers=1)

            def _run_clean_smoke(tag: str, env_d: Dict[str, Any] = e) -> Dict[str, Any]:
                vg, pl = env_d["mk"](f"G_POS_{tag}", profile="smoke")
                b = build_r6_a1_acquisition_session(
                    run_id=f"r_pos_{tag}", session_id=f"s_pos_{tag}", verified_grant=vg, plan=pl, registry=env_d["reg"],
                    ledger=env_d["led"], resource_sample_fn=lambda: collect_real_r6_resource_sample({"dest": env_d["dest"], "ledger": env_d["sroot"]}, active_workers=1),
                    expected_volumes={"dest": env_d["dest"], "ledger": env_d["sroot"]}, repo_root=env_d["fake_repo"],
                    logical_profile="smoke", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, actual_now=env_d["now"],
                )
                return run_r6_a1_acquisition_session(
                    ctx=b["context"], verified_grant=vg, plan=pl, registry=env_d["reg"], ledger=env_d["led"],
                    resource_sample_fn=lambda: collect_real_r6_resource_sample({"dest": env_d["dest"], "ledger": env_d["sroot"]}, active_workers=1),
                    expected_volumes={"dest": env_d["dest"], "ledger": env_d["sroot"]}, repo_root=env_d["fake_repo"],
                    utc_now_fn=lambda: env_d["now"],
                )

            # N01: Exception BEFORE claim -> 0 grant consumption, 0 workers, 0 custody mutation!
            def _n01_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                vg, pl = e["mk"]("G_N01_NEG")
                b = build_r6_a1_acquisition_session(run_id="r_n01", session_id="s_n01", verified_grant=vg, plan=pl, registry=e["reg"], ledger=e["led"], resource_sample_fn=_good_samp, expected_volumes=vols, repo_root=e["fake_repo"], logical_profile="smoke", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, actual_now=e["now"])
                def _boom_pre() -> Dict[str, Any]:
                    raise RuntimeError("PRECLAIM_TELEMETRY_EXPLOSION")
                rec = run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e["reg"], ledger=e["led"], resource_sample_fn=_boom_pre, expected_volumes=vols, repo_root=e["fake_repo"], utc_now_fn=lambda: e["now"])
                snap = e["led"].snapshot_grant(vg.grant_identity_key)
                cust_exists = (e["dest"] / "qa_fixture" / "r_n01").exists()
                ok = (rec["exit_code"] == 2 and rec["ledger_final_status"] == "NOT_CLAIMED" and snap is None and not cust_exists and len(rec["task_results"]) == 0)
                summary["preclaim_exception_zero_consumption"] = ok
                term_traces.append({"control": "N01", "status": rec["status"], "ledger_state": rec["ledger_final_status"], "grant_in_db": snap is not None})
                return {"preclaim_telemetry_raises": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(rec["violations"])}
            def _n01_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                r = _run_clean_smoke("N01")
                return {"clean_smoke": True}, {"positive_verified": r["status"] == TERMINAL_QA_PASS_STATUS_A1 and r["exit_code"] == 0, "unexpected_violations": len(r["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="N01", group="N", fault_kind="BASELINE_BUG_RED", target_symbol="run_r6_a1_acquisition_session", description="Pre-claim exception causes 0 grant consumption, 0 workers, 0 journal mutation", negative_fn=_n01_neg, positive_fn=_n01_pos, executable_sha=executable_sha, run_id=run_id))

            # N02: Claim commits then return raises -> reconciled & aborted ONCE via durable lookup!
            def _n02_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                e2 = _make_n_env(root, executable_sha=executable_sha, store_tag="n02")
                vg, pl = e2["mk"]("G_N02_NEG")
                b = build_r6_a1_acquisition_session(run_id="r_n02", session_id="s_n02", verified_grant=vg, plan=pl, registry=e2["reg"], ledger=e2["led"], resource_sample_fn=lambda: collect_real_r6_resource_sample({"dest": e2["dest"], "ledger": e2["sroot"]}, active_workers=1), expected_volumes={"dest": e2["dest"], "ledger": e2["sroot"]}, repo_root=e2["fake_repo"], logical_profile="smoke", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, actual_now=e2["now"])
                def _crash_after_commit(stage: str) -> None:
                    if stage == "post_claim_post_anchor":
                        raise RuntimeError("INJECTED_CLAIM_RETURN_CRASH")
                rec = run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e2["reg"], ledger=e2["led"], resource_sample_fn=lambda: collect_real_r6_resource_sample({"dest": e2["dest"], "ledger": e2["sroot"]}, active_workers=1), expected_volumes={"dest": e2["dest"], "ledger": e2["sroot"]}, repo_root=e2["fake_repo"], utc_now_fn=lambda: e2["now"], claim_crash_hook=_crash_after_commit)
                snap = e2["led"].snapshot_grant(vg.grant_identity_key)
                # Replay of same grant MUST be denied!
                rep = e2["led"].claim_r6_grant_session_atomic(vg, pl, "s_n02_replay", actual_now=e2["now"])
                ok = (rec["exit_code"] == 2 and rec["claim_status"] == "CLAIM_EXCEPTION_RECONCILED" and rec["ledger_terminal_verified_from_db"] is True and snap is not None and snap["state"] == "QUARANTINED" and rep["claimed"] is False)
                summary["postclaim_return_exception_reconciled_once"] = ok
                term_traces.append({"control": "N02", "claim_status": rec["claim_status"], "db_state": snap["state"] if snap else None, "replay_claimed": rep["claimed"]})
                return {"crash_stage": "after_commit_before_return"}, {"negative_enforced": ok, "violations_or_guards_triggered": len(rec["violations"]) + 1}
            def _n02_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                r = _run_clean_smoke("N02")
                return {"clean_claim_return": True}, {"positive_verified": r["status"] == TERMINAL_QA_PASS_STATUS_A1, "unexpected_violations": len(r["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="N02", group="N", fault_kind="BASELINE_BUG_RED", target_symbol="reconcile_r6_a1_claim_after_exception", description="Claim commits then return raises -> durable lookup reconciles & aborts grant once", negative_fn=_n02_neg, positive_fn=_n02_pos, executable_sha=executable_sha, run_id=run_id))

            # N03: Telemetry/init raises immediately post-claim -> terminal ledger verified (NEVER stuck CLAIMED)!
            def _n03_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                e3 = _make_n_env(root, executable_sha=executable_sha, store_tag="n03")
                vg, pl = e3["mk"]("G_N03_NEG")
                v3 = {"dest": e3["dest"], "ledger": e3["sroot"]}
                b = build_r6_a1_acquisition_session(run_id="r_n03", session_id="s_n03", verified_grant=vg, plan=pl, registry=e3["reg"], ledger=e3["led"], resource_sample_fn=lambda: collect_real_r6_resource_sample(v3, active_workers=1), expected_volumes=v3, repo_root=e3["fake_repo"], logical_profile="smoke", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, actual_now=e3["now"])
                rec = run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e3["reg"], ledger=e3["led"], resource_sample_fn=lambda: collect_real_r6_resource_sample(v3, active_workers=1), expected_volumes=v3, repo_root=e3["fake_repo"], utc_now_fn=lambda: e3["now"], post_claim_hook=lambda: (_ for _ in ()).throw(RuntimeError("IMMEDIATE_POSTCLAIM_BOOM")))
                snap = e3["led"].snapshot_grant(vg.grant_identity_key)
                ok = (rec["exit_code"] == 2 and rec["ledger_terminal_verified_from_db"] is True and snap is not None and snap["state"] == "QUARANTINED" and "IMMEDIATE_POSTCLAIM_BOOM" in str(rec["original_error"]))
                summary["immediate_postclaim_telemetry_exception_finalized"] = ok
                return {"post_claim_hook_raises": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(rec["violations"])}
            def _n03_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                r = _run_clean_smoke("N03")
                return {"no_post_claim_error": True}, {"positive_verified": r["status"] == TERMINAL_QA_PASS_STATUS_A1, "unexpected_violations": len(r["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="N03", group="N", fault_kind="BASELINE_BUG_RED", target_symbol="run_r6_a1_acquisition_session", description="Exception immediately after claim still finalizes ledger to terminal QUARANTINED", negative_fn=_n03_neg, positive_fn=_n03_pos, executable_sha=executable_sha, run_id=run_id))

            # N04: Partial task/worker failure -> all descendants accounted & reaped, truthful non-PASS!
            def _n04_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                vg, pl = e["mk"]("G_N04_NEG")
                b = build_r6_a1_acquisition_session(run_id="r_n04", session_id="s_n04", verified_grant=vg, plan=pl, registry=e["reg"], ledger=e["led"], resource_sample_fn=_good_samp, expected_volumes=vols, repo_root=e["fake_repo"], logical_profile="smoke", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, actual_now=e["now"])
                srv.fail_path = "/api/v2/mix/market/history-fund-rate"
                try:
                    rec = run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e["reg"], ledger=e["led"], resource_sample_fn=_good_samp, expected_volumes=vols, repo_root=e["fake_repo"], utc_now_fn=lambda: e["now"])
                finally:
                    srv.fail_path = None
                ok = (rec["exit_code"] == 2 and rec["tasks_failed_count"] >= 1 and rec["all_spawned_children_reaped_verified"] is True and rec["dataset_manifest"] is None and rec["ledger_terminal_verified_from_db"] is True)
                return {"fail_path": "/api/v2/mix/market/history-fund-rate"}, {"negative_enforced": ok, "violations_or_guards_triggered": len(rec["violations"])}
            def _n04_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                r = _run_clean_smoke("N04")
                return {"all_endpoints_healthy": True}, {"positive_verified": r["status"] == TERMINAL_QA_PASS_STATUS_A1 and r["all_spawned_children_reaped_verified"], "unexpected_violations": len(r["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="N04", group="N", fault_kind="INTEGRATION_FAULT", target_symbol="run_r6_a1_acquisition_session", description="Partial task failure accounts & reaps all descendants with truthful non-PASS", negative_fn=_n04_neg, positive_fn=_n04_pos, executable_sha=executable_sha, run_id=run_id))

            # N05: Writer exception mid-session -> all tasks terminal, zero late acceptance, ledger finalized!
            def _n05_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                e5 = _make_n_env(root, executable_sha=executable_sha, store_tag="n05")
                v5 = {"dest": e5["dest"], "ledger": e5["sroot"]}
                vg, pl = e5["mk"]("G_N05_NEG")
                b = build_r6_a1_acquisition_session(run_id="r_n05", session_id="s_n05", verified_grant=vg, plan=pl, registry=e5["reg"], ledger=e5["led"], resource_sample_fn=lambda: collect_real_r6_resource_sample(v5, active_workers=1), expected_volumes=v5, repo_root=e5["fake_repo"], logical_profile="smoke", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, actual_now=e5["now"])
                rec = run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e5["reg"], ledger=e5["led"], resource_sample_fn=lambda: collect_real_r6_resource_sample(v5, active_workers=1), expected_volumes=v5, repo_root=e5["fake_repo"], utc_now_fn=lambda: e5["now"], writer_per_commit_hook=lambda _env: (_ for _ in ()).throw(OSError("WRITER_DISK_IO_FAIL")))
                ok = (rec["exit_code"] == 2 and rec["committed_chunks_count"] == 0 and rec["ledger_final_status"] == "QUARANTINED" and rec["dataset_manifest"] is None)
                return {"writer_raises_on_first_chunk": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(rec["violations"])}
            def _n05_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                r = _run_clean_smoke("N05")
                return {"clean_writer": True}, {"positive_verified": r["status"] == TERMINAL_QA_PASS_STATUS_A1, "unexpected_violations": len(r["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="N05", group="N", fault_kind="INTEGRATION_FAULT", target_symbol="run_r6_a1_single_custody_writer", description="Writer exception aborts intake with zero late acceptance & terminal quarantine", negative_fn=_n05_neg, positive_fn=_n05_pos, executable_sha=executable_sha, run_id=run_id))

            # N06: Telemetry raises INSIDE finally: -> ledger STILL finalized & DB-verified! (Directly fixes RED-N1a!)
            def _n06_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                vg, pl = e["mk"]("G_N06_NEG")
                calls = [0]
                def _flaky() -> Dict[str, Any]:
                    calls[0] += 1
                    if calls[0] >= 3:  # 1=build, 2=pre-claim, 3=finally!
                        raise RuntimeError("INJECTED_FINALLY_TELEMETRY_ERROR")
                    return _good_samp()
                b = build_r6_a1_acquisition_session(run_id="r_n06", session_id="s_n06", verified_grant=vg, plan=pl, registry=e["reg"], ledger=e["led"], resource_sample_fn=_flaky, expected_volumes=vols, repo_root=e["fake_repo"], logical_profile="smoke", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, actual_now=e["now"])
                rec = run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e["reg"], ledger=e["led"], resource_sample_fn=_flaky, expected_volumes=vols, repo_root=e["fake_repo"], utc_now_fn=lambda: e["now"], cancel_between_claim_and_first_attempt=True)
                snap = e["led"].snapshot_grant(vg.grant_identity_key)
                ok = (rec["exit_code"] == 2 and rec["ledger_terminal_verified_from_db"] is True and snap is not None and snap["state"] in ("SPENT", "QUARANTINED") and any("FINALIZE_TELEMETRY_ERROR" in ce for ce in rec["cleanup_errors"]))
                summary["finally_telemetry_exception_still_finalizes_ledger"] = ok
                term_traces.append({"control": "N06", "db_state": snap["state"] if snap else None, "cleanup_errors": rec["cleanup_errors"]})
                return {"finally_telemetry_raises": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(rec["cleanup_errors"]) + len(rec["violations"])}
            def _n06_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                r = _run_clean_smoke("N06")
                return {"healthy_finally_telemetry": True}, {"positive_verified": r["status"] == TERMINAL_QA_PASS_STATUS_A1 and len(r["cleanup_errors"]) == 0, "unexpected_violations": len(r["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="N06", group="N", fault_kind="BASELINE_BUG_RED", target_symbol="finalize_r6_a1_session_safely", description="Telemetry exception inside finally: still finalizes & DB-verifies grant ledger", negative_fn=_n06_neg, positive_fn=_n06_pos, executable_sha=executable_sha, run_id=run_id))

            # N07: Original execution error + finalization cleanup error -> BOTH preserved separately!
            def _n07_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                e7 = _make_n_env(root, executable_sha=executable_sha, store_tag="n07")
                v7 = {"dest": e7["dest"], "ledger": e7["sroot"]}
                vg, pl = e7["mk"]("G_N07_NEG")
                calls = [0]
                def _flaky7() -> Dict[str, Any]:
                    calls[0] += 1
                    if calls[0] >= 3:
                        raise RuntimeError("CLEANUP_TELEMETRY_ERR_N07")
                    return collect_real_r6_resource_sample(v7, active_workers=1)
                b = build_r6_a1_acquisition_session(run_id="r_n07", session_id="s_n07", verified_grant=vg, plan=pl, registry=e7["reg"], ledger=e7["led"], resource_sample_fn=_flaky7, expected_volumes=v7, repo_root=e7["fake_repo"], logical_profile="smoke", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, actual_now=e7["now"])
                rec = run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e7["reg"], ledger=e7["led"], resource_sample_fn=_flaky7, expected_volumes=v7, repo_root=e7["fake_repo"], utc_now_fn=lambda: e7["now"], post_claim_hook=lambda: (_ for _ in ()).throw(ValueError("ORIGINAL_SETTLEMENT_ERR_N07")))
                ok = (
                    rec["exit_code"] == 2
                    and "ORIGINAL_SETTLEMENT_ERR_N07" in str(rec["original_error"])
                    and any("CLEANUP_TELEMETRY_ERR_N07" in ce for ce in rec["cleanup_errors"])
                    and rec["ledger_terminal_verified_from_db"] is True
                )
                summary["original_and_cleanup_errors_preserved_separately"] = ok
                return {"both_original_and_cleanup_errors": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(rec["cleanup_errors"]) + 1}
            def _n07_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                r = _run_clean_smoke("N07")
                return {"no_errors": True}, {"positive_verified": r["original_error"] is None and len(r["cleanup_errors"]) == 0 and r["exit_code"] == 0, "unexpected_violations": len(r["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="N07", group="N", fault_kind="INTEGRATION_FAULT", target_symbol="finalize_r6_a1_session_safely", description="Original error & cleanup error both preserved separately without masking", negative_fn=_n07_neg, positive_fn=_n07_pos, executable_sha=executable_sha, run_id=run_id))

            # N08: KeyboardInterrupt -> bounded cleanup runs, DB finalized, NO hidden PASS, re-raises KeyboardInterrupt!
            def _n08_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                vg, pl = e["mk"]("G_N08_NEG")
                b = build_r6_a1_acquisition_session(run_id="r_n08", session_id="s_n08", verified_grant=vg, plan=pl, registry=e["reg"], ledger=e["led"], resource_sample_fn=_good_samp, expected_volumes=vols, repo_root=e["fake_repo"], logical_profile="smoke", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, actual_now=e["now"])
                sink: List[Dict[str, Any]] = []
                reraised = False
                try:
                    run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e["reg"], ledger=e["led"], resource_sample_fn=_good_samp, expected_volumes=vols, repo_root=e["fake_repo"], utc_now_fn=lambda: e["now"], post_claim_hook=lambda: (_ for _ in ()).throw(KeyboardInterrupt("CTRL_C_SIM")), receipt_sink=sink)
                except KeyboardInterrupt:
                    reraised = True
                snap = e["led"].snapshot_grant(vg.grant_identity_key)
                ok = (reraised is True and len(sink) == 1 and sink[0]["exit_code"] == 2 and sink[0]["ledger_terminal_verified_from_db"] is True and snap is not None and snap["state"] in ("SPENT", "QUARANTINED"))
                summary["keyboard_interrupt_cancel_cleans_up_no_hidden_pass"] = ok
                return {"keyboard_interrupt_injected": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(sink[0]["violations"]) if sink else 0}
            def _n08_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                r = _run_clean_smoke("N08")
                return {"no_interrupt": True}, {"positive_verified": r["status"] == TERMINAL_QA_PASS_STATUS_A1, "unexpected_violations": len(r["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="N08", group="N", fault_kind="INTEGRATION_FAULT", target_symbol="run_r6_a1_acquisition_session", description="KeyboardInterrupt runs bounded cleanup, finalizes DB, never hides PASS & re-raises", negative_fn=_n08_neg, positive_fn=_n08_pos, executable_sha=executable_sha, run_id=run_id))

            # N09: Process crash after claim / mid-write / pre-finalize -> restart reconciliation, one-shot SPENT stays SPENT, uncertain retained!
            def _n09_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                e9 = _make_n_env(root, executable_sha=executable_sha, store_tag="n09")
                vg, pl = e9["mk"]("G_N09_CRASH")
                lease = e9["led"].claim_r6_grant_session_atomic(vg, pl, "s_n09_crash", actual_now=e9["now"])["lease"]
                dl = R6A1DiskBudgetLedger(store_root=e9["sroot"], volume_id=_volume_identity_for_path_corr(e9["dest"]), global_cap_bytes=500_000, safe_floor_bytes=0)
                dl.register_session_budget(session_id="s_n09_crash", session_cap_bytes=500_000, emergency_audit_reserve_bytes=0, manifest_and_wal_reserve_bytes=0)
                dl.reserve_r6_disk_bytes(session_id="s_n09_crash", reservation_id="r_mid", write_class="chunk", requested_bytes=32_768)
                # Simulate restart: open fresh ledger + disk ledger & run coordinated recovery!
                led2 = open_r6_durable_ledger({"ledger_store_root": str(e9["sroot"]), "store_id": e9["sid"]}, e9["sid"], expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=e9["now"])["ledger"]
                dl2 = R6A1DiskBudgetLedger(store_root=e9["sroot"], volume_id=_volume_identity_for_path_corr(e9["dest"]), global_cap_bytes=500_000, safe_floor_bytes=0)
                rec = recover_r6_a1_incomplete_sessions(led2, actual_now=e9["now"], disk_budget_ledger=dl2)
                snap = led2.snapshot_grant(vg.grant_identity_key)
                # Re-claiming same one-shot grant MUST be denied!
                rep = led2.claim_r6_grant_session_atomic(vg, pl, "s_n09_replay", actual_now=e9["now"])
                ok = (rec["recovered"] is True and snap["state"] == "SPENT_CRASH_RECOVERED" and rep["claimed"] is False and rec["disk_budget_recovery"]["uncertain_bytes_retained_total"] == 32_768)
                summary["crash_restart_one_shot_spent_remains_spent"] = ok
                return {"hard_crash_after_claim": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(rep["violations"]) + 1}
            def _n09_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                e9p = _make_n_env(root, executable_sha=executable_sha, store_tag="n09p")
                r = _run_clean_smoke("N09", env_d=e9p)
                rec = recover_r6_a1_incomplete_sessions(e9p["led"], actual_now=e9p["now"])
                return {"clean_restart_recovery": True}, {"positive_verified": (r["exit_code"] == 0 and rec["recovered"] is True and rec["ledger_recovery"]["status"] == "CLEAN_NO_INTERRUPTED_SESSIONS"), "unexpected_violations": 0}
            receipts.append(execute_corrective_fault_pair(control_id="N09", group="N", fault_kind="INTEGRATION_FAULT", target_symbol="recover_r6_a1_incomplete_sessions", description="Crash restart marks one-shot grant spent, retains uncertain bytes & blocks replay", negative_fn=_n09_neg, positive_fn=_n09_pos, executable_sha=executable_sha, run_id=run_id))

            # N10: Competing finalizers -> CAS idempotent single terminal transition!
            def _n10_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                e10 = _make_n_env(root, executable_sha=executable_sha, store_tag="n10")
                vg, pl = e10["mk"]("G_N10_RACE")
                lease = e10["led"].claim_r6_grant_session_atomic(vg, pl, "s_n10_race", actual_now=e10["now"])["lease"]
                bar = threading.Barrier(4, timeout=5.0)
                def _fin(i: int) -> Dict[str, Any]:
                    bar.wait()
                    return e10["led"].finalize_r6_session(lease, quarantine=(i % 2 == 1), reason=f"COMPETITOR_{i}", actual_now=e10["now"])
                with ThreadPoolExecutor(max_workers=4) as pool:
                    res_list = list(pool.map(_fin, range(4)))
                first_winners = [r for r in res_list if r.get("finalized") is True and r.get("idempotent_already_finalized") is False]
                idempotent_dups = [r for r in res_list if r.get("finalized") is True and r.get("idempotent_already_finalized") is True]
                all_verified = all(r.get("terminal_verified_from_db") is True for r in res_list)
                ok = (len(first_winners) == 1 and len(idempotent_dups) == 3 and all_verified)
                summary["competing_finalizers_cas_idempotent"] = ok
                return {"competing_finalizers": 4}, {"negative_enforced": ok, "violations_or_guards_triggered": len(idempotent_dups)}
            def _n10_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                vg, pl = e["mk"]("G_N10_POS")
                lease = e["led"].claim_r6_grant_session_atomic(vg, pl, "s_n10_pos", actual_now=e["now"])["lease"]
                r = e["led"].finalize_r6_session(lease, quarantine=False, reason="SINGLE_CLEAN", actual_now=e["now"])
                return {"single_finalize": True}, {"positive_verified": (r["finalized"] is True and r["idempotent_already_finalized"] is False and r["terminal_verified_from_db"] is True), "unexpected_violations": len(r["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="N10", group="N", fault_kind="INTEGRATION_FAULT", target_symbol="R6DurableGrantLedger.finalize_r6_session", description="4 competing finalizers produce exactly 1 CAS terminal transition", negative_fn=_n10_neg, positive_fn=_n10_pos, executable_sha=executable_sha, run_id=run_id))

            # N11: Unreaped worker -> quarantine & hard block new session via STORE_UNPROVEN_TERMINAL!
            def _n11_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                e11 = _make_n_env(root, executable_sha=executable_sha, store_tag="n11")
                v11 = {"dest": e11["dest"], "ledger": e11["sroot"]}
                vg, pl = e11["mk"]("G_N11_NEG")
                b = build_r6_a1_acquisition_session(run_id="r_n11", session_id="s_n11", verified_grant=vg, plan=pl, registry=e11["reg"], ledger=e11["led"], resource_sample_fn=lambda: collect_real_r6_resource_sample(v11, active_workers=1), expected_volumes=v11, repo_root=e11["fake_repo"], logical_profile="smoke", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, actual_now=e11["now"])
                rec = run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e11["reg"], ledger=e11["led"], resource_sample_fn=lambda: collect_real_r6_resource_sample(v11, active_workers=1), expected_volumes=v11, repo_root=e11["fake_repo"], utc_now_fn=lambda: e11["now"], simulate_unreaped_worker=True)
                # New session on same store MUST be hard-blocked!
                vg2, pl2 = e11["mk"]("G_N11_NEXT")
                cl_next = e11["led"].claim_r6_grant_session_atomic(vg2, pl2, "s_n11_next", actual_now=e11["now"])
                op_next = open_r6_durable_ledger({"ledger_store_root": str(e11["sroot"]), "store_id": e11["sid"]}, e11["sid"], expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=e11["now"])
                ok = (rec["exit_code"] == 2 and rec["unreaped_worker_detected"] is True and cl_next["claimed"] is False and op_next["opened"] is False and op_next["status"] == "BLOCKED_STORE_UNPROVEN_TERMINAL")
                summary["unreaped_worker_quarantines_and_blocks_new_session"] = ok
                return {"unreaped_worker_simulated": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(cl_next["violations"]) + len(op_next["violations"])}
            def _n11_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                r = _run_clean_smoke("N11")
                return {"all_workers_reaped": True}, {"positive_verified": r["all_spawned_children_reaped_verified"] is True and r["unreaped_worker_detected"] is False and r["exit_code"] == 0, "unexpected_violations": len(r["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="N11", group="N", fault_kind="INTEGRATION_FAULT", target_symbol="finalize_r6_a1_session_safely", description="Unreaped worker quarantines session & hard-blocks future store use", negative_fn=_n11_neg, positive_fn=_n11_pos, executable_sha=executable_sha, run_id=run_id))

            # N12: Failure to persist terminal DB receipt -> classified UNPROVEN_TERMINAL, store blocked!
            def _n12_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                e12 = _make_n_env(root, executable_sha=executable_sha, store_tag="n12")
                v12 = {"dest": e12["dest"], "ledger": e12["sroot"]}
                vg, pl = e12["mk"]("G_N12_NEG")
                b = build_r6_a1_acquisition_session(run_id="r_n12", session_id="s_n12", verified_grant=vg, plan=pl, registry=e12["reg"], ledger=e12["led"], resource_sample_fn=lambda: collect_real_r6_resource_sample(v12, active_workers=1), expected_volumes=v12, repo_root=e12["fake_repo"], logical_profile="smoke", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, actual_now=e12["now"])
                rec = run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e12["reg"], ledger=e12["led"], resource_sample_fn=lambda: collect_real_r6_resource_sample(v12, active_workers=1), expected_volumes=v12, repo_root=e12["fake_repo"], utc_now_fn=lambda: e12["now"], inject_db_finalize_error=True)
                op_next = open_r6_durable_ledger({"ledger_store_root": str(e12["sroot"]), "store_id": e12["sid"]}, e12["sid"], expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=e12["now"])
                ok = (rec["exit_code"] == 2 and rec["status"] == "FAILED_UNPROVEN_TERMINAL_STATE" and rec["ledger_final_status"] == "UNPROVEN_TERMINAL" and op_next["opened"] is False)
                summary["persist_failure_classified_unproven_terminal"] = ok
                return {"inject_db_finalize_error": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(rec["cleanup_errors"]) + len(op_next["violations"])}
            def _n12_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                r = _run_clean_smoke("N12")
                return {"clean_db_finalize": True}, {"positive_verified": r["ledger_terminal_verified_from_db"] is True and r["ledger_final_status"] == "SPENT", "unexpected_violations": len(r["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="N12", group="N", fault_kind="INTEGRATION_FAULT", target_symbol="R6DurableGrantLedger.finalize_r6_session", description="DB finalization persistence error classifies UNPROVEN_TERMINAL & blocks store reuse", negative_fn=_n12_neg, positive_fn=_n12_pos, executable_sha=executable_sha, run_id=run_id))

            # N13: Durable FINALIZED/SPENT after ABORTED is NOT acquisition PASS + full positive QA E2E dev chain!
            def _n13_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                disp = derive_r6_a1_terminal_disposition(
                    has_context=True, qa_loopback_mode=True, expected_tasks=2, completed_tasks=1, failed_tasks=1,
                    committed_chunks_count=1, total_raw_bytes=100, total_norm_rows=1, custody_readonly_valid=True,
                    all_children_reaped_verified=True, unreaped_worker_detected=False, ledger_final_status="SPENT",
                    ledger_terminal_verified_from_db=True, unproven_terminal=False, quarantined=False, cancelled=True,
                    deadline_expired=False, original_error=None, cleanup_errors=(), violations=(),
                )
                ok = (disp["ledger_terminal_state"] == "SPENT" and disp["acquisition_succeeded"] is False and disp["exit_code"] == 2 and "ABORTED" in disp["acquisition_status"])
                summary["durable_finalized_after_aborted_not_acquisition_pass"] = ok
                return {"ledger_spent_but_cancelled": True}, {"negative_enforced": ok, "violations_or_guards_triggered": 1 if ok else 0}
            def _n13_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                vg, pl = e["mk"]("G_N13_DEV", profile="dev", max_conc=2)
                b = build_r6_a1_acquisition_session(run_id="r_n13_dev", session_id="s_n13_dev", verified_grant=vg, plan=pl, registry=e["reg"], ledger=e["led"], resource_sample_fn=_good_samp, expected_volumes=vols, repo_root=e["fake_repo"], logical_profile="dev", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, qa_ws_base_url=srv.ws_base_url, actual_now=e["now"])
                rec = run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e["reg"], ledger=e["led"], resource_sample_fn=_good_samp, expected_volumes=vols, repo_root=e["fake_repo"], utc_now_fn=lambda: e["now"])
                ok = (
                    rec["status"] == TERMINAL_QA_PASS_STATUS_A1
                    and rec["exit_code"] == 0
                    and rec["committed_chunks_count"] >= 4
                    and rec["custody_readonly_valid"] is True
                    and rec["dataset_manifest"] is not None
                    and rec["accepted_real_observed_records"] == 0
                    and rec["external_market_requests_attempted"] == 0
                )
                summary["positive_qa_e2e_full_chain_verified"] = ok
                return {"full_qa_dev_e2e": True}, {"positive_verified": ok, "unexpected_violations": len(rec["violations"]), "committed_chunks": rec["committed_chunks_count"]}
            receipts.append(execute_corrective_fault_pair(control_id="N13", group="N", fault_kind="INTEGRATION_FAULT", target_symbol="derive_r6_a1_terminal_disposition", description="Durable SPENT after ABORTED != PASS; positive full QA DEV E2E chain verified", negative_fn=_n13_neg, positive_fn=_n13_pos, executable_sha=executable_sha, run_id=run_id))

            # N14: Expired deadline during session/cleanup -> bounded cleanup executes, zero late I/O!
            def _n14_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                vg, pl = e["mk"]("G_N14_EXP", dur_sec=1)
                b = build_r6_a1_acquisition_session(run_id="r_n14", session_id="s_n14", verified_grant=vg, plan=pl, registry=e["reg"], ledger=e["led"], resource_sample_fn=_good_samp, expected_volumes=vols, repo_root=e["fake_repo"], logical_profile="smoke", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, actual_now=e["now"])
                # Pass post-claim sleep exceeding 1s deadline so deadline expires BEFORE first attempt!
                hits_before = srv.http_hits
                rec = run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e["reg"], ledger=e["led"], resource_sample_fn=_good_samp, expected_volumes=vols, repo_root=e["fake_repo"], utc_now_fn=lambda: e["now"], post_claim_hook=lambda: time.sleep(1.1))
                hits_after = srv.http_hits
                ok = (rec["exit_code"] == 2 and rec["status"] == "ABORTED_DEADLINE_EXPIRED" and hits_after == hits_before and rec["ledger_terminal_verified_from_db"] is True)
                summary["expired_deadline_cleanup_bounded_no_late_io"] = ok
                return {"expired_before_first_attempt": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(rec["violations"])}
            def _n14_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                r = _run_clean_smoke("N14")
                return {"within_deadline": True}, {"positive_verified": r["status"] == TERMINAL_QA_PASS_STATUS_A1, "unexpected_violations": len(r["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="N14", group="N", fault_kind="INTEGRATION_FAULT", target_symbol="run_r6_a1_acquisition_session", description="Expired deadline runs bounded cleanup with zero late I/O & terminal ledger", negative_fn=_n14_neg, positive_fn=_n14_pos, executable_sha=executable_sha, run_id=run_id))

            # N15: Compound L+M+N fault: W4 dev profile + writer ENOSPC under budget pressure + telemetry error in finally!
            def _n15_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                e15 = _make_n_env(root, executable_sha=executable_sha, store_tag="n15")
                v15 = {"dest": e15["dest"], "ledger": e15["sroot"]}
                vg, pl = e15["mk"]("G_N15_COMP", profile="dev", max_conc=4)
                fail_finally = [False]
                def _comp_samp() -> Dict[str, Any]:
                    if fail_finally[0]:
                        raise RuntimeError("N15_COMPOUND_FINALLY_TELEMETRY_FAIL")
                    return collect_real_r6_resource_sample(v15, active_workers=2)
                b = build_r6_a1_acquisition_session(run_id="r_n15", session_id="s_n15", verified_grant=vg, plan=pl, registry=e15["reg"], ledger=e15["led"], resource_sample_fn=_comp_samp, expected_volumes=v15, repo_root=e15["fake_repo"], logical_profile="dev", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, qa_ws_base_url=srv.ws_base_url, actual_now=e15["now"])
                def _writer_boom(_env: Any) -> None:
                    fail_finally[0] = True
                    raise OSError(errno.ENOSPC, "N15_COMPOUND_WRITER_ENOSPC")
                rec = run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e15["reg"], ledger=e15["led"], resource_sample_fn=_comp_samp, expected_volumes=v15, repo_root=e15["fake_repo"], utc_now_fn=lambda: e15["now"], writer_per_commit_hook=_writer_boom)
                snap = e15["led"].snapshot_grant(vg.grant_identity_key)
                ok = (
                    rec["exit_code"] == 2
                    and rec["dataset_manifest"] is None
                    and rec["ledger_final_status"] == "QUARANTINED"
                    and rec["ledger_terminal_verified_from_db"] is True
                    and snap is not None and snap["state"] == "QUARANTINED"
                    and rec["all_spawned_children_reaped_verified"] is True
                    and any("N15_COMPOUND_FINALLY_TELEMETRY_FAIL" in ce for ce in rec["cleanup_errors"])
                    and any("N15_COMPOUND_WRITER_ENOSPC" in v for v in rec["violations"])
                )
                summary["compound_l_m_n_fault_quarantined_conserved"] = ok
                term_traces.append({"control": "N15_COMPOUND", "status": rec["status"], "ledger_state": rec["ledger_final_status"], "cleanup_errors_count": len(rec["cleanup_errors"]), "all_reaped": rec["all_spawned_children_reaped_verified"]})
                return {"compound_w4_writer_enospc_finally_telemetry_fail": True}, {"negative_enforced": ok, "violations_or_guards_triggered": len(rec["violations"]) + len(rec["cleanup_errors"])}
            def _n15_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
                e15p = _make_n_env(root, executable_sha=executable_sha, store_tag="n15p")
                v15p = {"dest": e15p["dest"], "ledger": e15p["sroot"]}
                vg, pl = e15p["mk"]("G_N15_POS", profile="dev", max_conc=4)
                b = build_r6_a1_acquisition_session(run_id="r_n15p", session_id="s_n15p", verified_grant=vg, plan=pl, registry=e15p["reg"], ledger=e15p["led"], resource_sample_fn=lambda: collect_real_r6_resource_sample(v15p, active_workers=2), expected_volumes=v15p, repo_root=e15p["fake_repo"], logical_profile="dev", qa_loopback_mode=True, qa_http_base_url=srv.http_base_url, qa_ws_base_url=srv.ws_base_url, actual_now=e15p["now"])
                rec = run_r6_a1_acquisition_session(ctx=b["context"], verified_grant=vg, plan=pl, registry=e15p["reg"], ledger=e15p["led"], resource_sample_fn=lambda: collect_real_r6_resource_sample(v15p, active_workers=2), expected_volumes=v15p, repo_root=e15p["fake_repo"], utc_now_fn=lambda: e15p["now"])
                return {"w4_clean_dev_e2e": True}, {"positive_verified": (rec["status"] == TERMINAL_QA_PASS_STATUS_A1 and rec["exit_code"] == 0 and rec["all_spawned_children_reaped_verified"]), "unexpected_violations": len(rec["violations"])}
            receipts.append(execute_corrective_fault_pair(control_id="N15", group="N", fault_kind="INTEGRATION_FAULT", target_symbol="run_r6_a1_acquisition_session", description="Compound L+M+N fault (W4 + writer ENOSPC + finally telemetry fail) -> quarantined, 0 orphans, no fake PASS", negative_fn=_n15_neg, positive_fn=_n15_pos, executable_sha=executable_sha, run_id=run_id))
    finally:
        srv.stop()

    return receipts, term_traces, summary


def test_kh03_r6_a1_corrective_n01_to_n15_paired_controls() -> None:
    recs, traces, summ = run_kh03_r6_a1_corrective_controls_n15(executable_sha="c" * 40, run_id="pytest_n15")
    assert len(recs) == 15
    assert all(r["status"] == "PASS" for r in recs), [r["control_id"] for r in recs if r["status"] != "PASS"]
    assert len(traces) >= 3
    assert summ["preclaim_exception_zero_consumption"] is True
    assert summ["postclaim_return_exception_reconciled_once"] is True
    assert summ["immediate_postclaim_telemetry_exception_finalized"] is True
    assert summ["competing_finalizers_cas_idempotent"] is True
    assert summ["persist_failure_classified_unproven_terminal"] is True
    assert summ["finally_telemetry_exception_still_finalizes_ledger"] is True
    assert summ["original_and_cleanup_errors_preserved_separately"] is True
    assert summ["keyboard_interrupt_cancel_cleans_up_no_hidden_pass"] is True
    assert summ["unreaped_worker_quarantines_and_blocks_new_session"] is True
    assert summ["expired_deadline_cleanup_bounded_no_late_io"] is True
    assert summ["compound_l_m_n_fault_quarantined_conserved"] is True
    assert summ["crash_restart_one_shot_spent_remains_spent"] is True
    assert summ["durable_finalized_after_aborted_not_acquisition_pass"] is True
    assert summ["positive_qa_e2e_full_chain_verified"] is True
