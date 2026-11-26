#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_03_r6_capture.py

AURUM DESK — KH-03.R6.A1 (S1 / F01 / F29) | ACQUISITION CLI & TRUST BOUNDARY ENTRYPOINT
SPEC VERSION: KH-03.R6.A1-v1.0 | READ-ONLY PUBLIC CLASSIC V2 CONTRACT | ZERO TRADING | DEFAULT DRY-RUN

Hard Security & Closure Invariants (Closes P1-G & P1-K):
  - Permanently removes the old handshake-only claimed-and-ready early exit!
  - FORBIDS `--allow-live`, `--force`, `--skip-signature`, `--owner-waive-disk-preflight`, `--auto-approve`,
    conflicting mode flags, or env-var approval bypasses (immediate exit code 2!).
  - Default `--dry-run` mode: opens ZERO sockets, claims ZERO grants, writes ZERO custody files;
    strictly validates bounded regular local plan schema, endpoints/channels against Classic v2 registry,
    window order, strict-int finite caps within stage limits, destination path safety, OS resource preflight,
    and optional release binding (`F27`/`F28`). Exits NONZERO on ANY invalid plan, wrong branch, or failed preflight!
  - Owner external mode (`--execute-authorized --stage smoke|dev ...`): validates prerequisites & frozen
    release binding (`F27`/`F28`), then UNCONDITIONALLY hard-blocks in KH-03.R6.A1 with
    `BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED` (exit code 2) BEFORE claiming any real owner grant or opening
    any network socket!
  - Isolated QA End-to-End mode (`--qa-loopback-e2e --stage smoke|dev --qa-config ... --grant ... --plan ...`):
    runs the REAL end-to-end acquisition orchestrator (`run_r6_a1_acquisition_session`) in this subprocess
    strictly against `127.0.0.1` loopback fixture servers using isolated `QA_ISOLATED_TEST_TRUST` +
    durable SQLite ledger + owned child workers + market-data validators + `R6CustodyJournal` + sealed QA
    manifest. Rejects conflicting owner flags or non-loopback URLs BEFORE claim!
"""
from __future__ import annotations

import json as _early_json
import os as _early_os
import sys as _early_sys

_FORBIDDEN_CLI_FLAGS = (
    "--allow-live",
    "--force",
    "--skip-signature",
    "--owner-waive-disk-preflight",
    "--auto-approve",
)
_FORBIDDEN_ENV_KEYS = (
    "KH03_R6_ALLOW_LIVE",
    "KH03_R6_FORCE",
    "KH03_R6_OWNER_APPROVAL",
    "KH03_R6_OWNER_WAIVE_DISK_PREFLIGHT",
    "KH03_R6_A1_ALLOW_EXTERNAL",
    "KH03_OWNER_WAIVE_DISK_PREFLIGHT",
    "KH02_OWNER_WAIVE_DISK_PREFLIGHT",
)

if __name__ == "__main__" and (
    any(f in _early_sys.argv for f in _FORBIDDEN_CLI_FLAGS)
    or any(str(_early_os.environ.get(k) or "").strip().lower() in ("1", "true", "yes") for k in _FORBIDDEN_ENV_KEYS)
):
    print(
        _early_json.dumps(
            {
                "status": "REJECTED_FORBIDDEN_BYPASS_FLAG_OR_ENV",
                "error": "CLI/env approval bypass or disk waiver is strictly forbidden in KH-03.R6.A1.",
                "external_market_requests_attempted": 0,
                "accepted_real_observed_records": 0,
                "exit_code": 2,
            },
            indent=2,
            sort_keys=True,
        ),
        file=_early_sys.stderr,
    )
    raise SystemExit(2)

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_03_r6_a1_acquisition_service import (
    build_r6_a1_acquisition_session,
    run_r6_a1_acquisition_session,
)
from services.kh_03_r6_a1_release_service import (
    build_r6_a1_unsigned_owner_review_template,
    prepare_r6_a1_frozen_execution_context,
    verify_r6_a1_release_binding,
)
from services.kh_03_r6_a1_trust_service import (
    A1_EXTERNAL_CAPTURE_ENABLED,
    ALLOWED_LOOPBACK_HOSTS_A1,
    BUDGET_FIELDS_A1,
    SPEC_VERSION_KH03_R6_A1,
    freeze_strict_int_caps_a1,
)
from services.kh_03_r6_bounded_transport_service import (
    build_r6_bound_capture_plan,
    collect_real_r6_resource_sample,
    validate_r6_resource_guard,
)
from services.kh_03_r6_durable_grant_service import (
    OWNER_EXTERNAL_TRUST_NAMESPACE_R6,
    QA_ISOLATED_TRUST_NAMESPACE_R6,
    _check_local_path_safety,
    _is_strict_int,
    _scan_for_secrets_or_bypass,
    _strict_loads_r6,
    load_r6_owner_trust_root,
    open_r6_durable_ledger,
    verify_r6_owner_grant,
)
from services.kh_03_r6_source_contract_service import (
    MANDATORY_BRANCH_KH03_R6,
    R6SourceContractRegistry,
    build_r6_source_contract_registry,
)

_ALLOWED_DRYRUN_PLAN_KEYS = frozenset({
    "stage",
    "endpoints",
    "ws_channels",
    "window_start_ms",
    "window_end_ms",
    "max_session_duration_sec",
    "max_concurrency",
    "destination_root",
    "caps",
    "task_policy_digest",
})


def _git_output(args: List[str]) -> str:
    proc = subprocess.run(["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, timeout=15)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _validate_dry_run_plan_file(
    plan_path: Path,
    *,
    stage_code: str,
    registry: R6SourceContractRegistry,
    allow_qa_stage: bool = False,
) -> Tuple[bool, Optional[Dict[str, Any]], List[str]]:
    """
    F01: Strict dry-run plan validation (NEVER accepts on JSON parse alone!).
    Validates regular local file, bounded size, no secrets/bypass, exact schema keys, endpoints &
    WS channels against Classic v2 registry, window order, strict-int finite caps within stage limits,
    and canonical local destination path safety (outside Git, no symlink/junction/UNC).
    """
    viols: List[str] = []
    if not plan_path.is_file():
        return False, None, ["DRY_RUN_PLAN_FILE_NOT_FOUND"]
    if plan_path.is_symlink():
        return False, None, ["DRY_RUN_PLAN_SYMLINK_FORBIDDEN"]
    try:
        raw = plan_path.read_bytes()
        doc = _strict_loads_r6(raw, max_bytes=65_536)
    except Exception as exc:
        return False, None, [f"DRY_RUN_PLAN_PARSE_ERROR:{exc}"]

    if not isinstance(doc, Mapping):
        return False, None, ["DRY_RUN_PLAN_NOT_OBJECT"]

    sec_hits = _scan_for_secrets_or_bypass(doc)
    if sec_hits:
        viols.extend(sec_hits)

    extra_k = sorted(set(doc.keys()) - _ALLOWED_DRYRUN_PLAN_KEYS)
    if extra_k:
        viols.append(f"PLAN_UNKNOWN_KEYS:{','.join(extra_k)}")

    p_stage = str(doc.get("stage") or "").strip()
    allowed_stages = {stage_code} | ({"QA_LOOPBACK"} if allow_qa_stage else set())
    if p_stage not in allowed_stages:
        viols.append(f"PLAN_STAGE_MISMATCH:expected={sorted(allowed_stages)}:got={p_stage}")

    eps = doc.get("endpoints")
    if not isinstance(eps, list) or not eps or not all(isinstance(x, str) and x.strip() for x in eps):
        viols.append("PLAN_ENDPOINTS_REQUIRED_NONEMPTY_LIST")
    else:
        if len(set(eps)) != len(eps):
            viols.append("PLAN_DUPLICATE_ENDPOINTS")
        if "classic_v2_contracts" not in eps:
            viols.append("PLAN_MISSING_MANDATORY_METADATA_ENDPOINT:classic_v2_contracts")
        for ep_id in eps:
            if ep_id not in registry.endpoints:
                viols.append(f"PLAN_UNKNOWN_ENDPOINT:{ep_id}")

    ws_chs = doc.get("ws_channels", [])
    if not isinstance(ws_chs, list) or not all(isinstance(x, str) and x.strip() for x in ws_chs):
        viols.append("PLAN_WS_CHANNELS_INVALID")
    else:
        if len(set(ws_chs)) != len(ws_chs):
            viols.append("PLAN_DUPLICATE_WS_CHANNELS")
        for ch in ws_chs:
            if ch not in ("books1", "ticker"):
                viols.append(f"PLAN_UNALLOWLISTED_WS_CHANNEL:{ch}")
        if isinstance(eps, list) and "classic_v2_ws_books1" in eps and "books1" not in ws_chs:
            viols.append("PLAN_WS_ENDPOINT_MISSING_MATCHING_CHANNEL")

    ws_ms = doc.get("window_start_ms")
    we_ms = doc.get("window_end_ms")
    if not _is_strict_int(ws_ms) or not _is_strict_int(we_ms) or int(ws_ms) <= 0 or int(ws_ms) >= int(we_ms):
        viols.append("PLAN_INVALID_WINDOW_START_END_MS")

    dur = doc.get("max_session_duration_sec")
    conc = doc.get("max_concurrency")
    max_dur_lim = 300 if stage_code == "B_SMOKE" else 3600
    max_conc_lim = 2 if not allow_qa_stage else 4
    if not _is_strict_int(dur) or int(dur) <= 0 or int(dur) > max_dur_lim:
        viols.append(f"PLAN_INVALID_DURATION:{dur}")
    if not _is_strict_int(conc) or int(conc) <= 0 or int(conc) > max_conc_lim:
        viols.append(f"PLAN_INVALID_CONCURRENCY:{conc}")

    dest_s = doc.get("destination_root")
    if not isinstance(dest_s, str) or not dest_s.strip():
        viols.append("PLAN_MISSING_DESTINATION_ROOT")
    else:
        viols.extend(_check_local_path_safety(Path(dest_s), repo_root=REPO_ROOT, must_exist=False))

    caps_raw = doc.get("caps")
    frozen_caps, _, cap_viols = freeze_strict_int_caps_a1(caps_raw, require_complete=True)
    viols.extend(cap_viols)
    if frozen_caps is not None:
        if stage_code == "B_SMOKE":
            ceilings = {"requests": 30, "connections": 2, "subscriptions": 2, "payload_bytes": 10 * 1024 * 1024, "messages": 10_000}
        else:
            ceilings = {"requests": 500, "connections": 4, "subscriptions": 4, "payload_bytes": 50 * 1024 * 1024, "messages": 200_000}
        for k, cv in ceilings.items():
            if frozen_caps[k] > cv:
                viols.append(f"PLAN_CAP_EXCEEDS_STAGE_POLICY:{k}:{frozen_caps[k]}>{cv}")
        if frozen_caps["requests"] < 1 or frozen_caps["payload_bytes"] < 1 or frozen_caps["decoded_bytes"] < 1:
            viols.append("PLAN_ZERO_REQUIRED_REST_CAP")

    return len(viols) == 0, dict(doc) if not viols else None, viols


def run_capture_cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="KH-03.R6.A1 Read-Only Public Market Data Acquisition CLI (Default --dry-run)")
    parser.add_argument("--stage", choices=("smoke", "dev"), required=True)
    parser.add_argument("--dry-run", action="store_true", default=False, help="Strictly validate plan/registry/resources/release (0 sockets, 0 claims, 0 writes).")
    parser.add_argument("--execute-authorized", action="store_true", default=False, help="Future R6.B owner external execution opt-in (HARD-BLOCKED in A1).")
    parser.add_argument("--qa-loopback-e2e", action="store_true", default=False, help="Isolated local 127.0.0.1 QA loopback end-to-end acquisition mode.")
    parser.add_argument("--plan", default=None, help="Local JSON plan file to validate/bind.")
    parser.add_argument("--grant", default=None, help="Local Ed25519-signed one-shot grant JSON outside Git.")
    parser.add_argument("--owner-config", default=None, help="Local owner configuration JSON outside Git (forbidden with --qa-loopback-e2e).")
    parser.add_argument("--qa-config", default=None, help="Local ephemeral QA loopback config JSON outside Git (strictly --qa-loopback-e2e only).")
    parser.add_argument("--release-manifest", default=None, help="Sealed independent verifier manifest `26_independent_OS_verifier_manifest_sha256.json`.")
    parser.add_argument("--release-receipt", default=None, help="Optional post-push remote readback receipt `28_phase2_remote_readback_<run_id>.json`.")
    parser.add_argument("--latest-pointer", default=None, help="Optional `latest_run.json` release pointer.")
    parser.add_argument("--expected-manifest-sha256", default=None, help="Owner-pinned expected SHA-256 of `--release-manifest`.")
    parser.add_argument("--write-unsigned-template", default=None, help="Optional local path to save unsigned review template JSON.")
    ns = parser.parse_args(argv)

    # Reject conflicting CLI mode flags IMMEDIATELY before any I/O or claim!
    conflicts: List[str] = []
    if ns.dry_run and ns.execute_authorized:
        conflicts.append("CONFLICT_DRY_RUN_AND_EXECUTE_AUTHORIZED")
    if ns.dry_run and ns.qa_loopback_e2e:
        conflicts.append("CONFLICT_DRY_RUN_AND_QA_LOOPBACK")
    if ns.execute_authorized and ns.qa_loopback_e2e:
        conflicts.append("CONFLICT_EXECUTE_AUTHORIZED_AND_QA_LOOPBACK")
    if ns.qa_loopback_e2e and ns.owner_config:
        conflicts.append("CONFLICT_QA_LOOPBACK_WITH_OWNER_CONFIG")
    if ns.execute_authorized and ns.qa_config:
        conflicts.append("CONFLICT_EXECUTE_AUTHORIZED_WITH_QA_CONFIG")
    if conflicts:
        print(json.dumps({
            "spec_version": SPEC_VERSION_KH03_R6_A1,
            "status": "REJECTED_CONFLICTING_CLI_FLAGS",
            "external_market_requests_attempted": 0,
            "accepted_real_observed_records": 0,
            "violations": conflicts,
            "exit_code": 2,
        }, indent=2, sort_keys=True))
        return 2

    stage_code = "B_SMOKE" if ns.stage == "smoke" else "C_DEV"
    registry = build_r6_source_contract_registry()
    head_sha = _git_output(["rev-parse", "HEAD"])
    branch = _git_output(["branch", "--show-current"])
    dirty = bool(_git_output(["status", "--porcelain"]))

    vol_check_targets = {"repo": REPO_ROOT, "temp": Path(os.environ.get("TEMP") or REPO_ROOT)}
    sample = collect_real_r6_resource_sample(vol_check_targets, active_workers=0)
    res_guard = validate_r6_resource_guard(sample, expected_volumes=vol_check_targets, phase="preflight", allow_fixture_sample=False)

    # Optional release binding verification (F27) if --release-manifest supplied
    rel_check: Optional[Dict[str, Any]] = None
    if ns.release_manifest:
        rel_check = verify_r6_a1_release_binding(
            repo_root=REPO_ROOT,
            release_manifest_path=Path(ns.release_manifest),
            expected_manifest_sha256=ns.expected_manifest_sha256,
            latest_pointer_path=Path(ns.latest_pointer) if ns.latest_pointer else None,
            remote_receipt_path=Path(ns.release_receipt) if ns.release_receipt else None,
            expected_branch=MANDATORY_BRANCH_KH03_R6,
            require_pinned_manifest_sha=bool(ns.expected_manifest_sha256 is not None),
            require_remote_receipt=bool(ns.release_receipt is not None),
            verify_sealed_inventory_files=True,
            allow_pre_publication_head_equals_tested=bool(ns.qa_loopback_e2e or ns.dry_run),
        )

    # =====================================================================
    # MODE 1: STRICT DRY-RUN (Default when neither --execute-authorized nor --qa-loopback-e2e nor --grant)
    # =====================================================================
    is_dry_run = bool(ns.dry_run or (not ns.execute_authorized and not ns.qa_loopback_e2e and ns.grant is None))
    if is_dry_run:
        dry_viols: List[str] = []
        if branch != MANDATORY_BRANCH_KH03_R6:
            dry_viols.append(f"WRONG_BRANCH:{branch}")
        if registry.status != "DOC_VERIFIED_READY_FOR_STAGE_B_REVIEW":
            dry_viols.append(f"SOURCE_REGISTRY_NOT_READY:{registry.status}")
        if not res_guard["ok"]:
            dry_viols.extend(res_guard["violations"])

        plan_check: Optional[Dict[str, Any]] = None
        if ns.plan:
            p_ok, p_doc, p_viols = _validate_dry_run_plan_file(Path(ns.plan), stage_code=stage_code, registry=registry)
            plan_check = {
                "plan_path_checked": str(Path(ns.plan)),
                "valid": p_ok,
                "violations": p_viols,
                "endpoints_count": len(p_doc["endpoints"]) if p_doc else 0,
            }
            if not p_ok:
                dry_viols.extend(p_viols)

        rel_summary: Optional[Dict[str, Any]] = None
        if rel_check is not None:
            rel_summary = {
                "valid": rel_check["valid"],
                "status": rel_check["status"],
                "binding": rel_check["binding"].to_redacted_dict() if rel_check["binding"] else None,
                "violations": rel_check["violations"],
            }
            if not rel_check["valid"]:
                dry_viols.extend(rel_check["violations"])

        tested_sha_disp = (
            rel_check["binding"].tested_executable_sha
            if (rel_check and rel_check.get("binding"))
            else (head_sha or "<UNKNOWN_HEAD>")
        )
        man_sha_disp = (
            rel_check["binding"].manifest_sha256
            if (rel_check and rel_check.get("binding"))
            else "<OWNER_PINS_SEALED_26_MANIFEST_SHA256>"
        )
        run_id_disp = (
            rel_check["binding"].run_id
            if (rel_check and rel_check.get("binding"))
            else "<STAGE_A1_CERTIFIED_RUN_ID>"
        )
        template = build_r6_a1_unsigned_owner_review_template(
            tested_executable_sha=tested_sha_disp,
            publication_head_sha=head_sha or None,
            stage_a1_run_id=run_id_disp,
            manifest_26_sha256=man_sha_disp,
            source_registry_digest=registry.registry_digest,
            stage=stage_code,
        )
        if ns.write_unsigned_template and not dry_viols:
            out_p = Path(ns.write_unsigned_template)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(json.dumps(template, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

        dry_ok = len(dry_viols) == 0
        report = {
            "spec_version": SPEC_VERSION_KH03_R6_A1,
            "mode": "DRY_RUN_NO_SOCKET",
            "status": "DRY_RUN_VALIDATED_ZERO_IO" if dry_ok else "DRY_RUN_VALIDATION_FAILED",
            "stage": stage_code,
            "branch": branch,
            "head_sha": head_sha,
            "tested_executable_sha": tested_sha_disp,
            "publication_head_sha": head_sha,
            "worktree_clean": not dirty,
            "source_registry_status": registry.status,
            "source_registry_digest": registry.registry_digest,
            "resource_preflight": res_guard,
            "plan_check": plan_check,
            "release_binding": rel_summary,
            "external_market_requests_attempted": 0,
            "accepted_real_observed_records": 0,
            "grant_claimed": False,
            "custody_writes_attempted": 0,
            "a1_external_capture_enabled": A1_EXTERNAL_CAPTURE_ENABLED,
            "authorization_status": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED",
            "production_live_paper_auto_modes": "ALL_OFF",
            "violations": dry_viols,
            "unsigned_owner_review_template": template,
            "exit_code": 0 if dry_ok else 2,
        }
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
        return 0 if dry_ok else 2

    # =====================================================================
    # MODE 2: ISOLATED LOCAL QA LOOPBACK END-TO-END CLI MODE (`--qa-loopback-e2e`)
    # =====================================================================
    if ns.qa_loopback_e2e:
        qa_missing: List[str] = []
        if not ns.qa_config:
            qa_missing.append("MISSING_QA_CONFIG_PATH")
        if not ns.grant:
            qa_missing.append("MISSING_QA_GRANT_PATH")
        if not ns.plan:
            qa_missing.append("MISSING_QA_PLAN_PATH")
        if branch != MANDATORY_BRANCH_KH03_R6:
            qa_missing.append(f"WRONG_BRANCH:{branch}")
        if not res_guard["ok"]:
            qa_missing.extend(res_guard["violations"])
        if qa_missing:
            print(json.dumps({
                "spec_version": SPEC_VERSION_KH03_R6_A1,
                "mode": "QA_LOOPBACK_E2E",
                "status": "BLOCKED_QA_E2E_PRECHECK",
                "external_market_requests_attempted": 0,
                "accepted_real_observed_records": 0,
                "grant_claimed": False,
                "violations": qa_missing,
                "exit_code": 2,
            }, indent=2, sort_keys=True))
            return 2

        qa_cfg_p = Path(ns.qa_config)
        if not qa_cfg_p.is_file():
            print(json.dumps({"status": "BLOCKED_QA_CONFIG_MISSING", "grant_claimed": False, "external_market_requests_attempted": 0, "violations": ["QA_CONFIG_NOT_FOUND"], "exit_code": 2}, indent=2))
            return 2
        qa_cfg = _strict_loads_r6(qa_cfg_p.read_bytes(), max_bytes=32_768)
        if not isinstance(qa_cfg, Mapping) or qa_cfg.get("mode") != "QA_TEST_LOOPBACK_ONLY":
            print(json.dumps({"status": "REJECTED_QA_CONFIG_MODE", "grant_claimed": False, "external_market_requests_attempted": 0, "violations": ["QA_CONFIG_MUST_DECLARE_QA_TEST_LOOPBACK_ONLY"], "exit_code": 2}, indent=2))
            return 2

        # Enforce loopback-only URLs strictly BEFORE opening ledger or claiming grant!
        http_base = str(qa_cfg.get("loopback_http_base_url") or "")
        ws_base = str(qa_cfg.get("loopback_ws_base_url") or "")
        for base_u, exp_sch in ((http_base, "http"), (ws_base, "ws")):
            if base_u:
                pu = urlsplit(base_u)
                if pu.scheme.lower() != exp_sch or (pu.hostname or "").lower() not in ALLOWED_LOOPBACK_HOSTS_A1:
                    print(json.dumps({
                        "status": "REJECTED_NONLOOPBACK_URL_IN_QA_MODE",
                        "grant_claimed": False,
                        "external_market_requests_attempted": 0,
                        "accepted_real_observed_records": 0,
                        "violations": [f"NONLOOPBACK_QA_URL_FORBIDDEN:{base_u}"],
                        "exit_code": 2,
                    }, indent=2))
                    return 2

        # Validate QA trust root (strictly QA_ISOLATED_TEST_TRUST — NEVER owner trust root!)
        tr_res = load_r6_owner_trust_root(
            qa_cfg.get("trust_root_path"),
            expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6,
            repo_root=REPO_ROOT,
        )
        if not tr_res["loaded"]:
            print(json.dumps({"status": tr_res["status"], "grant_claimed": False, "external_market_requests_attempted": 0, "violations": tr_res["violations"], "exit_code": 2}, indent=2))
            return 2

        # Validate plan file strictly before opening/claiming
        p_ok, p_doc, p_viols = _validate_dry_run_plan_file(Path(ns.plan), stage_code=stage_code, registry=registry, allow_qa_stage=True)
        if not p_ok or p_doc is None:
            print(json.dumps({"status": "REJECTED_QA_PLAN_INVALID", "grant_claimed": False, "external_market_requests_attempted": 0, "violations": p_viols, "exit_code": 2}, indent=2))
            return 2

        # Verify optional release binding & frozen executable check
        tested_sha_for_grant = head_sha
        rel_sha_for_grant = str(qa_cfg.get("expected_release_manifest_sha256") or "")
        rel_binding_obj = None
        if rel_check is not None:
            if not rel_check["valid"] or rel_check["binding"] is None:
                print(json.dumps({"status": rel_check["status"], "grant_claimed": False, "external_market_requests_attempted": 0, "violations": rel_check["violations"], "exit_code": 2}, indent=2))
                return 2
            rel_binding_obj = rel_check["binding"]
            tested_sha_for_grant = rel_binding_obj.tested_executable_sha
            rel_sha_for_grant = rel_binding_obj.manifest_sha256
        elif not rel_sha_for_grant:
            rel_sha_for_grant = "a" * 64

        now_utc = datetime.now(timezone.utc)
        store_id = str(qa_cfg.get("store_id") or "")
        led_res = open_r6_durable_ledger(
            qa_cfg,
            expected_store_id=store_id,
            expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6,
            repo_root=REPO_ROOT,
            actual_now=now_utc,
        )
        if not led_res["opened"]:
            print(json.dumps({"status": led_res["status"], "grant_claimed": False, "external_market_requests_attempted": 0, "violations": led_res["violations"], "exit_code": 2}, indent=2))
            return 2
        ledger = led_res["ledger"]

        grant_p = Path(ns.grant)
        if not grant_p.is_file():
            print(json.dumps({"status": "BLOCKED_GRANT_FILE_MISSING", "grant_claimed": False, "external_market_requests_attempted": 0, "violations": ["GRANT_FILE_NOT_FOUND"], "exit_code": 2}, indent=2))
            return 2
        gv = verify_r6_owner_grant(
            grant_p.read_bytes(),
            tr_res["trust_root"],
            now_utc,
            expected_executable_sha=tested_sha_for_grant,
            expected_release_manifest_sha256=rel_sha_for_grant,
            expected_store_id=store_id,
            expected_stage="QA_LOOPBACK",
            repo_root=REPO_ROOT,
            ledger_high_water_utc_ms=ledger.get_high_water_utc_ms(),
        )
        if not gv["valid"] or gv["verified_grant"] is None:
            print(json.dumps({"status": gv["status"], "grant_claimed": False, "external_market_requests_attempted": 0, "violations": gv["violations"], "exit_code": 2}, indent=2))
            return 2

        vg = gv["verified_grant"]
        plan_budget = dict(p_doc)
        plan_budget["stage"] = "QA_LOOPBACK"
        plan_res = build_r6_bound_capture_plan(
            vg,
            registry,
            plan_budget,
            res_guard,
            plan_id=f"qa_plan_{ns.stage}_{now_utc.strftime('%Y%m%dT%H%M%SZ')}",
        )
        if not plan_res["valid"] or plan_res["plan"] is None:
            print(json.dumps({"status": plan_res["status"], "grant_claimed": False, "external_market_requests_attempted": 0, "violations": plan_res["violations"], "exit_code": 2}, indent=2))
            return 2

        dest_path = Path(vg.destination_root)
        exp_vols = {"repo": REPO_ROOT, "dest": dest_path, "ledger": ledger.store_root}

        def _res_fn() -> Dict[str, Any]:
            return collect_real_r6_resource_sample(exp_vols, active_workers=1)

        run_id = str(qa_cfg.get("run_id") or f"qa_cli_{ns.stage}_{now_utc.strftime('%Y%m%dT%H%M%SZ')}_{os.getpid()}")
        sess_id = f"sess_{run_id}"
        sess_build = build_r6_a1_acquisition_session(
            run_id=run_id,
            session_id=sess_id,
            verified_grant=vg,
            plan=plan_res["plan"],
            registry=registry,
            ledger=ledger,
            resource_sample_fn=_res_fn,
            expected_volumes=exp_vols,
            repo_root=REPO_ROOT,
            logical_profile=ns.stage,
            qa_loopback_mode=True,
            qa_http_base_url=http_base,
            qa_ws_base_url=ws_base or None,
            release_binding=rel_binding_obj,
            actual_now=now_utc,
        )
        if not sess_build["built"] or sess_build["context"] is None:
            print(json.dumps({
                "status": sess_build["status"],
                "grant_claimed": False,
                "external_market_requests_attempted": 0,
                "accepted_real_observed_records": 0,
                "violations": sess_build["violations"],
                "exit_code": 2,
            }, indent=2, sort_keys=True))
            return 2

        receipt = run_r6_a1_acquisition_session(
            ctx=sess_build["context"],
            verified_grant=vg,
            plan=plan_res["plan"],
            registry=registry,
            ledger=ledger,
            resource_sample_fn=_res_fn,
            expected_volumes=exp_vols,
            repo_root=REPO_ROOT,
            utc_now_fn=lambda: datetime.now(timezone.utc),
        )
        out_receipt = {k: v for k, v in receipt.items() if k != "lifecycle_traces"}
        out_receipt["mode"] = "QA_LOOPBACK_E2E"
        out_receipt["cli_pid"] = os.getpid()
        print(json.dumps(out_receipt, indent=2, sort_keys=True, ensure_ascii=False))
        return int(out_receipt.get("exit_code", 2))

    # =====================================================================
    # MODE 3: OWNER EXTERNAL EXECUTION (`--execute-authorized`) — HARD-BLOCKED IN A1 BEFORE CLAIM!
    # =====================================================================
    missing_prereqs: List[str] = []
    if not ns.execute_authorized:
        missing_prereqs.append("EXPLICIT_EXECUTE_AUTHORIZED_FLAG_REQUIRED")
    if not ns.grant:
        missing_prereqs.append("MISSING_SIGNED_GRANT_PATH")
    if not ns.owner_config:
        missing_prereqs.append("MISSING_OWNER_CONFIG_PATH")
    if not ns.release_manifest:
        missing_prereqs.append("MISSING_STAGE_A_RELEASE_MANIFEST")
    if branch != MANDATORY_BRANCH_KH03_R6:
        missing_prereqs.append(f"WRONG_BRANCH:{branch}")
    if dirty:
        missing_prereqs.append("DIRTY_WORKTREE_FORBIDDEN_FOR_AUTHORIZED_CAPTURE")
    if not res_guard["ok"]:
        missing_prereqs.extend(res_guard["violations"])

    if missing_prereqs:
        print(json.dumps({
            "spec_version": SPEC_VERSION_KH03_R6_A1,
            "status": "BLOCKED_OWNER_AUTHORIZATION",
            "stage": stage_code,
            "grant_claimed": False,
            "external_market_requests_attempted": 0,
            "accepted_real_observed_records": 0,
            "violations": missing_prereqs,
            "exit_code": 2,
        }, indent=2, sort_keys=True))
        return 2

    # Verify frozen release binding (F27) & evidence-only descendant/frozen executable check (F28)
    rel_bind = verify_r6_a1_release_binding(
        repo_root=REPO_ROOT,
        release_manifest_path=Path(ns.release_manifest),
        expected_manifest_sha256=ns.expected_manifest_sha256,
        latest_pointer_path=Path(ns.latest_pointer) if ns.latest_pointer else None,
        remote_receipt_path=Path(ns.release_receipt) if ns.release_receipt else None,
        expected_branch=MANDATORY_BRANCH_KH03_R6,
        require_pinned_manifest_sha=True,
        require_remote_receipt=bool(ns.release_receipt is not None),
        verify_sealed_inventory_files=True,
    )
    if not rel_bind["valid"] or rel_bind["binding"] is None:
        print(json.dumps({
            "spec_version": SPEC_VERSION_KH03_R6_A1,
            "status": "BLOCKED_STAGE_A_RELEASE_UNVERIFIED_OR_SHA_DRIFT",
            "stage": stage_code,
            "grant_claimed": False,
            "external_market_requests_attempted": 0,
            "accepted_real_observed_records": 0,
            "violations": rel_bind["violations"],
            "exit_code": 2,
        }, indent=2, sort_keys=True))
        return 2

    frozen_ctx = prepare_r6_a1_frozen_execution_context(
        repo_root=REPO_ROOT,
        release_binding=rel_bind["binding"],
        mode="verify_evidence_only_descendant",
        require_clean_worktree=True,
        check_pythonpath=True,
    )
    if not frozen_ctx["ready"]:
        print(json.dumps({
            "spec_version": SPEC_VERSION_KH03_R6_A1,
            "status": frozen_ctx["status"],
            "stage": stage_code,
            "grant_claimed": False,
            "external_market_requests_attempted": 0,
            "accepted_real_observed_records": 0,
            "violations": frozen_ctx["violations"],
            "exit_code": 2,
        }, indent=2, sort_keys=True))
        return 2

    # HARD A1 POLICY: ALWAYS return BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED BEFORE reading/claiming any real owner grant!
    if not A1_EXTERNAL_CAPTURE_ENABLED:
        print(json.dumps({
            "spec_version": SPEC_VERSION_KH03_R6_A1,
            "status": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED",
            "stage": stage_code,
            "grant_claimed": False,
            "owner_secret_read": False,
            "external_market_requests_attempted": 0,
            "accepted_real_observed_records": 0,
            "release_binding": rel_bind["binding"].to_redacted_dict(),
            "frozen_context": frozen_ctx["context"].to_redacted_dict(),
            "reason_vi": (
                "KH-03.R6.A1 chi dong vung tin cay QA loopback. Capture mang ngoai BI KHOA CUNG "
                "truoc khi tieu thu grant cua chu du an; can giai doan R6.B rieng biet de mo khoa."
            ),
            "violations": ["A1_EXTERNAL_CAPTURE_DISABLED_ZERO_GRANT_CONSUMED"],
            "exit_code": 2,
        }, indent=2, sort_keys=True))
        return 2

    # Unreachable in A1
    return 2


if __name__ == "__main__":
    raise SystemExit(run_capture_cli())
