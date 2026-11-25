#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_03_r6_capture.py

AURUM DESK — KH-03.R6 (T2) | EXPLICIT OWNER-AUTHORIZED PUBLIC MARKET DATA ACQUISITION CLI
SPEC VERSION: KH-03.R6-v1.0 | READ-ONLY PUBLIC BITGET CLASSIC V2 ONLY | ZERO TRADING | DEFAULT DRY-RUN

Hard Security & Non-Bypass Invariants:
  - Separate entrypoint from offline acceptance (`run_kh_03_r6_acceptance.py`).
  - FORBIDS `--allow-live`, `--force`, `--skip-signature`, `--owner-waive-disk-preflight`, or any
    environment-variable / CLI / prompt approval bypass (immediate exit code 2!).
  - Default mode is `--dry-run`: opens ZERO network sockets (not even public server time!), validates
    source docs contract registry & local resource preflight, emits an UNSIGNED owner review template
    and exact local commands, and stops cleanly.
  - External PUBLIC READ-ONLY execution requires ALL of:
      1. `--execute-authorized` (explicit opt-in) AND `--stage smoke|dev`
      2. `--release-manifest <sealed Stage A 26_independent_OS_verifier_manifest_sha256.json>` bound to
         the exact current frozen Git executable SHA (`git rev-parse HEAD` with clean worktree)
      3. `--owner-config <local JSON outside Git>` pointing to an independently provisioned Ed25519
         owner trust root (`OWNER_EXTERNAL_PRODUCTION_TRUST`) and an explicitly provisioned SQLite
         durable ledger + external anchor (`ledger.sqlite3` + `ledger.anchor.json`)
      4. `--grant <local Ed25519-signed one-shot grant JSON>` matching exact stage (`B_SMOKE` vs `C_DEV`),
         frozen executable SHA, release manifest SHA, store ID, finite expiry, and conservative caps.
  - NEVER touches private/account/order endpoints; NEVER switches LIVE/PAPER/AUTO/PRODUCTION modes.
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
                "error": "CLI/env approval bypass or disk waiver is strictly forbidden in KH-03.R6.",
                "external_market_requests_attempted": 0,
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
from typing import Any, Dict, List, Optional

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_03_r6_acceptance_service import build_r6_unsigned_owner_review_template
from services.kh_03_r6_bounded_transport_service import (
    build_r6_bound_capture_plan,
    collect_real_r6_resource_sample,
    validate_r6_resource_guard,
)
from services.kh_03_r6_custody_readiness_service import assign_r6_dev_partition_before_io
from services.kh_03_r6_durable_grant_service import (
    OWNER_EXTERNAL_TRUST_NAMESPACE_R6,
    _strict_loads_r6,
    load_r6_owner_trust_root,
    open_r6_durable_ledger,
    verify_r6_owner_grant,
)
from services.kh_03_r6_source_contract_service import (
    MANDATORY_BRANCH_KH03_R6,
    SPEC_VERSION_KH03_R6,
    build_r6_source_contract_registry,
)


def _git_output(args: List[str]) -> str:
    proc = subprocess.run(["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, timeout=15)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_capture_cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="KH-03.R6 Read-Only Public Market Data Capture CLI (Default --dry-run)")
    parser.add_argument("--stage", choices=("smoke", "dev"), required=True)
    parser.add_argument("--dry-run", action="store_true", default=False, help="Validate docs/plan/resources and print unsigned owner review template (0 sockets).")
    parser.add_argument("--execute-authorized", action="store_true", default=False, help="Explicit opt-in requiring genuine signed grant, owner trust root, durable ledger, and Stage A release manifest.")
    parser.add_argument("--plan", default=None, help="Optional local JSON plan file to validate in --dry-run mode.")
    parser.add_argument("--grant", default=None, help="Local Ed25519-signed one-shot owner grant JSON outside Git.")
    parser.add_argument("--owner-config", default=None, help="Local owner configuration JSON outside Git specifying trust root & SQLite ledger store.")
    parser.add_argument("--release-manifest", default=None, help="Sealed Stage A 26_independent_OS_verifier_manifest_sha256.json.")
    parser.add_argument("--write-unsigned-template", default=None, help="Optional local path to save the unsigned owner review template JSON.")
    ns = parser.parse_args(argv)

    stage_code = "B_SMOKE" if ns.stage == "smoke" else "C_DEV"
    registry = build_r6_source_contract_registry()
    head_sha = _git_output(["rev-parse", "HEAD"])
    branch = _git_output(["branch", "--show-current"])
    dirty = bool(_git_output(["status", "--porcelain"]))

    # Resource check on repo & temp volumes
    vol_check_targets = {"repo": REPO_ROOT, "temp": Path(os.environ.get("TEMP") or REPO_ROOT)}
    sample = collect_real_r6_resource_sample(vol_check_targets, active_workers=0)
    res_guard = validate_r6_resource_guard(sample, expected_volumes=vol_check_targets, phase="preflight", allow_fixture_sample=False)

    # Default to dry-run if --dry-run passed OR --execute-authorized not explicitly set!
    is_dry_run = bool(ns.dry_run or (not ns.execute_authorized and ns.grant is None))
    if is_dry_run:
        template = build_r6_unsigned_owner_review_template(
            tested_executable_sha=head_sha or "<UNKNOWN_HEAD>",
            stage_a_run_id="<STAGE_A_CERTIFIED_RUN_ID>",
            source_registry_digest=registry.registry_digest,
            stage=stage_code,
        )
        plan_note: Optional[Dict[str, Any]] = None
        if ns.plan:
            pp = Path(ns.plan)
            if not pp.is_file():
                print(json.dumps({"status": "DRY_RUN_PLAN_FILE_NOT_FOUND", "external_market_requests_attempted": 0}, indent=2))
                return 2
            plan_note = {"plan_path_checked": str(pp), "plan_bytes": pp.stat().st_size, "parsed_ok": True}
            _strict_loads_r6(pp.read_bytes())

        if ns.write_unsigned_template:
            out_p = Path(ns.write_unsigned_template)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(json.dumps(template, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

        report = {
            "spec_version": SPEC_VERSION_KH03_R6,
            "mode": "DRY_RUN_NO_SOCKET",
            "stage": stage_code,
            "branch": branch,
            "head_sha": head_sha,
            "worktree_clean": not dirty,
            "source_registry_status": registry.status,
            "source_registry_digest": registry.registry_digest,
            "resource_preflight": res_guard,
            "plan_check": plan_note,
            "external_market_requests_attempted": 0,
            "accepted_real_observed_records": 0,
            "authorization_status": "BLOCKED_OWNER_AUTHORIZATION",
            "production_live_paper_auto_modes": "ALL_OFF",
            "unsigned_owner_review_template": template,
        }
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
        return 0

    # Authorized execution path: fail closed on ANY missing prerequisite!
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
            "status": "BLOCKED_OWNER_AUTHORIZATION",
            "stage": stage_code,
            "external_market_requests_attempted": 0,
            "accepted_real_observed_records": 0,
            "violations": missing_prereqs,
        }, indent=2, sort_keys=True))
        return 2

    # 1. Verify Stage A independent verifier release manifest matches exact HEAD SHA
    rel_p = Path(ns.release_manifest)
    if not rel_p.is_file():
        print(json.dumps({"status": "BLOCKED_MISSING_RELEASE_MANIFEST", "external_market_requests_attempted": 0, "violations": ["RELEASE_MANIFEST_NOT_FOUND"]}, indent=2))
        return 2
    rel_doc = _strict_loads_r6(rel_p.read_bytes(), max_bytes=2 * 1024 * 1024)
    rel_sha = _sha256_file(rel_p)
    if rel_doc.get("status") != "PASS" or rel_doc.get("tested_executable_sha") != head_sha or rel_doc.get("distinct_os_process_verified") is not True:
        print(json.dumps({
            "status": "BLOCKED_STAGE_A_RELEASE_UNVERIFIED_OR_SHA_DRIFT",
            "external_market_requests_attempted": 0,
            "violations": ["RELEASE_MANIFEST_NOT_PASS_OR_HEAD_SHA_MISMATCH"],
        }, indent=2))
        return 2

    # 2. Load owner config + independently provisioned Owner Ed25519 Trust Root outside Git
    oc_p = Path(ns.owner_config)
    if not oc_p.is_file():
        print(json.dumps({"status": "BLOCKED_OWNER_CONFIG_MISSING", "external_market_requests_attempted": 0, "violations": ["OWNER_CONFIG_NOT_FOUND"]}, indent=2))
        return 2
    owner_cfg = _strict_loads_r6(oc_p.read_bytes(), max_bytes=32_768)

    tr_res = load_r6_owner_trust_root(
        owner_cfg.get("trust_root_path"),
        expected_namespace=OWNER_EXTERNAL_TRUST_NAMESPACE_R6,
        repo_root=REPO_ROOT,
    )
    if not tr_res["loaded"]:
        print(json.dumps({
            "status": tr_res["status"],
            "external_market_requests_attempted": 0,
            "violations": tr_res["violations"],
        }, indent=2))
        return 2

    # 3. Open explicitly provisioned durable SQLite ledger + external anchor (NO auto-init!)
    now_utc = datetime.now(timezone.utc)
    store_id = str(owner_cfg.get("store_id") or "")
    led_res = open_r6_durable_ledger(
        owner_cfg,
        expected_store_id=store_id,
        expected_trust_namespace=OWNER_EXTERNAL_TRUST_NAMESPACE_R6,
        repo_root=REPO_ROOT,
        actual_now=now_utc,
    )
    if not led_res["opened"]:
        print(json.dumps({
            "status": led_res["status"],
            "external_market_requests_attempted": 0,
            "violations": led_res["violations"],
        }, indent=2))
        return 2

    ledger = led_res["ledger"]
    hw_ms = ledger.get_high_water_utc_ms()

    # 4. Verify Ed25519-signed one-shot owner grant
    grant_p = Path(ns.grant)
    if not grant_p.is_file():
        print(json.dumps({"status": "BLOCKED_GRANT_FILE_MISSING", "external_market_requests_attempted": 0, "violations": ["GRANT_FILE_NOT_FOUND"]}, indent=2))
        return 2

    gv = verify_r6_owner_grant(
        grant_p.read_bytes(),
        tr_res["trust_root"],
        now_utc,
        expected_executable_sha=head_sha,
        expected_release_manifest_sha256=rel_sha,
        expected_store_id=store_id,
        expected_stage=stage_code,
        repo_root=REPO_ROOT,
        ledger_high_water_utc_ms=hw_ms,
    )
    if not gv["valid"]:
        print(json.dumps({
            "status": gv["status"],
            "external_market_requests_attempted": 0,
            "violations": gv["violations"],
        }, indent=2))
        return 2

    vg = gv["verified_grant"]
    plan_res = build_r6_bound_capture_plan(vg, registry, {"stage": stage_code}, res_guard, plan_id=f"plan_{stage_code}_{now_utc.strftime('%Y%m%dT%H%M%SZ')}")
    if not plan_res["valid"]:
        print(json.dumps({"status": plan_res["status"], "external_market_requests_attempted": 0, "violations": plan_res["violations"]}, indent=2))
        return 2

    part_res = assign_r6_dev_partition_before_io(
        stage=stage_code,
        provenance_class="RAW_OBSERVED_VERIFIED",
        approved_root=Path(vg.destination_root),
        run_id=f"{now_utc.strftime('%Y%m%dT%H%M%SZ')}_{head_sha[:12]}",
        repo_root=REPO_ROOT,
    )
    if not part_res["assigned"]:
        print(json.dumps({"status": part_res["status"], "external_market_requests_attempted": 0, "violations": part_res["violations"]}, indent=2))
        return 2

    # Claim durable one-shot session BEFORE any external socket
    session_id = f"r6_ext_{stage_code.lower()}_{now_utc.strftime('%Y%m%dT%H%M%SZ')}"
    claim = ledger.claim_r6_grant_session_atomic(vg, plan_res["plan"].plan_digest, session_id, actual_now=now_utc)
    if not claim["claimed"]:
        print(json.dumps({"status": claim["status"], "external_market_requests_attempted": 0, "violations": claim["violations"]}, indent=2))
        return 2

    # Ready for owner-approved bounded execution; finalize cleanly if invoked as verification handshake
    ledger.finalize_r6_session(claim["lease"], quarantine=False, reason="AUTHORIZED_SESSION_HANDSHAKE_COMPLETE", actual_now=datetime.now(timezone.utc))
    print(json.dumps({
        "status": "AUTHORIZED_SESSION_CLAIMED_AND_READY",
        "stage": stage_code,
        "session_id": session_id,
        "partition": part_res["partition"],
        "grant_identity_key": vg.grant_identity_key,
        "plan_digest": plan_res["plan"].plan_digest,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_capture_cli())
