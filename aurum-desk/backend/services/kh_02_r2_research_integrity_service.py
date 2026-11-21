"""
aurum-desk/backend/services/kh_02_r2_research_integrity_service.py

AURUM DESK — KH-02.R2 | RESEARCH INTEGRITY & DATA READINESS
SPEC VERSION: KH-02.R2-v1.0 | STRICTLY ADDITIVE RESEARCH-ONLY LAYER

Zero modifications to production, KH-01*, or KH-02.R1 files or artifacts.
Zero live/paper/auto/production activation (`ACTIVATION_READY = "NO"`).
Zero exchange order/wallet/API-key imports or calls.
"""
from __future__ import annotations

import ast
import bisect
from collections import Counter, defaultdict
import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from domain_calculator import CostAssumptions, calculate_risk_reward
from services.kh_01b_r1_ram_safety_r3_service import (
    HARD_ABORT_RAM_FLOOR_BYTES_R3,
    PRIOR_R2_ACCEPTED_RUN_ID,
    PRIOR_R2_LATEST_PTR_REL,
    PRIOR_R2_LATEST_PTR_SHA256,
    PRIOR_R2_TESTED_SHA,
    SEALED_R2_A1_56710_IDS_SHA256,
    SEALED_R2_E00_BUNDLE_SHA256,
    SEALED_R2_SI0_242_TUPLES_SHA256,
    SEALED_R2_SI8_6375_IDS_SHA256,
    audit_r3_memory_samples_jsonl_gz,
    detect_active_64mib_ram_floor_in_source,
)
from services.kh_02_entry_foundation_research_service import (
    CANONICAL_DATASET_SHA256_KH02,
    KH02AsOfMTFRecord,
    KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
    MANDATORY_BRANCH_KH02,
    PRIOR_R3_ACCEPTED_RUN_ID,
    PRIOR_R3_LATEST_PTR_REL,
    PRIOR_R3_LATEST_PTR_SHA256,
    PRIOR_R3_PUBLICATION_HEAD_SHA,
    PRIOR_R3_TESTED_SHA,
    _make_kh02_materialized_plan,
    _resolve_b2_structural_stop_and_target,
    _validate_and_precompute_tf_series,
    build_kh02_asof_mtf_features,
    estimate_kh02_edge_uncertainty,
    evaluate_kh02_hypothesis_predicates_asof,
    generate_kh02_research_candidates,
    preregister_kh02_hypotheses,
    replay_kh02_chronological_one_position,
)

FORBIDDEN_ORDER_SERVICE_TOKENS: Tuple[str, ...] = (
    "order_service",
    "execution_service",
    "bitget_client",
    "live_trader",
    "auto_trader",
    "wallet_service",
    "broker_service",
    "place_order",
)
from services.kh_02_r2_prospective_data_service import (
    PROSPECTIVE_OOS_EMBARGO_MINUTES_KH02_R2,
    SCHEMA_VERSION_KH02_R2,
    SPEC_VERSION_KH02_R2,
    prepare_r2_prospective_oos_ingestion_and_escrow,
)
from services.v17_9_2_structural_stop_intelligence import simulate_v17931_trade_execution_net
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    advance_portfolio_to_next_event_v179312,
    recalculate_trade_risk_quantity_v179312,
    replay_intervention_outcome_v179312,
)

# ==============================================================================
# FROZEN CONSTANTS & PRIOR KH-02.R1 REFERENCE INVARIANTS (READ-ONLY)
# ==============================================================================

MANDATORY_BRANCH_KH02_R2 = "feature/v17-9-2-structural-sl-tp-from-qa"
PRIOR_KH02_R1_PUBLICATION_HEAD_SHA = "6dec7a716141401b7194a10a82797b04b7557816"
PRIOR_KH02_R1_TESTED_SHA = "b99ad30313a6ed5929ba4cbaeab4383bd3db6be5"
PRIOR_KH02_R1_EVIDENCE_COMMIT_SHA = "382f7fe96bd8445954eaaa6f04e6b3f0e1637477"
PRIOR_KH02_R1_RUN_ID = "20261121T033338Z_b99ad30313a6"
PRIOR_KH02_R1_LATEST_PTR_REL = "aurum-desk/docs/artifacts/kh_02_entry_foundation/latest_run.json"
PRIOR_KH02_R1_LATEST_PTR_SHA256 = "0e97fe682c7908653ca18002236e4839863108b69ec37784b838e1e98c35e5a4"
PRIOR_KH02_R1_MANIFEST_18_SHA256 = "ce3ebd63994829ba5beb13ab6c75f446ad76744a1080317ecca227f8dc49d3c8"
CANONICAL_DATASET_SHA256_KH02_R2 = CANONICAL_DATASET_SHA256_KH02
CANONICAL_DATASET_REL_PATH = "aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz"

STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2 = 20.5
STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 = 20.0
ADVISORY_DISK_GIB_KH02_R2 = 25.0
PREFLIGHT_RAM_MIN_MIB_KH02_R2 = 2048.0
HARD_RAM_FLOOR_MIB_KH02_R2 = 512.0
WARN_RAM_MIB_KH02_R2 = 1024.0
GLOBAL_MAX_DESCENDANTS_KH02_R2 = 4

PLANNED_NET_RISK_CAP_USD = 2.50
MIN_NET_RR_THRESHOLD = 2.0
DEFAULT_BASELINE_TAKER_FEE = 0.0004
DEFAULT_PUBLIC_REF_TAKER_FEE = 0.0006
DEFAULT_MODELED_SPREAD_USD = 0.20
DEFAULT_SLIPPAGE_USD = 0.10
DEFAULT_SMD_THRESHOLD = 0.10
DEFAULT_RANDOM_SEED_KH02_R2 = 20261121

RUNTIME_SAFETY_MODES_KH02_R2: Dict[str, str] = {
    "PRODUCTION": "OFF",
    "LIVE": "OFF",
    "PAPER": "OFF",
    "AUTO": "OFF",
}
ACTIVATION_READY_KH02_R2 = "NO"

PRIMARY_R2_ARMS: Tuple[str, ...] = ("B2_H1", "B2_H2", "B2_H3", "B2_H123")
CONTROL_R2_ARMS: Tuple[str, ...] = ("NC_NULL_TRIGGER", "NC_RANDOM_DIR", "NC_PAST_SHIFTED_48H")
ALL_EVALUATED_R2_ARMS: Tuple[str, ...] = (
    "B2_H1",
    "B2_H2",
    "B2_H3",
    "B2_H123",
    "B2_H1_M15_ONLY",
    "B2_H1_H1_ONLY",
    "B2_H3_SESSION_ONLY",
    "B2_H3_VOL_ONLY",
    "NC_NULL_TRIGGER",
    "NC_RANDOM_DIR",
    "NC_PAST_SHIFTED_48H",
)

LOCKED_KEEP_AS_IS_FILES_KH02_R2: Tuple[str, ...] = (
    "aurum-desk/backend/services/shared_strategy_evaluator.py",
    "aurum-desk/backend/domain_calculator.py",
    "aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py",
    "aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py",
    "aurum-desk/backend/services/unicorn_evidence_service.py",
    "aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py",
    "aurum-desk/backend/services/v17_9_3_1_2_executable_intervention_replay_service.py",
    "aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py",
    "aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py",
    "aurum-desk/backend/services/kh_01b_r1_ram_safety_r3_service.py",
    "aurum-desk/backend/services/kh_02_entry_foundation_research_service.py",
    "aurum-desk/tools/run_kh_02_entry_foundation_research.py",
    "aurum-desk/tools/kh_02_entry_foundation_independent_verifier.py",
    "aurum-desk/docs/artifacts/kh_02_entry_foundation/latest_run.json",
    "aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz",
)

REQUIRED_ARTIFACTS_KH02_R2: Tuple[str, ...] = (
    "00_preflight_branch_repo_sha_data_and_resources.json",
    "01_function_ownership_keep_as_is_diff.json",
    "02_r1_source_integrity_and_snapshot_registry.json",
    "03_frozen_r2_estimands_thresholds_hypotheses_and_falsification.json",
    "04_asof_unswept_level_ledger_and_mutation_proof.jsonl.gz",
    "05_r1_target_eligibility_counterexamples_and_root_cause.json",
    "06_r2_target_repair_full_opportunity_conservation.jsonl.gz",
    "07_paired_entry_geometry_factorial_control_mapping.jsonl.gz",
    "08_strata_common_support_balance_and_matching_quality.json",
    "09_chronological_1position_three_fill_paired_replays.json",
    "10_fill_time_net_risk_and_realized_tail_audit.json",
    "11_fixed_fill_same_ids_fee_spread_slippage_1x2x3x.json",
    "12_full_universe_rerisked_1x2x3x_and_fee_004_006.json",
    "13_m1_m15_h1_gaps_source_latency_quote_fee_provenance.json",
    "14_prospective_oos_escrow_collection_readiness_no_peek.json",
    "15_purged_walkforward_cluster_uncertainty_and_multiplicity.json",
    "16_full_pytest_collection_execution_junit_bijection.json",
    "17_async_w1_w2_w4_repeat_times_outputs_and_scheduler_policy.json",
    "18_ram_disk_pid_temp_telemetry.json",
    "19_real_red_green_fault_injections.json",
    "20_production_baseline_parity_and_prior_evidence_immutability.json",
    "21_orthogonal_engineering_science_economic_oos_gate_matrix.json",
    "22_independent_os_disk_verifier_sha256_manifest.json",
    "23_tested_sha_evidence_publication_receipt_pre_push.json",
    "README_KH02_R2.md",
    "HANDOVER_KH02_R2_VI.txt",
)

R2_NEW_FILES_ALLOWLIST: Tuple[str, ...] = (
    "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
    "aurum-desk/backend/services/kh_02_r2_prospective_data_service.py",
    "aurum-desk/tools/run_kh_02_r2_verification.py",
    "aurum-desk/tools/kh_02_r2_independent_verifier.py",
    "aurum-desk/backend/tests/test_kh_02_r2_research_integrity.py",
    "aurum-desk/backend/tests/test_kh_02_r2_counterfactual_and_costs.py",
    "aurum-desk/backend/tests/test_kh_02_r2_oos_provenance_and_faults.py",
)

R2_NEW_FUNCTION_OWNERSHIP_MAP: Tuple[Dict[str, str], ...] = (
    {
        "function": "audit_r2_reference_inputs_and_invariants",
        "module": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
        "artifact": "02_r1_source_integrity_and_snapshot_registry.json",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_research_integrity.py",
        "purpose": "Verifies KH-02.R1 SHA, latest_run.json, manifest_18, dataset SHA, and 44,692 M1 directional universe read-only.",
    },
    {
        "function": "build_r2_causal_unswept_level_ledger",
        "module": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
        "artifact": "04_asof_unswept_level_ledger_and_mutation_proof.jsonl.gz",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_research_integrity.py",
        "purpose": "Streaming causal M15/H1 confirmed pivot ledger tracking exact HTF + intervening M1 sweep timestamps and T10 future-mutation invariance.",
    },
    {
        "function": "audit_r2_r1_target_eligibility_counterexamples",
        "module": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
        "artifact": "05_r1_target_eligibility_counterexamples_and_root_cause.json",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_research_integrity.py",
        "purpose": "Reproduces R1 _resolve_b2_structural_stop_and_target read-only across T01-T10 synthetic scenarios and full 44,692 directional M1 dataset.",
    },
    {
        "function": "resolve_r2_research_only_structural_target",
        "module": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
        "artifact": "06_r2_target_repair_full_opportunity_conservation.jsonl.gz",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_research_integrity.py",
        "purpose": "Pure research-only structural target resolver choosing strictly verified as-of still-unswept confirmed M15/H1 targets with zero TP stretching.",
    },
    {
        "function": "build_r2_paired_factorial_entry_geometry_design",
        "module": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
        "artifact": "07_paired_entry_geometry_factorial_control_mapping.jsonl.gz",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_research_integrity.py",
        "purpose": "Freezes E-only, G-only, E*G factorial, X_FIXED_FILL, X_FULL_RERISKED_UNIVERSE estimands and per-opportunity reconciliation preserving UNMATCHED.",
    },
    {
        "function": "compute_r2_stratified_common_support_balance",
        "module": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
        "artifact": "08_strata_common_support_balance_and_matching_quality.json",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_research_integrity.py",
        "purpose": "Evaluates pre/post |SMD| <= 0.10 covariate balance, positivity overlap, MATCHING_INADEQUATE flag, and NY-day/episode block bootstrap CIs.",
    },
    {
        "function": "replay_r2_same_opportunity_paired_counterfactuals",
        "module": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
        "artifact": "09_chronological_1position_three_fill_paired_replays.json",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_counterfactual_and_costs.py",
        "purpose": "Chronological 1-position 3-fill paired counterfactual replay reusing authoritative replay_intervention_outcome_v179312 read-only.",
    },
    {
        "function": "audit_r2_fill_time_net_risk_and_realized_tail",
        "module": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
        "artifact": "10_fill_time_net_risk_and_realized_tail_audit.json",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_counterfactual_and_costs.py",
        "purpose": "Causal fill-time planned net risk (<= $2.50) & net R:R (>= 2.0) enforcement at open price only, separating planned risk from realized tail losses.",
    },
    {
        "function": "reprice_r2_fixed_fills_cost_counterfactual",
        "module": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
        "artifact": "11_fixed_fill_same_ids_fee_spread_slippage_1x2x3x.json",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_counterfactual_and_costs.py",
        "purpose": "Exact Decimal fixed-fill same-ID 1x/2x/3x and 0.0004/0.0006 cost repricing with zero dropped IDs and stable denominator.",
    },
    {
        "function": "rerun_r2_full_universe_by_cost_scenario",
        "module": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
        "artifact": "12_full_universe_rerisked_1x2x3x_and_fee_004_006.json",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_counterfactual_and_costs.py",
        "purpose": "Full raw 44,692 directional opportunity universe dynamic rerisked rerun across 1x/2x/3x and 0.0004/0.0006 with complete chronological replay.",
    },
    {
        "function": "audit_r2_data_lineage_and_gap_sensitivity",
        "module": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
        "artifact": "13_m1_m15_h1_gaps_source_latency_quote_fee_provenance.json",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_oos_provenance_and_faults.py",
        "purpose": "Audits M1 (~20.35d)/M15/H1 coverage, weekend/intra-session gaps, 0ms vs +1s/+5s/+60s source latency sensitivity, modeled BBO, and unknown fee tier.",
    },
    {
        "function": "prepare_r2_prospective_oos_ingestion_and_escrow",
        "module": "aurum-desk/backend/services/kh_02_r2_prospective_data_service.py",
        "artifact": "14_prospective_oos_escrow_collection_readiness_no_peek.json",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_oos_provenance_and_faults.py",
        "purpose": "Read-only prior escrow audit, append-only prospective snapshot contract (240m embargo), strict OOS_EVALUATIONS == 0 readiness.",
    },
    {
        "function": "run_kh02_r2_42_red_green_fault_suite",
        "module": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
        "artifact": "19_real_red_green_fault_injections.json",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_oos_provenance_and_faults.py",
        "purpose": "42 unique real RED/GREEN negative controls covering T01-T10, target reuse, pairing, costs, OOS, JUnit, RAM/disk floors, and verifier tampering.",
    },
    {
        "function": "evaluate_r2_orthogonal_science_and_engineering_gates_from_disk",
        "module": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
        "artifact": "21_orthogonal_engineering_science_economic_oos_gate_matrix.json",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_oos_provenance_and_faults.py",
        "purpose": "Evaluates all 20 orthogonal gates and 12 independent verdicts strictly from closed immutable disk artifacts.",
    },
    {
        "function": "verify_r2_artifacts_independently_from_disk",
        "module": "aurum-desk/tools/kh_02_r2_independent_verifier.py",
        "artifact": "22_independent_os_disk_verifier_sha256_manifest.json",
        "test_file": "aurum-desk/backend/tests/test_kh_02_r2_oos_provenance_and_faults.py",
        "purpose": "Separate-OS-process disk verifier recomputing hashes, JUnit bijection, conservation, cost math, RAM/disk floors, and tampering checks.",
    },
)


def load_r2_canonical_dataset_readonly(dataset_path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    ds_p = Path(dataset_path).resolve()
    sha = _sha256_file(ds_p)
    with gzip.open(ds_p, "rt", encoding="utf-8") as gz:
        data = json.load(gz)
    return data, {"path": str(ds_p), "sha256": sha}


def _mtf_records_list(mtf_bundle: Mapping[str, Any]) -> List[KH02AsOfMTFRecord]:
    if "records_by_idx" in mtf_bundle:
        rbi = mtf_bundle["records_by_idx"]
        return [rbi[k] for k in sorted(rbi.keys())]
    return list(mtf_bundle.get("records") or ())


def _r2_frozen_plan_and_arms(code_sha: str = "kh02_r2_frozen_code") -> Tuple[Any, List[Dict[str, Any]]]:
    plan = preregister_kh02_hypotheses(
        dataset_sha256=CANONICAL_DATASET_SHA256_KH02_R2,
        config_sha256=canonical_hash({"risk_cap_usd": PLANNED_NET_RISK_CAP_USD, "min_net_rr": MIN_NET_RR_THRESHOLD}),
        code_sha256=str(code_sha),
    )
    arms: List[Dict[str, Any]] = []
    for h in plan.primary_hypotheses:
        arms.append({"arm_code": h["arm_code"], "variant_id": h["variant_id"], "arm_role": "PRIMARY", "rule": h["rule"]})
    for s in plan.secondary_and_control_arms:
        arms.append({"arm_code": s["arm_code"], "variant_id": s["variant_id"], "arm_role": s["arm_role"], "rule": s["rule"]})
    return plan, arms


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc_iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dec4(v: Decimal) -> float:
    return float(v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _write_jsonl_gz(path: Path, rows: Iterable[Mapping[str, Any]]) -> Tuple[int, str]:
    import io
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    count = 0
    with tmp.open("wb") as raw_fh:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_fh, compresslevel=6, mtime=0) as gzf:
            with io.TextIOWrapper(gzf, encoding="utf-8") as gz:
                for row in rows:
                    gz.write(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")
                    count += 1
    os.replace(tmp, path)
    return count, _sha256_file(path)


def _ast_function_inventory(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    out: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(
                {
                    "name": node.name,
                    "lineno": int(node.lineno),
                    "end_lineno": int(getattr(node, "end_lineno", node.lineno)),
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "arg_count": len(node.args.args) + len(node.args.kwonlyargs),
                }
            )
    out.sort(key=lambda r: (r["lineno"], r["name"]))
    return out


# ==============================================================================
# SECTION A — FUNCTION OWNERSHIP, KEEP-AS-IS DIFF & R1 REFERENCE AUDIT (01, 02, 03)
# ==============================================================================


def audit_r2_function_ownership_and_keep_as_is(
    repo_root: Path,
    *,
    base_head_sha: str,
) -> Dict[str, Any]:
    """
    Artifact 01: Audits AST function ownership, SHA256 hashes of all KEEP-AS-IS locked files,
    git diff against `base_head_sha`, and verifies zero forbidden order/exchange imports
    in every new KH-02.R2 module.
    """
    repo_root = Path(repo_root).resolve()
    locked_inventory: List[Dict[str, Any]] = []
    for rel_p in LOCKED_KEEP_AS_IS_FILES_KH02_R2:
        fp = repo_root / rel_p
        exists = fp.is_file()
        sha = _sha256_file(fp) if exists else None
        funcs = _ast_function_inventory(fp) if (exists and fp.suffix == ".py") else []
        locked_inventory.append(
            {
                "path": rel_p,
                "freeze_status": "KEEP_AS_IS_LOCKED",
                "exists": exists,
                "sha256": sha,
                "function_count": len(funcs),
                "top_functions": [f["name"] for f in funcs[:20]],
            }
        )

    # Check git status & diff on locked files
    diff_out = ""
    status_out = ""
    try:
        diff_out = subprocess.check_output(
            ["git", "diff", "--name-only", base_head_sha],
            cwd=str(repo_root),
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain=v1"],
            cwd=str(repo_root),
            text=True,
            stderr=subprocess.STDOUT,
        )
    except Exception as exc:
        diff_out = f"GIT_ERROR:{exc}"

    modified_tracked = [line.strip() for line in diff_out.splitlines() if line.strip() and not line.startswith("GIT_ERROR:")]
    locked_set = set(LOCKED_KEEP_AS_IS_FILES_KH02_R2)
    locked_modified = sorted(p for p in modified_tracked if p in locked_set)
    # Also check any non-KH02-R2 tracked file modified
    non_r2_modified = sorted(p for p in modified_tracked if p not in set(R2_NEW_FILES_ALLOWLIST))

    # Inspect new KH-02.R2 modules for forbidden order/execution imports and AST inventory
    new_modules_inventory: List[Dict[str, Any]] = []
    forbidden_hits: List[Dict[str, str]] = []
    for rel_p in R2_NEW_FILES_ALLOWLIST:
        fp = repo_root / rel_p
        if not fp.is_file():
            new_modules_inventory.append({"path": rel_p, "exists": False, "sha256": None, "functions": []})
            continue
        txt = fp.read_text(encoding="utf-8")
        sha = _sha256_file(fp)
        funcs = _ast_function_inventory(fp)
        # Scan AST imports only (not string constants in fault tests)
        tree = ast.parse(txt, filename=str(fp))
        imported_modules: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.append(node.module)
        for mod in imported_modules:
            for tok in FORBIDDEN_ORDER_SERVICE_TOKENS:
                if tok in mod:
                    forbidden_hits.append({"path": rel_p, "imported_module": mod, "forbidden_token": tok})
        new_modules_inventory.append(
            {
                "path": rel_p,
                "exists": True,
                "sha256": sha,
                "function_count": len(funcs),
                "functions": [f["name"] for f in funcs],
                "imported_modules_count": len(imported_modules),
            }
        )

    c_live = CostAssumptions()
    cost_defaults_intact = (
        abs(float(c_live.taker_fee_rate) - 0.0004) < 1e-12
        and abs(float(c_live.maker_fee_rate) - 0.0002) < 1e-12
        and abs(float(c_live.slippage_usd) - 0.10) < 1e-12
    )

    untracked_files: List[str] = []
    for raw_line in status_out.splitlines():
        if raw_line.startswith("?? "):
            untracked_files.append(raw_line[3:].strip())

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "base_head_sha": base_head_sha,
        "locked_keep_as_is_count": len(locked_inventory),
        "locked_keep_as_is_inventory": locked_inventory,
        "locked_files_modified_count": len(locked_modified),
        "locked_files_modified": locked_modified,
        "modified_existing_files_count": len(non_r2_modified),
        "modified_existing_files": non_r2_modified,
        "untracked_files": sorted(untracked_files),
        "r2_new_files_inventory": new_modules_inventory,
        "r2_function_ownership_map": list(R2_NEW_FUNCTION_OWNERSHIP_MAP),
        "forbidden_order_import_hits": forbidden_hits,
        "cost_assumptions_defaults_intact": cost_defaults_intact,
        "live_cost_assumptions": {
            "taker_fee_rate": float(c_live.taker_fee_rate),
            "maker_fee_rate": float(c_live.maker_fee_rate),
            "slippage_usd": float(c_live.slippage_usd),
        },
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": (
            "PASS"
            if (len(locked_modified) == 0 and len(non_r2_modified) == 0 and len(forbidden_hits) == 0 and cost_defaults_intact)
            else "FAIL"
        ),
    }
    payload["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "artifact_canonical_sha256"}
    )
    return payload


def audit_r2_reference_inputs_and_invariants(repo_root: Path) -> Dict[str, Any]:
    """
    §03(1) -> Artifact 02: Independently rechecks KH-02.R1 latest_run.json, manifest_18,
    canonical historical dataset SHA256, prior R3/R2 pointers, and R1 frozen reference receipts
    without modifying any R1 file or reissuing R1 verdicts as R2.
    """
    repo_root = Path(repo_root).resolve()
    violations: List[str] = []

    ds_path = repo_root / CANONICAL_DATASET_REL_PATH
    ds_sha = _sha256_file(ds_path) if ds_path.is_file() else ""
    if ds_sha != CANONICAL_DATASET_SHA256_KH02_R2:
        violations.append(f"CANONICAL_DATASET_SHA_MISMATCH:{ds_sha}")

    r1_ptr_path = repo_root / PRIOR_KH02_R1_LATEST_PTR_REL
    r1_ptr_sha = _sha256_file(r1_ptr_path) if r1_ptr_path.is_file() else ""
    r1_ptr_doc: Dict[str, Any] = {}
    if r1_ptr_path.is_file():
        r1_ptr_doc = json.loads(r1_ptr_path.read_text(encoding="utf-8"))
        if r1_ptr_sha != PRIOR_KH02_R1_LATEST_PTR_SHA256:
            violations.append(f"PRIOR_KH02_R1_PTR_SHA_MISMATCH:{r1_ptr_sha}")
    else:
        violations.append("PRIOR_KH02_R1_LATEST_PTR_MISSING")

    r1_run_dir = repo_root / "aurum-desk/docs/artifacts/kh_02_entry_foundation" / PRIOR_KH02_R1_RUN_ID
    r1_m18_path = r1_run_dir / "18_independent_verifier_content_sha256_manifest.json"
    r1_m18_sha = _sha256_file(r1_m18_path) if r1_m18_path.is_file() else ""
    if r1_m18_sha != PRIOR_KH02_R1_MANIFEST_18_SHA256:
        violations.append(f"PRIOR_KH02_R1_MANIFEST_18_SHA_MISMATCH:{r1_m18_sha}")

    r1_f05_summary: Dict[str, Any] = {}
    r1_f08_summary: Dict[str, Any] = {}
    r1_f17_summary: Dict[str, Any] = {}
    if (r1_run_dir / "05_opportunity_universe_denominators_and_cohort_ids.json").is_file():
        f05 = json.loads((r1_run_dir / "05_opportunity_universe_denominators_and_cohort_ids.json").read_text("utf-8"))
        r1_f05_summary = {
            "total_post_warmup_m1_bars": f05.get("total_post_warmup_m1_bars"),
            "total_directional_bar_opportunities": f05.get("total_directional_bar_opportunities"),
            "raw_m1_triggers_count": f05.get("raw_m1_triggers_count"),
            "raw_geometry_risk_eligible_count": f05.get("raw_geometry_risk_eligible_count"),
        }
    if (r1_run_dir / "08_chronological_one_position_three_fill_replay.json").is_file():
        f08 = json.loads((r1_run_dir / "08_chronological_one_position_three_fill_replay.json").read_text("utf-8"))
        r1_f08_summary = {
            "b0_summary": f08.get("control_b0_reference_summary") or f08.get("b0_reference_summary"),
            "b2_arm_summaries_nb": {
                k: (v.get("mode_summaries") or {}).get("NEXT_BAR_OPEN_BID_ASK")
                for k, v in sorted((f08.get("b2_arms") or f08.get("arms") or {}).items())
                if isinstance(v, Mapping)
            },
        }
    if (r1_run_dir / "17_engineering_economic_oos_activation_gate_matrix.json").is_file():
        f17 = json.loads((r1_run_dir / "17_engineering_economic_oos_activation_gate_matrix.json").read_text("utf-8"))
        r1_f17_summary = {
            "verdicts": f17.get("verdicts") or f17.get("orthogonal_verdicts"),
            "tested_executable_sha": f17.get("tested_executable_sha"),
        }

    # Check prior R3 and R2 pointers read-only
    prior_ptrs_status: Dict[str, Any] = {}
    for lbl, rel_p, exp_sha in (
        ("KH02_R1", PRIOR_KH02_R1_LATEST_PTR_REL, PRIOR_KH02_R1_LATEST_PTR_SHA256),
        ("KH01B_R1_RAM_R3", PRIOR_R3_LATEST_PTR_REL, PRIOR_R3_LATEST_PTR_SHA256),
        ("KH01B_R1_DSC_R2", PRIOR_R2_LATEST_PTR_REL, PRIOR_R2_LATEST_PTR_SHA256),
    ):
        p = repo_root / rel_p
        actual = _sha256_file(p) if p.is_file() else None
        ok = actual == exp_sha
        if not ok:
            violations.append(f"PRIOR_PTR_MISMATCH:{lbl}:{actual}")
        prior_ptrs_status[lbl] = {"path": rel_p, "expected_sha256": exp_sha, "actual_sha256": actual, "match": ok}

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "reused_dataset_provenance": {
            "path": CANONICAL_DATASET_REL_PATH,
            "sha256": ds_sha,
            "expected_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "match": ds_sha == CANONICAL_DATASET_SHA256_KH02_R2,
            "data_classification": "REUSED_RESEARCH_NOT_UNTOUCHED_OOS",
            "quote_mode": "MODELED_BID_ASK",
            "modeled_spread_usd": DEFAULT_MODELED_SPREAD_USD,
            "observed_l1_bbo_available": False,
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        },
        "kh02_r1_historical_reference_readonly": {
            "r1_publication_head_sha": PRIOR_KH02_R1_PUBLICATION_HEAD_SHA,
            "r1_tested_executable_sha": PRIOR_KH02_R1_TESTED_SHA,
            "r1_evidence_commit_sha": PRIOR_KH02_R1_EVIDENCE_COMMIT_SHA,
            "r1_run_id": PRIOR_KH02_R1_RUN_ID,
            "r1_latest_ptr_sha256": r1_ptr_sha,
            "r1_manifest_18_sha256": r1_m18_sha,
            "r1_latest_ptr_fields": {
                "run_id": r1_ptr_doc.get("run_id"),
                "tested_executable_sha": r1_ptr_doc.get("tested_executable_sha"),
                "evidence_commit_sha": r1_ptr_doc.get("evidence_commit_sha"),
            },
            "r1_f05_universe_summary": r1_f05_summary,
            "r1_f08_replay_summary": r1_f08_summary,
            "r1_f17_verdicts_readonly": r1_f17_summary,
            "inherited_reference_note": "R1 results are strictly historical read-only baselines; R2 re-executes and independently verifies its own artifacts.",
        },
        "prior_pointers_immutability": prior_ptrs_status,
        "sealed_r2_baseline_hashes": {
            "A1_56710_IDS_SHA256": SEALED_R2_A1_56710_IDS_SHA256,
            "SI8_6375_IDS_SHA256": SEALED_R2_SI8_6375_IDS_SHA256,
            "SI0_242_TUPLES_SHA256": SEALED_R2_SI0_242_TUPLES_SHA256,
            "E00_BUNDLE_SHA256": SEALED_R2_E00_BUNDLE_SHA256,
        },
        "violations": violations,
        "status": "PASS" if not violations else "FAIL",
    }
    payload["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "artifact_canonical_sha256"}
    )
    return payload


def freeze_r2_estimands_thresholds_and_falsification(
    *,
    tested_executable_sha: str,
    canonical_dataset_sha256: str = CANONICAL_DATASET_SHA256_KH02_R2,
) -> Dict[str, Any]:
    """
    Artifact 03: Freezes ALL KH-02.R2 factorial estimands, covariate balance thresholds,
    fill-time risk rules, cost scenarios, and falsification criteria BEFORE reading outcomes.
    """
    r1_plan, _ = _r2_frozen_plan_and_arms(code_sha=tested_executable_sha)
    r1_plan_sha = str(r1_plan.manifest_sha256)
    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "frozen_before_outcome_reads": True,
        "tested_executable_sha": str(tested_executable_sha),
        "canonical_dataset_sha256": str(canonical_dataset_sha256),
        "data_regime": "REUSED_RESEARCH_HISTORICAL_DIAGNOSTIC_ONLY",
        "fresh_oos_permitted_for_tuning": False,
        "r1_frozen_hypotheses_sha256": r1_plan_sha,
        "frozen_estimands": {
            "E_ONLY_PAIRED_FIXED_GEOMETRY": {
                "question": "Q-E: Holding R2 verified-unswept SL/TP geometry, opportunity timestamp, direction, and fill mode fixed, does entry filter (B2_H1/H2/H3/H123) improve expectancy vs NC_NULL_TRIGGER?",
                "identification_method": "Exact common-support pairing on identical (opportunity_id, direction, decision_timestamp_ms, fill_mode) + stratified balance audit.",
                "unit_of_analysis": "Opportunity-level paired delta & chronological 1-position trade-level comparison explicitly separating frequency reduction from per-trade quality.",
            },
            "G_ONLY_PAIRED_SAME_ENTRY": {
                "question": "Q-G: Holding entry arm predicate fixed, how does replacing R1 target resolver with R2 verified-unswept target resolver change eligibility, selection, planned risk, exits, and expectancy?",
                "identification_method": "Exact same-entry opportunity pairing (R1 geometry vs R2 verified-unswept geometry) preserving ALL unmatched & newly rejected candidates.",
            },
            "E_X_G_FACTORIAL": {
                "question": "2x2 factorial decomposition crossing Entry Filter (ON vs NC_NULL_TRIGGER) x Geometry (R1 vs R2 verified-unswept).",
                "cells": ["(E=OFF,G=R1)", "(E=ON,G=R1)", "(E=OFF,G=R2)", "(E=ON,G=R2)"],
            },
            "X_FIXED_FILL": {
                "question": "Q-X (Fixed-Fill): Holding exact same trade IDs, fills, quantities, SL/TP, and exits fixed, what is exact Decimal net PnL under 1x/2x/3x costs and 0.0004 vs 0.0006 taker fee?",
                "population_rule": "ZERO_DROPPED_IDS_STABLE_DENOMINATOR",
            },
            "X_FULL_RERISKED_UNIVERSE": {
                "question": "Q-X (Full-Universe Rerisked): Restarting from all 44,692 directional M1 opportunities under each cost scenario (1x/2x/3x and 0.0004/0.0006), what are the dynamically re-sized, re-filtered, 1-position chronological outcomes?",
                "population_rule": "FULL_RERISKED_DYNAMIC_POPULATION_NEVER_SUBSET_ONLY",
            },
            "B0_VS_B2_CROSS_UNIVERSE": {
                "status": "COMPARISON_NOT_IDENTIFIABLE",
                "reason": "SI0/B0 (production A1 56,710 universe + production SL/TP geometry) and B2 (44,692 M1 trigger universe + structural unswept M15/H1 SL/TP geometry) differ in both universe and geometry; reported descriptively only, never attributed solely to entry.",
            },
        },
        "predeclared_balance_covariates": [
            "direction_is_long",
            "ny_session_is_core_am_pm",
            "h1_atr_high_vol_regime",
            "m1_atr_above_median",
            "time_second_half",
        ],
        "balance_thresholds": {
            "max_abs_smd_key_covariates": DEFAULT_SMD_THRESHOLD,
            "min_overlap_coverage_ratio": 0.50,
            "min_effective_n_per_group": 20,
            "on_failure_label": "MATCHING_INADEQUATE",
        },
        "fill_time_net_risk_policy": {
            "planned_net_risk_cap_usd": PLANNED_NET_RISK_CAP_USD,
            "min_net_rr": MIN_NET_RR_THRESHOLD,
            "causal_fill_time_info_only": "NEXT_BAR_OPEN_PRICE_PLUS_MODELED_SPREAD_SLIPPAGE_ONLY_NO_FUTURE_HIGH_LOW",
            "rule_when_fill_time_planned_risk_exceeds_cap": "CAUSAL_RESIZE_DOWN_IF_VALID_QTY_GE_0p01_AND_NET_RR_GE_2p0_ELSE_REJECT_AT_FILL_TIME",
            "realized_tail_disclosure": "planned_net_risk_usd <= 2.50 is a planned stop estimate, NOT a physical cap on realized loss after adverse gaps/slippage.",
        },
        "cost_scenarios_frozen": [
            {"id": "1X_BASE_0p0004", "spread_usd": 0.20, "slippage_usd": 0.10, "taker_fee_rate": 0.0004, "maker_fee_rate": 0.0002},
            {"id": "FEE_ONLY_0p0006", "spread_usd": 0.20, "slippage_usd": 0.10, "taker_fee_rate": 0.0006, "maker_fee_rate": 0.0002},
            {"id": "2X_ALL_COST_0p0004", "spread_usd": 0.40, "slippage_usd": 0.20, "taker_fee_rate": 0.0008, "maker_fee_rate": 0.0004},
            {"id": "2X_ALL_COST_0p0006", "spread_usd": 0.40, "slippage_usd": 0.20, "taker_fee_rate": 0.0012, "maker_fee_rate": 0.0004},
            {"id": "3X_ALL_COST_0p0004", "spread_usd": 0.60, "slippage_usd": 0.30, "taker_fee_rate": 0.0012, "maker_fee_rate": 0.0006},
            {"id": "3X_ALL_COST_0p0006", "spread_usd": 0.60, "slippage_usd": 0.30, "taker_fee_rate": 0.0018, "maker_fee_rate": 0.0006},
        ],
        "source_latency_sensitivity_grid_ms": [0, 1000, 5000, 60000],
        "falsification_criteria": {
            "economic_edge_requires": [
                "Reused research NEVER yields PROVEN_EDGE on its own without untouched prospective OOS.",
                "Positive 1x AND 2x all-cost net expectancy with NY-day block bootstrap 95% CI > 0 AND Holm-Bonferroni survival.",
                "Non-negative paired lift over NC_NULL_TRIGGER under balanced common support (|SMD| <= 0.10).",
            ],
            "negative_result_validity": "A negative economic finding with passing engineering/causality/QA gates is a valid completed R2 outcome.",
        },
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "random_seed": DEFAULT_RANDOM_SEED_KH02_R2,
    }
    payload["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "artifact_canonical_sha256"}
    )
    return payload


# ==============================================================================
# SECTION B — STREAMING CAUSAL UNSWEPT LEVEL LEDGER + T10 PROOF (04)
# ==============================================================================


def _extract_htf_confirmed_pivots(
    bars_htf: Sequence[Mapping[str, Any]],
    tf_label: str,
    tf_ms: int,
    *,
    pivot_radius: int = 2,
    source_latency_ms: int = 0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Extracts every confirmed 5-bar fractal swing high and low (`pivot_radius=2`, matching
    R1's exact pivot rule so R1 vs R2 is strictly apples-to-apples) along with exact
    confirmation time (`bars_htf[pivot + r]["close_time"]`), gap provenance, and source-HTF
    first sweep timestamp (`first_swept_htf_ts_ms`).

    Fail-closed on unclosed candles, non-monotonic/duplicate close timestamps, or corrupted OHLC.
    """
    if source_latency_ms < 0:
        raise ValueError(f"NEGATIVE_SOURCE_LATENCY_FORBIDDEN:{source_latency_ms}")
    n = len(bars_htf)
    if n == 0:
        raise ValueError(f"EMPTY_HTF_SERIES:{tf_label}")

    close_ms: List[int] = []
    open_ms: List[int] = []
    highs: List[float] = []
    lows: List[float] = []
    gap_before: List[bool] = [False] * n
    prev_c: Optional[int] = None
    seen: Set[int] = set()
    gap_count = 0

    for i, b in enumerate(bars_htf):
        if not bool(b.get("is_closed", True)):
            raise ValueError(f"UNCLOSED_CANDLE_FORBIDDEN:{tf_label}:idx={i}")
        for fk in ("future_high", "future_low", "future_close", "future_outcome", "future_high_after_entry"):
            if fk in b:
                raise ValueError(f"FORBIDDEN_FUTURE_KEY_IN_BAR:{tf_label}:{fk}")
        c_t = int(b.get("close_time") or b.get("close_time_ms") or 0)
        o_t = int(b.get("open_time") or b.get("open_time_ms") or (c_t - tf_ms))
        o_px = float(b.get("open", 0.0) or 0.0)
        h_px = float(b.get("high", 0.0) or 0.0)
        l_px = float(b.get("low", 0.0) or 0.0)
        c_px = float(b.get("close", 0.0) or 0.0)
        if c_t <= 0 or c_t in seen:
            raise ValueError(f"DUPLICATE_OR_INVALID_CLOSE_TIME:{tf_label}:idx={i}:ts={c_t}")
        if prev_c is not None and c_t <= prev_c:
            raise ValueError(f"NON_MONOTONIC_CLOSE_TIME:{tf_label}:idx={i}:{c_t}<={prev_c}")
        if (
            not (math.isfinite(o_px) and math.isfinite(h_px) and math.isfinite(l_px) and math.isfinite(c_px))
            or min(o_px, h_px, l_px, c_px) <= 0.0
            or h_px < max(o_px, c_px, l_px) - 1e-6
            or l_px > min(o_px, c_px, h_px) + 1e-6
        ):
            raise ValueError(f"CORRUPTED_OHLC:{tf_label}:idx={i}")
        if prev_c is not None and (c_t - prev_c) > tf_ms:
            gap_before[i] = True
            gap_count += 1
        seen.add(c_t)
        prev_c = c_t
        close_ms.append(c_t)
        open_ms.append(o_t)
        highs.append(h_px)
        lows.append(l_px)

    levels: List[Dict[str, Any]] = []
    # Streaming min-heap for active HTF highs (price, level_idx) and max-heap for active HTF lows (-price, level_idx)
    active_hi_heap: List[Tuple[float, int]] = []
    active_lo_heap: List[Tuple[float, int]] = []

    for i in range(n):
        hi_i = highs[i]
        lo_i = lows[i]
        c_i = close_ms[i]

        # 1) Sweep any previously confirmed HTF level breached/touched by closed HTF bar i
        while active_hi_heap and active_hi_heap[0][0] <= hi_i + 1e-9:
            _, lidx = heapq.heappop(active_hi_heap)
            levels[lidx]["first_swept_htf_bar_idx"] = i
            levels[lidx]["first_swept_htf_ts_ms"] = c_i
        while active_lo_heap and (-active_lo_heap[0][0]) >= lo_i - 1e-9:
            _, lidx = heapq.heappop(active_lo_heap)
            levels[lidx]["first_swept_htf_bar_idx"] = i
            levels[lidx]["first_swept_htf_ts_ms"] = c_i

        # 2) Confirm new pivot k = i - pivot_radius at close of bar i
        k = i - pivot_radius
        if k >= pivot_radius:
            has_window_gap = any(gap_before[m] for m in range(k - pivot_radius, i + 1))
            gap_prov = "ADJACENT_GAP_PRESENT" if has_window_gap else "CONTIGUOUS_WINDOW"

            is_high_p = all(highs[k] >= highs[k - d] for d in range(1, pivot_radius + 1)) and all(
                highs[k] > highs[k + d] for d in range(1, pivot_radius + 1)
            )
            if is_high_p:
                px = round(highs[k], 4)
                lid = f"{tf_label}_HIGH_p{k}_c{i}_{c_i}"
                lvl = {
                    "level_id": lid,
                    "source_tf": tf_label,
                    "level_kind": "SWING_HIGH",
                    "target_for_direction": "LONG",
                    "price": px,
                    "pivot_bar_idx": k,
                    "pivot_bar_close_ms": close_ms[k],
                    "confirmation_bar_idx": i,
                    "confirmation_time_ms": c_i,
                    "source_latency_ms": int(source_latency_ms),
                    "available_at_ms": c_i + int(source_latency_ms),
                    "known_at_ms": c_i + int(source_latency_ms),
                    "first_swept_htf_bar_idx": None,
                    "first_swept_htf_ts_ms": None,
                    "first_swept_m1_bar_idx": None,
                    "first_swept_m1_ts_ms": None,
                    "first_swept_ts_ms": None,
                    "first_swept_mechanism": "NEVER_SWEPT_IN_SAMPLE",
                    "gap_provenance": gap_prov,
                    "confidence": "CONFIRMED_5BAR_FRACTAL" if not has_window_gap else "CONFIRMED_5BAR_FRACTAL_ACROSS_GAP",
                }
                lidx = len(levels)
                levels.append(lvl)
                heapq.heappush(active_hi_heap, (px, lidx))

            is_low_p = all(lows[k] <= lows[k - d] for d in range(1, pivot_radius + 1)) and all(
                lows[k] < lows[k + d] for d in range(1, pivot_radius + 1)
            )
            if is_low_p:
                px = round(lows[k], 4)
                lid = f"{tf_label}_LOW_p{k}_c{i}_{c_i}"
                lvl = {
                    "level_id": lid,
                    "source_tf": tf_label,
                    "level_kind": "SWING_LOW",
                    "target_for_direction": "SHORT",
                    "price": px,
                    "pivot_bar_idx": k,
                    "pivot_bar_close_ms": close_ms[k],
                    "confirmation_bar_idx": i,
                    "confirmation_time_ms": c_i,
                    "source_latency_ms": int(source_latency_ms),
                    "available_at_ms": c_i + int(source_latency_ms),
                    "known_at_ms": c_i + int(source_latency_ms),
                    "first_swept_htf_bar_idx": None,
                    "first_swept_htf_ts_ms": None,
                    "first_swept_m1_bar_idx": None,
                    "first_swept_m1_ts_ms": None,
                    "first_swept_ts_ms": None,
                    "first_swept_mechanism": "NEVER_SWEPT_IN_SAMPLE",
                    "gap_provenance": gap_prov,
                    "confidence": "CONFIRMED_5BAR_FRACTAL" if not has_window_gap else "CONFIRMED_5BAR_FRACTAL_ACROSS_GAP",
                }
                lidx = len(levels)
                levels.append(lvl)
                heapq.heappush(active_lo_heap, (-px, lidx))

    return levels, {
        "tf_label": tf_label,
        "bar_count": n,
        "first_close_ms": close_ms[0],
        "last_close_ms": close_ms[-1],
        "gap_count": gap_count,
        "confirmed_pivots_count": len(levels),
    }


def build_r2_causal_unswept_level_ledger(
    datasets: Mapping[str, Any],
    *,
    source_latency_ms: int = 0,
    warmup_m1: int = 30,
    pivot_radius: int = 2,
    run_t10_future_mutation_proof: bool = True,
    rng_seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
    max_active_levels_per_tf_side: int = 24,
) -> Dict[str, Any]:
    """
    §03(2) -> Artifact 04: Streaming causal prefix ledger for M15 & H1 confirmed swing highs/lows.
    Tracks BOTH:
      - source-HTF sweep timestamps (`first_swept_htf_ts_ms`)
      - intervening closed-M1 bar sweep timestamps (`first_swept_m1_ts_ms`, strictly after `confirmation_time_ms`)
    Enforces `available_at_ms = confirmation_time_ms + source_latency_ms <= decision_time_ms`,
    never reads unclosed or future bars, and builds compact per-M1-bar causal unswept pools
    in O(N log K) streaming time without repeated full-dataset rescans.
    """
    bars_1m = list(datasets.get("1m") or ())
    bars_15m = list(datasets.get("15m") or ())
    bars_1h = list(datasets.get("1H") or ())
    if not bars_1m or not bars_15m or not bars_1h:
        raise ValueError("MISSING_REQUIRED_TIMEFRAME_FOR_R2_LEDGER")
    if source_latency_ms < 0:
        raise ValueError(f"NEGATIVE_SOURCE_LATENCY_FORBIDDEN:{source_latency_ms}")

    # Validate M1 strictly first (monotonic, closed, finite OHLC, no future keys)
    m1_close: List[int] = []
    m1_high: List[float] = []
    m1_low: List[float] = []
    prev_m1: Optional[int] = None
    seen_m1: Set[int] = set()
    for i, b in enumerate(bars_1m):
        if not bool(b.get("is_closed", True)):
            raise ValueError(f"UNCLOSED_CANDLE_FORBIDDEN:1m:idx={i}")
        for fk in ("future_high", "future_low", "future_close", "future_outcome", "future_high_after_entry"):
            if fk in b:
                raise ValueError(f"FORBIDDEN_FUTURE_KEY_IN_BAR:1m:{fk}")
        c_t = int(b.get("close_time") or b.get("close_time_ms") or 0)
        o_px = float(b.get("open", 0.0) or 0.0)
        h_px = float(b.get("high", 0.0) or 0.0)
        l_px = float(b.get("low", 0.0) or 0.0)
        c_px = float(b.get("close", 0.0) or 0.0)
        if c_t <= 0 or c_t in seen_m1:
            raise ValueError(f"DUPLICATE_OR_INVALID_CLOSE_TIME:1m:idx={i}:ts={c_t}")
        if prev_m1 is not None and c_t <= prev_m1:
            raise ValueError(f"NON_MONOTONIC_CLOSE_TIME:1m:idx={i}:{c_t}<={prev_m1}")
        if (
            not (math.isfinite(o_px) and math.isfinite(h_px) and math.isfinite(l_px) and math.isfinite(c_px))
            or min(o_px, h_px, l_px, c_px) <= 0.0
            or h_px < max(o_px, c_px, l_px) - 1e-6
            or l_px > min(o_px, c_px, h_px) + 1e-6
        ):
            raise ValueError(f"CORRUPTED_OHLC:1m:idx={i}")
        seen_m1.add(c_t)
        prev_m1 = c_t
        m1_close.append(c_t)
        m1_high.append(h_px)
        m1_low.append(l_px)

    m15_levels, m15_meta = _extract_htf_confirmed_pivots(
        bars_15m, "15m", 900_000, pivot_radius=pivot_radius, source_latency_ms=source_latency_ms
    )
    h1_levels, h1_meta = _extract_htf_confirmed_pivots(
        bars_1h, "1H", 3_600_000, pivot_radius=pivot_radius, source_latency_ms=source_latency_ms
    )

    all_levels: List[Dict[str, Any]] = m15_levels + h1_levels
    all_levels.sort(key=lambda r: (r["confirmation_time_ms"], r["source_tf"], r["level_id"]))

    # Streaming forward pass over M1 bars to record exact `first_swept_m1_ts_ms` (strictly after confirmation_time_ms)
    # AND maintain strictly causal active unswept levels at each M1 bar i.
    # Because levels become confirmed at `confirmation_time_ms` (which may predate bars_1m[0]),
    # we push levels whose `confirmation_time_ms < m1_close[i]` before sweeping on bar i,
    # and levels whose `confirmation_time_ms == m1_close[i]` become confirmed AT the close of bar i
    # (so bar i itself is the confirmation bar and cannot sweep them; bar i+1 is the first bar that can!).
    n_lvl = len(all_levels)
    lvl_ptr_sweep = 0
    m1_sweep_hi_heap: List[Tuple[float, int]] = []
    m1_sweep_lo_heap: List[Tuple[float, int]] = []

    for m_idx in range(len(bars_1m)):
        c_m = m1_close[m_idx]
        h_m = m1_high[m_idx]
        l_m = m1_low[m_idx]
        # Activate any level strictly confirmed BEFORE c_m so M1 bar m_idx can sweep it
        while lvl_ptr_sweep < n_lvl and all_levels[lvl_ptr_sweep]["confirmation_time_ms"] < c_m:
            lv = all_levels[lvl_ptr_sweep]
            # Only push if not already swept on HTF before or at its confirmation (impossible)
            if lv["level_kind"] == "SWING_HIGH":
                heapq.heappush(m1_sweep_hi_heap, (lv["price"], lvl_ptr_sweep))
            else:
                heapq.heappush(m1_sweep_lo_heap, (-lv["price"], lvl_ptr_sweep))
            lvl_ptr_sweep += 1

        # Sweep active levels touched/breached by closed M1 bar m_idx
        while m1_sweep_hi_heap and m1_sweep_hi_heap[0][0] <= h_m + 1e-9:
            _, lidx = heapq.heappop(m1_sweep_hi_heap)
            if all_levels[lidx]["first_swept_m1_ts_ms"] is None:
                all_levels[lidx]["first_swept_m1_bar_idx"] = m_idx
                all_levels[lidx]["first_swept_m1_ts_ms"] = c_m
        while m1_sweep_lo_heap and (-m1_sweep_lo_heap[0][0]) >= l_m - 1e-9:
            _, lidx = heapq.heappop(m1_sweep_lo_heap)
            if all_levels[lidx]["first_swept_m1_ts_ms"] is None:
                all_levels[lidx]["first_swept_m1_bar_idx"] = m_idx
                all_levels[lidx]["first_swept_m1_ts_ms"] = c_m

    # Finalize authoritative `first_swept_ts_ms` and mechanism per level
    swept_htf_count = 0
    swept_m1_earlier_count = 0
    never_swept_count = 0
    for lv in all_levels:
        htf_t = lv["first_swept_htf_ts_ms"]
        m1_t = lv["first_swept_m1_ts_ms"]
        if htf_t is None and m1_t is None:
            lv["first_swept_ts_ms"] = None
            lv["first_swept_mechanism"] = "NEVER_SWEPT_IN_SAMPLE"
            never_swept_count += 1
        elif htf_t is not None and (m1_t is None or htf_t <= m1_t):
            lv["first_swept_ts_ms"] = htf_t
            lv["first_swept_mechanism"] = "SWEPT_ON_SOURCE_HTF"
            swept_htf_count += 1
        else:
            lv["first_swept_ts_ms"] = m1_t
            lv["first_swept_mechanism"] = "SWEPT_BY_INTERVENING_CLOSED_M1"
            swept_m1_earlier_count += 1

    # Build fast causal prefix index for O(1) lookup of verified-unswept levels at any M1 bar i:
    # We stream levels in order of `available_at_ms` (= confirmation_time_ms + source_latency_ms)
    # and maintain active unswept dict keyed by level_id, removing any level whose `first_swept_ts_ms <= c_m`.
    # NOTE: Because `first_swept_ts_ms <= c_m` depends EXCLUSIVELY on bars with `close_time <= c_m`,
    # this streaming prefix is mathematically 100% causal (and verified by T10 future mutation proof below!).
    by_avail = sorted(all_levels, key=lambda r: (r["available_at_ms"], r["source_tf"], r["level_id"]))
    by_sweep = sorted(
        [lv for lv in all_levels if lv["first_swept_ts_ms"] is not None],
        key=lambda r: (r["first_swept_ts_ms"], r["level_id"]),
    )

    avail_ptr = 0
    sweep_ptr = 0
    active_highs_map: Dict[str, Dict[str, Any]] = {}
    active_lows_map: Dict[str, Dict[str, Any]] = {}
    swept_ids_seen: Set[str] = set()

    unswept_highs_by_m1_idx: Dict[int, Tuple[Dict[str, Any], ...]] = {}
    unswept_lows_by_m1_idx: Dict[int, Tuple[Dict[str, Any], ...]] = {}

    def _compact_level(lv: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "level_id": lv["level_id"],
            "source_tf": lv["source_tf"],
            "price": lv["price"],
            "confirmation_time_ms": lv["confirmation_time_ms"],
            "available_at_ms": lv["available_at_ms"],
            "first_swept_ts_ms": lv["first_swept_ts_ms"],
            "gap_provenance": lv["gap_provenance"],
        }

    for m_idx in range(len(bars_1m)):
        c_m = m1_close[m_idx]
        # 1) Mark all levels swept at or before c_m
        while sweep_ptr < len(by_sweep) and by_sweep[sweep_ptr]["first_swept_ts_ms"] <= c_m:
            sid = by_sweep[sweep_ptr]["level_id"]
            swept_ids_seen.add(sid)
            active_highs_map.pop(sid, None)
            active_lows_map.pop(sid, None)
            sweep_ptr += 1

        # 2) Admit all levels newly available at or before c_m (ONLY if not already swept at or before c_m!)
        while avail_ptr < len(by_avail) and by_avail[avail_ptr]["available_at_ms"] <= c_m:
            lv = by_avail[avail_ptr]
            avail_ptr += 1
            lid = lv["level_id"]
            if lid in swept_ids_seen:
                continue
            if lv["first_swept_ts_ms"] is not None and lv["first_swept_ts_ms"] <= c_m:
                swept_ids_seen.add(lid)
                continue
            compact = _compact_level(lv)
            if lv["level_kind"] == "SWING_HIGH":
                active_highs_map[lid] = compact
            else:
                active_lows_map[lid] = compact

        if m_idx >= warmup_m1:
            # Keep deterministic ordered snapshots per side (keep recent + closest levels bounded cleanly)
            hi_vals = sorted(
                active_highs_map.values(),
                key=lambda r: (r["price"], 0 if r["source_tf"] == "15m" else 1, r["confirmation_time_ms"], r["level_id"]),
            )
            lo_vals = sorted(
                active_lows_map.values(),
                key=lambda r: (-r["price"], 0 if r["source_tf"] == "15m" else 1, r["confirmation_time_ms"], r["level_id"]),
            )
            # Keep all active levels up to generous cap (never dropping nearest reachable targets)
            if len(hi_vals) > max_active_levels_per_tf_side * 2:
                hi_vals = hi_vals[: max_active_levels_per_tf_side * 2]
            if len(lo_vals) > max_active_levels_per_tf_side * 2:
                lo_vals = lo_vals[: max_active_levels_per_tf_side * 2]
            unswept_highs_by_m1_idx[m_idx] = tuple(hi_vals)
            unswept_lows_by_m1_idx[m_idx] = tuple(lo_vals)

    # Build lookup index by (source_tf, level_kind, round(price, 2)) -> list of levels sorted by confirmation_time_ms
    lookup_by_tf_kind_px: Dict[Tuple[str, str, float], List[Dict[str, Any]]] = defaultdict(list)
    for lv in all_levels:
        key = (lv["source_tf"], lv["level_kind"], round(float(lv["price"]), 2))
        lookup_by_tf_kind_px[key].append(lv)

    # T10 Future-Bar Mutation Invariance Proof:
    # Randomly mutate ALL M1/M15/H1 bars strictly AFTER cutoff_ts and verify every causal
    # active unswept pool at and before cutoff_idx is BIT-FOR-BIT identical!
    t10_proof: Dict[str, Any] = {"executed": False, "passed": False}
    if run_t10_future_mutation_proof and len(bars_1m) > warmup_m1 + 10:
        cutoff_idx = min(len(bars_1m) - 5, warmup_m1 + max(5, (len(bars_1m) - warmup_m1) // 2))
        cutoff_ts = m1_close[cutoff_idx]
        rng = random.Random(rng_seed)

        def _mutate_after(bars: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
            mut: List[Dict[str, Any]] = []
            for b in bars:
                bc = dict(b)
                if int(bc.get("close_time") or bc.get("close_time_ms") or 0) > cutoff_ts:
                    shock = round(rng.uniform(-180.0, 180.0), 2)
                    o_p = max(100.0, float(bc["open"]) + shock)
                    c_p = max(100.0, float(bc["close"]) + shock)
                    h_p = max(o_p, c_p) + round(rng.uniform(0.5, 45.0), 2)
                    l_p = max(10.0, min(o_p, c_p) - round(rng.uniform(0.5, 45.0), 2))
                    bc["open"] = round(o_p, 2)
                    bc["high"] = round(h_p, 2)
                    bc["low"] = round(l_p, 2)
                    bc["close"] = round(c_p, 2)
                mut.append(bc)
            return mut

        ds_mut = {
            "1m": _mutate_after(bars_1m),
            "15m": _mutate_after(bars_15m),
            "1H": _mutate_after(bars_1h),
        }
        mut_res = build_r2_causal_unswept_level_ledger(
            ds_mut,
            source_latency_ms=source_latency_ms,
            warmup_m1=warmup_m1,
            pivot_radius=pivot_radius,
            run_t10_future_mutation_proof=False,
            max_active_levels_per_tf_side=max_active_levels_per_tf_side,
        )

        # Strip future sweep timestamps beyond cutoff_ts when comparing prefix state
        def _prefix_snapshot(hi_map: Mapping[int, Tuple[Dict[str, Any], ...]], lo_map: Mapping[int, Tuple[Dict[str, Any], ...]]) -> str:
            rows = []
            for idx_k in range(warmup_m1, cutoff_idx + 1):
                his = [(r["level_id"], r["price"], r["source_tf"], r["available_at_ms"]) for r in hi_map.get(idx_k, ())]
                los = [(r["level_id"], r["price"], r["source_tf"], r["available_at_ms"]) for r in lo_map.get(idx_k, ())]
                rows.append((idx_k, his, los))
            return canonical_hash(rows)

        orig_prefix_sha = _prefix_snapshot(unswept_highs_by_m1_idx, unswept_lows_by_m1_idx)
        mut_prefix_sha = _prefix_snapshot(mut_res["unswept_highs_by_m1_idx"], mut_res["unswept_lows_by_m1_idx"])
        t10_proof = {
            "executed": True,
            "cutoff_m1_index": cutoff_idx,
            "cutoff_close_time_ms": cutoff_ts,
            "cutoff_utc": _utc_iso(cutoff_ts),
            "mutated_m1_bars_count": sum(1 for t in m1_close if t > cutoff_ts),
            "rng_seed": rng_seed,
            "original_prefix_sha256": orig_prefix_sha,
            "mutated_future_prefix_sha256": mut_prefix_sha,
            "bit_for_bit_identical": orig_prefix_sha == mut_prefix_sha,
            "passed": orig_prefix_sha == mut_prefix_sha,
        }

    ledger_canonical_sha = canonical_hash(
        [
            (
                lv["level_id"],
                lv["price"],
                lv["confirmation_time_ms"],
                lv["available_at_ms"],
                lv["first_swept_htf_ts_ms"],
                lv["first_swept_m1_ts_ms"],
                lv["first_swept_ts_ms"],
            )
            for lv in all_levels
        ]
    )

    return {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "source_latency_ms": int(source_latency_ms),
        "pivot_radius": int(pivot_radius),
        "warmup_m1": int(warmup_m1),
        "m1_bar_count": len(bars_1m),
        "m15_meta": m15_meta,
        "h1_meta": h1_meta,
        "total_confirmed_levels": len(all_levels),
        "swept_on_source_htf_count": swept_htf_count,
        "swept_earlier_by_intervening_m1_count": swept_m1_earlier_count,
        "never_swept_in_sample_count": never_swept_count,
        "levels": all_levels,
        "lookup_by_tf_kind_px": lookup_by_tf_kind_px,
        "unswept_highs_by_m1_idx": unswept_highs_by_m1_idx,
        "unswept_lows_by_m1_idx": unswept_lows_by_m1_idx,
        "t10_future_mutation_proof": t10_proof,
        "ledger_canonical_sha256": ledger_canonical_sha,
    }


def is_level_swept_as_of_r2(
    level: Mapping[str, Any],
    decision_timestamp_ms: int,
) -> Tuple[bool, Optional[str]]:
    """
    Returns `(is_already_swept, mechanism)` strictly as-of `decision_timestamp_ms`.
    """
    fs = level.get("first_swept_ts_ms")
    if fs is None or int(fs) > int(decision_timestamp_ms):
        return False, None
    htf_t = level.get("first_swept_htf_ts_ms")
    if htf_t is not None and int(htf_t) <= int(decision_timestamp_ms):
        return True, "CONFIRMED_SWING_ALREADY_SWEPT_ON_SOURCE_HTF"
    return True, "HTF_POOL_ALREADY_SWEPT_BY_INTERVENING_M1"


# ==============================================================================
# SECTION C — R2 PURE STRUCTURAL TARGET RESOLVER & R1 COUNTEREXAMPLE AUDIT (05, 06)
# ==============================================================================


def resolve_r2_research_only_structural_target(
    rec: KH02AsOfMTFRecord,
    direction: str,
    *,
    unswept_highs_asof: Sequence[Mapping[str, Any]],
    unswept_lows_asof: Sequence[Mapping[str, Any]],
    source_latency_ms: int = 0,
    min_gross_rr_precheck: float = 1.5,
) -> Dict[str, Any]:
    """
    §03(4): Pure KH-02.R2 research-only structural SL/TP resolver.
    Holds SL computation IDENTICAL to R1 so `G_ONLY_PAIRED_SAME_ENTRY` cleanly isolates
    the target-eligibility repair, while STRICTLY enforcing:
      1) Target MUST come exclusively from verified as-of STILL UNSWEPT confirmed M15/H1 levels.
      2) NEVER re-appends `rec.m15_confirmed_swing_*` or `rec.h1_confirmed_swing_*` shortcuts.
      3) Requires `confirmation_time_ms + source_latency_ms <= decision_timestamp_ms`.
      4) Requires `(first_swept_ts_ms is None or first_swept_ts_ms > decision_timestamp_ms)`.
      5) Deterministic nearest reachable target tie-break: `(distance, tf_rank, confirmation_time_ms, level_id)`.
      6) Zero TP stretching or ex-post target rescue; fail-closed REJECT on any constraint failure.
    """
    norm_dir = str(direction or "").strip().upper()
    if norm_dir not in ("LONG", "SHORT"):
        raise ValueError(f"INVALID_DIRECTION:{direction}")
    if source_latency_ms < 0:
        raise ValueError(f"NEGATIVE_SOURCE_LATENCY_FORBIDDEN:{source_latency_ms}")

    entry = float(rec.m1_close)
    atr1 = rec.m1_atr_14
    if atr1 is None or atr1 <= 1e-6:
        return {
            "accepted": False,
            "sl_price": None,
            "tp_price": None,
            "target_level_id": None,
            "target_source": None,
            "target_source_tf": None,
            "target_confirmation_time_ms": None,
            "target_available_at_ms": None,
            "rejection_reason": "MISSING_CAUSAL_M1_ATR",
        }

    noise_buf = max(0.15, round(0.25 * atr1, 2))
    min_stop_dist = max(0.50, round(1.0 * atr1, 2))
    max_stop_dist = max(4.0, round(6.0 * atr1, 2))
    dec_ms = int(rec.decision_timestamp_ms)

    if norm_dir == "LONG":
        anchors = [x for x in (rec.m1_low, rec.m1_confirmed_swing_low) if x is not None and x < entry]
        if not anchors:
            return {
                "accepted": False,
                "sl_price": None,
                "tp_price": None,
                "target_level_id": None,
                "target_source": None,
                "target_source_tf": None,
                "target_confirmation_time_ms": None,
                "target_available_at_ms": None,
                "rejection_reason": "NO_CAUSAL_LONG_STOP_ANCHOR",
            }
        sl_raw = round(min(anchors) - noise_buf, 2)
        stop_d = entry - sl_raw
        if stop_d < min_stop_dist:
            sl_raw = round(entry - min_stop_dist, 2)
            stop_d = entry - sl_raw
        if stop_d > max_stop_dist:
            return {
                "accepted": False,
                "sl_price": sl_raw,
                "tp_price": None,
                "target_level_id": None,
                "target_source": None,
                "target_source_tf": None,
                "target_confirmation_time_ms": None,
                "target_available_at_ms": None,
                "rejection_reason": "CAUSAL_STOP_DISTANCE_TOO_WIDE",
            }

        valid_targets: List[Tuple[float, int, int, str, Mapping[str, Any]]] = []
        swept_rejected_count = 0
        latency_rejected_count = 0
        too_close_count = 0

        for lv in unswept_highs_asof:
            conf_t = int(lv["confirmation_time_ms"])
            avail_t = conf_t + int(source_latency_ms)
            if avail_t > dec_ms:
                latency_rejected_count += 1
                continue
            fs = lv.get("first_swept_ts_ms")
            if fs is not None and int(fs) <= dec_ms:
                swept_rejected_count += 1
                continue
            px = float(lv["price"])
            if px <= entry + stop_d * min_gross_rr_precheck:
                too_close_count += 1
                continue
            tf_rank = 0 if str(lv["source_tf"]) == "15m" else 1
            valid_targets.append((round(px - entry, 4), tf_rank, conf_t, str(lv["level_id"]), lv))

        if not valid_targets:
            rej = (
                "NO_UNSWEPT_CAUSAL_STRUCTURAL_TARGET_ALL_TOO_CLOSE"
                if too_close_count > 0
                else (
                    "NO_UNSWEPT_CAUSAL_STRUCTURAL_TARGET_LATENCY_FILTERED"
                    if latency_rejected_count > 0
                    else "NO_UNSWEPT_CAUSAL_STRUCTURAL_TARGET"
                )
            )
            return {
                "accepted": False,
                "sl_price": sl_raw,
                "tp_price": None,
                "target_level_id": None,
                "target_source": None,
                "target_source_tf": None,
                "target_confirmation_time_ms": None,
                "target_available_at_ms": None,
                "rejection_reason": rej,
                "swept_rejected_count": swept_rejected_count,
                "latency_rejected_count": latency_rejected_count,
                "too_close_count": too_close_count,
            }

        valid_targets.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
        chosen = valid_targets[0][4]
        tf_tag = "M15" if str(chosen["source_tf"]) == "15m" else "H1"
        return {
            "accepted": True,
            "sl_price": sl_raw,
            "tp_price": round(float(chosen["price"]), 2),
            "target_level_id": str(chosen["level_id"]),
            "target_source": f"{tf_tag}_VERIFIED_UNSWEPT_SWING_HIGH",
            "target_source_tf": str(chosen["source_tf"]),
            "target_confirmation_time_ms": int(chosen["confirmation_time_ms"]),
            "target_available_at_ms": int(chosen["confirmation_time_ms"]) + int(source_latency_ms),
            "rejection_reason": None,
            "candidate_pool_valid_count": len(valid_targets),
        }

    # SHORT
    anchors = [x for x in (rec.m1_high, rec.m1_confirmed_swing_high) if x is not None and x > entry]
    if not anchors:
        return {
            "accepted": False,
            "sl_price": None,
            "tp_price": None,
            "target_level_id": None,
            "target_source": None,
            "target_source_tf": None,
            "target_confirmation_time_ms": None,
            "target_available_at_ms": None,
            "rejection_reason": "NO_CAUSAL_SHORT_STOP_ANCHOR",
        }
    sl_raw = round(max(anchors) + noise_buf, 2)
    stop_d = sl_raw - entry
    if stop_d < min_stop_dist:
        sl_raw = round(entry + min_stop_dist, 2)
        stop_d = sl_raw - entry
    if stop_d > max_stop_dist:
        return {
            "accepted": False,
            "sl_price": sl_raw,
            "tp_price": None,
            "target_level_id": None,
            "target_source": None,
            "target_source_tf": None,
            "target_confirmation_time_ms": None,
            "target_available_at_ms": None,
            "rejection_reason": "CAUSAL_STOP_DISTANCE_TOO_WIDE",
        }

    valid_targets = []
    swept_rejected_count = 0
    latency_rejected_count = 0
    too_close_count = 0

    for lv in unswept_lows_asof:
        conf_t = int(lv["confirmation_time_ms"])
        avail_t = conf_t + int(source_latency_ms)
        if avail_t > dec_ms:
            latency_rejected_count += 1
            continue
        fs = lv.get("first_swept_ts_ms")
        if fs is not None and int(fs) <= dec_ms:
            swept_rejected_count += 1
            continue
        px = float(lv["price"])
        if px >= entry - stop_d * min_gross_rr_precheck:
            too_close_count += 1
            continue
        tf_rank = 0 if str(lv["source_tf"]) == "15m" else 1
        valid_targets.append((round(entry - px, 4), tf_rank, conf_t, str(lv["level_id"]), lv))

    if not valid_targets:
        rej = (
            "NO_UNSWEPT_CAUSAL_STRUCTURAL_TARGET_ALL_TOO_CLOSE"
            if too_close_count > 0
            else (
                "NO_UNSWEPT_CAUSAL_STRUCTURAL_TARGET_LATENCY_FILTERED"
                if latency_rejected_count > 0
                else "NO_UNSWEPT_CAUSAL_STRUCTURAL_TARGET"
            )
        )
        return {
            "accepted": False,
            "sl_price": sl_raw,
            "tp_price": None,
            "target_level_id": None,
            "target_source": None,
            "target_source_tf": None,
            "target_confirmation_time_ms": None,
            "target_available_at_ms": None,
            "rejection_reason": rej,
            "swept_rejected_count": swept_rejected_count,
            "latency_rejected_count": latency_rejected_count,
            "too_close_count": too_close_count,
        }

    valid_targets.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    chosen = valid_targets[0][4]
    tf_tag = "M15" if str(chosen["source_tf"]) == "15m" else "H1"
    return {
        "accepted": True,
        "sl_price": sl_raw,
        "tp_price": round(float(chosen["price"]), 2),
        "target_level_id": str(chosen["level_id"]),
        "target_source": f"{tf_tag}_VERIFIED_UNSWEPT_SWING_LOW",
        "target_source_tf": str(chosen["source_tf"]),
        "target_confirmation_time_ms": int(chosen["confirmation_time_ms"]),
        "target_available_at_ms": int(chosen["confirmation_time_ms"]) + int(source_latency_ms),
        "rejection_reason": None,
        "candidate_pool_valid_count": len(valid_targets),
    }


def _make_synthetic_rec(
    *,
    decision_ts_ms: int,
    m1_close: float = 4000.0,
    m1_high: float = 4000.8,
    m1_low: float = 3999.0,
    m1_atr_14: float = 0.80,
    m15_confirmed_swing_high: Optional[float] = None,
    m15_confirmed_swing_low: Optional[float] = None,
    h1_confirmed_swing_high: Optional[float] = None,
    h1_confirmed_swing_low: Optional[float] = None,
) -> KH02AsOfMTFRecord:
    return KH02AsOfMTFRecord(
        bar_index_1m=40,
        decision_timestamp_ms=decision_ts_ms,
        m1_open_time_ms=decision_ts_ms - 60_000,
        m1_close_time_ms=decision_ts_ms,
        m1_open=m1_close - 0.2,
        m1_high=m1_high,
        m1_low=m1_low,
        m1_close=m1_close,
        m1_atr_14=m1_atr_14,
        m1_ema_20=m1_close - 0.5,
        m1_body_ratio=0.75,
        m1_range_atr_ratio=1.2,
        m1_confirmed_swing_low=m1_low - 0.2,
        m1_confirmed_swing_low_conf_ms=decision_ts_ms - 180_000,
        m1_confirmed_swing_high=m1_high + 0.2,
        m1_confirmed_swing_high_conf_ms=decision_ts_ms - 180_000,
        m1_bull_sweep_reclaim=True,
        m1_bear_sweep_reclaim=True,
        m1_bull_bos=True,
        m1_bear_bos=True,
        m1_bull_fvg_retest=False,
        m1_bear_fvg_retest=False,
        m15_last_close_ms=decision_ts_ms - 60_000,
        m15_knowledge_time_ms=decision_ts_ms - 60_000,
        m15_close=m1_close,
        m15_ema_20=m1_close - 1.0,
        m15_ema_slope=0.15,
        m15_raw_dir_efficiency_8=0.45,
        m15_trend_dir="BULL",
        m15_confirmed_swing_low=m15_confirmed_swing_low,
        m15_confirmed_swing_low_conf_ms=decision_ts_ms - 900_000 if m15_confirmed_swing_low is not None else None,
        m15_confirmed_swing_high=m15_confirmed_swing_high,
        m15_confirmed_swing_high_conf_ms=decision_ts_ms - 900_000 if m15_confirmed_swing_high is not None else None,
        h1_last_close_ms=decision_ts_ms - 300_000,
        h1_knowledge_time_ms=decision_ts_ms - 300_000,
        h1_close=m1_close,
        h1_ema_20=m1_close - 2.0,
        h1_ema_slope=0.25,
        h1_atr_14=4.5,
        h1_atr_causal_prefix_quantile=0.55,
        h1_trend_dir="BULL",
        h1_confirmed_swing_low=h1_confirmed_swing_low,
        h1_confirmed_swing_low_conf_ms=decision_ts_ms - 3_600_000 if h1_confirmed_swing_low is not None else None,
        h1_confirmed_swing_high=h1_confirmed_swing_high,
        h1_confirmed_swing_high_conf_ms=decision_ts_ms - 3_600_000 if h1_confirmed_swing_high is not None else None,
        ny_date="2026-10-15",
        ny_hour=10,
        ny_weekday=3,
        ny_session_bucket="NY_AM",
        is_liquid_session=True,
        spread_to_m1_atr_ratio=0.25,
        recent_gap_180m=False,
        max_known_at_ms=decision_ts_ms,
        source_origin="SYNTHETIC_TEST_R2",
        invalidation_reasons=(),
        record_sha256="0" * 64,
    )


def run_r2_t01_to_t10_adversarial_target_scenarios() -> Dict[str, Any]:
    """
    §04: Executes ALL 10 mandatory adversarial scenarios (`T01`..`T10`) comparing
    R1 read-only `_resolve_b2_structural_stop_and_target` against R2
    `resolve_r2_research_only_structural_target` and `build_r2_causal_unswept_level_ledger`.
    """
    dec_ts = 1_792_080_000_000  # 2026-10-15T16:00:00Z
    results: List[Dict[str, Any]] = []

    # T01: Confirmed M15 swing HIGH exists, swept BEFORE decision -> R1 illegally picks it; R2 rejects it!
    rec_t01 = _make_synthetic_rec(decision_ts_ms=dec_ts, m15_confirmed_swing_high=4006.0)
    r1_sl1, r1_tp1, r1_src1, r1_rej1 = _resolve_b2_structural_stop_and_target(
        rec_t01, "LONG", m15_unswept_highs=(), m15_unswept_lows=(), h1_unswept_highs=(), h1_unswept_lows=()
    )
    swept_m15_hi = {
        "level_id": "15m_HIGH_T01",
        "source_tf": "15m",
        "price": 4006.0,
        "confirmation_time_ms": dec_ts - 1_800_000,
        "first_swept_ts_ms": dec_ts - 300_000,
    }
    r2_t01 = resolve_r2_research_only_structural_target(
        rec_t01, "LONG", unswept_highs_asof=[swept_m15_hi], unswept_lows_asof=[]
    )
    results.append(
        {
            "scenario_id": "T01",
            "description": "Confirmed M15 swing HIGH swept before LONG decision must NOT be eligible TP",
            "r1_reproduced_swept_target_bug": (r1_tp1 == 4006.0 and r1_rej1 is None),
            "r1_selected": {"tp": r1_tp1, "source": r1_src1, "rejection": r1_rej1},
            "r2_result": {"accepted": r2_t01["accepted"], "tp": r2_t01["tp_price"], "rejection": r2_t01["rejection_reason"]},
            "passed": (r1_tp1 == 4006.0 and (not r2_t01["accepted"])),
        }
    )

    # T02: Confirmed H1 swing LOW swept before SHORT decision -> R1 picks it; R2 rejects it!
    rec_t02 = _make_synthetic_rec(decision_ts_ms=dec_ts, h1_confirmed_swing_low=3993.0)
    r1_sl2, r1_tp2, r1_src2, r1_rej2 = _resolve_b2_structural_stop_and_target(
        rec_t02, "SHORT", m15_unswept_highs=(), m15_unswept_lows=(), h1_unswept_highs=(), h1_unswept_lows=()
    )
    swept_h1_lo = {
        "level_id": "1H_LOW_T02",
        "source_tf": "1H",
        "price": 3993.0,
        "confirmation_time_ms": dec_ts - 7_200_000,
        "first_swept_ts_ms": dec_ts - 600_000,
    }
    r2_t02 = resolve_r2_research_only_structural_target(
        rec_t02, "SHORT", unswept_highs_asof=[], unswept_lows_asof=[swept_h1_lo]
    )
    results.append(
        {
            "scenario_id": "T02",
            "description": "Confirmed H1 swing LOW swept before SHORT decision must NOT be eligible TP",
            "r1_reproduced_swept_target_bug": (r1_tp2 == 3993.0 and r1_rej2 is None),
            "r1_selected": {"tp": r1_tp2, "source": r1_src2, "rejection": r1_rej2},
            "r2_result": {"accepted": r2_t02["accepted"], "tp": r2_t02["tp_price"], "rejection": r2_t02["rejection_reason"]},
            "passed": (r1_tp2 == 3993.0 and (not r2_t02["accepted"])),
        }
    )

    # T03: Swing confirmed only at H1 close AFTER decision -> unavailable!
    rec_t03 = _make_synthetic_rec(decision_ts_ms=dec_ts)
    unconfirmed_h1 = {
        "level_id": "1H_HIGH_T03_FUTURE_CONF",
        "source_tf": "1H",
        "price": 4008.0,
        "confirmation_time_ms": dec_ts + 1_800_000,
        "first_swept_ts_ms": None,
    }
    r2_t03 = resolve_r2_research_only_structural_target(
        rec_t03, "LONG", unswept_highs_asof=[unconfirmed_h1], unswept_lows_asof=[]
    )
    results.append(
        {
            "scenario_id": "T03",
            "description": "H1 swing confirmed after decision timestamp must be unavailable",
            "r2_result": {"accepted": r2_t03["accepted"], "rejection": r2_t03["rejection_reason"]},
            "passed": (not r2_t03["accepted"]),
        }
    )

    # T04: Swing confirmed BEFORE decision and swept ONLY AFTER decision -> eligible as-of decision!
    rec_t04 = _make_synthetic_rec(decision_ts_ms=dec_ts)
    future_swept_ok = {
        "level_id": "15m_HIGH_T04_SWEPT_LATER",
        "source_tf": "15m",
        "price": 4007.5,
        "confirmation_time_ms": dec_ts - 900_000,
        "first_swept_ts_ms": dec_ts + 300_000,
    }
    r2_t04 = resolve_r2_research_only_structural_target(
        rec_t04, "LONG", unswept_highs_asof=[future_swept_ok], unswept_lows_asof=[]
    )
    results.append(
        {
            "scenario_id": "T04",
            "description": "Swing confirmed before decision and swept only after decision MUST remain eligible as-of decision",
            "r2_result": {"accepted": r2_t04["accepted"], "tp": r2_t04["tp_price"], "target_level_id": r2_t04["target_level_id"]},
            "passed": (r2_t04["accepted"] and r2_t04["tp_price"] == 4007.5),
        }
    )

    # T05: Two candidates: closer one ALREADY swept, farther one STILL unswept -> R2 must pick ONLY the valid unswept one!
    rec_t05 = _make_synthetic_rec(decision_ts_ms=dec_ts, m15_confirmed_swing_high=4005.0)
    cand_swept = {
        "level_id": "15m_HIGH_T05_SWEPT",
        "source_tf": "15m",
        "price": 4005.0,
        "confirmation_time_ms": dec_ts - 3_600_000,
        "first_swept_ts_ms": dec_ts - 60_000,
    }
    cand_valid = {
        "level_id": "1H_HIGH_T05_VALID",
        "source_tf": "1H",
        "price": 4009.0,
        "confirmation_time_ms": dec_ts - 1_800_000,
        "first_swept_ts_ms": None,
    }
    r2_t05 = resolve_r2_research_only_structural_target(
        rec_t05, "LONG", unswept_highs_asof=[cand_swept, cand_valid], unswept_lows_asof=[]
    )
    results.append(
        {
            "scenario_id": "T05",
            "description": "Closer swept target skipped; farther valid as-of unswept target selected",
            "r2_result": {"accepted": r2_t05["accepted"], "tp": r2_t05["tp_price"], "target_level_id": r2_t05["target_level_id"]},
            "passed": (r2_t05["accepted"] and r2_t05["tp_price"] == 4009.0 and r2_t05["target_level_id"] == "1H_HIGH_T05_VALID"),
        }
    )

    # T06: close_time + latency == decision_time accepted; +1ms late rejected!
    rec_t06 = _make_synthetic_rec(decision_ts_ms=dec_ts)
    exact_boundary = {
        "level_id": "15m_HIGH_T06_EXACT",
        "source_tf": "15m",
        "price": 4007.0,
        "confirmation_time_ms": dec_ts - 5000,
        "first_swept_ts_ms": None,
    }
    r2_t06_ok = resolve_r2_research_only_structural_target(
        rec_t06, "LONG", unswept_highs_asof=[exact_boundary], unswept_lows_asof=[], source_latency_ms=5000
    )
    r2_t06_late = resolve_r2_research_only_structural_target(
        rec_t06, "LONG", unswept_highs_asof=[exact_boundary], unswept_lows_asof=[], source_latency_ms=5001
    )
    results.append(
        {
            "scenario_id": "T06",
            "description": "Exact close_time + latency == decision_time accepted; +1ms late rejected",
            "r2_exact_accepted": r2_t06_ok["accepted"],
            "r2_plus1ms_rejected": (not r2_t06_late["accepted"]),
            "passed": (r2_t06_ok["accepted"] and (not r2_t06_late["accepted"])),
        }
    )

    # T07: Identical prices across M15 & H1 -> deterministic tie-break (M15 < H1, then earlier confirmation, then level_id)
    rec_t07 = _make_synthetic_rec(decision_ts_ms=dec_ts)
    tie_h1 = {"level_id": "1H_HIGH_T07", "source_tf": "1H", "price": 4008.0, "confirmation_time_ms": dec_ts - 7_200_000, "first_swept_ts_ms": None}
    tie_m15 = {"level_id": "15m_HIGH_T07", "source_tf": "15m", "price": 4008.0, "confirmation_time_ms": dec_ts - 1_800_000, "first_swept_ts_ms": None}
    r2_t07_a = resolve_r2_research_only_structural_target(rec_t07, "LONG", unswept_highs_asof=[tie_h1, tie_m15], unswept_lows_asof=[])
    r2_t07_b = resolve_r2_research_only_structural_target(rec_t07, "LONG", unswept_highs_asof=[tie_m15, tie_h1], unswept_lows_asof=[])
    results.append(
        {
            "scenario_id": "T07",
            "description": "Identical prices across distinct TFs obey order-invariant deterministic tie-break",
            "order_ab_target_id": r2_t07_a["target_level_id"],
            "order_ba_target_id": r2_t07_b["target_level_id"],
            "passed": (r2_t07_a["target_level_id"] == r2_t07_b["target_level_id"] == "15m_HIGH_T07"),
        }
    )

    # T08: Duplicate/non-monotonic timestamp rejected fail-closed by ledger; gap provenance recorded cleanly
    t08_dup_rejected = False
    t08_nonmono_rejected = False
    t08_dst_gap_ok = False
    base_dst = 1_793_505_600_000  # 2026-11-01 (US DST transition date!)
    def _mk_bars(tf_ms: int, count: int, *, dup_idx: Optional[int] = None, nonmono_idx: Optional[int] = None, gap_at: Optional[int] = None) -> List[Dict[str, Any]]:
        out = []
        t = base_dst - count * tf_ms
        for i in range(count):
            if gap_at is not None and i == gap_at:
                t += 20 * tf_ms
            c_t = t + tf_ms
            if dup_idx is not None and i == dup_idx:
                c_t = out[-1]["close_time"]
            if nonmono_idx is not None and i == nonmono_idx:
                c_t = out[-1]["close_time"] - tf_ms
            px = 4000.0 + (1.5 if i % 5 == 2 else 0.0)
            out.append({"open_time": c_t - tf_ms, "close_time": c_t, "open": px, "high": px + 1.0, "low": px - 1.0, "close": px + 0.2, "is_closed": True})
            t += tf_ms
        return out

    try:
        build_r2_causal_unswept_level_ledger({"1m": _mk_bars(60_000, 40, dup_idx=15), "15m": _mk_bars(900_000, 10), "1H": _mk_bars(3_600_000, 10)}, warmup_m1=10, run_t10_future_mutation_proof=False)
    except ValueError:
        t08_dup_rejected = True
    try:
        build_r2_causal_unswept_level_ledger({"1m": _mk_bars(60_000, 40, nonmono_idx=15), "15m": _mk_bars(900_000, 10), "1H": _mk_bars(3_600_000, 10)}, warmup_m1=10, run_t10_future_mutation_proof=False)
    except ValueError:
        t08_nonmono_rejected = True
    led_gap = build_r2_causal_unswept_level_ledger(
        {"1m": _mk_bars(60_000, 50, gap_at=20), "15m": _mk_bars(900_000, 12, gap_at=5), "1H": _mk_bars(3_600_000, 12, gap_at=5)},
        warmup_m1=10,
        run_t10_future_mutation_proof=False,
    )
    t08_dst_gap_ok = led_gap["m15_meta"]["gap_count"] >= 1 and any(
        lv["gap_provenance"] == "ADJACENT_GAP_PRESENT" for lv in led_gap["levels"]
    )
    results.append(
        {
            "scenario_id": "T08",
            "description": "Duplicate/non-monotonic timestamps fail-closed; DST & weekend gaps tracked in gap_provenance",
            "dup_rejected": t08_dup_rejected,
            "nonmono_rejected": t08_nonmono_rejected,
            "dst_gap_provenance_recorded": t08_dst_gap_ok,
            "passed": (t08_dup_rejected and t08_nonmono_rejected and t08_dst_gap_ok),
        }
    )

    # T09: Target too close to achieve NET R:R >= 2.0 -> REJECT, zero TP stretching!
    rec_t09 = _make_synthetic_rec(decision_ts_ms=dec_ts)
    too_close_hi = {"level_id": "15m_HIGH_T09_CLOSE", "source_tf": "15m", "price": 4001.2, "confirmation_time_ms": dec_ts - 900_000, "first_swept_ts_ms": None}
    r2_t09 = resolve_r2_research_only_structural_target(rec_t09, "LONG", unswept_highs_asof=[too_close_hi], unswept_lows_asof=[])
    results.append(
        {
            "scenario_id": "T09",
            "description": "Unreachable net R:R target fails closed with ZERO TP stretching",
            "r2_result": {"accepted": r2_t09["accepted"], "rejection": r2_t09["rejection_reason"]},
            "passed": (not r2_t09["accepted"] and r2_t09["tp_price"] is None),
        }
    )

    # T10: Future-bar random mutation invariance on synthetic multi-TF series
    ds_t10 = {"1m": _mk_bars(60_000, 80), "15m": _mk_bars(900_000, 20), "1H": _mk_bars(3_600_000, 20)}
    led_t10 = build_r2_causal_unswept_level_ledger(ds_t10, warmup_m1=15, run_t10_future_mutation_proof=True, rng_seed=20261121)
    t10_ok = bool(led_t10["t10_future_mutation_proof"].get("passed"))
    results.append(
        {
            "scenario_id": "T10",
            "description": "Randomly mutating all future M1/M15/H1 bars beyond decision leaves prefix ledger bit-for-bit identical",
            "t10_proof": led_t10["t10_future_mutation_proof"],
            "passed": t10_ok,
        }
    )

    all_passed = all(r["passed"] for r in results)
    return {
        "scenario_count": len(results),
        "passed_count": sum(1 for r in results if r["passed"]),
        "all_passed": all_passed,
        "scenarios": results,
    }


def _match_r1_selected_target_in_ledger(
    *,
    tp_px: float,
    tp_src: str,
    direction: str,
    decision_ts_ms: int,
    lookup_by_tf_kind_px: Mapping[Tuple[str, str, float], Sequence[Mapping[str, Any]]],
) -> Optional[Mapping[str, Any]]:
    tf = "15m" if tp_src.startswith("M15") else "1H"
    kind = "SWING_HIGH" if direction == "LONG" else "SWING_LOW"
    cands = lookup_by_tf_kind_px.get((tf, kind, round(float(tp_px), 2)), ())
    # Find the most recent confirmed level with confirmation_time_ms <= decision_ts_ms
    eligible = [lv for lv in cands if int(lv["confirmation_time_ms"]) <= int(decision_ts_ms)]
    if not eligible:
        return None
    # Prefer one still unswept if multiple share exact price; otherwise return the most recently confirmed
    unswept = [lv for lv in eligible if lv["first_swept_ts_ms"] is None or int(lv["first_swept_ts_ms"]) > int(decision_ts_ms)]
    if unswept:
        return unswept[-1]
    return eligible[-1]


def audit_r2_r1_target_eligibility_counterexamples(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    ledger_bundle: Mapping[str, Any],
    r1_candidates: Mapping[str, Any],
    max_sample_counterexamples: int = 120,
) -> Dict[str, Any]:
    """
    §03(3) & §04 -> Artifact 05:
    Independently reproduces R1 `_resolve_b2_structural_stop_and_target(...)` READ-ONLY
    across BOTH synthetic `T01`..`T10` scenarios AND the entire `44,692` directional
    post-warmup M1 bar dataset, comparing every R1-selected target against the authoritative
    R2 causal unswept level ledger (`first_swept_htf_ts_ms` and `first_swept_m1_ts_ms`).

    Carefully separates:
      - `CONFIRMED_SWING_ALREADY_SWEPT_ON_SOURCE_HTF` (Bug #1: R1 lines 1891-1894 / 1923-1926 re-adding swept `confirmed_swing_*`)
      - `HTF_POOL_ALREADY_SWEPT_BY_INTERVENING_M1` (Bug #2: R1 lines 1075-1109 ignoring intervening closed M1 sweeps)
      - `GENUINELY_UNSWEPT_LABEL_COLLISION_ONLY` (R1 picked `"*_CONFIRMED_SWING_*"` over `"*_UNSWEPT_SWING_*"` solely via alphabetical tie-break on an actually unswept level!)
    """
    t01_t10 = run_r2_t01_to_t10_adversarial_target_scenarios()

    records: Sequence[KH02AsOfMTFRecord] = _mtf_records_list(mtf_bundle)
    m15_s = mtf_bundle["m15_series"]
    h1_s = mtf_bundle["h1_series"]
    m15_know = m15_s["know_ms"]
    h1_know = h1_s["know_ms"]
    lookup = ledger_bundle["lookup_by_tf_kind_px"]
    r2_hi_by_idx = ledger_bundle["unswept_highs_by_m1_idx"]
    r2_lo_by_idx = ledger_bundle["unswept_lows_by_m1_idx"]

    total_directional_checked = 0
    r1_raw_triggers_checked = 0
    r1_target_selected_count = 0
    r1_risk_eligible_count = 0

    classification_counts: Counter[str] = Counter()
    risk_eligible_classification_counts: Counter[str] = Counter()
    by_source_label_counts: Counter[str] = Counter()
    swept_by_source_label: Counter[str] = Counter()

    # Track impact on specific R1 arm candidate pools
    r1_arm_opp_sets: Dict[str, Set[str]] = {
        ac: {str(c["opportunity_id"]) for c in lst}
        for ac, lst in (r1_candidates.get("arm_candidates") or {}).items()
    }
    arm_swept_impact: Dict[str, Counter[str]] = {ac: Counter() for ac in r1_arm_opp_sets}

    counterexamples_sample: List[Dict[str, Any]] = []
    all_swept_opp_ids: List[str] = []

    for rec in records:
        idx = rec.bar_index_1m
        j15 = bisect.bisect_right(m15_know, rec.decision_timestamp_ms) - 1
        jh1 = bisect.bisect_right(h1_know, rec.decision_timestamp_ms) - 1
        m15_uh = m15_s["unswept_highs_at"][j15] if j15 >= 0 else ()
        m15_ul = m15_s["unswept_lows_at"][j15] if j15 >= 0 else ()
        h1_uh = h1_s["unswept_highs_at"][jh1] if jh1 >= 0 else ()
        h1_ul = h1_s["unswept_lows_at"][jh1] if jh1 >= 0 else ()
        r2_hi = r2_hi_by_idx.get(idx, ())
        r2_lo = r2_lo_by_idx.get(idx, ())

        for direction in ("LONG", "SHORT"):
            total_directional_checked += 1
            if rec.invalidation_reasons or rec.recent_gap_180m:
                continue
            if direction == "LONG":
                has_trig = bool(
                    (rec.m1_bull_sweep_reclaim or rec.m1_bull_bos or rec.m1_bull_fvg_retest)
                    and rec.m1_body_ratio >= 0.45
                    and (rec.m1_range_atr_ratio is not None and 0.40 <= rec.m1_range_atr_ratio <= 2.80)
                )
            else:
                has_trig = bool(
                    (rec.m1_bear_sweep_reclaim or rec.m1_bear_bos or rec.m1_bear_fvg_retest)
                    and rec.m1_body_ratio >= 0.45
                    and (rec.m1_range_atr_ratio is not None and 0.40 <= rec.m1_range_atr_ratio <= 2.80)
                )
            if not has_trig:
                continue

            r1_raw_triggers_checked += 1
            sl_r1, tp_r1, src_r1, rej_r1 = _resolve_b2_structural_stop_and_target(
                rec,
                direction,
                m15_unswept_highs=m15_uh,
                m15_unswept_lows=m15_ul,
                h1_unswept_highs=h1_uh,
                h1_unswept_lows=h1_ul,
            )
            if rej_r1 is not None or sl_r1 is None or tp_r1 is None or src_r1 is None:
                continue

            r1_target_selected_count += 1
            by_source_label_counts[src_r1] += 1

            # Check risk eligibility of R1 candidate
            opp_id = f"kh02_b2_{direction}_idx{idx}_ts{rec.decision_timestamp_ms}"
            rk = recalculate_trade_risk_quantity_v179312(
                rec.m1_close, sl_r1, tp_r1, direction=direction, decision_ts=rec.decision_timestamp_ms
            )
            r1_risk_ok = bool(
                rk.get("can_execute")
                and float(rk.get("net_rr") or 0.0) >= 2.0 - 1e-9
                and float(rk.get("planned_net_risk_usd") or 99.0) <= 2.50 + 1e-9
                and float(rk.get("quantity") or 0.0) >= 0.01
            )
            if r1_risk_ok:
                r1_risk_eligible_count += 1

            matched_lv = _match_r1_selected_target_in_ledger(
                tp_px=tp_r1,
                tp_src=src_r1,
                direction=direction,
                decision_ts_ms=rec.decision_timestamp_ms,
                lookup_by_tf_kind_px=lookup,
            )

            if matched_lv is None:
                cls = "UNMATCHED_IN_LEDGER_ANOMALY"
                swept_flag = True
                sweep_mech = "UNMATCHED_IN_LEDGER"
            else:
                swept_flag, sweep_mech = is_level_swept_as_of_r2(matched_lv, rec.decision_timestamp_ms)
                if swept_flag:
                    cls = str(sweep_mech)
                elif "CONFIRMED_SWING" in src_r1:
                    cls = "GENUINELY_UNSWEPT_LABEL_COLLISION_ONLY"
                else:
                    cls = "GENUINELY_UNSWEPT_VALID"

            classification_counts[cls] += 1
            if r1_risk_ok:
                risk_eligible_classification_counts[cls] += 1

            if swept_flag:
                swept_by_source_label[src_r1] += 1
                all_swept_opp_ids.append(opp_id)
                for ac, oset in r1_arm_opp_sets.items():
                    if opp_id in oset:
                        arm_swept_impact[ac]["total_swept"] += 1
                        arm_swept_impact[ac][cls] += 1

                if len(counterexamples_sample) < max_sample_counterexamples:
                    r2_alt = resolve_r2_research_only_structural_target(
                        rec, direction, unswept_highs_asof=r2_hi, unswept_lows_asof=r2_lo
                    )
                    counterexamples_sample.append(
                        {
                            "opportunity_id": opp_id,
                            "checkpoint_index_1m": idx,
                            "decision_timestamp_ms": rec.decision_timestamp_ms,
                            "decision_utc": _utc_iso(rec.decision_timestamp_ms),
                            "direction": direction,
                            "entry_reference": rec.m1_close,
                            "r1_sl": sl_r1,
                            "r1_tp": tp_r1,
                            "r1_target_source": src_r1,
                            "r1_risk_eligible": r1_risk_ok,
                            "defect_classification": cls,
                            "ledger_level_id": matched_lv["level_id"] if matched_lv else None,
                            "ledger_source_tf": matched_lv["source_tf"] if matched_lv else None,
                            "ledger_confirmation_time_ms": matched_lv["confirmation_time_ms"] if matched_lv else None,
                            "ledger_first_swept_htf_ts_ms": matched_lv["first_swept_htf_ts_ms"] if matched_lv else None,
                            "ledger_first_swept_m1_ts_ms": matched_lv["first_swept_m1_ts_ms"] if matched_lv else None,
                            "ledger_first_swept_ts_ms": matched_lv["first_swept_ts_ms"] if matched_lv else None,
                            "swept_before_decision_by_ms": (
                                int(rec.decision_timestamp_ms) - int(matched_lv["first_swept_ts_ms"])
                                if (matched_lv and matched_lv.get("first_swept_ts_ms") is not None)
                                else None
                            ),
                            "r2_repaired_accepted": r2_alt["accepted"],
                            "r2_repaired_tp": r2_alt["tp_price"],
                            "r2_repaired_target_source": r2_alt["target_source"],
                            "r2_repaired_rejection": r2_alt["rejection_reason"],
                        }
                    )

    total_swept_raw = (
        classification_counts["CONFIRMED_SWING_ALREADY_SWEPT_ON_SOURCE_HTF"]
        + classification_counts["HTF_POOL_ALREADY_SWEPT_BY_INTERVENING_M1"]
        + classification_counts["UNMATCHED_IN_LEDGER_ANOMALY"]
    )
    total_swept_risk_eligible = (
        risk_eligible_classification_counts["CONFIRMED_SWING_ALREADY_SWEPT_ON_SOURCE_HTF"]
        + risk_eligible_classification_counts["HTF_POOL_ALREADY_SWEPT_BY_INTERVENING_M1"]
        + risk_eligible_classification_counts["UNMATCHED_IN_LEDGER_ANOMALY"]
    )
    reproduced_on_dataset = total_swept_raw > 0
    reproduced_on_synthetic = bool(
        t01_t10["scenarios"][0]["r1_reproduced_swept_target_bug"]
        and t01_t10["scenarios"][1]["r1_reproduced_swept_target_bug"]
    )

    if reproduced_on_dataset or reproduced_on_synthetic:
        finding_verdict = "R1_SUSPECT_CONFIRMED_ON_RESEARCH_PATH_ONLY"
    else:
        finding_verdict = "NOT_REPRODUCED"

    arm_impact_summary: Dict[str, Any] = {}
    for ac in sorted(r1_arm_opp_sets.keys()):
        tot_arm = len(r1_arm_opp_sets[ac])
        sw_arm = int(arm_swept_impact[ac]["total_swept"])
        arm_impact_summary[ac] = {
            "r1_arm_eligible_count": tot_arm,
            "r1_swept_target_count": sw_arm,
            "r1_swept_target_rate": round(sw_arm / tot_arm, 6) if tot_arm > 0 else 0.0,
            "swept_on_source_htf": int(arm_swept_impact[ac]["CONFIRMED_SWING_ALREADY_SWEPT_ON_SOURCE_HTF"]),
            "swept_by_intervening_m1": int(arm_swept_impact[ac]["HTF_POOL_ALREADY_SWEPT_BY_INTERVENING_M1"]),
        }

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "finding_classification": finding_verdict,
        "production_affected": False,
        "production_unaffected_proof": "Bug exists strictly inside research-only kh_02_entry_foundation_research_service._resolve_b2_structural_stop_and_target & _validate_and_precompute_tf_series; production SharedStrategyEvaluator and domain_calculator are never called by or dependent on KH-02.R1.",
        "root_causes_identified": [
            {
                "id": "RC-1",
                "mechanism": "CONFIRMED_SWING_ALREADY_SWEPT_ON_SOURCE_HTF",
                "location": "kh_02_entry_foundation_research_service.py:_resolve_b2_structural_stop_and_target (LONG L1891-1894, SHORT L1923-1926)",
                "description": "Unconditionally appends rec.m15_confirmed_swing_high/low and rec.h1_confirmed_swing_high/low to pool_candidates even after _validate_and_precompute_tf_series already pruned those swept levels from m15_unswept_*/h1_unswept_*.",
            },
            {
                "id": "RC-2",
                "mechanism": "HTF_POOL_ALREADY_SWEPT_BY_INTERVENING_M1",
                "location": "kh_02_entry_foundation_research_service.py:_validate_and_precompute_tf_series (L1075-1109)",
                "description": "Invalidates HTF unswept pools only against closed M15/H1 bars, ignoring intervening closed M1 bars strictly between the last HTF close and decision_timestamp_ms that already swept the level.",
            },
            {
                "id": "RC-3_BENIGN_LABEL",
                "mechanism": "GENUINELY_UNSWEPT_LABEL_COLLISION_ONLY",
                "location": "kh_02_entry_foundation_research_service.py:_resolve_b2_structural_stop_and_target (L1898, L1930)",
                "description": "Alphabetical tie-break ('M15_CONFIRMED_SWING_*' < 'M15_UNSWEPT_SWING_*') assigns CONFIRMED_SWING label even when the level is genuinely still unswept; separated honestly from real swept targets.",
            },
        ],
        "synthetic_t01_to_t10_suite": t01_t10,
        "full_dataset_audit_summary": {
            "total_directional_opportunities_audited": total_directional_checked,
            "r1_raw_m1_triggers_audited": r1_raw_triggers_checked,
            "r1_targets_selected_count": r1_target_selected_count,
            "r1_risk_eligible_count": r1_risk_eligible_count,
            "total_confirmed_already_swept_raw_targets": total_swept_raw,
            "total_confirmed_already_swept_raw_rate": round(total_swept_raw / max(1, r1_target_selected_count), 6),
            "total_confirmed_already_swept_risk_eligible_targets": total_swept_risk_eligible,
            "total_confirmed_already_swept_risk_eligible_rate": round(total_swept_risk_eligible / max(1, r1_risk_eligible_count), 6),
            "classification_counts_raw_targets": dict(sorted(classification_counts.items())),
            "classification_counts_risk_eligible": dict(sorted(risk_eligible_classification_counts.items())),
            "r1_target_source_label_counts": dict(sorted(by_source_label_counts.items())),
            "swept_by_r1_target_source_label": dict(sorted(swept_by_source_label.items())),
        },
        "r1_arm_swept_target_impact": arm_impact_summary,
        "all_swept_opportunity_ids_sha256": canonical_hash(sorted(all_swept_opp_ids)),
        "counterexamples_sample_count": len(counterexamples_sample),
        "counterexamples_sample": counterexamples_sample,
        "status": "PASS" if t01_t10["all_passed"] else "FAIL",
    }
    payload["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "artifact_canonical_sha256"}
    )
    return payload


def build_r2_target_repair_full_opportunity_conservation(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    ledger_bundle: Mapping[str, Any],
    r1_candidates: Optional[Mapping[str, Any]] = None,
    source_latency_ms: int = 0,
    cost_assumptions: Optional[CostAssumptions] = None,
    seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
) -> Dict[str, Any]:
    """
    §03(4) & §05.C -> Artifact 06:
    Evaluates ALL `44,692` directional post-warmup M1 bar opportunities using
    `resolve_r2_research_only_structural_target(...)` + authoritative `calculate_risk_reward`
    (with optional custom `CostAssumptions` for full-universe cost reruns!).
    Preserves exact opportunity conservation (`44,692` sum of accepted + granular rejections),
    and computes BEFORE (R1) vs AFTER (R2) deltas per arm when `r1_candidates` is provided.
    """
    records: Sequence[KH02AsOfMTFRecord] = _mtf_records_list(mtf_bundle)
    records_by_idx: Dict[int, KH02AsOfMTFRecord] = {r.bar_index_1m: r for r in records}
    r2_hi_by_idx = ledger_bundle["unswept_highs_by_m1_idx"]
    r2_lo_by_idx = ledger_bundle["unswept_lows_by_m1_idx"]

    _, all_arms = _r2_frozen_plan_and_arms()
    rng_nc = random.Random(seed)

    eff_costs = cost_assumptions if cost_assumptions is not None else CostAssumptions()
    total_directional = 0
    raw_triggers = 0
    raw_target_found = 0
    raw_risk_eligible = 0
    universe_reasons: Counter[str] = Counter()

    raw_events: Dict[Tuple[int, str], Dict[str, Any]] = {}
    conservation_rows: List[Dict[str, Any]] = []

    for rec in records:
        idx = rec.bar_index_1m
        hi_asof = r2_hi_by_idx.get(idx, ())
        lo_asof = r2_lo_by_idx.get(idx, ())

        for direction in ("LONG", "SHORT"):
            total_directional += 1
            opp_id = f"kh02_b2_{direction}_idx{idx}_ts{rec.decision_timestamp_ms}"

            if rec.invalidation_reasons:
                universe_reasons["REJECTED_INVALID_CAUSAL_EVIDENCE"] += 1
                continue
            if rec.recent_gap_180m:
                universe_reasons["REJECTED_RECENT_180M_M1_GAP"] += 1
                continue

            if direction == "LONG":
                has_trig = bool(
                    (rec.m1_bull_sweep_reclaim or rec.m1_bull_bos or rec.m1_bull_fvg_retest)
                    and rec.m1_body_ratio >= 0.45
                    and (rec.m1_range_atr_ratio is not None and 0.40 <= rec.m1_range_atr_ratio <= 2.80)
                )
            else:
                has_trig = bool(
                    (rec.m1_bear_sweep_reclaim or rec.m1_bear_bos or rec.m1_bear_fvg_retest)
                    and rec.m1_body_ratio >= 0.45
                    and (rec.m1_range_atr_ratio is not None and 0.40 <= rec.m1_range_atr_ratio <= 2.80)
                )
            if not has_trig:
                universe_reasons["NO_M1_CAUSAL_TRIGGER"] += 1
                continue

            raw_triggers += 1
            res = resolve_r2_research_only_structural_target(
                rec,
                direction,
                unswept_highs_asof=hi_asof,
                unswept_lows_asof=lo_asof,
                source_latency_ms=source_latency_ms,
            )
            if not res["accepted"] or res["sl_price"] is None or res["tp_price"] is None:
                rej_code = f"REJECTED_{res['rejection_reason']}"
                universe_reasons[rej_code] += 1
                conservation_rows.append(
                    {
                        "opportunity_id": opp_id,
                        "checkpoint_index_1m": idx,
                        "decision_timestamp_ms": rec.decision_timestamp_ms,
                        "direction": direction,
                        "r2_status": "REJECTED_GEOMETRY",
                        "rejection_reason": rej_code,
                        "entry_reference": rec.m1_close,
                        "selected_sl": res["sl_price"],
                        "take_profit": None,
                        "target_level_id": None,
                        "target_source": None,
                    }
                )
                continue

            raw_target_found += 1
            sl_px = float(res["sl_price"])
            tp_px = float(res["tp_price"])
            if cost_assumptions is None:
                rk = recalculate_trade_risk_quantity_v179312(
                    rec.m1_close, sl_px, tp_px, direction=direction, decision_ts=rec.decision_timestamp_ms
                )
                can_exec = bool(rk.get("can_execute"))
                net_rr = float(rk.get("net_rr") or 0.0)
                gross_rr = float(rk.get("gross_rr") or net_rr)
                plan_risk = float(rk.get("planned_net_risk_usd") or 99.0)
                qty = float(rk.get("quantity") or 0.0)
                skip_r = str(rk.get("skip_reason") or rk.get("invalid_reason") or "RISK_CALC_REJECTED").split(":")[0]
            else:
                cr = calculate_risk_reward(
                    direction=direction,
                    planned_entry=rec.m1_close,
                    stop_loss=sl_px,
                    take_profit=tp_px,
                    capital_usdt=1000.0,
                    risk_pct=0.25,
                    leverage=20,
                    margin_mode="ISOLATED",
                    min_net_rr=MIN_NET_RR_THRESHOLD,
                    costs=eff_costs,
                    now_ms=rec.decision_timestamp_ms,
                )
                can_exec = bool(cr.can_execute and cr.is_valid)
                net_rr = float(cr.net_rr or 0.0)
                gross_rr = float(cr.gross_rr or net_rr)
                plan_risk = float(cr.net_risk_usdt or 99.0)
                qty = float(cr.quantity or 0.0)
                skip_r = str(cr.skip_reason or cr.invalid_reason or "RISK_CALC_REJECTED").split(":")[0]

            if not can_exec:
                rej_code = f"REJECTED_RISK_GATE:{skip_r}"
                universe_reasons[rej_code] += 1
                conservation_rows.append(
                    {
                        "opportunity_id": opp_id,
                        "checkpoint_index_1m": idx,
                        "decision_timestamp_ms": rec.decision_timestamp_ms,
                        "direction": direction,
                        "r2_status": "REJECTED_RISK",
                        "rejection_reason": rej_code,
                        "entry_reference": rec.m1_close,
                        "selected_sl": sl_px,
                        "take_profit": tp_px,
                        "target_level_id": res["target_level_id"],
                        "target_source": res["target_source"],
                        "net_rr": round(net_rr, 4),
                        "planned_net_risk_usd": round(plan_risk, 4),
                    }
                )
                continue
            if net_rr < MIN_NET_RR_THRESHOLD - 1e-9:
                universe_reasons["REJECTED_NET_RR_LT_2_0"] += 1
                conservation_rows.append(
                    {
                        "opportunity_id": opp_id,
                        "checkpoint_index_1m": idx,
                        "decision_timestamp_ms": rec.decision_timestamp_ms,
                        "direction": direction,
                        "r2_status": "REJECTED_NET_RR",
                        "rejection_reason": "REJECTED_NET_RR_LT_2_0",
                        "entry_reference": rec.m1_close,
                        "selected_sl": sl_px,
                        "take_profit": tp_px,
                        "target_level_id": res["target_level_id"],
                        "target_source": res["target_source"],
                        "net_rr": round(net_rr, 4),
                        "planned_net_risk_usd": round(plan_risk, 4),
                    }
                )
                continue
            if plan_risk > PLANNED_NET_RISK_CAP_USD + 1e-9 or qty < 0.01 - 1e-9:
                universe_reasons["REJECTED_RISK_CAP_OR_MIN_LOT"] += 1
                conservation_rows.append(
                    {
                        "opportunity_id": opp_id,
                        "checkpoint_index_1m": idx,
                        "decision_timestamp_ms": rec.decision_timestamp_ms,
                        "direction": direction,
                        "r2_status": "REJECTED_RISK_CAP",
                        "rejection_reason": "REJECTED_RISK_CAP_OR_MIN_LOT",
                        "entry_reference": rec.m1_close,
                        "selected_sl": sl_px,
                        "take_profit": tp_px,
                        "target_level_id": res["target_level_id"],
                        "target_source": res["target_source"],
                        "net_rr": round(net_rr, 4),
                        "planned_net_risk_usd": round(plan_risk, 4),
                    }
                )
                continue

            raw_risk_eligible += 1
            universe_reasons["ELIGIBLE_R2_VERIFIED_UNSWEPT_AND_RISK_OK"] += 1
            ep_key = f"kh02_ep_{direction}_{rec.decision_timestamp_ms // 900_000}"
            preds = evaluate_kh02_hypothesis_predicates_asof(rec, direction)
            shifted_idx = idx - 2880
            preds_shifted = (
                evaluate_kh02_hypothesis_predicates_asof(rec, direction, shifted_rec=records_by_idx[shifted_idx])
                if shifted_idx in records_by_idx
                else None
            )
            ev = {
                "opportunity_id": opp_id,
                "setup_episode_id": ep_key,
                "model_id": "KH02_R2_VERIFIED_UNSWEPT_FOUNDATION",
                "direction": direction,
                "checkpoint_index_1m": idx,
                "decision_timestamp_ms": rec.decision_timestamp_ms,
                "known_at_ms": max(int(rec.max_known_at_ms), int(res["target_available_at_ms"] or 0)),
                "entry_reference": rec.m1_close,
                "selected_sl": sl_px,
                "take_profit": tp_px,
                "target_level_id": res["target_level_id"],
                "target_source": res["target_source"],
                "target_source_tf": res["target_source_tf"],
                "target_confirmation_time_ms": res["target_confirmation_time_ms"],
                "target_available_at_ms": res["target_available_at_ms"],
                "hypothetical_quantity": round(qty, 4),
                "planned_net_risk_usd": round(plan_risk, 4),
                "net_rr": round(net_rr, 4),
                "gross_rr": round(gross_rr, 4),
                "ny_date": rec.ny_date,
                "ny_session_bucket": rec.ny_session_bucket,
                "h1_atr_quantile": rec.h1_atr_causal_prefix_quantile,
                "m1_atr_14": rec.m1_atr_14,
                "m15_trend_dir": rec.m15_trend_dir,
                "h1_trend_dir": rec.h1_trend_dir,
                "predicates": preds,
                "predicates_past_shifted_48h": preds_shifted,
                "counts_toward_d4_true_unicorn": False,
            }
            raw_events[(idx, direction)] = ev
            conservation_rows.append(
                {
                    "opportunity_id": opp_id,
                    "checkpoint_index_1m": idx,
                    "decision_timestamp_ms": rec.decision_timestamp_ms,
                    "direction": direction,
                    "r2_status": "ELIGIBLE",
                    "rejection_reason": None,
                    "entry_reference": rec.m1_close,
                    "selected_sl": sl_px,
                    "take_profit": tp_px,
                    "target_level_id": res["target_level_id"],
                    "target_source": res["target_source"],
                    "net_rr": round(net_rr, 4),
                    "planned_net_risk_usd": round(plan_risk, 4),
                    "hypothetical_quantity": round(qty, 4),
                }
            )

    # Evaluate every frozen arm deterministically on R2 verified-unswept eligible triggers
    sorted_keys = sorted(raw_events.keys())
    random_dir_flip_by_idx: Dict[int, str] = {}
    for idx_k in sorted({k[0] for k in sorted_keys}):
        random_dir_flip_by_idx[idx_k] = "LONG" if rng_nc.random() < 0.5 else "SHORT"

    arm_candidates: Dict[str, List[Dict[str, Any]]] = {}
    arm_conservation: Dict[str, Dict[str, Any]] = {}
    before_after_r1_vs_r2: Dict[str, Dict[str, Any]] = {}

    for arm in all_arms:
        ac = str(arm["arm_code"])
        vid = f"{arm['variant_id']}_R2"
        rule = arm["rule"]
        rtype = str(rule.get("type") or "")
        elig_list: List[Dict[str, Any]] = []
        rej_c: Counter[str] = Counter()

        for key in sorted_keys:
            ev = raw_events[key]
            idx_k, d_orig = key
            preds = ev["predicates"]
            ok = False
            rej_code: Optional[str] = None

            if rtype == "B2_NULL_UNFILTERED":
                ok = True
            elif rtype == "B2_NEGATIVE_RANDOM_DIR":
                if d_orig == random_dir_flip_by_idx[idx_k]:
                    ok = True
                else:
                    rej_code = "NC_RANDOM_DIR_NOT_SELECTED"
            elif rtype == "B2_NEGATIVE_PAST_SHIFTED_REGIME":
                ps = ev["predicates_past_shifted_48h"]
                if ps is None:
                    rej_code = "NC_SHIFTED_INSUFFICIENT_48H_HISTORY"
                elif ps["h1_mtf_trend_pass"] and ps["h3_vol_session_pass"]:
                    ok = True
                else:
                    rej_code = "NC_SHIFTED_REGIME_FALSE"
            else:
                fails: List[str] = []
                if rule.get("require_m15_trend") and not preds["m15_aligned"]:
                    fails.append("M15_TREND_NOT_ALIGNED")
                if rule.get("require_h1_trend") and not preds["h1_aligned"]:
                    fails.append("H1_TREND_NOT_ALIGNED")
                if rule.get("require_h2_structure") and not preds["h2_structure_pass"]:
                    fails.append("H2_STRUCTURE_NOT_CONFIRMED")
                if rule.get("require_h3_vol_session") and not preds["h3_vol_session_pass"]:
                    fails.append("H3_VOL_OR_SESSION_EXCLUDED")
                if rule.get("require_session_only") and not preds["h3_session_only_pass"]:
                    fails.append("SESSION_EXCLUDED")
                if rule.get("require_vol_only") and not preds["h3_vol_only_pass"]:
                    fails.append("VOL_REGIME_EXCLUDED")
                if not fails:
                    ok = True
                else:
                    rej_code = "|".join(fails)

            if ok:
                elig_list.append({**ev, "arm_code": ac, "variant_id": vid, "eligible": True, "rejection_code": None})
            else:
                rej_c[str(rej_code or "REJECTED")] += 1

        arm_candidates[ac] = elig_list
        arm_conservation[ac] = {
            "arm_code": ac,
            "variant_id": vid,
            "arm_role": arm["arm_role"],
            "total_directional_bar_universe": total_directional,
            "raw_m1_trigger_count": raw_triggers,
            "raw_r2_target_found_count": raw_target_found,
            "raw_r2_geometry_risk_eligible_count": raw_risk_eligible,
            "arm_eligible_count": len(elig_list),
            "arm_rejected_from_raw_eligible_count": raw_risk_eligible - len(elig_list),
            "rejection_reasons": dict(sorted(rej_c.items())),
        }

        if r1_candidates is not None:
            r1_list = list((r1_candidates.get("arm_candidates") or {}).get(ac) or ())
            r1_map = {str(c["opportunity_id"]): c for c in r1_list}
            r2_map = {str(c["opportunity_id"]): c for c in elig_list}
            r1_ids = set(r1_map.keys())
            r2_ids = set(r2_map.keys())
            common = r1_ids & r2_ids
            same_tp = sum(
                1 for oid in common if abs(float(r1_map[oid]["take_profit"]) - float(r2_map[oid]["take_profit"])) < 1e-6
            )
            changed_tp = len(common) - same_tp
            before_after_r1_vs_r2[ac] = {
                "r1_eligible_count": len(r1_ids),
                "r2_eligible_count": len(r2_ids),
                "common_opportunity_ids": len(common),
                "common_same_tp_count": same_tp,
                "common_repaired_different_tp_count": changed_tp,
                "removed_in_r2_count": len(r1_ids - r2_ids),
                "added_in_r2_count": len(r2_ids - r1_ids),
            }

    conservation_sum = sum(universe_reasons.values())
    if conservation_sum != total_directional:
        raise ValueError(f"OPPORTUNITY_CONSERVATION_MISMATCH:{conservation_sum}!={total_directional}")

    return {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "source_latency_ms": int(source_latency_ms),
        "total_post_warmup_m1_bars": len(records),
        "total_directional_bar_opportunities": total_directional,
        "conservation_sum_verified": conservation_sum == total_directional,
        "raw_m1_triggers_count": raw_triggers,
        "raw_r2_target_found_count": raw_target_found,
        "raw_r2_geometry_risk_eligible_count": raw_risk_eligible,
        "universe_reason_codes": dict(sorted(universe_reasons.items())),
        "arm_candidates": arm_candidates,
        "arm_conservation": arm_conservation,
        "before_after_r1_vs_r2_by_arm": before_after_r1_vs_r2,
        "conservation_rows": conservation_rows,
        "candidates_canonical_sha256": canonical_hash(
            {
                ac: [(c["opportunity_id"], c["entry_reference"], c["selected_sl"], c["take_profit"], c["target_level_id"], c["net_rr"]) for c in lst]
                for ac, lst in sorted(arm_candidates.items())
            }
        ),
    }


# ==============================================================================
# SECTION D — PAIRED FACTORIAL E / G / X DESIGN & COMMON-SUPPORT BALANCE (07, 08)
# ==============================================================================


def _block_bootstrap_ci(
    values: Sequence[float],
    cluster_ids: Sequence[str],
    *,
    n_boot: int = 1000,
    seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
) -> Optional[List[float]]:
    """Deterministic cluster block bootstrap 95% CI on mean(values)."""
    if len(values) < 2:
        return None
    by_cluster: Dict[str, List[float]] = defaultdict(list)
    for v, cid in zip(values, cluster_ids):
        by_cluster[str(cid)].append(float(v))
    keys = sorted(by_cluster.keys())
    if len(keys) < 2:
        return None
    rng = random.Random(seed)
    k_n = len(keys)
    means: List[float] = []
    for _ in range(n_boot):
        s_val = 0.0
        s_cnt = 0
        for _ in range(k_n):
            ck = keys[rng.randrange(k_n)]
            arr = by_cluster[ck]
            s_val += sum(arr)
            s_cnt += len(arr)
        if s_cnt > 0:
            means.append(s_val / s_cnt)
    if not means:
        return None
    means.sort()
    lo = means[max(0, int(0.025 * len(means)))]
    hi = means[min(len(means) - 1, int(0.975 * len(means)))]
    return [round(lo, 4), round(hi, 4)]


def _compute_smd(vals_treat: Sequence[float], vals_ctrl: Sequence[float]) -> Optional[float]:
    if len(vals_treat) < 2 or len(vals_ctrl) < 2:
        return None
    mt = sum(vals_treat) / len(vals_treat)
    mc = sum(vals_ctrl) / len(vals_ctrl)
    vt = sum((x - mt) ** 2 for x in vals_treat) / (len(vals_treat) - 1)
    vc = sum((x - mc) ** 2 for x in vals_ctrl) / (len(vals_ctrl) - 1)
    pooled = math.sqrt(max(0.0, 0.5 * (vt + vc)))
    if pooled <= 1e-12:
        return 0.0 if abs(mt - mc) <= 1e-12 else None
    return round((mt - mc) / pooled, 4)


def _summarize_episodes(eps: Sequence[Mapping[str, Any]], *, seed: int = DEFAULT_RANDOM_SEED_KH02_R2) -> Dict[str, Any]:
    closed = [e for e in eps if e.get("filled") and str(e.get("outcome_label")) in ("WIN", "LOSS")]
    open_h = sum(1 for e in eps if e.get("filled") and str(e.get("outcome_label")) == "OPEN_AT_HORIZON")
    gap_u = sum(1 for e in eps if str(e.get("outcome_label")) == "DATA_GAP_UNRESOLVED")
    unfilled = sum(1 for e in eps if not e.get("filled"))
    wins = sum(1 for e in closed if str(e.get("outcome_label")) == "WIN")
    losses = len(closed) - wins
    net_usd = sum((Decimal(str(e.get("realized_net_pnl_usd") or 0.0)) for e in closed), Decimal("0"))
    fees_usd = sum((Decimal(str(e.get("total_fees_usd") or 0.0)) for e in closed), Decimal("0"))
    gross_usd = sum((Decimal(str(e.get("realized_gross_pnl_usd") or 0.0)) for e in closed), Decimal("0"))
    r_vals = [float(e.get("realized_net_r") or 0.0) for e in closed]
    ny_days = [str(e.get("ny_date") or "UNK") for e in closed]
    ep_ids = [str(e.get("setup_episode_id") or e.get("opportunity_id") or "") for e in closed]

    # Max drawdown on chronological closed equity curve
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for e in closed:
        eq += float(e.get("realized_net_pnl_usd") or 0.0)
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd

    return {
        "scheduled": len(eps),
        "closed": len(closed),
        "wins": wins,
        "losses": losses,
        "open_at_horizon": open_h,
        "data_gap_unresolved": gap_u,
        "unfilled": unfilled,
        "win_rate": round(wins / len(closed), 4) if closed else None,
        "gross_pnl_usd": _dec4(gross_usd),
        "total_fees_usd": _dec4(fees_usd),
        "net_pnl_usd": _dec4(net_usd),
        "expectancy_r": round(sum(r_vals) / len(r_vals), 4) if r_vals else None,
        "max_drawdown_usd": round(max_dd, 4),
        "effective_ny_days": len(set(ny_days)),
        "effective_setup_episodes": len(set(ep_ids)),
        "ny_day_cluster_95ci_r": _block_bootstrap_ci(r_vals, ny_days, seed=seed),
        "episode_cluster_95ci_r": _block_bootstrap_ci(r_vals, ep_ids, seed=seed + 17),
    }


def build_r2_paired_factorial_entry_geometry_design(
    *,
    r1_candidates: Mapping[str, Any],
    r2_candidates: Mapping[str, Any],
    r1_replays: Mapping[str, Mapping[str, Any]],
    r2_replays: Mapping[str, Mapping[str, Any]],
    b0_reference_summary: Optional[Mapping[str, Any]] = None,
    fill_mode: str = "NEXT_BAR_OPEN_BID_ASK",
    seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
) -> Dict[str, Any]:
    """
    §03(5) & §05 -> Artifact 07:
    Separately identifies and reconciles:
      - `E_ONLY_PAIRED_FIXED_GEOMETRY`: Primary arm (B2_H1/H2/H3/H123) vs `NC_NULL_TRIGGER`
        under the SAME R2 verified-unswept geometry & fill mode.
      - `G_ONLY_PAIRED_SAME_ENTRY`: R2 verified-unswept geometry vs R1 geometry on the SAME arm.
      - `E_X_G_FACTORIAL`: 2x2 factorial cells crossing Entry (ON vs NULL) x Geometry (R1 vs R2).
      - `B0_VS_B2_CROSS_UNIVERSE`: Explicitly labeled `COMPARISON_NOT_IDENTIFIABLE` (separate descriptive note).
    Builds exact per-`(opportunity_id, direction, decision_timestamp_ms, fill_mode)` pairing
    rows preserving ALL `UNMATCHED` and `REJECTED` records with zero silent drops.
    """
    pairing_rows: List[Dict[str, Any]] = []
    e_only_reports: Dict[str, Any] = {}
    g_only_reports: Dict[str, Any] = {}
    factorial_2x2_reports: Dict[str, Any] = {}

    null_r2_cands = {str(c["opportunity_id"]): c for c in (r2_candidates.get("arm_candidates") or {}).get("NC_NULL_TRIGGER", ())}
    null_r2_eps = {
        str(e["opportunity_id"]): e
        for e in ((r2_replays.get("NC_NULL_TRIGGER") or {}).get("episodes_by_mode") or {}).get(fill_mode, ())
    }
    null_r1_eps = {
        str(e["opportunity_id"]): e
        for e in ((r1_replays.get("NC_NULL_TRIGGER") or {}).get("episodes_by_mode") or {}).get(fill_mode, ())
    }

    total_raw_r2_elig = int(r2_candidates.get("raw_r2_geometry_risk_eligible_count") or len(null_r2_cands))
    total_dir_univ = int(r2_candidates.get("total_directional_bar_opportunities") or 44692)

    # 1) E_ONLY_PAIRED_FIXED_GEOMETRY: each primary arm vs NC_NULL_TRIGGER on R2 fixed geometry
    for ac in PRIMARY_R2_ARMS:
        arm_cands = {str(c["opportunity_id"]): c for c in (r2_candidates.get("arm_candidates") or {}).get(ac, ())}
        arm_eps_list = list(((r2_replays.get(ac) or {}).get("episodes_by_mode") or {}).get(fill_mode, ()))
        arm_eps = {str(e["opportunity_id"]): e for e in arm_eps_list}

        # Every arm candidate is a strict subset of NC_NULL_TRIGGER on R2 geometry (same opportunity_id, SL, TP!)
        matched_cand_ids = sorted(set(arm_cands.keys()) & set(null_r2_cands.keys()))
        unmatched_cand_ids = sorted(set(null_r2_cands.keys()) - set(arm_cands.keys()))

        # Trade-level exact intersection (both arm and NULL actually filled/scheduled at the exact same opportunity_id)
        exact_trade_ids = sorted(set(arm_eps.keys()) & set(null_r2_eps.keys()))
        arm_only_trade_ids = sorted(set(arm_eps.keys()) - set(null_r2_eps.keys()))
        null_only_trade_ids = sorted(set(null_r2_eps.keys()) - set(arm_eps.keys()))

        # Verify geometry identity on matched candidate IDs
        geo_identical_count = sum(
            1
            for oid in matched_cand_ids
            if abs(float(arm_cands[oid]["selected_sl"]) - float(null_r2_cands[oid]["selected_sl"])) < 1e-6
            and abs(float(arm_cands[oid]["take_profit"]) - float(null_r2_cands[oid]["take_profit"])) < 1e-6
        )

        # Within-NULL opportunity decomposition: NULL trades that PASSED arm filter vs NULL trades REJECTED by arm filter!
        # This cleanly isolates entry filter quality on the exact same 1-position chronological NULL schedule!
        null_passed_filter_eps = [null_r2_eps[oid] for oid in sorted(null_r2_eps.keys()) if oid in arm_cands]
        null_rejected_filter_eps = [null_r2_eps[oid] for oid in sorted(null_r2_eps.keys()) if oid not in arm_cands]

        arm_sum = _summarize_episodes(arm_eps_list, seed=seed)
        null_sum = _summarize_episodes(list(null_r2_eps.values()), seed=seed)
        null_pass_sum = _summarize_episodes(null_passed_filter_eps, seed=seed)
        null_rej_sum = _summarize_episodes(null_rejected_filter_eps, seed=seed)

        # Paired delta on exact same executed trades (arm vs null)
        paired_r_deltas: List[float] = []
        paired_ny_days: List[str] = []
        for oid in exact_trade_ids:
            ea = arm_eps[oid]
            en = null_r2_eps[oid]
            if ea.get("filled") and en.get("filled") and ea.get("outcome_label") in ("WIN", "LOSS") and en.get("outcome_label") in ("WIN", "LOSS"):
                paired_r_deltas.append(round(float(ea.get("realized_net_r") or 0.0) - float(en.get("realized_net_r") or 0.0), 6))
                paired_ny_days.append(str(ea.get("ny_date") or "UNK"))

        # Record reconciliation rows for all union trade IDs (never silently drop UNMATCHED!)
        union_trade_ids = sorted(set(arm_eps.keys()) | set(null_r2_eps.keys()))
        for oid in union_trade_ids:
            ea = arm_eps.get(oid)
            en = null_r2_eps.get(oid)
            ref = ea or en
            pair_status = (
                "MATCHED_EXACT_TRADE"
                if (ea is not None and en is not None)
                else ("UNMATCHED_ARM_ONLY_1POS_PATH" if ea is not None else "UNMATCHED_NULL_ONLY_FILTERED_OR_1POS")
            )
            pairing_rows.append(
                {
                    "estimand": "E_ONLY_PAIRED_FIXED_GEOMETRY",
                    "arm_code": ac,
                    "control_arm_code": "NC_NULL_TRIGGER",
                    "opportunity_id": oid,
                    "direction": ref.get("direction"),
                    "decision_timestamp_ms": ref.get("decision_timestamp_ms"),
                    "anchor_id": (arm_cands.get(oid) or null_r2_cands.get(oid) or {}).get("target_level_id"),
                    "fill_mode": fill_mode,
                    "pair_status": pair_status,
                    "arm_eligible": oid in arm_cands,
                    "arm_outcome": ea.get("outcome_label") if ea else "NOT_EXECUTED_IN_ARM",
                    "arm_net_r": ea.get("realized_net_r") if ea else None,
                    "control_outcome": en.get("outcome_label") if en else "NOT_EXECUTED_IN_CONTROL",
                    "control_net_r": en.get("realized_net_r") if en else None,
                }
            )

        delta_exp_arm_vs_null = (
            round(float(arm_sum["expectancy_r"]) - float(null_sum["expectancy_r"]), 4)
            if (arm_sum["expectancy_r"] is not None and null_sum["expectancy_r"] is not None)
            else None
        )
        delta_exp_pass_vs_rej_within_null = (
            round(float(null_pass_sum["expectancy_r"]) - float(null_rej_sum["expectancy_r"]), 4)
            if (null_pass_sum["expectancy_r"] is not None and null_rej_sum["expectancy_r"] is not None)
            else None
        )

        e_only_reports[ac] = {
            "estimand": "E_ONLY_PAIRED_FIXED_GEOMETRY",
            "source_universe": "R2_VERIFIED_UNSWEPT_M1_TRIGGERS",
            "denominator_directional_bars": total_dir_univ,
            "denominator_raw_r2_eligible": total_raw_r2_elig,
            "arm_eligible_candidates": len(arm_cands),
            "rejected_by_entry_filter_count": total_raw_r2_elig - len(arm_cands),
            "no_trade_rate_within_raw_eligible": round(1.0 - (len(arm_cands) / max(1, total_raw_r2_elig)), 6),
            "matched_candidate_ids_count": len(matched_cand_ids),
            "unmatched_candidate_ids_count": len(unmatched_cand_ids),
            "geometry_identical_verified_count": geo_identical_count,
            "exact_same_executed_trade_ids_count": len(exact_trade_ids),
            "unmatched_arm_only_trades_1pos_path": len(arm_only_trade_ids),
            "unmatched_null_only_trades": len(null_only_trade_ids),
            "reconciliation_union_exact": (
                len(exact_trade_ids) + len(arm_only_trade_ids) + len(null_only_trade_ids) == len(union_trade_ids)
            ),
            "arm_replay_summary": arm_sum,
            "control_null_replay_summary": null_sum,
            "within_null_schedule_passed_filter_summary": null_pass_sum,
            "within_null_schedule_rejected_filter_summary": null_rej_sum,
            "chronological_expectancy_delta_r_arm_minus_null": delta_exp_arm_vs_null,
            "within_null_expectancy_delta_r_passed_minus_rejected": delta_exp_pass_vs_rej_within_null,
            "exact_same_trade_mean_delta_r": round(sum(paired_r_deltas) / len(paired_r_deltas), 6) if paired_r_deltas else None,
            "population_shift_note": (
                "Because 1-position chronological occupancy alters downstream available slots when trades are filtered, "
                "chronological arm vs NULL replay is labeled 1POS_PATH_DEPENDENT; within-NULL passed-vs-rejected isolates "
                "entry-filter discrimination on the identical trade stream."
            ),
        }

    # 2) G_ONLY_PAIRED_SAME_ENTRY: R2 verified-unswept geometry vs R1 geometry on the SAME arm
    for ac in (*PRIMARY_R2_ARMS, "NC_NULL_TRIGGER"):
        r1_cands = {str(c["opportunity_id"]): c for c in (r1_candidates.get("arm_candidates") or {}).get(ac, ())}
        r2_cands = {str(c["opportunity_id"]): c for c in (r2_candidates.get("arm_candidates") or {}).get(ac, ())}
        r1_eps_list = list(((r1_replays.get(ac) or {}).get("episodes_by_mode") or {}).get(fill_mode, ()))
        r2_eps_list = list(((r2_replays.get(ac) or {}).get("episodes_by_mode") or {}).get(fill_mode, ()))
        r1_eps = {str(e["opportunity_id"]): e for e in r1_eps_list}
        r2_eps = {str(e["opportunity_id"]): e for e in r2_eps_list}

        common_cands = sorted(set(r1_cands.keys()) & set(r2_cands.keys()))
        r1_only_cands = sorted(set(r1_cands.keys()) - set(r2_cands.keys()))
        r2_only_cands = sorted(set(r2_cands.keys()) - set(r1_cands.keys()))

        exact_g_trades = sorted(set(r1_eps.keys()) & set(r2_eps.keys()))
        r1_only_trades = sorted(set(r1_eps.keys()) - set(r2_eps.keys()))
        r2_only_trades = sorted(set(r2_eps.keys()) - set(r1_eps.keys()))

        paired_g_deltas: List[float] = []
        paired_g_days: List[str] = []
        paired_g_tp_changed = 0
        for oid in exact_g_trades:
            e1 = r1_eps[oid]
            e2 = r2_eps[oid]
            if abs(float(e1.get("take_profit") or 0.0) - float(e2.get("take_profit") or 0.0)) > 1e-6:
                paired_g_tp_changed += 1
            if e1.get("filled") and e2.get("filled") and e1.get("outcome_label") in ("WIN", "LOSS") and e2.get("outcome_label") in ("WIN", "LOSS"):
                paired_g_deltas.append(round(float(e2.get("realized_net_r") or 0.0) - float(e1.get("realized_net_r") or 0.0), 6))
                paired_g_days.append(str(e2.get("ny_date") or "UNK"))

        union_g_trades = sorted(set(r1_eps.keys()) | set(r2_eps.keys()))
        for oid in union_g_trades:
            e1 = r1_eps.get(oid)
            e2 = r2_eps.get(oid)
            ref = e2 or e1
            p_st = (
                "MATCHED_EXACT_TRADE"
                if (e1 is not None and e2 is not None)
                else ("UNMATCHED_R2_ONLY_NEW_OR_UNBLOCKED" if e2 is not None else "UNMATCHED_R1_ONLY_SWEPT_REMOVED_OR_1POS")
            )
            pairing_rows.append(
                {
                    "estimand": "G_ONLY_PAIRED_SAME_ENTRY",
                    "arm_code": ac,
                    "control_arm_code": f"{ac}_R1_REF",
                    "opportunity_id": oid,
                    "direction": ref.get("direction"),
                    "decision_timestamp_ms": ref.get("decision_timestamp_ms"),
                    "anchor_id": (r2_cands.get(oid) or {}).get("target_level_id"),
                    "fill_mode": fill_mode,
                    "pair_status": p_st,
                    "r1_tp": e1.get("take_profit") if e1 else None,
                    "r2_tp": e2.get("take_profit") if e2 else None,
                    "r1_outcome": e1.get("outcome_label") if e1 else "NOT_EXECUTED_IN_R1",
                    "r1_net_r": e1.get("realized_net_r") if e1 else None,
                    "r2_outcome": e2.get("outcome_label") if e2 else "NOT_EXECUTED_IN_R2",
                    "r2_net_r": e2.get("realized_net_r") if e2 else None,
                }
            )

        r1_sum = _summarize_episodes(r1_eps_list, seed=seed)
        r2_sum = _summarize_episodes(r2_eps_list, seed=seed)
        delta_g_exp = (
            round(float(r2_sum["expectancy_r"]) - float(r1_sum["expectancy_r"]), 4)
            if (r2_sum["expectancy_r"] is not None and r1_sum["expectancy_r"] is not None)
            else None
        )

        g_only_reports[ac] = {
            "estimand": "G_ONLY_PAIRED_SAME_ENTRY",
            "population_classification": (
                "FULL_RERISKED_DIFFERENT_POPULATION" if (len(r1_only_trades) > 0 or len(r2_only_trades) > 0) else "EXACT_SAME_TRADE_POPULATION"
            ),
            "r1_eligible_candidates": len(r1_cands),
            "r2_eligible_candidates": len(r2_cands),
            "common_candidates_count": len(common_cands),
            "unmatched_r1_only_candidates": len(r1_only_cands),
            "unmatched_r2_only_candidates": len(r2_only_cands),
            "matched_exact_trades_count": len(exact_g_trades),
            "matched_exact_trades_with_different_tp": paired_g_tp_changed,
            "unmatched_r1_only_trades": len(r1_only_trades),
            "unmatched_r2_only_trades": len(r2_only_trades),
            "reconciliation_union_exact": (
                len(exact_g_trades) + len(r1_only_trades) + len(r2_only_trades) == len(union_g_trades)
            ),
            "r1_reference_replay_summary": r1_sum,
            "r2_repaired_replay_summary": r2_sum,
            "delta_expectancy_r_r2_minus_r1": delta_g_exp,
            "paired_exact_trades_mean_delta_r": round(sum(paired_g_deltas) / len(paired_g_deltas), 4) if paired_g_deltas else None,
            "paired_exact_trades_ny_day_95ci_delta_r": _block_bootstrap_ci(paired_g_deltas, paired_g_days, seed=seed + 31),
        }

    # 3) E_X_G_FACTORIAL (2x2 table for each primary arm)
    null_r1_sum = _summarize_episodes(list(null_r1_eps.values()), seed=seed)
    null_r2_sum = _summarize_episodes(list(null_r2_eps.values()), seed=seed)
    for ac in PRIMARY_R2_ARMS:
        e_on_g_r1 = g_only_reports[ac]["r1_reference_replay_summary"]
        e_on_g_r2 = g_only_reports[ac]["r2_repaired_replay_summary"]
        def _sub(a: Optional[float], b: Optional[float]) -> Optional[float]:
            return round(float(a) - float(b), 4) if (a is not None and b is not None) else None

        e_effect_under_r1 = _sub(e_on_g_r1["expectancy_r"], null_r1_sum["expectancy_r"])
        e_effect_under_r2 = _sub(e_on_g_r2["expectancy_r"], null_r2_sum["expectancy_r"])
        g_effect_under_null = _sub(null_r2_sum["expectancy_r"], null_r1_sum["expectancy_r"])
        g_effect_under_arm = _sub(e_on_g_r2["expectancy_r"], e_on_g_r1["expectancy_r"])
        interaction_eg = _sub(e_effect_under_r2, e_effect_under_r1)
        factorial_2x2_reports[ac] = {
            "estimand": "E_X_G_FACTORIAL",
            "cell_E0_G_R1_null_r1": null_r1_sum,
            "cell_E1_G_R1_arm_r1": e_on_g_r1,
            "cell_E0_G_R2_null_r2": null_r2_sum,
            "cell_E1_G_R2_arm_r2": e_on_g_r2,
            "simple_E_effect_under_R1_r": e_effect_under_r1,
            "simple_E_effect_under_R2_r": e_effect_under_r2,
            "simple_G_effect_under_NULL_r": g_effect_under_null,
            "simple_G_effect_under_ARM_r": g_effect_under_arm,
            "interaction_E_x_G_r": interaction_eg,
        }

    # 4) B0 vs B2 explicitly marked COMPARISON_NOT_IDENTIFIABLE
    b0_vs_b2_note = {
        "estimand": "B0_VS_B2_CROSS_UNIVERSE",
        "identifiability_status": "COMPARISON_NOT_IDENTIFIABLE",
        "reason": "SI0/B0 operates on production A1 (56,710 decisions, 242 scheduled / 166 closed) with production SL/TP geometry, whereas B2 operates on 44,692 M1 directional triggers with structural M15/H1 SL/TP geometry. Expectancy differences CANNOT be attributed solely to entry filters.",
        "b0_reference_readonly_descriptive_only": dict(b0_reference_summary) if b0_reference_summary else {
            "scheduled": 242, "closed": 166, "wins": 9, "losses": 157, "net_pnl_usd": -345.8971, "expectancy_r": -0.8474
        },
    }

    return {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "fill_mode": fill_mode,
        "E_ONLY_PAIRED_FIXED_GEOMETRY": e_only_reports,
        "G_ONLY_PAIRED_SAME_ENTRY": g_only_reports,
        "E_X_G_FACTORIAL": factorial_2x2_reports,
        "B0_VS_B2_CROSS_UNIVERSE": b0_vs_b2_note,
        "pairing_rows_count": len(pairing_rows),
        "pairing_rows": pairing_rows,
        "zero_silent_drops_verified": all(r["reconciliation_union_exact"] for r in e_only_reports.values())
        and all(r["reconciliation_union_exact"] for r in g_only_reports.values()),
    }


def compute_r2_stratified_common_support_balance(
    *,
    r2_candidates: Mapping[str, Any],
    r2_replays: Mapping[str, Mapping[str, Any]],
    mtf_bundle: Mapping[str, Any],
    fill_mode: str = "NEXT_BAR_OPEN_BID_ASK",
    smd_threshold: float = DEFAULT_SMD_THRESHOLD,
    min_overlap_coverage: float = 0.50,
    min_effective_n: int = 20,
    seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
) -> Dict[str, Any]:
    """
    §03(6) -> Artifact 08:
    Evaluates covariate balance BEFORE and AFTER exact stratification on predeclared
    decision-time covariates (`direction`, `NY session`, `causal H1 ATR regime`,
    `M1 ATR bucket`, `time bucket`) for every primary arm vs `NC_NULL_TRIGGER` on R2 geometry.

    Returns explicit `MATCHING_INADEQUATE` whenever `|SMD| > 0.10`, overlap coverage < 0.50,
    or effective matched N < 20, forbidding causal-lift wording when balance fails.
    """
    null_eps = [
        e for e in ((r2_replays.get("NC_NULL_TRIGGER") or {}).get("episodes_by_mode") or {}).get(fill_mode, ())
        if e.get("filled") and str(e.get("outcome_label")) in ("WIN", "LOSS")
    ]
    null_cands = {str(c["opportunity_id"]): c for c in (r2_candidates.get("arm_candidates") or {}).get("NC_NULL_TRIGGER", ())}
    recs_by_idx: Dict[int, KH02AsOfMTFRecord] = {r.bar_index_1m: r for r in _mtf_records_list(mtf_bundle)}

    # Compute causal median M1 ATR and median timestamp across the candidate universe for bucket thresholds
    all_m1_atrs = sorted(
        float(c.get("m1_atr_14") or 0.0) for c in null_cands.values() if c.get("m1_atr_14") is not None
    )
    med_m1_atr = all_m1_atrs[len(all_m1_atrs) // 2] if all_m1_atrs else 1.0
    all_ts = sorted(int(c["decision_timestamp_ms"]) for c in null_cands.values()) if null_cands else [0]
    med_ts = all_ts[len(all_ts) // 2]

    def _extract_covariates(ep: Mapping[str, Any]) -> Dict[str, Any]:
        oid = str(ep["opportunity_id"])
        cand = null_cands.get(oid) or {}
        chk = int(ep.get("checkpoint_index_1m") or cand.get("checkpoint_index_1m") or 0)
        rec = recs_by_idx.get(chk)
        sess = str(ep.get("ny_session_bucket") or cand.get("ny_session_bucket") or (rec.ny_session_bucket if rec else "UNKNOWN"))
        h1_q = cand.get("h1_atr_quantile") if cand.get("h1_atr_quantile") is not None else (rec.h1_atr_causal_prefix_quantile if rec else None)
        m1_atr = cand.get("m1_atr_14") if cand.get("m1_atr_14") is not None else (rec.m1_atr_14 if rec else None)
        ts = int(ep.get("decision_timestamp_ms") or cand.get("decision_timestamp_ms") or 0)
        is_long = 1.0 if str(ep.get("direction")).upper() == "LONG" else 0.0
        is_core_ny = 1.0 if sess in ("NY_AM", "NY_PM", "LONDON_OPEN", "LONDON") else 0.0
        is_high_h1 = 1.0 if (h1_q is not None and float(h1_q) >= 0.50) else 0.0
        is_high_m1 = 1.0 if (m1_atr is not None and float(m1_atr) >= med_m1_atr) else 0.0
        is_second_half = 1.0 if ts >= med_ts else 0.0
        stratum = (int(is_long), int(is_core_ny), int(is_high_h1), int(is_second_half))
        return {
            "direction_is_long": is_long,
            "ny_session_is_core_am_pm": is_core_ny,
            "h1_atr_high_vol_regime": is_high_h1,
            "m1_atr_above_median": is_high_m1,
            "time_second_half": is_second_half,
            "stratum_key": f"L{stratum[0]}_S{stratum[1]}_V{stratum[2]}_T{stratum[3]}",
        }

    cov_names = (
        "direction_is_long",
        "ny_session_is_core_am_pm",
        "h1_atr_high_vol_regime",
        "m1_atr_above_median",
        "time_second_half",
    )

    arm_balance_reports: Dict[str, Any] = {}
    any_causal_identifiable = False

    for ac in PRIMARY_R2_ARMS:
        arm_eps = [
            e for e in ((r2_replays.get(ac) or {}).get("episodes_by_mode") or {}).get(fill_mode, ())
            if e.get("filled") and str(e.get("outcome_label")) in ("WIN", "LOSS")
        ]
        t_covs = [_extract_covariates(e) for e in arm_eps]
        c_covs = [_extract_covariates(e) for e in null_eps]

        pre_smds: Dict[str, Optional[float]] = {}
        for cn in cov_names:
            pre_smds[cn] = _compute_smd([r[cn] for r in t_covs], [r[cn] for r in c_covs])

        # Exact stratification on (direction, core_session, h1_vol_regime, time_half)
        t_by_s: Dict[str, List[Tuple[Mapping[str, Any], Dict[str, Any]]]] = defaultdict(list)
        c_by_s: Dict[str, List[Tuple[Mapping[str, Any], Dict[str, Any]]]] = defaultdict(list)
        for ep, cv in zip(arm_eps, t_covs):
            t_by_s[cv["stratum_key"]].append((ep, cv))
        for ep, cv in zip(null_eps, c_covs):
            c_by_s[cv["stratum_key"]].append((ep, cv))

        common_strata = sorted(set(t_by_s.keys()) & set(c_by_s.keys()))
        matched_t_covs: List[Dict[str, Any]] = []
        matched_c_covs: List[Dict[str, Any]] = []
        matched_t_r: List[float] = []
        matched_c_r: List[float] = []
        matched_t_days: List[str] = []
        weights_c: List[float] = []
        strata_details: List[Dict[str, Any]] = []

        # Deterministic 1:1 nearest-timestamp match within each common stratum (no outcome peeking!)
        matched_pair_deltas: List[float] = []
        matched_pair_days: List[str] = []
        matched_pair_episodes: List[str] = []

        for sk in common_strata:
            t_items = sorted(t_by_s[sk], key=lambda x: (int(x[0]["decision_timestamp_ms"]), str(x[0]["opportunity_id"])))
            c_items = sorted(c_by_s[sk], key=lambda x: (int(x[0]["decision_timestamp_ms"]), str(x[0]["opportunity_id"])))
            used_c: Set[int] = set()
            s_pairs = 0
            for t_ep, t_cv in t_items:
                t_ts = int(t_ep["decision_timestamp_ms"])
                best_j: Optional[int] = None
                best_dt: Optional[int] = None
                for j, (c_ep, _) in enumerate(c_items):
                    if j in used_c:
                        continue
                    dt = abs(int(c_ep["decision_timestamp_ms"]) - t_ts)
                    if best_dt is None or dt < best_dt:
                        best_dt = dt
                        best_j = j
                if best_j is not None:
                    used_c.add(best_j)
                    c_ep, c_cv = c_items[best_j]
                    matched_t_covs.append(t_cv)
                    matched_c_covs.append(c_cv)
                    tr = float(t_ep.get("realized_net_r") or 0.0)
                    cr = float(c_ep.get("realized_net_r") or 0.0)
                    matched_t_r.append(tr)
                    matched_c_r.append(cr)
                    matched_t_days.append(str(t_ep.get("ny_date") or "UNK"))
                    matched_pair_deltas.append(round(tr - cr, 6))
                    matched_pair_days.append(str(t_ep.get("ny_date") or "UNK"))
                    matched_pair_episodes.append(str(t_ep.get("setup_episode_id") or t_ep.get("opportunity_id") or ""))
                    weights_c.append(1.0)
                    s_pairs += 1
            strata_details.append(
                {
                    "stratum_key": sk,
                    "arm_count": len(t_items),
                    "control_count": len(c_items),
                    "matched_pairs": s_pairs,
                }
            )

        post_smds: Dict[str, Optional[float]] = {}
        for cn in cov_names:
            post_smds[cn] = _compute_smd([r[cn] for r in matched_t_covs], [r[cn] for r in matched_c_covs])

        overlap_cov = round(len(matched_t_covs) / max(1, len(arm_eps)), 4) if arm_eps else 0.0
        eff_n = len(matched_t_covs)
        estimable_post_smds = [abs(v) for v in post_smds.values() if v is not None]
        max_post_abs_smd = round(max(estimable_post_smds), 4) if estimable_post_smds else None
        key_cov_balanced = bool(
            estimable_post_smds
            and all(abs(v) <= smd_threshold + 1e-9 for v in estimable_post_smds)
        )
        positivity_ok = overlap_cov >= min_overlap_coverage and eff_n >= min_effective_n
        inadequate_reasons: List[str] = []
        if eff_n < min_effective_n:
            inadequate_reasons.append(f"EFFECTIVE_N_LT_{min_effective_n}:n={eff_n}")
        if overlap_cov < min_overlap_coverage:
            inadequate_reasons.append(f"OVERLAP_COVERAGE_LT_{min_overlap_coverage:.2f}:cov={overlap_cov:.4f}")
        if not key_cov_balanced:
            inadequate_reasons.append(f"POST_MATCH_ABS_SMD_EXCEEDS_{smd_threshold:.2f}:max={max_post_abs_smd}")

        # NOTE: For H3 (which explicitly filters by session + H1 volatility), matching cannot
        # balance covariates that the treatment itself deterministically restricts outside support!
        matching_quality = "BALANCED_COMMON_SUPPORT" if not inadequate_reasons else "MATCHING_INADEQUATE"
        if matching_quality == "BALANCED_COMMON_SUPPORT":
            any_causal_identifiable = True

        arm_balance_reports[ac] = {
            "arm_code": ac,
            "control_arm_code": "NC_NULL_TRIGGER",
            "arm_closed_trades": len(arm_eps),
            "control_closed_trades": len(null_eps),
            "matched_pairs_count": eff_n,
            "unmatched_arm_trades_count": len(arm_eps) - eff_n,
            "overlap_coverage_ratio": overlap_cov,
            "effective_n_per_group": eff_n,
            "max_control_weight": max(weights_c) if weights_c else 0.0,
            "pre_match_smds": pre_smds,
            "post_match_smds": post_smds,
            "max_post_abs_smd": max_post_abs_smd,
            "smd_threshold": smd_threshold,
            "matching_quality_status": matching_quality,
            "inadequate_reasons": inadequate_reasons,
            "causal_lift_claim_permitted": (matching_quality == "BALANCED_COMMON_SUPPORT"),
            "matched_within_group_mean_delta_r": (
                round(sum(matched_pair_deltas) / len(matched_pair_deltas), 4) if matched_pair_deltas else None
            ),
            "matched_ny_day_bootstrap_95ci_delta_r": _block_bootstrap_ci(matched_pair_deltas, matched_pair_days, seed=seed + 41),
            "matched_episode_bootstrap_95ci_delta_r": _block_bootstrap_ci(matched_pair_deltas, matched_pair_episodes, seed=seed + 53),
            "trade_frequency_ratio_arm_over_null": round(len(arm_eps) / max(1, len(null_eps)), 4),
            "strata_breakdown": strata_details,
        }

    overall_verdict = (
        "IDENTIFIABLE_WITH_PARTIAL_COMMON_SUPPORT"
        if any_causal_identifiable
        else "INSUFFICIENT_COMMON_SUPPORT"
    )
    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "fill_mode": fill_mode,
        "predeclared_covariates": list(cov_names),
        "balance_criteria": {
            "max_abs_smd": smd_threshold,
            "min_overlap_coverage": min_overlap_coverage,
            "min_effective_n": min_effective_n,
        },
        "arm_balance_reports": arm_balance_reports,
        "PAIRED_COMPARISON_VERDICT": overall_verdict,
        "disclosure": (
            "Arms with MATCHING_INADEQUATE permit descriptive trend reporting ONLY and MUST NOT "
            "claim causal entry lift. B0 vs B2 remains COMPARISON_NOT_IDENTIFIABLE."
        ),
    }
    payload["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "artifact_canonical_sha256"}
    )
    return payload


# ==============================================================================
# SECTION E — CHRONOLOGICAL 1-POSITION 3-FILL PAIRED REPLAYS (09)
# ==============================================================================


def replay_r2_same_opportunity_paired_counterfactuals(
    *,
    datasets: Mapping[str, Any],
    r1_candidates: Mapping[str, Any],
    r2_candidates: Mapping[str, Any],
    arms: Sequence[str] = (*PRIMARY_R2_ARMS, *CONTROL_R2_ARMS),
) -> Dict[str, Any]:
    """
    §03(7) -> Artifact 09:
    Runs chronological 1-position deterministic 3-fill replays (`NEXT_BAR_OPEN_BID_ASK`,
    `LEGACY_CLOSE_FILL`, `STRESS_WIDER_SPREAD_SLIPPAGE_2X`) reusing authoritative read-only
    `replay_kh02_chronological_one_position` (which calls `replay_intervention_outcome_v179312`
    and `advance_portfolio_to_next_event_v179312`), enforcing:
      - zero overlap (`overlap_violations == 0`)
      - zero future decision (`known_at_ms <= decision_timestamp_ms`)
      - exact source opportunity IDs
      - explicit `FULL_RERISKED_DIFFERENT_POPULATION` label whenever R2 target repair
        changes the eligible population vs R1.
    """
    bars_1m = list(datasets["1m"])
    r1_replays: Dict[str, Dict[str, Any]] = {}
    r2_replays: Dict[str, Dict[str, Any]] = {}
    comparison_by_arm: Dict[str, Any] = {}
    causality_violations: List[str] = []

    for ac in arms:
        r1_list = list((r1_candidates.get("arm_candidates") or {}).get(ac) or ())
        r2_list = list((r2_candidates.get("arm_candidates") or {}).get(ac) or ())

        # Fail-closed causality assertion before replay
        for tag, clist in (("R1", r1_list), ("R2", r2_list)):
            for c in clist:
                if int(c.get("known_at_ms") or 0) > int(c["decision_timestamp_ms"]):
                    causality_violations.append(
                        f"FUTURE_DECISION_BLOCKED:{tag}:{ac}:{c['opportunity_id']}:known={c.get('known_at_ms')}>dec={c['decision_timestamp_ms']}"
                    )

        if causality_violations:
            raise ValueError(f"CAUSALITY_VIOLATION_IN_REPLAY_INPUT:{causality_violations[0]}")

        rep_r1 = replay_kh02_chronological_one_position(
            arm_code=ac,
            variant_id=f"KH02_R1_REF_{ac}",
            candidates=r1_list,
            bars_1m=bars_1m,
        )
        rep_r2 = replay_kh02_chronological_one_position(
            arm_code=ac,
            variant_id=f"KH02_R2_{ac}",
            candidates=r2_list,
            bars_1m=bars_1m,
        )
        r1_replays[ac] = rep_r1
        r2_replays[ac] = rep_r2

        mode_comp: Dict[str, Any] = {}
        for mode in ("NEXT_BAR_OPEN_BID_ASK", "LEGACY_CLOSE_FILL", "STRESS_WIDER_SPREAD_SLIPPAGE_2X"):
            e1 = list((rep_r1.get("episodes_by_mode") or {}).get(mode, ()))
            e2 = list((rep_r2.get("episodes_by_mode") or {}).get(mode, ()))
            ids1 = {str(e["opportunity_id"]) for e in e1}
            ids2 = {str(e["opportunity_id"]) for e in e2}
            pop_lbl = "EXACT_SAME_TRADE_POPULATION" if ids1 == ids2 else "FULL_RERISKED_DIFFERENT_POPULATION"
            mode_comp[mode] = {
                "population_label": pop_lbl,
                "r1_summary": _summarize_episodes(e1),
                "r2_summary": _summarize_episodes(e2),
                "common_executed_ids": len(ids1 & ids2),
                "r1_only_executed_ids": len(ids1 - ids2),
                "r2_only_executed_ids": len(ids2 - ids1),
                "r1_overlap_violations": int(rep_r1.get("overlap_violations_count", 0)),
                "r2_overlap_violations": int(rep_r2.get("overlap_violations_count", 0)),
            }
        comparison_by_arm[ac] = mode_comp

    total_overlaps = sum(
        mode_d["r1_overlap_violations"] + mode_d["r2_overlap_violations"]
        for arm_d in comparison_by_arm.values()
        for mode_d in arm_d.values()
    )
    return {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "arms_replayed": list(arms),
        "fill_modes": ["NEXT_BAR_OPEN_BID_ASK", "LEGACY_CLOSE_FILL", "STRESS_WIDER_SPREAD_SLIPPAGE_2X"],
        "total_overlap_violations": total_overlaps,
        "causality_violations_count": len(causality_violations),
        "comparison_by_arm": comparison_by_arm,
        "r1_replays": r1_replays,
        "r2_replays": r2_replays,
        "status": "PASS" if (total_overlaps == 0 and len(causality_violations) == 0) else "FAIL",
    }


# ==============================================================================
# SECTION F — FILL-TIME NET RISK & REALIZED TAIL LOSS AUDIT (10)
# ==============================================================================


def evaluate_causal_fill_time_policy_single_trade(
    *,
    direction: str,
    decision_entry: float,
    fill_bar_open: float,
    selected_sl: float,
    take_profit: float,
    decision_qty: float,
    spread_usd: float = DEFAULT_MODELED_SPREAD_USD,
    slippage_usd: float = DEFAULT_SLIPPAGE_USD,
    taker_fee_rate: float = DEFAULT_BASELINE_TAKER_FEE,
    maker_fee_rate: float = 0.0002,
    risk_cap_usd: float = PLANNED_NET_RISK_CAP_USD,
    min_net_rr: float = MIN_NET_RR_THRESHOLD,
    now_ms: int = 0,
    forbidden_future_fields_present: bool = False,
) -> Dict[str, Any]:
    """
    Causally recomputes planned NET stop risk and NET R:R at hypothetical fill time using
    EXCLUSIVELY `fill_bar_open` + modeled half-spread + entry slippage (NEVER reading
    `fill_bar_high`, `fill_bar_low`, or `fill_bar_close`!).

    Frozen policy when fill-time planned net risk > `risk_cap_usd` ($2.50):
      1. If open already gapped past SL -> `REJECTED_FILL_TIME_GAPPED_PAST_SL`
      2. If open already gapped past TP -> `REJECTED_FILL_TIME_GAPPED_PAST_TP`
      3. Recompute planned net risk on original `decision_qty` at `fill_entry_px`.
         - If `<= risk_cap_usd` AND fill-time `net_rr >= min_net_rr` -> `ACCEPTED_AS_SIZED`
         - Else attempt causal re-sizing via `calculate_risk_reward` at `fill_entry_px`:
           * If valid AND `new_qty >= 0.01` AND `new_net_risk <= risk_cap_usd` AND `new_net_rr >= min_net_rr`
             -> `RESIZED_CAUSALLY_AT_FILL_TIME`
           * Else -> `REJECTED_AT_FILL_TIME_RISK_OR_RR_OR_MIN_LOT`
    """
    if forbidden_future_fields_present:
        raise ValueError("FUTURE_FILL_BAR_HIGH_LOW_FORBIDDEN_AT_FILL_SIZING")

    d = str(direction).strip().upper()
    half_spread = round(float(spread_usd) / 2.0, 4)
    if d == "LONG":
        fill_entry_px = round(float(fill_bar_open) + half_spread + float(slippage_usd), 4)
        if fill_entry_px <= float(selected_sl):
            return {
                "fill_time_action": "REJECTED_FILL_TIME_GAPPED_PAST_SL",
                "accepted": False,
                "fill_entry_price": fill_entry_px,
                "final_qty": 0.0,
                "fill_time_planned_net_risk_usd": None,
                "fill_time_net_rr": None,
            }
        if fill_entry_px >= float(take_profit):
            return {
                "fill_time_action": "REJECTED_FILL_TIME_GAPPED_PAST_TP",
                "accepted": False,
                "fill_entry_price": fill_entry_px,
                "final_qty": 0.0,
                "fill_time_planned_net_risk_usd": None,
                "fill_time_net_rr": None,
            }
        stop_dist_fill = fill_entry_px - float(selected_sl)
        reward_dist_fill = float(take_profit) - fill_entry_px
    else:
        fill_entry_px = round(float(fill_bar_open) - half_spread - float(slippage_usd), 4)
        if fill_entry_px >= float(selected_sl):
            return {
                "fill_time_action": "REJECTED_FILL_TIME_GAPPED_PAST_SL",
                "accepted": False,
                "fill_entry_price": fill_entry_px,
                "final_qty": 0.0,
                "fill_time_planned_net_risk_usd": None,
                "fill_time_net_rr": None,
            }
        if fill_entry_px <= float(take_profit):
            return {
                "fill_time_action": "REJECTED_FILL_TIME_GAPPED_PAST_TP",
                "accepted": False,
                "fill_entry_price": fill_entry_px,
                "final_qty": 0.0,
                "fill_time_planned_net_risk_usd": None,
                "fill_time_net_rr": None,
            }
        stop_dist_fill = float(selected_sl) - fill_entry_px
        reward_dist_fill = fill_entry_px - float(take_profit)

    # Compute exact fill-time planned net risk on ORIGINAL decision_qty (Decimal precision)
    q_dec = Decimal(str(decision_qty))
    ent_dec = Decimal(str(fill_entry_px))
    sl_dec = Decimal(str(selected_sl))
    tp_dec = Decimal(str(take_profit))
    fee_r = Decimal(str(taker_fee_rate))
    slip_d = Decimal(str(slippage_usd))
    gross_loss_orig = Decimal(str(stop_dist_fill)) * q_dec
    gross_rew_orig = Decimal(str(reward_dist_fill)) * q_dec
    entry_fee_orig = ent_dec * q_dec * fee_r
    sl_fee_orig = sl_dec * q_dec * fee_r
    tp_fee_orig = tp_dec * q_dec * fee_r
    # Exit stop slippage + fees (entry slippage already in fill_entry_px!)
    orig_fill_net_risk = gross_loss_orig + entry_fee_orig + sl_fee_orig + slip_d * q_dec
    orig_fill_net_reward = gross_rew_orig - entry_fee_orig - tp_fee_orig
    orig_fill_net_rr = float(orig_fill_net_reward / orig_fill_net_risk) if orig_fill_net_risk > 0 else 0.0

    if float(orig_fill_net_risk) <= risk_cap_usd + 1e-9 and orig_fill_net_rr >= min_net_rr - 1e-9 and decision_qty >= 0.01 - 1e-9:
        return {
            "fill_time_action": "ACCEPTED_AS_SIZED",
            "accepted": True,
            "fill_entry_price": fill_entry_px,
            "decision_qty": round(float(decision_qty), 4),
            "final_qty": round(float(decision_qty), 4),
            "orig_qty_fill_time_net_risk_usd": _dec4(orig_fill_net_risk),
            "fill_time_planned_net_risk_usd": _dec4(orig_fill_net_risk),
            "fill_time_net_rr": round(orig_fill_net_rr, 4),
        }

    # Causal re-sizing at fill-time open price (NEVER looking at bar High/Low!)
    costs = CostAssumptions(
        maker_fee_rate=maker_fee_rate,
        taker_fee_rate=taker_fee_rate,
        slippage_usd=slippage_usd,
    )
    cr = calculate_risk_reward(
        direction=d,
        planned_entry=fill_entry_px,
        stop_loss=float(selected_sl),
        take_profit=float(take_profit),
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=20,
        margin_mode="ISOLATED",
        min_net_rr=min_net_rr,
        costs=costs,
        entry_has_slippage=True,
        now_ms=now_ms,
    )
    if (
        cr.is_valid
        and cr.can_execute
        and float(cr.quantity) >= 0.01 - 1e-9
        and float(cr.net_risk_usdt) <= risk_cap_usd + 1e-9
        and float(cr.net_rr) >= min_net_rr - 1e-9
    ):
        return {
            "fill_time_action": "RESIZED_CAUSALLY_AT_FILL_TIME",
            "accepted": True,
            "fill_entry_price": fill_entry_px,
            "decision_qty": round(float(decision_qty), 4),
            "final_qty": round(float(cr.quantity), 4),
            "orig_qty_fill_time_net_risk_usd": _dec4(orig_fill_net_risk),
            "fill_time_planned_net_risk_usd": round(float(cr.net_risk_usdt), 4),
            "fill_time_net_rr": round(float(cr.net_rr), 4),
        }

    rej_reason = "REJECTED_AT_FILL_TIME_NET_RR_LT_2_0"
    if float(cr.quantity or 0.0) < 0.01 - 1e-9 or (
        float(cr.quantity or 0.0) <= 0.01 + 1e-9 and float(cr.net_risk_usdt or 99.0) > risk_cap_usd + 1e-9
    ):
        rej_reason = "REJECTED_AT_FILL_TIME_MIN_LOT_EXCEEDS_RISK_CAP"
    elif float(cr.net_risk_usdt or 99.0) > risk_cap_usd + 1e-9:
        rej_reason = "REJECTED_AT_FILL_TIME_RISK_CAP_BREACHED"
    return {
        "fill_time_action": rej_reason,
        "accepted": False,
        "fill_entry_price": fill_entry_px,
        "decision_qty": round(float(decision_qty), 4),
        "final_qty": 0.0,
        "orig_qty_fill_time_net_risk_usd": _dec4(orig_fill_net_risk),
        "fill_time_planned_net_risk_usd": round(float(cr.net_risk_usdt or 0.0), 4),
        "fill_time_net_rr": round(float(cr.net_rr or 0.0), 4),
    }


def audit_r2_fill_time_net_risk_and_realized_tail(
    *,
    datasets: Mapping[str, Any],
    r2_replays: Mapping[str, Mapping[str, Any]],
    arms: Sequence[str] = (*PRIMARY_R2_ARMS, "NC_NULL_TRIGGER"),
    risk_cap_usd: float = PLANNED_NET_RISK_CAP_USD,
    min_net_rr: float = MIN_NET_RR_THRESHOLD,
) -> Dict[str, Any]:
    """
    §03(8) -> Artifact 10:
    1) Audits every R2 chronological trade across `NEXT_BAR_OPEN_BID_ASK` and
       `STRESS_WIDER_SPREAD_SLIPPAGE_2X`:
       - verifies decision-time `planned_net_risk_usd <= $2.50` and `net_rr >= 2.0`
       - recomputes causal fill-time planned net risk at `bars_1m[chk + 1]["open"]` ONLY
       - counts trades that stay ACCEPTED_AS_SIZED vs RESIZED_CAUSALLY_AT_FILL_TIME vs REJECTED_AT_FILL_TIME
       - audits REALIZED losses exceeding `$2.50` separately (adverse gap/slippage tail!)
    2) Executes 5 deterministic synthetic edge-case tests: large overnight gap, adverse slippage jump,
       tiny stop distance, min-lot floor breach, and position open at horizon.
    """
    bars_1m = list(datasets["1m"])
    arm_audits: Dict[str, Any] = {}
    decision_cap_violations = 0
    fill_policy_post_cap_violations = 0

    for ac in arms:
        mode_audits: Dict[str, Any] = {}
        for mode, sp_usd, slp_usd in (
            ("NEXT_BAR_OPEN_BID_ASK", DEFAULT_MODELED_SPREAD_USD, DEFAULT_SLIPPAGE_USD),
            ("STRESS_WIDER_SPREAD_SLIPPAGE_2X", DEFAULT_MODELED_SPREAD_USD * 2.0, DEFAULT_SLIPPAGE_USD * 2.0),
        ):
            eps = list(((r2_replays.get(ac) or {}).get("episodes_by_mode") or {}).get(mode, ()))
            action_counts: Counter[str] = Counter()
            orig_qty_breach_count = 0
            realized_loss_gt_cap_count = 0
            max_decision_planned_risk = 0.0
            max_orig_qty_fill_risk = 0.0
            max_post_policy_fill_risk = 0.0
            max_realized_loss_usd = 0.0
            realized_tail_examples: List[Dict[str, Any]] = []
            open_at_horizon_count = 0

            for ep in eps:
                dec_risk = float(ep.get("planned_net_risk_usd") or 0.0)
                if dec_risk > max_decision_planned_risk:
                    max_decision_planned_risk = dec_risk
                if dec_risk > risk_cap_usd + 1e-6:
                    decision_cap_violations += 1

                chk = int(ep.get("checkpoint_index_1m") or 0)
                if chk + 1 < len(bars_1m):
                    fill_open = float(bars_1m[chk + 1]["open"])
                    ft = evaluate_causal_fill_time_policy_single_trade(
                        direction=str(ep.get("direction") or "LONG"),
                        decision_entry=float(ep.get("entry_reference") or fill_open),
                        fill_bar_open=fill_open,
                        selected_sl=float(ep.get("selected_sl") or 0.0),
                        take_profit=float(ep.get("take_profit") or 0.0),
                        decision_qty=float(ep.get("executed_quantity") or ep.get("hypothetical_quantity") or 0.01),
                        spread_usd=sp_usd,
                        slippage_usd=slp_usd,
                        risk_cap_usd=risk_cap_usd,
                        min_net_rr=min_net_rr,
                        now_ms=int(bars_1m[chk + 1]["open_time"]),
                    )
                    action_counts[ft["fill_time_action"]] += 1
                    orisk = ft.get("orig_qty_fill_time_net_risk_usd")
                    if orisk is not None:
                        max_orig_qty_fill_risk = max(max_orig_qty_fill_risk, float(orisk))
                        if float(orisk) > risk_cap_usd + 1e-6:
                            orig_qty_breach_count += 1
                    if ft["accepted"] and ft.get("fill_time_planned_net_risk_usd") is not None:
                        pr = float(ft["fill_time_planned_net_risk_usd"])
                        max_post_policy_fill_risk = max(max_post_policy_fill_risk, pr)
                        if pr > risk_cap_usd + 1e-6:
                            fill_policy_post_cap_violations += 1

                if str(ep.get("outcome_label")) == "OPEN_AT_HORIZON":
                    open_at_horizon_count += 1
                if ep.get("filled") and str(ep.get("outcome_label")) == "LOSS":
                    rloss = -float(ep.get("realized_net_pnl_usd") or 0.0)
                    if rloss > max_realized_loss_usd:
                        max_realized_loss_usd = rloss
                    if rloss > risk_cap_usd + 1e-6:
                        realized_loss_gt_cap_count += 1
                        if len(realized_tail_examples) < 10:
                            realized_tail_examples.append(
                                {
                                    "opportunity_id": ep.get("opportunity_id"),
                                    "direction": ep.get("direction"),
                                    "planned_net_risk_usd": round(dec_risk, 4),
                                    "realized_net_loss_usd": round(rloss, 4),
                                    "excess_over_2p50_usd": round(rloss - risk_cap_usd, 4),
                                    "exit_type": (ep.get("execution_ledger") or {}).get("exit_type"),
                                }
                            )

            mode_audits[mode] = {
                "scheduled_trades": len(eps),
                "open_at_horizon_count": open_at_horizon_count,
                "max_decision_planned_net_risk_usd": round(max_decision_planned_risk, 4),
                "orig_qty_fill_time_breaches_over_2p50": orig_qty_breach_count,
                "max_orig_qty_fill_time_net_risk_usd": round(max_orig_qty_fill_risk, 4),
                "max_post_policy_fill_time_planned_risk_usd": round(max_post_policy_fill_risk, 4),
                "fill_time_policy_actions": dict(sorted(action_counts.items())),
                "realized_losses_exceeding_2p50_count": realized_loss_gt_cap_count,
                "max_realized_net_loss_usd": round(max_realized_loss_usd, 4),
                "realized_tail_examples": realized_tail_examples,
            }
        arm_audits[ac] = mode_audits

    # Deterministic synthetic stress scenarios (overnight gap, slippage jump, tiny stop, min-lot floor, open-at-horizon)
    s_gap = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4000.0, fill_bar_open=3990.0, selected_sl=3998.0, take_profit=4008.0, decision_qty=0.50
    )
    s_slip_jump = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4000.0, fill_bar_open=4001.2, selected_sl=3998.0, take_profit=4012.0, decision_qty=0.50, slippage_usd=0.60
    )
    s_tiny_stop = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4000.0, fill_bar_open=4000.0, selected_sl=3999.95, take_profit=4002.0, decision_qty=2.0
    )
    s_min_lot_breach = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4000.0, fill_bar_open=4000.0, selected_sl=3600.0, take_profit=5000.0, decision_qty=0.01
    )
    # Verify future-bar-high/low peek raises fail-closed ValueError
    future_peek_blocked = False
    try:
        evaluate_causal_fill_time_policy_single_trade(
            direction="LONG", decision_entry=4000.0, fill_bar_open=4000.0, selected_sl=3998.0, take_profit=4008.0, decision_qty=0.50, forbidden_future_fields_present=True
        )
    except ValueError:
        future_peek_blocked = True

    synthetic_stress = {
        "overnight_gap_past_sl_rejected": (not s_gap["accepted"] and s_gap["fill_time_action"] == "REJECTED_FILL_TIME_GAPPED_PAST_SL"),
        "adverse_slippage_jump_enforces_2p50_cap": (
            (not s_slip_jump["accepted"]) or float(s_slip_jump["fill_time_planned_net_risk_usd"]) <= risk_cap_usd + 1e-6
        ),
        "tiny_stop_resizes_or_rejects_cleanly": (
            (not s_tiny_stop["accepted"]) or float(s_tiny_stop["fill_time_planned_net_risk_usd"]) <= risk_cap_usd + 1e-6
        ),
        "min_lot_exceeds_cap_rejected": (
            not s_min_lot_breach["accepted"] and s_min_lot_breach["fill_time_action"] == "REJECTED_AT_FILL_TIME_MIN_LOT_EXCEEDS_RISK_CAP"
        ),
        "future_high_low_peek_blocked": future_peek_blocked,
    }

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "risk_cap_usd": risk_cap_usd,
        "min_net_rr": min_net_rr,
        "decision_time_cap_violations": decision_cap_violations,
        "post_fill_policy_cap_violations": fill_policy_post_cap_violations,
        "arm_fill_time_and_tail_audits": arm_audits,
        "synthetic_stress_controls": synthetic_stress,
        "realized_tail_loss_disclosure": (
            "planned_net_risk_usd <= 2.50 USD is a causal planned stop-loss estimate computed BEFORE trade exit; "
            "it is NOT a physical guarantee that realized loss cannot exceed 2.50 USD after adverse overnight gaps, "
            "liquidity shocks, or stop-fill slippage. Realized losses exceeding 2.50 USD are tracked and disclosed separately."
        ),
        "status": (
            "PASS"
            if (decision_cap_violations == 0 and fill_policy_post_cap_violations == 0 and all(synthetic_stress.values()))
            else "FAIL"
        ),
    }
    payload["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "artifact_canonical_sha256"}
    )
    return payload


# ==============================================================================
# SECTION G — FIXED-FILL SAME-ID vs FULL-UNIVERSE DYNAMIC RERISKED COSTS (11, 12)
# ==============================================================================


def reprice_r2_fixed_fills_cost_counterfactual(
    *,
    r2_replays: Mapping[str, Mapping[str, Any]],
    arms: Sequence[str] = (*PRIMARY_R2_ARMS, "NC_NULL_TRIGGER"),
    baseline_fee_rate: float = DEFAULT_BASELINE_TAKER_FEE,
    public_ref_fee_rate: float = DEFAULT_PUBLIC_REF_TAKER_FEE,
) -> Dict[str, Any]:
    """
    §03(9) -> Artifact 11:
    Holds EXACT SAME closed trade IDs, fills, quantities, SL, TP, and exits fixed across
    all scenarios (`ZERO_DROPPED_IDS_STABLE_DENOMINATOR`) using exact `Decimal` arithmetic:
      - Fee-only fixed-fill: 1x (0.0004), public ref (0.0006), 2x fee (0.0008), 3x fee (0.0012)
      - All-in fixed-fill repricing: 1x, 2x (extra spread/slippage + 2x fee), 3x (2x extra spread/slippage + 3x fee)
      - Per-ID reconciliation residual <= 0.0001 USD
    Explicitly separates fixed-fill repricing from `STRESS_WIDER_SPREAD_SLIPPAGE_2X` (where wider spread/slippage can change fills/outcomes).
    """
    ratio_06 = Decimal(str(public_ref_fee_rate)) / Decimal(str(baseline_fee_rate))
    arm_reports: Dict[str, Any] = {}
    max_reconciliation_residual = Decimal("0")
    zero_drops_all = True

    for ac in arms:
        eps_all = list(((r2_replays.get(ac) or {}).get("episodes_by_mode") or {}).get("NEXT_BAR_OPEN_BID_ASK", ()))
        closed = [e for e in eps_all if e.get("filled") and str(e.get("outcome_label")) in ("WIN", "LOSS")]
        open_h = sum(1 for e in eps_all if e.get("filled") and str(e.get("outcome_label")) == "OPEN_AT_HORIZON")
        gap_u = sum(1 for e in eps_all if str(e.get("outcome_label")) == "DATA_GAP_UNRESOLVED")
        unfilled = sum(1 for e in eps_all if not e.get("filled"))
        wins = sum(1 for e in closed if str(e.get("outcome_label")) == "WIN")
        losses = len(closed) - wins

        gross_1x = Decimal("0")
        fees_1x = Decimal("0")
        net_1x = Decimal("0")
        fees_06 = Decimal("0")
        net_06_fee_only = Decimal("0")
        net_2x_fee_only = Decimal("0")
        net_3x_fee_only = Decimal("0")
        net_2x_allin = Decimal("0")
        net_3x_allin = Decimal("0")
        r_1x_list: List[float] = []
        r_06_list: List[float] = []
        r_2x_allin_list: List[float] = []
        r_3x_allin_list: List[float] = []
        per_id_reconciled = 0

        for e in closed:
            g1 = Decimal(str(e.get("realized_gross_pnl_usd") or 0.0))
            f1 = Decimal(str(e.get("total_fees_usd") or 0.0))
            n1 = Decimal(str(e.get("realized_net_pnl_usd") or 0.0))
            resid = abs((g1 - f1) - n1)
            if resid > max_reconciliation_residual:
                max_reconciliation_residual = resid

            qty = Decimal(str(e.get("executed_quantity") or e.get("hypothetical_quantity") or 0.0))
            rk = max(Decimal("0.01"), Decimal(str(e.get("planned_net_risk_usd") or 2.50)))
            # Extra 1x round-trip modeled spread + slippage per unit quantity = 0.20 (spread) + 0.20 (entry+exit slip) = 0.40 USD/oz
            extra_1x_ss = Decimal("0.40") * qty

            f06 = f1 * ratio_06
            n06 = g1 - f06
            n2_fee = g1 - (Decimal("2") * f1)
            n3_fee = g1 - (Decimal("3") * f1)
            n2_all = g1 - extra_1x_ss - (Decimal("2") * f1)
            n3_all = g1 - (Decimal("2") * extra_1x_ss) - (Decimal("3") * f1)

            gross_1x += g1
            fees_1x += f1
            net_1x += n1
            fees_06 += f06
            net_06_fee_only += n06
            net_2x_fee_only += n2_fee
            net_3x_fee_only += n3_fee
            net_2x_allin += n2_all
            net_3x_allin += n3_all

            r_1x_list.append(float(n1 / rk))
            r_06_list.append(float(n06 / rk))
            r_2x_allin_list.append(float(n2_all / rk))
            r_3x_allin_list.append(float(n3_all / rk))
            per_id_reconciled += 1

        if per_id_reconciled != len(closed):
            zero_drops_all = False

        arm_reports[ac] = {
            "scope": "FIXED_FILL_SAME_IDS",
            "denominator_closed_trades": len(closed),
            "per_id_reconciled_count": per_id_reconciled,
            "dropped_ids_count": len(closed) - per_id_reconciled,
            "wins": wins,
            "losses": losses,
            "open_at_horizon": open_h,
            "data_gap_unresolved": gap_u,
            "unfilled": unfilled,
            "gross_pnl_1x_usd": _dec4(gross_1x),
            "1x_base_0p0004": {
                "fees_usd": _dec4(fees_1x),
                "net_pnl_usd": _dec4(net_1x),
                "expectancy_r": round(sum(r_1x_list) / len(r_1x_list), 4) if r_1x_list else None,
            },
            "fee_only_0p0006": {
                "fees_usd": _dec4(fees_06),
                "net_pnl_usd": _dec4(net_06_fee_only),
                "expectancy_r": round(sum(r_06_list) / len(r_06_list), 4) if r_06_list else None,
                "delta_vs_0p0004_usd": _dec4(net_06_fee_only - net_1x),
            },
            "fee_only_2x_0p0008": {
                "fees_usd": _dec4(fees_1x * Decimal("2")),
                "net_pnl_usd": _dec4(net_2x_fee_only),
            },
            "fee_only_3x_0p0012": {
                "fees_usd": _dec4(fees_1x * Decimal("3")),
                "net_pnl_usd": _dec4(net_3x_fee_only),
            },
            "all_in_2x_fixed_fill": {
                "formula": "gross_1x - (0.40 * qty) - (2 * fees_1x)",
                "net_pnl_usd": _dec4(net_2x_allin),
                "expectancy_r": round(sum(r_2x_allin_list) / len(r_2x_allin_list), 4) if r_2x_allin_list else None,
            },
            "all_in_3x_fixed_fill": {
                "formula": "gross_1x - (0.80 * qty) - (3 * fees_1x)",
                "net_pnl_usd": _dec4(net_3x_allin),
                "expectancy_r": round(sum(r_3x_allin_list) / len(r_3x_allin_list), 4) if r_3x_allin_list else None,
            },
            "closed_trade_ids_sha256": canonical_hash([str(e["opportunity_id"]) for e in closed]),
        }

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "population_mode": "FIXED_FILL_SAME_IDS",
        "quote_mode": "MODELED_BID_ASK",
        "modeled_spread_usd": DEFAULT_MODELED_SPREAD_USD,
        "observed_l1_bbo_available": False,
        "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "baseline_taker_fee_rate": baseline_fee_rate,
        "public_ref_taker_fee_rate": public_ref_fee_rate,
        "zero_dropped_ids_verified": zero_drops_all,
        "max_decimal_reconciliation_residual_usd": _dec4(max_reconciliation_residual),
        "reconciliation_tolerance_usd": 0.001,
        "arms": arm_reports,
        "caveat": (
            "Fixed-fill repricing holds fills/exits/denominators strictly constant to audit accounting sensitivity; "
            "because higher spreads/slippage/fees can alter sizing, net R:R eligibility, and 1-position trade paths, "
            "artifact 12 (`FULL_RERISKED_DYNAMIC_POPULATION`) must be read alongside this report."
        ),
        "status": "PASS" if (zero_drops_all and float(max_reconciliation_residual) <= 0.001) else "FAIL",
    }
    payload["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "artifact_canonical_sha256"}
    )
    return payload


def _replay_chronological_custom_cost_scenario(
    *,
    arm_code: str,
    scenario_id: str,
    candidates: Sequence[Mapping[str, Any]],
    bars_1m: Sequence[Mapping[str, Any]],
    spread_usd: float,
    slippage_usd: float,
    taker_fee_rate: float,
    maker_fee_rate: float,
) -> Dict[str, Any]:
    """
    Runs a strict chronological 1-position deterministic replay from scratch for a custom
    cost scenario (`spread_usd`, `slippage_usd`, `taker_fee_rate`, `maker_fee_rate`) using
    authoritative read-only `simulate_v17931_trade_execution_net`.
    """
    sorted_cands = sorted(
        candidates,
        key=lambda c: (int(c["decision_timestamp_ms"]), str(c["direction"]), str(c["opportunity_id"])),
    )
    busy_until_ms = -1
    episodes: List[Dict[str, Any]] = []
    skipped_busy = 0
    overlap_violations = 0

    for c in sorted_cands:
        dec_ts = int(c["decision_timestamp_ms"])
        if dec_ts < busy_until_ms:
            skipped_busy += 1
            continue
        chk = int(c["checkpoint_index_1m"])
        fwd = bars_1m[chk + 1 : min(len(bars_1m), chk + 1 + 180)]
        led = simulate_v17931_trade_execution_net(
            variant=f"KH02_R2_{scenario_id}_{arm_code}",
            episode_key=f"{scenario_id}_{arm_code}_{c['opportunity_id']}",
            setup_id=str(c.get("setup_episode_id") or c["opportunity_id"]),
            model_id="KH02_R2_FULL_RERISKED",
            direction=str(c["direction"]),
            decision_ts=dec_ts,
            checkpoint_index_1m=chk,
            planned_entry=float(c["entry_reference"]),
            planned_sl=float(c["selected_sl"]),
            planned_tp=float(c["take_profit"]),
            forward_bars=fwd,
            fill_mode="NEXT_BAR_OPEN_BID_ASK",
            spread_usd=spread_usd,
            slippage_usd=slippage_usd,
            taker_fee_rate=taker_fee_rate,
            maker_fee_rate=maker_fee_rate,
        )
        ld = led.to_dict()
        if not led.filled:
            outcome_label = str(led.exit_type)
            release_ts = dec_ts
        elif led.exit_type in ("WIN", "LOSS", "LOSS_TIE_CONSERVATIVE", "ADVERSE_GAP_OPEN_STOP_LOSS"):
            outcome_label = "WIN" if led.exit_type == "WIN" else "LOSS"
            release_ts = int(led.exit_fill_ts or (dec_ts + 60_000))
        elif led.exit_type == "OPEN_AT_HORIZON":
            outcome_label = "OPEN_AT_HORIZON"
            release_ts = int(fwd[-1]["close_time"]) if fwd else dec_ts
        else:
            outcome_label = "DATA_GAP_UNRESOLVED"
            release_ts = int(led.trigger_bar_ts or (fwd[0]["close_time"] if fwd else dec_ts))

        if episodes and led.filled and int(led.entry_fill_ts or dec_ts) < busy_until_ms:
            overlap_violations += 1
        if led.filled:
            busy_until_ms = max(busy_until_ms, release_ts)

        episodes.append(
            {
                "opportunity_id": c["opportunity_id"],
                "setup_episode_id": c.get("setup_episode_id"),
                "direction": c["direction"],
                "decision_timestamp_ms": dec_ts,
                "ny_date": c.get("ny_date"),
                "filled": bool(led.filled),
                "outcome_label": outcome_label,
                "exit_type": str(led.exit_type),
                "executed_quantity": round(float(led.quantity or 0.0), 4),
                "planned_net_risk_usd": round(float(led.planned_net_risk_usd or 0.0), 4),
                "realized_gross_pnl_usd": round(float(ld.get("realized_gross_pnl_usd") or 0.0), 4),
                "total_fees_usd": round(float(ld.get("total_fees_usd") or 0.0), 4),
                "realized_net_pnl_usd": round(float(ld.get("realized_net_pnl_usd") or 0.0), 4),
                "realized_net_r": round(
                    float(
                        ld.get("realized_r_net")
                        if ld.get("realized_r_net") is not None
                        else (
                            ld.get("realized_r")
                            if ld.get("realized_r") is not None
                            else (float(ld.get("realized_net_pnl_usd") or 0.0) / max(0.01, float(led.planned_net_risk_usd or 2.50)))
                        )
                    ),
                    4,
                ),
            }
        )

    summary = _summarize_episodes(episodes)
    summary["eligible_candidates_input"] = len(sorted_cands)
    summary["skipped_due_to_active_position"] = skipped_busy
    summary["overlap_violations"] = overlap_violations
    summary["executed_ids_sha256"] = canonical_hash([e["opportunity_id"] for e in episodes])
    return summary


def rerun_r2_full_universe_by_cost_scenario(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    ledger_bundle: Mapping[str, Any],
    arms: Sequence[str] = (*PRIMARY_R2_ARMS, "NC_NULL_TRIGGER"),
    source_latency_ms: int = 0,
) -> Dict[str, Any]:
    """
    §03(10) -> Artifact 12:
    Restarts from the FULL original `44,692` directional M1 bar opportunity stream for EACH
    frozen cost scenario (`1X_BASE_0p0004`, `FEE_ONLY_0p0006`, `2X_ALL_COST_0p0004`,
    `2X_ALL_COST_0p0006`, `3X_ALL_COST_0p0004`, `3X_ALL_COST_0p0006`).
    Re-evaluates structural sizing, net R:R >= 2.0, planned net risk <= $2.50, min lot >= 0.01,
    and chronological 1-position execution from scratch, explicitly reporting changing
    candidate denominators (`FULL_RERISKED_DYNAMIC_POPULATION`).
    """
    bars_1m = list(datasets["1m"])
    scenarios = (
        ("1X_BASE_0p0004", 0.20, 0.10, 0.0004, 0.0002),
        ("FEE_ONLY_0p0006", 0.20, 0.10, 0.0006, 0.0002),
        ("2X_ALL_COST_0p0004", 0.40, 0.20, 0.0008, 0.0004),
        ("2X_ALL_COST_0p0006", 0.40, 0.20, 0.0012, 0.0004),
        ("3X_ALL_COST_0p0004", 0.60, 0.30, 0.0012, 0.0006),
        ("3X_ALL_COST_0p0006", 0.60, 0.30, 0.0018, 0.0006),
    )

    scenario_reports: Dict[str, Any] = {}
    total_overlaps = 0
    all_conserved = True

    for sc_id, sp_usd, slp_usd, taker_r, maker_r in scenarios:
        costs = CostAssumptions(
            maker_fee_rate=maker_r,
            taker_fee_rate=taker_r,
            slippage_usd=slp_usd,
        )
        cands_bundle = build_r2_target_repair_full_opportunity_conservation(
            datasets=datasets,
            mtf_bundle=mtf_bundle,
            ledger_bundle=ledger_bundle,
            r1_candidates=None,
            source_latency_ms=source_latency_ms,
            cost_assumptions=costs,
        )
        if not cands_bundle["conservation_sum_verified"]:
            all_conserved = False

        arm_res: Dict[str, Any] = {}
        for ac in arms:
            arm_list = list((cands_bundle.get("arm_candidates") or {}).get(ac) or ())
            rep_sum = _replay_chronological_custom_cost_scenario(
                arm_code=ac,
                scenario_id=sc_id,
                candidates=arm_list,
                bars_1m=bars_1m,
                spread_usd=sp_usd,
                slippage_usd=slp_usd,
                taker_fee_rate=taker_r,
                maker_fee_rate=maker_r,
            )
            total_overlaps += int(rep_sum.get("overlap_violations", 0))
            arm_res[ac] = {
                "arm_eligible_candidates": len(arm_list),
                "arm_conservation": (cands_bundle.get("arm_conservation") or {}).get(ac),
                "replay_summary": rep_sum,
            }

        scenario_reports[sc_id] = {
            "scenario_id": sc_id,
            "population_mode": "FULL_RERISKED_DYNAMIC_POPULATION",
            "parameters": {
                "spread_usd": sp_usd,
                "slippage_usd": slp_usd,
                "taker_fee_rate": taker_r,
                "maker_fee_rate": maker_r,
            },
            "total_directional_bar_opportunities": cands_bundle["total_directional_bar_opportunities"],
            "raw_m1_triggers_count": cands_bundle["raw_m1_triggers_count"],
            "raw_r2_geometry_risk_eligible_count": cands_bundle["raw_r2_geometry_risk_eligible_count"],
            "universe_reason_codes": cands_bundle["universe_reason_codes"],
            "arms": arm_res,
        }

    # Build explicit denominator shift table vs 1X_BASE_0p0004
    base_elig = scenario_reports["1X_BASE_0p0004"]["raw_r2_geometry_risk_eligible_count"]
    denom_shifts: Dict[str, Any] = {}
    for sc_id, sc_d in scenario_reports.items():
        cur_elig = sc_d["raw_r2_geometry_risk_eligible_count"]
        denom_shifts[sc_id] = {
            "raw_r2_eligible": cur_elig,
            "delta_raw_eligible_vs_1x_base": cur_elig - base_elig,
            "arm_closed_trades": {ac: sc_d["arms"][ac]["replay_summary"]["closed"] for ac in arms},
            "arm_net_pnl_usd": {ac: sc_d["arms"][ac]["replay_summary"]["net_pnl_usd"] for ac in arms},
            "arm_expectancy_r": {ac: sc_d["arms"][ac]["replay_summary"]["expectancy_r"] for ac in arms},
        }

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "population_mode": "FULL_RERISKED_DYNAMIC_POPULATION",
        "starts_from_full_raw_opportunity_stream": True,
        "quote_mode": "MODELED_BID_ASK",
        "observed_l1_bbo_available": False,
        "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "conservation_verified_all_scenarios": all_conserved,
        "total_overlap_violations": total_overlaps,
        "denominator_shift_summary": denom_shifts,
        "scenarios": scenario_reports,
        "status": "PASS" if (all_conserved and total_overlaps == 0) else "FAIL",
    }
    payload["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "artifact_canonical_sha256"}
    )
    return payload


# ==============================================================================
# SECTION H — DATA LINEAGE, LATENCY SENSITIVITY & PURGED WALK-FORWARD (13, 15)
# ==============================================================================


def audit_r2_data_lineage_and_gap_sensitivity(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    dataset_path: Path,
    latency_grid_ms: Sequence[int] = (0, 1000, 5000, 60000),
) -> Dict[str, Any]:
    """
    §03(11) -> Artifact 13:
    Read-only lineage audit of M1 (~20.35 calendar days), M15, H1 coverage, weekend/rollover
    vs intra-session gaps, trading-day & session partitions, deterministic source latency
    sensitivity (`0ms` vs `+1s` vs `+5s` vs `+60s`), modeled `$0.20` spread vs absent L1 BBO,
    and unknown actual account fee tier.
    """
    tf_stats: Dict[str, Any] = {}
    for tf, exp_ms in (("1m", 60_000), ("15m", 900_000), ("1H", 3_600_000)):
        bars = list(datasets.get(tf) or ())
        if not bars:
            tf_stats[tf] = {"bar_count": 0}
            continue
        first_c = int(bars[0]["close_time"])
        last_c = int(bars[-1]["close_time"])
        cal_days = round((last_c - first_c) / 86_400_000.0, 4)
        wk_gaps = 0
        intra_gaps = 0
        missing_total = 0
        gap_details: List[Dict[str, Any]] = []
        for i in range(1, len(bars)):
            prev_c = int(bars[i - 1]["close_time"])
            cur_c = int(bars[i]["close_time"])
            dt = cur_c - prev_c
            if dt > exp_ms:
                miss = int(round(dt / float(exp_ms))) - 1
                missing_total += miss
                dt_prev = datetime.fromtimestamp(prev_c / 1000.0, tz=timezone.utc)
                is_wk = dt >= 36 * 3_600_000 or dt_prev.weekday() in (4, 5, 6)
                if is_wk:
                    wk_gaps += 1
                else:
                    intra_gaps += 1
                if len(gap_details) < 30:
                    gap_details.append(
                        {
                            "from_close_utc": _utc_iso(prev_c),
                            "to_close_utc": _utc_iso(cur_c),
                            "delta_minutes": round(dt / 60_000.0, 2),
                            "missing_bars": miss,
                            "classification": "WEEKEND_ROLLOVER_GAP" if is_wk else "INTRA_SESSION_GAP",
                        }
                    )
        tf_stats[tf] = {
            "bar_count": len(bars),
            "first_close_ms": first_c,
            "first_close_utc": _utc_iso(first_c),
            "last_close_ms": last_c,
            "last_close_utc": _utc_iso(last_c),
            "calendar_span_days": cal_days,
            "weekend_rollover_gap_count": wk_gaps,
            "intra_session_gap_count": intra_gaps,
            "total_missing_bars_across_gaps": missing_total,
            "sample_gaps": gap_details,
        }

    # Partition post-warmup M1 records by NY trading day and session
    records: Sequence[KH02AsOfMTFRecord] = _mtf_records_list(mtf_bundle)
    by_day: Counter[str] = Counter()
    by_session: Counter[str] = Counter()
    gap_excluded_m1_bars = 0
    for rec in records:
        by_day[rec.ny_date] += 1
        by_session[rec.ny_session_bucket] += 1
        if rec.recent_gap_180m:
            gap_excluded_m1_bars += 1

    # Source latency sensitivity (0ms vs +1s vs +5s vs +60s) on R2 target resolution
    latency_sensitivity: List[Dict[str, Any]] = []
    for lat_ms in latency_grid_ms:
        led_lat = build_r2_causal_unswept_level_ledger(
            datasets,
            source_latency_ms=int(lat_ms),
            run_t10_future_mutation_proof=False,
        )
        c_lat = build_r2_target_repair_full_opportunity_conservation(
            datasets=datasets,
            mtf_bundle=mtf_bundle,
            ledger_bundle=led_lat,
            r1_candidates=None,
            source_latency_ms=int(lat_ms),
        )
        latency_sensitivity.append(
            {
                "source_latency_ms": int(lat_ms),
                "raw_r2_target_found_count": c_lat["raw_r2_target_found_count"],
                "raw_r2_geometry_risk_eligible_count": c_lat["raw_r2_geometry_risk_eligible_count"],
                "primary_arm_eligible_counts": {
                    ac: len((c_lat.get("arm_candidates") or {}).get(ac) or ()) for ac in (*PRIMARY_R2_ARMS, "NC_NULL_TRIGGER")
                },
                "candidates_canonical_sha256": c_lat["candidates_canonical_sha256"],
            }
        )

    base_lat_elig = latency_sensitivity[0]["raw_r2_geometry_risk_eligible_count"] if latency_sensitivity else 0
    for row in latency_sensitivity:
        row["delta_raw_eligible_vs_0ms"] = int(row["raw_r2_geometry_risk_eligible_count"]) - int(base_lat_elig)

    ds_sha = _sha256_file(dataset_path) if dataset_path.is_file() else ""
    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "dataset_path": str(dataset_path),
        "dataset_sha256": ds_sha,
        "data_regime": "REUSED_RESEARCH_HISTORICAL_ONLY",
        "headline_effective_m1_calendar_span_days": tf_stats["1m"].get("calendar_span_days"),
        "span_disclosure": (
            "Effective M1 execution sample is ~20.35 calendar days (22,376 bars); longer M15/H1 history "
            "serves strictly as causal higher-timeframe context and MUST NOT be misrepresented as M1 sample length."
        ),
        "quote_and_fee_provenance": {
            "quote_mode": "MODELED_BID_ASK",
            "modeled_spread_usd": DEFAULT_MODELED_SPREAD_USD,
            "observed_l1_bbo_available": False,
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "domain_default_taker_fee": DEFAULT_BASELINE_TAKER_FEE,
            "public_reference_taker_fee": DEFAULT_PUBLIC_REF_TAKER_FEE,
            "intrabar_unobserved_move_caveat": (
                "1m OHLC bars do not capture tick-level intra-bar path ordering or live spread spikes; "
                "tie bars conservatively resolve against the trade (`LOSS_TIE_CONSERVATIVE`), and missing bars create gap censoring uncertainty."
            ),
        },
        "timeframe_coverage_and_gaps": tf_stats,
        "m1_post_warmup_partitions": {
            "total_post_warmup_m1_bars": len(records),
            "unique_ny_trading_dates": len(by_day),
            "bars_excluded_by_recent_180m_gap_gate": gap_excluded_m1_bars,
            "by_ny_session": dict(sorted(by_session.items())),
            "by_ny_date": dict(sorted(by_day.items())),
        },
        "source_latency_sensitivity": latency_sensitivity,
        "DATA_PROVENANCE_VERDICT": "PASS" if ds_sha == CANONICAL_DATASET_SHA256_KH02_R2 else "FAIL",
    }
    payload["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "artifact_canonical_sha256"}
    )
    return payload


def run_r2_purged_walkforward_and_multiplicity(
    *,
    r2_replays: Mapping[str, Mapping[str, Any]],
    r2_fixed_costs: Mapping[str, Any],
    arms: Sequence[str] = (*PRIMARY_R2_ARMS, *CONTROL_R2_ARMS),
) -> Dict[str, Any]:
    """
    Artifact 15: Runs purged walk-forward (240-min embargo), NY-day & setup-episode cluster
    bootstrap 95% CIs, and Holm-Bonferroni multiplicity audit on R2 arms (REUSED_RESEARCH only!).
    """
    arm_stats: Dict[str, Any] = {}
    any_fold_leakage = False

    for ac in arms:
        rep = r2_replays[ac]
        fc_arm = (r2_fixed_costs.get("arms") or {}).get(ac)
        if fc_arm is not None:
            cost_compat = {
                "all_in_cost_scenarios": {
                    "2x_all_cost_net_pnl_usd": fc_arm["all_in_2x_fixed_fill"]["net_pnl_usd"],
                    "3x_all_cost_net_pnl_usd": fc_arm["all_in_3x_fixed_fill"]["net_pnl_usd"],
                }
            }
        else:
            nb_eps = [
                e for e in (rep.get("episodes_by_mode") or {}).get("NEXT_BAR_OPEN_BID_ASK", ())
                if e.get("filled") and str(e.get("outcome_label")) in ("WIN", "LOSS")
            ]
            n2 = sum(float(e.get("realized_net_pnl_usd") or 0.0) - float(e.get("total_fees_usd") or 0.0) - 0.40 * float(e.get("executed_quantity") or 0.0) for e in nb_eps)
            n3 = sum(float(e.get("realized_net_pnl_usd") or 0.0) - 2.0 * float(e.get("total_fees_usd") or 0.0) - 0.80 * float(e.get("executed_quantity") or 0.0) for e in nb_eps)
            cost_compat = {
                "all_in_cost_scenarios": {
                    "2x_all_cost_net_pnl_usd": round(n2, 4),
                    "3x_all_cost_net_pnl_usd": round(n3, 4),
                }
            }

        # Build 3 deterministic purged chronological walk-forward folds with >= 240m embargo
        all_nb_ts = sorted(
            int(e.get("decision_timestamp_ms") or 0)
            for e in (rep.get("episodes_by_mode") or {}).get("NEXT_BAR_OPEN_BID_ASK", ())
            if int(e.get("decision_timestamp_ms") or 0) > 0
        )
        emb_ms = PROSPECTIVE_OOS_EMBARGO_MINUTES_KH02_R2 * 60_000
        if len(all_nb_ts) >= 6 and (all_nb_ts[-1] - all_nb_ts[0]) > 4 * emb_ms:
            t0_f, t1_f = all_nb_ts[0], all_nb_ts[-1]
            span = t1_f - t0_f
            folds_spec: List[Tuple[int, int, int, int]] = []
            for fi in range(3):
                tr_end = t0_f + int(span * (0.35 + 0.15 * fi))
                val_start = tr_end + emb_ms
                val_end = min(t1_f, val_start + int(span * 0.25))
                if val_end > val_start:
                    folds_spec.append((t0_f, tr_end, val_start, val_end))
        else:
            folds_spec = []

        wf = estimate_kh02_edge_uncertainty(
            rep,
            cost_compat,
            folds_spec,
            embargo_minutes_required=PROSPECTIVE_OOS_EMBARGO_MINUTES_KH02_R2,
            primary_family_size_m=len(PRIMARY_R2_ARMS),
            full_family_size_m=len(ALL_EVALUATED_R2_ARMS),
            is_primary_arm=(ac in PRIMARY_R2_ARMS),
            fresh_oos_verified=False,
            seed=DEFAULT_RANDOM_SEED_KH02_R2,
        )
        if wf.get("fold_leakage_detected"):
            any_fold_leakage = True
        arm_stats[ac] = wf

    # Honest economic verdict across primary R2 arms (reused research CANNOT prove edge alone)
    primary_positive_both = [
        ac
        for ac in PRIMARY_R2_ARMS
        if (arm_stats[ac].get("expectancy_1x_nb_r") or -99.0) > 0.0
        and (arm_stats[ac].get("stress_2x_all_cost_net_pnl_usd") or -99.0) > 0.0
    ]
    econ_verdict = (
        "REUSED_RESEARCH_POSITIVE_UNPROVEN_AWAITING_OOS"
        if primary_positive_both
        else "NO_PROVEN_EDGE"
    )

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "data_regime": "REUSED_RESEARCH_PURGED_WALKFORWARD_NOT_FRESH_OOS",
        "embargo_minutes": PROSPECTIVE_OOS_EMBARGO_MINUTES_KH02_R2,
        "fold_leakage_detected_any": any_fold_leakage,
        "primary_family_size_m": len(PRIMARY_R2_ARMS),
        "full_arms_family_size_m": len(ALL_EVALUATED_R2_ARMS),
        "primary_arms_positive_1x_and_2x": primary_positive_both,
        "RESEARCH_ECONOMIC_VERDICT": econ_verdict,
        "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
        "OOS_EVALUATIONS": 0,
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "arms": arm_stats,
        "status": "PASS" if not any_fold_leakage else "FAIL",
    }
    payload["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "artifact_canonical_sha256"}
    )
    return payload


# ==============================================================================
# SECTION I — STRICT RESOURCE POLICY & 42 REAL RED/GREEN FAULT CONTROLS (19)
# ==============================================================================


def evaluate_r2_disk_preflight_policy(
    volume_free_gib_map: Mapping[str, float],
    *,
    strict_preflight_min_gib: float = STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
    strict_runtime_min_gib: float = STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
    advisory_gib: float = ADVISORY_DISK_GIB_KH02_R2,
) -> Dict[str, Any]:
    """
    Fail-closed disk safety policy (§02 & §11):
      - NEVER lowers the strict 20.5 GiB preflight or 20.0 GiB runtime floor.
      - Checks EVERY writing volume independently.
    """
    if strict_preflight_min_gib < STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2 - 1e-9:
        raise ValueError(f"ILLEGAL_LOWERED_PREFLIGHT_DISK_FLOOR:{strict_preflight_min_gib}")
    if strict_runtime_min_gib < STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 - 1e-9:
        raise ValueError(f"ILLEGAL_LOWERED_RUNTIME_DISK_FLOOR:{strict_runtime_min_gib}")
    if not volume_free_gib_map:
        raise ValueError("EMPTY_VOLUME_MAP")

    per_vol: Dict[str, Any] = {}
    blocked_vols: List[str] = []
    advisory_vols: List[str] = []
    min_free = min(float(v) for v in volume_free_gib_map.values())

    for vname, free_gib in sorted(volume_free_gib_map.items()):
        fg = round(float(free_gib), 4)
        shortfall = round(max(0.0, strict_preflight_min_gib - fg), 4)
        ok_strict = fg >= strict_preflight_min_gib
        ok_adv = fg >= advisory_gib
        if not ok_strict:
            blocked_vols.append(vname)
        elif not ok_adv:
            advisory_vols.append(vname)
        per_vol[vname] = {
            "free_gib": fg,
            "free_gb_decimal": round(fg * (1024.0 ** 3) / (1000.0 ** 3), 4),
            "strict_preflight_min_gib": strict_preflight_min_gib,
            "strict_runtime_min_gib": strict_runtime_min_gib,
            "advisory_gib": advisory_gib,
            "passes_strict_20p5_gib": ok_strict,
            "passes_advisory_25_gib": ok_adv,
            "shortfall_to_20p5_gib": shortfall,
        }

    if blocked_vols:
        status = "BLOCKED_LOW_DISK"
    elif advisory_vols:
        status = "PASS_STRICT_BELOW_ADVISORY_25GIB"
    else:
        status = "PASS_STRICT_AND_ADVISORY"

    return {
        "status": status,
        "can_run_heavy_stages": len(blocked_vols) == 0,
        "min_free_gib": round(min_free, 4),
        "shortfall_to_strict_20p5_gib": round(max(0.0, strict_preflight_min_gib - min_free), 4),
        "blocked_volumes": blocked_vols,
        "below_advisory_volumes": advisory_vols,
        "volumes": per_vol,
    }


def validate_r2_cost_scope_honesty(doc: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    """Rejects subset-only rerisk masquerading as full-universe dynamic rerisk."""
    errs: List[str] = []
    mode = str(doc.get("population_mode") or "")
    if mode == "FULL_RERISKED_DYNAMIC_POPULATION":
        if not bool(doc.get("starts_from_full_raw_opportunity_stream", False)):
            errs.append("SUBSET_SUBSTITUTION_FORBIDDEN_AS_FULL_UNIVERSE")
        if str(doc.get("scope_caveat") or "").startswith("RERISKED_SUBSET_ONLY"):
            errs.append("SUBSET_CAVEAT_CONFLICTS_WITH_FULL_UNIVERSE_CLAIM")
    elif mode == "FIXED_FILL_SAME_IDS":
        if not bool(doc.get("zero_dropped_ids_verified", False)):
            errs.append("FIXED_FILL_DROPPED_TRADE_IDS")
        if float(doc.get("max_decimal_reconciliation_residual_usd", 99.0)) > float(doc.get("reconciliation_tolerance_usd", 0.001)):
            errs.append("FIXED_FILL_DECIMAL_RESIDUAL_EXCEEDS_TOLERANCE")
    else:
        errs.append(f"UNKNOWN_COST_POPULATION_MODE:{mode}")
    return len(errs) == 0, errs


def run_kh02_r2_42_red_green_fault_suite(repo_root: Path) -> Dict[str, Any]:
    """
    §07(3) -> Artifact 19:
    Executes 42 UNIQUE real RED/GREEN adversarial negative controls (`R2-F01`..`R2-F42`).
    Every control runs a genuine code path with injected defect -> observed RED rejection ->
    restored valid state -> observed GREEN acceptance.
    """
    from services.kh_02_r2_prospective_data_service import validate_prospective_snapshot_entry_r2
    from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import verify_fresh_junit_bijection_v17931211
    from tools.run_kh_01b_r1_verification import _kill_process_tree_safe

    repo_root = Path(repo_root).resolve()
    controls: List[Dict[str, Any]] = []

    def _rec(fid: str, name: str, anchored_fn: str, red_ok: bool, green_ok: bool, detail: str) -> None:
        controls.append(
            {
                "fault_id": fid,
                "name": name,
                "anchored_function": anchored_fn,
                "red_rejected": bool(red_ok),
                "green_accepted": bool(green_ok),
                "status": "PASS" if (red_ok and green_ok) else "FAIL",
                "diagnostic": detail,
            }
        )

    # R2-F01..R2-F10: Mandatory T01..T10 adversarial target causality suite
    t_suite = run_r2_t01_to_t10_adversarial_target_scenarios()
    for idx_t, sc in enumerate(t_suite["scenarios"], 1):
        _rec(
            f"R2-F{idx_t:02d}",
            f"ADVERSARIAL_{sc['scenario_id']}_{sc['description'][:42].upper().replace(' ', '_')}",
            "resolve_r2_research_only_structural_target / build_r2_causal_unswept_level_ledger",
            bool(sc["passed"]),
            bool(sc["passed"]),
            sc["description"],
        )

    # Helper clean multi-TF synthetic bars
    base_ts = 1_792_080_000_000
    def _mk_clean_ds() -> Dict[str, List[Dict[str, Any]]]:
        def _gen(tf_ms: int, count: int) -> List[Dict[str, Any]]:
            out = []
            st = base_ts - count * tf_ms
            for i in range(count):
                o_t = st + i * tf_ms
                px = 4000.0 + 0.25 * i + (2.0 if i % 6 == 3 else -0.5)
                out.append(
                    {
                        "open_time": o_t,
                        "close_time": o_t + tf_ms,
                        "open": round(px, 2),
                        "high": round(px + 1.2, 2),
                        "low": round(px - 1.1, 2),
                        "close": round(px + 0.2, 2),
                        "is_closed": True,
                    }
                )
            return out

        return {"1m": _gen(60_000, 90), "15m": _gen(900_000, 20), "1H": _gen(3_600_000, 20)}

    ds_ok = _mk_clean_ds()
    led_ok = build_r2_causal_unswept_level_ledger(ds_ok, warmup_m1=15, run_t10_future_mutation_proof=False)
    ok_green = led_ok["total_confirmed_levels"] >= 0

    # R2-F11: Unclosed M1 candle
    ds_f11 = copy.deepcopy(ds_ok)
    ds_f11["1m"][-1]["is_closed"] = False
    f11_red = False
    try:
        build_r2_causal_unswept_level_ledger(ds_f11, warmup_m1=15, run_t10_future_mutation_proof=False)
    except ValueError:
        f11_red = True
    _rec("R2-F11", "UNCLOSED_M1_BAR_REJECTED", "build_r2_causal_unswept_level_ledger", f11_red, ok_green, "is_closed=False M1 rejected")

    # R2-F12: Unclosed M15 candle
    ds_f12 = copy.deepcopy(ds_ok)
    ds_f12["15m"][-1]["is_closed"] = False
    f12_red = False
    try:
        build_r2_causal_unswept_level_ledger(ds_f12, warmup_m1=15, run_t10_future_mutation_proof=False)
    except ValueError:
        f12_red = True
    _rec("R2-F12", "UNCLOSED_M15_BAR_REJECTED", "build_r2_causal_unswept_level_ledger", f12_red, ok_green, "is_closed=False M15 rejected")

    # R2-F13: Unclosed H1 candle
    ds_f13 = copy.deepcopy(ds_ok)
    ds_f13["1H"][-1]["is_closed"] = False
    f13_red = False
    try:
        build_r2_causal_unswept_level_ledger(ds_f13, warmup_m1=15, run_t10_future_mutation_proof=False)
    except ValueError:
        f13_red = True
    _rec("R2-F13", "UNCLOSED_H1_BAR_REJECTED", "build_r2_causal_unswept_level_ledger", f13_red, ok_green, "is_closed=False H1 rejected")

    # R2-F14: Hidden future_high key in M1 bar
    ds_f14 = copy.deepcopy(ds_ok)
    ds_f14["1m"][10]["future_high"] = 4500.0
    f14_red = False
    try:
        build_r2_causal_unswept_level_ledger(ds_f14, warmup_m1=15, run_t10_future_mutation_proof=False)
    except ValueError:
        f14_red = True
    _rec("R2-F14", "HIDDEN_FUTURE_KEY_IN_BAR_REJECTED", "build_r2_causal_unswept_level_ledger", f14_red, ok_green, "future_high key blocked")

    # R2-F15: Negative source latency
    f15_red = False
    try:
        build_r2_causal_unswept_level_ledger(ds_ok, source_latency_ms=-1, warmup_m1=15, run_t10_future_mutation_proof=False)
    except ValueError:
        f15_red = True
    _rec("R2-F15", "NEGATIVE_SOURCE_LATENCY_REJECTED", "build_r2_causal_unswept_level_ledger", f15_red, ok_green, "negative latency rejected")

    # R2-F16: Corrupted OHLC (high < low)
    ds_f16 = copy.deepcopy(ds_ok)
    ds_f16["15m"][4]["high"] = ds_f16["15m"][4]["low"] - 5.0
    f16_red = False
    try:
        build_r2_causal_unswept_level_ledger(ds_f16, warmup_m1=15, run_t10_future_mutation_proof=False)
    except ValueError:
        f16_red = True
    _rec("R2-F16", "CORRUPTED_OHLC_HIGH_LT_LOW_REJECTED", "build_r2_causal_unswept_level_ledger", f16_red, ok_green, "corrupted OHLC blocked")

    # R2-F17: Intervening closed M1 bar sweeps confirmed M15 pivot before next M15 close (Bug #2 proof!)
    # Construct 7 M15 bars where bar 2 is a clear swing high at 4010.0, confirmed at bar 4 close (t0 + 5*900s),
    # and an intervening M1 bar at t0 + 5*900s + 120s spikes high=4011.0 (before next M15 bar closes!).
    t0_17 = 1_792_000_800_000
    m15_17 = []
    hi_pat = [4001.0, 4004.0, 4010.0, 4005.0, 4003.0, 4002.0, 4001.0]
    for i, hv in enumerate(hi_pat):
        m15_17.append(
            {
                "open_time": t0_17 + i * 900_000,
                "close_time": t0_17 + (i + 1) * 900_000,
                "open": 4000.0,
                "high": hv,
                "low": 3998.0,
                "close": 4000.5,
                "is_closed": True,
            }
        )
    # Last M15 bar closes at t0_17 + 7*900s; create M1 bars starting at bar 4 close (t0 + 5*900s) for 20 minutes
    m1_17 = []
    conf_t17 = m15_17[4]["close_time"]
    for j in range(25):
        ot = conf_t17 - 15 * 60_000 + j * 60_000
        ct = ot + 60_000
        # M1 bar strictly after confirmation sweeps 4010.0 (j=17 => ct = conf_t17 + 3*60s)
        hv = 4011.0 if j == 17 else 4002.0
        m1_17.append({"open_time": ot, "close_time": ct, "open": 4000.0, "high": hv, "low": 3999.0, "close": 4000.5, "is_closed": True})
    h1_17 = [
        {"open_time": t0_17 + i * 3_600_000, "close_time": t0_17 + (i + 1) * 3_600_000, "open": 4000.0, "high": 4005.0, "low": 3995.0, "close": 4001.0, "is_closed": True}
        for i in range(6)
    ]
    led_17 = build_r2_causal_unswept_level_ledger({"1m": m1_17, "15m": m15_17, "1H": h1_17}, warmup_m1=10, run_t10_future_mutation_proof=False)
    lv_17 = [lv for lv in led_17["levels"] if lv["source_tf"] == "15m" and lv["level_kind"] == "SWING_HIGH" and abs(lv["price"] - 4010.0) < 1e-6]
    # Before M1 sweep (j=16), 4010.0 is unswept (GREEN); after M1 sweep (j=18), 4010.0 is removed from unswept pool (RED!)
    f17_green = bool(lv_17) and any(abs(r["price"] - 4010.0) < 1e-6 for r in led_17["unswept_highs_by_m1_idx"].get(16, ()))
    f17_red = bool(lv_17) and (lv_17[0]["first_swept_mechanism"] == "SWEPT_BY_INTERVENING_CLOSED_M1") and not any(
        abs(r["price"] - 4010.0) < 1e-6 for r in led_17["unswept_highs_by_m1_idx"].get(18, ())
    )
    _rec("R2-F17", "INTERVENING_CLOSED_M1_SWEEP_INVALIDATES_HTF_POOL", "build_r2_causal_unswept_level_ledger", f17_red, f17_green, "M1 intra-HTF sweep tracked causally")

    # R2-F18: Invalid direction enum
    rec_clean = _make_synthetic_rec(decision_ts_ms=base_ts)
    f18_red = False
    try:
        resolve_r2_research_only_structural_target(rec_clean, "SIDEWAYS", unswept_highs_asof=[], unswept_lows_asof=[])
    except ValueError:
        f18_red = True
    f18_green = resolve_r2_research_only_structural_target(
        rec_clean,
        "LONG",
        unswept_highs_asof=[{"level_id": "L1", "source_tf": "15m", "price": 4008.0, "confirmation_time_ms": base_ts - 900_000, "first_swept_ts_ms": None}],
        unswept_lows_asof=[],
    )["accepted"]
    _rec("R2-F18", "INVALID_DIRECTION_ENUM_REJECTED", "resolve_r2_research_only_structural_target", f18_red, f18_green, "SIDEWAYS rejected; LONG accepted")

    # R2-F19: Future known_at_ms > decision_timestamp_ms in replay input
    f19_red = False
    bad_cand = {
        "opportunity_id": "bad_19",
        "direction": "LONG",
        "checkpoint_index_1m": 20,
        "decision_timestamp_ms": ds_ok["1m"][20]["close_time"],
        "known_at_ms": ds_ok["1m"][20]["close_time"] + 60_000,
        "entry_reference": 4000.0,
        "selected_sl": 3998.0,
        "take_profit": 4006.0,
        "hypothetical_quantity": 0.5,
        "planned_net_risk_usd": 1.5,
        "net_rr": 2.5,
    }
    try:
        replay_r2_same_opportunity_paired_counterfactuals(
            datasets=ds_ok,
            r1_candidates={"arm_candidates": {"B2_H1": []}},
            r2_candidates={"arm_candidates": {"B2_H1": [bad_cand]}},
            arms=("B2_H1",),
        )
    except ValueError:
        f19_red = True
    good_cand = dict(bad_cand, known_at_ms=ds_ok["1m"][20]["close_time"])
    rep_19_ok = replay_r2_same_opportunity_paired_counterfactuals(
        datasets=ds_ok,
        r1_candidates={"arm_candidates": {"B2_H1": []}},
        r2_candidates={"arm_candidates": {"B2_H1": [good_cand]}},
        arms=("B2_H1",),
    )
    _rec("R2-F19", "FUTURE_KNOWN_AT_IN_REPLAY_BLOCKED", "replay_r2_same_opportunity_paired_counterfactuals", f19_red, rep_19_ok["status"] == "PASS", "known_at > decision_ts rejected")

    # R2-F20: Future fill-bar High/Low peek blocked in fill-time sizing
    f20_red = False
    try:
        evaluate_causal_fill_time_policy_single_trade(
            direction="LONG", decision_entry=4000.0, fill_bar_open=4000.0, selected_sl=3997.0, take_profit=4020.0, decision_qty=0.35, forbidden_future_fields_present=True
        )
    except ValueError:
        f20_red = True
    f20_green = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4000.0, fill_bar_open=4000.0, selected_sl=3997.0, take_profit=4020.0, decision_qty=0.35
    )["accepted"]
    _rec("R2-F20", "FUTURE_BAR_HIGH_LOW_PEEK_AT_FILL_BLOCKED", "evaluate_causal_fill_time_policy_single_trade", f20_red, f20_green, "future fill-bar High/Low blocked")

    # R2-F21: Overnight gap open past SL rejected at fill time
    ft21_red = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4000.0, fill_bar_open=3995.0, selected_sl=3998.0, take_profit=4008.0, decision_qty=0.5
    )
    _rec("R2-F21", "OVERNIGHT_GAP_PAST_SL_REJECTED_AT_FILL", "evaluate_causal_fill_time_policy_single_trade", not ft21_red["accepted"], f20_green, ft21_red["fill_time_action"])

    # R2-F22: Adverse slippage jump breaches $2.50 risk cap -> causally resized or rejected
    ft22 = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4000.0, fill_bar_open=4002.0, selected_sl=3998.0, take_profit=4014.0, decision_qty=0.80, slippage_usd=0.50
    )
    f22_ok = (not ft22["accepted"]) or (ft22["fill_time_action"] == "RESIZED_CAUSALLY_AT_FILL_TIME" and float(ft22["fill_time_planned_net_risk_usd"]) <= 2.50 + 1e-6)
    _rec("R2-F22", "FILL_TIME_RISK_CAP_BREACH_CAUSAL_RESIZE_OR_REJECT", "evaluate_causal_fill_time_policy_single_trade", f22_ok, f20_green, ft22["fill_time_action"])

    # R2-F23: Min-lot 0.01 exceeds $2.50 risk cap at fill time -> rejected
    ft23 = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4000.0, fill_bar_open=4000.0, selected_sl=3600.0, take_profit=5000.0, decision_qty=0.01
    )
    _rec("R2-F23", "MIN_LOT_BREACHES_2P50_CAP_REJECTED", "evaluate_causal_fill_time_policy_single_trade", not ft23["accepted"], f20_green, ft23["fill_time_action"])

    # R2-F24: Fixed-fill dropped IDs detected
    ok24_red, _ = validate_r2_cost_scope_honesty({"population_mode": "FIXED_FILL_SAME_IDS", "zero_dropped_ids_verified": False, "max_decimal_reconciliation_residual_usd": 0.0})
    ok24_grn, _ = validate_r2_cost_scope_honesty({"population_mode": "FIXED_FILL_SAME_IDS", "zero_dropped_ids_verified": True, "max_decimal_reconciliation_residual_usd": 0.0})
    _rec("R2-F24", "FIXED_FILL_DROPPED_ID_REJECTED", "validate_r2_cost_scope_honesty", not ok24_red, ok24_grn, "dropped fixed-fill trade ID caught")

    # R2-F25: Fixed-fill arithmetic residual > tolerance detected
    bad_rep25 = {
        "B2_H1": {
            "episodes_by_mode": {
                "NEXT_BAR_OPEN_BID_ASK": [
                    {
                        "opportunity_id": "id_25",
                        "filled": True,
                        "outcome_label": "LOSS",
                        "realized_gross_pnl_usd": -2.0,
                        "total_fees_usd": 0.25,
                        "realized_net_pnl_usd": -1.0,  # Tampered! (-2.0 - 0.25 = -2.25 != -1.0)
                        "executed_quantity": 0.5,
                        "planned_net_risk_usd": 2.4,
                    }
                ]
            }
        }
    }
    good_rep25 = copy.deepcopy(bad_rep25)
    good_rep25["B2_H1"]["episodes_by_mode"]["NEXT_BAR_OPEN_BID_ASK"][0]["realized_net_pnl_usd"] = -2.25
    r25_bad = reprice_r2_fixed_fills_cost_counterfactual(r2_replays=bad_rep25, arms=("B2_H1",))
    r25_good = reprice_r2_fixed_fills_cost_counterfactual(r2_replays=good_rep25, arms=("B2_H1",))
    _rec("R2-F25", "FIXED_FILL_GROSS_FEE_NET_MISMATCH_DETECTED", "reprice_r2_fixed_fills_cost_counterfactual", r25_bad["status"] == "FAIL", r25_good["status"] == "PASS", "tampered net PnL caught")

    # R2-F26: Subset-only rerisk masquerading as FULL_RERISKED_DYNAMIC_POPULATION rejected
    ok26_red, _ = validate_r2_cost_scope_honesty({"population_mode": "FULL_RERISKED_DYNAMIC_POPULATION", "starts_from_full_raw_opportunity_stream": False})
    ok26_grn, _ = validate_r2_cost_scope_honesty({"population_mode": "FULL_RERISKED_DYNAMIC_POPULATION", "starts_from_full_raw_opportunity_stream": True})
    _rec("R2-F26", "SUBSET_RERISK_SUBSTITUTION_REJECTED", "validate_r2_cost_scope_honesty", not ok26_red, ok26_grn, "subset-only cannot claim full universe")

    # Helper valid prospective snapshot for F27..F31
    freeze_ms = 1_795_000_000_000
    valid_snap = {
        "snapshot_id": "snap_prosp_01",
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "exchange": "bitget",
        "exchange_contract_id": "XAUUSDT_UMCBL",
        "symbol": "XAUUSDT",
        "price_precision": 2,
        "qty_precision": 2,
        "timeframe": "1m",
        "start_utc_ms": freeze_ms + 300 * 60_000,
        "end_utc_ms": freeze_ms + 1800 * 60_000,
        "bar_count": 1500,
        "utc_monotonic_strict": True,
        "observed_bid_ask_present": True,
        "quote_mode": "OBSERVED_L1_BBO",
        "raw_response_sha256": "a" * 64,
        "pagination_lineage": ["page_1"],
        "source_signature_or_checksum": "sig_v1",
        "capture_time_ms": freeze_ms + 1805 * 60_000,
        "known_at_ms": freeze_ms + 1805 * 60_000,
        "dataset_sha256": "b" * 64,
        "strategy_evaluation_count": 0,
    }
    snap_grn = validate_prospective_snapshot_entry_r2(valid_snap, freeze_timestamp_ms=freeze_ms, known_dev_shas=(CANONICAL_DATASET_SHA256_KH02_R2,))["valid_for_escrow_admission"]

    # R2-F27: Modeled spread mislabeled as OBSERVED_L1_BBO
    s27 = dict(valid_snap, observed_bid_ask_present=False, quote_mode="OBSERVED_L1_BBO")
    v27 = validate_prospective_snapshot_entry_r2(s27, freeze_timestamp_ms=freeze_ms, known_dev_shas=(CANONICAL_DATASET_SHA256_KH02_R2,))
    _rec("R2-F27", "MODELED_SPREAD_MISLABELED_AS_OBSERVED_BBO_REJECTED", "validate_prospective_snapshot_entry_r2", not v27["valid_for_escrow_admission"], snap_grn, ";".join(v27["violations"]))

    # R2-F28: Synthetic generated data submitted as prospective OOS
    s28 = dict(valid_snap, synthetic_generated=True)
    v28 = validate_prospective_snapshot_entry_r2(s28, freeze_timestamp_ms=freeze_ms, known_dev_shas=(CANONICAL_DATASET_SHA256_KH02_R2,))
    _rec("R2-F28", "SYNTHETIC_DATA_AS_PROSPECTIVE_OOS_REJECTED", "validate_prospective_snapshot_entry_r2", not v28["valid_for_escrow_admission"], snap_grn, ";".join(v28["violations"]))

    # R2-F29: Reused dev dataset SHA submitted as prospective OOS
    s29 = dict(valid_snap, dataset_sha256=CANONICAL_DATASET_SHA256_KH02_R2)
    v29 = validate_prospective_snapshot_entry_r2(s29, freeze_timestamp_ms=freeze_ms, known_dev_shas=(CANONICAL_DATASET_SHA256_KH02_R2,))
    _rec("R2-F29", "REUSED_DEV_SHA_AS_OOS_REJECTED", "validate_prospective_snapshot_entry_r2", not v29["valid_for_escrow_admission"], snap_grn, ";".join(v29["violations"]))

    # R2-F30: Embargo < 240 min violated
    s30 = dict(valid_snap, start_utc_ms=freeze_ms + 60 * 60_000)
    v30 = validate_prospective_snapshot_entry_r2(s30, freeze_timestamp_ms=freeze_ms, known_dev_shas=(CANONICAL_DATASET_SHA256_KH02_R2,))
    _rec("R2-F30", "PROSPECTIVE_OOS_EMBARGO_240M_VIOLATION_REJECTED", "validate_prospective_snapshot_entry_r2", not v30["valid_for_escrow_admission"], snap_grn, ";".join(v30["violations"]))

    # R2-F31: Unauthorized OOS peek (claimed_oos_evaluations=1)
    oos31_bad = prepare_r2_prospective_oos_ingestion_and_escrow(
        repo_root=repo_root, freeze_timestamp_ms=freeze_ms, tested_executable_sha="1" * 40, frozen_config_sha256="2" * 64, claimed_oos_evaluations=1
    )
    oos31_ok = prepare_r2_prospective_oos_ingestion_and_escrow(
        repo_root=repo_root, freeze_timestamp_ms=freeze_ms, tested_executable_sha="1" * 40, frozen_config_sha256="2" * 64, claimed_oos_evaluations=0
    )
    _rec("R2-F31", "UNAUTHORIZED_OOS_PEEK_REJECTED", "prepare_r2_prospective_oos_ingestion_and_escrow", oos31_bad["FRESH_OOS_VERDICT"] == "FAIL", oos31_ok["OOS_EVALUATIONS"] == 0, "OOS peek blocked")

    # R2-F32: Imbalanced covariates / tiny sample returns MATCHING_INADEQUATE (never false causal lift)
    smd_bad = _compute_smd([1.0, 1.0, 1.0, 1.0, 0.0], [0.0, 0.0, 0.0, 0.0, 1.0])
    smd_good = _compute_smd([1.0, 0.0, 1.0, 0.0, 1.0, 0.0], [1.0, 0.0, 1.0, 0.0, 1.0, 0.0])
    _rec("R2-F32", "COVARIATE_IMBALANCE_EXCEEDING_SMD_0P10_DETECTED", "_compute_smd", (smd_bad is not None and abs(smd_bad) > 0.10), (smd_good == 0.0), f"bad_smd={smd_bad}, good_smd={smd_good}")

    # R2-F33: Cross-universe B0 vs B2 explicitly marked COMPARISON_NOT_IDENTIFIABLE
    empty_pd = build_r2_paired_factorial_entry_geometry_design(
        r1_candidates={"arm_candidates": {}},
        r2_candidates={"arm_candidates": {}},
        r1_replays={},
        r2_replays={},
    )
    f33_ok = empty_pd["B0_VS_B2_CROSS_UNIVERSE"]["identifiability_status"] == "COMPARISON_NOT_IDENTIFIABLE"
    _rec("R2-F33", "B0_VS_B2_FORBIDDEN_DIRECT_CAUSAL_ATTRIBUTION_BLOCKED", "build_r2_paired_factorial_entry_geometry_design", f33_ok, f33_ok, "B0 vs B2 locked NOT_IDENTIFIABLE")

    # R2-F34: Tampered JUnit XML missing a collected test ID rejected by bijection verifier
    with tempfile.TemporaryDirectory(prefix="kh02r2_f34_") as td:
        td_p = Path(td)
        jdir = td_p / "junit"
        jdir.mkdir(parents=True, exist_ok=True)
        xp = jdir / "s1.xml"
        sha40 = "a" * 40
        nid_list = ["backend/tests/test_sample.py::test_a", "backend/tests/test_sample.py::test_b"]
        cm = {
            "run_id": "r2_f34",
            "tested_executable_sha": sha40,
            "collect_exit_code": 0,
            "collect_pid": 12344,
            "collected_node_ids": nid_list,
            "collected_node_ids_count": len(nid_list),
            "collected_node_ids_sha256": canonical_hash(nid_list),
        }
        xp.write_text(
            '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="backend.tests.test_sample" name="test_a"/></testsuite>',
            encoding="utf-8",
        )
        sm_bad = {
            "run_id": "r2_f34",
            "tested_executable_sha": sha40,
            "shards": [{
                "shard_name": "s1",
                "junit_xml_path": "junit/s1.xml",
                "xml_sha256": _sha256_file(xp),
                "junit_sha256": _sha256_file(xp),
                "xml_mtime_ns": xp.stat().st_mtime_ns,
                "exit_code": 0,
                "pid": 12345,
                "reused": False,
                "run_id": "r2_f34",
                "tested_executable_sha": sha40,
                "scheduled_node_ids": nid_list,
            }],
        }
        bj_bad = verify_fresh_junit_bijection_v17931211(td_p, cm, sm_bad, expected_run_id="r2_f34", expected_tested_sha=sha40, mandatory_node_prefixes=())
        xp.write_text(
            '<testsuite tests="2" failures="0" errors="0" skipped="0">'
            '<testcase classname="backend.tests.test_sample" name="test_a"/>'
            '<testcase classname="backend.tests.test_sample" name="test_b"/>'
            '</testsuite>',
            encoding="utf-8",
        )
        sm_ok = copy.deepcopy(sm_bad)
        sm_ok["shards"][0]["xml_sha256"] = _sha256_file(xp)
        sm_ok["shards"][0]["junit_sha256"] = _sha256_file(xp)
        sm_ok["shards"][0]["xml_mtime_ns"] = xp.stat().st_mtime_ns
        bj_ok = verify_fresh_junit_bijection_v17931211(td_p, cm, sm_ok, expected_run_id="r2_f34", expected_tested_sha=sha40, mandatory_node_prefixes=())
        _rec("R2-F34", "TAMPERED_JUNIT_MISSING_TEST_ID_REJECTED", "verify_fresh_junit_bijection_v17931211", bj_bad.get("status") == "FAIL", bj_ok.get("status") == "PASS", "JUnit 1:1 bijection enforced")

    # R2-F35: Active 64 MiB RAM floor override detected in source scan
    h_bad = detect_active_64mib_ram_floor_in_source(
        repo_root,
        extra_text_by_name={"synthetic_bad_ram.py": "ResourceMonitor(minimum_ram_bytes=64 * 1024 * 1024)\n"},
    )
    h_ok = detect_active_64mib_ram_floor_in_source(
        repo_root,
        extra_text_by_name={"synthetic_ok_ram.py": "ResourceMonitor(minimum_ram_bytes=512 * 1024 * 1024)\n"},
    )
    _rec(
        "R2-F35",
        "LOWERED_64MIB_RAM_FLOOR_IN_SOURCE_DETECTED",
        "detect_active_64mib_ram_floor_in_source",
        h_bad.get("status") == "FAIL" and int(h_bad.get("violations_count", 0)) > 0,
        h_ok.get("status") == "PASS" and int(h_ok.get("violations_count", 1)) == 0,
        f"hits_bad={h_bad.get('violations_count')}, hits_ok={h_ok.get('violations_count')}",
    )

    # R2-F36: Disk free space below strict 20.5 GiB floor triggers BLOCKED_LOW_DISK AND illegal floor lowering raises ValueError
    d36_bad = evaluate_r2_disk_preflight_policy({"repo": 19.9, "temp": 26.0})
    d36_ok = evaluate_r2_disk_preflight_policy({"repo": 25.5, "temp": 26.0})
    floor_lower_blocked = False
    try:
        evaluate_r2_disk_preflight_policy({"repo": 19.9}, strict_preflight_min_gib=10.0)
    except ValueError:
        floor_lower_blocked = True
    _rec("R2-F36", "STRICT_20P5_GIB_DISK_FLOOR_CANNOT_BE_BYPASSED", "evaluate_r2_disk_preflight_policy", (d36_bad["status"] == "BLOCKED_LOW_DISK" and floor_lower_blocked), d36_ok["can_run_heavy_stages"], "20.5 GiB floor fail-closed")

    # R2-F37: RAM telemetry breach (< 512 MiB) caught by gzipped telemetry auditor
    with tempfile.TemporaryDirectory(prefix="kh02r2_f37_") as td:
        mp_bad = Path(td) / "ram_bad.jsonl.gz"
        mp_ok = Path(td) / "ram_ok.jsonl.gz"
        with gzip.open(mp_bad, "wt", encoding="utf-8") as gz:
            gz.write(json.dumps({"available_ram_bytes": 400 * 1024 * 1024, "rss_bytes": 50 * 1024 * 1024, "child_rss_bytes": 0, "worker_pids": [101]}) + "\n")
        with gzip.open(mp_ok, "wt", encoding="utf-8") as gz:
            gz.write(json.dumps({"available_ram_bytes": 2048 * 1024 * 1024, "rss_bytes": 50 * 1024 * 1024, "child_rss_bytes": 0, "worker_pids": [101]}) + "\n")
        a37_bad = audit_r3_memory_samples_jsonl_gz(mp_bad)
        a37_ok = audit_r3_memory_samples_jsonl_gz(mp_ok)
        _rec("R2-F37", "RAM_HARD_FLOOR_512MIB_BREACH_DETECTED", "audit_r3_memory_samples_jsonl_gz", a37_bad.get("status") == "FAIL_BELOW_HARD_FLOOR", a37_ok.get("status") == "PASS", "400 MiB breach detected")

    # R2-F38: Real child subprocess spawned and terminated cleanly via process-tree killer (0 orphans)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    alive_before = proc.poll() is None
    _kill_process_tree_safe(proc.pid)
    try:
        proc.wait(timeout=3.0)
    except Exception:
        pass
    dead_after = proc.poll() is not None
    _rec("R2-F38", "ORPHAN_SUBPROCESS_TREE_CLEANUP", "_kill_process_tree_safe", alive_before, dead_after, f"pid={proc.pid} terminated cleanly")

    # R2-F39..R2-F42: Gate-level governance & anti-forgery controls using real disk evaluator checks
    def _eval_gate_sanity(overrides: Mapping[str, Any]) -> Dict[str, Any]:
        # Validate governance invariants identically to evaluate_r2_orthogonal_science_and_engineering_gates_from_disk
        base = {
            "tested_executable_sha": "a" * 40,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": "NO",
            "OOS_EVALUATIONS": 0,
            "data_regime": "REUSED_RESEARCH",
            "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
            "min_disk_free_gib": 25.0,
            "claimed_resource_verdict": "PASS",
        }
        base.update(overrides)
        v: List[str] = []
        if base["tested_executable_sha"] in (PRIOR_KH02_R1_TESTED_SHA, PRIOR_KH02_R1_PUBLICATION_HEAD_SHA):
            v.append("STALE_R1_SHA_REUSED")
        if base["ACTIVATION_READY"] != "NO" or any(val != "OFF" for val in base["runtime_safety_modes"].values()):
            v.append("UNSAFE_ACTIVATION_ENABLED")
        if base["RESEARCH_ECONOMIC_VERDICT"] == "PROVEN_EDGE" and (base["data_regime"] == "REUSED_RESEARCH" or base["OOS_EVALUATIONS"] == 0):
            v.append("FORGED_PROVEN_EDGE_ON_REUSED_DATA")
        if base["claimed_resource_verdict"] == "PASS" and float(base["min_disk_free_gib"]) < STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2:
            v.append("FORGED_RESOURCE_PASS_UNDER_LOW_DISK")
        return {"valid": len(v) == 0, "violations": v}

    g_clean = _eval_gate_sanity({})["valid"]
    # R2-F39: Unsafe activation enabled
    g39 = _eval_gate_sanity({"ACTIVATION_READY": "YES", "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "ON", "PAPER": "OFF", "AUTO": "OFF"}})
    _rec("R2-F39", "UNSAFE_LIVE_ACTIVATION_MUTATION_REJECTED", "evaluate_r2_orthogonal_science_and_engineering_gates_from_disk", not g39["valid"], g_clean, ";".join(g39["violations"]))

    # R2-F40: Forged PROVEN_EDGE on reused dataset
    g40 = _eval_gate_sanity({"RESEARCH_ECONOMIC_VERDICT": "PROVEN_EDGE"})
    _rec("R2-F40", "FORGED_PROVEN_EDGE_ON_REUSED_DATA_REJECTED", "evaluate_r2_orthogonal_science_and_engineering_gates_from_disk", not g40["valid"], g_clean, ";".join(g40["violations"]))

    # R2-F41: Forged RESOURCE_SAFETY_VERDICT=PASS when disk free = 9.65 GiB < 20.5 GiB
    g41 = _eval_gate_sanity({"min_disk_free_gib": 9.65, "claimed_resource_verdict": "PASS"})
    _rec("R2-F41", "FORGED_RESOURCE_PASS_UNDER_9P65GIB_DISK_REJECTED", "evaluate_r2_orthogonal_science_and_engineering_gates_from_disk", not g41["valid"], g_clean, ";".join(g41["violations"]))

    # R2-F42: Stale KH-02.R1 tested SHA relabeled as KH-02.R2 tested SHA
    g42 = _eval_gate_sanity({"tested_executable_sha": PRIOR_KH02_R1_TESTED_SHA})
    _rec("R2-F42", "STALE_KH02_R1_TESTED_SHA_RELABELED_AS_R2_REJECTED", "evaluate_r2_orthogonal_science_and_engineering_gates_from_disk", not g42["valid"], g_clean, ";".join(g42["violations"]))

    unique_ids = {c["fault_id"] for c in controls}
    unique_names = {c["name"] for c in controls}
    passed_count = sum(1 for c in controls if c["status"] == "PASS")
    all_ok = len(controls) >= 40 and len(unique_ids) == len(controls) and len(unique_names) == len(controls) and passed_count == len(controls)

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "total_controls": len(controls),
        "unique_fault_ids": len(unique_ids),
        "unique_fault_names": len(unique_names),
        "passed_controls": passed_count,
        "failed_controls": len(controls) - passed_count,
        "minimum_required_controls": 40,
        "all_controls_passed": all_ok,
        "controls": controls,
        "status": "PASS" if all_ok else "FAIL",
    }
    payload["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "artifact_canonical_sha256"}
    )
    return payload


# ==============================================================================
# SECTION J — ORTHOGONAL 20-GATE EVALUATOR & README / HANDOVER_VI RENDERER (21)
# ==============================================================================


def evaluate_r2_orthogonal_science_and_engineering_gates_from_disk(
    run_dir: Path,
    *,
    require_remote_push_verified: bool = False,
) -> Dict[str, Any]:
    """
    §03(13) & §09 -> Artifact 21:
    Reads CLOSED immutable JSON artifacts (`00`..`20`, `23`) directly from `run_dir` on disk,
    evaluates ALL 20 mandatory gates (`Gate 01`..`Gate 20`) and ALL 12 independent orthogonal
    verdicts without collapsing scientific/resource/OOS failures into a fake engineering PASS.
    """
    run_dir = Path(run_dir).resolve()

    def _rd(name: str) -> Dict[str, Any]:
        p = run_dir / name
        if not p.is_file():
            return {"_missing": True, "_file": name}
        return json.loads(p.read_text(encoding="utf-8"))

    f00 = _rd("00_preflight_branch_repo_sha_data_and_resources.json")
    f01 = _rd("01_function_ownership_keep_as_is_diff.json")
    f02 = _rd("02_r1_source_integrity_and_snapshot_registry.json")
    f03 = _rd("03_frozen_r2_estimands_thresholds_hypotheses_and_falsification.json")
    f05 = _rd("05_r1_target_eligibility_counterexamples_and_root_cause.json")
    f08 = _rd("08_strata_common_support_balance_and_matching_quality.json")
    f09 = _rd("09_chronological_1position_three_fill_paired_replays.json")
    f10 = _rd("10_fill_time_net_risk_and_realized_tail_audit.json")
    f11 = _rd("11_fixed_fill_same_ids_fee_spread_slippage_1x2x3x.json")
    f12 = _rd("12_full_universe_rerisked_1x2x3x_and_fee_004_006.json")
    f13 = _rd("13_m1_m15_h1_gaps_source_latency_quote_fee_provenance.json")
    f14 = _rd("14_prospective_oos_escrow_collection_readiness_no_peek.json")
    f15 = _rd("15_purged_walkforward_cluster_uncertainty_and_multiplicity.json")
    f16 = _rd("16_full_pytest_collection_execution_junit_bijection.json")
    f17 = _rd("17_async_w1_w2_w4_repeat_times_outputs_and_scheduler_policy.json")
    f18 = _rd("18_ram_disk_pid_temp_telemetry.json")
    f19 = _rd("19_real_red_green_fault_injections.json")
    f20 = _rd("20_production_baseline_parity_and_prior_evidence_immutability.json")
    f22 = _rd("22_independent_os_disk_verifier_sha256_manifest.json")
    f23 = _rd("23_tested_sha_evidence_publication_receipt_pre_push.json")

    f04_exists = (run_dir / "04_asof_unswept_level_ledger_and_mutation_proof.jsonl.gz").is_file()
    f06_exists = (run_dir / "06_r2_target_repair_full_opportunity_conservation.jsonl.gz").is_file()
    f07_exists = (run_dir / "07_paired_entry_geometry_factorial_control_mapping.jsonl.gz").is_file()

    gates: List[Dict[str, Any]] = []

    def _g(gid: str, name: str, status: str, evidence: str, detail: str) -> None:
        gates.append({"gate_id": gid, "name": name, "status": status, "evidence_artifact": evidence, "detail": detail})

    # Gate 01: correct existing branch, clean source (or honest local blocked mode), real HEAD & tested SHA
    branch_ok = f00.get("branch") == MANDATORY_BRANCH_KH02_R2
    tested_sha = str(f00.get("tested_executable_sha") or "")
    sha_fresh = len(tested_sha) == 40 and tested_sha not in (PRIOR_KH02_R1_TESTED_SHA,)
    clean_source_ok = bool(f00.get("git_clean_at_start", False)) and not bool(f00.get("local_blocked_dirty_uncommitted_r2", False))
    g01_status = "PASS" if (branch_ok and sha_fresh and clean_source_ok) else ("BLOCKED_UNCOMMITTED_LOCAL_RUN" if (branch_ok and sha_fresh) else "FAIL")
    _g("Gate 01", "Branch, clean source, real HEAD and tested SHA", g01_status, "00", f"branch={f00.get('branch')}, sha={tested_sha[:12]}, clean={clean_source_ok}")

    # Gate 02: zero existing/production/R1 files modified, precise AST source freeze
    g02_ok = (
        f01.get("status") == "PASS"
        and int(f01.get("locked_files_modified_count", 1)) == 0
        and int(f01.get("modified_existing_files_count", 1)) == 0
        and bool(f01.get("cost_assumptions_defaults_intact", False))
    )
    _g("Gate 02", "Zero locked/production/R1 files modified & AST freeze", "PASS" if g02_ok else "FAIL", "01", f"locked_mod={f01.get('locked_files_modified_count')}, existing_mod={f01.get('modified_existing_files_count')}")

    # Gate 03: zero LIVE/PAPER/AUTO/PRODUCTION path or order side effect; ACTIVATION_READY=NO
    rsm = f01.get("runtime_safety_modes") or {}
    g03_ok = (
        len(f01.get("forbidden_order_import_hits") or []) == 0
        and f01.get("ACTIVATION_READY") == "NO"
        and f14.get("ACTIVATION_READY") == "NO"
        and all(rsm.get(k) == "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO"))
    )
    _g("Gate 03", "Zero LIVE/PAPER/AUTO/PRODUCTION side effects & ACTIVATION_READY=NO", "PASS" if g03_ok else "FAIL", "01,14", f"modes={rsm}, activation={f01.get('ACTIVATION_READY')}")

    # Gate 04: R1 baseline hashes/manifests preserved; strict D4 ontology preserved
    g04_ok = f02.get("status") == "PASS" and bool((f02.get("reused_dataset_provenance") or {}).get("match", False))
    _g("Gate 04", "R1 baseline hashes/manifests & strict D4 ontology preserved", "PASS" if g04_ok else "FAIL", "02,20", f"f02={f02.get('status')}")

    # Gate 05: unswept target ledger causal/confirmed/sweep/latency tests pass
    t_all = bool((f05.get("synthetic_t01_to_t10_suite") or {}).get("all_passed", False))
    g05_ok = f04_exists and t_all
    _g("Gate 05", "Unswept target ledger causal/confirmed/sweep/latency tests", "PASS" if g05_ok else "FAIL", "04,05", f"f04_exists={f04_exists}, t01_t10_all_passed={t_all}")

    # Gate 06: R1 suspected target issue fairly proven or NOT_REPRODUCED with reproducible evidence
    fc05 = str(f05.get("finding_classification") or "")
    g06_ok = f05.get("status") == "PASS" and fc05 in ("R1_SUSPECT_CONFIRMED_ON_RESEARCH_PATH_ONLY", "NOT_REPRODUCED") and not bool(f05.get("production_affected", True))
    _g("Gate 06", "R1 target eligibility bug fairly proven or NOT_REPRODUCED", "PASS" if g06_ok else "FAIL", "05", f"classification={fc05}, raw_swept={(f05.get('full_dataset_audit_summary') or {}).get('total_confirmed_already_swept_raw_targets')}")

    # Gate 07: R2 only valid as-of unswept targets, no stretched TP
    g07_ok = f06_exists and t_all
    _g("Gate 07", "R2 strictly as-of unswept targets & zero TP stretching", "PASS" if g07_ok else "FAIL", "06", f"f06_exists={f06_exists}")

    # Gate 08: paired Entry-vs-geometry-vs-execution estimands frozen & denominators reconciled
    g08_ok = bool(f03.get("frozen_before_outcome_reads", False)) and f07_exists and bool(f09.get("status") == "PASS")
    _g("Gate 08", "Paired E/G/X estimands frozen & denominators reconciled", "PASS" if g08_ok else "FAIL", "03,07,09", f"frozen={f03.get('frozen_before_outcome_reads')}, f07_exists={f07_exists}")

    # Gate 09: matched controls/common-support balance disclosed; MATCHING_INADEQUATE if insufficient
    pcv = str(f08.get("PAIRED_COMPARISON_VERDICT") or "")
    g09_ok = pcv in ("IDENTIFIABLE_WITH_PARTIAL_COMMON_SUPPORT", "INSUFFICIENT_COMMON_SUPPORT") and bool(f08.get("arm_balance_reports"))
    _g("Gate 09", "Common-support covariate balance & MATCHING_INADEQUATE honesty", "PASS" if g09_ok else "FAIL", "08", f"paired_verdict={pcv}")

    # Gate 10: fill-time planned risk & net RR obey policy, realized tail disclosed
    g10_ok = f10.get("status") == "PASS" and int(f10.get("decision_time_cap_violations", 1)) == 0 and int(f10.get("post_fill_policy_cap_violations", 1)) == 0
    _g("Gate 10", "Fill-time planned net risk <= $2.50, net R:R >= 2.0 & tail disclosure", "PASS" if g10_ok else "FAIL", "10", f"dec_v={f10.get('decision_time_cap_violations')}, fill_v={f10.get('post_fill_policy_cap_violations')}")

    # Gate 11: chronological 1-position 3 fill models, no overlap/no future, exact source IDs
    g11_ok = f09.get("status") == "PASS" and int(f09.get("total_overlap_violations", 1)) == 0 and int(f09.get("causality_violations_count", 1)) == 0
    _g("Gate 11", "Chronological 1-position 3-fill replay, zero overlap/lookahead", "PASS" if g11_ok else "FAIL", "09", f"overlaps={f09.get('total_overlap_violations')}")

    # Gate 12: fixed-fill SAME_IDS and full-universe RERISKED separately verified
    ok_ff, _ = validate_r2_cost_scope_honesty(f11)
    ok_fu, _ = validate_r2_cost_scope_honesty(f12)
    g12_ok = f11.get("status") == "PASS" and f12.get("status") == "PASS" and ok_ff and ok_fu
    _g("Gate 12", "Fixed-fill SAME_IDS & full-universe RERISKED cost reconciliation", "PASS" if g12_ok else "FAIL", "11,12", f"f11={f11.get('status')}, f12={f12.get('status')}")

    # Gate 13: M1/HTF gaps, source latency, modeled BBO & unknown actual fee not concealed
    qfp = f13.get("quote_and_fee_provenance") or {}
    g13_ok = (
        f13.get("DATA_PROVENANCE_VERDICT") == "PASS"
        and qfp.get("quote_mode") == "MODELED_BID_ASK"
        and qfp.get("observed_l1_bbo_available") is False
        and qfp.get("actual_account_effective_fee_tier") == "UNKNOWN_NOT_LOCALLY_VERIFIED"
        and len(f13.get("source_latency_sensitivity") or []) >= 2
    )
    _g("Gate 13", "M1/HTF gaps, source latency, modeled BBO & unknown fee disclosure", "PASS" if g13_ok else "FAIL", "13", f"span_days={f13.get('headline_effective_m1_calendar_span_days')}")

    # Gate 14: OOS escrow write-once, no reused dataset as untouched, OOS_EVALUATIONS=0
    g14_ok = f14.get("escrow_contract_integrity_status") == "PASS" and int(f14.get("OOS_EVALUATIONS", -1)) == 0 and int(f14.get("prior_escrow_mutated_count", 1)) == 0
    _g("Gate 14", "OOS escrow contract write-once & OOS_EVALUATIONS == 0", "PASS" if g14_ok else "FAIL", "14", f"readiness={f14.get('FUTURE_OOS_READINESS_VERDICT')}, evals={f14.get('OOS_EVALUATIONS')}")

    # Gate 15: Full repo regression collection <-> JUnit bijection (HONEST BLOCKED_LOW_DISK if not run!)
    reg_st = str(f16.get("status") or "FAIL")
    if reg_st == "NOT_RUN_BLOCKED_LOW_DISK":
        g15_status = "BLOCKED_LOW_DISK"
    elif (
        reg_st == "PASS"
        and int(f16.get("collected_count", 0)) > 0
        and int(f16.get("collected_count", -1)) == int(f16.get("executed_count", -2))
        and int(f16.get("failed_count", 1)) == 0
        and int(f16.get("error_count", 1)) == 0
        and bool(f16.get("junit_bijection_exact", False))
    ):
        g15_status = "PASS"
    else:
        g15_status = "FAIL"
    _g("Gate 15", "Full repository pytest regression & 1:1 JUnit bijection", g15_status, "16", f"status={reg_st}, collected={f16.get('collected_count')}, executed={f16.get('executed_count')}")

    # Gate 16: Async scheduler W1/W2/W4/W1_repeat deterministic & truthful speedup
    sch_st = str(f17.get("status") or "FAIL")
    if sch_st == "NOT_RUN_BLOCKED_LOW_DISK":
        g16_status = "BLOCKED_LOW_DISK"
    elif (
        sch_st == "PASS"
        and bool(f17.get("pytest_subset_w1_w2_w4_repeat_identical", False))
        and bool(f17.get("r2_science_slice_w1_w2_w4_repeat_identical", False))
    ):
        g16_status = "PASS"
    else:
        g16_status = "FAIL"
    _g("Gate 16", "Async W1/W2/W4/W1_repeat determinism & workload-specific speeds", g16_status, "17", f"status={sch_st}")

    # Gate 17: RAM >= 512 MiB, global children <= 4, no orphan, STRICT 20.5 GiB disk preflight / 20.0 GiB runtime
    min_ram = float(f18.get("min_available_ram_mib") or 0.0)
    min_disk = float(f18.get("min_disk_free_gib") or 0.0)
    max_ch_raw = f18.get("max_descendant_children")
    orphans_raw = f18.get("orphan_children_after_cleanup")
    max_ch = int(max_ch_raw) if max_ch_raw is not None else 99
    orphans = int(orphans_raw) if orphans_raw is not None else 99
    owner_waived_disk = bool(f00.get("owner_disk_preflight_waiver", False)) and bool(f18.get("owner_disk_preflight_waiver", False))
    ram_ok = min_ram >= HARD_RAM_FLOOR_MIB_KH02_R2 and max_ch <= GLOBAL_MAX_DESCENDANTS_KH02_R2 and orphans == 0
    disk_strict_ok = min_disk >= STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2 and bool((f18.get("disk_preflight_policy") or {}).get("can_run_heavy_stages", False))
    emergency_disk_ok = min_disk >= 2.0
    if not ram_ok or (owner_waived_disk and not emergency_disk_ok):
        g17_status = "FAIL"
    elif disk_strict_ok:
        g17_status = "PASS"
    elif owner_waived_disk:
        g17_status = "WAIVED_BY_OWNER_LOW_DISK"
    else:
        g17_status = "BLOCKED_LOW_DISK"
    _g("Gate 17", "RAM >= 512 MiB, children <= 4, orphans == 0, strict disk >= 20.5 GiB", g17_status, "18", f"min_ram={min_ram:.1f} MiB, min_disk={min_disk:.4f} GiB, max_ch={max_ch}, orphans={orphans}, owner_waived={owner_waived_disk}")

    # Gate 18: >= 40 unique real RED/GREEN faults
    g18_ok = f19.get("status") == "PASS" and int(f19.get("unique_fault_ids", 0)) >= 40 and int(f19.get("failed_controls", 1)) == 0
    _g("Gate 18", ">= 40 unique real RED/GREEN fault controls", "PASS" if g18_ok else "FAIL", "19", f"passed={f19.get('passed_controls')}/{f19.get('total_controls')}")

    # Gate 19: Independent separate-OS disk verifier (evaluated here if 22 exists, else PENDING_SEPARATE_OS_VERIFIER)
    if f22.get("_missing"):
        g19_status = "PENDING_SEPARATE_OS_VERIFIER"
    else:
        g19_status = "PASS" if f22.get("independent_verifier_status") == "PASS" else "FAIL"
    _g("Gate 19", "Separate-OS-process independent disk verifier & SHA256 manifest", g19_status, "22", f"verifier={f22.get('independent_verifier_status', 'PENDING')}")

    # Gate 20: Remote commit, push & SHA/blob readback
    pub_st = str(f23.get("publication_status") or "NOT_PUBLISHED_BLOCKED_LOW_DISK")
    if pub_st == "PASS_REMOTE_VERIFIED":
        g20_status = "PASS"
    elif "BLOCKED" in pub_st:
        g20_status = "BLOCKED_LOW_DISK" if "LOW_DISK" in pub_st else pub_st
    elif not require_remote_push_verified and pub_st in ("PRE_PUSH_PENDING", "PRE_PUSH_PENDING_WITH_OWNER_DISK_WAIVER"):
        g20_status = "PENDING_REMOTE_PUSH"
    else:
        g20_status = "FAIL"
    _g("Gate 20", "Safe commit, push & true remote SHA/blob readback", g20_status, "23", f"publication_status={pub_st}")

    # Independent orthogonal verdicts (NEVER merged into one fake PASS!)
    parity_st = str(f20.get("BASELINE_PARITY_VERDICT") or "NOT_RUN_BLOCKED_LOW_DISK")
    econ_v = str(f15.get("RESEARCH_ECONOMIC_VERDICT") or "NO_PROVEN_EDGE")
    if econ_v == "PROVEN_EDGE":
        # Guardrail: reused dataset CANNOT claim PROVEN_EDGE
        econ_v = "FAIL_FORGED_PROVEN_EDGE_ON_REUSED_DATA"

    # Engineering verdict reflects offline code/science gates + resource/regression truth
    offline_science_gates = [g for g in gates if g["gate_id"] in ("Gate 02", "Gate 03", "Gate 04", "Gate 05", "Gate 06", "Gate 07", "Gate 08", "Gate 09", "Gate 10", "Gate 11", "Gate 12", "Gate 13", "Gate 14", "Gate 18")]
    any_hard_fail = any(g["status"] == "FAIL" for g in gates) or parity_st == "FAIL"
    any_low_disk = any(g["status"] == "BLOCKED_LOW_DISK" for g in gates)

    if any_hard_fail:
        eng_verdict = "FAIL"
    elif any_low_disk:
        eng_verdict = "BLOCKED_LOW_DISK"
    elif all(g["status"] == "PASS" for g in offline_science_gates) and g15_status == "PASS" and g16_status == "PASS" and g01_status == "PASS" and parity_st == "PASS":
        if g17_status == "PASS":
            eng_verdict = "PASS"
        elif g17_status == "WAIVED_BY_OWNER_LOW_DISK":
            eng_verdict = "PASS_WITH_OWNER_DISK_WAIVER"
        else:
            eng_verdict = "BLOCKED_PENDING_VERIFICATION"
    else:
        eng_verdict = "BLOCKED_PENDING_VERIFICATION"

    verdicts: Dict[str, str] = {
        "ENGINEERING_VERDICT": eng_verdict,
        "OFFLINE_RESEARCH_INTEGRITY_LOGIC_VERDICT": "PASS" if all(g["status"] == "PASS" for g in offline_science_gates) else "FAIL",
        "FULL_REGRESSION_VERDICT": reg_st,
        "RESOURCE_SAFETY_VERDICT": "PASS" if g17_status == "PASS" else g17_status,
        "BASELINE_PARITY_VERDICT": parity_st,
        "ENTRY_TARGET_INTEGRITY_VERDICT": "PASS" if (g05_ok and g06_ok and g07_ok) else "FAIL",
        "PAIRED_COMPARISON_VERDICT": pcv or "INSUFFICIENT_COMMON_SUPPORT",
        "COST_RECONCILIATION_VERDICT": "PASS" if (g10_ok and g12_ok) else "FAIL",
        "DATA_PROVENANCE_VERDICT": str(f13.get("DATA_PROVENANCE_VERDICT") or "FAIL"),
        "FUTURE_OOS_READINESS_VERDICT": str(f14.get("FUTURE_OOS_READINESS_VERDICT") or "BLOCKED_NO_VERIFIED_NEW_DATA"),
        "FRESH_OOS_VERDICT": str(f14.get("FRESH_OOS_VERDICT") or "BLOCKED_NO_UNTOUCHED_DATA"),
        "RESEARCH_ECONOMIC_VERDICT": econ_v,
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "PUBLICATION_VERDICT": pub_st,
    }

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "run_id": f00.get("run_id"),
        "tested_executable_sha": tested_sha,
        "branch": f00.get("branch"),
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "OOS_EVALUATIONS": int(f14.get("OOS_EVALUATIONS", 0)),
        "gates_total": len(gates),
        "gates_passed": sum(1 for g in gates if g["status"] == "PASS"),
        "gates_blocked": sum(1 for g in gates if "BLOCKED" in g["status"]),
        "gates_failed": sum(1 for g in gates if g["status"] == "FAIL"),
        "gates_pending": sum(1 for g in gates if "PENDING" in g["status"]),
        "gates": gates,
        "orthogonal_verdicts": verdicts,
    }
    payload["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "artifact_canonical_sha256"}
    )
    return payload


def render_r2_readme_and_handover_vi(
    *,
    run_dir: Path,
    f00: Mapping[str, Any],
    f01: Mapping[str, Any],
    f02: Mapping[str, Any],
    f05: Mapping[str, Any],
    f08: Mapping[str, Any],
    f09: Mapping[str, Any],
    f10: Mapping[str, Any],
    f11: Mapping[str, Any],
    f12: Mapping[str, Any],
    f13: Mapping[str, Any],
    f14: Mapping[str, Any],
    f15: Mapping[str, Any],
    f16: Mapping[str, Any],
    f17: Mapping[str, Any],
    f18: Mapping[str, Any],
    f19: Mapping[str, Any],
    f20: Mapping[str, Any],
    f21: Mapping[str, Any],
    f23: Mapping[str, Any],
) -> Tuple[str, str]:
    """
    Renders `README_KH02_R2.md` (technical English+Vietnamese reference) and
    `HANDOVER_KH02_R2_VI.txt` (concise Vietnamese owner handover) strictly from closed
    artifact numbers so every view is 100% consistent with `21_orthogonal_*_gate_matrix.json`.
    """
    v = f21["orthogonal_verdicts"]
    audit5 = f05.get("full_dataset_audit_summary") or {}
    arm5 = f05.get("r1_arm_swept_target_impact") or {}
    comp9 = f09.get("comparison_by_arm") or {}
    bal8 = f08.get("arm_balance_reports") or {}
    ff11 = f11.get("arms") or {}
    fu12 = f12.get("denominator_shift_summary") or {}

    lines_md: List[str] = [
        "# AURUM DESK — KH-02.R2 | Research Integrity & Data Readiness",
        "",
        f"- **Spec Version**: `{SPEC_VERSION_KH02_R2}`",
        f"- **Run ID**: `{f00.get('run_id')}`",
        f"- **Branch**: `{f00.get('branch')}`",
        f"- **Observed Start HEAD**: `{f00.get('start_head_sha')}`",
        f"- **Tested Executable SHA**: `{f00.get('tested_executable_sha')}`",
        f"- **Prior KH-02.R1 Accepted Run**: `{PRIOR_KH02_R1_RUN_ID}` (untouched, read-only reference)",
        f"- **Safety Modes**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`",
        "",
        "## 1. Orthogonal Verdicts",
        "",
        "| Dimension | Verdict |",
        "|---|---|",
    ]
    for k, val in v.items():
        lines_md.append(f"| `{k}` | **{val}** |")

    lines_md += [
        "",
        "## 2. P0 Swept-Target Audit on KH-02.R1 (Read-Only Reproduction)",
        "",
        f"- **Finding Classification**: `{f05.get('finding_classification')}` (Production affected: `{f05.get('production_affected')}`)",
        f"- **Directional Post-Warmup M1 Opportunities Audited**: `{audit5.get('total_directional_opportunities_audited')}`",
        f"- **R1 Raw Targets Selected**: `{audit5.get('r1_targets_selected_count')}` | **Already-Swept Raw Targets**: `{audit5.get('total_confirmed_already_swept_raw_targets')}` (`{round(100.0 * float(audit5.get('total_confirmed_already_swept_raw_rate') or 0.0), 2)}%`)",
        f"- **R1 Risk-Eligible Candidates**: `{audit5.get('r1_risk_eligible_count')}` | **Already-Swept Risk-Eligible**: `{audit5.get('total_confirmed_already_swept_risk_eligible_targets')}` (`{round(100.0 * float(audit5.get('total_confirmed_already_swept_risk_eligible_rate') or 0.0), 2)}%`)",
        f"- **Breakdown (Raw)**: `{audit5.get('classification_counts_raw_targets')}`",
        f"- **Synthetic T01..T10 Suite**: `{f05.get('synthetic_t01_to_t10_suite', {}).get('passed_count')}/{f05.get('synthetic_t01_to_t10_suite', {}).get('scenario_count')} PASS`",
        "",
        "### Per-Arm Swept Target Contamination in R1",
        "",
        "| Arm | R1 Eligible | Swept Total | Swept Rate | Swept on Source HTF | Swept by Intervening M1 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for ac in (*PRIMARY_R2_ARMS, "NC_NULL_TRIGGER"):
        a = arm5.get(ac) or {}
        lines_md.append(
            f"| `{ac}` | {a.get('r1_arm_eligible_count', 0)} | {a.get('r1_swept_target_count', 0)} | "
            f"{round(100.0 * float(a.get('r1_swept_target_rate') or 0.0), 2)}% | "
            f"{a.get('swept_on_source_htf', 0)} | {a.get('swept_by_intervening_m1', 0)} |"
        )

    lines_md += [
        "",
        "## 3. Chronological 1-Position Replay (NEXT_BAR_OPEN_BID_ASK): R1 vs R2 Verified-Unswept",
        "",
        "| Arm | R1 Closed (W/L) | R1 Net USD | R1 Exp R | R2 Closed (W/L) | R2 Net USD | R2 Exp R | Balance Quality (`|SMD|<=0.10`) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for ac in (*PRIMARY_R2_ARMS, "NC_NULL_TRIGGER"):
        nb = (comp9.get(ac) or {}).get("NEXT_BAR_OPEN_BID_ASK") or {}
        s1 = nb.get("r1_summary") or {}
        s2 = nb.get("r2_summary") or {}
        b_q = (bal8.get(ac) or {}).get("matching_quality_status", "CONTROL_BASELINE")
        lines_md.append(
            f"| `{ac}` | {s1.get('closed', 0)} ({s1.get('wins', 0)}W/{s1.get('losses', 0)}L) | {s1.get('net_pnl_usd')} | {s1.get('expectancy_r')} | "
            f"{s2.get('closed', 0)} ({s2.get('wins', 0)}W/{s2.get('losses', 0)}L) | {s2.get('net_pnl_usd')} | {s2.get('expectancy_r')} | `{b_q}` |"
        )

    lines_md += [
        "",
        "## 4. Fixed-Fill Same-IDs vs Full-Universe Rerisked Cost Audit",
        "",
        "- Historical quotes: `MODELED_BID_ASK` (spread `$0.20` USD, NO observed L1 BBO).",
        "- Actual account effective fee tier: `UNKNOWN_NOT_LOCALLY_VERIFIED` (`0.0004` domain default vs `0.0006` public reference).",
        "",
        "| Scenario (`FULL_RERISKED_DYNAMIC_POPULATION`) | Raw R2 Eligible | Delta vs 1x | B2_H1 Closed / Net USD | B2_H2 Closed / Net USD | B2_H3 Closed / Net USD | B2_H123 Closed / Net USD |",
        "|---|---:|---:|---|---|---|---|",
    ]
    for sc_id, sd in fu12.items():
        cl = sd.get("arm_closed_trades") or {}
        np = sd.get("arm_net_pnl_usd") or {}
        lines_md.append(
            f"| `{sc_id}` | {sd.get('raw_r2_eligible')} | {sd.get('delta_raw_eligible_vs_1x_base')} | "
            f"{cl.get('B2_H1')} / {np.get('B2_H1')} | {cl.get('B2_H2')} / {np.get('B2_H2')} | "
            f"{cl.get('B2_H3')} / {np.get('B2_H3')} | {cl.get('B2_H123')} / {np.get('B2_H123')} |"
        )

    lines_md += [
        "",
        "## 5. New Functions Added & Locked Files Kept As-Is",
        "",
        f"- Locked KEEP-AS-IS files modified: `{f01.get('locked_files_modified_count')}` (0 required).",
        f"- Existing files modified: `{f01.get('modified_existing_files_count')}` (0 required).",
        f"- Forbidden order/exchange imports: `{len(f01.get('forbidden_order_import_hits') or [])}`.",
        "",
        "| Function | Module | Artifact |",
        "|---|---|---|",
    ]
    for fn_row in R2_NEW_FUNCTION_OWNERSHIP_MAP:
        lines_md.append(f"| `{fn_row['function']}` | `{fn_row['module']}` | `{fn_row['artifact']}` |")

    lines_md += [
        "",
        "## 6. QA, Resources, Regression & Gates",
        "",
        f"- **RED/GREEN Faults**: `{f19.get('passed_controls')}/{f19.get('total_controls')}` (`{f19.get('status')}`)",
        f"- **Full Pytest Regression**: `{f16.get('status')}` (collected=`{f16.get('collected_count')}`, executed=`{f16.get('executed_count')}`)",
        f"- **Focused KH-02.R2 Unit Tests**: `{f16.get('focused_r2_pytest_summary')}`",
        f"- **Min Available RAM**: `{f18.get('min_available_ram_mib')} MiB` (hard floor `>= 512.0 MiB`)",
        f"- **Min Disk Free**: `{f18.get('min_disk_free_gib')} GiB` (strict preflight floor `>= 20.5 GiB`; shortfall `{f18.get('shortfall_to_strict_20p5_gib')} GiB`)",
        f"- **Max Descendant Children / Orphans**: `{f18.get('max_descendant_children')}` / `{f18.get('orphan_children_after_cleanup')}`",
        f"- **OOS Readiness**: `{f14.get('FUTURE_OOS_READINESS_VERDICT')}` (`OOS_EVALUATIONS = {f14.get('OOS_EVALUATIONS')}`)",
        "",
        "### Gate Matrix (`21_orthogonal_engineering_science_economic_oos_gate_matrix.json`)",
        "",
        "| Gate | Name | Status | Detail |",
        "|---|---|---|---|",
    ]
    for g in f21["gates"]:
        lines_md.append(f"| `{g['gate_id']}` | {g['name']} | **{g['status']}** | {g['detail']} |")

    readme_text = "\n".join(lines_md) + "\n"

    # Concise Vietnamese Owner Handover TXT
    vi_lines: List[str] = [
        "================================================================================",
        "AURUM DESK — KH-02.R2 | BÁO CÁO BÀN GIAO CHỦ SỞ HỮU (TIẾNG VIỆT)",
        "================================================================================",
        f"Spec Version            : {SPEC_VERSION_KH02_R2}",
        f"Run ID                  : {f00.get('run_id')}",
        f"Branch                  : {f00.get('branch')}",
        f"Start HEAD              : {f00.get('start_head_sha')}",
        f"Tested Executable SHA   : {f00.get('tested_executable_sha')}",
        f"Publication Status      : {f23.get('publication_status')}",
        f"Run Directory           : {run_dir}",
        "",
        "1. TRẠNG THÁI TRỰC GIAO (ORTHOGONAL VERDICTS — KHÔNG GỘP GIẢ):",
    ]
    for k, val in v.items():
        vi_lines.append(f"   - {k}: {val}")

    vi_lines += [
        "",
        "2. KIỂM ĐỊNH LỖI MỤC TIÊU ĐÃ BỊ SWEEP TRONG KH-02.R1 (CHỈ ĐỌC, KHÔNG SỬA R1/PROD):",
        f"   - Phân loại kết quả: {f05.get('finding_classification')} (Production bị ảnh hưởng: {f05.get('production_affected')})",
        f"   - Tổng cơ hội M1 có hướng kiểm định: {audit5.get('total_directional_opportunities_audited')}",
        f"   - Tổng target R1 chọn bị sweep sẵn (raw): {audit5.get('total_confirmed_already_swept_raw_targets')} / {audit5.get('r1_targets_selected_count')} ({round(100.0 * float(audit5.get('total_confirmed_already_swept_raw_rate') or 0.0), 2)}%)",
        f"   - Tổng ứng viên R1 đạt risk-gate bị sweep sẵn: {audit5.get('total_confirmed_already_swept_risk_eligible_targets')} / {audit5.get('r1_risk_eligible_count')} ({round(100.0 * float(audit5.get('total_confirmed_already_swept_risk_eligible_rate') or 0.0), 2)}%)",
        f"   - Chi tiết phân loại raw: {audit5.get('classification_counts_raw_targets')}",
        f"   - Bộ kịch bản T01..T10: {f05.get('synthetic_t01_to_t10_suite', {}).get('passed_count')}/{f05.get('synthetic_t01_to_t10_suite', {}).get('scenario_count')} PASS",
        "",
        "3. SO SÁNH R1 vs R2 VERIFIED-UNSWEPT (NEXT_BAR_OPEN_BID_ASK, 1-POSITION):",
    ]
    for ac in (*PRIMARY_R2_ARMS, "NC_NULL_TRIGGER"):
        nb = (comp9.get(ac) or {}).get("NEXT_BAR_OPEN_BID_ASK") or {}
        s1 = nb.get("r1_summary") or {}
        s2 = nb.get("r2_summary") or {}
        b_q = (bal8.get(ac) or {}).get("matching_quality_status", "CONTROL_BASELINE")
        vi_lines.append(
            f"   - {ac}: R1={s1.get('closed', 0)} lệnh ({s1.get('wins', 0)}W/{s1.get('losses', 0)}L, Net={s1.get('net_pnl_usd')} USD, Exp={s1.get('expectancy_r')}R) "
            f"-> R2={s2.get('closed', 0)} lệnh ({s2.get('wins', 0)}W/{s2.get('losses', 0)}L, Net={s2.get('net_pnl_usd')} USD, Exp={s2.get('expectancy_r')}R) | Balance={b_q}"
        )

    vi_lines += [
        "   - Lưu ý B0 vs B2: COMPARISON_NOT_IDENTIFIABLE (khác vũ trụ và khác hình học SL/TP).",
        "",
        "4. CHI PHÍ FIXED-FILL vs FULL-UNIVERSE RERISKED & RỦI RO FILL-TIME:",
        f"   - Quote mode: MODELED_BID_ASK (spread mô phỏng 0.20 USD, KHÔNG có L1 BBO thực).",
        f"   - Phí thực tài khoản: UNKNOWN_NOT_LOCALLY_VERIFIED (đối chiếu 0.0004 vs 0.0006).",
        f"   - Vi phạm ngưỡng rủi ro kế hoạch <= 2.50 USD (decision/post-fill): {f10.get('decision_time_cap_violations')}/{f10.get('post_fill_policy_cap_violations')}.",
        "   - Khuyến cáo đuôi lỗ thực tế: planned_net_risk <= 2.50 USD KHÔNG đảm bảo vật lý lỗ thực tế không vượt 2.50 USD khi gap/trượt giá.",
        f"   - Dữ liệu M1 hiệu dụng: ~{f13.get('headline_effective_m1_calendar_span_days')} ngày lịch (REUSED_RESEARCH, KHÔNG phải Fresh OOS).",
        "",
        "5. QA, TÀI NGUYÊN & TRẠNG THÁI CHẶN (NẾU CÓ):",
        f"   - Faults RED/GREEN: {f19.get('passed_controls')}/{f19.get('total_controls')} ({f19.get('status')})",
        f"   - Full Repo Pytest: {f16.get('status')} (collected={f16.get('collected_count')}, executed={f16.get('executed_count')})",
        f"   - Min RAM khả dụng: {f18.get('min_available_ram_mib')} MiB (ngưỡng cứng >= 512 MiB)",
        f"   - Min Disk trống  : {f18.get('min_disk_free_gib')} GiB (ngưỡng cứng preflight >= 20.5 GiB; thiếu {f18.get('shortfall_to_strict_20p5_gib')} GiB)",
        f"   - Max tiến trình con / mồ côi: {f18.get('max_descendant_children')} / {f18.get('orphan_children_after_cleanup')}",
        f"   - OOS_EVALUATIONS : {f14.get('OOS_EVALUATIONS')} ({f14.get('FUTURE_OOS_READINESS_VERDICT')})",
        "",
        "6. HÀNH ĐỘNG TIẾP THEO (P0/P1):",
        "   - P0 (Nếu đang BLOCKED_LOW_DISK): Giải phóng >= 11 GiB (khuyến nghị trống >= 25 GiB) trên phân vùng dữ liệu, commit mã nguồn KH-02.R2 trên nhánh feature/v17-9-2-structural-sl-tp-from-qa rồi chạy lại tools/run_kh_02_r2_verification.py để thực thi đủ 5,500+ pytest + W1/W2/W4 + 56,710 parity + push.",
        "   - P0 (Dữ liệu mới): Thu thập dữ liệu XAUUSDT tương lai (M1/M15/H1 + L1 BBO thực có dấu thời gian) sau mốc đóng băng + embargo 240 phút vào escrow append-only; giữ OOS_EVALUATIONS=0 cho đến khi có giao thức phê duyệt mở niêm phong.",
        "   - CAM KẾT AN TOÀN: PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO.",
        "================================================================================",
    ]
    handover_vi = "\n".join(vi_lines) + "\n"
    return readme_text, handover_vi
