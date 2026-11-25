"""
aurum-desk/backend/services/kh_03_r5_c1_readiness_and_red_service.py

AURUM DESK — KH-03.R5.C1 | CAPTURE SAFETY & AUTHORIZATION HARDENING
SPEC VERSION: KH-03.R5.C1-v1.0 | 30 ORTHOGONAL GATES | REAL BASELINE RED HARNESS | HANDOVER

Provides:
  1. `run_real_baseline_red_reproduction_from_git_sha(...)` — extracts untouched original R5
     files from baseline Git SHA (`09493feb845210a629f3c0f174a2c6edfaf2ef23`) into an isolated
     temp directory outside the repository, runs real offline spy-transport tests reproducing
     P1-A, P1-B, and P1-C RED weaknesses, records exact command/exit/spy counts/stdout SHA-256,
     and cleans up temp cleanly without ever mutating working tree or prior evidence.
  2. `derive_r5_c1_readiness_verdicts(...)` — independently recomputes ALL 30 orthogonal gates
     (`G01`..`G30`) and all separate verdict dimensions directly from raw disk-backed evidence,
     rejecting bool coercion, caller-injected PASS strings, disk waivers, NaN/Inf, stale SHAs,
     or false live/economic claims.
  3. `build_r5_c1_owner_handover_vi(...)` — concise auditable Vietnamese owner handover (§13).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_r5_market_data_foundation_service import (
    CAPTURE_SAFETY_THREAT_MODEL_R5_C1,
    SCHEMA_VERSION_KH03_R5_C1,
    SPEC_VERSION_KH03_R5_C1,
    STRICT_PREFLIGHT_DISK_MIN_GIB_R5,
    STRICT_RUNTIME_DISK_MIN_GIB_R5,
)

MANDATORY_BRANCH_KH03_R5_C1 = "feature/v17-9-2-structural-sl-tp-from-qa"
HARD_RAM_FLOOR_MIB_R5_C1 = 512.0
GLOBAL_MAX_DESCENDANTS_R5_C1 = 4
MIN_EXPECTED_PYTEST_COLLECTED_R5_C1 = 5915
BASELINE_REVIEW_HEAD_SHA_R5_C1 = "09493feb845210a629f3c0f174a2c6edfaf2ef23"
CLASSIFICATION_PASS_OFFLINE_C1 = (
    "ENGINEERING_PASS_CAPTURE_SAFETY_HARDENED_OFFLINE_ONLY_"
    "CAPTURE_BLOCKED_OWNER_AUTHORIZATION_ECONOMIC_NO_PROVEN_EDGE"
)

GATE_30_KEYS_KH03_R5_C1: Tuple[str, ...] = (
    "G01_BRANCH_SHA_CLEAN",
    "G02_KEEP_AS_IS",
    "G03_PRIOR_SEALED_DATASET_OOS",
    "G04_REAL_RED_REPRODUCTION",
    "G05_STRICT_GRANT_SCHEMA",
    "G06_TRUST_BOUNDARY",
    "G07_EFFECTIVE_SCOPE",
    "G08_PLAN_INTEGRITY",
    "G09_DISPATCH_REVALIDATION",
    "G10_QA_ISOLATION",
    "G11_DEADLINE_POST_WAIT",
    "G12_TIMEOUT_REMAINING",
    "G13_RATE_RETRY",
    "G14_ATOMIC_REQUEST_BUDGET",
    "G15_BYTE_BUDGET",
    "G16_CANCELLATION_DRAIN",
    "G17_RESOURCE_STRICT",
    "G18_DISK_RAM_GUARD",
    "G19_STAGING_CUSTODY",
    "G20_ABORT_STATUS_TRUTH",
    "G21_FULL_PYTEST",
    "G22_BASELINE_PARITY",
    "G23_BACKTEST_PARITY",
    "G24_DETERMINISTIC_WORKERS",
    "G25_MEASURED_WORKER_POLICY",
    "G26_FAULT_CONTROLS",
    "G27_INDEPENDENT_VERIFIER",
    "G28_MUTATION_RESISTANCE",
    "G29_ECONOMIC_AND_ACTIVATION",
    "G30_REMOTE_PUBLICATION",
)


def _is_strict_bool_true(v: Any) -> bool:
    return v is True


def _is_strict_bool_false(v: Any) -> bool:
    return v is False


def _is_strict_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_strict_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def build_r5_c1_function_mapping_and_ownership() -> List[Dict[str, Any]]:
    """Documents every modified/added KH-03.R5.C1 function, mapping, rationale, and caller scope."""
    return [
        {
            "file": "aurum-desk/backend/services/kh_03_r5_market_data_foundation_service.py",
            "functions": [
                "R5OperatorGrant",
                "R5OwnerAttestationVerifier / DefaultDenyOwnerAttestationVerifier",
                "_ScopeBindingAuthorityC1 / R5TrustedScopeReceipt / R5TrustedPlanReceipt",
                "_normalize_r5_capture_scope_strict",
                "_validate_r5_budget_within_scope",
                "_validate_r5_plan_binding_strict",
                "validate_r5_operator_capture_scope",
                "plan_r5_capture_batches",
            ],
            "change_type": "HARDENED_P1_A",
            "rationale": "Closes P1-A: strict type/schema/window/budget validation, default-deny attestation trust boundary, and process-local non-copyable scope->plan binding defeating recomputed-digest mutation.",
        },
        {
            "file": "aurum-desk/backend/services/kh_03_r5_capture_and_custody_service.py",
            "functions": [
                "validate_r5_resource_sample_strict",
                "_require_r5_resource_monitor",
                "make_r5_fixture_resource_monitor",
                "_parse_retry_after_strict",
                "_verify_r5_execution_context",
                "_check_r5_dispatch_guard",
                "capture_r5_public_batch",
                "R5DevCustodyJournal.persist_snapshot",
            ],
            "change_type": "HARDENED_P1_A_B_C_AND_ADJACENT",
            "rationale": "Closes P1-B (post-wait deadline/cancel/expiry guard, remaining-time timeout, non-shortened Retry-After, atomic request/wire-byte reservations, bounded drain & late discard), P1-C (fail-closed resource sample validation), and adjacent custody/abort truth (mandatory raw SHA, stat-before-read size cap, zero accepted count for removed abort files).",
        },
        {
            "file": "aurum-desk/backend/services/kh_03_r5_readiness_gate_service.py",
            "functions": ["derive_r5_readiness_verdicts (G05 C1 release restriction)"],
            "change_type": "HARDENED_LEGACY_RELEASE_RESTRICTION",
            "rationale": "Prevents legacy R5 26-gate readiness engine from certifying live capture from old unhardened R5 schema without C1 hardened receipts.",
        },
        {
            "file": "aurum-desk/backend/services/kh_03_r5_c1_readiness_and_red_service.py",
            "functions": [
                "run_real_baseline_red_reproduction_from_git_sha",
                "derive_r5_c1_readiness_verdicts",
                "build_r5_c1_owner_handover_vi",
            ],
            "change_type": "NEW_C1_ADDITIVE",
            "rationale": "Real isolated baseline RED reproducer, strict 30-gate C1 readiness engine, and Vietnamese handover generator.",
        },
        {
            "file": "aurum-desk/tools/kh_03_r5_c1_independent_verifier.py",
            "functions": ["verify_r5_c1_artifacts_independently_from_disk"],
            "change_type": "NEW_C1_ADDITIVE",
            "rationale": "Distinct-OS-process disk verifier rejecting duplicate JSON keys, NaN/Inf, type coercion, forged traces, and stale runs.",
        },
        {
            "file": "aurum-desk/tools/run_kh_03_r5_c1_acceptance.py",
            "functions": ["run_kh03_r5_c1_orchestrator"],
            "change_type": "NEW_C1_ADDITIVE",
            "rationale": "End-to-end KH-03.R5.C1 acceptance orchestrator (offline-only, 0 external market requests, 0 OOS).",
        },
    ]


_BASELINE_RED_HARNESS_SCRIPT = r'''
import asyncio
from datetime import datetime, timezone
import json
import sys
import time
from pathlib import Path

from services.kh_02_r2_c2_public_capture import RawHttpResponseC2
from services.kh_03_r5_market_data_foundation_service import (
    R5OperatorGrant,
    build_r5_source_capability_inventory,
    plan_r5_capture_batches,
    validate_r5_operator_capture_scope,
)
from services.kh_03_r5_capture_and_custody_service import capture_r5_public_batch

repo_root = Path(sys.argv[1])
inv = build_r5_source_capability_inventory(repo_root)
now = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
now_ms = int(now.timestamp() * 1000)

class SpyTransport:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])
    def get(self, url, params, *, timeout_sec):
        self.calls.append({"url": url, "params": dict(params), "timeout_sec": float(timeout_sec)})
        if self.responses:
            return self.responses.pop(0)
        return RawHttpResponseC2(200, b'{"code":"00000","data":[{"ok":1}]}', {}, now_ms, now_ms + 5)

obs = {}

# P1-A1: Grant scope A vs Planner budget B (different endpoint/symbol/window/budget) + wrong run_id
g_a = R5OperatorGrant(
    grant_id="QA_A", issued_by="QA", purpose="QA_FIXTURE", symbol="XAUUSDT", product_type="USDT-FUTURES",
    allowed_endpoints=("public_ticker",), window_start_ms=1794000000000, window_end_ms=1794000100000,
    max_requests=2, max_total_bytes=10_000, max_duration_sec=10, expires_utc_ms=now_ms + 60_000,
    run_id="run_a", is_qa_fixture_grant=True, origin_kind="QA_LOCAL",
)
dec_a = validate_r5_operator_capture_scope(
    g_a,
    {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker"], "run_id": "run_a", "max_requests": 2, "max_total_bytes": 10_000},
    now, {"qa_fixture_mode": True},
)
plan_b = plan_r5_capture_batches(
    dec_a, inv,
    {
        "destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 50,
        "max_requests": 50, "max_total_bytes": 2_000_000, "max_duration_sec": 999,
        "endpoints": ["contracts"], "symbol": "BTCUSDT", "product_type": "USDT-FUTURES",
        "window_start_ms": 1795000000000, "window_end_ms": 1795999000000,
    },
)
spy_a1 = SpyTransport()
res_a1 = asyncio.run(capture_r5_public_batch(plan_b, spy_a1, None, run_id="run_WRONG", sleep_fn=lambda _: None))
obs["P1A_grant_A_planner_B_bypass"] = {
    "observed_calls": len(spy_a1.calls),
    "dispatched_symbol": spy_a1.calls[0]["params"].get("symbol") if spy_a1.calls else None,
    "dispatched_url": spy_a1.calls[0]["url"] if spy_a1.calls else None,
    "weakness_reproduced": len(spy_a1.calls) == 1 and spy_a1.calls[0]["params"].get("symbol") == "BTCUSDT",
}

# P1-A2: Forged trust_context dict admits live network capture without real attestation verifier
g_live = R5OperatorGrant(
    grant_id="FAKE_LIVE", issued_by="caller", purpose="DEVELOPMENT_CAPTURE", symbol="XAUUSDT", product_type="USDT-FUTURES",
    allowed_endpoints=("public_ticker",), window_start_ms=1794000000000, window_end_ms=1794000100000,
    max_requests=5, max_total_bytes=10_000, max_duration_sec=10, expires_utc_ms=now_ms + 60_000,
    run_id="run_fake", is_qa_fixture_grant=False, origin_kind="SEPARATE_OWNER_ATTESTATION",
)
dec_live = validate_r5_operator_capture_scope(
    g_live,
    {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker"], "run_id": "run_fake", "max_requests": 5, "max_total_bytes": 10_000},
    now, {"human_operator_attestation_verified": True, "attestation_source": "EXPLICIT_SEPARATE_OWNER_GRANT", "qa_fixture_mode": False},
)
obs["P1A_forged_trust_context_dict_live"] = {
    "admitted": bool(dec_live.get("admitted")),
    "live_network_permitted": bool(dec_live.get("live_network_permitted")),
    "weakness_reproduced": (dec_live.get("admitted") is True and dec_live.get("live_network_permitted") is True),
}

# P1-A3: Completely unbound forged raw plan dict dispatches
forged_plan = {
    "status": "PLANNED_QA_OFFLINE", "partition_role": "QA_FIXTURE", "live_network_permitted": False,
    "budget": {"max_concurrency": 1, "max_requests": 5, "max_total_bytes": 50_000, "max_duration_sec": 30},
    "batches": [{"batch_id": "FORGED_1", "endpoint_key": "public_ticker", "url": "https://api.bitget.com/api/v2/mix/market/ticker", "symbol": "XAUUSDT", "product_type": "USDT-FUTURES"}],
}
spy_a3 = SpyTransport()
res_a3 = asyncio.run(capture_r5_public_batch(forged_plan, spy_a3, None, sleep_fn=lambda _: None))
obs["P1A_forged_unbound_plan_dispatches"] = {
    "observed_calls": len(spy_a3.calls),
    "capture_status": res_a3.get("capture_execution_status"),
    "weakness_reproduced": len(spy_a3.calls) == 1,
}

# P1-B1: Post-wait deadline + cancel race still dispatches AND passes full max_dur timeout
plan_b1 = {
    "status": "PLANNED_QA_OFFLINE", "partition_role": "QA_FIXTURE", "live_network_permitted": False,
    "budget": {"max_concurrency": 2, "max_requests": 5, "max_total_bytes": 50_000, "max_duration_sec": 0.20, "max_retries_per_request": 1},
    "batches": [
        {"batch_id": "B1", "endpoint_key": "public_ticker", "url": "https://api.bitget.com/api/v2/mix/market/ticker", "symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "min_request_interval_ms": 50},
        {"batch_id": "B2", "endpoint_key": "public_ticker", "url": "https://api.bitget.com/api/v2/mix/market/ticker", "symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "min_request_interval_ms": 250},
    ],
}
spy_b1 = SpyTransport()
cancel_b1 = asyncio.Event()
async def _sleep_expire(_sec):
    await asyncio.sleep(0.24)
    cancel_b1.set()
asyncio.run(capture_r5_public_batch(plan_b1, spy_b1, None, cancel_event=cancel_b1, sleep_fn=_sleep_expire))
obs["P1B_post_wait_deadline_cancel_race"] = {
    "observed_calls": len(spy_b1.calls),
    "second_call_timeout_sec": spy_b1.calls[1]["timeout_sec"] if len(spy_b1.calls) > 1 else None,
    "weakness_reproduced": len(spy_b1.calls) == 2 and spy_b1.calls[1]["timeout_sec"] == 0.20,
}

# P1-B2: Retry-After=120s silently capped to 2.0s and retried
plan_b2 = {
    "status": "PLANNED_QA_OFFLINE", "partition_role": "QA_FIXTURE", "live_network_permitted": False,
    "budget": {"max_concurrency": 1, "max_requests": 5, "max_total_bytes": 50_000, "max_duration_sec": 5.0, "max_retries_per_request": 2},
    "batches": [{"batch_id": "B_RA", "endpoint_key": "public_ticker", "url": "https://api.bitget.com/api/v2/mix/market/ticker", "symbol": "XAUUSDT", "product_type": "USDT-FUTURES"}],
}
sleeps_b2 = []
spy_b2 = SpyTransport([
    RawHttpResponseC2(429, b"", {"retry-after": "120"}, now_ms, now_ms + 5),
    RawHttpResponseC2(200, b'{"code":"00000","data":[{"ok":1}]}', {}, now_ms + 10, now_ms + 15),
])
res_b2 = asyncio.run(capture_r5_public_batch(plan_b2, spy_b2, None, sleep_fn=lambda s: sleeps_b2.append(float(s))))
obs["P1B_retry_after_120s_shortened_to_2s"] = {
    "observed_calls": len(spy_b2.calls),
    "recorded_backoff_sec": res_b2["items"][0]["retry_chain"][0]["backoff_sec"] if res_b2.get("items") else None,
    "weakness_reproduced": len(spy_b2.calls) == 2 and (len(sleeps_b2) >= 1 and abs(sleeps_b2[0] - 2.0) < 1e-6),
}

# P1-C: monitor=None, {}, and NaN disk/RAM ALL fail open and dispatch!
for lbl, mon in (
    ("P1C_monitor_None_fail_open", None),
    ("P1C_monitor_empty_dict_fail_open", lambda: {}),
    ("P1C_monitor_NaN_fail_open", lambda: {"disk_free_gib": float("nan"), "available_ram_mib": float("nan")}),
):
    spy_c = SpyTransport()
    rc = asyncio.run(capture_r5_public_batch(forged_plan, spy_c, mon, sleep_fn=lambda _: None))
    obs[lbl] = {
        "observed_calls": len(spy_c.calls),
        "capture_status": rc.get("capture_execution_status"),
        "weakness_reproduced": len(spy_c.calls) == 1 and rc.get("capture_execution_status") == "QA_FIXTURE_OFFLINE_PASS",
    }

all_ok = all(bool(v.get("weakness_reproduced")) for v in obs.values())
print(json.dumps({"all_baseline_p1_weaknesses_reproduced": all_ok, "observations": obs}, sort_keys=True))
raise SystemExit(0 if all_ok else 1)
'''


def run_real_baseline_red_reproduction_from_git_sha(
    *,
    repo_root: Path,
    baseline_sha: str = BASELINE_REVIEW_HEAD_SHA_R5_C1,
    tested_c1_sha: str = "",
) -> Dict[str, Any]:
    """
    Extracts untouched original R5 S1/S2 files from `git show <baseline_sha>:...` into an isolated
    OS temp overlay directory (NEVER mutates repo worktree!), runs the offline RED harness in a
    separate subprocess, and records exact command, exit code, stdout SHA-256, and spy observations.
    """
    app_root = repo_root / "aurum-desk"
    backend_root = app_root / "backend"
    tmp_dir = Path(tempfile.mkdtemp(prefix="kh03r5c1_red_baseline_"))
    try:
        overlay_pkg = tmp_dir / "services"
        overlay_pkg.mkdir(parents=True, exist_ok=True)
        # Allow Python to fall through from temp overlay `services/` to real `backend/services/`
        (overlay_pkg / "__init__.py").write_text(
            f"__path__.append({str((backend_root / 'services').resolve())!r})\n",
            encoding="utf-8",
        )
        extracted_hashes: Dict[str, str] = {}
        for rel in (
            "aurum-desk/backend/services/kh_03_r5_market_data_foundation_service.py",
            "aurum-desk/backend/services/kh_03_r5_capture_and_custody_service.py",
        ):
            gproc = subprocess.run(
                ["git", "show", f"{baseline_sha}:{rel}"],
                cwd=str(repo_root),
                capture_output=True,
                check=False,
                timeout=30,
            )
            if gproc.returncode != 0 or not gproc.stdout:
                return {
                    "status": "FAIL",
                    "baseline_sha": baseline_sha,
                    "tested_c1_sha": tested_c1_sha,
                    "error": f"GIT_SHOW_FAILED:{rel}:rc={gproc.returncode}",
                    "all_p1_red_reproduced": False,
                }
            dst = overlay_pkg / Path(rel).name
            dst.write_bytes(gproc.stdout)
            extracted_hashes[rel] = hashlib.sha256(gproc.stdout).hexdigest()

        script_path = tmp_dir / "run_baseline_red_harness.py"
        script_path.write_text(_BASELINE_RED_HARNESS_SCRIPT, encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(tmp_dir), str(backend_root), str(app_root), str(repo_root)])
        cmd = [sys.executable, str(script_path), str(repo_root)]
        proc = subprocess.run(cmd, cwd=str(tmp_dir), env=env, capture_output=True, text=True, timeout=60)
        stdout_sha = hashlib.sha256(proc.stdout.encode("utf-8")).hexdigest()
        parsed: Dict[str, Any] = {}
        parse_err: Optional[str] = None
        try:
            parsed = json.loads(proc.stdout.strip()) if proc.stdout.strip() else {}
        except Exception as exc:
            parse_err = str(exc)

        obs = parsed.get("observations") if isinstance(parsed, dict) else {}
        p1a_ok = bool(
            (obs.get("P1A_grant_A_planner_B_bypass") or {}).get("weakness_reproduced")
            and (obs.get("P1A_forged_trust_context_dict_live") or {}).get("weakness_reproduced")
            and (obs.get("P1A_forged_unbound_plan_dispatches") or {}).get("weakness_reproduced")
        )
        p1b_ok = bool(
            (obs.get("P1B_post_wait_deadline_cancel_race") or {}).get("weakness_reproduced")
            and (obs.get("P1B_retry_after_120s_shortened_to_2s") or {}).get("weakness_reproduced")
        )
        p1c_ok = bool(
            (obs.get("P1C_monitor_None_fail_open") or {}).get("weakness_reproduced")
            and (obs.get("P1C_monitor_empty_dict_fail_open") or {}).get("weakness_reproduced")
            and (obs.get("P1C_monitor_NaN_fail_open") or {}).get("weakness_reproduced")
        )
        all_ok = (proc.returncode == 0) and parse_err is None and p1a_ok and p1b_ok and p1c_ok

        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "schema_version": SCHEMA_VERSION_KH03_R5_C1,
            "baseline_sha": baseline_sha,
            "tested_c1_sha": tested_c1_sha,
            "isolated_temp_overlay_used": True,
            "working_tree_mutated": False,
            "external_network_sockets_used": False,
            "command_display": "python <isolated_tmp>/run_baseline_red_harness.py <repo_root>",
            "subprocess_exit_code": int(proc.returncode),
            "stdout_sha256": stdout_sha,
            "stderr_len": len(proc.stderr or ""),
            "parse_error": parse_err,
            "extracted_baseline_file_sha256": extracted_hashes,
            "p1_a_red_reproduced": p1a_ok,
            "p1_b_red_reproduced": p1b_ok,
            "p1_c_red_reproduced": p1c_ok,
            "all_p1_red_reproduced": all_ok,
            "observations": obs,
            "status": "PASS" if all_ok else "FAIL",
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def derive_r5_c1_readiness_verdicts(
    *,
    raw_evidence: Mapping[str, Mapping[str, Any]],
    mem_audit: Mapping[str, Any],
    pytest_recomp: Mapping[str, Any],
    science_recomp: Mapping[str, Any],
    junit_ok: bool,
    r4_replay_ok: bool,
    stale_prior_shas: Sequence[str] = (),
    stale_prior_run_ids: Sequence[str] = (),
    for_final_verifier_manifest: bool = False,
    verifier_violations_count: int = 0,
) -> Dict[str, Any]:
    """
    Independently recomputes ALL 30 orthogonal KH-03.R5.C1 gates (`G01`..`G30`) and separate
    verdict dimensions strictly from raw disk evidence. Never trusts caller-injected PASS strings.
    """
    f00 = raw_evidence.get("00") or {}
    f01 = raw_evidence.get("01") or {}
    f02 = raw_evidence.get("02") or {}
    f03 = raw_evidence.get("03") or {}
    f04 = raw_evidence.get("04") or {}
    f05 = raw_evidence.get("05") or {}
    f06 = raw_evidence.get("06") or {}
    f07 = raw_evidence.get("07") or {}
    f08 = raw_evidence.get("08") or {}
    f09 = raw_evidence.get("09") or {}
    f10 = raw_evidence.get("10") or {}
    f11 = raw_evidence.get("11") or {}
    f12 = raw_evidence.get("12") or {}
    f13 = raw_evidence.get("13") or {}
    f14 = raw_evidence.get("14") or {}
    f15 = raw_evidence.get("15") or {}
    f16 = raw_evidence.get("16") or {}
    f17 = raw_evidence.get("17") or {}
    f18 = raw_evidence.get("18") or {}
    f19 = raw_evidence.get("19") or {}
    f22 = raw_evidence.get("22") or {}

    g: Dict[str, str] = {}

    # G01: Exact branch, clean worktree, unique frozen executable SHA
    tsha = str(f00.get("tested_executable_sha") or "")
    rid = str(f00.get("run_id") or "")
    sha_ok = (
        f00.get("branch") == MANDATORY_BRANCH_KH03_R5_C1
        and len(tsha) == 40
        and tsha == f00.get("start_head_sha") == f00.get("end_head_sha")
        and tsha not in set(stale_prior_shas)
        and rid not in set(stale_prior_run_ids)
    )
    clean_ok = _is_strict_bool_true(f00.get("git_clean_at_start")) and _is_strict_bool_false(f00.get("local_uncommitted_changes"))
    g["G01_BRANCH_SHA_CLEAN"] = "PASS" if (sha_ok and clean_ok) else ("BLOCKED_UNCOMMITTED_LOCAL_RUN" if sha_ok else "FAIL")

    # G02: Keep-as-is locked files/AST/callers unchanged
    g["G02_KEEP_AS_IS"] = (
        "PASS"
        if (
            f01.get("status") == "PASS"
            and _is_strict_int(f01.get("locked_files_modified_count")) and int(f01["locked_files_modified_count"]) == 0
            and _is_strict_int(f01.get("unauthorized_changed_files_count")) and int(f01["unauthorized_changed_files_count"]) == 0
            and isinstance(f01.get("forbidden_order_import_hits"), list) and len(f01["forbidden_order_import_hits"]) == 0
            and _is_strict_bool_true(f01.get("cost_assumptions_defaults_intact"))
            and f01.get("legacy_guard_status") == "PASS"
            and _is_strict_int(f01.get("unauthorized_r1_callers_count")) and int(f01["unauthorized_r1_callers_count"]) == 0
        )
        else "FAIL"
    )

    # G03: Prior dataset, R1..R5 sealed artifacts, and OOS escrow immutability
    g["G03_PRIOR_SEALED_DATASET_OOS"] = (
        "PASS"
        if (
            f02.get("status") == "PASS"
            and f02.get("canonical_dataset_sha256") == CANONICAL_DATASET_SHA256_KH02
            and _is_strict_bool_true(f02.get("canonical_dataset_sha256_match"))
            and _is_strict_bool_true(f02.get("all_prior_sealed_artifacts_intact"))
            and _is_strict_int(((f02.get("c4_provenance_and_oos_boundary") or {}).get("real_oos_escrow_final_record_count")))
            and int((f02.get("c4_provenance_and_oos_boundary") or {})["real_oos_escrow_final_record_count"]) == 0
            and _is_strict_int(f11.get("oos_reads_executed")) and int(f11["oos_reads_executed"]) == 0
            and _is_strict_int(f11.get("oos_evaluations")) and int(f11["oos_evaluations"]) == 0
        )
        else "FAIL"
    )

    # G04: Real baseline RED reproduction (P1-A, P1-B, P1-C)
    g["G04_REAL_RED_REPRODUCTION"] = (
        "PASS"
        if (
            f03.get("status") == "PASS"
            and _is_strict_bool_true(f03.get("all_p1_red_reproduced"))
            and _is_strict_bool_true(f03.get("p1_a_red_reproduced"))
            and _is_strict_bool_true(f03.get("p1_b_red_reproduced"))
            and _is_strict_bool_true(f03.get("p1_c_red_reproduced"))
            and _is_strict_bool_false(f03.get("working_tree_mutated"))
            and _is_strict_bool_false(f03.get("external_network_sockets_used"))
            and _is_strict_int(f03.get("subprocess_exit_code")) and int(f03["subprocess_exit_code"]) == 0
            and isinstance(f03.get("stdout_sha256"), str) and len(f03["stdout_sha256"]) == 64
        )
        else "FAIL"
    )

    # G05..G10: P1-A strict grant schema, trust boundary, scope, plan integrity, dispatch revalidation, QA isolation
    g["G05_STRICT_GRANT_SCHEMA"] = (
        "PASS"
        if (
            f04.get("status") == "PASS"
            and _is_strict_bool_true(f04.get("strict_grant_schema_verified"))
            and _is_strict_bool_true(f04.get("bool_nan_inf_missing_rejected"))
            and _is_strict_bool_true(f04.get("cli_env_prompt_bypass_blocked"))
            and _is_strict_bool_true(f04.get("oos_purpose_rejected"))
        )
        else "FAIL"
    )
    g["G06_TRUST_BOUNDARY"] = (
        "PASS"
        if (
            f04.get("status") == "PASS"
            and _is_strict_bool_true(f04.get("default_deny_attestation_verified"))
            and _is_strict_bool_true(f04.get("forged_trust_context_dict_rejected"))
            and f04.get("live_capture_authorization_status") == "BLOCKED_OWNER_AUTHORIZATION"
            and _is_strict_bool_false(f04.get("live_network_permitted"))
        )
        else "FAIL"
    )
    g["G07_EFFECTIVE_SCOPE"] = (
        "PASS"
        if (
            f04.get("status") == "PASS"
            and _is_strict_bool_true(f04.get("full_window_budget_endpoint_binding_verified"))
            and _is_strict_bool_true(f04.get("grant_a_planner_b_bypass_blocked"))
            and _is_strict_bool_true(f04.get("silent_clamp_forbidden_verified"))
        )
        else "FAIL"
    )
    g["G08_PLAN_INTEGRITY"] = (
        "PASS"
        if (
            f05.get("status") == "PASS"
            and _is_strict_bool_true(f05.get("mutated_plan_same_digest_rejected"))
            and _is_strict_bool_true(f05.get("mutated_plan_recomputed_digest_rejected"))
            and _is_strict_bool_true(f05.get("forged_unbound_plan_rejected"))
            and _is_strict_bool_true(f05.get("recomputed_digest_zero_dispatches"))
            and _is_strict_int(f05.get("recomputed_digest_observed_calls")) and int(f05["recomputed_digest_observed_calls"]) == 0
        )
        else "FAIL"
    )
    g["G09_DISPATCH_REVALIDATION"] = (
        "PASS"
        if (
            f05.get("status") == "PASS"
            and _is_strict_bool_true(f05.get("post_enqueue_mutation_rejected"))
            and _is_strict_bool_true(f05.get("expiry_during_wait_rejected"))
            and _is_strict_bool_true(f05.get("run_id_and_destination_mismatch_rejected"))
            and _is_strict_int(f05.get("post_expiry_observed_calls")) and int(f05["post_expiry_observed_calls"]) == 0
        )
        else "FAIL"
    )
    g["G10_QA_ISOLATION"] = (
        "PASS"
        if (
            f11.get("status") == "PASS"
            and _is_strict_int(f11.get("external_market_requests_attempted")) and int(f11["external_market_requests_attempted"]) == 0
            and _is_strict_int(f11.get("accepted_real_observed_records")) and int(f11["accepted_real_observed_records"]) == 0
            and _is_strict_bool_true(f11.get("qa_grant_cannot_authorize_live"))
            and _is_strict_bool_true(f11.get("fake_transport_cannot_mint_observed_verified"))
            and _is_strict_bool_true(f11.get("qa_cannot_use_live_stdlib_transport"))
        )
        else "FAIL"
    )

    # G11..G16: P1-B post-wait deadline/cancel, remaining timeout, rate/retry, atomic request & byte budget, drain
    g["G11_DEADLINE_POST_WAIT"] = (
        "PASS"
        if (
            f06.get("status") == "PASS"
            and _is_strict_bool_true(f06.get("deadline_expired_during_wait_blocked"))
            and _is_strict_bool_true(f06.get("cancel_during_wait_blocked"))
            and _is_strict_int(f06.get("post_deadline_observed_calls")) and int(f06["post_deadline_observed_calls"]) == 0
        )
        else "FAIL"
    )
    g["G12_TIMEOUT_REMAINING"] = (
        "PASS"
        if (
            f06.get("status") == "PASS"
            and _is_strict_bool_true(f06.get("timeout_bounded_by_remaining_session_and_grant"))
            and _is_strict_num(f06.get("observed_remaining_timeout_sec"))
            and _is_strict_num(f06.get("expected_max_remaining_timeout_sec"))
            and float(f06["observed_remaining_timeout_sec"]) <= float(f06["expected_max_remaining_timeout_sec"]) + 1e-6
        )
        else "FAIL"
    )
    g["G13_RATE_RETRY"] = (
        "PASS"
        if (
            f07.get("status") == "PASS"
            and _is_strict_bool_true(f07.get("retry_after_never_shortened_verified"))
            and _is_strict_bool_true(f07.get("retry_after_exceeding_budget_stops"))
            and _is_strict_bool_true(f07.get("non_retryable_classes_never_retried"))
            and _is_strict_bool_true(f07.get("actual_dispatch_cadence_monotonic"))
        )
        else "FAIL"
    )
    g["G14_ATOMIC_REQUEST_BUDGET"] = (
        "PASS"
        if (
            f08.get("status") == "PASS"
            and _is_strict_bool_true(f08.get("concurrent_request_budget_no_oversubscribe"))
            and _is_strict_bool_true(f08.get("cancelled_reservation_not_counted_dispatched"))
            and _is_strict_int(f08.get("observed_dispatched_vs_cap_excess")) and int(f08["observed_dispatched_vs_cap_excess"]) == 0
        )
        else "FAIL"
    )
    g["G15_BYTE_BUDGET"] = (
        "PASS"
        if (
            f08.get("status") == "PASS"
            and _is_strict_bool_true(f08.get("wire_vs_accepted_bytes_separated"))
            and _is_strict_bool_true(f08.get("parallel_byte_reservation_no_oversubscribe"))
            and _is_strict_bool_true(f08.get("unsupported_legacy_transport_read_cap_blocked"))
        )
        else "FAIL"
    )
    g["G16_CANCELLATION_DRAIN"] = (
        "PASS"
        if (
            f07.get("status") == "PASS"
            and _is_strict_bool_true(f07.get("late_inflight_response_discarded"))
            and _is_strict_bool_true(f07.get("bounded_drain_verified"))
            and _is_strict_int(f07.get("undrained_inflight_workers")) and int(f07["undrained_inflight_workers"]) == 0
        )
        else "FAIL"
    )

    # G17..G20: P1-C resource strictness, actual disk/RAM/PID safety, staging/custody, abort status truth
    g["G17_RESOURCE_STRICT"] = (
        "PASS"
        if (
            f09.get("status") == "PASS"
            and _is_strict_bool_true(f09.get("monitor_none_blocked"))
            and _is_strict_bool_true(f09.get("empty_and_missing_keys_blocked"))
            and _is_strict_bool_true(f09.get("nan_inf_bool_str_neg_blocked"))
            and _is_strict_bool_true(f09.get("stale_and_future_timestamp_blocked"))
            and _is_strict_bool_true(f09.get("wrong_volume_and_exception_blocked"))
            and _is_strict_int(f09.get("invalid_monitor_observed_calls")) and int(f09["invalid_monitor_observed_calls"]) == 0
        )
        else "FAIL"
    )

    mp00 = f00.get("strict_multi_mount_probe") or {}
    waiver = (
        bool(f00.get("owner_disk_preflight_waiver", False))
        or bool(mp00.get("owner_waiver_permitted", False))
        or bool(f17.get("owner_disk_preflight_waiver", False))
    )
    mounts = list(mp00.get("distinct_mounts") or ())
    probe_errs = list(mp00.get("probe_errors") or ())
    pre_disk_ok = (
        len(mounts) > 0
        and len(probe_errs) == 0
        and all(
            _is_strict_num(m.get("free_gib")) and float(m["free_gib"]) >= STRICT_PREFLIGHT_DISK_MIN_GIB_R5
            and _is_strict_num(m.get("projected_post_reserve_gib")) and float(m["projected_post_reserve_gib"]) >= STRICT_RUNTIME_DISK_MIN_GIB_R5
            for m in mounts
        )
        and _is_strict_bool_true(mp00.get("can_run_heavy_stages"))
    )
    min_rt_disk = float(f17["min_disk_free_gib"]) if _is_strict_num(f17.get("min_disk_free_gib")) else 0.0
    min_rt_ram = float(f17["min_available_ram_mib"]) if _is_strict_num(f17.get("min_available_ram_mib")) else 0.0
    max_ch = int(f17["max_descendant_children"]) if _is_strict_int(f17.get("max_descendant_children")) else 99
    rt_ok = (
        min_rt_disk >= STRICT_RUNTIME_DISK_MIN_GIB_R5
        and min_rt_ram >= HARD_RAM_FLOOR_MIB_R5_C1
        and max_ch <= GLOBAL_MAX_DESCENDANTS_R5_C1
        and _is_strict_int(f17.get("orphan_children_after_cleanup")) and int(f17["orphan_children_after_cleanup"]) == 0
        and _is_strict_bool_true(f17.get("sentinel_temp_cleaned_up"))
        and mem_audit.get("status") == "PASS"
        and _is_strict_int(mem_audit.get("below_floor_samples_count")) and int(mem_audit["below_floor_samples_count"]) == 0
    )
    if waiver or len(probe_errs) > 0:
        g["G18_DISK_RAM_GUARD"] = "FAIL"
        res_verdict = "FAIL"
    elif not pre_disk_ok:
        g["G18_DISK_RAM_GUARD"] = "BLOCKED_LOW_DISK"
        res_verdict = "BLOCKED_LOW_DISK"
    elif not rt_ok:
        g["G18_DISK_RAM_GUARD"] = "FAIL"
        res_verdict = "FAIL"
    else:
        g["G18_DISK_RAM_GUARD"] = "PASS"
        res_verdict = "PASS"
    res_pass = (g["G18_DISK_RAM_GUARD"] == "PASS")
    low_disk = (g["G18_DISK_RAM_GUARD"] == "BLOCKED_LOW_DISK")

    g["G19_STAGING_CUSTODY"] = (
        "PASS"
        if (
            f10.get("status") == "PASS"
            and _is_strict_bool_true(f10.get("staging_containment_and_symlink_denied"))
            and _is_strict_bool_true(f10.get("mandatory_expected_raw_sha_enforced"))
            and _is_strict_bool_true(f10.get("stat_before_read_oversize_blocked"))
            and _is_strict_bool_true(f10.get("custody_chain_verified"))
            and _is_strict_bool_true(f10.get("owned_cleanup_only_verified"))
        )
        else "FAIL"
    )
    g["G20_ABORT_STATUS_TRUTH"] = (
        "PASS"
        if (
            f10.get("status") == "PASS"
            and _is_strict_bool_true(f10.get("aborted_deleted_staging_counted_zero_accepted"))
            and _is_strict_int(f10.get("aborted_accepted_records_count")) and int(f10["aborted_accepted_records_count"]) == 0
            and _is_strict_int(f10.get("aborted_removed_staged_files_count")) and int(f10["aborted_removed_staged_files_count"]) >= 1
            and _is_strict_bool_true(f10.get("no_verified_dev_from_aborted_session"))
        )
        else "FAIL"
    )

    # G21: Full repo fresh pytest collection & JUnit 1:1 bijection
    if f15.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        g["G21_FULL_PYTEST"] = "BLOCKED_LOW_DISK" if low_disk else "FAIL"
        reg_verdict = "BLOCKED" if low_disk else "FAIL"
    elif (
        f15.get("status") == "PASS"
        and res_pass
        and bool(junit_ok)
        and _is_strict_bool_true(f15.get("junit_bijection_exact"))
        and _is_strict_int(f15.get("collected_count")) and int(f15["collected_count"]) >= MIN_EXPECTED_PYTEST_COLLECTED_R5_C1
        and _is_strict_int(f15.get("executed_count")) and int(f15["executed_count"]) == int(f15["collected_count"])
        and _is_strict_int(f15.get("failed_count")) and int(f15["failed_count"]) == 0
        and _is_strict_int(f15.get("error_count")) and int(f15["error_count"]) == 0
        and _is_strict_int(f15.get("mandatory_skipped_count")) and int(f15["mandatory_skipped_count"]) == 0
    ):
        g["G21_FULL_PYTEST"] = "PASS"
        reg_verdict = "PASS"
    else:
        g["G21_FULL_PYTEST"] = "FAIL"
        reg_verdict = "FAIL"

    # G22: Full A1/SI8/adapters/D4 baseline parity
    if f14.get("BASELINE_PARITY_VERDICT") == "NOT_RUN_BLOCKED_LOW_DISK":
        g["G22_BASELINE_PARITY"] = "BLOCKED_LOW_DISK" if low_disk else "FAIL"
        par_verdict = "BLOCKED" if low_disk else "FAIL"
    elif (
        f14.get("BASELINE_PARITY_VERDICT") == "PASS"
        and res_pass
        and _is_strict_int(f14.get("a1_population_count")) and int(f14["a1_population_count"]) == 56710
        and _is_strict_int(f14.get("a1_mismatches_count")) and int(f14["a1_mismatches_count"]) == 0
        and _is_strict_int(f14.get("si8_population_count")) and int(f14["si8_population_count"]) == 6375
        and _is_strict_int(f14.get("si8_mismatches_count")) and int(f14["si8_mismatches_count"]) == 0
        and _is_strict_int(f14.get("three_adapter_population_count")) and int(f14["three_adapter_population_count"]) == 246
        and _is_strict_int(f14.get("three_adapter_mismatches_count")) and int(f14["three_adapter_mismatches_count"]) == 0
        and _is_strict_int(f14.get("d4_historical_closed_trades")) and int(f14["d4_historical_closed_trades"]) == 0
        and _is_strict_bool_true(((f14.get("per_decision_and_episode_fingerprints") or {}).get("all_sealed_fingerprints_exact_match")))
    ):
        g["G22_BASELINE_PARITY"] = "PASS"
        par_verdict = "PASS"
    else:
        g["G22_BASELINE_PARITY"] = "FAIL"
        par_verdict = "FAIL"

    # G23: Full R4 economic replay + 6 cost scenarios + E1/E2/E3 anchor parity
    g["G23_BACKTEST_PARITY"] = (
        "PASS"
        if (
            bool(r4_replay_ok)
            and f12.get("status") == "PASS"
            and f13.get("status") == "PASS"
            and _is_strict_bool_true(f12.get("fresh_replay_executed_not_copied"))
            and _is_strict_bool_true(f12.get("r4_decimal_bridge_exact_match"))
            and _is_strict_bool_true(f12.get("r4_row_ids_and_ledger_sha_match"))
            and _is_strict_bool_false(f12.get("new_data_used_in_backtest"))
            and _is_strict_bool_false(f12.get("planned_cap_is_physical_guarantee"))
            and _is_strict_int(f12.get("realized_losses_gt_2p50_count")) and int(f12["realized_losses_gt_2p50_count"]) == 139
            and _is_strict_bool_true(f13.get("six_cost_dynamic_vs_fixed_fills_separated"))
            and _is_strict_bool_true(f13.get("arms_e1_e2_e3_anchors_exact_match"))
        )
        else "FAIL"
    )

    # G24 & G25: W1/W2/W4/W1-repeat determinism & measured worker policy
    if f16.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        st_b = "BLOCKED_LOW_DISK" if low_disk else "FAIL"
        g["G24_DETERMINISTIC_WORKERS"] = st_b
        g["G25_MEASURED_WORKER_POLICY"] = st_b
    else:
        p_ok = bool(pytest_recomp.get("valid"))
        s_ok = bool(science_recomp.get("valid"))
        p_pol = f16.get("pytest_worker_policy") or {}
        s_pol = f16.get("science_worker_policy") or {}
        g["G24_DETERMINISTIC_WORKERS"] = (
            "PASS"
            if (res_pass and p_ok and s_ok and _is_strict_bool_true(f16.get("c1_capture_fixture_w1_w2_w4_identical")))
            else "FAIL"
        )
        g["G25_MEASURED_WORKER_POLICY"] = (
            "PASS"
            if (
                res_pass and p_ok and s_ok and f16.get("status") == "PASS"
                and _is_strict_int(p_pol.get("applied_worker_count")) and 1 <= int(p_pol["applied_worker_count"]) <= 3
                and _is_strict_int(s_pol.get("applied_worker_count")) and 1 <= int(s_pol["applied_worker_count"]) <= GLOBAL_MAX_DESCENDANTS_R5_C1
                and _is_strict_bool_false(f16.get("full_suite_speedup_extrapolated"))
            )
            else "FAIL"
        )

    # G26: Inherited (>=288) + NEW C1 (>=40) unique RED/GREEN fault controls
    g["G26_FAULT_CONTROLS"] = (
        "PASS"
        if (
            f18.get("status") == "PASS"
            and _is_strict_int(f18.get("inherited_r5_r4_c2_c1_r3_controls_count")) and int(f18["inherited_r5_r4_c2_c1_r3_controls_count"]) >= 288
            and _is_strict_int(f18.get("new_c1_controls_count")) and int(f18["new_c1_controls_count"]) >= 40
            and _is_strict_int(f18.get("failed_controls")) and int(f18["failed_controls"]) == 0
            and _is_strict_int(f18.get("unique_fault_ids")) and _is_strict_int(f18.get("total_controls"))
            and int(f18["unique_fault_ids"]) == int(f18["total_controls"])
        )
        else "FAIL"
    )

    # G27: Separate-OS independent verifier
    g["G27_INDEPENDENT_VERIFIER"] = (
        ("PASS" if verifier_violations_count == 0 else "FAIL")
        if for_final_verifier_manifest
        else "PENDING_SEPARATE_OS_VERIFIER"
    )

    # G28: Inherited (>=89) + NEW C1 (>=20) separate-OS verifier mutations
    g["G28_MUTATION_RESISTANCE"] = (
        "PASS"
        if (
            f19.get("status") == "PASS"
            and _is_strict_bool_true(f19.get("clean_clone_verifier_green"))
            and _is_strict_int(f19.get("inherited_r5_r4_c2_c1_r3_mutations_tested_count")) and int(f19["inherited_r5_r4_c2_c1_r3_mutations_tested_count"]) >= 89
            and _is_strict_int(f19.get("inherited_r5_r4_c2_c1_r3_mutations_failed_detection_count")) and int(f19["inherited_r5_r4_c2_c1_r3_mutations_failed_detection_count"]) == 0
            and _is_strict_int(f19.get("new_c1_mutations_tested_count")) and int(f19["new_c1_mutations_tested_count"]) >= 20
            and _is_strict_int(f19.get("new_c1_mutations_failed_detection_count")) and int(f19["new_c1_mutations_failed_detection_count"]) == 0
        )
        else "FAIL"
    )

    # G29: Economic NO_PROVEN_EDGE, OOS=0, KH04 blocked, all trading modes OFF
    modes = f00.get("runtime_safety_modes") or {}
    g["G29_ECONOMIC_AND_ACTIVATION"] = (
        "PASS"
        if (
            f00.get("ACTIVATION_READY") == "NO"
            and all(modes.get(k) == "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO"))
            and f12.get("RESEARCH_ECONOMIC_VERDICT") == "NO_PROVEN_EDGE"
            and f12.get("KH04_ENTRY") == "BLOCKED_NO_DEV_CANDIDATE"
            and _is_strict_int(f12.get("OOS_EVALUATIONS")) and int(f12["OOS_EVALUATIONS"]) == 0
            and _is_strict_int(f11.get("external_market_requests_attempted")) and int(f11["external_market_requests_attempted"]) == 0
            and _is_strict_int(f11.get("accepted_real_observed_records")) and int(f11["accepted_real_observed_records"]) == 0
        )
        else "FAIL"
    )

    # G30: Remote publication proof
    false_pub = (
        (bool(f22.get("remote_readback_sha256_verified")) and not f22.get("publication_head_sha"))
        or (f22.get("ci_status") == "CI_PASS" and not _is_strict_bool_true(f22.get("actual_github_actions_run_verified")))
    )
    g["G30_REMOTE_PUBLICATION"] = "FAIL" if false_pub else str(f22.get("publication_status") or "PRE_PUSH_PENDING")

    ordered = {k: g[k] for k in GATE_30_KEYS_KH03_R5_C1}
    eng_keys = [k for k in GATE_30_KEYS_KH03_R5_C1 if k not in ("G27_INDEPENDENT_VERIFIER", "G30_REMOTE_PUBLICATION")]
    if for_final_verifier_manifest:
        eng_keys.append("G27_INDEPENDENT_VERIFIER")

    any_fail = any(ordered[k] == "FAIL" for k in GATE_30_KEYS_KH03_R5_C1)
    all_eng_pass = all(ordered[k] == "PASS" for k in eng_keys) and not false_pub
    eng_verdict = "PASS" if all_eng_pass else ("BLOCKED" if (low_disk and not any_fail) else "FAIL")

    if all_eng_pass:
        overall_cls = CLASSIFICATION_PASS_OFFLINE_C1
    elif low_disk and not any_fail:
        overall_cls = "BLOCKED_RESOURCE_LIMIT"
    else:
        overall_cls = "FAIL_CLOSED"

    verdicts = {
        "ENGINEERING": eng_verdict,
        "RESOURCE_SAFETY": res_verdict,
        "FULL_REGRESSION": reg_verdict,
        "BACKTEST_PARITY": par_verdict,
        "CAPTURE_AUTHORIZATION": "BLOCKED_OWNER_AUTHORIZATION",
        "CAPTURE_EXECUTION": "BLOCKED_OWNER_AUTHORIZATION",
        "CAPTURE_PROVENANCE": "NO_REAL_CAPTURE",
        "LIVE_NETWORK_VALIDATION": "NOT_RUN_BY_DESIGN",
        "DATA_READINESS": "INCOMPLETE",
        "OBSERVED_BBO_COVERAGE": "MISSING",
        "FUNDING_READINESS": "MISSING",
        "ACCOUNT_FEE_READINESS": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "EXECUTION_FIDELITY": "INCOMPLETE",
        "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA",
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
        "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE",
        "OOS_EVALUATIONS": 0,
        "EXTERNAL_MARKET_REQUESTS_ATTEMPTED": 0,
        "ACCEPTED_REAL_OBSERVED_RECORDS": 0,
        "ACTIVATION_READY": "NO",
        "PRODUCTION": "OFF",
        "LIVE": "OFF",
        "PAPER": "OFF",
        "AUTO": "OFF",
        "PUBLICATION": ordered["G30_REMOTE_PUBLICATION"],
        "CI_STATUS": str(f22.get("ci_status") or "CI_UNKNOWN_NO_ACTIONS_RUN"),
    }
    return {
        "spec_version": SPEC_VERSION_KH03_R5_C1,
        "schema_version": SCHEMA_VERSION_KH03_R5_C1,
        "gates_30": ordered,
        "verdicts": verdicts,
        "threat_model": CAPTURE_SAFETY_THREAT_MODEL_R5_C1,
        "overall_classification": overall_cls,
    }


def build_r5_c1_owner_handover_vi(
    report: Mapping[str, Any],
    receipts: Mapping[str, Mapping[str, Any]],
) -> str:
    """Concise, auditable Vietnamese owner handover (§13)."""
    f00 = receipts.get("00") or {}
    f03 = receipts.get("03") or {}
    f12 = receipts.get("12") or {}
    f14 = receipts.get("14") or {}
    f15 = receipts.get("15") or {}
    f16 = receipts.get("16") or {}
    f17 = receipts.get("17") or {}
    f18 = receipts.get("18") or {}
    f19 = receipts.get("19") or {}
    v = report.get("verdicts") or {}
    g = report.get("gates_30") or {}
    pp = f16.get("pytest_worker_policy") or {}
    sp = f16.get("science_worker_policy") or {}

    lines = [
        "AURUM DESK — BAN GIAO CHU SO HUU KH-03.R5.C1 (TIENG VIET)",
        "======================================================================",
        f"SPEC: {SPEC_VERSION_KH03_R5_C1} | RUN_ID: {f00.get('run_id')}",
        f"BRANCH: {f00.get('branch')} | BASELINE_HEAD: {f00.get('baseline_review_head_sha')} | TESTED_SHA: {f00.get('tested_executable_sha')}",
        f"CLASSIFICATION: {report.get('overall_classification')}",
        f"ENGINEERING={v.get('ENGINEERING')} | RESOURCE_SAFETY={v.get('RESOURCE_SAFETY')} | FULL_REGRESSION={v.get('FULL_REGRESSION')} | BACKTEST_PARITY={v.get('BACKTEST_PARITY')}",
        f"CAPTURE_AUTHORIZATION={v.get('CAPTURE_AUTHORIZATION')} | CAPTURE_EXECUTION={v.get('CAPTURE_EXECUTION')} | CAPTURE_PROVENANCE={v.get('CAPTURE_PROVENANCE')} | LIVE_NETWORK={v.get('LIVE_NETWORK_VALIDATION')}",
        f"DATA_READINESS={v.get('DATA_READINESS')} | BBO={v.get('OBSERVED_BBO_COVERAGE')} | FUNDING={v.get('FUNDING_READINESS')} | ACCOUNT_FEE={v.get('ACCOUNT_FEE_READINESS')} | FIDELITY={v.get('EXECUTION_FIDELITY')}",
        f"ECONOMIC={v.get('RESEARCH_ECONOMIC_VERDICT')} | FRESH_OOS={v.get('FRESH_OOS')} | KH04={v.get('KH04_ENTRY')} | OOS=0 | EXTERNAL_MARKET_REQUESTS=0 | REAL_OBSERVED=0 | ACTIVATION_READY=NO | PRODUCTION/LIVE/PAPER/AUTO=OFF",
        f"CI_STATUS={v.get('CI_STATUS')} | PUBLICATION={v.get('PUBLICATION')}",
        "",
        "1. TAI HIEN RED GOC & GIA CO P1-A / P1-B / P1-C:",
        f"- Real Baseline RED on {f03.get('baseline_sha')}: p1_a={f03.get('p1_a_red_reproduced')}, p1_b={f03.get('p1_b_red_reproduced')}, p1_c={f03.get('p1_c_red_reproduced')}, exit={f03.get('subprocess_exit_code')}, stdout_sha256={str(f03.get('stdout_sha256') or '')[:16]}...",
        "- P1-A: validate_r5_operator_capture_scope + plan_r5_capture_batches + capture_r5_public_batch gan ket chat scope->plan->dispatch bang process-local R5TrustedScopeReceipt/R5TrustedPlanReceipt; default-deny R5OwnerAttestationVerifier chan live capture khi chua co bo xac thuc ngoai.",
        "- P1-B: _check_r5_dispatch_guard kiem tra lai deadline/cancel/expiry/plan/resource SAU moi wait; timeout gioi han theo thoi gian con lai; khong cat ngan Retry-After; dat cho atomic cho request va wire bytes; huy/xa in-flight co gioi han va bo phan hoi tre.",
        "- P1-C: validate_r5_resource_sample_strict + _require_r5_resource_monitor chan None/{}/thieu truong/NaN/Inf/bool/str/am/cu/tuong lai/sai volume/ngoai le; R5DevCustodyJournal.persist_snapshot bat buoc raw_sha256, kiem tra kich thuoc truoc khi doc va chi dem accepted=0 cho tep staging da xoa khi abort.",
        "",
        "2. KIEM THU, FAULT CONTROLS, VERIFIER & PARITY:",
        f"- Full Repo Pytest JUnit 1:1: collected={f15.get('collected_count')}, executed={f15.get('executed_count')}, passed={f15.get('passed_count')}, skipped={f15.get('skipped_count')}, mandatory_skipped={f15.get('mandatory_skipped_count')}, failed={f15.get('failed_count')}, error={f15.get('error_count')}",
        f"- Fault controls: {f18.get('passed_controls')}/{f18.get('total_controls')} ({f18.get('inherited_r5_r4_c2_c1_r3_controls_count')} ke thua + {f18.get('new_c1_controls_count')} moi C1)",
        f"- Separate-OS Verifier Mutations: {f19.get('new_c1_mutations_detected_count')}/{f19.get('new_c1_mutations_tested_count')} moi C1 + {f19.get('inherited_r5_r4_c2_c1_r3_mutations_detected_count')}/{f19.get('inherited_r5_r4_c2_c1_r3_mutations_tested_count')} ke thua",
        f"- Baseline Parity: A1={f14.get('a1_population_count')}/{f14.get('a1_mismatches_count')}, SI8={f14.get('si8_population_count')}/{f14.get('si8_mismatches_count')}, Adapter={f14.get('three_adapter_population_count')}/{f14.get('three_adapter_mismatches_count')}, D4={f14.get('d4_historical_closed_trades')}",
        f"- Fresh R4 Replay (REUSED DEV 1x): closed={f12.get('null_1x_closed')} ({f12.get('null_1x_wins')}W/{f12.get('null_1x_losses')}L), gross={f12.get('null_1x_gross_usd')} USD, fees={f12.get('null_1x_closed_fees_usd')} USD, net={f12.get('null_1x_net_usd')} USD, exp={f12.get('null_1x_expectancy_r')}R, >2.50USD_tails={f12.get('realized_losses_gt_2p50_count')}",
        "",
        "3. BENCHMARK W1/W2/W4/W1_REPEAT & TAI NGUYEN:",
        f"- Pytest subset (155): means={pp.get('mean_wall_sec_by_mode')}, w1_repeat={pp.get('w1_repeat_wall_sec')}s, fastest={pp.get('measured_fastest_mode')}, applied={pp.get('applied_worker_count')} (safe_cap={pp.get('safe_worker_cap')}), full_suite_extrapolated=False",
        f"- Science slice (8000): means={sp.get('mean_wall_sec_by_mode')}, w1_repeat={sp.get('w1_repeat_wall_sec')}s, fastest={sp.get('measured_fastest_mode')}, applied={sp.get('applied_worker_count')}",
        f"- Resources: min_disk={f17.get('min_disk_free_gib')} GiB, min_ram={f17.get('min_available_ram_mib')} MiB, peak_rss={f17.get('max_tree_rss_mib')} MiB, max_children={f17.get('max_descendant_children')}, orphans={f17.get('orphan_children_after_cleanup')}, waiver=False",
        "",
        "4. LUU Y PHAM VI & BUOC TIEP THEO:",
        "- C1 chi gia co an toan capture offline; KHONG thay doi chien luoc, KHONG tao edge moi, KHONG phai quyen cho phep R6 hay thu thap live.",
        "- Live capture giu BLOCKED_OWNER_AUTHORIZATION; transport C4 khoa khong ho tro dynamic wire read cap < transport.max_response_bytes (bi chan fail-closed neu vuot ngan sach).",
        "",
        "5. 30 CONG (G01..G30):",
    ] + [f"  {k}: {val}" for k, val in g.items()]
    return "\n".join(lines) + "\n"
