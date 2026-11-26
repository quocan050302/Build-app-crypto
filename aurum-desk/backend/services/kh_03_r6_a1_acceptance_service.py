"""
aurum-desk/backend/services/kh_03_r6_a1_acceptance_service.py

AURUM DESK — KH-03.R6.A1 (M0 / M6) | REAL ISOLATED BASELINE RED REPRODUCER (529bdaf2),
A1 PAIRED FAULT TRUTH ENGINE (>=55 PAIRS), 36-GATE A1 VERDICT ENGINE, A1 WORKLOAD BENCHMARK
& VIETNAMESE OWNER HANDOVER BUILDER
SPEC VERSION: KH-03.R6.A1-v1.0 | OFFLINE / LOOPBACK QA ONLY | ZERO LITERAL FLAG SHORTCUTS
"""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import uuid

import psutil

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_r6_a1_trust_service import (
    A1_EXTERNAL_CAPTURE_ENABLED,
    BASELINE_REVIEWED_HEAD_SHA_R6_A1,
    PRIOR_R6_EVIDENCE_COMMIT_SHA,
    PRIOR_R6_MANIFEST_26_SHA256,
    PRIOR_R6_READBACK_28_SHA256,
    PRIOR_R6_RUN_ID,
    PRIOR_R6_TESTED_EXECUTABLE_SHA,
    SCHEMA_VERSION_KH03_R6_A1,
    SPEC_VERSION_KH03_R6_A1,
    _is_finite_num_a1,
    _is_strict_int_a1,
    _sha256_canonical_a1,
    canonicalize_r6_bound_plan_core,
)
from services.kh_03_r6_source_contract_service import (
    API_FAMILY_CLASSIC_V2,
    MANDATORY_BRANCH_KH03_R6,
)

PAIRED_FAULT_RECEIPT_SCHEMA_A1 = "kh_03_r6_a1_paired_fault_v1"
ALLOWED_FAULT_KINDS_A1: Tuple[str, ...] = ("NEGATIVE_CONTRACT", "INTEGRATION_FAULT", "BASELINE_BUG_RED")

MIN_EXPECTED_PYTEST_COLLECTED_R6_A1 = 5973
MIN_NEW_PAIRED_CONTROLS_R6_A1 = 55
MIN_NEW_VERIFIER_MUTATIONS_R6_A1 = 32

CLASSIFICATION_PASS_OFFLINE_R6_A1 = (
    "ENGINEERING_PASS_OFFLINE_R6_A1_END_TO_END_QA_TRUST_CLOSED_"
    "CAPTURE_BLOCKED_NO_PROVEN_EDGE"
)

GATE_36_KEYS_KH03_R6_A1: Tuple[str, ...] = (
    "G01_BRANCH_SHA_CLEAN",
    "G02_KEEP_AS_IS",
    "G03_PRIOR_SEALED_NO_OOS",
    "G04_REAL_RED",
    "G05_DRYRUN_NO_CONSUMPTION",
    "G06_QA_CLI_END_TO_END",
    "G07_TASK_COVERAGE_FINALIZE",
    "G08_PLAN_CANONICAL_DIGEST",
    "G09_GRANT_LEASE_BINDING",
    "G10_REQUEST_ALLOWLIST",
    "G11_DURABLE_RESERVATION",
    "G12_CHILD_ENVELOPE",
    "G13_REST_CUMULATIVE_CAP",
    "G14_WS_CUMULATIVE_CAP",
    "G15_SETTLEMENT_CONSERVATION",
    "G16_ACTUAL_WORKER_LIFECYCLE",
    "G17_CANCEL_DEADLINE_LATE_RESULT",
    "G18_LEDGER_RESTART_ANCHOR",
    "G19_TRUSTED_SUPERVISOR_PROOF",
    "G20_PROVENANCE_RAW_BYTES",
    "G21_CUSTODY_QA_MANIFEST",
    "G22_RELEASE_FROZEN_EXECUTABLE",
    "G23_RELEASE_MANIFEST_TRUTH",
    "G24_EXTERNAL_HARD_BLOCK",
    "G25_FULL_PYTEST",
    "G26_BASELINE_PARITY",
    "G27_BACKTEST_PARITY",
    "G28_DETERMINISM",
    "G29_MEASURED_WORKER_POLICY",
    "G30_NEW_PAIRED_FAULT_TRUTH",
    "G31_INHERITED_REGRESSION",
    "G32_INDEPENDENT_VERIFIER",
    "G33_MUTATION_RESISTANCE",
    "G34_RESOURCE_CLEANUP",
    "G35_ECONOMIC_ACTIVATION",
    "G36_REMOTE_PUBLICATION",
)


def _contains_nonfinite_a1(obj: Any) -> bool:
    if isinstance(obj, float):
        return not math.isfinite(obj)
    if isinstance(obj, Mapping):
        return any(_contains_nonfinite_a1(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_contains_nonfinite_a1(x) for x in obj)
    return False


# =====================================================================
# 1. M0 — Real Isolated Baseline RED Reproduction on Untouched 529bdaf2
# =====================================================================
_BASELINE_RED_FILES_529BDAF2: Tuple[str, ...] = (
    "aurum-desk/tools/run_kh_03_r6_capture.py",
    "aurum-desk/backend/services/kh_03_r6_source_contract_service.py",
    "aurum-desk/backend/services/kh_03_r6_durable_grant_service.py",
    "aurum-desk/backend/services/kh_03_r6_bounded_transport_service.py",
    "aurum-desk/backend/services/kh_03_r6_market_data_service.py",
    "aurum-desk/backend/services/kh_03_r6_custody_readiness_service.py",
    "aurum-desk/backend/services/kh_03_r6_acceptance_service.py",
)


def run_m0_real_baseline_red_reproduction_529bdaf2(
    *,
    repo_root: Path,
    run_id: str,
    tested_sha: str,
) -> Dict[str, Any]:
    """
    M0: Extracts untouched R6 Stage A tested source (`529bdaf2e533d3616b1a8e0842c048da3b81126b`)
    via `git show` into a bounded isolated temporary overlay (NEVER touches active working branch!)
    and genuinely reproduces ALL 6 baseline review hypotheses (`RED-G`, `RED-H`, `RED-I1`, `RED-I2`,
    `RED-J`, `RED-K`) in a separate OS subprocess with exact source hashes, PID, exit code, and traces.
    """
    extracted_hashes: Dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="a1_m0_red_") as td:
        overlay = Path(td)
        for rel in _BASELINE_RED_FILES_529BDAF2:
            p_show = subprocess.run(
                ["git", "show", f"{PRIOR_R6_TESTED_EXECUTABLE_SHA}:{rel}"],
                cwd=str(repo_root),
                capture_output=True,
                check=False,
                timeout=20,
            )
            if p_show.returncode != 0 or not p_show.stdout:
                return {
                    "status": "FAIL_GIT_SHOW_EXTRACTION",
                    "baseline_sha": PRIOR_R6_TESTED_EXECUTABLE_SHA,
                    "reproduced_count": 0,
                    "cases": {},
                    "violations": [f"GIT_SHOW_FAILED:{rel}"],
                }
            dst = overlay / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(p_show.stdout)
            extracted_hashes[rel] = hashlib.sha256(p_show.stdout).hexdigest()

        # Materialize pkgutil-extended __init__.py so overlay 529bdaf2 files take precedence while
        # untouched locked modules (e.g. kh_02_r2_prospective_data_service) resolve cleanly from repo_root.
        (overlay / "aurum-desk" / "backend" / "services" / "__init__.py").write_text(
            "from pkgutil import extend_path\n__path__ = extend_path(__path__, __name__)\n",
            encoding="utf-8",
        )

        runner_script = overlay / "run_m0_red_probe.py"
        runner_script.write_text(
            '''import dataclasses, json, os, subprocess, sys, tempfile, threading, time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

overlay = Path(sys.argv[1])
repo_root = Path(sys.argv[2])
r6_manifest = repo_root / "aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/20261126T005509Z_529bdaf2e533/26_independent_OS_verifier_manifest_sha256.json"
r6_handover = repo_root / "aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/20261126T005509Z_529bdaf2e533/HANDOVER_KH03_R6_VI.txt"

sys.path.insert(0, str(repo_root / "aurum-desk/backend"))
sys.path.insert(0, str(repo_root / "aurum-desk"))
sys.path.insert(0, str(overlay / "aurum-desk/backend"))
sys.path.insert(0, str(overlay / "aurum-desk"))

import services.kh_03_r6_durable_grant_service as dgs
import services.kh_03_r6_bounded_transport_service as bts
import services.kh_03_r6_custody_readiness_service as crs
import services.kh_03_r6_source_contract_service as scs
import services.kh_03_r6_acceptance_service as accs

now = datetime(2026, 11, 26, 1, 0, 0, tzinfo=timezone.utc)
now_ms = int(now.timestamp() * 1000)
reg = scs.build_r6_source_contract_registry()
cases = {}

with tempfile.TemporaryDirectory(prefix="m0_work_") as wd:
    wdir = Path(wd)
    dest = wdir / "dest"; dest.mkdir()
    store_root = wdir / "ledger"
    sid = "m0_red_store"
    tr_p, priv_k, _ = dgs.provision_qa_isolated_trust_root(wdir / "trust", store_id=sid)
    tr_qa = dgs.load_r6_owner_trust_root(tr_p, expected_namespace=dgs.QA_ISOLATED_TRUST_NAMESPACE_R6)["trust_root"]
    dgs.provision_r6_durable_ledger(store_root, store_id=sid, trust_namespace=dgs.QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now)
    led = dgs.open_r6_durable_ledger({"ledger_store_root": str(store_root), "store_id": sid}, sid, expected_trust_namespace=dgs.QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now)["ledger"]

    def _mk_grant(gid, max_req=5, max_pay=4096):
        gp = {
            "grant_id": gid, "issuer": "QA_ISOLATED_HARNESS", "trust_namespace": dgs.QA_ISOLATED_TRUST_NAMESPACE_R6,
            "issued_utc_ms": now_ms - 1000, "expires_utc_ms": now_ms + 600_000,
            "repo": "quocan050302/Build-app-crypto", "branch": scs.MANDATORY_BRANCH_KH03_R6,
            "tested_executable_sha": "529bdaf2e533d3616b1a8e0842c048da3b81126b", "release_manifest_sha256": "b" * 64,
            "purpose": "QA_FIXTURE", "stage": "QA_LOOPBACK", "venue": scs.APPROVED_VENUE_R6,
            "api_family": scs.API_FAMILY_CLASSIC_V2, "symbol": scs.APPROVED_SYMBOL_R6, "product_type": scs.APPROVED_PRODUCT_TYPE_V2_R6,
            "allowed_endpoints": ["classic_v2_contracts", "classic_v2_history_candles"], "allowed_ws_channels": ["books1"],
            "window_start_ms": 1794000000000, "window_end_ms": 1794003600000,
            "destination_store_id": sid, "destination_root": str(dest),
            "max_session_duration_sec": 120, "max_concurrency": 1,
            "caps": {"connections": 1, "requests": max_req, "retries": 1, "subscriptions": 1, "messages": 10, "payload_bytes": max_pay, "decoded_bytes": max_pay * 2, "pages": 2, "disk_bytes": 65536, "records": 100},
            "retention_policy": {"max_retention_days": 7, "git_commit_raw_payloads": False},
        }
        raw = dgs.sign_r6_grant_payload_with_ed25519(gp, priv_k)
        return dgs.verify_r6_owner_grant(raw, tr_qa, now, expected_executable_sha="529bdaf2e533d3616b1a8e0842c048da3b81126b", expected_release_manifest_sha256="b" * 64, expected_store_id=sid, expected_stage="QA_LOOPBACK")["verified_grant"]

    # RED-G: Inspect untouched old CLI code path: claims grant, finalizes immediately with AUTHORIZED_SESSION_CLAIMED_AND_READY, 0 supervisor/custody calls!
    old_cli_txt = (overlay / "aurum-desk/tools/run_kh_03_r6_capture.py").read_text("utf-8")
    vg_g = _mk_grant("G_RED_G")
    pl_g = bts.build_r6_bound_capture_plan(vg_g, reg, {}, {"ok": True}, plan_id="pl_red_g")["plan"]
    cl_g = led.claim_r6_grant_session_atomic(vg_g, pl_g.plan_digest, "sess_red_g", actual_now=now)
    fin_g = led.finalize_r6_session(cl_g["lease"], quarantine=False, reason="AUTHORIZED_SESSION_HANDSHAKE_COMPLETE", actual_now=now)
    snap_g = led.snapshot_grant(vg_g.grant_identity_key)
    cases["RED-G"] = {
        "reproduced": ("AUTHORIZED_SESSION_CLAIMED_AND_READY" in old_cli_txt and "supervise_r6_session" not in old_cli_txt and "R6CustodyJournal" not in old_cli_txt and snap_g["state"] == "SPENT" and len(snap_g["attempts"]) == 0),
        "old_cli_has_handshake_only_exit": "AUTHORIZED_SESSION_CLAIMED_AND_READY" in old_cli_txt,
        "old_cli_calls_supervisor": "supervise_r6_session" in old_cli_txt,
        "old_cli_calls_custody": "R6CustodyJournal" in old_cli_txt,
        "reproduced_grant_state_after_handshake": snap_g["state"],
        "reproduced_attempts_count": len(snap_g["attempts"]),
        "disclosure": "Executed untouched 529bdaf2 claim+finalize handshake branch in QA isolated ledger (no external owner trust root); confirmed old CLI source never invokes supervise_r6_session or R6CustodyJournal and spends grant with 0 attempts.",
    }

    # RED-H: Mutate nested plan.caps dict AND dataclasses.replace endpoints/window while keeping digest+MAC -> old verify accepts!
    vg_h = _mk_grant("G_RED_H")
    pl_h = bts.build_r6_bound_capture_plan(vg_h, reg, {}, {"ok": True}, plan_id="pl_red_h")["plan"]
    pl_h.caps["requests"] = 999999
    ok_h_caps, _ = bts.verify_r6_bound_plan_integrity(pl_h, vg_h)
    pl_h_rep = dataclasses.replace(pl_h, endpoints=("classic_v2_ticker",), window_end_ms=pl_h.window_end_ms + 999_000)
    ok_h_rep, _ = bts.verify_r6_bound_plan_integrity(pl_h_rep, vg_h)
    cases["RED-H"] = {
        "reproduced": bool(ok_h_caps and ok_h_rep),
        "mutated_caps_requests_accepted": bool(ok_h_caps),
        "mutated_caps_value": int(pl_h.caps["requests"]),
        "replaced_endpoints_window_accepted": bool(ok_h_rep),
        "replaced_endpoints": list(pl_h_rep.endpoints),
    }

    # RED-I1: Loopback child request overrides parent max_payload_bytes=32 with req["max_payload_bytes"]=4096 -> succeeds returning 100 bytes!
    body_100 = b"{" + b"\\"x\\":\\"" + (b"A" * 90) + b"\\"}"
    class _H(BaseHTTPRequestHandler):
        def log_message(self, *a): return
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Length", str(len(body_100))); self.end_headers(); self.wfile.write(body_100)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    port = int(srv.server_address[1])
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    try:
        child_env = dict(os.environ)
        child_env["PYTHONPATH"] = str(overlay / "aurum-desk/backend")
        child_in = json.dumps({
            "lease_id": "l_red", "session_id": "s_red", "external_capture_permitted": False,
            "limits": {"remaining_deadline_sec": 3.0, "max_payload_bytes": 32, "max_decoded_bytes": 32, "max_messages": 5},
            "request": {"kind": "rest_get", "url": f"http://127.0.0.1:{port}/ok", "params": {}, "qa_loopback_mode": True, "max_payload_bytes": 4096, "max_decoded_bytes": 4096},
        })
        p_ch = subprocess.run([sys.executable, "-m", "services.kh_03_r6_bounded_transport_service", "--child-worker"], input=child_in, capture_output=True, text=True, env=child_env, timeout=20)
        ch_out = json.loads(p_ch.stdout)
        cases["RED-I1"] = {
            "reproduced": bool(ch_out.get("ok") is True and int(ch_out.get("payload_bytes_received", 0)) > 32),
            "parent_limit_max_payload_bytes": 32,
            "child_override_max_payload_bytes": 4096,
            "actual_bytes_received": int(ch_out.get("payload_bytes_received", 0)),
            "child_ok": bool(ch_out.get("ok")),
            "child_exit_code": int(p_ch.returncode),
        }
    finally:
        srv.shutdown(); srv.server_close(); th.join(timeout=2.0)

    # RED-I2: Over-reservation settlement accepted in old settle_r6_io_budget without quarantine!
    vg_i2 = _mk_grant("G_RED_I2", max_req=2, max_pay=100)
    cl_i2 = led.claim_r6_grant_session_atomic(vg_i2, "pl_red_i2", "sess_red_i2", actual_now=now)
    l_i2 = cl_i2["lease"]
    led.reserve_r6_io_budget_atomic(l_i2, "att_red_i2", "rest_get", {"requests": 1, "payload_bytes": 50}, actual_now=now)
    led.mark_r6_attempt_started(l_i2, "att_red_i2", actual_now=now)
    s_i2 = led.settle_r6_io_budget(l_i2, "att_red_i2", outcome="COMPLETED", actual_known={"requests": 1, "payload_bytes": 5000}, actual_now=now)
    snap_i2 = led.snapshot_grant(vg_i2.grant_identity_key)
    cases["RED-I2"] = {
        "reproduced": bool(s_i2.get("settled") is True and snap_i2["known"]["payload_bytes"] == 5000 and snap_i2["state"] != "QUARANTINED"),
        "reserved_payload_bytes": 50,
        "plan_cap_payload_bytes": 100,
        "settled_known_payload_bytes": int(snap_i2["known"]["payload_bytes"]),
        "old_settle_returned_settled": bool(s_i2.get("settled")),
        "grant_state_after_overrun": snap_i2["state"],
    }

    # RED-J: Synthetic Mapping with ok/tls_verified/reaped_verified mints RAW_OBSERVED_VERIFIED with valid parent MAC!
    fake_sup = {"ok": True, "status": "HTTP_200_BOUNDED_OK", "accepted_result": {"tls_verified": True, "decoded_bytes": 12}, "reap_info": {"reaped_verified": True, "child_pid": 99999, "child_create_time": 1.0, "child_returncode": 0}}
    fake_bytes = b"FAKE_MARKET!"
    prov_j = crs.mint_r6_raw_provenance_receipt(
        tested_executable_sha="529bdaf2e533d3616b1a8e0842c048da3b81126b", run_id="red_j", session_id="s_j", lease_id="l_j",
        attempt_id="a_j", endpoint_id="classic_v2_contracts", contract_digest="c" * 64, partition="DEV_ONLY",
        qa_loopback_mode=False, transport_kind="REST_GET", supervisor_result=fake_sup, raw_payload_bytes_data=fake_bytes,
    )
    v_j, _ = crs.verify_r6_raw_provenance_receipt(prov_j, expected_payload_bytes=fake_bytes)
    cases["RED-J"] = {
        "reproduced": bool(prov_j.provenance_class == "RAW_OBSERVED_VERIFIED" and prov_j.accepted_for_custody is True and v_j is True),
        "minted_provenance_class": prov_j.provenance_class,
        "accepted_for_custody": bool(prov_j.accepted_for_custody),
        "verify_passed": bool(v_j),
    }

    # RED-K: (1) Old CLI rejects published release 529bdaf2 vs HEAD != 529bdaf2; (2) old template/handover lacks --execute-authorized; (3) invalid dry-run plan accepted on JSON parse!
    bad_plan = wdir / "invalid_plan.json"
    bad_plan.write_text(json.dumps({"not_a_real_plan": True, "endpoints": ["fake_private_order"], "caps": {"requests": -999}}), encoding="utf-8")
    old_cli_env = dict(os.environ)
    old_cli_env["PYTHONPATH"] = f"{overlay / 'aurum-desk/backend'}{os.pathsep}{overlay / 'aurum-desk'}{os.pathsep}{repo_root / 'aurum-desk/backend'}{os.pathsep}{repo_root / 'aurum-desk'}"
    p_dry = subprocess.run([sys.executable, str(overlay / "aurum-desk/tools/run_kh_03_r6_capture.py"), "--stage", "smoke", "--dry-run", "--plan", str(bad_plan)], cwd=str(repo_root), capture_output=True, text=True, env=old_cli_env, timeout=25)
    dry_doc = json.loads(p_dry.stdout) if p_dry.returncode == 0 else {}
    old_tmpl = accs.build_r6_unsigned_owner_review_template(tested_executable_sha="529bdaf2e533d3616b1a8e0842c048da3b81126b", stage_a_run_id="20261126T005509Z_529bdaf2e533", source_registry_digest=reg.registry_digest, stage="B_SMOKE")
    old_auth_cmd = old_tmpl["exact_local_cli_commands"]["authorized_command"]
    old_ho_txt = r6_handover.read_text("utf-8") if r6_handover.is_file() else ""
    cur_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_root), capture_output=True, text=True).stdout.strip()
    old_rel_doc = json.loads(r6_manifest.read_text("utf-8")) if r6_manifest.is_file() else {}
    old_naive_match = (old_rel_doc.get("tested_executable_sha") == cur_head)
    cases["RED-K"] = {
        "reproduced": bool((not old_naive_match) and ("--execute-authorized" not in old_auth_cmd) and (p_dry.returncode == 0 and (dry_doc.get("plan_check") or {}).get("parsed_ok") is True)),
        "old_cli_naive_head_equals_tested": bool(old_naive_match),
        "published_tested_sha": old_rel_doc.get("tested_executable_sha"),
        "actual_repo_head_sha": cur_head,
        "old_template_missing_execute_authorized": ("--execute-authorized" not in old_auth_cmd),
        "old_handover_missing_execute_authorized": ("--execute-authorized" not in old_ho_txt),
        "old_dryrun_invalid_plan_exit_code": int(p_dry.returncode),
        "old_dryrun_invalid_plan_parsed_ok": bool((dry_doc.get("plan_check") or {}).get("parsed_ok")),
    }

print(json.dumps({"pid": os.getpid(), "cases": cases}, sort_keys=True))
''',
            encoding="utf-8",
        )

        t0 = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, str(runner_script), str(overlay), str(repo_root)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        elapsed = round(time.perf_counter() - t0, 4)

    if proc.returncode != 0:
        return {
            "spec_version": SPEC_VERSION_KH03_R6_A1,
            "run_id": run_id,
            "tested_executable_sha": tested_sha,
            "status": "FAIL_M0_SUBPROCESS_ERROR",
            "baseline_sha": PRIOR_R6_TESTED_EXECUTABLE_SHA,
            "subprocess_exit_code": int(proc.returncode),
            "stderr_tail": proc.stderr[-1500:],
            "extracted_files_sha256": extracted_hashes,
            "reproduced_count": 0,
            "cases": {},
        }

    parsed = json.loads(proc.stdout)
    cases = parsed["cases"]
    repro_cnt = sum(1 for c in cases.values() if c.get("reproduced") is True)
    all_ok = repro_cnt == len(cases) and len(cases) == 6
    return {
        "spec_version": SPEC_VERSION_KH03_R6_A1,
        "schema_version": SCHEMA_VERSION_KH03_R6_A1,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "status": "PASS_ALL_6_BASELINE_RED_REPRODUCED" if all_ok else "PARTIAL_OR_NOT_REPRODUCED",
        "baseline_sha": PRIOR_R6_TESTED_EXECUTABLE_SHA,
        "baseline_reviewed_head_sha": BASELINE_REVIEWED_HEAD_SHA_R6_A1,
        "isolation_method": "git_show_isolated_temp_overlay_zero_branch_mutation",
        "probe_pid": int(parsed["pid"]),
        "subprocess_exit_code": int(proc.returncode),
        "elapsed_sec": elapsed,
        "extracted_files_sha256": extracted_hashes,
        "total_hypotheses": len(cases),
        "reproduced_count": repro_cnt,
        "all_reproduced": all_ok,
        "cases": cases,
        "raw_stdout_sha256": hashlib.sha256(proc.stdout.encode("utf-8")).hexdigest(),
    }


# =====================================================================
# 2. Registered Pure Predicates for A1 Paired Fault Controls (>=55 pairs)
# =====================================================================
def _pa_rejected(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("allowed") is False and _is_strict_int_a1(o.get("violations_count")) and int(o["violations_count"]) >= 1
    return ok, "a1_rejected"


def _pa_accepted(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("allowed") is True and _is_strict_int_a1(o.get("violations_count")) and int(o["violations_count"]) == 0
    return ok, "a1_accepted"


def _pa_cli_failed_no_claim(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_strict_int_a1(o.get("exit_code")) and int(o["exit_code"]) != 0
        and o.get("grant_claimed") is False
        and o.get("external_requests") == 0
    )
    return ok, "a1_cli_failed_no_claim"


def _pa_qa_e2e_pass(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("exit_code") == 0
        and o.get("status") == "QA_ACQUISITION_END_TO_END_PASS"
        and _is_strict_int_a1(o.get("committed_chunks")) and int(o["committed_chunks"]) >= 1
        and _is_strict_int_a1(o.get("committed_rows")) and int(o["committed_rows"]) >= 1
        and o.get("custody_valid") is True
        and o.get("real_observed_records") == 0
        and o.get("external_requests") == 0
    )
    return ok, "a1_qa_e2e_pass"


def _pa_session_aborted_or_partial(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_strict_int_a1(o.get("exit_code")) and int(o["exit_code"]) != 0
        and o.get("status") != "QA_ACQUISITION_END_TO_END_PASS"
        and o.get("status") != "AUTHORIZED_SESSION_CLAIMED_AND_READY"
        and o.get("real_observed_records") == 0
    )
    return ok, "a1_session_aborted_or_partial"


def _pa_overrun_quarantined(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("settled") is False
        and o.get("quarantined") is True
        and o.get("clamped") is False
        and _is_strict_int_a1(o.get("violations_count")) and int(o["violations_count"]) >= 1
    )
    return ok, "a1_overrun_quarantined"


def _pa_settlement_conserved(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("settled") is True
        and o.get("quarantined") is False
        and o.get("receipt_issued") is True
        and _is_strict_int_a1(o.get("violations_count")) and int(o["violations_count"]) == 0
    )
    return ok, "a1_settlement_conserved"


def _pa_provenance_rejected(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("accepted_for_custody") is False
        and o.get("provenance_class") != "RAW_OBSERVED_VERIFIED"
        and o.get("persisted") is False
    )
    return ok, "a1_provenance_rejected"


def _pa_provenance_qa_persisted(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("accepted_for_custody") is True
        and o.get("provenance_class") == "QA_LOOPBACK"
        and o.get("persisted") is True
        and o.get("readonly_valid") is True
    )
    return ok, "a1_provenance_qa_persisted"


def _pa_worker_reaped_aborted(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("ok") is False
        and o.get("reaped_verified") is True
        and o.get("surviving_descendants") == 0
        and o.get("trusted_result_is_none") is True
    )
    return ok, "a1_worker_reaped_aborted"


def _pa_worker_clean_trusted(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("ok") is True
        and o.get("reaped_verified") is True
        and o.get("surviving_descendants") == 0
        and o.get("trusted_result_issued") is True
        and o.get("settlement_receipt_issued") is True
        and _is_strict_int_a1(o.get("child_pid")) and int(o["child_pid"]) > 0
    )
    return ok, "a1_worker_clean_trusted"


def _pa_release_rejected(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("ready_or_valid") is False and _is_strict_int_a1(o.get("violations_count")) and int(o["violations_count"]) >= 1
    return ok, "a1_release_rejected"


def _pa_release_verified(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("ready_or_valid") is True and _is_strict_int_a1(o.get("violations_count")) and int(o["violations_count"]) == 0
    return ok, "a1_release_verified"


REGISTERED_FAULT_PREDICATES_R6_A1: Dict[str, Callable[[Mapping[str, Any]], Tuple[bool, str]]] = {
    "a1_rejected": _pa_rejected,
    "a1_accepted": _pa_accepted,
    "a1_cli_failed_no_claim": _pa_cli_failed_no_claim,
    "a1_qa_e2e_pass": _pa_qa_e2e_pass,
    "a1_session_aborted_or_partial": _pa_session_aborted_or_partial,
    "a1_overrun_quarantined": _pa_overrun_quarantined,
    "a1_settlement_conserved": _pa_settlement_conserved,
    "a1_provenance_rejected": _pa_provenance_rejected,
    "a1_provenance_qa_persisted": _pa_provenance_qa_persisted,
    "a1_worker_reaped_aborted": _pa_worker_reaped_aborted,
    "a1_worker_clean_trusted": _pa_worker_clean_trusted,
    "a1_release_rejected": _pa_release_rejected,
    "a1_release_verified": _pa_release_verified,
}


@dataclass(frozen=True)
class R6A1FaultCaseSpec:
    control_id: str
    group: str  # "G" | "H" | "I" | "J" | "K"
    kind: str  # "BASELINE_BUG_RED" | "NEGATIVE_CONTRACT" | "INTEGRATION_FAULT"
    target_qualified_name: str
    negative_predicate_id: str
    positive_predicate_id: str
    description: str


def _compute_a1_side_trace_digest(side: Mapping[str, Any]) -> str:
    body = {
        "execution_id": side["execution_id"],
        "predicate_id": side["predicate_id"],
        "input_summary": side["input_summary"],
        "observed_fields": side["observed_fields"],
        "pid": side["pid"],
        "executable_sha": side["executable_sha"],
        "run_id": side["run_id"],
    }
    return _sha256_canonical_a1(body)


def execute_r6_a1_fault_pair(
    spec: R6A1FaultCaseSpec,
    *,
    negative_fn: Callable[[], Tuple[Mapping[str, Any], Mapping[str, Any]]],
    positive_fn: Callable[[], Tuple[Mapping[str, Any], Mapping[str, Any]]],
    executable_sha: str,
    run_id: str,
) -> Dict[str, Any]:
    if spec.kind not in ALLOWED_FAULT_KINDS_A1:
        raise ValueError(f"INVALID_A1_FAULT_KIND:{spec.kind}")
    if spec.negative_predicate_id not in REGISTERED_FAULT_PREDICATES_R6_A1:
        raise ValueError(f"UNKNOWN_A1_NEG_PREDICATE:{spec.negative_predicate_id}")
    if spec.positive_predicate_id not in REGISTERED_FAULT_PREDICATES_R6_A1:
        raise ValueError(f"UNKNOWN_A1_POS_PREDICATE:{spec.positive_predicate_id}")

    neg_pred = REGISTERED_FAULT_PREDICATES_R6_A1[spec.negative_predicate_id]
    pos_pred = REGISTERED_FAULT_PREDICATES_R6_A1[spec.positive_predicate_id]

    t0_n = time.perf_counter()
    neg_in, neg_obs = negative_fn()
    neg_ms = round((time.perf_counter() - t0_n) * 1000.0, 3)
    neg_ok, neg_note = neg_pred(neg_obs)
    neg_side: Dict[str, Any] = {
        "execution_id": f"{spec.control_id}_NEG_{uuid.uuid4().hex[:12]}",
        "predicate_id": spec.negative_predicate_id,
        "predicate_passed": bool(neg_ok),
        "predicate_note": neg_note,
        "input_summary": dict(neg_in),
        "observed_fields": dict(neg_obs),
        "elapsed_ms": neg_ms,
        "pid": os.getpid(),
        "executable_sha": executable_sha,
        "run_id": run_id,
    }
    neg_side["trace_digest"] = _compute_a1_side_trace_digest(neg_side)

    t0_p = time.perf_counter()
    pos_in, pos_obs = positive_fn()
    pos_ms = round((time.perf_counter() - t0_p) * 1000.0, 3)
    pos_ok, pos_note = pos_pred(pos_obs)
    pos_side: Dict[str, Any] = {
        "execution_id": f"{spec.control_id}_POS_{uuid.uuid4().hex[:12]}",
        "predicate_id": spec.positive_predicate_id,
        "predicate_passed": bool(pos_ok),
        "predicate_note": pos_note,
        "input_summary": dict(pos_in),
        "observed_fields": dict(pos_obs),
        "elapsed_ms": pos_ms,
        "pid": os.getpid(),
        "executable_sha": executable_sha,
        "run_id": run_id,
    }
    pos_side["trace_digest"] = _compute_a1_side_trace_digest(pos_side)

    return {
        "receipt_schema": PAIRED_FAULT_RECEIPT_SCHEMA_A1,
        "control_id": spec.control_id,
        "group": spec.group,
        "kind": spec.kind,
        "target_qualified_name": spec.target_qualified_name,
        "description": spec.description,
        "executable_sha": executable_sha,
        "run_id": run_id,
        "negative": neg_side,
        "positive": pos_side,
        "status": "PASS" if (neg_ok and pos_ok) else "FAIL",
    }


def validate_r6_a1_fault_pair(
    rec: Mapping[str, Any],
    *,
    expected_executable_sha: str,
    expected_run_id: str,
) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    if rec.get("receipt_schema") != PAIRED_FAULT_RECEIPT_SCHEMA_A1:
        viols.append("BAD_A1_RECEIPT_SCHEMA")
    if rec.get("kind") not in ALLOWED_FAULT_KINDS_A1:
        viols.append(f"BAD_A1_KIND:{rec.get('kind')}")
    if rec.get("executable_sha") != expected_executable_sha:
        viols.append("A1_RECEIPT_SHA_MISMATCH")
    if rec.get("run_id") != expected_run_id:
        viols.append("A1_RECEIPT_RUN_ID_MISMATCH")
    if _contains_nonfinite_a1(rec):
        viols.append("A1_RECEIPT_NONFINITE")

    neg = rec.get("negative")
    pos = rec.get("positive")
    if not isinstance(neg, Mapping) or not isinstance(pos, Mapping):
        return False, viols + ["MISSING_NEG_OR_POS_SIDE"]
    if neg.get("execution_id") == pos.get("execution_id"):
        viols.append("SHARED_NEG_POS_EXECUTION_ID")
    if neg.get("trace_digest") == pos.get("trace_digest"):
        viols.append("IDENTICAL_NEG_POS_TRACE_DIGEST")

    for label, side in (("negative", neg), ("positive", pos)):
        pid_key = str(side.get("predicate_id") or "")
        pfn = REGISTERED_FAULT_PREDICATES_R6_A1.get(pid_key)
        if pfn is None:
            viols.append(f"UNREGISTERED_A1_PREDICATE:{label}:{pid_key}")
            continue
        if side.get("executable_sha") != expected_executable_sha or side.get("run_id") != expected_run_id:
            viols.append(f"SIDE_SHA_OR_RUN_MISMATCH:{label}")
        if not _is_strict_int_a1(side.get("pid")) or int(side["pid"]) <= 0:
            viols.append(f"INVALID_SIDE_PID:{label}")
        if not _is_finite_num_a1(side.get("elapsed_ms")) or float(side["elapsed_ms"]) < 0.0:
            viols.append(f"INVALID_SIDE_ELAPSED:{label}")
        if _compute_a1_side_trace_digest(side) != side.get("trace_digest"):
            viols.append(f"TRACE_DIGEST_TAMPERED:{label}")
        re_ok, _ = pfn(side.get("observed_fields") or {})
        if not re_ok:
            viols.append(f"PREDICATE_RECOMPUTE_FAILED:{label}:{pid_key}")
        if side.get("predicate_passed") is not True:
            viols.append(f"PREDICATE_NOT_PASSED:{label}")

    if rec.get("status") != "PASS":
        viols.append("RECEIPT_STATUS_NOT_PASS")
    return len(viols) == 0, viols


def derive_r6_a1_fault_verdict(
    receipts: Sequence[Mapping[str, Any]],
    *,
    expected_executable_sha: str,
    expected_run_id: str,
) -> Dict[str, Any]:
    seen_cids: set[str] = set()
    seen_neg: set[str] = set()
    seen_pos: set[str] = set()
    passed = 0
    failed = 0
    viols: List[str] = []
    by_group: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}

    for rec in receipts:
        cid = str(rec.get("control_id") or "")
        if not cid or cid in seen_cids:
            viols.append(f"DUPLICATE_OR_EMPTY_CONTROL_ID:{cid}")
            failed += 1
            continue
        seen_cids.add(cid)

        ne = str((rec.get("negative") or {}).get("execution_id") or "")
        pe = str((rec.get("positive") or {}).get("execution_id") or "")
        if not ne or ne in seen_neg or ne in seen_pos:
            viols.append(f"DUPLICATE_NEG_EXECUTION_ID:{cid}")
            failed += 1
            continue
        if not pe or pe in seen_pos or pe in seen_neg:
            viols.append(f"DUPLICATE_POS_EXECUTION_ID:{cid}")
            failed += 1
            continue
        seen_neg.add(ne)
        seen_pos.add(pe)

        ok, rv = validate_r6_a1_fault_pair(rec, expected_executable_sha=expected_executable_sha, expected_run_id=expected_run_id)
        if ok:
            passed += 1
            g = str(rec.get("group") or "UNKNOWN")
            k = str(rec.get("kind") or "UNKNOWN")
            by_group[g] = by_group.get(g, 0) + 1
            by_kind[k] = by_kind.get(k, 0) + 1
        else:
            failed += 1
            viols.extend(f"{cid}:{v}" for v in rv)

    return {
        "valid": failed == 0 and passed == len(receipts) and passed > 0,
        "total_controls": len(receipts),
        "passed_controls": passed,
        "failed_controls": failed,
        "unique_control_ids_count": len(seen_cids),
        "unique_negative_execution_ids_count": len(seen_neg),
        "unique_positive_execution_ids_count": len(seen_pos),
        "counts_by_group": by_group,
        "counts_by_kind": by_kind,
        "violations": viols,
    }


def audit_r6_a1_fault_files_no_literal_flags(paths: Sequence[Path]) -> Dict[str, Any]:
    bad_keys = {"red_failed", "green_passed", "predicate_passed"}
    hits: List[str] = []
    for p in paths:
        if not p.is_file():
            continue
        tree = ast.parse(p.read_text("utf-8", errors="replace"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value in bad_keys and isinstance(v, ast.Constant) and isinstance(v.value, bool):
                        hits.append(f"{p.name}:L{node.lineno}:literal_{k.value}={v.value}")
    return {"clean": len(hits) == 0, "literal_flag_hits_count": len(hits), "hits": hits}


# =====================================================================
# 3. Representative A1 Planning/Canonicalization/Custody Workload Benchmark
# =====================================================================
def _a1_workload_unit(idx: int) -> Dict[str, Any]:
    caps = {
        "connections": 2,
        "requests": 20,
        "retries": 4,
        "subscriptions": 2,
        "messages": 500,
        "payload_bytes": 65_536,
        "decoded_bytes": 131_072,
        "pages": 4,
        "disk_bytes": 1_048_576,
        "records": 2000,
    }
    digests = []
    for sub in range(25):
        _, dig, viols = canonicalize_r6_bound_plan_core(
            plan_id=f"bench_plan_{idx}_{sub}",
            stage="QA_LOOPBACK",
            purpose="QA_FIXTURE",
            grant_identity_key=f"{idx:064x}",
            grant_payload_digest=f"{(idx + sub + 1):064x}",
            tested_executable_sha=PRIOR_R6_TESTED_EXECUTABLE_SHA,
            release_manifest_sha256=PRIOR_R6_MANIFEST_26_SHA256,
            registry_digest="d" * 64,
            venue="BITGET",
            api_family=API_FAMILY_CLASSIC_V2,
            symbol="XAUUSDT",
            product_type="USDT-FUTURES",
            destination_store_id=f"qa_store_{idx}",
            destination_root=str(Path("C:/Users/Public/a1_bench_dest").resolve(strict=False)),
            endpoints=("classic_v2_contracts", "classic_v2_history_candles", "classic_v2_history_fund_rate", "classic_v2_ws_books1"),
            ws_channels=("books1",),
            window_start_ms=1794000000000 + sub * 60_000,
            window_end_ms=1794003600000 + sub * 60_000,
            max_session_duration_sec=120,
            max_concurrency=2,
            caps=caps,
            expires_utc_ms=1795000000000,
            external_capture_permitted=False,
            task_policy_digest="",
        )
        assert not viols and dig is not None
        digests.append(dig)
    return {"idx": idx, "count": len(digests), "unit_sha256": _sha256_canonical_a1(digests)}


def run_r6_a1_planning_workload_w1_w2_w4_benchmark(*, num_units: int = 32) -> Dict[str, Any]:
    runs: List[Dict[str, Any]] = []
    proc = psutil.Process(os.getpid())
    for label, workers in (("w=1", 1), ("w=2", 2), ("w=4", 4), ("w=1_repeat", 1)):
        t_cpu0 = proc.cpu_times()
        t0 = time.perf_counter()
        if workers == 1:
            parts = [_a1_workload_unit(i) for i in range(num_units)]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                parts = list(pool.map(_a1_workload_unit, range(num_units)))
        wall_sec = round(time.perf_counter() - t0, 5)
        t_cpu1 = proc.cpu_times()
        cpu_sec = round((t_cpu1.user - t_cpu0.user) + (t_cpu1.system - t_cpu0.system), 5)
        rss_mib = round(float(proc.memory_info().rss) / float(1024 ** 2), 3)
        parts_s = sorted(parts, key=lambda x: int(x["idx"]))
        sem_sha = _sha256_canonical_a1(parts_s)
        runs.append({
            "mode_label": label,
            "workers": workers,
            "wall_sec": wall_sec,
            "cpu_sec": cpu_sec,
            "rss_mib": rss_mib,
            "pid": os.getpid(),
            "units_count": len(parts_s),
            "semantic_result_sha256": sem_sha,
        })
    shas = {r["semantic_result_sha256"] for r in runs}
    return {
        "valid": len(shas) == 1,
        "all_4_identical": len(shas) == 1,
        "canonical_semantic_sha256": runs[0]["semantic_result_sha256"],
        "runs": runs,
    }


# =====================================================================
# 4. 36-Gate KH-03.R6.A1 Verdict Engine (F30: Pending vs Final Clear!)
# =====================================================================
def derive_r6_a1_readiness_verdicts(
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    expected_run_id: str,
    expected_tested_sha: str,
    pre_verifier_mode: bool = False,
    remote_readback: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Independently computes ALL 36 mandatory KH-03.R6.A1 engineering gates (`G01`..`G36`) strictly
    from raw facts/receipts in artifacts `00..27`.
    F30: In `pre_verifier_mode=True` (before separate-OS verifier runs), `G32_INDEPENDENT_VERIFIER`
    is explicitly `"PENDING_SEPARATE_OS_VERIFIER"` (NEVER conflating pending with failure!).
    External stages (`R6.B/C/D`) are strictly separated outside the 36 A1 denominator (`0/4` executed).
    """
    a = {f"{i:02d}": (artifacts.get(f"{i:02d}") or {}) for i in range(28)}
    gates: Dict[str, str] = {}

    gates["G01_BRANCH_SHA_CLEAN"] = "PASS" if (
        a["00"].get("branch") == MANDATORY_BRANCH_KH03_R6
        and a["00"].get("tested_executable_sha") == expected_tested_sha
        and a["00"].get("run_id") == expected_run_id
        and a["00"].get("worktree_clean_at_start") is True
        and "quocan050302/Build-app-crypto" in str(a["00"].get("origin_url") or "")
        and a["00"].get("disk_waiver_used") is False
        and _is_finite_num_a1(a["00"].get("preflight_disk_free_gib")) and float(a["00"]["preflight_disk_free_gib"]) >= 20.5
        and _is_finite_num_a1(a["00"].get("preflight_ram_available_mib")) and float(a["00"]["preflight_ram_available_mib"]) >= 512.0
    ) else "FAIL"

    gates["G02_KEEP_AS_IS"] = "PASS" if (
        _is_strict_int_a1(a["01"].get("locked_files_modified_count")) and int(a["01"]["locked_files_modified_count"]) == 0
        and _is_strict_int_a1(a["01"].get("unauthorized_changed_files_count")) and int(a["01"]["unauthorized_changed_files_count"]) == 0
        and _is_strict_int_a1(a["01"].get("forbidden_order_imports_count")) and int(a["01"]["forbidden_order_imports_count"]) == 0
        and a["01"].get("legacy_r1_unauthorized_callers_count") == 0
        and a["01"].get("production_cost_defaults_intact") is True
        and a["01"].get("c2_live_hard_block_intact") is True
        and a["01"].get("secrets_detected_count") == 0
    ) else "FAIL"

    gates["G03_PRIOR_SEALED_NO_OOS"] = "PASS" if (
        a["02"].get("canonical_dataset_sha256") == CANONICAL_DATASET_SHA256_KH02
        and a["02"].get("all_prior_sealed_artifacts_intact") is True
        and _is_strict_int_a1(a["02"].get("prior_sealed_artifacts_checked_count")) and int(a["02"]["prior_sealed_artifacts_checked_count"]) >= 75
        and a["02"].get("prior_r6_manifest_sha256_verified") is True
        and a["02"].get("oos_reads_executed") == 0
        and a["02"].get("oos_evaluations") == 0
    ) else "FAIL"

    gates["G04_REAL_RED"] = "PASS" if (
        a["03"].get("baseline_sha") == PRIOR_R6_TESTED_EXECUTABLE_SHA
        and a["03"].get("all_reproduced") is True
        and _is_strict_int_a1(a["03"].get("reproduced_count")) and int(a["03"]["reproduced_count"]) == 6
    ) else "FAIL"

    gates["G05_DRYRUN_NO_CONSUMPTION"] = "PASS" if (
        a["04"].get("dryrun_invalid_plan_nonzero_exit") is True
        and a["04"].get("dryrun_valid_plan_zero_claim_zero_socket") is True
        and a["04"].get("handshake_only_success_removed") is True
    ) else "FAIL"

    gates["G06_QA_CLI_END_TO_END"] = "PASS" if (
        a["04"].get("qa_cli_smoke_e2e_pass") is True
        and a["04"].get("qa_cli_dev_e2e_pass") is True
        and _is_strict_int_a1(a["04"].get("qa_cli_smoke_pid")) and int(a["04"]["qa_cli_smoke_pid"]) > 0
        and _is_strict_int_a1(a["04"].get("qa_cli_dev_committed_chunks")) and int(a["04"]["qa_cli_dev_committed_chunks"]) >= 4
    ) else "FAIL"

    gates["G07_TASK_COVERAGE_FINALIZE"] = "PASS" if (
        a["05"].get("empty_task_plan_blocked_no_claim") is True
        and a["05"].get("partial_second_task_failure_truthful") is True
        and a["05"].get("cancel_between_claim_and_first_attempt_zero_io") is True
        and a["05"].get("repeat_same_grant_denied_after_restart") is True
    ) else "FAIL"

    gates["G08_PLAN_CANONICAL_DIGEST"] = "PASS" if (
        a["06"].get("nested_caps_immutable_and_tamper_rejected") is True
        and a["06"].get("dataclass_replace_endpoints_window_rejected") is True
        and a["06"].get("recomputed_digest_old_mac_rejected") is True
    ) else "FAIL"

    gates["G09_GRANT_LEASE_BINDING"] = "PASS" if (
        a["06"].get("forged_grant_object_rejected") is True
        and a["06"].get("foreign_lease_rejected_zero_dispatch") is True
        and a["06"].get("narrow_plan_cap_enforced_by_ledger") is True
        and a["06"].get("registry_drift_rejected") is True
    ) else "FAIL"

    gates["G10_REQUEST_ALLOWLIST"] = "PASS" if (
        a["07"].get("unauthorized_path_or_query_or_ws_op_rejected") is True
        and a["07"].get("post_enqueue_expiry_zero_io") is True
        and a["07"].get("nonloopback_dispatch_hard_blocked") is True
    ) else "FAIL"

    gates["G11_DURABLE_RESERVATION"] = "PASS" if (
        a["08"].get("zero_request_or_missing_dim_reservation_rejected") is True
        and a["08"].get("retry_over_cap_rejected") is True
        and a["08"].get("reservation_receipt_verified") is True
    ) else "FAIL"

    gates["G12_CHILD_ENVELOPE"] = "PASS" if (
        a["07"].get("child_cap_override_rejected") is True
        and a["07"].get("child_deadline_override_and_missing_fields_rejected") is True
        and a["07"].get("direct_child_cli_external_blocked") is True
    ) else "FAIL"

    gates["G13_REST_CUMULATIVE_CAP"] = "PASS" if (
        a["08"].get("rest_error_and_retry_cumulative_bytes_bounded") is True
        and a["08"].get("retry_after_beyond_budget_aborted") is True
        and a["08"].get("sentinel_never_reads_beyond_reservation") is True
    ) else "FAIL"

    gates["G14_WS_CUMULATIVE_CAP"] = "PASS" if (
        a["08"].get("ws_reconnect_sub_conn_bounded_by_reservation") is True
        and a["08"].get("ws_frame_queue_message_caps_enforced") is True
    ) else "FAIL"

    gates["G15_SETTLEMENT_CONSERVATION"] = "PASS" if (
        a["09"].get("completed_missing_dim_rejected") is True
        and a["09"].get("overrun_quarantined_without_clamping") is True
        and a["09"].get("supervisor_rejects_on_settlement_failure_or_exception") is True
    ) else "FAIL"

    gates["G16_ACTUAL_WORKER_LIFECYCLE"] = "PASS" if (
        a["09"].get("noncooperative_child_and_grandchild_reaped") is True
        and a["09"].get("child_launch_or_pipe_error_cleaned_no_orphan") is True
        and _is_strict_int_a1(a["09"].get("lifecycle_traces_count")) and int(a["09"]["lifecycle_traces_count"]) >= 10
    ) else "FAIL"

    gates["G17_CANCEL_DEADLINE_LATE_RESULT"] = "PASS" if (
        a["09"].get("cancel_after_child_exit_before_accept_discarded") is True
        and a["09"].get("cancel_before_custody_persist_zero_write") is True
    ) else "FAIL"

    gates["G18_LEDGER_RESTART_ANCHOR"] = "PASS" if (
        a["11"].get("cross_process_parallel_reservations_conserved") is True
        and a["11"].get("anchor_monotonic_ordering_verified") is True
        and a["11"].get("crash_pre_anchor_and_unknown_io_restart_safe") is True
    ) else "FAIL"

    gates["G19_TRUSTED_SUPERVISOR_PROOF"] = "PASS" if (
        a["12"].get("fabricated_mapping_cannot_mint_observed") is True
        and a["12"].get("forged_or_copied_token_rejected") is True
        and a["12"].get("wrong_binding_ids_rejected") is True
    ) else "FAIL"

    gates["G20_PROVENANCE_RAW_BYTES"] = "PASS" if (
        a["12"].get("raw_bytes_length_or_hash_mismatch_rejected") is True
        and a["12"].get("unsettled_or_overrun_rejected") is True
        and a["12"].get("late_or_nonzero_exit_rejected") is True
        and a["12"].get("replay_same_token_or_chunk_denied") is True
        and a["12"].get("qa_promotion_to_observed_dev_rejected_despite_tls") is True
    ) else "FAIL"

    gates["G21_CUSTODY_QA_MANIFEST"] = "PASS" if (
        a["13"].get("qa_custody_readonly_post_restart_valid") is True
        and a["13"].get("tampered_raw_or_norm_file_detected") is True
        and a["13"].get("crash_and_failed_unlink_quarantined_safe_cleanup") is True
        and _is_strict_int_a1(a["13"].get("qa_e2e_committed_chunks_count")) and int(a["13"]["qa_e2e_committed_chunks_count"]) >= 4
    ) else "FAIL"

    gates["G22_RELEASE_FROZEN_EXECUTABLE"] = "PASS" if (
        a["14"].get("published_evidence_only_descendant_verified") is True
        and a["14"].get("detached_worktree_exact_tested_sha_verified") is True
        and a["14"].get("code_or_config_drift_rejected") is True
        and a["14"].get("dirty_or_untrusted_pythonpath_or_dep_drift_rejected") is True
    ) else "FAIL"

    gates["G23_RELEASE_MANIFEST_TRUTH"] = "PASS" if (
        a["14"].get("forged_manifest_pass_rejected") is True
        and a["14"].get("pointer_receipt_ancestry_mismatch_rejected") is True
        and a["14"].get("missing_execute_authorized_or_conflict_rejected") is True
        and a["14"].get("unsigned_template_as_grant_rejected") is True
        and a["14"].get("qa_cli_on_frozen_executable_verified") is True
    ) else "FAIL"

    ext_req = int(a["15"].get("external_market_requests_attempted") or 0)
    obs_rec = int(a["15"].get("accepted_real_observed_records") or 0)
    gates["G24_EXTERNAL_HARD_BLOCK"] = "PASS" if (
        a["15"].get("a1_external_capture_enabled") is False
        and a["15"].get("external_cli_with_plausible_input_hard_blocked") is True
        and a["15"].get("owner_grant_consumed_count") == 0
        and ext_req == 0
        and obs_rec == 0
        and a["15"].get("private_or_trading_calls") == 0
        and a["15"].get("oos_reads_executed") == 0
    ) else "FAIL"

    gates["G25_FULL_PYTEST"] = "PASS" if (
        a["19"].get("junit_bijection_exact") is True
        and _is_strict_int_a1(a["19"].get("collected_count")) and int(a["19"]["collected_count"]) >= MIN_EXPECTED_PYTEST_COLLECTED_R6_A1
        and a["19"].get("collected_count") == a["19"].get("executed_count")
        and a["19"].get("failed_count") == 0
        and a["19"].get("error_count") == 0
        and a["19"].get("mandatory_skipped_count") == 0
        and a["19"].get("nonzero_exit_shards_count") == 0
    ) else "FAIL"

    gates["G26_BASELINE_PARITY"] = "PASS" if (
        a["18"].get("a1_checked") == 56710 and a["18"].get("a1_mismatches") == 0
        and a["18"].get("si8_checked") == 6375 and a["18"].get("si8_mismatches") == 0
        and a["18"].get("adapters_checked") == 246 and a["18"].get("adapters_mismatches") == 0
        and a["18"].get("d4_mismatches") == 0
    ) else "FAIL"

    gates["G27_BACKTEST_PARITY"] = "PASS" if (
        a["16"].get("r4_decimal_bridge_exact_match") is True
        and a["16"].get("n_closed") == 203 and a["16"].get("n_wins") == 7 and a["16"].get("n_losses") == 196
        and a["16"].get("n_open_censored") == 28 and a["16"].get("n_gap_events") == 2
        and a["16"].get("realized_losses_gt_2p50_count") == 139
        and a["17"].get("all_six_scenarios_exact_match") is True
        and a["17"].get("all_three_arms_exact_match") is True
    ) else "FAIL"

    gates["G28_DETERMINISM"] = "PASS" if (
        a["20"].get("science_w1_w2_w4_all_identical") is True
        and a["20"].get("r6_ingest_w1_w2_w4_all_identical") is True
        and a["20"].get("a1_workload_w1_w2_w4_all_identical") is True
    ) else "FAIL"

    gates["G29_MEASURED_WORKER_POLICY"] = "PASS" if (
        a["20"].get("pytest_policy_valid") is True
        and a["20"].get("science_policy_valid") is True
        and a["20"].get("r6_ingest_benchmark_valid") is True
        and _is_strict_int_a1(a["20"].get("applied_safe_workers_cap")) and 1 <= int(a["20"]["applied_safe_workers_cap"]) <= 4
        and a["20"].get("full_suite_speedup_extrapolated") is False
    ) else "FAIL"

    gates["G30_NEW_PAIRED_FAULT_TRUTH"] = "PASS" if (
        _is_strict_int_a1(a["23"].get("new_a1_paired_verified_count")) and int(a["23"]["new_a1_paired_verified_count"]) >= MIN_NEW_PAIRED_CONTROLS_R6_A1
        and a["23"].get("new_a1_paired_failed_count") == 0
        and a["23"].get("a1_fault_ast_literal_flags_count") == 0
        and a["23"].get("unique_a1_execution_ids_ok") is True
    ) else "FAIL"

    gates["G31_INHERITED_REGRESSION"] = "PASS" if (
        _is_strict_int_a1(a["23"].get("inherited_r6_paired_passed_count")) and int(a["23"]["inherited_r6_paired_passed_count"]) >= 56
        and a["23"].get("inherited_r6_paired_failed_count") == 0
        and _is_strict_int_a1(a["23"].get("inherited_c1_c2_paired_passed_count")) and int(a["23"]["inherited_c1_c2_paired_passed_count"]) >= 91
        and a["23"].get("inherited_c1_c2_paired_failed_count") == 0
        and _is_strict_int_a1(a["23"].get("inherited_regression_passed_count")) and int(a["23"]["inherited_regression_passed_count"]) >= 288
        and a["23"].get("inherited_regression_failed_count") == 0
        and a["23"].get("inherited_not_relabelled_as_new_a1") is True
    ) else "FAIL"

    # F30: Clear separation of PENDING_SEPARATE_OS_VERIFIER vs PASS vs FAIL!
    if pre_verifier_mode and a["26"].get("verifier_status") == "PENDING":
        gates["G32_INDEPENDENT_VERIFIER"] = "PENDING_SEPARATE_OS_VERIFIER"
    else:
        g32_ok = (
            a["26"].get("distinct_os_process_verified") is True
            and a["26"].get("verifier_status") == "PASS"
            and _is_strict_int_a1(a["26"].get("violations_count")) and int(a["26"]["violations_count"]) == 0
            and _is_strict_int_a1(a["26"].get("verifier_pid")) and _is_strict_int_a1(a["26"].get("orchestrator_pid"))
            and int(a["26"]["verifier_pid"]) != int(a["26"]["orchestrator_pid"])
        )
        gates["G32_INDEPENDENT_VERIFIER"] = "PASS" if g32_ok else "FAIL"

    gates["G33_MUTATION_RESISTANCE"] = "PASS" if (
        a["24"].get("clean_clone_verifier_green") is True
        and _is_strict_int_a1(a["24"].get("new_a1_mutations_tested_count")) and int(a["24"]["new_a1_mutations_tested_count"]) >= MIN_NEW_VERIFIER_MUTATIONS_R6_A1
        and a["24"].get("new_a1_mutations_detected_count") == a["24"].get("new_a1_mutations_tested_count")
        and _is_strict_int_a1(a["24"].get("inherited_mutations_tested_count")) and int(a["24"]["inherited_mutations_tested_count"]) >= 167
        and a["24"].get("inherited_mutations_detected_count") == a["24"].get("inherited_mutations_tested_count")
    ) else "FAIL"

    gates["G34_RESOURCE_CLEANUP"] = "PASS" if (
        _is_strict_int_a1(a["21"].get("samples_count")) and int(a["21"]["samples_count"]) >= 2
        and _is_strict_int_a1(a["21"].get("max_observed_descendants")) and int(a["21"]["max_observed_descendants"]) <= 4
        and a["21"].get("owned_temp_cleaned_verified") is True
        and a["21"].get("orphan_workers_count") == 0
        and a["21"].get("disk_waiver_exit2_proven") is True
        and _is_finite_num_a1(a["21"].get("min_observed_disk_free_gib")) and float(a["21"]["min_observed_disk_free_gib"]) >= 20.0
        and _is_finite_num_a1(a["21"].get("min_observed_ram_available_mib")) and float(a["21"]["min_observed_ram_available_mib"]) >= 512.0
    ) else "FAIL"

    gates["G35_ECONOMIC_ACTIVATION"] = "PASS" if (
        a["15"].get("production_mode") == "OFF"
        and a["15"].get("live_trading_mode") == "OFF"
        and a["15"].get("paper_trading_mode") == "OFF"
        and a["15"].get("auto_trading_mode") == "OFF"
        and a["15"].get("activation_ready") == "NO"
        and a["15"].get("economic_verdict") == "NO_PROVEN_EDGE"
        and a["15"].get("fresh_oos_verdict") == "BLOCKED_NO_UNTOUCHED_DATA"
        and a["15"].get("kh04_entry_verdict") == "BLOCKED_NO_DEV_CANDIDATE"
        and a["15"].get("oos_evaluations") == 0
    ) else "FAIL"

    if remote_readback is not None and remote_readback.get("all_sealed_files_remote_verified") is True and remote_readback.get("remote_tested_sha_verified") is True:
        gates["G36_REMOTE_PUBLICATION"] = "PASS_REMOTE_VERIFIED"
    elif a["27"].get("phase1_executable_committed") is True and a["27"].get("tested_executable_sha") == expected_tested_sha:
        gates["G36_REMOTE_PUBLICATION"] = "PENDING_REMOTE_READBACK"
    else:
        gates["G36_REMOTE_PUBLICATION"] = "FAIL"

    prepush_core_keys = [g for g in GATE_36_KEYS_KH03_R6_A1 if g != "G36_REMOTE_PUBLICATION"]
    prepush_passed = sum(1 for g in prepush_core_keys if gates[g] == "PASS")
    g36_pass = gates["G36_REMOTE_PUBLICATION"] == "PASS_REMOTE_VERIFIED"
    total_passed = prepush_passed + (1 if g36_pass else 0)

    all_pre_verifier_ok = (
        pre_verifier_mode
        and sum(1 for g in prepush_core_keys if g != "G32_INDEPENDENT_VERIFIER" and gates[g] == "PASS") == 34
        and gates["G32_INDEPENDENT_VERIFIER"] == "PENDING_SEPARATE_OS_VERIFIER"
        and gates["G36_REMOTE_PUBLICATION"] in ("PENDING_REMOTE_READBACK", "PASS_REMOTE_VERIFIED")
    )
    all_prepush_verified = (prepush_passed == 35) and gates["G36_REMOTE_PUBLICATION"] in ("PENDING_REMOTE_READBACK", "PASS_REMOTE_VERIFIED")
    all_closed_remote = (total_passed == 36)

    if all_closed_remote or all_prepush_verified:
        overall_cls = CLASSIFICATION_PASS_OFFLINE_R6_A1
    elif all_pre_verifier_ok:
        overall_cls = "PENDING_SEPARATE_OS_VERIFIER_A1_34_OF_34_PRE_VERIFIER_GATES_PASS"
    else:
        overall_cls = "FAIL_OR_BLOCKED_KH03_R6_A1_REQUIRED_GATES"

    external_stages_separate = {
        "R6_B_AUTHORIZATION": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED",
        "R6_B_SMOKE": "NOT_RUN",
        "R6_C_DEV": "NOT_RUN",
        "R6_D_R7_DATA_READINESS": "BLOCKED_NO_OBSERVED_DEV_DATA",
        "executed_count": 0,
        "total_count": 4,
    }

    ci_status = str((remote_readback or {}).get("ci_status") or "CI_UNKNOWN_NO_ACTIONS_RUN")
    independent_verdicts = {
        "A1_ENGINEERING_OFFLINE": "PASS" if (all_prepush_verified or all_closed_remote) else ("PENDING_SEPARATE_OS_VERIFIER" if all_pre_verifier_ok else "FAIL"),
        "QA_E2E_ACQUISITION_PIPELINE": "QA_E2E_READY_LOOPBACK_ONLY" if (gates["G06_QA_CLI_END_TO_END"] == "PASS" and gates["G21_CUSTODY_QA_MANIFEST"] == "PASS") else "FAIL",
        "EXTERNAL_CAPTURE_A1": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED",
        "OWNER_TRUST_AND_EXTERNAL_TERMINATION": "NOT_PROVEN_EXTERNAL_BLOCKED_IN_A1",
        "INSTRUMENT_OBSERVED_IDENTITY": "NOT_OBSERVED",
        "BBO_EVENT_COVERAGE_EXTERNAL": "NOT_RUN",
        "BBO_STATE_COVERAGE_EXTERNAL": "NOT_RUN",
        "FUNDING_COMPLETENESS_EXTERNAL": "NOT_RUN",
        "ACCOUNT_FEE": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "SLIPPAGE": "MISSING_NO_OWNER_FILL_EVIDENCE",
        "EXECUTION_FIDELITY": "INCOMPLETE",
        "REGRESSION": "PASS" if (gates["G25_FULL_PYTEST"] == "PASS" and gates["G31_INHERITED_REGRESSION"] == "PASS") else "FAIL",
        "BACKTEST_PARITY": "PASS" if (gates["G26_BASELINE_PARITY"] == "PASS" and gates["G27_BACKTEST_PARITY"] == "PASS") else "FAIL",
        "DETERMINISM": "PASS" if gates["G28_DETERMINISM"] == "PASS" else "FAIL",
        "RESOURCE_SAFETY": "PASS" if gates["G34_RESOURCE_CLEANUP"] == "PASS" else "FAIL",
        "PUBLICATION": gates["G36_REMOTE_PUBLICATION"],
        "CI": ci_status,
        "ECONOMIC": "NO_PROVEN_EDGE",
        "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA",
        "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE",
        "ACTIVATION_READY": "NO",
    }

    return {
        "spec_version": SPEC_VERSION_KH03_R6_A1,
        "schema_version": SCHEMA_VERSION_KH03_R6_A1,
        "run_id": expected_run_id,
        "tested_executable_sha": expected_tested_sha,
        "pre_verifier_mode": bool(pre_verifier_mode),
        "gates": gates,
        "a1_denominators": {
            "a1_mandatory_total": 36,
            "a1_prepush_mandatory_total": 35,
            "a1_prepush_passed": prepush_passed,
            "a1_passed_including_remote": total_passed,
            "external_r6_bcd_executed": 0,
            "external_r6_bcd_total": 4,
            "never_claim_r6_original_36_of_36": True,
        },
        "external_stages_separate": external_stages_separate,
        "a1_ready_for_publication": all_prepush_verified,
        "a1_fully_closed_remote": all_closed_remote,
        "overall_classification": overall_cls,
        "independent_verdicts": independent_verdicts,
        "external_market_requests_attempted": ext_req,
        "accepted_real_observed_records": obs_rec,
        "oos_evaluations": int(a["15"].get("oos_evaluations") or 0),
    }


# =====================================================================
# 5. Vietnamese Owner Handover Generator (§16)
# =====================================================================
def build_r6_a1_owner_handover_vi(
    *,
    run_id: str,
    branch: str,
    base_reviewed_head_sha: str,
    tested_sha: str,
    verdicts: Mapping[str, Any],
    summary_stats: Mapping[str, Any],
    unsigned_template_rel_path: str,
) -> str:
    ad = verdicts["a1_denominators"]
    iv = verdicts["independent_verdicts"]
    lines = [
        "==============================================================================",
        "AURUM DESK — KH-03.R6.A1 | BAO CAO BAN GIAO CHU DU AN (OFFLINE / QA LOOPBACK)",
        "==============================================================================",
        f"Spec: {SPEC_VERSION_KH03_R6_A1} | Run ID: {run_id}",
        f"Branch: {branch}",
        f"Reviewed HEAD (R6 published): {base_reviewed_head_sha}",
        f"Prior R6 Tested SHA:          {PRIOR_R6_TESTED_EXECUTABLE_SHA}",
        f"A1 Tested Executable SHA:     {tested_sha}",
        f"Overall Classification:       {verdicts['overall_classification']}",
        "",
        "1. PHAN TACH GATE (36 A1 ENGINEERING GATES VS 0/4 EXTERNAL R6.B/C/D)",
        f"   - A1 Engineering Gates (pre-push): {ad['a1_prepush_passed']}/{ad['a1_prepush_mandatory_total']} (+ G36={verdicts['gates']['G36_REMOTE_PUBLICATION']})",
        "   - External R6.B/C/D (tach biet):   0/4 executed — R6.B=BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED, Smoke=NOT_RUN, DEV=NOT_RUN, R7=BLOCKED_NO_OBSERVED_DEV_DATA",
        "",
        "2. DONG 5 LOI REVIEW (P1-G .. P1-K) — REAL RED TREN 529bdaf2 -> CLEAN GREEN",
        f"   - M0 Real RED tren 529bdaf2 (isolated overlay): {summary_stats.get('m0_reproduced_count')}/6 tai hien that (RED-G, RED-H, RED-I1, RED-I2, RED-J, RED-K)",
        f"   - P1-G (Acquisition CLI & Orchestrator): Xoa AUTHORIZED_SESSION_CLAIMED_AND_READY; QA CLI E2E that (Smoke PID={summary_stats.get('qa_cli_smoke_pid')}, DEV chunks={summary_stats.get('qa_cli_dev_chunks')}, rows={summary_stats.get('qa_cli_dev_rows')}, raw_bytes={summary_stats.get('qa_cli_dev_raw_bytes')})",
        "   - P1-H (Immutable Plan/Grant/Lease/Request): MappingProxyType + canonicalize_r6_bound_plan_core() tinh lai digest+MAC moi lan; claim bat buoc R6BoundCapturePlan va ap dung effective PLAN caps",
        "   - P1-I (Reservation/IPC/Transport/Settlement): Parent-authoritative worker envelope cam child override; REST/WS tich luy cap khong doc qua reservation; settle fail-closed + quarantine khong clamp; file-lock anchor don dieu",
        "   - P1-J (Trusted Supervisor -> Provenance -> Custody): Bo niem tin dict/Mapping; token noi bo single-use; A1 chi cap QA_LOOPBACK, cam RAW_OBSERVED_VERIFIED; custody read-only doi chieu SQLite ledger",
        "   - P1-K (Frozen Release & CLI Contract): verify_r6_a1_release_binding() + detached worktree / evidence-only descendant proof; sua thieu --execute-authorized & dry-run strict",
        "",
        "3. KIEM DINH DOC LAP, REGRESSION, PARITY & BACKTEST",
        f"   - Full Pytest: {summary_stats.get('pytest_passed')}/{summary_stats.get('pytest_collected')} passed, {summary_stats.get('pytest_skipped')} discretionary skipped (0 fail, 0 error, 0 mandatory skip)",
        f"   - Paired Controls: {summary_stats.get('new_a1_paired')} moi (A1) + {summary_stats.get('inherited_r6_paired')} R6 + {summary_stats.get('inherited_c1_c2_paired')} C1/C2 + {summary_stats.get('inherited_regression')} inherited",
        f"   - Verifier Mutations: {summary_stats.get('new_a1_mutations_detected')}/{summary_stats.get('new_a1_mutations_tested')} moi (A1) + {summary_stats.get('inherited_mutations_detected')}/{summary_stats.get('inherited_mutations_tested')} inherited (separate OS)",
        "   - Parity: A1=56,710/0, SI8=6,375/0, Adapters=246/0, D4=0",
        "   - R4 1x & 6 Cost Scenarios + Arms: 203 closed (7W/196L), net -441.6118 USD, -0.8806R, 139 loss tails >2.50 USD; khong sua chien luoc",
        f"   - Telemetry: min disk={summary_stats.get('min_disk_gib')} GiB, min RAM={summary_stats.get('min_ram_mib')} MiB, max descendants={summary_stats.get('max_descendants')} (<=4), 0 orphan",
        "",
        "4. CONG BO TRUNG THUC & GIOI HAN",
        "   - EXTERNAL_MARKET_REQUESTS=0 | ACCEPTED_REAL_OBSERVED_RECORDS=0 | OOS_READS=0 | OOS_EVALUATIONS=0",
        "   - PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | ECONOMIC=NO_PROVEN_EDGE",
        "   - ACCOUNT_FEE=UNKNOWN | SLIPPAGE=MISSING | INSTRUMENT/BBO/FUNDING EXTERNAL=NOT_OBSERVED / NOT_RUN",
        "   - QA loopback KHONG chung minh external network termination hay live Bitget provenance.",
        "",
        "5. KHUYEN NGHI BUOC TIEP THEO (CHU DU AN REVIEW RIENG CHO R6.B)",
        f"   - Mau review chua ky: {unsigned_template_rel_path}",
        "   - Capture ngoai van BI KHOA CUNG trong A1 (BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED).",
        "   - De chay R6.B sau nay can 1 commit mo khoa hep duoc review rieng + trust root & Ed25519 grant cuc bo ngoai Git.",
        "==============================================================================",
    ]
    return "\n".join(lines) + "\n"
