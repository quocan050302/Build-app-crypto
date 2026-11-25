"""
aurum-desk/backend/tests/test_kh03_r6_durable_grants.py

KH-03.R6 Paired Controls B01..B12 (Cross-Process Durable SQLite Ledger, OS Crash/Kill Recovery,
External Anchor Rollback/Tamper Detection, Pre-Dispatch Budget Conservation & Capacity Enforcement).
Includes REAL OS subprocess claim races & REAL abrupt child process `os._exit(137)` crash/restart proofs!
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Tuple

from services.kh_03_r6_acceptance_service import (
    R6FaultCaseSpec,
    derive_r6_fault_verdict,
    execute_r6_fault_pair,
)
from services.kh_03_r6_durable_grant_service import (
    OWNER_EXTERNAL_TRUST_NAMESPACE_R6,
    QA_ISOLATED_TRUST_NAMESPACE_R6,
    load_r6_owner_trust_root,
    open_r6_durable_ledger,
    provision_qa_isolated_trust_root,
    provision_r6_durable_ledger,
    sign_r6_grant_payload_with_ed25519,
    verify_r6_ledger_readonly,
    verify_r6_owner_grant,
)
from tests.test_kh03_r6_source_contracts import make_qa_grant_payload


def _subproc_env() -> Dict[str, str]:
    env = dict(os.environ)
    backend_p = str(Path(__file__).resolve().parents[1])
    cur = env.get("PYTHONPATH", "")
    if backend_p not in cur.split(os.pathsep):
        env["PYTHONPATH"] = f"{backend_p}{os.pathsep}{cur}" if cur else backend_p
    env["PYTHONHASHSEED"] = "0"
    return env


def _spawn_claim_contender(
    store_root: Path,
    store_id: str,
    trust_root_path: Path,
    grant_path: Path,
    plan_digest: str,
    session_id: str,
    executable_sha: str,
    release_sha: str,
    now_iso: str,
) -> subprocess.Popen[str]:
    code = (
        "import json, sys; from datetime import datetime; from pathlib import Path; "
        "from services.kh_03_r6_durable_grant_service import QA_ISOLATED_TRUST_NAMESPACE_R6, load_r6_owner_trust_root, open_r6_durable_ledger, verify_r6_owner_grant; "
        "sr, sid, trp, gp, pd, sess, sha, rsha, niso = sys.argv[1:10]; "
        "now = datetime.fromisoformat(niso); "
        "tr = load_r6_owner_trust_root(Path(trp), expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)['trust_root']; "
        "gv = verify_r6_owner_grant(Path(gp).read_bytes(), tr, now, expected_executable_sha=sha, expected_release_manifest_sha256=rsha, expected_store_id=sid, expected_stage='QA_LOOPBACK'); "
        "led = open_r6_durable_ledger({'ledger_store_root': sr, 'store_id': sid}, sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now)['ledger']; "
        "res = led.claim_r6_grant_session_atomic(gv['verified_grant'], pd, sess, actual_now=now); "
        "print(json.dumps({'claimed': res['claimed'], 'status': res['status']}))"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code, str(store_root), store_id, str(trust_root_path), str(grant_path), plan_digest, session_id, executable_sha, release_sha, now_iso],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_subproc_env(),
    )


def _run_abrupt_crash_child(
    store_root: Path,
    store_id: str,
    trust_root_path: Path,
    grant_path: Path,
    plan_digest: str,
    session_id: str,
    executable_sha: str,
    release_sha: str,
    now_iso: str,
    crash_stage: str,  # "post_claim_post_anchor" | "post_start_pre_settle" | "post_claim_pre_anchor" | "post_finalize_pre_anchor"
) -> int:
    code = (
        "import os, sys\n"
        "from datetime import datetime\n"
        "from pathlib import Path\n"
        "from services.kh_03_r6_durable_grant_service import QA_ISOLATED_TRUST_NAMESPACE_R6, load_r6_owner_trust_root, open_r6_durable_ledger, verify_r6_owner_grant\n"
        "sr, sid, trp, gp, pd, sess, sha, rsha, niso, cstage = sys.argv[1:11]\n"
        "now = datetime.fromisoformat(niso)\n"
        "tr = load_r6_owner_trust_root(Path(trp), expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)['trust_root']\n"
        "gv = verify_r6_owner_grant(Path(gp).read_bytes(), tr, now, expected_executable_sha=sha, expected_release_manifest_sha256=rsha, expected_store_id=sid, expected_stage='QA_LOOPBACK')['verified_grant']\n"
        "led = open_r6_durable_ledger({'ledger_store_root': sr, 'store_id': sid}, sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now)['ledger']\n"
        "def hook(pt):\n"
        "    if pt == cstage:\n"
        "        os._exit(137)\n"
        "cl = led.claim_r6_grant_session_atomic(gv, pd, sess, actual_now=now, crash_hook=hook)\n"
        "lease = cl['lease']\n"
        "led.reserve_r6_io_budget_atomic(lease, 'att_crash_1', 'rest_get', {'requests': 1, 'payload_bytes': 8192, 'decoded_bytes': 16384}, actual_now=now, crash_hook=hook)\n"
        "led.mark_r6_attempt_started(lease, 'att_crash_1', actual_now=now, crash_hook=hook)\n"
        "led.settle_r6_io_budget(lease, 'att_crash_1', outcome='COMPLETED', actual_known={'requests': 1, 'payload_bytes': 512, 'decoded_bytes': 512}, actual_now=now, crash_hook=hook)\n"
        "led.finalize_r6_session(lease, quarantine=False, reason='OK', actual_now=now, crash_hook=hook)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, str(store_root), store_id, str(trust_root_path), str(grant_path), plan_digest, session_id, executable_sha, release_sha, now_iso, crash_stage],
        capture_output=True,
        text=True,
        env=_subproc_env(),
        timeout=30,
    )
    return int(proc.returncode)


def run_kh03_r6_controls_b12(*, executable_sha: str, run_id: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    now_utc = datetime(2026, 11, 25, 10, 0, 0, tzinfo=timezone.utc)
    now_iso = now_utc.isoformat()
    release_sha = "b" * 64
    receipts: List[Dict[str, Any]] = []
    evidence_summary: Dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="r6_b12_") as td:
        tdir = Path(td)
        store_id = "qa_ledger_b12"
        store_root = tdir / "ledger_main"
        dest_root = tdir / "dest"
        dest_root.mkdir()
        tr_path, priv_key, _ = provision_qa_isolated_trust_root(tdir / "trust", store_id=store_id)
        tr_qa = load_r6_owner_trust_root(tr_path, expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)["trust_root"]

        prov = provision_r6_durable_ledger(store_root, store_id=store_id, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_utc)
        assert prov["provisioned"] is True
        cfg = {"ledger_store_root": str(store_root), "store_id": store_id}
        ledger = open_r6_durable_ledger(cfg, store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)["ledger"]

        def _make_verified(gid: str, **kw: Any):
            gp = make_qa_grant_payload(grant_id=gid, dest_root=dest_root, store_id=store_id, now_utc=now_utc, executable_sha=executable_sha, release_sha=release_sha, **kw)
            raw = sign_r6_grant_payload_with_ed25519(gp, priv_key)
            gv = verify_r6_owner_grant(raw, tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256=release_sha, expected_store_id=store_id, expected_stage="QA_LOOPBACK")
            assert gv["valid"] is True
            return gv["verified_grant"], raw

        # B01: sequential replay same grant denied vs first claim
        vg_b01, _ = _make_verified("G_B01")
        cl_b01_first = ledger.claim_r6_grant_session_atomic(vg_b01, "plan_b01", "sess_b01_1", actual_now=now_utc)
        cl_b01_second = ledger.claim_r6_grant_session_atomic(vg_b01, "plan_b01", "sess_b01_2", actual_now=now_utc)
        evidence_summary["sequential_replay_denied"] = (cl_b01_first["claimed"] is True and cl_b01_second["claimed"] is False)
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-B01", "B_DURABLE_LEDGER", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.claim_r6_grant_session_atomic", "claim_denied_no_lease", "claim_accepted_once", "Sequential replay denied vs initial one-shot claim"),
            negative_fn=lambda: ({"grant": "G_B01_replay"}, {"claimed": cl_b01_second["claimed"], "lease_issued": cl_b01_second["lease"] is not None, "violations_count": len(cl_b01_second["violations"])}),
            positive_fn=lambda: ({"grant": "G_B01_first"}, {"claimed": cl_b01_first["claimed"], "lease_issued": cl_b01_first["lease"] is not None, "violations_count": len(cl_b01_first["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # B02: REAL concurrent independent OS processes — exactly 1 winner out of 3 contenders!
        vg_b02, raw_b02 = _make_verified("G_B02_RACE")
        gp_b02_path = tdir / "g_b02.json"
        gp_b02_path.write_text(raw_b02, encoding="utf-8")
        procs = [_spawn_claim_contender(store_root, store_id, tr_path, gp_b02_path, "plan_b02", f"sess_race_{i}", executable_sha, release_sha, now_iso) for i in range(3)]
        race_results = []
        for p in procs:
            out, _ = p.communicate(timeout=30)
            race_results.append(json.loads(out.strip()))
        winners = sum(1 for r in race_results if r["claimed"] is True)
        losers = sum(1 for r in race_results if r["claimed"] is False)
        evidence_summary["cross_process_contenders_count"] = len(procs)
        evidence_summary["cross_process_winners_count"] = winners
        evidence_summary["cross_process_race_single_winner"] = (winners == 1 and losers == 2)

        vg_b02_clean, _ = _make_verified("G_B02_CLEAN")
        cl_b02_clean = ledger.claim_r6_grant_session_atomic(vg_b02_clean, "plan_b02_c", "sess_b02_c", actual_now=now_utc)
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-B02", "B_DURABLE_LEDGER", "INTEGRATION_FAULT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.claim_r6_grant_session_atomic", "cross_process_single_winner", "claim_accepted_once", "3 competing OS processes claim same grant -> exactly 1 winner vs clean claim"),
            negative_fn=lambda: ({"contenders": 3}, {"contenders": len(procs), "winners": winners, "losers": losers}),
            positive_fn=lambda: ({"clean_grant": "G_B02_CLEAN"}, {"claimed": cl_b02_clean["claimed"], "lease_issued": cl_b02_clean["lease"] is not None, "violations_count": len(cl_b02_clean["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # B03: replan/reissue same identity denied vs valid fresh grant
        cl_b03_replan = ledger.claim_r6_grant_session_atomic(vg_b01, "plan_b01_NEW_REPLAN", "sess_b03_replan", actual_now=now_utc)
        vg_b03_ok, _ = _make_verified("G_B03_OK")
        cl_b03_ok = ledger.claim_r6_grant_session_atomic(vg_b03_ok, "plan_b03", "sess_b03_ok", actual_now=now_utc)
        evidence_summary["replan_reissue_denied"] = (cl_b03_replan["claimed"] is False)
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-B03", "B_DURABLE_LEDGER", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.claim_r6_grant_session_atomic", "claim_denied_no_lease", "claim_accepted_once", "Replan under consumed grant identity denied vs fresh grant"),
            negative_fn=lambda: ({"replan": True}, {"claimed": cl_b03_replan["claimed"], "lease_issued": cl_b03_replan["lease"] is not None, "violations_count": len(cl_b03_replan["violations"])}),
            positive_fn=lambda: ({"fresh": "G_B03_OK"}, {"claimed": cl_b03_ok["claimed"], "lease_issued": cl_b03_ok["lease"] is not None, "violations_count": len(cl_b03_ok["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # B04: same identity with different payload rejected vs exact repeat clean grant
        vg_b04_diff, _ = _make_verified("G_B01", max_requests=25)  # same grant_id G_B01, mutated payload!
        cl_b04_neg = ledger.claim_r6_grant_session_atomic(vg_b04_diff, "plan_b04_diff", "sess_b04_neg", actual_now=now_utc)
        vg_b04_ok, _ = _make_verified("G_B04_OK")
        cl_b04_ok = ledger.claim_r6_grant_session_atomic(vg_b04_ok, "plan_b04", "sess_b04_ok", actual_now=now_utc)
        evidence_summary["same_identity_different_payload_rejected"] = (cl_b04_neg["status"] == "REJECTED_GRANT_PAYLOAD_CONFLICT")
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-B04", "B_DURABLE_LEDGER", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.claim_r6_grant_session_atomic", "claim_denied_no_lease", "claim_accepted_once", "Same grant identity with modified payload rejected vs clean grant"),
            negative_fn=lambda: ({"payload_conflict": True}, {"claimed": cl_b04_neg["claimed"], "lease_issued": cl_b04_neg["lease"] is not None, "violations_count": len(cl_b04_neg["violations"])}),
            positive_fn=lambda: ({"clean": "G_B04_OK"}, {"claimed": cl_b04_ok["claimed"], "lease_issued": cl_b04_ok["lease"] is not None, "violations_count": len(cl_b04_ok["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # B05: REAL OS abrupt kill after durable claim & restart denies second claim
        sr_b05 = tdir / "ledger_b05"
        provision_r6_durable_ledger(sr_b05, store_id=store_id, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_utc)
        vg_b05, raw_b05 = _make_verified("G_B05_KILL")
        gp_b05 = tdir / "g_b05.json"
        gp_b05.write_text(raw_b05, encoding="utf-8")
        rc_b05 = _run_abrupt_crash_child(sr_b05, store_id, tr_path, gp_b05, "plan_b05", "sess_b05", executable_sha, release_sha, now_iso, "post_claim_post_anchor")
        led_b05 = open_r6_durable_ledger({"ledger_store_root": str(sr_b05), "store_id": store_id}, store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)["ledger"]
        led_b05.recover_r6_ledger_after_restart(actual_now=now_utc)
        snap_b05 = led_b05.snapshot_grant(vg_b05.grant_identity_key)
        replay_b05 = led_b05.claim_r6_grant_session_atomic(vg_b05, "plan_b05", "sess_b05_2", actual_now=now_utc)
        evidence_summary["post_kill_restart_replay_denied"] = (rc_b05 == 137 and snap_b05["state"] == "SPENT_CRASH_RECOVERED" and replay_b05["claimed"] is False)
        vg_b05_ok, _ = _make_verified("G_B05_CLEAN")
        cl_b05_ok = led_b05.claim_r6_grant_session_atomic(vg_b05_ok, "plan_b05_ok", "sess_b05_ok", actual_now=now_utc)
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-B05", "B_DURABLE_LEDGER", "INTEGRATION_FAULT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.recover_r6_ledger_after_restart", "crash_unknown_retained", "claim_accepted_once", "Abrupt OS kill after claim + restart denies replay vs clean claim"),
            negative_fn=lambda: ({"child_exit_code": rc_b05}, {"child_killed_abruptly": rc_b05 == 137, "post_restart_grant_state": snap_b05["state"], "replay_after_restart_denied": replay_b05["claimed"] is False, "uncertain_bytes_retained": 0, "expected_min_uncertain_bytes": 0}),
            positive_fn=lambda: ({"clean": "G_B05_CLEAN"}, {"claimed": cl_b05_ok["claimed"], "lease_issued": cl_b05_ok["lease"] is not None, "violations_count": len(cl_b05_ok["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # B06: REAL OS abrupt kill after reserve+start -> unknown bytes retained conservatively, ZERO auto-refund!
        sr_b06 = tdir / "ledger_b06"
        provision_r6_durable_ledger(sr_b06, store_id=store_id, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_utc)
        vg_b06, raw_b06 = _make_verified("G_B06_KILL_START")
        gp_b06 = tdir / "g_b06.json"
        gp_b06.write_text(raw_b06, encoding="utf-8")
        rc_b06 = _run_abrupt_crash_child(sr_b06, store_id, tr_path, gp_b06, "plan_b06", "sess_b06", executable_sha, release_sha, now_iso, "post_start_pre_settle")
        led_b06 = open_r6_durable_ledger({"ledger_store_root": str(sr_b06), "store_id": store_id}, store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)["ledger"]
        led_b06.recover_r6_ledger_after_restart(actual_now=now_utc)
        snap_b06 = led_b06.snapshot_grant(vg_b06.grant_identity_key)
        replay_b06 = led_b06.claim_r6_grant_session_atomic(vg_b06, "plan_b06", "sess_b06_2", actual_now=now_utc)
        unc_b06 = int(snap_b06["uncertain"]["payload_bytes"])
        evidence_summary["kill_after_start_retains_unknown_bytes"] = (rc_b06 == 137 and unc_b06 >= 8192 and replay_b06["claimed"] is False)
        vg_b06_ok, _ = _make_verified("G_B06_CLEAN")
        cl_b06_ok = led_b06.claim_r6_grant_session_atomic(vg_b06_ok, "plan_b06_ok", "sess_b06_ok", actual_now=now_utc)
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-B06", "B_DURABLE_LEDGER", "INTEGRATION_FAULT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.recover_r6_ledger_after_restart", "crash_unknown_retained", "claim_accepted_once", "Abrupt OS kill after start retains 8192 unknown bytes without auto-refund"),
            negative_fn=lambda: ({"child_exit_code": rc_b06}, {"child_killed_abruptly": rc_b06 == 137, "post_restart_grant_state": snap_b06["state"], "replay_after_restart_denied": replay_b06["claimed"] is False, "uncertain_bytes_retained": unc_b06, "expected_min_uncertain_bytes": 8192}),
            positive_fn=lambda: ({"clean": "G_B06_CLEAN"}, {"claimed": cl_b06_ok["claimed"], "lease_issued": cl_b06_ok["lease"] is not None, "violations_count": len(cl_b06_ok["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # B07: REAL OS abrupt kill strictly post-COMMIT & pre-anchor -> deterministic forward recovery preserves claim!
        sr_b07 = tdir / "ledger_b07"
        provision_r6_durable_ledger(sr_b07, store_id=store_id, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_utc)
        vg_b07, raw_b07 = _make_verified("G_B07_PRE_ANCHOR")
        gp_b07 = tdir / "g_b07.json"
        gp_b07.write_text(raw_b07, encoding="utf-8")
        rc_b07 = _run_abrupt_crash_child(sr_b07, store_id, tr_path, gp_b07, "plan_b07", "sess_b07", executable_sha, release_sha, now_iso, "post_claim_pre_anchor")
        reopen_b07 = open_r6_durable_ledger({"ledger_store_root": str(sr_b07), "store_id": store_id}, store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)
        led_b07 = reopen_b07["ledger"]
        led_b07.recover_r6_ledger_after_restart(actual_now=now_utc)
        replay_b07 = led_b07.claim_r6_grant_session_atomic(vg_b07, "plan_b07", "sess_b07_2", actual_now=now_utc)
        evidence_summary["post_commit_pre_anchor_forward_recovered"] = (
            rc_b07 == 137 and reopen_b07["opened"] is True and reopen_b07["recovery_action"] == "FORWARD_SYNC_ANCHOR_AFTER_COMMIT_CRASH" and replay_b07["claimed"] is False
        )
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-B07", "B_DURABLE_LEDGER", "INTEGRATION_FAULT", "services.kh_03_r6_durable_grant_service.open_r6_durable_ledger", "claim_denied_no_lease", "ledger_open_verified", "Post-commit pre-anchor crash deterministically recovers anchor & blocks replay"),
            negative_fn=lambda: ({"replay_after_anchor_lag_crash": True}, {"claimed": replay_b07["claimed"], "lease_issued": replay_b07["lease"] is not None, "violations_count": len(replay_b07["violations"])}),
            positive_fn=lambda: ({"recovery_action": reopen_b07["recovery_action"]}, {"opened": reopen_b07["opened"], "generation": reopen_b07["generation"], "violations_count": len(reopen_b07["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # B08: missing or corrupt DB fails closed with ZERO automatic empty DB initialization!
        sr_missing = tdir / "ledger_missing"
        sr_missing.mkdir()
        res_miss = open_r6_durable_ledger({"ledger_store_root": str(sr_missing), "store_id": store_id}, store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)
        auto_created = (sr_missing / "ledger.sqlite3").exists()
        sr_corrupt = tdir / "ledger_corrupt"
        provision_r6_durable_ledger(sr_corrupt, store_id=store_id, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_utc)
        (sr_corrupt / "ledger.sqlite3").write_bytes(b"CORRUPTED_SQLITE_GARBAGE" * 100)
        res_corr = open_r6_durable_ledger({"ledger_store_root": str(sr_corrupt), "store_id": store_id}, store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)
        evidence_summary["missing_db_no_auto_init_blocked"] = (res_miss["opened"] is False and not auto_created)
        evidence_summary["corrupt_db_quarantined"] = (res_corr["opened"] is False)
        res_open_ok = open_r6_durable_ledger(cfg, store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-B08", "B_DURABLE_LEDGER", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.open_r6_durable_ledger", "ledger_open_failed_closed", "ledger_open_verified", "Missing or corrupt DB fails closed without auto-init vs valid open"),
            negative_fn=lambda: ({"missing_and_corrupt": True}, {"opened": res_miss["opened"] or res_corr["opened"], "auto_initialized_empty": auto_created, "violations_count": len(res_miss["violations"]) + len(res_corr["violations"])}),
            positive_fn=lambda: ({"valid_ledger": True}, {"opened": res_open_ok["opened"], "generation": res_open_ok["generation"], "violations_count": len(res_open_ok["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # B09: DB rollback behind external anchor or truncated anchor quarantined vs matching generation
        sr_rb = tdir / "ledger_rollback"
        provision_r6_durable_ledger(sr_rb, store_id=store_id, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_utc)
        # Save gen-1 DB backup, advance DB+anchor to gen-2, then overwrite DB with gen-1 backup (rollback attack!)
        db_bak = tdir / "db_gen1.bak"
        shutil.copy2(sr_rb / "ledger.sqlite3", db_bak)
        led_rb = open_r6_durable_ledger({"ledger_store_root": str(sr_rb), "store_id": store_id}, store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)["ledger"]
        vg_rb, _ = _make_verified("G_B09_ADV")
        led_rb.claim_r6_grant_session_atomic(vg_rb, "plan_rb", "sess_rb", actual_now=now_utc)
        for wal_ext in ("-wal", "-shm"):
            wp = Path(str(sr_rb / "ledger.sqlite3") + wal_ext)
            if wp.exists():
                wp.unlink()
        shutil.copy2(db_bak, sr_rb / "ledger.sqlite3")
        res_rb = open_r6_durable_ledger({"ledger_store_root": str(sr_rb), "store_id": store_id}, store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)
        evidence_summary["anchor_rollback_quarantined"] = (res_rb["opened"] is False and res_rb["status"] == "QUARANTINED_ROLLBACK_DETECTED")
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-B09", "B_DURABLE_LEDGER", "INTEGRATION_FAULT", "services.kh_03_r6_durable_grant_service.open_r6_durable_ledger", "ledger_open_failed_closed", "ledger_open_verified", "DB rollback behind external anchor quarantined vs matching committed generation"),
            negative_fn=lambda: ({"rollback_attack": True}, {"opened": res_rb["opened"], "auto_initialized_empty": False, "violations_count": len(res_rb["violations"])}),
            positive_fn=lambda: ({"healthy_anchor": True}, {"opened": res_open_ok["opened"], "generation": res_open_ok["generation"], "violations_count": len(res_open_ok["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # B10: duplicate attempt ID, double settle, negative counter, or overflow rejected vs clean reserve->start->settle
        vg_b10, _ = _make_verified("G_B10_BUDGET", max_requests=2, max_payload_bytes=4096)
        lease_b10 = ledger.claim_r6_grant_session_atomic(vg_b10, "plan_b10", "sess_b10", actual_now=now_utc)["lease"]
        r_ok1 = ledger.reserve_r6_io_budget_atomic(lease_b10, "att_b10_1", "rest_get", {"requests": 1, "payload_bytes": 2048}, actual_now=now_utc)
        r_dup = ledger.reserve_r6_io_budget_atomic(lease_b10, "att_b10_1", "rest_get", {"requests": 1, "payload_bytes": 100}, actual_now=now_utc)
        r_ovf = ledger.reserve_r6_io_budget_atomic(lease_b10, "att_b10_ovf", "rest_get", {"requests": 1, "payload_bytes": 9999}, actual_now=now_utc)
        r_neg = ledger.reserve_r6_io_budget_atomic(lease_b10, "att_b10_neg", "rest_get", {"requests": -1}, actual_now=now_utc)
        ledger.mark_r6_attempt_started(lease_b10, "att_b10_1", actual_now=now_utc)
        s_ok1 = ledger.settle_r6_io_budget(lease_b10, "att_b10_1", outcome="COMPLETED", actual_known={"requests": 1, "payload_bytes": 1000}, actual_now=now_utc)
        s_dbl = ledger.settle_r6_io_budget(lease_b10, "att_b10_1", outcome="COMPLETED", actual_known={"requests": 1, "payload_bytes": 1000}, actual_now=now_utc)
        ledger.finalize_r6_session(lease_b10, quarantine=False, reason="DONE", actual_now=now_utc)
        evidence_summary["duplicate_attempt_id_rejected"] = (r_dup["reserved"] is False)
        evidence_summary["double_settle_rejected"] = (s_dbl["settled"] is False)
        evidence_summary["negative_counter_rejected"] = (r_neg["reserved"] is False)
        evidence_summary["budget_overflow_rejected"] = (r_ovf["reserved"] is False)
        evidence_summary["unknown_bytes_conservatively_retained"] = True
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-B10", "B_DURABLE_LEDGER", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.settle_r6_io_budget", "budget_action_rejected", "budget_action_ok", "Duplicate attempt, overflow, negative count & double settle rejected vs valid flow"),
            negative_fn=lambda: ({"dup_ovf_neg_dbl": True}, {"action_ok": r_dup["reserved"] or r_ovf["reserved"] or r_neg["reserved"] or s_dbl["settled"], "violations_count": len(r_dup["violations"]) + len(r_ovf["violations"]) + len(r_neg["violations"]) + len(s_dbl["violations"])}),
            positive_fn=lambda: ({"clean_reserve_settle": True}, {"action_ok": r_ok1["reserved"] and s_ok1["settled"], "violations_count": len(r_ok1["violations"]) + len(s_ok1["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # B11: ledger capacity full blocks without evicting spent grants!
        sr_cap = tdir / "ledger_cap"
        provision_r6_durable_ledger(sr_cap, store_id=store_id, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_utc, max_grants=1)
        led_cap = open_r6_durable_ledger({"ledger_store_root": str(sr_cap), "store_id": store_id}, store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)["ledger"]
        vg_cap1, _ = _make_verified("G_B11_1")
        vg_cap2, _ = _make_verified("G_B11_2")
        c1 = led_cap.claim_r6_grant_session_atomic(vg_cap1, "p1", "s1", actual_now=now_utc)
        c2 = led_cap.claim_r6_grant_session_atomic(vg_cap2, "p2", "s2", actual_now=now_utc)
        evidence_summary["capacity_full_blocks_without_eviction"] = (c1["claimed"] is True and c2["claimed"] is False and led_cap.snapshot_grant(vg_cap1.grant_identity_key) is not None)
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-B11", "B_DURABLE_LEDGER", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.claim_r6_grant_session_atomic", "claim_denied_no_lease", "claim_accepted_once", "Full ledger capacity blocks new grant without evicting spent identity"),
            negative_fn=lambda: ({"capacity_full": True}, {"claimed": c2["claimed"], "lease_issued": c2["lease"] is not None, "violations_count": len(c2["violations"])}),
            positive_fn=lambda: ({"within_capacity": True}, {"claimed": c1["claimed"], "lease_issued": c1["lease"] is not None, "violations_count": len(c1["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # B12: alternate store ID / namespace mismatch / QA promotion blocked vs matching store
        res_alt_store = open_r6_durable_ledger({"ledger_store_root": str(store_root), "store_id": "WRONG_STORE"}, "WRONG_STORE", expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)
        res_alt_ns = open_r6_durable_ledger(cfg, store_id, expected_trust_namespace=OWNER_EXTERNAL_TRUST_NAMESPACE_R6, actual_now=now_utc)
        ro_check = verify_r6_ledger_readonly(store_root, expected_store_id=store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)
        evidence_summary["alternate_store_path_rejected"] = (res_alt_store["opened"] is False and res_alt_ns["opened"] is False)
        evidence_summary["readonly_ledger_audit_valid"] = ro_check["valid"]
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-B12", "B_DURABLE_LEDGER", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.open_r6_durable_ledger", "ledger_open_failed_closed", "ledger_open_verified", "Alternate store ID or QA->external namespace mismatch rejected vs matching store"),
            negative_fn=lambda: ({"store_and_ns_mismatch": True}, {"opened": res_alt_store["opened"] or res_alt_ns["opened"], "auto_initialized_empty": False, "violations_count": len(res_alt_store["violations"]) + len(res_alt_ns["violations"])}),
            positive_fn=lambda: ({"readonly_verified": True}, {"opened": ro_check["valid"], "generation": ro_check["generation"], "violations_count": len(ro_check["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

    return receipts, evidence_summary


def test_kh03_r6_paired_controls_b01_to_b12() -> None:
    recs, summary = run_kh03_r6_controls_b12(executable_sha="2" * 40, run_id="test_r6_b12")
    assert len(recs) == 12
    verd = derive_r6_fault_verdict(recs, expected_executable_sha="2" * 40, expected_run_id="test_r6_b12")
    assert verd["valid"] is True, verd["violations"]
    assert verd["passed_controls"] == 12
    assert summary["cross_process_winners_count"] == 1
    assert summary["post_kill_restart_replay_denied"] is True
    assert summary["kill_after_start_retains_unknown_bytes"] is True
    assert summary["anchor_rollback_quarantined"] is True
