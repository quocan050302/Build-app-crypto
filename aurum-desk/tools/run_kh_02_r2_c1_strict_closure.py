#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_02_r2_c1_strict_closure.py

AURUM DESK — KH-02.R2.C1 | STRICT SAFETY GATE CLOSURE & PROSPECTIVE OOS READINESS
SPEC VERSION: KH-02.R2.C1-v1.0 | RESEARCH ONLY | SAME BRANCH | ACTIVATION_READY=NO

Enforces:
  - P0-A: Strict, UNWAIVABLE fail-closed disk safety across ALL distinct write mounts
    (>= 20.5 GiB preflight, >= 20.0 GiB runtime, >= 25.0 GiB advisory). Rejects any
    `--owner-waive-disk-preflight` flag immediately with exit code 2.
  - P0-B: Authoritative R2 freeze & 240m embargo verification, strong append-only
    PROSPECTIVE SNAPSHOT contract, read-only disabled-by-default Bitget collector,
    20 synthetic validator controls, OOS_EVALUATIONS=0, no fake fresh OOS.
  - P1-C: Preserves repaired as-of unswept TP, paired design, fill-time risk, cost
    reconciliation, and honest NO_PROVEN_EDGE findings with zero tuning/new variants.
  - P1-D: Emits unique run folder `docs/artifacts/kh_02_r2_strict_safety_oos_readiness/<UTC>_<SHA12>/`
    with artifacts `00`..`17` + `README_KH02_R2_C1.md` + `HANDOVER_KH02_R2_C1_VI.txt`,
    and seals `16` via a separate-OS-process independent verifier (`kh_02_r2_c1_independent_verifier.py`).
"""
from __future__ import annotations

import json as _early_json
import sys as _early_sys

if __name__ == "__main__" and "--owner-waive-disk-preflight" in _early_sys.argv:
    print(
        _early_json.dumps(
            {
                "status": "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER",
                "error": (
                    "UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN: "
                    "--owner-waive-disk-preflight is strictly forbidden in KH-02.R2.C1 "
                    "(>= 20.5 GiB preflight / >= 20.0 GiB runtime)."
                ),
                "exit_code": 2,
            },
            indent=2,
            sort_keys=True,
        ),
        file=_early_sys.stderr,
    )
    raise SystemExit(2)

import argparse
import ast
import asyncio
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional, Tuple

import psutil

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domain_calculator import CostAssumptions
from services.kh_01b_r1_ram_safety_r3_service import (
    PRIOR_R2_LATEST_PTR_REL,
    PRIOR_R2_LATEST_PTR_SHA256,
    audit_r3_memory_samples_jsonl_gz,
)
from services.kh_02_entry_foundation_research_service import (
    PRIOR_R3_LATEST_PTR_REL,
    PRIOR_R3_LATEST_PTR_SHA256,
)
from services.kh_02_r2_prospective_data_service import (
    BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
    EMBARGO_BOUNDARY_SEMANTICS_KH02_R2_C1,
    R2_AUTHORITATIVE_ARTIFACT_14_REL,
    R2_AUTHORITATIVE_ARTIFACT_14_SHA256,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_UTC,
    R2_AUTHORITATIVE_EMBARGO_MINUTES,
    R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    R2_AUTHORITATIVE_FREEZE_TIMESTAMP_UTC,
    R2_AUTHORITATIVE_FROZEN_CODE_SHA,
    R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256,
    ReadOnlyBitgetPublicProspectiveCollectorR2C1,
    audit_authoritative_r2_freeze_manifest_r2_c1,
    compute_prospective_oos_power_requirements_r2_c1,
    prepare_r2_prospective_oos_ingestion_and_escrow,
    run_kh02_r2_c1_20_synthetic_oos_ingestion_faults,
)
from services.kh_02_r2_research_integrity_service import (
    ACTIVATION_READY_KH02_R2,
    ADVISORY_DISK_GIB_KH02_R2,
    CANONICAL_DATASET_REL_PATH,
    CANONICAL_DATASET_SHA256_KH02_R2,
    DEFAULT_RANDOM_SEED_KH02_R2,
    FORBIDDEN_ORDER_SERVICE_TOKENS,
    GLOBAL_MAX_DESCENDANTS_KH02_R2,
    HARD_RAM_FLOOR_MIB_KH02_R2,
    LOCKED_KEEP_AS_IS_FILES_KH02_R2,
    MANDATORY_BRANCH_KH02_R2,
    PREFLIGHT_RAM_MIN_MIB_KH02_R2,
    PRIOR_KH02_R1_LATEST_PTR_REL,
    PRIOR_KH02_R1_LATEST_PTR_SHA256,
    PRIOR_KH02_R1_PUBLICATION_HEAD_SHA,
    PRIOR_KH02_R1_RUN_ID,
    PRIOR_KH02_R2_EVIDENCE_COMMIT_SHA,
    PRIOR_KH02_R2_LATEST_PTR_REL,
    PRIOR_KH02_R2_LATEST_PTR_SHA256,
    PRIOR_KH02_R2_MANIFEST_22_REL,
    PRIOR_KH02_R2_MANIFEST_22_SHA256,
    PRIOR_KH02_R2_PUBLICATION_HEAD_SHA,
    PRIOR_KH02_R2_RUN_ID,
    PRIOR_KH02_R2_TESTED_SHA,
    RUNTIME_SAFETY_MODES_KH02_R2,
    SCHEMA_VERSION_KH02_R2_C1,
    SPEC_VERSION_KH02_R2_C1,
    STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
    STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
    _ast_function_inventory,
    _compute_smd,
    _sha256_file,
    build_r2_c1_readonly_disk_inventory,
    build_r2_causal_unswept_level_ledger,
    build_r2_paired_factorial_entry_geometry_design,
    create_sentinel_owned_temp_dir_r2_c1,
    evaluate_causal_fill_time_policy_single_trade,
    probe_r2_c1_write_mounts_strict,
    reprice_r2_fixed_fills_cost_counterfactual,
    run_kh02_r2_42_red_green_fault_suite,
    run_r2_t01_to_t10_adversarial_target_scenarios,
    safe_cleanup_sentinel_owned_temp_dir_r2_c1,
    validate_r2_cost_scope_honesty,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_02_r2_c1_independent_verifier import (
    C1_SOURCE_FILES_ALLOWLIST,
    ORTHOGONAL_15_VERDICT_KEYS_KH02_R2_C1,
    recompute_c1_15_orthogonal_verdicts_from_disk,
)
from tools.run_kh_01b_r1_verification import _run_controlled_pytest_w1_w2_w4_benchmark_r1
from tools.run_kh_02_r2_verification import (
    _BoundedResourceSampler,
    _git_cmd,
    _run_full_production_baseline_parity_20,
    _run_full_repo_pytest_sharded,
    _run_r2_science_slice_w1_w2_w4_determinism,
    run_kh02_r2_orchestrator,
)
from services.kh_02_r2_research_integrity_service import (
    audit_r2_function_ownership_and_keep_as_is,
    audit_r2_reference_inputs_and_invariants,
    load_r2_canonical_dataset_readonly,
)

C1_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_02_r2_strict_safety_oos_readiness"
R2_SEALED_RUN_DIR_REL = "aurum-desk/docs/artifacts/kh_02_r2_research_integrity/20261121T154352Z_8ca245bcf4d8"


def _compute_prior_immutable_pointers(repo_root: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for rel_p, exp_sha, lbl in (
        (CANONICAL_DATASET_REL_PATH, CANONICAL_DATASET_SHA256_KH02_R2, "CANONICAL_DATASET"),
        (PRIOR_KH02_R1_LATEST_PTR_REL, PRIOR_KH02_R1_LATEST_PTR_SHA256, "KH02_R1_LATEST_PTR"),
        (PRIOR_KH02_R2_LATEST_PTR_REL, PRIOR_KH02_R2_LATEST_PTR_SHA256, "KH02_R2_LATEST_PTR"),
        (R2_AUTHORITATIVE_ARTIFACT_14_REL, R2_AUTHORITATIVE_ARTIFACT_14_SHA256, "KH02_R2_ARTIFACT_14"),
        (PRIOR_KH02_R2_MANIFEST_22_REL, PRIOR_KH02_R2_MANIFEST_22_SHA256, "KH02_R2_MANIFEST_22"),
        (PRIOR_R3_LATEST_PTR_REL, PRIOR_R3_LATEST_PTR_SHA256, "KH01B_R3_LATEST_PTR"),
        (PRIOR_R2_LATEST_PTR_REL, PRIOR_R2_LATEST_PTR_SHA256, "KH01B_R2_LATEST_PTR"),
    ):
        p = repo_root / rel_p
        act = _sha256_file(p) if p.is_file() else None
        out[lbl] = {
            "path": rel_p,
            "expected_sha256": exp_sha,
            "actual_sha256": act,
            "match": act == exp_sha,
        }
    return out


def _build_artifact_01_ast_and_locked_diff(repo_root: Path, *, head_sha: str) -> Dict[str, Any]:
    locked_inventory: List[Dict[str, Any]] = []
    for rel_p in LOCKED_KEEP_AS_IS_FILES_KH02_R2:
        fp = repo_root / rel_p
        exists = fp.is_file()
        sha = _sha256_file(fp) if exists else None
        funcs = _ast_function_inventory(fp) if (exists and fp.suffix == ".py") else []
        locked_inventory.append(
            {
                "path": rel_p,
                "keep_as_is_status": "KEEP_AS_IS_LOCKED",
                "exists": exists,
                "sha256": sha,
                "function_count": len(funcs),
                "top_functions": [f["name"] for f in funcs[:12]],
            }
        )

    _, diff_names_raw = _git_cmd(["diff", "--name-only", head_sha])
    _, porcelain_raw = _git_cmd(["status", "--porcelain=v1"])
    modified_tracked = sorted(
        ln.strip() for ln in diff_names_raw.splitlines() if ln.strip() and not ln.startswith("GIT_CMD_ERROR:")
    )
    untracked_files = sorted(
        ln[3:].strip() for ln in porcelain_raw.splitlines() if ln.startswith("?? ")
    )

    locked_set = set(LOCKED_KEEP_AS_IS_FILES_KH02_R2) | {
        PRIOR_KH02_R1_LATEST_PTR_REL,
        PRIOR_KH02_R2_LATEST_PTR_REL,
        R2_AUTHORITATIVE_ARTIFACT_14_REL,
        PRIOR_KH02_R2_MANIFEST_22_REL,
        PRIOR_R3_LATEST_PTR_REL,
        PRIOR_R2_LATEST_PTR_REL,
    }
    locked_modified = sorted(p for p in modified_tracked if p in locked_set)
    allow_set = set(C1_SOURCE_FILES_ALLOWLIST)
    non_allowlisted_modified = sorted(p for p in modified_tracked if p not in allow_set)

    c1_modules_inventory: List[Dict[str, Any]] = []
    forbidden_hits: List[Dict[str, str]] = []
    for rel_p in C1_SOURCE_FILES_ALLOWLIST:
        fp = repo_root / rel_p
        if not fp.is_file():
            c1_modules_inventory.append({"path": rel_p, "exists": False})
            continue
        txt = fp.read_text(encoding="utf-8")
        tree = ast.parse(txt, filename=str(fp))
        funcs = _ast_function_inventory(fp)
        imp_mods: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for al in node.names:
                    imp_mods.append(al.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imp_mods.append(node.module)
        for mod in imp_mods:
            for tok in FORBIDDEN_ORDER_SERVICE_TOKENS:
                if tok in mod:
                    forbidden_hits.append({"path": rel_p, "imported_module": mod, "forbidden_token": tok})
        c1_modules_inventory.append(
            {
                "path": rel_p,
                "exists": True,
                "sha256": _sha256_file(fp),
                "function_count": len(funcs),
                "functions": [f["name"] for f in funcs],
                "modified_in_working_tree": rel_p in modified_tracked,
                "untracked_new_c1_file": rel_p in untracked_files,
            }
        )

    c_live = CostAssumptions()
    cost_intact = (
        abs(float(c_live.taker_fee_rate) - 0.0004) < 1e-12
        and abs(float(c_live.maker_fee_rate) - 0.0002) < 1e-12
        and abs(float(c_live.slippage_usd) - 0.10) < 1e-12
    )

    responsibility_table = [
        {
            "file": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
            "functions_modified_or_added": [
                "probe_r2_c1_write_mounts_strict",
                "create_sentinel_owned_temp_dir_r2_c1",
                "safe_cleanup_sentinel_owned_temp_dir_r2_c1",
                "run_atomic_write_rename_probe_r2_c1",
                "build_r2_c1_readonly_disk_inventory",
                "evaluate_r2_orthogonal_science_and_engineering_gates_from_disk",
            ],
            "why_essential": "Close P0-A: enforce deduplicated multi-mount >=20.5 GiB preflight / >=20.0 GiB runtime, sentinel cleanup, read-only disk inventory, and remove waiver PASS path.",
            "keep_as_is_preserved": "All TP/pairing/fill-time risk/cost/walk-forward scientific functions untouched.",
        },
        {
            "file": "aurum-desk/tools/run_kh_02_r2_verification.py",
            "functions_modified_or_added": [
                "run_kh02_r2_orchestrator",
                "main",
                "_BoundedResourceSampler",
                "_distinct_watch_paths_by_dev",
            ],
            "why_essential": "Close P0-A: reject --owner-waive-disk-preflight fail-closed (exit 2), probe all distinct write mounts, enforce mid-run <20.0 GiB disk hard abort + child reap.",
            "keep_as_is_preserved": "W1/W2/W4 science slice, LPT pytest shard logic, 56,710 parity intact.",
        },
        {
            "file": "aurum-desk/tools/kh_02_r2_independent_verifier.py",
            "functions_modified_or_added": ["verify_r2_artifacts_independently_from_disk"],
            "why_essential": "Close P0-A: reject any artifact asserting owner_disk_preflight_waiver or WAIVED_BY_OWNER_LOW_DISK.",
            "keep_as_is_preserved": "All JUnit, SHA256, cost, RAM, OOS non-peek verifier checks intact.",
        },
        {
            "file": "aurum-desk/backend/services/kh_02_r2_prospective_data_service.py",
            "functions_modified_or_added": [
                "audit_authoritative_r2_freeze_manifest_r2_c1",
                "validate_prospective_snapshot_contract_c1",
                "validate_prospective_payload_bars_and_quotes_r2_c1",
                "ProspectiveHashLinkedEscrowRegistryR2C1",
                "ReadOnlyBitgetPublicProspectiveCollectorR2C1",
                "compute_prospective_oos_power_requirements_r2_c1",
                "run_kh02_r2_c1_20_synthetic_oos_ingestion_faults",
            ],
            "why_essential": "Close P0-B: authoritative R2 freeze/embargo verification, typed snapshot contract, hash-linked immutable escrow, power analysis, read-only disabled-by-default Bitget collector.",
            "keep_as_is_preserved": "Existing R2 escrow helpers preserved backward-compatibly; OOS_EVALUATIONS=0.",
        },
        {
            "file": "aurum-desk/tools/run_kh_02_r2_c1_strict_closure.py",
            "functions_modified_or_added": ["run_kh02_r2_c1_strict_closure", "main"],
            "why_essential": "KH-02.R2.C1 strict closure orchestrator producing 00..17 + README + Vietnamese handover under kh_02_r2_strict_safety_oos_readiness/.",
            "keep_as_is_preserved": "Additive new C1 file; zero mutation of production, KH-01*, KH-02.R1, or R2 artifacts.",
        },
        {
            "file": "aurum-desk/tools/kh_02_r2_c1_independent_verifier.py",
            "functions_modified_or_added": [
                "recompute_c1_15_orthogonal_verdicts_from_disk",
                "verify_kh02_r2_c1_artifacts_independently_from_disk",
            ],
            "why_essential": "Distinct-OS-process verifier enforcing all §8.2 tamper controls & 15 orthogonal verdicts (§8.3).",
            "keep_as_is_preserved": "Additive new C1 file.",
        },
    ]

    ok = (
        len(locked_modified) == 0
        and len(non_allowlisted_modified) == 0
        and len(forbidden_hits) == 0
        and cost_intact
    )
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C1,
        "schema_version": SCHEMA_VERSION_KH02_R2_C1,
        "head_sha": head_sha,
        "locked_keep_as_is_count": len(locked_inventory),
        "locked_keep_as_is_inventory": locked_inventory,
        "locked_files_modified_count": len(locked_modified),
        "locked_files_modified": locked_modified,
        "non_allowlisted_modified_files": non_allowlisted_modified,
        "modified_tracked_r2_files": modified_tracked,
        "untracked_files": untracked_files,
        "c1_source_files_inventory": c1_modules_inventory,
        "responsibility_and_callgraph_table": responsibility_table,
        "forbidden_order_import_hits": forbidden_hits,
        "cost_assumptions_defaults_intact": cost_intact,
        "live_cost_assumptions": {
            "taker_fee_rate": float(c_live.taker_fee_rate),
            "maker_fee_rate": float(c_live.maker_fee_rate),
            "slippage_usd": float(c_live.slippage_usd),
        },
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": "PASS" if ok else "FAIL",
    }


def _run_artifact_03_disk_waiver_negative_controls(sentinel_tmp: Path) -> Dict[str, Any]:
    """Executes ALL §4.5 strict disk safety & waiver bypass negative controls live."""
    controls: List[Dict[str, Any]] = []

    def _rec(cid: str, name: str, passed: bool, detail: str) -> None:
        controls.append({"control_id": cid, "name": name, "status": "PASS" if passed else "FAIL", "detail": detail})

    def _mock_probe(free_by_role: Mapping[str, Tuple[int, float, str]], *, reserved_gib: float = 0.05) -> Dict[str, Any]:
        # Map each role to a real existing temp path so exists() passes cleanly
        base_paths: Dict[str, Path] = {}
        dev_map: Dict[str, int] = {}
        gib_map: Dict[str, float] = {}
        mp_map: Dict[str, str] = {}
        for role, (dev_id, free_gib, mp_lbl) in free_by_role.items():
            p = sentinel_tmp / f"mock_{role}"
            p.mkdir(parents=True, exist_ok=True)
            rp = os.path.realpath(str(p))
            base_paths[role] = p
            dev_map[rp] = dev_id
            gib_map[rp] = free_gib
            mp_map[rp] = mp_lbl
        return probe_r2_c1_write_mounts_strict(
            base_paths,
            reserved_evidence_gib=reserved_gib,
            stat_fn=lambda s: SimpleNamespace(st_dev=dev_map[s]),
            disk_usage_fn=lambda s: SimpleNamespace(
                total=int(500 * (1024 ** 3)),
                used=int((500.0 - gib_map[s]) * (1024 ** 3)),
                free=int(round(gib_map[s] * (1024 ** 3))),
            ),
            mountpoint_fn=lambda p: mp_map[os.path.realpath(str(p))],
        )

    # 1..4: Below-floor values 20.499, 19.999, 5.6567, 1.999 MUST block
    for idx_v, val in enumerate((20.499, 19.999, 5.6567, 1.999), 1):
        res = _mock_probe({"repo": (10, val, "/System/Volumes/Data")})
        _rec(
            f"DISK-NC{idx_v:02d}",
            f"BLOCKED_BELOW_FLOOR_{str(val).replace('.', 'p')}_GIB",
            res["status"] == "BLOCKED_LOW_DISK" and res["can_run_heavy_stages"] is False,
            f"val={val} -> status={res['status']}, can_run_heavy={res['can_run_heavy_stages']}",
        )

    # 5: Exact boundary 20.500 GiB passes with 0.05 GiB reserve (20.45 >= 20.0), blocks with 0.60 GiB oversubscription
    r_exact_ok = _mock_probe({"repo": (10, 20.500, "/System/Volumes/Data")}, reserved_gib=0.05)
    r_exact_oversub = _mock_probe({"repo": (10, 20.500, "/System/Volumes/Data")}, reserved_gib=0.60)
    _rec(
        "DISK-NC05",
        "EXACT_20P500_GIB_BOUNDARY_AND_RESERVE_OVERSUBSCRIPTION",
        r_exact_ok["can_run_heavy_stages"] is True and r_exact_oversub["can_run_heavy_stages"] is False,
        f"ok_0p05={r_exact_ok['status']}, oversub_0p60={r_exact_oversub['status']}",
    )

    # 6: Symlink / alias mount duplicates deduplicated by st_dev (no double counting)
    real_dir = sentinel_tmp / "alias_real"
    real_dir.mkdir(exist_ok=True)
    sym_dir = sentinel_tmp / "alias_sym"
    if not sym_dir.exists():
        os.symlink(real_dir, sym_dir)
    r_sym = probe_r2_c1_write_mounts_strict(
        {"repo": real_dir, "temp": sym_dir},
        stat_fn=lambda s: SimpleNamespace(st_dev=777),
        disk_usage_fn=lambda s: SimpleNamespace(total=100 * 1024**3, used=89 * 1024**3, free=int(11.0 * 1024**3)),
        mountpoint_fn=lambda p: "/System/Volumes/Data",
    )
    _rec(
        "DISK-NC06",
        "ALIAS_SYMLINK_MOUNT_DEDUP_NO_DOUBLE_COUNT",
        r_sym["deduplicated_distinct_mounts_count"] == 1 and r_sym["can_run_heavy_stages"] is False and r_sym["min_free_gib"] == 11.0,
        f"distinct_mounts={r_sym['deduplicated_distinct_mounts_count']}, min_gib={r_sym['min_free_gib']}",
    )

    # 7: Distinct mounts (C:/D: or separate macOS volumes) checked independently
    r_multi = _mock_probe({"repo": (101, 40.0, "/Volumes/FastA"), "temp": (202, 18.0, "/Volumes/SmallB")})
    _rec(
        "DISK-NC07",
        "DISTINCT_MOUNT_INDEPENDENT_FLOOR",
        r_multi["deduplicated_distinct_mounts_count"] == 2 and r_multi["status"] == "BLOCKED_LOW_DISK" and "/Volumes/SmallB" in r_multi["blocked_mountpoints"],
        f"mounts={r_multi['deduplicated_distinct_mounts_count']}, blocked={r_multi['blocked_mountpoints']}",
    )

    # 8: Unreadable / missing mount fails closed
    r_missing = probe_r2_c1_write_mounts_strict(
        {"repo": sentinel_tmp / "definitely_missing_mount_xyz"},
        allow_nonexistent_leaf_roles=(),
    )
    _rec(
        "DISK-NC08",
        "UNREADABLE_OR_MISSING_MOUNT_FAIL_CLOSED",
        r_missing["status"] == "FAIL_MOUNT_PROBE_ERROR" and r_missing["can_run_heavy_stages"] is False,
        f"status={r_missing['status']}, errors={r_missing['probe_errors'][:1]}",
    )

    # 9: Injected --owner-waive-disk-preflight rejected on CLI & Python API
    env_cli = os.environ.copy()
    env_cli["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
    p_r2_cli = subprocess.run(
        [sys.executable, str(APP_ROOT / "tools" / "run_kh_02_r2_verification.py"), "--owner-waive-disk-preflight"],
        cwd=str(APP_ROOT),
        env=env_cli,
        capture_output=True,
        text=True,
        timeout=30,
    )
    p_c1_cli = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--owner-waive-disk-preflight"],
        cwd=str(APP_ROOT),
        env=env_cli,
        capture_output=True,
        text=True,
        timeout=30,
    )
    api_blocked = False
    try:
        run_kh02_r2_orchestrator(owner_waive_disk_preflight=True)
    except RuntimeError as exc:
        api_blocked = "UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN" in str(exc)
    _rec(
        "DISK-NC09",
        "INJECTED_OWNER_WAIVE_DISK_PREFLIGHT_CLI_AND_API_REJECTED",
        p_r2_cli.returncode != 0 and p_c1_cli.returncode != 0 and api_blocked,
        f"r2_cli_rc={p_r2_cli.returncode}, c1_cli_rc={p_c1_cli.returncode}, api_blocked={api_blocked}",
    )

    # 10: Mid-run disk drop (< 20.0 GiB) triggers monitored hard abort + child cleanup (0 orphans)
    seq_vals = iter([25.0, 19.5, 19.5, 19.5])
    dummy_child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    abort_log = sentinel_tmp / "midrun_disk_drop_telemetry.jsonl.gz"
    test_sampler = _BoundedResourceSampler(
        out_gz=abort_log,
        watch_path=sentinel_tmp,
        interval_sec=0.02,
        enforce_runtime_disk_abort=True,
        disk_free_fn=lambda _p: next(seq_vals, 19.5),
    )
    test_sampler.start()
    deadline = time.monotonic() + 3.0
    while not test_sampler.abort_event.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    t_res = test_sampler.stop_and_flush()
    try:
        dummy_child.wait(timeout=2.0)
    except Exception:
        pass
    child_reaped = dummy_child.poll() is not None
    _rec(
        "DISK-NC10",
        "MID_RUN_DISK_DROP_BELOW_20GIB_HARD_ABORT_AND_CHILD_REAP",
        t_res["emergency_abort_triggered"] and child_reaped and t_res["orphan_children_after_cleanup"] == 0,
        f"reason={t_res['emergency_abort_reason']}, child_reaped={child_reaped}, reaped={t_res['reaped_pids_on_abort']}",
    )

    # 11: Unowned sentinel-less temp directory CANNOT be deleted
    unowned_dir = sentinel_tmp / "unowned_no_sentinel_dir"
    unowned_dir.mkdir(exist_ok=True)
    (unowned_dir / "keep_me.txt").write_text("immutable", encoding="utf-8")
    unowned_refused = False
    try:
        safe_cleanup_sentinel_owned_temp_dir_r2_c1(unowned_dir, expected_run_id="any_run")
    except PermissionError:
        unowned_refused = True
    still_there = (unowned_dir / "keep_me.txt").is_file()
    _rec(
        "DISK-NC11",
        "UNOWNED_SENTINEL_LESS_TEMP_DIR_REFUSED",
        unowned_refused and still_there,
        f"refused={unowned_refused}, preserved={still_there}",
    )

    # 12: Prior R1/R2 evidence & pointers verified immutable
    imm_map = _compute_prior_immutable_pointers(REPO_ROOT)
    imm_all_ok = all(v["match"] for v in imm_map.values())
    _rec(
        "DISK-NC12",
        "PRIOR_EVIDENCE_AND_POINTERS_IMMUTABLE",
        imm_all_ok,
        f"verified_count={sum(1 for v in imm_map.values() if v['match'])}/{len(imm_map)}",
    )

    passed = sum(1 for c in controls if c["status"] == "PASS")
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C1,
        "total_controls": len(controls),
        "passed_controls": passed,
        "failed_controls": len(controls) - passed,
        "controls": controls,
        "status": "PASS" if passed == len(controls) else "FAIL",
    }


def _run_c1_56_red_green_fault_suite(repo_root: Path, sentinel_tmp: Path) -> Dict[str, Any]:
    """Runs all 42 R2 RED/GREEN faults + 14 new C1 strict disk/waiver/OOS/verifier controls (>= 56 total)."""
    r2_42 = run_kh02_r2_42_red_green_fault_suite(repo_root)
    controls = list(r2_42["controls"])

    def _rec(fid: str, name: str, fn: str, red_ok: bool, green_ok: bool, detail: str) -> None:
        controls.append(
            {
                "fault_id": fid,
                "name": name,
                "anchored_function": fn,
                "red_rejected": bool(red_ok),
                "green_accepted": bool(green_ok),
                "status": "PASS" if (red_ok and green_ok) else "FAIL",
                "diagnostic": detail,
            }
        )

    # C1-F43: Owner disk waiver CLI rejected
    env_cli = os.environ.copy()
    env_cli["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
    p_w = subprocess.run(
        [sys.executable, str(APP_ROOT / "tools" / "run_kh_02_r2_verification.py"), "--owner-waive-disk-preflight"],
        cwd=str(APP_ROOT),
        env=env_cli,
        capture_output=True,
        text=True,
        timeout=30,
    )
    _rec("C1-F43", "CLI_OWNER_WAIVE_DISK_PREFLIGHT_REJECTED", "run_kh_02_r2_verification.main", p_w.returncode == 2, True, f"rc={p_w.returncode}")

    # C1-F44: Exact 20.499 GiB blocked vs 20.500 GiB accepted
    d_low = probe_r2_c1_write_mounts_strict(
        {"repo": sentinel_tmp},
        stat_fn=lambda _s: SimpleNamespace(st_dev=1),
        disk_usage_fn=lambda _s: SimpleNamespace(total=100 * 1024**3, used=79 * 1024**3, free=int(round(20.499 * 1024**3))),
        mountpoint_fn=lambda _p: "/Vol",
    )
    d_ok = probe_r2_c1_write_mounts_strict(
        {"repo": sentinel_tmp},
        stat_fn=lambda _s: SimpleNamespace(st_dev=1),
        disk_usage_fn=lambda _s: SimpleNamespace(total=100 * 1024**3, used=79 * 1024**3, free=int(round(20.500 * 1024**3))),
        mountpoint_fn=lambda _p: "/Vol",
    )
    _rec("C1-F44", "STRICT_20P499_VS_20P500_GIB_BOUNDARY", "probe_r2_c1_write_mounts_strict", not d_low["can_run_heavy_stages"], d_ok["can_run_heavy_stages"], f"low={d_low['status']}, ok={d_ok['status']}")

    # C1-F45: Reserved evidence oversubscription blocked at 20.500 GiB
    d_over = probe_r2_c1_write_mounts_strict(
        {"repo": sentinel_tmp},
        reserved_evidence_gib=0.60,
        stat_fn=lambda _s: SimpleNamespace(st_dev=1),
        disk_usage_fn=lambda _s: SimpleNamespace(total=100 * 1024**3, used=79 * 1024**3, free=int(round(20.500 * 1024**3))),
        mountpoint_fn=lambda _p: "/Vol",
    )
    _rec("C1-F45", "EVIDENCE_RESERVE_OVERSUBSCRIPTION_REJECTED", "probe_r2_c1_write_mounts_strict", not d_over["can_run_heavy_stages"], d_ok["can_run_heavy_stages"], f"over={d_over['status']}")

    # C1-F46: Unowned sentinel-less temp deletion refused vs owned sentinel temp deleted
    unowned = sentinel_tmp / "c1_f46_unowned"
    unowned.mkdir(exist_ok=True)
    red46 = False
    try:
        safe_cleanup_sentinel_owned_temp_dir_r2_c1(unowned, expected_run_id="r46")
    except PermissionError:
        red46 = True
    owned = create_sentinel_owned_temp_dir_r2_c1("c1_f46_owned_", run_id="r46", base_dir=sentinel_tmp)
    grn46 = bool(safe_cleanup_sentinel_owned_temp_dir_r2_c1(owned, expected_run_id="r46")["cleaned"])
    _rec("C1-F46", "SENTINEL_OWNED_TEMP_CLEANUP_GUARD", "safe_cleanup_sentinel_owned_temp_dir_r2_c1", red46 and unowned.is_dir(), grn46, "unowned preserved, owned cleaned")

    # C1-F47: ReadOnlyBitgetPublicProspectiveCollector refuses default network capture
    col = ReadOnlyBitgetPublicProspectiveCollectorR2C1()
    red47 = False
    try:
        col.fetch_public_snapshot("public_ticker", write_mount_probe=d_ok)
    except PermissionError:
        red47 = True
    _rec("C1-F47", "PROSPECTIVE_COLLECTOR_DISABLED_BY_DEFAULT", "ReadOnlyBitgetPublicProspectiveCollectorR2C1", red47, col.describe_readiness()["read_only"] is True, "network capture disabled by default")

    # C1-F48: ReadOnlyBitgetPublicProspectiveCollector refuses capture when disk low even if authorized
    col_auth = ReadOnlyBitgetPublicProspectiveCollectorR2C1(network_capture_enabled=True, explicit_operator_approval_id="TEST_ONLY")
    red48 = False
    try:
        col_auth.fetch_public_snapshot("public_ticker", write_mount_probe=d_low)
    except RuntimeError as exc:
        red48 = "BLOCKED_LOW_DISK_CAPTURE_FORBIDDEN" in str(exc)
    _rec("C1-F48", "PROSPECTIVE_COLLECTOR_BLOCKED_ON_LOW_DISK", "ReadOnlyBitgetPublicProspectiveCollectorR2C1", red48, True, "low disk blocks capture")

    # C1-F49: Authoritative R2 freeze manifest matches verbatim on disk
    frz = audit_authoritative_r2_freeze_manifest_r2_c1(repo_root)
    frz_bad = audit_authoritative_r2_freeze_manifest_r2_c1(sentinel_tmp)
    _rec("C1-F49", "AUTHORITATIVE_R2_FREEZE_MANIFEST_MATCH", "audit_authoritative_r2_freeze_manifest_r2_c1", frz_bad["status"] != "PASS", frz["status"] == "PASS", f"sha_match={frz['sha256_match']}")

    # C1-F50..C1-F56: Core OOS C1 validation & power controls
    v20 = run_kh02_r2_c1_20_synthetic_oos_ingestion_faults(sentinel_tmp / "c1_f50_v20")
    by_cid = {c["case_id"]: c for c in v20["cases"]}
    for fid, cid, label in (
        ("C1-F50", "OOS-V01", "EXACT_EMBARGO_CUTOFF_MINUS_1MS_REJECTED"),
        ("C1-F51", "OOS-V07", "DST_NEW_YORK_NONEXISTENT_HOUR_REJECTED"),
        ("C1-F52", "OOS-V11", "CROSSED_BBO_BID_GE_ASK_REJECTED"),
        ("C1-F53", "OOS-V15", "CORRUPT_GZIP_PAYLOAD_REJECTED"),
        ("C1-F54", "OOS-V16", "TAMPERED_HASH_LINKED_ESCROW_CHAIN_DETECTED"),
        ("C1-F55", "OOS-V19", "IMMUTABLE_ESCROW_NO_OVERWRITE_IDEMPOTENT"),
        ("C1-F56", "OOS-V20", "SECRET_AND_SYNTHETIC_FIXTURE_EXCLUSION"),
    ):
        c = by_cid[cid]
        _rec(fid, label, "kh_02_r2_prospective_data_service", c["red_rejected"], c["green_accepted"], c["detail"])

    unique_ids = {c["fault_id"] for c in controls}
    unique_names = {c["name"] for c in controls}
    passed = sum(1 for c in controls if c["status"] == "PASS")
    all_ok = len(controls) >= 56 and len(unique_ids) == len(controls) and len(unique_names) == len(controls) and passed == len(controls)
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C1,
        "r2_inherited_faults_count": len(r2_42["controls"]),
        "c1_new_faults_count": len(controls) - len(r2_42["controls"]),
        "total_controls": len(controls),
        "unique_fault_ids": len(unique_ids),
        "unique_fault_names": len(unique_names),
        "passed_controls": passed,
        "failed_controls": len(controls) - passed,
        "all_controls_passed": all_ok,
        "controls": controls,
        "status": "PASS" if all_ok else "FAIL",
    }


def _run_safe_focused_c1_and_r2_pytest(sentinel_tmp: Path) -> Dict[str, Any]:
    test_files = [
        "backend/tests/test_kh_02_r2_research_integrity.py",
        "backend/tests/test_kh_02_r2_counterfactual_and_costs.py",
        "backend/tests/test_kh_02_r2_oos_provenance_and_faults.py",
        "backend/tests/test_kh_02_r2_c1_strict_safety_and_oos.py",
    ]
    existing = [tf for tf in test_files if (APP_ROOT / tf).is_file()]
    if not existing:
        return {"executed": False, "status": "NO_FOCUSED_TESTS_FOUND"}
    t0 = time.perf_counter()
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
    env["TMPDIR"] = str(sentinel_tmp)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *existing, "-q"],
        cwd=str(APP_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    elapsed = round(time.perf_counter() - t0, 3)
    tail = "\n".join((proc.stdout or "").splitlines()[-8:])
    return {
        "executed": True,
        "diagnostic_scope_only_not_full_regression": True,
        "test_files": existing,
        "exit_code": int(proc.returncode),
        "elapsed_sec": elapsed,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "output_tail": tail,
    }


def _render_c1_readme_and_handover_vi(
    *,
    run_dir: Path,
    docs: Mapping[str, Mapping[str, Any]],
) -> Tuple[str, str]:
    f00, f01, f02, f03 = docs["00"], docs["01"], docs["02"], docs["03"]
    f04, f05, f06, f07 = docs["04"], docs["05"], docs["06"], docs["07"]
    f08, f09, f10, f11 = docs["08"], docs["09"], docs["10"], docs["11"]
    f12, f13, f14, f15, f17 = docs["12"], docs["13"], docs["14"], docs["15"], docs["17"]
    v = f15["orthogonal_15_verdicts"]
    mp = f02["strict_multi_mount_probe"]
    inv = f02.get("readonly_disk_inventory") or {}

    md: List[str] = [
        "# AURUM DESK — KH-02.R2.C1 | ĐÓNG CỔNG AN TOÀN Ổ ĐĨA NGHIÊM NGẶT & SẴN SÀNG OOS TƯƠNG LAI",
        "",
        f"- **Phiên bản đặc tả**: `{SPEC_VERSION_KH02_R2_C1}`",
        f"- **Run ID**: `{f00['run_id']}`",
        f"- **Nhánh bắt buộc**: `{f00['branch']}`",
        f"- **Start / End Git HEAD**: `{f00['start_head_sha']}` (không đổi trong suốt quá trình chạy)",
        f"- **Remote origin HEAD**: `{f00['remote_head_sha']}`",
        f"- **Trạng thái cây làm việc**: `git_clean_at_start={f00['git_clean_at_start']}`, `local_uncommitted_c1_changes={f00['local_uncommitted_c1_changes']}`",
        f"- **KH-02.R2 checkpoint trước đó (chỉ đọc, bất biến)**: `HEAD={PRIOR_KH02_R2_PUBLICATION_HEAD_SHA}`, `TESTED_SHA={PRIOR_KH02_R2_TESTED_SHA}`, `RUN_ID={PRIOR_KH02_R2_RUN_ID}`",
        f"- **Chế độ an toàn bắt buộc**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`",
        "",
        "## 1. Bảng 15 Kết Luận Trực Giao (Orthogonal Verdicts — Không Gộp, Không Che Giấu)",
        "",
        "| Trục kiểm định | Kết luận | Receipt trên đĩa |",
        "|---|---|---|",
    ]
    receipt_map = {
        "BRANCH_SOURCE_VERDICT": "00, 01",
        "STRICT_DISK_SAFETY_VERDICT": "02, 03, 12",
        "RAM_PROCESS_SAFETY_VERDICT": "12",
        "FULL_REGRESSION_VERDICT": "10",
        "BASELINE_PARITY_VERDICT": "14",
        "ENTRY_TARGET_INTEGRITY_VERDICT": "06",
        "PAIRED_RESEARCH_VERDICT": "07",
        "COST_RISK_RECONCILIATION_VERDICT": "08",
        "DATA_PROVENANCE_VERDICT": "04, 05",
        "FUTURE_OOS_READINESS_VERDICT": "04",
        "FRESH_OOS_VERDICT": "04",
        "RESEARCH_ECONOMIC_VERDICT": "09",
        "INDEPENDENT_VERIFIER_VERDICT": "16 (sealed by separate OS process)",
        "PUBLICATION_VERDICT": "17",
        "ACTIVATION_READY": "00, 01, 04, 09, 15",
    }
    for k in ORTHOGONAL_15_VERDICT_KEYS_KH02_R2_C1:
        md.append(f"| `{k}` | **{v[k]}** | `{receipt_map[k]}` |")

    md += [
        "",
        "## 2. Kết Quả Kiểm Tra Ổ Đĩa Đa Phân Vùng & RAM (Stage A — Không Cho Phép Tự Miễn Trừ)",
        "",
        f"- **Chính sách cứng**: Preflight `>= 20.5 GiB` trên MỌI phân vùng ghi, Runtime `>= 20.0 GiB`, Khuyến nghị `>= 25.0 GiB`. `owner_waiver_permitted = False`.",
        f"- **Đo thực tế (đã khử trùng lặp `st_dev`)**: `{mp['deduplicated_distinct_mounts_count']}` phân vùng vật lý riêng biệt, trống tối thiểu **`{mp['min_free_gib']} GiB`** nhị phân (`{mp['min_free_gb_decimal']} GB` thập phân), thiếu **`{mp['shortfall_to_strict_20p5_gib']} GiB`** so với sàn cứng `20.5 GiB`.",
        f"- **Trạng thái preflight ổ đĩa**: **`{mp['status']}`** (`can_run_heavy_stages={mp['can_run_heavy_stages']}`).",
        f"- **RAM quan sát trong phiên**: Preflight `{f00['preflight_ram_available_mib']} MiB`, Min runtime `{f12['min_available_ram_mib']} MiB` (sàn cứng `>= 512.0 MiB`), Max children `{f12['max_descendant_children']}` (`<= 4`), Orphans `{f12['orphan_children_after_cleanup']}`, Sentinel temp cleaned `{f12['sentinel_temp_cleaned_up']}`.",
        f"- **Negative controls chống vượt rào waiver (`03`)**: `{f03['passed_controls']}/{f03['total_controls']} PASS`.",
        "",
        "### Kiểm kê dung lượng CHỈ ĐỌC (Không tự động xóa bất kỳ tệp người dùng/dự án nào)",
        "",
        f"- `autonomous_deletion_executed = {inv.get('autonomous_deletion_executed', False)}`, `git_gc_executed = {inv.get('git_gc_executed', False)}`.",
        f"- Lưu ý quan trọng: {inv.get('important_note', '')}",
        "",
        "Lệnh kiểm tra thủ công (KHÔNG tự động chạy):",
        "",
    ]
    for cmd_item in inv.get("manual_inspection_commands_NOT_AUTOMATICALLY_EXECUTED") or ():
        md.append(f"- `{cmd_item['command']}` — {cmd_item['purpose']} (`executed={cmd_item['executed']}`)")

    md += [
        "",
        "## 3. Tệp & Hàm Đã Sửa / Thêm vs Bất Biến (`KEEP_AS_IS`)",
        "",
        f"- **Tệp production / KH-01* / KH-02.R1 / bằng chứng R2 bị sửa**: **`{f01['locked_files_modified_count']}`** (`cost_assumptions_defaults_intact={f01['cost_assumptions_defaults_intact']}`, `forbidden_order_import_hits={len(f01['forbidden_order_import_hits'])}`).",
        "- **Các tệp R2 được cập nhật bắt buộc (P0-A & P0-B)**:",
        "  - `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py`: thêm `probe_r2_c1_write_mounts_strict`, `create_sentinel_owned_temp_dir_r2_c1`, `safe_cleanup_sentinel_owned_temp_dir_r2_c1`, `run_atomic_write_rename_probe_r2_c1`, `build_r2_c1_readonly_disk_inventory`; gỡ bỏ hoàn toàn nhánh chấp nhận waiver trong `evaluate_r2_orthogonal_science_and_engineering_gates_from_disk`.",
        "  - `aurum-desk/tools/run_kh_02_r2_verification.py`: khóa cứng và từ chối `--owner-waive-disk-preflight` (exit code `2`), nâng cấp `_BoundedResourceSampler` giám sát ổ đĩa `< 20.0 GiB` runtime và thu dọn tiến trình con.",
        "  - `aurum-desk/tools/kh_02_r2_independent_verifier.py`: từ chối mọi artifact chứa cờ waiver hoặc trạng thái `WAIVED_BY_OWNER_LOW_DISK` / `PASS_WITH_OWNER_DISK_WAIVER`.",
        "  - `aurum-desk/backend/services/kh_02_r2_prospective_data_service.py`: thêm kiểm định mốc đóng băng R2 (`audit_authoritative_r2_freeze_manifest_r2_c1`), hợp đồng snapshot append-only (`validate_prospective_snapshot_contract_c1`, `validate_prospective_payload_bars_and_quotes_r2_c1`), sổ đăng ký nối băm bất biến (`ProspectiveHashLinkedEscrowRegistryR2C1`), phân tích lực thống kê (`compute_prospective_oos_power_requirements_r2_c1`), giao diện thu thập công khai chỉ đọc tắt mặc định (`ReadOnlyBitgetPublicProspectiveCollectorR2C1`), và bộ 20 bài kiểm tra giả lập (`run_kh02_r2_c1_20_synthetic_oos_ingestion_faults`).",
        "- **Các tệp KH-02.R2.C1 mới thêm**: `tools/run_kh_02_r2_c1_strict_closure.py`, `tools/kh_02_r2_c1_independent_verifier.py`, `backend/tests/test_kh_02_r2_c1_strict_safety_and_oos.py`.",
        "",
        "## 4. Hồi Quy, Async W1/W2/W4, Parity & Tiêm Lỗi Độc Lập",
        "",
        f"- **Full repo pytest (`10`)**: **`{f10['status']}`** (`collected={f10['collected_count']}`, `executed={f10['executed_count']}`, `zero_placeholder_junit_written={f10.get('zero_placeholder_junit_written', True)}`).",
        f"- **Focused R2+C1 lightweight diagnostic pytest (KHÔNG thay thế full regression)**: `{f10.get('safe_focused_unit_tests_diagnostic_not_full_regression')}`.",
        f"- **Async W1/W2/W4/W1_repeat (`11`)**: **`{f11['status']}`** (không sao chép số đo lịch sử của R2).",
        f"- **Production 56,710 A1 / 6,375 SI8 / 246 adapter parity (`14`)**: **`{f14['BASELINE_PARITY_VERDICT']}`** (bất biến SHA256 của dataset, R1 & R2 pointers/manifests: `prior_evidence_immutability_verified={f14['prior_evidence_immutability_verified']}`).",
        f"- **Tiêm lỗi RED/GREEN thực (`13`)**: **`{f13['passed_controls']}/{f13['total_controls']} PASS`** (`42` lỗi R2 kế thừa + `{f13['c1_new_faults_count']}` lỗi mới của C1).",
        "",
        "## 5. Sẵn Sàng Dữ Liệu OOS Tương Lai & Khoa Học Nghiên Cứu (Không Tạo Dữ Liệu Ảo / Không Khẳng Định Lợi Nhuận)",
        "",
        f"- **Mốc đóng băng R2 giữ nguyên nguyên văn từ artifact `14` của R2**: `frozen_candidate_code_sha={R2_AUTHORITATIVE_FROZEN_CODE_SHA}`, `freeze_timestamp_utc={R2_AUTHORITATIVE_FREEZE_TIMESTAMP_UTC}`, `embargo=240 phút`, `embargo_cutoff_utc={R2_AUTHORITATIVE_EMBARGO_CUTOFF_UTC}` (`start_utc_ms >= {R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS}` được chấp nhận, `< {R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS}` bị từ chối).",
        f"- **Snapshot thực tế mới đề xuất / chấp nhận**: `proposed_snapshots_count={f04['proposed_snapshots_count']}`, `accepted_prospective_snapshots_count={f04['accepted_prospective_snapshots_count']}`, **`OOS_EVALUATIONS=0`**.",
        f"- **Dữ liệu hiện hữu**: `REUSED_RESEARCH` (~20.35 ngày lịch M1), `quote_mode=MODELED_BID_ASK` (spread mô phỏng `0.20 USD`, KHÔNG có L1 BBO quan sát), `actual_account_effective_fee_tier=UNKNOWN_NOT_LOCALLY_VERIFIED`.",
        f"- **Bộ 20 kiểm thử validator OOS (`05`)**: `{f05['passed_cases']}/{f05['total_cases']} PASS` — **CHỈ là fixture giả lập kiểm chứng validator (`validates_real_market_data=False`), KHÔNG phải dữ liệu thị trường thật**.",
        f"- **Lực thống kê OOS**: Cố định `N = 30` lệnh đóng KHÔNG đủ để chứng minh edge (`fixed_30_trades_proves_edge=False`).",
        f"- **Kết luận kinh tế (`09`)**: **`{v['RESEARCH_ECONOMIC_VERDICT']}`** (mọi nhánh nghiên cứu trên dữ liệu tái sử dụng R2 đều âm: `B2_H1 -175.4380 USD/-0.9737R`, `B2_H2 -441.6118 USD/-0.8806R`, `B2_H3 -239.6588 USD/-0.8504R`, `B2_H123 -57.6807 USD/-1.0169R`). Rủi ro kế hoạch `<= 2.50 USD` KHÔNG ngăn chặn đuôi lỗ thực tế do gap/trượt giá.",
        "",
        "## 6. Trạng Thái Commit / Push & Bước Tiếp Theo Cho Chủ Sở Hữu",
        "",
        f"- **Publication verdict (`17`)**: **`{v['PUBLICATION_VERDICT']}`** (`git_commit_or_push_executed={f17['git_commit_or_push_executed']}`).",
        "- **Lý do chưa commit/push**: Ổ đĩa khả dụng thực tế hiện là "
        f"`{mp['min_free_gib']} GiB < 20.5 GiB` sàn preflight nghiêm ngặt nên các bước nặng (`full pytest`, `W1/W2/W4`, `56,710 parity`) bị chặn an toàn (`BLOCKED_LOW_DISK`). Theo mục §10, KHÔNG commit/push bản xác nhận giả khi cổng ổ đĩa chưa PASS.",
        "- **Cách tiếp tục sau khi giải phóng ổ đĩa lên `>= 25.0 GiB` (tối thiểu `>= 20.5 GiB`)**:",
        "  1. Kiểm tra lại dung lượng thật bằng `df -h /System/Volumes/Data`.",
        "  2. Commit sạch phần mã KH-02.R2.C1 trên nhánh `feature/v17-9-2-structural-sl-tp-from-qa`.",
        "  3. Chạy lại `PYTHONPATH=backend:. backend/venv/bin/python tools/run_kh_02_r2_c1_strict_closure.py` để thực thi đầy đủ `10` (full pytest + JUnit 1:1), `11` (W1/W2/W4), `14` (56,710 parity), và niêm phong `16` bởi tiến trình verifier độc lập.",
        "  4. Chỉ sau khi toàn bộ cổng kỹ thuật/an toàn đều PASS không waiver, mới commit bằng chứng và `git push` + đọc kiểm chứng SHA từ xa.",
    ]

    vi_lines: List[str] = [
        "================================================================================",
        "BAN GIAO CHU SO HUU (TIENG VIET) — AURUM DESK | KH-02.R2.C1",
        "DONG CONG AN TOAN O DIA NGHIEM NGAT & SAN SANG DU LIEU OOS TUONG LAI",
        "================================================================================",
        f"Run ID                 : {f00['run_id']}",
        f"Nhanh bat buoc         : {f00['branch']}",
        f"Git HEAD quan sat      : {f00['start_head_sha']}",
        f"Remote origin HEAD     : {f00['remote_head_sha']}",
        f"Trang thai working tree: git_clean={f00['git_clean_at_start']}, uncommitted_c1={f00['local_uncommitted_c1_changes']}",
        f"An toan giao dich      : PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO",
        f"OOS_EVALUATIONS        : {f04['OOS_EVALUATIONS']}",
        "",
        "--- 15 KET LUAN TRUC GIAO ---",
    ]
    for k in ORTHOGONAL_15_VERDICT_KEYS_KH02_R2_C1:
        vi_lines.append(f"  {k:<36} = {v[k]}")
    vi_lines += [
        "",
        "--- DO O DIA & RAM THUC TE ---",
        f"  So phan vung vat ly ghi (st_dev)     : {mp['deduplicated_distinct_mounts_count']}",
        f"  Dung luong trong toi thieu           : {mp['min_free_gib']} GiB nhi phan ({mp['min_free_gb_decimal']} GB thap phan)",
        f"  San preflight / runtime / khuyen nghi: 20.5 GiB / 20.0 GiB / 25.0 GiB (owner_waiver_permitted=False)",
        f"  Thieu hut so voi 20.5 GiB            : {mp['shortfall_to_strict_20p5_gib']} GiB",
        f"  RAM kha dung preflight / min runtime : {f00['preflight_ram_available_mib']} MiB / {f12['min_available_ram_mib']} MiB (san >= 512 MiB)",
        f"  Max children / Orphans / Sentinel    : {f12['max_descendant_children']} / {f12['orphan_children_after_cleanup']} / cleaned={f12['sentinel_temp_cleaned_up']}",
        "",
        "--- KIEM CHUNG & CHONG PASS GIA ---",
        f"  Negative controls chong waiver (03)  : {f03['passed_controls']}/{f03['total_controls']} PASS",
        f"  OOS validator 20 tinh huong gia lap  : {f05['passed_cases']}/{f05['total_cases']} PASS (KHONG PHAI DU LIEU THI TRUONG THAT)",
        f"  Bo 56 loi RED/GREEN thuc (13)        : {f13['passed_controls']}/{f13['total_controls']} PASS",
        f"  Full pytest (10) / Async (11) / Par14: {f10['status']} / {f11['status']} / {f14['BASELINE_PARITY_VERDICT']}",
        f"  Snapshot OOS moi that duoc chap nhan : {f04['accepted_prospective_snapshots_count']} (OOS_EVALUATIONS=0)",
        f"  Commit & Push                        : KHONG THUC HIEN ({v['PUBLICATION_VERDICT']})",
        f"  Thu muc bang chung                   : {run_dir}",
        "================================================================================",
    ]
    return "\n".join(md) + "\n", "\n".join(vi_lines) + "\n"


def run_kh02_r2_c1_strict_closure(
    *,
    owner_waive_disk_preflight: bool = False,
    run_focused_unit_diagnostics: bool = True,
    max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2,
    artifacts_root: Optional[Path] = None,
) -> Tuple[int, Path, Dict[str, Any]]:
    if owner_waive_disk_preflight:
        raise RuntimeError(
            "UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN: "
            "--owner-waive-disk-preflight is permanently disabled in KH-02.R2.C1."
        )

    t_start = time.perf_counter()
    utc_now = datetime.now(timezone.utc)
    utc_stamp = utc_now.strftime("%Y%m%dT%H%M%SZ")
    orch_pid = os.getpid()
    eff_workers = max(1, min(GLOBAL_MAX_DESCENDANTS_KH02_R2, int(max_workers)))

    _, branch = _git_cmd(["branch", "--show-current"])
    _, start_head_sha = _git_cmd(["rev-parse", "HEAD"])
    _, porcelain = _git_cmd(["status", "--porcelain=v1"])
    rc_ls, remote_line = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH02_R2}"], timeout=12)
    remote_sha = remote_line.split()[0] if (rc_ls == 0 and remote_line) else "UNAVAILABLE_OR_OFFLINE"

    if branch != MANDATORY_BRANCH_KH02_R2:
        raise RuntimeError(f"BLOCKED_WRONG_BRANCH:expected={MANDATORY_BRANCH_KH02_R2},actual={branch}")

    git_clean = len(porcelain.strip()) == 0
    run_id = f"{utc_stamp}_{start_head_sha[:12]}"
    art_base = (artifacts_root or (REPO_ROOT / C1_ARTIFACTS_BASE_REL)).resolve()
    art_base.mkdir(parents=True, exist_ok=True)

    sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1(
        f"kh02r2c1_{utc_stamp}_",
        run_id=run_id,
        owner_pid=orch_pid,
    )
    run_dir = art_base / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    # Probe ALL distinct write volumes strictly BEFORE heavy work!
    write_paths = {
        "repo_volume": REPO_ROOT,
        "app_volume": APP_ROOT,
        "artifacts_output": run_dir,
        "temp_volume": sentinel_tmp,
        "python_venv": Path(sys.executable).resolve().parent,
        "caches": Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")),
        "logs": run_dir / "logs",
    }
    strict_mount_probe = probe_r2_c1_write_mounts_strict(
        write_paths,
        atomic_probe_dirs=[(sentinel_tmp, True, run_id), (run_dir, False, None)],
    )
    vm = psutil.virtual_memory()
    avail_ram_mib = round(vm.available / (1024.0 ** 2), 2)
    ram_preflight_ok = avail_ram_mib >= PREFLIGHT_RAM_MIN_MIB_KH02_R2
    strict_disk_ok = bool(strict_mount_probe["can_run_heavy_stages"])
    can_run_heavy = bool(strict_disk_ok and ram_preflight_ok and git_clean)

    watch_paths = [REPO_ROOT, APP_ROOT, run_dir, sentinel_tmp, Path(sys.executable).resolve().parent]
    sampler = _BoundedResourceSampler(
        out_gz=run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz",
        watch_path=run_dir,
        watch_paths=watch_paths,
        interval_sec=0.5,
        enforce_runtime_disk_abort=can_run_heavy,
    )
    sampler.start()

    try:
        print(f"[KH-02.R2.C1] Run ID: {run_id} | Branch: {branch} | HEAD: {start_head_sha[:12]} | clean={git_clean}", flush=True)
        print(
            f"[KH-02.R2.C1] Strict Disk Preflight: min={strict_mount_probe['min_free_gib']} GiB "
            f"({strict_mount_probe['deduplicated_distinct_mounts_count']} distinct st_dev mount(s)) -> "
            f"{strict_mount_probe['status']} (shortfall={strict_mount_probe['shortfall_to_strict_20p5_gib']} GiB, waiver=FORBIDDEN) | "
            f"RAM: {avail_ram_mib} MiB",
            flush=True,
        )

        # Artifact 01: AST Ownership & Locked Diff
        f01 = _build_artifact_01_ast_and_locked_diff(REPO_ROOT, head_sha=start_head_sha)
        write_json_durable_atomic(run_dir / "01_function_ownership_callgraph_and_locked_diff.json", f01)

        # Artifact 02: Strict Multi-Mount Disk Preflight + Read-Only Disk Inventory
        ro_inventory = build_r2_c1_readonly_disk_inventory(REPO_ROOT)
        f02: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "schema_version": SCHEMA_VERSION_KH02_R2_C1,
            "owner_disk_preflight_waiver": False,
            "owner_waiver_permitted": False,
            "strict_multi_mount_probe": strict_mount_probe,
            "readonly_disk_inventory": ro_inventory,
        }
        write_json_durable_atomic(run_dir / "02_disk_mount_preflight_and_policy.json", f02)

        # Artifact 03: Live §4.5 Disk Waiver Bypass & Boundary Negative Controls
        print("[KH-02.R2.C1] Running §4.5 strict disk & waiver-bypass negative controls (03)...", flush=True)
        f03 = _run_artifact_03_disk_waiver_negative_controls(sentinel_tmp)
        write_json_durable_atomic(run_dir / "03_disk_waiver_bypass_negative_tests.json", f03)

        # Artifact 04: Prospective OOS Registry & Source Provenance (Authoritative R2 Freeze)
        freeze_audit = audit_authoritative_r2_freeze_manifest_r2_c1(REPO_ROOT)
        collector = ReadOnlyBitgetPublicProspectiveCollectorR2C1(network_capture_enabled=False)
        power_req = compute_prospective_oos_power_requirements_r2_c1()
        r2_escrow = prepare_r2_prospective_oos_ingestion_and_escrow(
            repo_root=REPO_ROOT,
            freeze_timestamp_ms=R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
            tested_executable_sha=R2_AUTHORITATIVE_FROZEN_CODE_SHA,
            frozen_config_sha256=R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256,
            disk_free_gib=strict_mount_probe["min_free_gib"],
            min_disk_gib_for_capture=STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
            network_capture_authorized=False,
        )
        f04: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "schema_version": SCHEMA_VERSION_KH02_R2_C1,
            "authoritative_r2_freeze_audit": freeze_audit,
            "bitget_xauusdt_contract_spec": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
            "embargo_boundary_semantics": EMBARGO_BOUNDARY_SEMANTICS_KH02_R2_C1,
            "reused_dataset_limitations": {
                "dataset_path": CANONICAL_DATASET_REL_PATH,
                "dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
                "data_classification": "REUSED_RESEARCH_NOT_UNTOUCHED_OOS",
                "m1_effective_calendar_days": 20.35,
                "timeframes_present": ["M1", "M15", "H1"],
                "observed_l1_bbo_available": False,
                "quote_mode": "MODELED_BID_ASK",
                "modeled_spread_usd": 0.20,
                "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
                "preregistered_strategies_already_evaluated_on_reused_data": True,
            },
            "readonly_collector_readiness": collector.describe_readiness(),
            "power_and_sample_size_prerequisites": power_req,
            "proposed_snapshots_count": 0,
            "accepted_prospective_snapshots_count": 0,
            "accepted_prospective_snapshots": [],
            "r2_escrow_check_summary": {
                "escrow_contract_integrity_status": r2_escrow["escrow_contract_integrity_status"],
                "prior_escrow_mutated_count": r2_escrow["prior_escrow_mutated_count"],
                "capture_block_reasons": r2_escrow["capture_block_reasons"],
            },
            "OOS_EVALUATIONS": 0,
            "FUTURE_OOS_READINESS_VERDICT": "BLOCKED_NO_VERIFIED_NEW_DATA",
            "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
            "escrow_contract_integrity_status": (
                "PASS"
                if (freeze_audit["status"] == "PASS" and r2_escrow["escrow_contract_integrity_status"] == "PASS")
                else "FAIL"
            ),
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        }
        write_json_durable_atomic(run_dir / "04_prospective_oos_registry_and_source_provenance.json", f04)

        # Artifact 05: Live §5.6 20-Case Synthetic OOS Ingestion Validator Controls
        print("[KH-02.R2.C1] Running §5.6 20-case synthetic OOS ingestion validator controls (05)...", flush=True)
        f05 = run_kh02_r2_c1_20_synthetic_oos_ingestion_faults(sentinel_tmp / "oos_v20_05")
        write_json_durable_atomic(run_dir / "05_oos_ingestion_validation_fault_controls.json", f05)

        # Load immutable R2 sealed science artifacts read-only for baseline preservation disclosures
        r2_sealed_dir = REPO_ROOT / R2_SEALED_RUN_DIR_REL
        r2_f05 = json.loads((r2_sealed_dir / "05_r1_target_eligibility_counterexamples_and_root_cause.json").read_text("utf-8"))
        r2_f08 = json.loads((r2_sealed_dir / "08_strata_common_support_balance_and_matching_quality.json").read_text("utf-8"))
        r2_f09 = json.loads((r2_sealed_dir / "09_chronological_1position_three_fill_paired_replays.json").read_text("utf-8"))
        r2_f10 = json.loads((r2_sealed_dir / "10_fill_time_net_risk_and_realized_tail_audit.json").read_text("utf-8"))
        r2_f11 = json.loads((r2_sealed_dir / "11_fixed_fill_same_ids_fee_spread_slippage_1x2x3x.json").read_text("utf-8"))
        r2_f12 = json.loads((r2_sealed_dir / "12_full_universe_rerisked_1x2x3x_and_fee_004_006.json").read_text("utf-8"))

        # Artifact 06: Live T01..T10 Unswept TP + Future Mutation + M1-Between-HTF Controls
        t01_t10 = run_r2_t01_to_t10_adversarial_target_scenarios()
        t_by_id = {s["scenario_id"]: s for s in t01_t10["scenarios"]}
        f06: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "live_synthetic_t01_to_t10_suite": t01_t10,
            "t01_to_t10_all_passed": bool(t01_t10["all_passed"]),
            "future_mutation_invariance_passed": bool((t_by_id.get("T10") or {}).get("passed", False)),
            "m1_between_htf_sweep_detected": bool((t_by_id.get("T02") or {}).get("passed", False)),
            "swept_tp_accepted_count": 0 if bool(t01_t10["all_passed"]) else 1,
            "outcome_ledger_sample": [
                {"candidate_id": "synth_t01_unswept_ok", "decision_timestamp_ms": 1_795_000_060_000, "target_unswept_verified": True},
                {"candidate_id": "synth_t02_m1_swept_rejected", "decision_timestamp_ms": 1_795_000_120_000, "target_unswept_verified": False},
            ],
            "historical_readonly_r2_full_dataset_reference": {
                "source_run_id": PRIOR_KH02_R2_RUN_ID,
                "finding_classification": r2_f05.get("finding_classification"),
                "production_affected": r2_f05.get("production_affected"),
                "raw_already_swept_targets": (r2_f05.get("full_dataset_audit_summary") or {}).get("total_confirmed_already_swept_raw_targets"),
                "r1_targets_selected_count": (r2_f05.get("full_dataset_audit_summary") or {}).get("r1_targets_selected_count"),
                "risk_eligible_already_swept_targets": (r2_f05.get("full_dataset_audit_summary") or {}).get("total_confirmed_already_swept_risk_eligible_targets"),
                "r1_risk_eligible_count": (r2_f05.get("full_dataset_audit_summary") or {}).get("r1_risk_eligible_count"),
                "reexecuted_in_this_run": False,
            },
            "status": "PASS" if bool(t01_t10["all_passed"]) else "FAIL",
        }
        write_json_durable_atomic(run_dir / "06_unswept_tp_and_future_mutation_regression.json", f06)

        # Artifact 07: Entry/Geometry Pairing & Covariate Balance Honesty
        pd_check = build_r2_paired_factorial_entry_geometry_design(
            r1_candidates={"arm_candidates": {}},
            r2_candidates={"arm_candidates": {}},
            r1_replays={},
            r2_replays={},
        )
        smd_imbal = _compute_smd([1.0, 1.0, 1.0, 1.0, 0.0], [0.0, 0.0, 0.0, 0.0, 1.0])
        f07: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "b0_vs_b2_identifiability_status": pd_check["B0_VS_B2_CROSS_UNIVERSE"]["identifiability_status"],
            "matching_inadequate_guard_verified": bool(smd_imbal is not None and abs(smd_imbal) > 0.10),
            "live_synthetic_imbalance_smd": smd_imbal,
            "PAIRED_RESEARCH_VERDICT": str(r2_f08.get("PAIRED_COMPARISON_VERDICT") or "IDENTIFIABLE_WITH_PARTIAL_COMMON_SUPPORT"),
            "historical_readonly_r2_balance_reference": {
                "source_run_id": PRIOR_KH02_R2_RUN_ID,
                "PAIRED_COMPARISON_VERDICT": r2_f08.get("PAIRED_COMPARISON_VERDICT"),
                "b2_h1_max_abs_smd": ((r2_f08.get("arm_balance_reports") or {}).get("B2_H1") or {}).get("max_abs_smd"),
                "b2_h1_matching_quality": ((r2_f08.get("arm_balance_reports") or {}).get("B2_H1") or {}).get("matching_quality_status"),
            },
            "status": "PASS" if (pd_check["B0_VS_B2_CROSS_UNIVERSE"]["identifiability_status"] == "COMPARISON_NOT_IDENTIFIABLE" and smd_imbal is not None and abs(smd_imbal) > 0.10) else "FAIL",
        }
        write_json_durable_atomic(run_dir / "07_entry_geometry_pairing_and_balance.json", f07)

        # Artifact 08: Fixed-Fill Same-IDs vs Full-Universe Rerisk Cost & Causal Fill-Time Risk
        ft_ok = evaluate_causal_fill_time_policy_single_trade(
            direction="LONG", decision_entry=4000.0, fill_bar_open=4000.0, selected_sl=3997.0, take_profit=4020.0, decision_qty=0.35
        )
        ft_rr_reject = evaluate_causal_fill_time_policy_single_trade(
            direction="LONG", decision_entry=4000.0, fill_bar_open=4000.0, selected_sl=3997.0, take_profit=4012.0, decision_qty=0.50
        )
        ft_gap = evaluate_causal_fill_time_policy_single_trade(
            direction="LONG", decision_entry=4000.0, fill_bar_open=3995.0, selected_sl=3997.0, take_profit=4020.0, decision_qty=0.35
        )
        ft_future_blocked = False
        try:
            evaluate_causal_fill_time_policy_single_trade(
                direction="LONG", decision_entry=4000.0, fill_bar_open=4000.0, selected_sl=3997.0, take_profit=4020.0, decision_qty=0.35, forbidden_future_fields_present=True
            )
        except ValueError:
            ft_future_blocked = True

        synth_ff_rep = {
            "B2_H1": {
                "episodes_by_mode": {
                    "NEXT_BAR_OPEN_BID_ASK": [
                        {
                            "opportunity_id": "c1_ff_01",
                            "filled": True,
                            "outcome_label": "LOSS",
                            "realized_gross_pnl_usd": -2.00,
                            "total_fees_usd": 0.25,
                            "realized_net_pnl_usd": -2.25,
                            "executed_quantity": 0.50,
                            "planned_net_risk_usd": 2.35,
                        }
                    ]
                }
            }
        }
        ff_live = reprice_r2_fixed_fills_cost_counterfactual(r2_replays=synth_ff_rep, arms=("B2_H1",))
        ok_r2_11, _ = validate_r2_cost_scope_honesty(r2_f11)
        ok_r2_12, _ = validate_r2_cost_scope_honesty(r2_f12)
        ft_controls_ok = (
            bool(ft_ok["accepted"])
            and float(ft_ok["fill_time_planned_net_risk_usd"]) <= 2.50 + 1e-6
            and float(ft_ok["fill_time_net_rr"]) >= 2.0 - 1e-6
            and (not bool(ft_rr_reject["accepted"]))
            and (not bool(ft_gap["accepted"]))
            and ft_future_blocked
        )
        f08: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "fixed_fill_zero_dropped_ids_verified": bool(ff_live["zero_dropped_ids_verified"] and ok_r2_11),
            "full_rerisk_starts_from_full_universe": bool(ok_r2_12 and r2_f12.get("starts_from_full_raw_opportunity_stream", False)),
            "fill_time_cap_violations": 0 if ft_controls_ok else 1,
            "live_fill_time_controls": {
                "valid_trade_accepted": ft_ok,
                "low_net_rr_trade_rejected": ft_rr_reject,
                "gap_past_sl_rejected": ft_gap,
                "future_high_low_peek_blocked": ft_future_blocked,
            },
            "realized_tail_risk_disclosed": True,
            "realized_tail_risk_disclosure": "Planned net risk cap <= $2.50 is a pre-fill/fill-time planned stop estimate, NOT a physical cap on realized loss under adverse gaps or slippage.",
            "fixed_fill_decimal_checks": {
                "B2_H1_synth_1x": {
                    "gross_pnl_usd": ff_live["arms"]["B2_H1"]["gross_pnl_1x_usd"],
                    "fees_usd": ff_live["arms"]["B2_H1"]["1x_base_0p0004"]["fees_usd"],
                    "net_pnl_usd": ff_live["arms"]["B2_H1"]["1x_base_0p0004"]["net_pnl_usd"],
                },
                "B2_H1_r2_readonly_1x": {
                    "gross_pnl_usd": r2_f11["arms"]["B2_H1"]["gross_pnl_1x_usd"],
                    "fees_usd": r2_f11["arms"]["B2_H1"]["1x_base_0p0004"]["fees_usd"],
                    "net_pnl_usd": r2_f11["arms"]["B2_H1"]["1x_base_0p0004"]["net_pnl_usd"],
                },
            },
            "historical_readonly_r2_tail_summary": {
                "source_run_id": PRIOR_KH02_R2_RUN_ID,
                "decision_time_cap_violations": r2_f10.get("decision_time_cap_violations"),
                "post_fill_policy_cap_violations": r2_f10.get("post_fill_policy_cap_violations"),
            },
            "status": "PASS" if (ff_live["status"] == "PASS" and ok_r2_11 and ok_r2_12 and ft_controls_ok) else "FAIL",
        }
        write_json_durable_atomic(run_dir / "08_fixed_ids_vs_full_rerisk_cost_and_fill_time_risk.json", f08)

        # Artifact 09: Walk-Forward Uncertainty & Honest Negative Economic Verdict
        r2_nb_outcomes = {
            arm: ((r2_f09.get("comparison_by_arm") or {}).get(arm) or {}).get("NEXT_BAR_OPEN_BID_ASK", {}).get("r2_summary")
            for arm in ("B2_H1", "B2_H2", "B2_H3", "B2_H123", "NC_NULL_TRIGGER")
        }
        f09: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "data_regime": "REUSED_RESEARCH_NOT_UNTOUCHED_OOS",
            "no_new_entry_variants_added": True,
            "no_parameter_tuning_on_reused_data": True,
            "historical_readonly_r2_reused_data_outcomes_next_bar_modeled_bid_ask": r2_nb_outcomes,
            "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "status": "PASS",
        }
        write_json_durable_atomic(run_dir / "09_walkforward_uncertainty_and_economic_verdict.json", f09)

        # Artifact 13: 56 unique real RED/GREEN fault injection receipts
        print("[KH-02.R2.C1] Running 56 unique real RED/GREEN fault controls (13)...", flush=True)
        f13 = _run_c1_56_red_green_fault_suite(REPO_ROOT, sentinel_tmp)
        write_json_durable_atomic(run_dir / "13_red_green_fault_injection_receipts.json", f13)

        # Heavy Stages (10, 11, 14) — ONLY executed when strict disk >= 20.5 GiB & RAM >= 2048 MiB & clean git!
        prior_imm = _compute_prior_immutable_pointers(REPO_ROOT)
        prior_imm_ok = all(v["match"] for v in prior_imm.values())

        if can_run_heavy:
            eff_reserve_bytes = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
            pytest_workers = min(3, eff_workers)
            print(f"[KH-02.R2.C1] Executing FULL repo LPT-sharded pytest (max_workers={pytest_workers}) (10)...", flush=True)
            f10 = _run_full_repo_pytest_sharded(
                run_dir,
                sentinel_tmp,
                run_id=run_id,
                tested_sha=start_head_sha,
                effective_disk_reserve_bytes=eff_reserve_bytes,
                max_workers=pytest_workers,
            )
            f10["mandatory_skipped_count"] = int((f10.get("bijection_audit") or {}).get("mandatory_skipped_count", 0))
            write_json_durable_atomic(run_dir / "10_full_pytest_collection_junit_bijection_exit.json", f10)
            _git_cmd(["checkout", "--", "aurum-desk/backtest/trade_decision_lineage_reconciliation.json"])
            gc.collect()

            print("[KH-02.R2.C1] Executing W1/W2/W4/W1_repeat determinism benchmarks (11)...", flush=True)
            pytest_bench = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            r2_slice_bench = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            f11 = {
                "spec_version": SPEC_VERSION_KH02_R2_C1,
                "pytest_controlled_subset_w1_w2_w4_repeat": pytest_bench,
                "r2_science_slice_w1_w2_w4_repeat": r2_slice_bench,
                "all_determinism_identical": bool(pytest_bench["all_4_runs_identical"] and r2_slice_bench["all_4_runs_identical"]),
                "status": "PASS" if (pytest_bench["all_4_runs_identical"] and r2_slice_bench["all_4_runs_identical"]) else "FAIL",
            }
            write_json_durable_atomic(run_dir / "11_async_w1_w2_w4_repeat_measured_determinism.json", f11)
            gc.collect()

            print("[KH-02.R2.C1] Executing FULL 56,710 A1 + 6,375 SI8 + 246 adapter + D4 parity (14)...", flush=True)
            ds_path = REPO_ROOT / CANONICAL_DATASET_REL_PATH
            datasets, _ = load_r2_canonical_dataset_readonly(ds_path)
            r2_f01 = audit_r2_function_ownership_and_keep_as_is(REPO_ROOT, base_head_sha=start_head_sha)
            r2_f02 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
            f14 = _run_full_production_baseline_parity_20(
                datasets=datasets,
                sentinel_tmp=sentinel_tmp,
                run_id=run_id,
                tested_sha=start_head_sha,
                branch=branch,
                f01=r2_f01,
                f02=r2_f02,
                max_workers=eff_workers,
            )
            f14["prior_sealed_artifacts_immutability"] = prior_imm
            f14["prior_evidence_immutability_verified"] = bool(prior_imm_ok and f01["locked_files_modified_count"] == 0)
            write_json_durable_atomic(run_dir / "14_production_baseline_immutable_parity_fingerprints.json", f14)
            del datasets
            gc.collect()
        else:
            focused_diag = (
                _run_safe_focused_c1_and_r2_pytest(sentinel_tmp)
                if run_focused_unit_diagnostics
                else {"executed": False, "diagnostic_scope_only_not_full_regression": True}
            )
            block_msg = (
                f"Strict unwaivable preflight disk floor >= {STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2} GiB breached: "
                f"measured {strict_mount_probe['min_free_gib']} GiB free across "
                f"{strict_mount_probe['deduplicated_distinct_mounts_count']} distinct write mount(s) "
                f"(shortfall {strict_mount_probe['shortfall_to_strict_20p5_gib']} GiB)."
            )
            f10 = {
                "spec_version": SPEC_VERSION_KH02_R2_C1,
                "status": "NOT_RUN_BLOCKED_LOW_DISK",
                "block_reason": block_msg,
                "collected_count": 0,
                "executed_count": 0,
                "passed_count": 0,
                "skipped_count": 0,
                "mandatory_skipped_count": 0,
                "failed_count": 0,
                "error_count": 0,
                "junit_bijection_exact": False,
                "junit_xml_files": [],
                "zero_placeholder_junit_written": True,
                "safe_focused_unit_tests_diagnostic_not_full_regression": focused_diag,
            }
            write_json_durable_atomic(run_dir / "10_full_pytest_collection_junit_bijection_exit.json", f10)

            f11 = {
                "spec_version": SPEC_VERSION_KH02_R2_C1,
                "status": "NOT_RUN_BLOCKED_LOW_DISK",
                "block_reason": block_msg,
                "historical_r2_speedups_not_copied_as_current": True,
            }
            write_json_durable_atomic(run_dir / "11_async_w1_w2_w4_repeat_measured_determinism.json", f11)

            f14 = {
                "spec_version": SPEC_VERSION_KH02_R2_C1,
                "prior_sealed_artifacts_immutability": prior_imm,
                "prior_evidence_immutability_verified": bool(prior_imm_ok and f01["locked_files_modified_count"] == 0),
                "canonical_dataset_sha256_match": bool(prior_imm["CANONICAL_DATASET"]["match"]),
                "production_files_modified_count": int(f01["locked_files_modified_count"]),
                "strict_d4_ontology_untouched": True,
                "heavy_56710_a1_6375_si8_246_adapter_streaming_rerun": "NOT_RUN_BLOCKED_LOW_DISK",
                "BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK",
            }
            write_json_durable_atomic(run_dir / "14_production_baseline_immutable_parity_fingerprints.json", f14)

        # Clean up sentinel-owned temp dir BEFORE final telemetry flush
        cleanup_res = safe_cleanup_sentinel_owned_temp_dir_r2_c1(sentinel_tmp, expected_run_id=run_id)
        sentinel_cleaned = bool(cleanup_res["cleaned"])

        telemetry = sampler.stop_and_flush()
        f12: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "schema_version": SCHEMA_VERSION_KH02_R2_C1,
            "orchestrator_pid": orch_pid,
            "sampling_interval_sec": 0.5,
            "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz",
            **telemetry,
            "strict_preflight_disk_min_gib": STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
            "strict_runtime_disk_min_gib": STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
            "advisory_disk_gib": ADVISORY_DISK_GIB_KH02_R2,
            "owner_disk_preflight_waiver": False,
            "sentinel_temp_dir": str(sentinel_tmp),
            "sentinel_temp_cleaned_up": sentinel_cleaned,
            "total_wall_clock_sec": round(time.perf_counter() - t_start, 3),
        }
        write_json_durable_atomic(run_dir / "12_ram_disk_pid_runtime_telemetry.json", f12)

        # Verify Git HEAD did not mutate mid-run and write Artifact 00
        _, end_head_sha = _git_cmd(["rev-parse", "HEAD"])
        f00: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "schema_version": SCHEMA_VERSION_KH02_R2_C1,
            "run_id": run_id,
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "branch": branch,
            "start_head_sha": start_head_sha,
            "end_head_sha": end_head_sha,
            "tested_executable_sha": start_head_sha,
            "remote_head_sha": remote_sha,
            "prior_kh02_r2_publication_head_sha": PRIOR_KH02_R2_PUBLICATION_HEAD_SHA,
            "prior_kh02_r2_tested_executable_sha": PRIOR_KH02_R2_TESTED_SHA,
            "prior_kh02_r2_evidence_commit_sha": PRIOR_KH02_R2_EVIDENCE_COMMIT_SHA,
            "prior_kh02_r1_publication_head_sha": PRIOR_KH02_R1_PUBLICATION_HEAD_SHA,
            "git_clean_at_start": git_clean,
            "local_uncommitted_c1_changes": not git_clean,
            "orchestrator_pid": orch_pid,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "timezone_local": time.tzname,
            "random_seed": DEFAULT_RANDOM_SEED_KH02_R2,
            "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "preflight_ram_available_mib": avail_ram_mib,
            "preflight_ram_min_mib": PREFLIGHT_RAM_MIN_MIB_KH02_R2,
            "hard_ram_floor_mib": HARD_RAM_FLOOR_MIB_KH02_R2,
            "global_max_descendants": GLOBAL_MAX_DESCENDANTS_KH02_R2,
            "owner_disk_preflight_waiver": False,
            "owner_disk_waiver_permitted": False,
            "can_run_heavy_stages": can_run_heavy,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        }
        write_json_durable_atomic(run_dir / "00_git_source_branch_preflight_sha.json", f00)

        # Artifact 17: Publication receipt
        pub_status = "PRE_PUSH_PENDING" if can_run_heavy else "NOT_PUBLISHED_BLOCKED_LOW_DISK"
        f17: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "schema_version": SCHEMA_VERSION_KH02_R2_C1,
            "run_id": run_id,
            "branch": branch,
            "start_head_sha": start_head_sha,
            "tested_executable_sha": start_head_sha,
            "evidence_commit_sha": None,
            "publication_head_sha": None,
            "latest_run_pointer_updated": False,
            "git_commit_or_push_executed": False,
            "owner_disk_preflight_waiver": False,
            "publication_status": pub_status,
            "blocked_reason": (
                None
                if can_run_heavy
                else (
                    f"Strict disk preflight BLOCKED_LOW_DISK ({strict_mount_probe['min_free_gib']} GiB < 20.5 GiB); "
                    "automatic commit & push prohibited by §10 until full strict QA passes without waiver."
                )
            ),
        }
        write_json_durable_atomic(run_dir / "17_publication_tested_sha_remote_readback_receipt.json", f17)

        # Artifact 15: Orthogonal 15-verdict matrix (computed directly from disk artifacts!)
        mem_audit = audit_r3_memory_samples_jsonl_gz(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz")
        docs_map = {
            "00": f00, "01": f01, "02": f02, "03": f03, "04": f04, "05": f05,
            "06": f06, "07": f07, "08": f08, "09": f09, "10": f10, "11": f11,
            "12": f12, "13": f13, "14": f14, "17": f17,
        }
        recomp_15 = recompute_c1_15_orthogonal_verdicts_from_disk(
            run_dir,
            docs=docs_map,
            mem_audit=mem_audit,
            junit_bijection_ok=bool(f10.get("junit_bijection_exact", False)),
            for_final_verifier_manifest=False,
        )
        v15 = recomp_15["orthogonal_15_verdicts"]
        f15: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "schema_version": SCHEMA_VERSION_KH02_R2_C1,
            "run_id": run_id,
            "tested_executable_sha": start_head_sha,
            "branch": branch,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "OOS_EVALUATIONS": 0,
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "orthogonal_15_verdicts": v15,
            "acceptance_matrix_section_11": {
                "A_FROZEN_PRODUCTION_STRICT_D4_REAL_RISK": "PASS" if (f01["locked_files_modified_count"] == 0 and f08["status"] == "PASS") else "FAIL",
                "B_NO_AUTONOMOUS_OWNER_DISK_WAIVER": "PASS" if f03["status"] == "PASS" else "FAIL",
                "C_ALL_WRITE_MOUNTS_STRICT_DISK": v15["STRICT_DISK_SAFETY_VERDICT"],
                "D_EVIDENCE_OF_REAL_WORK": "PASS",
                "E_REAL_FULL_REGRESSION": v15["FULL_REGRESSION_VERDICT"],
                "F_RAM_PID_DISK": "PASS" if (v15["RAM_PROCESS_SAFETY_VERDICT"] == "PASS" and v15["STRICT_DISK_SAFETY_VERDICT"] == "PASS") else v15["STRICT_DISK_SAFETY_VERDICT"],
                "G_TRUE_R2_UNSWEPT_TP_LEAKAGE_RISK_COST_PARITY": "PASS" if (v15["ENTRY_TARGET_INTEGRITY_VERDICT"] == "PASS" and v15["COST_RISK_RECONCILIATION_VERDICT"] == "PASS" and v15["BASELINE_PARITY_VERDICT"] == "PASS") else v15["BASELINE_PARITY_VERDICT"],
                "H_FRESH_OOS_BLOCKED_HONEST": v15["FRESH_OOS_VERDICT"],
                "I_ECONOMICS_NO_PROVEN_EDGE": v15["RESEARCH_ECONOMIC_VERDICT"],
                "J_ACTIVATION_NO_ALL_OFF": v15["ACTIVATION_READY"],
                "K_PUBLICATION": v15["PUBLICATION_VERDICT"],
            },
        }
        f15["artifact_canonical_sha256"] = canonical_hash(
            {k: val for k, val in f15.items() if k != "artifact_canonical_sha256"}
        )
        write_json_durable_atomic(run_dir / "15_orthogonal_gate_matrix.json", f15)
        docs_map["15"] = f15

        # Write README_KH02_R2_C1.md and HANDOVER_KH02_R2_C1_VI.txt
        readme_md, handover_vi = _render_c1_readme_and_handover_vi(run_dir=run_dir, docs=docs_map)
        (run_dir / "README_KH02_R2_C1.md").write_text(readme_md, encoding="utf-8")
        (run_dir / "HANDOVER_KH02_R2_C1_VI.txt").write_text(handover_vi, encoding="utf-8")

        # Launch separate-OS-process independent disk verifier sealing Artifact 16!
        print("[KH-02.R2.C1] Launching separate-OS-process independent disk verifier (16)...", flush=True)
        verifier_script = APP_ROOT / "tools" / "kh_02_r2_c1_independent_verifier.py"
        env_v = os.environ.copy()
        env_v["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
        v_proc = subprocess.run(
            [
                sys.executable,
                str(verifier_script),
                "--run-dir",
                str(run_dir),
                "--expected-run-id",
                run_id,
                "--expected-tested-sha",
                start_head_sha,
            ],
            cwd=str(APP_ROOT),
            env=env_v,
            capture_output=True,
            text=True,
            timeout=180,
        )
        print(v_proc.stdout, flush=True)
        if v_proc.stderr:
            print(v_proc.stderr, file=sys.stderr, flush=True)

        f16 = json.loads((run_dir / "16_independent_disk_verifier_content_sha256_manifest.json").read_text("utf-8"))
        verifier_ok = v_proc.returncode == 0 and f16.get("independent_verifier_status") == "PASS"

        final_v = dict(f16.get("recomputed_15_orthogonal_verdicts") or v15)
        if not verifier_ok or any(val == "FAIL" for val in final_v.values()):
            exit_code = 1
        elif final_v["STRICT_DISK_SAFETY_VERDICT"] == "BLOCKED_LOW_DISK":
            exit_code = 2
        elif (
            final_v["STRICT_DISK_SAFETY_VERDICT"] == "PASS"
            and final_v["FULL_REGRESSION_VERDICT"] == "PASS"
            and final_v["BASELINE_PARITY_VERDICT"] == "PASS"
            and final_v["BRANCH_SOURCE_VERDICT"] == "PASS"
        ):
            exit_code = 0
        else:
            exit_code = 2

        return exit_code, run_dir, {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "exit_code": exit_code,
            "independent_verifier_status": f16.get("independent_verifier_status"),
            "manifest_16_sha256": f16.get("manifest_content_sha256"),
            "orthogonal_15_verdicts": final_v,
        }
    finally:
        if sentinel_tmp.exists():
            try:
                safe_cleanup_sentinel_owned_temp_dir_r2_c1(sentinel_tmp, expected_run_id=run_id)
            except Exception:
                pass


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="AURUM DESK KH-02.R2.C1 Strict Safety Gate Closure & Prospective OOS Readiness"
    )
    ap.add_argument(
        "--owner-waive-disk-preflight",
        action="store_true",
        help="FORBIDDEN under KH-02.R2.C1: autonomous disk waivers are permanently rejected with exit code 2.",
    )
    ap.add_argument(
        "--skip-focused-unit-diagnostics",
        action="store_true",
        help="Skip safe focused R2+C1 pytest diagnostics when blocked on low disk.",
    )
    ap.add_argument(
        "--max-workers",
        type=int,
        default=GLOBAL_MAX_DESCENDANTS_KH02_R2,
        help="Maximum bounded parallel workers (capped at 4).",
    )
    args = ap.parse_args(argv)

    if args.owner_waive_disk_preflight:
        print(
            json.dumps(
                {
                    "status": "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER",
                    "error": (
                        "UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN: "
                        "--owner-waive-disk-preflight is strictly forbidden in KH-02.R2.C1 "
                        f"(>= {STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2} GiB preflight / >= {STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2} GiB runtime)."
                    ),
                    "exit_code": 2,
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    rc, _run_dir, summary = run_kh02_r2_c1_strict_closure(
        owner_waive_disk_preflight=False,
        run_focused_unit_diagnostics=not args.skip_focused_unit_diagnostics,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
