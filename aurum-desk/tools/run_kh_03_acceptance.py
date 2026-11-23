#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_03_acceptance.py

AURUM DESK — KH-03.R1 | VERIFIED DATA READINESS, FEE-LEDGER ROOT FIX,
CAUSAL ENTRY EDGE RESEARCH, SEALED FRESH OOS & INDEPENDENT ACCEPTANCE
SPEC VERSION: KH-03.R1-v1.0 | RESEARCH ONLY | SAME BRANCH | ACTIVATION_READY=NO
"""
from __future__ import annotations

import json as _early_json
import os as _early_os
import sys as _early_sys

if __name__ == "__main__" and (
    "--owner-waive-disk-preflight" in _early_sys.argv
    or str(_early_os.environ.get("KH02_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
    or str(_early_os.environ.get("KH03_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
):
    print(
        _early_json.dumps(
            {
                "status": "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER",
                "error": "UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN (>=20.5 GiB preflight / >=20.0 GiB runtime).",
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
import copy
from datetime import datetime, timezone
from decimal import Decimal
import gc
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

import psutil

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domain_calculator import CostAssumptions
from services.kh_01b_r1_ram_safety_r3_service import load_empirical_r2_shard_rss_bytes
from services.kh_02_entry_foundation_research_service import (
    build_kh02_asof_mtf_features,
    generate_kh02_research_candidates,
)
from services.kh_02_r2_c4_risk_tail_evidence import (
    build_c4_per_episode_risk_ledger,
    build_c4_realized_tail_evidence_from_r2_replays,
    recompute_c4_tail_counters_from_episode_ledger,
    reconcile_c4_full_universe_cost_fees,
)
from services.kh_02_r2_prospective_data_service import (
    KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
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
    RUNTIME_SAFETY_MODES_KH02_R2,
    STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
    STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
    _ast_function_inventory,
    _r2_frozen_plan_and_arms,
    _sha256_file,
    audit_r2_reference_inputs_and_invariants,
    build_r2_c1_readonly_disk_inventory,
    build_r2_causal_unswept_level_ledger,
    build_r2_target_repair_full_opportunity_conservation,
    create_sentinel_owned_temp_dir_r2_c1,
    load_r2_canonical_dataset_readonly,
    probe_r2_c1_write_mounts_strict,
    replay_r2_same_opportunity_paired_counterfactuals,
    reprice_r2_fixed_fills_cost_counterfactual,
    rerun_r2_full_universe_by_cost_scenario,
)
from services.kh_03_data_governance_service import (
    audit_kh03_collector_and_escrow_readiness,
    build_kh03_partition_manifest_and_noncontamination,
    build_kh03_source_contract_and_quality_inventory,
    validate_kh03_noncontamination_entry,
    validate_kh03_source_contracts,
)
from services.kh_03_fee_integrity_service import (
    SCHEMA_VERSION_KH03,
    SPEC_VERSION_KH03,
    build_kh03_six_scenario_fee_ledger_and_reconciliation,
    extract_authoritative_ledger_fees_kh03,
    validate_kh03_fee_ledger_independently,
)
from services.kh_03_research_service import (
    preregister_kh03_research_plan,
    run_kh03_full_research_study_10_to_16,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_02_r2_c3_independent_verifier import _audit_memory_samples_c3
from tools.kh_03_independent_verifier import (
    KH03_SOURCE_FILES_ALLOWLIST,
    PRIOR_KH02_R2_C4_EVIDENCE_SHA,
    PRIOR_KH02_R2_C4_PUB_HEAD_SHA,
    PRIOR_KH02_R2_C4_RUN_ID,
    PRIOR_KH02_R2_C4_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03,
    recompute_kh03_24_gates_and_verdicts,
)
from tools.run_kh_01b_r1_verification import (
    _build_lpt_shards_kh01b_r1,
    _load_empirical_file_durations_ms_from_kh01b,
    _run_controlled_pytest_w1_w2_w4_benchmark_r1,
    run_async_lpt_pytest_shards_kh01b_r1,
)
from tools.run_kh_02_r2_c4_acceptance import (
    C4_EXCLUSIVE_SUBPROCESS_TEST_FILES,
    _cleanup_sentinel_temp_c4_win_safe,
    _pypath_str,
    _run_c4_126_red_green_fault_suite,
)
from tools.run_kh_02_r2_verification import (
    _BoundedResourceSampler,
    _git_cmd,
    _run_full_production_baseline_parity_20,
    _run_r2_science_slice_w1_w2_w4_determinism,
)
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

KH03_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_03_research_data"

KH03_EXCLUSIVE_SUBPROCESS_TEST_FILES: Set[str] = set(C4_EXCLUSIVE_SUBPROCESS_TEST_FILES) | {
    "test_kh_03_async_and_resource_safety.py",
    "test_kh_03_verifier_mutations.py",
}

# Genuine pre-patch RED proof captured against unmodified C4 HEAD 9200c802 before KH-03 root fix
KH03_PRE_PATCH_FEE_RED_PROOF_9200C802: Dict[str, Any] = {
    "spec_version": SPEC_VERSION_KH03,
    "baseline_sha": PRIOR_KH02_R2_C4_PUB_HEAD_SHA,
    "captured_before_kh03_edits": True,
    "classification": "REAL_RED",
    "root_bug_location": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py:_replay_chronological_custom_cost_scenario:3808",
    "root_bug_explanation": (
        "Read `ld.get('total_fees_usd') or 0.0`, which is absent from `ExecutableTradeLedgerEntryV17931.to_dict()` "
        "(authoritative fields are `entry_fee_usd` and `exit_fee_usd`). Caused 29/30 scenario x arm cells to display "
        "`total_fees_usd = 0.0` while `realized_net_pnl_usd` already deducted fees."
    ),
    "zero_fee_display_bug_cells_count": 29,
    "single_simulator_trade_red_proof": {
        "opportunity_id": "kh02_b2_LONG_idx3063_ts1791766800000",
        "exit_type": "LOSS",
        "quantity": 0.3,
        "entry_fill_price": 4214.65,
        "exit_fill_price": 4209.84,
        "entry_fee_usd": 0.5058,
        "exit_fee_usd": 0.5052,
        "total_friction_usd": 1.011,
        "has_total_fees_usd_key_in_to_dict": False,
        "realized_gross_pnl_usd": -1.443,
        "realized_net_pnl_usd": -2.454,
        "gross_minus_net_usd": 1.011,
        "entry_plus_exit_fee_usd": 1.011,
    },
    "dataflow_graph": [
        "simulate_v17931_trade_execution_net() [v17_9_2_structural_stop_intelligence.py]",
        "-> ExecutableTradeLedgerEntryV17931.to_dict() [emits entry_fee_usd, exit_fee_usd, total_friction_usd]",
        "-> extract_authoritative_ledger_fees_kh03() [kh_03_fee_integrity_service.py: strict Decimal entry+exit]",
        "-> _replay_chronological_custom_cost_scenario() [kh_02_r2_research_integrity_service.py]",
        "-> _summarize_episodes() -> rerun_r2_full_universe_by_cost_scenario() -> 04_fee_per_episode_ledger.jsonl.gz & 05",
    ],
    "six_scenario_old_cells": {
        "1X_BASE_0p0004": {
            "B2_H1": {"closed": 73, "wins": 1, "losses": 72, "open_at_horizon": 9, "gross_pnl_usd": -73.1555, "net_pnl_usd": -175.438, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 102.2825, "executed_ids_sha256": "2e31a75b3e83fbe13ed88adfae5d57cfc0ef1ccb86d98aa30dfadfe445f7cadd"},
            "B2_H123": {"closed": 23, "wins": 0, "losses": 23, "open_at_horizon": 8, "gross_pnl_usd": -26.2462, "net_pnl_usd": -57.6807, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 31.4345, "executed_ids_sha256": "54e41563dcd8464b5f19502fdbf5d6aeeaa44244f65102cd5f2fbf5daf9c12ef"},
            "B2_H2": {"closed": 203, "wins": 7, "losses": 196, "open_at_horizon": 28, "gross_pnl_usd": -164.9541, "net_pnl_usd": -441.6118, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 276.6577, "executed_ids_sha256": "5f9252f91c3d3e3da3e320c80b89e01556f97dcd624327b27f27c05e881348d7"},
            "B2_H3": {"closed": 114, "wins": 5, "losses": 109, "open_at_horizon": 16, "gross_pnl_usd": -89.8935, "net_pnl_usd": -239.6588, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 149.7653, "executed_ids_sha256": "a144cd86c018afb5a9e1cb02ac48d2d809969c2939a7f9760f92ce465997bb54"},
            "NC_NULL_TRIGGER": {"closed": 203, "wins": 7, "losses": 196, "open_at_horizon": 28, "gross_pnl_usd": -164.9541, "net_pnl_usd": -441.6118, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 276.6577, "executed_ids_sha256": "5f9252f91c3d3e3da3e320c80b89e01556f97dcd624327b27f27c05e881348d7"},
        },
        "FEE_ONLY_0p0006": {
            "B2_H1": {"closed": 41, "wins": 2, "losses": 39, "open_at_horizon": 6, "gross_pnl_usd": -20.041, "net_pnl_usd": -84.88, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 64.839, "executed_ids_sha256": "30be7016bad0e406e8e010bde5550bf2cee2ed7f60b5a7592d19ba446444014a"},
            "B2_H123": {"closed": 12, "wins": 1, "losses": 11, "open_at_horizon": 6, "gross_pnl_usd": -0.9485, "net_pnl_usd": -21.0497, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 20.1012, "executed_ids_sha256": "88c5e55beaecb4371af529992e361b405266215c888eb4e9176c724a8007fec9"},
            "B2_H2": {"closed": 138, "wins": 5, "losses": 133, "open_at_horizon": 28, "gross_pnl_usd": -81.5907, "net_pnl_usd": -299.6384, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 218.0477, "executed_ids_sha256": "55974211cd6b9cee64e016f8cc7938d6657694099ade102175476782554d65a0"},
            "B2_H3": {"closed": 84, "wins": 2, "losses": 82, "open_at_horizon": 15, "gross_pnl_usd": -59.7984, "net_pnl_usd": -190.3363, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 130.5379, "executed_ids_sha256": "5cdaa316d0c130d2b5cbd8cf0142db7f110fc52d0bcad72f4e3410869718c3ef"},
            "NC_NULL_TRIGGER": {"closed": 138, "wins": 5, "losses": 133, "open_at_horizon": 28, "gross_pnl_usd": -82.0567, "net_pnl_usd": -299.6037, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 217.547, "executed_ids_sha256": "69f818f779b73fa04c6b1e95fdb19c2991517ff7ce5042d2e9b02300266da742"},
        },
        "2X_ALL_COST_0p0004": {
            "B2_H1": {"closed": 24, "wins": 0, "losses": 24, "open_at_horizon": 4, "gross_pnl_usd": -20.8608, "net_pnl_usd": -59.7975, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 38.9367, "executed_ids_sha256": "46b5a18582cd4723482eb96736c15c844d1263c0ce44e6576aceec7572c434fe"},
            "B2_H123": {"closed": 6, "wins": 0, "losses": 6, "open_at_horizon": 4, "gross_pnl_usd": -5.8829, "net_pnl_usd": -14.8701, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 8.9872, "executed_ids_sha256": "cc3d80540303fed703d8146759d319b865f92df06a03c7003cdc0f7d138c24e7"},
            "B2_H2": {"closed": 111, "wins": 3, "losses": 108, "open_at_horizon": 20, "gross_pnl_usd": -64.1289, "net_pnl_usd": -253.7513, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 189.6224, "executed_ids_sha256": "a5e6c214f46a167f90ef25bb124f8f212851e68dffbab52cb3f56b888689918e"},
            "B2_H3": {"closed": 70, "wins": 2, "losses": 68, "open_at_horizon": 12, "gross_pnl_usd": -39.9362, "net_pnl_usd": -158.8308, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 118.8946, "executed_ids_sha256": "ed8497b201d1805adf80bce9bb1026f43a69e56e536207203cb908db1b229d71"},
            "NC_NULL_TRIGGER": {"closed": 111, "wins": 3, "losses": 108, "open_at_horizon": 20, "gross_pnl_usd": -64.1289, "net_pnl_usd": -253.7513, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 189.6224, "executed_ids_sha256": "a5e6c214f46a167f90ef25bb124f8f212851e68dffbab52cb3f56b888689918e"},
        },
        "2X_ALL_COST_0p0006": {
            "B2_H1": {"closed": 5, "wins": 0, "losses": 5, "open_at_horizon": 0, "gross_pnl_usd": -3.694, "net_pnl_usd": -12.3382, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 8.6442, "executed_ids_sha256": "a4abdacefeaa5eff03ab7760965ea106d449c0f67b2997c04086e4f5cbbfd8d0"},
            "B2_H123": {"closed": 5, "wins": 0, "losses": 5, "open_at_horizon": 0, "gross_pnl_usd": -2.7415, "net_pnl_usd": -12.5217, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 9.7802, "executed_ids_sha256": "5b343999a4de0a049c84b058d86430358bdb0b94287bc57bb9be6f1e313e1fae"},
            "B2_H2": {"closed": 46, "wins": 0, "losses": 46, "open_at_horizon": 10, "gross_pnl_usd": -26.1278, "net_pnl_usd": -113.8658, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 87.738, "executed_ids_sha256": "11f298ff49adafb7d92ba5e3423c916647549d661b746b70510a5dc37f8b504a"},
            "B2_H3": {"closed": 39, "wins": 0, "losses": 39, "open_at_horizon": 4, "gross_pnl_usd": -22.1387, "net_pnl_usd": -96.6987, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 74.56, "executed_ids_sha256": "79c136ca843bf773050abda0a1d950cdb1fc582405d599dd90a16e144f4a4ca8"},
            "NC_NULL_TRIGGER": {"closed": 46, "wins": 0, "losses": 46, "open_at_horizon": 10, "gross_pnl_usd": -26.1278, "net_pnl_usd": -113.8658, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 87.738, "executed_ids_sha256": "11f298ff49adafb7d92ba5e3423c916647549d661b746b70510a5dc37f8b504a"},
        },
        "3X_ALL_COST_0p0004": {
            "B2_H1": {"closed": 5, "wins": 0, "losses": 5, "open_at_horizon": 0, "gross_pnl_usd": -3.864, "net_pnl_usd": -12.3146, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 8.4506, "executed_ids_sha256": "a4abdacefeaa5eff03ab7760965ea106d449c0f67b2997c04086e4f5cbbfd8d0"},
            "B2_H123": {"closed": 5, "wins": 0, "losses": 5, "open_at_horizon": 0, "gross_pnl_usd": -2.9364, "net_pnl_usd": -12.426, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 9.4896, "executed_ids_sha256": "5b343999a4de0a049c84b058d86430358bdb0b94287bc57bb9be6f1e313e1fae"},
            "B2_H2": {"closed": 45, "wins": 0, "losses": 45, "open_at_horizon": 10, "gross_pnl_usd": -27.3175, "net_pnl_usd": -112.2769, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 84.9594, "executed_ids_sha256": "617cbd197d204d8d43307a5c3b3b8ed4ce6fe2a324e8fdfb2f4e5b67533f8852"},
            "B2_H3": {"closed": 37, "wins": 0, "losses": 37, "open_at_horizon": 4, "gross_pnl_usd": -23.1675, "net_pnl_usd": -92.0618, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 68.8943, "executed_ids_sha256": "91e9533ef9395342b9564da5b9e9a1052df1727ae87f2e3f3c0e25dbaa9e8703"},
            "NC_NULL_TRIGGER": {"closed": 45, "wins": 0, "losses": 45, "open_at_horizon": 10, "gross_pnl_usd": -27.3175, "net_pnl_usd": -112.2769, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 84.9594, "executed_ids_sha256": "617cbd197d204d8d43307a5c3b3b8ed4ce6fe2a324e8fdfb2f4e5b67533f8852"},
        },
        "3X_ALL_COST_0p0006": {
            "B2_H1": {"closed": 2, "wins": 0, "losses": 2, "open_at_horizon": 0, "gross_pnl_usd": -1.3787, "net_pnl_usd": -4.8151, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 3.4364, "executed_ids_sha256": "e36758d9bcd1d665f5071b2cd45898e48fbff8025197dba19add3e2b66f9e20f"},
            "B2_H123": {"closed": 0, "wins": 0, "losses": 0, "open_at_horizon": 0, "gross_pnl_usd": 0.0, "net_pnl_usd": 0.0, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 0.0, "executed_ids_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},
            "B2_H2": {"closed": 14, "wins": 0, "losses": 14, "open_at_horizon": 4, "gross_pnl_usd": -7.2788, "net_pnl_usd": -34.0747, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 26.7959, "executed_ids_sha256": "61fe83008dd858a58d7801785be5f09c2963e3992a95c6b2ff78055a0b7a7f12"},
            "B2_H3": {"closed": 9, "wins": 0, "losses": 9, "open_at_horizon": 1, "gross_pnl_usd": -4.4576, "net_pnl_usd": -21.8573, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 17.3997, "executed_ids_sha256": "4a1348d1cc0f192ee1600a63169b7190b3f73ffc05e61197f7ef30a7c3e6e2b2"},
            "NC_NULL_TRIGGER": {"closed": 14, "wins": 0, "losses": 14, "open_at_horizon": 4, "gross_pnl_usd": -7.2788, "net_pnl_usd": -34.0747, "old_displayed_total_fees_usd": 0.0, "implied_gross_minus_net_usd": 26.7959, "executed_ids_sha256": "61fe83008dd858a58d7801785be5f09c2963e3992a95c6b2ff78055a0b7a7f12"},
        },
    },
}


def _audit_kh03_function_ownership_01(tested_sha: str) -> Dict[str, Any]:
    locked_inv: List[Dict[str, Any]] = []
    for rel in LOCKED_KEEP_AS_IS_FILES_KH02_R2:
        p = REPO_ROOT / rel
        is_py = rel.endswith(".py")
        locked_inv.append(
            {
                "rel_path": rel,
                "exists": p.is_file(),
                "sha256": _sha256_file(p) if p.is_file() else None,
                "ast_function_count": len(_ast_function_inventory(p)) if (is_py and p.is_file()) else None,
            }
        )

    rc_diff, diff_out = _git_cmd(["diff", "--name-only", f"{PRIOR_KH02_R2_C4_PUB_HEAD_SHA}..HEAD"])
    changed_files = [ln.strip() for ln in diff_out.splitlines() if ln.strip()] if rc_diff == 0 else []
    locked_set = set(LOCKED_KEEP_AS_IS_FILES_KH02_R2)
    locked_mod = [f for f in changed_files if f in locked_set]

    forbidden_hits: List[str] = []
    kh03_inv: List[Dict[str, Any]] = []
    for rel in KH03_SOURCE_FILES_ALLOWLIST:
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        if rel.endswith(".py") and "kh_03" in rel:
            src = p.read_text("utf-8", errors="replace")
            tree = ast.parse(src, filename=str(p))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if any(tok in alias.name for tok in FORBIDDEN_ORDER_SERVICE_TOKENS):
                            forbidden_hits.append(f"{rel}:import:{alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if any(tok in mod for tok in FORBIDDEN_ORDER_SERVICE_TOKENS):
                        forbidden_hits.append(f"{rel}:from:{mod}")
            kh03_inv.append(
                {
                    "rel_path": rel,
                    "sha256": _sha256_file(p),
                    "functions_count": len(_ast_function_inventory(p)),
                }
            )

    c_live = CostAssumptions()
    cost_ok = (
        abs(float(c_live.maker_fee_rate) - 0.0002) <= 1e-12
        and abs(float(c_live.taker_fee_rate) - 0.0004) <= 1e-12
        and abs(float(c_live.slippage_usd) - 0.10) <= 1e-12
    )
    ok = len(locked_mod) == 0 and len(forbidden_hits) == 0 and cost_ok
    return {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "tested_executable_sha": tested_sha,
        "prior_c4_pub_head_sha": PRIOR_KH02_R2_C4_PUB_HEAD_SHA,
        "locked_keep_as_is_count": len(locked_inv),
        "locked_keep_as_is_inventory": locked_inv,
        "locked_files_modified": locked_mod,
        "locked_files_modified_count": len(locked_mod),
        "changed_files_vs_c4": changed_files,
        "kh03_new_and_edited_inventory": kh03_inv,
        "forbidden_order_import_hits": forbidden_hits,
        "cost_assumptions_defaults_intact": cost_ok,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": "PASS" if ok else "FAIL",
    }


def _audit_prior_sealed_02() -> Dict[str, Any]:
    ds_p = REPO_ROOT / CANONICAL_DATASET_REL_PATH
    ds_sha = _sha256_file(ds_p) if ds_p.is_file() else ""
    ds_ok = ds_sha == CANONICAL_DATASET_SHA256_KH02_R2

    prior_map: Dict[str, Any] = {}
    all_ok = ds_ok
    for rel_p, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03:
        p = REPO_ROOT / rel_p
        act = _sha256_file(p) if p.is_file() else ""
        m = p.is_file() and (act == exp_sha)
        if not m:
            all_ok = False
        prior_map[lbl] = {"rel_path": rel_p, "expected_sha256": exp_sha, "actual_sha256": act, "match": m}

    return {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "canonical_dataset_rel_path": CANONICAL_DATASET_REL_PATH,
        "canonical_dataset_sha256": ds_sha,
        "canonical_dataset_expected_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
        "canonical_dataset_sha256_match": ds_ok,
        "all_prior_sealed_artifacts_intact": all_ok,
        "prior_sealed_artifacts_immutability": prior_map,
        "status": "PASS" if all_ok else "FAIL",
    }


def _run_kh03_150_red_green_fault_suite(
    sentinel_tmp: Path,
    f05: Mapping[str, Any],
    fee_ledger_gz: Path,
    c4_tail_f11: Mapping[str, Any],
    c4_tail_gz: Path,
) -> Dict[str, Any]:
    c4_sub = sentinel_tmp / "c4_126_sub"
    c4_sub.mkdir(parents=True, exist_ok=True)
    c4_126 = _run_c4_126_red_green_fault_suite(c4_sub, c4_tail_f11, c4_tail_gz)
    controls = list(c4_126["controls"])
    kh03_new = 0

    def _rec(fid: str, name: str, fn: str, red_ok: bool, green_ok: bool, detail: str) -> None:
        nonlocal kh03_new
        kh03_new += 1
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

    valid_closed_ld = {
        "entry_fee_usd": 0.5058,
        "exit_fee_usd": 0.5052,
        "total_friction_usd": 1.0110,
        "realized_gross_pnl_usd": -1.4430,
        "realized_net_pnl_usd": -2.4540,
    }
    green_fee = extract_authoritative_ledger_fees_kh03(valid_closed_ld, filled=True, outcome_label="LOSS")["total_fees_usd"] == 1.011

    def _raises_val(ld: Mapping[str, Any], filled: bool = True, lbl: str = "LOSS") -> bool:
        try:
            extract_authoritative_ledger_fees_kh03(ld, filled=filled, outcome_label=lbl)
            return False
        except ValueError:
            return True

    # KH03-F01..F10: Fee root & accounting controls
    _rec("KH03-F01", "MISSING_EXIT_FEE_ON_CLOSED_REJECTED", "extract_authoritative_ledger_fees_kh03", _raises_val({"entry_fee_usd": 0.5, "realized_gross_pnl_usd": -1.0, "realized_net_pnl_usd": -1.5}), green_fee, "missing exit fee rejected")
    _rec("KH03-F02", "NEGATIVE_FEE_REJECTED", "extract_authoritative_ledger_fees_kh03", _raises_val(dict(valid_closed_ld, entry_fee_usd=-0.1)), green_fee, "negative fee rejected")
    _rec("KH03-F03", "NAN_FEE_REJECTED", "extract_authoritative_ledger_fees_kh03", _raises_val(dict(valid_closed_ld, exit_fee_usd=float("nan"))), green_fee, "NaN fee rejected")
    _rec("KH03-F04", "INF_FEE_REJECTED", "extract_authoritative_ledger_fees_kh03", _raises_val(dict(valid_closed_ld, entry_fee_usd=float("inf"))), green_fee, "Inf fee rejected")
    _rec("KH03-F05", "DOUBLE_COUNT_TOTAL_FEE_CONFLICT_REJECTED", "extract_authoritative_ledger_fees_kh03", _raises_val(dict(valid_closed_ld, total_fees_usd=2.0220)), green_fee, "double-counted total_fees_usd rejected")
    _rec("KH03-F06", "GROSS_MINUS_FEE_NE_NET_DRIFT_REJECTED", "extract_authoritative_ledger_fees_kh03", _raises_val(dict(valid_closed_ld, realized_net_pnl_usd=-1.4430)), green_fee, "material gross-fee-net drift rejected")
    _rec("KH03-F07", "UNFILLED_NONZERO_FEE_REJECTED", "extract_authoritative_ledger_fees_kh03", _raises_val({"entry_fee_usd": 0.5, "exit_fee_usd": 0.0}, filled=False, lbl="NO_FILL"), extract_authoritative_ledger_fees_kh03({"entry_fee_usd": 0.0, "exit_fee_usd": 0.0}, filled=False, outcome_label="NO_FILL")["total_fees_usd"] == 0.0, "unfilled nonzero fee rejected")
    _rec("KH03-F08", "OPEN_AT_HORIZON_NONZERO_EXIT_FEE_REJECTED", "extract_authoritative_ledger_fees_kh03", _raises_val({"entry_fee_usd": 0.5, "exit_fee_usd": 0.5}, filled=True, lbl="OPEN_AT_HORIZON"), extract_authoritative_ledger_fees_kh03({"entry_fee_usd": 0.5, "exit_fee_usd": 0.0}, filled=True, outcome_label="OPEN_AT_HORIZON")["accrued_open_entry_fee_usd"] == 0.5, "open exit fee rejected, entry accrued separately")

    # Tampered 05 fee=0 caught by streaming independent validator
    f05_tamper = copy.deepcopy(dict(f05))
    f05_tamper["scenarios"]["1X_BASE_0p0004"]["arms"]["B2_H1"]["total_fees_usd"] = 0.0
    _rec("KH03-F09", "TAMPERED_ZERO_FEE_IN_05_CAUGHT", "validate_kh03_fee_ledger_independently", not validate_kh03_fee_ledger_independently(fee_ledger_gz, f05_tamper)["valid"], validate_kh03_fee_ledger_independently(fee_ledger_gz, f05)["valid"], "tampered zero fee caught vs ledger")

    bad_c4_nonzero = reconcile_c4_full_universe_cost_fees({"scenarios": {"1X_BASE_0p0004": {"raw_r2_geometry_risk_eligible_count": 10, "arms": {"B2_H1": {"replay_summary": {"closed": 1, "gross_pnl_usd": -10.0, "net_pnl_usd": -20.0, "total_fees_usd": 20.0}}}}}}, {"arms": {"B2_H1": {"1x_base_0p0004": {"fees_usd": 10.0}}}})
    ok_c4_fixed = reconcile_c4_full_universe_cost_fees({"scenarios": {"1X_BASE_0p0004": {"raw_r2_geometry_risk_eligible_count": 10, "arms": {"B2_H1": {"replay_summary": {"closed": 1, "gross_pnl_usd": -10.0, "net_pnl_usd": -20.0, "total_fees_usd": 10.0}}}}}}, {"arms": {"B2_H1": {"1x_base_0p0004": {"fees_usd": 10.0}}}})
    _rec("KH03-F10", "C4_RECONCILE_CATCHES_DOUBLE_COUNTED_NONZERO_FEE", "reconcile_c4_full_universe_cost_fees", bad_c4_nonzero["status"] == "NOT_RECONCILED", ok_c4_fixed["status"] == "PASS" and ok_c4_fixed["upstream_zero_fee_display_hits"] == 0, "C4 compat checks nonzero double-count")

    # KH03-F11..F20: Data contracts, L1 BBO & partition non-contamination controls
    good_bar = {"open_time": 1791500000000, "close_time": 1791500060000, "open": 4000.0, "high": 4002.0, "low": 3999.0, "close": 4001.0, "volume": 10.0, "is_closed": True}
    g_src = validate_kh03_source_contracts(bars_by_tf={"1m": [good_bar]})["valid"]
    _rec("KH03-F11", "CONTRACT_SYMBOL_DRIFT_REJECTED", "validate_kh03_source_contracts", not validate_kh03_source_contracts(bars_by_tf={"1m": [good_bar]}, source_meta={"exchange": "bitget", "symbol": "XAUUSD", "product_type": "SPOT"})["valid"], g_src, "XAUUSD spot substitute blocked")
    _rec("KH03-F12", "UNCLOSED_BAR_REJECTED", "validate_kh03_source_contracts", not validate_kh03_source_contracts(bars_by_tf={"1m": [dict(good_bar, is_closed=False)]})["valid"], g_src, "unclosed bar blocked")
    _rec("KH03-F13", "INVALID_OHLC_HIGH_LT_CLOSE_REJECTED", "validate_kh03_source_contracts", not validate_kh03_source_contracts(bars_by_tf={"1m": [dict(good_bar, high=3999.5)]})["valid"], g_src, "broken OHLC envelope blocked")
    _rec("KH03-F14", "DUPLICATE_CONFLICTING_BAR_REJECTED", "validate_kh03_source_contracts", not validate_kh03_source_contracts(bars_by_tf={"1m": [good_bar, dict(good_bar, close=4001.5)]})["valid"], g_src, "duplicate conflicting bar blocked")
    _rec("KH03-F15", "INCOMPLETE_PAGINATION_REJECTED", "validate_kh03_source_contracts", not validate_kh03_source_contracts(bars_by_tf={"1m": [good_bar]}, paging_meta={"has_more": True, "pagination_complete": False})["valid"], g_src, "incomplete pagination blocked")
    _rec("KH03-F16", "L1_CROSSED_BOOK_REJECTED", "validate_kh03_source_contracts", not validate_kh03_source_contracts(bars_by_tf={"1m": [good_bar]}, l1_quotes=[{"symbol": "XAUUSDT", "bid": 4001.0, "ask": 4000.0, "event_utc_ms": 1791500060000, "receive_utc_ms": 1791500060020}])["valid"], validate_kh03_source_contracts(bars_by_tf={"1m": [good_bar]}, l1_quotes=[{"symbol": "XAUUSDT", "bid": 4000.10, "ask": 4000.30, "event_utc_ms": 1791500060000, "receive_utc_ms": 1791500060020}])["valid"], "crossed L1 rejected")
    _rec("KH03-F17", "L1_SYNTHETIC_PASSED_AS_OBSERVED_REJECTED", "validate_kh03_source_contracts", not validate_kh03_source_contracts(bars_by_tf={"1m": [good_bar]}, l1_quotes=[{"symbol": "XAUUSDT", "bid": 4000.10, "ask": 4000.30, "event_utc_ms": 1791500060000, "receive_utc_ms": 1791500060020, "is_synthetic": True}])["valid"], g_src, "synthetic L1 rejected")

    g_part = validate_kh03_noncontamination_entry(dataset_sha256="d" * 64, target_role="FUTURE_FRESH_SEALED_UNTOUCHED", start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS, end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 60_000)["admitted"]
    _rec("KH03-F18", "REUSED_DEV_SHA_CANNOT_BE_FRESH_OOS", "validate_kh03_noncontamination_entry", not validate_kh03_noncontamination_entry(dataset_sha256=CANONICAL_DATASET_SHA256_KH02_R2, target_role="FUTURE_FRESH_SEALED_UNTOUCHED", start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 60_000, end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 120_000)["admitted"], g_part, "canonical DEV SHA blocked as fresh OOS")
    _rec("KH03-F19", "EXTENDED_DEV_SHA_CANNOT_BE_FRESH_OOS", "validate_kh03_noncontamination_entry", not validate_kh03_noncontamination_entry(dataset_sha256=KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256, target_role="FUTURE_FRESH_SEALED_UNTOUCHED", start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 60_000, end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 120_000)["admitted"], g_part, "extended DEV SHA blocked")
    _rec("KH03-F20", "PEEKED_OOS_SHA_CANNOT_RESEAL", "validate_kh03_noncontamination_entry", not validate_kh03_noncontamination_entry(dataset_sha256="e" * 64, target_role="FUTURE_FRESH_SEALED_UNTOUCHED", start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 60_000, end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 120_000, peeked_shas=("e" * 64,))["admitted"], g_part, "peeked SHA permanently tainted")
    _rec("KH03-F21", "EMBARGO_MINUS_1MS_REJECTED", "validate_kh03_noncontamination_entry", not validate_kh03_noncontamination_entry(dataset_sha256="f" * 64, target_role="FUTURE_FRESH_SEALED_UNTOUCHED", start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS - 1, end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 120_000)["admitted"], g_part, "embargo boundary enforced")

    # KH03-F22..F24: CLI disk waiver env/flag + stale SHA check
    env_w = os.environ.copy()
    env_w["KH03_OWNER_WAIVE_DISK_PREFLIGHT"] = "1"
    p_w = subprocess.run([sys.executable, str(APP_ROOT / "tools" / "run_kh_03_acceptance.py"), "--help"], cwd=str(APP_ROOT), env=env_w, capture_output=True, text=True, timeout=20)
    _rec("KH03-F22", "KH03_DISK_WAIVER_ENV_REFUSED", "run_kh_03_acceptance.main", p_w.returncode == 2, True, "KH03 env waiver exits 2")

    p_f = subprocess.run([sys.executable, str(APP_ROOT / "tools" / "run_kh_03_acceptance.py"), "--owner-waive-disk-preflight"], cwd=str(APP_ROOT), capture_output=True, text=True, timeout=20)
    _rec("KH03-F23", "KH03_DISK_WAIVER_CLI_FLAG_REFUSED", "run_kh_03_acceptance.main", p_f.returncode == 2, True, "KH03 CLI waiver exits 2")

    _rec("KH03-F24", "INVALID_PARTITION_ROLE_REJECTED", "validate_kh03_noncontamination_entry", not validate_kh03_noncontamination_entry(dataset_sha256="d" * 64, target_role="LIVE_TRADING_AUTO", start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS, end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 60_000)["admitted"], g_part, "unknown partition role blocked")

    passed_n = sum(1 for c in controls if c["status"] == "PASS")
    fids = [c["fault_id"] for c in controls]
    return {
        "spec_version": SPEC_VERSION_KH03,
        "inherited_c4_controls_count": len(c4_126["controls"]),
        "new_kh03_controls_count": kh03_new,
        "total_controls": len(controls),
        "unique_fault_ids": len(set(fids)),
        "passed_controls": passed_n,
        "failed_controls": len(controls) - passed_n,
        "controls": controls,
        "status": "PASS" if (passed_n == len(controls) and len(set(fids)) == len(controls) and len(controls) >= 146) else "FAIL",
    }


def _run_kh03_verifier_mutation_suite_21(sentinel_tmp: Path) -> Dict[str, Any]:
    """Runs 14 separate-OS verifier mutation-resistance controls strictly inside isolated temp clones."""
    from tests.test_kh_03_verifier_mutations import (
        KH03_VERIFIER_MUTATIONS_SPECS,
        build_synthetic_valid_kh03_run_dir,
        run_kh03_verifier_subprocess,
    )

    base_clone = sentinel_tmp / "kh03_verif_mut_base"
    rid, tsha = build_synthetic_valid_kh03_run_dir(base_clone)
    rc_clean, clean_out = run_kh03_verifier_subprocess(base_clone, rid, tsha)
    clean_ok = (rc_clean == 0)

    results: List[Dict[str, Any]] = []
    for mid, mut_fn, expected_tok in KH03_VERIFIER_MUTATIONS_SPECS:
        cdir = sentinel_tmp / f"kh03_mut_{mid}"
        shutil.copytree(base_clone, cdir)
        mut_fn(cdir)
        rc, out_txt = run_kh03_verifier_subprocess(cdir, rid, tsha)
        detected = (rc != 0) and (expected_tok in out_txt)
        results.append(
            {
                "mutation_id": mid,
                "expected_violation_token": expected_tok,
                "verifier_exit_code": rc,
                "detected_fail_closed": detected,
            }
        )

    det_n = sum(1 for r in results if r["detected_fail_closed"])
    return {
        "spec_version": SPEC_VERSION_KH03,
        "isolated_temp_clones_only": True,
        "sealed_run_dir_mutated": False,
        "clean_clone_verifier_green": clean_ok,
        "mutations_tested_count": len(results),
        "mutations_detected_count": det_n,
        "mutations_failed_detection_count": len(results) - det_n,
        "results": results,
        "status": "PASS" if (clean_ok and det_n == len(results) and len(results) >= 12) else "FAIL",
    }


def _run_kh03_full_repo_pytest_sharded(
    run_dir: Path,
    sentinel_tmp: Path,
    *,
    run_id: str,
    tested_sha: str,
    effective_disk_reserve_bytes: int,
    max_workers: int,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    env = os.environ.copy()
    env["PYTHONPATH"] = _pypath_str()
    proc_col = subprocess.run(
        [sys.executable, "-m", "pytest", "backend/tests", "--collect-only", "-q"],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=180,
    )
    if proc_col.returncode != 0:
        raise RuntimeError(f"KH03_PYTEST_COLLECTION_FAILED:rc={proc_col.returncode}:{proc_col.stderr[:400]}")

    collected_by_file: Dict[str, List[str]] = {}
    for line in proc_col.stdout.splitlines():
        s = line.strip()
        if "::" in s and s.startswith("backend/tests/"):
            nid = canonicalize_collect_node_id_v1793122(s)
            fpath = nid.split("::", 1)[0]
            collected_by_file.setdefault(fpath, []).append(nid)

    emp_dur = _load_empirical_file_durations_ms_from_kh01b()
    base_shards = _build_lpt_shards_kh01b_r1(collected_by_file, emp_dur)
    kh03_shards: List[Dict[str, Any]] = []
    extracted_subproc_files: List[str] = []
    for sh in base_shards:
        keep_files: List[str] = []
        for f in sh["files"]:
            if Path(f).name in KH03_EXCLUSIVE_SUBPROCESS_TEST_FILES:
                extracted_subproc_files.append(f)
            else:
                keep_files.append(f)
        if keep_files:
            ns = dict(sh)
            ns["files"] = keep_files
            kh03_shards.append(ns)
    for idx, sf in enumerate(sorted(set(extracted_subproc_files)), 1):
        kh03_shards.append(
            {
                "shard_id": f"SHARD_SINGLE_CHILD_KH03_SUBPROC_{idx:02d}",
                "files": [sf],
                "exclusive": True,
                "child_budget_cost": 4,
            }
        )

    active_shards_tracker: Dict[str, Any] = {}
    junit_res = asyncio.run(
        run_async_lpt_pytest_shards_kh01b_r1(
            run_dir,
            sentinel_tmp / "s_r1",
            run_id=run_id,
            tested_sha=tested_sha,
            max_workers=max_workers,
            effective_disk_reserve_bytes=effective_disk_reserve_bytes,
            enforce_r3_ram_guard=True,
            empirical_rss_map=load_empirical_r2_shard_rss_bytes(),
            active_shards_tracker=active_shards_tracker,
            shard_specs_override=kh03_shards,
            collected_by_file_override=collected_by_file,
        )
    )
    elapsed = round(time.perf_counter() - t0, 3)
    cm = junit_res["collection_manifest"]
    sm = junit_res["shard_manifest"]
    bj = verify_fresh_junit_bijection_v17931211(
        run_dir, cm, sm,
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        mandatory_node_prefixes=("backend/tests/test_kh_02_r2", "backend/tests/test_kh_03"),
        require_shard_schedule=True,
    )
    collected_n = int(cm.get("collected_node_ids_count") or len(cm.get("collected_node_ids") or ()))
    executed_n = int(bj.get("executed_node_ids_count") or bj.get("executed_count") or 0)
    passed_n = int(bj.get("passed_count") or 0)
    skipped_n = int(bj.get("skipped_count") or 0)
    failed_n = int(bj.get("failed_count") or 0)
    error_n = int(bj.get("error_count") or 0)
    mand_skip_n = int(bj.get("mandatory_skipped_count") or 0)
    nonzero_shards = int(sm.get("nonzero_exit_shards_count") or 0)
    ok = bj.get("status") == "PASS" and nonzero_shards == 0 and collected_n >= 5578 and collected_n == executed_n and failed_n == 0 and error_n == 0 and mand_skip_n == 0
    return {
        "spec_version": SPEC_VERSION_KH03,
        "status": "PASS" if ok else "FAIL",
        "elapsed_sec": elapsed,
        "max_workers_used": max_workers,
        "collected_count": collected_n,
        "executed_count": executed_n,
        "passed_count": passed_n,
        "skipped_count": skipped_n,
        "mandatory_skipped_count": mand_skip_n,
        "failed_count": failed_n,
        "error_count": error_n,
        "nonzero_exit_shards_count": nonzero_shards,
        "junit_bijection_exact": bj.get("status") == "PASS",
        "collection_manifest": cm,
        "shard_manifest": sm,
        "bijection_audit": bj,
    }


def _render_kh03_readme_and_handover(
    *,
    run_id: str,
    run_dir: Path,
    f00: Mapping[str, Any],
    f05: Mapping[str, Any],
    f15: Mapping[str, Any],
    f17: Mapping[str, Any],
    f18: Mapping[str, Any],
    f19: Mapping[str, Any],
    f20: Mapping[str, Any],
    f21: Mapping[str, Any],
    f22: Mapping[str, Any],
    f23: Mapping[str, Any],
) -> Tuple[str, str]:
    v = f22["verdicts"]
    g = f22["gates_24"]
    telem = f18["runtime_resource_telemetry"]
    mp = f00["strict_multi_mount_probe"]

    md_lines = [
        "# AURUM DESK — KH-03.R1 | RESEARCH & DATA INTEGRITY CLOSURE",
        "",
        f"- **Phân loại kỹ thuật**: **`{f22['overall_classification']}`**",
        f"- **Spec Version**: `{SPEC_VERSION_KH03}`",
        f"- **Run ID**: `{run_id}`",
        f"- **Nhánh bắt buộc**: `{f00['branch']}`",
        f"- **Tested Executable SHA**: `{f00['tested_executable_sha']}` (`git_clean_at_start={f00['git_clean_at_start']}`)",
        "- **Khóa an toàn**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`",
        "",
        "## 1. Kết luận trực giao (Không gộp giả)",
        "",
        "| Chiều kiểm định | Kết luận |",
        "|---|---|",
        *[f"| `{k}` | **{val}** |" for k, val in v.items()],
        "",
        "## 2. Ma trận 24 cổng (`G01`..`G24`)",
        "",
        "| Cổng | Trạng thái |",
        "|---|---|",
        *[f"| `{k}` | **{val}** |" for k, val in g.items()],
        "",
        "## 3. Tóm tắt số đo thực tế",
        f"- **P0 Fee Root Fix (`03`, `04`, `05`)**: `C4_UPSTREAM_FEE_DISPLAY_ROOT_CAUSE_FIXED={f05['C4_UPSTREAM_FEE_DISPLAY_ROOT_CAUSE_FIXED']}`, zero-fee bug hits after fix = `{f05['zero_fee_display_bug_hits_after_fix']}`, episode rows = `{f05['linked_per_episode_fee_ledger']['total_episode_rows']}`, max episode residual = `{f05['global_max_episode_gross_fee_net_residual_usd']} USD`, `ACCOUNT_EFFECTIVE_FEE_VERDICT={f05['ACCOUNT_EFFECTIVE_FEE_VERDICT']}`.",
        f"- **KH-03 Research Science (`10`..`16`)**: `KH03_DEV_CANDIDATE_VERDICT={f15['KH03_DEV_CANDIDATE_VERDICT']}`, `RESEARCH_ECONOMIC_VERDICT={f15['RESEARCH_ECONOMIC_VERDICT']}`, `FRESH_OOS_VERDICT={f15['FRESH_OOS_VERDICT']}`, `KH04_ENTRY={f15['KH04_ENTRY']}`, `OOS_EVALUATIONS=0`.",
        f"- **Full Pytest (`17`)**: `{f17['status']}` — collected=`{f17.get('collected_count')}`, executed=`{f17.get('executed_count')}`, passed=`{f17.get('passed_count')}`, skipped=`{f17.get('skipped_count')}`, mandatory_skipped=`{f17.get('mandatory_skipped_count')}`, failed=`{f17.get('failed_count')}`.",
        f"- **Faults (`19`)**: `{f19['passed_controls']}/{f19['total_controls']}` (`{f19['new_kh03_controls_count']}` new KH-03 controls).",
        f"- **Verifier Mutations (`21`)**: `{f21['mutations_detected_count']}/{f21['mutations_tested_count']}` detected.",
        f"- **Baseline Parity (`20`)**: `{f20.get('BASELINE_PARITY_VERDICT')}` (`A1={f20.get('a1_population_count')}/0`, `SI8={f20.get('si8_population_count')}/0`, `3-adapter={f20.get('three_adapter_population_count')}/0`, `D4={f20.get('d4_historical_closed_trades')}`).",
        f"- **Tài nguyên (`00`, `18`)**: Preflight `{mp['min_free_gib']} GiB`, runtime min `{telem['min_disk_free_gib']} GiB`, RAM min `{telem['min_available_ram_mib']} MiB`, max children `{telem['max_descendant_children']}`, orphans `{telem['orphan_children_after_cleanup']}`.",
    ]

    vi_lines = [
        "=" * 80,
        "BAN GIAO CHU SO HUU (TIENG VIET) — AURUM DESK | KH-03.R1",
        "=" * 80,
        f"Phan loai ky thuat        : {f22['overall_classification']}",
        f"Run ID                    : {run_id}",
        f"Nhanh                     : {f00['branch']}",
        f"Tested Executable SHA     : {f00['tested_executable_sha']}",
        "An toan giao dich         : PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO",
        f"KH03_DEV_CANDIDATE_VERDICT: {f15['KH03_DEV_CANDIDATE_VERDICT']}",
        f"RESEARCH_ECONOMIC_VERDICT : {f15['RESEARCH_ECONOMIC_VERDICT']} (NO_PROVEN_EDGE)",
        f"FRESH_OOS_VERDICT         : {f15['FRESH_OOS_VERDICT']} (OOS_EVALUATIONS=0)",
        f"KH04_ENTRY                : {f15['KH04_ENTRY']}",
        f"Fee root fix (03/04/05)   : fixed={f05['C4_UPSTREAM_FEE_DISPLAY_ROOT_CAUSE_FIXED']} zero_hits={f05['zero_fee_display_bug_hits_after_fix']} rows={f05['linked_per_episode_fee_ledger']['total_episode_rows']}",
        f"O dia preflight / min     : {mp['min_free_gib']} GiB / {telem['min_disk_free_gib']} GiB",
        f"RAM min / children / orph : {telem['min_available_ram_mib']} MiB / {telem['max_descendant_children']} / {telem['orphan_children_after_cleanup']}",
        f"Full pytest (17)          : {f17['status']} ({f17.get('passed_count')}/{f17.get('collected_count')} passed, {f17.get('skipped_count')} skipped, {f17.get('failed_count')} failed)",
        f"Async W1/W2/W4 (18)       : {f18['status']}",
        f"Parity 56710 (20)         : {f20.get('BASELINE_PARITY_VERDICT')}",
        f"Faults RED/GREEN (19)     : {f19['passed_controls']}/{f19['total_controls']} PASS ({f19['new_kh03_controls_count']} new KH-03)",
        f"Verifier Mutations (21)   : {f21['mutations_detected_count']}/{f21['mutations_tested_count']} detected PASS",
        f"Publication (23)          : {f23['publication_status']}",
        f"Thu muc bang chung        : {run_dir}",
        "=" * 80,
    ]
    return "\n".join(md_lines) + "\n", "\n".join(vi_lines) + "\n"


def run_kh03_acceptance(
    *,
    allow_local_dirty: bool = False,
    owner_waive_disk_preflight: bool = False,
    max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2,
    artifacts_root: Optional[Path] = None,
) -> Tuple[int, Path, Dict[str, Any]]:
    if owner_waive_disk_preflight:
        raise RuntimeError("UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN")

    t_start = time.perf_counter()
    utc_now = datetime.now(timezone.utc)
    utc_stamp = utc_now.strftime("%Y%m%dT%H%M%SZ")
    orch_pid = os.getpid()
    eff_workers = max(1, min(GLOBAL_MAX_DESCENDANTS_KH02_R2, int(max_workers)))

    _, branch = _git_cmd(["branch", "--show-current"])
    _, start_head = _git_cmd(["rev-parse", "HEAD"])
    _, porcelain = _git_cmd(["status", "--porcelain=v1"])
    rc_ls, remote_line = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH02_R2}"], timeout=15)
    remote_sha = remote_line.split()[0] if (rc_ls == 0 and remote_line) else "UNAVAILABLE_OR_OFFLINE"
    git_clean = len(porcelain.strip()) == 0
    if branch != MANDATORY_BRANCH_KH02_R2:
        raise RuntimeError(f"BLOCKED_WRONG_BRANCH:expected={MANDATORY_BRANCH_KH02_R2},actual={branch}")
    if (not git_clean) and (not allow_local_dirty):
        raise RuntimeError("BLOCKED_DIRTY_WORKING_TREE: commit KH-03 source cleanly before verified acceptance run.")

    run_id = f"{utc_stamp}_{start_head[:12]}"
    art_base = (artifacts_root or (REPO_ROOT / KH03_ARTIFACTS_BASE_REL)).resolve()
    sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1(f"kh03r1_{utc_stamp}_", run_id=run_id, owner_pid=orch_pid)

    write_mount_paths = {
        "repo_volume": REPO_ROOT,
        "app_volume": APP_ROOT,
        "artifacts_output": art_base if art_base.exists() else APP_ROOT,
        "temp_volume": sentinel_tmp,
        "python_venv": Path(sys.executable).resolve().parent,
        "caches": Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")),
        "logs": art_base / run_id / "logs",
    }
    strict_probe = probe_r2_c1_write_mounts_strict(write_mount_paths, atomic_probe_dirs=[(sentinel_tmp, True, run_id)])
    vm = psutil.virtual_memory()
    avail_ram_mib = round(vm.available / (1024.0 ** 2), 2)
    ram_ok = avail_ram_mib >= PREFLIGHT_RAM_MIN_MIB_KH02_R2
    strict_disk_ok = bool(strict_probe["can_run_heavy_stages"])
    can_run_heavy = bool(strict_disk_ok and ram_ok)

    run_dir = art_base / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    watch_paths = [REPO_ROOT, APP_ROOT, run_dir, sentinel_tmp, Path(sys.executable).resolve().parent]
    sampler = _BoundedResourceSampler(
        out_gz=run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz",
        watch_path=run_dir,
        watch_paths=watch_paths,
        interval_sec=0.5,
        enforce_runtime_disk_abort=can_run_heavy,
    )
    sampler.start()
    sampler_stopped = False

    try:
        print(f"[KH-03.R1] Run ID: {run_id} | Branch: {branch} | Tested SHA: {start_head[:12]} | clean={git_clean}", flush=True)
        print(f"[KH-03.R1] Preflight Disk: min={strict_probe['min_free_gib']} GiB ({strict_probe['status']}) | RAM: {avail_ram_mib} MiB | can_run_heavy={can_run_heavy}", flush=True)

        # Stage 1: 01, 02, 03
        f01 = _audit_kh03_function_ownership_01(start_head)
        write_json_durable_atomic(run_dir / "01_file_function_ownership_and_keep_as_is_ast_diff.json", f01)
        f02 = _audit_prior_sealed_02()
        write_json_durable_atomic(run_dir / "02_prior_dataset_and_sealed_sha256_immutability.json", f02)
        write_json_durable_atomic(run_dir / "03_fee_root_bug_red_proof_and_diff.json", KH03_PRE_PATCH_FEE_RED_PROOF_9200C802)

        # Stage 2: Load canonical dataset & run P0 fee root reconciliation (04, 05) + Data Governance (06, 07, 08, 09)
        print("[KH-03.R1] Stage 1: Root-fee 6-scenario streaming ledger (04, 05) & Data Governance / Collector / Escrow (06..09)...", flush=True)
        ds_path = REPO_ROOT / CANONICAL_DATASET_REL_PATH
        datasets, _ = load_r2_canonical_dataset_readonly(ds_path)
        mtf_bundle = build_kh02_asof_mtf_features(datasets)
        r1_frozen_plan, _ = _r2_frozen_plan_and_arms(code_sha=start_head)
        r1_candidates = generate_kh02_research_candidates(mtf_bundle, r1_frozen_plan)
        ledger_bundle = build_r2_causal_unswept_level_ledger(
            datasets, source_latency_ms=0, warmup_m1=30, run_t10_future_mutation_proof=True, rng_seed=DEFAULT_RANDOM_SEED_KH02_R2
        )
        r2_candidates = build_r2_target_repair_full_opportunity_conservation(
            datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r1_candidates=r1_candidates, source_latency_ms=0
        )
        replay_bundle = replay_r2_same_opportunity_paired_counterfactuals(
            datasets=datasets, r1_candidates=r1_candidates, r2_candidates=r2_candidates
        )
        live_ff = reprice_r2_fixed_fills_cost_counterfactual(r2_replays=replay_bundle["r2_replays"])

        fee_ledger_gz = run_dir / "04_fee_per_episode_ledger.jsonl.gz"
        _f04_meta, f05 = build_kh03_six_scenario_fee_ledger_and_reconciliation(
            datasets=datasets,
            mtf_bundle=mtf_bundle,
            ledger_bundle=ledger_bundle,
            fixed_fill_doc=live_ff,
            out_episode_ledger_gz_path=fee_ledger_gz,
            prior_c4_red_proof=KH03_PRE_PATCH_FEE_RED_PROOF_9200C802,
        )
        write_json_durable_atomic(run_dir / "05_fee_full_universe_six_scenario_reconciliation.json", f05)

        f06 = build_kh03_source_contract_and_quality_inventory(
            datasets=datasets, dataset_path=ds_path, dataset_sha256=CANONICAL_DATASET_SHA256_KH02_R2, tested_code_sha=start_head
        )
        write_json_durable_atomic(run_dir / "06_data_source_contract_and_quality_inventory.json", f06)

        f07 = build_kh03_partition_manifest_and_noncontamination(
            repo_root=REPO_ROOT, datasets=datasets, dataset_sha256=CANONICAL_DATASET_SHA256_KH02_R2
        )
        write_json_durable_atomic(run_dir / "07_partition_freeze_pre_registration_and_noncontamination.json", f07)

        f08, f09 = audit_kh03_collector_and_escrow_readiness(sentinel_tmp)
        write_json_durable_atomic(run_dir / "08_public_collector_readiness_authorization_and_provenance.json", f08)
        write_json_durable_atomic(run_dir / "09_escrow_integrity_and_hash_linked_readiness.json", f09)

        # Stage 3: Pre-register KH-03 hypotheses BEFORE running ANY KH-03 outcome replay (10 -> 11..16)
        print("[KH-03.R1] Stage 2: Writing pre-registered KH-03 hypothesis manifest (10) & executing causal study (11..16)...", flush=True)
        f10 = preregister_kh03_research_plan(tested_code_sha=start_head, dataset_sha256=CANONICAL_DATASET_SHA256_KH02_R2)
        write_json_durable_atomic(run_dir / "10_kh03_frozen_hypotheses_with_falsification_budget.json", f10)

        study_docs = run_kh03_full_research_study_10_to_16(
            datasets=datasets,
            mtf_bundle=mtf_bundle,
            ledger_bundle=ledger_bundle,
            r1_candidates=r1_candidates,
            tested_code_sha=start_head,
            prereg_plan=f10,
            seed=DEFAULT_RANDOM_SEED_KH02_R2,
        )
        f11, f12, f13, f14, f15, f16 = (
            study_docs["11"], study_docs["12"], study_docs["13"],
            study_docs["14"], study_docs["15"], study_docs["16"],
        )
        write_json_durable_atomic(run_dir / "11_causal_asof_mtf_and_future_mutation_tests.json", f11)
        write_json_durable_atomic(run_dir / "12_full_opportunity_conservation_and_candidate_replay.json", f12)
        write_json_durable_atomic(run_dir / "13_paired_null_matching_and_balance.json", f13)
        write_json_durable_atomic(run_dir / "14_net_economics_cost_1x_2x_3x_stress.json", f14)
        write_json_durable_atomic(run_dir / "15_purged_walkforward_bootstrap_multiplicity_power.json", f15)
        write_json_durable_atomic(run_dir / "16_risk_tail_gap_fill_time_and_fee_disclosure.json", f16)

        # Build temp C4 tail ledger strictly for inheriting all 126 C4 fault controls inside Stage 3 (19)
        print("[KH-03.R1] Stage 3: Running 150 unique RED/GREEN fault controls (126 C4 + 24 KH-03) (19)...", flush=True)
        tmp_c4_gz = sentinel_tmp / "c4_tail_for_faults.jsonl.gz"
        tmp_c4_meta = build_c4_per_episode_risk_ledger(
            datasets=datasets, r2_replays=replay_bundle["r2_replays"], out_jsonl_gz_path=tmp_c4_gz
        )
        tmp_c4_rec = recompute_c4_tail_counters_from_episode_ledger(tmp_c4_gz)
        tmp_c4_f11 = build_c4_realized_tail_evidence_from_r2_replays(
            datasets=datasets, r2_replays=replay_bundle["r2_replays"], ledger_meta=tmp_c4_meta, ledger_recomputed=tmp_c4_rec
        )
        f19 = _run_kh03_150_red_green_fault_suite(sentinel_tmp, f05, fee_ledger_gz, tmp_c4_f11, tmp_c4_gz)
        write_json_durable_atomic(run_dir / "19_all_faults_unique_red_green_receipts.json", f19)

        del datasets, mtf_bundle, r1_candidates, r2_candidates, replay_bundle, ledger_bundle, live_ff
        gc.collect()

        # Stage 4: Separate-OS Verifier Mutation Resistance (14 attacks in temp clones) (21)
        print("[KH-03.R1] Stage 4: Running 14 separate-OS verifier mutation-resistance controls in temp clones (21)...", flush=True)
        f21 = _run_kh03_verifier_mutation_suite_21(sentinel_tmp)
        write_json_durable_atomic(run_dir / "21_independent_verifier_mutation_resistance.json", f21)

        # Stage 5: Heavy Stages (17 full pytest, 18 W1/W2/W4, 20 full 56,710 parity)
        if can_run_heavy:
            eff_res_b = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
            pytest_workers = min(3, eff_workers)
            print(f"[KH-03.R1] Stage 5A: Running FULL repo LPT-sharded pytest regression (max_workers={pytest_workers}) (17)...", flush=True)
            f17 = _run_kh03_full_repo_pytest_sharded(
                run_dir, sentinel_tmp, run_id=run_id, tested_sha=start_head,
                effective_disk_reserve_bytes=eff_res_b, max_workers=pytest_workers,
            )
            write_json_durable_atomic(run_dir / "17_full_regression_collected_executed_junit_bijection.json", f17)
            if git_clean:
                _git_cmd(["checkout", "--", "aurum-desk/backtest/trade_decision_lineage_reconciliation.json"])
            gc.collect()

            print("[KH-03.R1] Stage 5B: Running W1/W2/W4/W1_repeat pytest + R2 science slice determinism (18)...", flush=True)
            pb = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            sb = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            gc.collect()

            print("[KH-03.R1] Stage 5C: Running FULL 56,710 A1 + 6,375 SI8 + 242/166 SI0 + 246 adapter + D4 parity (20)...", flush=True)
            ds_p, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
            f02_r1 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
            f20 = _run_full_production_baseline_parity_20(
                datasets=ds_p, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head,
                branch=branch, f01=f01, f02=f02_r1, max_workers=eff_workers,
            )
            f20["spec_version"] = SPEC_VERSION_KH03
            write_json_durable_atomic(run_dir / "20_full_A1_SI8_246_adapter_D4_parity.json", f20)
            del ds_p
            gc.collect()
        else:
            f17 = {"spec_version": SPEC_VERSION_KH03, "status": "NOT_RUN_BLOCKED_LOW_DISK", "collected_count": 0, "executed_count": 0, "passed_count": 0, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": False}
            pb = {"all_4_runs_identical": False, "runs": []}
            sb = {"all_4_runs_identical": False, "runs": []}
            f20 = {"spec_version": SPEC_VERSION_KH03, "BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK", "a1_population_count": 0, "a1_mismatches_count": 0, "si8_population_count": 0, "si8_mismatches_count": 0, "three_adapter_population_count": 0, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": False}}
            write_json_durable_atomic(run_dir / "17_full_regression_collected_executed_junit_bijection.json", f17)
            write_json_durable_atomic(run_dir / "20_full_A1_SI8_246_adapter_D4_parity.json", f20)

        # Clean sentinel temp (Windows long-path safe!) BEFORE final telemetry flush
        cl_info = _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
        sentinel_cleaned = bool(cl_info["cleaned"])
        telem = sampler.stop_and_flush()
        sampler_stopped = True

        telem_block = {
            "orchestrator_pid": orch_pid,
            "sampling_interval_sec": 0.5,
            "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz",
            **telem,
            "strict_preflight_disk_min_gib": STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
            "strict_runtime_disk_min_gib": STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
            "advisory_disk_gib": ADVISORY_DISK_GIB_KH02_R2,
            "owner_disk_preflight_waiver": False,
            "sentinel_temp_cleaned_up": sentinel_cleaned,
            "total_wall_clock_sec": round(time.perf_counter() - t_start, 3),
        }

        f18 = {
            "spec_version": SPEC_VERSION_KH03,
            "schema_version": SCHEMA_VERSION_KH03,
            "pytest_controlled_subset_w1_w2_w4_repeat": pb,
            "r2_science_slice_w1_w2_w4_repeat": sb,
            "measured_pytest_speedup_w2_vs_w1": pb.get("measured_speedup_w2_vs_w1"),
            "measured_pytest_speedup_w4_vs_w1": pb.get("measured_speedup_w4_vs_w1"),
            "measured_science_speedup_w2_vs_w1": sb.get("measured_speedup_w2_vs_w1"),
            "measured_science_speedup_w4_vs_w1": sb.get("measured_speedup_w4_vs_w1"),
            "all_determinism_identical": bool(pb.get("all_4_runs_identical") and sb.get("all_4_runs_identical")),
            "runtime_resource_telemetry": telem_block,
            "status": (
                "NOT_RUN_BLOCKED_LOW_DISK"
                if not can_run_heavy
                else ("PASS" if (pb.get("all_4_runs_identical") and sb.get("all_4_runs_identical")) else "FAIL")
            ),
        }
        write_json_durable_atomic(run_dir / "18_w1_w2_w4_w1repeat_speed_hash_and_resource.json", f18)

        _, end_head = _git_cmd(["rev-parse", "HEAD"])
        f00 = {
            "spec_version": SPEC_VERSION_KH03,
            "schema_version": SCHEMA_VERSION_KH03,
            "run_id": run_id,
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "branch": branch,
            "start_head_sha": start_head,
            "end_head_sha": end_head,
            "tested_executable_sha": start_head,
            "remote_head_sha": remote_sha,
            "prior_kh02_r2_c4_pub_head_sha": PRIOR_KH02_R2_C4_PUB_HEAD_SHA,
            "prior_kh02_r2_c4_tested_sha": PRIOR_KH02_R2_C4_TESTED_SHA,
            "prior_kh02_r2_c4_evidence_sha": PRIOR_KH02_R2_C4_EVIDENCE_SHA,
            "prior_kh02_r2_c4_run_id": PRIOR_KH02_R2_C4_RUN_ID,
            "git_clean_at_start": git_clean,
            "local_uncommitted_changes": not git_clean,
            "orchestrator_pid": orch_pid,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "random_seed": DEFAULT_RANDOM_SEED_KH02_R2,
            "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "owner_disk_preflight_waiver": False,
            "strict_multi_mount_probe": strict_probe,
            "preflight_ram_available_mib": avail_ram_mib,
            "can_run_heavy_stages": can_run_heavy,
            "readonly_disk_inventory": build_r2_c1_readonly_disk_inventory(REPO_ROOT) if not strict_disk_ok else None,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "status": "PASS" if (strict_disk_ok and ram_ok and git_clean) else ("BLOCKED_LOW_DISK" if not strict_disk_ok else "BLOCKED_PREFLIGHT"),
        }
        write_json_durable_atomic(run_dir / "00_source_branch_sha_and_resource_preflight.json", f00)

        pub_st = "PRE_PUSH_PENDING" if (strict_disk_ok and git_clean) else "NOT_PUBLISHED_BLOCKED_LOW_DISK"
        f23 = {
            "spec_version": SPEC_VERSION_KH03,
            "run_id": run_id,
            "branch": branch,
            "start_head_sha": start_head,
            "tested_executable_sha": start_head,
            "evidence_commit_sha": None,
            "publication_head_sha": None,
            "remote_readback_sha256_verified": False,
            "latest_run_pointer_updated": False,
            "owner_disk_preflight_waiver": False,
            "publication_status": pub_st,
        }
        write_json_durable_atomic(run_dir / "23_phase1_code_commit_push_receipt.json", f23)

        docs_all = {
            "00": f00, "01": f01, "02": f02, "03": KH03_PRE_PATCH_FEE_RED_PROOF_9200C802,
            "05": f05, "06": f06, "07": f07, "08": f08, "09": f09, "10": f10,
            "11": f11, "12": f12, "13": f13, "14": f14, "15": f15, "16": f16,
            "17": f17, "18": f18, "19": f19, "20": f20, "21": f21, "23": f23,
        }
        mem_disk_aud = _audit_memory_samples_c3(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz")
        fee_disk_aud = validate_kh03_fee_ledger_independently(fee_ledger_gz, f05)
        recomp22 = recompute_kh03_24_gates_and_verdicts(
            docs=docs_all,
            mem_audit=mem_disk_aud,
            fee_ledger_audit=fee_disk_aud,
            junit_bijection_ok=(f17.get("status") == "PASS" and bool(f17.get("junit_bijection_exact", False))),
            for_final_verifier_manifest=False,
        )
        f22 = {
            "spec_version": SPEC_VERSION_KH03,
            "schema_version": SCHEMA_VERSION_KH03,
            "run_id": run_id,
            "tested_executable_sha": start_head,
            "OOS_EVALUATIONS": 0,
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "overall_classification": recomp22["overall_classification"],
            "gates_24": recomp22["gates_24"],
            "verdicts": recomp22["verdicts"],
        }
        f22["artifact_canonical_sha256"] = canonical_hash(f22)
        write_json_durable_atomic(run_dir / "22_orthogonal_gate_matrix_prepush.json", f22)

        readme_md, handover_vi = _render_kh03_readme_and_handover(
            run_id=run_id, run_dir=run_dir, f00=f00, f05=f05, f15=f15,
            f17=f17, f18=f18, f19=f19, f20=f20, f21=f21, f22=f22, f23=f23,
        )
        (run_dir / "README_KH03_R1.md").write_text(readme_md, encoding="utf-8")
        (run_dir / "HANDOVER_KH03_R1_VI.txt").write_text(handover_vi, encoding="utf-8")

        # Stage 6: Separate OS process independent verifier sealing 24
        print("[KH-03.R1] Stage 6: Launching separate-OS-process independent disk verifier (24)...", flush=True)
        verifier_script = APP_ROOT / "tools" / "kh_03_independent_verifier.py"
        env_v = os.environ.copy()
        env_v["PYTHONPATH"] = _pypath_str()
        v_proc = subprocess.run(
            [sys.executable, str(verifier_script), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", start_head],
            cwd=str(APP_ROOT), env=env_v, capture_output=True, text=True, timeout=240,
        )
        print(v_proc.stdout, flush=True)
        if v_proc.stderr:
            print(v_proc.stderr, file=sys.stderr, flush=True)

        f24 = json.loads((run_dir / "24_independent_os_verifier_manifest_sha256.json").read_text("utf-8"))
        return int(v_proc.returncode), run_dir, {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "verifier_exit_code": int(v_proc.returncode),
            "independent_verifier_status": f24.get("independent_verifier_status"),
            "overall_classification": f24.get("overall_classification"),
            "recomputed_verdicts": f24.get("recomputed_verdicts"),
            "violations": f24.get("violations"),
        }
    finally:
        if not sampler_stopped:
            try:
                sampler.stop_and_flush()
            except Exception:
                pass
        if sentinel_tmp.exists():
            try:
                _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
            except Exception:
                pass


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK KH-03.R1 Acceptance Orchestrator")
    ap.add_argument("--allow-local-dirty", action="store_true")
    ap.add_argument("--owner-waive-disk-preflight", action="store_true")
    ap.add_argument("--max-workers", type=int, default=GLOBAL_MAX_DESCENDANTS_KH02_R2)
    args = ap.parse_args(argv)
    if args.owner_waive_disk_preflight:
        return 2
    rc, _rdir, summary = run_kh03_acceptance(
        allow_local_dirty=args.allow_local_dirty,
        owner_waive_disk_preflight=False,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
